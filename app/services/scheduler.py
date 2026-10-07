"""
Scheduled task engine.

== Admin tasks ==
    Stored at: {user_dir}/tasks/{task_id}.json
    Steps  at: {user_dir}/tasks/{task_id}.steps/{run_id}.jsonl  (one line per step)
    task_type: "script" | "agent"
    agent tasks use the full user agent (create_user_agent) with humanchat
    capability, enabling send_message tool calls that are intercepted and
    forwarded to WeChat when reply_to is configured.

== Service tasks ==
    Stored at: {user_dir}/services/{service_id}/tasks/{task_id}.json
    Steps  at: {user_dir}/services/{service_id}/tasks/{task_id}.steps/{run_id}.jsonl
    task_type: "agent" only (no scripts)
    Executes using the service's consumer agent with humanchat injected.
    send_message tool calls are intercepted and forwarded to WeChat.

== Storage layout (since 2026-04-23) ==
    Task JSONs no longer embed each run's `steps[]` — they only keep run
    SUMMARIES (run_id/started_at/finished_at/status/output).  Each run's
    full step log lives in its own JSONL sibling so listing tasks /
    reading task config doesn't need to drag hundreds of step entries
    into memory.  Lazy migration: when a legacy task.json with embedded
    steps is loaded, ``_externalize_steps`` splits them out on the next
    save (or eagerly via ``get_task_runs``).

Common fields:
    id, name, description
    schedule_type: "cron" | "once" | "interval"
    schedule:
        cron:     cron expression, e.g. "0 9 * * 1"
        once:     ISO datetime string, e.g. "2026-04-01T09:00:00"
        interval: seconds, e.g. 3600
    task_config:
        script:  {script_path, script_args, input_data, timeout, permissions}
        agent:   {prompt, doc_path, model, capabilities, permissions}
        permissions (shared):
            read_dirs:  list[str] — dirs relative to user fs root
            write_dirs: list[str] — dirs relative to user fs root
    reply_to (optional):
        channel: "wechat" | "web"
        admin_id: str
        service_id: str | None
        session_id: str (WeChat session_id)
        conversation_id: str
    enabled: bool
    created_at: ISO
    last_run_at: ISO | null
    next_run_at: ISO | null
    runs: list[{run_id, started_at, finished_at, status, output}]  (last 20)

The scheduler loop runs every 30 seconds, checks next_run_at,
and executes due tasks in background asyncio tasks.
"""

import heapq
import os
import json
import time
import uuid
import asyncio
import logging
from functools import wraps
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Literal

from app.core.security import get_user_dir
from app.core.jsonl_store import append_jsonl_many, read_jsonl
from app.services import scheduler_tree as st
from app.services.token_usage import build_usage_callbacks
from app.execution import context as execution
from app.execution.store import Conflict, task_key

from app.services.scheduler_policy import validate_task, validate_reply, permission_dirs, service_doc_paths

log = logging.getLogger("scheduler")
_state_lock = st.TASK_STORAGE_LOCK


def _configure_admin_runtime(uid: str, task_type: str, raw_config: dict) -> dict:
    """Snapshot an explicitly selected CLI binding at an admin task save."""
    if not isinstance(raw_config, dict):
        raise ValueError('task_config must be an object')
    config = dict(raw_config)
    config.pop('runtime_binding', None)  # Never trust a binding supplied by HTTP or a child task.
    choice = config.get('runtime_choice')
    if choice is not None and (not isinstance(choice, dict) or choice.get('runtime') not in ('deepagents', 'codex', 'cursor')):
        raise ValueError('定时任务引擎选择无效')
    if task_type != 'agent' and choice and choice.get('runtime') != 'deepagents':
        raise ValueError('脚本任务不能选择 CLI 引擎')
    if task_type != 'agent':
        return config
    from app.execution.grants import SERVICE_SUPPORTED_CAPABILITIES
    capabilities = config.get('capabilities') or []
    if not isinstance(capabilities, list) or any(not isinstance(c, str) for c in capabilities):
        raise ValueError('定时任务能力必须是列表')
    if choice is None or choice['runtime'] == 'deepagents':
        unsupported = set(capabilities) - SERVICE_SUPPORTED_CAPABILITIES
        if unsupported:
            raise ValueError('DeepAgents 定时任务尚不支持这些能力：' + ', '.join(sorted(unsupported)))
        return config  # Legacy tasks retain their DeepAgents executor.
    if not isinstance(choice.get('profile_id'), str) or not choice['profile_id'] or not isinstance(choice.get('model'), str) or not choice['model']:
        raise ValueError('请为 CLI 定时任务选择连接和模型')
    unsupported = set(capabilities) - {'docs', 'documents', 'humanchat', 'web', 'image'}
    if unsupported:
        raise ValueError('CLI 定时任务尚不支持这些能力：' + ', '.join(sorted(unsupported)))
    from app.runtime.chat import choice as runtime_choice
    requested = {**choice, 'image_mode': 'native' if 'image' in capabilities else 'off'}
    config['runtime_binding'] = runtime_choice(uid, requested)
    return config


def _serialized(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with _state_lock:
            return fn(*args, **kwargs)
    return call


def _execution_disabled():
    return os.getenv("DISABLE_SCHEDULER", "").strip().lower() in ("1", "true", "yes", "on")


def _prune_run_steps(task_dir, runs):
    """Keep only logs referenced by committed summaries (never prune before save)."""
    keep = {r["run_id"] + ".jsonl" for r in runs if r.get("run_id")}
    directory = st.runs_dir(task_dir)
    if not os.path.isdir(directory):
        return
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= 256:
                break  # bounded work per save; subsequent saves continue removing old logs
            if entry.name.startswith("run_") and entry.name.endswith(".jsonl") and entry.name not in keep:
                try:
                    os.unlink(entry.path)
                except OSError:
                    log.exception("Cannot prune scheduled run log %s", entry.name)


# 同时为「就绪」状态的定时任务数上限 —— 重启后多条 cron/interval 若同时过期，
# 未限流时为每条任务起一个 create_user_agent，易在 4GB 实例上触发 OOM。
_DEFAULT_SCHED_SLOTS = 4
_SCHED_SLOTS_CAP = 64


def scheduler_concurrency_slots() -> int:
    """可读 env SCHEDULER_MAX_CONCURRENT；默认 4，夹在 [1, _SCHED_SLOTS_CAP]。"""
    raw = os.environ.get("SCHEDULER_MAX_CONCURRENT", "").strip()
    if not raw:
        return _DEFAULT_SCHED_SLOTS
    try:
        n = int(raw, 10)
    except ValueError:
        log.warning(
            "Invalid SCHEDULER_MAX_CONCURRENT=%r — using default %d",
            raw, _DEFAULT_SCHED_SLOTS,
        )
        return _DEFAULT_SCHED_SLOTS
    if n < 1:
        return 1
    if n > _SCHED_SLOTS_CAP:
        log.warning(
            "SCHEDULER_MAX_CONCURRENT=%d capped at %d",
            n, _SCHED_SLOTS_CAP,
        )
        return _SCHED_SLOTS_CAP
    return n


_MAX_RUNS_STORED = 20
_TASK_TIMEOUT_S  = 1800        # max 30 min per task run

# ── Runaway guards ────────────────────────────────────────────────────────
# A task whose next_run_at keeps landing in the past re-fires the instant the
# previous run finishes, burning one full agent + LLM call per cycle.  The
# root cause (once-tasks with an empty schedule) is fixed in
# `_compute_next_run`, but these two guards bound the blast radius of any
# future scheduling bug.  Both are applied in `_apply_post_run_schedule`,
# i.e. only when re-scheduling **after** a run — never at creation time, so
# "create and fire ASAP" ergonomics stay intact.
_DEFAULT_MAX_CONSECUTIVE_FAILURES = 5
_DEFAULT_MIN_RUN_INTERVAL_S = 5


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        n = int(raw, 10)
    except ValueError:
        log.warning("Invalid %s=%r — using default %d", name, raw, default)
        return default
    return max(minimum, n)


def max_consecutive_failures() -> int:
    """连续失败多少次后自动禁用任务。0 = 关闭该保护。"""
    return _env_int("SCHEDULER_MAX_CONSECUTIVE_FAILURES",
                    _DEFAULT_MAX_CONSECUTIVE_FAILURES)


def min_run_interval_seconds() -> int:
    """同一任务两次执行之间的最小间隔秒数。0 = 关闭该保护。"""
    return _env_int("SCHEDULER_MIN_RUN_INTERVAL_S",
                    _DEFAULT_MIN_RUN_INTERVAL_S)

# Bound the heap-driven idle wait so we still wake up to re-scan the disk
# periodically (catches: out-of-band edits, clock jumps, missed wake_event,
# dropped tasks if a future bug).  Not a polling interval — only used when
# the heap is empty OR for a sanity re-scan.
_IDLE_RESCAN_S = 60
_RESCAN_INTERVAL_S = 600       # safety re-scan every 10 min regardless

# L3 (descendants_summary) configuration — see .cursorrules §"Scheduled task v2"
_L3_SUMMARY_MAX_CHARS = 1500
_L3_SUMMARY_LINE_PREVIEW = 80


# ── Execution context (set by _run_*_agent_task, read by spawn tool) ─────

@dataclass
class TaskContext:
    """Per-execution snapshot consumed by ``spawn_child_task`` and L3 propagation.

    Set via ``_current_task_var`` at the top of every scheduled-task agent run
    and reset in the matching ``finally`` block.  Read by:
      * ``app/services/tools.py::create_spawn_child_task_tool`` to determine
        parent/root/depth and inheritance defaults
      * L3 ancestor write-back inside this module
      * Optional logging / observability hooks
    """
    scope: Literal["admin", "service"]
    uid: str                                        # admin user_id (always)
    service_id: Optional[str]                       # None for admin scope
    task_id: str
    root_task_id: str
    parent_task_id: Optional[str]
    spawn_chain: List[str]                          # [root, ..., parent]; empty for root tasks
    depth: int                                      # 0 = root
    reply_to: Optional[Dict[str, Any]] = None
    permissions: Optional[Dict[str, Any]] = None
    capabilities: Optional[List[str]] = field(default_factory=list)
    tz_offset_hours: float = 8.0
    model: Optional[str] = None                     # 父任务执行用的 LLM model id（spawn 默认继承）


_current_task_var: ContextVar[Optional[TaskContext]] = ContextVar(
    "_current_task_var", default=None
)


def get_current_task_context() -> Optional[TaskContext]:
    """Public accessor used by ``spawn_child_task`` (avoids private import)."""
    return _current_task_var.get()


# ── Path helpers ──────────────────────────────────────────────────────────
#
# v2 storage uses the filesystem-tree layout owned by ``app.services.scheduler_tree``.
# These thin wrappers exist so call sites stay readable; the heavy lifting
# (path lookup, lazy migration, walk_tree) lives in scheduler_tree.

def _tasks_dir(user_id: str) -> str:
    """Root of the v2 admin task tree (also the legacy v1 flat dir)."""
    return st.scope_root("admin", user_id)


def _service_tasks_dir(admin_id: str, service_id: str) -> str:
    """Root of the v2 service task tree (also the legacy v1 flat dir)."""
    return st.scope_root("service", admin_id, service_id)


def _task_runs_dir_v2(user_id: str, task_id: str) -> Optional[str]:
    """Per-task ``runs/`` directory in the v2 tree, or None if task missing."""
    task_dir = st.task_path_for("admin", user_id, task_id)
    return st.runs_dir(task_dir) if task_dir else None


def _service_task_runs_dir_v2(admin_id: str, service_id: str,
                              task_id: str) -> Optional[str]:
    task_dir = st.task_path_for("service", admin_id, task_id, service_id)
    return st.runs_dir(task_dir) if task_dir else None


# ── Step externalization (per-run JSONL, written once per run) ────────────

def _externalize_run_steps(task_dir: str, runs: List[Dict[str, Any]]) -> None:
    """Move each run's ``steps`` array out of the task dict into a per-run
    JSONL file under ``{task_dir}/runs/``.  Mutates ``runs`` in place
    (drops the ``steps`` key).

    Idempotent: if the JSONL already exists we don't overwrite it.  Runs
    without a ``run_id`` (very old shape) are left untouched so we don't
    silently lose data.
    """
    runs_d = st.runs_dir(task_dir)
    for r in runs:
        steps = r.get("steps")
        if not steps:
            r.pop("steps", None)
            continue
        run_id = r.get("run_id")
        if not run_id:
            continue
        path = os.path.join(runs_d, f"{run_id}.jsonl")
        try:
            if not os.path.isfile(path):
                os.makedirs(runs_d, exist_ok=True)
                append_jsonl_many(path, steps)
            r.pop("steps", None)
        except OSError:
            log.exception("Failed to externalize steps for run %s -> %s",
                          run_id, path)


def _attach_run_steps(task_dir: str, runs: List[Dict[str, Any]]
                      ) -> List[Dict[str, Any]]:
    """For UI / API responses: re-attach steps[] from per-run JSONL files
    under ``{task_dir}/runs/`` into a copy of the run summaries.  Falls
    back to the inline ``steps`` array if it's still there (un-migrated
    runs)."""
    runs_d = st.runs_dir(task_dir)
    out = []
    for r in runs:
        rec = dict(r)
        if "steps" in rec and rec["steps"]:
            out.append(rec)
            continue
        run_id = rec.get("run_id")
        if run_id:
            path = os.path.join(runs_d, f"{run_id}.jsonl")
            if os.path.isfile(path):
                rec["steps"] = read_jsonl(path)
        out.append(rec)
    return out


# ── Scheduled-task tool-block builder ─────────────────────────────────────

def _compose_clean_task_output(delivered_parts: List[str],
                               output_parts: List[str]) -> str:
    """Build the user-facing `result` text for a scheduled_task block.

    Priority:
      1. Concatenate everything actually delivered via `send_message`
         (text + media placeholders) — this is what the user really saw.
      2. Fallback to the raw AI monologue (`output_parts`) ONLY when the agent
         never called `send_message` (e.g. failed to follow instructions); in
         that case the raw text is the best signal we have for diagnosis.
      3. Last resort: a fixed placeholder.

    Keeps scheduled_task cards / agent recall (L1 + L2) clean and prevents
    ReAct-style internal monologue ("我需要搜索…", "任务完成，已发送给用户")
    from leaking into chat history.
    """
    if delivered_parts:
        return "\n\n".join(p for p in delivered_parts if p)
    if output_parts:
        return "\n".join(output_parts)
    return "（Agent 未返回输出）"


def _build_scheduled_task_block(task_meta: Optional[Dict[str, Any]],
                                output: str,
                                success: bool = True,
                                error: Optional[str] = None) -> Dict[str, Any]:
    """Build a `tool` block representing a scheduled task execution result.

    Persisted into messages.json so the frontend renders it as a dedicated
    ScheduledTaskCard (admin) or friendly variant (service-chat), instead of an
    indistinguishable agent reply. `args` carries metadata (task_name / id /
    schedule type / scheduled_at / status) for the card header; `result` holds
    the full output text rendered as markdown body.

    `task_meta` may be None for legacy callers — in that case we fall back to a
    minimal block. Tool name `scheduled_task` is reserved and matched verbatim
    by the frontend (see MessageBubble.BlocksRenderer / ScheduledTaskCard).
    """
    meta = dict(task_meta or {})
    meta["status"] = "success" if success else "error"
    if error:
        meta["error"] = error[:200]
    return {
        "type": "tool",
        "name": "scheduled_task",
        "args": json.dumps(meta, ensure_ascii=False),
        "result": output,
        "done": True,
        "source": "scheduled_task",
    }


async def _safe_persist_admin_failure(user_id: str,
                                      conv_id: str,
                                      task_meta: Optional[Dict[str, Any]],
                                      error_text: str) -> None:
    """Persist a failed admin scheduled-task as a tool block (visible in chat)
    and also enqueue an L2 injection so the agent's next turn knows it failed.

    Best-effort; swallows persistence errors so the scheduler loop keeps running.
    No-op when conv_id is missing (task without a target conversation).
    """
    if execution.current() or not conv_id:
        return
    output_text = f"任务执行失败：{error_text}"
    try:
        from app.services.conversations import save_message
        block = _build_scheduled_task_block(
            task_meta, output_text, success=False, error=error_text,
        )
        save_message(user_id, conv_id, "assistant", output_text, blocks=[block])
    except Exception:
        log.exception("Failed to persist admin task FAILURE to conv %s", conv_id)
    try:
        from app.services import scheduled_inject
        await scheduled_inject.enqueue_admin(
            user_id=user_id, conv_id=conv_id,
            task_meta=task_meta or {}, output=output_text,
            success=False, error=error_text,
        )
    except Exception:
        log.exception("Failed to enqueue admin task FAILURE L2 injection for conv %s",
                      conv_id)


async def _safe_persist_service_failure(admin_id: str, service_id: str,
                                        conversation_id: str,
                                        task_meta: Optional[Dict[str, Any]],
                                        error_text: str) -> None:
    """Persist a failed service scheduled-task as a tool block + enqueue L2 injection."""
    if execution.current() or not conversation_id:
        return
    output_text = f"任务执行失败：{error_text}"
    try:
        from app.services.published import save_consumer_message
        block = _build_scheduled_task_block(
            task_meta, output_text, success=False, error=error_text,
        )
        save_consumer_message(admin_id, service_id, conversation_id, "assistant",
                              output_text, blocks=[block])
    except Exception:
        log.exception("Failed to persist service task FAILURE to conv %s",
                      conversation_id)
    try:
        from app.services import scheduled_inject
        await scheduled_inject.enqueue_service(
            admin_id=admin_id, service_id=service_id, conv_id=conversation_id,
            task_meta=task_meta or {}, output=output_text,
            success=False, error=error_text,
        )
    except Exception:
        log.exception("Failed to enqueue service task FAILURE L2 injection for conv %s",
                      conversation_id)


# ── croniter (optional dep) ───────────────────────────────────────────────

def _next_cron(expr: str, after: datetime, tz_offset_hours: float = 0) -> Optional[datetime]:
    """Return next UTC datetime after `after` for a cron expression.

    The cron expression is interpreted in the user's timezone (tz_offset_hours).
    `after` must be UTC-aware.  The returned datetime is UTC-aware.
    """
    try:
        from croniter import croniter
        user_tz = timezone(timedelta(hours=tz_offset_hours))
        after_local = after.astimezone(user_tz)
        it = croniter(expr, after_local)
        next_local = it.get_next(datetime)
        if next_local.tzinfo is None:
            next_local = next_local.replace(tzinfo=user_tz)
        return next_local.astimezone(timezone.utc)
    except ImportError:
        log.warning("croniter not installed — cron schedules won't work. pip install croniter")
        return None
    except Exception as e:
        log.warning("Invalid cron expression %r: %s", expr, e)
        return None


# ── Task CRUD ─────────────────────────────────────────────────────────────

def _load_task(user_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    """Load an admin task by id. Auto-migrates v1 → v2 on first hit."""
    return execution.load_task("admin", user_id, task_id)


@_serialized
def _save_task(user_id: str, task: Dict[str, Any]) -> None:
    """Persist an admin task into the v2 tree.

    For root tasks: writes to ``users/{uid}/tasks/{task_id}/_meta.json``.
    For child tasks: writes to the existing tree path (located via
    ``task_path_for``); raises ``RuntimeError`` if the directory is missing
    (caller bug — must use ``create_child_task`` to make the dir first).
    """
    task_id = task["id"]
    task_dir = st.task_path_for("admin", user_id, task_id)
    if not task_dir:
        # Root task: create at scope_root/{task_id}/
        if task.get("parent_task_id"):
            raise RuntimeError(
                f"_save_task: child task {task_id} has no on-disk dir; "
                "create_child_task must run before _save_task")
        task_dir = st.create_root_dir("admin", user_id, task_id)

    runs = task.get("runs") or []
    _externalize_run_steps(task_dir, runs)
    task["runs"] = runs
    st.save_task_meta(task_dir, task)
    _prune_run_steps(task_dir, runs)
    # New mtime on _meta.json — caller (HeapScheduler.upsert) handles re-heap.


def _new_task_meta_v2(scope: Literal["admin", "service"], uid: str,
                      data: Dict[str, Any], task_id: str,
                      service_id: Optional[str] = None) -> Dict[str, Any]:
    """Build a fresh task dict pre-populated with v2 spawn-tree fields.

    Centralises the field defaults so admin / service / spawn paths stay in
    sync as the schema evolves.
    """
    now = datetime.now(timezone.utc)
    tz_offset = data.get("tz_offset_hours")
    if tz_offset is None:
        from app.services.preferences import get_tz_offset
        tz_offset = get_tz_offset(uid)

    parent_task_id = data.get("parent_task_id")
    spawn_chain = list(data.get("spawn_chain") or [])
    spawn_depth = int(data.get("spawn_depth", 0))
    root_task_id = data.get("root_task_id") or (
        spawn_chain[0] if spawn_chain else task_id
    )

    meta: Dict[str, Any] = {
        "id": task_id,
        "name": data.get("name", "Unnamed Task"),
        "description": data.get("description", ""),
        "schedule_type": data.get("schedule_type", "once"),
        "schedule": data.get("schedule", ""),
        "task_type": data.get("task_type", "agent" if scope == "service" else "script"),
        "task_config": (_configure_admin_runtime(uid, data.get("task_type", "script"), data.get("task_config", {}))
                        if scope == "admin" else data.get("task_config", {})),
        "reply_to": data.get("reply_to"),
        "enabled": data.get("enabled", True),
        "tz_offset_hours": tz_offset,
        "created_at": now.isoformat(),
        "last_run_at": None,
        "last_scheduled_run_at": None,
        "next_run_at": None,
        "consecutive_failures": 0,
        "runs": [],
        "run_count": 0,
        "revision": 1,
        # v2 spawn-tree fields
        "parent_task_id": parent_task_id,
        "root_task_id": root_task_id,
        "spawn_chain": spawn_chain,
        "spawn_depth": spawn_depth,
        "spawn_reason": data.get("spawn_reason", ""),
        "children_count": 0,
        "descendants_count": 0,
        "descendants_summary": "",
    }
    if scope == "admin":
        meta["user_id"] = uid
    else:
        meta["admin_id"] = uid
        meta["service_id"] = service_id
    validate_task(meta, uid, service_id)
    return meta


@_serialized
def create_task(user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new admin scheduled task (root by default) and compute next_run_at.

    To create a CHILD task under an existing parent, use :func:`create_child_task`
    which sets up parent linkage and bumps parent counters atomically.
    """
    ctx = get_current_task_context()
    if ctx:
        if ctx.scope != "admin" or ctx.uid != user_id:
            raise PermissionError("Scheduled tasks cannot create tasks in another scope")
        return create_child_task(ctx, data)
    task_id = "task_" + uuid.uuid4().hex[:8]
    task = _new_task_meta_v2("admin", user_id, data, task_id)
    task["next_run_at"] = _compute_next_run(task, datetime.now(timezone.utc))
    _save_task(user_id, task)
    _heap_upsert("admin", user_id, task_id, task.get("next_run_at"))
    return task


@_serialized
def list_tasks(user_id: str, *,
               roots_only: bool = True) -> List[Dict[str, Any]]:
    """List admin tasks, summary form.

    ``roots_only=True`` (default) hides spawn descendants — keeps the
    sidebar manageable when many tasks self-spawn children.  Inspect
    descendants via the pedigree graph (``walk_tree`` / ``/tree`` endpoint).
    ``roots_only=False`` falls back to the legacy flat-with-descendants
    list (used by diagnostic / migration callers).
    """
    if roots_only:
        out: List[Dict[str, Any]] = []
        for m in st.list_root_tasks("admin", user_id):
            count = m.get("run_count", len(m.get("runs", [])))
            m = {k: v for k, v in m.items() if k != "runs"}
            m["run_count"] = count
            out.append(m)
        tasks = out
    else:
        tasks = st.list_all_tasks_flat("admin", user_id, include_runs=False)
    tasks = [execution.overlay("admin", user_id, None, t) for t in tasks]
    tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return tasks


@_serialized
def get_task(user_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    return _load_task(user_id, task_id)


@_serialized
def update_task(user_id: str, task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    task = _load_task(user_id, task_id)
    if not task:
        return None
    # Protect identity / lineage / lifecycle fields from external override.
    allowed = {"name", "description", "schedule_type", "schedule", "task_type",
               "task_config", "reply_to", "enabled", "tz_offset_hours"}
    changed = {k: v for k, v in updates.items() if k in allowed and task.get(k) != v}
    was_enabled = task.get("enabled", True)
    task.update(changed)
    if 'task_config' in changed or 'task_type' in changed:
        if changed.get('task_type') == 'script' and 'task_config' not in changed:
            task['task_config'] = {k: v for k, v in task['task_config'].items()
                                   if k not in ('runtime_choice', 'runtime_binding')}
        task['task_config'] = _configure_admin_runtime(user_id, task['task_type'], task['task_config'])
    if not (set(changed) == {"enabled"} and changed["enabled"] is False):
        validate_task(task, user_id, check_reply_session="reply_to" in changed)
    if changed.get("enabled") is True and not was_enabled:
        task["consecutive_failures"] = 0
    if any(k in changed for k in ("schedule_type", "schedule", "enabled", "tz_offset_hours")):
        task["next_run_at"] = _compute_next_run(task, datetime.now(timezone.utc))
    if changed:
        task["revision"] = task.get("revision", 0) + 1
    _save_task(user_id, task)
    _heap_upsert("admin", user_id, task_id, task.get("next_run_at"))
    return task


@_serialized
def delete_task(user_id: str, task_id: str) -> bool:
    """Delete an admin task and its **entire** spawn subtree (recursive).

    Also evicts the deleted task ids from the run heap index so the loop
    doesn't try to fire them after deletion (orphan dispatch bug).
    """
    # Snapshot ids BEFORE deletion so we can clean the heap index afterwards.
    victims = st.list_descendants("admin", user_id, task_id, include_root=True)
    deleted = st.delete_task_subtree("admin", user_id, task_id)
    if deleted:
        for v in victims:
            _heap_upsert("admin", user_id, v["id"], None)
    return deleted


@_serialized
def get_task_runs(user_id: str, task_id: str) -> List[Dict[str, Any]]:
    task_dir = st.task_path_for("admin", user_id, task_id)
    if not task_dir:
        # legacy fallback: trigger migration
        if not _load_task(user_id, task_id):
            return []
        task_dir = st.task_path_for("admin", user_id, task_id)
        if not task_dir:
            return []
    meta = st.load_task_meta(task_dir) or {}
    return execution.merge_runs("admin", user_id, None, task_id, _attach_run_steps(task_dir, meta.get("runs", [])))


# ── Service task CRUD ─────────────────────────────────────────────────────

def _load_service_task(admin_id: str, service_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    """Load a service task by id. Auto-migrates v1 → v2 on first hit."""
    return execution.load_task("service", admin_id, task_id, service_id)


@_serialized
def _save_service_task(admin_id: str, service_id: str, task: Dict[str, Any]) -> None:
    """Persist a service task into the v2 tree.

    Same root vs child semantics as :func:`_save_task` — see its docstring.
    """
    task_id = task["id"]
    task_dir = st.task_path_for("service", admin_id, task_id, service_id)
    if not task_dir:
        if task.get("parent_task_id"):
            raise RuntimeError(
                f"_save_service_task: child task {task_id} has no on-disk dir; "
                "create_child_task must run before _save_service_task")
        task_dir = st.create_root_dir("service", admin_id, task_id, service_id)

    runs = task.get("runs") or []
    _externalize_run_steps(task_dir, runs)
    task["runs"] = runs
    st.save_task_meta(task_dir, task)
    _prune_run_steps(task_dir, runs)


@_serialized
def create_service_task(admin_id: str, service_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a scheduled task under a published service (root by default).

    For child tasks within a service tree, use :func:`create_child_task`.
    """
    ctx = get_current_task_context()
    if ctx:
        if ctx.scope != "service" or ctx.uid != admin_id or ctx.service_id != service_id:
            raise PermissionError("Scheduled cross-service fan-out requires a shared budget and is not enabled")
        return create_child_task(ctx, data)
    task_id = "stask_" + uuid.uuid4().hex[:8]
    task = _new_task_meta_v2("service", admin_id, data, task_id, service_id)
    task["next_run_at"] = _compute_next_run(task, datetime.now(timezone.utc))
    _save_service_task(admin_id, service_id, task)
    _heap_upsert("service", admin_id, task_id, task.get("next_run_at"),
                 service_id=service_id)
    return task


@_serialized
def list_service_tasks(admin_id: str, service_id: str, *,
                       roots_only: bool = True) -> List[Dict[str, Any]]:
    """List service tasks for one service, summary form.

    See :func:`list_tasks` for the ``roots_only`` contract — same semantics,
    just scoped to ``services/{service_id}/tasks``.
    """
    if roots_only:
        out: List[Dict[str, Any]] = []
        for m in st.list_root_tasks("service", admin_id, service_id):
            count = m.get("run_count", len(m.get("runs", [])))
            m = {k: v for k, v in m.items() if k != "runs"}
            m["run_count"] = count
            out.append(m)
        tasks = out
    else:
        tasks = st.list_all_tasks_flat("service", admin_id, service_id,
                                       include_runs=False)
    tasks = [execution.overlay("service", admin_id, service_id, t) for t in tasks]
    tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return tasks


@_serialized
def get_service_task(admin_id: str, service_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    return _load_service_task(admin_id, service_id, task_id)


@_serialized
def update_service_task(
    admin_id: str, service_id: str, task_id: str, updates: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    task = _load_service_task(admin_id, service_id, task_id)
    if not task:
        return None
    allowed = {"name", "description", "schedule_type", "schedule", "task_type",
               "task_config", "reply_to", "enabled", "tz_offset_hours"}
    changed = {k: v for k, v in updates.items() if k in allowed and task.get(k) != v}
    was_enabled = task.get("enabled", True)
    task.update(changed)
    if not (set(changed) == {"enabled"} and changed["enabled"] is False):
        validate_task(task, admin_id, service_id, check_reply_session="reply_to" in changed)
    if changed.get("enabled") is True and not was_enabled:
        task["consecutive_failures"] = 0
    if any(k in changed for k in ("schedule_type", "schedule", "enabled", "tz_offset_hours")):
        task["next_run_at"] = _compute_next_run(task, datetime.now(timezone.utc))
    if changed:
        task["revision"] = task.get("revision", 0) + 1
    _save_service_task(admin_id, service_id, task)
    _heap_upsert("service", admin_id, task_id, task.get("next_run_at"),
                 service_id=service_id)
    return task


@_serialized
def delete_service_task(admin_id: str, service_id: str, task_id: str) -> bool:
    """Delete a service task and its **entire** spawn subtree (recursive).

    Also evicts deleted ids from the run heap (see :func:`delete_task`).
    """
    victims = st.list_descendants("service", admin_id, task_id, service_id,
                                  include_root=True)
    deleted = st.delete_task_subtree("service", admin_id, task_id, service_id)
    if deleted:
        for v in victims:
            _heap_upsert("service", admin_id, v["id"], None,
                         service_id=service_id)
    return deleted


@_serialized
def get_service_task_runs(admin_id: str, service_id: str, task_id: str) -> List[Dict[str, Any]]:
    task_dir = st.task_path_for("service", admin_id, task_id, service_id)
    if not task_dir:
        if not _load_service_task(admin_id, service_id, task_id):
            return []
        task_dir = st.task_path_for("service", admin_id, task_id, service_id)
        if not task_dir:
            return []
    meta = st.load_task_meta(task_dir) or {}
    return execution.merge_runs("service", admin_id, service_id, task_id, _attach_run_steps(task_dir, meta.get("runs", [])))


def list_all_service_tasks(admin_id: str, *,
                           roots_only: bool = True) -> List[Dict[str, Any]]:
    """List tasks across all services for a given admin.

    Honors ``roots_only`` for the same reason as :func:`list_service_tasks`
    — used by the cross-service "全部" tab in the scheduler sidebar.
    """
    services_dir = os.path.join(get_user_dir(admin_id), "services")
    if not os.path.isdir(services_dir):
        return []
    all_tasks = []
    for svc_id in os.listdir(services_dir):
        all_tasks.extend(list_service_tasks(admin_id, svc_id,
                                            roots_only=roots_only))
    all_tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return all_tasks


# ── B6: Spawn helpers (used by spawn_child_task tool & v2 chain semantics) ─

@_serialized
def create_child_task(parent_ctx: TaskContext,
                      data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a child task under the currently-executing parent task.

    Wires the spawn-tree fields (parent_task_id / root_task_id / spawn_chain /
    spawn_depth) from ``parent_ctx`` and returns the persisted task dict.

    Raises ``RuntimeError`` if the parent's on-disk directory cannot be
    located — should only happen if parent was deleted mid-execution.
    """
    if execution.current():
        from app.execution.grants import Grant
        data = Grant().child(data)
    parent_dir = st.task_path_for(parent_ctx.scope, parent_ctx.uid,
                                  parent_ctx.task_id, parent_ctx.service_id)
    if not parent_dir:
        raise RuntimeError(
            f"create_child_task: parent {parent_ctx.task_id} has no on-disk dir")

    prefix = "task_" if parent_ctx.scope == "admin" else "stask_"
    child_id = prefix + uuid.uuid4().hex[:8]

    # Compose lineage: child's chain = parent's chain + parent itself
    child_spawn_chain = parent_ctx.spawn_chain + [parent_ctx.task_id]
    child_data = dict(data)
    child_data.setdefault("reply_to", parent_ctx.reply_to)
    if child_data.get("reply_to") not in (None, parent_ctx.reply_to):
        raise PermissionError("Child tasks must inherit their parent's delivery target")
    child_data.update({
        "parent_task_id": parent_ctx.task_id,
        "root_task_id": parent_ctx.root_task_id,
        "spawn_chain": child_spawn_chain,
        "spawn_depth": parent_ctx.depth + 1,
        # Inherit defaults from parent if caller didn't override
        "tz_offset_hours": child_data.get(
            "tz_offset_hours", parent_ctx.tz_offset_hours),
    })

    # Build meta then drop it onto disk under parent_dir/{child_id}/
    child_meta = _new_task_meta_v2(parent_ctx.scope, parent_ctx.uid,
                                   child_data, child_id,
                                   parent_ctx.service_id)
    child_meta["next_run_at"] = _compute_next_run(
        child_meta, datetime.now(timezone.utc))

    from app.services.spawn_limits import check_chain_quota
    quota = check_chain_quota(parent_ctx.scope, parent_ctx.uid, parent_ctx.root_task_id,
                              service_id=parent_ctx.service_id)
    if not quota.allowed:
        raise ValueError(f"派生频次超限：{quota.current}/{quota.limit}，释放时间 {quota.reset_at}")
    child_dir = st.create_child_dir(parent_dir, child_id)
    runs = child_meta.get("runs") or []
    _externalize_run_steps(child_dir, runs)
    child_meta["runs"] = runs
    st.save_task_meta(child_dir, child_meta)

    # Bump parent's children_count + invalidate cache so subsequent lookups
    # see the new child. The path cache was already cleared by create_child_dir.
    parent_meta = st.load_task_meta(parent_dir) or {}
    parent_meta["children_count"] = int(parent_meta.get("children_count", 0)) + 1
    parent_meta["descendants_count"] = int(
        parent_meta.get("descendants_count", 0)) + 1
    st.save_task_meta(parent_dir, parent_meta)

    # Bump descendants_count on all higher ancestors (keeps UI counters honest)
    for ancestor_id in parent_ctx.spawn_chain:
        a_dir = st.task_path_for(parent_ctx.scope, parent_ctx.uid,
                                 ancestor_id, parent_ctx.service_id)
        if not a_dir:
            continue
        a_meta = st.load_task_meta(a_dir)
        if not a_meta:
            continue
        a_meta["descendants_count"] = int(
            a_meta.get("descendants_count", 0)) + 1
        st.save_task_meta(a_dir, a_meta)

    _heap_upsert(parent_ctx.scope, parent_ctx.uid, child_id,
                 child_meta.get("next_run_at"),
                 service_id=parent_ctx.service_id)

    log.info("Spawned child task %s under parent %s (depth=%d, chain=%s)",
             child_id, parent_ctx.task_id, child_meta["spawn_depth"],
             "→".join(child_spawn_chain) if child_spawn_chain else "<root>")
    return child_meta


# ── B5: L3 descendants_summary propagation ───────────────────────────────

@_serialized
def _propagate_descendant_summary(scope: Literal["admin", "service"],
                                  uid: str,
                                  service_id: Optional[str],
                                  child_meta: Dict[str, Any],
                                  run_record: Dict[str, Any]) -> None:
    """Append a one-line summary of a child's run to every ancestor's
    ``descendants_summary`` field, LRU-truncated to 1500 chars total.

    Called from execute paths after a child task finishes (success OR error).
    The ancestors are derived from ``child_meta.spawn_chain``; root tasks
    (empty chain) are no-ops.

    Concurrency note: multiple descendants may write the same ancestor's
    ``_meta.json`` simultaneously.  We accept "last writer wins" semantics
    here — losing one summary line is preferable to introducing a global
    lock; full run history remains in each child's own ``runs[]``.
    """
    chain = child_meta.get("spawn_chain") or []
    if not chain:
        return  # root tasks have no ancestors

    status = run_record.get("status", "?")
    icon = "✓" if status == "success" else ("⌛" if status == "timeout" else "✗")
    finished = (run_record.get("finished_at") or "")[:16]
    output = (run_record.get("output") or "").strip().splitlines()
    snippet = output[0][:_L3_SUMMARY_LINE_PREVIEW] if output else ""
    line = (
        f"[{finished}] {icon} {child_meta.get('id')}"
        f"(d={child_meta.get('spawn_depth', '?')}): "
        f"{child_meta.get('name', '')} — {snippet}".rstrip(" — ")
    )

    for ancestor_id in chain:
        a_dir = st.task_path_for(scope, uid, ancestor_id, service_id)
        if not a_dir:
            continue
        a_meta = st.load_task_meta(a_dir)
        if not a_meta:
            continue
        prior = a_meta.get("descendants_summary") or ""
        merged = (prior + ("\n" if prior else "") + line)
        # LRU truncate by line, oldest first
        while len(merged) > _L3_SUMMARY_MAX_CHARS and "\n" in merged:
            merged = merged.split("\n", 1)[1]
        a_meta["descendants_summary"] = merged
        try:
            st.save_task_meta(a_dir, a_meta)
        except Exception:
            log.exception("L3 propagate: failed to save ancestor %s",
                          ancestor_id)


# ── Schedule helpers ──────────────────────────────────────────────────────

def _resolve_task_tz_offset(task: Dict[str, Any]) -> float:
    """Wall-clock schedules use the task's stored offset when set.

    If ``tz_offset_hours`` is absent (legacy tasks), fall back to the user's
    preferences default — **not** UTC (0), otherwise cron runs 8h late for +8 users.
    """
    raw = task.get("tz_offset_hours")
    if raw is not None:
        return float(raw)
    uid = task.get("user_id") or task.get("admin_id")
    if uid:
        from app.services.preferences import get_tz_offset

        return get_tz_offset(uid)
    return 8.0


def _compute_next_run(task: Dict[str, Any], after: datetime) -> Optional[str]:
    if not task.get("enabled"):
        return None
    stype = task.get("schedule_type", "once")
    sched = task.get("schedule", "")
    tz_offset = _resolve_task_tz_offset(task)

    if stype == "once":
        # v2: empty / "now" schedule on a once-task means "fire ASAP" — chosen
        # so spawn_child_task's default ergonomics (no schedule arg → run now)
        # work cleanly.  v1 callers that omitted the time still got None here
        # and were silently dropped, so this loosening doesn't break old data.
        #
        # "ASAP" is strictly a *creation-time* affordance.  `last_run_at` is
        # written before this is called from the execute paths, so a task that
        # has already fired reports None and the `once` contract holds.  Without
        # this check next_run_at resolves to "just now" on every cycle and the
        # heap re-fires the task forever (one agent + LLM call per iteration).
        if not sched or sched.strip().lower() == "now":
            return after.isoformat() if not task.get("last_scheduled_run_at", task.get("last_run_at")) else None
        try:
            dt = datetime.fromisoformat(sched)
            if dt.tzinfo is None:
                user_tz = timezone(timedelta(hours=tz_offset))
                dt = dt.replace(tzinfo=user_tz)
            dt_utc = dt.astimezone(timezone.utc)
            return dt_utc.isoformat() if dt_utc > after else None
        except Exception:
            return None

    elif stype == "cron":
        nxt = _next_cron(sched, after, tz_offset_hours=tz_offset)
        return nxt.isoformat() if nxt else None

    elif stype == "interval":
        try:
            seconds = int(sched)
            return (after + timedelta(seconds=seconds)).isoformat()
        except Exception:
            return None

    return None


def _apply_post_run_schedule(task: Dict[str, Any], status: str,
                             finished: datetime) -> Optional[str]:
    """Re-schedule a task after a run, with the runaway guards applied.

    Mutates ``task`` in place (``consecutive_failures`` / ``enabled`` /
    ``next_run_at``) and returns a human-readable reason when the task was
    auto-disabled, so the caller can surface it in the run's step log.

    Shared by the admin and service execute paths — keep them in sync by
    changing only this function.
    """
    if status == "success":
        task["consecutive_failures"] = 0
    else:
        task["consecutive_failures"] = int(
            task.get("consecutive_failures", 0)) + 1

    disabled_reason: Optional[str] = None
    fail_limit = max_consecutive_failures()
    if fail_limit and task["consecutive_failures"] >= fail_limit:
        task["enabled"] = False
        disabled_reason = (
            f"连续失败 {task['consecutive_failures']} 次"
            f"（上限 {fail_limit}），任务已自动禁用。"
            "修复问题后可在定时任务页重新启用。"
        )

    # Must run after the enabled flag above — a disabled task yields None.
    next_run = _compute_next_run(task, finished)

    floor_s = min_run_interval_seconds()
    if next_run and floor_s:
        epoch = _parse_next_run_epoch(next_run)
        earliest = finished + timedelta(seconds=floor_s)
        if epoch is not None and epoch < earliest.timestamp():
            log.warning(
                "Task %s: next_run_at %s is within %ds of this run — "
                "clamping to %s to avoid a hot re-fire loop",
                task.get("id"), next_run, floor_s, earliest.isoformat(),
            )
            next_run = earliest.isoformat()

    task["next_run_at"] = next_run
    return disabled_reason


# ── Task execution ────────────────────────────────────────────────────────

def _step(step_type: str, content: str, **extra) -> dict:
    """Create a log step entry."""
    entry = {
        "type": step_type,
        "content": content,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    entry.update(extra)
    return entry


_DEFAULT_READ_DIRS = ["docs", "scripts", "generated", "tasks"]
_DEFAULT_WRITE_DIRS = ["docs", "scripts", "generated", "tasks"]


def _resolve_permission_dirs(user_id: str, dir_names: List[str]) -> List[str]:
    """Resolve relative dir names to absolute paths under user filesystem root.

    Special value "*" maps to the user filesystem root itself (full access).
    """
    from app.core.security import get_user_filesystem_dir
    fs_dir = get_user_filesystem_dir(user_id)
    resolved = permission_dirs(fs_dir, dir_names)
    for path in resolved:
        os.makedirs(path, exist_ok=True)
    return resolved


async def _run_script_task(user_id: str, config: Dict[str, Any]) -> dict:
    """Returns {"output": str, "success": bool, "steps": list}."""
    if execution.current():
        raise PermissionError('Native script execution is blocked until an OS-isolated grant adapter is configured')
    from app.services.script_runner import run_script, superadmin_script_unrestricted
    from app.core.security import get_user_filesystem_dir
    fs_dir = get_user_filesystem_dir(user_id)
    scripts_dir = os.path.join(fs_dir, "scripts")
    steps: List[dict] = []

    perms = config.get("permissions", {})
    read_dirs = _resolve_permission_dirs(user_id, perms.get("read_dirs", _DEFAULT_READ_DIRS))
    write_dirs = _resolve_permission_dirs(user_id, perms.get("write_dirs", _DEFAULT_WRITE_DIRS))

    # scripts_dir must always be writable (script's cwd)
    if scripts_dir not in write_dirs:
        write_dirs.append(scripts_dir)
    if scripts_dir not in read_dirs:
        read_dirs.append(scripts_dir)

    script_path = config.get("script_path", "")
    script_args = config.get("script_args")
    steps.append(_step("start", f"启动脚本: {script_path}", args=script_args or [],
                        read_dirs=perms.get("read_dirs", _DEFAULT_READ_DIRS),
                        write_dirs=perms.get("write_dirs", _DEFAULT_WRITE_DIRS),
                        resolved_write_dirs=write_dirs,
                        scripts_dir=scripts_dir,
                        fs_dir=fs_dir))

    log.info("Script sandbox dirs — write: %s | read: %s | scripts_dir: %s | fs_dir: %s",
             write_dirs, read_dirs, scripts_dir, fs_dir)

    from app.services.venv_manager import get_user_python
    worker = asyncio.create_task(asyncio.to_thread(run_script,
        script_path=script_path,
        scripts_dir=scripts_dir,
        input_data=config.get("input_data"),
        args=script_args,
        timeout=min(config.get("timeout", 60), _TASK_TIMEOUT_S),
        allowed_read_dirs=read_dirs,
        allowed_write_dirs=write_dirs,
        unrestricted=superadmin_script_unrestricted(user_id),
        python_executable=get_user_python(user_id),
    ))
    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        # to_thread does not stop a subprocess. Drain before releasing capacity.
        await worker
        raise

    if result["error"]:
        steps.append(_step("error", result["error"]))
        return {"output": f"错误: {result['error']}", "success": False, "steps": steps}
    if result["stdout"]:
        steps.append(_step("stdout", result["stdout"]))
    if result["stderr"]:
        steps.append(_step("stderr", result["stderr"]))
    steps.append(_step("exit", f"退出码: {result['exit_code']}", exit_code=result["exit_code"]))

    out = []
    if result["stdout"]:
        out.append(result["stdout"])
    if result["stderr"]:
        out.append(f"[stderr] {result['stderr']}")
    out.append(f"退出码: {result['exit_code']}")
    text = "\n".join(out) or "（无输出）"
    return {"output": text, "success": result["exit_code"] == 0, "steps": steps}


def _read_docs(user_id: str, doc_paths) -> str:
    """Read one or more docs from the user's docs/ directory."""
    from app.core.security import get_user_filesystem_dir
    from app.core.path_security import safe_join
    fs_dir = get_user_filesystem_dir(user_id)
    docs_dir = os.path.join(fs_dir, "docs")

    if isinstance(doc_paths, str):
        doc_paths = [doc_paths]

    if execution.current():
        from app.execution.grants import Grant
        from app.storage import get_storage_service
        grant = Grant()
        parts = []
        for path in doc_paths:
            clean = 'docs/' + path.replace('\\', '/').lstrip('/')
            clean = grant.path(clean)
            parts.append(f'=== 文档: {path} ===\n' + get_storage_service().read_text(user_id, clean))
        return '\n\n'.join(parts)
    parts = []
    for dp in doc_paths:
        dp = dp.strip()
        if not dp:
            continue
        try:
            full = safe_join(docs_dir, dp.lstrip("/"))
            if os.path.isfile(full):
                with open(full, "r", encoding="utf-8") as f:
                    content = f.read()
                parts.append(f"=== 文档: {dp} ===\n{content}")
            else:
                log.warning("Doc not found: %s", dp)
        except Exception as e:
            log.warning("Failed to read doc %s: %s", dp, e)
    return "\n\n".join(parts)


def _resolve_wechat_client(reply_to: Optional[Dict[str, Any]], *, owner_id: str,
                           owner_service_id: Optional[str] = None):
    """Resolve WeChat client, to_user, ctx_token from reply_to config.

    Returns (client, to_user, ctx_token) or (None, None, None).
    """
    if not reply_to or reply_to.get("channel") != "wechat":
        return None, None, None

    validate_reply(reply_to, owner_id, owner_service_id)
    service_id = owner_service_id
    try:
        if service_id:
            from app.channels.wechat.session_manager import get_session_manager
            mgr = get_session_manager()
            session = mgr.get_session(reply_to.get("session_id", ""))
            if not session:
                return None, None, None
            client = mgr.get_client(session.session_id)
            return client, session.from_user_id, session.context_token
        else:
            from app.channels.wechat.admin_router import _get_session as _get_admin_session
            admin_sess = _get_admin_session(owner_id)
            if not admin_sess or not admin_sess.get("connected"):
                return None, None, None
            if reply_to.get("conversation_id") != admin_sess.get("conversation_id"):
                raise PermissionError("Admin WeChat conversation no longer matches reply_to")
            return admin_sess.get("client"), admin_sess.get("from_user_id", ""), admin_sess.get("context_token", "")
    except Exception:
        log.exception("Failed to resolve WeChat client from reply_to")
        return None, None, None


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_TTS_CONVERTIBLE = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"}


async def _send_media_for_task(user_id: str, client, to_user: str, ctx_token: str,
                               media_path: str, *, service_context=None):
    """Send a media file to WeChat. Works for both admin and service contexts."""
    from app.storage import get_storage_service
    storage = get_storage_service()

    clean = media_path.lstrip("/").replace("\\", "/")

    if service_context:
        admin_id, service_id, conv_id = service_context
        if clean.startswith("generated/"):
            clean = clean[len("generated/"):]
        try:
            file_bytes = storage.read_consumer_bytes(admin_id, service_id, conv_id, clean)
        except FileNotFoundError:
            log.warning("Scheduled task media not found (consumer): %s", media_path)
            return
    else:
        if not clean.startswith("generated/"):
            clean = f"generated/{clean}"
        rel_path = f"/{clean}"
        if not storage.is_file(user_id, rel_path):
            log.warning("Scheduled task media not found (admin): %s", rel_path)
            return
        file_bytes = storage.read_bytes(user_id, rel_path)

    filename = os.path.basename(clean)
    ext = os.path.splitext(filename)[1].lower()
    in_audio_dir = "/audio/" in clean

    if ext in _IMAGE_EXTS:
        await client.send_image(to_user, file_bytes, ctx_token, filename)
    elif ext in _VIDEO_EXTS:
        await client.send_video(to_user, file_bytes, ctx_token)
    elif ext == ".silk":
        await client.send_voice(to_user, file_bytes, ctx_token)
    elif in_audio_dir and ext in _TTS_CONVERTIBLE:
        await client.send_file(to_user, file_bytes, filename, ctx_token)
    else:
        await client.send_file(to_user, file_bytes, filename, ctx_token)
    log.info("Scheduled task sent media: %s (%d bytes)", filename, len(file_bytes))


async def _handle_send_message_tool(content: str, client, to_user: str, ctx_token: str,
                                    user_id: str, steps: List[dict], *,
                                    service_context=None,
                                    delivered_parts: Optional[List[str]] = None):
    """Intercept send_message ToolMessage and send to WeChat.

    `delivered_parts`: when provided, append a user-visible representation of
    what was actually delivered (clean text or `[图片]`/`[语音]`/… placeholder
    for media). Used by `_run_agent_task` / `_run_service_agent_task` to build
    the scheduled_task block's `result` from real outbound content instead of
    the agent's raw ReAct monologue.
    """
    if not to_user:
        log.warning("Cannot send WeChat message: to_user (from_user_id) is empty — "
                    "no user has sent a message to this session yet")
        steps.append(_step("wechat_error",
                           "微信投递失败：目标用户为空（from_user_id 未设置，"
                           "可能用户还未发送过消息）"))
        return
    if not ctx_token:
        log.warning("Cannot send WeChat message: context_token is empty — "
                    "session may be stale after restart")
        steps.append(_step("wechat_warning",
                           "context_token 为空，消息可能无法送达"))
    try:
        payload = json.loads(content)
        text = payload.get("text", "")
        media = payload.get("media")

        if media:
            await _send_media_for_task(
                user_id, client, to_user, ctx_token, media,
                service_context=service_context,
            )
            steps.append(_step("wechat_send", f"已发送媒体: {media}"))
            if delivered_parts is not None:
                # Record a friendly placeholder so the persisted result
                # reflects that media was delivered, even when no text.
                media_label = media if isinstance(media, str) else "媒体"
                delivered_parts.append(f"[已推送媒体: {media_label}]")
        if text:
            await client.send_text(to_user, text, ctx_token)
            steps.append(_step("wechat_send", f"已发送消息: {text[:100]}"))
            if delivered_parts is not None:
                delivered_parts.append(text)
            log.info("Scheduled task sent message: %s", text[:50])
    except Exception:
        log.exception("Failed to send scheduled task message via WeChat")
        steps.append(_step("wechat_error", "发送微信消息失败"))


async def _run_agent_loop(agent, input_payload, agent_config, steps: List[dict],
                          output_parts: List[str], *,
                          wechat_client=None, wechat_to_user: str = "",
                          wechat_ctx_token: str = "", user_id: str = "",
                          service_context=None,
                          delivered_parts: Optional[List[str]] = None):
    """Shared astream loop with send_message interception for both admin & service tasks.

    `delivered_parts`: optional list collecting user-visible content actually
    pushed via send_message (text + media placeholders). Callers use it to
    build a clean scheduled_task `result` instead of the raw AI monologue.
    """
    from langgraph.types import Command
    max_loops = 20

    for loop_i in range(max_loops):
        steps.append(_step("loop", f"Agent 执行循环 #{loop_i + 1}"))
        async for event in agent.astream(input_payload, config=agent_config):
            if execution.current():
                from app.execution.grants import Grant
                Grant().policies()
            if not isinstance(event, dict):
                continue
            for node_name, node_output in event.items():
                if not isinstance(node_output, dict):
                    continue
                msgs = node_output.get("messages")
                if not isinstance(msgs, (list, tuple)):
                    continue
                for msg in msgs:
                    if not hasattr(msg, "type"):
                        continue

                    if msg.type == "ai" and hasattr(msg, "tool_calls") and msg.tool_calls:
                        for tc in msg.tool_calls:
                            tc_name = tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                            tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                            args_preview = json.dumps(tc_args, ensure_ascii=False, default=str)
                            if len(args_preview) > 500:
                                args_preview = args_preview[:500] + "…"
                            steps.append(_step("tool_call", f"调用工具: {tc_name}",
                                               tool=tc_name, args_preview=args_preview, node=node_name))

                    if msg.type == "tool":
                        tool_name = getattr(msg, "name", "")
                        tool_content = ""
                        if hasattr(msg, "content"):
                            if isinstance(msg.content, str):
                                tool_content = msg.content
                            elif isinstance(msg.content, list):
                                parts = []
                                for p in msg.content:
                                    if isinstance(p, dict) and p.get("type") == "text":
                                        parts.append(p["text"])
                                    elif isinstance(p, str):
                                        parts.append(p)
                                tool_content = "\n".join(parts)

                        if tool_name == "send_message" and execution.current():
                            payload = json.loads(tool_content)
                            execution.current().collect_message(payload)
                            if delivered_parts is not None and payload.get('text'):
                                delivered_parts.append(str(payload['text']))
                            steps.append(_step('delivery_queued', '结果将在运行提交后投递'))
                        elif tool_name == "send_message" and wechat_client:
                            await _handle_send_message_tool(
                                tool_content, wechat_client, wechat_to_user,
                                wechat_ctx_token, user_id, steps,
                                service_context=service_context,
                                delivered_parts=delivered_parts,
                            )

                        if len(tool_content) > 800:
                            tool_content = tool_content[:800] + "…"
                        steps.append(_step("tool_result", f"工具返回: {tool_name}",
                                           tool=tool_name, result_preview=tool_content, node=node_name))

                    if msg.type == "ai":
                        if not hasattr(msg, "content") or not msg.content:
                            continue
                        content = msg.content
                        text_parts = []
                        if isinstance(content, str):
                            text_parts.append(content)
                        elif isinstance(content, list):
                            for part in content:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    text_parts.append(part["text"])
                                elif isinstance(part, str):
                                    text_parts.append(part)
                        if text_parts:
                            combined = "\n".join(text_parts)
                            output_parts.append(combined)
                            preview = combined if len(combined) <= 500 else combined[:500] + "…"
                            steps.append(_step("ai_message", preview, node=node_name))

        state = await agent.aget_state(agent_config)
        has_interrupt = False
        if state and hasattr(state, "tasks") and state.tasks:
            for task in state.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    has_interrupt = True
                    break
        if not has_interrupt:
            break

        if execution.current():
            raise PermissionError('Scheduled execution requires a scoped grant; unexpected approval is not auto-approved')
        decisions = []
        action_names = []
        for task in state.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                for intr in task.interrupts:
                    val = intr.value if hasattr(intr, "value") else {}
                    if isinstance(val, dict) and "action_requests" in val:
                        for ar in val["action_requests"]:
                            # langchain HITL middleware expects {"type": "approve"} after upgrade
                            decisions.append({"type": "approve"})
                            action_desc = str(ar)[:200] if not isinstance(ar, dict) else json.dumps(ar, ensure_ascii=False, default=str)[:200]
                            action_names.append(action_desc)
        if not decisions:
            break
        steps.append(_step("auto_approve", f"自动审批 {len(decisions)} 个操作", actions=action_names))
        input_payload = Command(resume={"decisions": decisions})
        log.info("Auto-approving %d file operations for scheduled task (loop %d)", len(decisions), loop_i + 1)


async def _delete_temporary_checkpoint(thread_id: str):
    from app.services.agent import _checkpointer
    if _checkpointer is not None:
        try:
            await _checkpointer.adelete_thread(thread_id)
        except Exception:
            log.exception("Temporary scheduled checkpoint cleanup failed: %s", thread_id)


async def _run_cli_agent_task(user_id: str, config: dict, full_prompt: str, steps: list) -> dict:
    """Run one CLI turn under the current durable admin task lease."""
    from app.execution.grants import Grant
    from app.runtime.chat import choice as runtime_choice
    from app.runtime.manager import get_runtime
    from app.runtime.business_tools import scheduler_specifications
    from app.runtime.service import MAX_CHAT_MESSAGE_CHARS
    from app.runtime.store import TERMINAL as RUNTIME_TERMINAL

    durable = execution.current()
    if not durable:
        raise PermissionError('CLI 定时任务需要持久化执行授权')
    grant = Grant(durable)
    grant.policies()
    if len(full_prompt) > MAX_CHAT_MESSAGE_CHARS:
        reason = (f'CLI 定时任务输入共 {len(full_prompt)} 字符，超过 {MAX_CHAT_MESSAGE_CHARS} 字符上限。'
                  '请缩短任务指令或参考文档，再重新运行。')
        steps.append(_step('cli_failed', reason))
        return {'output': reason, 'success': False, 'steps': steps}
    choice = config['runtime_choice']
    saved = config.get('runtime_binding')
    if not isinstance(saved, dict) or saved.get('runtime') != choice['runtime']:
        raise PermissionError('CLI 定时任务缺少可信的引擎绑定，请重新保存任务')
    requested = {**choice, 'image_mode': 'native' if 'image' in grant.saved['capabilities'] else 'off'}
    if runtime_choice(user_id, requested) != saved:
        raise PermissionError('CLI 定时任务引擎授权已经改变，请重新保存任务')
    scope = {'run_id': durable.run['id'], 'task_id': grant.tid,
             'revision': grant.snapshot['revision'],
             'web': 'web' in grant.saved['capabilities'],
             'image': 'image' in grant.saved['capabilities']}
    binding = {**saved, 'scheduler_scope': scope}
    runtime = get_runtime()
    runtime.runs.authorize(user_id, binding)
    instructions = ('你正在执行 OpenJellyfish 管理员定时任务。仅使用本会话注册的 jellyfish_scheduled_* 工具访问授权文件。'
                    '可使用获授权的网页搜索和生图。不要使用原生命令、文件工具、其他 MCP 或插件。'
                    '直接给出最终结果；系统会在任务提交后把最终文本送到指定会话。')
    session = runtime.runs.create_session(user_id, binding, instructions=instructions,
                                          dynamic_tools=scheduler_specifications())
    session['instructions_version'] = 3
    runtime.store.put('session', session)
    run = None
    try:
        run = runtime.runs.enqueue(user_id, session['id'], f"scheduled:{durable.run['id']}", full_prompt, yolo=False)
        steps.append(_step('cli_started', f"{saved['runtime']} 任务已提交", runtime_run_id=run['id']))
        while True:
            grant.policies()
            runtime.runs.authorize(user_id, binding)
            current = runtime.store.get('run', run['id'])
            if current and current['status'] in RUNTIME_TERMINAL:
                break
            await asyncio.sleep(.25)
        grant.policies()
        if current['status'] != 'completed':
            steps.append(_step('cli_failed', current.get('error') or current['status']))
            return {'output': current.get('error') or 'CLI 定时任务未完成', 'success': False, 'steps': steps}
        output = current.get('output') or ''
        for artifact in current.get('artifacts') or []:
            output += f"\n\n<<FILE:{artifact['path']}>>"
        steps.append(_step('finish', f"{saved['runtime']} 任务执行完成"))
        return {'output': output, 'success': True, 'steps': steps}
    finally:
        if run:
            current = runtime.store.get('run', run['id'])
            if current and current['status'] not in RUNTIME_TERMINAL:
                stop = asyncio.create_task(runtime.runs.cancel(user_id, run['id']))
                try:
                    await asyncio.shield(stop)
                except asyncio.CancelledError:
                    await asyncio.shield(stop)
                    raise


async def _run_agent_task(user_id: str, config: Dict[str, Any],
                          reply_to: Optional[Dict[str, Any]] = None,
                          task_meta: Optional[Dict[str, Any]] = None) -> dict:
    """Run an admin agent task using the full user agent with humanchat.

    `task_meta` carries {task_id, task_name, schedule_type, scheduled_at}; when
    provided and the task has a target conversation, the final output is
    persisted as a `scheduled_task` tool block (rendered as a dedicated card
    in chat history) instead of a plain text bubble. See `_persist_scheduled_task_result`.

    Returns {"output": str, "success": bool, "steps": list}.
    """
    if execution.current():
        from app.execution.grants import Grant
        Grant().policies()
    cli = isinstance(config.get('runtime_choice'), dict) and config['runtime_choice'].get('runtime') in ('codex', 'cursor')
    if not cli:
        from app.services.agent import create_user_agent, _get_default_model
    from app.services.memory_tools import load_recent_admin_messages
    prompt_text = config.get("prompt", "")
    doc_path = config.get("doc_path", "")
    model = (config.get('runtime_binding') or {}).get('model', '') if cli else config.get("model", "")
    capabilities = config.get("capabilities", [])
    perms = config.get("permissions", {})
    steps: List[dict] = []

    steps.append(_step("start", f"Agent 任务启动 (model={model or 'default'})",
                        prompt=prompt_text[:200],
                        doc_paths=doc_path if isinstance(doc_path, list) else ([doc_path] if doc_path else []),
                        capabilities=capabilities,
                        permissions=perms or None))

    doc_content = _read_docs(user_id, doc_path) if doc_path else ""
    if doc_content:
        steps.append(_step("docs_loaded", f"已加载 {len(doc_content)} 字符的文档内容"))
        full_prompt = (
            "你需要根据以下文档/技能说明书来执行任务。"
            "请仔细阅读文档内容，按照其中描述的步骤和要求逐步完成所有任务。\n\n"
            f"{doc_content}\n\n"
            "---\n\n"
        )
        if prompt_text:
            full_prompt += f"执行指令：{prompt_text}"
        else:
            full_prompt += "请按照上述文档内容完成所有描述的任务。"
    else:
        full_prompt = prompt_text

    # Inject short-term memory from conversation history
    conv_id = reply_to.get("conversation_id", "") if reply_to else ""
    if conv_id:
        recent = load_recent_admin_messages(user_id, conv_id)
        if recent:
            full_prompt = (
                f"[对话上下文 - 最近消息]\n---\n{recent}\n---\n\n"
                f"{full_prompt}"
            )

    if cli:
        full_prompt += '\n\n---\n这是定时任务。请直接给出最终结果；系统会在授权检查和提交后送达。'
    else:
        full_prompt += (
            "\n\n---\n"
            "[重要] 这是一个定时任务。你的直接文本输出用户看不到。"
            "任务完成后，你必须使用 `send_message` 工具将结果发送给用户，否则用户将收不到任何信息。"
        )

    if not model and not cli:
        # 传 user_id 以尊重用户在设置页选的默认 LLM（capability_defaults.llm）；
        # 不传会退回全局 agent_config.json 的旧默认（如 sonnet 4.5）。
        model = _get_default_model(user_id)

    if not cli and "humanchat" not in capabilities:
        capabilities = list(capabilities) + ["humanchat"]

    if cli:
        agent = None
    elif execution.current():
        from app.execution.agent import create_scheduled_agent
        agent = create_scheduled_agent(model)
    else:
        agent = create_user_agent(user_id, model=model, capabilities=capabilities,
                                  service_message_enabled=False)
    thread_id = f"scheduled-{execution.current().run['id']}" if cli and execution.current() else f"scheduled-{uuid.uuid4().hex[:8]}"
    agent_config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": build_usage_callbacks(
            user_id, channel="scheduler", conv_id=conv_id, model_hint=model or ""
        ),
    }
    output_parts: List[str] = []
    delivered_parts: List[str] = []

    wechat_client, wechat_to_user, wechat_ctx_token = (None, None, None) if execution.current() else _resolve_wechat_client(reply_to, owner_id=user_id)
    if wechat_client:
        steps.append(_step("wechat_connected", "已连接微信推送通道"))
    elif not execution.current() and reply_to and reply_to.get("channel") == "wechat":
        log.warning("Admin task %s: reply_to specifies wechat but client not resolved "
                    "(admin may be disconnected)", "")
        steps.append(_step("wechat_warning",
                           "微信推送通道不可用（管理员可能已断开连接），"
                           "send_message 将不会发送到微信"))

    # ── workspace lock: acquire the task's declared write regions ──
    # Scheduled admin tasks share the admin's filesystem with interactive chats
    # and other tasks. Acquire fail-fast with a short wait; if still contended,
    # skip this run (it will fire again on the next schedule tick).
    from app.services import workspace_lock as wl
    _task_label = (task_meta or {}).get("task_name") or "定时任务"
    _write_dirs = perms.get("write_dirs") or ["docs", "scripts", "generated", "tasks"]
    if execution.current():
        _write_dirs = execution.current().run['snapshot']['execution_grant']['write_dirs']
    _regions = ['/' if str(d).strip('/') == '*' else '/' + str(d).strip('/') for d in _write_dirs]
    wl.register_process(thread_id, user_id, kind="scheduled", label=_task_label)
    _wl_tokens = wl.set_context(thread_id, user_id)
    try:
        _acq = wl.try_acquire(thread_id, _regions, ttl=2100)
        if not _acq.ok:
            for _ in range(10):  # up to ~30s grace
                await asyncio.sleep(3)
                _acq = wl.try_acquire(thread_id, _regions, ttl=2100)
                if _acq.ok:
                    break
        if not _acq.ok:
            detail = "；".join(f"{p}←「{lbl}」" for p, _o, lbl in _acq.conflicts)
            steps.append(_step("workspace_busy", f"工作区被占用，本次运行跳过：{detail}"))
            return {"output": f"工作区被占用，本次运行跳过（{detail}）", "success": False, "deferred": True, "steps": steps}
        steps.append(_step("workspace_locked", f"已锁定工作区写权限：{_acq.granted}"))

        if cli:
            return await _run_cli_agent_task(user_id, config, full_prompt, steps)

        input_payload = {"messages": [{"role": "user", "content": full_prompt}]}
        await _run_agent_loop(
            agent, input_payload, agent_config, steps, output_parts,
            wechat_client=wechat_client, wechat_to_user=wechat_to_user or "",
            wechat_ctx_token=wechat_ctx_token or "", user_id=user_id,
            delivered_parts=delivered_parts,
        )
        steps.append(_step("finish", "Agent 执行完成"))
    except asyncio.TimeoutError:
        steps.append(_step("error", "任务超时"))
        await _safe_persist_admin_failure(user_id, conv_id, task_meta, "任务超时（>30min）")
        return {"output": "任务超时", "success": False, "steps": steps}
    except PermissionError:
        raise
    except Exception as e:
        steps.append(_step("error", f"Agent 执行失败: {e}"))
        await _safe_persist_admin_failure(user_id, conv_id, task_meta, str(e))
        return {"output": f"Agent 执行失败: {e}", "success": False, "steps": steps}
    finally:
        # Release the workspace lock as soon as agent execution ends (before
        # the bookkeeping/persist tail) so queued tasks can proceed.
        wl.reset_context(_wl_tokens)
        wl.unregister_process(thread_id)
        await _delete_temporary_checkpoint(thread_id)

    text = _compose_clean_task_output(delivered_parts, output_parts)
    # Stash the raw ReAct monologue in steps for debugging, but keep it OUT of
    # the persisted scheduled_task block / L2 injection — see
    # _compose_clean_task_output for rationale.
    if delivered_parts and output_parts:
        raw_combined = "\n".join(output_parts)
        steps.append(_step(
            "raw_trace",
            f"Agent 原始输出（{len(output_parts)} 段，已折叠；用户实际收到的是 send_message 内容）",
            raw_text=raw_combined[:4000],
        ))

    # Persist task output to conversation history as a scheduled_task tool block,
    # so the frontend renders it via ScheduledTaskCard (admin) / friendly variant
    # (service-chat) and can be visually distinguished from spontaneous agent replies.
    if conv_id and not execution.current():
        try:
            from app.services.conversations import save_message
            block = _build_scheduled_task_block(task_meta, text, success=True)
            save_message(user_id, conv_id, "assistant", text,
                         blocks=[block])
        except Exception:
            log.exception("Failed to persist admin task output to conv %s", conv_id)

        # Phase 2 (L2): also inject into the main conversation's LangGraph state
        # so the agent's NEXT user turn naturally remembers the task ran. The
        # injection is queued and drained when the thread is idle (see
        # scheduled_inject.enqueue_admin); failure here is non-fatal — L1 above
        # already ensures the task is visible to the user.
        try:
            from app.services import scheduled_inject
            await scheduled_inject.enqueue_admin(
                user_id=user_id, conv_id=conv_id,
                task_meta=task_meta or {}, output=text, success=True,
            )
        except Exception:
            log.exception("Failed to enqueue admin task L2 injection for conv %s",
                          conv_id)

    return {"output": text, "success": True, "steps": steps}


async def _run_service_agent_task(admin_id: str, service_id: str, conversation_id: str,
                                  config: Dict[str, Any],
                                  reply_to: Optional[Dict[str, Any]] = None,
                                  task_meta: Optional[Dict[str, Any]] = None) -> dict:
    """Run a service agent task using the full consumer agent with humanchat.

    If reply_to points to a WeChat session, send_message tool calls are
    intercepted and delivered to the user in real time (text + media).

    Returns {"output": str, "success": bool, "steps": list}.
    """
    if execution.current():
        from app.execution.grants import Grant
        Grant().policies()
    from app.services.consumer_agent import create_consumer_agent
    from app.services.memory_tools import load_recent_consumer_messages
    prompt_text = config.get("prompt", "")
    doc_path = config.get("doc_path", "")
    steps: List[dict] = []

    steps.append(_step("start", f"Service Agent 任务启动",
                        service_id=service_id,
                        prompt=prompt_text[:200],
                        doc_paths=doc_path if isinstance(doc_path, list) else ([doc_path] if doc_path else [])))

    doc_content = _read_docs(admin_id, service_doc_paths(admin_id, service_id, doc_path)) if doc_path else ""

    # Build task instruction with admin source tagging
    task_instruction = ""
    if doc_content:
        steps.append(_step("docs_loaded", f"已加载 {len(doc_content)} 字符的文档内容"))
        task_instruction = (
            "你需要根据以下文档/技能说明书来执行任务。"
            "请仔细阅读文档内容，按照其中描述的步骤和要求逐步完成所有任务。\n\n"
            f"{doc_content}\n\n---\n\n"
        )
        if prompt_text:
            task_instruction += f"执行指令：{prompt_text}"
        else:
            task_instruction += "请按照上述文档内容完成所有描述的任务。"
    else:
        task_instruction = prompt_text

    # Inject short-term memory from conversation history
    recent_ctx = load_recent_consumer_messages(
        admin_id, service_id, conversation_id)

    # Tag the message source so the agent knows this is from admin
    full_prompt = (
        "[定时任务指令]\n"
        "以下是此前保存的定时任务，受当前服务发布权限约束，不代表新增管理员授权。\n\n"
    )
    if recent_ctx:
        full_prompt += f"[对话上下文 - 最近消息]\n---\n{recent_ctx}\n---\n\n"
    full_prompt += (
        f"任务指令：{task_instruction}\n\n"
        "---\n"
        "[重要] 这是一个定时任务。你的直接文本输出用户看不到。"
        "请结合服务规则与对话上下文判断是否需要通知用户；需要通知时使用 `send_message`。"
        "不需要通知时不要调用它。执行说明和错误只保留给管理员。\n"
        "如需向管理员反馈，请使用 contact_admin 工具。"
    )

    extra_caps = ["humanchat"] if reply_to and reply_to.get("channel") == "wechat" else None
    task_model = config.get("model") or None
    if task_model:
        steps.append(_step("model", f"使用指定模型: {task_model}"))
    if execution.current():
        from app.execution.agent import create_scheduled_agent
        agent = create_scheduled_agent(task_model)
    else:
        agent = create_consumer_agent(admin_id, service_id, conversation_id,
                                     extra_capabilities=extra_caps, channel='scheduler', model_override=task_model)
    thread_id = f"svc-scheduled-{uuid.uuid4().hex[:8]}"
    agent_config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": build_usage_callbacks(
            admin_id, service_id=service_id, channel="scheduler",
            conv_id=conversation_id, model_hint=task_model or "",
        ),
    }
    output_parts: List[str] = []
    delivered_parts: List[str] = []

    wechat_client, wechat_to_user, wechat_ctx_token = (None, None, None) if execution.current() else _resolve_wechat_client(reply_to, owner_id=admin_id, owner_service_id=service_id)
    if wechat_client:
        steps.append(_step("wechat_connected", "已连接微信推送通道"))
    elif not execution.current() and reply_to and reply_to.get("channel") == "wechat":
        log.warning("Service task (svc=%s): reply_to specifies wechat but client not resolved "
                    "(session may be expired)", service_id)
        steps.append(_step("wechat_warning",
                           "微信推送通道不可用（会话可能已过期），"
                           "send_message 将不会发送到微信"))

    service_context = (admin_id, service_id, conversation_id)

    try:
        input_payload = {"messages": [{"role": "user", "content": full_prompt}]}
        await _run_agent_loop(
            agent, input_payload, agent_config, steps, output_parts,
            wechat_client=wechat_client, wechat_to_user=wechat_to_user or "",
            wechat_ctx_token=wechat_ctx_token or "", user_id=admin_id,
            service_context=service_context,
            delivered_parts=delivered_parts,
        )
        steps.append(_step("finish", "Service Agent 执行完成"))
    except asyncio.TimeoutError:
        steps.append(_step("error", "任务超时"))
        await _safe_persist_service_failure(admin_id, service_id, conversation_id,
                                            task_meta, "任务超时（>30min）")
        return {"output": "任务超时", "success": False, "steps": steps}
    except PermissionError:
        raise
    except Exception as e:
        steps.append(_step("error", f"Service Agent 执行失败: {e}"))
        await _safe_persist_service_failure(admin_id, service_id, conversation_id,
                                            task_meta, str(e))
        return {"output": f"Service Agent 执行失败: {e}", "success": False, "steps": steps}

    finally:
        await _delete_temporary_checkpoint(thread_id)

    text = _compose_clean_task_output(delivered_parts, output_parts)
    if delivered_parts and output_parts:
        raw_combined = "\n".join(output_parts)
        steps.append(_step(
            "raw_trace",
            f"Agent 原始输出（{len(output_parts)} 段，已折叠；用户实际收到的是 send_message 内容）",
            raw_text=raw_combined[:4000],
        ))

    if execution.current():
        return {"output": text, "success": True, "steps": steps}

    # Persist task output to consumer conversation history as a scheduled_task tool
    # block. Service-chat renders this via the friendly ScheduledTaskCard variant
    # which hides task_id / internal prompt from end users (see ServiceChatApp).
    try:
        from app.services.published import save_consumer_message
        block = _build_scheduled_task_block(task_meta, text, success=True)
        save_consumer_message(admin_id, service_id, conversation_id,
                              "assistant", text,
                              blocks=[block])
    except Exception:
        log.exception("Failed to persist service task output to conv %s", conversation_id)

    # Phase 2 (L2): inject into the consumer conversation's LangGraph state so
    # the agent remembers the task on its next user turn. Queued + drained when
    # the thread is idle (see scheduled_inject.enqueue_service).
    try:
        from app.services import scheduled_inject
        await scheduled_inject.enqueue_service(
            admin_id=admin_id, service_id=service_id, conv_id=conversation_id,
            task_meta=task_meta or {}, output=text, success=True,
        )
    except Exception:
        log.exception("Failed to enqueue service task L2 injection for conv %s",
                      conversation_id)

    return {"output": text, "success": True, "steps": steps}



def _build_task_context_from_meta(scope: Literal["admin", "service"],
                                  uid: str,
                                  task: Dict[str, Any],
                                  service_id: Optional[str] = None
                                  ) -> TaskContext:
    """Build a TaskContext from a freshly-loaded task meta dict.

    Pulls spawn lineage from v2 fields (with safe defaults for legacy/just-
    migrated tasks that may have missing keys).
    """
    cfg = task.get("task_config") or {}
    return TaskContext(
        scope=scope,
        uid=uid,
        service_id=service_id,
        task_id=task["id"],
        root_task_id=task.get("root_task_id") or task["id"],
        parent_task_id=task.get("parent_task_id"),
        spawn_chain=list(task.get("spawn_chain") or []),
        depth=int(task.get("spawn_depth", 0)),
        reply_to=task.get("reply_to"),
        permissions=cfg.get("permissions"),
        capabilities=list(cfg.get("capabilities") or []),
        tz_offset_hours=_resolve_task_tz_offset(task),
        model=cfg.get("model") or None,
    )


def _finalize_execution(scope, uid, sid, tid, revision, manual, record, task_meta):
    durable = execution.current()
    with _state_lock:
        task = execution.load_task(scope, uid, tid, sid)
        snapshot_config = (durable.run['snapshot'].get('task_config') or {}) if durable else {}
        snapshot_choice = snapshot_config.get('runtime_choice')
        cli_snapshot = (durable.run['snapshot'] if durable and scope == 'admin' and
                        isinstance(snapshot_choice, dict) and snapshot_choice.get('runtime') in ('codex', 'cursor')
                        else None)
        suppress_delivery = False
        if cli_snapshot:
            suppress_delivery = (not task or (not manual and not task.get('enabled')) or
                                 task.get('revision') != cli_snapshot.get('revision') or
                                 task.get('reply_to') != cli_snapshot.get('reply_to') or
                                 (task.get('task_config') or {}).get('runtime_binding') !=
                                 (cli_snapshot.get('task_config') or {}).get('runtime_binding'))
            if not suppress_delivery:
                from app.execution.grants import Grant
                try:
                    Grant(durable).policies()
                    from app.runtime.manager import get_runtime
                    get_runtime().profiles.authorize(uid, snapshot_config['runtime_binding'])
                except Exception:
                    suppress_delivery = True
            if suppress_delivery:
                record['status'] = 'blocked'
                record['steps'].append(_step('permission_denied', 'CLI 定时任务在结果提交前失去授权；结果未投递'))
        cursor = None
        if task:
            finished = datetime.fromisoformat(record['finished_at'])
            status = record['status']
            task['run_count'] = task.get('run_count', len(task.get('runs', []))) + 1
            task.setdefault('last_scheduled_run_at', task.get('last_run_at'))
            task['last_run_at'] = record['started_at']
            if not manual and status != 'deferred':
                task['last_scheduled_run_at'] = record['started_at']
            if status == 'blocked' and task.get('revision', 0) == revision:
                task.update(enabled=False, next_run_at=None)
            if not manual and task.get('revision', 0) == revision:
                if status == 'deferred' and task.get('enabled'):
                    task['next_run_at'] = (finished + timedelta(seconds=60)).isoformat()
                elif status == 'cancelled':
                    task.update(enabled=False, next_run_at=None)
                else:
                    reason = _apply_post_run_schedule(task, status, finished)
                    if reason:
                        record['steps'].append(_step('error', reason))
            fields = ('run_count', 'last_run_at', 'last_scheduled_run_at', 'next_run_at', 'enabled', 'consecutive_failures')
            cursor = (task.get('revision', 0), {k: task[k] for k in fields if k in task})
        if durable:
            durable.store.finish(durable.run['id'], durable.run['token'], record, cursor,
                                 () if suppress_delivery else durable.deliveries(record, task_meta))
        # This compatibility export is not the commit point. Failure must never rerun the executor.
        if task:
            try:
                task['runs'] = (task.get('runs', []) + [record])[-_MAX_RUNS_STORED:]
                if scope == 'admin':
                    _save_task(uid, task)
                else:
                    _save_service_task(uid, sid, task)
                _propagate_descendant_summary(scope, uid, sid, task, record)
            except Exception:
                if not durable:
                    raise
                log.exception('Run committed; task history export will be recovered from ledger')
            _heap_upsert(scope, uid, tid, task.get('next_run_at'), sid)


async def _execute_task(user_id: str, task_id: str, *, manual: bool = False) -> None:
    durable = execution.current()
    task = durable.run["snapshot"] if durable else _load_task(user_id, task_id)
    if not task or _execution_disabled():
        return
    revision = task.get("revision", 0)

    run_id = durable.run["id"] if durable else "run_" + uuid.uuid4().hex[:6]
    started = datetime.now(timezone.utc)
    log.info("Executing task %s (run %s)", task_id, run_id)

    reply_to = task.get("reply_to")
    status = "success"
    output = ""
    steps: List[dict] = []
    task_meta = {
        "task_id": task.get("id"),
        "task_name": task.get("name") or task.get("id"),
        "schedule_type": task.get("schedule_type"),
        "scheduled_at": started.isoformat(),
        "scope": "admin",
        "spawn_depth": task.get("spawn_depth", 0),
        "parent_task_id": task.get("parent_task_id"),
        "root_task_id": task.get("root_task_id") or task.get("id"),
    }

    # B4: bind execution context so spawn_child_task / L3 / observability
    # hooks can read it from anywhere down the call stack.
    ctx = _build_task_context_from_meta("admin", user_id, task)
    ctx_token = _current_task_var.set(ctx)
    try:
        validate_task(task, user_id)
        ttype = task.get("task_type", "script")
        cfg = task.get("task_config", {})
        if ttype == "script":
            result = await asyncio.wait_for(
                _run_script_task(user_id, cfg), timeout=_TASK_TIMEOUT_S
            )
        elif ttype == "agent":
            result = await asyncio.wait_for(
                _run_agent_task(user_id, cfg, reply_to=reply_to,
                                task_meta=task_meta),
                timeout=_TASK_TIMEOUT_S,
            )
        else:
            result = {"output": f"未知任务类型: {ttype}", "success": False, "steps": []}

        output = result["output"]
        steps = result.get("steps", [])
        if result.get("deferred"):
            status = "deferred"
        elif not result["success"]:
            status = "error"
    except asyncio.CancelledError:
        output = "任务已取消"
        status = "cancelled"
    except asyncio.TimeoutError:
        output = f"任务超时（>{_TASK_TIMEOUT_S}s）"
        status = "timeout"
        steps.append(_step("error", output))
    except PermissionError as e:
        output = str(e)
        status = "blocked"
        steps.append(_step("permission_denied", output))
    except Exception as e:
        output = str(e)
        status = "error"
        steps.append(_step("error", output))
        log.exception("Task %s run %s failed", task_id, run_id)
    finally:
        # ⚠️ MUST reset — see C3 in design doc.  ContextVar leaks across tasks
        # if not reset and would cause spawn_child_task to attribute children
        # to the wrong parent on the next scheduled execution in this loop.
        _current_task_var.reset(ctx_token)

    finished = datetime.now(timezone.utc)
    run_record = {
        "run_id": run_id,
        "trigger": "manual" if manual else "scheduled",
        "task_revision": revision,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "status": status,
        "output": output if durable else output[:4000],
        "steps": steps,
    }
    log.info("Task %s run %s finished: %s", task_id, run_id, status)

    _finalize_execution("admin", user_id, None, task_id, revision, manual, run_record, task_meta)


async def _execute_service_task(admin_id: str, service_id: str, task_id: str, *, manual: bool = False) -> None:
    """Execute a service-scoped scheduled task."""
    durable = execution.current()
    task = durable.run["snapshot"] if durable else _load_service_task(admin_id, service_id, task_id)
    if not task or _execution_disabled():
        return
    revision = task.get("revision", 0)

    run_id = durable.run["id"] if durable else "run_" + uuid.uuid4().hex[:6]
    started = datetime.now(timezone.utc)
    log.info("Executing service task %s/%s (run %s)", service_id, task_id, run_id)

    reply_to = task.get("reply_to") or {}
    status = "success"
    output = ""
    steps: List[dict] = []
    task_meta = {
        "task_id": task.get("id"),
        "task_name": task.get("name") or task.get("id"),
        "schedule_type": task.get("schedule_type"),
        "scheduled_at": started.isoformat(),
        "scope": "service",
        "service_id": service_id,
        "spawn_depth": task.get("spawn_depth", 0),
        "parent_task_id": task.get("parent_task_id"),
        "root_task_id": task.get("root_task_id") or task.get("id"),
    }

    ctx = _build_task_context_from_meta("service", admin_id, task,
                                        service_id=service_id)
    ctx_token = _current_task_var.set(ctx)
    try:
        validate_task(task, admin_id, service_id)
        cfg = task.get("task_config", {})
        conv_id = reply_to.get("conversation_id", f"sched-{task_id}")

        result = await asyncio.wait_for(
            _run_service_agent_task(admin_id, service_id, conv_id, cfg,
                                   reply_to=reply_to or None,
                                   task_meta=task_meta),
            timeout=_TASK_TIMEOUT_S,
        )
        output = result["output"]
        steps = result.get("steps", [])
        if result.get("deferred"):
            status = "deferred"
        elif not result["success"]:
            status = "error"
    except asyncio.CancelledError:
        output = "任务已取消"
        status = "cancelled"
    except asyncio.TimeoutError:
        output = f"任务超时（>{_TASK_TIMEOUT_S}s）"
        status = "timeout"
        steps.append(_step("error", output))
    except PermissionError as e:
        output = str(e)
        status = "blocked"
        steps.append(_step("permission_denied", output))
    except Exception as e:
        output = str(e)
        status = "error"
        steps.append(_step("error", output))
        log.exception("Service task %s/%s run %s failed", service_id, task_id, run_id)
    finally:
        _current_task_var.reset(ctx_token)

    finished = datetime.now(timezone.utc)
    run_record = {
        "run_id": run_id,
        "trigger": "manual" if manual else "scheduled",
        "task_revision": revision,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "status": status,
        "output": output if durable else output[:4000],
        "steps": steps,
    }
    log.info("Service task %s/%s run %s finished: %s", service_id, task_id, run_id, status)

    _finalize_execution("service", admin_id, service_id, task_id, revision, manual, run_record, task_meta)


# ── Scheduler loop (B2: heap-driven) ──────────────────────────────────────
#
# Design: a single global min-heap keyed on next_run_at epoch seconds.  CRUD
# entry points (create_task / update_task / create_child_task / ...) call
# ``_heap_upsert`` to push new entries; the loop sleeps **exactly** until the
# soonest entry's fire time (or until woken) instead of the old 30s polling.
#
# Stale entries (entry's stamp doesn't match the latest in ``_heap_index``)
# are discarded lazily on pop, so re-scheduling is O(log n) without needing
# a heap-remove.  Re-scan still runs every ``_RESCAN_INTERVAL_S`` to recover
# from any out-of-band edits or dropped wake-ups.

# Heap entry: (fire_epoch, seq, scope, uid, task_id, service_id)
HeapEntry = tuple

_heap: List[HeapEntry] = []
_heap_index: Dict[str, float] = {}     # composite key → latest fire_epoch
_heap_lock = _state_lock                 # sync tools and event-loop mutations
_heap_seq = 0                           # monotonic tiebreaker for heap stability
_wake_event: Optional[asyncio.Event] = None
_main_loop_ref: Optional[asyncio.AbstractEventLoop] = None


def _heap_key(scope: Literal["admin", "service"], uid: str, task_id: str,
              service_id: Optional[str] = None) -> str:
    """Composite key used for heap lazy-deletion bookkeeping."""
    if scope == "admin":
        return f"admin::{uid}::{task_id}"
    return f"svc::{uid}::{service_id}::{task_id}"


def _parse_next_run_epoch(next_run_iso: Optional[str]) -> Optional[float]:
    """Parse ISO-8601 → epoch seconds; assume UTC if no tz; return None on bad input."""
    if not next_run_iso:
        return None
    try:
        dt = datetime.fromisoformat(next_run_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


@_serialized
def _heap_upsert(scope: Literal["admin", "service"], uid: str, task_id: str,
                 next_run_iso: Optional[str],
                 service_id: Optional[str] = None) -> None:
    """Re-insert a task into the run heap with its updated next_run_at.

    Safe to call from any thread / sync context.  ``next_run_iso=None``
    deactivates the task (e.g. once-tasks after firing, disabled tasks).
    """
    global _heap_seq
    key = _heap_key(scope, uid, task_id, service_id)
    epoch = _parse_next_run_epoch(next_run_iso)
    if epoch is None:
        # Deactivate: drop from index, lazy-purge from heap on next pop
        _heap_index.pop(key, None)
        _wake()
        return

    _heap_index[key] = epoch
    _heap_seq += 1
    heapq.heappush(_heap, (epoch, _heap_seq, scope, uid, task_id, service_id))
    _wake()


def _wake() -> None:
    """Signal the loop to re-evaluate the heap top, thread-safe."""
    if _wake_event is None or _main_loop_ref is None:
        return
    if _main_loop_ref.is_running():
        _main_loop_ref.call_soon_threadsafe(_wake_event.set)


class HeapScheduler:
    """One owner per data directory; all queued/running coroutines are tracked."""

    def __init__(self):
        self._task = None
        self._running_tasks = set()
        self._handles = {}
        self._exec_sem = None
        self._owner = None
        self._loop_ref = None
        self._stopping = False
        self._outbox_task = None

    def start(self):
        global _wake_event, _main_loop_ref
        if _execution_disabled() or (self._task and not self._task.done()):
            return
        from app.core.security import USERS_DIR
        from app.services.scheduler_owner import SchedulerOwner
        loop = asyncio.get_running_loop()
        self._owner = SchedulerOwner(USERS_DIR)
        self._stopping = False
        self._loop_ref = loop
        self._exec_sem = asyncio.Semaphore(scheduler_concurrency_slots())
        _wake_event = asyncio.Event()
        _main_loop_ref = loop
        try:
            execution.get_store().recover()
            self._reload_from_disk()
            self._task = loop.create_task(self._loop())
            from app.execution.outbox import delivery_loop
            self._outbox_task = loop.create_task(delivery_loop(execution.get_store()))
            for run in execution.get_store().queued(self._capacity()):
                self._launch_run(run)
        except BaseException:
            self._owner.close()
            self._owner = None
            raise

    async def stop(self):
        with _state_lock:
            self._stopping = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._outbox_task:
            self._outbox_task.cancel()
            await asyncio.gather(self._outbox_task, return_exceptions=True)
        # Flush submit callbacks already accepted from tool worker threads.
        await asyncio.sleep(0)
        handles = list(self._handles.values())
        for task in handles:
            task.cancel()
        await asyncio.gather(*handles, return_exceptions=True)
        self._handles.clear()
        self._running_tasks.clear()
        self._exec_sem = None
        if self._owner:
            self._owner.close()
            self._owner = None
        log.info("HeapScheduler stopped; all executions drained")

    async def _loop(self):
        last_rescan = time.monotonic()
        while True:
            try:
                if not _execution_disabled() and not self._stopping:
                    for run in execution.get_store().queued(self._capacity()):
                        if len(self._running_tasks) >= self._capacity():
                            break
                        self._launch_run(run)
                if time.monotonic() - last_rescan >= _RESCAN_INTERVAL_S:
                    self._reload_from_disk()
                    last_rescan = time.monotonic()
                if _execution_disabled():
                    wait_s = 1.0
                else:
                    wait_s = min(self._compute_sleep(), max(0, _RESCAN_INTERVAL_S - (time.monotonic() - last_rescan)))
                if wait_s > 0:
                    try:
                        await asyncio.wait_for(_wake_event.wait(), timeout=wait_s)
                    except asyncio.TimeoutError:
                        pass
                    _wake_event.clear()
                else:
                    await self._dispatch_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("HeapScheduler loop error")
                await asyncio.sleep(5)

    def _capacity(self):
        return _env_int("SCHEDULER_MAX_PENDING", 128, minimum=1)

    @_serialized
    def _compute_sleep(self):
        if len(self._running_tasks) >= self._capacity():
            return 1.0
        while _heap:
            fire, _, scope, uid, tid, sid = _heap[0]
            if _heap_index.get(_heap_key(scope, uid, tid, sid)) != fire:
                heapq.heappop(_heap)
                continue
            return min(float(_RESCAN_INTERVAL_S), max(0.0, fire - time.time()))
        return float(_IDLE_RESCAN_S)

    async def _dispatch_due(self):
        with _state_lock:
            while _heap and not _execution_disabled() and not self._stopping:
                if len(self._running_tasks) >= self._capacity():
                    return
                fire, _, scope, uid, tid, sid = _heap[0]
                key = _heap_key(scope, uid, tid, sid)
                if _heap_index.get(key) != fire:
                    heapq.heappop(_heap)
                    continue
                if fire > time.time():
                    return
                heapq.heappop(_heap)
                _heap_index.pop(key, None)
                self._fire(scope, uid, tid, sid, key)

    def _fire(self, scope, uid, task_id, service_id, key):
        accepted = self._submit(scope, uid, task_id, service_id, manual=False)
        if not accepted:
            _heap_upsert(scope, uid, task_id, (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat(), service_id)
        return accepted

    @_serialized
    def _reload_from_disk(self):
        from app.core.security import USERS_DIR
        # Rebuild, rather than only upserting: remove disk-deleted/disabled tasks.
        _heap.clear()
        _heap_index.clear()
        st.invalidate_path_cache()
        if not os.path.isdir(USERS_DIR):
            return
        for uid in os.listdir(USERS_DIR):
            udir = os.path.join(USERS_DIR, uid)
            if uid.startswith(".") or not os.path.isdir(udir):
                continue
            scopes = [("admin", None)]
            services = os.path.join(udir, "services")
            if os.path.isdir(services):
                scopes.extend(("service", sid) for sid in os.listdir(services))
            for scope, sid in scopes:
                try:
                    for task in st.list_all_tasks_flat(scope, uid, sid):
                        task = execution.overlay(scope, uid, sid, task)
                        if task.get("enabled") and task.get("next_run_at"):
                            _heap_upsert(scope, uid, task["id"], task["next_run_at"], sid)
                except Exception:
                    log.exception("Scheduler rescan failed: %s/%s/%s", scope, uid, sid)

    async def _run_checked(self, key, scope, uid, sid, tid, run):
        snapshot, manual = run['snapshot'], run['trigger'] == 'manual'
        store = execution.get_store()
        claimed = None
        token = None
        try:
            async with self._exec_sem:
                with _state_lock:
                    current = execution.load_task(scope, uid, tid, sid)
                    valid = (current and not self._stopping and not _execution_disabled()
                             and not current.get('recovery_required')
                             and current.get('revision', 0) == snapshot.get('revision', 0)
                             and (manual or (current.get('enabled') and current.get('next_run_at') == snapshot.get('next_run_at'))))
                    if not valid:
                        store.request_cancel(run['id'], uid)
                        return
                    claimed = store.claim(run['id'])
                if not claimed:
                    return
                token = execution._current.set(execution.ExecutionContext(store, claimed))
                if scope == 'admin':
                    if manual:
                        await _execute_task(uid, tid, manual=True)
                    else:
                        await _execute_task(uid, tid)
                else:
                    if manual:
                        await _execute_service_task(uid, sid, tid, manual=True)
                    else:
                        await _execute_service_task(uid, sid, tid)
        except asyncio.CancelledError:
            # Queued work survives clean shutdown. Active work acknowledges only after drain.
            if claimed and store.get(run['id'])['status'] in ('running', 'cancel_requested'):
                store.finish(run['id'], claimed['token'], {'run_id': run['id'], 'status': 'cancelled', 'output': 'Cancelled before execution completed', 'steps': []})
            raise
        except Exception:
            log.exception('Execution adapter failed: %s', run['id'])
            if claimed:
                store.interrupt(run['id'], claimed['token'], 'Execution adapter failed; review effects before retrying')
        finally:
            if token is not None:
                execution._current.reset(token)
            with _state_lock:
                current = execution.load_task(scope, uid, tid, sid)
                if current and current.get('enabled') and not current.get('recovery_required'):
                    _heap_upsert(scope, uid, tid, current.get('next_run_at'), sid)
            _wake()

    def _launch_run(self, run):
        scope, uid, sid, tid = json.loads(run['task_key'])
        key = _heap_key(scope, uid, tid, sid)
        if key in self._running_tasks or run['status'] != 'queued':
            return
        self._running_tasks.add(key)
        def launch():
            if self._stopping:
                self._running_tasks.discard(key)
                return
            handle = self._loop_ref.create_task(self._run_checked(key, scope, uid, sid, tid, run))
            self._handles[key] = handle
            def done(future):
                if self._handles.get(key) is future:
                    self._handles.pop(key, None)
                    self._running_tasks.discard(key)
                    _wake()
                if not future.cancelled() and future.exception():
                    log.error('Scheduled execution failed: %s', key, exc_info=future.exception())
            handle.add_done_callback(done)
        self._loop_ref.call_soon_threadsafe(launch)

    @_serialized
    def submit_run(self, scope, uid, tid, sid=None, *, manual=True, request_id=None):
        if (_execution_disabled() or self._stopping or self._owner is None
                or self._loop_ref is None or not self._loop_ref.is_running()):
            raise Conflict('Scheduler is not accepting runs')
        snapshot = execution.load_task(scope, uid, tid, sid)
        if not snapshot or (not manual and not snapshot.get('enabled')):
            raise Conflict('Task is missing or disabled')
        from app.execution.grants import capture
        snapshot = {**snapshot, 'execution_grant': capture(snapshot, uid, sid)}
        run = execution.get_store().submit(task_key(scope, uid, sid, tid), uid, snapshot,
            manual=manual, request_id=request_id, max_pending=self._capacity())
        self._launch_run(run)
        return run

    def _submit(self, scope, uid, tid, sid=None, *, manual):
        try:
            self.submit_run(scope, uid, tid, sid, manual=manual)
            return True
        except Conflict:
            return False

    def cancel_run(self, rid, uid):
        row = execution.get_store().request_cancel(rid, uid)
        if row and row['status'] == 'cancel_requested':
            scope, owner, sid, tid = json.loads(row['task_key'])
            handle = self._handles.get(_heap_key(scope, owner, tid, sid))
            if handle:
                self._loop_ref.call_soon_threadsafe(handle.cancel)
        return row

    def run_now(self, user_id, task_id):
        return self._submit("admin", user_id, task_id, manual=True)

    def run_service_task_now(self, admin_id, service_id, task_id):
        return self._submit("service", admin_id, task_id, service_id, manual=True)


# Backward-compat alias — older code may still import TaskScheduler by name
# (routes/scheduler.py, tests, etc.).  Keep the symbol stable.
TaskScheduler = HeapScheduler


# ── Singleton ─────────────────────────────────────────────────────────────

_scheduler: Optional[HeapScheduler] = None


def get_scheduler() -> HeapScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = HeapScheduler()
    return _scheduler
