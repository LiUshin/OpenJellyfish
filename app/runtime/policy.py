"""Keep trust policy separate from execution placement. Unsupported fails closed."""
import os
import sys
from dataclasses import dataclass

from fastapi import HTTPException
from app.core.roles import is_owner


def enabled() -> bool:
    return os.getenv('JELLYFISH_RUNTIME_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on')


def bounded_env(name: str, default: int, maximum: int) -> int:
    try:
        return max(1, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


@dataclass(frozen=True)
class DeploymentPolicy:
    access_mode: str = 'trusted_shared'
    execution_backend: str = 'local'
    max_running: int = 2
    max_queued: int = 32
    max_queued_per_actor: int = 4
    run_timeout: int = 1800
    approval_timeout: int = 300

    @classmethod
    def from_env(cls):
        return cls(
            access_mode=os.getenv('JELLYFISH_RUNTIME_ACCESS_MODE', 'trusted_shared'),
            execution_backend=os.getenv('JELLYFISH_RUNTIME_BACKEND', 'local'),
            max_running=bounded_env('JELLYFISH_RUNTIME_MAX_RUNNING', 2, 32),
            max_queued=bounded_env('JELLYFISH_RUNTIME_MAX_QUEUED', 32, 1000),
            max_queued_per_actor=bounded_env('JELLYFISH_RUNTIME_QUEUE_PER_ADMIN', 4, 100),
            run_timeout=bounded_env('JELLYFISH_RUNTIME_RUN_TIMEOUT', 1800, 7200),
            approval_timeout=bounded_env('JELLYFISH_RUNTIME_APPROVAL_TIMEOUT', 300, 1800),
        )

    def ensure_supported(self):
        if sys.platform not in ('darwin', 'linux'):
            raise HTTPException(503, '当前本机执行后端仅支持 macOS / Linux；DeepAgents 不受此限制')
        if self.access_mode != 'trusted_shared' or self.execution_backend != 'local':
            raise HTTPException(503, '此执行后端尚未通过隔离验收，不能启用；不会回退到本机执行')

    def require_connection_owner(self, actor_id: str):
        self.ensure_supported()
        if not is_owner(actor_id):
            raise HTTPException(403, '标准模式由超管管理连接；admin 使用获授权的团队连接')

    def public(self, actor_id: str):
        reason = None
        try:
            self.ensure_supported()
        except HTTPException as exc:
            reason = exc.detail
        return {
            'enabled': enabled(), 'access_mode': self.access_mode,
            'execution_backend': self.execution_backend,
            'available': enabled() and reason is None,
            'reason': reason if enabled() else '外部引擎尚未在部署端启用',
            'can_manage_connections': enabled() and reason is None and is_owner(actor_id),
            'isolation': 'trusted_team_only', 'max_running': self.max_running,
            'runtimes': ['deepagents', 'codex', 'cursor'],
        }
