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
}

_URL_FIELDS = {
    "openai_base_url",
    "anthropic_base_url",
    "image_base_url",
    "tts_base_url",
    "video_base_url",
    "s2s_base_url",
    "stt_base_url",
}

ALL_FIELDS = _SECRET_FIELDS | _URL_FIELDS


def _keys_path(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), "api_keys.json")


def get_user_api_keys(user_id: str) -> Dict[str, str]:
    """Load and decrypt all user API keys. Returns empty strings for unset keys."""
    path = _keys_path(user_id)
    result: Dict[str, str] = {f: "" for f in ALL_FIELDS}
    if not os.path.isfile(path):
        return result
    try:
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
    except Exception:
        return result
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
    """Save user API keys (encrypting secret fields)."""
    current = {}
    path = _keys_path(user_id)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                current = json.load(f)
        except Exception:
            pass

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

    os.makedirs(os.path.dirname(path), exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(path, current, ensure_ascii=False, indent=2)


def get_masked_keys(user_id: str) -> Dict[str, Any]:
    """Return keys with secrets masked for frontend display."""
    raw = get_user_api_keys(user_id)
    result = {}
    for field in ALL_FIELDS:
        val = raw.get(field, "")
        if field in _SECRET_FIELDS:
            result[field] = mask_secret(val) if val else ""
            result[f"{field}_configured"] = bool(val)
        else:
            result[field] = val
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
