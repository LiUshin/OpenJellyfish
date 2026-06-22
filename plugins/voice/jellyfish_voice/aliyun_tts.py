"""阿里云 CosyVoice 流式 TTS(DashScope WebSocket)适配 LiveKit Agents。

迁移自 ``livekit-plugins-aliyun`` —— 原插件已停更且把 ``livekit-agents`` 锁死 1.2.9。
本模块只依赖 livekit.agents 稳定 API(``tts.TTS``/``tts.SynthesizeStream``/
``tts.AudioEmitter``/``utils.ConnectionPool``)+ aiohttp,兼容 livekit-agents 1.6。

⚠️ 原插件用 ``osc_data.TextStreamSentencizer`` 做流式分句,但 osc-data 会拖进
librosa/kaldifst/av 等一堆重依赖。这里用自写的轻量 ``_Sentencizer`` 替代(只需
按句末标点切句 + 去 emoji),保持 worker 镜像精简。

协议:DashScope 双工流式 ``wss://dashscope.aliyuncs.com/api-ws/v1/inference``
(run-task → continue-task(text) → finish-task;BINARY 帧为 PCM 音频)。

凭据:``DASHSCOPE_API_KEY`` 环境变量,或构造参数 ``api_key``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import AsyncIterable, Dict, List, Optional

import aiohttp
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    tts,
    utils,
)

logger = logging.getLogger("jellyfish-voice")

# 句末标点(中英混排):到这些字符就断一句,尽早送 TTS 降低首字延迟。
_TERMINATORS = set("。！？!?；;…\n")
# 去 emoji(及部分符号区段),避免被读出来或干扰合成。
_EMOJI_RE = re.compile(
    "[" 
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "]",
    flags=re.UNICODE,
)
# 单句无标点的最大累积长度,超过即强制断句(防止超长 blob 一直不送)。
_MAX_SENTENCE_LEN = 80


class _Sentencizer:
    """极简流式分句器:push 增量文本,返回已成形的整句列表;flush 取剩余。"""

    def __init__(self, *, remove_emoji: bool = True) -> None:
        self._buf = ""
        self._remove_emoji = remove_emoji

    def _finalize(self, seg: str) -> str:
        seg = seg.strip()
        if self._remove_emoji:
            seg = _EMOJI_RE.sub("", seg).strip()
        return seg

    def push(self, text: str) -> List[str]:
        if not text:
            return []
        self._buf += text
        out: List[str] = []
        last = 0
        for i, ch in enumerate(self._buf):
            if ch in _TERMINATORS or (i - last + 1) >= _MAX_SENTENCE_LEN:
                seg = self._finalize(self._buf[last : i + 1])
                if seg:
                    out.append(seg)
                last = i + 1
        self._buf = self._buf[last:]
        return out

    def flush(self) -> List[str]:
        seg = self._finalize(self._buf)
        self._buf = ""
        return [seg] if seg else []


@dataclass
class TTSOptions:
    api_key: str
    model: str
    rate: float          # 语速 0.5~2
    voice: str           # 音色
    speech_rate: int
    volume: int          # 音量 0~100
    sample_rate: int     # 8000/16000/22050/24000/44100/48000
    pitch: float = 1.0   # 音调 0.5~2

    def get_ws_url(self) -> str:
        return "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

    def get_ws_header(self) -> Dict[str, str]:
        return {
            "Authorization": f"bearer {self.api_key}",
            "X-DashScope-DataInspection": "enable",
        }

    def get_run_task_params(self) -> Dict:
        return {
            "header": {
                "action": "run-task",
                "task_id": utils.shortuuid(),
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self.model,
                "parameters": {
                    "text_type": "PlainText",
                    "voice": self.voice,
                    "format": "pcm",
                    "sample_rate": self.sample_rate,
                    "volume": self.volume,
                    "rate": self.rate,
                    "pitch": self.pitch,
                },
                "input": {},
            },
        }

    def get_continue_task_params(self, text: str) -> Dict:
        return {
            "header": {
                "action": "continue-task",
                "task_id": utils.shortuuid(),
                "streaming": "duplex",
            },
            "payload": {"input": {"text": text}},
        }

    def get_finish_task_params(self) -> Dict:
        return {
            "header": {
                "action": "finish-task",
                "task_id": utils.shortuuid(),
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        sample_rate: int = 24000,
        voice: str = "longcheng",
        model: str = "cosyvoice-v2",
        speech_rate: int = 1,
        volume: int = 100,
        rate: float = 1.0,
        pitch: float = 1.0,
        http_session: aiohttp.ClientSession | None = None,
        max_session_duration: float = 600,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )
        api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY 未配置(阿里云 CosyVoice TTS)")
        self._session = http_session
        self._opts = TTSOptions(
            model=model,
            api_key=api_key,
            voice=voice,
            speech_rate=speech_rate,
            volume=volume,
            sample_rate=sample_rate,
            rate=rate,
            pitch=pitch,
        )
        self._pool = utils.ConnectionPool[aiohttp.ClientWebSocketResponse](
            connect_cb=self._connect_ws,
            close_cb=self._close_ws,
            max_session_duration=max_session_duration,
            mark_refreshed_on_get=True,
        )

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    async def _connect_ws(self, timeout: float) -> aiohttp.ClientWebSocketResponse:
        session = self._ensure_session()
        return await asyncio.wait_for(
            session.ws_connect(
                self._opts.get_ws_url(), headers=self._opts.get_ws_header()
            ),
            timeout=timeout,
        )

    async def _close_ws(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        await ws.close()

    def synthesize(self, text: str) -> AsyncIterable[tts.SynthesizedAudio]:
        raise NotImplementedError("阿里云 TTS 仅支持流式(stream())")

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "SynthesizeStream":
        return SynthesizeStream(tts=self, opts=self._opts, conn_options=conn_options)


class SynthesizeStream(tts.SynthesizeStream):
    def __init__(
        self,
        *,
        tts: TTS,
        opts: TTSOptions,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._opts = opts

    async def _run(self, emitter: tts.AudioEmitter) -> None:
        request_id = utils.shortuuid()
        emitter.initialize(
            request_id=request_id,
            sample_rate=self._opts.sample_rate,
            mime_type="audio/pcm",
            stream=True,
            num_channels=1,
            frame_size_ms=200,
        )

        async def _send_task(sentence: str, ws: aiohttp.ClientWebSocketResponse):
            await ws.send_json(self._opts.get_run_task_params())
            await ws.send_json(self._opts.get_continue_task_params(text=sentence))
            await ws.send_json(self._opts.get_finish_task_params())

        async def _recv_task(ws: aiohttp.ClientWebSocketResponse):
            is_first_response = True
            start_time = time.perf_counter()
            while True:
                try:
                    msg = await ws.receive()
                except Exception as e:  # noqa: BLE001
                    logger.warning("阿里云 TTS 接收出错: %s", e)
                    break
                if msg.type == aiohttp.WSMsgType.BINARY:
                    if is_first_response:
                        logger.info(
                            "阿里云 TTS 首帧 %.3fs", time.perf_counter() - start_time
                        )
                        is_first_response = False
                    emitter.push(data=msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    msg_json = json.loads(msg.data)
                    event = (msg_json.get("header") or {}).get("event")
                    if event == "task-finished":
                        break
                    if event == "task-failed":
                        logger.error("阿里云 TTS 任务失败: %s", msg_json)
                        break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    break

        splitter = _Sentencizer(remove_emoji=True)
        async for token in self._input_ch:
            if isinstance(token, self._FlushSentinel):
                sentences = splitter.flush()
            else:
                sentences = splitter.push(text=token)
            for sentence in sentences:
                emitter.start_segment(segment_id=utils.shortuuid())
                async with self._tts._pool.connection(
                    timeout=self._conn_options.timeout
                ) as ws:
                    assert not ws.closed, "WebSocket 连接已关闭"
                    tasks = [
                        asyncio.create_task(_send_task(sentence=sentence, ws=ws)),
                        asyncio.create_task(_recv_task(ws=ws)),
                    ]
                    try:
                        await asyncio.gather(*tasks)
                    finally:
                        await utils.aio.gracefully_cancel(*tasks)
                    emitter.end_segment()
