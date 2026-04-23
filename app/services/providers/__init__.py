"""
Provider 适配层公共入口。

新增 vendor 时：
  1. 在本目录新建 ``<vendor>_<capability>.py``，继承对应 *Provider 抽象类
  2. 在文件末尾 ``register(MyProvider())``
  3. 在 ``registry._bootstrap`` 中追加 import
  4. 在 ``config/model_catalog.json`` 中加入 model 条目
  5. 如有新凭据字段，更新 ``app/core/user_api_keys.py`` 与 ``api_config.get_provider_credentials``
"""

from app.services.providers.base import (
    ProviderInfo, ProviderResult, ProviderError, UnknownModelError,
    BaseProvider, ImageProvider, TTSProvider, VideoProvider, STTProvider,
)
from app.services.providers.registry import (
    register, get_provider, list_providers, parse_model_id, dispatch,
    _bootstrap,
)

# 触发内置 provider 注册（OpenAI 五大能力）
_bootstrap()

__all__ = [
    "ProviderInfo", "ProviderResult", "ProviderError", "UnknownModelError",
    "BaseProvider", "ImageProvider", "TTSProvider", "VideoProvider", "STTProvider",
    "register", "get_provider", "list_providers", "parse_model_id", "dispatch",
]
