"""
Centralised API key and base URL configuration.

Design: Per-user keys > per-capability env overrides > provider env defaults.

Simple case:  set OPENAI_API_KEY only → all OpenAI capabilities use it.
Advanced:     set IMAGE_API_KEY + IMAGE_BASE_URL → image generation uses
              a different provider while everything else keeps the default.
Per-user:     admin sets keys in Settings → takes priority over env vars.
"""

import os
from typing import Tuple, Optional

# ── Default base URLs ───────────────────────────────────────────────

_OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
_ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"

# ── Capability → env-var mapping ────────────────────────────────────
# (capability_key, capability_env_prefix, provider_fallback)

_CAPABILITY_MAP = {
    "image":  ("IMAGE",  "openai"),
    "tts":    ("TTS",    "openai"),
    "video":  ("VIDEO",  "openai"),
    "s2s":    ("S2S",    "openai"),
    "stt":    ("STT",    "openai"),
}

# ── Per-capability env prefix → user_api_keys field mapping ─────────
_CAP_PREFIX_TO_USER_FIELD = {
    "IMAGE": "image",
    "TTS":   "tts",
    "VIDEO": "video",
    "S2S":   "s2s",
    "STT":   "stt",
}


def _get_provider_key(provider: str) -> str:
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY", "")
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY", "")
    return ""


def _get_provider_base(provider: str) -> str:
    if provider == "openai":
        return os.getenv("OPENAI_BASE_URL", _OPENAI_DEFAULT_BASE).rstrip("/")
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_BASE_URL", _ANTHROPIC_DEFAULT_BASE).rstrip("/")
    return ""


def _get_user_keys(user_id: Optional[str]) -> Optional[dict]:
    """Lazy-load user API keys. Returns None if no user_id or no keys."""
    if not user_id:
        return None
    try:
        from app.core.user_api_keys import get_user_api_keys
        keys = get_user_api_keys(user_id)
        if any(v for v in keys.values()):
            return keys
    except Exception:
        pass
    return None


# ── Public API ──────────────────────────────────────────────────────

def get_api_config(capability: str, user_id: Optional[str] = None) -> Tuple[str, str]:
    """
    Return ``(api_key, base_url)`` for *capability*.

    Lookup order:
      1. Per-user capability key (if user_id provided)
      2. Per-user provider fallback key
      3. ``{CAPABILITY}_API_KEY`` / ``{CAPABILITY}_BASE_URL`` env var
      4. Provider defaults (``OPENAI_API_KEY`` / ``OPENAI_BASE_URL``)

    Raises ``RuntimeError`` if no key is available.
    """
    if capability not in _CAPABILITY_MAP:
        raise ValueError(f"Unknown capability: {capability}")

    prefix, provider = _CAPABILITY_MAP[capability]
    user_keys = _get_user_keys(user_id)
    cap_field = _CAP_PREFIX_TO_USER_FIELD.get(prefix, "")

    # 1. Per-user: capability-specific key
    key = ""
    base = ""
    if user_keys and cap_field:
        key = user_keys.get(f"{cap_field}_api_key", "")
        base = user_keys.get(f"{cap_field}_base_url", "")

    # 2. Per-user: provider fallback
    if not key and user_keys:
        key = user_keys.get(f"{provider}_api_key", "")
    if not base and user_keys:
        base = user_keys.get(f"{provider}_base_url", "")

    # 3. Env: capability-specific
    if not key:
        key = os.getenv(f"{prefix}_API_KEY", "")
    if not base:
        base = os.getenv(f"{prefix}_BASE_URL", "")

    # 4. Env: provider defaults
    if not key:
        key = _get_provider_key(provider)
    if not base:
        base = _get_provider_base(provider)

    if not key:
        raise RuntimeError(
            f"未配置 {prefix}_API_KEY 或 {provider.upper()}_API_KEY"
        )
    return key, base.rstrip("/")


def get_openai_llm_config(user_id: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Return ``(api_key, base_url_or_None)`` for OpenAI LLM chat models."""
    user_keys = _get_user_keys(user_id)

    key = ""
    base = ""
    if user_keys:
        key = user_keys.get("openai_api_key", "")
        base = user_keys.get("openai_base_url", "")
    if not key:
        key = os.getenv("OPENAI_API_KEY", "")
    if not base:
        base = os.getenv("OPENAI_BASE_URL", "")

    return key, base.rstrip("/") if base else None


def get_anthropic_llm_config(user_id: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Return ``(api_key, base_url_or_None)`` for Anthropic LLM chat models."""
    user_keys = _get_user_keys(user_id)

    key = ""
    base = ""
    if user_keys:
        key = user_keys.get("anthropic_api_key", "")
        base = user_keys.get("anthropic_base_url", "")
    if not key:
        key = os.getenv("ANTHROPIC_API_KEY", "")
    if not base:
        base = os.getenv("ANTHROPIC_BASE_URL", "")

    return key, base.rstrip("/") if base else None


def has_provider(provider: str, user_id: Optional[str] = None) -> bool:
    """Check whether a provider has a valid API key configured.

    支持：openai / anthropic（旧版）+ kimi / minimax / doubao（Phase 1+ 新增）。
    新增 vendor 通过 ``has_provider_credentials`` 兜底（更准确，例如豆包需要 ak+sk）。

    注意：``has_provider("minimax")`` 走严格判定（要求 group_id），用于 TTS/Video 路径；
    若仅判断 LLM 用 ``has_provider_credentials("minimax", capability="llm")`` 即可。
    """
    if provider in ("kimi", "minimax", "doubao"):
        return has_provider_credentials(provider, user_id=user_id)

    user_keys = _get_user_keys(user_id)
    if user_keys:
        user_key = user_keys.get(f"{provider}_api_key", "")
        if user_key:
            return True
    key = _get_provider_key(provider)
    return bool(key) and key != "your-openai-api-key-here"


# ── Provider credentials abstraction (used by providers/registry.dispatch) ──
#
# 每个 vendor 的凭据形态不同（OpenAI/Anthropic/Kimi: api_key+base_url；MiniMax: api_key+group_id；
# 豆包: access_key+secret_key+region）。这里把差异封装在 get_provider_credentials 里，
# 让 provider 模块只关心「拿到一个 dict」，不关心从哪读。

_KIMI_DEFAULT_BASE = "https://api.moonshot.cn/v1"
_DOUBAO_DEFAULT_REGION = "cn-beijing"


def get_provider_credentials(
    provider: str,
    user_id: Optional[str] = None,
    capability: Optional[str] = None,
) -> dict:
    """统一返回 provider 凭据 dict（按 vendor 形态填字段）。

    capability 用于 OpenAI 系：当传入 image/tts/video/s2s/stt 时复用
    ``get_api_config(capability)`` 的 per-capability 优先逻辑（IMAGE_API_KEY 等覆盖）。
    """
    if provider == "openai":
        if capability and capability in ("image", "tts", "video", "s2s", "stt"):
            try:
                key, base = get_api_config(capability, user_id=user_id)
                return {"api_key": key, "base_url": base}
            except RuntimeError:
                pass
        key, base = get_openai_llm_config(user_id=user_id)
        return {"api_key": key, "base_url": base or _OPENAI_DEFAULT_BASE}

    if provider == "anthropic":
        key, base = get_anthropic_llm_config(user_id=user_id)
        return {"api_key": key, "base_url": base or _ANTHROPIC_DEFAULT_BASE}

    user_keys = _get_user_keys(user_id) or {}

    if provider == "kimi":
        key = user_keys.get("kimi_api_key", "") or os.getenv("KIMI_API_KEY", "") or os.getenv("MOONSHOT_API_KEY", "")
        base = (user_keys.get("kimi_base_url", "")
                or os.getenv("KIMI_BASE_URL", "")
                or os.getenv("MOONSHOT_BASE_URL", "")
                or _KIMI_DEFAULT_BASE).rstrip("/")
        return {"api_key": key, "base_url": base}

    if provider == "minimax":
        return {
            "api_key":  user_keys.get("minimax_api_key", "")  or os.getenv("MINIMAX_API_KEY", ""),
            "group_id": user_keys.get("minimax_group_id", "") or os.getenv("MINIMAX_GROUP_ID", ""),
        }

    if provider == "doubao":
        return {
            "access_key": user_keys.get("doubao_access_key", "") or os.getenv("VOLC_ACCESSKEY", ""),
            "secret_key": user_keys.get("doubao_secret_key", "") or os.getenv("VOLC_SECRETKEY", ""),
            "region":     user_keys.get("doubao_region", "")     or os.getenv("VOLC_REGION", _DOUBAO_DEFAULT_REGION),
        }

    return {}


def has_provider_credentials(
    provider: str,
    user_id: Optional[str] = None,
    capability: Optional[str] = None,
) -> bool:
    """凭据是否齐全（至少能尝试调用）。供设置页生成「可用模型」过滤使用。

    MiniMax 特殊：LLM（Anthropic-compat）只需 api_key；TTS/Video 才需要 group_id。
    """
    creds = get_provider_credentials(provider, user_id=user_id, capability=capability)
    if provider in ("openai", "anthropic", "kimi"):
        return bool(creds.get("api_key"))
    if provider == "minimax":
        if capability == "llm":
            return bool(creds.get("api_key"))
        # 默认严格：TTS/Video/未指定 capability 都要求 group_id
        return bool(creds.get("api_key") and creds.get("group_id"))
    if provider == "doubao":
        return bool(creds.get("access_key") and creds.get("secret_key"))
    return False
