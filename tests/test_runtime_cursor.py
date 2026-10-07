from app.core.host_auth import HOST_ID
import asyncio
import base64
import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from app.runtime.cursor import CursorAdapter, cursor_identity, cursor_environment, credential_path
from app.runtime.cursor_mcp import CursorMCP
from app.runtime.rpc import RuntimeFailure, RuntimeUnavailable


def cursor_auth(subject='cursor-one'):
    payload = base64.urlsafe_b64encode(json.dumps({'sub': subject}).encode()).decode().rstrip('=')
    return json.dumps({'accessToken': 'test.' + payload + '.test', 'refreshToken': 'refresh-test'}).encode()


class FakeACP:
    def __init__(self):
        self.events = asyncio.Queue()
        self.calls, self.sent = [], []
    async def start(self): pass
    async def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == 'initialize': return {'protocolVersion': 1, 'agentCapabilities': {'loadSession': True}}
        if method in ('session/new', 'session/load'):
            if method == 'session/load':
                self.events.put_nowait({'method': 'session/update', 'params': {'sessionId': 'thread', 'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'old history'}}}})
            return {'sessionId': 'thread', 'models': {'availableModels': [{'modelId': 'model', 'name': 'Model'}]}}
        if method == 'session/prompt':
            for text in ('Hello', ' world'):
                self.events.put_nowait({'method': 'session/update', 'params': {'sessionId': 'thread', 'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text}}}})
            return {'stopReason': 'end_turn'}
        return {}
    async def next_event(self): return await self.events.get()
    async def send(self, message): self.sent.append(message)
    async def capture_children(self): pass
    async def close(self): pass


class CursorAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_loaded_session_refreshes_mcp_catalog_before_resume(self):
        from app.runtime.business_tools import specifications
        old_tools = [{'name': 'jellyfish_list_documents'}]
        session = {'id': 'admin-session', 'thread_id': 'thread', 'dynamic_tools': specifications()}
        previous = CursorAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'model',
                                 dynamic_tools=old_tools)
        previous.loaded_threads.add('thread')
        previous.bridges['admin-session'] = SimpleNamespace(tools=old_tools)
        self.assertTrue(previous.needs_tool_refresh(session))
        self.assertFalse(previous.needs_tool_refresh({**session, 'dynamic_tools': old_tools}))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, workspace = root / 'home', root / 'workspace'
            auth = credential_path(home)
            auth.parent.mkdir(parents=True)
            auth.write_bytes(cursor_auth())
            adapter = CursorAdapter('unused', home, workspace, 'model',
                                    dynamic_tools=session['dynamic_tools'])
            adapter.rpc = FakeACP()
            adapter.session_key = session['id']
            async def call(_params):
                return {'contentItems': [{'text': 'ok'}], 'success': True}
            adapter.tool_call = call
            try:
                self.assertEqual(await adapter.open_session(str(workspace), 'instructions', 'thread'), 'thread')
                loaded = next(params for method, params in adapter.rpc.calls if method == 'session/load')
                self.assertEqual(len(loaded['mcpServers']), 1)
                headers = {'Authorization': 'Bearer ' + adapter.mcp.token}
                async with httpx.AsyncClient(trust_env=False) as client:
                    response = await client.post(loaded['mcpServers'][0]['url'], headers=headers,
                                                 json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn('jellyfish_write_document',
                              {tool['name'] for tool in response.json()['result']['tools']})
            finally:
                await adapter.close()

    async def test_prompt_response_usage_is_forwarded_when_supplier_reports_it(self):
        class MeteredACP(FakeACP):
            async def request(self, method, params, **kwargs):
                result = await super().request(method, params, **kwargs)
                if method == 'session/prompt':
                    result['usage'] = {'inputTokens': 15, 'outputTokens': 4, 'totalTokens': 19}
                return result

        adapter = CursorAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'model')
        adapter.rpc = MeteredACP()
        events = [event async for event in adapter.stream_turn('thread', 'hello')]
        self.assertEqual([event.payload['usage'] for event in events if event.type == 'usage'],
                         [{'inputTokens': 15, 'outputTokens': 4, 'totalTokens': 19}])

    async def test_missing_git_is_rejected_before_browser_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CursorAdapter('unused', tmp, tmp)
            with patch('app.runtime.cursor.shutil.which', return_value=None), \
                 patch('app.runtime.cursor.LoginProcess') as login:
                with self.assertRaisesRegex(RuntimeUnavailable, 'Git'):
                    await adapter.login_start()
                login.assert_not_called()
            await adapter.close()

    def test_private_environment_and_account_digest(self):
        with patch.dict(os.environ, {'CURSOR_API_KEY': 'must-not-copy', 'CODEX_HOME': '/owner'}):
            env = cursor_environment(Path('/private-home'))
        self.assertNotIn('CURSOR_API_KEY', env)
        self.assertNotIn('CODEX_HOME', env)
        self.assertEqual(env['AGENT_CLI_CREDENTIAL_STORE'], 'file')
        self.assertEqual(env['HOME'], '/private-home')
        self.assertEqual(cursor_identity(cursor_auth()), cursor_identity(cursor_auth()))
        self.assertNotEqual(cursor_identity(cursor_auth()), cursor_identity(cursor_auth('two')))
        with self.assertRaises(ValueError): cursor_identity(b'{"apiKey":"test"}')

    async def test_resume_model_binding_and_no_history_duplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); auth = credential_path(home)
            auth.parent.mkdir(parents=True); auth.write_bytes(cursor_auth())
            adapter = CursorAdapter('unused', home, home, 'model')
            adapter.rpc = FakeACP()
            self.assertEqual(await adapter.open_session(tmp, 'actor instructions', 'thread'), 'thread')
            events = [e async for e in adapter.stream_turn('thread', 'hello')]
            self.assertEqual(''.join(e.payload['text'] for e in events if e.type == 'text_delta'), 'Hello world')
            self.assertEqual(events[-1].type, 'completed')
            self.assertIn(('session/set_model', {'sessionId': 'thread', 'modelId': 'model'}), adapter.rpc.calls)
            await adapter.cancel()
            self.assertEqual(adapter.rpc.sent[-1], {'method': 'session/cancel', 'params': {'sessionId': 'thread'}})
            config = json.loads((home / '.cursor/cli-config.json').read_text())
            self.assertEqual(config['permissions']['allow'], [])

    async def test_project_discovery_stops_at_private_workspace_and_rejects_mcp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            home, work = root / 'home', root / 'workspace'
            adapter = CursorAdapter('unused', home, work, 'model')
            adapter._prepare_workspace()
            self.assertTrue((work / '.git/HEAD').is_file())
            config = work / '.cursor/mcp.json'
            config.parent.mkdir(); config.write_text('{}')
            second = CursorAdapter('unused', home, work, 'model')
            with self.assertRaises(RuntimeFailure): second._prepare_workspace()
            self.assertTrue(config.exists())

    async def test_unavailable_model_and_missing_auth_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); adapter = CursorAdapter('unused', home, home, 'removed-model')
            adapter.rpc = FakeACP()
            with self.assertRaises(RuntimeFailure): await adapter.open_session(tmp, '')
            self.assertEqual(adapter.rpc.calls, [])
            auth = credential_path(home); auth.parent.mkdir(parents=True); auth.write_bytes(cursor_auth())
            with self.assertRaises(RuntimeFailure): await adapter.open_session(tmp, '')
            self.assertFalse(any(m == 'session/prompt' for m, _ in adapter.rpc.calls))

    async def test_vendor_error_text_envelope_is_not_a_successful_turn(self):
        class ErrorACP(FakeACP):
            async def request(self, method, params, **kwargs):
                if method != 'session/prompt':
                    return await super().request(method, params, **kwargs)
                self.events.put_nowait({'method': 'session/update', 'params': {'sessionId': 'thread',
                    'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text',
                               'text': '\n\nError: RetriableError: [canceled] upstream stream closed'}}}})
                return {'stopReason': 'end_turn'}
        adapter = CursorAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'model')
        adapter.rpc = ErrorACP()
        with self.assertRaises(RuntimeFailure):
            _ = [event async for event in adapter.stream_turn('thread', 'hello')]

    async def test_mcp_requires_ephemeral_token_and_exposes_only_bound_tools(self):
        calls = []
        async def call(params):
            calls.append(params)
            return {'contentItems': [{'text': 'actor-only'}], 'success': True}
        bridge = CursorMCP([{'name': 'jellyfish_read_memory', 'description': 'read', 'inputSchema': {'type': 'object'}}], call)
        server = await bridge.start()
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                data = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'jellyfish_read_memory', 'arguments': {}}}
                self.assertEqual((await client.post(server['url'], json=data)).status_code, 403)
                headers = {'Authorization': 'Bearer ' + bridge.token}
                r = await client.post(server['url'], json=data, headers=headers)
                self.assertEqual(r.json()['result']['content'][0]['text'], 'actor-only')
                self.assertEqual((await client.post(server['url'], json=data, headers={**headers, 'Origin': 'https://untrusted.invalid'})).status_code, 403)
                data['params']['name'] = 'unknown'
                self.assertEqual((await client.post(server['url'], json=data, headers=headers)).status_code, 400)
                self.assertEqual(len(calls), 1)
        finally: await bridge.close()


class CursorConnectionRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_loaded_cursor_connection_is_rebuilt_for_new_tools(self):
        from app.runtime.business_tools import specifications
        from app.runtime.connection_backend import ConnectionBackend
        from app.runtime.store import RuntimeStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RuntimeStore(root / 'store')
            profile = {'id': 'cursor-profile', 'auth_generation': 1,
                       'credential_owner_id': HOST_ID, 'status': 'ready',
                       'runtime': 'cursor', 'models': [{'id': 'model'}]}
            store.put('profile', profile)
            closed, built = [], []

            @contextlib.asynccontextmanager
            async def lease(_binding, _home):
                yield

            async def close_adapter(_pid, adapter):
                closed.append(adapter)

            credentials = SimpleNamespace(
                store=store, get=lambda pid: store.get('profile', pid),
                binding=lambda actor, pid, model: {'runtime': 'cursor', 'profile_id': pid,
                                                    'model': model, 'auth_generation': 1},
                lease=lease, close_adapter=close_adapter)

            class FakeCursor:
                needs_tool_refresh = CursorAdapter.needs_tool_refresh

                def __init__(self):
                    self.loaded_threads = set()
                    self.bridges = {}
                    self.session_count = 1
                    self.rpc = SimpleNamespace(process=None, failure=None)

                async def warm_up(self):
                    return {}

                async def prepare_history(self, _session, _legacy_home):
                    return None

            def factory(*_args, **_kwargs):
                adapter = FakeCursor()
                built.append(adapter)
                return adapter

            backend = ConnectionBackend(root / 'backend', credentials, 'unused',
                                        max_clients=1, adapter_factory=factory)
            old_tools = [{'name': 'jellyfish_list_documents'}]
            session = {'id': 'admin-session', 'actor_id': 'alice',
                       'thread_id': 'thread', 'dynamic_tools': old_tools,
                       'binding': credentials.binding('alice', profile['id'], 'model')}
            try:
                async with backend.execution(session) as first:
                    first.loaded_threads.add('thread')
                    first.bridges[session['id']] = SimpleNamespace(tools=old_tools)
                    first.reusable = True
                session['dynamic_tools'] = specifications()
                async with backend.execution(session) as second:
                    self.assertIsNot(first, second)
                    self.assertIn(first, closed)
                    self.assertEqual(len(built), 2)
                    second.reusable = True
            finally:
                await backend.shutdown()
                store.close()

from test_runtime_profiles import ProfileFixture
from app.runtime.providers import CursorProvider
from fastapi import HTTPException


class CursorProfileTests(ProfileFixture):
    async def test_cursor_uses_shared_grants_and_encrypted_lease(self):
        self.manager.providers['cursor'] = CursorProvider('unused')
        p = self.manager.create(HOST_ID, 'cursor', 'cursor')
        p['models'] = [{'id': 'model', 'name': 'Model'}]
        self.manager.save_auth(p, cursor_auth())
        grant = self.manager.grant(HOST_ID, p['id'], 'admin', ['model'])
        binding = self.manager.binding('admin', p['id'], 'model')
        self.assertEqual(binding['runtime'], 'cursor')
        with self.assertRaises(HTTPException): self.manager.authorize('admin', {**binding, 'runtime': 'codex'})
        home = self.root / 'actor'
        async with self.manager.lease(binding, home):
            self.assertEqual(credential_path(home).read_bytes(), cursor_auth())
            self.assertFalse((home / 'auth.json').exists())
        self.assertFalse(credential_path(home).exists())
        self.assertNotIn(b'refresh-test', self.manager.vault.path(p['id']).read_bytes())
        await self.manager.revoke(HOST_ID, p['id'], grant['id'])
        with self.assertRaises(HTTPException): self.manager.authorize('admin', binding)
        await self.manager.disconnect(HOST_ID, p['id'])
        self.assertFalse(self.manager.vault.path(p['id']).exists())

    async def test_cursor_duplicate_account_and_changed_identity_fail_closed(self):
        self.manager.providers['cursor'] = CursorProvider('unused')
        p = self.manager.create(HOST_ID, 'cursor', 'cursor');p['models'] = [{'id': 'model'}]
        self.manager.save_auth(p, cursor_auth())
        other = self.manager.create(HOST_ID, 'another', 'cursor')
        with self.assertRaises(RuntimeFailure): self.manager.save_auth(other, cursor_auth())
        binding = self.manager.binding(HOST_ID, p['id'], 'model')
        self.manager.vault.write(p['id'], cursor_auth('changed'), validator=cursor_identity)
        with self.assertRaises(RuntimeFailure):
            async with self.manager.lease(binding, self.root / 'actor'):
                self.fail('changed account supplied')
        self.assertEqual(self.manager.get(p['id'])['status'], 'error')

class BrowserAuthAdapter:
    def __init__(self, home):
        self.home, self.rpc, self.closed = home, self, False
        self.events = asyncio.Queue()
    async def start(self): pass
    async def login_start(self): return {'loginId': 'browser', 'authUrl': 'https://cursor.com/loginDeepControl?test=true'}
    async def login_wait(self):
        if not await self.events.get(): raise RuntimeFailure('login rejected')
    async def account_models(self): return {'email': 'cursor@example.invalid', 'planType': 'Cursor'}, [{'id': 'model', 'name': 'Model'}]
    async def close(self): self.closed = True
    async def complete(self, subject='one', success=True):
        path = credential_path(self.home);path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(cursor_auth(subject))
        await self.events.put(success)


class BrowserProvider(CursorProvider):
    def adapter(self, home, *args): return BrowserAuthAdapter(home)


class CursorLoginTests(ProfileFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.manager.providers['cursor'] = BrowserProvider('unused')
        self.profile = self.manager.create(HOST_ID, 'Cursor', 'cursor')

    async def complete(self, subject='one'):
        attempt = await self.manager.start_login(HOST_ID, self.profile['id'], 'cursorBrowser')
        entry = self.manager.logins[attempt['id']]
        await entry['adapter'].complete(subject)
        await entry['task']
        return attempt

    async def test_browser_login_probe_and_account_switch_invalidate_binding(self):
        attempt = await self.complete()
        self.assertEqual(self.store.get('login', attempt['id'])['status'], 'completed')
        self.assertFalse((self.root / 'login' / attempt['id']).exists())
        binding = self.manager.binding(HOST_ID, self.profile['id'], 'model')
        self.assertEqual((await self.manager.probe(HOST_ID, self.profile['id']))['runtime'], 'cursor')
        await self.complete('other-account')
        with self.assertRaises(HTTPException): self.manager.authorize(HOST_ID, binding)

    async def test_adapter_construction_failure_does_not_leave_pending_login(self):
        with patch.object(self.manager.providers['cursor'], 'adapter', side_effect=RuntimeError('constructor failed')):
            with self.assertRaises(RuntimeError):
                await self.manager.start_login(HOST_ID, self.profile['id'], 'cursorBrowser')
        self.assertFalse(self.manager.logins)
        profile=self.manager.get(self.profile['id'])
        self.assertEqual(profile['status'], 'disconnected')
        self.assertFalse(profile['recovery_required'])
        self.assertIsNone(profile['login_id'])
        self.assertEqual(self.store.all('login')[0]['status'], 'failed')

    async def test_browser_cancel_preserves_old_connection(self):
        await self.complete()
        attempt = await self.manager.start_login(HOST_ID, self.profile['id'], 'cursorBrowser')
        adapter = self.manager.logins[attempt['id']]['adapter']
        await self.manager.cancel_login(HOST_ID, self.profile['id'], attempt['id'])
        self.assertTrue(adapter.closed)
        self.assertEqual(self.store.get('login', attempt['id'])['status'], 'cancelled')
        self.assertIsNone(self.store.get('login', attempt['id'])['challenge'])
        self.assertEqual(self.manager.get(self.profile['id'])['status'], 'ready')

    async def test_wrong_login_mode_and_non_owner_rejected_before_process(self):
        for actor, mode in [(HOST_ID, 'chatgpt'), ('admin', 'cursorBrowser')]:
            with self.assertRaises(HTTPException): await self.manager.start_login(actor, self.profile['id'], mode)
        self.assertFalse(self.manager.logins)

    async def test_browser_failure_and_expiration_release_reservation(self):
        for expired in (False, True):
            attempt = await self.manager.start_login(HOST_ID, self.profile['id'], 'cursorBrowser')
            entry = self.manager.logins[attempt['id']]
            if expired:
                entry['attempt']['expires_at'] = 0
            else:
                await entry['adapter'].complete(success=False)
            await entry['task']
            self.assertEqual(self.store.get('login',attempt['id'])['status'], 'expired' if expired else 'failed')
            self.assertTrue(entry['adapter'].closed)
            self.assertFalse(self.manager.logins)
            self.assertIsNone(self.manager.get(self.profile['id'])['login_id'])


class CursorPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_use_only_and_vendor_option_ids(self):
        a = CursorAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'model');a.rpc = FakeACP()
        msg = {'id': 7, 'method': 'session/request_permission', 'params': {
            'toolCall': {'kind': 'execute', 'title': 'echo hello'},
            'options': [{'kind':'allow_always','optionId':'persist'}, {'kind':'allow_once','optionId':'one'}, {'kind':'reject_once','optionId':'no'}]}}
        events = [e async for e in a.request_events(msg)]
        self.assertEqual(events[-1].payload['params']['availableDecisions'], ['accept','decline'])
        await a.respond(7, {'decision':'accept'})
        self.assertEqual(a.rpc.sent[-1]['result'], {'outcome':{'outcome':'selected','optionId':'one'}})
        msg['params']['options'] = [{'kind':'allow_always','optionId':'persist'}]
        events = [e async for e in a.request_events(msg)]
        self.assertEqual(events[-1].payload['params']['availableDecisions'], ['decline'])
        await a.respond(7, {'decision':'decline'})
        self.assertEqual(a.rpc.sent[-1]['result'], {'outcome':{'outcome':'cancelled'}})

    async def test_unknown_network_and_questions_never_auto_approved(self):
        a = CursorAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'model');a.rpc = FakeACP()
        for kind in ('fetch', 'search', 'other'):
            events=[e async for e in a.request_events({'id':kind,'method':'session/request_permission','params':{'toolCall':{'kind':kind,'title':'external'},'options':[{'kind':'allow_once','optionId':'one'}]}})]
            self.assertEqual(events[-1].payload['params']['command'], 'external' if kind in ('fetch', 'search') else None)
            self.assertEqual(events[-1].type, 'request')  # Still requires a one-time user approval.
        events=[e async for e in a.request_events({'id':1,'method':'cursor/ask_question','params':{}})]
        self.assertEqual(a.rpc.sent[-1]['result']['outcome']['outcome'], 'skipped')
        events=[e async for e in a.request_events({'id':2,'method':'unknown','params':{}})]
        self.assertEqual(events[0].payload['method'],'unknown')

    async def test_real_cursor_mcp_title_matches_only_registered_business_tools(self):
        adapter = CursorAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'model',
                                dynamic_tools=[{'name': 'jellyfish_list_documents'}])
        for title, accepted in [('jellyfish-jellyfish_list_documents: jellyfish_list_documents', True),
                                ('jellyfish: jellyfish_list_documents', True),
                                ('jellyfish: unknown_tool', False),
                                ('other-jellyfish_list_documents: jellyfish_list_documents', False)]:
            events = [e async for e in adapter.request_events({'id': 5, 'method': 'session/request_permission',
                'params': {'toolCall': {'kind': 'other', 'title': title},
                           'options': [{'kind': 'allow_once', 'optionId': 'one'}]}})]
            self.assertEqual(events[-1].payload['params']['command'], title if accepted else None)

    async def test_file_changes_preserve_paths_for_shared_boundary_check(self):
        a = CursorAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'model')
        events=[e async for e in a.request_events({'id':7,'method':'session/request_permission','params':{'toolCall':{'toolCallId':'file','kind':'edit','content':[{'type':'diff','path':'../outside','newText':'danger'}]},'options':[{'kind':'allow_once','optionId':'one'}]}})]
        self.assertEqual(events[0].payload['changes'][0]['path'],'../outside')
        self.assertEqual(events[-1].payload['method'],'item/fileChange/requestApproval')

import test_runtime_chat as chat_tests


class CursorChatTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = chat_tests.ChatTests.asyncSetUp
    asyncTearDown = chat_tests.ChatTests.asyncTearDown
    until = chat_tests.ChatTests.until

    async def test_cursor_main_chat_binding_and_cross_provider_rejection(self):
        self.profiles.providers['cursor'] = BrowserProvider('unused')
        p = self.profiles.create(HOST_ID,'Cursor','cursor');p['models']=[{'id':'model','name':'Model'}]
        self.profiles.save_auth(p,cursor_auth())
        self.profiles.grant(HOST_ID,p['id'],'alice',['model'])
        mismatch=await self.client.post('/api/conversations',json={'runtime_choice':{'runtime':'codex','profile_id':p['id'],'model':'model'}})
        self.assertEqual(mismatch.status_code,400)
        r=await self.client.post('/api/conversations',json={'runtime_choice':{'runtime':'cursor','profile_id':p['id'],'model':'model'}})
        self.assertEqual(r.status_code,200,r.text);conv=r.json()
        self.assertEqual(conv['runtime_binding']['runtime'],'cursor')
        self.backend.gate.set()
        r=await self.client.post('/api/chat',json={'conversation_id':conv['id'],'request_id':'cursor-request-0001','message':'hello'})
        self.assertEqual(r.status_code,200,r.text);self.assertIn('first',r.text)
        history=(await self.client.get('/api/conversations/'+conv['id'])).json()
        self.assertEqual(history['runtime_binding']['runtime'],'cursor')
        self.assertEqual((await self.client.get('/api/runtime/sessions/'+conv['runtime_session_id'],headers={'Authorization':'Bearer bob'})).status_code,404)
        r=await self.client.put('/api/runtime/preferences',json={'runtime':'cursor','profile_id':p['id'],'model':'model'})
        self.assertEqual(r.status_code,200,r.text)
        implicit=(await self.client.post('/api/conversations',json={})).json()
        self.assertEqual(implicit['runtime_binding']['runtime'],'cursor')

    async def test_cursor_business_bridge_uses_actor_and_rechecks_revocation(self):
        self.profiles.providers['cursor']=BrowserProvider('unused')
        p=self.profiles.create(HOST_ID,'Cursor','cursor');p['models']=[{'id':'model'}]
        self.profiles.save_auth(p,cursor_auth());g=self.profiles.grant(HOST_ID,p['id'],'alice',['model'])
        session=self.runs.create_session('alice',self.profiles.binding('alice',p['id'],'model'),
                                         conversation_id='admin-chat')
        run={'id':'mcp-run','actor_id':'alice','binding':session['binding'],'seq':0,'status':'running'}
        self.store.put('run',run)
        business=__import__('app.runtime.business_tools',fromlist=['BusinessTools']).BusinessTools(self.storage,self.profiles.authorize,self.store)
        self.storage.write_bytes('alice','/docs/own.txt',b'alice document')
        self.storage.write_bytes('bob','/docs/own.txt',b'bob secret')
        route_reply_through_approval = False
        reply_state = {'run': run, 'answer': None, 'cancel': False, 'finalizing': False}
        self.runs.tool_bridge = business
        async def call(params):
            if route_reply_through_approval and params.get('tool') == 'jellyfish_send_service_message':
                return await self.runs._call_business_tool(session, reply_state, params)
            return await business(session, run, params)
        specs=__import__('app.runtime.business_tools',fromlist=['specifications']).specifications()
        # Check actor-bound MCP transport, then enable the production reply
        # approval gate. The next test covers document-write approval.
        bridge=CursorMCP(specs,call);server=await bridge.start()
        try:
            async with httpx.AsyncClient(trust_env=False) as c:
                headers={'Authorization':'Bearer '+bridge.token}
                listed=await c.post(server['url'],headers=headers,json={
                    'jsonrpc':'2.0','id':0,'method':'tools/list'})
                self.assertIn('jellyfish_write_document',
                              {tool['name'] for tool in listed.json()['result']['tools']})
                self.assertIn('jellyfish_send_service_message',
                              {tool['name'] for tool in listed.json()['result']['tools']})
                queued={'message':{'id':'msg-cursor'}, 'deliveries':[
                    {'id':'delivery-cursor', 'channel':'web', 'status':'pending'}]}
                target = {'owner_id': 'alice', 'service_id': 'svc',
                          'conversation_id': 'consumer-c1', 'source': 'web'}
                with patch('app.services.service_messaging.send_service_message', return_value=queued) as send, \
                        patch.object(self.runs, '_service_message_target', return_value=target):
                    request = {'jsonrpc':'2.0','id':8,'method':'tools/call','params':{
                        'name':'jellyfish_send_service_message',
                        'arguments':{'service_id':'svc','conversation_id':'consumer-c1',
                                     'message':'Cursor reply'}}}
                    reply=await c.post(server['url'],headers=headers,json=request)
                    self.assertEqual(reply.status_code,200,reply.text)
                    self.assertTrue(reply.json()['result']['isError'],reply.text)
                    self.assertIn('确认',reply.json()['result']['content'][0]['text'])
                    send.assert_not_called()

                    # Route through the real approval gate; only a trusted YOLO
                    # turn may supply the approved destination to the bridge.
                    route_reply_through_approval = True
                    run['yolo'] = True
                    reply=await c.post(server['url'],headers=headers,json=request)
                    self.assertEqual(reply.status_code,200,reply.text)
                    self.assertFalse(reply.json()['result']['isError'],reply.text)
                    self.assertIn('msg-cursor',reply.text)
                    send.assert_called_once_with('alice','svc','consumer-c1','Cursor reply',
                        inbox_id=None,idempotency_key='cli:mcp-run:8',expected_target=target)
                    resolved = [event for event in self.store.events(run['id'])
                                if event['type'] == 'approval_resolved']
                    self.assertTrue(any(event['payload'].get('automatic') for event in resolved))
                doc_path='/docs/寻味江南-2026年十月菜单.md'
                content='# 寻味江南\n\n桂花糖藕。\n'
                saved=await c.post(server['url'],headers=headers,json={
                    'jsonrpc':'2.0','id':2,'method':'tools/call','params':{
                        'name':'jellyfish_write_document',
                        'arguments':{'path':doc_path,'content':content}}})
                self.assertEqual(saved.status_code,200,saved.text)
                self.assertFalse(saved.json()['result']['isError'],saved.text)
                self.assertEqual(self.storage.read_text('alice',doc_path),content)
                self.assertFalse(self.storage.is_file('bob',doc_path))
                documents=await c.post(server['url'],headers=headers,json={
                    'jsonrpc':'2.0','id':3,'method':'tools/call','params':{
                        'name':'jellyfish_list_documents','arguments':{'path':'/docs'}}})
                doc_items=json.loads(documents.json()['result']['content'][0]['text'])
                self.assertIn('寻味江南-2026年十月菜单.md',
                              {item['name'] for item in doc_items})
                saved_read=await c.post(server['url'],headers=headers,json={
                    'jsonrpc':'2.0','id':4,'method':'tools/call','params':{
                        'name':'jellyfish_read_document','arguments':{'path':doc_path}}})
                self.assertEqual(saved_read.json()['result']['content'][0]['text'],content)

                # Cursor's MCP transport must carry the full 64 KiB UTF-8
                # document even after the JSON-RPC envelope is added.
                limit_path='/docs/mcp-size-limit.md'
                boundary=await c.post(server['url'],headers=headers,json={
                    'jsonrpc':'2.0','id':5,'method':'tools/call','params':{
                        'name':'jellyfish_write_document',
                        'arguments':{'path':limit_path,'content':'x'*65536}}})
                self.assertEqual(boundary.status_code,200,boundary.text[:200])
                self.assertFalse(boundary.json()['result']['isError'],boundary.text[:200])
                self.assertEqual(len(self.storage.read_bytes('alice',limit_path)),65536)
                req={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'jellyfish_read_document','arguments':{'path':'/docs/own.txt'}}}
                r=await c.post(server['url'],headers=headers,json=req)
                self.assertIn('alice document',r.text);self.assertNotIn('bob secret',r.text)
                req['params']['arguments']['actor_id']='bob'
                r=await c.post(server['url'],headers=headers,json=req)
                self.assertTrue(r.json()['result']['isError'])
                await self.profiles.revoke(HOST_ID,p['id'],g['id'])
                r=await c.post(server['url'],headers=headers,json={
                    'jsonrpc':'2.0','id':6,'method':'tools/call','params':{
                        'name':'jellyfish_write_document',
                        'arguments':{'path':'/docs/after-revocation.md','content':'blocked'}}})
                self.assertEqual(r.json()['error']['code'],-32001)
                self.assertFalse(self.storage.is_file('alice','/docs/after-revocation.md'))
        finally:await bridge.close()

    async def test_cursor_mcp_tool_callback_waits_for_document_write_approval(self):
        from app.runtime.business_tools import BusinessTools
        from app.runtime.store import TERMINAL
        from app.runtime.types import RuntimeEvent
        import test_runtime_service as service_tests

        class ToolAdapter(service_tests.FakeAdapter):
            def __init__(self, gate, path, content):
                super().__init__(gate)
                self.path, self.content, self.result = path, content, None

            async def stream_turn(self, _thread_id, _text):
                # CursorMCP calls this exact per-turn closure after receiving
                # tools/call on its loopback endpoint.
                self.result = await self.tool_call({
                    'tool': 'jellyfish_write_document',
                    'arguments': {'path': self.path, 'content': self.content},
                    'callId': 'cursor-document-write',
                })
                yield RuntimeEvent('completed', {})

        class ToolBackend(service_tests.FakeBackend):
            path, content = '', ''

            @contextlib.asynccontextmanager
            async def execution(self, _session):
                adapter = ToolAdapter(self.gate, self.path, self.content)
                self.adapters.append(adapter)
                try:
                    yield adapter
                finally:
                    await adapter.close()

        self.profiles.providers['cursor'] = BrowserProvider('unused')
        profile = self.profiles.create(HOST_ID, 'Cursor', 'cursor')
        profile['models'] = [{'id': 'model', 'name': 'Model'}]
        self.profiles.save_auth(profile, cursor_auth())
        self.profiles.grant(HOST_ID, profile['id'], 'alice', ['model'])
        binding = self.profiles.binding('alice', profile['id'], 'model')
        self.backend = self.manager.backend = self.runs.backend = ToolBackend(self.backend.root)
        self.runs.tool_bridge = BusinessTools(self.storage, self.profiles.authorize, self.store)
        session = self.runs.create_session('alice', binding, conversation_id='admin-chat')

        for decision, yolo in (('accept', False), ('decline', False), ('accept', True)):
            label = 'automatic' if yolo else decision
            with self.subTest(label=label):
                path = f'/docs/cursor-{label}.md'
                self.backend.path, self.backend.content = path, label
                run = self.runs.enqueue('alice', session['id'], f'cursor-{label}',
                                        f'write {label}', yolo=yolo)
                if not yolo:
                    await self.until(lambda: self.store.get('run', run['id'])['status'] == 'waiting_approval')
                    pending = self.store.get('run', run['id'])['pending']
                    self.assertEqual(pending['allowed'], ['accept', 'decline'])
                    self.assertFalse(self.storage.is_file('alice', path))
                    self.runs.approve('alice', run['id'], pending['id'], decision)
                await self.until(lambda: self.store.get('run', run['id'])['status'] in TERMINAL)
                self.assertEqual(self.store.get('run', run['id'])['status'], 'completed')
                result = self.backend.adapters[-1].result
                self.assertEqual(result['success'], decision == 'accept')
                self.assertEqual(self.storage.is_file('alice', path), decision == 'accept')
                events = self.store.events(run['id'])
                if yolo:
                    self.assertNotIn('approval_requested', [event['type'] for event in events])
                    self.assertTrue(any(event['type'] == 'approval_resolved' and
                                        event['payload'].get('automatic') for event in events))
                else:
                    self.assertIn('approval_requested', [event['type'] for event in events])


import test_runtime_service as service_tests


class CursorServiceApprovalTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = service_tests.ServiceTests.asyncSetUp
    asyncTearDown = service_tests.ServiceTests.asyncTearDown
    until = service_tests.ServiceTests.until

    async def prepare(self, tool):
        session = self.service.create_session('a', {**self.binding, 'runtime': 'cursor'})
        adapter = CursorAdapter('unused', self.store.root / 'home', self.backend.workspace(session), 'test')
        adapter.rpc = FakeACP()
        run = {'id': 'approval-run', 'actor_id': 'a', 'binding': session['binding'], 'seq': 0, 'status': 'running'}
        state = {'adapter': adapter, 'run': run, 'changes': {}, 'answer': None}
        events = [e async for e in adapter.request_events({'id': 7, 'method': 'session/request_permission',
            'params': {'toolCall': tool, 'options': [{'kind': 'allow_once', 'optionId': 'one'},
                                                   {'kind': 'reject_once', 'optionId': 'no'}]}})]
        for event in events:
            if event.type == 'tool':
                state['changes'][event.payload['item_id']] = event.payload['changes']
        task = asyncio.create_task(self.service._request(session, state, events[-1].payload))
        await self.until(lambda: state['answer'] is not None)
        return session, state, task

    async def test_outside_workspace_cannot_be_accepted(self):
        _, state, task = await self.prepare({'kind': 'edit', 'toolCallId': 'write',
            'content': [{'type': 'diff', 'path': '../outside.txt', 'newText': 'unsafe'}]})
        self.assertEqual(state['run']['pending']['allowed'], ['decline'])
        state['answer'].set_result('decline')
        await task
        self.assertEqual(state['adapter'].rpc.sent[-1]['result']['outcome']['optionId'], 'no')

    async def test_late_approval_after_revocation_is_not_sent_to_cursor(self):
        _, state, task = await self.prepare({'kind': 'execute', 'title': 'echo hello'})
        self.revoked.add('a')
        state['answer'].set_result('accept')
        with self.assertRaises(HTTPException):
            await task
        self.assertEqual(state['adapter'].rpc.sent, [])

    async def test_plan_extensions_use_vendor_nested_outcome(self):
        adapter = CursorAdapter('unused', self.store.root, self.store.root, 'test')
        adapter.rpc = FakeACP()
        for decision, expected in [('accept', 'accepted'), ('decline', 'rejected')]:
            events = [e async for e in adapter.request_events({'id': 9, 'method': 'cursor/create_plan',
                'params': {'name': 'test', 'plan': 'First do one thing, then another.'}})]
            self.assertEqual(events[0].type, 'request')
            self.assertEqual(events[0].payload['method'], 'plan/requestApproval')
            self.assertEqual(events[0].payload['params'], {
                'title': 'test', 'plan': 'First do one thing, then another.',
                'availableDecisions': ['accept', 'decline']})
            await adapter.respond(9, {'decision': decision})
            self.assertEqual(adapter.rpc.sent[-1]['result'], {'outcome': {'outcome': expected}})


class CursorBackendTests(ProfileFixture):
    async def test_binding_selects_cursor_provider_without_codex_fallback(self):
        from app.runtime.backend import LocalBackend
        provider = BrowserProvider('unused')
        self.manager.providers['cursor'] = provider
        profile = self.manager.create(HOST_ID, 'Cursor', 'cursor')
        profile['models'] = [{'id': 'model'}]
        self.manager.save_auth(profile, cursor_auth())
        session = {'id': 'session', 'actor_id': HOST_ID,
                   'binding': self.manager.binding(HOST_ID, profile['id'], 'model')}
        backend = LocalBackend(self.root, self.manager, 'codex-must-not-run')
        with patch.object(self.manager.providers['codex'], 'adapter', side_effect=AssertionError('wrong provider')):
            async with backend.execution(session) as adapter:
                self.assertIsInstance(adapter, BrowserAuthAdapter)
                self.assertTrue(credential_path(adapter.home).exists())
            self.assertTrue(adapter.closed)
            self.assertFalse(credential_path(adapter.home).exists())
