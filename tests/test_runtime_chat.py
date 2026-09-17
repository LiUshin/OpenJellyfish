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
        self.root = Path(self.tmp.name)
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
        self.app = FastAPI(); self.app.include_router(runtime_router); self.app.include_router(conversations_router); self.app.include_router(chat_router)
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
    async def test_legacy_without_binding_keeps_deepagent_entry(self):
        from app.services.conversations import create_conversation
        conv = create_conversation('alice')
        async def legacy_stream(*args, **kwargs):
            yield 'data: {"type":"token","content":"legacy"}\n\n'
            yield 'data: {"type":"done"}\n\n'
        with patch('app.routes.chat._create_user_agent_bounded', new_callable=AsyncMock) as build, patch('app.routes.chat._stream_agent', legacy_stream):
            r = await self.client.post('/api/chat', json={'conversation_id':conv['id'], 'message':'hello', 'model':'old-model'})
            self.assertEqual(r.status_code,200,r.text); self.assertIn('legacy',r.text)
            self.assertEqual(build.call_args.kwargs['model'], 'old-model')
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
