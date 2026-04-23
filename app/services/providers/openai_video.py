"""OpenAI Sora 视频 provider（``sora-2-2025-12-08`` 等）。包含内部轮询。"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import httpx

from app.services.providers.base import (
    VideoProvider, ProviderInfo, ProviderResult, ProviderError,
)
from app.services.providers.registry import register

_DEFAULT_MAX_WAIT = 300


class OpenAIVideoProvider(VideoProvider):
    info = ProviderInfo(name="openai", capability="video")

    def invoke(
        self,
        *,
        model: str,
        credentials: Dict[str, str],
        prompt: str,
        seconds: int = 4,
        size: str = "1280x720",
        extras: Optional[Dict[str, Any]] = None,
    ) -> ProviderResult:
        api_key = credentials.get("api_key", "")
        base_url = credentials.get("base_url", "").rstrip("/")
        if not api_key or not base_url:
            raise ProviderError("OpenAI Sora provider 缺少 api_key 或 base_url")

        max_wait = int((extras or {}).get("max_wait", _DEFAULT_MAX_WAIT))
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "seconds": str(seconds),
            "size": size,
        }
        if extras:
            for k, v in extras.items():
                if k != "max_wait":
                    body[k] = v

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{base_url}/videos", headers=headers, json=body)
            if resp.status_code not in (200, 201):
                raise ProviderError(
                    f"Sora 提交任务失败 ({resp.status_code}): {resp.text[:300]}",
                    status=resp.status_code, raw=resp.text,
                )
            job = resp.json()
            job_id = job.get("id", "")
            if not job_id:
                raise ProviderError("Sora API 未返回任务 ID", raw=job)

            elapsed = 0
            poll_interval = 5
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
                if status == "failed":
                    msg = status_data.get("error", {}).get("message", "未知错误")
                    raise ProviderError(f"视频生成失败: {msg}", raw=status_data)
                progress = status_data.get("progress", 0)
                if progress > 80:
                    poll_interval = 2
                elif progress > 50:
                    poll_interval = 3
            else:
                raise ProviderError(
                    f"视频生成超时（{max_wait}s），任务 ID: {job_id}",
                    raw={"job_id": job_id},
                )

            content_resp = client.get(
                f"{base_url}/videos/{job_id}/content",
                headers=headers,
                follow_redirects=True,
            )
        if content_resp.status_code != 200:
            raise ProviderError(
                f"视频下载失败 ({content_resp.status_code})",
                status=content_resp.status_code,
            )
        return content_resp.content, {"model": model, "job_id": job_id, "seconds": seconds, "size": size}


register(OpenAIVideoProvider())
