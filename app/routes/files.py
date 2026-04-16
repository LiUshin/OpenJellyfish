import os
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File

from app.schemas.requests import WriteFileRequest, EditFileRequest, MoveFileRequest
from app.deps import get_current_user
from app.core import security
from app.storage import get_storage_service

router = APIRouter(prefix="/api/files", tags=["files"])

MEDIA_MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".m4a": "audio/mp4", ".flac": "audio/flac", ".aac": "audio/aac",
    ".mp4": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg",
    ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
    ".pdf": "application/pdf",
    ".html": "text/html", ".htm": "text/html",
}


@router.get("")
async def api_list_files(path: str = "/", user=Depends(get_current_user)):
    storage = get_storage_service()
    entries = storage.list_dir(user["user_id"], path)
    return [
        {"name": e.name, "path": e.path, "is_dir": e.is_dir,
         "size": e.size, "modified_at": e.modified_at}
        for e in entries
    ]


@router.get("/read")
async def api_read_file(path: str, user=Depends(get_current_user)):
    storage = get_storage_service()
    if not storage.is_file(user["user_id"], path):
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        content = storage.read_text(user["user_id"], path)
        return {"path": path, "content": content}
    except UnicodeDecodeError:
        return {"path": path, "content": "[二进制文件，无法预览]", "binary": True}


@router.post("/write")
async def api_write_file(req: WriteFileRequest, user=Depends(get_current_user)):
    storage = get_storage_service()
    storage.write_text(user["user_id"], req.path, req.content)
    return {"success": True, "path": req.path}


@router.put("/edit")
async def api_edit_file(req: EditFileRequest, user=Depends(get_current_user)):
    storage = get_storage_service()
    if not storage.is_file(user["user_id"], req.path):
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        storage.edit_text(user["user_id"], req.path, req.old_string, req.new_string)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "path": req.path}


@router.delete("")
async def api_delete_file(path: str, user=Depends(get_current_user)):
    storage = get_storage_service()
    if not storage.exists(user["user_id"], path):
        raise HTTPException(status_code=404, detail="文件不存在")
    storage.delete(user["user_id"], path)
    return {"success": True}


@router.post("/move")
async def api_move_file(req: MoveFileRequest, user=Depends(get_current_user)):
    storage = get_storage_service()
    if not storage.exists(user["user_id"], req.source):
        raise HTTPException(status_code=404, detail="源文件不存在")
    try:
        new_rel = storage.move(user["user_id"], req.source, req.destination)
    except FileExistsError:
        raise HTTPException(status_code=400, detail="目标路径已存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"移动失败: {str(e)}")
    return {"success": True, "source": req.source, "destination": new_rel}


@router.post("/upload")
async def api_upload_files(
    path: str = "/",
    keep_structure: bool = False,
    files: list[UploadFile] = File(...),
    user=Depends(get_current_user),
):
    MAX_FILE_SIZE = 50 * 1024 * 1024
    storage = get_storage_service()
    user_id = user["user_id"]
    results = []
    for file in files:
        raw_name = (file.filename or "unnamed").replace("\\", "/")
        if keep_structure and "/" in raw_name:
            parts = raw_name.split("/")
            sanitized = "/".join(
                p for p in parts if p and not p.startswith(".") and p not in ("..", ".")
            )
            safe_name = sanitized or "uploaded_file"
        else:
            safe_name = os.path.basename(raw_name)
            if not safe_name or safe_name.startswith("."):
                safe_name = "uploaded_file"
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"文件 {safe_name} 超过 50MB 限制")
        rel_path = f"{path.rstrip('/')}/{safe_name}"
        storage.write_bytes(user_id, rel_path, content)
        results.append({"filename": file.filename, "path": rel_path, "size": len(content)})
    return {"success": True, "files": results}


@router.get("/download")
async def api_download_file(path: str, user=Depends(get_current_user)):
    storage = get_storage_service()
    user_id = user["user_id"]
    if not storage.is_file(user_id, path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return storage.file_response(user_id, path, filename=os.path.basename(path), inline=False)


@router.get("/media")
async def api_media_file(path: str, token: str):
    user = security.verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="无效的 token")
    user_id = user["user_id"]
    storage = get_storage_service()
    if not storage.is_file(user_id, path):
        raise HTTPException(status_code=404, detail="文件不存在")
    ext = os.path.splitext(path)[1].lower()
    media_type = MEDIA_MIME_MAP.get(ext)
    if not media_type:
        raise HTTPException(status_code=415, detail=f"不支持的媒体类型: {ext}")
    return storage.file_response(user_id, path, media_type=media_type, inline=True)
