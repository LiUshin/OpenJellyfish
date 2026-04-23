"""OpenAI 文生图 provider（``gpt-image-1`` 等）。"""

from __future__ import annotations

import base64
from typing import Any, Dict, Optional

import httpx

from app.services.providers.base import (
    ImageProvider, ProviderInfo, ProviderResult, ProviderError,
)
from app.services.providers.registry import register


class OpenAIImageProvider(ImageProvider):
    info = ProviderInfo(name="openai", capability="image")

    def invoke(
        self,
        *,
        model: str,
        credentials: Dict[str, str],
        prompt: str,
        size: str = "1024x1024",
        quality: str = "auto",
        extras: Optional[Dict[str, Any]] = None,
    ) -> ProviderResult:
        api_key = credentials.get("api_key", "")
        base_url = credentials.get("base_url", "").rstrip("/")
        if not api_key or not base_url:
            raise ProviderError("OpenAI image provider 缺少 api_key 或 base_url")

        body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": 1,
        }
        if extras:
            body.update(extras)

        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{base_url}/images/generations",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )

        if resp.status_code != 200:
            raise ProviderError(
                f"OpenAI images API 错误 ({resp.status_code}): {resp.text[:300]}",
                status=resp.status_code,
                raw=resp.text,
            )
        data = resp.json()
        b64 = (data.get("data") or [{}])[0].get("b64_json", "")
        if not b64:
            raise ProviderError("OpenAI images API 未返回图片数据", raw=data)
        return base64.b64decode(b64), {"model": model, "raw": data}


register(OpenAIImageProvider())
