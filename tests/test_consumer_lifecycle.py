"""Deletion and delayed writes serialize across processes without reviving data."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.services import published

# Child processes call the real storage functions and coordinate only at the
# filesystem operation, while the production lifecycle lock is already held.
CHILD = r'''
import json, shutil, sys
from app.core import security
from app.services import published
security.USERS_DIR = sys.argv[1]
service, conversation, operation = sys.argv[2:5]
if operation in ('config', 'sessions'):
    print('ready', flush=True)
    if operation == 'config':
        print('missing' if published.update_service('alice', service, {'name': 'late'}) is None else 'saved', flush=True)
    else:
        from app.channels.wechat.session_manager import WeChatSessionManager
        WeChatSessionManager()._save_sessions('alice', service)
        print('finished', flush=True)
elif operation.startswith('delete'):
    if operation.endswith('-paused'):
        remove = shutil.rmtree
        def paused(path, *args, **kwargs):
            print('locked', flush=True)
            sys.stdin.readline()
            return remove(path, *args, **kwargs)
        shutil.rmtree = paused
    else:
        print('ready', flush=True)
    result = (published.delete_service('alice', service) if 'service' in operation else
              published.delete_consumer_conversation('alice', service, conversation))
    print('deleted' if result else 'absent', flush=True)
else:
    if operation == 'save-paused':
        append = published.append_jsonl
        def paused(*args, **kwargs):
            print('locked', flush=True)
            sys.stdin.readline()
            return append(*args, **kwargs)
        published.append_jsonl = paused
    else:
        print('ready', flush=True)
    try:
        published.save_consumer_message('alice', service, conversation, 'assistant', 'late output')
        print('saved', flush=True)
    except FileNotFoundError:
        print('missing', flush=True)
'''


class ConsumerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch('app.core.security.USERS_DIR', self.temp.name); self.patch.start()
        self.service = published.create_service('alice', {'name': 'fixture', 'published': True})['id']
        self.conv = published.create_consumer_conversation('alice', self.service)['id']
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None: child.kill()
            child.communicate()
        self.patch.stop(); self.temp.cleanup()

    def child(self, operation):
        child = subprocess.Popen([sys.executable, '-c', CHILD, self.temp.name, self.service, self.conv, operation],
                                 cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        return child

    def test_late_message_cannot_create_a_missing_conversation(self):
        published.delete_consumer_conversation('alice', self.service, self.conv)
        for kwargs in ({}, {'event_id': 'projection'}):
            with self.assertRaises(FileNotFoundError):
                published.save_consumer_message('alice', self.service, self.conv, 'assistant', 'late', **kwargs)
        self.assertIsNone(published.get_consumer_conversation('alice', self.service, self.conv))
        self.assertFalse(Path(published._conv_dir('alice', self.service, self.conv)).exists())

    def test_legacy_conversation_is_migrated_before_a_valid_save(self):
        directory = Path(published._conv_dir('alice', self.service, self.conv))
        (directory / 'meta.json').unlink(); (directory / 'messages.jsonl').unlink()
        (directory / 'messages.json').write_text(json.dumps({'id': self.conv, 'messages': [
            {'role': 'user', 'content': 'before'}]}))
        published.save_consumer_message('alice', self.service, self.conv, 'assistant', 'after')
        conv = published.get_consumer_conversation('alice', self.service, self.conv)
        self.assertEqual([m['content'] for m in conv['messages']], ['before', 'after'])
        self.assertEqual(conv['message_count'], 2)

    def test_concurrent_appends_keep_metadata_count_and_event_deduplication(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda n: published.save_consumer_message('alice', self.service, self.conv,
                     'assistant', str(n)), range(20)))
            list(pool.map(lambda n: published.save_consumer_message('alice', self.service, self.conv,
                     'assistant', 'one event', event_id='same-event'), range(8)))
        conv = published.get_consumer_conversation('alice', self.service, self.conv)
        self.assertEqual(len(conv['messages']), 21)
        self.assertEqual(conv['message_count'], 21)

    def test_delete_holds_stable_lock_against_late_writer_in_another_process(self):
        for operation in ('delete-conversation-paused', 'delete-service-paused'):
            with self.subTest(operation=operation):
                if not published.get_service('alice', self.service):
                    self.service = published.create_service('alice', {'name': 'fixture'})['id']
                self.conv = published.create_consumer_conversation('alice', self.service)['id']
                delete = self.child(operation)
                self.assertEqual(delete.stdout.readline().strip(), 'locked')
                late = self.child('save')
                self.assertEqual(late.stdout.readline().strip(), 'ready')
                deleted, err = delete.communicate('\n', timeout=5)
                self.assertEqual(delete.returncode, 0, err)
                self.assertIn('deleted', deleted)
                result, err = late.communicate(timeout=5)
                self.assertEqual(late.returncode, 0, err)
                self.assertIn('missing', result)
                self.assertFalse(Path(published._conv_dir('alice', self.service, self.conv)).exists())
                self.assertTrue(Path(published._consumer_lock_path('alice', self.service) + '.lock').exists())

    def test_delete_waits_for_full_message_commit_then_removes_it(self):
        save = self.child('save-paused')
        self.assertEqual(save.stdout.readline().strip(), 'locked')
        delete = self.child('delete-conversation')
        self.assertEqual(delete.stdout.readline().strip(), 'ready')
        result, err = save.communicate('\n', timeout=5)
        self.assertEqual(save.returncode, 0, err); self.assertIn('saved', result)
        result, err = delete.communicate(timeout=5)
        self.assertEqual(delete.returncode, 0, err); self.assertIn('deleted', result)
        self.assertFalse(Path(published._conv_dir('alice', self.service, self.conv)).exists())

    def test_late_service_config_or_wechat_bookkeeping_cannot_revive_deleted_service(self):
        for operation in ('config', 'sessions'):
            with self.subTest(operation=operation):
                self.service = published.create_service('alice', {'name': 'fixture'})['id']
                delete = self.child('delete-service-paused')
                self.assertEqual(delete.stdout.readline().strip(), 'locked')
                late = self.child(operation)
                self.assertEqual(late.stdout.readline().strip(), 'ready')
                result, err = delete.communicate('\n', timeout=5)
                self.assertEqual(delete.returncode, 0, err); self.assertIn('deleted', result)
                result, err = late.communicate(timeout=5)
                self.assertEqual(late.returncode, 0, err)
                self.assertIn('missing' if operation == 'config' else 'finished', result)
                self.assertFalse(Path(published._service_dir('alice', self.service)).exists())


if __name__ == '__main__': unittest.main()
