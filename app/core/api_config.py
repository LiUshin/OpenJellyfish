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
    """Check whether a provider has a valid API key configured."""
    user_keys = _get_user_keys(user_id)
    if user_keys:
        user_key = user_keys.get(f"{provider}_api_key", "")
        if user_key:
            return True
    key = _get_provider_key(provider)
    return bool(key) and key != "your-openai-api-key-here"
