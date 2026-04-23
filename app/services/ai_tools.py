"""
AI 多模态生成工具 — image / TTS / video。

本模块只负责：
  1. 解析当前 capability 默认 model（``model_catalog.resolve_model``）
  2. 调 ``providers.dispatch`` 拿到 bytes
  3. 决定文件名 / 写盘位置（admin 走 storage，consumer 走 ``write_func``）

所有模型/凭据/HTTP 细节都在 ``app/services/providers/*`` 内，本文件不再硬编码模型名。
"""

from __future__ import annotations

import os
import uuid
from typing import Optional, Callable, Dict, Any

from app.services.providers import dispatch, ProviderError
from app.services.model_catalog import resolve_model
from app.storage import get_storage_service


def _persist(rel_path: str, data: bytes, user_id: Optional[str], write_func: Optional[Callable]):
    if write_func:
        write_func(rel_path, data)
    else:
        get_storage_service().write_bytes(user_id or "", rel_path, data)


def generate_image(
    prompt: str,
    fs_dir: str,                  # noqa: ARG001 — 兼容旧签名（消费侧仍传入；写盘走 storage/write_func）
    size: str = "1024x1024",
    quality: str = "auto",
    filename: Optional[str] = None,
    user_id: Optional[str] = None,
    write_func: Optional[Callable] = None,
    model: Optional[str] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> dict:
    try:
        model_id = resolve_model("image", user_id=user_id, explicit=model)
        if not model_id:
            return {"success": False, "path": "", "message": "未配置可用的图片模型（请在设置页选择默认或填写凭据）"}
        image_bytes, _meta = dispatch(
            "image", model_id, user_id=user_id,
            prompt=prompt, size=size, quality=quality, extras=extras or {},
        )

        if not filename:
            filename = f"img_{uuid.uuid4().hex[:8]}.png"
        elif not os.path.splitext(filename)[1]:
            filename += ".png"
        rel_path = f"/generated/images/{filename}"
        _persist(rel_path, image_bytes, user_id, write_func)
        return {"success": True, "path": rel_path, "message": f"图片已生成并保存到 {rel_path}"}
    except ProviderError as e:
        return {"success": False, "path": "", "message": str(e)}
    except Exception as e:
        return {"success": False, "path": "", "message": f"图片生成失败: {e}"}


def generate_speech(
    text: str,
    fs_dir: str,                  # noqa: ARG001
    voice: str = "alloy",
    model: Optional[str] = None,  # 现在可由调用方覆盖；缺省走 catalog
    speed: float = 1.0,
    filename: Optional[str] = None,
    user_id: Optional[str] = None,
    write_func: Optional[Callable] = None,
    response_format: str = "mp3",
    extras: Optional[Dict[str, Any]] = None,
) -> dict:
    try:
        model_id = resolve_model("tts", user_id=user_id, explicit=model)
        if not model_id:
            return {"success": False, "path": "", "message": "未配置可用的 TTS 模型"}
        audio_bytes, _meta = dispatch(
            "tts", model_id, user_id=user_id,
            text=text, voice=voice, speed=speed,
            response_format=response_format, extras=extras or {},
        )

        ext = "." + response_format.lower()
        if not filename:
            filename = f"tts_{uuid.uuid4().hex[:8]}{ext}"
        elif not os.path.splitext(filename)[1]:
            filename += ext
        rel_path = f"/generated/audio/{filename}"
        _persist(rel_path, audio_bytes, user_id, write_func)
        return {"success": True, "path": rel_path, "message": f"语音已生成并保存到 {rel_path}"}
    except ProviderError as e:
        return {"success": False, "path": "", "message": str(e)}
    except Exception as e:
        return {"success": False, "path": "", "message": f"语音生成失败: {e}"}


def generate_video(
    prompt: str,
    fs_dir: str,                  # noqa: ARG001
    seconds: int = 4,
    size: str = "1280x720",
    filename: Optional[str] = None,
    user_id: Optional[str] = None,
    write_func: Optional[Callable] = None,
    model: Optional[str] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> dict:
    try:
        model_id = resolve_model("video", user_id=user_id, explicit=model)
        if not model_id:
            return {"success": False, "path": "", "message": "未配置可用的视频模型"}
        video_bytes, _meta = dispatch(
            "video", model_id, user_id=user_id,
            prompt=prompt, seconds=seconds, size=size, extras=extras or {},
        )

        if not filename:
            filename = f"video_{uuid.uuid4().hex[:8]}.mp4"
        elif not os.path.splitext(filename)[1]:
            filename += ".mp4"
        rel_path = f"/generated/videos/{filename}"
        _persist(rel_path, video_bytes, user_id, write_func)
        return {"success": True, "path": rel_path, "message": f"视频已生成并保存到 {rel_path}（{seconds}秒，{size}）"}
    except ProviderError as e:
        return {"success": False, "path": "", "message": str(e)}
    except Exception as e:
        return {"success": False, "path": "", "message": f"视频生成失败: {e}"}
