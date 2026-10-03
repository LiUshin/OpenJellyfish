import os
import re
from fastapi import APIRouter, HTTPException, Depends, Header, Query
from fastapi.responses import FileResponse

from app.schemas.requests import CreateConversationRequest, MoveConversationProjectRequest, SetConversationTestModeRequest
from app.services.conversations import (
    list_conversations, create_conversation, get_conversation, delete_conversation,
    get_attachment_path, get_conversation_meta,
)
from app.services.projects import assign_conversation_project, create_project_conversation
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
    try:
        conv = (create_project_conversation(actor, req.title, req.project_id)
                if req.project_id is not None else create_conversation(actor, req.title))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    try:
        bound = bind_conversation(actor, conv, binding, req.context_paths)
        # A concurrent delete may have ungrouped the conversation during bind.
        return {**bound, "project_id": (get_conversation_meta(actor, conv['id']) or {}).get("project_id")}
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


@router.patch("/{conv_id}/project")
async def api_move_conversation_project(conv_id: str, req: MoveConversationProjectRequest,
                                        user=Depends(get_current_user)):
    _validate_conv_id(conv_id)
    try:
        meta = assign_conversation_project(user["user_id"], conv_id, req.project_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    if meta is None:
        raise HTTPException(404, "对话不存在")
    return meta


@router.patch("/{conv_id}/test-mode")
async def api_set_conversation_test_mode(conv_id: str, req: SetConversationTestModeRequest,
                                         user=Depends(get_current_user)):
    _validate_conv_id(conv_id)
    from app.services.service_test import set_test_mode
    return set_test_mode(user["user_id"], conv_id, req.service_id)


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


@router.get("/{conv_id}/test-files/{service_id}/{preview_id}/{file_path:path}")
async def api_get_service_test_file(conv_id: str, service_id: str, preview_id: str,
                                    file_path: str, token: str | None = Query(None),
                                    authorization: str | None = Header(None),
                                    download: bool = Query(False)):
    """Serve only this admin's preview artifacts, including after mode closes."""
    from app.core.security import verify_token
    from app.services.published import get_consumer_conversation
    from app.storage import get_storage_service
    _validate_conv_id(conv_id)
    credential = token or (authorization.removeprefix('Bearer ') if authorization else '')
    user = verify_token(credential)
    if not user:
        raise HTTPException(401, '无效的管理员登录凭证')
    uid = user['user_id']
    if not get_conversation_meta(uid, conv_id):
        raise HTTPException(404, '对话不存在')
    try:
        preview = get_consumer_conversation(uid, service_id, preview_id)
    except HTTPException:
        raise HTTPException(404, '测试文件不存在')
    if not preview or preview.get('source') != 'admin_test' or preview.get('admin_conversation_id') != conv_id:
        raise HTTPException(404, '测试文件不存在')
    try:
        return get_storage_service().consumer_file_response(
            uid, service_id, preview_id, file_path, download=download)
    except (ValueError, FileNotFoundError, PermissionError):
        raise HTTPException(404, '测试文件不存在')


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
