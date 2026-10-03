"""Persistent rolling-hour spawn quota, shared by scheduler tools and the UI.

Admission reserves a slot before creating a child. Failed writes conservatively
consume that slot; neither a restart nor resetting an in-memory cache refunds it.
"""
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional
from app.execution.context import get_store
from app.execution.store import task_key

log = logging.getLogger('spawn_limits')
WINDOW_SECONDS = 3600

def _env_rate() -> int:
    """Read the per-hour limit from env on every call.

    Re-reading lets ops change ``SCHED_SPAWN_RATE_PER_HOUR`` without a process
    restart by ``export``-ing then issuing a follow-up Agent message.  Cheap
    enough — single int parse — and prevents silent drift.
    """
    raw = os.environ.get("SCHED_SPAWN_RATE_PER_HOUR", "30")
    try:
        n = int(raw)
        return max(1, n)  # always at least 1; 0 would brick spawning entirely
    except ValueError:
        log.warning("Invalid SCHED_SPAWN_RATE_PER_HOUR=%r — falling back to 30",
                    raw)
        return 30


@dataclass
class QuotaResult:
    allowed: bool
    current: int           # spawn count in window AFTER this attempt's registration
    limit: int
    remaining: int
    window_seconds: int
    reset_at: str          # ISO timestamp when oldest entry will expire

    def as_dict(self) -> dict:
        return asdict(self)



def _quota(scope, uid, root_task_id, service_id, reserve):
    limit = _env_rate()
    result = get_store().spawn_budget(uid, task_key(scope, uid, service_id, root_task_id), limit, reserve=reserve)
    return QuotaResult(allowed=result['allowed'], current=result['current'], limit=limit,
        remaining=max(0, limit-result['current']), window_seconds=WINDOW_SECONDS,
        reset_at=datetime.fromtimestamp(result['reset_at']).isoformat())


def check_chain_quota(scope: str, uid: str, root_task_id: str, service_id: Optional[str] = None) -> QuotaResult:
    return _quota(scope, uid, root_task_id, service_id, True)


def peek_chain_quota(scope: str, uid: str, root_task_id: str, service_id: Optional[str] = None) -> QuotaResult:
    return _quota(scope, uid, root_task_id, service_id, False)


def get_user_chain_stats(scope: str, uid: str, service_id: Optional[str] = None) -> list[dict]:
    result = []
    for s, owner, sid, root in get_store().spawn_chains(uid):
        if s != scope or (service_id and service_id != sid):
            continue
        quota = peek_chain_quota(s, owner, root, sid)
        result.append({'scope': s, 'root_task_id': root, 'service_id': sid or '',
            'used': quota.current, 'limit': quota.limit, 'remaining': quota.remaining,
            'reset_at': quota.reset_at, 'window_seconds': WINDOW_SECONDS})
    return sorted(result, key=lambda value: value['root_task_id'])


def reset_chain(scope: str, uid: str, root_task_id: str, service_id: Optional[str] = None) -> bool:
    """Legacy cache reset: deliberately cannot erase durable reservations."""
    return False


def reset_all() -> None:
    """Legacy cache reset; tests should use a temporary USERS_DIR for isolation."""
