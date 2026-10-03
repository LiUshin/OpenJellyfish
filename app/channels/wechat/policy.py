"""Current Service/channel authorization, shared by ingress and delivery.

Capacity is deliberately absent here: it governs creation, never an existing
session's right to receive messages. Naive legacy expiry dates use host time.
"""
from datetime import datetime, timezone

from fastapi import HTTPException


def ensure_service_active(admin_id: str, service_id: str) -> dict:
    from app.core.security import _load_users
    from app.services.published import get_service
    owner = _load_users().get(admin_id)
    if not owner or owner.get("disabled"):
        raise HTTPException(403, "管理员账号已不可用")
    service = get_service(admin_id, service_id)
    if not service:
        raise HTTPException(404, "Service 不存在")
    if not service.get("published", True):
        raise HTTPException(403, "Service 已下线")
    return service


def ensure_wechat_active(admin_id: str, service_id: str) -> dict:
    service = ensure_service_active(admin_id, service_id)
    channel = service.get("wechat_channel") or {}
    if not channel.get("enabled"):
        raise HTTPException(403, "微信渠道已停用")
    expiry = channel.get("expires_at")
    if expiry:
        try:
            deadline = datetime.fromisoformat(expiry)
            if deadline.tzinfo is None:
                deadline = deadline.astimezone()
        except (TypeError, ValueError):
            raise HTTPException(403, "微信渠道有效期配置无效")
        if deadline <= datetime.now(timezone.utc):
            raise HTTPException(403, "微信渠道已过期")
    return service


def ensure_wechat_session(admin_id: str, service_id: str, session_id: str,
                          *, conversation_id: str | None = None):
    ensure_wechat_active(admin_id, service_id)
    from app.channels.wechat.session_manager import get_session_manager
    from app.services.published import consumer_conversation_exists
    session = get_session_manager().get_session(session_id)
    if not session or (session.admin_id, session.service_id) != (admin_id, service_id):
        raise HTTPException(403, "微信会话已失效或不属于此 Service")
    if conversation_id is not None and session.conversation_id != conversation_id:
        raise HTTPException(403, "微信会话与投递会话不匹配")
    if not consumer_conversation_exists(admin_id, service_id, session.conversation_id):
        raise HTTPException(404, "Service 会话已删除")
    return session
