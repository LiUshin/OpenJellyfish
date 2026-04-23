"""
User-facing backup / restore endpoints.

Each authenticated user can export and import THEIR OWN
`users/{user_id}/` slice. No cross-user access.

Endpoints:
    POST /api/backup/preview     → estimate uncompressed size per module
    POST /api/backup/export      → stream a ZIP file (one-shot, deletes temp on close)
    POST /api/backup/import      → restore a ZIP (multipart upload)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse

from app.core.security import _load_users, _verify_password
from app.deps import get_current_user
from app.services.backup import (
    ALL_MODULES, MODULE_PATHS,
    estimate_module_sizes, export_user_data, import_user_data,
)

router = APIRouter(prefix="/api/backup", tags=["backup"])
log = logging.getLogger(__name__)

MAX_IMPORT_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB hard cap


def _parse_modules(modules_csv: Optional[str]) -> List[str]:
    if not modules_csv:
        return []
    raw = [s.strip() for s in modules_csv.split(",") if s.strip()]
    return [m for m in raw if m in MODULE_PATHS]


def _verify_password_for_user(user_id: str, password: str) -> bool:
    users = _load_users()
    info = users.get(user_id)
    if not info:
        return False
    return _verify_password(password, info.get("password_hash", ""))


@router.get("/modules")
async def api_list_modules(_user=Depends(get_current_user)):
    """Return the list of available modules + a friendly label for each."""
    labels = {
        "filesystem":    "文件系统 (docs / scripts / generated / soul 内容)",
        "conversations": "对话历史 (含附件)",
        "services":      "已发布服务 (config + 服务侧对话/任务)",
        "tasks":         "定时任务",
        "settings":      "设置 (system prompt, profile, subagents, preferences, capability prompts, soul config)",
        "api_keys":      "API Keys (导出为明文 JSON，仅在备份私密的情况下勾选)",
    }
    return {
        "modules": [{"id": m, "label": labels.get(m, m)} for m in ALL_MODULES],
        "default_selected": [m for m in ALL_MODULES if m != "api_keys"],
    }


@router.post("/preview")
async def api_preview(
    modules: str = Form(""),
    include_media: bool = Form(True),
    include_api_keys: bool = Form(False),
    user=Depends(get_current_user),
):
    """Estimate per-module file count + uncompressed total bytes."""
    selected = _parse_modules(modules) or [m for m in ALL_MODULES if m != "api_keys"]
    sizes = estimate_module_sizes(
        user["user_id"], selected, include_media, include_api_keys,
    )
    grand_files = sum(v["file_count"] for v in sizes.values())
    grand_bytes = sum(v["total_bytes"] for v in sizes.values())
    return {
        "modules": sizes,
        "total_file_count": grand_files,
        "total_uncompressed_bytes": grand_bytes,
        "selection": selected,
    }


@router.post("/export")
async def api_export(
    modules: str = Form(""),
    include_media: bool = Form(True),
    include_api_keys: bool = Form(False),
    user=Depends(get_current_user),
):
    """Build the backup ZIP and stream it back. Temp file is deleted after."""
    selected = _parse_modules(modules) or [m for m in ALL_MODULES if m != "api_keys"]
    try:
        result = export_user_data(
            user["user_id"], selected, include_media, include_api_keys,
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("backup export failed for user %s", user["user_id"])
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")

    zip_path = result.zip_path
    filename = f"jellyfishbot-{user['username']}-{datetime.now():%Y%m%d-%H%M%S}.zip"

    def _stream():
        try:
            with open(zip_path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.unlink(zip_path)
            except OSError:
                pass

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Backup-File-Count": str(result.file_count),
        "X-Backup-Uncompressed-Bytes": str(result.total_bytes),
        "X-Backup-Modules": ",".join(result.modules),
    }
    return StreamingResponse(_stream(), media_type="application/zip", headers=headers)


@router.post("/import")
async def api_import(
    file: UploadFile = File(...),
    mode: str = Form("merge"),
    password: str = Form(""),
    modules: str = Form(""),
    user=Depends(get_current_user),
):
    """Restore from a backup ZIP.

    `mode`:
      - merge    : only writes files that don't already exist
      - overwrite: snapshots existing modules to `users/{uid}.pre-restore-<ts>/`,
                   then extracts fresh — REQUIRES `password` confirmation.
    """
    if mode not in ("merge", "overwrite"):
        raise HTTPException(status_code=400, detail=f"无效的 mode: {mode}")
    if mode == "overwrite":
        if not password:
            raise HTTPException(status_code=400, detail="覆盖模式需要密码确认")
        if not _verify_password_for_user(user["user_id"], password):
            raise HTTPException(status_code=403, detail="密码错误")

    # Buffer + size cap.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(8 * 1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_IMPORT_BYTES:
            raise HTTPException(status_code=413, detail="备份文件过大 (>5GB)")
        chunks.append(chunk)
    zip_bytes = b"".join(chunks)
    if not zip_bytes:
        raise HTTPException(status_code=400, detail="未上传文件")

    modules_filter = _parse_modules(modules) or None

    try:
        result = import_user_data(
            user["user_id"], zip_bytes, mode=mode, modules_filter=modules_filter,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("backup import failed for user %s", user["user_id"])
        raise HTTPException(status_code=500, detail=f"导入失败: {e}")

    return JSONResponse({
        "ok": True,
        "mode": result.mode,
        "files_written": result.files_written,
        "files_skipped": result.files_skipped,
        "api_keys_imported": result.api_keys_imported,
        "snapshot_path": result.backup_snapshot_path,
        "warnings": result.warnings,
    })
