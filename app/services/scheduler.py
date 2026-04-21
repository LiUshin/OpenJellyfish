"""
Scheduled task engine.

== Admin tasks ==
    Stored at: {user_dir}/tasks/{task_id}.json
    task_type: "script" | "agent"
    agent tasks use the full user agent (create_user_agent) with humanchat
    capability, enabling send_message tool calls that are intercepted and
    forwarded to WeChat when reply_to is configured.

== Service tasks ==
    Stored at: {user_dir}/services/{service_id}/tasks/{task_id}.json
    task_type: "agent" only (no scripts)
    Executes using the service's consumer agent with humanchat injected.
    send_message tool calls are intercepted and forwarded to WeChat.

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

import os
import json
import uuid
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from app.core.security import get_user_dir

log = logging.getLogger("scheduler")

_MAX_RUNS_STORED = 20
_LOOP_INTERVAL_S = 30          # check every 30 seconds
_TASK_TIMEOUT_S  = 300         # max 5 min per task run


# ── Path helpers ──────────────────────────────────────────────────────────

def _tasks_dir(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), "tasks")


def _task_path(user_id: str, task_id: str) -> str:
    return os.path.join(_tasks_dir(user_id), f"{task_id}.json")


def _service_tasks_dir(admin_id: str, service_id: str) -> str:
    return os.path.join(get_user_dir(admin_id), "services", service_id, "tasks")


def _service_task_path(admin_id: str, service_id: str, task_id: str) -> str:
    return os.path.join(_service_tasks_dir(admin_id, service_id), f"{task_id}.json")


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
    path = _task_path(user_id, task_id)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_task(user_id: str, task: Dict[str, Any]) -> None:
    os.makedirs(_tasks_dir(user_id), exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(_task_path(user_id, task["id"]), task, ensure_ascii=False, indent=2)


def create_task(user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new scheduled task and compute next_run_at."""
    task_id = "task_" + uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    tz_offset = data.get("tz_offset_hours")
    if tz_offset is None:
        from app.services.preferences import get_tz_offset
        tz_offset = get_tz_offset(user_id)

    task: Dict[str, Any] = {
        "id": task_id,
        "user_id": user_id,
        "name": data.get("name", "Unnamed Task"),
        "description": data.get("description", ""),
        "schedule_type": data.get("schedule_type", "once"),   # cron | once | interval
        "schedule": data.get("schedule", ""),
        "task_type": data.get("task_type", "script"),         # script | agent
        "task_config": data.get("task_config", {}),
        "reply_to": data.get("reply_to"),
        "enabled": data.get("enabled", True),
        "tz_offset_hours": tz_offset,
        "created_at": now.isoformat(),
        "last_run_at": None,
        "next_run_at": None,
        "runs": [],
    }
    task["next_run_at"] = _compute_next_run(task, now)
    _save_task(user_id, task)
    return task


def list_tasks(user_id: str) -> List[Dict[str, Any]]:
    d = _tasks_dir(user_id)
    if not os.path.isdir(d):
        return []
    tasks = []
    for fname in os.listdir(d):
        if fname.endswith(".json"):
            fpath = os.path.join(d, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    t = json.load(f)
                # Return without full run history for listing
                t_summary = {k: v for k, v in t.items() if k != "runs"}
                t_summary["run_count"] = len(t.get("runs", []))
                tasks.append(t_summary)
            except Exception:
                pass
    tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return tasks


def get_task(user_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    return _load_task(user_id, task_id)


def update_task(user_id: str, task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    task = _load_task(user_id, task_id)
    if not task:
        return None
    for k, v in updates.items():
        if k not in ("id", "user_id", "created_at", "runs"):
            task[k] = v
    # Recompute next_run_at if schedule changed
    if any(k in updates for k in ("schedule_type", "schedule", "enabled")):
        now = datetime.now(timezone.utc)
        task["next_run_at"] = _compute_next_run(task, now) if task["enabled"] else None
    _save_task(user_id, task)
    return task


def delete_task(user_id: str, task_id: str) -> bool:
    path = _task_path(user_id, task_id)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def get_task_runs(user_id: str, task_id: str) -> List[Dict[str, Any]]:
    task = _load_task(user_id, task_id)
    if not task:
        return []
    return task.get("runs", [])


# ── Service task CRUD ─────────────────────────────────────────────────────

def _load_service_task(admin_id: str, service_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    path = _service_task_path(admin_id, service_id, task_id)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_service_task(admin_id: str, service_id: str, task: Dict[str, Any]) -> None:
    d = _service_tasks_dir(admin_id, service_id)
    os.makedirs(d, exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(os.path.join(d, f"{task['id']}.json"), task, ensure_ascii=False, indent=2)


def create_service_task(admin_id: str, service_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a scheduled task under a published service."""
    task_id = "stask_" + uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    tz_offset = data.get("tz_offset_hours")
    if tz_offset is None:
        from app.services.preferences import get_tz_offset
        tz_offset = get_tz_offset(admin_id)

    task: Dict[str, Any] = {
        "id": task_id,
        "admin_id": admin_id,
        "service_id": service_id,
        "name": data.get("name", "Unnamed Task"),
        "description": data.get("description", ""),
        "schedule_type": data.get("schedule_type", "once"),
        "schedule": data.get("schedule", ""),
        "task_type": "agent",
        "task_config": data.get("task_config", {}),
        "reply_to": data.get("reply_to"),
        "enabled": data.get("enabled", True),
        "tz_offset_hours": tz_offset,
        "created_at": now.isoformat(),
        "last_run_at": None,
        "next_run_at": None,
        "runs": [],
    }
    task["next_run_at"] = _compute_next_run(task, now)
    _save_service_task(admin_id, service_id, task)
    return task


def list_service_tasks(admin_id: str, service_id: str) -> List[Dict[str, Any]]:
    d = _service_tasks_dir(admin_id, service_id)
    if not os.path.isdir(d):
        return []
    tasks = []
    for fname in os.listdir(d):
        if fname.endswith(".json"):
            fpath = os.path.join(d, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    t = json.load(f)
                t_summary = {k: v for k, v in t.items() if k != "runs"}
                t_summary["run_count"] = len(t.get("runs", []))
                tasks.append(t_summary)
            except Exception:
                pass
    tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return tasks


def get_service_task(admin_id: str, service_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    return _load_service_task(admin_id, service_id, task_id)


def update_service_task(
    admin_id: str, service_id: str, task_id: str, updates: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    task = _load_service_task(admin_id, service_id, task_id)
    if not task:
        return None
    for k, v in updates.items():
        if k not in ("id", "admin_id", "service_id", "created_at", "runs"):
            task[k] = v
    if any(k in updates for k in ("schedule_type", "schedule", "enabled")):
        now = datetime.now(timezone.utc)
        task["next_run_at"] = _compute_next_run(task, now) if task["enabled"] else None
    _save_service_task(admin_id, service_id, task)
    return task


def delete_service_task(admin_id: str, service_id: str, task_id: str) -> bool:
    path = _service_task_path(admin_id, service_id, task_id)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def get_service_task_runs(admin_id: str, service_id: str, task_id: str) -> List[Dict[str, Any]]:
    task = _load_service_task(admin_id, service_id, task_id)
    if not task:
        return []
    return task.get("runs", [])


def list_all_service_tasks(admin_id: str) -> List[Dict[str, Any]]:
    """List tasks across all services for a given admin."""
    services_dir = os.path.join(get_user_dir(admin_id), "services")
    if not os.path.isdir(services_dir):
        return []
    all_tasks = []
    for svc_id in os.listdir(services_dir):
        all_tasks.extend(list_service_tasks(admin_id, svc_id))
    all_tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return all_tasks


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
    resolved = []
    for name in dir_names:
        name = name.strip().strip("/").strip("\\")
        if not name:
            continue
        if name == "*":
            resolved.append(fs_dir)
            continue
        abs_path = os.path.join(fs_dir, name)
        os.makedirs(abs_path, exist_ok=True)
        resolved.append(abs_path)
    return resolved


async def _run_script_task(user_id: str, config: Dict[str, Any]) -> dict:
    """Returns {"output": str, "success": bool, "steps": list}."""
    from app.services.script_runner import run_script
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

    result = run_script(
        script_path=script_path,
        scripts_dir=scripts_dir,
        input_data=config.get("input_data"),
        args=script_args,
        timeout=min(config.get("timeout", 60), _TASK_TIMEOUT_S),
        allowed_read_dirs=read_dirs,
        allowed_write_dirs=write_dirs,
    )

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


def _resolve_wechat_client(reply_to: Optional[Dict[str, Any]]):
    """Resolve WeChat client, to_user, ctx_token from reply_to config.

    Returns (client, to_user, ctx_token) or (None, None, None).
    """
    if not reply_to or reply_to.get("channel") != "wechat":
        return None, None, None

    service_id = reply_to.get("service_id")
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
            admin_sess = _get_admin_session(reply_to.get("admin_id", ""))
            if not admin_sess or not admin_sess.get("connected"):
                return None, None, None
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
                                    service_context=None):
    """Intercept send_message ToolMessage and send to WeChat."""
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
        if text:
            await client.send_text(to_user, text, ctx_token)
            steps.append(_step("wechat_send", f"已发送消息: {text[:100]}"))
            log.info("Scheduled task sent message: %s", text[:50])
    except Exception:
        log.exception("Failed to send scheduled task message via WeChat")
        steps.append(_step("wechat_error", "发送微信消息失败"))


async def _run_agent_loop(agent, input_payload, agent_config, steps: List[dict],
                          output_parts: List[str], *,
                          wechat_client=None, wechat_to_user: str = "",
                          wechat_ctx_token: str = "", user_id: str = "",
                          service_context=None):
    """Shared astream loop with send_message interception for both admin & service tasks."""
    from langgraph.types import Command
    max_loops = 20

    for loop_i in range(max_loops):
        steps.append(_step("loop", f"Agent 执行循环 #{loop_i + 1}"))
        async for event in agent.astream(input_payload, config=agent_config):
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

                        if tool_name == "send_message" and wechat_client:
                            await _handle_send_message_tool(
                                tool_content, wechat_client, wechat_to_user,
                                wechat_ctx_token, user_id, steps,
                                service_context=service_context,
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


async def _run_agent_task(user_id: str, config: Dict[str, Any],
                          reply_to: Optional[Dict[str, Any]] = None) -> dict:
    """Run an admin agent task using the full user agent with humanchat.

    Returns {"output": str, "success": bool, "steps": list}.
    """
    from app.services.agent import create_user_agent, _get_default_model
    from app.services.memory_tools import load_recent_admin_messages
    prompt_text = config.get("prompt", "")
    doc_path = config.get("doc_path", "")
    model = config.get("model", "")
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

    full_prompt += (
        "\n\n---\n"
        "[重要] 这是一个定时任务。你的直接文本输出用户看不到。"
        "任务完成后，你必须使用 `send_message` 工具将结果发送给用户，否则用户将收不到任何信息。"
    )

    if not model:
        model = _get_default_model()

    if "humanchat" not in capabilities:
        capabilities = list(capabilities) + ["humanchat"]

    agent = create_user_agent(user_id, model=model, capabilities=capabilities)
    thread_id = f"scheduled-{uuid.uuid4().hex[:8]}"
    agent_config = {"configurable": {"thread_id": thread_id}}
    output_parts = []

    wechat_client, wechat_to_user, wechat_ctx_token = _resolve_wechat_client(reply_to)
    if wechat_client:
        steps.append(_step("wechat_connected", "已连接微信推送通道"))
    elif reply_to and reply_to.get("channel") == "wechat":
        log.warning("Admin task %s: reply_to specifies wechat but client not resolved "
                    "(admin may be disconnected)", "")
        steps.append(_step("wechat_warning",
                           "微信推送通道不可用（管理员可能已断开连接），"
                           "send_message 将不会发送到微信"))

    try:
        input_payload = {"messages": [{"role": "user", "content": full_prompt}]}
        await _run_agent_loop(
            agent, input_payload, agent_config, steps, output_parts,
            wechat_client=wechat_client, wechat_to_user=wechat_to_user or "",
            wechat_ctx_token=wechat_ctx_token or "", user_id=user_id,
        )
        steps.append(_step("finish", "Agent 执行完成"))
    except asyncio.TimeoutError:
        steps.append(_step("error", "任务超时"))
        return {"output": "任务超时", "success": False, "steps": steps}
    except Exception as e:
        steps.append(_step("error", f"Agent 执行失败: {e}"))
        return {"output": f"Agent 执行失败: {e}", "success": False, "steps": steps}
    text = "\n".join(output_parts) or "（Agent 未返回输出）"

    # Persist task output to conversation history
    if conv_id:
        try:
            from app.services.conversations import save_message
            save_message(user_id, conv_id, "assistant", text,
                         blocks=[{"type": "text", "content": text,
                                  "source": "scheduled_task"}])
        except Exception:
            log.exception("Failed to persist admin task output to conv %s", conv_id)

    return {"output": text, "success": True, "steps": steps}


async def _run_service_agent_task(admin_id: str, service_id: str, conversation_id: str,
                                  config: Dict[str, Any],
                                  reply_to: Optional[Dict[str, Any]] = None) -> dict:
    """Run a service agent task using the full consumer agent with humanchat.

    If reply_to points to a WeChat session, send_message tool calls are
    intercepted and delivered to the user in real time (text + media).

    Returns {"output": str, "success": bool, "steps": list}.
    """
    from app.services.consumer_agent import create_consumer_agent
    from app.services.memory_tools import load_recent_consumer_messages
    prompt_text = config.get("prompt", "")
    doc_path = config.get("doc_path", "")
    steps: List[dict] = []

    steps.append(_step("start", f"Service Agent 任务启动",
                        service_id=service_id,
                        prompt=prompt_text[:200],
                        doc_paths=doc_path if isinstance(doc_path, list) else ([doc_path] if doc_path else [])))

    doc_content = _read_docs(admin_id, doc_path) if doc_path else ""

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
        "[系统指令 - 来自管理员]\n"
        "以下是管理员下达的任务指令，不是来自终端用户的消息。\n\n"
    )
    if recent_ctx:
        full_prompt += f"[对话上下文 - 最近消息]\n---\n{recent_ctx}\n---\n\n"
    full_prompt += (
        f"管理员指令：{task_instruction}\n\n"
        "---\n"
        "[重要] 这是一个定时任务。你的直接文本输出用户看不到。"
        "任务完成后，你必须使用 `send_message` 工具将结果发送给用户，否则用户将收不到任何信息。\n"
        "如需向管理员反馈，请使用 contact_admin 工具。"
    )

    extra_caps = ["humanchat"] if reply_to and reply_to.get("channel") == "wechat" else None
    agent = create_consumer_agent(admin_id, service_id, conversation_id,
                                  extra_capabilities=extra_caps,
                                  channel="scheduler")
    thread_id = f"svc-scheduled-{uuid.uuid4().hex[:8]}"
    agent_config = {"configurable": {"thread_id": thread_id}}
    output_parts = []

    wechat_client, wechat_to_user, wechat_ctx_token = _resolve_wechat_client(reply_to)
    if wechat_client:
        steps.append(_step("wechat_connected", "已连接微信推送通道"))
    elif reply_to and reply_to.get("channel") == "wechat":
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
        )
        steps.append(_step("finish", "Service Agent 执行完成"))
    except asyncio.TimeoutError:
        steps.append(_step("error", "任务超时"))
        return {"output": "任务超时", "success": False, "steps": steps}
    except Exception as e:
        steps.append(_step("error", f"Service Agent 执行失败: {e}"))
        return {"output": f"Service Agent 执行失败: {e}", "success": False, "steps": steps}

    text = "\n".join(output_parts) or "（Agent 未返回输出）"

    # Persist task output to consumer conversation history
    try:
        from app.services.published import save_consumer_message
        save_consumer_message(admin_id, service_id, conversation_id,
                              "assistant", text,
                              blocks=[{"type": "text", "content": text,
                                       "source": "admin_broadcast"}])
    except Exception:
        log.exception("Failed to persist service task output to conv %s", conversation_id)

    return {"output": text, "success": True, "steps": steps}



async def _execute_task(user_id: str, task_id: str) -> None:
    task = _load_task(user_id, task_id)
    if not task:
        return

    run_id = "run_" + uuid.uuid4().hex[:6]
    started = datetime.now(timezone.utc)
    log.info("Executing task %s (run %s)", task_id, run_id)

    reply_to = task.get("reply_to")
    status = "success"
    output = ""
    steps: List[dict] = []
    try:
        ttype = task.get("task_type", "script")
        cfg = task.get("task_config", {})
        if ttype == "script":
            result = await asyncio.wait_for(
                _run_script_task(user_id, cfg), timeout=_TASK_TIMEOUT_S
            )
        elif ttype == "agent":
            result = await asyncio.wait_for(
                _run_agent_task(user_id, cfg, reply_to=reply_to), timeout=_TASK_TIMEOUT_S
            )
        else:
            result = {"output": f"未知任务类型: {ttype}", "success": False, "steps": []}

        output = result["output"]
        steps = result.get("steps", [])
        if not result["success"]:
            status = "error"
    except asyncio.TimeoutError:
        output = f"任务超时（>{_TASK_TIMEOUT_S}s）"
        status = "timeout"
        steps.append(_step("error", output))
    except Exception as e:
        output = str(e)
        status = "error"
        steps.append(_step("error", output))
        log.exception("Task %s run %s failed", task_id, run_id)

    finished = datetime.now(timezone.utc)
    run_record = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "status": status,
        "output": output[:4000],
        "steps": steps,
    }
    log.info("Task %s run %s finished: %s", task_id, run_id, status)

    # Reload task (may have been updated while running)
    task = _load_task(user_id, task_id)
    if not task:
        return
    runs = task.get("runs", [])
    runs.append(run_record)
    task["runs"] = runs[-_MAX_RUNS_STORED:]
    task["last_run_at"] = started.isoformat()
    task["next_run_at"] = _compute_next_run(task, finished)
    _save_task(user_id, task)


async def _execute_service_task(admin_id: str, service_id: str, task_id: str) -> None:
    """Execute a service-scoped scheduled task."""
    task = _load_service_task(admin_id, service_id, task_id)
    if not task:
        return

    run_id = "run_" + uuid.uuid4().hex[:6]
    started = datetime.now(timezone.utc)
    log.info("Executing service task %s/%s (run %s)", service_id, task_id, run_id)

    reply_to = task.get("reply_to") or {}
    status = "success"
    output = ""
    steps: List[dict] = []
    try:
        cfg = task.get("task_config", {})
        conv_id = reply_to.get("conversation_id", f"sched-{task_id}")

        result = await asyncio.wait_for(
            _run_service_agent_task(admin_id, service_id, conv_id, cfg,
                                   reply_to=reply_to or None),
            timeout=_TASK_TIMEOUT_S,
        )
        output = result["output"]
        steps = result.get("steps", [])
        if not result["success"]:
            status = "error"
    except asyncio.TimeoutError:
        output = f"任务超时（>{_TASK_TIMEOUT_S}s）"
        status = "timeout"
        steps.append(_step("error", output))
    except Exception as e:
        output = str(e)
        status = "error"
        steps.append(_step("error", output))
        log.exception("Service task %s/%s run %s failed", service_id, task_id, run_id)

    finished = datetime.now(timezone.utc)
    run_record = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "status": status,
        "output": output[:4000],
        "steps": steps,
    }
    log.info("Service task %s/%s run %s finished: %s", service_id, task_id, run_id, status)

    # Reload & persist
    task = _load_service_task(admin_id, service_id, task_id)
    if not task:
        return
    runs = task.get("runs", [])
    runs.append(run_record)
    task["runs"] = runs[-_MAX_RUNS_STORED:]
    task["last_run_at"] = started.isoformat()
    task["next_run_at"] = _compute_next_run(task, finished)
    _save_service_task(admin_id, service_id, task)


# ── Scheduler loop ────────────────────────────────────────────────────────

class TaskScheduler:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running_tasks: set = set()

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            log.info("Task scheduler started")

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Task scheduler stopped")

    async def _loop(self):
        while True:
            try:
                await self._check_all_tasks()
            except Exception:
                log.exception("Scheduler loop error")
            await asyncio.sleep(_LOOP_INTERVAL_S)

    async def _check_all_tasks(self):
        from app.core.security import USERS_DIR
        if not os.path.isdir(USERS_DIR):
            return
        now = datetime.now(timezone.utc)
        for uid in os.listdir(USERS_DIR):
            # Admin tasks
            tasks_dir = os.path.join(USERS_DIR, uid, "tasks")
            if os.path.isdir(tasks_dir):
                self._scan_dir(tasks_dir, now, uid)

            # Service tasks
            services_dir = os.path.join(USERS_DIR, uid, "services")
            if not os.path.isdir(services_dir):
                continue
            for svc_id in os.listdir(services_dir):
                svc_tasks = os.path.join(services_dir, svc_id, "tasks")
                if os.path.isdir(svc_tasks):
                    self._scan_service_dir(svc_tasks, now, uid, svc_id)

    def _scan_dir(self, tasks_dir: str, now: datetime, uid: str):
        for fname in os.listdir(tasks_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(tasks_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    task = json.load(f)
            except Exception:
                continue
            if not task.get("enabled"):
                continue
            next_run_str = task.get("next_run_at")
            if not next_run_str:
                continue
            try:
                next_run = datetime.fromisoformat(next_run_str)
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if next_run <= now:
                key = f"{uid}::{task['id']}"
                if key not in self._running_tasks:
                    self._running_tasks.add(key)
                    asyncio.create_task(self._run_and_cleanup(key, uid, task["id"]))

    def _scan_service_dir(self, tasks_dir: str, now: datetime, admin_id: str, service_id: str):
        for fname in os.listdir(tasks_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(tasks_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    task = json.load(f)
            except Exception:
                continue
            if not task.get("enabled"):
                continue
            next_run_str = task.get("next_run_at")
            if not next_run_str:
                continue
            try:
                next_run = datetime.fromisoformat(next_run_str)
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if next_run <= now:
                key = f"svc::{admin_id}::{service_id}::{task['id']}"
                if key not in self._running_tasks:
                    self._running_tasks.add(key)
                    asyncio.create_task(
                        self._run_service_and_cleanup(key, admin_id, service_id, task["id"])
                    )

    async def _run_and_cleanup(self, key: str, user_id: str, task_id: str):
        try:
            await _execute_task(user_id, task_id)
        finally:
            self._running_tasks.discard(key)

    async def _run_service_and_cleanup(self, key: str, admin_id: str, service_id: str, task_id: str):
        try:
            await _execute_service_task(admin_id, service_id, task_id)
        finally:
            self._running_tasks.discard(key)

    def _schedule_coro(self, key: str, coro) -> bool:
        """Schedule a coroutine on the event loop, thread-safe.

        Works both from async context (main thread) and from sync tools
        running in a thread pool.
        """
        self._running_tasks.add(key)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
            return True
        except RuntimeError:
            pass
        from app.services.inbox import _main_loop
        if _main_loop is not None and _main_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, _main_loop)
            return True
        self._running_tasks.discard(key)
        log.warning("Cannot schedule task %s: no event loop available", key)
        return False

    def run_now(self, user_id: str, task_id: str) -> bool:
        """Trigger an admin task immediately (thread-safe)."""
        key = f"{user_id}::{task_id}"
        return self._schedule_coro(key, self._run_and_cleanup(key, user_id, task_id))

    def run_service_task_now(self, admin_id: str, service_id: str, task_id: str) -> bool:
        """Trigger a service task immediately (thread-safe)."""
        key = f"svc::{admin_id}::{service_id}::{task_id}"
        return self._schedule_coro(
            key, self._run_service_and_cleanup(key, admin_id, service_id, task_id)
        )


# ── Singleton ─────────────────────────────────────────────────────────────

_scheduler: Optional[TaskScheduler] = None


def get_scheduler() -> TaskScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler
