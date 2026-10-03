"""Service WeChat ingress/egress regressions; local ASGI and fake iLink only."""
import asyncio
import contextlib
import importlib
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from app.channels.wechat import policy
from app.channels.wechat.client import ILinkClient, ILinkAPIError, ensure_ilink_success
from app.channels.wechat.delivery import deliver_tool_message, WeChatDeliveryError
from app.channels.wechat.session_manager import WeChatSessionManager
from app.deps import get_current_user
from app.services import published

wc_router = importlib.import_module('app.channels.wechat.router')
admin_router = importlib.import_module('app.channels.wechat.admin_router')


class FakeClient:
    def __init__(self, **kwargs):
        self.close = AsyncMock()
        self.get_updates = AsyncMock(return_value=[])
        self.send_text = AsyncMock(return_value={'ret': 0})
        self.send_image = AsyncMock(return_value={'ret': 0})
        self.updates_buf = ''
        self.authorize_send = None


class WeChatPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.owners_path = Path(self.temp.name, 'users.json')
        self.owners = {'alice': {'username': 'Alice'}, 'bob': {'username': 'Bob'}}
        self.owners_path.write_text(json.dumps(self.owners))
        self.manager = WeChatSessionManager()
        self.patches = [patch('app.core.security.USERS_DIR', self.temp.name),
                        patch('app.core.security.USERS_JSON', str(self.owners_path)),
                        patch('app.channels.wechat.session_manager._manager', self.manager),
                        patch('app.channels.wechat.session_manager.ILinkClient', FakeClient),
                        patch('app.channels.wechat.rate_limiter.check_qr_rate', return_value=(True, 'ok')),
                        patch.object(admin_router, '_admin_sessions', {}),
                        patch.object(admin_router, '_admin_qr_challenges', {}),
                        patch.object(admin_router, '_admin_confirm_locks', {}),
                        patch.object(admin_router, 'ILinkClient', FakeClient),
                        patch.object(admin_router, '_start_admin_polling')]
        for p in self.patches: p.start()
        self.service = self.new_service()
        self.sid = self.service['id']
        wc_router._qr_challenges.clear()
        app = FastAPI(); app.include_router(wc_router.router)
        app.dependency_overrides[get_current_user] = lambda: {'user_id': 'alice'}
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture')

    async def asyncTearDown(self):
        await self.manager.stop_service_polling('alice', self.sid)
        await self.manager.shutdown()
        await self.http.aclose()
        wc_router._qr_challenges.clear()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def new_service(self, owner='alice', maximum=10):
        return published.create_service(owner, {'name': 'fixture', 'model': 'fixture', 'published': True,
            'capabilities': [], 'wechat_channel': {'enabled': True, 'max_sessions': maximum}})

    def set_channel(self, **fields):
        svc = published.get_service('alice', self.sid)
        published.update_service('alice', self.sid, {'wechat_channel': {**svc['wechat_channel'], **fields}})

    async def session(self, token='token', user='user', service=None, owner='alice'):
        return await self.manager.create_session(owner, service or self.sid, token, user, 'bot')

    async def issue(self, qr='qr', service=None):
        with patch.object(wc_router, 'generate_qrcode', AsyncMock(return_value={
            'qr_id': qr, 'qr_url': 'https://fixture/scan', 'qr_image_png': b'fake'})):
            response = await self.http.get(f'/api/wc/{service or self.sid}/qrcode')
        self.assertEqual(response.status_code, 200, response.text)
        return qr

    @staticmethod
    def confirmed(qr='qr'):
        return {'status': 'confirmed', 'bot_token': 'token-' + qr,
                'ilink_user_id': 'user-' + qr, 'ilink_bot_id': 'bot'}

    async def test_qr_must_be_issued_for_same_service_and_not_expired(self):
        other = self.new_service()
        await self.issue()
        with patch.object(wc_router, 'poll_qrcode_status', AsyncMock()) as poll:
            for sid, qr in [(other['id'], 'qr'), (self.sid, 'foreign')]:
                response = await self.http.get(f'/api/wc/{sid}/qrcode/status', params={'qrcode': qr})
                self.assertEqual(response.status_code, 404)
            wc_router._qr_challenges['qr']['expires_at'] = time.monotonic() - 1
            response = await self.http.get(f'/api/wc/{self.sid}/qrcode/status', params={'qrcode': 'qr'})
            self.assertEqual(response.status_code, 410)
            poll.assert_not_awaited()

    async def test_qr_rechecks_disable_during_provider_wait(self):
        await self.issue()
        async def provider(_):
            self.set_channel(enabled=False)
            return self.confirmed()
        with patch.object(wc_router, 'poll_qrcode_status', provider):
            r = await self.http.get(f'/api/wc/{self.sid}/qrcode/status', params={'qrcode': 'qr'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.manager.list_sessions(), [])

    async def test_owner_disabled_or_deleted_blocks_qr_session_and_agent_admission(self):
        from app.services.consumer_agent import create_consumer_agent
        await self.issue()
        for missing in (False, True):
            with self.subTest(missing=missing):
                owners = {} if missing else {'alice': {'username': 'Alice', 'disabled': True}}
                self.owners_path.write_text(json.dumps(owners))
                with patch.object(wc_router, 'generate_qrcode', AsyncMock()) as generate, \
                     patch.object(wc_router, 'poll_qrcode_status', AsyncMock()) as poll:
                    generated = await self.http.get(f'/api/wc/{self.sid}/qrcode')
                    confirmed = await self.http.get(f'/api/wc/{self.sid}/qrcode/status', params={'qrcode': 'qr'})
                self.assertEqual(generated.status_code, 403)
                self.assertEqual(confirmed.status_code, 403)
                generate.assert_not_awaited(); poll.assert_not_awaited()
                with self.assertRaises(HTTPException) as admission:
                    await self.session()
                self.assertEqual(admission.exception.status_code, 403)
                with self.assertRaises(HTTPException) as agent:
                    create_consumer_agent('alice', self.sid, 'no-model-called')
                self.assertEqual(agent.exception.status_code, 403)
                self.assertEqual(self.manager.list_sessions(), [])

    async def test_owner_disable_during_provider_wait_blocks_qr_attach_and_inbound_dispatch(self):
        session = await self.session()
        await self.issue()
        def disable_owner():
            self.owners['alice']['disabled'] = True
            self.owners_path.write_text(json.dumps(self.owners))
        async def confirm(_):
            disable_owner()
            return self.confirmed()
        with patch.object(wc_router, 'poll_qrcode_status', confirm):
            response = await self.http.get(f'/api/wc/{self.sid}/qrcode/status', params={'qrcode': 'qr'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self.manager.list_sessions()), 1)
        self.owners['alice']['disabled'] = False
        self.owners_path.write_text(json.dumps(self.owners))
        handler = AsyncMock(); self.manager.set_message_handler(handler)
        async def updates():
            disable_owner()
            return [{'from_user_id': 'user', 'item_list': []}]
        self.manager.get_client(session.session_id).get_updates = updates
        await self.manager._poll_loop(session.session_id)
        handler.assert_not_awaited()

    async def test_owner_revocation_stops_real_protocol_send_before_transport(self):
        requests = []
        def response(request):
            requests.append(request)
            return httpx.Response(200, json={'ret': 0})
        transport = httpx.AsyncClient(transport=httpx.MockTransport(response))
        with patch('app.channels.wechat.session_manager.ILinkClient', ILinkClient), \
             patch('app.channels.wechat.client._create_http_client', return_value=transport):
            session = await self.session()
        client = self.manager.get_client(session.session_id)
        await client.send_text('user', 'allowed', 'context')
        self.assertEqual(len(requests), 1)
        for owners in ({'alice': {'username': 'Alice', 'disabled': True}}, {}):
            self.owners_path.write_text(json.dumps(owners))
            with self.assertRaises(HTTPException) as blocked:
                await client.send_text('user', 'must not leave process', 'context')
            self.assertEqual(blocked.exception.status_code, 403)
            self.assertEqual(len(requests), 1)

    async def test_duplicate_confirmations_create_one_session(self):
        await self.issue()
        with patch.object(wc_router, 'poll_qrcode_status', AsyncMock(return_value=self.confirmed())) as poll:
            first, second = await asyncio.gather(*[self.http.get(f'/api/wc/{self.sid}/qrcode/status',
                                                        params={'qrcode': 'qr'}) for _ in range(2)])
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(self.manager.list_sessions()), 1)
        self.assertEqual(poll.await_count, 1)

    async def test_concurrent_different_qr_confirmations_enforce_capacity(self):
        self.set_channel(max_sessions=1)
        await self.issue('one'); await self.issue('two')
        async def provider(qr):
            await asyncio.sleep(0)
            return self.confirmed(qr)
        with patch.object(wc_router, 'poll_qrcode_status', provider):
            responses = await asyncio.gather(*[self.http.get(f'/api/wc/{self.sid}/qrcode/status',
                                                 params={'qrcode': qr}) for qr in ('one', 'two')])
        self.assertEqual(sorted(r.status_code for r in responses), [200, 403])
        self.assertEqual(len(self.manager.list_sessions()), 1)
        active = self.manager.list_sessions()[0]
        self.assertIs(policy.ensure_wechat_session('alice', self.sid, active.session_id), active)
        succeeded = next(qr for qr, reply in zip(('one', 'two'), responses) if reply.status_code == 200)
        with patch.object(wc_router, 'poll_qrcode_status', AsyncMock(side_effect=AssertionError('must use receipt'))):
            retry = await self.http.get(f'/api/wc/{self.sid}/qrcode/status', params={'qrcode': succeeded})
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()['session_id'], active.session_id)

    async def test_cached_confirmation_cannot_revive_disabled_or_removed_session(self):
        await self.issue()
        with patch.object(wc_router, 'poll_qrcode_status', AsyncMock(return_value=self.confirmed())):
            first = await self.http.get(f'/api/wc/{self.sid}/qrcode/status', params={'qrcode': 'qr'})
            await self.manager.remove_session(first.json()['session_id'])
            second = await self.http.get(f'/api/wc/{self.sid}/qrcode/status', params={'qrcode': 'qr'})
        self.assertEqual(second.status_code, 403)
        self.assertEqual(self.manager.list_sessions(), [])

    async def test_policy_timezone_expiry_invalid_date_and_unpublish(self):
        self.set_channel(expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).astimezone(
            timezone(timedelta(hours=-7))).isoformat())
        policy.ensure_wechat_active('alice', self.sid)
        for expiry in ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), 'invalid'):
            self.set_channel(expires_at=expiry)
            with self.assertRaises(HTTPException): policy.ensure_wechat_active('alice', self.sid)
        self.set_channel(expires_at=None)
        published.update_service('alice', self.sid, {'published': False})
        with self.assertRaises(HTTPException): policy.ensure_service_active('alice', self.sid)
        from app.services.consumer_agent import create_consumer_agent
        with self.assertRaises(HTTPException): create_consumer_agent('alice', self.sid, 'no-model-called')

    async def test_policy_rejects_foreign_owner_service_conversation_and_deleted_history(self):
        s = await self.session()
        other = self.new_service()
        foreign = self.new_service(owner='bob')
        for owner, sid, cid in [('alice', other['id'], None), ('bob', foreign['id'], None),
                                 ('alice', self.sid, 'wrong')]:
            with self.assertRaises(HTTPException):
                policy.ensure_wechat_session(owner, sid, s.session_id, conversation_id=cid)
        published.delete_consumer_conversation('alice', self.sid, s.conversation_id)
        with self.assertRaises(HTTPException): policy.ensure_wechat_session('alice', self.sid, s.session_id)

    async def test_bot_token_cannot_attach_to_second_service(self):
        await self.session()
        other = self.new_service()
        with self.assertRaises(HTTPException): await self.session(service=other['id'])
        self.assertEqual(len(self.manager.list_sessions()), 1)

    async def test_disabled_channel_stops_waiting_poll_and_preserves_session(self):
        s = await self.session()
        started = asyncio.Event()
        async def wait():
            started.set(); await asyncio.Event().wait()
        self.manager.get_client(s.session_id).get_updates = wait
        self.manager.start_polling(s.session_id)
        await asyncio.wait_for(started.wait(), 1)
        task = self.manager._poll_tasks[s.session_id]
        r = await self.http.put(f'/api/wc/{self.sid}/config', json={'enabled': False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(task.done())
        self.assertIsNotNone(self.manager.get_session(s.session_id))

    async def test_poll_rechecks_policy_before_dispatch_after_network_wait(self):
        s = await self.session()
        handler = AsyncMock(); self.manager.set_message_handler(handler)
        async def provider():
            self.set_channel(enabled=False)
            return [{'from_user_id': 'user', 'item_list': []}]
        self.manager.get_client(s.session_id).get_updates = provider
        await self.manager._poll_loop(s.session_id)
        handler.assert_not_awaited()

    async def test_missing_media_and_negative_ack_are_failures(self):
        s = await self.session(); s.from_user_id = 'user'; s.context_token = 'context'
        client = self.manager.get_client(s.session_id)
        storage = SimpleNamespace(read_consumer_bytes=lambda *a: (_ for _ in ()).throw(FileNotFoundError()))
        with patch('app.storage.get_storage_service', return_value=storage):
            with self.assertRaises(WeChatDeliveryError) as error:
                await deliver_tool_message(json.dumps({'text': '<<FILE:/generated/missing.png>>'}), s, client)
        self.assertEqual(error.exception.sent_count, 0)
        client.send_text.assert_not_awaited(); client.send_image.assert_not_awaited()
        client.send_text.return_value = {'ret': -1}
        with self.assertRaises(WeChatDeliveryError):
            await deliver_tool_message(json.dumps({'text': 'hello'}), s, client)

    async def test_deleted_conversation_is_not_recreated_by_stream_finally(self):
        from langchain_core.messages import AIMessageChunk
        from app.channels.wechat.bridge import _run_agent_and_reply
        session = await self.session()
        async def stream(*args, **kwargs):
            yield (), (AIMessageChunk(content='partial'), {})
            published.delete_consumer_conversation('alice', self.sid, session.conversation_id)
            raise RuntimeError('fixture deletion during stream')
        @contextlib.asynccontextmanager
        async def active(*args, **kwargs):
            yield
        with patch('app.channels.wechat.bridge.create_consumer_agent', return_value=SimpleNamespace(astream=stream)), \
             patch('app.channels.wechat.bridge.build_usage_callbacks', return_value=[]), \
             patch('app.services.scheduled_inject.thread_active', active):
            with self.assertRaises(RuntimeError):
                await _run_agent_and_reply(session, self.manager.get_client(session.session_id), 'user', 'ctx', 'hello')
        self.assertFalse(published.consumer_conversation_exists('alice', self.sid, session.conversation_id))

    async def test_partial_delivery_and_revocation_are_not_success(self):
        s = await self.session(); s.from_user_id = 'user'; s.context_token = 'context'
        client = self.manager.get_client(s.session_id)
        storage = SimpleNamespace(read_consumer_bytes=lambda *a: (_ for _ in ()).throw(FileNotFoundError()))
        with patch('app.storage.get_storage_service', return_value=storage):
            with self.assertRaises(WeChatDeliveryError) as error:
                await deliver_tool_message(json.dumps({'text': 'hello <<FILE:/missing.png>>'}), s, client)
        self.assertEqual((error.exception.sent_count, error.exception.failed_count), (1, 1))
        client.send_text.reset_mock(); self.set_channel(enabled=False)
        with self.assertRaises(HTTPException):
            await deliver_tool_message(json.dumps({'text': 'forbidden'}), s, client)
        client.send_text.assert_not_awaited()

    async def test_admin_qr_is_owner_bound_and_confirmation_is_idempotent(self):
        with patch.object(admin_router, 'generate_qrcode', AsyncMock(return_value={
            'qr_id': 'admin-qr', 'qr_url': 'https://fixture/scan', 'qr_image_png': b'fake'})):
            await admin_router.api_admin_qrcode(user={'user_id': 'alice'})
        with patch.object(admin_router, 'poll_qrcode_status', AsyncMock(return_value=self.confirmed())) as poll:
            with self.assertRaises(HTTPException) as foreign:
                await admin_router.api_admin_qrcode_status('admin-qr', user={'user_id': 'bob'})
            self.assertEqual(foreign.exception.status_code, 404)
            poll.assert_not_awaited()
            a, b = await asyncio.gather(*[admin_router.api_admin_qrcode_status(
                'admin-qr', user={'user_id': 'alice'}) for _ in range(2)])
        self.assertEqual(a, b)
        self.assertEqual(poll.await_count, 1)
        self.assertEqual(list(admin_router._admin_sessions), ['alice'])
        client = admin_router._admin_sessions['alice']['client']
        client.authorize_send()
        await admin_router.api_admin_disconnect(user={'user_id': 'alice'})
        with self.assertRaises(HTTPException): client.authorize_send()
        with self.assertRaises(HTTPException):
            await admin_router.api_admin_qrcode_status('admin-qr', user={'user_id': 'alice'})

    async def test_admin_disconnect_invalidates_inflight_confirmation(self):
        with patch.object(admin_router, 'generate_qrcode', AsyncMock(return_value={
            'qr_id': 'admin-qr', 'qr_url': 'https://fixture/scan', 'qr_image_png': b'fake'})):
            await admin_router.api_admin_qrcode(user={'user_id': 'alice'})
        async def provider(_):
            await admin_router.api_admin_disconnect(user={'user_id': 'alice'})
            return self.confirmed()
        with patch.object(admin_router, 'poll_qrcode_status', provider):
            with self.assertRaises(HTTPException) as expired:
                await admin_router.api_admin_qrcode_status('admin-qr', user={'user_id': 'alice'})
        self.assertEqual(expired.exception.status_code, 410)
        self.assertEqual(admin_router._admin_sessions, {})

    async def test_real_ilink_client_rejects_200_error_ack_and_checks_before_send(self):
        requests = []
        def response(request):
            requests.append(request)
            return httpx.Response(200, json={'ret': -14, 'errmsg': 'fixture'})
        transport = httpx.AsyncClient(transport=httpx.MockTransport(response))
        with patch('app.channels.wechat.client._create_http_client', return_value=transport):
            client = ILinkClient('fake', 'fake', 'fake')
        try:
            with self.assertRaises(ILinkAPIError) as rejected: await client.send_text('u', 'hello', 'ctx')
            self.assertTrue(rejected.exception.explicit_rejection)
            client.authorize_send = lambda: (_ for _ in ()).throw(HTTPException(403, 'revoked'))
            with self.assertRaises(HTTPException): await client.send_text('u', 'hello', 'ctx')
            self.assertEqual(len(requests), 1)
        finally:
            await client.close()

    async def test_missing_ack_is_unknown_but_negative_ack_is_explicit(self):
        for payload in (None, {}, {'message': 'no receipt'}):
            with self.assertRaises(ILinkAPIError) as unknown:
                ensure_ilink_success(payload, require_ack=True)
            self.assertFalse(unknown.exception.explicit_rejection)
        with self.assertRaises(ILinkAPIError) as refused:
            ensure_ilink_success({'errcode': 40001}, require_ack=True)
        self.assertTrue(refused.exception.explicit_rejection)
        ensure_ilink_success({'ret': 0}, require_ack=True)


if __name__ == '__main__': unittest.main()
