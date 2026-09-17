from app.core.host_auth import HOST_ID
import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import HTTPException
from app.runtime.backend import LocalBackend
from app.runtime.rpc import RuntimeFailure
import test_runtime_chat as chat_tests
from test_runtime_cursor import FakeACP, cursor_auth
from app.runtime.cursor import CursorAdapter, credential_path
from app.runtime.codex import CodexAdapter


class ModelSwitchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = chat_tests.ChatTests.asyncSetUp
    asyncTearDown = chat_tests.ChatTests.asyncTearDown
    new = chat_tests.ChatTests.new
    until = chat_tests.ChatTests.until
    async def test_model_is_per_turn_snapshot_and_reauthorized(self):
        profile = self.profiles.get(self.pid)
        profile['models'] += [{'id': 'next', 'name': 'Next'}, {'id': 'denied', 'name': 'Denied'}]
        self.store.put('profile', profile)
        self.profiles.grant(HOST_ID, self.pid, 'alice', ['model', 'next'])
        conv = await self.new()
        sid = conv['runtime_session_id']
        self.backend.gate.set()
        first = self.runs.enqueue('alice', sid, 'first-request', 'first', model='model')
        await self.until(lambda: self.store.get('run', first['id'])['status'] == 'completed')
        response = await self.client.post('/api/runtime/turns', json={
            'conversation_id': conv['id'], 'request_id': 'second-request-id', 'message': 'second', 'model': 'next'})
        self.assertEqual(response.status_code, 200, response.text)
        second = response.json()
        await self.until(lambda: self.store.get('run', second['id'])['status'] == 'completed')
        self.assertEqual(self.store.get('run', first['id'])['binding']['model'], 'model')
        self.assertEqual(self.store.get('run', second['id'])['binding']['model'], 'next')
        self.assertEqual(self.store.get('session', sid)['thread_id'], 'thread')
        meta = (await self.client.get('/api/conversations/' + conv['id'])).json()
        self.assertEqual(meta['runtime_binding']['model'], 'next')
        self.assertEqual(self.runs.enqueue('alice', sid, 'first-request', 'first', model='model')['id'], first['id'])
        with self.assertRaises(HTTPException) as conflict:
            self.runs.enqueue('alice', sid, 'first-request', 'first', model='next')
        self.assertEqual(conflict.exception.status_code, 409)
        for model in ('denied', 'missing'):
            with self.assertRaises(HTTPException):
                self.runs.enqueue('alice', sid, 'third-' + model, 'third', model=model)
        self.assertEqual(self.store.get('session', sid)['binding']['model'], 'next')


class WarmBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.adapters, self.leased, self.closed = [], set(), []
        self.credentials = type('Credentials', (), {})()
        @contextlib.asynccontextmanager
        async def lease(binding, home):
            pid = binding['profile_id']
            self.assertNotIn(pid, self.leased)
            self.leased.add(pid)
            try: yield
            finally: self.leased.remove(pid)
        async def close(pid, adapter):
            self.closed.append(adapter)
            self.assertIn(pid, self.leased)
        self.credentials.lease = lease
        self.credentials.close_adapter = close
        def factory(*args, **kwargs):
            adapter = type('Adapter', (), {})()
            self.adapters.append(adapter)
            return adapter
        self.backend = LocalBackend(self.tmp.name, self.credentials, 'fake', factory, max_idle=1)
        self.session = {'id': 's1', 'actor_id': 'alice', 'binding': {'profile_id': 'p1', 'auth_generation': 1, 'model': 'm1'}, 'instructions': '', 'dynamic_tools': []}
    async def asyncTearDown(self):
        await self.backend.shutdown()
        self.assertFalse(self.leased)
        self.tmp.cleanup()
    async def warm(self, session=None):
        async with self.backend.execution(session or self.session) as adapter:
            adapter.reusable = True
        return adapter
    async def test_reuses_client_and_exclusive_auth_lease_across_models(self):
        first = await self.warm()
        first.tool_call = AsyncMock()
        self.session['binding'] = {**self.session['binding'], 'model': 'm2'}
        second = await self.warm()
        self.assertIs(second, first)
        self.assertTrue(second.reused)
        self.assertEqual(second.model, 'm2')
        self.assertIsNone(second.tool_call)
        self.assertEqual(self.closed, [])
        self.assertEqual(self.leased, {'p1'})
    async def test_actor_session_authorization_and_capacity_do_not_cross(self):
        previous = await self.warm()
        for changes in ({'actor_id': 'bob'}, {'id': 's2'}, {'binding': {**self.session['binding'], 'auth_generation': 2}},
                        {'binding': {**self.session['binding'], 'profile_id': 'p2'}}):
            self.session = {**self.session, **changes}
            current = await self.warm()
            self.assertIsNot(current, previous)
            self.assertIn(previous, self.closed)
            previous = current
        self.assertEqual(len(self.backend.idle), 1)
    async def test_failure_and_cancellation_close_before_releasing_credential(self):
        await self.warm()
        for error in (RuntimeFailure('upstream'), asyncio.CancelledError()):
            with self.assertRaises(type(error)):
                async with self.backend.execution(self.session) as adapter:
                    adapter.reusable = True
                    raise error
            self.assertIn(adapter, self.closed)
            self.assertFalse(self.leased)
            self.assertFalse(self.backend.idle)
    async def test_expiry_revocation_and_explicit_release(self):
        first = await self.warm()
        self.backend.idle['p1']['idle_since'] = 0
        await self.backend.reap(lambda *args: None)
        self.assertIn(first, self.closed)
        second = await self.warm()
        def denied(*args): raise HTTPException(403)
        await self.backend.reap(denied)
        self.assertIn(second, self.closed)
        third = await self.warm()
        await self.backend.release(profile_id='p1', actor_id='bob')
        self.assertNotIn(third, self.closed)
        await self.backend.release(profile_id='p1', actor_id='alice')
        self.assertIn(third, self.closed)
    async def test_failed_idle_eviction_still_closes_new_client(self):
        first = await self.warm()
        close = self.credentials.close_adapter
        async def fail_old(pid, adapter):
            await close(pid, adapter)
            if adapter is first: raise RuntimeFailure('cleanup failed')
        self.credentials.close_adapter = fail_old
        self.session = {**self.session, 'binding': {**self.session['binding'], 'profile_id': 'p2'}}
        with self.assertRaises(RuntimeFailure): await self.warm()
        self.assertFalse(self.leased)


class AdapterModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_cursor_warm_turn_keeps_session_changes_model_and_does_not_repeat_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth = credential_path(home); auth.parent.mkdir(parents=True); auth.write_bytes(cursor_auth())
            adapter = CursorAdapter('unused', home, home, 'model')
            adapter.rpc = FakeACP()
            await adapter.open_session(tmp, 'system instructions')
            _ = [e async for e in adapter.stream_turn('thread', 'first')]
            await adapter.open_session(tmp, 'system instructions', 'thread')
            self.assertEqual(sum(m == 'session/set_model' for m, _ in adapter.rpc.calls), 1)
            adapter.model = 'next-model'
            await adapter.open_session(tmp, 'system instructions', 'thread')
            _ = [e async for e in adapter.stream_turn('thread', 'second')]
            methods = [m for m, _ in adapter.rpc.calls]
            self.assertEqual(methods.count('initialize'), 1)
            self.assertEqual(methods.count('session/new'), 1)
            self.assertEqual(methods.count('session/load'), 0)
            self.assertIn(('session/set_model', {'sessionId': 'thread', 'modelId': 'next-model'}), adapter.rpc.calls)
            prompts = [p['prompt'][0]['text'] for m, p in adapter.rpc.calls if m == 'session/prompt']
            self.assertIn('system instructions', prompts[0])
            self.assertEqual(prompts[1], 'second')
    async def test_codex_model_sent_each_turn_without_reinitializing(self):
        class RPC:
            def __init__(self): self.calls = []
            async def start(self): pass
            async def send(self, msg): pass
            async def request(self, method, params):
                self.calls.append((method, params))
                return {'thread': {'id': 'thread'}, 'turn': {'id': 'turn'}}
            async def next_event(self): return {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}
        adapter = CodexAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'm1')
        adapter.rpc = RPC()
        await adapter.open_session('/tmp/work', 'instructions')
        _ = [e async for e in adapter.stream_turn('thread', 'first')]
        adapter.model = 'm2'
        await adapter.open_session('/tmp/work', 'instructions', 'thread')
        _ = [e async for e in adapter.stream_turn('thread', 'second')]
        self.assertEqual([p['model'] for m, p in adapter.rpc.calls if m == 'turn/start'], ['m1', 'm2'])
        self.assertEqual([m for m, _ in adapter.rpc.calls].count('initialize'), 1)
        self.assertEqual([m for m, _ in adapter.rpc.calls].count('thread/start'), 1)
        self.assertNotIn('thread/resume', [m for m, _ in adapter.rpc.calls])


class StreamBlockTests(unittest.TestCase):
    def test_text_tool_order_updates_and_terminal_survive_snapshot(self):
        from app.runtime.blocks import append_event
        blocks = []
        for kind, payload in [
            ('text_delta', {'text': 'first'}), ('text_delta', {'text': ' part'}),
            ('tool', {'item_id': 't1', 'kind': 'execute', 'command': 'pwd', 'status': 'inProgress'}),
            ('tool', {'item_id': 't1', 'status': 'completed'}),
            ('business_tool', {'name': 'jellyfish_list_documents', 'status': 'running'}),
            ('business_tool', {'name': 'jellyfish_list_documents', 'status': 'completed'}),
            ('business_tool', {'name': 'jellyfish_list_documents', 'status': 'running'}),
            ('text_delta', {'text': 'last'}), ('cancelled', {}),
        ]:
            append_event(blocks, kind, payload)
        self.assertEqual([b['type'] for b in blocks], ['text', 'tool', 'tool', 'tool', 'text'])
        self.assertEqual(blocks[0]['content'], 'first part')
        self.assertEqual(blocks[1]['result'], '已完成')
        self.assertEqual(blocks[1]['args'], 'pwd')
        self.assertEqual(blocks[3]['result'], '本轮已结束；未收到工具完成事件')
