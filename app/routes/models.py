"""
模型与 capability 默认 API。

数据源：
  - LLM 列表与 capability 默认均来自 Hybrid Catalog（见 ``services/model_catalog.py``）。
  - 用户偏好（capability_defaults）来自 ``services/preferences.py``。

向后兼容：
  - ``GET /api/models`` 响应形状沿用旧版（``models`` + ``default``），仅按用户已配置凭据过滤。
"""

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_current_user
from app.services.model_catalog import (
    list_models, get_default_model, find_model, CAPABILITIES,
)
from app.services.preferences import get_preferences, update_preferences

router = APIRouter(tags=["models"])

# capability_defaults 允许的 key（与 preferences 内部一致）
_CAPS_FOR_DEFAULTS = {"llm", "image", "tts", "video", "stt", "s2s"}


@router.get("/api/models")
async def api_list_models(user=Depends(get_current_user)):
    """LLM 列表（按已配置凭据过滤）+ 当前默认 LLM。"""
    user_id = user["user_id"]
    items = list_models("llm", user_id=user_id, only_available=True)
    models = [
        {"id": m["id"], "name": m.get("display_name") or m["id"],
         "provider": m.get("provider", ""), "tier": m.get("tier", "")}
        for m in items
    ]
    default_model = get_default_model("llm", user_id=user_id)
    if default_model and not any(m["id"] == default_model for m in models):
        # 用户偏好或仓库默认已被禁用/缺凭据，回退到首项
        default_model = models[0]["id"] if models else ""
    return {"models": models, "default": default_model}


@router.get("/api/capabilities/{capability}/models")
async def api_list_capability_models(capability: str, user=Depends(get_current_user)):
    """列出某 capability 下的可用 model（按凭据过滤），并返回当前默认。"""
    if capability not in CAPABILITIES:
        raise HTTPException(status_code=404, detail=f"未知 capability: {capability}")
    user_id = user["user_id"]
    available = list_models(capability, user_id=user_id, only_available=True)
    all_items = list_models(capability, user_id=user_id, only_available=False)

    available_ids = {m["id"] for m in available}

    items = []
    for m in all_items:
        items.append({
            "id": m["id"],
            "name": m.get("display_name") or m["id"],
            "provider": m.get("provider", ""),
            "tier": m.get("tier", ""),
            "available": m["id"] in available_ids,
            "default_params": m.get("default_params") or {},
            "params_schema": m.get("params_schema") or {},
        })
    return {
        "capability": capability,
        "models": items,
        "default": get_default_model(capability, user_id=user_id),
    }


@router.get("/api/capabilities/defaults")
async def api_get_capability_defaults(user=Depends(get_current_user)):
    """返回当前用户每个 capability 的默认 model（已合并仓库 fallback）。"""
    user_id = user["user_id"]
    return {cap: get_default_model(cap, user_id=user_id) for cap in CAPABILITIES}


@router.put("/api/capabilities/defaults")
async def api_update_capability_defaults(req: dict, user=Depends(get_current_user)):
    """更新用户的 capability 默认 model。

    Body: {"image": "openai:gpt-image-1", "tts": "...", ...}
    传入空串或 null 表示清除该 capability 的用户偏好（回落到 catalog defaults）。
    """
    user_id = user["user_id"]
    cleaned: dict = {}
    for cap, mid in (req or {}).items():
        if cap not in _CAPS_FOR_DEFAULTS:
            continue
        if mid in (None, ""):
            cleaned[cap] = ""
            continue
        if not isinstance(mid, str):
            continue
        # 校验存在性（仅 warn，不阻断）—— 允许用户手填 catalog 之外的自定义 model
        _ = find_model(mid, user_id=user_id)
        cleaned[cap] = mid
    update_preferences(user_id, {"capability_defaults": cleaned})
    return {
        "success": True,
        "defaults": {cap: get_default_model(cap, user_id=user_id) for cap in CAPABILITIES},
    }


@router.get("/api/catalog")
async def api_get_catalog(user=Depends(get_current_user)):
    """完整 catalog（仓库 + 用户合并）。前端模型管理页可基于此渲染。"""
    from app.services.model_catalog import get_catalog
    return get_catalog(user["user_id"])
