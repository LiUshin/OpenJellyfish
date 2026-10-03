"""Usage totals must combine measured CLI turns without double-counting Services."""
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.runtime.store import RuntimeStore
from app.services.token_usage import _usage_path, aggregate_usage, record_llm_usage


class TokenUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        users = root / 'users'
        users.mkdir()
        self.runtime_root = root / 'runtime'
        patch_users = patch('app.core.security.USERS_DIR', str(users))
        patch_runtime = patch.dict(os.environ, {'JELLYFISH_RUNTIME_DATA_DIR': str(self.runtime_root)})
        patch_users.start()
        patch_runtime.start()
        self.addCleanup(patch_users.stop)
        self.addCleanup(patch_runtime.stop)

    def _run(self, store, name, runtime, *, usage=None, scope=None, channel='web'):
        binding = {'runtime': runtime, 'model': 'model'}
        if scope:
            binding.update(scope)
        store.put('run', {'id': name, 'actor_id': 'alice', 'binding': binding,
                          'status': 'completed', 'started_at': time.time(),
                          'created_at': time.time(), 'channel': channel, 'usage': usage})

    def test_cli_admin_turns_merge_without_counting_service_twice(self):
        record_llm_usage('alice', 'openai:chat', 10, 2, channel='web')
        record_llm_usage('alice', 'codex:model', 12, 3, service_id='svc',
                         channel='api', runtime='codex')
        store = RuntimeStore(self.runtime_root)
        try:
            self._run(store, 'admin-codex', 'codex', usage={
                'inputTokens': 20, 'outputTokens': 5, 'totalTokens': 25}, channel='wechat')
            self._run(store, 'admin-cursor', 'cursor')
            self._run(store, 'service-codex', 'codex', usage={
                'last': {'inputTokens': 12, 'outputTokens': 3}},
                scope={'service_scope': {'service_id': 'svc'}})
            self._run(store, 'service-cursor', 'cursor',
                      scope={'service_scope': {'service_id': 'svc'}})
            self._run(store, 'other-service-cursor', 'cursor',
                      scope={'service_scope': {'service_id': 'other'}})
            self._run(store, 'future-cursor', 'cursor')
            future = store.get('run', 'future-cursor')
            future['created_at'] = future['started_at'] = time.time() + 62 * 86400
            store.put('run', future)
            self._run(store, 'scheduled-codex', 'codex', usage={
                'inputTokens': 7, 'outputTokens': 1},
                scope={'scheduler_scope': {'task_id': 'task'}})
            self._run(store, 'other-user', 'codex', usage={
                'inputTokens': 100, 'outputTokens': 100})
            row = store.get('run', 'other-user')
            row['actor_id'] = 'bob'
            store.put('run', row)
            all_usage = aggregate_usage('alice', months=1)
            service_usage = aggregate_usage('alice', months=1, service_id='svc')
        finally:
            store.close()
        self.assertEqual(all_usage['total'], {'calls': 4, 'input_tokens': 49,
                                               'output_tokens': 11, 'total_tokens': 60})
        self.assertEqual(service_usage['total']['total_tokens'], 15)
        self.assertEqual(service_usage['total']['calls'], 1)
        self.assertEqual(all_usage['coverage']['unreported_by_core'], {'cursor': 3, 'codex': 1})
        self.assertEqual(service_usage['coverage']['unreported_by_core'], {'cursor': 1, 'codex': 1})
        self.assertEqual({r['name']: r['total_tokens'] for r in all_usage['by_core']},
                         {'deepagents': 12, 'codex': 48})
        self.assertEqual({r['name']: r['total_tokens'] for r in all_usage['by_channel']},
                         {'web': 12, 'api': 15, 'wechat': 25, 'scheduler': 8})

    def test_empty_current_month_does_not_relabel_old_usage_as_this_month(self):
        now = datetime.now().astimezone()
        year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        old_path = Path(_usage_path('alice', year, month))
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_text('{"model":"old","input_tokens":10,"output_tokens":2}\n')
        self.assertEqual(aggregate_usage('alice', months=1)['total']['calls'], 0)
        self.assertEqual(aggregate_usage('alice', months=2)['total']['calls'], 1)

    def test_thread_cumulative_total_is_not_counted_as_a_turn(self):
        store = RuntimeStore(self.runtime_root)
        try:
            self._run(store, 'missing-last', 'codex', usage={
                'total': {'inputTokens': 1000, 'outputTokens': 100}})
            summary = aggregate_usage('alice', months=1)
        finally:
            store.close()
        self.assertEqual(summary['total']['total_tokens'], 0)
        self.assertEqual(summary['coverage']['unreported_by_core'], {'codex': 1})
