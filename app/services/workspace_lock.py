"""In-process workspace lock manager (reader-writer, prefix-scoped).

Solves the admin single-process contention problem: multiple admin agent
"processes" (interactive chat turns, scheduled tasks, spawned subagents) all
share ONE filesystem backend (`users/{uid}/filesystem/`). Without coordination,
two processes writing the same file race silently.

Model (per the agreed design):
  * A "process" is one agent turn, identified by an owner token (its thread_id).
    Subagents run inside the same async context => they inherit the parent's
    owner via contextvars and therefore share the parent's write locks.
  * Locks are path-prefix regions (e.g. "/scripts", "/docs/project-a"). "/" locks
    the whole workspace. Conflict detection is hierarchical.
  * Holding a lock covering a path grants WRITE. Not holding one => READ-ONLY for
    that path (writes are rejected with a clear message). Readers never block.
  * Acquisition is fail-fast: if any target region conflicts with a region held
    by ANOTHER process of the same user, the acquire fails (no blocking waits at
    this layer; callers decide whether to retry).
  * Auto-released when the owning process ends (stream `finally` / task `finally`).
    Scheduled tasks additionally carry a TTL as an orphan safety net.

This registry is intentionally in-memory and per-worker: the deployment is a
single uvicorn worker (workers=1) sharing one asyncio loop with the scheduler,
so an in-process registry is coherent. A server restart clears everything, which
is correct (all in-flight streams die on restart anyway).

Thread-safety: guarded by a `threading.RLock` (NOT asyncio.Lock) because writes
are also validated from worker threads — deepagents runs `awrite`/`aedit` via
`asyncio.to_thread(self.write, ...)` and `run_script` executes in a thread pool.
All registry critical sections are tiny (dict ops, no I/O, no await).
"""

from __future__ import annotations

import logging
import posixpath
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Optional

_log = logging.getLogger("workspace_lock")

# ── contextvars carrying the current process identity ──
# Set at stream/task start; naturally inherited by child asyncio tasks
# (create_task copies context) and by `asyncio.to_thread` (copies context).
_current_owner: ContextVar[Optional[str]] = ContextVar("ws_owner", default=None)
_current_user: ContextVar[Optional[str]] = ContextVar("ws_user", default=None)


@dataclass
class ProcessInfo:
    owner: str          # thread_id, e.g. "{uid}-{conv_id}" or "sched-{task}-{run}"
    user_id: str
    kind: str           # "interactive" | "scheduled" | "manual"
    label: str          # human-readable (conversation title / task name)
    started_at: float
    paths: list[str] = field(default_factory=list)   # held write regions (normalized)
    expires_at: Optional[float] = None                # TTL (scheduled orphan guard)


@dataclass
class AcquireResult:
    ok: bool
    granted: list[str]
    # conflicts: list of (requested_path, holder_owner, holder_label)
    conflicts: list[tuple[str, str, str]] = field(default_factory=list)


# owner -> ProcessInfo  (owner tokens are globally unique, include user_id)
_processes: dict[str, ProcessInfo] = {}
_reg_lock = threading.RLock()


# ==================== path helpers ====================

def _norm(p: str) -> str:
    """Normalize a workspace path to a canonical prefix form.

    Returns "/" for root, otherwise a leading-slash posix path with no trailing
    slash and no ".." traversal (collapsed by normpath).
    """
    if not p or not str(p).strip():
        return "/"
    raw = "/" + str(p).strip().strip("/")
    norm = posixpath.normpath(raw)
    if not norm.startswith("/"):
        norm = "/" + norm
    return norm


def _conflicts(a: str, b: str) -> bool:
    """True if two lock regions overlap (one contains or equals the other)."""
    if a == "/" or b == "/":
        return True
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def _covers(lock_path: str, target: str) -> bool:
    """True if holding `lock_path` grants write access to `target`."""
    if lock_path == "/":
        return True
    return target == lock_path or target.startswith(lock_path + "/")


def _merge_paths(paths: list[str]) -> list[str]:
    """Collapse a list of regions: drop any region covered by a broader one."""
    uniq = sorted(set(paths))
    if "/" in uniq:
        return ["/"]
    kept: list[str] = []
    for p in uniq:
        if any(_covers(other, p) for other in kept):
            continue
        # remove already-kept regions that this one covers (p is <= other only
        # if sorted asc, so this rarely triggers, but keep it correct)
        kept = [k for k in kept if not _covers(p, k)]
        kept.append(p)
    return sorted(kept)


# ==================== process lifecycle ====================

def register_process(owner: str, user_id: str, *, kind: str, label: str) -> None:
    """Register an active process (with no locks yet). Idempotent."""
    with _reg_lock:
        existing = _processes.get(owner)
        if existing is not None:
            # keep existing locks; refresh label/kind
            existing.label = label or existing.label
            existing.kind = kind or existing.kind
            return
        _processes[owner] = ProcessInfo(
            owner=owner, user_id=user_id, kind=kind,
            label=label, started_at=time.time(),
        )


def unregister_process(owner: str) -> None:
    """Remove a process and drop all its locks (call in stream/task finally)."""
    with _reg_lock:
        _processes.pop(owner, None)


def set_ttl(owner: str, ttl: Optional[float]) -> None:
    """Attach/refresh a TTL (orphan guard) on a process's held locks."""
    with _reg_lock:
        me = _processes.get(owner)
        if me is not None:
            me.expires_at = (time.time() + ttl) if ttl is not None else None


def release_locks(owner: str) -> None:
    """Free a process's locks but keep it registered (voluntary early release)."""
    with _reg_lock:
        me = _processes.get(owner)
        if me is not None:
            me.paths = []
            me.expires_at = None


# ==================== acquire / query ====================

def try_acquire(owner: str, paths: list[str], *, ttl: Optional[float] = None) -> AcquireResult:
    """Attempt to lock `paths` for `owner` (fail-fast on conflict).

    The process must already be registered. On success the regions are merged
    into the process's held set. On conflict, nothing is granted.
    """
    norm = _merge_paths([_norm(p) for p in paths]) if paths else []
    if not norm:
        return AcquireResult(ok=True, granted=[])
    _sweep_expired_locked_free()
    with _reg_lock:
        me = _processes.get(owner)
        if me is None:
            _log.warning("try_acquire for unregistered owner=%s", owner)
            return AcquireResult(ok=False, granted=[], conflicts=[])
        conflicts: list[tuple[str, str, str]] = []
        for p in norm:
            for other_owner, info in _processes.items():
                if other_owner == owner or info.user_id != me.user_id:
                    continue
                if any(_conflicts(p, held) for held in info.paths):
                    conflicts.append((p, other_owner, info.label))
                    break
        if conflicts:
            return AcquireResult(ok=False, granted=[], conflicts=conflicts)
        me.paths = _merge_paths(me.paths + norm)
        if ttl is not None:
            me.expires_at = time.time() + ttl
        return AcquireResult(ok=True, granted=list(me.paths))


def acquire_broadest(
    owner: str,
    user_id: str,
    *,
    top_level: Optional[list[str]] = None,
    top_level_fn: Optional[Callable[[], list[str]]] = None,
) -> AcquireResult:
    """Grab the broadest currently-free region for an interactive default.

    Tries to lock "/" (whole workspace). If that conflicts with a background
    holder, falls back to locking every free top-level dir (so the interactive
    session keeps full write access everywhere EXCEPT the contended subtree).
    Never fails: returns whatever was free (possibly empty => read-only).

    `top_level_fn` is only invoked on "/" contention (rare), so the common solo
    case never pays the cost of enumerating real directories. Falls back to the
    static `top_level` list if the provider errors or returns nothing.
    """
    whole = try_acquire(owner, ["/"])
    if whole.ok:
        return whole
    dirs: list[str] = list(top_level or [])
    if top_level_fn is not None:
        try:
            fetched = top_level_fn()
            if fetched:
                dirs = fetched
        except Exception:  # noqa: BLE001 — degrade to static list on any error
            pass
    free: list[str] = []
    for d in dirs:
        r = try_acquire(owner, [d])
        if r.ok:
            free.append(_norm(d))
    return AcquireResult(ok=bool(free), granted=free)


def is_write_allowed(owner: Optional[str], target: str) -> bool:
    """True if the given owner holds a lock covering `target`.

    Fail-open when `owner` is None: writes happening outside any tracked agent
    process (internal machinery) are always allowed. Within a tracked process,
    only regions the process holds are writable (no-lock => read-only).
    """
    if owner is None:
        return True
    t = _norm(target)
    with _reg_lock:
        me = _processes.get(owner)
        if me is None:
            return True
        return any(_covers(lp, t) for lp in me.paths)


def get_process(owner: str) -> Optional[ProcessInfo]:
    with _reg_lock:
        return _processes.get(owner)


def check_write(target: str) -> Optional[str]:
    """Return None if the current process may write `target`, else a deny message.

    Reads the process identity from contextvars, so this is the single entry
    point used by tools that mutate the workspace (run_script, generate_*,
    move_file, soul_write/delete).
    """
    owner = current_owner()
    if is_write_allowed(owner, target):
        return None
    return deny_message(owner, current_user() or "", target)


def who_blocks(user_id: str, target: str, exclude_owner: Optional[str]) -> Optional[ProcessInfo]:
    """Return another process that holds a region conflicting with `target`."""
    t = _norm(target)
    with _reg_lock:
        for owner, info in _processes.items():
            if owner == exclude_owner or info.user_id != user_id:
                continue
            if any(_conflicts(held, t) for held in info.paths):
                return info
    return None


def deny_message(owner: Optional[str], user_id: str, target: str) -> str:
    """Build a clear, agent-actionable rejection message for a blocked write."""
    holder = who_blocks(user_id, target, owner)
    if holder is not None:
        return (
            f"⛔ 写入被拒绝：路径 `{target}` 所在工作区正被另一进程「{holder.label}」"
            f"（{holder.kind}）锁定。你当前对该区域只有只读权限。请改在未锁定的区域"
            f"操作，或稍后重试。"
        )
    return (
        f"⛔ 写入被拒绝：本进程未持有覆盖 `{target}` 的工作区写锁（只读模式）。"
        f"如需写入，请先调用 acquire_workspace 声明该区域。"
    )


# ==================== introspection (UI) ====================

def list_processes(user_id: Optional[str] = None) -> list[dict]:
    """Snapshot of active processes + their locked regions (for the UI panel)."""
    _sweep_expired_locked_free()
    now = time.time()
    out: list[dict] = []
    with _reg_lock:
        for info in _processes.values():
            if user_id is not None and info.user_id != user_id:
                continue
            out.append({
                "owner": info.owner,
                "user_id": info.user_id,
                "kind": info.kind,
                "label": info.label,
                "started_at": info.started_at,
                "elapsed_sec": round(now - info.started_at, 1),
                "locked_paths": list(info.paths),
                "expires_at": info.expires_at,
            })
    out.sort(key=lambda x: x["started_at"])
    return out


# ==================== TTL sweep ====================

def _sweep_expired_locked_free() -> None:
    """Clear locks whose TTL has passed (scheduled orphan guard). The process
    stays registered until its own finally runs; only its locks are freed."""
    now = time.time()
    with _reg_lock:
        for info in _processes.values():
            if info.expires_at is not None and now > info.expires_at:
                if info.paths:
                    _log.warning(
                        "workspace lock TTL expired, freeing owner=%s paths=%s",
                        info.owner, info.paths,
                    )
                info.paths = []
                info.expires_at = None


# ==================== contextvar helpers ====================

def set_context(owner: Optional[str], user_id: Optional[str]) -> tuple:
    """Set the current process identity; returns tokens for reset()."""
    return (_current_owner.set(owner), _current_user.set(user_id))


def reset_context(tokens: tuple) -> None:
    try:
        _current_owner.reset(tokens[0])
        _current_user.reset(tokens[1])
    except Exception:
        pass


def current_owner() -> Optional[str]:
    return _current_owner.get()


def current_user() -> Optional[str]:
    return _current_user.get()


def clear_all() -> None:
    """Reset the entire registry (used by tests / startup)."""
    with _reg_lock:
        _processes.clear()
