"""Bounded, connection-owned native clients for the trusted shared deployment."""
import asyncio
import contextlib
import time
from pathlib import Path

from app.runtime.backend import LocalBackend
from app.runtime.rpc import RuntimeFailure
from app.runtime.business_tools import specifications


class ConnectionBackend(LocalBackend):
    def __init__(self, root, credentials, executable, *, max_clients=2, adapter_factory=None):
        super().__init__(root, credentials, executable, adapter_factory)
        self.max_clients = max_clients
        self.clients, self.states, self.retry = {}, {}, {}
        self.paused = set()
        self.closed = False
        self.changed = asyncio.Event()

    def public_state(self, pid):
        state = self.states.get(pid, {'status': 'sleeping'})
        return {k: v for k, v in state.items() if k in ('status', 'ready_at', 'error')}

    def connection_home(self, profile):
        return self.root / 'connections' / profile['id'] / str(profile['auth_generation']) / 'home'

    def _binding(self, p):
        if not p.get('models'):
            raise RuntimeFailure('连接未返回可用模型')
        return self.credentials.binding(p['credential_owner_id'], p['id'], p['models'][0]['id'])

    @staticmethod
    def client_policy(profile, scope=None):
        # Cursor reads global permissions when ACP starts; project config is
        # deliberately disabled. Never lend an admin-mode client to a Service.
        if profile.get('runtime') != 'cursor' or not scope:
            return None
        return (True, bool(scope.get('web')), bool(scope.get('image')))

    async def _build(self, entry):
        pid = entry['profile']['id']
        lease = adapter = None
        try:
            p = self.credentials.get(pid)
            binding = self._binding(p)
            home = self.connection_home(p)
            neutral = home.parent / 'bootstrap'
            neutral.mkdir(parents=True, exist_ok=True, mode=0o700)
            lease = self.credentials.lease(binding, home)
            await lease.__aenter__()
            entry['lease'] = lease
            if self.adapter_factory:
                adapter = self.adapter_factory(self.executable, home, neutral, binding['model'], managed=True, dynamic_tools=specifications())
            else:
                adapter = self.credentials.provider(p).adapter(home, neutral, binding['model'], specifications())
            adapter.service_scope = entry.get('service_scope')
            entry['adapter'] = adapter
            async with asyncio.timeout(120):
                capabilities = await adapter.warm_up()
            current = self.credentials.get(pid)
            if current['auth_generation'] != p['auth_generation'] or current['status'] != 'ready':
                raise RuntimeFailure('预热期间连接状态改变')
            current['capabilities'] = capabilities
            self.credentials.store.put('profile', current)
            entry['ready'] = True
            self.retry.pop(pid, None)
            self.states[pid] = {'status': 'ready', 'ready_at': time.time()}
        except BaseException as exc:
            try:
                if adapter is not None:
                    await self.credentials.close_adapter(pid, adapter)
            finally:
                if lease is not None and entry.pop('lease', None):
                    await lease.__aexit__(None, None, None)
                entry['adapter'] = None
            attempts = self.retry.get(pid, (0, 0))[0] + 1
            self.retry[pid] = (attempts, time.monotonic() + min(60, 2 ** min(attempts, 6)))
            self.states[pid] = {'status': 'sleeping' if isinstance(exc, asyncio.CancelledError) else 'error',
                                'error': '客户端预热未完成，将自动重试' if not isinstance(exc, asyncio.CancelledError) else None}
            raise
        finally:
            self.changed.set()

    async def _drop_locked(self, pid):
        entry = self.clients.pop(pid, None)
        if not entry:
            return
        task = entry['task']
        if not task.done():
            task.cancel()
        with contextlib.suppress(BaseException):
            await task
        adapter = entry.get('adapter')
        if adapter:
            adapter.tool_call = None
            try:
                await self.credentials.close_adapter(pid, adapter)
            finally:
                await entry['lease'].__aexit__(None, None, None)
        if self.states.get(pid, {}).get('status') != 'error':
            self.states[pid] = {'status': 'sleeping'}
        self.changed.set()

    def _launch_locked(self, p, scope=None):
        entry = {'profile': p, 'busy': False, 'ready': False, 'last_used': time.monotonic(), 'session': None, 'adapter': None}
        entry['service_scope'] = scope
        entry['client_policy'] = self.client_policy(p, scope)
        self.clients[p['id']] = entry
        self.states[p['id']] = {'status': 'warming'}
        entry['task'] = asyncio.create_task(self._build(entry))
        # _ensure and reap handle errors; always consume background exceptions.
        entry['task'].add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return entry

    async def _ensure(self, pid, scope=None):
        while not self.closed:
            self.changed.clear()
            async with self.guard:
                if pid not in self.paused:
                    p = self.credentials.get(pid)
                    self._binding(p)
                    entry = self.clients.get(pid)
                    if entry and not entry['busy'] and entry.get('client_policy') != self.client_policy(p, scope):
                        await self._drop_locked(pid)
                        entry = None
                    if entry and not entry['busy'] and entry['task'].done() and (not entry['ready'] or not self.healthy(entry) or entry['profile']['auth_generation'] != p['auth_generation']):
                        await self._drop_locked(pid)
                        entry = None
                    if entry is None:
                        if len(self.clients) >= self.max_clients:
                            idle = [e for e in self.clients.values() if not e['busy']]
                            if idle:
                                victim = min(idle, key=lambda e: e['last_used'])['profile']['id']
                                await self._drop_locked(victim)
                        if len(self.clients) < self.max_clients:
                            entry = self._launch_locked(p, scope)
                    if entry and not entry['busy']:
                        # Reserve before awaiting warmup. The scheduler still
                        # serializes per account; the backend enforces it too.
                        entry['busy'] = True
                        break
            await self.changed.wait()
        else:
            raise RuntimeFailure('运行服务正在停止')
        try:
            await asyncio.shield(entry['task'])
            return entry
        except BaseException:
            entry['busy'] = False
            self.changed.set()
            raise

    async def reap(self, authorize):
        if self.closed:
            return
        async with self.guard:
            for pid, entry in list(self.clients.items()):
                if entry['busy']:
                    continue
                try:
                    p = self.credentials.get(pid)
                    self._binding(p)
                    invalid = p['auth_generation'] != entry['profile']['auth_generation']
                except Exception:
                    invalid = True
                if entry['task'].done() and (not entry['ready'] or not self.healthy(entry)):
                    invalid = True
                if invalid:
                    with contextlib.suppress(Exception):
                        await self._drop_locked(pid)
            # Fill vacant slots only. Do not churn an LRU cache when configured
            # connections exceed the process budget.
            for p in self.credentials.store.all('profile'):
                pid = p['id']
                if len(self.clients) >= self.max_clients:
                    break
                if pid in self.clients or pid in self.paused or self.retry.get(pid, (0, 0))[1] > time.monotonic():
                    continue
                try:
                    self._binding(p)
                except Exception:
                    continue
                self._launch_locked(p)

    async def pause(self, pid):
        self.paused.add(pid)
        await self.release(profile_id=pid)

    def resume(self, pid):
        self.paused.discard(pid)
        self.changed.set()

    async def release(self, *, profile_id=None, actor_id=None, session_id=None):
        async with self.guard:
            for pid, entry in list(self.clients.items()):
                s = entry.get('session') or {}
                if (profile_id is None or profile_id == pid) and (actor_id is None or actor_id == s.get('actor_id')) and (session_id is None or session_id == s.get('id')):
                    if entry['busy']:
                        raise RuntimeFailure('连接仍在执行，请先停止任务')
                    await self._drop_locked(pid)

    @contextlib.asynccontextmanager
    async def execution(self, session):
        pid = session['binding']['profile_id']
        prior = self.clients.get(pid)
        was_ready = self.public_state(pid)['status'] == 'ready'
        entry = await self._ensure(pid, session['binding'].get('service_scope'))
        adapter = entry['adapter']
        ok = False
        try:
            entry['session'] = session
            adapter.model, adapter.reused, adapter.reusable = session['binding']['model'], was_ready and entry is prior, False
            adapter.workspace = self.workspace(session)
            adapter.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            adapter.session_key = session['id']
            adapter.native_capabilities = self.credentials.get(pid).get('capabilities', {})
            self.states[pid] = {'status': 'busy'}
            await adapter.prepare_history(session, self.session_dir(session) / 'home')
            yield adapter
            ok = adapter.reusable
        finally:
            adapter.tool_call = None
            adapter.input_files = []
            entry['busy'] = False
            entry['last_used'] = time.monotonic()
            async with self.guard:
                if not ok or not self.healthy(entry) or getattr(adapter, 'session_count', 0) >= 32:
                    await self._drop_locked(pid)
                else:
                    self.states[pid] = {'status': 'ready', 'ready_at': time.time()}
            self.changed.set()

    async def shutdown(self):
        self.closed = True
        self.changed.set()
        async with self.guard:
            failures = []
            for pid in list(self.clients):
                try:
                    await self._drop_locked(pid)
                except Exception as exc:
                    failures.append(exc)
            if failures:
                raise failures[0]
