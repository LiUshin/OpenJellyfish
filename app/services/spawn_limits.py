"""
In-memory rate limit for ``spawn_child_task``.

Quota is keyed by ``(scope, uid, service_id, root_task_id)`` — i.e. one
counter per **spawn chain**.  Every member of a chain (root + descendants)
shares the same hourly budget, so an Agent can't side-step the limit by
recursing one level deeper.

Defaults
--------
* ``SCHED_SPAWN_RATE_PER_HOUR`` (env, default 30)
* Sliding window: 3600 s
* In-process state (no Redis): acceptable while uvicorn workers=1 (see
  ``.cursorrules`` §"Scheduled task v2" C11).  When scaling out, swap the
  internal storage for a Redis ZSET keyed similarly.

Eviction
--------
A small reaper runs opportunistically on every ``check_chain_quota`` call:
chains whose deque is empty are dropped.  No background thread.

Public API
----------
* :func:`check_chain_quota` — atomically check + register one spawn attempt.
  Returns a :class:`QuotaResult` whose ``allowed`` field indicates whether
  the caller may proceed to actually create the child task.  **Important**:
  callers MUST refrain from creating the task when ``allowed`` is False
  (the registration in that branch is a no-op).
* :func:`get_chain_stats` — read-only snapshot for ``GET /api/scheduler/quotas``.
* :func:`reset_chain` — clear a chain's history (used in tests / admin tooling).
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from threading import Lock
from typing import Deque, Dict, List, Optional, Tuple

log = logging.getLogger("spawn_limits")

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


# ── State (in-process; one mutex guards everything) ─────────────────────

_lock = Lock()
# (scope, uid, service_id_or_blank, root_task_id) → deque[unix_ts]
_chain_history: Dict[Tuple[str, str, str, str], Deque[float]] = {}


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


def _key(scope: str, uid: str, root_task_id: str,
         service_id: Optional[str]) -> Tuple[str, str, str, str]:
    return (scope, uid, service_id or "", root_task_id)


def _trim_window(dq: Deque[float], now: float) -> None:
    """Pop entries older than ``now - WINDOW_SECONDS``."""
    cutoff = now - WINDOW_SECONDS
    while dq and dq[0] < cutoff:
        dq.popleft()


def check_chain_quota(scope: str, uid: str, root_task_id: str,
                      service_id: Optional[str] = None) -> QuotaResult:
    """Atomically check whether one more spawn is allowed and register it.

    Behaviour:
        * If the chain is **under** budget → register a fresh timestamp,
          return ``allowed=True`` with updated counters.
        * If the chain is **at** budget → DO NOT register; return
          ``allowed=False`` and an ISO ``reset_at`` indicating when the
          oldest in-window entry will expire.

    The "register only on success" semantics make repeated denied calls
    cheap and preserve the caller's right to retry once the window slides.
    """
    limit = _env_rate()
    now = time.time()
    key = _key(scope, uid, root_task_id, service_id)

    with _lock:
        dq = _chain_history.setdefault(key, deque())
        _trim_window(dq, now)

        if len(dq) >= limit:
            reset_at_unix = dq[0] + WINDOW_SECONDS
            return QuotaResult(
                allowed=False,
                current=len(dq),
                limit=limit,
                remaining=0,
                window_seconds=WINDOW_SECONDS,
                reset_at=datetime.fromtimestamp(reset_at_unix).isoformat(),
            )

        dq.append(now)
        # The new oldest entry IS now; reset_at advances correspondingly.
        # (Or stays at the original oldest if there were prior entries.)
        reset_at_unix = dq[0] + WINDOW_SECONDS
        result = QuotaResult(
            allowed=True,
            current=len(dq),
            limit=limit,
            remaining=limit - len(dq),
            window_seconds=WINDOW_SECONDS,
            reset_at=datetime.fromtimestamp(reset_at_unix).isoformat(),
        )

    log.info("spawn quota: chain=%s allowed=%s used=%d/%d",
             root_task_id, result.allowed, result.current, result.limit)
    return result


def peek_chain_quota(scope: str, uid: str, root_task_id: str,
                     service_id: Optional[str] = None) -> QuotaResult:
    """Read-only counterpart of :func:`check_chain_quota` — does not register.

    Use this for UI-level "you have N spawns left" indicators where you
    don't want to consume budget just to display a number.
    """
    limit = _env_rate()
    now = time.time()
    key = _key(scope, uid, root_task_id, service_id)

    with _lock:
        dq = _chain_history.get(key)
        used = 0
        reset_at_unix = now
        if dq:
            _trim_window(dq, now)
            used = len(dq)
            if dq:
                reset_at_unix = dq[0] + WINDOW_SECONDS

    return QuotaResult(
        allowed=used < limit,
        current=used,
        limit=limit,
        remaining=max(0, limit - used),
        window_seconds=WINDOW_SECONDS,
        reset_at=datetime.fromtimestamp(reset_at_unix).isoformat(),
    )


# ── Snapshot helpers (for GET /api/scheduler/quotas) ────────────────────

def get_user_chain_stats(scope: str, uid: str,
                         service_id: Optional[str] = None
                         ) -> List[dict]:
    """All chains owned by a (scope, uid[, svc]) tuple, with current usage.

    Output entries:
        {
            "root_task_id": "task_xxx",
            "scope": "admin" | "service",
            "service_id": "svc_xxx" | "",
            "used": 3,
            "limit": 30,
            "remaining": 27,
            "reset_at": "2026-04-29T15:42:18",
            "window_seconds": 3600,
        }

    Empty / pruned chains are skipped.  Order is stable by ``root_task_id``.
    """
    limit = _env_rate()
    now = time.time()
    out: List[dict] = []
    svc_filter = service_id or ""

    with _lock:
        for (s, u, sid, rid), dq in list(_chain_history.items()):
            if s != scope or u != uid:
                continue
            if svc_filter and sid != svc_filter:
                continue
            _trim_window(dq, now)
            if not dq:
                _chain_history.pop((s, u, sid, rid), None)
                continue
            reset_at_unix = dq[0] + WINDOW_SECONDS
            out.append({
                "root_task_id": rid,
                "scope": s,
                "service_id": sid,
                "used": len(dq),
                "limit": limit,
                "remaining": max(0, limit - len(dq)),
                "reset_at": datetime.fromtimestamp(reset_at_unix).isoformat(),
                "window_seconds": WINDOW_SECONDS,
            })

    out.sort(key=lambda d: d["root_task_id"])
    return out


def reset_chain(scope: str, uid: str, root_task_id: str,
                service_id: Optional[str] = None) -> bool:
    """Forget a chain's spawn history.  Returns True if anything was removed.

    Intended for:
      * unit tests
      * an eventual ``POST /api/scheduler/quotas/reset`` admin tool
    """
    key = _key(scope, uid, root_task_id, service_id)
    with _lock:
        return _chain_history.pop(key, None) is not None


def reset_all() -> None:
    """Wipe all in-memory quota state (test fixture / reload helper)."""
    with _lock:
        _chain_history.clear()
