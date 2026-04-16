"""
AI generation tools — image, speech, video.

Each capability reads its own API key / base URL via ``api_config``,
falling back to the default OpenAI provider config.
"""

import os
import base64
import time
import uuid
from typing import Optional

import httpx

from app.core.api_config import get_api_config
from app.storage import get_storage_service


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def generate_image(
    prompt: str,
    fs_dir: str,
    size: str = "1024x1024",
    quality: str = "auto",
    filename: Optional[str] = None,
    user_id: Optional[str] = None,
    write_func: Optional[callable] = None,
) -> dict:
    try:
        api_key, base_url = get_api_config("image", user_id=user_id)
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{base_url}/images/generations",
                headers=_headers(api_key),
                json={"model": "gpt-image-1", "prompt": prompt, "size": size, "quality": quality, "n": 1},
            )
        if resp.status_code != 200:
            return {"success": False, "path": "", "message": f"API 错误 ({resp.status_code}): {resp.text[:300]}"}
        data = resp.json()
        b64 = data["data"][0].get("b64_json", "")
        if not b64:
            return {"success": False, "path": "", "message": "API 未返回图片数据"}
        image_bytes = base64.b64decode(b64)
        if not filename:
            filename = f"img_{uuid.uuid4().hex[:8]}.png"
        elif not os.path.splitext(filename)[1]:
            filename += ".png"
        rel_path = f"/generated/images/{filename}"
        if write_func:
            write_func(rel_path, image_bytes)
        else:
            get_storage_service().write_bytes(user_id or "", rel_path, image_bytes)
        return {"success": True, "path": rel_path, "message": f"图片已生成并保存到 {rel_path}"}
    except Exception as e:
        return {"success": False, "path": "", "message": f"图片生成失败: {str(e)}"}


def generate_speech(
    text: str,
    fs_dir: str,
    voice: str = "alloy",
    model: str = "tts-1",
    speed: float = 1.0,
    filename: Optional[str] = None,
    user_id: Optional[str] = None,
    write_func: Optional[callable] = None,
) -> dict:
    try:
        api_key, base_url = get_api_config("tts", user_id=user_id)
        if len(text) > 4096:
            text = text[:4096]
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{base_url}/audio/speech",
                headers=_headers(api_key),
                json={"model": model, "input": text, "voice": voice, "response_format": "mp3", "speed": speed},
            )
        if resp.status_code != 200:
            return {"success": False, "path": "", "message": f"API 错误 ({resp.status_code}): {resp.text[:300]}"}
        audio_bytes = resp.content
        if not filename:
            filename = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        elif not os.path.splitext(filename)[1]:
            filename += ".mp3"
        rel_path = f"/generated/audio/{filename}"
        if write_func:
            write_func(rel_path, audio_bytes)
        else:
            get_storage_service().write_bytes(user_id or "", rel_path, audio_bytes)
        return {"success": True, "path": rel_path, "message": f"语音已生成并保存到 {rel_path}"}
    except Exception as e:
        return {"success": False, "path": "", "message": f"语音生成失败: {str(e)}"}


def generate_video(
    prompt: str,
    fs_dir: str,
    seconds: int = 4,
    size: str = "1280x720",
    filename: Optional[str] = None,
    user_id: Optional[str] = None,
    write_func: Optional[callable] = None,
) -> dict:
    try:
        api_key, base_url = get_api_config("video", user_id=user_id)
        headers = _headers(api_key)
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{base_url}/videos",
                headers=headers,
                json={"model": "sora-2-2025-12-08", "prompt": prompt, "seconds": str(seconds), "size": size},
            )
        if resp.status_code not in (200, 201):
            return {"success": False, "path": "", "message": f"Sora API 错误 ({resp.status_code}): {resp.text[:300]}"}
        job = resp.json()
        job_id = job.get("id", "")
        if not job_id:
            return {"success": False, "path": "", "message": "Sora API 未返回任务 ID"}

        max_wait = 300
        poll_interval = 5
        elapsed = 0
        with httpx.Client(timeout=30.0) as client:
            while elapsed < max_wait:
                time.sleep(poll_interval)
                elapsed += poll_interval
                status_resp = client.get(f"{base_url}/videos/{job_id}", headers=headers)
                if status_resp.status_code != 200:
                    continue
                status_data = status_resp.json()
                status = status_data.get("status", "")
                if status == "completed":
                    break
                elif status == "failed":
                    msg = status_data.get("error", {}).get("message", "未知错误")
                    return {"success": False, "path": "", "message": f"视频生成失败: {msg}"}
                progress = status_data.get("progress", 0)
                if progress > 80:
                    poll_interval = 2
                elif progress > 50:
                    poll_interval = 3
            else:
                return {"success": False, "path": "", "message": f"视频生成超时（{max_wait}s），任务 ID: {job_id}"}

        with httpx.Client(timeout=30.0) as client:
            content_resp = client.get(f"{base_url}/videos/{job_id}/content", headers=headers, follow_redirects=True)
        if content_resp.status_code != 200:
            return {"success": False, "path": "", "message": f"视频下载失败 ({content_resp.status_code})"}

        video_bytes = content_resp.content
        if not filename:
            filename = f"sora_{uuid.uuid4().hex[:8]}.mp4"
        elif not os.path.splitext(filename)[1]:
            filename += ".mp4"
        rel_path = f"/generated/videos/{filename}"
        if write_func:
            write_func(rel_path, video_bytes)
        else:
            get_storage_service().write_bytes(user_id or "", rel_path, video_bytes)
        return {"success": True, "path": rel_path, "message": f"视频已生成并保存到 {rel_path}（{seconds}秒，{size}）"}
    except Exception as e:
        return {"success": False, "path": "", "message": f"视频生成失败: {str(e)}"}
