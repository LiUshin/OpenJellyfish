"""
Provider 适配层 — 抽象接口与共用类型。

设计原则（Pure A）：
  - 五个 capability（llm/image/tts/video/stt/s2s）各有一个抽象基类。
  - 所有 provider 暴露统一方法 ``invoke(**kwargs)``，便于 dispatcher 一致分发。
  - **共用字段** + ``extras: dict``：参数差异通过 extras 透传 vendor 私参，避免硬抹平。
  - 凭据通过 ``credentials: dict`` 注入，由 dispatcher 调 ``get_provider_credentials`` 读出，
    每个 provider 自取所需键（OpenAI → api_key/base_url；豆包 → access_key/secret_key/region；
    MiniMax → api_key/group_id）。
  - **不持久化**：image/tts/video provider 返回 ``bytes``；持久化由调用方（``ai_tools.py``）决定，
    便于 consumer 路径接管目录。
  - **不打包 SDK**：默认走直 HTTP；个别厂商需要 SDK 才方便实现可在该 provider 模块内自行 import。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

ProviderResult = Tuple[bytes, Dict[str, Any]]
"""(payload_bytes, metadata)。metadata 用于回传 cost/duration/raw_response 等可选信息。"""


@dataclass(frozen=True)
class ProviderInfo:
    """注册到 registry 时使用的 provider 元数据。"""
    name: str
    capability: str


class BaseProvider(ABC):
    """所有 provider 的共同基类。子类必须声明 ``info: ProviderInfo``。"""
    info: ProviderInfo

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def capability(self) -> str:
        return self.info.capability


# ── Capability-specific abstracts ────────────────────────────────────


class ImageProvider(BaseProvider):
    """文生图。"""

    @abstractmethod
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
        """返回 (image_bytes, metadata)。"""


class TTSProvider(BaseProvider):
    """文本转语音。"""

    @abstractmethod
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
        """返回 (audio_bytes, metadata)。"""


class VideoProvider(BaseProvider):
    """文生视频（包含内部轮询）。"""

    @abstractmethod
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
        """返回 (video_bytes, metadata)。"""


class STTProvider(BaseProvider):
    """语音转文字。"""

    @abstractmethod
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
        """返回转写文本。"""


# ── Errors ───────────────────────────────────────────────────────────


class ProviderError(RuntimeError):
    """provider 内部错误的统一封装，便于上层统一处理。"""
    def __init__(self, message: str, *, status: Optional[int] = None, raw: Any = None):
        super().__init__(message)
        self.status = status
        self.raw = raw


class UnknownModelError(LookupError):
    """model_id 未在 catalog/registry 中找到。"""
