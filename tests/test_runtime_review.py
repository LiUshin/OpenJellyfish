from app.core.host_auth import HOST_ID
"""Regressions found in the PR 00–06 self review."""
import asyncio
import os
import runpy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from app.runtime.rpc import RuntimeFailure
from app.runtime.policy import DeploymentPolicy
from app.runtime.store import RuntimeStore
from test_runtime_profiles import ProfileFixture, AuthAdapter, auth_bytes


class ProfileReviewTests(ProfileFixture):
    async def test_duplicate_identity_cannot_bypass_account_serialization(self):
        await self.finish_login()
        duplicate = self.manager.create(HOST_ID, 'Duplicate')
        with self.assertRaises(RuntimeFailure):
            self.manager.save_auth(duplicate, auth_bytes())
        self.assertFalse(self.manager.vault.path(duplicate['id']).exists())

    async def test_disconnect_preserves_fence_set_during_shutdown(self):
        await self.finish_login()
        async def cleanup_failed(pid):
            p = self.manager.get(pid)
            p['recovery_required'] = True
            self.store.put('profile', p)
        self.manager.runs = type('Runs', (), {'cancel_profile': staticmethod(cleanup_failed)})()
        await self.manager.disconnect(HOST_ID, self.profile['id'])
        self.assertTrue(self.manager.get(self.profile['id'])['recovery_required'])
        with self.assertRaises(HTTPException):
            await self.manager.start_login(HOST_ID, self.profile['id'], 'chatgptDeviceCode')

    async def test_login_cleanup_failure_finishes_attempt_and_fences_profile(self):
        a = await self.manager.start_login(HOST_ID, self.profile['id'], 'chatgptDeviceCode')
        entry = self.manager.logins[a['id']]
        entry['adapter'].close = AsyncMock(side_effect=RuntimeFailure('test process still alive'))
        await asyncio.sleep(0)
        await self.manager.cancel_login(HOST_ID, self.profile['id'], a['id'])
        self.assertFalse(self.manager.logins)
        self.assertTrue(self.manager.get(self.profile['id'])['recovery_required'])
        saved = self.store.get('login', a['id'])
        self.assertNotEqual(saved['status'], 'pending')
        self.assertIsNone(saved['challenge'])
        self.assertTrue(entry['home'].exists())

    async def test_login_start_failure_does_not_leak_global_reservation(self):
        original = AuthAdapter.request
        async def fail_start(adapter, method, params, **kwargs):
            if method == 'account/login/start':
                raise RuntimeFailure('test login failed')
            return await original(adapter, method, params, **kwargs)
        with patch.object(AuthAdapter, 'request', fail_start), patch.object(AuthAdapter, 'close', AsyncMock(side_effect=RuntimeFailure('test close failed'))):
            with self.assertRaises(RuntimeFailure):
                await self.manager.start_login(HOST_ID, self.profile['id'], 'chatgptDeviceCode')
        self.assertFalse(self.manager.logins)
        self.assertTrue(self.manager.get(self.profile['id'])['recovery_required'])

    async def test_probe_cleanup_failure_fences_connection(self):
        await self.finish_login()
        with patch.object(AuthAdapter, 'close', AsyncMock(side_effect=RuntimeFailure('test close failed'))):
            with self.assertRaises(RuntimeFailure):
                await self.manager.probe(HOST_ID, self.profile['id'])
        self.assertTrue(self.manager.get(self.profile['id'])['recovery_required'])

    async def test_failed_process_close_never_checks_credentials_back_in(self):
        from app.runtime.backend import LocalBackend
        await self.finish_login()
        binding = self.manager.binding(HOST_ID, self.profile['id'], 'test-model')
        backend = LocalBackend(self.root, self.manager, 'fake', adapter_factory=AuthAdapter)
        session = {'id':'session','actor_id':'actor','binding':binding}
        with patch.object(AuthAdapter, 'close', AsyncMock(side_effect=RuntimeFailure('test close failed'))):
            with self.assertRaises(RuntimeFailure):
                async with backend.execution(session):
                    home = backend.session_dir(session) / 'home'
                    (home / 'auth.json').write_bytes(auth_bytes(refresh='unconfirmed-refresh'))
        self.assertEqual(self.manager.vault.read(self.profile['id']), auth_bytes())
        self.assertFalse((home / 'auth.json').exists())
        self.assertTrue(self.manager.get(self.profile['id'])['recovery_required'])
        self.assertIn('active_lease', self.manager.get(self.profile['id']))

    async def test_host_recovery_checks_login_pid_before_clearing_fence(self):
        from types import SimpleNamespace
        from scripts.runtime_admin import main
        from app.runtime.backend import LocalBackend
        await self.finish_login()
        p = self.manager.get(self.profile['id'])
        home = self.root / 'probe' / 'crashed'
        home.mkdir(parents=True)
        (home / 'auth.json').write_bytes(auth_bytes())
        p.update(recovery_required=True, active_lease={'home':'probe/crashed'})
        self.store.put('profile',p)
        self.store.put('login', {'id':'crashed-login','profile_id':p['id'],'status':'failed','cleanup_required':True,'process_pid':os.getpid()})
        manager = SimpleNamespace(profiles=self.manager,store=self.store,
                                  backend=LocalBackend(self.root,self.manager,'fake'),shutdown=AsyncMock())
        args = SimpleNamespace(command='recover',profile_id=p['id'],confirm_stopped=True)
        with patch('app.runtime.manager.get_runtime', return_value=manager):
            with self.assertRaises(ValueError):
                await main(args)
            self.assertTrue(self.manager.get(p['id'])['recovery_required'])
            self.assertTrue(self.manager.vault.path(p['id']).exists())
            with patch('os.kill', side_effect=ProcessLookupError), patch('builtins.print'):
                await main(args)
        saved = self.manager.get(p['id'])
        self.assertFalse(saved['recovery_required'])
        self.assertNotIn('active_lease',saved)
        self.assertEqual(saved['status'],'disconnected')
        self.assertFalse((home/'auth.json').exists())
        self.assertFalse(self.manager.vault.path(p['id']).exists())


class RestartReviewTests(unittest.TestCase):
    def test_pending_login_restart_clears_challenge_and_fences_process(self):
        with tempfile.TemporaryDirectory() as path:
            store = RuntimeStore(Path(path))
            store.put('profile', {'id': 'p', 'recovery_required': False})
            store.put('login', {'id': 'login', 'profile_id': 'p', 'status': 'pending', 'challenge': {'user_code': 'TEST'}})
            store.close()
            store = RuntimeStore(Path(path))
            try:
                self.assertTrue(store.get('profile','p')['recovery_required'])
                self.assertIsNone(store.get('login','login')['challenge'])
            finally:
                store.close()

    def test_probe_lease_is_fenced_after_restart_without_a_run(self):
        with tempfile.TemporaryDirectory() as path:
            store = RuntimeStore(Path(path))
            store.put('profile', {'id':'p', 'active_lease':{'home':'probe/attempt'}})
            store.close()
            store = RuntimeStore(Path(path))
            try:
                self.assertTrue(store.get('profile','p')['recovery_required'])
            finally:
                store.close()

    def test_store_import_does_not_require_posix_when_runtime_is_disabled(self):
        with patch.dict(sys.modules, {'fcntl': None}):
            runpy.run_path(str(Path(__file__).parents[1] / 'app/runtime/store.py'))
        with patch.dict(os.environ, {'JELLYFISH_OWNER_USER_ID':HOST_ID}), patch('sys.platform','win32'):
            with self.assertRaises(HTTPException):
                DeploymentPolicy().ensure_supported()

    def test_history_explicitly_closes_read_connection(self):
        from app.runtime.chat import history
        connections = []
        real_connect = sqlite3.connect
        class Connection(sqlite3.Connection):
            closed = False
            def close(self):
                self.closed = True
                super().close()
        def connect(*args, **kwargs):
            result = real_connect(*args, **kwargs, factory=Connection)
            connections.append(result)
            return result
        with tempfile.TemporaryDirectory() as path:
            store = RuntimeStore(Path(path))
            try:
                with patch.dict(os.environ, {'JELLYFISH_RUNTIME_DATA_DIR':path}), patch('sqlite3.connect', connect):
                    self.assertEqual(history('actor','session'), [])
                self.assertTrue(connections[0].closed)
            finally:
                for connection in connections: connection.close()
                store.close()
