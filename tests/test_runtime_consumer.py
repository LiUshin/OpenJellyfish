"""Real HTTP/stream/storage boundaries with deterministic supplier protocol fixtures."""
import asyncio
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from fastapi import HTTPException
from app.core.host_auth import HOST_ID
from app.routes.services import router as services_router
from app.routes.consumer import router as consumer_router
from app.runtime.business_tools import BusinessTools
from app.runtime.consumer import RuntimeConsumerAgent, service_authorizer
from app.runtime.consumer_tools import ServiceTools, Directory, Document, History, Script, WriteFile
from app.runtime.providers import CursorProvider
from app.runtime.rpc import RuntimeFailure
from app.runtime.types import RuntimeEvent
from app.services import published
import test_runtime_chat as chat_tests
from test_runtime_cursor import cursor_auth, FakeACP


class Adapter:
    def __init__(self, backend, session):
        self.backend, self.session = backend, session
        self.responses = []
    async def open_session(self, work, instructions, thread_id):
        self.work = Path(work)
        self.backend.opened.append((self.session['id'], thread_id, instructions, self.dynamic_tools))
        return thread_id or self.session['id']
    async def stream_turn(self, thread_id, text):
        yield RuntimeEvent('text_delta', {'text': 'hello'})
        await self.backend.gate.wait()
        if 'fail-provider' in text:
            raise RuntimeFailure('fixture provider failed')
        if 'tool-document' in text:
            result = await self.tool_call({'tool': 'jellyfish_service_read_document', 'arguments': {'path': '/docs/open.txt'}})
            self.backend.results.append(result)
        if 'tool-script' in text:
            result = await self.tool_call({'tool': 'jellyfish_service_run_script', 'arguments': {'script_path': 'open.py'}})
            self.backend.results.append(result)
        if 'native-approval' in text:
            yield RuntimeEvent('request', {'request_id': 1, 'method': 'item/commandExecution/requestApproval',
                                          'params': {'command': 'cat host-private-file', 'availableDecisions': ['accept', 'cancel']}})
        if 'private-tool-details' in text:
            yield RuntimeEvent('tool', {'item_id': 'private', 'kind': 'execute', 'status': 'in_progress'})
            yield RuntimeEvent('tool', {'item_id': 'private', 'result_delta': 'PRIVATE_PARTIAL_OUTPUT'})
            yield RuntimeEvent('tool', {'item_id': 'private', 'kind': 'execute', 'status': 'completed', 'result': 'PRIVATE_NATIVE_TOOL_OUTPUT', 'input': {'command': 'PRIVATE_INPUT'}})
        if 'make-artifact' in text:
            (self.work / 'result.txt').write_text('service artifact')
        yield RuntimeEvent('usage', {'usage': {'last': {'inputTokens': 12, 'outputTokens': 3, 'totalTokens': 15}}})
        yield RuntimeEvent('completed', {})
    async def respond(self, _, value): self.responses.append(value)
    async def cancel(self): pass


class Backend:
    def __init__(self, root):
        self.root, self.gate = root, asyncio.Event()
        self.opened, self.results, self.adapters = [], [], []
    def workspace(self, session): return self.root / session['id']
    @contextlib.asynccontextmanager
    async def execution(self, session):
        adapter = Adapter(self, session)
        self.adapters.append(adapter)
        yield adapter


class ConsumerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = chat_tests.ChatTests.asyncTearDown
    until = chat_tests.ChatTests.until
    async def asyncSetUp(self):
        await chat_tests.ChatTests.asyncSetUp(self)
        self.app.include_router(services_router)
        self.app.include_router(consumer_router)
        self.runs.authorize = service_authorizer(self.profiles)
        self.runs.tool_bridge = BusinessTools(self.storage, self.runs.authorize, self.store)
        self.backend = Backend(self.root / 'service-work')
        self.manager.backend = self.runs.backend = self.backend
        self.backend.gate.set()
        self.patches.append(patch('app.routes.consumer.build_usage_callbacks', return_value=[]))
        self.patches[-1].start()
        p = self.profiles.get(self.pid)
        p['models'].append({'id': 'ungranted', 'name': 'Denied'})
        self.store.put('profile', p)
        self.svc, self.key, self.conv = await self.new_service()

    async def new_service(self, provider='codex', **updates):
        pid = self.pid
        if provider == 'cursor':
            self.profiles.providers['cursor'] = CursorProvider('fake-cursor')
            p = self.profiles.create(HOST_ID, 'Cursor', runtime='cursor')
            p['models'] = [{'id': 'model', 'name': 'Model'}]
            self.profiles.save_auth(p, cursor_auth())
            pid = p['id']
            self.profiles.grant(HOST_ID, pid, 'alice', ['model'])
        data = {'name': 'Internal', 'model': 'model', 'allowed_docs': ['open.txt'], 'allowed_scripts': [],
                'capabilities': [], 'runtime_choice': {'runtime': provider, 'profile_id': pid, 'model': 'model'}, **updates}
        response = await self.client.post('/api/services', json=data)
        self.assertEqual(response.status_code, 200, response.text)
        svc = response.json()
        key = published.create_service_key('alice', svc['id'])
        conv = published.create_consumer_conversation('alice', svc['id'])
        return svc, key, conv

    def agent(self, **kwargs):
        return RuntimeConsumerAgent('alice', self.svc['id'], self.conv['id'], key_id=self.key['id'], **kwargs)

    def headers(self, key=None):
        return {'Authorization': 'Bearer ' + (key or self.key)['key']}

    async def complete(self, stream=False, text='tool-document', **overrides):
        return await self.client.post('/api/v1/chat/completions', headers=self.headers(), json={
            'conversation_id': self.conv['id'], 'messages': [{'role': 'user', 'content': text}], 'stream': stream, **overrides})

    async def test_web_api_stream_and_nonstream_use_same_service_grant(self):
        self.storage.write_bytes('alice', '/docs/open.txt', b'allowed document')
        with patch('deepagents.create_deep_agent') as legacy:
            for provider in ('codex', 'cursor'):
                if provider == 'cursor':
                    self.svc, self.key, self.conv = await self.new_service('cursor')
                web = await self.client.post('/api/v1/chat', headers=self.headers(), json={
                    'conversation_id': self.conv['id'], 'message': 'tool-document'})
                self.assertEqual(web.status_code, 200, web.text)
                self.assertIn('"type": "token"', web.text)
                self.assertIn('"type": "tool_result"', web.text)
                api = await self.complete(True)
                self.assertIn('chat.completion.chunk', api.text)
                self.assertIn('[DONE]', api.text)
                full = await self.complete()
                self.assertEqual(full.json()['choices'][0]['message']['content'], 'hello')
                self.assertEqual(full.json()['usage']['total_tokens'], 15)
                self.assertEqual(full.json()['model'], 'model')
                runs = sorted(self.store.all('run'), key=lambda r: r['created_at'])
                self.assertEqual(runs[-1]['binding']['runtime'], provider)
                self.assertEqual(runs[-1]['binding']['credential_owner_id'], HOST_ID)
            legacy.assert_not_called()
        self.assertTrue(all(r['success'] for r in self.backend.results))

    async def test_public_stream_does_not_forward_native_details(self):
        response = await self.client.post('/api/v1/chat', headers=self.headers(), json={
            'conversation_id': self.conv['id'], 'message': 'private-tool-details'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('PRIVATE_NATIVE_TOOL_OUTPUT', response.text)
        self.assertNotIn('PRIVATE_INPUT', response.text)
        self.assertNotIn('PRIVATE_PARTIAL_OUTPUT', response.text)
        self.assertEqual(response.text.count('\"type\": \"tool_result\"'), 1)

    async def test_continuation_scope_and_config_change(self):
        first = self.agent()
        self.assertEqual(first.session['id'], self.agent().session['id'])
        old = first.session['id']
        await self.complete(text='hello')
        await self.complete(text='again')
        self.assertEqual(self.backend.opened[-1][0], self.backend.opened[-2][0])
        self.assertIsNotNone(self.backend.opened[-1][1])
        other = published.create_consumer_conversation('alice', self.svc['id'])
        different = RuntimeConsumerAgent('alice', self.svc['id'], other['id'], key_id=self.key['id'])
        self.assertNotEqual(different.session['id'], old)
        await self.client.put('/api/services/' + self.svc['id'], json={'allowed_docs': []})
        self.assertNotEqual(self.agent().session['id'], old)
        with self.assertRaises(HTTPException): self.runs.authorize('alice', first.session['binding'])

    async def test_ungranted_foreign_profile_invalid_model_byok_and_credentials_rejected(self):
        for choice in ({'runtime':'codex', 'profile_id':self.pid, 'model':'ungranted'},
                       {'runtime':'cursor', 'profile_id':self.pid, 'model':'model'}):
            r = await self.client.put('/api/services/' + self.svc['id'], json={'runtime_choice': choice})
            self.assertIn(r.status_code, (400,403), r.text)
        foreign = await self.client.post('/api/services', headers={'Authorization':'Bearer bob'}, json={
            'name':'bad', 'model':'model', 'runtime_choice':self.svc['runtime_choice']})
        self.assertEqual(foreign.status_code,403)
        r = await self.client.post('/api/services/' + self.svc['id'] + '/keys', json={'billing':'byok'})
        self.assertEqual(r.status_code,400)
        self.assertEqual((await self.complete(api_key='not-a-real-key')).status_code,400)
        byok = published.create_service_key('alice', self.svc['id'], billing='byok')
        r = await self.client.post('/api/v1/chat', headers=self.headers(byok), json={'conversation_id':self.conv['id'], 'message':'hello'})
        self.assertEqual(r.status_code,400)
        self.assertFalse(self.store.all('run'))

    async def test_pending_and_running_runs_stop_on_key_delete_service_unpublish_or_grant_revoke(self):
        for action in ('key', 'service', 'grant'):
            if action != 'key': self.svc, self.key, self.conv = await self.new_service()
            self.backend.gate.clear()
            a = self.agent()
            r = self.runs.enqueue('alice', a.session['id'], action, 'waiting')
            await self.until(lambda: self.store.get('run',r['id'])['status']=='running')
            conv2 = published.create_consumer_conversation('alice', self.svc['id'])
            a2 = RuntimeConsumerAgent('alice',self.svc['id'],conv2['id'],key_id=self.key['id'])
            r2 = self.runs.enqueue('alice',a2.session['id'],action+'queued','waiting')
            if action == 'key': published.delete_service_key('alice',self.svc['id'],self.key['id'])
            elif action == 'service': published.update_service('alice',self.svc['id'],{'published':False})
            else:
                g = self.store.find('grant',profile_id=self.pid,actor_id='alice')[0]
                g['enabled']=False; self.store.put('grant',g)
            self.runs.changed.set()
            await self.until(lambda: self.store.get('run',r['id'])['status']=='cancelled')
            await self.until(lambda: self.store.get('run',r2['id'])['status']=='cancelled')

    async def test_native_approval_denied_artifact_archives_to_consumer_only(self):
        r = await self.complete(text='native-approval make-artifact')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.backend.adapters[-1].responses,[{'decision':'cancel'}])
        artifact = self.store.all('run')[-1]['artifacts'][0]
        self.assertEqual(self.storage.read_consumer_bytes('alice',self.svc['id'],self.conv['id'],artifact['path'].removeprefix('/generated/')),b'service artifact')
        self.assertFalse(self.storage.is_file('alice', artifact['path']))
        self.assertIn('<<FILE:',r.json()['choices'][0]['message']['content'])
        instructions = self.backend.opened[-1][2]
        self.assertIn('内部 Service',instructions)
        self.assertNotIn('jellyfish_update_memory',instructions)
        self.assertTrue(all(t['name'].startswith('jellyfish_service_') for t in self.backend.opened[-1][3]))

    async def test_documents_memory_generated_paths_and_scripts_are_service_scoped(self):
        self.storage.write_bytes('alice','/docs/open.txt',b'open')
        self.storage.write_bytes('alice','/docs/private.txt',b'private')
        self.storage.write_bytes('bob','/docs/open.txt',b'bob-private')
        published.save_consumer_message('alice',self.svc['id'],self.conv['id'],'user','my-history')
        a = self.agent(); binding = a.session['binding']
        tools = ServiceTools(self.storage,self.store,self.runs.authorize)
        svc = published.get_service('alice',self.svc['id'])
        def invoke(name,args): return tools.invoke('alice',binding,svc,name,args)
        self.assertEqual(invoke('list_documents',Directory()),[{'path':'/docs/open.txt','is_dir':False,'size':4}])
        self.assertEqual(invoke('read_document',Document(path='/docs/open.txt')),'open')
        for path in ('/docs/private.txt','/docs/open.txt/../private.txt','../bob/docs/open.txt'):
            with self.assertRaises(ValueError): invoke('read_document',Document(path=path))
        from app.core.security import get_user_filesystem_dir
        root = Path(get_user_filesystem_dir('alice'))
        (root/'docs/open.txt').unlink(); (root/'docs/open.txt').symlink_to(root/'docs/private.txt')
        with self.assertRaises(ValueError): invoke('read_document',Document(path='/docs/open.txt'))
        svc['allowed_docs']=[]
        self.assertEqual(invoke('list_documents',Directory()),[])
        self.assertEqual(invoke('read_my_conversation',History())[0]['content'],'my-history')
        self.assertIn('<<FILE:',invoke('write_file',WriteFile(path='/generated/note.md',content='mine')))
        with self.assertRaises(ValueError): invoke('write_file',WriteFile(path='../escape.md',content='no'))
        with self.assertRaises(ValueError): tools.script('alice',binding,svc,Script(script_path='no.py'))
        # Even a forged call to the admin memory tool is denied by dispatch.
        result = await self.runs.tool_bridge(a.session, {}, {'tool':'jellyfish_read_memory','arguments':{}})
        self.assertFalse(result['success'])

    async def test_script_receives_only_published_inputs_and_never_unrestricted(self):
        self.storage.write_bytes('alice','/docs/open.txt',b'open')
        self.storage.write_bytes('alice','/docs/private.txt',b'private')
        self.storage.write_bytes('alice','/scripts/open.py',b'print("hi")')
        self.storage.write_bytes('alice','/scripts/private.py',b'print("private")')
        await self.client.put('/api/services/'+self.svc['id'],json={'allowed_scripts':['open.py']})
        a=self.agent();svc=published.get_service('alice',self.svc['id'])
        def runner(**kwargs):
            scripts=Path(kwargs['scripts_dir']);docs=scripts.parent/'docs'
            self.assertTrue((scripts/'open.py').exists());self.assertFalse((scripts/'private.py').exists())
            self.assertTrue((docs/'open.txt').exists());self.assertFalse((docs/'private.txt').exists())
            self.assertFalse(kwargs['unrestricted'])
            (scripts.parent/'generated/out.txt').write_text('done')
            return {'stdout':'hi','error':None}
        with patch('app.services.script_runner.run_script',side_effect=runner):
            result,files = ServiceTools(self.storage,self.store,self.runs.authorize).script('alice',a.session['binding'],svc,Script(script_path='open.py'))
        self.assertEqual(result['stdout'],'hi');self.assertEqual(files[0][0],'out.txt')
        with patch('app.services.script_runner.run_script',side_effect=runner):
            response=await self.complete(text='tool-script')
        self.assertEqual(response.status_code,200,response.text)
        output=self.backend.results[-1]
        self.assertTrue(output['success'])
        self.assertIn('<<FILE:/generated/out.txt>>',json.dumps(output))
        self.assertEqual(self.storage.read_consumer_bytes('alice',self.svc['id'],self.conv['id'],'out.txt'),b'done')
        self.assertFalse(self.storage.is_file('alice','/generated/out.txt'))

    async def test_provider_failure_is_api_error_not_success(self):
        response = await self.complete(stream=True,text='fail-provider')
        self.assertIn('"type": "runtime_error"',response.text)
        self.assertNotIn('"finish_reason": "stop"',response.text)
        from app.services.usage_log import list_records
        records = list_records('alice',self.svc['id'])
        self.assertFalse(records[0]['ok'])

    async def test_wechat_real_bridge_consumes_runtime_stream_and_rejects_expired_identity(self):
        from app.channels.wechat.bridge import _run_agent_and_reply
        published.update_service('alice',self.svc['id'],{'wechat_channel':{'enabled':True}})
        session=SimpleNamespace(admin_id='alice',service_id=self.svc['id'],conversation_id=self.conv['id'],
                                session_id='wechat-fixture',from_user_id='fixture-user',context_token='fixture-context')
        mgr=SimpleNamespace(get_session=lambda sid: session if sid==session.session_id else None)
        client=SimpleNamespace(send_text=AsyncMock(),send_file=AsyncMock())
        with patch('app.channels.wechat.session_manager._manager',mgr), patch('app.channels.wechat.bridge.build_usage_callbacks',return_value=[]):
            await _run_agent_and_reply(session,client,'fixture-user','fixture-context','make-artifact')
            client.send_text.assert_awaited_once()
            client.send_file.assert_awaited_once()
            self.assertEqual(self.store.all('run')[-1]['binding']['service_scope']['channel'],'wechat')
            mgr.get_session=lambda _:None
            with self.assertRaises(HTTPException):
                await _run_agent_and_reply(session,client,'fixture-user','fixture-context','hello')

    async def test_service_prompt_does_not_inherit_admin_long_term_memory(self):
        from app.services.prompt import set_agent_notes, build_user_profile_prompt, set_user_profile
        set_user_profile('alice', {'custom_notes': 'published persona'}, auto_version=False)
        set_agent_notes('alice','admin-private-memory-marker')
        self.assertIn('admin-private-memory-marker',build_user_profile_prompt('alice'))
        from app.services.consumer_agent import _build_consumer_system_prompt, _create_consumer_read_tools
        svc=published.get_service('alice',self.svc['id'])
        prompt=_build_consumer_system_prompt('alice',svc)
        self.assertNotIn('admin-private-memory-marker',prompt)
        self.assertNotIn('admin-private-memory-marker',self.agent().session['instructions'])
        self.storage.write_bytes('alice','/docs/private.txt',b'admin doc')
        tools={t.name:t for t in _create_consumer_read_tools('alice',[],[])}
        self.assertNotIn('private.txt',tools['ls'].invoke({'path':'/docs'}))
        self.assertEqual(tools['read_file'].invoke({'path':'/docs/private.txt'}),'无权限访问该文件')

    async def test_disconnect_cancels_the_service_run(self):
        self.backend.gate.clear()
        a=self.agent()
        stream=a.astream({'messages':[{'role':'user','content':'wait'}]})
        first=await anext(stream)
        self.assertEqual(first[1][0].content,'hello')
        await stream.aclose()
        self.assertEqual(self.store.all('run')[0]['status'],'cancelled')

    async def test_unpublish_when_connection_is_disconnected(self):
        p=self.profiles.get(self.pid);p['status']='disconnected';self.store.put('profile',p)
        response=await self.client.put('/api/services/'+self.svc['id'],json={'published':False})
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['published'])

    async def test_input_path_validation_and_no_legacy_provider_fallback(self):
        r=await self.client.post('/api/v1/chat',headers=self.headers(),json={'conversation_id':'../../outside','message':'no'})
        self.assertEqual(r.status_code,400)
        for cap in ('scheduler','speech','video'):
            r=await self.client.put('/api/services/'+self.svc['id'],json={'capabilities':[cap]})
            self.assertEqual(r.status_code,400)
        from app.services.consumer_agent import create_consumer_agent
        with self.assertRaises(HTTPException):
            create_consumer_agent('alice',self.svc['id'],self.conv['id'],channel='scheduler',extra_capabilities=['humanchat'])


class ServiceProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_service_config_disables_native_filesystem_tools(self):
        from app.runtime.codex import CodexAdapter
        with tempfile.TemporaryDirectory() as tmp:
            a=CodexAdapter('fake',Path(tmp),Path(tmp),'model',managed=True,dynamic_tools=[{'name':'jellyfish_service_list_files'}])
            a.initialized=True
            a.service_scope={'web':False,'image':False}
            a.rpc=SimpleNamespace(request=AsyncMock(return_value={'thread':{'id':'fixture'}}))
            await a.open_session(tmp,'service instructions')
            params=a.rpc.request.call_args.args[1]
            self.assertEqual(params['sandbox'],'read-only')
            self.assertEqual(params['approvalPolicy'],'never')
            self.assertFalse(params['config']['features.shell_tool'])
            self.assertFalse(params['config']['tools.view_image'])
            self.assertEqual(params['config']['web_search'],'disabled')
            self.assertEqual(params['dynamicTools'][0]['name'],'jellyfish_service_list_files')

    async def test_cursor_service_denies_native_commands_and_accepts_only_registered_bridge(self):
        from app.runtime.cursor import CursorAdapter
        with tempfile.TemporaryDirectory() as tmp:
            a=CursorAdapter('fake',Path(tmp),Path(tmp),'model',dynamic_tools=[{'name':'jellyfish_service_list_files'}])
            a.rpc=FakeACP();a.service_scope={'web':False,'image':False}
            for kind,title,expected in [('execute','cat outside','deny'),('other','jellyfish: jellyfish_service_list_files','allow'),('other','jellyfish: jellyfish_read_memory','deny')]:
                request={'id':1,'method':'session/request_permission','params':{'options':[{'optionId':'allow','kind':'allow_once'},{'optionId':'deny','kind':'reject_once'}],'toolCall':{'kind':kind,'title':title}}}
                events=[e async for e in a.request_events(request)]
                self.assertEqual(events,[])
                self.assertEqual(a.rpc.sent[-1]['result']['outcome']['optionId'],expected)


import test_runtime_native as native_tests


class ServicePoolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native_tests.ConnectionPoolTests.asyncSetUp
    asyncTearDown = native_tests.ConnectionPoolTests.asyncTearDown
    session = native_tests.ConnectionPoolTests.session
    use = native_tests.ConnectionPoolTests.use

    async def test_cursor_permission_class_changes_replace_client_and_same_class_reuses(self):
        p=self.store.get('profile','p1');p['runtime']='cursor';self.store.put('profile',p)
        admin=await self.use(self.session())
        svc=self.session(sid='svc1');svc['binding']['service_scope']={'service_id':'svc1','web':False,'image':False}
        scoped=await self.use(svc)
        self.assertIsNot(admin,scoped)
        self.assertIn(admin,self.closed)
        other=self.session(sid='svc2');other['binding']['service_scope']={'service_id':'svc2','web':False,'image':False}
        self.assertIs(scoped,await self.use(other))
        self.assertTrue(scoped.reused)
        self.assertIsNot(scoped,await self.use(self.session()))


class CursorServiceConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_deny_policy_is_global_even_when_project_configs_are_disabled(self):
        from app.runtime.cursor import CursorAdapter, credential_path
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);home=root/'home';work=root/'work'
            auth=credential_path(home);auth.parent.mkdir(parents=True);auth.write_bytes(cursor_auth())
            a=CursorAdapter('fake',home,work,'model');a.rpc=FakeACP()
            a.service_scope={'web':False,'image':False}
            await a._start_acp()
            config=json.loads((home/'.cursor/cli-config.json').read_text())
            self.assertIn('Read(/**)',config['permissions']['deny'])
            self.assertIn('Shell(*)',config['permissions']['deny'])
            self.assertFalse(config['autoAcceptWebSearch'])
