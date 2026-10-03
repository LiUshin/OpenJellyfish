"""
WeChat channel API routes.

QR code generation, scan status polling, session management.
"""

import asyncio
import base64
import time
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from app.deps import get_current_user
from app.channels.wechat.client import generate_qrcode, poll_qrcode_status
from app.channels.wechat.session_manager import get_session_manager
from app.services.published import get_service, update_service
from app.channels.wechat.policy import ensure_wechat_active, ensure_wechat_session

log = logging.getLogger("wechat.router")

router = APIRouter(prefix="/api/wc", tags=["wechat"])

# Issued QR challenges are bound to a Service before returning them to a browser.
# A restart expires pending QR codes safely; a fresh scan is required.
_qr_challenges: dict[str, dict] = {}
_QR_TTL_SECONDS = 600
_QR_MAX_PENDING = 2000


def _prune_qr_challenges():
    now = time.monotonic()
    for qr_id, entry in list(_qr_challenges.items()):
        if entry["expires_at"] <= now and not entry["lock"].locked():
            _qr_challenges.pop(qr_id, None)



# ── schemas ─────────────────────────────────────────────────────────


class EnableWeChatRequest(BaseModel):
    enabled: bool = True
    expires_at: Optional[str] = None
    max_sessions: int = Field(default=100, ge=1, le=10000)


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

    _prune_qr_challenges()
    if len(_qr_challenges) >= _QR_MAX_PENDING:
        raise HTTPException(429, "扫码请求过多，请稍后重试")
    qr_data = await generate_qrcode()
    # Configuration can change while the provider request is pending.
    ensure_wechat_active(admin_id, service_id)
    _qr_challenges[qr_data["qr_id"]] = {
        "admin_id": admin_id, "service_id": service_id,
        "expires_at": time.monotonic() + _QR_TTL_SECONDS,
        "lock": asyncio.Lock(), "result": None,
    }

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

    challenge = _qr_challenges.get(qrcode)
    if not challenge or (challenge["admin_id"], challenge["service_id"]) != (admin_id, service_id):
        raise HTTPException(404, "二维码不存在，请重新获取")
    async with challenge["lock"]:
        if challenge["expires_at"] <= time.monotonic():
            _qr_challenges.pop(qrcode, None)
            raise HTTPException(410, "二维码已过期，请重新获取")
        ensure_wechat_active(admin_id, service_id)
        if challenge["result"] is not None:
            result = challenge["result"]
            ensure_wechat_session(admin_id, service_id, result["session_id"],
                                  conversation_id=result["conversation_id"])
            return result

        info = await poll_qrcode_status(qrcode)
        # Recheck after the network wait, before attaching any credentials.
        ensure_wechat_active(admin_id, service_id)
        if challenge["expires_at"] <= time.monotonic():
            raise HTTPException(410, "二维码已过期，请重新获取")
        status = info.get("status", "waiting")
        if status == "confirmed":
            if not all(info.get(key) for key in ("bot_token", "ilink_user_id", "ilink_bot_id")):
                raise HTTPException(502, "微信确认信息不完整，请重新扫码")
            manager = get_session_manager()
            session = await manager.create_session(
                admin_id=admin_id, service_id=service_id,
                bot_token=info["bot_token"], ilink_user_id=info["ilink_user_id"],
                ilink_bot_id=info["ilink_bot_id"],
                base_url=info.get("baseurl", "https://ilinkai.weixin.qq.com"),
            )
            manager.start_polling(session.session_id)
            result = {"status": "confirmed", "session_id": session.session_id,
                      "conversation_id": session.conversation_id}
            challenge["result"] = result
            return result
        if status == "expired":
            _qr_challenges.pop(qrcode, None)
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

    manager = get_session_manager()
    try:
        ensure_wechat_active(user["user_id"], service_id)
    except HTTPException:
        await manager.stop_service_polling(user["user_id"], service_id)
    else:
        manager.resume_service_polling(user["user_id"], service_id)
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
    try:
        svc = ensure_wechat_active(admin_id, service_id)
    except HTTPException as exc:
        return False, str(exc.detail)
    return True, "ok"
