"""
Admin inbox — receives notifications from service agents via contact_admin.

Storage: {USERS_DIR}/{admin_id}/inbox/{msg_id}.json
Each message can optionally trigger a read-only admin agent to evaluate
whether to forward the notification to the admin's WeChat.
"""

import json
import os
import uuid
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.core.security import USERS_DIR

log = logging.getLogger("inbox")

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop):
    """Cache the main event loop so sync tools (running in thread pool) can
    schedule coroutines back onto it via run_coroutine_threadsafe."""
    global _main_loop
    _main_loop = loop


def _inbox_dir(admin_id: str) -> str:
    return os.path.join(USERS_DIR, admin_id, "inbox")


def _msg_path(admin_id: str, msg_id: str) -> str:
    return os.path.join(_inbox_dir(admin_id), f"{msg_id}.json")


def _load_msg(admin_id: str, msg_id: str) -> Optional[Dict[str, Any]]:
    path = _msg_path(admin_id, msg_id)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_msg(admin_id: str, msg: Dict[str, Any]):
    d = _inbox_dir(admin_id)
    os.makedirs(d, exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(_msg_path(admin_id, msg["id"]), msg, ensure_ascii=False, indent=2)


# ===== CRUD =====

def list_inbox(admin_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    d = _inbox_dir(admin_id)
    if not os.path.isdir(d):
        return []
    msgs = []
    for fname in os.listdir(d):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(d, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                msg = json.load(f)
            if status and msg.get("status") != status:
                continue
            msgs.append(msg)
        except Exception:
            continue
    msgs.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return msgs


def get_inbox_message(admin_id: str, msg_id: str) -> Optional[Dict[str, Any]]:
    return _load_msg(admin_id, msg_id)


def update_inbox_status(admin_id: str, msg_id: str, status: str) -> Optional[Dict[str, Any]]:
    msg = _load_msg(admin_id, msg_id)
    if not msg:
        return None
    msg["status"] = status
    if status in ("read", "handled"):
        msg["handled_by"] = "manual"
    _save_msg(admin_id, msg)
    return msg


def delete_inbox_message(admin_id: str, msg_id: str) -> bool:
    path = _msg_path(admin_id, msg_id)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def count_unread(admin_id: str) -> int:
    d = _inbox_dir(admin_id)
    if not os.path.isdir(d):
        return 0
    count = 0
    for fname in os.listdir(d):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fname), "r", encoding="utf-8") as f:
                msg = json.load(f)
            if msg.get("status") == "unread":
                count += 1
        except Exception:
            continue
    return count


# ===== Post + Agent trigger =====

def post_to_inbox(
    admin_id: str,
    service_id: str,
    conversation_id: str,
    message: str,
    wechat_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a message to admin's inbox and optionally trigger admin agent.

    Returns {"id": ..., "summary": ...}.
    """
    from app.services.published import get_service

    svc = get_service(admin_id, service_id)
    svc_name = svc.get("name", service_id) if svc else service_id

    wechat_user_id = ""
    wechat_session_display = ""
    if wechat_session_id:
        try:
            from app.channels.wechat.session_manager import get_session_manager
            sess = get_session_manager().get_session(wechat_session_id)
            if sess:
                wechat_user_id = sess.from_user_id or ""
                wechat_session_display = wechat_session_id
        except Exception:
            pass

    msg_id = f"inbox_{uuid.uuid4().hex[:8]}"
    msg = {
        "id": msg_id,
        "service_id": service_id,
        "service_name": svc_name,
        "conversation_id": conversation_id,
        "wechat_session_id": wechat_session_display,
        "wechat_user_id": wechat_user_id,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "unread",
        "handled_by": None,
        "agent_response": None,
    }
    _save_msg(admin_id, msg)
    log.info("Inbox message %s from service %s (user=%s): %s",
             msg_id, svc_name, wechat_user_id or "unknown", message[:100])

    admin_wc = _get_admin_wechat_session(admin_id)
    if admin_wc and admin_wc.get("connected"):
        coro = _trigger_inbox_agent(
            admin_id, msg_id, svc_name, message, admin_wc,
            wechat_user_id=wechat_user_id,
        )
        scheduled = False
        # Try current event loop first (works when called from async context)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
            scheduled = True
        except RuntimeError:
            pass
        # Fallback: sync tool running in thread pool — use cached main loop
        if not scheduled and _main_loop is not None and _main_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, _main_loop)
            scheduled = True
        if scheduled:
            return {"id": msg_id, "summary": "已通知管理员，消息已记录到收件箱。微信通知已触发。"}
        else:
            log.warning("Cannot schedule inbox agent: no event loop available")

    return {"id": msg_id, "summary": "已通知管理员，消息已记录到收件箱。"}


def _get_admin_wechat_session(admin_id: str) -> Optional[dict]:
    try:
        from app.channels.wechat.admin_router import _get_session
        return _get_session(admin_id)
    except Exception:
        return None


async def _trigger_inbox_agent(
    admin_id: str,
    msg_id: str,
    service_name: str,
    message: str,
    admin_wc: dict,
    wechat_user_id: str = "",
):
    """Run a minimal admin agent to evaluate and optionally forward to WeChat."""
    try:
        from app.services.agent import create_user_agent, _get_default_model
        from app.services.scheduler import (
            _resolve_wechat_client, _run_agent_loop, _step,
        )
        from app.services.memory_tools import load_recent_inbox

        # Inject recent inbox history so the agent knows what was already handled
        recent_inbox = load_recent_inbox(admin_id, last_n=3)
        inbox_ctx = ""
        if recent_inbox:
            inbox_ctx = f"[最近收件箱记录]\n---\n{recent_inbox}\n---\n\n"

        user_line = f"来源微信用户：{wechat_user_id}\n" if wechat_user_id else ""
        prompt = (
            "[系统指令 - Service 收件箱通知]\n"
            "以下是来自 Service Agent 的通知，不是来自终端用户的消息。\n\n"
            f"{inbox_ctx}"
            f"[新通知] 来自 Service「{service_name}」的反馈：\n"
            f"{user_line}\n"
            f"{message}\n\n"
            "请评估这条信息的重要性和紧急程度。\n"
            "如果你认为管理员需要立即知道，请用 send_message 通知管理员。\n"
            "如果不够重要或已经通知过类似内容，可以不发送通知。\n"
            "回复时简洁说明来源和内容要点。\n\n"
            "---\n"
            "send_message 工具将消息发送给管理员本人（微信）。"
        )

        model = _get_default_model()
        agent = create_user_agent(admin_id, model=model, capabilities=["humanchat"])

        reply_to = {
            "channel": "wechat",
            "admin_id": admin_id,
            "service_id": None,
            "session_id": "",
            "conversation_id": admin_wc.get("conversation_id", ""),
        }

        wechat_client, wechat_to_user, wechat_ctx_token = _resolve_wechat_client(reply_to)
        if not wechat_client:
            log.warning("Inbox agent for %s: admin WeChat client not resolved, "
                        "notification will not be forwarded to WeChat", admin_id)
        elif not wechat_to_user:
            log.warning("Inbox agent for %s: from_user_id is empty, "
                        "WeChat delivery may fail", admin_id)

        # Stable thread_id per admin so the inbox agent accumulates context
        thread_id = f"inbox-{admin_id}"
        agent_config = {"configurable": {"thread_id": thread_id}}
        input_payload = {"messages": [{"role": "user", "content": prompt}]}
        steps = []
        output_parts = []

        await _run_agent_loop(
            agent, input_payload, agent_config, steps, output_parts,
            wechat_client=wechat_client,
            wechat_to_user=wechat_to_user or "",
            wechat_ctx_token=wechat_ctx_token or "",
            user_id=admin_id,
        )

        sent = any(s.get("type") == "wechat_send" for s in steps)
        msg = _load_msg(admin_id, msg_id)
        if msg:
            msg["status"] = "handled"
            msg["handled_by"] = "agent"
            msg["agent_response"] = "\n".join(output_parts)[:2000] if output_parts else None
            _save_msg(admin_id, msg)

        log.info("Inbox agent for %s completed (sent_to_wechat=%s)", msg_id, sent)

    except Exception:
        log.exception("Failed to run inbox agent for %s", msg_id)
