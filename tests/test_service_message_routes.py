"""Native contact tools and HTTP notices/events; no provider or network calls."""
import json
import unittest
from unittest.mock import patch

from app.routes.inbox import router as inbox_router
from app.runtime.consumer_tools import ServiceTools
from app.services import published, service_messaging as messaging
from tests import test_runtime_consumer as runtime_fixture


class ServiceMessageRouteTests(unittest.IsolatedAsyncioTestCase):
    new_service = runtime_fixture.ConsumerRuntimeTests.new_service
    agent = runtime_fixture.ConsumerRuntimeTests.agent
    headers = runtime_fixture.ConsumerRuntimeTests.headers
    until = runtime_fixture.ConsumerRuntimeTests.until

    async def asyncSetUp(self):
        await runtime_fixture.ConsumerRuntimeTests.asyncSetUp(self)
        self.app.include_router(inbox_router)
        admin = patch('app.channels.wechat.admin_router._get_session', return_value=None)
        admin.start(); self.patches.append(admin)
        self.messages = messaging.get_store()

    async def asyncTearDown(self):
        self.messages.close()
        messaging._STORES.pop(str(self.messages.path), None)
        await runtime_fixture.ConsumerRuntimeTests.asyncTearDown(self)

    async def drain_messages(self):
        # Work the durable queue explicitly. Disconnected admin notification
        # rows become retry_wait; consumer projections remain wholly local.
        while delivery := self.messages.claim():
            await messaging.deliver_one(self.messages, delivery)

    def tool_run(self):
        agent = self.agent()
        run = {'id': 'contact-test-run', 'actor_id': 'alice', 'binding': agent.session['binding'],
               'status': 'running', 'output': '', 'seq': 0}
        self.store.put('run', run)
        return agent, run, ServiceTools(self.storage, self.store, self.runs.authorize)

    async def test_runtime_contact_call_id_replay_creates_one_scoped_case(self):
        agent, run, tools = self.tool_run()
        params = {'tool': 'jellyfish_service_contact_admin', 'callId': 'supplier-call-1',
                  'arguments': {'message': 'Please ask the owner to help'}}
        first = await tools(agent.session, run, params)
        second = await tools(agent.session, run, params)
        self.assertTrue(first['success'], first)
        self.assertEqual(first, second)
        cases = self.messages.list_cases('alice')
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['service_id'], self.svc['id'])
        self.assertEqual(cases[0]['conversation_id'], self.conv['id'])
        self.assertEqual(self.messages.list_cases('bob'), [])
        self.assertEqual(len(cases[0]['deliveries']), 1)

    async def test_runtime_contact_rejects_forged_scope_arguments(self):
        agent, run, tools = self.tool_run()
        for extra in ({'owner_id': 'bob'}, {'service_id': 'foreign'}, {'conversation_id': 'foreign'}):
            result = await tools(agent.session, run, {'tool': 'jellyfish_service_contact_admin',
                'arguments': {'message': 'Forged scope', **extra}})
            self.assertFalse(result['success'], result)
        self.assertEqual(self.messages.list_cases('alice'), [])
        self.assertEqual(self.messages.list_cases('bob'), [])

    async def test_runtime_contact_without_provider_call_id_deduplicates_within_run(self):
        agent, run, tools = self.tool_run()
        params = {'tool': 'jellyfish_service_contact_admin', 'arguments': {'message': 'Same Cursor request'}}
        self.assertTrue((await tools(agent.session, run, params))['success'])
        self.assertTrue((await tools(agent.session, run, params))['success'])
        self.assertEqual(len(self.messages.list_cases('alice')), 1)
        params['arguments']['message'] = 'A different request'
        self.assertTrue((await tools(agent.session, run, params))['success'])
        self.assertEqual(len(self.messages.list_cases('alice')), 2)

    async def test_runtime_old_tool_registry_gets_new_session_then_reuses_current_version(self):
        old = self.agent().session
        old['service_tools_version'] = 3
        old['thread_id'] = 'old-native-thread'
        old['dynamic_tools'] = [t for t in old['dynamic_tools'] if t['name'] != 'jellyfish_service_contact_admin']
        self.store.put('session', old)
        current = self.agent().session
        self.assertNotEqual(current['id'], old['id'])
        self.assertIsNone(current['thread_id'])
        self.assertEqual(current['service_tools_version'], 4)
        self.assertIn('jellyfish_service_contact_admin', {t['name'] for t in current['dynamic_tools']})
        self.assertEqual(self.agent().session['id'], current['id'])
        self.assertEqual(self.store.get('session', old['id'])['thread_id'], 'old-native-thread')

    async def test_conversation_create_preserves_web_source_and_defaults_api(self):
        web = await self.client.post('/api/v1/conversations', headers=self.headers(), json={'title': 'Browser', 'source': 'web'})
        api = await self.client.post('/api/v1/conversations', headers=self.headers(), json={'title': 'Client'})
        self.assertEqual(web.status_code, 200, web.text)
        self.assertEqual(api.status_code, 200, api.text)
        self.assertEqual(web.json()['source'], 'web')
        self.assertEqual(api.json()['source'], 'api')
        forged = await self.client.post('/api/v1/conversations', headers=self.headers(), json={'source': 'wechat'})
        self.assertEqual(forged.status_code, 422)

    async def create_notice(self, **overrides):
        return await self.client.post(f"/api/services/{self.svc['id']}/broadcasts", json={
            'message': 'Service maintenance notice', 'conversation_ids': [self.conv['id']],
            'idempotency_key': 'notice-request-1', **overrides,
        })

    async def test_notice_http_replay_is_idempotent_and_conflicting_reuse_is_rejected(self):
        first = await self.create_notice()
        second = await self.create_notice()
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(second.status_code, 202, second.text)
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertEqual(len(first.json()['messages']), 1)
        self.assertEqual(len(first.json()['deliveries']), 1)
        changed = await self.create_notice(message='Different message')
        self.assertEqual(changed.status_code, 409, changed.text)
        await self.drain_messages()
        conv = published.get_consumer_conversation('alice', self.svc['id'], self.conv['id'])
        self.assertEqual([m['content'] for m in conv['messages']], ['Service maintenance notice'])

    async def test_events_are_service_key_bound_and_cursor_replayable(self):
        response = await self.create_notice()
        self.assertEqual(response.status_code, 202, response.text)
        path = f"/api/v1/conversations/{self.conv['id']}/events"
        before = await self.client.get(path, headers=self.headers())
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(before.json()['events'], [])
        await self.drain_messages()
        first = await self.client.get(path, headers=self.headers())
        self.assertEqual(first.status_code, 200, first.text)
        events = first.json()['events']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['message']['content'], 'Service maintenance notice')
        self.assertNotIn('target', events[0]['message'])
        again = await self.client.get(path, headers=self.headers())
        self.assertEqual(again.json()['events'][0]['id'], events[0]['id'])
        after = await self.client.get(path + '?after=' + str(first.json()['next_cursor']), headers=self.headers())
        self.assertEqual(after.json()['events'], [])
        other_svc, other_key, _ = await self.new_service(name='Other Service')
        foreign = await self.client.get(path, headers=self.headers(other_key))
        self.assertEqual(foreign.status_code, 404, foreign.text)
        self.assertNotIn('Service maintenance notice', foreign.text)
        published.delete_service_key('alice', self.svc['id'], self.key['id'])
        revoked = await self.client.get(path, headers=self.headers())
        self.assertEqual(revoked.status_code, 401)

    async def test_notice_rejects_other_owner_and_unknown_conversation(self):
        foreign = await self.client.post(f"/api/services/{self.svc['id']}/broadcasts", headers={'Authorization': 'Bearer bob'},
            json={'message': 'Bad', 'conversation_ids': [self.conv['id']], 'idempotency_key': 'forged'})
        self.assertEqual(foreign.status_code, 404, foreign.text)
        missing = await self.create_notice(conversation_ids=[self.conv['id'], 'missing-conversation'])
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(messaging.list_notice_broadcasts('alice'), [])

    async def test_contact_admin_http_reply_reaches_events_only_after_local_projection(self):
        case = messaging.post_contact('alice', self.svc['id'], self.conv['id'], 'Please help', idempotency_key='contact-1')
        first = await self.client.post(f"/api/inbox/{case['id']}/replies", json={'message': 'Owner reply', 'idempotency_key': 'reply-1'})
        self.assertEqual(first.status_code, 200, first.text)
        second = await self.client.post(f"/api/inbox/{case['id']}/replies", json={'message': 'Owner reply', 'idempotency_key': 'reply-1'})
        self.assertEqual(first.json()['message']['id'], second.json()['message']['id'])
        foreign = await self.client.post(f"/api/inbox/{case['id']}/replies", headers={'Authorization': 'Bearer bob'},
            json={'message': 'Forged reply', 'idempotency_key': 'reply-bob'})
        self.assertEqual(foreign.status_code, 404)
        path = f"/api/v1/conversations/{self.conv['id']}/events"
        self.assertEqual((await self.client.get(path, headers=self.headers())).json()['events'], [])
        await self.drain_messages()
        events = (await self.client.get(path, headers=self.headers())).json()['events']
        self.assertEqual([e['message']['content'] for e in events], ['Owner reply'])
