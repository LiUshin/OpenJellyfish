import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import httpx
from fastapi import FastAPI
from app.core.host_auth import HOST_ID, ensure_key, read_key
from app.deps import get_current_user
from app.routes.runtime import router, management_router
from app.runtime.manager import get_runtime
from app.runtime.profiles import ProfileManager
from app.runtime.providers import CodexProvider, CursorProvider
from app.runtime.policy import DeploymentPolicy
from app.runtime.store import RuntimeStore


class HostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch('app.core.host_auth.key_path', return_value=self.root / 'host.key'),
                        patch.dict(os.environ, {'JELLYFISH_RUNTIME_ENABLED': '1', 'JELLYFISH_OWNER_USER_ID': 'legacy'}),
                        patch('app.runtime.profiles.active_user', side_effect=lambda uid: uid in ('legacy','alice')),
                        patch('app.core.security.verify_token', return_value=None),
                        patch('app.core.security._load_users', return_value={'legacy': {'username':'Legacy'}, 'alice': {'username':'Alice'}})]
        for p in self.patches: p.start()
        self.key = ensure_key()
        self.store = RuntimeStore(self.root / 'runtime')
        self.profiles = ProfileManager(self.store, DeploymentPolicy(), 'unused', providers={'codex':CodexProvider('unused'),'cursor':CursorProvider('unused')})
        self.manager = SimpleNamespace(profiles=self.profiles, store=self.store, runs=SimpleNamespace(cancel_profile=AsyncMock()))
        self.app = FastAPI(); self.app.include_router(router); self.app.include_router(management_router)
        self.app.dependency_overrides[get_runtime] = lambda: self.manager
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose(); await self.profiles.shutdown(); self.store.close()
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def headers(self, key=None):
        return {'Authorization': 'Bearer ' + (key or self.key)}

    async def test_host_key_is_distinct_from_admin_and_rotation_revokes(self):
        path = '/api/superadmin/runtime/capabilities'
        for token in ('', 'legacy', 'admin-token', 'host:owner'):
            self.assertEqual((await self.client.get(path, headers=self.headers(token or 'invalid'))).status_code, 401)
        self.assertEqual((await self.client.get(path)).status_code, 401)
        response = await self.client.get(path, headers=self.headers())
        self.assertTrue(response.json()['can_manage_connections'])
        self.assertNotIn(self.key, response.text)
        # Admin requests use their own auth dependency; a host key is not an admin login.
        with patch('app.deps.verify_token', return_value=None):
            self.assertEqual((await self.client.get('/api/runtime/profiles', headers=self.headers())).status_code, 401)
        replacement = ensure_key(rotate=True)
        self.assertNotEqual(replacement, self.key)
        self.assertEqual((await self.client.get(path, headers=self.headers())).status_code, 401)
        self.assertEqual((await self.client.get(path, headers=self.headers(replacement))).status_code, 200)

    async def test_management_endpoints_reject_admin_before_any_mutation(self):
        base = '/api/superadmin/runtime'
        for method, path, data in [('POST','/profiles',{'name':'x'}), ('POST','/profiles/p/login',{}),
                                  ('GET','/profiles/p/login/l',None), ('DELETE','/profiles/p/login/l',None),
                                  ('POST','/profiles/p/probe',None), ('DELETE','/profiles/p/connection',None),
                                  ('GET','/admins',None), ('GET','/profiles/p/grants',None),
                                  ('PUT','/profiles/p/grants',{'actor_id':'alice','models':['m']}),
                                  ('DELETE','/profiles/p/grants/g',None)]:
            response = await self.client.request(method,base+path,json=data,headers=self.headers('legacy'))
            self.assertEqual(response.status_code,401,(path,response.text))
        self.assertEqual(self.store.all('profile'),[])
        response = await self.client.post(base+'/profiles',json={'name':'Host Cursor','runtime':'cursor'},headers=self.headers())
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.store.all('profile')[0]['credential_owner_id'],HOST_ID)
        # The former env-bound owner is now an ordinary admin.
        self.app.dependency_overrides[get_current_user] = lambda: {'user_id':'legacy'}
        self.assertFalse((await self.client.get('/api/runtime/capabilities')).json()['can_manage_connections'])
        self.assertEqual((await self.client.get('/api/runtime/profiles')).json(),[])
        self.assertIn((await self.client.post('/api/runtime/profiles',json={'name':'bypass'})).status_code,(404,405))

    async def test_missing_external_cli_returns_deployment_error_without_stranding_login(self):
        for runtime, provider, mode in [('cursor', CursorProvider, 'cursorBrowser'),
                                        ('codex', CodexProvider, 'chatgptDeviceCode')]:
            with self.subTest(runtime=runtime):
                p = self.profiles.create(HOST_ID, runtime, runtime)
                self.profiles.providers[runtime] = provider(str(self.root / 'missing-cli'))
                response = await self.client.post(f"/api/superadmin/runtime/profiles/{p['id']}/login",
                    json={'mode': mode}, headers=self.headers())
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn(f'JELLYFISH_RUNTIME_{runtime.upper()}_BIN', response.json()['detail'])
                self.assertNotIn(str(self.root), response.text)
                self.assertEqual(self.profiles.logins, {})
                latest = self.profiles.get(p['id'])
                self.assertEqual(latest['status'], 'disconnected')
                self.assertFalse(latest.get('recovery_required'))
                self.assertEqual(self.store.find('login', profile_id=p['id'])[0]['status'], 'failed')

    async def test_cursor_login_timeout_has_safe_message_and_releases_reservation(self):
        p = self.profiles.create(HOST_ID, 'Cursor', 'cursor')
        rpc = SimpleNamespace(process=None, start=AsyncMock(), close=AsyncMock(),
                              next_event=AsyncMock(side_effect=TimeoutError))
        with patch('app.runtime.cursor.LoginProcess', return_value=rpc):
            response = await self.client.post(f"/api/superadmin/runtime/profiles/{p['id']}/login",
                json={'mode': 'cursorBrowser'}, headers=self.headers())
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn('30 秒', response.json()['detail'])
        rpc.close.assert_awaited_once()
        self.assertEqual(self.profiles.logins, {})
        self.assertEqual(self.profiles.get(p['id'])['status'], 'disconnected')
        self.assertFalse(self.profiles.get(p['id']).get('recovery_required'))

    async def test_key_persistence_private_permissions_and_missing_fail_closed(self):
        self.assertEqual(ensure_key(), self.key)
        if os.name != 'nt':
            self.assertEqual((self.root/'host.key').stat().st_mode & 0o777,0o600)
            (self.root/'host.key').chmod(0o644)
            with self.assertRaises(ValueError): read_key()
            (self.root/'host.key').chmod(0o600)
        (self.root/'host.key').unlink()
        self.assertEqual((await self.client.get('/api/superadmin/runtime/capabilities',headers=self.headers())).status_code,503)

    def legacy(self, *, grant=None):
        p={'id':'old','runtime':'codex','credential_owner_id':'legacy','auth_generation':2,
           'status':'ready','models':[{'id':'m','name':'M'}]}
        self.store.put('profile',p)
        binding={'profile_id':'old','credential_owner_id':'legacy','auth_generation':2,'model':'m'}
        self.store.put('session',{'id':'session','actor_id':'legacy','binding':binding,'thread_id':'native-history'})
        if grant: self.store.put('grant',grant)

    async def test_migration_retains_usage_and_native_history_without_new_privilege(self):
        self.legacy()
        self.profiles.migrate_host_ownership()
        migrated=self.store.get('session','session')
        self.assertEqual(migrated['thread_id'],'native-history')
        self.assertEqual(migrated['binding']['credential_owner_id'],HOST_ID)
        self.profiles.authorize('legacy',migrated['binding'])
        self.assertFalse(self.profiles.list('legacy')[0]['can_manage'])
        self.assertEqual(self.profiles.list('alice'),[])
        grants=self.store.all('grant')
        self.profiles.migrate_host_ownership()
        self.assertEqual(self.store.all('grant'),grants)

    async def test_migration_never_restores_revoked_grants_and_is_atomic(self):
        from fastapi import HTTPException
        self.legacy(grant={'id':'revoked','profile_id':'old','actor_id':'legacy','enabled':False,
                           'version':3,'auth_generation':2,'models':['m']})
        original=self.store._put
        def fail_session(kind,row):
            if kind=='session': raise RuntimeError('simulated disk failure')
            original(kind,row)
        with patch.object(self.store,'_put',side_effect=fail_session), self.assertRaises(RuntimeError):
            self.profiles.migrate_host_ownership()
        self.assertEqual(self.store.get('profile','old')['credential_owner_id'],'legacy')
        self.profiles.migrate_host_ownership()
        with self.assertRaises(HTTPException): self.profiles.authorize('legacy',self.store.get('session','session')['binding'])
        self.assertFalse(self.store.get('grant','revoked')['enabled'])
