"""阿里云 Paraformer 实时流式 STT(DashScope WebSocket)适配 LiveKit Agents。

迁移自 ``livekit-plugins-aliyun`` —— 该插件已停更且把 ``livekit-agents`` 精确锁死在
``1.2.9``,会拖累整个栈无法升级(尤其 fishaudio 升不上去拿不到 reference_id)。

本模块**只依赖 livekit.agents 的稳定 API**(``stt.STT``/``stt.SpeechStream``/``utils``)
+ ``aiohttp``,与 livekit-agents 1.6 完全兼容,从而彻底甩掉 livekit-plugins-aliyun。

协议:DashScope 双工流式推理 ``wss://dashscope.aliyuncs.com/api-ws/v1/inference``
(run-task → 推音频帧 → finish-task;result-generated 事件回传中间/最终结果)。

凭据:``DASHSCOPE_API_KEY`` 环境变量,或构造参数 ``api_key``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import List

import aiohttp
from livekit import rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    APIStatusError,
    stt,
    utils,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr

logger = logging.getLogger("jellyfish-voice")


@dataclass
class STTOptions:
    api_key: str | None
    language: str | None
    detect_language: bool
    interim_results: bool
    punctuate: bool
    model: str
    max_sentence_silence: int = 500
    sample_rate: int = 16000
    workspace: str | None = None
    # 热词表(可选):https://help.aliyun.com/zh/model-studio/custom-hot-words
    vocabulary_id: str | None = None
    disfluency_removal_enabled: bool = False
    semantic_punctuation_enabled: bool = False
    punctuation_prediction_enabled: bool = True
    inverse_text_normalization_enabled: bool = True

    def get_ws_url(self) -> str:
        return "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

    def get_header(self) -> dict:
        header = {
            "Authorization": f"bearer {self.api_key}",
            "X-DashScope-DataInspection": "enable",
        }
        if self.workspace is not None:
            header["X-DashScope-WorkSpace"] = self.workspace
        return header

    def get_run_task_params(self, task_id: str) -> dict:
        return {
            "header": {
                "action": "run-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self.model,
                "parameters": {
                    "format": "wav",
                    "sample_rate": self.sample_rate,
                    "vocabulary_id": self.vocabulary_id,
                    "disfluency_removal_enabled": self.disfluency_removal_enabled,
                    "semantic_punctuation_enabled": self.semantic_punctuation_enabled,
                    "punctuation_prediction_enabled": self.punctuation_prediction_enabled,
                    "inverse_text_normalization_enabled": self.inverse_text_normalization_enabled,
                    "max_sentence_silence": self.max_sentence_silence,
                    "heartbeat": True,
                    "language_hints": [self.language],
                },
                "input": {},
            },
        }

    def get_finish_task_params(self, task_id: str) -> dict:
        return {
            "header": {
                "action": "finish-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }


class STT(stt.STT):
    def __init__(
        self,
        *,
        language: str = "zh",
        detect_language: bool = False,
        interim_results: bool = True,
        punctuate: bool = True,
        model: str = "paraformer-realtime-v2",
        api_key: str | None = None,
        max_sentence_silence: int = 500,
        disfluency_removal_enabled: bool = False,
        semantic_punctuation_enabled: bool = False,
        punctuation_prediction_enabled: bool = True,
        inverse_text_normalization_enabled: bool = True,
        vocabulary_id: str | None = None,
        workspace: str | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True, interim_results=interim_results
            )
        )
        api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if api_key is None:
            raise ValueError("DASHSCOPE_API_KEY 未配置(阿里云 Paraformer STT)")
        self._opts = STTOptions(
            api_key=api_key,
            language=language,
            detect_language=detect_language,
            interim_results=interim_results,
            punctuate=punctuate,
            model=model,
            max_sentence_silence=max_sentence_silence,
            disfluency_removal_enabled=disfluency_removal_enabled,
            semantic_punctuation_enabled=semantic_punctuation_enabled,
            punctuation_prediction_enabled=punctuation_prediction_enabled,
            inverse_text_normalization_enabled=inverse_text_normalization_enabled,
            vocabulary_id=vocabulary_id,
            workspace=workspace,
        )
        self._session = http_session

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()
        return self._session

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        raise NotImplementedError("阿里云 STT 仅支持流式(stream())")

    def stream(
        self,
        *,
        language: str | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "SpeechStream":
        return SpeechStream(
            stt=self,
            opts=self._opts,
            conn_options=conn_options,
            http_session=self._ensure_session(),
        )


class SpeechStream(stt.SpeechStream):
    def __init__(
        self,
        stt: STT,
        opts: STTOptions,
        conn_options: APIConnectOptions,
        http_session: aiohttp.ClientSession,
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options)
        if opts.language is None:
            raise ValueError("流式模式不支持自动语言检测,请显式指定 language")
        self._opts = opts
        self._speaking = False
        self._request_id = utils.shortuuid()
        self._reconnect_event = asyncio.Event()
        self._session = http_session

    async def _connect_ws(self) -> aiohttp.ClientWebSocketResponse:
        ws = await asyncio.wait_for(
            self._session.ws_connect(
                self._opts.get_ws_url(), headers=self._opts.get_header()
            ),
            self._conn_options.timeout,
        )
        logger.info("阿里云 STT WebSocket 已连接")
        return ws

    async def _run(self) -> None:
        closing_ws = False
        task_id = utils.shortuuid()

        @utils.log_exceptions(logger=logger)
        async def send_task(ws: aiohttp.ClientWebSocketResponse):
            samples_100ms = self._opts.sample_rate // 10
            audio_bstream = utils.audio.AudioByteStream(
                sample_rate=self._opts.sample_rate,
                num_channels=1,
                samples_per_channel=samples_100ms,
            )
            has_ended = False
            async for data in self._input_ch:
                frames: list[rtc.AudioFrame] = []
                if isinstance(data, rtc.AudioFrame):
                    frames.extend(audio_bstream.write(data.data.tobytes()))
                elif isinstance(data, self._FlushSentinel):
                    frames.extend(audio_bstream.flush())
                    has_ended = True
                for frame in frames:
                    await ws.send_bytes(frame.data.tobytes())
                if has_ended:
                    await ws.send_json(self._opts.get_finish_task_params(task_id))
                    has_ended = False

        @utils.log_exceptions(logger=logger)
        async def recv_task(ws: aiohttp.ClientWebSocketResponse):
            nonlocal closing_ws
            while True:
                msg = await ws.receive()
                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    if closing_ws:
                        return
                    raise APIStatusError(message="connection closed unexpectedly")
                try:
                    self._process_stream_event(json.loads(msg.data))
                except Exception:
                    logger.exception("阿里云 STT 处理消息失败")

        ws: aiohttp.ClientWebSocketResponse | None = None
        while True:
            try:
                ws = await self._connect_ws()
                await ws.send_json(self._opts.get_run_task_params(task_id=task_id))
                tasks = [
                    asyncio.create_task(send_task(ws)),
                    asyncio.create_task(recv_task(ws)),
                ]
                wait_reconnect_task = asyncio.create_task(self._reconnect_event.wait())
                try:
                    done, _ = await asyncio.wait(
                        [asyncio.gather(*tasks), wait_reconnect_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )  # type: ignore
                    for task in done:
                        if task != wait_reconnect_task:
                            task.result()
                    if wait_reconnect_task not in done:
                        break
                    self._reconnect_event.clear()
                finally:
                    await utils.aio.gracefully_cancel(*tasks, wait_reconnect_task)
            finally:
                if ws is not None:
                    await ws.close()

    def _process_stream_event(self, data: dict) -> None:
        header = data.get("header") or {}
        event_type = header.get("event")
        if event_type != "result-generated":
            return
        output = data["payload"]["output"]["sentence"]
        is_sentence_end = output["sentence_end"]
        # 某些情况下时间戳可能缺失,兜底成 0.0,避免下游 end_time>0 比较报 NoneType。
        start_time = output.get("begin_time") or 0.0
        end_time = output.get("end_time") or 0.0
        text = output.get("text") or ""

        if not self._speaking:
            self._event_ch.send_nowait(
                stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH)
            )
            self._speaking = True

        if text and not is_sentence_end:
            self._event_ch.send_nowait(
                stt.SpeechEvent(
                    type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                    request_id=self._request_id,
                    alternatives=[
                        stt.SpeechData(
                            language=self._opts.language or "",
                            text=text,
                            start_time=start_time,
                            end_time=end_time,
                        )
                    ],
                )
            )
        elif text and is_sentence_end:
            self._event_ch.send_nowait(
                stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    request_id=self._request_id,
                    alternatives=[
                        stt.SpeechData(
                            language=self._opts.language or "",
                            text=text,
                            start_time=start_time,
                            end_time=end_time,
                        )
                    ],
                )
            )
            self._event_ch.send_nowait(
                stt.SpeechEvent(
                    type=stt.SpeechEventType.END_OF_SPEECH,
                    request_id=self._request_id,
                )
            )
            self._speaking = False


def live_transcription_to_speech_data(language: str, data: dict) -> List[stt.SpeechData]:
    return [
        stt.SpeechData(
            language=language,
            start_time=data.get("begin_time") or 0.0,
            end_time=data.get("end_time") or 0.0,
            confidence=0.0,
            text=data.get("text") or "",
        )
    ]
