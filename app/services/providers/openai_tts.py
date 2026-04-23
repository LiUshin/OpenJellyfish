"""OpenAI TTS provider（``tts-1`` / ``tts-1-hd`` / ``gpt-4o-mini-tts`` 等）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from app.services.providers.base import (
    TTSProvider, ProviderInfo, ProviderResult, ProviderError,
)
from app.services.providers.registry import register

_MAX_INPUT_CHARS = 4096


class OpenAITTSProvider(TTSProvider):
    info = ProviderInfo(name="openai", capability="tts")

    def invoke(
        self,
        *,
        model: str,
        credentials: Dict[str, str],
        text: str,
        voice: str = "alloy",
        speed: float = 1.0,
        response_format: str = "mp3",
        extras: Optional[Dict[str, Any]] = None,
    ) -> ProviderResult:
        api_key = credentials.get("api_key", "")
        base_url = credentials.get("base_url", "").rstrip("/")
        if not api_key or not base_url:
            raise ProviderError("OpenAI TTS provider 缺少 api_key 或 base_url")

        if len(text) > _MAX_INPUT_CHARS:
            text = text[:_MAX_INPUT_CHARS]

        body: Dict[str, Any] = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": response_format,
            "speed": speed,
        }
        if extras:
            body.update(extras)

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{base_url}/audio/speech",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if resp.status_code != 200:
            raise ProviderError(
                f"OpenAI TTS API 错误 ({resp.status_code}): {resp.text[:300]}",
                status=resp.status_code,
                raw=resp.text,
            )
        return resp.content, {"model": model, "voice": voice, "format": response_format}


register(OpenAITTSProvider())
