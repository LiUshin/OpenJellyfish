import os
import re
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse

from app.schemas.requests import CreateConversationRequest
from app.services.conversations import (
    list_conversations, create_conversation, get_conversation, delete_conversation,
    get_attachment_path,
)
from app.deps import get_current_user

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

MEDIA_MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".m4a": "audio/mp4", ".mp4": "video/mp4", ".webm": "video/webm",
    ".pdf": "application/pdf",
}


def _validate_conv_id(conv_id: str):
    if not re.match(r'^[a-zA-Z0-9_-]{1,36}$', conv_id):
        raise HTTPException(status_code=400, detail="无效的对话 ID")


@router.get("")
async def api_list_conversations(user=Depends(get_current_user)):
    return list_conversations(user["user_id"])


@router.post("")
async def api_create_conversation(req: CreateConversationRequest, user=Depends(get_current_user)):
    from app.runtime.chat import choice, bind_conversation
    actor = user['user_id']
    binding = choice(actor, req.runtime_choice.model_dump() if req.runtime_choice else None)
    conv = create_conversation(actor, req.title)
    try:
        return bind_conversation(actor, conv, binding, req.context_paths)
    except (ValueError, FileNotFoundError) as exc:
        delete_conversation(actor, conv['id'])
        raise HTTPException(400, str(exc))
    except Exception:
        delete_conversation(actor, conv['id'])
        raise


@router.get("/{conv_id}")
async def api_get_conversation(conv_id: str, user=Depends(get_current_user)):
    _validate_conv_id(conv_id)
    conv = get_conversation(user["user_id"], conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conv


@router.delete("/{conv_id}")
async def api_delete_conversation(conv_id: str, user=Depends(get_current_user)):
    _validate_conv_id(conv_id)
    conv = get_conversation(user['user_id'], conv_id)
    if conv and conv.get('runtime_session_id'):
        from app.runtime.manager import get_runtime
        from app.runtime.store import TERMINAL
        runtime = get_runtime()
        session = runtime.runs.own('session', conv['runtime_session_id'], user['user_id'])
        session['deleted'] = True
        runtime.store.put('session', session)
        for run in runtime.store.find('run', session_id=session['id'], actor_id=user['user_id']):
            if run['status'] not in TERMINAL:
                await runtime.runs.cancel(user['user_id'], run['id'])
        if hasattr(runtime.backend, 'release'):
            await runtime.backend.release(session_id=session['id'])
    if delete_conversation(user["user_id"], conv_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="对话不存在")


@router.get("/{conv_id}/attachments/{file_path:path}")
async def api_get_conversation_attachment(conv_id: str, file_path: str,
                                          user=Depends(get_current_user)):
    _validate_conv_id(conv_id)
    try:
        full = get_attachment_path(user["user_id"], conv_id, file_path)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="文件不存在")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="文件不存在")
    ext = os.path.splitext(file_path)[1].lower()
    media_type = MEDIA_MIME_MAP.get(ext, "application/octet-stream")
    return FileResponse(full, media_type=media_type,
                        headers={"Content-Disposition": "inline"})
