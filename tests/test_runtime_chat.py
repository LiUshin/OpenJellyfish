from app.core.host_auth import HOST_ID
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import httpx
from fastapi import FastAPI, Header, HTTPException
from app.deps import get_current_user
from app.routes.runtime import router as runtime_router
from app.routes.conversations import router as conversations_router
from app.routes.chat import router as chat_router
from app.routes.files import router as files_router
from app.routes.voice_live import router as voice_router, get_voice_worker_session
from app.runtime.manager import get_runtime
from app.runtime.profiles import ProfileManager
from app.runtime.policy import DeploymentPolicy
from app.runtime.store import RuntimeStore
from app.runtime.service import RunService
from app.runtime.business_tools import BusinessTools, Document, Empty, MemoryWrite, ServiceDocument
from app.runtime.files import digest
from app.storage.local import LocalStorageService
from test_runtime_service import FakeBackend
from test_runtime_profiles import auth_bytes


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.users = self.root / 'users'; self.users.mkdir()
        (self.users / 'users.json').write_text(json.dumps({uid: {'username': uid} for uid in ['owner','alice','bob']}))
        self.patches = [patch.dict(os.environ, {'JELLYFISH_OWNER_USER_ID': 'owner', 'JELLYFISH_RUNTIME_ENABLED': '1', 'JELLYFISH_RUNTIME_DATA_DIR': str(self.root / 'runtime')}),
                        patch('app.core.security.USERS_DIR', str(self.users)), patch('app.core.security.USERS_JSON', str(self.users / 'users.json')),
                        patch('app.storage.local.USERS_DIR', str(self.users))]
        for p in self.patches: p.start()
        self.store = RuntimeStore(self.root / 'runtime')
        self.storage = LocalStorageService()
        self.patches.append(patch('app.storage._storage_service', self.storage)); self.patches[-1].start()
        self.backend = FakeBackend(self.root / 'work')
        self.profiles = ProfileManager(self.store, DeploymentPolicy(), 'fake')
        self.runs = RunService(self.store, DeploymentPolicy(), self.backend, self.profiles.authorize, storage=self.storage)
        self.profiles.runs = self.runs
        self.manager = SimpleNamespace(store=self.store, profiles=self.profiles, runs=self.runs, backend=self.backend)
        self.patches.append(patch('app.runtime.manager._manager', self.manager)); self.patches[-1].start()
        p = self.profiles.create(HOST_ID, 'Shared'); p['models'] = [{'id': 'model', 'name': 'Model'}]
        self.profiles.save_auth(p, auth_bytes()); self.pid = p['id']
        self.profiles.grant(HOST_ID, self.pid, 'alice', ['model'])
        self.app = FastAPI(); self.app.include_router(runtime_router); self.app.include_router(conversations_router); self.app.include_router(chat_router); self.app.include_router(voice_router); self.app.include_router(files_router)
        def actor(authorization: str = Header()):
            uid = authorization.removeprefix('Bearer ')
            if uid not in ('owner','alice','bob'): raise HTTPException(401)
            return {'user_id': uid, 'username': uid}
        self.app.dependency_overrides[get_current_user] = actor
        self.app.dependency_overrides[get_runtime] = lambda: self.manager
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test', headers={'Authorization': 'Bearer alice'})
    async def asyncTearDown(self):
        await self.runs.shutdown(); await self.profiles.shutdown(); await self.client.aclose()
        self.store.close()
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()
    async def new(self):
        r = await self.client.post('/api/conversations', json={'runtime_choice': {'runtime': 'codex', 'profile_id': self.pid, 'model': 'model'}})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()
    async def until(self, check):
        for _ in range(200):
            if check(): return
            await asyncio.sleep(.01)
        self.fail('timed out')
    async def test_main_chat_snapshot_replay_and_cross_actor_denials(self):
        conv = await self.new()
        self.assertEqual(conv['runtime_binding']['credential_owner_id'], HOST_ID)
        r = await self.client.post('/api/runtime/turns', json={'conversation_id': conv['id'], 'request_id': 'request000000001', 'message': 'hello'})
        self.assertEqual(r.status_code, 200, r.text); rid = r.json()['id']
        self.backend.gate.set()
        await self.until(lambda: self.store.get('run', rid)['status'] == 'completed')
        for endpoint in [f"/api/runtime/sessions/{conv['runtime_session_id']}", f'/api/runtime/runs/{rid}/events', f"/api/conversations/{conv['id']}"]:
            self.assertEqual((await self.client.get(endpoint, headers={'Authorization':'Bearer bob'})).status_code, 404)
        self.assertEqual((await self.client.post(f'/api/runtime/runs/{rid}/cancel', headers={'Authorization':'Bearer bob'})).status_code, 404)
        history = (await self.client.get('/api/conversations/' + conv['id'])).json()
        self.assertEqual([m['content'] for m in history['messages']], ['hello','first'])
        last = self.store.get('run', rid)['seq']
        replay = await self.client.get(f'/api/runtime/runs/{rid}/events?after={last-1}')
        self.assertIn('completed', replay.text)
        self.assertNotIn('text_delta', replay.text)
        # Updating defaults cannot move the existing conversation to DeepAgents.
        await self.client.put('/api/runtime/preferences', json={'runtime':'deepagents'})
        self.assertEqual((await self.client.get('/api/conversations/' + conv['id'])).json()['runtime_binding']['runtime'], 'codex')
    async def test_chat_endpoint_dispatches_codex_and_never_builds_deepagent(self):
        conv = await self.new(); self.backend.gate.set()
        with patch('app.routes.chat._create_user_agent_bounded', new_callable=AsyncMock) as legacy:
            r = await self.client.post('/api/chat', json={'conversation_id':conv['id'], 'request_id':'request000000002', 'message':'hello'})
            self.assertEqual(r.status_code, 200, r.text); self.assertIn('first', r.text); self.assertIn('done', r.text)
            legacy.assert_not_called()
        self.assertEqual((await self.client.post('/api/chat/resume', json={'conversation_id':conv['id'], 'decisions':[]})).status_code, 409)
    async def test_voice_delegate_uses_bound_cli_run_and_chat_history(self):
        conv = await self.new(); self.backend.gate.set()
        self.app.dependency_overrides[get_voice_worker_session] = lambda: {
            'admin_id': 'alice', 'conv_id': conv['id'], 'model': 'model',
        }
        with patch('app.routes.chat._create_user_agent_bounded', new_callable=AsyncMock) as legacy:
            response = await self.client.post('/api/voice/live/delegate', json={'message': 'voice hello'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('first', response.text)
            self.assertIn('done', response.text)
            legacy.assert_not_called()
        runs = self.store.find('run', session_id=conv['runtime_session_id'], actor_id='alice')
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['message'], 'voice hello')
        self.assertEqual(runs[0]['channel'], 'voice')
        self.assertTrue(runs[0]['yolo'])
        history = (await self.client.get('/api/conversations/' + conv['id'])).json()
        self.assertEqual([m['content'] for m in history['messages']], ['voice hello', 'first'])
    async def test_voice_delegate_preserves_legacy_graph_and_rejects_invalid_binding(self):
        from app.services.conversations import create_conversation, get_conversation, _write_meta
        conv = create_conversation('alice')
        self.app.dependency_overrides[get_voice_worker_session] = lambda: {
            'admin_id': 'alice', 'conv_id': conv['id'], 'model': 'old-model',
            'capabilities': ['web'],
        }
        async def legacy_stream(*args, **kwargs):
            yield 'data: {"type":"token","content":"legacy voice"}\n\n'
            yield 'data: {"type":"done"}\n\n'
        with patch('app.routes.chat._create_user_agent_bounded', new_callable=AsyncMock) as build, patch('app.routes.chat._stream_agent', legacy_stream):
            response = await self.client.post('/api/voice/live/delegate', json={'message': 'hello'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('legacy voice', response.text)
            self.assertEqual(build.call_args.kwargs['model'], 'old-model')
            self.assertEqual(build.call_args.kwargs['capabilities'], ['web'])
        self.assertEqual(get_conversation('alice', conv['id'])['messages'][0]['content'], 'hello')
        _write_meta('alice', conv['id'], {**conv, 'runtime_binding': {'runtime': 'cursor'}})
        with patch('app.routes.chat._create_user_agent_bounded', new_callable=AsyncMock) as legacy:
            response = await self.client.post('/api/voice/live/delegate', json={'message': 'again'})
            self.assertEqual(response.status_code, 409, response.text)
            legacy.assert_not_called()
    async def test_voice_token_requires_an_owned_conversation(self):
        from app.services.conversations import create_conversation
        conv = create_conversation('bob')
        with patch('app.routes.voice_live.is_livekit_configured', return_value=True), \
             patch('app.routes.voice_live.livekit_server_config', return_value={'url': 'wss://example.test', 'api_key': 'key', 'api_secret': 'secret'}), \
             patch('app.routes.voice_live.mint_livekit_token', return_value='signed') as mint:
            response = await self.client.post('/api/voice/live/token', json={'conversation_id': conv['id']})
            self.assertEqual(response.status_code, 404, response.text)
            mint.assert_not_called()
    async def test_legacy_without_binding_keeps_deepagent_entry(self):
        from app.services.conversations import create_conversation
        conv = create_conversation('alice')
        async def legacy_stream(*args, **kwargs):
            yield 'data: {"type":"token","content":"legacy"}\n\n'
            yield 'data: {"type":"done"}\n\n'
        with patch('app.routes.chat._create_user_agent_bounded', new_callable=AsyncMock) as build, \
             patch('app.routes.chat._stream_agent', legacy_stream), \
             patch('app.routes.chat.context_for_conversation', return_value='<current-project-brief>current</current-project-brief>'):
            r = await self.client.post('/api/chat', json={'conversation_id':conv['id'], 'message':'hello', 'model':'old-model'})
            self.assertEqual(r.status_code,200,r.text); self.assertIn('legacy',r.text)
            self.assertEqual(build.call_args.kwargs['model'], 'old-model')
            self.assertIn('current', build.call_args.kwargs['project_brief'])
    async def test_business_tools_document_memory_and_service_scope(self):
        bridge = BusinessTools(self.storage, self.profiles.authorize, self.store)
        self.storage.write_text('alice','/docs/public.txt','ALICE')
        self.storage.write_text('bob','/docs/public.txt','BOB')
        self.assertEqual(bridge.invoke('alice','jellyfish_read_document',Document(path='/docs/public.txt')), 'ALICE')
        for p in ['/docs/../secret', '/generated/private', '/docs/../../bob/filesystem/docs/public.txt']:
            with self.assertRaises(ValueError): bridge.invoke('alice','jellyfish_read_document',Document(path=p))
        with self.assertRaises(ValueError): Document.model_validate({'path':'/docs/public.txt','actor_id':'bob'})
        with patch('app.services.prompt.set_agent_notes') as write, patch('app.services.prompt.get_agent_notes',return_value='old'), patch('app.services.prompt.is_agent_notes_locked',return_value=False):
            bridge.invoke('alice','jellyfish_update_memory',MemoryWrite(content='new',previous_sha256=digest(b'old')))
            write.assert_called_once_with('alice','new')
            with self.assertRaises(ValueError): bridge.invoke('alice','jellyfish_update_memory',MemoryWrite(content='new',previous_sha256=digest(b'wrong')))
        service = {'id':'s1','admin_id':'alice','allowed_docs':['public.txt']}
        with patch('app.services.published.get_service', return_value=service) as get:
            self.assertEqual(bridge.invoke('alice','jellyfish_read_service_document', ServiceDocument(service_id='s1',path='/docs/public.txt')), 'ALICE')
            get.assert_called_with('alice','s1')
            with self.assertRaises(ValueError): bridge.invoke('alice','jellyfish_read_service_document',ServiceDocument(service_id='s1',path='/docs/private.txt'))
        with patch('app.services.published.get_service', return_value={**service,'admin_id':'bob'}):
            with self.assertRaises(ValueError): bridge.invoke('alice','jellyfish_read_service_document',ServiceDocument(service_id='s1',path='/docs/public.txt'))

    async def test_admin_document_write_is_visible_in_library_and_idempotent(self):
        bridge = BusinessTools(self.storage, self.profiles.authorize, self.store)
        binding = self.profiles.binding('alice', self.pid, 'model')
        session = {'actor_id': 'alice', 'binding': binding, 'conversation_id': 'admin-chat'}
        run = {'id': 'admin-document-write', 'actor_id': 'alice', 'binding': binding,
               'seq': 0, 'status': 'running', 'channel': 'web'}
        self.store.put('run', run)
        path = '/docs/菜单/寻味江南-2026年十月菜单.md'
        content = '# 寻味江南\n\n桂花糖藕。\n'

        async def write(value, **options):
            result = await bridge(session, run, {'tool': 'jellyfish_write_document',
                                                 'arguments': {'path': path, 'content': value, **options}})
            return result, json.loads(result['contentItems'][0]['text']) if result['success'] else None

        first, metadata = await write(content)
        self.assertTrue(first['success'], first)
        self.assertEqual(metadata['path'], path)
        self.assertEqual(metadata['size'], len(content.encode('utf-8')))
        self.assertTrue(metadata['created'])
        self.assertFalse(metadata['updated'])
        listed = await self.client.get('/api/files', params={'path': '/docs/菜单'})
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn(path, {entry['path'] for entry in listed.json()})
        read = await self.client.get('/api/files/read', params={'path': path})
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json()['content'], content)
        self.assertEqual(bridge.invoke('alice', 'jellyfish_read_document', Document(path=path)), content)
        self.assertIn('寻味江南-2026年十月菜单.md',
                      {entry['name'] for entry in bridge.invoke('alice', 'jellyfish_list_documents',
                                                                SimpleNamespace(path='/docs/菜单'))})
        bob_list = await self.client.get('/api/files', params={'path': '/docs/菜单'},
                                         headers={'Authorization': 'Bearer bob'})
        self.assertNotIn(path, {entry['path'] for entry in bob_list.json()})
        self.assertEqual((await self.client.get('/api/files/read', params={'path': path},
                                                headers={'Authorization': 'Bearer bob'})).status_code, 404)

        retry, metadata = await write(content)
        self.assertTrue(retry['success'], retry)
        self.assertFalse(metadata['created'])
        self.assertFalse(metadata['updated'])
        conflict, _ = await write('# Different')
        self.assertFalse(conflict['success'])
        self.assertEqual(self.storage.read_text('alice', path), content)
        replacement = '# Updated\n'
        overwritten, metadata = await write(replacement, overwrite=True)
        self.assertTrue(overwritten['success'], overwritten)
        self.assertFalse(metadata['created'])
        self.assertTrue(metadata['updated'])
        self.assertEqual((await self.client.get('/api/files/read', params={'path': path})).json()['content'], replacement)

    async def test_admin_document_write_rejects_unsafe_paths_and_oversize_content(self):
        bridge = BusinessTools(self.storage, self.profiles.authorize, self.store)
        binding = self.profiles.binding('alice', self.pid, 'model')
        session = {'actor_id': 'alice', 'binding': binding, 'conversation_id': 'admin-chat'}
        run = {'id': 'admin-document-invalid', 'actor_id': 'alice', 'binding': binding,
               'seq': 0, 'status': 'running', 'channel': 'web'}
        self.store.put('run', run)

        async def write(path, content='unsafe', **options):
            return await bridge(session, run, {'tool': 'jellyfish_write_document',
                                               'arguments': {'path': path, 'content': content, **options}})

        for path in ('/docs/../generated/escape.md', '/generated/escape.md',
                     '/docs/.hidden.md', '/docs/sub\\escape.md', '/docs', 'docs/relative.md'):
            with self.subTest(path=path):
                self.assertFalse((await write(path))['success'])
        self.assertFalse(self.storage.is_file('alice', '/generated/escape.md'))
        self.assertFalse(self.storage.is_file('alice', '/docs/.hidden.md'))

        fs_root = self.users / 'alice' / 'filesystem'
        (fs_root / 'docs').mkdir(parents=True, exist_ok=True)
        (fs_root / 'generated').mkdir(parents=True, exist_ok=True)
        (fs_root / 'docs' / 'alias').symlink_to(fs_root / 'generated', target_is_directory=True)
        self.assertFalse((await write('/docs/alias/escape.md'))['success'])
        self.assertFalse((fs_root / 'generated' / 'escape.md').exists())

        boundary = await write('/docs/max-size.md', 'x' * 65536)
        self.assertTrue(boundary['success'], boundary)
        self.assertEqual(len(self.storage.read_bytes('alice', '/docs/max-size.md')), 65536)
        too_large = await write('/docs/too-large.md', '中' * 21846)
        self.assertFalse(too_large['success'])
        self.assertFalse(self.storage.is_file('alice', '/docs/too-large.md'))

    async def test_codex_document_tool_call_waits_for_each_write_approval(self):
        self.runs.tool_bridge = BusinessTools(self.storage, self.profiles.authorize, self.store)
        binding = self.profiles.binding('alice', self.pid, 'model')
        session = self.runs.create_session('alice', binding, conversation_id='admin-chat')

        async def attempt(label, *, decision=None, yolo=False, change_during_approval=False):
            path = f'/docs/{label}.md'
            run = {'id': f'approval-{label}', 'session_id': session['id'], 'actor_id': 'alice',
                   'binding': binding, 'seq': 0, 'status': 'running', 'pending': None,
                   'channel': 'web', 'yolo': yolo}
            self.store.put('run', run)
            adapter = SimpleNamespace(respond=AsyncMock())
            state = {'run': run, 'adapter': adapter, 'answer': None, 'changes': {},
                     'cancel': False, 'finalizing': False}
            self.runs.active[run['id']] = state
            task = asyncio.create_task(self.runs._request(session, state, {
                'request_id': label, 'method': 'item/tool/call',
                'params': {'tool': 'jellyfish_write_document',
                           'arguments': {'path': path, 'content': label}},
            }))
            try:
                if not yolo:
                    await self.until(lambda: self.store.get('run', run['id'])['status'] == 'waiting_approval')
                    pending = self.store.get('run', run['id'])['pending']
                    self.assertEqual(pending['allowed'], ['accept', 'decline'])
                    self.assertFalse(self.storage.is_file('alice', path))
                    if change_during_approval:
                        self.storage.write_text('alice', path, 'changed during approval')
                    self.runs.approve('alice', run['id'], pending['id'], decision)
                await task
                result = adapter.respond.call_args.args[1]
                if change_during_approval:
                    self.assertFalse(result['success'])
                    self.assertEqual(self.storage.read_text('alice', path), 'changed during approval')
                else:
                    self.assertEqual(result['success'], decision == 'accept' or yolo)
                    self.assertEqual(self.storage.is_file('alice', path), decision == 'accept' or yolo)
                events = self.store.events(run['id'])
                if yolo:
                    self.assertNotIn('approval_requested', [event['type'] for event in events])
                    self.assertTrue(any(event['type'] == 'approval_resolved' and
                                        event['payload'].get('automatic') for event in events))
                else:
                    self.assertIn('approval_requested', [event['type'] for event in events])
            finally:
                if not task.done():
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.runs.active.pop(run['id'], None)
                self.store.emit(run, 'completed', {}, status='completed')

        await attempt('approved', decision='accept')
        await attempt('declined', decision='decline')
        await attempt('changed', decision='accept', change_during_approval=True)
        await attempt('automatic', yolo=True)

    async def test_admin_service_reply_uses_shared_approval_and_durable_queue(self):
        self.runs.tool_bridge = BusinessTools(self.storage, self.profiles.authorize, self.store)
        binding = self.profiles.binding('alice', self.pid, 'model')
        session = self.runs.create_session('alice', binding, conversation_id='admin-chat')
        target = {'owner_id': 'alice', 'service_id': 'svc', 'conversation_id': 'consumer-c1',
                  'source': 'wechat', 'session_id': 'wx-s1', 'recipient_id': 'recipient-1'}
        message = '请按这个方案继续。\n第二行原样发送。'
        queued = {'message': {'id': 'msg-1'}, 'deliveries': [
            {'id': 'delivery-web', 'channel': 'web', 'status': 'pending'},
            {'id': 'delivery-wx', 'channel': 'wechat', 'status': 'pending'},
        ]}

        async def attempt(label, *, decision=None, yolo=False, changed_target=False, cancelled=False):
            run = {'id': f'reply-{label}', 'session_id': session['id'], 'actor_id': 'alice',
                   'binding': binding, 'seq': 0, 'status': 'running', 'pending': None,
                   'channel': 'wechat' if yolo else 'web', 'yolo': yolo}
            self.store.put('run', run)
            adapter = SimpleNamespace(respond=AsyncMock())
            state = {'run': run, 'adapter': adapter, 'answer': None, 'changes': {},
                     'cancel': False, 'finalizing': False}
            self.runs.active[run['id']] = state
            changed = {**target, 'recipient_id': 'recipient-2'}
            targets = [target, changed] if changed_target else [target, target]
            with patch.object(self.runs, '_service_message_target', side_effect=targets), \
                 patch('app.services.service_messaging.send_service_message', return_value=queued) as send:
                task = asyncio.create_task(self.runs._request(session, state, {
                    'request_id': label, 'method': 'item/tool/call',
                    'params': {'tool': 'jellyfish_send_service_message',
                               'arguments': {'service_id': 'svc', 'conversation_id': 'consumer-c1',
                                             'message': message, 'inbox_id': 'inbox_case1'}},
                }))
                try:
                    if not yolo:
                        await self.until(lambda: self.store.get('run', run['id'])['status'] == 'waiting_approval')
                        pending = self.store.get('run', run['id'])['pending']
                        self.assertIn('consumer-c1', pending['command'])
                        self.assertIn('recipient-1', pending['command'])
                        self.assertIn(message, pending['command'])
                        send.assert_not_called()
                        if cancelled:
                            state['cancel'] = True
                        self.runs.approve('alice', run['id'], pending['id'], decision)
                    await task
                    result = adapter.respond.call_args.args[1]
                    expected = yolo or decision == 'accept' and not (changed_target or cancelled)
                    self.assertEqual(result['success'], expected)
                    self.assertEqual(send.call_count, int(expected))
                    if expected:
                        value = json.loads(result['contentItems'][0]['text'])
                        self.assertEqual(value['message_id'], 'msg-1')
                        self.assertEqual([d['status'] for d in value['deliveries']], ['pending', 'pending'])
                        self.assertIn('不能据此认定用户已收到', value['summary'])
                        send.assert_called_once_with('alice', 'svc', 'consumer-c1', message,
                            inbox_id='inbox_case1', idempotency_key=f'cli:{run["id"]}:{label}',
                            expected_target=target)
                    events = self.store.events(run['id'])
                    if yolo:
                        self.assertFalse(any(e['type'] == 'approval_requested' for e in events))
                        self.assertTrue(any(e['type'] == 'approval_resolved' and
                                            e['payload'].get('automatic') for e in events))
                finally:
                    if not task.done():
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    self.runs.active.pop(run['id'], None)
                    self.store.emit(run, 'completed', {}, status='completed')

        await attempt('accepted', decision='accept')
        await attempt('declined', decision='decline')
        await attempt('changed', decision='accept', changed_target=True)
        await attempt('cancelled', decision='accept', cancelled=True)
        await attempt('automatic', yolo=True)

    async def test_inbox_reply_preflight_uses_frozen_case_target(self):
        from app.runtime.business_tools import ServiceMessage
        target = {'owner_id': 'alice', 'service_id': 'svc', 'conversation_id': 'consumer-c1',
                  'source': 'wechat', 'session_id': 'wx-frozen', 'recipient_id': 'recipient-1'}
        case = {'service_id': 'svc', 'conversation_id': 'consumer-c1'}
        args = ServiceMessage(service_id='svc', conversation_id='consumer-c1',
                              message='原样回复', inbox_id='inbox_case1')
        with patch('app.services.inbox.get_inbox_message', return_value=case), \
             patch('app.services.service_messaging.get_store', return_value=SimpleNamespace(case_target=lambda *_: target)), \
             patch('app.services.service_messaging._authorize_target') as authorize, \
             patch('app.services.service_messaging._consumer_target', side_effect=AssertionError('must use frozen case target')):
            self.assertEqual(self.runs._service_message_target('alice', args), target)
            authorize.assert_called_once_with(target)
            wrong = ServiceMessage(service_id='svc', conversation_id='other-conv',
                                   message='不得发送', inbox_id='inbox_case1')
            with self.assertRaises(KeyError):
                self.runs._service_message_target('alice', wrong)

    async def test_old_codex_thread_cannot_call_unregistered_reply_tool(self):
        binding = self.profiles.binding('alice', self.pid, 'model')
        session = {'actor_id': 'alice', 'binding': binding, 'conversation_id': 'admin-chat',
                   'thread_id': 'old-native-thread', 'service_message_available': False}
        state = {'run': {'id': 'old-reply-run', 'binding': binding, 'status': 'running',
                         'pending': None}, 'answer': None, 'cancel': False, 'finalizing': False}
        with patch.object(self.runs, '_service_message_target') as target, \
             patch('app.services.service_messaging.send_service_message') as send:
            result = await self.runs._call_business_tool(session, state, {
                'tool': 'jellyfish_send_service_message', 'callId': 'old-call',
                'arguments': {'service_id': 'svc', 'conversation_id': 'consumer-c1',
                              'message': '应被拒绝'},
            })
            self.assertFalse(result['success'])
            self.assertIn('请新建对话', result['contentItems'][0]['text'])
            target.assert_not_called()
            send.assert_not_called()

    async def test_admin_service_reply_call_id_prevents_duplicate_queue_entries(self):
        from app.services import service_messaging as messaging
        outbox = messaging.MessageStore(self.root / 'service-outbox.sqlite3')
        self.addCleanup(outbox.close)
        bridge = BusinessTools(self.storage, self.profiles.authorize, self.store)
        binding = self.profiles.binding('alice', self.pid, 'model')
        session = {'actor_id': 'alice', 'binding': binding, 'conversation_id': 'admin-chat'}
        run = {'id': 'send-run', 'actor_id': 'alice', 'binding': binding,
               'seq': 0, 'status': 'running', 'channel': 'web'}
        self.store.put('run', run)
        target = {'owner_id': 'alice', 'service_id': 'svc',
                  'conversation_id': 'consumer-c1', 'source': 'web'}
        params = {'tool': 'jellyfish_send_service_message', 'callId': 'tool-1',
                  'approved_target': target,
                  'arguments': {'service_id': 'svc', 'conversation_id': 'consumer-c1',
                                'message': '只投递一次'}}
        with patch.object(messaging, 'get_store', return_value=outbox), \
             patch.object(messaging, '_consumer_target', return_value=target):
            unapproved = await bridge(session, run, {k: v for k, v in params.items() if k != 'approved_target'})
            self.assertFalse(unapproved['success'])
            self.assertEqual(outbox.db.execute('SELECT COUNT(*) FROM sm_messages').fetchone()[0], 0)
            first = await bridge(session, run, params)
            retry = await bridge(session, run, params)
            self.assertTrue(first['success'])
            self.assertEqual(first, retry)
            self.assertEqual(outbox.db.execute('SELECT COUNT(*) FROM sm_messages').fetchone()[0], 1)
            self.assertEqual(outbox.db.execute('SELECT COUNT(*) FROM sm_deliveries').fetchone()[0], 1)
            conflict = await bridge(session, run, {**params, 'arguments': {**params['arguments'],
                                                                           'message': '不能复用同一调用 ID'}})
            self.assertFalse(conflict['success'])
            self.assertEqual(outbox.db.execute('SELECT COUNT(*) FROM sm_messages').fetchone()[0], 1)

    async def test_service_directory_allowlist_includes_nested_documents(self):
        bridge = BusinessTools(self.storage, self.profiles.authorize, self.store)
        self.storage.write_text('alice','/docs/team/nested/readme.txt','TEAM')
        for allowed in [['team'], ['/team/']]:
            with patch('app.services.published.get_service', return_value={'admin_id':'alice','allowed_docs':allowed}):
                result = bridge.invoke('alice','jellyfish_read_service_document',ServiceDocument(service_id='svc',path='/docs/team/nested/readme.txt'))
                self.assertEqual(result,'TEAM')
                with self.assertRaises(ValueError):
                    bridge.invoke('alice','jellyfish_read_service_document',ServiceDocument(service_id='svc',path='/docs/team-other/readme.txt'))

    async def test_deleted_conversation_cancels_and_cannot_enqueue_again(self):
        conv = await self.new()
        r = await self.client.post('/api/runtime/turns', json={'conversation_id':conv['id'],'request_id':'request000000003','message':'wait'})
        rid = r.json()['id']
        self.assertEqual((await self.client.delete('/api/conversations/' + conv['id'])).status_code, 200)
        self.assertEqual(self.store.get('run',rid)['status'],'cancelled')
        r = await self.client.post('/api/runtime/turns',json={'conversation_id':conv['id'],'request_id':'request000000004','message':'again'})
        self.assertEqual(r.status_code,404)

    async def test_existing_deepagents_stream_preserves_tool_events_and_history(self):
        from app.services.conversations import create_conversation, get_conversation
        from langchain_core.messages import AIMessageChunk, ToolMessage
        conv = create_conversation('alice')
        class Graph:
            async def astream(self, *args, **kwargs):
                yield ((), (AIMessageChunk(content='', tool_call_chunks=[{'name':'read_file','args':'{"path":"/docs/example.txt"}','id':'t1','index':0}]), {}))
                yield ((), (ToolMessage(content='fixture contents',name='read_file',tool_call_id='t1'), {}))
                yield ((), (AIMessageChunk(content='DeepAgents reply'), {}))
            async def aget_state(self, config):
                return SimpleNamespace(tasks=[])
        with patch('app.routes.chat._create_user_agent_bounded', new_callable=AsyncMock, return_value=Graph()):
            response = await self.client.post('/api/chat',json={'conversation_id':conv['id'],'message':'read my document'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('tool_call',response.text)
        self.assertIn('tool_result',response.text)
        self.assertIn('DeepAgents reply',response.text)
        result = get_conversation('alice',conv['id'])
        self.assertEqual(result['messages'][-1]['content'],'DeepAgents reply')
        self.assertEqual(result['messages'][-1]['tool_calls'][0]['name'],'read_file')

    async def test_custom_prompt_and_profile_render_into_actor_instructions(self):
        from app.runtime.business_tools import instructions
        with patch('app.services.prompt.get_user_system_prompt',return_value='Actor prompt {today}\n{user_profile_context}') as prompt, patch('app.services.prompt.build_user_profile_prompt',return_value='ACTOR_PROFILE'):
            value = instructions('alice')
        prompt.assert_called_once_with('alice')
        self.assertIn('Actor prompt',value)
        self.assertIn('ACTOR_PROFILE',value)
        self.assertNotIn('{today}',value)
        self.assertNotIn('{user_profile_context}',value)

    async def test_unknown_or_incomplete_binding_never_falls_back(self):
        from app.services.conversations import create_conversation, _write_meta
        conv = create_conversation('alice')
        for runtime in ('future_runtime','codex'):
            _write_meta('alice', conv['id'], {**conv,'runtime_binding':{'runtime':runtime}})
            with patch('app.routes.chat._create_user_agent_bounded', new_callable=AsyncMock) as legacy:
                response = await self.client.post('/api/chat',json={'conversation_id':conv['id'],'message':'hello'})
                self.assertEqual(response.status_code,409)
                legacy.assert_not_called()
