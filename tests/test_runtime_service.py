import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from fastapi import HTTPException
from app.runtime.policy import DeploymentPolicy
from app.runtime.store import RuntimeStore, TERMINAL
from app.runtime.service import RunService
from app.runtime.types import RuntimeEvent


class FakeAdapter:
    def __init__(self, gate):
        self.gate = gate
        self.closed = False
        self.responses = []
    async def open_session(self, *args):
        return 'thread'
    async def stream_turn(self, thread_id, text):
        yield RuntimeEvent('text_delta', {'text': 'first'})
        await self.gate.wait()
        if text in ('approval', 'approval_cancel'):
            yield RuntimeEvent('request', {'request_id': 7, 'method': 'item/commandExecution/requestApproval',
                                           'params': {'command': 'echo hello', 'availableDecisions': ['accept','cancel'] if text == 'approval_cancel' else None}})
        yield RuntimeEvent('completed', {})
    async def respond(self, key, result):
        self.responses.append(result)
    async def cancel(self):
        pass
    async def close(self):
        self.closed = True


class FakeBackend:
    def __init__(self, root):
        self.root = root
        self.gate = asyncio.Event()
        self.adapters = []
    def workspace(self, session):
        return self.root / session['id']
    @contextlib.asynccontextmanager
    async def execution(self, session):
        adapter = FakeAdapter(self.gate)
        self.adapters.append(adapter)
        try:
            yield adapter
        finally:
            await adapter.close()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RuntimeStore(Path(self.tmp.name))
        self.backend = FakeBackend(Path(self.tmp.name) / 'work')
        self.revoked = set()
        def authorize(actor, binding):
            if actor in self.revoked:
                raise HTTPException(403, 'revoked')
        self.service = RunService(self.store, DeploymentPolicy(max_queued=4, max_queued_per_actor=2), self.backend, authorize)
        self.binding = {'profile_id': 'p', 'model': 'test', 'auth_generation': 1}
    async def asyncTearDown(self):
        await self.service.shutdown()
        self.store.close()
        self.tmp.cleanup()
    def session(self, actor='a'):
        return self.service.create_session(actor, self.binding)
    async def until(self, predicate):
        for _ in range(150):
            if predicate():
                return
            await asyncio.sleep(.01)
        self.fail('timed out')
    async def test_idempotency_replay_and_browser_independence(self):
        s = self.session()
        run = self.service.enqueue('a', s['id'], 'once', 'hello')
        self.assertEqual(run['id'], self.service.enqueue('a', s['id'], 'once', 'hello')['id'])
        with self.assertRaises(HTTPException):
            self.service.enqueue('a', s['id'], 'once', 'different')
        await self.until(lambda: self.store.get('run', run['id'])['output'] == 'first')
        self.backend.gate.set()
        await self.until(lambda: self.store.get('run', run['id'])['status'] == 'completed')
        events = self.store.events(run['id'])
        self.assertEqual([e['seq'] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(self.store.events(run['id'], events[-2]['seq'])[-1]['type'], 'completed')
        self.assertTrue(self.backend.adapters[0].closed)
    async def test_profile_serialization_and_global_capacity(self):
        runs = [self.service.enqueue(a, self.session(a)['id'], 'r', 'hello') for a in ['a','b']]
        await self.until(lambda: len(self.service.active) == 1)
        self.assertEqual(sum(r['status'] == 'queued' for r in self.store.all('run')), 1)
        self.backend.gate.set()
        await self.until(lambda: all(r['status'] == 'completed' for r in self.store.all('run')))
        self.assertEqual(len(self.backend.adapters), 2)
    async def test_actor_bound_approval_revoke_and_process_exit(self):
        self.backend.gate.set()
        s = self.session()
        run = self.service.enqueue('a', s['id'], 'r', 'approval')
        await self.until(lambda: self.store.get('run', run['id'])['status'] == 'waiting_approval')
        pending = self.store.get('run', run['id'])['pending']
        with self.assertRaises(HTTPException) as denial:
            self.service.approve('b', run['id'], pending['id'], 'accept')
        self.assertEqual(denial.exception.status_code, 404)
        self.revoked.add('a')
        with self.assertRaises(HTTPException):
            self.service.approve('a', run['id'], pending['id'], 'accept')
        await self.service.cancel_profile('p', 'a')
        self.assertTrue(self.backend.adapters[0].closed)
        self.assertEqual(self.store.get('run', run['id'])['status'], 'cancelled')
    async def test_queue_is_bounded_and_cancelled_before_start(self):
        s = self.session()
        run = self.service.enqueue('a', s['id'], '1', 'hello')
        await self.until(lambda: len(self.service.active) == 1)
        for i in (2, 3):
            self.service.enqueue('a', self.session()['id'], str(i), 'hello')
        with self.assertRaises(HTTPException) as full:
            self.service.enqueue('a', self.session()['id'], '4', 'hello')
        self.assertEqual(full.exception.status_code, 429)
        await self.service.cancel_profile('p')
        self.assertTrue(all(r['status'] == 'cancelled' for r in self.store.all('run')))
    async def test_single_process_and_restart_fence(self):
        with self.assertRaises(RuntimeError):
            RuntimeStore(Path(self.tmp.name))
        self.store.put('profile', {'id': 'p'})
        self.store.put('run', {'id': 'crashed', 'actor_id': 'a', 'status': 'running', 'binding': self.binding})
        self.store.close()
        self.store = RuntimeStore(Path(self.tmp.name))
        self.service.store = self.store
        self.assertEqual(self.store.get('run', 'crashed')['status'], 'failed')
        self.assertIn('服务重启中断', self.store.get('run', 'crashed')['error'])
        self.assertTrue(self.store.get('profile', 'p')['recovery_required'])

    async def test_vendor_cancel_remains_available_as_decline(self):
        self.backend.gate.set()
        run = self.service.enqueue('a', self.session()['id'], 'r', 'approval_cancel')
        await self.until(lambda: self.store.get('run',run['id'])['status'] == 'waiting_approval')
        pending = self.store.get('run',run['id'])['pending']
        self.assertIn('decline',pending['allowed'])
        self.service.approve('a',run['id'],pending['id'],'decline')
        await self.until(lambda: self.store.get('run',run['id'])['status'] in TERMINAL)
        self.assertEqual(self.backend.adapters[0].responses[-1], {'decision':'cancel'})

    async def test_distinct_profiles_obey_global_limit(self):
        for i in range(3):
            actor = f'actor-{i}'
            session = self.service.create_session(actor, {**self.binding,'profile_id':f'profile-{i}'})
            self.service.enqueue(actor, session['id'], f'request-{i}', 'hello')
        await self.until(lambda: len(self.service.active) == 2)
        self.assertEqual(len(self.store.find('run',status='queued')), 1)
        self.backend.gate.set()
        await self.until(lambda: all(r['status'] == 'completed' for r in self.store.all('run')))

    async def test_cancelled_request_cannot_interrupt_execution_cleanup(self):
        from unittest.mock import patch
        entered, release = asyncio.Event(), asyncio.Event()
        original = FakeAdapter.close
        async def delayed_close(adapter):
            entered.set()
            await release.wait()
            await original(adapter)
        with patch.object(FakeAdapter, 'close', delayed_close):
            run = self.service.enqueue('a', self.session()['id'], 'request-cleanup', 'hello')
            await self.until(lambda: bool(self.backend.adapters))
            caller = asyncio.create_task(self.service.cancel('a', run['id']))
            await asyncio.wait_for(entered.wait(), 2)
            caller.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await caller
            self.assertIn(run['id'], self.service.active)
            self.assertFalse(self.backend.adapters[0].closed)
            release.set()
            await self.until(lambda: self.store.get('run',run['id'])['status'] == 'cancelled')
            self.assertTrue(self.backend.adapters[0].closed)

if __name__ == '__main__':
    unittest.main()
