"""Scheduled-task → main-conversation LangGraph state injection.

Phase 2 of "scheduled task as tool use": after `app/services/scheduler.py`
persists a `scheduled_task` tool block into messages.json (Phase 1 / L1, for
visible chat history), this module ALSO injects a synthetic
`AIMessage(tool_calls=[scheduled_task])` + `ToolMessage(result)` pair into
the main conversation's LangGraph checkpoint (`AsyncSqliteSaver`), so the
agent's NEXT user turn naturally "remembers" the scheduled task ran without
relying on the memory subagent or per-call short-term memory injection.

Key design:

- **Per-thread asyncio.Queue + drainer task.** Scheduled tasks may fire while
  the user is actively streaming a turn on the same thread_id. Concurrent
  `aupdate_state` against a live `astream` causes checkpoint races. We queue
  the injection and drain only when the thread is idle (refcount 0).
- **10-min hard timeout per item.** If the user keeps streaming non-stop
  (e.g. long agentic loop), we drop the L2 injection after 10 minutes —
  L1 is still persisted to messages.json so the user/admin sees the task,
  only the agent's internal LangGraph memory misses it. A WARN is logged.
- **Truncation @ 5 pairs.** Cap the count of synthetic injection pairs in
  state to the 5 most recent. Older pairs are removed via `RemoveMessage`
  and replaced with a single `SystemMessage` summarizing the evicted runs
  ("[历史定时任务摘要] N 条已折叠..."). Prevents context inflation while
  preserving background awareness for the agent.
- **Active-stream tracking via refcount.** Streaming entry points
  (`app/routes/chat.py`, `app/routes/consumer.py`,
  `app/channels/wechat/admin_bridge.py`, `app/channels/wechat/bridge.py`)
  bracket their `agent.astream()` loop with `mark_thread_active(tid)` /
  `mark_thread_inactive(tid)`. Refcount tolerates the (rare) case where
  the same thread_id is being streamed concurrently from multiple channels.
- **Agent factory captured at enqueue time.** We don't hold the agent
  reference (it could be evicted from cache); we re-create at drain time
  through a captured factory closure. `create_user_agent` /
  `create_consumer_agent` are LRU-cached so this is cheap.

Marker convention on injected messages (for later detection / truncation):
    AIMessage / ToolMessage:
        id = "sched_inj_ai_<run_marker>"  /  "sched_inj_tool_<run_marker>"
        additional_kwargs = {"_sched_pair_id": <run_marker>,
                             "_scheduled_task": True}
    Summary SystemMessage:
        id = "sched_summary_<random>"
        additional_kwargs = {"_sched_summary": True}
"""

import asyncio
import contextlib
import logging
import time
import uuid
from typing import Optional, Dict, Any, List, Callable, Awaitable

log = logging.getLogger("scheduled_inject")

_QUEUE_MAX_WAIT_S = 600     # 10 min — drop L2 injection if thread never idles
_DRAIN_SETTLE_S = 0.5       # small delay before draining (let final state commit)
_MAX_LIVE_PAIRS = 5         # keep last N pairs uncompressed; older → SystemMessage summary
_TOOL_CONTENT_MAX_CHARS = 1500  # cap per-pair tool message size in state


# ── State ────────────────────────────────────────────────────────────────

# thread_id → asyncio.Queue[InjectionItem]
_pending: Dict[str, asyncio.Queue] = {}

# thread_id → refcount of currently-active streams
_active_refcount: Dict[str, int] = {}

# Background per-thread drainer tasks (so we don't lose injections even if
# no stream-end event ever fires for a thread).
_drainer_tasks: Dict[str, asyncio.Task] = {}

# Lock guarding _pending / _active_refcount / _drainer_tasks mutations.
_lock = asyncio.Lock()


# ── Active-stream tracking (called by streaming entry points) ───────────

async def mark_thread_active(thread_id: str) -> None:
    """Mark a thread as actively streaming. Pair with mark_thread_inactive in finally."""
    if not thread_id:
        return
    async with _lock:
        _active_refcount[thread_id] = _active_refcount.get(thread_id, 0) + 1


async def mark_thread_inactive(thread_id: str) -> None:
    """Decrement active refcount; if reaches 0 and there's a pending queue, kick the drainer."""
    if not thread_id:
        return
    drain_now = False
    async with _lock:
        n = _active_refcount.get(thread_id, 0) - 1
        if n <= 0:
            _active_refcount.pop(thread_id, None)
            q = _pending.get(thread_id)
            drain_now = q is not None and not q.empty()
        else:
            _active_refcount[thread_id] = n
    if drain_now:
        await _wake_drainer(thread_id)


@contextlib.asynccontextmanager
async def thread_active(thread_id: str):
    """Async context manager for streaming entry points.

    Brackets an `agent.astream` loop so any L2 injections queued during the
    stream wait for it to finish — guaranteed exception-safe via try/finally.
    Equivalent to manually pairing mark_thread_active / mark_thread_inactive.
    """
    await mark_thread_active(thread_id)
    try:
        yield
    finally:
        await mark_thread_inactive(thread_id)


async def _wake_drainer(thread_id: str) -> None:
    """Ensure a single drainer task exists for this thread."""
    async with _lock:
        task = _drainer_tasks.get(thread_id)
        if task is not None and not task.done():
            return
        _drainer_tasks[thread_id] = asyncio.create_task(
            _drainer_loop(thread_id),
            name=f"sched-inject-drainer:{thread_id}",
        )


# ── Public enqueue API (called from scheduler.py after L1 persistence) ──

async def enqueue_admin(user_id: str,
                        conv_id: str,
                        task_meta: Dict[str, Any],
                        output: str,
                        success: bool = True,
                        error: Optional[str] = None) -> None:
    """Queue a scheduled-task injection for an admin conversation thread."""
    if not conv_id or not user_id:
        return
    thread_id = f"{user_id}-{conv_id}"
    item = _build_item(task_meta, output, success, error,
                       agent_factory=_admin_agent_factory(user_id))
    await _enqueue(thread_id, item)


async def enqueue_service(admin_id: str,
                          service_id: str,
                          conv_id: str,
                          task_meta: Dict[str, Any],
                          output: str,
                          success: bool = True,
                          error: Optional[str] = None) -> None:
    """Queue a scheduled-task injection for a service consumer conversation thread."""
    if not conv_id or not service_id or not admin_id:
        return
    thread_id = f"svc-{service_id}-{conv_id}"
    item = _build_item(task_meta, output, success, error,
                       agent_factory=_consumer_agent_factory(admin_id, service_id, conv_id))
    await _enqueue(thread_id, item)


async def _enqueue(thread_id: str, item: dict) -> None:
    async with _lock:
        q = _pending.get(thread_id)
        if q is None:
            q = asyncio.Queue()
            _pending[thread_id] = q
        await q.put(item)
        active = _active_refcount.get(thread_id, 0)
        qsize = q.qsize()
    log.info("Queued scheduled-task L2 injection for thread=%s "
             "(active_streams=%d, pending=%d, task=%s)",
             thread_id, active, qsize, item["task_meta"].get("task_id"))
    # Always wake drainer; it will idle-wait if the thread is currently active.
    await _wake_drainer(thread_id)


# ── Drainer loop (one per thread, lifecycle = while queue non-empty) ────

async def _drainer_loop(thread_id: str) -> None:
    """Drain pending injections for a single thread.

    Waits while the thread is active; processes a batch when it idles.
    Items older than _QUEUE_MAX_WAIT_S are dropped (L1 still persisted).
    Loop exits when queue is empty.
    """
    try:
        while True:
            async with _lock:
                q = _pending.get(thread_id)
                if q is None or q.empty():
                    return
                active = _active_refcount.get(thread_id, 0)
            if active > 0:
                # Wait briefly and re-check; mark_thread_inactive will also
                # explicitly wake us via _wake_drainer.
                await asyncio.sleep(2.0)
                continue

            # Drain everything currently queued in one batch (cheaper than
            # one aupdate_state per item; each call would re-scan state).
            items: List[dict] = []
            now = time.time()
            while not q.empty():
                it = await q.get()
                if now - it["enqueued_at"] > _QUEUE_MAX_WAIT_S:
                    log.warning("Dropping scheduled-task L2 injection (thread=%s, "
                                "task=%s) — queued >%ds, L1 still persisted in messages.json",
                                thread_id, it["task_meta"].get("task_id"),
                                _QUEUE_MAX_WAIT_S)
                    continue
                items.append(it)
            if not items:
                continue

            # Small settle delay to let any just-finished stream's checkpoint
            # write fully commit before we read+update state.
            await asyncio.sleep(_DRAIN_SETTLE_S)
            try:
                await _inject_batch(thread_id, items)
            except Exception:
                log.exception("Drainer failed to inject batch on thread=%s "
                              "(items=%d)", thread_id, len(items))
    finally:
        async with _lock:
            _drainer_tasks.pop(thread_id, None)
            q = _pending.get(thread_id)
            if q is not None and q.empty():
                _pending.pop(thread_id, None)


# ── Injection logic ──────────────────────────────────────────────────────

async def _inject_batch(thread_id: str, items: List[dict]) -> None:
    """Inject N synthetic pairs into thread state, then truncate to MAX_LIVE_PAIRS."""
    # All items in a batch share the same agent factory (same thread = same scope).
    agent_factory = items[0]["agent_factory"]
    try:
        agent = await agent_factory()
    except Exception:
        log.exception("Agent factory failed for thread=%s — skipping injection",
                      thread_id)
        return

    config = {"configurable": {"thread_id": thread_id}}

    try:
        state = await agent.aget_state(config)
    except Exception as e:
        log.warning("aget_state failed for thread=%s (%s) — skipping injection. "
                    "L1 already persisted; agent will lack direct memory of these tasks.",
                    thread_id, e)
        return

    existing_messages = list((getattr(state, "values", None) or {}).get("messages") or [])

    from langchain_core.messages import AIMessage, ToolMessage

    new_pairs: List[Dict[str, Any]] = []
    for it in items:
        meta = it["task_meta"]
        run_marker = it["run_marker"]
        tool_call_id = f"sched_{run_marker}"
        ai = AIMessage(
            id=f"sched_inj_ai_{run_marker}",
            content="",
            tool_calls=[{
                "id": tool_call_id,
                "name": "scheduled_task",
                "args": {"task_meta": meta, "status": meta.get("status", "success")},
            }],
            additional_kwargs={"_sched_pair_id": run_marker, "_scheduled_task": True},
        )
        tm = ToolMessage(
            id=f"sched_inj_tool_{run_marker}",
            tool_call_id=tool_call_id,
            content=_truncate_for_state(it["output"]),
            additional_kwargs={"_sched_pair_id": run_marker, "_scheduled_task": True},
        )
        new_pairs.append({"ai": ai, "tool": tm, "meta": meta})

    existing_pairs: List[Dict[str, Any]] = _scan_existing_pairs(existing_messages)
    existing_summary_id = _find_summary_id(existing_messages)

    total_after = len(existing_pairs) + len(new_pairs)
    evict_count = max(0, total_after - _MAX_LIVE_PAIRS)

    update_messages: List[Any] = []

    if evict_count > 0:
        from langchain_core.messages import RemoveMessage, SystemMessage

        evicted = existing_pairs[:evict_count]

        # Combine old summary's lines (if any) with newly-evicted lines into a
        # single fresh summary message, preserving all previously-evicted runs'
        # info while keeping at most one summary message in state.
        prior_summary_lines = _extract_prior_summary_lines(
            existing_messages, existing_summary_id)
        evicted_lines = [_summarize_pair(p) for p in evicted]
        all_lines = prior_summary_lines + evicted_lines

        if existing_summary_id is not None:
            update_messages.append(RemoveMessage(id=existing_summary_id))
        for pair in evicted:
            for mid in pair["msg_ids"]:
                update_messages.append(RemoveMessage(id=mid))

        if all_lines:
            summary_text = (
                "[历史定时任务摘要] 以下是更早已执行的定时任务（已折叠以节约上下文）：\n"
                + "\n".join(f"- {ln}" for ln in all_lines)
            )
            update_messages.append(SystemMessage(
                id=f"sched_summary_{uuid.uuid4().hex[:8]}",
                content=summary_text,
                additional_kwargs={"_sched_summary": True},
            ))

        log.info("Truncating scheduled_task pairs on thread=%s: "
                 "existing_pairs=%d, evicting=%d, new=%d, summary_lines=%d",
                 thread_id, len(existing_pairs), evict_count,
                 len(new_pairs), len(all_lines))

    # Append new pairs (AIMessage immediately followed by its ToolMessage).
    for p in new_pairs:
        update_messages.append(p["ai"])
        update_messages.append(p["tool"])

    if not update_messages:
        return

    try:
        await agent.aupdate_state(config, {"messages": update_messages})
        removed_n = sum(1 for m in update_messages
                        if m.__class__.__name__ == "RemoveMessage")
        log.info("Injected %d scheduled-task pair(s) into thread=%s "
                 "(removed=%d msgs)", len(new_pairs), thread_id, removed_n)
    except Exception:
        log.exception("aupdate_state failed for thread=%s — L1 still persisted",
                      thread_id)


# ── Helpers ──────────────────────────────────────────────────────────────

def _build_item(task_meta: Dict[str, Any], output: str, success: bool,
                error: Optional[str],
                agent_factory: Callable[[], Awaitable[Any]]) -> dict:
    meta = dict(task_meta or {})
    meta.setdefault("status", "success" if success else "error")
    if error:
        meta["error"] = error[:200]
    return {
        "task_meta": meta,
        "output": output or "",
        "agent_factory": agent_factory,
        # run_marker links the AI/Tool sides of one pair. Prefer task_id but
        # always append a short random suffix so multiple runs of the same
        # task (e.g. cron) get distinct pairs.
        "run_marker": f"{meta.get('task_id', 'unk')}_{uuid.uuid4().hex[:6]}",
        "enqueued_at": time.time(),
    }


def _truncate_for_state(text: str, max_chars: int = _TOOL_CONTENT_MAX_CHARS) -> str:
    """Cap injected output text — full text is in messages.json (L1)."""
    if not text:
        return "(任务无输出)"
    if len(text) <= max_chars:
        return text
    return (text[:max_chars]
            + f"\n\n[…已截断 {len(text) - max_chars} 字符；完整结果见聊天记录]")


def _msg_kwargs(msg: Any) -> dict:
    return getattr(msg, "additional_kwargs", None) or {}


def _scan_existing_pairs(messages: List[Any]) -> List[Dict[str, Any]]:
    """Find synthetic scheduled_task pairs in chronological order."""
    by_pair: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for m in messages:
        kw = _msg_kwargs(m)
        pid = kw.get("_sched_pair_id")
        if not pid or not kw.get("_scheduled_task"):
            continue
        if pid not in by_pair:
            by_pair[pid] = {"pair_id": pid, "ai_id": None, "tool_id": None,
                            "meta": None, "output": ""}
            order.append(pid)
        slot = by_pair[pid]
        cls = m.__class__.__name__
        if cls == "AIMessage":
            slot["ai_id"] = getattr(m, "id", None)
            try:
                tcs = getattr(m, "tool_calls", None) or []
                if tcs:
                    args = tcs[0].get("args") or {}
                    if isinstance(args, dict):
                        slot["meta"] = args.get("task_meta") or {}
            except Exception:
                pass
        elif cls == "ToolMessage":
            slot["tool_id"] = getattr(m, "id", None)
            content = getattr(m, "content", "") or ""
            if isinstance(content, str):
                slot["output"] = content
    pairs = []
    for pid in order:
        s = by_pair[pid]
        if s["ai_id"] and s["tool_id"]:
            pairs.append({
                "pair_id": pid,
                "msg_ids": [s["ai_id"], s["tool_id"]],
                "meta": s.get("meta") or {},
                "output": s.get("output", ""),
            })
    return pairs


def _find_summary_id(messages: List[Any]) -> Optional[str]:
    for m in messages:
        if _msg_kwargs(m).get("_sched_summary"):
            return getattr(m, "id", None)
    return None


_SUMMARY_PREFIX = (
    "[历史定时任务摘要] 以下是更早已执行的定时任务（已折叠以节约上下文）：\n"
)


def _extract_prior_summary_lines(messages: List[Any],
                                 summary_id: Optional[str]) -> List[str]:
    """Extract bullet lines from an existing summary message so we can rebuild it."""
    if not summary_id:
        return []
    for m in messages:
        if getattr(m, "id", None) != summary_id:
            continue
        content = getattr(m, "content", "") or ""
        if not isinstance(content, str):
            return []
        body = content[len(_SUMMARY_PREFIX):] if content.startswith(_SUMMARY_PREFIX) else content
        lines = []
        for ln in body.splitlines():
            ln = ln.strip()
            if ln.startswith("- "):
                lines.append(ln[2:])
            elif ln:
                lines.append(ln)
        return lines
    return []


def _summarize_pair(pair: Dict[str, Any]) -> str:
    """Build a short bullet line for one evicted pair."""
    meta = pair.get("meta") or {}
    name = meta.get("task_name") or meta.get("task_id") or "未命名任务"
    when = (meta.get("scheduled_at") or "")[:10]
    status = meta.get("status", "success")
    status_mark = "✓" if status == "success" else "✗"
    output_lines = (pair.get("output") or "").strip().splitlines()
    snippet = output_lines[0][:50] if output_lines else ""
    base = f"{status_mark} {name}（{when}）"
    return f"{base}: {snippet}" if snippet else base


# ── Agent factories (closures) ───────────────────────────────────────────

def _admin_agent_factory(user_id: str) -> Callable[[], Awaitable[Any]]:
    async def _make():
        from app.services.agent import create_user_agent
        return create_user_agent(user_id)
    return _make


def _consumer_agent_factory(admin_id: str, service_id: str, conv_id: str
                            ) -> Callable[[], Awaitable[Any]]:
    async def _make():
        from app.services.consumer_agent import create_consumer_agent
        return create_consumer_agent(admin_id, service_id, conv_id)
    return _make
