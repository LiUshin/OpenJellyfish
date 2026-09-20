"""Admin YOLO transport, native approval decisions, and unchanged scope checks."""
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

from fastapi import HTTPException
from app.core.host_auth import HOST_ID
from app.runtime.cursor import CursorAdapter
from app.runtime.providers import CursorProvider
from app.runtime.store import TERMINAL
from app.runtime.types import RuntimeEvent
import test_runtime_chat as chat_tests
from test_runtime_cursor import cursor_auth
import test_runtime_service as service_tests


class ApprovalAdapter(service_tests.FakeAdapter):
    def __init__(self, gate, workspace, engine):
        super().__init__(gate)
        self.workspace, self.engine = workspace, engine
        self.native = None

    async def stream_turn(self, thread_id, text):
        yield RuntimeEvent('text_delta', {'text': 'working'})
        await self.gate.wait()
        path = str(self.workspace / 'out.txt')
        if text == 'outside':
            path = str(self.workspace.parent / 'private.txt')
        if self.engine == 'cursor':
            self.native = CursorAdapter('unused', self.workspace, self.workspace)
            self.native.rpc = SimpleNamespace(send=AsyncMock())
            self.native.service_scope = getattr(self, 'service_scope', None)
            if text == 'plan':
                event = {'id': 7, 'method': 'cursor/create_plan', 'params': {'name': 'write a report'}}
            else:
                options = [{'kind': 'reject_once', 'optionId': 'no'}]
                if text != 'deny-only':
                    options.append({'kind': 'allow_once', 'optionId': 'yes'})
                tool = {'kind': 'execute', 'title': 'echo hello', 'toolCallId': 'tool-1'}
                if text in ('file', 'outside', 'missing-diff'):
                    tool = {'kind': 'edit', 'toolCallId': 'tool-1', 'title': 'write report',
                            'content': [] if text == 'missing-diff' else [{'type': 'diff', 'path': path, 'newText': 'report'}]}
                event = {'id': 7, 'method': 'session/request_permission', 'params': {'toolCall': tool, 'options': options}}
            async for event in self.native.request_events(event):
                yield event
        else:
            params = {'command': 'echo hello', 'cwd': str(self.workspace), 'availableDecisions': ['accept', 'cancel']}
            method = 'item/commandExecution/requestApproval'
            if text in ('file', 'outside', 'missing-diff'):
                method = 'item/fileChange/requestApproval'
                params = {'itemId': 'tool-1', 'availableDecisions': ['accept', 'cancel']}
                if text != 'missing-diff':
                    yield RuntimeEvent('tool', {'item_id': 'tool-1', 'kind': 'fileChange', 'changes': [{'path': path, 'diff': '+report'}]})
            if text == 'network':
                params['networkApprovalContext'] = {'host': 'example.test'}
            if text == 'deny-only':
                params['availableDecisions'] = ['cancel']
            yield RuntimeEvent('request', {'request_id': 7, 'method': method, 'params': params})
        yield RuntimeEvent('completed', {})

    async def respond(self, key, result):
        self.responses.append(result)
        if self.native:
            await self.native.respond(key, result)


class ApprovalBackend(service_tests.FakeBackend):
    @contextlib.asynccontextmanager
    async def execution(self, session):
        adapter = ApprovalAdapter(self.gate, self.workspace(session), session['binding'].get('runtime', 'codex'))
        self.adapters.append(adapter)
        try:
            yield adapter
        finally:
            await adapter.close()


class YoloApprovalTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = service_tests.ServiceTests.asyncTearDown
    until = service_tests.ServiceTests.until

    async def asyncSetUp(self):
        await service_tests.ServiceTests.asyncSetUp(self)
        self.backend = self.service.backend = ApprovalBackend(self.backend.root)
        self.backend.gate.set()

    def session(self, engine='codex', **extra):
        return self.service.create_session('a', {**self.binding, 'runtime': engine, **extra})

    async def test_yolo_auto_approves_native_commands_files_and_cursor_plans(self):
        for engine in ('codex', 'cursor'):
            session = self.session(engine)
            for action in ('command', 'file', *(('plan',) if engine == 'cursor' else ())):
                with self.subTest(engine=engine, action=action):
                    run = self.service.enqueue('a', session['id'], engine + action, action, yolo=True)
                    await self.until(lambda: self.store.get('run', run['id'])['status'] in TERMINAL)
                    final = self.store.get('run', run['id'])
                    self.assertEqual(final['status'], 'completed')
                    self.assertTrue(final['yolo'])
                    self.assertIsNone(final['pending'])
                    events = self.store.events(run['id'])
                    self.assertNotIn('approval_requested', [e['type'] for e in events])
                    resolution = next(e['payload'] for e in events if e['type'] == 'approval_resolved')
                    self.assertTrue(resolution['automatic'])
                    self.assertEqual(resolution['decision'], 'accept')
                    adapter = self.backend.adapters[-1]
                    self.assertEqual(adapter.responses, [{'decision': 'accept'}])
                    if engine == 'cursor':
                        outcome = adapter.native.rpc.send.call_args.args[0]['result']['outcome']
                        self.assertEqual(outcome, {'outcome': 'accepted'} if action == 'plan' else {'outcome': 'selected', 'optionId': 'yes'})

    async def test_yolo_denies_outside_missing_diff_network_and_unoffered_accept(self):
        for engine in ('codex', 'cursor'):
            for action in ('outside', 'missing-diff', 'deny-only', *(('network',) if engine == 'codex' else ())):
                with self.subTest(engine=engine, action=action):
                    run = self.service.enqueue('a', self.session(engine)['id'], engine + action, action, yolo=True)
                    await self.until(lambda: self.store.get('run', run['id'])['status'] in TERMINAL)
                    self.assertEqual(self.store.get('run', run['id'])['status'], 'completed')
                    self.assertEqual(self.backend.adapters[-1].responses, [{'decision': 'cancel' if engine == 'codex' else 'decline'}])
                    events = self.store.events(run['id'])
                    self.assertNotIn('approval_requested', [e['type'] for e in events])
                    self.assertTrue(any(e['type'] == 'notice' for e in events))

    async def test_yolo_is_per_turn_and_same_request_cannot_change_it(self):
        session = self.session()
        first = self.service.enqueue('a', session['id'], 'once', 'command', yolo=True)
        self.assertEqual(self.service.enqueue('a', session['id'], 'once', 'command', yolo=True)['id'], first['id'])
        with self.assertRaises(HTTPException) as exc:
            self.service.enqueue('a', session['id'], 'once', 'command', yolo=False)
        self.assertEqual(exc.exception.status_code, 409)
        await self.until(lambda: self.store.get('run', first['id'])['status'] == 'completed')
        second = self.service.enqueue('a', session['id'], 'manual', 'command')
        await self.until(lambda: self.store.get('run', second['id'])['status'] == 'waiting_approval')
        self.assertFalse(self.store.get('run', second['id'])['yolo'])
        self.assertFalse(self.backend.adapters[-1].responses)
        pending = self.store.get('run', second['id'])['pending']
        self.service.approve('a', second['id'], pending['id'], 'accept')
        await self.until(lambda: self.store.get('run', second['id'])['status'] == 'completed')

    async def test_service_scope_cannot_opt_into_admin_yolo(self):
        run = self.service.enqueue('a', self.session(service_scope={'service_id': 'fixture'})['id'], 'service', 'command', yolo=True)
        self.assertFalse(run['yolo'])
        await self.until(lambda: self.store.get('run', run['id'])['status'] == 'completed')
        self.assertEqual(self.backend.adapters[-1].responses, [{'decision': 'cancel'}])
        self.assertNotIn('approval_requested', [e['type'] for e in self.store.events(run['id'])])

    async def test_revocation_still_stops_yolo_before_approval(self):
        self.backend.gate.clear()
        run = self.service.enqueue('a', self.session()['id'], 'revoked', 'command', yolo=True)
        await self.until(lambda: self.store.get('run', run['id'])['status'] == 'running')
        self.revoked.add('a')
        self.backend.gate.set()
        await self.until(lambda: self.store.get('run', run['id'])['status'] in TERMINAL)
        self.assertIn(self.store.get('run', run['id'])['status'], ('cancelled', 'failed'))
        self.assertFalse(self.backend.adapters[-1].responses)


class YoloHTTPTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = chat_tests.ChatTests.asyncTearDown
    until = chat_tests.ChatTests.until

    async def asyncSetUp(self):
        await chat_tests.ChatTests.asyncSetUp(self)
        self.backend = self.manager.backend = self.runs.backend = ApprovalBackend(self.backend.root)
        self.backend.gate.set()

    async def test_admin_turn_and_legacy_chat_forward_yolo_for_both_engines(self):
        for engine in ('codex', 'cursor'):
            pid = self.pid
            if engine == 'cursor':
                self.profiles.providers['cursor'] = CursorProvider('fake-cursor')
                profile = self.profiles.create(HOST_ID, 'Cursor', runtime='cursor')
                profile['models'] = [{'id': 'model', 'name': 'Model'}]
                self.profiles.save_auth(profile, cursor_auth())
                pid = profile['id']
                self.profiles.grant(HOST_ID, pid, 'alice', ['model'])
            response = await self.client.post('/api/conversations', json={'runtime_choice': {'runtime': engine, 'profile_id': pid, 'model': 'model'}})
            self.assertEqual(response.status_code, 200, response.text)
            conv = response.json()
            for endpoint in ('/api/runtime/turns', '/api/chat'):
                with self.subTest(engine=engine, endpoint=endpoint):
                    response = await self.client.post(endpoint, json={'conversation_id': conv['id'], 'request_id': 'yolo-' + engine + '-' + ('runtime' if endpoint.endswith('turns') else 'legacy'), 'message': 'command', 'yolo': True})
                    self.assertEqual(response.status_code, 200, response.text)
                    if endpoint.endswith('turns'):
                        run = response.json()
                        await self.until(lambda: self.store.get('run', run['id'])['status'] in TERMINAL)
                        run = self.store.get('run', run['id'])
                        self.assertTrue(run['yolo'])
                        self.assertEqual(run['status'], 'completed')
                    else:
                        self.assertIn('auto_approve', response.text)
                        self.assertNotIn('interrupt', response.text)
                    self.assertEqual(self.backend.adapters[-1].responses, [{'decision': 'accept'}])


if __name__ == '__main__':
    unittest.main()
