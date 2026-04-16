"""
Admin WeChat API — allows the admin to connect their own
main agent via WeChat QR scan.

Session persistence: saved to users/{user_id}/admin_wechat_session.json
so connections survive Docker/server restarts.
"""

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional, Dict

from fastapi import APIRouter, HTTPException, Depends

from app.deps import get_current_user
from app.channels.wechat.client import ILinkClient, generate_qrcode, poll_qrcode_status
from app.services.conversations import create_conversation, get_conversation

log = logging.getLogger("wechat.admin_router")

router = APIRouter(prefix="/api/admin/wechat", tags=["admin-wechat"])

_admin_sessions: Dict[str, dict] = {}
_admin_poll_tasks: Dict[str, asyncio.Task] = {}
_admin_confirmed_qrcodes: Dict[str, dict] = {}

_PERSIST_FIELDS = (
    "user_id", "conversation_id", "bot_token", "ilink_user_id",
    "ilink_bot_id", "base_url", "connected", "connected_at",
    "context_token", "from_user_id",
)


def _session_path(user_id: str) -> str:
    from app.core.security import USERS_DIR
    return os.path.join(USERS_DIR, user_id, "admin_wechat_session.json")


def _save_admin_session(user_id: str):
    session = _admin_sessions.get(user_id)
    if not session:
        return
    data = {k: session.get(k, "") for k in _PERSIST_FIELDS}
    path = _session_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(path, data, ensure_ascii=False, indent=2)


def _delete_admin_session_file(user_id: str):
    path = _session_path(user_id)
    try:
        os.remove(path)
    except OSError:
        pass


def _get_session(user_id: str) -> Optional[dict]:
    return _admin_sessions.get(user_id)


@router.post("/qrcode")
async def api_admin_qrcode(user=Depends(get_current_user)):
    """Generate iLink QR code for admin to scan."""
    user_id = user["user_id"]

    existing = _get_session(user_id)
    if existing and existing.get("connected"):
        raise HTTPException(
            status_code=409,
            detail="已有活跃的微信连接，请先断开再重新扫码",
        )

    from app.channels.wechat.rate_limiter import check_qr_rate
    ok, reason = check_qr_rate(f"admin_{user_id}")
    if not ok:
        raise HTTPException(status_code=429, detail=reason)

    qr_data = await generate_qrcode()
    return {
        "qr_id": qr_data["qr_id"],
        "qr_image_b64": base64.b64encode(qr_data["qr_image_png"]).decode(),
        "qr_url": qr_data["qr_url"],
    }


@router.get("/qrcode/status")
async def api_admin_qrcode_status(qrcode: str, user=Depends(get_current_user)):
    """Poll QR scan status. On confirmed, create session + conversation."""
    user_id = user["user_id"]
    if qrcode in _admin_confirmed_qrcodes:
        return _admin_confirmed_qrcodes[qrcode]

    info = await poll_qrcode_status(qrcode)
    status = info.get("status", "waiting")

    if status == "confirmed":
        bot_token = info.get("bot_token", "")
        ilink_user_id = info.get("ilink_user_id", "")
        ilink_bot_id = info.get("ilink_bot_id", "")
        base_url = info.get("baseurl", "https://ilinkai.weixin.qq.com")

        conv = create_conversation(user_id, title="微信对话")

        client = ILinkClient(
            bot_token=bot_token,
            ilink_user_id=ilink_user_id,
            ilink_bot_id=ilink_bot_id,
            base_url=base_url,
        )

        session = {
            "user_id": user_id,
            "conversation_id": conv["id"],
            "client": client,
            "bot_token": bot_token,
            "ilink_user_id": ilink_user_id,
            "ilink_bot_id": ilink_bot_id,
            "base_url": base_url,
            "connected": True,
            "connected_at": datetime.now().isoformat(),
            "context_token": "",
            "from_user_id": "",
        }
        _admin_sessions[user_id] = session
        _save_admin_session(user_id)

        _start_admin_polling(user_id)

        result = {
            "status": "confirmed",
            "conversation_id": conv["id"],
        }
        _admin_confirmed_qrcodes[qrcode] = result
        return result

    return {"status": status}


@router.get("/session")
async def api_admin_session(user=Depends(get_current_user)):
    """Get current admin WeChat session info."""
    user_id = user["user_id"]
    session = _get_session(user_id)
    if not session or not session.get("connected"):
        return {"connected": False}

    return {
        "connected": True,
        "conversation_id": session["conversation_id"],
        "connected_at": session.get("connected_at", ""),
        "from_user_id": session.get("from_user_id", ""),
    }


@router.delete("/session")
async def api_admin_disconnect(user=Depends(get_current_user)):
    """Disconnect admin WeChat session."""
    user_id = user["user_id"]
    await _remove_admin_session(user_id)
    return {"success": True}


@router.get("/messages")
async def api_admin_messages(user=Depends(get_current_user)):
    """Get messages from admin's WeChat conversation."""
    user_id = user["user_id"]
    session = _get_session(user_id)
    if not session:
        return {"messages": []}

    conv = get_conversation(user_id, session["conversation_id"])
    if not conv:
        return {"messages": []}
    return {"messages": conv.get("messages", [])}


# ── polling ──────────────────────────────────────────────────────────


def _start_admin_polling(user_id: str):
    if user_id in _admin_poll_tasks:
        task = _admin_poll_tasks[user_id]
        if not task.done():
            return
    _admin_poll_tasks[user_id] = asyncio.create_task(_admin_poll_loop(user_id))
    log.info("Admin WeChat polling started for %s", user_id)


async def _admin_poll_loop(user_id: str):
    consecutive_errors = 0
    while user_id in _admin_sessions:
        session = _admin_sessions.get(user_id)
        if not session or not session.get("connected"):
            break
        client: ILinkClient = session["client"]
        try:
            msgs = await client.get_updates()
            consecutive_errors = 0

            for msg in msgs:
                from_user = msg.get("from_user_id", "")
                if from_user and not session.get("from_user_id"):
                    session["from_user_id"] = from_user
                    _save_admin_session(user_id)

                from app.channels.wechat.admin_bridge import handle_admin_wechat_message
                try:
                    await handle_admin_wechat_message(session, msg)
                except Exception:
                    log.exception("Admin bridge error (user=%s)", user_id)

        except asyncio.CancelledError:
            break
        except Exception:
            consecutive_errors += 1
            backoff = min(5 * (2 ** min(consecutive_errors - 1, 5)), 300)
            log.warning("Admin poll error (user=%s, attempt=%d), retry in %ds",
                        user_id, consecutive_errors, backoff)
            if consecutive_errors >= 20:
                log.error("Too many errors, disconnecting admin %s", user_id)
                await _remove_admin_session(user_id)
                break
            await asyncio.sleep(backoff)


async def _remove_admin_session(user_id: str):
    task = _admin_poll_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()

    session = _admin_sessions.pop(user_id, None)
    if session:
        client = session.get("client")
        if client:
            await client.close()
    _delete_admin_session_file(user_id)
    log.info("Admin WeChat session disconnected: %s", user_id)


async def shutdown_admin_sessions():
    """Called on app shutdown — stop polling but keep session files for restart."""
    for uid in list(_admin_sessions.keys()):
        task = _admin_poll_tasks.pop(uid, None)
        if task and not task.done():
            task.cancel()
        session = _admin_sessions.pop(uid, None)
        if session:
            client = session.get("client")
            if client:
                await client.close()


async def restore_admin_sessions():
    """Scan all users and restore persisted admin WeChat sessions."""
    from app.core.security import USERS_DIR
    if not os.path.isdir(USERS_DIR):
        return

    count = 0
    for user_id in os.listdir(USERS_DIR):
        path = _session_path(user_id)
        if not os.path.isfile(path):
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning("Failed to read admin session file: %s", path)
            continue

        if not data.get("connected") or not data.get("bot_token"):
            _delete_admin_session_file(user_id)
            continue

        client = ILinkClient(
            bot_token=data["bot_token"],
            ilink_user_id=data.get("ilink_user_id", ""),
            ilink_bot_id=data.get("ilink_bot_id", ""),
            base_url=data.get("base_url", "https://ilinkai.weixin.qq.com"),
        )

        session = {
            "user_id": user_id,
            "conversation_id": data.get("conversation_id", ""),
            "client": client,
            "bot_token": data["bot_token"],
            "ilink_user_id": data.get("ilink_user_id", ""),
            "ilink_bot_id": data.get("ilink_bot_id", ""),
            "base_url": data.get("base_url", "https://ilinkai.weixin.qq.com"),
            "connected": True,
            "connected_at": data.get("connected_at", ""),
            "context_token": data.get("context_token", ""),
            "from_user_id": data.get("from_user_id", ""),
        }
        _admin_sessions[user_id] = session
        _start_admin_polling(user_id)
        count += 1
        log.info("Restored admin WeChat session for %s (conv=%s)",
                 user_id, data.get("conversation_id", ""))

    if count:
        log.info("Restored %d admin WeChat session(s)", count)
