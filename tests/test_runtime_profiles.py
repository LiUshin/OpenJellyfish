from app.core.host_auth import HOST_ID
import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from app.runtime.policy import DeploymentPolicy
from app.runtime.profiles import ProfileManager
from app.runtime.store import RuntimeStore
from app.runtime.vault import CredentialVault, identity


def auth_bytes(subject='one', refresh='test-refresh'):
    payload = base64.urlsafe_b64encode(json.dumps({'sub': subject}).encode()).decode().rstrip('=')
    return json.dumps({'auth_mode': 'chatgpt', 'tokens': {'account_id': 'test-account', 'id_token': 'test.' + payload + '.test',
            'access_token': 'test-access', 'refresh_token': refresh}}).encode()


class AuthAdapter:
    instances = []
    def __init__(self, executable, home, cwd, *args, **kwargs):
        self.home = home
        self.rpc = self
        self.events = asyncio.Queue()
        self.closed = False
        self.instances.append(self)
    async def start(self):
        pass
    async def request(self, method, params, **kwargs):
        if method == 'account/login/start':
            return {'loginId': 'provider-login', 'verificationUrl': 'https://auth.openai.com/device', 'userCode': 'TEST-CODE'}
        if method == 'account/read':
            return {'account': {'type': 'chatgpt', 'email': 'test@example.invalid', 'planType': 'test'}}
        if method == 'model/list':
            return {'data': [{'id': 'test-model', 'displayName': 'Test Model'}]}
        return {}
    async def next_event(self):
        return await self.events.get()
    async def close(self):
        self.closed = True
    async def complete(self, subject='one', login_id='provider-login'):
        (self.home / 'auth.json').write_bytes(auth_bytes(subject))
        await self.events.put({'method': 'account/login/completed', 'params': {'loginId': login_id, 'success': True}})


class ProfileFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = RuntimeStore(self.root)
        self.env = patch.dict(os.environ, {'JELLYFISH_OWNER_USER_ID': HOST_ID})
        self.env.start()
        self.users = patch('app.runtime.profiles.active_user', return_value=True)
        self.users.start()
        self.manager = ProfileManager(self.store, DeploymentPolicy(), 'test', adapter_factory=AuthAdapter)
        self.profile = self.manager.create(HOST_ID, 'test')
    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.users.stop(); self.env.stop()
        self.store.close(); self.tmp.cleanup()
    async def finish_login(self, subject='one'):
        a = await self.manager.start_login(HOST_ID, self.profile['id'], 'chatgptDeviceCode')
        entry = self.manager.logins[a['id']]
        await entry['adapter'].complete(subject)
        await entry['task']
        return a


class ProfileTests(ProfileFixture):
    async def test_real_lifecycle_contract_has_no_credential_response(self):
        a = await self.finish_login()
        p = self.manager.get(self.profile['id'])
        self.assertEqual(p['status'], 'ready')
        self.assertEqual(self.store.get('login', a['id'])['status'], 'completed')
        public = json.dumps(self.manager.public(p, HOST_ID))
        self.assertNotIn('test-refresh', public)
        self.assertNotIn('identity', public)
        self.assertEqual(self.manager.list('admin'), [])
        self.assertFalse((self.root / 'login' / a['id']).exists())
        self.assertFalse(b'test-refresh' in self.manager.vault.path(p['id']).read_bytes())
    async def test_admin_cannot_start_disconnect_or_probe(self):
        for action in (lambda: self.manager.start_login('admin', self.profile['id'], 'chatgpt'),
                       lambda: self.manager.disconnect('admin', self.profile['id']),
                       lambda: self.manager.probe('admin', self.profile['id'])):
            with self.assertRaises(HTTPException) as denial:
                await action()
            self.assertEqual(denial.exception.status_code, 403)
    async def test_immediate_cancel_and_duplicate_login(self):
        a = await self.manager.start_login(HOST_ID, self.profile['id'], 'chatgptDeviceCode')
        with self.assertRaises(HTTPException):
            await self.manager.start_login(HOST_ID, self.profile['id'], 'chatgptDeviceCode')
        await self.manager.cancel_login(HOST_ID, self.profile['id'], a['id'])
        self.assertEqual(self.store.get('login', a['id'])['status'], 'cancelled')
        self.assertFalse(self.manager.logins)
        self.assertTrue(AuthAdapter.instances[-1].closed)
        with self.assertRaises(HTTPException):
            await self.manager.cancel_login(HOST_ID, self.profile['id'], a['id'])
    async def test_credential_refresh_serialization_and_cleanup(self):
        await self.finish_login()
        binding = self.manager.binding(HOST_ID, self.profile['id'], 'test-model')
        h1, h2 = self.root / 'actor-a', self.root / 'actor-b'
        entered = asyncio.Event()
        async def second():
            async with self.manager.lease(binding, h2):
                self.assertEqual(json.loads((h2 / 'auth.json').read_bytes())['tokens']['refresh_token'], 'refreshed')
                entered.set()
        async with self.manager.lease(binding, h1):
            task = asyncio.create_task(second())
            await asyncio.sleep(.01)
            self.assertFalse(entered.is_set())
            (h1 / 'auth.json').write_bytes(auth_bytes(refresh='refreshed'))
        await task
        self.assertFalse((h1 / 'auth.json').exists())
        self.assertFalse((h2 / 'auth.json').exists())
    async def test_switch_invalidates_old_binding_but_refresh_does_not(self):
        await self.finish_login()
        binding = self.manager.binding(HOST_ID, self.profile['id'], 'test-model')
        await self.finish_login()
        self.manager.authorize(HOST_ID, binding)
        await self.finish_login('two')
        with self.assertRaises(HTTPException):
            self.manager.authorize(HOST_ID, binding)
        await self.manager.disconnect(HOST_ID, self.profile['id'])
        self.assertFalse(self.manager.vault.path(self.profile['id']).exists())
    async def test_vault_tampering_and_identity_account_subject(self):
        vault = self.manager.vault
        vault.write(self.profile['id'], auth_bytes())
        path = vault.path(self.profile['id'])
        blob = bytearray(path.read_bytes()); blob[-1] ^= 1; path.write_bytes(blob)
        with self.assertRaises(ValueError):
            vault.read(self.profile['id'])
        self.assertNotEqual(identity(auth_bytes('one')), identity(auth_bytes('two')))
        self.assertEqual(identity(auth_bytes(refresh='new')), identity(auth_bytes()))
        self.assertEqual((self.root / 'credentials' / 'master.key').stat().st_mode & 0o777, 0o600)

    async def test_crash_between_vault_and_metadata_fails_before_checkout(self):
        await self.finish_login()
        binding = self.manager.binding(HOST_ID, self.profile['id'], 'test-model')
        self.manager.vault.write(self.profile['id'], auth_bytes('changed-account'))
        home = self.root / 'would-execute'
        with self.assertRaises(RuntimeError):
            async with self.manager.lease(binding, home):
                self.fail('changed credentials were supplied before verification')
        self.assertFalse((home / 'auth.json').exists())
        self.assertEqual(self.manager.get(self.profile['id'])['status'], 'error')

    async def test_expired_login_closes_process_and_removes_challenge(self):
        a = await self.manager.start_login(HOST_ID, self.profile['id'], 'chatgptDeviceCode')
        entry = self.manager.logins[a['id']]
        entry['attempt']['expires_at'] = time.time() - 1
        await entry['task']
        saved = self.store.get('login', a['id'])
        self.assertEqual(saved['status'], 'expired')
        self.assertIsNone(saved['challenge'])
        self.assertTrue(entry['adapter'].closed)
        self.assertFalse(entry['home'].exists())

if __name__ == '__main__':
    unittest.main()
