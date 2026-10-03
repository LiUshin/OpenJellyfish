"""Real broadcast entrypoint/ledger/agent graphs with fake models and transports."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.core import security
from app.execution import context
from app.execution.grants import Grant, capture, validate_service_task_support
from app.execution.outbox import deliver_one
from app.execution.store import task_key
from app.services import published, scheduler as sch, scheduler_tree as tree
from app.services.tools import create_publish_service_task_tool
from app.channels.wechat.session_manager import WeChatSession, WeChatSessionManager


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def idle_delivery(store):
    await asyncio.Future()


class BroadcastSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patches = []
        def bind(p):
            result = p.start(); self.addCleanup(p.stop)
            return result
        bind(patch.object(security, 'USERS_DIR', self.tmp.name))
        bind(patch('app.storage.local.USERS_DIR', self.tmp.name))
        bind(patch.object(security, '_load_users', return_value={'owner': {'username': 'test'}}))
        bind(patch.dict(os.environ, {'DISABLE_SCHEDULER': '0'}))
        bind(patch('app.execution.outbox.delivery_loop', idle_delivery))
        bind(patch('app.services.agent._checkpointer', InMemorySaver()))
        bind(patch('app.services.agent._get_default_model', return_value='fake'))
        bind(patch.object(sch, 'build_usage_callbacks', return_value=[]))
        self.model = ToolModel(responses=[
            AIMessage(content='', tool_calls=[{'name': 'send_message', 'args': {'message': 'Public notice'}, 'id': 'send-1'}]),
            AIMessage(content='Internal execution notes'),
        ])
        self.resolve_model = bind(patch('app.services.agent._resolve_model', return_value=self.model))
        tree.invalidate_path_cache(); sch._heap.clear(); sch._heap_index.clear()
        self.store = context.get_store()
        self.addCleanup(self.store.close)
        self.service = published.create_service('owner', {
            'name': 'Fixture Service', 'published': True, 'model': 'fake',
            'capabilities': ['web', 'image', 'humanchat'], 'allowed_docs': ['*'],
            'allowed_scripts': ['*'], 'wechat_channel': {'enabled': True},
        })
        self.sid = self.service['id']
        self.conv = published.create_consumer_conversation('owner', self.sid, source='wechat')['id']
        self.session = WeChatSession(session_id='ws_test', service_id=self.sid, admin_id='owner',
            conversation_id=self.conv, bot_token='fake', ilink_user_id='fake', ilink_bot_id='fake',
            base_url='http://never.invalid', from_user_id='consumer', context_token='fake')
        self.manager = WeChatSessionManager()
        self.manager._sessions[self.session.session_id] = self.session
        self.client = SimpleNamespace(send_text=AsyncMock(return_value={'ret': 0}))
        self.manager._clients[self.session.session_id] = self.client
        bind(patch('app.channels.wechat.session_manager.get_session_manager', return_value=self.manager))
        self.scheduler = sch.HeapScheduler(); self.scheduler.start()
        bind(patch.object(sch, 'get_scheduler', return_value=self.scheduler))

    async def asyncTearDown(self):
        await self.scheduler.stop()

    def tasks(self):
        return sch.list_service_tasks('owner', self.sid)

    def runs(self, task):
        return self.store.list_runs(task_key('service', 'owner', self.sid, task['id']))

    async def broadcast(self, **kwargs):
        # The actual synchronous LangChain tool executes in its worker thread.
        return await create_publish_service_task_tool('owner').ainvoke({'message': 'Send notice if useful', **kwargs})

    async def completed(self, task, count=1):
        async with asyncio.timeout(5):
            while True:
                rows = self.runs(task)
                if len(rows) >= count and all(r['status'] not in ('queued', 'running') for r in rows) and not self.scheduler._running_tasks:
                    return rows
                await asyncio.sleep(.01)

    async def drain(self):
        with patch('app.services.scheduled_inject.project_delivery', AsyncMock()):
            while delivery := self.store.claim_delivery():
                await deliver_one(self.store, delivery)

    async def test_default_once_real_tool_graph_executes_and_sends_exactly_once(self):
        response = await self.broadcast()
        self.assertIn('已创建 1 个任务', response)
        task = self.tasks()[0]
        runs = await self.completed(task)
        await asyncio.sleep(.1)
        self.assertEqual([(r['trigger'], r['status']) for r in self.runs(task)], [('scheduled', 'success')])
        self.assertEqual(task['task_config']['capabilities'], ['docs', 'documents', 'humanchat'])
        self.assertNotIn('web', runs[0]['snapshot']['execution_grant']['capabilities'])
        await self.drain()
        self.client.send_text.assert_awaited_once_with('consumer', 'Public notice', 'fake')
        self.assertIsNone(self.tasks()[0]['next_run_at'])
        messages = published.get_consumer_conversation('owner', self.sid, self.conv)['messages']
        self.assertEqual([m['content'] for m in messages if m['role'] == 'assistant'], ['Public notice'])

    async def test_explicit_run_now_does_not_add_manual_run_to_immediate_once(self):
        await self.broadcast(run_now=True)
        task = self.tasks()[0]
        await self.completed(task); await asyncio.sleep(.05)
        self.assertEqual([r['trigger'] for r in self.runs(task)], ['scheduled'])

    async def test_future_once_waits_for_its_schedule_by_default(self):
        future = (datetime.now(timezone.utc) + timedelta(seconds=.4)).isoformat()
        await self.broadcast(schedule=future)
        task = self.tasks()[0]
        self.assertEqual(self.runs(task), [])
        rows = await self.completed(task)
        self.assertEqual([(r['trigger'], r['status']) for r in rows], [('scheduled', 'success')])
        self.assertGreaterEqual(rows[0]['started_at'], datetime.fromisoformat(future).timestamp())

    async def test_explicit_manual_future_once_does_not_consume_scheduled_cursor(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        await self.broadcast(schedule=future, run_now=True)
        task = self.tasks()[0]
        rows = await self.completed(task)
        self.assertEqual([r['trigger'] for r in rows], ['manual'])
        self.assertEqual(self.tasks()[0]['next_run_at'], future)
        self.assertIsNone(self.tasks()[0]['last_scheduled_run_at'])

    async def test_cron_default_waits_and_explicit_manual_preserves_cursor(self):
        await self.broadcast(schedule_type='cron', schedule='0 0 * * *')
        task = self.tasks()[0]; cursor = task['next_run_at']
        self.assertEqual(self.runs(task), [])
        self.assertTrue(self.scheduler.run_service_task_now('owner', self.sid, task['id']))
        rows = await self.completed(task)
        self.assertEqual([r['trigger'] for r in rows], ['manual'])
        self.assertEqual(self.tasks()[0]['next_run_at'], cursor)

    async def test_no_send_intent_keeps_commentary_only_in_owner_run(self):
        self.resolve_model.return_value = ToolModel(responses=[AIMessage(content='Internal: do not notify this consumer')])
        await self.broadcast()
        row = (await self.completed(self.tasks()[0]))[0]
        self.assertEqual(row['status'], 'success')
        self.assertIn('Internal:', row['result']['output'])
        self.assertEqual(self.store.deliveries(row['id'], 'owner'), [])
        self.assertEqual(published.get_consumer_conversation('owner', self.sid, self.conv)['messages'], [])
        await self.drain(); self.client.send_text.assert_not_awaited()

    async def test_failed_run_does_not_expose_internal_error_to_consumer(self):
        self.resolve_model.side_effect = PermissionError('private execution diagnostic')
        await self.broadcast()
        row = (await self.completed(self.tasks()[0]))[0]
        self.assertEqual(row['status'], 'blocked')
        self.assertIn('private execution diagnostic', row['result']['output'])
        self.assertEqual(self.store.deliveries(row['id'], 'owner'), [])
        await self.drain(); self.client.send_text.assert_not_awaited()

    async def test_revoked_publication_cancels_committed_outbox(self):
        await self.broadcast()
        row = (await self.completed(self.tasks()[0]))[0]
        published.update_service('owner', self.sid, {'published': False})
        await self.drain()
        self.client.send_text.assert_not_awaited()
        self.assertEqual({d['status'] for d in self.store.deliveries(row['id'], 'owner')}, {'cancelled'})

    async def test_disabled_wechat_cancels_committed_outbox(self):
        await self.broadcast()
        row = (await self.completed(self.tasks()[0]))[0]
        published.update_service('owner', self.sid, {'wechat_channel': {'enabled': False}})
        await self.drain()
        self.client.send_text.assert_not_awaited()
        self.assertEqual({d['status'] for d in self.store.deliveries(row['id'], 'owner')}, {'cancelled'})

    async def test_expired_wechat_cancels_committed_outbox(self):
        await self.broadcast()
        row = (await self.completed(self.tasks()[0]))[0]
        published.update_service('owner', self.sid, {'wechat_channel': {'enabled': True, 'expires_at': '2000-01-01T00:00:00Z'}})
        await self.drain()
        self.client.send_text.assert_not_awaited()
        self.assertEqual({d['status'] for d in self.store.deliveries(row['id'], 'owner')}, {'cancelled'})

    async def test_external_executor_is_rejected_before_any_fanout_task(self):
        published.create_service('owner', {'name': 'External', 'published': True,
            'runtime_choice': {'runtime': 'codex'}, 'capabilities': []})
        response = await self.broadcast()
        self.assertIn('未创建', response)
        self.assertIn('Codex/Cursor', response)
        self.assertEqual(self.tasks(), [])

    async def test_invalid_schedule_has_no_partial_tasks(self):
        response = await self.broadcast(schedule_type='cron', schedule='broken')
        self.assertIn('未创建', response); self.assertEqual(self.tasks(), [])

    async def test_explicit_unsupported_capability_is_rejected(self):
        with self.assertRaisesRegex(PermissionError, 'web'):
            validate_service_task_support('owner', self.sid, {'capabilities': ['web']})

    async def test_service_persona_profile_and_contact_tool_are_preserved_without_unrestricted_tools(self):
        import langchain.agents
        published.update_service('owner', self.sid, {'system_prompt_version_id': 'chosen', 'user_profile_version_id': 'profile'})
        with patch('app.services.prompt.get_prompt_version', return_value={'content': 'Published expert persona {user_profile_context}'}), \
             patch('app.services.prompt.get_profile_version', return_value={'content': 'Selected profile style'}), \
             patch('langchain.agents.create_agent', wraps=langchain.agents.create_agent) as build:
            await self.broadcast()
            await self.completed(self.tasks()[0])
        prompt = build.call_args.kwargs['system_prompt']
        self.assertIn('Published expert persona', prompt)
        self.assertIn('Selected profile style', prompt)
        names = {t.name for t in build.call_args.kwargs['tools']}
        self.assertIn('contact_admin', names)
        self.assertNotIn('run_script', names)
        self.assertNotIn('web_search', names)

    async def test_real_scheduled_contact_tool_graph_commits_case_and_effect_receipt(self):
        from app.services import service_messaging as messaging
        messages = messaging.get_store()
        self.addCleanup(messages.close)
        self.resolve_model.return_value = ToolModel(responses=[
            AIMessage(content='', tool_calls=[{'name': 'contact_admin', 'args': {'message': 'Human help requested'}, 'id': 'contact-real'}]),
            AIMessage(content='Internal: request recorded'),
        ])
        with patch('app.channels.wechat.admin_router._get_session', return_value=None):
            await self.broadcast()
            row = (await self.completed(self.tasks()[0]))[0]
        self.assertEqual(row['status'], 'success', row['result'])
        cases = messages.list_cases('owner')
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['conversation_id'], self.conv)
        self.assertEqual(cases[0]['wechat_session_id'], 'ws_test')
        self.assertEqual(cases[0]['message'], 'Human help requested')
        self.assertEqual([e['status'] for e in self.store.effects(row['id'], 'owner')], ['completed'])
        self.assertEqual(self.store.deliveries(row['id'], 'owner'), [])
        self.client.send_text.assert_not_awaited()

    async def test_contact_tool_binds_owner_service_conversation_and_rechecks_revocation(self):
        from app.execution.agent import build_tools
        from langchain_core.tools import tool
        calls = []
        @tool
        def contact_admin(message: str) -> str:
            """Fake local contact record."""
            calls.append(message)
            return 'Recorded'
        await self.broadcast(schedule=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat())
        task = self.tasks()[0]
        snapshot = {**task, 'execution_grant': capture(task, 'owner', self.sid)}
        row = self.store.claim(self.store.submit(task_key('service', 'owner', self.sid, task['id']), 'owner', snapshot, manual=True)['id'])
        grant = Grant(context.ExecutionContext(self.store, row))
        with patch('app.services.tools.create_contact_admin_tool', return_value=contact_admin) as factory:
            tools = {t.name: t for t in build_tools(grant)}
            result = await tools['contact_admin'].coroutine('Needs human attention', SimpleNamespace(tool_call_id='contact-1'))
        factory.assert_called_once_with('owner', self.sid, self.conv, wechat_session_id='ws_test',
                                        idempotency_key=f"{row['id']}:contact-1")
        self.assertEqual(result, 'Recorded'); self.assertEqual(calls, ['Needs human attention'])
        published.update_service('owner', self.sid, {'published': False})
        with self.assertRaises(PermissionError):
            await tools['contact_admin'].coroutine('Must not record', SimpleNamespace(tool_call_id='contact-2'))
        self.assertEqual(calls, ['Needs human attention'])

    async def legacy_delivery(self, status='success', explicit=False, result_preview='{"text": "Old payload"}'):
        await self.broadcast(schedule=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat())
        task = self.tasks()[0]
        snapshot = {**task, 'execution_grant': capture(task, 'owner', self.sid)}
        row = self.store.claim(self.store.submit(task_key('service', 'owner', self.sid, task['id']), 'owner', snapshot, manual=True)['id'])
        record = {'status': status, 'output': 'Old payload', 'steps': [
            {'type': 'delivery_queued'},
            {'type': 'tool_result', 'tool': 'send_message', 'result_preview': result_preview},
        ] if explicit else []}
        self.store.finish(row['id'], row['token'], record, deliveries=[{
            'channel': 'wechat', 'target': task['reply_to'],
            'payload': {'text': 'Old payload', 'success': status == 'success', 'task_meta': {}},
        }])
        await self.drain()
        return self.store.deliveries(row['id'], 'owner')[0]

    async def test_upgrade_cancels_old_pending_diagnostics_without_send_intent(self):
        delivery = await self.legacy_delivery()
        self.assertEqual(delivery['status'], 'cancelled')
        self.client.send_text.assert_not_awaited()

    async def test_upgrade_preserves_old_pending_explicit_successful_send(self):
        delivery = await self.legacy_delivery(explicit=True)
        self.assertEqual(delivery['status'], 'delivered')
        self.client.send_text.assert_awaited_once_with('consumer', 'Old payload', 'fake')

    async def test_upgrade_cancels_old_failure_even_with_a_prior_send_intent(self):
        delivery = await self.legacy_delivery(status='error', explicit=True)
        self.assertEqual(delivery['status'], 'cancelled')
        self.client.send_text.assert_not_awaited()

    async def test_upgrade_cancels_old_empty_send_followed_by_commentary(self):
        delivery = await self.legacy_delivery(explicit=True, result_preview='{"text": ""}')
        self.assertEqual(delivery['status'], 'cancelled')
        self.assertIn('重新发送', delivery['error'])
        self.client.send_text.assert_not_awaited()

    async def test_upgrade_cancels_truncated_legacy_evidence_for_review(self):
        delivery = await self.legacy_delivery(explicit=True, result_preview='{"text": "incomplete…')
        self.assertEqual(delivery['status'], 'cancelled')
        self.client.send_text.assert_not_awaited()
