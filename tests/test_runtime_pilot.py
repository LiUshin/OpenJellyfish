"""Offline integration tests: python -m unittest discover -s tests -p test_runtime_pilot.py."""
import asyncio
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from PIL import Image

from app.runtime.codex import CodexAdapter, codex_environment
from app.runtime.files import collect_files, digest, safe_read
from app.runtime.pilot import Pilot, get_pilot
from app.runtime.rpc import StdioRPC, RuntimeFailure
from app.runtime.types import RuntimeEvent
from app.storage.base import FileEntry
from app.routes.runtime_pilot import router, pilot as pilot_dep


class MemoryStorage:
    def __init__(self):
        self.files = {'/docs/brief.txt': b'original brief'}
    def list_dir(self, user, path):
        return [FileEntry(Path(p).name, p, False, len(data)) for p, data in self.files.items() if str(Path(p).parent) == path]
    def read_bytes(self, user, path):
        return self.files[path]
    def write_bytes(self, user, path, data):
        self.files[path] = data


class FakeAdapter:
    opens = []
    executions = 0
    def __init__(self, executable, home, cwd, model=None):
        self.workspace = cwd
        self.closed = False
        self.rpc = self
    async def open_session(self, workspace, instructions, thread_id=None):
        self.opens.append(thread_id)
        return thread_id or 'provider-thread'
    async def stream_turn(self, thread_id, text):
        type(self).executions += 1
        yield RuntimeEvent('text_delta', {'text': 'hello'})
        if text == 'wait':
            await asyncio.sleep(60)
        if text == 'approve':
            yield RuntimeEvent('request', {'request_id': 'rpc-approval', 'method': 'item/commandExecution/requestApproval',
                                          'params': {'command': 'write report', 'cwd': str(self.workspace)}})
            if self.decision['decision'] == 'decline':
                yield RuntimeEvent('text_delta', {'text': ' operation declined'})
                yield RuntimeEvent('completed', {})
                return
        if text == 'missing-image':
            yield RuntimeEvent('image', {'status': 'completed', 'failure': None, 'saved_path': str(self.workspace / 'missing.png'), 'item_id': 'im1'})
        else:
            (self.workspace / 'report.txt').write_text(text)
        if text == 'image':
            Image.new('RGB', (8, 8), 'red').save(self.workspace / 'art.png')
            yield RuntimeEvent('image', {'status': 'completed', 'failure': None, 'saved_path': str(self.workspace / 'art.png'), 'item_id': 'im1'})
        yield RuntimeEvent('completed', {})
    async def respond(self, request_id, result):
        self.decision = result
    async def cancel(self):
        pass
    async def close(self):
        self.closed = True


class PilotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.storage = MemoryStorage()
        self.p = Pilot('admin', root / 'pilot', '/codex', root / 'home', self.storage)
        self.p.claim()
        self.p.adapter_factory = FakeAdapter
        FakeAdapter.opens, FakeAdapter.executions = [], 0
        with patch('app.services.prompt.get_user_system_prompt', return_value='Hello {today} {user_profile_context}'), \
             patch('app.services.prompt.build_user_profile_prompt', return_value='profile'), \
             patch('app.services.preferences.get_tz_offset', return_value=8):
            self.s = self.p.create_session(None, ['/docs/brief.txt'])
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[pilot_dep] = lambda: self.p
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.p.shutdown()
        self.temp.cleanup()

    async def run_turn(self, text):
        rid = uuid.uuid4().hex
        self.p.start_turn(self.s['id'], rid, text)
        task = self.p.active[(self.s['id'], rid)]['task']
        await asyncio.wait_for(task, 3)
        return self.p.read_run(self.s['id'], rid)

    async def test_recovery_uses_same_provider_session_and_documents_remain_original(self):
        run = await self.run_turn('first')
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(self.storage.files['/docs/brief.txt'], b'original brief')
        # Reload only durable session state as a new application instance would.
        saved = self.p.read_session(self.s['id'])
        self.assertEqual(saved['thread_id'], 'provider-thread')
        await self.run_turn('second')
        self.assertEqual(FakeAdapter.opens, [None, 'provider-thread'])
        self.assertEqual(len(self.p.read_session(self.s['id'])['artifacts']), 2)

    async def test_sse_replay_idempotency_and_artifact_download(self):
        rid = uuid.uuid4().hex
        url = f"/api/runtime-pilot/sessions/{self.s['id']}/turns"
        body = {'request_id': rid, 'message': 'image'}
        self.assertEqual((await self.client.post(url, json=body)).status_code, 200)
        task = self.p.active[(self.s['id'], rid)]['task']
        await task
        await self.client.post(url, json=body)
        self.assertEqual(FakeAdapter.executions, 1)
        self.assertEqual((await self.client.post(url, json={**body, 'message': 'different'})).status_code, 409)
        stream = await self.client.get(f"/api/runtime-pilot/sessions/{self.s['id']}/runs/{rid}/events?after=1")
        self.assertNotIn('user_message', stream.text)
        self.assertIn('artifact_created', stream.text)
        self.assertIn('completed', stream.text)
        s = self.p.read_session(self.s['id'])
        image = next(a for a in s['artifacts'] if a['native_image'])
        response = await self.client.get(f"/api/runtime-pilot/sessions/{s['id']}/artifacts/{image['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'image/png')
        self.storage.files[image['path']] = b'changed'
        self.assertEqual((await self.client.get(f"/api/runtime-pilot/sessions/{s['id']}/artifacts/{image['id']}")).status_code, 409)

    async def test_approval_reject_and_stale_response(self):
        sid, rid = self.s['id'], uuid.uuid4().hex
        self.p.start_turn(sid, rid, 'approve')
        state = self.p.active[(sid, rid)]
        for _ in range(100):
            if state['run']['pending']:
                break
            await asyncio.sleep(.01)
        pending = state['run']['pending']
        self.assertIsNotNone(pending)
        with self.assertRaises(HTTPException):
            self.p.approve(sid, rid, pending['id'], 'acceptForSession')
        self.p.approve(sid, rid, pending['id'], 'decline')
        await state['task']
        self.assertEqual(state['adapter'].decision, {'decision': 'decline'})
        self.assertFalse((state['adapter'].workspace / 'report.txt').exists())
        with self.assertRaises(HTTPException):
            self.p.approve(sid, rid, pending['id'], 'accept')

    async def test_cancel_pending_approval_terminates_and_no_stale_prompt(self):
        sid, rid = self.s['id'], uuid.uuid4().hex
        self.p.start_turn(sid, rid, 'approve')
        state = self.p.active[(sid, rid)]
        await asyncio.sleep(.05)
        await self.p.cancel(sid, rid)
        run = self.p.read_run(sid, rid)
        self.assertEqual(run['status'], 'cancelled')
        self.assertIsNone(run['pending'])
        self.assertTrue(state['adapter'].closed)
        self.assertFalse(self.p.active)

    async def test_immediate_cancel_before_task_scheduled(self):
        sid, rid = self.s['id'], uuid.uuid4().hex
        self.p.start_turn(sid, rid, 'wait')
        await self.p.cancel(sid, rid)
        self.assertEqual(self.p.read_run(sid, rid)['status'], 'cancelled')
        self.assertFalse(self.p.active)

    async def test_concurrent_run_rejected(self):
        sid, rid = self.s['id'], uuid.uuid4().hex
        self.p.start_turn(sid, rid, 'wait')
        with self.assertRaises(HTTPException) as ctx:
            self.p.start_turn(sid, uuid.uuid4().hex, 'other')
        self.assertEqual(ctx.exception.status_code, 409)
        await self.p.cancel(sid, rid)

    async def test_image_event_without_file_is_failure(self):
        run = await self.run_turn('missing-image')
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.p.read_session(self.s['id'])['artifacts'], [])

    async def test_profile_change_and_invalid_session_rejected(self):
        self.p.home = Path('/another/account')
        with self.assertRaises(HTTPException) as ctx:
            self.p.start_turn(self.s['id'], uuid.uuid4().hex, 'new')
        self.assertEqual(ctx.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            self.p.read_session('../escape')
        self.assertEqual((await self.client.get('/api/runtime-pilot/sessions/' + 'f' * 32)).status_code, 404)

    async def test_second_worker_refused(self):
        other = Pilot('admin', self.p.root, '/codex', self.p.home, self.storage)
        with self.assertRaises(HTTPException):
            other.claim()

    async def test_restart_marks_unfinished_run_failed(self):
        run = {'id': uuid.uuid4().hex, 'status': 'running', 'seq': 0, 'pending': {'id': 'stale'}}
        self.p.emit(self.s['id'], run, 'started', {})
        await self.p.shutdown()
        other = Pilot('admin', self.p.root, '/codex', self.p.home, self.storage)
        other.claim()
        try:
            self.assertEqual(other.read_run(self.s['id'], run['id'])['status'], 'failed')
            self.assertIsNone(other.read_run(self.s['id'], run['id'])['pending'])
            with self.assertRaises(HTTPException):
                other.start_turn(self.s['id'], uuid.uuid4().hex, 'do not replay')
        finally:
            await other.shutdown()


class BoundaryTests(unittest.TestCase):
    def test_feature_flag_and_wrong_user_fail_closed(self):
        with patch.dict(os.environ, {'JELLYFISH_CODEX_PILOT': '0'}):
            with self.assertRaises(HTTPException): get_pilot('admin')
        with patch.dict(os.environ, {'JELLYFISH_CODEX_PILOT': '1', 'JELLYFISH_CODEX_ADMIN_ID': 'owner'}):
            with self.assertRaises(HTTPException): get_pilot('visitor')

    def test_environment_does_not_inherit_provider_keys(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only', 'ANTHROPIC_API_KEY': 'test-only', 'CODEX_HOME': '/wrong'}):
            env = codex_environment(Path('/dedicated'))
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('ANTHROPIC_API_KEY', env)
            self.assertEqual(env['CODEX_HOME'], '/dedicated')

    def test_artifact_symlink_hardlink_traversal_and_invalid_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'workspace'; root.mkdir()
            outside = Path(temp) / 'outside'; outside.write_text('private')
            (root / 'link').symlink_to(outside)
            with self.assertRaises(ValueError): safe_read(root, root / 'link')
            (root / 'link').unlink()
            os.link(outside, root / 'hardlink')
            with self.assertRaises(ValueError): safe_read(root, root / 'hardlink')
            (root / 'hardlink').unlink()
            with self.assertRaises(ValueError): safe_read(root, root / '..' / 'outside')
            (root / 'fake.png').write_text('not a png')
            with self.assertRaises(Exception): collect_files(root, {}, [str(root / 'fake.png')])


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_adapter_normalizes_events_and_keeps_resume_binding(self):
        class ScriptedRPC:
            def __init__(self): self.calls = []
            async def start(self): pass
            async def send(self, message): self.calls.append(message)
            async def request(self, method, params, **kwargs):
                self.calls.append({'method': method, 'params': params})
                if method.startswith('thread/'):
                    return {'thread': {'id': 'same-thread'}}
                if method == 'turn/start':
                    return {'turn': {'id': 'turn-1'}}
                return {}
            async def next_event(self): return self.messages.pop(0)
        adapter = CodexAdapter('/unused', Path('/tmp/home'), Path('/tmp/workspace'))
        peer = ScriptedRPC()
        adapter.rpc = peer
        peer.messages = [
            {'method': 'item/agentMessage/delta', 'params': {'threadId': 'other', 'delta': 'wrong'}},
            {'method': 'item/agentMessage/delta', 'params': {'threadId': 'same-thread', 'turnId': 'turn-1', 'delta': 'hello'}},
            {'method': 'item/completed', 'params': {'item': {'type': 'imageGeneration', 'id': 'im', 'status': 'completed', 'savedPath': '/tmp/workspace/a.png'}}},
            {'id': 'approve-1', 'method': 'item/fileChange/requestApproval', 'params': {'itemId': 'patch-1'}},
            {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}},
        ]
        self.assertEqual(await adapter.open_session('/tmp/workspace', 'instructions', 'same-thread'), 'same-thread')
        resume = next(c for c in peer.calls if c['method'] == 'thread/resume')
        self.assertEqual(resume['params']['approvalPolicy'], 'untrusted')
        self.assertEqual(resume['params']['sandbox'], 'workspace-write')
        self.assertEqual(resume['params']['threadId'], 'same-thread')
        events = [e async for e in adapter.stream_turn('same-thread', 'message')]
        self.assertEqual([e.type for e in events], ['started', 'text_delta', 'image', 'request', 'completed'])
        self.assertEqual(events[1].payload['text'], 'hello')
        self.assertEqual(events[2].payload['saved_path'], '/tmp/workspace/a.png')
        await adapter.respond('approve-1', {'decision': 'decline'})
        self.assertEqual(peer.calls[-1], {'id': 'approve-1', 'result': {'decision': 'decline'}})

    async def test_bidirectional_rpc_notifications_and_missing_terminal(self):
        code = '''import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if m.get('method')=='probe':
  print(json.dumps({'method':'item/agentMessage/delta','params':{'delta':'你好'}}),flush=True)
  print(json.dumps({'id':m['id'],'result':{'ok':True}}),flush=True)
  print(json.dumps({'id':'approval','method':'approve','params':{}}),flush=True)
 elif m.get('id')=='approval':
  break
'''
        rpc = StdioRPC([sys.executable, '-u', '-c', code], env={'PATH': os.environ['PATH']}, cwd='/tmp')
        await rpc.start()
        try:
            self.assertEqual(await rpc.request('probe', {}), {'ok': True})
            self.assertEqual((await rpc.next_event())['params']['delta'], '你好')
            self.assertEqual((await rpc.next_event())['id'], 'approval')
            await rpc.send({'id': 'approval', 'result': {'decision': 'decline'}})
            with self.assertRaises(RuntimeFailure):
                await asyncio.wait_for(rpc.next_event(), 2)
        finally:
            await rpc.close()


if __name__ == '__main__':
    unittest.main()
