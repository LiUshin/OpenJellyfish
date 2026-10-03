import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.runtime.business_tools import AreaDirectory, AreaDocument, BusinessTools, Document, specifications
from app.services import published, service_messaging
from app.services.memory_tools import get_soul_config, save_soul_config
from app.services.service_records import list_area, read_area


class ServiceRecordsAreaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.users = Path(self.tmp.name) / 'users'
        self.users.mkdir()
        self.patch_users = patch('app.core.security.USERS_DIR', str(self.users))
        self.patch_users.start()
        self.addCleanup(self.patch_users.stop)
        self.addCleanup(self.tmp.cleanup)
        self.service = published.create_service('alice', {
            'name': 'Alice Service', 'published': True, 'secret_config': 'DO_NOT_EXPOSE_CONFIG',
        })
        self.service_id = self.service['id']
        self.other = published.create_service('bob', {'name': 'Bob Service', 'published': True})
        self.conv = published.create_consumer_conversation('alice', self.service_id,
                                                            title='Customer issue', source='api')
        self.conv_id = self.conv['id']
        self.base = f'/service-records/{self.service_id}'
        self.events = []
        self.bridge = BusinessTools(None, lambda actor, binding: None,
                                    SimpleNamespace(emit=lambda *args: self.events.append(args)))
        self.session = {'actor_id': 'alice', 'binding': {'runtime': 'codex'}}

    def enable(self, actor='alice', **extra):
        save_soul_config(actor, {'service_records_enabled': True, **extra})

    async def test_disabled_by_default_and_legacy_flag_never_grants_access(self):
        save_soul_config('alice', {'include_consumer_conversations': True})
        with self.assertRaises(ValueError):
            list_area('alice', '/service-records')
        with self.assertRaises(ValueError):
            read_area('alice', self.base + '/conversations/' + self.conv_id)
        self.enable(include_consumer_conversations=False)
        self.assertEqual(list_area('alice', '/service-records')['items'][0]['name'], 'inbox')
        save_soul_config('alice', {'service_records_enabled': False,
                                   'include_consumer_conversations': True})
        with self.assertRaises(ValueError):
            list_area('alice', '/service-records')

    async def test_legacy_soul_put_cannot_reissue_old_consumer_permission(self):
        from app.routes.settings_routes import api_update_soul_config

        with self.assertRaises(HTTPException) as caught:
            await api_update_soul_config({'include_consumer_conversations': True}, user={'user_id': 'alice'})
        self.assertEqual(caught.exception.status_code, 422)
        self.assertFalse(get_soul_config('alice')['service_records_enabled'])
        save_soul_config('alice', {'include_consumer_conversations': True})
        result = await api_update_soul_config({'service_records_enabled': True}, user={'user_id': 'alice'})
        self.assertTrue(result['config']['service_records_enabled'])
        self.assertFalse(result['config']['include_consumer_conversations'])

    async def test_owner_paths_and_pagination_are_strict(self):
        self.enable()
        self.enable('bob')
        root = list_area('alice', '/service-records')
        self.assertIn(self.service_id, [item.get('id') for item in root['items']])
        self.assertNotIn(self.other['id'], [item.get('id') for item in root['items']])
        self.assertEqual([item['name'] for item in list_area('alice', self.base)['items']],
                         ['conversations', 'usage', 'token-usage'])
        for bad in ('/service-recordsX', '/service-records/../bob',
                    '/service-records/' + self.service_id + '/conversations/../x',
                    '/service-records/' + self.service_id + '/conversations/%2e%2e',
                    '/service-records/' + self.service_id + '/conversations//x',
                    '/service-records/' + self.service_id + '/keys.json'):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                list_area('alice', bad)
        with self.assertRaises(ValueError):
            list_area('bob', self.base)
        for offset, limit in ((-1, 1), (0, 0), (0, 21), (1000001, 1), (True, 1)):
            with self.subTest(offset=offset, limit=limit), self.assertRaises(ValueError):
                list_area('alice', '/service-records', offset, limit)

    async def test_conversation_page_and_complete_long_message_continuation(self):
        self.enable()
        published.save_consumer_message('alice', self.service_id, self.conv_id,
                                        'user', 'first', metadata={'message_id': 'first-id'})
        published.save_consumer_message('alice', self.service_id, self.conv_id,
                                        'assistant', 'middle')
        long_text = '你好🙂' * 14000
        published.save_consumer_message('alice', self.service_id, self.conv_id,
                                        'user', long_text, metadata={'message_id': 'long-id'})
        path = self.base + '/conversations/' + self.conv_id
        summary = list_area('alice', self.base + '/conversations')
        self.assertEqual(summary['items'][0]['id'], self.conv_id)
        latest = read_area('alice', path, limit=1)
        self.assertTrue(latest['items'][0]['content_truncated'])
        self.assertEqual(latest['items'][0]['message_offset'], 0)
        ref = latest['items'][0]['message_ref']
        assembled = latest['items'][0]['content']
        next_chunk = latest['items'][0]['next_content_offset']
        while next_chunk is not None:
            page = read_area('alice', path, offset=0, limit=1, content_offset=next_chunk, message_ref=ref)
            item = page['items'][0]
            self.assertEqual(item['message_ref'], ref)
            self.assertTrue(item['content'])
            assembled += item['content']
            next_chunk = item['next_content_offset']
        self.assertEqual(assembled, long_text)
        self.assertEqual(read_area('alice', path, offset=1, limit=1)['items'][0]['content'], 'middle')
        self.assertEqual(read_area('alice', path, offset=2, limit=1)['items'][0]['content'], 'first')
        with self.assertRaises(ValueError):
            read_area('alice', path, offset=0, limit=1, content_offset=1)
        published.save_consumer_message('alice', self.service_id, self.conv_id, 'user', 'newest')
        with self.assertRaises(ValueError):
            read_area('alice', path, offset=0, limit=1, content_offset=1, message_ref=ref)

    async def test_conversation_can_page_beyond_500_messages(self):
        self.enable()
        path = Path(published._conv_msgs_path('alice', self.service_id, self.conv_id))
        path.write_text(''.join(json.dumps({'role': 'user', 'content': f'message-{i}',
                                            'timestamp': f't-{i}'}) + '\n' for i in range(520)))
        result = read_area('alice', self.base + '/conversations/' + self.conv_id,
                           offset=501, limit=1)
        self.assertEqual(result['items'][0]['content'], 'message-18')
        self.assertEqual(result['next_offset'], 502)

    async def test_feedback_usage_and_token_projection_excludes_private_fields(self):
        self.enable()
        store = service_messaging.get_store()
        case_id = store.create_case('alice', self.service_id, self.conv_id, 'Customer feedback',
                                    {'source': 'api', 'recipient_id': 'SECRET_RECIPIENT',
                                     'session_id': 'SECRET_SESSION'},
                                    'Alice Service', request_key='case-1',
                                    admin_target={'recipient_id': 'SECRET_ADMIN_TARGET'})
        inbox_page = list_area('alice', '/service-records/inbox')
        self.assertEqual(inbox_page['items'][0]['id'], case_id)
        detail = read_area('alice', '/service-records/inbox/' + case_id)
        self.assertEqual(detail['message']['content'], 'Customer feedback')
        self.assertEqual(detail['service_id'], self.service_id)
        combined = json.dumps([inbox_page, detail], ensure_ascii=False)
        for secret in ('SECRET_RECIPIENT', 'SECRET_SESSION', 'SECRET_ADMIN_TARGET',
                       'target', 'deliveries', 'wechat_session_id', 'agent_response'):
            self.assertNotIn(secret, combined)
        from app.services.usage_log import record_request
        from app.services.token_usage import record_llm_usage

        record_request('alice', self.service_id, channel='api', key_id='SECRET_KEY_ID',
                       conv_id=self.conv_id, endpoint='POST /api/v1/chat', status_code=200)
        record_llm_usage('alice', 'provider:fixture', 123, 45,
                         service_id=self.service_id, key_id='SECRET_KEY_ID', channel='api')
        usage = list_area('alice', self.base + '/usage')
        self.assertEqual(usage['items'][0]['channel'], 'api')
        self.assertEqual(usage['window'], 'latest_six_month_files')
        token = read_area('alice', self.base + '/token-usage')
        self.assertEqual(token['total']['total_tokens'], 168)
        self.assertNotIn('by_key', token)
        self.assertNotIn('SECRET_KEY_ID', json.dumps([usage, token]))
        self.assertNotIn('DO_NOT_EXPOSE_CONFIG', json.dumps([root := list_area('alice', '/service-records'), usage, token]))
        self.assertTrue(root['items'])

    async def test_long_feedback_can_be_continued_with_stable_reference(self):
        self.enable()
        store = service_messaging.get_store()
        feedback = '🙂' * 20000
        case_id = store.create_case('alice', self.service_id, self.conv_id, feedback,
                                    {'source': 'api', 'recipient_id': 'SECRET_RECIPIENT'},
                                    'Alice Service', request_key='long-case', admin_target={})
        path = '/service-records/inbox/' + case_id
        first = read_area('alice', path)
        self.assertTrue(first['message']['content_truncated'])
        ref = first['message_ref']
        with self.assertRaises(ValueError):
            read_area('alice', path, content_offset=first['message']['next_content_offset'])
        assembled = first['message']['content']
        next_chunk = first['message']['next_content_offset']
        while next_chunk is not None:
            page = read_area('alice', path, content_offset=next_chunk, message_ref=ref)
            self.assertTrue(page['message']['content'])
            assembled += page['message']['content']
            next_chunk = page['message']['next_content_offset']
        self.assertEqual(assembled, feedback)

    async def test_revocation_during_read_and_cli_bridge_scope(self):
        self.enable()
        with patch('app.services.published.list_services', side_effect=lambda actor: (
                save_soul_config(actor, {'service_records_enabled': False}) or [])):
            with self.assertRaises(ValueError):
                list_area('alice', '/service-records')
        self.enable()
        names = {s['name'] for s in specifications()}
        self.assertIn('jellyfish_list_documents', names)
        self.assertIn('jellyfish_read_document', names)
        self.assertNotIn('jellyfish_list_area', names)
        listed = await self.bridge(self.session, {}, {'tool': 'jellyfish_list_documents',
                       'arguments': {'path': '/service-records'}})
        self.assertTrue(listed['success'])
        self.assertIn(self.service_id, listed['contentItems'][0]['text'])
        # The old tool's path-only schema still works for existing native threads.
        self.assertEqual(self.bridge.invoke('alice', 'jellyfish_list_documents',
                                            AreaDirectory(path='/service-records'))['path'], '/service-records')
        self.assertEqual(self.bridge.invoke('alice', 'jellyfish_read_document',
                                            Document(path=self.base + '/token-usage'))['service_id'], self.service_id)
        save_soul_config('alice', {'service_records_enabled': False})
        denied = await self.bridge(self.session, {}, {'tool': 'jellyfish_list_documents',
                       'arguments': {'path': '/service-records'}})
        self.assertFalse(denied['success'])
        self.assertIn('缩小 limit', denied['contentItems'][0]['text'])
        self.assertNotIn(self.service_id, denied['contentItems'][0]['text'])
        with patch('app.runtime.consumer_tools.ServiceTools.config', return_value={}):
            service = await self.bridge({'actor_id': 'alice', 'binding': {'service_scope': {'service_id': self.service_id}}},
                                        {}, {'tool': 'jellyfish_read_document',
                                             'arguments': {'path': self.base + '/token-usage'}})
        self.assertFalse(service['success'])
        with patch('app.runtime.consumer.authorize_scheduler', return_value=object()):
            scheduled = await self.bridge({'actor_id': 'alice', 'binding': {'scheduler_scope': {'run_id': 'r'}}},
                                          {}, {'tool': 'jellyfish_read_document',
                                               'arguments': {'path': self.base + '/token-usage'}})
        self.assertFalse(scheduled['success'])


if __name__ == '__main__':
    unittest.main()
