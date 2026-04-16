"""
Admin inbox API — view and manage notifications from service agents.
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Optional

from app.deps import get_current_user
from app.services.inbox import (
    list_inbox, get_inbox_message, update_inbox_status,
    delete_inbox_message, count_unread,
)

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


@router.get("")
async def api_list_inbox(
    status: Optional[str] = None,
    user=Depends(get_current_user),
):
    messages = list_inbox(user["user_id"], status=status)
    return {"messages": messages, "unread_count": count_unread(user["user_id"])}


@router.get("/unread-count")
async def api_unread_count(user=Depends(get_current_user)):
    return {"count": count_unread(user["user_id"])}


@router.get("/{msg_id}")
async def api_get_message(msg_id: str, user=Depends(get_current_user)):
    msg = get_inbox_message(user["user_id"], msg_id)
    if not msg:
        raise HTTPException(404, "消息不存在")
    return msg


@router.put("/{msg_id}")
async def api_update_status(
    msg_id: str,
    body: dict,
    user=Depends(get_current_user),
):
    status = body.get("status")
    if status not in ("unread", "read", "handled"):
        raise HTTPException(400, "status 必须为 unread/read/handled")
    msg = update_inbox_status(user["user_id"], msg_id, status)
    if not msg:
        raise HTTPException(404, "消息不存在")
    return msg


@router.delete("/{msg_id}")
async def api_delete_message(msg_id: str, user=Depends(get_current_user)):
    ok = delete_inbox_message(user["user_id"], msg_id)
    if not ok:
        raise HTTPException(404, "消息不存在")
    return {"ok": True}
