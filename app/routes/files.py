import io
import json
import os
import posixpath
import zipfile
from typing import Iterable

from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from fastapi.responses import StreamingResponse

from app.schemas.requests import (
    WriteFileRequest, EditFileRequest, MoveFileRequest, CopyFileRequest,
)
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


# Filenames / dir-names skipped when building the @ mention index.
# Junk that nobody wants to reference by name from the chat input.
_INDEX_SKIP_DIRS = {
    "__pycache__", "node_modules", ".git", "venv", ".venv", ".cache",
    ".pytest_cache", ".mypy_cache", "target", ".idea", ".vscode",
}
_INDEX_SKIP_FILES = {".DS_Store", "Thumbs.db", ".gitkeep"}
_INDEX_MAX_ENTRIES = 10000
_INDEX_MAX_DEPTH = 12


@router.get("/index")
async def api_list_file_index(
    root: str = "/",
    include_dirs: bool = True,
    user=Depends(get_current_user),
):
    """Return a flat listing of all files (and optionally dirs) under `root`,
    suitable for in-memory fuzzy matching by the frontend `@`-mention picker.

    - Skips junk dirs (__pycache__, node_modules, .git, venv, …).
    - Caps at _INDEX_MAX_ENTRIES so a runaway tree never DoS's the request.
    - BFS via list_dir so the abstraction works for both local and S3
      backends without adding a new method.
    - Frontend caches the result and only re-fetches on focus / explicit
      refresh; index size for typical user (a few hundred files) is small.
    """
    storage = get_storage_service()
    user_id = user["user_id"]
    if not storage.is_dir(user_id, root):
        return {"entries": [], "truncated": False, "root": root}

    out: list[dict] = []
    truncated = False
    queue: list[tuple[str, int]] = [(root, 0)]
    while queue and len(out) < _INDEX_MAX_ENTRIES:
        cur_path, depth = queue.pop(0)
        try:
            entries = storage.list_dir(user_id, cur_path)
        except Exception:
            continue
        for e in entries:
            name = e.name
            if e.is_dir and name in _INDEX_SKIP_DIRS:
                continue
            if not e.is_dir and name in _INDEX_SKIP_FILES:
                continue
            if name.startswith("."):
                continue
            if e.is_dir:
                if include_dirs:
                    out.append({
                        "path": e.path, "name": name, "is_dir": True,
                        "size": 0, "modified_at": e.modified_at,
                    })
                if depth + 1 < _INDEX_MAX_DEPTH:
                    queue.append((e.path, depth + 1))
            else:
                out.append({
                    "path": e.path, "name": name, "is_dir": False,
                    "size": e.size, "modified_at": e.modified_at,
                })
            if len(out) >= _INDEX_MAX_ENTRIES:
                truncated = True
                break
    return {"entries": out, "truncated": truncated, "root": root}


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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"移动失败: {str(e)}")
    return {"success": True, "source": req.source, "destination": new_rel}


@router.post("/copy")
async def api_copy_file(req: CopyFileRequest, user=Depends(get_current_user)):
    """Copy a file or recursively copy a folder.

    Frontend uses this to back the Ctrl+C / Ctrl+V virtual clipboard and
    the right-click 「复制到…」 entry. Conflicts return 400 so the UI
    can prompt-and-rename rather than silently overwrite.
    """
    storage = get_storage_service()
    if not storage.exists(user["user_id"], req.source):
        raise HTTPException(status_code=404, detail="源文件不存在")
    try:
        new_rel = storage.copy(user["user_id"], req.source, req.destination)
    except FileExistsError:
        raise HTTPException(status_code=400, detail="目标路径已存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复制失败: {str(e)}")
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


# ── Streaming ZIP download ──────────────────────────────────────────
#
# Why a custom streaming generator instead of zipfile + tempfile:
#   - tempfile would buffer the entire archive on disk; for a 500MB folder
#     under S3 that's a) double bandwidth and b) latency until first byte.
#   - We write each entry into an in-memory buffer, flush to the wire, then
#     truncate the buffer. Memory usage stays at O(largest single file).
#   - ZIP_DEFLATED compresslevel=1 prioritises throughput over ratio
#     (good for already-compressed media; tiny win for text but no spike).

def _zip_filename_from_paths(paths: list[str]) -> str:
    if len(paths) == 1:
        base = os.path.basename(paths[0].rstrip("/")) or "files"
        return f"{base}.zip"
    return "files.zip"


def _iter_zip_stream(
    user_id: str, paths: list[str], storage,
) -> Iterable[bytes]:
    buf = io.BytesIO()
    # zipfile + a sliding BytesIO buffer; flush after every entry.
    with zipfile.ZipFile(
        buf, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=1,
    ) as zf:
        for raw_path in paths:
            top = os.path.basename(raw_path.rstrip("/")) or "root"
            for rel, data in storage.walk_files(user_id, raw_path):
                # rel is the path under raw_path; combine with top so multi-path
                # downloads keep distinct roots inside the archive.
                arcname = posixpath.join(top, rel) if rel else top
                zf.writestr(arcname, data)
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)
    yield buf.getvalue()


@router.get("/zip")
async def api_zip_download(
    paths: str = Query(..., description='JSON array of file or folder paths, e.g. ["/docs", "/notes/x.md"]'),
    user=Depends(get_current_user),
):
    """Stream a ZIP archive of one or more files / folders.

    `paths` is a JSON-encoded array passed via query string so a plain
    `<a href>` click triggers a browser download (no fetch wrapper needed).
    Each requested path is preserved as a top-level entry in the archive
    (basename used as root) so multi-select downloads keep their structure.
    """
    try:
        path_list: list[str] = json.loads(paths)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="paths 参数必须为 JSON 数组")
    if not isinstance(path_list, list) or not path_list:
        raise HTTPException(status_code=400, detail="paths 不能为空")
    if len(path_list) > 200:
        raise HTTPException(status_code=400, detail="一次最多打包 200 个项")
    storage = get_storage_service()
    user_id = user["user_id"]
    for p in path_list:
        if not isinstance(p, str) or not storage.exists(user_id, p):
            raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    fname = _zip_filename_from_paths(path_list)
    return StreamingResponse(
        _iter_zip_stream(user_id, path_list, storage),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/zip-token")
async def api_zip_download_token(
    paths: str = Query(...),
    token: str = Query(...),
):
    """Token-based variant of /zip for use from <a> tags / window.open().

    Same semantics as /zip but accepts the auth token in the query string
    instead of an Authorization header. The frontend uses this for
    one-click ZIP downloads where setting custom headers would force a
    blob-fetch + saveAs detour.
    """
    user = security.verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="无效的 token")
    try:
        path_list: list[str] = json.loads(paths)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="paths 参数必须为 JSON 数组")
    if not isinstance(path_list, list) or not path_list:
        raise HTTPException(status_code=400, detail="paths 不能为空")
    if len(path_list) > 200:
        raise HTTPException(status_code=400, detail="一次最多打包 200 个项")
    storage = get_storage_service()
    user_id = user["user_id"]
    for p in path_list:
        if not isinstance(p, str) or not storage.exists(user_id, p):
            raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    fname = _zip_filename_from_paths(path_list)
    return StreamingResponse(
        _iter_zip_stream(user_id, path_list, storage),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store",
        },
    )


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
