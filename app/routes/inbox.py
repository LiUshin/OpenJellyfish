"""Authenticated admin feedback management and bound replies."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_current_user
from app.services.inbox import (
    list_inbox, get_inbox_message, update_inbox_status,
    delete_inbox_message, count_unread, reply_to_inbox, retry_delivery,
)
from app.services.service_messaging import MessagingConflict

router = APIRouter(prefix='/api/inbox', tags=['inbox'])


class InboxReply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=16000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class InboxStatus(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Optional[str] = None
    case_status: Optional[str] = None


class DeliveryRetry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    allow_unknown: bool = False


def _error(exc):
    if isinstance(exc, KeyError):
        return HTTPException(404, '反馈或投递不存在')
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(409 if isinstance(exc, MessagingConflict) else 400, str(exc))


@router.get('')
async def api_list_inbox(status: Optional[str] = None, user=Depends(get_current_user)):
    if status not in (None, 'unread', 'read', 'handled'):
        raise HTTPException(400, 'status 必须为 unread/read/handled')
    return {'messages': list_inbox(user['user_id'], status=status), 'unread_count': count_unread(user['user_id'])}


@router.get('/unread-count')
async def api_unread_count(user=Depends(get_current_user)):
    return {'count': count_unread(user['user_id'])}


@router.get('/{msg_id}')
async def api_get_message(msg_id: str, user=Depends(get_current_user)):
    msg = get_inbox_message(user['user_id'], msg_id)
    if not msg:
        raise HTTPException(404, '反馈不存在')
    return msg


@router.put('/{msg_id}')
async def api_update_status(msg_id: str, body: InboxStatus, user=Depends(get_current_user)):
    if body.status is None and body.case_status is None:
        raise HTTPException(400, '请指定反馈状态')
    try:
        msg = update_inbox_status(user['user_id'], msg_id, body.status, case_status=body.case_status)
    except ValueError as exc:
        raise _error(exc) from exc
    if not msg:
        raise HTTPException(404, '反馈不存在')
    return msg


@router.delete('/{msg_id}')
async def api_delete_message(msg_id: str, user=Depends(get_current_user)):
    if not delete_inbox_message(user['user_id'], msg_id):
        raise HTTPException(404, '反馈不存在')
    return {'ok': True}


@router.post('/{msg_id}/replies')
async def api_reply(msg_id: str, body: InboxReply, user=Depends(get_current_user)):
    try:
        return reply_to_inbox(user['user_id'], msg_id, body.message, idempotency_key=body.idempotency_key)
    except (KeyError, ValueError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post('/{msg_id}/deliveries/{delivery_id}/retry')
async def api_retry_delivery(msg_id: str, delivery_id: str, body: DeliveryRetry, user=Depends(get_current_user)):
    try:
        return retry_delivery(user['user_id'], msg_id, delivery_id, allow_unknown=body.allow_unknown)
    except (KeyError, ValueError, PermissionError) as exc:
        raise _error(exc) from exc
