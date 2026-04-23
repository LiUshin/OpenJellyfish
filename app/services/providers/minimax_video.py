"""MiniMax 文生视频 provider（含异步轮询 + 文件下载三段式）。

流程：
  1. ``POST /video_generation``                    → 拿 ``task_id``
  2. ``GET  /query/video_generation?task_id=...``  → 轮询直到 status=Success，拿 ``file_id``
  3. ``GET  /files/retrieve?file_id=...``          → 拿 ``download_url``
  4. ``GET  download_url``                         → 拿到视频 bytes
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import httpx

from app.services.providers.base import (
    VideoProvider, ProviderInfo, ProviderResult, ProviderError,
)
from app.services.providers.registry import register

_API_BASE = "https://api.minimax.io/v1"
_DEFAULT_MAX_WAIT = 600  # 视频生成时间不可控，留 10 分钟


class MiniMaxVideoProvider(VideoProvider):
    info = ProviderInfo(name="minimax", capability="video")

    def invoke(
        self,
        *,
        model: str,
        credentials: Dict[str, str],
        prompt: str,
        seconds: int = 6,
        size: str = "1280x720",
        extras: Optional[Dict[str, Any]] = None,
    ) -> ProviderResult:
        api_key = credentials.get("api_key", "")
        group_id = credentials.get("group_id", "")  # 视频请求体不强制；查询/下载阶段也无需
        if not api_key:
            raise ProviderError("MiniMax video provider 缺少 api_key")

        ex = dict(extras or {})
        max_wait = int(ex.pop("max_wait", _DEFAULT_MAX_WAIT))

        body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "duration": int(seconds),
            "resolution": size,
        }
        # 透传可选参数（first_frame_image / prompt_optimizer 等）
        body.update(ex)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=60.0) as client:
            # ① 提交任务
            submit = client.post(f"{_API_BASE}/video_generation", headers=headers, json=body)
            if submit.status_code != 200:
                raise ProviderError(
                    f"MiniMax video 提交失败 ({submit.status_code}): {submit.text[:300]}",
                    status=submit.status_code, raw=submit.text,
                )
            sj = submit.json()
            task_id = sj.get("task_id") or (sj.get("data") or {}).get("task_id")
            if not task_id:
                raise ProviderError(
                    f"MiniMax video 未返回 task_id: {sj}", raw=sj,
                )

            # ② 轮询
            elapsed = 0
            poll_interval = 5
            file_id: Optional[str] = None
            while elapsed < max_wait:
                time.sleep(poll_interval)
                elapsed += poll_interval
                qresp = client.get(
                    f"{_API_BASE}/query/video_generation",
                    params={"task_id": task_id},
                    headers=headers,
                )
                if qresp.status_code != 200:
                    continue
                qj = qresp.json()
                status = (qj.get("status") or qj.get("data", {}).get("status") or "").strip()
                if status == "Success":
                    file_id = qj.get("file_id") or (qj.get("data") or {}).get("file_id")
                    if file_id:
                        break
                    raise ProviderError("MiniMax video 状态 Success 但缺 file_id", raw=qj)
                if status == "Fail":
                    msg = qj.get("base_resp", {}).get("status_msg", "未知失败")
                    raise ProviderError(f"MiniMax video 任务失败: {msg}", raw=qj)
            else:
                raise ProviderError(
                    f"MiniMax video 任务超时（{max_wait}s），task_id={task_id}",
                    raw={"task_id": task_id},
                )

            # ③ 取下载 URL
            params: Dict[str, Any] = {"file_id": file_id}
            if group_id:
                params["GroupId"] = group_id
            file_resp = client.get(
                f"{_API_BASE}/files/retrieve",
                params=params,
                headers=headers,
            )
            if file_resp.status_code != 200:
                raise ProviderError(
                    f"MiniMax files/retrieve 失败 ({file_resp.status_code}): {file_resp.text[:300]}",
                    status=file_resp.status_code, raw=file_resp.text,
                )
            fj = file_resp.json()
            download_url = ((fj.get("file") or {}).get("download_url")
                            or fj.get("download_url"))
            if not download_url:
                raise ProviderError("MiniMax files/retrieve 未返回 download_url", raw=fj)

            # ④ 下载视频字节（download_url 通常是 OSS 临时签名，直 GET 即可）
            dl = client.get(download_url, follow_redirects=True, timeout=120.0)
        if dl.status_code != 200:
            raise ProviderError(
                f"MiniMax video 下载失败 ({dl.status_code})",
                status=dl.status_code,
            )
        return dl.content, {
            "model": model, "task_id": task_id, "file_id": file_id,
            "duration": seconds, "resolution": size,
        }


register(MiniMaxVideoProvider())
