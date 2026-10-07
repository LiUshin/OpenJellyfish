"""Durable contact/reply tests: synthetic data, no model or real network."""
import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import service_messaging as sm
from app.services import inbox


class MessagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = sm.MessageStore(Path(self.temp.name, 'executions.sqlite3'))
        self.addCleanup(self.store.close)
        self.target = {'owner_id': 'owner', 'service_id': 'svc', 'conversation_id': 'conv', 'source': 'web'}

    def case(self, key='contact', owner='owner', target=None):
        return self.store.create_case(owner, 'svc', 'conv', '需要人工处理', target or self.target, 'Synthetic', request_key=key, admin_target={'owner_id': owner})

    def notice(self, key='notice', target=None, scheduled_at=None):
        return self.store.create_broadcast('owner', 'svc', [target or self.target], '管理员通知', request_key=key, scheduled_at=scheduled_at)

    def test_case_commit_is_idempotent_and_collision_rejects_different_body(self):
        cid = self.case()
        self.assertEqual(self.case(), cid)
        case = self.store.get_case('owner', cid)
        self.assertEqual(len(case['messages']), 1)
        self.assertEqual(len(case['deliveries']), 1)
        self.assertEqual(case['notification']['status'], 'pending')
        with self.assertRaises(sm.MessagingConflict):
            self.store.create_case('owner', 'svc', 'conv', 'changed', self.target, 'Synthetic', request_key='contact', admin_target={})

    def test_message_and_delivery_transaction_rolls_back_together(self):
        with patch.object(self.store, '_delivery', side_effect=OSError('fault')):
            with self.assertRaises(OSError):
                self.case()
        self.assertEqual(self.store.list_cases('owner'), [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sm_messages').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sm_requests').fetchone()[0], 0)

    def test_owner_and_frozen_target_are_enforced(self):
        cid = self.case()
        self.assertIsNone(self.store.get_case('other', cid))
        self.assertFalse(self.store.delete_case('other', cid))
        with self.assertRaises(KeyError):
            self.store.reply('other', cid, 'reply', self.target, request_key='reply')
        with self.assertRaises(sm.MessagingConflict):
            self.store.reply('owner', cid, 'reply', {**self.target, 'conversation_id': 'different'}, request_key='reply')
        self.assertEqual(len(self.store.get_case('owner', cid)['messages']), 1)

    def test_reply_has_durable_message_and_does_not_claim_remote_receipt(self):
        cid = self.case()
        mid = self.store.reply('owner', cid, '回复正文', self.target, request_key='reply')
        self.assertEqual(mid, self.store.reply('owner', cid, '回复正文', self.target, request_key='reply'))
        case = self.store.get_case('owner', cid)
        self.assertEqual(case['case_status'], 'replied')
        self.assertEqual(case['messages'][-1]['author_type'], 'admin')
        self.assertEqual([d['status'] for d in case['deliveries']], ['pending', 'pending'])
        self.assertEqual(self.store.list_events('owner', 'svc', 'conv')['events'], [])

    def test_send_service_message_direct_is_bound_and_idempotent(self):
        with patch.object(sm, 'get_store', return_value=self.store), \
             patch.object(sm, '_consumer_target', return_value=self.target):
            first = sm.send_service_message('owner', 'svc', 'conv', '管理员回复', idempotency_key='direct-1', expected_target=self.target)
            again = sm.send_service_message('owner', 'svc', 'conv', '管理员回复', idempotency_key='direct-1', expected_target=self.target)
            self.assertEqual(first, again)
            self.assertEqual(first['message']['author_type'], 'admin')
            self.assertEqual(first['message']['purpose'], 'reply')
            self.assertEqual([d['channel'] for d in first['deliveries']], ['web'])
            self.assertEqual(first['deliveries'][0]['status'], 'pending')
            with self.assertRaises(sm.MessagingConflict):
                sm.send_service_message('owner', 'svc', 'conv', '另一条回复', idempotency_key='direct-1')

        with self.assertRaises(sm.MessagingConflict):
            self.store.direct_reply('other', 'svc', 'conv', '无权回复', self.target, request_key='other-owner')
        with self.assertRaises(sm.MessagingConflict):
            self.store.direct_reply('owner', 'other-service', 'conv', '目标不匹配', self.target, request_key='wrong-service')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sm_messages').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sm_deliveries').fetchone()[0], 1)

    def test_send_service_message_rejects_target_change_before_or_during_enqueue(self):
        approved = {**self.target, 'source': 'wechat', 'session_id': 'old-ws', 'recipient_id': 'old-user'}
        rebound = {**approved, 'session_id': 'new-ws', 'recipient_id': 'new-user'}
        with patch.object(sm, 'get_store', return_value=self.store), \
             patch.object(sm, '_consumer_target', return_value=rebound):
            with self.assertRaises(sm.MessagingConflict):
                sm.send_service_message('owner', 'svc', 'conv', '只给原用户',
                    idempotency_key='rebound-before', expected_target=approved)
        with patch.object(sm, 'get_store', return_value=self.store), \
             patch.object(sm, '_consumer_target', side_effect=[approved, rebound]):
            with self.assertRaises(sm.MessagingConflict):
                sm.send_service_message('owner', 'svc', 'conv', '只给原用户',
                    idempotency_key='rebound-during', expected_target=approved)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sm_messages').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sm_deliveries').fetchone()[0], 0)

    def test_send_service_message_inbox_requires_exact_case_scope(self):
        cid = self.case()
        bad_target_case = self.case(key='mismatched-target', target={**self.target, 'conversation_id': 'other'})
        with patch.object(sm, 'get_store', return_value=self.store), \
             patch.object(sm, '_authorize_target'), \
             patch('app.core.security.USERS_DIR', self.temp.name):
            with self.assertRaises(sm.MessagingConflict):
                sm.send_service_message('owner', 'svc', 'another-conv', '回复', idempotency_key='case-reply', inbox_id=cid)
            with self.assertRaises(sm.MessagingConflict):
                sm.send_service_message('owner', 'another-svc', 'conv', '回复', idempotency_key='case-reply', inbox_id=cid)
            with self.assertRaises(KeyError):
                sm.send_service_message('other', 'svc', 'conv', '回复', idempotency_key='case-reply', inbox_id=cid)
            with self.assertRaises(sm.MessagingConflict):
                sm.send_service_message('owner', 'svc', 'conv', '回复', idempotency_key='bad-target', inbox_id=bad_target_case)
            with self.assertRaises(sm.MessagingConflict):
                sm.send_service_message('owner', 'svc', 'conv', '回复', idempotency_key='changed-approval',
                    inbox_id=cid, expected_target={**self.target, 'conversation_id': 'another-conv'})
            result = sm.send_service_message('owner', 'svc', 'conv', '回复', idempotency_key='case-reply', inbox_id=cid)
            self.assertEqual(result, sm.send_service_message('owner', 'svc', 'conv', '回复', idempotency_key='case-reply', inbox_id=cid))
        self.assertEqual(result['message']['case_id'], cid)
        self.assertEqual([d['status'] for d in result['deliveries']], ['pending'])
        self.assertEqual(self.store.get_case('owner', cid)['case_status'], 'replied')
        self.assertEqual(self.store.get_case('owner', cid)['status'], 'read')

    def test_send_service_message_projects_web_history_only_after_worker_claim(self):
        target = {**self.target, 'source': 'wechat', 'session_id': 'ws', 'recipient_id': 'consumer'}
        with patch.object(sm, 'get_store', return_value=self.store), \
             patch.object(sm, '_consumer_target', return_value=target):
            result = sm.send_service_message('owner', 'svc', 'conv', '稍后送达', idempotency_key='direct-wechat')
        self.assertEqual([d['channel'] for d in result['deliveries']], ['web', 'wechat'])
        self.assertTrue(all(d['status'] == 'pending' for d in result['deliveries']))
        self.assertEqual(self.store.list_events('owner', 'svc', 'conv')['events'], [])

        projected = {}
        def save(*args, **kwargs):
            projected.setdefault(kwargs['event_id'], (args, kwargs))
        with patch.object(sm, '_owner'), patch.object(sm, '_authorize_target'), \
             patch('app.services.published.save_consumer_message', side_effect=save):
            delivery = self.store.claim()
            self.assertEqual(delivery['channel'], 'web')
            asyncio.run(sm.deliver_one(self.store, delivery))
        self.assertEqual(set(projected), {result['message']['id']})
        events = self.store.list_events('owner', 'svc', 'conv')['events']
        self.assertEqual([e['message']['id'] for e in events], [result['message']['id']])
        statuses = {d['channel']: d['status'] for d in self.store.get_message_deliveries('owner', result['message']['id'])}
        self.assertEqual(statuses, {'web': 'delivered', 'wechat': 'pending'})

    def test_legacy_import_uses_messages_not_stale_index_and_never_resurrects_deleted(self):
        directory = Path(self.temp.name, 'owner', 'inbox')
        directory.mkdir(parents=True)
        old = {'id': 'inbox_legacy', 'service_id': 'svc', 'conversation_id': 'conv', 'message': 'old',
               'timestamp': '2026-01-01', 'status': 'handled', 'handled_by': 'agent', 'agent_response': 'evaluated'}
        (directory / 'inbox_legacy.json').write_text(json.dumps(old))
        (directory / '_index.json').write_text(json.dumps({'messages': {'inbox_legacy': {**old, 'status': 'unread'}}}))
        self.store.migrate_legacy('owner', self.temp.name)
        imported = self.store.get_case('owner', 'inbox_legacy')
        self.assertEqual(imported['case_status'], 'acknowledged')
        self.assertEqual(imported['channel'], 'unknown')
        self.assertEqual(imported['legacy_status'], 'handled')
        self.assertEqual(imported['status'], 'read')
        self.assertIsNone(imported['notification'])
        self.store.delete_case('owner', 'inbox_legacy')
        self.store.migrated.clear()
        self.store.migrate_legacy('owner', self.temp.name)
        self.assertIsNone(self.store.get_case('owner', 'inbox_legacy'))

    def test_case_status_is_independent_from_notification_success(self):
        cid = self.case()
        delivery = self.store.claim()
        self.store.finish(delivery, 'delivered', receipt={'ret': 0})
        self.assertEqual(self.store.get_case('owner', cid)['status'], 'unread')
        self.assertEqual(self.store.get_case('owner', cid)['case_status'], 'open')
        self.store.update_case('owner', cid, status='read')
        self.assertEqual(self.store.list_cases('owner', 'unread'), [])
        self.assertEqual(self.store.get_case('owner', cid)['case_status'], 'acknowledged')

    def test_failure_before_send_retries_but_failure_after_send_is_unknown(self):
        cid = self.case()
        delivery = self.store.claim()
        with patch.object(sm, '_owner'), patch('app.channels.wechat.admin_router._get_session', return_value=None):
            asyncio.run(sm.deliver_one(self.store, delivery))
        self.assertEqual(self.store.get_case('owner', cid)['notification']['status'], 'retry_wait')
        self.store.manage_deliveries('owner', [delivery['id']])
        client = SimpleNamespace(send_text=AsyncMock(side_effect=OSError('network lost after send')))
        session = {'connected': True, 'client': client, 'from_user_id': 'adminwx', 'context_token': 'ctx', 'conversation_id': 'adminconv'}
        delivery = self.store.claim()
        with patch.object(sm, '_owner'), patch('app.channels.wechat.admin_router._get_session', return_value=session):
            asyncio.run(sm.deliver_one(self.store, delivery))
        case = self.store.get_case('owner', cid)
        self.assertEqual(case['status'], 'unread')
        self.assertEqual(case['notification']['status'], 'unknown')
        self.assertIsNone(self.store.claim())
        with self.assertRaises(sm.MessagingConflict):
            self.store.manage_deliveries('owner', [delivery['id']])
        self.store.manage_deliveries('owner', [delivery['id']], allow_unknown=True)
        self.assertIsNotNone(self.store.claim())

    def test_explicit_provider_rejection_is_cancelled_but_missing_ack_is_unknown(self):
        cid = self.case()
        client = SimpleNamespace(send_text=AsyncMock(return_value={'ret': 0, 'errcode': 13}))
        session = {'connected': True, 'client': client, 'from_user_id': 'adminwx', 'context_token': 'ctx', 'conversation_id': 'adminconv'}
        with patch.object(sm, '_owner'), patch('app.channels.wechat.admin_router._get_session', return_value=session):
            delivery = self.store.claim()
            asyncio.run(sm.deliver_one(self.store, delivery))
            self.assertEqual(self.store.get_case('owner', cid)['notification']['status'], 'cancelled')
            cid = self.case(key='second-contact')
            client.send_text.return_value = {}
            asyncio.run(sm.deliver_one(self.store, self.store.claim()))
            self.assertEqual(self.store.get_case('owner', cid)['notification']['status'], 'unknown')

    def test_admin_rescan_cannot_silently_change_recipient(self):
        cid = self.store.create_case('owner', 'svc', 'conv', 'feedback', self.target, 'Synthetic', request_key='contact',
            admin_target={'owner_id': 'owner', 'conversation_id': 'old', 'recipient_id': 'olduser'})
        client = SimpleNamespace(send_text=AsyncMock())
        session = {'connected': True, 'client': client, 'from_user_id': 'newuser', 'context_token': 'ctx', 'conversation_id': 'new'}
        with patch.object(sm, '_owner'), patch('app.channels.wechat.admin_router._get_session', return_value=session):
            asyncio.run(sm.deliver_one(self.store, self.store.claim()))
        self.assertEqual(self.store.get_case('owner', cid)['notification']['status'], 'cancelled')
        client.send_text.assert_not_awaited()

    def test_local_history_projection_is_idempotent_and_emits_one_event(self):
        self.notice()
        delivery = self.store.claim()
        history = {}
        def save(*args, **kwargs):
            history.setdefault(kwargs['event_id'], args[4])
        with patch.object(sm, '_owner'), patch.object(sm, '_authorize_target'), patch('app.services.published.save_consumer_message', side_effect=save):
            asyncio.run(sm.deliver_one(self.store, delivery))
            asyncio.run(sm.deliver_one(self.store, delivery))
        events = self.store.list_events('owner', 'svc', 'conv')
        self.assertEqual(len(history), 1)
        self.assertEqual(len(events['events']), 1)
        self.assertEqual(events['events'][0]['id'], next(iter(history)))
        self.assertEqual(self.store.list_events('other', 'svc', 'conv')['events'], [])

    def test_visibility_sequence_handles_out_of_order_projection(self):
        first = self.notice('first')
        first_delivery = self.store.claim()
        self.store.finish(first_delivery, 'retry_wait')
        second = self.notice('second')
        second_delivery = self.store.claim()
        self.store.finish(second_delivery, 'delivered')
        fetched = self.store.list_events('owner', 'svc', 'conv')
        self.assertEqual(fetched['events'][0]['message']['broadcast_id'], second)
        self.store.manage_deliveries('owner', [first_delivery['id']])
        self.store.finish(self.store.claim(), 'delivered')
        later = self.store.list_events('owner', 'svc', 'conv', after=fetched['next_cursor'])
        self.assertEqual(len(later['events']), 1)
        self.assertEqual(later['events'][0]['message']['broadcast_id'], first)

    def test_crash_recovers_local_projection_but_never_retries_uncertain_remote(self):
        self.case()
        remote = self.store.claim()
        self.notice()
        local = self.store.claim()
        self.store.db.execute('UPDATE sm_deliveries SET claimed_at=?', (time.time() - 1000,))
        recovered = self.store.claim()
        self.assertEqual(recovered['id'], local['id'])
        status = self.store.db.execute('SELECT status FROM sm_deliveries WHERE id=?', (remote['id'],)).fetchone()[0]
        self.assertEqual(status, 'unknown')
        self.store.finish(local, 'delivered')  # stale completion is fenced
        self.assertEqual(self.store.list_events('owner', 'svc', 'conv')['events'], [])

    def test_expired_remote_claim_cannot_send_after_recovery(self):
        cid = self.case()
        delivery = self.store.claim()
        self.store.db.execute('UPDATE sm_deliveries SET claimed_at=?', (time.time() - 1000,))
        self.assertIsNone(self.store.claim())
        client = SimpleNamespace(send_text=AsyncMock(return_value={'ret': 0}))
        session = {'connected': True, 'client': client, 'from_user_id': 'adminwx', 'context_token': 'ctx', 'conversation_id': 'adminconv'}
        with patch.object(sm, '_owner'), patch('app.channels.wechat.admin_router._get_session', return_value=session):
            asyncio.run(sm.deliver_one(self.store, delivery))
        client.send_text.assert_not_awaited()
        self.assertEqual(self.store.get_case('owner', cid)['notification']['status'], 'unknown')

    def test_scheduled_notice_waits_and_cancel_does_not_send(self):
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        bid = self.notice(scheduled_at=later)
        self.assertIsNone(self.store.claim())
        item = self.store.get_broadcast('owner', bid)
        self.assertEqual(item['scheduled_at'], later)
        self.store.manage_deliveries('owner', [d['id'] for d in item['deliveries']], cancel=True)
        self.store.db.execute('UPDATE sm_deliveries SET next_attempt=0')
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.list_events('owner', 'svc', 'conv')['events'], [])

    def test_consumer_wechat_reply_uses_original_session_and_rechecks_revocation(self):
        target = {**self.target, 'source': 'wechat', 'session_id': 'ws', 'recipient_id': 'userwx'}
        bid = self.notice(target=target)
        with patch.object(sm, '_owner'), patch.object(sm, '_authorize_target', side_effect=PermissionError('revoked')):
            asyncio.run(sm.deliver_one(self.store, self.store.claim()))
            asyncio.run(sm.deliver_one(self.store, self.store.claim()))
        item = self.store.get_broadcast('owner', bid)
        self.assertEqual([d['status'] for d in item['deliveries']], ['cancelled', 'cancelled'])
        self.assertEqual(self.store.list_events('owner', 'svc', 'conv')['events'], [])

    def test_future_notice_does_not_block_an_immediate_message_in_same_conversation(self):
        later = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        future = self.notice('tomorrow', scheduled_at=later)
        immediate = self.notice('now')
        claimed = self.store.claim()
        self.assertEqual(claimed['message']['broadcast_id'], immediate)
        self.assertNotEqual(claimed['message']['broadcast_id'], future)

    def test_broadcast_retry_does_not_revive_user_cancelled_recipients(self):
        targets = [{**self.target, 'source': 'wechat', 'session_id': 'a', 'recipient_id': 'a'},
                   {**self.target, 'conversation_id': 'second', 'source': 'wechat', 'session_id': 'b', 'recipient_id': 'b'}]
        bid = self.store.create_broadcast('owner', 'svc', targets, 'notice', request_key='bcast')
        self.store.finish(self.store.claim(), 'delivered')
        self.store.finish(self.store.claim(), 'unknown')
        item = self.store.get_broadcast('owner', bid)
        self.store.manage_deliveries('owner', [d['id'] for d in item['deliveries']], cancel=True)
        with patch.object(sm, 'get_store', return_value=self.store):
            result = sm.retry_notice_broadcast('owner', bid, allow_unknown=True)
        self.assertEqual([d['status'] for d in result['deliveries']], ['delivered', 'pending', 'cancelled', 'cancelled'])

    def test_broadcast_history_filters_service_before_limit(self):
        wanted = self.notice()
        for i in range(55):
            self.store.create_broadcast('owner', 'other-service', [{**self.target, 'service_id': 'other-service'}], 'other', request_key=f'other-{i}')
        rows = self.store.list_broadcasts('owner', 'svc', limit=1)
        self.assertEqual([row['id'] for row in rows], [wanted])

    def test_two_connections_claim_only_once_and_data_survives_restart(self):
        cid = self.case()
        other = sm.MessageStore(self.store.path)
        self.addCleanup(other.close)
        self.assertIsNotNone(self.store.claim())
        self.assertIsNone(other.claim())
        self.assertEqual(other.get_case('owner', cid)['message'], '需要人工处理')

    def test_public_post_contact_and_inbox_never_create_an_admin_agent(self):
        with patch.object(sm, 'get_store', return_value=self.store), patch.object(sm, '_consumer_target', return_value=self.target), \
             patch.object(sm, '_admin_target', return_value={'owner_id': 'owner'}), \
             patch('app.services.published.get_service', return_value={'name': 'Synthetic'}), \
             patch('app.services.agent.create_user_agent', side_effect=AssertionError('must never run')):
            result = inbox.post_to_inbox('owner', 'svc', 'conv', 'feedback', idempotency_key='tool-call')
            self.assertIn('反馈已提交', result['summary'])
            self.assertEqual(result['id'], inbox.post_to_inbox('owner', 'svc', 'conv', 'feedback', idempotency_key='tool-call')['id'])

    def test_reply_http_contract_rejects_client_target_override(self):
        from pydantic import ValidationError
        from app.routes.inbox import InboxReply, api_reply
        with self.assertRaises(ValidationError):
            InboxReply(message='reply', idempotency_key='k', service_id='other')
        cid = self.case()
        with patch.object(sm, 'get_store', return_value=self.store), patch.object(sm, '_authorize_target'), patch.object(inbox, '_store', return_value=self.store):
            result = asyncio.run(api_reply(cid, InboxReply(message='reply', idempotency_key='reply'), user={'user_id': 'owner'}))
            self.assertEqual(result['message']['conversation_id'], 'conv')
            with self.assertRaises(Exception) as error:
                asyncio.run(api_reply(cid, InboxReply(message='reply', idempotency_key='reply'), user={'user_id': 'other'}))
            self.assertEqual(error.exception.status_code, 404)

    def test_admin_wechat_reply_command_bypasses_model_and_is_idempotent(self):
        from app.channels.wechat.admin_bridge import handle_admin_wechat_message
        cid = self.case()
        client = SimpleNamespace(send_text=AsyncMock(return_value={'ret': 0}))
        session = {'user_id': 'owner', 'conversation_id': 'adminconv', 'from_user_id': 'adminwx', 'client': client}
        raw = {'message_id': 12345, 'from_user_id': 'adminwx', 'context_token': 'ctx',
               'item_list': [{'type': 1, 'text_item': {'text': f'回复 {cid}：请按这个方案处理'}}]}
        with patch.object(sm, 'get_store', return_value=self.store), patch.object(sm, '_authorize_target'), \
             patch.object(inbox, '_store', return_value=self.store), \
             patch('app.channels.wechat.admin_router._save_admin_session'), \
             patch('app.channels.wechat.rate_limiter.check_message_rate', return_value=(True, '')), \
             patch('app.channels.wechat.admin_bridge._run_admin_agent_and_reply', side_effect=AssertionError('no model')):
            asyncio.run(handle_admin_wechat_message(session, raw))
            asyncio.run(handle_admin_wechat_message(session, raw))
        case = self.store.get_case('owner', cid)
        self.assertEqual(len(case['messages']), 2)
        self.assertEqual(case['messages'][-1]['content'], '请按这个方案处理')
        self.assertEqual(client.send_text.await_count, 2)
        self.assertIn('尚不代表对方已收到', client.send_text.await_args.args[1])
        self.assertEqual(client.send_text.await_args.args[0], 'adminwx')

    def test_wechat_case_command_rejects_other_owner_and_requires_transport_id(self):
        from app.channels.wechat.admin_bridge import _handle_case_reply
        cid = self.case()
        client = SimpleNamespace(send_text=AsyncMock(return_value={'ret': 0}))
        session = {'user_id': 'other', 'conversation_id': 'adminconv', 'from_user_id': 'adminwx', 'client': client}
        raw = {'message_id': 12, 'from_user_id': 'adminwx', 'context_token': 'ctx',
               'item_list': [{'type': 1, 'text_item': {'text': f'回复 {cid}：reply'}}]}
        with patch.object(sm, 'get_store', return_value=self.store), patch.object(sm, '_authorize_target'), patch.object(inbox, '_store', return_value=self.store):
            self.assertTrue(asyncio.run(_handle_case_reply(session, raw)))
            self.assertIn('无权限', client.send_text.await_args.args[1])
            session['user_id'] = 'owner'
            raw.pop('message_id')
            asyncio.run(_handle_case_reply(session, raw))
            self.assertIn('缺少消息编号', client.send_text.await_args.args[1])
            raw['from_user_id'] = 'intruder'
            calls = client.send_text.await_count
            asyncio.run(_handle_case_reply(session, raw))
            self.assertEqual(client.send_text.await_count, calls)
        self.assertEqual(len(self.store.get_case('owner', cid)['messages']), 1)
        ordinary = {'item_list': [{'type': 1, 'text_item': {'text': '今天有什么任务？'}}]}
        self.assertFalse(asyncio.run(_handle_case_reply(session, ordinary)))

    def test_feedback_admin_wechat_reply_reaches_original_consumer_with_fake_transport(self):
        from app.channels.wechat.admin_bridge import _handle_case_reply
        target = {**self.target, 'source': 'wechat', 'session_id': 'consumer-ws', 'recipient_id': 'consumerwx'}
        cid = self.case(target=target)
        admin_client = SimpleNamespace(send_text=AsyncMock(return_value={'ret': 0}))
        consumer_client = SimpleNamespace(send_text=AsyncMock(return_value={'ret': 0}))
        admin = {'user_id': 'owner', 'connected': True, 'conversation_id': 'adminconv', 'from_user_id': 'adminwx',
                 'context_token': 'adminctx', 'client': admin_client}
        consumer = SimpleNamespace(from_user_id='consumerwx', context_token='consumerctx')
        manager = SimpleNamespace(get_session=lambda sid: consumer, get_client=lambda sid: consumer_client)
        with patch.object(sm, '_owner'), patch.object(sm, '_authorize_target'), \
             patch.object(sm, 'get_store', return_value=self.store), patch.object(inbox, '_store', return_value=self.store), \
             patch('app.channels.wechat.admin_router._get_session', return_value=admin), \
             patch('app.channels.wechat.session_manager.get_session_manager', return_value=manager), \
             patch('app.services.published.save_consumer_message') as history:
            asyncio.run(sm.deliver_one(self.store, self.store.claim()))
            self.assertIn(f'回复 {cid}：', admin_client.send_text.await_args.args[1])
            raw = {'message_id': 'incoming-1', 'from_user_id': 'adminwx', 'context_token': 'adminctx',
                   'item_list': [{'type': 1, 'text_item': {'text': f'回复 {cid}：管理员的明确回复'}}]}
            asyncio.run(_handle_case_reply(admin, raw))
            asyncio.run(sm.deliver_one(self.store, self.store.claim()))
            asyncio.run(sm.deliver_one(self.store, self.store.claim()))
        consumer_client.send_text.assert_awaited_once_with('consumerwx', '管理员的明确回复', 'consumerctx')
        self.assertEqual(history.call_args.kwargs['metadata']['author_type'], 'admin')
        self.assertEqual(self.store.get_case('owner', cid)['case_status'], 'replied')
        self.assertTrue(all(d['status'] == 'delivered' for d in self.store.get_case('owner', cid)['deliveries']))


if __name__ == '__main__':
    unittest.main()
