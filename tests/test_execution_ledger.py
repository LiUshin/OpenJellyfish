"""Fault-injection tests for the scheduling ledger. No accounts, LLM or remote sends."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from concurrent.futures import ThreadPoolExecutor

from app.execution.store import ExecutionStore, Conflict, task_key
from app.execution import context
from app.execution.grants import Grant, capture
from app.execution.agent import build_tools
from app.execution.outbox import deliver_one
from app.core import security
from app.core.jsonl_store import append_jsonl, append_jsonl_once, read_jsonl
from app.services import scheduler as sch, scheduler_tree as st, scheduled_inject as inj
from app.routes import scheduler as routes


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name, 'runs.sqlite3')
        self.store = ExecutionStore(self.path)
        self.addCleanup(self.store.close)
        self.key = task_key('admin', 'owner', None, 'task')
        self.snapshot = {'id': 'task', 'revision': 1, 'next_run_at': '2030-01-01', 'task_config': {'prompt': 'test'}}

    def submit(self, **kwargs):
        return self.store.submit(self.key, 'owner', self.snapshot, **kwargs)

    def finish(self, row, **kwargs):
        self.store.finish(row['id'], row['token'], {'run_id': row['id'], 'status': 'success', 'output': 'done'}, **kwargs)

    def test_manual_request_is_stable_after_runtime_fields_change(self):
        row = self.store.claim(self.submit(manual=True, request_id='request')['id'])
        self.finish(row)
        self.snapshot.update(run_count=1, last_run_at='now', next_run_at='later')
        again = self.submit(manual=True, request_id='request')
        self.assertEqual(again['id'], row['id'])
        self.snapshot['task_config'] = {'prompt': 'different'}
        with self.assertRaises(Conflict):
            self.submit(manual=True, request_id='request')

    def test_occurrence_deduplicates_and_other_manual_work_conflicts(self):
        row = self.submit()
        self.assertEqual(self.submit()['id'], row['id'])
        with self.assertRaises(Conflict):
            self.submit(manual=True)
        self.assertIsNone(self.store.get(row['id'], 'other-owner'))

    def test_submission_capacity_and_owner_budget_are_transactional(self):
        self.submit()
        with self.assertRaises(Conflict):
            self.store.submit(task_key('admin', 'owner', None, 'other'), 'owner', self.snapshot, per_owner=1)
        self.assertEqual(len(self.store.queued()), 1)

    def test_two_connections_cannot_claim_same_occurrence(self):
        second = ExecutionStore(self.path)
        self.addCleanup(second.close)
        with ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(lambda store: store.submit(self.key, 'owner', self.snapshot), [self.store, second]))
        self.assertEqual(rows[0]['id'], rows[1]['id'])
        self.assertIsNotNone(self.store.claim(rows[0]['id']))
        self.assertIsNone(second.claim(rows[0]['id']))

    def test_result_outbox_cursor_transaction_rolls_back_together(self):
        row = self.store.claim(self.submit()['id'])
        delivery = {'channel': 'web', 'target': {'conversation_id': 'conv'}, 'payload': {'text': 'done'}}
        with self.assertRaises(Exception):
            self.finish(row, cursor=(1, {'next_run_at': None}), deliveries=[delivery, delivery])
        self.assertEqual(self.store.get(row['id'])['status'], 'running')
        self.assertEqual(self.store.deliveries(row['id'], 'owner'), [])
        self.assertEqual(self.store.overlay(self.key, self.snapshot), self.snapshot)
        self.finish(row, cursor=(1, {'next_run_at': None}), deliveries=[delivery])
        self.assertIsNone(self.store.overlay(self.key, self.snapshot)['next_run_at'])
        self.assertEqual(len(self.store.deliveries(row['id'], 'owner')), 1)

    def test_real_process_exit_recovers_running_but_preserves_queue(self):
        queued_key = task_key('admin', 'owner', None, 'queued')
        queued = self.store.submit(queued_key, 'owner', {**self.snapshot, 'id': 'queued'})
        code = ('from app.execution.store import ExecutionStore; import sys,os,json; '
                's=ExecutionStore(sys.argv[1]); r=s.submit(sys.argv[2],"owner",json.loads(sys.argv[3])); '
                's.claim(r["id"]); os._exit(17)')
        result = subprocess.run([sys.executable, '-c', code, str(self.path), self.key, json.dumps(self.snapshot)])
        self.assertEqual(result.returncode, 17)
        self.assertEqual(self.store.recover(), 1)
        self.assertEqual(self.store.get(queued['id'])['status'], 'queued')
        self.assertTrue(self.store.overlay(self.key, {**self.snapshot, 'revision': 2})['recovery_required'])
        with self.assertRaises(Conflict):
            self.submit(manual=True)

    def test_recovery_fences_old_completion_and_effects(self):
        row = self.store.claim(self.submit()['id'])
        self.store.effect_start(row['id'], row['token'], 'op', {'kind': 'write'})
        self.store.recover()
        with self.assertRaises(Conflict):
            self.finish(row)
        with self.assertRaises(PermissionError):
            self.store.effect_done(row['id'], row['token'], 'op', {})
        self.assertEqual(self.store.get(row['id'])['status'], 'interrupted')

    def test_unreceipted_effect_blocks_next_execution(self):
        row = self.store.claim(self.submit()['id'])
        self.store.effect_start(row['id'], row['token'], 'write', {})
        self.finish(row)
        self.assertEqual(self.store.get(row['id'])['status'], 'interrupted')
        with self.assertRaises(Conflict):
            self.submit(manual=True)

    def test_cancellation_revokes_tools_and_only_finishes_after_ack(self):
        row = self.store.claim(self.submit()['id'])
        self.assertEqual(self.store.request_cancel(row['id'], 'owner')['status'], 'cancel_requested')
        with self.assertRaises(PermissionError):
            self.store.check_token(row['id'], row['token'])
        self.finish(row, deliveries=[{'channel': 'web', 'target': {}, 'payload': {}}])
        self.assertEqual(self.store.get(row['id'])['status'], 'cancelled')
        self.assertEqual(self.store.deliveries(row['id'], 'owner'), [])

    def test_delivery_restart_and_token_fencing(self):
        row = self.store.claim(self.submit()['id'])
        self.finish(row, deliveries=[{'channel': ch, 'target': {}, 'payload': {}} for ch in ['web','wechat']])
        web = self.store.claim_delivery()
        wechat = self.store.claim_delivery()
        self.store.recover()
        statuses = {d['channel']: d['status'] for d in self.store.deliveries(row['id'], 'owner')}
        self.assertEqual(statuses, {'web': 'retry_wait', 'wechat': 'unknown'})
        with self.assertRaises(Conflict):
            self.store.delivery_done(web, 'delivered')
        with self.assertRaises(Conflict):
            self.store.retry_delivery(wechat['id'], 'owner')
        again = self.store.claim_delivery()
        self.assertEqual(again['id'], web['id'])
        self.store.delivery_done(again, 'delivered')

    def test_spawn_quota_survives_reopen(self):
        self.store.reserve_spawn('owner', self.key, 1)
        other = ExecutionStore(self.path)
        self.addCleanup(other.close)
        with self.assertRaises(Conflict):
            other.reserve_spawn('owner', self.key, 1)

    def test_jsonl_dedup_and_torn_tail(self):
        path = str(Path(self.tmp.name, 'messages.jsonl'))
        append_jsonl(path, {'content': 'before'})
        with open(path, 'ab') as f:
            f.write(b'{"incomplete')
        self.assertTrue(append_jsonl_once(path, {'content': 'result'}, 'event'))
        self.assertFalse(append_jsonl_once(path, {'content': 'result'}, 'event'))
        self.assertEqual([r['content'] for r in read_jsonl(path)], ['before','result'])


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(security, 'USERS_DIR', self.tmp.name); p.start(); self.addCleanup(p.stop)
        p = patch('app.storage.local.USERS_DIR', self.tmp.name); p.start(); self.addCleanup(p.stop)
        p = patch.object(security, '_load_users', return_value={'owner': {'username':'fixture'}}); p.start(); self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'DISABLE_SCHEDULER': '0'}); p.start(); self.addCleanup(p.stop)
        st.invalidate_path_cache()
        sch._heap.clear(); sch._heap_index.clear()
        self.store = context.get_store()
        self.addCleanup(self.store.close)
        self.schedulers = []

    async def asyncTearDown(self):
        for scheduler in self.schedulers:
            await scheduler.stop()

    def make(self, **extra):
        return sch.create_task('owner', {'name': 'fixture', 'task_type': 'agent', 'schedule_type': 'interval',
            'schedule': '3600', 'task_config': {'prompt': 'test', 'permissions': {'read_dirs': ['docs'], 'write_dirs': ['generated']}}, **extra})

    def start(self):
        scheduler = sch.HeapScheduler(); scheduler.start(); self.schedulers.append(scheduler)
        return scheduler

    async def settle(self, scheduler):
        async with asyncio.timeout(3):
            while scheduler._running_tasks:
                await asyncio.sleep(.005)

    def bind(self, task):
        snapshot = {**task, 'execution_grant': capture(task, 'owner')}
        row = self.store.claim(self.store.submit(task_key('admin', 'owner', None, task['id']), 'owner', snapshot, manual=True)['id'])
        ctx = context.ExecutionContext(self.store, row)
        return ctx, Grant(ctx)

    async def test_export_failure_does_not_lose_result_or_repeat_once(self):
        task = self.make(schedule_type='once', schedule='now')
        scheduler = self.start()
        runner = AsyncMock(return_value={'output': 'durable output', 'success': True, 'steps': [{'type': 'finish'}]})
        with patch.object(sch, '_run_agent_task', runner), patch.object(sch, '_save_task', side_effect=OSError('export failed')):
            await scheduler._dispatch_due()
            await self.settle(scheduler)
        current = sch.get_task('owner', task['id'])
        self.assertIsNone(current['next_run_at'])
        self.assertEqual(sch.get_task_runs('owner', task['id'])[-1]['output'], 'durable output')
        scheduler._reload_from_disk()
        await scheduler._dispatch_due()
        runner.assert_awaited_once()

    async def test_queued_run_survives_scheduler_shutdown(self):
        task = self.make(); scheduler = self.start(); scheduler._exec_sem = asyncio.Semaphore(0)
        row = scheduler.submit_run('admin', 'owner', task['id'], request_id='queue')
        await asyncio.sleep(.01); await scheduler.stop()
        self.assertEqual(self.store.get(row['id'])['status'], 'queued')
        with patch.object(sch, '_run_agent_task', AsyncMock(return_value={'output':'done','success':True})) as runner:
            resumed = self.start(); await self.settle(resumed)
            runner.assert_awaited_once()
        self.assertEqual(self.store.get(row['id'])['status'], 'success')

    async def test_recovery_queue_drains_after_capacity_is_reduced(self):
        tasks=[self.make(),self.make()]
        rows=[self.store.submit(task_key('admin','owner',None,t['id']),'owner',t,manual=True) for t in tasks]
        with patch.dict(os.environ,{'SCHEDULER_MAX_PENDING':'1'}), patch.object(sch,'_run_agent_task',AsyncMock(return_value={'success':True,'output':'done'})) as runner:
            self.start()
            async with asyncio.timeout(3):
                while any(self.store.get(r['id'])['status'] in ('queued','running') for r in rows):
                    await asyncio.sleep(.005)
            self.assertEqual(runner.await_count,2)

    async def test_api_owner_isolation_idempotency_and_no_tokens(self):
        task = self.make(); scheduler = self.start(); scheduler._exec_sem = asyncio.Semaphore(0)
        with patch.object(routes, 'get_scheduler', return_value=scheduler):
            one = await routes.api_run_now(task['id'], {'user_id':'owner'}, 'same')
            two = await routes.api_run_now(task['id'], {'user_id':'owner'}, 'same')
            self.assertEqual(one['run_id'], two['run_id'])
            detail = await routes.api_run_detail(one['run_id'], {'user_id':'owner'})
            self.assertNotIn('token', detail); self.assertNotIn('snapshot', detail)
            with self.assertRaises(Exception) as error:
                await routes.api_run_detail(one['run_id'], {'user_id':'different'})
            self.assertEqual(error.exception.status_code, 404)
            cancelled = await routes.api_cancel_run(one['run_id'], {'user_id':'owner'})
            self.assertEqual(cancelled['status'], 'cancelled')

    async def test_review_clears_transient_flag_and_requires_explicit_reenable(self):
        task=self.make(); ctx, grant=self.bind(task)
        self.store.recover()
        self.assertTrue(sch.get_task('owner',task['id'])['recovery_required'])
        req=routes.RecoveryReview(confirmed_stopped_and_effects_reviewed=True,note='Synthetic executor exited; effects checked')
        await routes.api_resolve_recovery(ctx.run['id'],req,{'user_id':'owner'})
        current=sch.get_task('owner',task['id'])
        self.assertFalse(current.get('recovery_required',False))
        self.assertFalse(current['enabled'])
        self.assertIsNone(current['next_run_at'])

    async def test_grants_enforce_paths_current_revocation_and_owner(self):
        task = self.make(); ctx, grant = self.bind(task)
        self.assertEqual(grant.path('docs/a'), 'docs/a')
        self.assertEqual(grant.path('generated/a', write=True), 'generated/a')
        for path in ['../secret','docs/../secret','scripts/a','/generated/a']:
            with self.assertRaises(PermissionError): grant.path(path)
        root = Path(security.get_user_filesystem_dir('owner')); (root/'docs').mkdir(parents=True, exist_ok=True)
        (root/'secret').mkdir(exist_ok=True); (root/'docs'/'alias').symlink_to(root/'secret', target_is_directory=True)
        with self.assertRaises(PermissionError): grant.path('docs/alias/a')
        sch.update_task('owner', task['id'], {'task_config': {'permissions': {'read_dirs': [], 'write_dirs': []}}})
        with self.assertRaises(PermissionError): grant.path('docs/a')
        with patch.object(security, '_load_users', return_value={'owner': {'disabled':True}}):
            with self.assertRaises(PermissionError): grant.policies()

    async def test_service_reads_intersect_saved_and_current_publication(self):
        task = sch.create_service_task('owner', 'svc', {'name':'service', 'schedule_type':'interval','schedule':'3600','task_config':{}})
        with patch('app.services.published.get_service', return_value={'allowed_docs':['public']}):
            snapshot = {**task, 'execution_grant':capture(task,'owner','svc')}
            row = self.store.claim(self.store.submit(task_key('service','owner','svc',task['id']), 'owner',snapshot,manual=True)['id'])
            grant = Grant(context.ExecutionContext(self.store,row))
            self.assertEqual(grant.path('docs/public/a'), 'docs/public/a')
            with self.assertRaises(PermissionError): grant.path('docs/private/a')
        with patch('app.services.published.get_service', return_value={'allowed_docs':['private']}):
            for path in ['docs/public/a','docs/private/a']:
                with self.assertRaises(PermissionError): grant.path(path)

    async def test_tool_surface_and_writes_are_checked_with_effect_receipts(self):
        task = self.make(); ctx, grant = self.bind(task)
        tools = {t.name:t for t in build_tools(grant)}
        self.assertEqual(set(tools), {'read_file','ls','write_file','edit_file','send_message'})
        result = await tools['write_file'].coroutine('generated/result.txt', 'saved', SimpleNamespace(tool_call_id='write'))
        self.assertIn('Saved', result)
        self.assertEqual(Path(security.get_user_filesystem_dir('owner'),'generated/result.txt').read_text(), 'saved')
        with self.assertRaises(PermissionError):
            await tools['write_file'].coroutine('docs/denied.txt', 'denied', SimpleNamespace(tool_call_id='denied'))
        self.store.request_cancel(ctx.run['id'], 'owner')
        with self.assertRaises(PermissionError):
            await tools['read_file'].coroutine('docs/x')

    async def test_child_cannot_gain_permissions_or_native_executor(self):
        task = self.make(task_config={'capabilities':['scheduler'], 'permissions':{'read_dirs':['docs'],'write_dirs':['generated']}})
        ctx, grant = self.bind(task)
        child = grant.child({'task_type':'agent','task_config':{}})
        self.assertEqual(child['task_config']['permissions']['read_dirs'], ['docs'])
        for change in [{'task_type':'script'}, {'task_config':{'permissions':{'read_dirs':['*']}}}, {'task_config':{'capabilities':['web']}}]:
            with self.assertRaises(PermissionError): grant.child(change)

    async def test_scoped_agent_executes_real_tool_graph_without_live_model(self):
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from langgraph.checkpoint.memory import InMemorySaver
        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self
        model=ToolModel(responses=[
            AIMessage(content='',tool_calls=[{'name':'write_file','args':{'file_path':'generated/result.txt','content':'graph write'},'id':'w1'}]),
            AIMessage(content='',tool_calls=[{'name':'send_message','args':{'message':'completed'},'id':'m1'}]),
            AIMessage(content='finished'),
        ])
        task=self.make(task_config={'prompt':'fixture','model':'fake','permissions':{'read_dirs':['docs'],'write_dirs':['generated']}})
        ctx, grant=self.bind(task)
        token=context._current.set(ctx)
        try:
            with patch('app.services.agent._resolve_model',return_value=model), patch('app.services.agent._checkpointer',InMemorySaver()), patch.object(sch,'build_usage_callbacks',return_value=[]):
                result=await sch._run_agent_task('owner',task['task_config'])
            self.assertTrue(result['success'],result)
            self.assertEqual(result['output'],'completed')
            self.assertEqual(ctx.intents,['completed'])
            self.assertEqual(Path(security.get_user_filesystem_dir('owner'),'generated/result.txt').read_text(),'graph write')
        finally:
            context._current.reset(token)

    async def test_native_script_fails_closed_without_claiming_sandbox(self):
        task = self.make(task_type='script', task_config={'script_path':'x.py'})
        scheduler = self.start(); row = scheduler.submit_run('admin','owner',task['id'])
        await self.settle(scheduler)
        self.assertEqual(self.store.get(row['id'])['status'], 'blocked')
        self.assertIn('OS-isolated', self.store.get(row['id'])['result']['output'])
        self.assertFalse(sch.get_task('owner',task['id'])['enabled'])

    async def test_web_delivery_is_idempotent_after_unacknowledged_append(self):
        from app.services.conversations import save_message, get_conversation
        save_message('owner','conv','user','hello')
        task = self.make(reply_to={'channel':'web','conversation_id':'conv'})
        ctx, grant = self.bind(task)
        record={'status':'success','output':'result'}
        self.store.finish(ctx.run['id'],ctx.run['token'],record,deliveries=ctx.deliveries(record,{}))
        d = self.store.claim_delivery()
        # Simulate append succeeded, process exited before the outbox ack.
        save_message('owner','conv','assistant','result',event_id=d['id'])
        self.store.recover(); d=self.store.claim_delivery()
        await deliver_one(self.store,d)
        messages=get_conversation('owner','conv')['messages']
        self.assertEqual(len([m for m in messages if m.get('event_id')==d['id']]),1)
        self.assertEqual(next(d for d in self.store.deliveries(ctx.run['id'],'owner') if d['channel']=='web')['status'], 'delivered')

    async def test_ambiguous_wechat_send_is_unknown_and_not_retried(self):
        from app.services.conversations import save_message
        save_message('owner','conv','user','hello')
        task=self.make(reply_to={'channel':'wechat','conversation_id':'conv'})
        ctx, grant=self.bind(task)
        record={'status':'success','output':'result'}
        ds=[d for d in ctx.deliveries(record,{}) if d['channel']=='wechat']
        self.store.finish(ctx.run['id'],ctx.run['token'],record,deliveries=ds)
        d=self.store.claim_delivery(); client=SimpleNamespace(send_text=AsyncMock(side_effect=ConnectionError('lost ack')))
        with patch.object(sch,'_resolve_wechat_client',return_value=(client,'target','token')):
            await deliver_one(self.store,d)
        self.assertEqual(self.store.deliveries(ctx.run['id'],'owner')[0]['status'],'unknown')
        self.assertIsNone(self.store.claim_delivery())
        self.assertEqual(self.store.get(ctx.run['id'])['status'],'success')

    async def test_memory_projection_replay_is_idempotent_and_busy_is_backpressure(self):
        from langgraph.graph.message import add_messages
        messages=[]
        async def update(config, update):
            nonlocal messages
            messages=add_messages(messages,update['messages'])
        agent=SimpleNamespace(aget_state=AsyncMock(side_effect=lambda _:SimpleNamespace(values={'messages':messages})),
                              aupdate_state=AsyncMock(side_effect=update))
        factory=AsyncMock(return_value=agent)
        for seq in range(1,9):
            item=inj._build_item({'run_id':f'run_{seq}','projection_seq':seq},f'result {seq}',True,None,factory)
            await inj._inject_batch('thread',[item],strict=True)
        before=len(messages)
        old=inj._build_item({'run_id':'run_1','projection_seq':1},'old',True,None,factory)
        await inj._inject_batch('thread',[old],strict=True)
        self.assertEqual(len(messages),before)
        self.assertEqual(len(inj._scan_existing_pairs(messages)),5)
        self.assertFalse(any(getattr(m,'id','')=='sched_inj_tool_run_1' for m in messages))
        inj._lock=asyncio.Lock(); inj._gate=asyncio.Condition(inj._lock)
        inj._active_refcount['owner-conv']=1
        try:
            with self.assertRaises(BlockingIOError):
                await inj.project_delivery({'admin_id':'owner','conversation_id':'conv'},
                    {'task_meta':{'run_id':'run_9'},'text':'busy','success':True},9)
        finally:
            inj._active_refcount.pop('owner-conv',None)

