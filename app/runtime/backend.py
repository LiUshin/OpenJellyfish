"""Execution placement and bounded warm clients, separate from chat identity."""
import asyncio
import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class BackendCapabilities:
    isolation: str
    persistent_state: bool
    materialize_files: bool
    network_approval: bool


class ExecutionBackend(Protocol):
    capabilities: BackendCapabilities
    def workspace(self, session: dict) -> Path: ...
    def execution(self, session: dict): ...


class LocalBackend:
    capabilities = BackendCapabilities('trusted_team_only', True, True, False)

    def __init__(self, root, credentials, executable, adapter_factory=None, *, max_idle=2, idle_seconds=120):
        self.root, self.credentials = Path(root), credentials
        self.executable, self.adapter_factory = executable, adapter_factory
        self.max_idle, self.idle_seconds = max_idle, idle_seconds
        self.idle = {}
        self.guard = asyncio.Lock()

    def session_dir(self, session):
        return self.root / 'actors' / session['actor_id'] / session['id']

    def workspace(self, session):
        return self.session_dir(session) / 'workspace'

    @staticmethod
    def identity(session):
        # Model is a turn parameter. Every authorization/workspace field remains
        # part of the reuse key; an actor can never inherit another actor's client.
        return (session['actor_id'], session['id'],
                {k: v for k, v in session['binding'].items() if k != 'model'},
                session.get('instructions'), session.get('dynamic_tools'))

    @staticmethod
    def healthy(entry):
        rpc = getattr(entry['adapter'], 'rpc', None)
        process = getattr(rpc, 'process', None)
        return not getattr(rpc, 'failure', None) and (process is None or process.returncode is None)

    async def _close(self, entry):
        adapter = entry['adapter']
        adapter.tool_call = None
        try:
            await self.credentials.close_adapter(entry['session']['binding']['profile_id'], adapter)
        finally:
            # Keep the credential lock until the complete process group exits.
            # Uncertain cleanup fences the profile in close_adapter/lease.
            await entry['lease'].__aexit__(None, None, None)

    async def release(self, *, profile_id=None, actor_id=None, session_id=None):
        async with self.guard:
            for pid, entry in list(self.idle.items()):
                s = entry['session']
                if (profile_id is None or pid == profile_id) and (actor_id is None or s['actor_id'] == actor_id) and (session_id is None or s['id'] == session_id):
                    self.idle.pop(pid)
                    await self._close(entry)

    async def reap(self, authorize):
        async with self.guard:
            for pid, entry in list(self.idle.items()):
                expired = time.monotonic() - entry['idle_since'] >= self.idle_seconds or not self.healthy(entry)
                try:
                    authorize(entry['session']['actor_id'], entry['session']['binding'])
                except Exception:
                    expired = True
                if expired:
                    self.idle.pop(pid)
                    # Profile cleanup errors are persisted by the credential
                    # manager; one failed account must not stop the dispatcher.
                    with contextlib.suppress(Exception):
                        await self._close(entry)

    async def shutdown(self):
        # Attempt every cleanup even if one account requires recovery.
        async with self.guard:
            entries, self.idle = list(self.idle.values()), {}
            results = await asyncio.gather(*(self._close(e) for e in entries), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    @contextlib.asynccontextmanager
    async def execution(self, session):
        pid = session['binding']['profile_id']
        async with self.guard:
            entry = self.idle.pop(pid, None)
            if entry and (entry['identity'] != self.identity(session) or not self.healthy(entry)
                          or time.monotonic() - entry['idle_since'] >= self.idle_seconds):
                await self._close(entry)
                entry = None
        reused = entry is not None
        if entry is None:
            directory = self.session_dir(session)
            workspace, home = directory / 'workspace', directory / 'home'
            # Warm conversations retain their native history when prewarming is disabled.
            if session.get('connection_history'):
                home = self.root / 'connections' / pid / str(session['binding']['auth_generation']) / 'home'
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
            lease = self.credentials.lease(session['binding'], home)
            await lease.__aenter__()
            try:
                if self.adapter_factory:
                    adapter = self.adapter_factory(self.executable, home, workspace, session['binding']['model'],
                                                   managed=True, dynamic_tools=session.get('dynamic_tools', []))
                else:
                    adapter = self.credentials.provider(session['binding']).adapter(
                        home, workspace, session['binding']['model'], session.get('dynamic_tools', []))
            except BaseException:
                await lease.__aexit__(None, None, None)
                raise
            entry = {'adapter': adapter, 'lease': lease, 'identity': self.identity(session)}
        entry['session'] = session
        adapter = entry['adapter']
        adapter.model, adapter.reused, adapter.reusable = session['binding']['model'], reused, False
        ok = False
        try:
            yield adapter
            ok = adapter.reusable
        finally:
            adapter.tool_call = None
            cached = False
            try:
                if ok and self.max_idle > 0 and self.healthy(entry):
                    async with self.guard:
                        while len(self.idle) >= self.max_idle:
                            oldest = min(self.idle, key=lambda k: self.idle[k]['idle_since'])
                            await self._close(self.idle.pop(oldest))
                        entry['idle_since'] = time.monotonic()
                        self.idle[pid] = entry
                        cached = True
            finally:
                if not cached:
                    await self._close(entry)
