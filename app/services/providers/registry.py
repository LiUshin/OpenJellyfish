"""
Provider 注册表与统一分发入口。

用法：
    from app.services.providers import dispatch
    img_bytes, meta = dispatch("image", "openai:gpt-image-1",
                               user_id=uid, prompt="...", size="1024x1024")
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.services.providers.base import (
    BaseProvider, ImageProvider, TTSProvider, VideoProvider, STTProvider,
    UnknownModelError, ProviderError,
)

# capability → { provider_name: instance }
_REGISTRY: Dict[str, Dict[str, BaseProvider]] = {
    "image": {},
    "tts": {},
    "video": {},
    "stt": {},
}


def register(provider: BaseProvider) -> None:
    """把 provider 实例注册到对应 capability。重复注册将覆盖，便于测试 mock。"""
    cap = provider.capability
    if cap not in _REGISTRY:
        _REGISTRY[cap] = {}
    _REGISTRY[cap][provider.name] = provider


def get_provider(capability: str, provider_name: str) -> BaseProvider:
    bucket = _REGISTRY.get(capability) or {}
    if provider_name not in bucket:
        raise UnknownModelError(
            f"未注册的 provider: capability={capability}, provider={provider_name}"
        )
    return bucket[provider_name]


def list_providers(capability: str) -> Dict[str, BaseProvider]:
    return dict(_REGISTRY.get(capability) or {})


def parse_model_id(model_id: str) -> Tuple[str, str]:
    """``"openai:gpt-image-1"`` → ``("openai", "gpt-image-1")``。"""
    if ":" not in model_id:
        raise UnknownModelError(
            f"非法 model_id（缺少 provider 前缀）: {model_id!r}，"
            "期望形如 'openai:gpt-image-1'"
        )
    provider_name, model = model_id.split(":", 1)
    if not provider_name or not model:
        raise UnknownModelError(f"非法 model_id: {model_id!r}")
    return provider_name, model


def dispatch(
    capability: str,
    model_id: str,
    *,
    user_id: Optional[str] = None,
    credentials: Optional[Dict[str, str]] = None,
    **kwargs: Any,
) -> Any:
    """根据 ``capability + model_id`` 找到 provider 并调用 ``invoke``。

    Args:
        capability: ``image`` / ``tts`` / ``video`` / ``stt``
        model_id:   ``provider:model``，如 ``openai:gpt-image-1``
        user_id:    用于解析凭据；若调用方已自备 ``credentials`` 可省略
        credentials: 自定义凭据（可选，主要给测试用）
        **kwargs:   透传给 provider.invoke
    """
    provider_name, model = parse_model_id(model_id)
    provider = get_provider(capability, provider_name)

    if credentials is None:
        from app.core.api_config import get_provider_credentials
        credentials = get_provider_credentials(
            provider_name,
            user_id=user_id,
            capability=capability,
        )

    try:
        return provider.invoke(model=model, credentials=credentials, **kwargs)
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(
            f"{provider_name}/{capability} 调用失败: {type(e).__name__}: {e}"
        ) from e


# ── Auto-register built-in providers on package import ─────────────


def _bootstrap() -> None:
    """导入所有内置 provider 模块，触发其模块级 register() 副作用。"""
    from app.services.providers import (  # noqa: F401
        openai_image, openai_tts, openai_video, openai_stt,
        minimax_tts, minimax_video,
    )


# Type hint export for users; not used at runtime
__all__ = [
    "register", "get_provider", "list_providers",
    "parse_model_id", "dispatch", "_bootstrap",
    "ImageProvider", "TTSProvider", "VideoProvider", "STTProvider",
]
