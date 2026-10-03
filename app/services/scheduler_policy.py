"""Validation shared by HTTP, tools and scheduled-task execution."""
import math
import re
import os
from datetime import datetime, timedelta, timezone

from app.core.path_security import safe_join


def permission_dirs(root: str, names: list[str]) -> list[str]:
    if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
        raise ValueError("permissions must contain lists of directory names")
    # Validate the whole list before creating anything. Resolve symlinks too.
    return [safe_join(root, "" if n.strip() == "*" else n.strip())
            for n in names if n.strip()]


def validate_task(task: dict, uid: str, service_id: str | None = None, *,
                  check_reply_session: bool = True) -> None:
    from croniter import croniter
    from app.core.security import get_user_filesystem_dir

    kind, value = task.get("schedule_type"), task.get("schedule", "")
    offset = task.get("tz_offset_hours", 8)
    if not isinstance(offset, (int, float)) or not math.isfinite(offset) or not -24 < offset < 24:
        raise ValueError("tz_offset_hours must be between -24 and 24")
    if kind == "interval":
        try:
            valid = 0 < int(value) <= 2147483647 and str(int(value)) == str(value).strip()
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("interval must be a positive integer number of seconds")
    elif kind == "cron":
        if not isinstance(value, str) or len(value.split()) != 5:
            raise ValueError("cron must have five fields")
        try:
            croniter(value, datetime.now(timezone(timedelta(hours=offset)))).get_next(datetime)
        except (ValueError, KeyError, OverflowError) as exc:
            raise ValueError("invalid cron schedule") from exc
    elif kind == "once":
        if value not in ("", "now"):
            try:
                datetime.fromisoformat(value)
            except (ValueError, TypeError) as exc:
                raise ValueError("once must be an ISO datetime, 'now', or empty") from exc
    else:
        raise ValueError("unknown schedule_type")
    if not isinstance(task.get("enabled"), bool):
        raise ValueError("enabled must be a boolean")
    if task.get("task_type") not in ("agent", "script") or (service_id and task.get("task_type") != "agent"):
        raise ValueError("invalid task_type for this scope")
    cfg = task.get("task_config")
    if not isinstance(cfg, dict):
        raise ValueError("task_config must be an object")
    perms = cfg.get("permissions", {})
    if not isinstance(perms, dict):
        raise ValueError("permissions must be an object")
    for key in ("read_dirs", "write_dirs"):
        if key in perms:
            permission_dirs(get_user_filesystem_dir(uid), perms[key])
    if "timeout" in cfg and (not isinstance(cfg["timeout"], (int, float)) or
                             not math.isfinite(cfg["timeout"]) or cfg["timeout"] <= 0):
        raise ValueError("timeout must be positive")
    validate_reply(task.get("reply_to"), uid, service_id, check_session=check_reply_session)


def validate_reply(reply: dict | None, uid: str, service_id: str | None = None, *,
                   check_session: bool = True) -> None:
    if reply is None:
        return
    if not isinstance(reply, dict) or reply.get("channel") not in ("web", "wechat"):
        raise ValueError("invalid reply_to channel")
    if reply.get("admin_id", uid) != uid or (reply.get("service_id") or None) != service_id:
        raise PermissionError("reply_to must belong to the task owner and service")
    for field in ('conversation_id', 'session_id'):
        value = reply.get(field)
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value)):
            raise PermissionError(f'Invalid reply_to {field}')
    if check_session and reply.get("channel") == "wechat" and service_id:
        from app.channels.wechat.session_manager import get_session_manager
        session = get_session_manager().get_session(reply.get("session_id", ""))
        if (not session or session.admin_id != uid or session.service_id != service_id or
                session.conversation_id != reply.get("conversation_id")):
            raise PermissionError("reply_to session does not match this task's conversation")


def service_doc_paths(uid: str, service_id: str, paths) -> list[str]:
    """Authorize canonical file paths, never the ancestor-listing exception."""
    from app.core.security import get_user_filesystem_dir
    from app.services.published import get_service
    service = get_service(uid, service_id)
    if not service:
        raise ValueError("service does not exist")
    root = os.path.realpath(os.path.join(get_user_filesystem_dir(uid), "docs"))
    grants = service.get("allowed_docs") or []
    allowed = permission_dirs(root, grants)
    resolved = []
    for path in [paths] if isinstance(paths, str) else paths:
        clean = path.replace("\\", "/").lstrip("/")
        if clean.startswith("docs/") and not os.path.exists(safe_join(root, clean)):
            clean = clean[5:]
        target = safe_join(root, clean)
        if not any(target == base or target.startswith(base + os.sep) for base in allowed):
            raise PermissionError(f"Document is not published to this service: {path}")
        resolved.append(os.path.relpath(target, root))
    return resolved
