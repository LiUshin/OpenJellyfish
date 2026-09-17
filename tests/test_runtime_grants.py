from app.core.host_auth import HOST_ID
from fastapi import HTTPException
from test_runtime_profiles import ProfileFixture


class GrantTests(ProfileFixture):
    async def test_grants_bind_actor_model_generation_and_version(self):
        await self.finish_login()
        pid = self.profile['id']
        with self.assertRaises(HTTPException):
            self.manager.binding('alice', pid, 'test-model')
        g = self.manager.grant(HOST_ID, pid, 'alice', ['test-model'])
        binding = self.manager.binding('alice', pid, 'test-model')
        self.manager.authorize('alice', binding)
        self.assertEqual(binding['credential_owner_id'], HOST_ID)
        self.assertEqual(binding['grant_id'], g['id'])
        with self.assertRaises(HTTPException):
            self.manager.authorize('bob', binding)
        with self.assertRaises(HTTPException):
            self.manager.binding('alice', pid, 'other-model')
        public = self.manager.list('alice')[0]
        self.assertEqual(public['source'], 'owner_shared')
        self.assertFalse(public['can_manage'])
        self.assertNotIn('account', public)
        self.assertNotIn('login_id', public)
        self.manager.grant(HOST_ID, pid, 'alice', ['test-model'])
        with self.assertRaises(HTTPException):
            self.manager.authorize('alice', binding)
    async def test_account_switch_requires_explicit_regrant(self):
        await self.finish_login()
        pid = self.profile['id']
        self.manager.grant(HOST_ID, pid, 'alice', ['test-model'])
        await self.finish_login('second-account')
        self.assertEqual(self.manager.list('alice')[0]['status'], 'authorization_required')
        with self.assertRaises(HTTPException):
            self.manager.binding('alice', pid, 'test-model')
        self.manager.grant(HOST_ID, pid, 'alice', ['test-model'])
        self.manager.binding('alice', pid, 'test-model')
    async def test_revoke_invalidates_before_process_cleanup(self):
        await self.finish_login()
        pid = self.profile['id']
        g = self.manager.grant(HOST_ID, pid, 'alice', ['test-model'])
        binding = self.manager.binding('alice', pid, 'test-model')
        manager = self.manager
        class Runs:
            async def cancel_profile(self, profile_id, actor_id):
                assert profile_id == pid and actor_id == 'alice'
                try:
                    manager.authorize('alice', binding)
                except HTTPException:
                    return
                raise AssertionError('permission not revoked before cleanup')
        self.manager.runs = Runs()
        await self.manager.revoke(HOST_ID, pid, g['id'])
        self.assertEqual(self.manager.list('alice'), [])
