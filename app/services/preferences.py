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
}


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
    allowed_keys = {"tz_offset_hours"}
    for k, v in updates.items():
        if k in allowed_keys:
            prefs[k] = v
    path = _pref_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(path, prefs, ensure_ascii=False, indent=2)
    return prefs
