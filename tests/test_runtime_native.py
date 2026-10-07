from app.core.host_auth import HOST_ID
import asyncio
import base64
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from PIL import Image
from app.runtime.connection_backend import ConnectionBackend
from app.runtime.rpc import RuntimeFailure
from app.runtime.types import RuntimeEvent
from app.runtime.media import decode_inputs, save_inputs, input_files, native_image
from app.runtime.files import collect_files
from app.runtime.store import RuntimeStore
import test_runtime_chat as chat_tests


class ConnectionPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = RuntimeStore(self.root)
        self.live, self.adapters, self.closed = set(), [], []
        for pid in ('p1', 'p2'):
            self.store.put('profile', {'id': pid, 'auth_generation': 1, 'credential_owner_id': HOST_ID, 'status': 'ready', 'models': [{'id': 'm1'}]})
        def binding(actor, pid, model):
            p = self.store.get('profile', pid)
            if p['status'] != 'ready': raise HTTPException(409)
            return {'profile_id': pid, 'model': model, 'auth_generation': p['auth_generation']}
        @contextlib.asynccontextmanager
        async def lease(binding, home):
            pid = binding['profile_id']
            self.assertNotIn(pid, self.live)
            self.live.add(pid)
            try: yield
            finally: self.live.remove(pid)
        async def close(pid, adapter):
            self.assertIn(pid, self.live)
            self.closed.append(adapter)
        self.credentials = SimpleNamespace(store=self.store, get=lambda pid: self.store.get('profile', pid),
                                           binding=binding, lease=lease, close_adapter=close)
        def factory(*a, **k):
            adapter = SimpleNamespace(warm_up=AsyncMock(return_value={'image_input': True}), prepare_history=AsyncMock(), session_count=0)
            self.adapters.append(adapter)
            return adapter
        self.backend = ConnectionBackend(self.root, self.credentials, 'fake', max_clients=1, adapter_factory=factory)
    async def asyncTearDown(self):
        await self.backend.shutdown()
        self.assertFalse(self.live)
        self.store.close()
        self.tmp.cleanup()
    def session(self, actor='alice', sid='s1', pid='p1'):
        return {'id': sid, 'actor_id': actor, 'binding': self.credentials.binding(actor, pid, 'm1')}
    async def use(self, session):
        async with self.backend.execution(session) as adapter:
            adapter.reusable = True
            return adapter
    async def test_connect_prewarm_cross_chat_reuse_and_workspace_rebind(self):
        await self.backend.reap(None)
        await self.backend.clients['p1']['task']
        self.assertEqual(self.backend.public_state('p1')['status'], 'ready')
        first = await self.use(self.session())
        first.tool_call = AsyncMock()
        second = await self.use(self.session('bob', 's2'))
        self.assertIs(first, second)
        self.assertTrue(second.reused)
        self.assertEqual(second.workspace, self.root / 'actors/bob/s2/workspace')
        self.assertIsNone(second.tool_call)
        self.assertEqual(second.session_key, 's2')
        self.assertEqual(first.warm_up.await_count, 1)
        self.assertEqual(first.prepare_history.await_count, 2)
        self.assertEqual(len(self.adapters), 1)

    async def test_connection_reuse_rebinds_model_and_image_mode(self):
        off = self.session()
        off['binding']['image_mode'] = 'off'
        first = await self.use(off)
        self.assertEqual((first.model, first.image_mode), ('m1', 'off'))
        native = self.session('bob', 's2')
        native['binding'].update(model='m2', image_mode='native')
        second = await self.use(native)
        self.assertIs(first, second)
        self.assertEqual((second.model, second.image_mode), ('m2', 'native'))
        self.assertTrue(second.reused)
        third = await self.use(off)
        self.assertIs(second, third)
        self.assertEqual((third.model, third.image_mode), ('m1', 'off'))
    async def test_capacity_eviction_pause_and_generation_invalidation(self):
        old = await self.use(self.session())
        new = await self.use(self.session(pid='p2'))
        self.assertIn(old, self.closed)
        self.assertIsNot(old, new)
        await self.backend.pause('p2')
        await self.backend.reap(None)
        self.assertNotIn('p2', self.backend.clients)
        self.backend.resume('p2')
        await self.use(self.session(pid='p2'))
        p = self.credentials.get('p2'); p['auth_generation'] += 1; self.store.put('profile', p)
        count = len(self.adapters)
        await self.use(self.session(pid='p2'))
        self.assertEqual(len(self.adapters), count + 1)
    async def test_same_connection_serializes_and_cancel_closes_lease(self):
        async with self.backend.execution(self.session()) as first:
            other = asyncio.create_task(self.use(self.session('bob', 's2')))
            await asyncio.sleep(.01)
            self.assertFalse(other.done())
            first.reusable = True
        self.assertIs(await other, first)
        with self.assertRaises(asyncio.CancelledError):
            async with self.backend.execution(self.session()):
                raise asyncio.CancelledError()
        self.assertFalse(self.live)
        self.assertIn(first, self.closed)
    async def test_workspace_setup_failure_releases_reserved_client(self):
        with patch.object(self.backend, 'workspace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                async with self.backend.execution(self.session()):
                    self.fail('must fail before executing')
        self.assertFalse(self.live)
        self.assertNotIn('p1', self.backend.clients)
        await self.use(self.session())

    async def test_warm_failure_backs_off_without_credential_leak(self):
        def broken(*a, **k):
            return SimpleNamespace(warm_up=AsyncMock(side_effect=RuntimeError('network')), prepare_history=AsyncMock())
        self.backend.adapter_factory = broken
        await self.backend.reap(None)
        with self.assertRaises(RuntimeError): await self.backend.clients['p1']['task']
        await self.backend.reap(None)
        self.assertNotIn('p1', self.backend.clients)
        self.assertEqual(self.backend.public_state('p1')['status'], 'error')
        self.assertNotIn('p1', self.live)


def png():
    stream = io.BytesIO(); Image.new('RGB', (16, 16), 'blue').save(stream, 'PNG'); return stream.getvalue()

def attachment(data=b'private attachment', name='note.txt', mime='text/plain'):
    return {'name': name, 'data_url': f'data:{mime};base64,' + base64.b64encode(data).decode()}


class MediaTests(unittest.TestCase):
    def test_reject_remote_malformed_path_oversize_and_fake_image(self):
        for f in [dict(name='../x', data_url='data:;base64,YQ=='), dict(name='x', data_url='https://example.com/x'),
                  attachment(b'not an image', 'x.png', 'image/png'), attachment(b'x' * (8*1024*1024+1))]:
            with self.assertRaises(HTTPException): decode_inputs([f])
    def test_images_input_integrity_and_native_output_path_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = save_inputs(root, 'r1', decode_inputs([attachment(png(), 'blue.png', 'image/png')]))
            f = input_files(root, inputs)[0]
            self.assertEqual(f['mime'], 'image/png')
            self.assertEqual(collect_files(root, {}, []), [])
            Path(f['absolute_path']).write_bytes(b'changed')
            with self.assertRaises(HTTPException): input_files(root, inputs)
            path = native_image(root, {'result': base64.b64encode(png()).decode()})
            output = collect_files(root, {}, [path])
            self.assertTrue(output[0][-1])
            self.assertEqual(output[0][-2], 'image/png')
            (root/'link.png').symlink_to(path)
            with self.assertRaises(ValueError): native_image(root, {'saved_path': str(root/'link.png')})
            with self.assertRaises(ValueError): native_image(root, {'saved_path': '/etc/passwd'})


class ImageModeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_codex_thread_registers_admin_document_write_tool(self):
        from app.runtime.business_tools import specifications
        from app.runtime.codex import CodexAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter('unused', Path(tmp), Path(tmp), 'model',
                                   managed=True, dynamic_tools=specifications())
            adapter.initialized = True
            adapter.rpc = SimpleNamespace(request=AsyncMock(return_value={'thread': {'id': 'thread'}}))
            await adapter.open_session(tmp, 'admin instructions')
            method, params = adapter.rpc.request.call_args.args
            self.assertEqual(method, 'thread/start')
            self.assertIn('jellyfish_write_document',
                          {tool['name'] for tool in params['dynamicTools']})
            self.assertIn('jellyfish_send_service_message',
                          {tool['name'] for tool in params['dynamicTools']})

    async def test_codex_session_config_respects_binding_and_scope(self):
        from app.runtime.codex import CodexAdapter
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter('unused', Path(tmp), Path(tmp), 'model', managed=True)
            adapter.initialized = True
            adapter.rpc = SimpleNamespace(request=AsyncMock(return_value={'thread': {'id': 'thread'}}))
            adapter.image_mode = 'off'
            await adapter.open_session(tmp, '')
            self.assertFalse(adapter.rpc.request.call_args.args[1]['config']['features.image_generation'])
            adapter.image_mode = 'native'
            await adapter.open_session(tmp, '')
            self.assertTrue(adapter.rpc.request.call_args.args[1]['config']['features.image_generation'])
            adapter.service_scope = {'image': True, 'web': False}
            adapter.image_mode = 'off'
            await adapter.open_session(tmp, '')
            self.assertFalse(adapter.rpc.request.call_args.args[1]['config']['features.image_generation'])

    async def test_cursor_image_callback_and_notification_reject_off(self):
        from app.runtime.cursor import CursorAdapter
        from test_runtime_cursor import FakeACP
        adapter = CursorAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'model')
        adapter.rpc = FakeACP()
        adapter.image_mode = 'off'
        request = {'id': 7, 'method': 'cursor/generate_image',
                   'params': {'toolCallId': 'image-1', 'filePath': '/tmp/work/image.png'}}
        self.assertEqual([event async for event in adapter.request_events(request)], [])
        self.assertEqual(adapter.rpc.sent[-1]['error']['code'], -32001)
        class ImageACP(FakeACP):
            async def request(self, method, params, **kwargs):
                if method == 'session/prompt':
                    self.events.put_nowait({'method': 'cursor/generate_image',
                                            'params': {'sessionId': 'thread', 'filePath': '/tmp/work/image.png'}})
                    return {'stopReason': 'end_turn'}
                return await super().request(method, params, **kwargs)
        adapter.rpc = ImageACP()
        with self.assertRaisesRegex(RuntimeFailure, '生图授权'):
            _ = [event async for event in adapter.stream_turn('thread', 'draw')]
        adapter.image_mode = 'native'
        self.assertEqual([event.type async for event in adapter.request_events(request)], ['image'])
        self.assertEqual(adapter.rpc.sent[-1]['result'], {})


class ImageModeRunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import test_runtime_service as service_tests
        await service_tests.ServiceTests.asyncSetUp(self)
        class ImageAdapter(service_tests.FakeAdapter):
            def __init__(self, gate, workspace):
                super().__init__(gate)
                self.workspace = workspace
            async def stream_turn(self, thread_id, text):
                yield RuntimeEvent('image', {'item_id': 'native-image',
                                             'result': base64.b64encode(png()).decode()})
                yield RuntimeEvent('completed', {})
        class ImageBackend(service_tests.FakeBackend):
            @contextlib.asynccontextmanager
            async def execution(self, session):
                adapter = ImageAdapter(self.gate, self.workspace(session))
                self.adapters.append(adapter)
                try:
                    yield adapter
                finally:
                    await adapter.close()
        self.backend = self.service.backend = ImageBackend(self.backend.root)
        self.writes = []
        self.service.storage = SimpleNamespace(write_bytes=lambda actor, path, data: self.writes.append((actor, path, data)))

    async def asyncTearDown(self):
        import test_runtime_service as service_tests
        await service_tests.ServiceTests.asyncTearDown(self)

    async def until(self, predicate):
        import test_runtime_service as service_tests
        await service_tests.ServiceTests.until(self, predicate)

    async def test_off_rejects_native_event_and_on_archives_image(self):
        off = self.service.create_session('a', {**self.binding, 'runtime': 'codex', 'image_mode': 'off'})
        run = self.service.enqueue('a', off['id'], 'off-image', 'draw')
        await self.until(lambda: self.store.get('run', run['id'])['status'] == 'failed')
        self.assertIn('未开启原生生图', self.store.get('run', run['id'])['error'])
        self.assertEqual(self.store.get('run', run['id'])['artifacts'], [])
        self.assertEqual(self.writes, [])
        native = self.service.create_session('a', {**self.binding, 'runtime': 'codex', 'image_mode': 'native'})
        run = self.service.enqueue('a', native['id'], 'on-image', 'draw')
        await self.until(lambda: self.store.get('run', run['id'])['status'] in ('completed', 'failed'))
        result = self.store.get('run', run['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertTrue(result['artifacts'][0]['native_image'])
        self.assertEqual(len(self.writes), 1)

    async def test_archive_rechecks_mode_even_if_native_path_is_injected(self):
        session = self.service.create_session('a', {**self.binding, 'runtime': 'codex', 'image_mode': 'off'})
        workspace = self.backend.workspace(session)
        path = workspace / 'native.png'
        path.write_bytes(png())
        state = {'run': {'id': 'forced', 'artifacts': []}, 'cancel': False, 'image_paths': [str(path.resolve())]}
        with self.assertRaisesRegex(RuntimeFailure, '未开启原生生图'):
            await self.service._archive(session, state)
        self.assertEqual(self.writes, [])

    async def test_service_scope_without_image_capability_rejects_native_event(self):
        session = self.service.create_session('a', {**self.binding, 'runtime': 'cursor',
            'image_mode': 'native', 'service_scope': {'image': False}})
        run = self.service.enqueue('a', session['id'], 'scope-no-image', 'draw')
        await self.until(lambda: self.store.get('run', run['id'])['status'] == 'failed')
        self.assertIn('未获得生图授权', self.store.get('run', run['id'])['error'])
        self.assertEqual(self.writes, [])


class AttachmentAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = chat_tests.ChatTests.asyncSetUp
    asyncTearDown = chat_tests.ChatTests.asyncTearDown
    new = chat_tests.ChatTests.new
    until = chat_tests.ChatTests.until
    async def test_file_only_idempotency_and_owner_only_download(self):
        conv = await self.new()
        body = {'conversation_id': conv['id'], 'request_id': 'attachment-request', 'message': '', 'attachments': [attachment()]}
        response = await self.client.post('/api/runtime/turns', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()
        self.assertNotIn('data_url', str(run['attachments']))
        repeat = await self.client.post('/api/runtime/turns', json=body)
        self.assertEqual(repeat.json()['id'], run['id'])
        body['attachments'] = [attachment(b'changed')]
        self.assertEqual((await self.client.post('/api/runtime/turns', json=body)).status_code, 409)
        url = f"/api/runtime/sessions/{conv['runtime_session_id']}/inputs/{run['attachments'][0]['id']}"
        self.assertEqual((await self.client.get(url)).content, b'private attachment')
        self.assertEqual((await self.client.get(url, headers={'Authorization': 'Bearer bob'})).status_code, 404)
        self.backend.gate.set()
        await self.until(lambda: self.store.get('run', run['id'])['status'] == 'completed')


class CursorPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_picker_does_not_skip_first_model_configuration(self):
        from app.runtime.cursor import CursorAdapter, credential_path
        from test_runtime_cursor import FakeACP, cursor_auth
        class ACP(FakeACP):
            async def request(self, method, params, **kwargs):
                result = await super().request(method, params, **kwargs)
                if method == 'session/new':
                    result['sessionId'] = 'thread-' + str(len(self.calls))
                    result['models']['currentModelId'] = 'model'
                    result['modes'] = {'currentModeId': 'agent'}
                return result
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth = credential_path(root); auth.parent.mkdir(parents=True); auth.write_bytes(cursor_auth())
            adapter = CursorAdapter('unused', root, root, 'model'); adapter.rpc = ACP()
            await adapter.open_session(tmp, '')
            self.assertEqual(sum(m == 'session/set_model' for m, _ in adapter.rpc.calls), 1)
            self.assertIn('session/set_model', adapter.prepare_timings)
            await adapter.open_session(tmp, '')
            self.assertEqual(sum(m == 'session/set_model' for m, _ in adapter.rpc.calls), 1)
            self.assertIn('session/new', adapter.prepare_timings)
            self.assertNotIn('session/set_model', adapter.prepare_timings)
            self.assertNotIn('session/set_mode', [m for m, _ in adapter.rpc.calls])
