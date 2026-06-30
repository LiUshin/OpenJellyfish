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
    # UI/messages 语言："zh" | "en"。空字符串视为未设置 → 后端按 Accept-Language
    # 头解析（前端首次访问由 navigator.language 推断，写入 localStorage 后跨设备
    # 通过此偏好同步）。
    "language": "",
    # 用户主动隐藏的 LLM model ID 列表（不在对话框显示）。默认空 = 全部显示。
    "hidden_models": [],
}

# capability_defaults 内允许的 key
_CAPABILITY_KEYS = {"llm", "image", "tts", "video", "stt", "s2s"}

# 支持的 UI 语言。新增语言时把 ISO 639-1 加进来即可。
SUPPORTED_LANGUAGES = ("zh", "en")


def _pref_path(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), "preferences.json")


def _global_default_tz() -> float:
    """全局默认时区偏移（小时），由超管在启动器 / .env 设 ``DEFAULT_TZ_OFFSET_HOURS``。

    call-time 读 env（改值重启后端即生效，无需改代码）。仅对**未显式**设置
    ``tz_offset_hours`` 的用户生效——已在偏好里存了自己时区的用户不受影响。
    """
    try:
        return float(os.getenv("DEFAULT_TZ_OFFSET_HOURS", "8"))
    except (TypeError, ValueError):
        return 8.0


def get_preferences(user_id: str) -> Dict[str, Any]:
    path = _pref_path(user_id)
    result = dict(_DEFAULTS)
    # 动态默认：未显式设置时区的用户回退全局默认（DEFAULT_TZ_OFFSET_HOURS）。
    result["tz_offset_hours"] = _global_default_tz()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                result.update(json.load(f))  # 用户已存的 tz_offset_hours 会覆盖全局默认
        except Exception:
            pass
    return result


def get_tz_offset(user_id: str) -> float:
    return get_preferences(user_id).get("tz_offset_hours", _global_default_tz())


def update_preferences(user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    prefs = get_preferences(user_id)
    allowed_keys = {"tz_offset_hours", "capability_defaults", "language", "hidden_models"}
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
        elif k == "language":
            # Empty string → "auto"; otherwise must be in supported list.
            if v in (None, ""):
                prefs["language"] = ""
            elif isinstance(v, str) and v in SUPPORTED_LANGUAGES:
                prefs["language"] = v
        elif k == "hidden_models":
            if isinstance(v, list):
                prefs["hidden_models"] = [m for m in v if isinstance(m, str)]
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


def get_language_preference(user_id: str) -> str:
    """读取用户的 UI 语言偏好。空串表示未设置（应回退到 Accept-Language）。"""
    lang = (get_preferences(user_id).get("language") or "").strip()
    if lang in SUPPORTED_LANGUAGES:
        return lang
    return ""


def get_hidden_models(user_id: str) -> list:
    """读取用户隐藏的 LLM model ID 列表。"""
    return list(get_preferences(user_id).get("hidden_models") or [])


def set_hidden_models(user_id: str, hidden: list) -> None:
    """更新用户的隐藏 model 列表。"""
    update_preferences(user_id, {"hidden_models": hidden})
