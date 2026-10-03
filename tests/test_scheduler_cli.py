"""Bounded admin scheduler -> CLI authorization without a live provider."""
import os
import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from app.core import security
from app.execution import context
from app.execution.grants import capture
from app.execution.agent import create_scheduled_agent
from app.execution.store import task_key
from app.runtime.business_tools import ScheduledBusinessTools
from app.runtime.consumer import authorize_scheduler
from app.runtime.cursor import CursorAdapter
from app.services import scheduler as sch, scheduler_tree as tree, workspace_lock as wl
from app.storage.local import LocalStorageService


CHOICE = {'runtime': 'codex', 'profile_id': 'profile', 'model': 'model'}
BINDING = {'runtime': 'codex', 'profile_id': 'profile', 'model': 'model', 'image_mode': 'off'}


class SchedulerCliTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, name, value in (
            (security, 'USERS_DIR', self.tmp.name),
            (__import__('app.storage.local', fromlist=['USERS_DIR']), 'USERS_DIR', self.tmp.name),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(security, '_load_users', return_value={'owner': {'username': 'owner'}})
        p.start()
        self.addCleanup(p.stop)
        p = patch('app.runtime.chat.choice', return_value=dict(BINDING))
        p.start()
        self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'DISABLE_SCHEDULER': '0'})
        p.start()
        self.addCleanup(p.stop)
        tree.invalidate_path_cache()
        sch._heap.clear()
        sch._heap_index.clear()
        self.store = context.get_store()
        self.addCleanup(self.store.close)

    def make(self, **config):
        return sch.create_task('owner', {'name': 'CLI', 'task_type': 'agent',
            'schedule_type': 'interval', 'schedule': '3600', 'task_config': {
                'prompt': 'summarize', 'runtime_choice': dict(CHOICE),
                'permissions': {'read_dirs': ['docs'], 'write_dirs': ['generated']}, **config}})

    def bind(self, task):
        snapshot = {**task, 'execution_grant': capture(task, 'owner')}
        queued = self.store.submit(task_key('admin', 'owner', None, task['id']), 'owner', snapshot, manual=True)
        row = self.store.claim(queued['id'])
        binding = {**BINDING, 'scheduler_scope': {'run_id': row['id'], 'task_id': task['id'],
                   'revision': task['revision'], 'web': False, 'image': False}}
        return row, binding

    def test_save_overwrites_binding_and_legacy_remains_deepagents(self):
        task = self.make(runtime_binding={'runtime': 'cursor', 'profile_id': 'attacker'})
        self.assertEqual(task['task_config']['runtime_binding'], BINDING)
        legacy = sch.create_task('owner', {'name': 'old', 'task_type': 'agent',
            'schedule_type': 'interval', 'schedule': '3600', 'task_config': {'prompt': 'old'}})
        self.assertNotIn('runtime_binding', legacy['task_config'])
        switched = sch.update_task('owner', task['id'], {'task_type': 'script'})
        self.assertNotIn('runtime_choice', switched['task_config'])
        self.assertNotIn('runtime_binding', switched['task_config'])

    def test_save_rejects_incomplete_or_unsupported_choice(self):
        with self.assertRaises(ValueError):
            self.make(runtime_choice={'runtime': 'codex', 'profile_id': 'profile'})
        with self.assertRaises(ValueError):
            self.make(capabilities=['speech'])
        with self.assertRaises(ValueError):
            self.make(capabilities=['scheduler'])
        with self.assertRaises(ValueError):
            sch.create_task('owner', {'task_type': 'agent', 'schedule_type': 'interval',
                'schedule': '3600', 'task_config': {'prompt': 'legacy', 'capabilities': ['web']}})

    def test_deepagents_never_advertises_cli_only_capabilities(self):
        grant = SimpleNamespace(saved={'capabilities': ['web']}, policies=Mock())
        with patch('app.execution.agent.Grant', return_value=grant), self.assertRaises(PermissionError):
            create_scheduled_agent('model')

    def test_local_binary_archive_uses_atomic_fsynced_write(self):
        storage = LocalStorageService()
        data = b'\x89PNG\r\n\x1a\nfixture'
        with patch('app.storage.local.os.fsync', wraps=os.fsync) as sync, \
             patch('app.storage.local.os.replace', wraps=os.replace) as replace:
            storage.write_bytes_durable('owner', '/generated/image.png', data)
        self.assertEqual(storage.read_bytes('owner', '/generated/image.png'), data)
        self.assertEqual(replace.call_count, 1)
        self.assertGreaterEqual(sync.call_count, 2)  # Temporary file and parent directory.

    def test_scoped_authorization_follows_task_revision_and_grant(self):
        task = self.make()
        row, binding = self.bind(task)
        self.assertEqual(authorize_scheduler('owner', binding).run['id'], row['id'])
        with self.assertRaises(HTTPException):
            authorize_scheduler('owner', {**binding, 'scheduler_scope': {**binding['scheduler_scope'], 'web': True}})
        sch.update_task('owner', task['id'], {'name': 'edited'})
        with self.assertRaises(HTTPException):
            authorize_scheduler('owner', binding)

    def test_finalize_suppresses_delivery_after_task_edit(self):
        task = self.make()
        sch.update_task('owner', task['id'], {'reply_to': {
            'channel': 'web', 'admin_id': 'owner', 'conversation_id': 'conversation'}})
        task = sch.get_task('owner', task['id'])
        row, _binding = self.bind(task)
        sch.update_task('owner', task['id'], {'name': 'changed while CLI returned'})
        now = datetime.now(timezone.utc).isoformat()
        record = {'run_id': row['id'], 'status': 'success', 'output': 'stale result',
                  'started_at': now, 'finished_at': now, 'steps': []}
        token = context._current.set(context.ExecutionContext(self.store, row))
        try:
            sch._finalize_execution('admin', 'owner', None, task['id'], task['revision'], True,
                                    record, {'task_id': task['id']})
        finally:
            context._current.reset(token)
        self.assertEqual(self.store.get(row['id'])['result']['status'], 'blocked')
        self.assertEqual(self.store.deliveries(row['id'], 'owner'), [])

    def test_manual_disabled_cli_task_can_deliver_if_revision_unchanged(self):
        task = self.make()
        task = sch.update_task('owner', task['id'], {'reply_to': {
            'channel': 'web', 'admin_id': 'owner', 'conversation_id': 'conversation'}})
        task = sch.update_task('owner', task['id'], {'enabled': False})
        row, _binding = self.bind(task)
        now = datetime.now(timezone.utc).isoformat()
        record = {'run_id': row['id'], 'status': 'success', 'output': 'manual result',
                  'started_at': now, 'finished_at': now, 'steps': []}
        token = context._current.set(context.ExecutionContext(self.store, row))
        try:
            runtime = SimpleNamespace(profiles=SimpleNamespace(authorize=Mock()))
            with patch('app.runtime.manager.get_runtime', return_value=runtime):
                sch._finalize_execution('admin', 'owner', None, task['id'], task['revision'], True,
                                        record, {'task_id': task['id']})
        finally:
            context._current.reset(token)
        self.assertEqual(self.store.get(row['id'])['result']['status'], 'success')
        self.assertTrue(self.store.deliveries(row['id'], 'owner'))

    async def test_file_tools_require_path_lock_and_stable_write_id(self):
        task = self.make()
        row, binding = self.bind(task)
        owner = 'scheduled-' + row['id']
        wl.register_process(owner, 'owner', kind='scheduled', label='test')
        self.addCleanup(wl.unregister_process, owner)
        self.assertTrue(wl.try_acquire(owner, ['/generated']).ok)
        storage = LocalStorageService()
        runtime_store = SimpleNamespace(emit=Mock())
        tools = ScheduledBusinessTools(storage, runtime_store, lambda actor, choice: True)
        session = {'actor_id': 'owner', 'binding': binding}
        run = {'id': 'native-run'}
        params = {'tool': 'jellyfish_scheduled_write_file', 'callId': 'call-1',
                  'arguments': {'path': '/generated/result.txt', 'content': 'first'}}
        self.assertTrue((await tools(session, run, params))['success'])
        self.assertEqual(storage.read_text('owner', '/generated/result.txt'), 'first')
        self.assertFalse((await tools(session, run, {**params, 'arguments': {**params['arguments'], 'content': 'second'}}))['success'])
        self.assertEqual(storage.read_text('owner', '/generated/result.txt'), 'first')
        self.assertFalse((await tools(session, run, {**params, 'callId': 'call-2',
            'arguments': {'path': '/docs/private.txt', 'content': 'bad'}}))['success'])
        self.assertFalse((await tools(session, run, {**params, 'callId': 'call-3',
            'arguments': {'path': '/generated/large.txt', 'content': '中' * 30000}}))['success'])
        self.assertEqual(len(self.store.effects(row['id'], 'owner')), 1)

    async def test_outer_cancellation_stops_inner_runtime_run(self):
        task = self.make()
        row, _binding = self.bind(task)
        queued = asyncio.Event()
        inner = {'id': 'inner', 'status': 'running'}
        runtime = SimpleNamespace(
            store=SimpleNamespace(put=Mock(), get=Mock(return_value=inner)),
            runs=SimpleNamespace(authorize=Mock(), create_session=Mock(return_value={'id': 'session'}),
                enqueue=Mock(side_effect=lambda *a, **k: (queued.set(), {'id': 'inner'})[1]),
                cancel=AsyncMock()))
        token = context._current.set(context.ExecutionContext(self.store, row))
        try:
            with patch('app.runtime.manager.get_runtime', return_value=runtime):
                work = asyncio.create_task(sch._run_cli_agent_task('owner', task['task_config'], 'prompt', []))
                await asyncio.wait_for(queued.wait(), 1)
                work.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await work
            runtime.runs.cancel.assert_awaited_once_with('owner', 'inner')
        finally:
            context._current.reset(token)

    async def test_oversized_reference_document_fails_before_cli_submission(self):
        task = self.make()
        row, _binding = self.bind(task)
        token = context._current.set(context.ExecutionContext(self.store, row))
        try:
            with patch('app.runtime.manager.get_runtime') as runtime:
                result = await sch._run_cli_agent_task('owner', task['task_config'], '文' * 32001, [])
            runtime.assert_not_called()
        finally:
            context._current.reset(token)
        self.assertFalse(result['success'])
        self.assertIn('超过 32000 字符上限', result['output'])
        self.assertEqual(result['steps'][-1]['type'], 'cli_failed')

    async def test_cursor_generic_search_cannot_read_local_files(self):
        adapter = CursorAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'model')
        adapter.service_scope = {'run_id': 'scheduled', 'web': True, 'image': False}
        adapter.rpc = SimpleNamespace(send=AsyncMock())
        options = [{'kind': 'allow_once', 'optionId': 'yes'}, {'kind': 'reject_once', 'optionId': 'no'}]
        for call_id, title, expected in [('local_search_1', 'Search files', 'no'),
                                         ('web_search_0', 'Web search: test query', 'yes')]:
            events = [event async for event in adapter.request_events({'id': call_id,
                'method': 'session/request_permission', 'params': {'toolCall': {
                    'kind': 'search', 'title': title, 'toolCallId': call_id}, 'options': options}})]
            self.assertEqual(events, [])
            self.assertEqual(adapter.rpc.send.await_args.args[0]['result']['outcome']['optionId'], expected)
