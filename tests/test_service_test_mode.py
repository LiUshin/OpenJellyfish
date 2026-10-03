"""Admin-only Service preview: core switching, hidden records and safe side effects."""

import json
import asyncio
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessageChunk

from app.deps import get_service_context
from app.routes.consumer import router as consumer_router
from app.routes.services import router as services_router
from app.runtime.consumer import service_authorizer
from app.runtime.consumer_tools import ServiceTools
from app.services.conversations import create_conversation, get_conversation, get_conversation_meta
from app.services.published import (create_service, create_service_key,
                                    get_consumer_conversation, list_consumer_conversations,
                                    update_service)
from app.services.service_test import review_context
from app.storage import get_storage_service
import test_runtime_chat as chat_tests


class _Agent:
    def __init__(self, content='Service preview answer'):
        self.content = content
    async def astream(self, *args, **kwargs):
        for chunk in (self.content if isinstance(self.content, list) else [self.content]):
            yield (), (AIMessageChunk(content=chunk), {})


class ServiceTestModeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = chat_tests.ChatTests.asyncSetUp
    asyncTearDown = chat_tests.ChatTests.asyncTearDown
    new = chat_tests.ChatTests.new
    until = chat_tests.ChatTests.until

    async def _setup(self):
        self.app.include_router(services_router)
        self.app.include_router(consumer_router)
        self.runs.authorize = service_authorizer(self.profiles)
        self.runs.tool_bridge = ServiceTools(self.storage, self.store, self.runs.authorize)
        binding = self.profiles.binding('alice', self.pid, 'model', 'off')
        deep = create_service('alice', {'name': 'Deep Service', 'model': 'fixture',
                                       'allowed_docs': [], 'allowed_scripts': [],
                                       'capabilities': [], 'published': False,
                                       'runtime_choice': {'runtime': 'deepagents'}})
        cli = create_service('alice', {'name': 'CLI Service', 'model': 'model',
                                      'allowed_docs': [], 'allowed_scripts': [],
                                      'capabilities': [], 'published': True,
                                      'runtime_choice': {'runtime': 'codex',
                                                         'profile_id': self.pid, 'model': 'model'},
                                      'runtime_binding': binding})
        key = create_service_key('alice', cli['id'])['key']
        return deep, cli, key

    async def test_admin_core_switches_to_draft_deep_service_then_reviews(self):
        deep, _, _ = await self._setup()
        conv = await self.new()  # Admin chat is Codex; Service is DeepAgent.
        r = await self.client.patch(f"/api/conversations/{conv['id']}/test-mode",
                                    json={'service_id': deep['id']})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['test_service_id'], deep['id'])
        preview_id = r.json()['test_consumer_conversation_id']
        self.assertEqual(list_consumer_conversations('alice', deep['id']), [])
        with patch('app.services.consumer_agent.create_consumer_agent', return_value=_Agent()):
            r = await self.client.post('/api/chat', json={
                'conversation_id': conv['id'], 'message': 'Please test this service'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('Service preview', r.text)
        self.assertEqual(list_consumer_conversations('alice', deep['id']), [])
        preview = get_consumer_conversation('alice', deep['id'], preview_id)
        self.assertEqual(preview['source'], 'admin_test')
        self.assertEqual([m['role'] for m in preview['messages']], ['user', 'assistant'])
        visible = get_conversation('alice', conv['id'])['messages']
        self.assertEqual([m['role'] for m in visible], ['user', 'assistant'])
        self.assertTrue(all(m.get('test_service_id') == deep['id'] for m in visible))

        # Preview artifacts remain Service-native internally, while the admin
        # bubble receives an owner-scoped URL that works after mode closes.
        self.storage.write_consumer_bytes('alice', deep['id'], preview_id,
                                          'images/asset.png', b'preview-pixels')
        with patch('app.services.consumer_agent.create_consumer_agent',
                   return_value=_Agent(['<<FI', 'LE:/generated/', 'images/asset', '.png>>'])):
            media_turn = await self.client.post('/api/chat', json={
                'conversation_id': conv['id'], 'message': 'make an image'})
        marker = f'<<FILE:/service-test/{conv["id"]}/{deep["id"]}/{preview_id}/generated/images/asset.png>>'
        rendered = ''.join(json.loads(line[6:]).get('content', '') for line in media_turn.text.splitlines()
                           if line.startswith('data: '))
        self.assertIn(marker, rendered)
        self.assertIn(marker, get_conversation('alice', conv['id'])['messages'][-1]['content'])
        self.assertIn('<<FILE:/generated/images/asset.png>>',
                      get_consumer_conversation('alice', deep['id'], preview_id)['messages'][-1]['content'])
        with patch('app.core.security.verify_token', return_value={'user_id': 'alice'}):
            media = await self.client.get(
                f'/api/conversations/{conv["id"]}/test-files/{deep["id"]}/{preview_id}/images/asset.png?token=admin-token')
        self.assertEqual(media.status_code, 200, media.text)
        self.assertEqual(media.content, b'preview-pixels')
        r = await self.client.patch(f"/api/conversations/{conv['id']}/test-mode",
                                    json={'service_id': None})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()['test_service_id'])
        self.assertIn('Service preview answer', review_context('alice', conv['id']))
        self.assertEqual((await self.client.post('/api/runtime/turns', json={
            'conversation_id': conv['id'], 'request_id': 'review00000000001',
            'message': 'Adjust the service'})).status_code, 200)
        self.assertEqual(get_conversation_meta('alice', conv['id'])['message_count'], 6)

    async def test_cli_service_scope_and_service_key_cannot_read_guessed_preview(self):
        _, cli, key = await self._setup()
        conv = create_conversation('alice')  # Admin chat is DeepAgent; Service is Codex.
        r = await self.client.patch(f"/api/conversations/{conv['id']}/test-mode",
                                    json={'service_id': cli['id']})
        self.assertEqual(r.status_code, 200, r.text)
        preview_id = r.json()['test_consumer_conversation_id']
        self.backend.gate.set()
        r = await self.client.post('/api/chat', json={
            'conversation_id': conv['id'], 'request_id': 'preview0000000001',
            'message': 'hello'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('first', r.text)
        self.assertEqual((await self.client.post('/api/runtime/turns', json={
            'conversation_id': conv['id'], 'request_id': 'bypass0000000001',
            'message': 'bypass'})).status_code, 409)
        self.assertEqual(list_consumer_conversations('alice', cli['id']), [])
        self.assertEqual((await self.client.get(
            f"/api/services/{cli['id']}/conversations/{preview_id}")).status_code, 404)
        headers = {'Authorization': f'Bearer {key}'}
        checks = [
            ('GET', f'/api/v1/conversations/{preview_id}', None),
            ('GET', f'/api/v1/conversations/{preview_id}/events', None),
            ('GET', f'/api/v1/conversations/{preview_id}/media-token', None),
            ('GET', f'/api/v1/conversations/{preview_id}/files', None),
            ('POST', '/api/v1/chat', {'conversation_id': preview_id, 'message': 'intrude'}),
            ('POST', '/api/v1/chat/completions', {'conversation_id': preview_id,
                       'messages': [{'role': 'user', 'content': 'intrude'}], 'stream': False}),
        ]
        for method, path, body in checks:
            with self.subTest(path=path):
                response = await self.client.request(method, path, headers=headers, json=body)
                self.assertEqual(response.status_code, 404, response.text)
        preview = get_consumer_conversation('alice', cli['id'], preview_id)
        self.assertEqual(len(preview['messages']), 2)
        from app.services.service_messaging import post_contact, create_notice_broadcast, list_conversation_events
        with self.assertRaises(KeyError):
            post_contact('alice', cli['id'], preview_id, 'must stay in admin chat')
        with self.assertRaises(KeyError):
            create_notice_broadcast('alice', cli['id'], [preview_id], 'must not deliver',
                                    idempotency_key='preview-broadcast')
        with self.assertRaises(KeyError):
            list_conversation_events('alice', cli['id'], preview_id)

        # The bound CLI tool must simulate contact rather than create a case.
        session = next(s for s in self.store.all('session') if s.get('conversation_id') == preview_id)
        run = next(r for r in self.store.all('run') if r['session_id'] == session['id'])
        with patch('app.services.service_messaging.post_contact', side_effect=AssertionError('real inbox')):
            result = await ServiceTools(self.storage, self.store, self.runs.authorize)(
                session, run, {'tool': 'jellyfish_service_contact_admin',
                               'arguments': {'message': 'please help'}})
        self.assertIn('preview_only', json.dumps(result))

        update_service('alice', cli['id'], {'description': 'changed'})
        # Description isn't execution-relevant; changing the model is.
        update_service('alice', cli['id'], {'model': 'another'})
        stale = await self.client.post('/api/chat', json={
            'conversation_id': conv['id'], 'request_id': 'preview0000000002', 'message': 'next'})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertIn('重新开启', stale.text)

    async def test_mode_switch_rejects_active_admin_or_preview_turn(self):
        _, cli, _ = await self._setup()
        conv = await self.new()
        self.backend.gate.clear()
        admin_run = await self.client.post('/api/runtime/turns', json={
            'conversation_id': conv['id'], 'request_id': 'adminrun000000001', 'message': 'work'})
        self.assertEqual(admin_run.status_code, 200, admin_run.text)
        blocked = await self.client.patch(f"/api/conversations/{conv['id']}/test-mode",
                                          json={'service_id': cli['id']})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.backend.gate.set()
        await self.until(lambda: self.store.get('run', admin_run.json()['id'])['status'] == 'completed')

        selected = await self.client.patch(f"/api/conversations/{conv['id']}/test-mode",
                                           json={'service_id': cli['id']})
        self.assertEqual(selected.status_code, 200, selected.text)
        self.backend.gate.clear()
        pending = asyncio.create_task(self.client.post('/api/chat', json={
            'conversation_id': conv['id'], 'request_id': 'previewrun000001', 'message': 'test'}))
        from app.services.service_test import active_test_conversations
        await self.until(lambda: conv['id'] in active_test_conversations('alice'))
        blocked = await self.client.patch(f"/api/conversations/{conv['id']}/test-mode",
                                          json={'service_id': None})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.backend.gate.set()
        self.assertEqual((await pending).status_code, 200)


if __name__ == '__main__':
    unittest.main()
