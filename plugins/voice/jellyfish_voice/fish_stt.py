"""Fish Audio STT(批量 /v1/asr)适配 LiveKit Agents。

Fish Audio 只提供**批量** ASR(`POST /v1/asr`,multipart/msgpack),没有流式 WebSocket。
因此这里实现 LiveKit 的**非流式** STT 接口(``capabilities.streaming=False``):
由 ``AgentSession`` 的 VAD 负责断句,断句后把整段音频丢给本类调 ``/v1/asr``。

延迟会比流式 STT 高(要等用户停顿、整段上传识别),但实现简单、依赖少。
上层用 ``stt.StreamAdapter(stt=FishSTT(), vad=vad)`` 包一层即可接进 AgentSession。

环境/凭据:
  FISH_API_KEY      Bearer Token(与 Fish TTS 共用同一把 key)
  FISH_BASE_URL     可选,默认 https://api.fish.audio
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from livekit import rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectionError,
    APIStatusError,
    stt,
)

_DEFAULT_BASE_URL = "https://api.fish.audio"


class STT(stt.STT):
    """非流式 Fish Audio STT。"""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        language: Optional[str] = None,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        key = api_key or os.environ.get("FISH_API_KEY") or os.environ.get("FISH_AUDIO_API_KEY")
        if not key:
            raise ValueError("Fish Audio STT 需要 FISH_API_KEY")
        self._api_key = key
        self._base_url = (
            base_url or os.environ.get("FISH_BASE_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._language = language
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def _recognize_impl(
        self,
        buffer,
        *,
        language=None,
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        # VAD 断出的整段音频(可能是单帧或帧列表)合并成一个 WAV
        frame = rtc.combine_audio_frames(buffer)
        wav_bytes = frame.to_wav_bytes()

        # language 可能是框架的 NOT_GIVEN 哨兵,只接受真正的字符串
        lang = language if isinstance(language, str) and language else self._language

        data = {"ignore_timestamps": "true"}
        if lang:
            data["language"] = lang

        timeout = getattr(conn_options, "timeout", None) or 30.0
        try:
            resp = await self._client.post(
                f"{self._base_url}/v1/asr",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"audio": ("audio.wav", wav_bytes, "audio/wav")},
                data=data,
                timeout=timeout,
            )
        except Exception as e:  # noqa: BLE001
            raise APIConnectionError() from e

        if resp.status_code != 200:
            raise APIStatusError(
                message=f"Fish ASR 失败: {resp.text[:300]}",
                status_code=resp.status_code,
            )

        body = resp.json()
        text = (body.get("text") or "").strip()
        # 显式给出 start/end_time(0.0),避免 LiveKit 内部 end_time>0 比较拿到 None。
        duration = float(body.get("duration") or 0.0)
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(
                    text=text,
                    language=lang or "",
                    start_time=0.0,
                    end_time=duration,
                )
            ],
        )

    async def aclose(self) -> None:
        await self._client.aclose()
