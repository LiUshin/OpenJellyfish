"""
模型目录（Hybrid Catalog）。

来源：
  - 仓库内置：``config/model_catalog.json``
  - 用户覆盖：``users/{uid}/model_catalog.json``

合并规则（深合并，针对每个 capability 列表）：
  - 仓库默认 + 用户列表，按 ``id`` 去重；用户条目优先（同 id 整条覆盖）。
  - ``defaults`` 字典：用户覆盖优先；用户未指定的 capability 沿用内置默认。

凭据过滤：
  ``list_models(user_id, capability, only_available=True)`` 仅返回当前用户已配置凭据的条目，
  便于设置页生成「可用模型选择器」。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from app.core.security import get_user_dir
from app.core.settings import ROOT_DIR

_BUILTIN_CATALOG_PATH = os.path.join(ROOT_DIR, "config", "model_catalog.json")
_USER_CATALOG_FILENAME = "model_catalog.json"

CAPABILITIES = ("llm", "image", "tts", "video", "stt", "s2s")

# 进程级缓存：内置 catalog 文件不会频繁变更
_builtin_cache: Optional[Dict[str, Any]] = None


def _load_builtin() -> Dict[str, Any]:
    global _builtin_cache
    if _builtin_cache is not None:
        return _builtin_cache
    if not os.path.isfile(_BUILTIN_CATALOG_PATH):
        _builtin_cache = {"version": 1, "defaults": {}, **{c: [] for c in CAPABILITIES}}
        return _builtin_cache
    try:
        with open(_BUILTIN_CATALOG_PATH, "r", encoding="utf-8") as f:
            _builtin_cache = json.load(f)
    except Exception:
        _builtin_cache = {"version": 1, "defaults": {}, **{c: [] for c in CAPABILITIES}}
    return _builtin_cache


def reload_builtin() -> None:
    """强制重读仓库 catalog；测试或手工热更时用。"""
    global _builtin_cache
    _builtin_cache = None


def _user_catalog_path(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), _USER_CATALOG_FILENAME)


def _load_user(user_id: Optional[str]) -> Dict[str, Any]:
    if not user_id:
        return {}
    path = _user_catalog_path(user_id)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _merge_lists(builtin: List[Dict[str, Any]], user: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """user 优先；同 id 用 user 整条覆盖；user 独有的追加在末尾。"""
    by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for item in builtin:
        mid = item.get("id")
        if not mid:
            continue
        by_id[mid] = item
        order.append(mid)
    for item in user:
        mid = item.get("id")
        if not mid:
            continue
        if mid not in by_id:
            order.append(mid)
        by_id[mid] = item
    return [by_id[mid] for mid in order]


def get_catalog(user_id: Optional[str] = None) -> Dict[str, Any]:
    """合并后的完整 catalog。"""
    builtin = _load_builtin()
    user = _load_user(user_id)

    out: Dict[str, Any] = {
        "version": builtin.get("version", 1),
        "defaults": {**builtin.get("defaults", {}), **(user.get("defaults") or {})},
    }
    for cap in CAPABILITIES:
        out[cap] = _merge_lists(builtin.get(cap) or [], user.get(cap) or [])
    return out


def list_models(
    capability: str,
    user_id: Optional[str] = None,
    only_available: bool = False,
) -> List[Dict[str, Any]]:
    """列出某 capability 下所有 model 条目。

    only_available=True 时按当前用户已配置凭据过滤（设置页选择器使用）。
    """
    catalog = get_catalog(user_id)
    items: List[Dict[str, Any]] = list(catalog.get(capability) or [])
    if only_available:
        from app.core.api_config import has_provider_credentials
        items = [m for m in items if has_provider_credentials(
            m.get("provider", ""), user_id=user_id, capability=capability,
        )]
    return items


def find_model(model_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """根据完整 ``provider:model`` 找到 catalog 条目（任一 capability）。"""
    catalog = get_catalog(user_id)
    for cap in CAPABILITIES:
        for m in catalog.get(cap) or []:
            if m.get("id") == model_id:
                return {**m, "capability": cap}
    return None


def get_default_model(capability: str, user_id: Optional[str] = None) -> str:
    """解析 capability 默认 model：用户偏好 > catalog defaults 块。

    用户偏好走 ``preferences.capability_defaults``（在 ``services/preferences.py``）。
    """
    if user_id:
        try:
            from app.services.preferences import get_capability_default
            override = get_capability_default(user_id, capability)
            if override:
                return override
        except Exception:
            pass
    catalog = get_catalog(user_id)
    return (catalog.get("defaults") or {}).get(capability, "")


def resolve_model(
    capability: str,
    user_id: Optional[str] = None,
    explicit: Optional[str] = None,
) -> str:
    """供 @tool 使用：调用方传入 explicit（例如 LLM 由 chat 页传入），
    没有就回退到用户/默认。"""
    if explicit:
        return explicit
    return get_default_model(capability, user_id=user_id)
