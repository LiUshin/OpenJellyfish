"""MiniMax T2A v2 TTS provider。

Endpoint: ``POST https://api.minimax.io/v1/t2a_v2?GroupId={group_id}``
返回 JSON ``data.audio`` 为 hex 编码音频字节。

注意：
  - GroupId 必填（凭据 dict 中读 ``group_id``）。
  - voice_id 与 emotion 等私参通过 ``extras`` 透传，UI 表单可基于 ``params_schema`` 渲染。
  - speed/vol 与 OpenAI TTS 字段语义不同：speed=1.0 中速；vol 默认 1.0。
"""

from __future__ import annotations

import binascii
from typing import Any, Dict, Optional

import httpx

from app.services.providers.base import (
    TTSProvider, ProviderInfo, ProviderResult, ProviderError,
)
from app.services.providers.registry import register

_DEFAULT_VOICE = "male-qn-qingse"
_API_BASE = "https://api.minimax.io/v1"


class MiniMaxTTSProvider(TTSProvider):
    info = ProviderInfo(name="minimax", capability="tts")

    def invoke(
        self,
        *,
        model: str,
        credentials: Dict[str, str],
        text: str,
        voice: str = _DEFAULT_VOICE,
        speed: float = 1.0,
        response_format: str = "mp3",
        extras: Optional[Dict[str, Any]] = None,
    ) -> ProviderResult:
        api_key = credentials.get("api_key", "")
        group_id = credentials.get("group_id", "")
        if not api_key or not group_id:
            raise ProviderError("MiniMax TTS provider 缺少 api_key 或 group_id")

        ex = dict(extras or {})
        voice_setting: Dict[str, Any] = {
            "voice_id": voice or _DEFAULT_VOICE,
            "speed":    float(speed),
            "vol":      float(ex.pop("vol", 1.0)),
        }
        if "emotion" in ex:
            voice_setting["emotion"] = ex.pop("emotion")
        if "pitch" in ex:
            voice_setting["pitch"] = ex.pop("pitch")

        audio_setting: Dict[str, Any] = {
            "format":      response_format,
            "sample_rate": int(ex.pop("sample_rate", 32000)),
            "bitrate":     int(ex.pop("bitrate", 128000)),
            "channel":     int(ex.pop("channel", 1)),
        }

        body: Dict[str, Any] = {
            "model": model,
            "text": text,
            "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": audio_setting,
        }
        body.update(ex)

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{_API_BASE}/t2a_v2",
                params={"GroupId": group_id},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if resp.status_code != 200:
            raise ProviderError(
                f"MiniMax TTS API 错误 ({resp.status_code}): {resp.text[:300]}",
                status=resp.status_code, raw=resp.text,
            )
        data = resp.json()
        base = data.get("data") or {}
        hex_audio = base.get("audio", "")
        if not hex_audio:
            err = (data.get("base_resp") or {}).get("status_msg", "")
            raise ProviderError(f"MiniMax TTS 未返回音频数据: {err or data}", raw=data)
        try:
            audio_bytes = binascii.unhexlify(hex_audio)
        except (binascii.Error, ValueError) as e:
            raise ProviderError(f"MiniMax TTS 音频解码失败: {e}", raw=data) from e

        meta: Dict[str, Any] = {
            "model": model, "voice": voice_setting["voice_id"], "format": response_format,
        }
        if "extra_info" in data:
            meta["extra_info"] = data["extra_info"]
        return audio_bytes, meta


register(MiniMaxTTSProvider())
