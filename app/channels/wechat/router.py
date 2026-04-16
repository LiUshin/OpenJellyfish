"""
WeChat channel API routes.

QR code generation, scan status polling, session management.
"""

import base64
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.deps import get_current_user
from app.channels.wechat.client import generate_qrcode, poll_qrcode_status
from app.channels.wechat.session_manager import get_session_manager
from app.services.published import get_service, update_service

log = logging.getLogger("wechat.router")

router = APIRouter(prefix="/api/wc", tags=["wechat"])

_confirmed_qrcodes: dict[str, dict] = {}


# ── schemas ─────────────────────────────────────────────────────────


class EnableWeChatRequest(BaseModel):
    enabled: bool = True
    expires_at: Optional[str] = None
    max_sessions: int = 100


# ── public endpoints (no auth — used by QR scan visitors) ───────────


@router.get("/{service_id}/qrcode")
async def api_generate_qrcode(service_id: str):
    """Generate a fresh iLink QR code for a service."""
    admin_id = _find_service_admin(service_id)
    if not admin_id:
        raise HTTPException(status_code=404, detail="Service not found")

    ok, reason = _check_wechat_enabled(admin_id, service_id)
    if not ok:
        raise HTTPException(status_code=403, detail=reason)

    from app.channels.wechat.rate_limiter import check_qr_rate
    qr_ok, qr_reason = check_qr_rate(service_id)
    if not qr_ok:
        raise HTTPException(status_code=429, detail=qr_reason)

    qr_data = await generate_qrcode()

    return {
        "qr_id": qr_data["qr_id"],
        "qr_image_b64": base64.b64encode(qr_data["qr_image_png"]).decode(),
        "qr_url": qr_data["qr_url"],
    }


@router.get("/{service_id}/qrcode/status")
async def api_qrcode_status(service_id: str, qrcode: str):
    """Poll iLink QR scan status. On confirmed, creates session + conversation."""
    admin_id = _find_service_admin(service_id)
    if not admin_id:
        raise HTTPException(status_code=404, detail="Service not found")

    if qrcode in _confirmed_qrcodes:
        return _confirmed_qrcodes[qrcode]

    info = await poll_qrcode_status(qrcode)
    status = info.get("status", "waiting")

    if status == "confirmed":
        bot_token = info.get("bot_token", "")
        ilink_user_id = info.get("ilink_user_id", "")
        ilink_bot_id = info.get("ilink_bot_id", "")
        base_url = info.get("baseurl", "https://ilinkai.weixin.qq.com")

        manager = get_session_manager()
        session = await manager.create_session(
            admin_id=admin_id,
            service_id=service_id,
            bot_token=bot_token,
            ilink_user_id=ilink_user_id,
            ilink_bot_id=ilink_bot_id,
            base_url=base_url,
        )
        manager.start_polling(session.session_id)

        result = {
            "status": "confirmed",
            "session_id": session.session_id,
            "conversation_id": session.conversation_id,
        }
        _confirmed_qrcodes[qrcode] = result

        if len(_confirmed_qrcodes) > 500:
            oldest = list(_confirmed_qrcodes.keys())[:250]
            for k in oldest:
                _confirmed_qrcodes.pop(k, None)

        return result

    return {"status": status}


# ── admin endpoints (require auth) ──────────────────────────────────


@router.put("/{service_id}/config")
async def api_configure_wechat(
    service_id: str,
    req: EnableWeChatRequest,
    user=Depends(get_current_user),
):
    """Enable/disable WeChat channel for a service."""
    svc = get_service(user["user_id"], service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    wc_config = {
        "enabled": req.enabled,
        "expires_at": req.expires_at,
        "max_sessions": req.max_sessions,
        "updated_at": datetime.now().isoformat(),
    }

    caps = svc.get("capabilities", [])
    if req.enabled and "humanchat" not in caps:
        caps.append("humanchat")
    elif not req.enabled and "humanchat" in caps:
        caps.remove("humanchat")

    update_service(user["user_id"], service_id, {
        "wechat_channel": wc_config,
        "capabilities": caps,
    })

    from app.services.consumer_agent import clear_consumer_cache
    clear_consumer_cache(admin_id=user["user_id"], service_id=service_id)

    return {"success": True, "wechat_channel": wc_config}


@router.get("/{service_id}/sessions")
async def api_list_sessions(service_id: str, user=Depends(get_current_user)):
    """List active WeChat sessions for a service (admin-scoped)."""
    admin_id = user["user_id"]
    svc = get_service(admin_id, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    manager = get_session_manager()
    sessions = manager.list_sessions(service_id=service_id, admin_id=admin_id)

    return [
        {
            "session_id": s.session_id,
            "conversation_id": s.conversation_id,
            "from_user_id": s.from_user_id,
            "created_at": s.created_at,
            "last_active_at": s.last_active_at,
        }
        for s in sessions
    ]


@router.get("/{service_id}/sessions/{session_id}/messages")
async def api_session_messages(
    service_id: str, session_id: str, user=Depends(get_current_user)
):
    """Get conversation messages for a WeChat session."""
    admin_id = user["user_id"]
    svc = get_service(admin_id, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    manager = get_session_manager()
    session = manager.get_session(session_id)
    if not session or session.service_id != service_id or session.admin_id != admin_id:
        raise HTTPException(status_code=404, detail="Session not found")

    from app.services.published import get_consumer_conversation
    conv = get_consumer_conversation(admin_id, service_id, session.conversation_id)
    if not conv:
        return {"messages": []}
    return {"messages": conv.get("messages", [])}


@router.delete("/{service_id}/sessions/{session_id}")
async def api_remove_session(
    service_id: str, session_id: str, user=Depends(get_current_user)
):
    """Disconnect a WeChat session (admin-scoped)."""
    admin_id = user["user_id"]
    svc = get_service(admin_id, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    manager = get_session_manager()
    session = manager.get_session(session_id)
    if not session or session.service_id != service_id or session.admin_id != admin_id:
        raise HTTPException(status_code=404, detail="Session not found")

    await manager.remove_session(session_id)
    return {"success": True}


# ── helpers ─────────────────────────────────────────────────────────


def _find_service_admin(service_id: str) -> Optional[str]:
    """Locate admin_id that owns service_id."""
    import os
    from app.core.security import USERS_DIR
    if not os.path.isdir(USERS_DIR):
        return None
    for uid in os.listdir(USERS_DIR):
        svc_cfg = os.path.join(USERS_DIR, uid, "services", service_id, "config.json")
        if os.path.isfile(svc_cfg):
            return uid
    return None


def _check_wechat_enabled(admin_id: str, service_id: str) -> tuple[bool, str]:
    svc = get_service(admin_id, service_id)
    if not svc:
        return False, "Service not found"
    if not svc.get("published", True):
        return False, "Service not published"
    wc = svc.get("wechat_channel", {})
    if not wc.get("enabled"):
        return False, "WeChat channel not enabled"
    expires_at = wc.get("expires_at")
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at).replace(tzinfo=None) < datetime.now():
                return False, "WeChat channel expired"
        except ValueError:
            pass
    max_sessions = wc.get("max_sessions", 100)
    manager = get_session_manager()
    current = len(manager.list_sessions(service_id))
    if current >= max_sessions:
        return False, f"已达最大会话数 ({max_sessions})"
    return True, "ok"
