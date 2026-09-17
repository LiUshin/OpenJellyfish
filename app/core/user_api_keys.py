"""
Per-user API key storage with AES encryption.

Storage: users/{user_id}/api_keys.json

Keys are encrypted at rest. The file stores both encrypted key values
and plaintext base_url values.
"""

import os
import json
from typing import Dict, Any, Optional

from app.core.security import get_user_dir
from app.core.encryption import encrypt_value, decrypt_value, mask_secret

_SECRET_FIELDS = {
    "openai_api_key",
    "anthropic_api_key",
    "tavily_api_key",
    "cloudsway_search_key",
    "image_api_key",
    "tts_api_key",
    "video_api_key",
    "s2s_api_key",
    "stt_api_key",
    # 第三方大模型/多模态厂商（Phase 1+ 接入）
    "kimi_api_key",
    "minimax_api_key",
    "doubao_access_key",
    "doubao_secret_key",
    # AWS Bedrock (Bearer Token auth)
    "bedrock_api_key",
    # OpenAI-compat aggregators
    "openrouter_api_key",
    "siliconflow_api_key",
}

_URL_FIELDS = {
    "openai_base_url",
    "anthropic_base_url",
    "image_base_url",
    "tts_base_url",
    "video_base_url",
    "s2s_base_url",
    "stt_base_url",
    "kimi_base_url",
    # MiniMax / 豆包除 key 外的辅助字段（非 URL 也走 plaintext 槽位）
    "minimax_group_id",
    "doubao_region",
    # AWS Bedrock region
    "bedrock_region",
    "openrouter_base_url",
    "siliconflow_base_url",
}

ALL_FIELDS = _SECRET_FIELDS | _URL_FIELDS

# Per-provider credential source: "platform" (deployment env / 超管) | "user" (admin 自己的).
# Stored in api_keys.json as plaintext meta field ``credential_sources``.
CREDENTIAL_SOURCE_PROVIDERS = (
    "openai",
    "anthropic",
    "kimi",
    "minimax",
    "bedrock",
    "openrouter",
    "siliconflow",
    "tavily",
    "doubao",
)
VALID_CREDENTIAL_SOURCES = frozenset({"platform", "user"})

# Fields that imply "user has configured this provider" (for default inference).
_PROVIDER_KEY_FIELDS: Dict[str, tuple] = {
    "openai": ("openai_api_key",),
    "anthropic": ("anthropic_api_key",),
    "kimi": ("kimi_api_key",),
    "minimax": ("minimax_api_key",),
    "bedrock": ("bedrock_api_key",),
    "openrouter": ("openrouter_api_key",),
    "siliconflow": ("siliconflow_api_key",),
    "tavily": ("tavily_api_key", "cloudsway_search_key"),
    "doubao": ("doubao_access_key",),
}


def _keys_path(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), "api_keys.json")


def _load_stored(user_id: str) -> Dict[str, Any]:
    path = _keys_path(user_id)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_stored(user_id: str, stored: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_keys_path(user_id)), exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(_keys_path(user_id), stored, ensure_ascii=False, indent=2)


def get_user_api_keys(user_id: str) -> Dict[str, str]:
    """Load and decrypt all user API keys. Returns empty strings for unset keys."""
    result: Dict[str, str] = {f: "" for f in ALL_FIELDS}
    stored = _load_stored(user_id)
    for field in ALL_FIELDS:
        raw = stored.get(field, "")
        if not raw:
            continue
        if field in _SECRET_FIELDS:
            try:
                result[field] = decrypt_value(raw)
            except Exception:
                result[field] = ""
        else:
            result[field] = raw
    return result


def save_user_api_keys(user_id: str, keys: Dict[str, str]) -> None:
    """Save user API keys (encrypting secret fields). Preserves credential_sources."""
    current = _load_stored(user_id)

    for field, value in keys.items():
        if field not in ALL_FIELDS:
            continue
        if field in _SECRET_FIELDS:
            if value:
                current[field] = encrypt_value(value)
            else:
                current.pop(field, None)
        else:
            if value:
                current[field] = value.rstrip("/")
            else:
                current.pop(field, None)

    _save_stored(user_id, current)


def get_raw_credential_sources(user_id: str) -> Dict[str, str]:
    """Return explicitly stored sources only (no inference)."""
    stored = _load_stored(user_id).get("credential_sources") or {}
    if not isinstance(stored, dict):
        return {}
    out: Dict[str, str] = {}
    for prov, src in stored.items():
        if prov in CREDENTIAL_SOURCE_PROVIDERS and src in VALID_CREDENTIAL_SOURCES:
            out[str(prov)] = str(src)
    return out


def get_credential_source(user_id: Optional[str], provider: str) -> str:
    """Resolve credential source for a provider: ``platform`` or ``user``.

    Explicit preference wins. If unset: ``user`` when the admin has a key for
    that provider (backward compatible), otherwise ``platform``.
    """
    if not user_id or provider not in CREDENTIAL_SOURCE_PROVIDERS:
        return "platform"
    explicit = get_raw_credential_sources(user_id).get(provider)
    if explicit in VALID_CREDENTIAL_SOURCES:
        return explicit
    keys = get_user_api_keys(user_id)
    for field in _PROVIDER_KEY_FIELDS.get(provider, ()):
        if keys.get(field):
            return "user"
    return "platform"


def save_credential_sources(user_id: str, sources: Dict[str, str]) -> Dict[str, str]:
    """Merge credential source preferences. Returns the full stored map."""
    current = _load_stored(user_id)
    existing = current.get("credential_sources")
    if not isinstance(existing, dict):
        existing = {}
    for prov, src in sources.items():
        if prov not in CREDENTIAL_SOURCE_PROVIDERS:
            continue
        if src not in VALID_CREDENTIAL_SOURCES:
            continue
        existing[prov] = src
    current["credential_sources"] = existing
    _save_stored(user_id, current)
    return {k: v for k, v in existing.items()
            if k in CREDENTIAL_SOURCE_PROVIDERS and v in VALID_CREDENTIAL_SOURCES}


def is_platform_provider_configured(provider: str) -> bool:
    """Whether deployment env has a key for *provider* (never returns the secret)."""
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY", "").strip())
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    if provider == "kimi":
        return bool(
            os.getenv("KIMI_API_KEY", "").strip()
            or os.getenv("MOONSHOT_API_KEY", "").strip()
        )
    if provider == "minimax":
        return bool(os.getenv("MINIMAX_API_KEY", "").strip())
    if provider == "bedrock":
        return bool(os.getenv("BEDROCK_API_KEY", "").strip())
    if provider == "openrouter":
        return bool(os.getenv("OPENROUTER_API_KEY", "").strip())
    if provider == "tavily":
        return bool(
            os.getenv("TAVILY_API_KEY", "").strip()
            or os.getenv("CLOUDSWAY_SEARCH_KEY", "").strip()
        )
    if provider == "doubao":
        return bool(
            os.getenv("VOLC_ACCESSKEY", "").strip()
            and os.getenv("VOLC_SECRETKEY", "").strip()
        )
    return False


def get_masked_keys(user_id: str) -> Dict[str, Any]:
    """Return keys with secrets masked for frontend display + source metadata."""
    raw = get_user_api_keys(user_id)
    result: Dict[str, Any] = {}
    for field in ALL_FIELDS:
        val = raw.get(field, "")
        if field in _SECRET_FIELDS:
            result[field] = mask_secret(val) if val else ""
            result[f"{field}_configured"] = bool(val)
        else:
            result[field] = val

    sources = {
        p: get_credential_source(user_id, p) for p in CREDENTIAL_SOURCE_PROVIDERS
    }
    platform_configured = {
        p: is_platform_provider_configured(p) for p in CREDENTIAL_SOURCE_PROVIDERS
    }
    result["credential_sources"] = sources
    result["platform_configured"] = platform_configured
    return result


def get_user_provider_key(user_id: str, provider: str) -> Optional[str]:
    """Get a specific provider API key for a user. Returns None if not set."""
    field = f"{provider}_api_key"
    if field not in _SECRET_FIELDS:
        return None
    keys = get_user_api_keys(user_id)
    val = keys.get(field, "")
    return val if val else None


def get_user_provider_base(user_id: str, provider: str) -> Optional[str]:
    """Get a specific provider base URL for a user. Returns None if not set."""
    field = f"{provider}_base_url"
    if field not in _URL_FIELDS:
        return None
    keys = get_user_api_keys(user_id)
    val = keys.get(field, "")
    return val if val else None
