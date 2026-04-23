"""
User preferences storage.

Stored at: {user_dir}/preferences.json
"""

import os
import json
from typing import Dict, Any

from app.core.security import get_user_dir

_DEFAULTS: Dict[str, Any] = {
    "tz_offset_hours": 8,
    # 每 capability 的默认 model（覆盖 config/model_catalog.json 的 defaults 块）
    # 形如 {"image": "openai:gpt-image-1", "tts": "openai:tts-1", ...}
    "capability_defaults": {},
}

# capability_defaults 内允许的 key
_CAPABILITY_KEYS = {"llm", "image", "tts", "video", "stt", "s2s"}


def _pref_path(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), "preferences.json")


def get_preferences(user_id: str) -> Dict[str, Any]:
    path = _pref_path(user_id)
    result = dict(_DEFAULTS)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                result.update(json.load(f))
        except Exception:
            pass
    return result


def get_tz_offset(user_id: str) -> float:
    return get_preferences(user_id).get("tz_offset_hours", 8)


def update_preferences(user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    prefs = get_preferences(user_id)
    allowed_keys = {"tz_offset_hours", "capability_defaults"}
    for k, v in updates.items():
        if k not in allowed_keys:
            continue
        if k == "capability_defaults" and isinstance(v, dict):
            current = dict(prefs.get("capability_defaults") or {})
            for cap, mid in v.items():
                if cap not in _CAPABILITY_KEYS:
                    continue
                if mid is None or mid == "":
                    current.pop(cap, None)
                elif isinstance(mid, str):
                    current[cap] = mid
            prefs["capability_defaults"] = current
        else:
            prefs[k] = v
    path = _pref_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(path, prefs, ensure_ascii=False, indent=2)
    return prefs


def get_capability_default(user_id: str, capability: str) -> str:
    """读取用户对单个 capability 的默认 model 偏好（无则返回空串）。"""
    if capability not in _CAPABILITY_KEYS:
        return ""
    caps = (get_preferences(user_id).get("capability_defaults") or {})
    return caps.get(capability, "") or ""
