import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends

from app.schemas.requests import (
    SystemPromptRequest, SaveVersionRequest, UpdateVersionMetaRequest,
    UserProfileRequest, SubagentRequest, SubagentUpdateRequest,
)
from app.services.prompt import (
    get_user_system_prompt, set_user_system_prompt, reset_user_system_prompt,
    DEFAULT_SYSTEM_PROMPT, list_prompt_versions, save_prompt_version,
    get_prompt_version, update_prompt_version_meta, delete_prompt_version, rollback_prompt_version,
    get_user_profile, set_user_profile,
    list_profile_versions, get_profile_version, delete_profile_version, rollback_profile_version,
)
from app.services.subagents import (
    list_user_subagents, get_user_subagent, add_user_subagent,
    update_user_subagent, delete_user_subagent, SHARED_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
)
from app.services.memory_tools import get_soul_config, save_soul_config, sync_soul_symlink
from app.services.prompt import get_capability_prompts, save_capability_prompts
from app.services.preferences import get_preferences, update_preferences
from app.services.venv_manager import (
    list_all_packages, install_package, uninstall_package,
    ensure_venv, venv_exists,
)
from app.deps import get_current_user

router = APIRouter(tags=["settings"])


# ── System Prompt ──────────────────────────────────────────────────

@router.get("/api/system-prompt")
async def api_get_system_prompt(user=Depends(get_current_user)):
    prompt = get_user_system_prompt(user["user_id"])
    return {"prompt": prompt, "is_default": prompt == DEFAULT_SYSTEM_PROMPT}


@router.put("/api/system-prompt")
async def api_update_system_prompt(req: SystemPromptRequest, user=Depends(get_current_user)):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt 不能为空")
    set_user_system_prompt(user["user_id"], req.prompt)
    return {"success": True, "message": "System prompt 已更新，下次对话将使用新 prompt"}


@router.delete("/api/system-prompt")
async def api_reset_system_prompt(user=Depends(get_current_user)):
    reset_user_system_prompt(user["user_id"])
    return {"success": True, "prompt": DEFAULT_SYSTEM_PROMPT}


# ── Prompt Versions ────────────────────────────────────────────────

@router.get("/api/system-prompt/versions")
async def api_list_prompt_versions(user=Depends(get_current_user)):
    return list_prompt_versions(user["user_id"])


@router.post("/api/system-prompt/versions")
async def api_save_prompt_version(req: SaveVersionRequest, user=Depends(get_current_user)):
    return save_prompt_version(user["user_id"], req.content, req.label, req.note)


@router.get("/api/system-prompt/versions/{version_id}")
async def api_get_prompt_version(version_id: str, user=Depends(get_current_user)):
    v = get_prompt_version(user["user_id"], version_id)
    if not v:
        raise HTTPException(status_code=404, detail="版本不存在")
    return v


@router.put("/api/system-prompt/versions/{version_id}")
async def api_update_prompt_version(version_id: str, req: UpdateVersionMetaRequest, user=Depends(get_current_user)):
    ok = update_prompt_version_meta(user["user_id"], version_id, req.label, req.note)
    if not ok:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"success": True}


@router.delete("/api/system-prompt/versions/{version_id}")
async def api_delete_prompt_version(version_id: str, user=Depends(get_current_user)):
    ok = delete_prompt_version(user["user_id"], version_id)
    if not ok:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"success": True}


@router.post("/api/system-prompt/versions/{version_id}/rollback")
async def api_rollback_prompt_version(version_id: str, user=Depends(get_current_user)):
    content = rollback_prompt_version(user["user_id"], version_id)
    if content is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"success": True, "prompt": content}


# ── User Profile ───────────────────────────────────────────────────

@router.get("/api/user-profile")
async def api_get_user_profile(user=Depends(get_current_user)):
    return {"profile": get_user_profile(user["user_id"])}


@router.put("/api/user-profile")
async def api_update_user_profile(req: UserProfileRequest, user=Depends(get_current_user)):
    profile = {
        "portfolio": req.portfolio,
        "risk_preference": req.risk_preference,
        "investment_habits": req.investment_habits,
        "user_persona": req.user_persona,
        "custom_notes": req.custom_notes,
    }
    set_user_profile(user["user_id"], profile)
    return {"success": True, "message": "个性规则已更新，下次对话将根据规则个性化回复"}


@router.get("/api/user-profile/versions")
async def api_list_profile_versions(user=Depends(get_current_user)):
    return list_profile_versions(user["user_id"])


@router.get("/api/user-profile/versions/{version_id}")
async def api_get_profile_version(version_id: str, user=Depends(get_current_user)):
    v = get_profile_version(user["user_id"], version_id)
    if not v:
        raise HTTPException(status_code=404, detail="版本不存在")
    return v


@router.delete("/api/user-profile/versions/{version_id}")
async def api_delete_profile_version(version_id: str, user=Depends(get_current_user)):
    if not delete_profile_version(user["user_id"], version_id):
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"success": True}


@router.post("/api/user-profile/versions/{version_id}/rollback")
async def api_rollback_profile_version(version_id: str, user=Depends(get_current_user)):
    content = rollback_profile_version(user["user_id"], version_id)
    if content is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"success": True, "content": content}


# ── Subagents ──────────────────────────────────────────────────────

@router.get("/api/subagents")
async def api_list_subagents(user=Depends(get_current_user)):
    all_tools = sorted(SHARED_TOOL_NAMES | MEMORY_TOOL_NAMES)
    return {"subagents": list_user_subagents(user["user_id"]), "available_tools": all_tools}


@router.post("/api/subagents")
async def api_add_subagent(req: SubagentRequest, user=Depends(get_current_user)):
    config = {"name": req.name, "description": req.description, "system_prompt": req.system_prompt,
              "tools": req.tools, "enabled": req.enabled}
    if req.model:
        config["model"] = req.model
    return add_user_subagent(user["user_id"], config)


@router.get("/api/subagents/{subagent_id}")
async def api_get_subagent(subagent_id: str, user=Depends(get_current_user)):
    sa = get_user_subagent(user["user_id"], subagent_id)
    if not sa:
        raise HTTPException(status_code=404, detail="Subagent 不存在")
    return sa


@router.put("/api/subagents/{subagent_id}")
async def api_update_subagent(subagent_id: str, req: SubagentUpdateRequest, user=Depends(get_current_user)):
    updates = {k: v for k, v in req.dict().items() if v is not None}
    if not update_user_subagent(user["user_id"], subagent_id, updates):
        raise HTTPException(status_code=404, detail="Subagent 不存在")
    return {"success": True}


@router.delete("/api/subagents/{subagent_id}")
async def api_delete_subagent(subagent_id: str, user=Depends(get_current_user)):
    if not delete_user_subagent(user["user_id"], subagent_id):
        raise HTTPException(status_code=404, detail="Subagent 不存在")
    return {"success": True}


# ── Soul Config ───────────────────────────────────────────────────

@router.get("/api/soul/config")
async def api_get_soul_config(user=Depends(get_current_user)):
    return get_soul_config(user["user_id"])


@router.put("/api/soul/config")
async def api_update_soul_config(req: dict, user=Depends(get_current_user)):
    from app.services.agent import clear_agent_cache
    user_id = user["user_id"]
    current = get_soul_config(user_id)
    allowed_keys = {"memory_enabled", "include_consumer_conversations",
                    "max_recent_messages", "memory_subagent_enabled", "soul_edit_enabled"}
    for k, v in req.items():
        if k in allowed_keys:
            current[k] = v
    save_soul_config(user_id, current)
    sync_soul_symlink(user_id)
    clear_agent_cache(user_id)
    return {"success": True, "config": current}


# ── Capability Prompts ────────────────────────────────────────────

@router.get("/api/capability-prompts")
async def api_get_capability_prompts(user=Depends(get_current_user)):
    from app.services.tools import CAPABILITY_PROMPTS
    overrides = get_capability_prompts(user["user_id"])
    items = []
    for key, default_text in CAPABILITY_PROMPTS.items():
        items.append({
            "key": key,
            "default": default_text.strip(),
            "custom": overrides.get(key, "").strip() if key in overrides else None,
        })
    return {"prompts": items}


@router.put("/api/capability-prompts/{key}")
async def api_update_capability_prompt(key: str, req: dict, user=Depends(get_current_user)):
    from app.services.tools import CAPABILITY_PROMPTS
    if key not in CAPABILITY_PROMPTS:
        raise HTTPException(status_code=404, detail=f"未知的能力提示词: {key}")
    user_id = user["user_id"]
    overrides = get_capability_prompts(user_id)
    text = req.get("text", "")
    if text.strip():
        overrides[key] = text
    elif key in overrides:
        del overrides[key]
    save_capability_prompts(user_id, overrides)
    return {"success": True}


@router.delete("/api/capability-prompts/{key}")
async def api_reset_capability_prompt(key: str, user=Depends(get_current_user)):
    user_id = user["user_id"]
    overrides = get_capability_prompts(user_id)
    if key in overrides:
        del overrides[key]
        save_capability_prompts(user_id, overrides)
    return {"success": True}


# ── Python Packages (per-user venv) ──────────────────────────────

@router.get("/api/packages")
async def api_list_packages(user=Depends(get_current_user)):
    user_id = user["user_id"]
    packages = list_all_packages(user_id)
    return {"packages": packages, "venv_ready": venv_exists(user_id)}


@router.post("/api/packages/init")
async def api_init_venv(user=Depends(get_current_user)):
    user_id = user["user_id"]
    try:
        await ensure_venv(user_id)
        return {"success": True}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/packages/install")
async def api_install_package(req: dict, user=Depends(get_current_user)):
    package = req.get("package", "").strip()
    if not package:
        raise HTTPException(status_code=400, detail="请指定包名")
    if any(c in package for c in ";|&$`"):
        raise HTTPException(status_code=400, detail="包名包含非法字符")
    result = await install_package(user["user_id"], package)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "安装失败"))
    return result


@router.post("/api/packages/uninstall")
async def api_uninstall_package(req: dict, user=Depends(get_current_user)):
    package = req.get("package", "").strip()
    if not package:
        raise HTTPException(status_code=400, detail="请指定包名")
    result = await uninstall_package(user["user_id"], package)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "卸载失败"))
    return result


# ── API Keys (per-user) ──────────────────────────────────────────

@router.get("/api/settings/api-keys")
async def api_get_api_keys(user=Depends(get_current_user)):
    from app.core.user_api_keys import get_masked_keys
    return get_masked_keys(user["user_id"])


@router.put("/api/settings/api-keys")
async def api_update_api_keys(req: dict, user=Depends(get_current_user)):
    from app.core.user_api_keys import save_user_api_keys, get_masked_keys, ALL_FIELDS
    from app.services.agent import clear_agent_cache
    from app.services.consumer_agent import clear_consumer_cache

    user_id = user["user_id"]
    filtered = {k: v for k, v in req.items() if k in ALL_FIELDS and isinstance(v, str)}
    if not filtered:
        return {"success": False, "detail": "未提供有效的字段"}

    save_user_api_keys(user_id, filtered)
    clear_agent_cache(user_id)
    clear_consumer_cache(admin_id=user_id)

    return {"success": True, "keys": get_masked_keys(user_id)}


@router.post("/api/settings/api-keys/test")
async def api_test_api_keys(req: dict, user=Depends(get_current_user)):
    """Test connectivity for a specific provider using the user's configured keys."""
    import httpx as _httpx
    from app.core.user_api_keys import get_user_api_keys

    user_id = user["user_id"]
    provider = req.get("provider", "")
    keys = get_user_api_keys(user_id)
    results = {}

    if provider in ("openai", "all"):
        api_key = keys.get("openai_api_key", "") or os.getenv("OPENAI_API_KEY", "")
        base_url = (keys.get("openai_base_url", "") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        if api_key:
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                results["openai"] = {"ok": resp.status_code == 200, "status": resp.status_code}
            except Exception as e:
                results["openai"] = {"ok": False, "error": str(e)[:200]}
        else:
            results["openai"] = {"ok": False, "error": "未配置 API Key"}

    if provider in ("anthropic", "all"):
        api_key = keys.get("anthropic_api_key", "") or os.getenv("ANTHROPIC_API_KEY", "")
        base_url = (keys.get("anthropic_base_url", "") or os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")).rstrip("/")
        if api_key:
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{base_url}/v1/models",
                        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                    )
                results["anthropic"] = {"ok": resp.status_code == 200, "status": resp.status_code}
            except Exception as e:
                results["anthropic"] = {"ok": False, "error": str(e)[:200]}
        else:
            results["anthropic"] = {"ok": False, "error": "未配置 API Key"}

    if provider in ("tavily", "all"):
        api_key = keys.get("tavily_api_key", "") or os.getenv("TAVILY_API_KEY", "")
        if api_key:
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        "https://api.tavily.com/search",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={"query": "test", "max_results": 1, "search_depth": "basic"},
                    )
                results["tavily"] = {"ok": resp.status_code == 200, "status": resp.status_code}
            except Exception as e:
                results["tavily"] = {"ok": False, "error": str(e)[:200]}
        else:
            results["tavily"] = {"ok": False, "error": "未配置 API Key"}

    return {"results": results}


@router.get("/api/settings/api-keys/status")
async def api_keys_status(user=Depends(get_current_user)):
    """Quick check: does the user have at least one LLM provider configured?"""
    from app.core.api_config import has_provider
    user_id = user["user_id"]
    has_any_llm = has_provider("openai", user_id=user_id) or has_provider("anthropic", user_id=user_id)
    return {
        "has_llm": has_any_llm,
        "has_openai": has_provider("openai", user_id=user_id),
        "has_anthropic": has_provider("anthropic", user_id=user_id),
    }


# ── User Preferences ─────────────────────────────────────────────

@router.get("/api/preferences")
async def api_get_preferences(user=Depends(get_current_user)):
    return get_preferences(user["user_id"])


@router.put("/api/preferences")
async def api_update_preferences(req: dict, user=Depends(get_current_user)):
    from app.services.agent import clear_agent_cache
    prefs = update_preferences(user["user_id"], req)
    clear_agent_cache(user["user_id"])
    return prefs


@router.get("/api/server-time")
async def api_server_time(user=Depends(get_current_user)):
    return {"server_time": datetime.now(timezone.utc).isoformat()}
