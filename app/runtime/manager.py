"""One opt-in runtime coordinator in the existing API process."""
import os
import shutil
from pathlib import Path
from fastapi import HTTPException
from app.core.settings import ROOT_DIR
from app.runtime.backend import LocalBackend
from app.runtime.connection_backend import ConnectionBackend
from app.runtime.policy import DeploymentPolicy, enabled, bounded_env
from app.runtime.profiles import ProfileManager
from app.runtime.providers import CodexProvider, CursorProvider
from app.runtime.service import RunService
from app.runtime.store import RuntimeStore


class RuntimeManager:
    def __init__(self):
        self.policy = DeploymentPolicy.from_env()
        self.policy.ensure_supported()
        root = Path(os.getenv('JELLYFISH_RUNTIME_DATA_DIR', str(Path(ROOT_DIR) / 'data' / 'runtime'))).resolve()
        executable = os.getenv('JELLYFISH_RUNTIME_CODEX_BIN') or shutil.which('codex') or 'codex'
        self.store = RuntimeStore(root)
        cursor = os.getenv('JELLYFISH_RUNTIME_CURSOR_BIN') or shutil.which('cursor-agent') or shutil.which('agent') or 'cursor-agent'
        self.profiles = ProfileManager(self.store, self.policy, executable, providers={
            'codex': CodexProvider(executable), 'cursor': CursorProvider(cursor),
        })
        self.backend = (LocalBackend(root, self.profiles, executable, max_idle=0)
            if os.getenv('JELLYFISH_RUNTIME_KEEP_WARM', '1') == '0' else
            ConnectionBackend(root, self.profiles, executable,
                max_clients=bounded_env('JELLYFISH_RUNTIME_MAX_CLIENTS', self.policy.max_running, 16)))
        from app.storage import get_storage_service
        from app.runtime.consumer import service_authorizer
        self.runs = RunService(self.store, self.policy, self.backend, service_authorizer(self.profiles),
                               storage=get_storage_service())
        self.profiles.runs = self.runs
        from app.runtime.business_tools import BusinessTools
        self.runs.tool_bridge = BusinessTools(self.runs.storage, self.runs.authorize, self.store)

    async def shutdown(self):
        await self.profiles.shutdown()
        await self.runs.shutdown()
        self.store.close()


_manager = None


def get_runtime():
    global _manager
    if not enabled():
        raise HTTPException(404, '外部运行引擎未启用')
    if _manager is None:
        _manager = RuntimeManager()
    return _manager
