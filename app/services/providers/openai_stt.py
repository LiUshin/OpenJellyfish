"""OpenAI Whisper / gpt-4o-transcribe STT provider。

注意：与其他 provider 不同，``invoke`` 返回的是 ``str`` 文本，而非 ``(bytes, meta)``。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from app.services.providers.base import (
    STTProvider, ProviderInfo, ProviderError,
)
from app.services.providers.registry import register


class OpenAISTTProvider(STTProvider):
    info = ProviderInfo(name="openai", capability="stt")

    def invoke(
        self,
        *,
        model: str,
        credentials: Dict[str, str],
        audio_bytes: bytes,
        filename: str = "audio.webm",
        content_type: str = "audio/webm",
        language: Optional[str] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> str:
        api_key = credentials.get("api_key", "")
        base_url = credentials.get("base_url", "").rstrip("/")
        if not api_key or not base_url:
            raise ProviderError("OpenAI STT provider 缺少 api_key 或 base_url")

        data: Dict[str, Any] = {"model": model}
        if language:
            data["language"] = language
        if extras:
            data.update(extras)

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (filename, audio_bytes, content_type)},
                data=data,
            )
        if resp.status_code != 200:
            raise ProviderError(
                f"Whisper API 错误 ({resp.status_code}): {resp.text[:300]}",
                status=resp.status_code, raw=resp.text,
            )
        return (resp.json().get("text") or "").strip()


register(OpenAISTTProvider())
