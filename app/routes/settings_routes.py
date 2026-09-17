import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Request

from app.core.i18n import resolve_lang, t

from app.schemas.requests import (
    SystemPromptRequest, SaveVersionRequest, UpdateVersionMetaRequest,
    UserProfileRequest, AgentNotesRequest, SubagentRequest, SubagentUpdateRequest,
)
from app.services.prompt import (
    get_user_system_prompt, set_user_system_prompt, reset_user_system_prompt,
    DEFAULT_SYSTEM_PROMPT, list_prompt_versions, save_prompt_version,
    get_prompt_version, update_prompt_version_meta, delete_prompt_version, rollback_prompt_version,
    get_user_profile, set_user_profile,
    list_profile_versions, get_profile_version, update_profile_version_meta,
    delete_profile_version, rollback_profile_version,
    get_agent_notes, set_agent_notes, is_agent_notes_locked,
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
async def api_update_system_prompt(req: SystemPromptRequest, request: Request, user=Depends(get_current_user)):
    lang = resolve_lang(request)
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail=t("system_prompt.empty", lang))
    set_user_system_prompt(user["user_id"], req.prompt)
    return {"success": True, "message": t("system_prompt.updated", lang)}


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
async def api_get_prompt_version(version_id: str, request: Request, user=Depends(get_current_user)):
    v = get_prompt_version(user["user_id"], version_id)
    if not v:
        raise HTTPException(status_code=404, detail=t("version.not_found", resolve_lang(request)))
    return v


@router.put("/api/system-prompt/versions/{version_id}")
async def api_update_prompt_version(version_id: str, req: UpdateVersionMetaRequest, request: Request, user=Depends(get_current_user)):
    ok = update_prompt_version_meta(user["user_id"], version_id, req.label, req.note)
    if not ok:
        raise HTTPException(status_code=404, detail=t("version.not_found", resolve_lang(request)))
    return {"success": True}


@router.delete("/api/system-prompt/versions/{version_id}")
async def api_delete_prompt_version(version_id: str, request: Request, user=Depends(get_current_user)):
    ok = delete_prompt_version(user["user_id"], version_id)
    if not ok:
        raise HTTPException(status_code=404, detail=t("version.not_found", resolve_lang(request)))
    return {"success": True}


@router.post("/api/system-prompt/versions/{version_id}/rollback")
async def api_rollback_prompt_version(version_id: str, request: Request, user=Depends(get_current_user)):
    content = rollback_prompt_version(user["user_id"], version_id)
    if content is None:
        raise HTTPException(status_code=404, detail=t("version.not_found", resolve_lang(request)))
    return {"success": True, "prompt": content}


# ── User Profile ───────────────────────────────────────────────────

@router.get("/api/user-profile")
async def api_get_user_profile(user=Depends(get_current_user)):
    return {"profile": get_user_profile(user["user_id"])}


@router.put("/api/user-profile")
async def api_update_user_profile(req: UserProfileRequest, request: Request, user=Depends(get_current_user)):
    # `agent_notes` / `agent_notes_locked` are user-managed here (Edit & Lock).
    # The agent itself writes via the `update_personal_memory` tool, which
    # bypasses versioning. Versioning continues to track `custom_notes` only.
    profile = {
        "portfolio": req.portfolio,
        "risk_preference": req.risk_preference,
        "investment_habits": req.investment_habits,
        "user_persona": req.user_persona,
        "custom_notes": req.custom_notes,
        "agent_notes": req.agent_notes,
        "agent_notes_locked": req.agent_notes_locked,
    }
    set_user_profile(user["user_id"], profile)
    return {"success": True, "message": t("user_profile.updated", resolve_lang(request))}


@router.get("/api/user-profile/agent-notes")
async def api_get_agent_notes(user=Depends(get_current_user)):
    """Return the agent-managed memory block (separate from custom_notes).

    Read by the UserProfileEditor's upper "Agent 记忆" pane. Writes go
    through PUT to keep version history clean (no profile_versions entry).
    """
    return {
        "content": get_agent_notes(user["user_id"]),
        "locked": is_agent_notes_locked(user["user_id"]),
    }


@router.put("/api/user-profile/agent-notes")
async def api_update_agent_notes(req: AgentNotesRequest, user=Depends(get_current_user)):
    profile = get_user_profile(user["user_id"])
    profile["agent_notes"] = req.content
    profile["agent_notes_locked"] = bool(req.locked)
    # auto_version=False: agent-notes edits should not snapshot custom_notes.
    set_user_profile(user["user_id"], profile, auto_version=False)
    return {"success": True}


@router.get("/api/user-profile/versions")
async def api_list_profile_versions(user=Depends(get_current_user)):
    return list_profile_versions(user["user_id"])


@router.get("/api/user-profile/versions/{version_id}")
async def api_get_profile_version(version_id: str, request: Request, user=Depends(get_current_user)):
    v = get_profile_version(user["user_id"], version_id)
    if not v:
        raise HTTPException(status_code=404, detail=t("version.not_found", resolve_lang(request)))
    return v


@router.put("/api/user-profile/versions/{version_id}")
async def api_update_profile_version(version_id: str, req: UpdateVersionMetaRequest, request: Request, user=Depends(get_current_user)):
    ok = update_profile_version_meta(user["user_id"], version_id, req.label, req.note)
    if not ok:
        raise HTTPException(status_code=404, detail=t("version.not_found", resolve_lang(request)))
    return {"success": True}


@router.delete("/api/user-profile/versions/{version_id}")
async def api_delete_profile_version(version_id: str, request: Request, user=Depends(get_current_user)):
    if not delete_profile_version(user["user_id"], version_id):
        raise HTTPException(status_code=404, detail=t("version.not_found", resolve_lang(request)))
    return {"success": True}


@router.post("/api/user-profile/versions/{version_id}/rollback")
async def api_rollback_profile_version(version_id: str, request: Request, user=Depends(get_current_user)):
    content = rollback_profile_version(user["user_id"], version_id)
    if content is None:
        raise HTTPException(status_code=404, detail=t("version.not_found", resolve_lang(request)))
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
    from app.core.user_api_keys import (
        save_user_api_keys, save_credential_sources, get_masked_keys, ALL_FIELDS,
    )
    from app.services.agent import clear_agent_cache
    from app.services.consumer_agent import clear_consumer_cache

    user_id = user["user_id"]
    filtered = {k: v for k, v in req.items() if k in ALL_FIELDS and isinstance(v, str)}
    sources_raw = req.get("credential_sources")
    sources = sources_raw if isinstance(sources_raw, dict) else None
    if not filtered and not sources:
        return {"success": False, "detail": "未提供有效的字段"}

    if filtered:
        save_user_api_keys(user_id, filtered)
    if sources:
        # values must be str "platform"|"user"
        cleaned = {str(k): str(v) for k, v in sources.items()}
        save_credential_sources(user_id, cleaned)
    clear_agent_cache(user_id)
    clear_consumer_cache(admin_id=user_id)

    return {"success": True, "keys": get_masked_keys(user_id)}


@router.post("/api/settings/api-keys/test")
async def api_test_api_keys(req: dict, user=Depends(get_current_user)):
    """Test connectivity using the active credential source (platform vs user)."""
    import httpx as _httpx
    from app.core.api_config import get_provider_credentials, resolve_credential_source
    from app.services.web_tools import _resolve_keys

    user_id = user["user_id"]
    provider = req.get("provider", "")
    results = {}

    if provider in ("openai", "all"):
        creds = get_provider_credentials("openai", user_id=user_id)
        api_key = creds.get("api_key", "")
        base_url = (creds.get("base_url") or "https://api.openai.com/v1").rstrip("/")
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
            src = resolve_credential_source("openai", user_id)
            results["openai"] = {
                "ok": False,
                "error": "未配置我的 Key" if src == "user" else "未配置平台 Key",
            }

    if provider in ("anthropic", "all"):
        creds = get_provider_credentials("anthropic", user_id=user_id)
        api_key = creds.get("api_key", "")
        base_url = (creds.get("base_url") or "https://api.anthropic.com").rstrip("/")
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
            src = resolve_credential_source("anthropic", user_id)
            results["anthropic"] = {
                "ok": False,
                "error": "未配置我的 Key" if src == "user" else "未配置平台 Key",
            }

    if provider in ("tavily", "all"):
        _cw, api_key = _resolve_keys(user_id)
        # Prefer tavily for this button; fall back to cloudsway presence as ok-ish probe
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
        elif _cw:
            results["tavily"] = {"ok": True, "status": 0}  # platform/user has CloudsWay
        else:
            src = resolve_credential_source("tavily", user_id)
            results["tavily"] = {
                "ok": False,
                "error": "未配置我的 Key" if src == "user" else "未配置平台 Key",
            }

    if provider in ("kimi", "all"):
        creds = get_provider_credentials("kimi", user_id=user_id)
        api_key = creds.get("api_key", "")
        base_url = (creds.get("base_url") or "https://api.moonshot.cn/v1").rstrip("/")
        if api_key:
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                results["kimi"] = {"ok": resp.status_code == 200, "status": resp.status_code}
            except Exception as e:
                results["kimi"] = {"ok": False, "error": str(e)[:200]}
        else:
            src = resolve_credential_source("kimi", user_id)
            results["kimi"] = {
                "ok": False,
                "error": "未配置我的 Key" if src == "user" else "未配置平台 Key",
            }

    if provider in ("minimax", "all"):
        creds = get_provider_credentials("minimax", user_id=user_id, capability="llm")
        api_key = creds.get("api_key", "")
        if api_key:
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        "https://api.minimax.io/v1/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                results["minimax"] = {"ok": resp.status_code == 200, "status": resp.status_code}
            except Exception as e:
                results["minimax"] = {"ok": False, "error": str(e)[:200]}
        else:
            src = resolve_credential_source("minimax", user_id)
            results["minimax"] = {
                "ok": False,
                "error": "未配置我的 Key" if src == "user" else "未配置平台 Key",
            }

    if provider in ("bedrock", "all"):
        creds = get_provider_credentials("bedrock", user_id=user_id)
        api_key = creds.get("api_key", "")
        region = creds.get("region") or "us-east-1"
        if api_key:
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"https://bedrock.{region}.amazonaws.com/foundation-models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                results["bedrock"] = {"ok": resp.status_code == 200, "status": resp.status_code}
            except Exception as e:
                results["bedrock"] = {"ok": False, "error": str(e)[:200]}
        else:
            src = resolve_credential_source("bedrock", user_id)
            results["bedrock"] = {
                "ok": False,
                "error": "未配置我的 Key" if src == "user" else "未配置平台 Key",
            }

    # OpenAI-compat aggregators all probe the same way: GET {base}/models.
    from app.core.api_config import AGGREGATORS
    for name in AGGREGATORS:
        if provider not in (name, "all"):
            continue
        creds = get_provider_credentials(name, user_id=user_id)
        api_key = creds.get("api_key", "")
        base_url = (creds.get("base_url") or AGGREGATORS[name]["default_base"]).rstrip("/")
        if api_key:
            try:
                async with _httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        f"{base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                results[name] = {"ok": resp.status_code == 200, "status": resp.status_code}
            except Exception as e:
                results[name] = {"ok": False, "error": str(e)[:200]}
        else:
            src = resolve_credential_source(name, user_id)
            results[name] = {
                "ok": False,
                "error": "未配置我的 Key" if src == "user" else "未配置平台 Key",
            }

    return {"results": results}


@router.get("/api/settings/api-keys/status")
async def api_keys_status(user=Depends(get_current_user)):
    """Quick check: does the user have at least one LLM provider configured?"""
    from app.core.api_config import has_provider
    user_id = user["user_id"]
    from app.core.api_config import has_provider_credentials
    minimax_llm_ok = has_provider_credentials("minimax", user_id=user_id, capability="llm")
    has_any_llm = (
        has_provider("openai", user_id=user_id)
        or has_provider("anthropic", user_id=user_id)
        or has_provider("kimi", user_id=user_id)
        or minimax_llm_ok
        or has_provider("bedrock", user_id=user_id)
        or has_provider("openrouter", user_id=user_id)
        or has_provider("siliconflow", user_id=user_id)
    )
    return {
        "has_llm": has_any_llm,
        "has_openai": has_provider("openai", user_id=user_id),
        "has_anthropic": has_provider("anthropic", user_id=user_id),
        "has_kimi": has_provider("kimi", user_id=user_id),
        # MiniMax LLM 仅需 api_key；TTS/Video 才需要 group_id（has_provider("minimax") 严格版）
        "has_minimax_llm": minimax_llm_ok,
        "has_minimax_full": has_provider("minimax", user_id=user_id),
        "has_bedrock": has_provider("bedrock", user_id=user_id),
        "has_openrouter": has_provider("openrouter", user_id=user_id),
        "has_siliconflow": has_provider("siliconflow", user_id=user_id),
    }


# ── Aggregator model whitelists (OpenRouter / 硅基流动) ───────────
#
# These vendors resell hundreds of models that turn over constantly, so instead
# of listing them in config/model_catalog.json the Admin picks the ones they
# want and the picks become catalog entries.

# Query params narrowing each vendor's /models listing to text chat models.
_AGGREGATOR_LIST_PARAMS = {
    "openrouter": {"output_modalities": "text"},
    "siliconflow": {"type": "text", "sub_type": "chat"},
}


def _require_aggregator(provider: str) -> str:
    from app.services.preferences import AGGREGATOR_PROVIDERS
    if provider not in AGGREGATOR_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider: {provider}")
    return provider


@router.get("/api/settings/aggregators/{provider}/enabled-models")
async def api_get_aggregator_enabled(provider: str, user=Depends(get_current_user)):
    """Admin whitelist for one aggregator (synthetic catalog entries)."""
    from app.services.preferences import get_enabled_models
    return {"models": get_enabled_models(user["user_id"], _require_aggregator(provider))}


@router.put("/api/settings/aggregators/{provider}/enabled-models")
async def api_put_aggregator_enabled(
    provider: str, req: dict, user=Depends(get_current_user),
):
    """Replace one aggregator's whitelist.

    Body: {"models": [{"id":"Qwen/Qwen3-32B","name":"...","reasoning":true,
                       "thinking_budget":4096}, ...]}
    """
    from app.services.preferences import set_enabled_models
    from app.services.agent import clear_agent_cache
    from app.services.consumer_agent import clear_consumer_cache

    _require_aggregator(provider)
    models = req.get("models") if isinstance(req, dict) else None
    if not isinstance(models, list):
        raise HTTPException(status_code=400, detail="models must be a list")
    user_id = user["user_id"]
    saved = set_enabled_models(user_id, provider, models)
    clear_agent_cache(user_id)
    clear_consumer_cache(admin_id=user_id)
    return {"success": True, "models": saved}


@router.get("/api/settings/aggregators/{provider}/remote-models")
async def api_proxy_aggregator_models(provider: str, user=Depends(get_current_user)):
    """Proxy the vendor's GET /models with the active credentials.

    SiliconFlow requires auth to list models, so this is the only path for it.
    OpenRouter's list is public and the frontend prefers calling it directly;
    this stays as the fallback for when browser CORS blocks that.
    """
    import httpx as _httpx
    from app.core.api_config import AGGREGATORS, get_provider_credentials

    _require_aggregator(provider)
    creds = get_provider_credentials(provider, user_id=user["user_id"])
    base_url = (creds.get("base_url") or AGGREGATORS[provider]["default_base"]).rstrip("/")
    headers = {}
    if creds.get("api_key"):
        headers["Authorization"] = f"Bearer {creds['api_key']}"
    try:
        async with _httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{base_url}/models",
                params=_AGGREGATOR_LIST_PARAMS.get(provider) or {},
                headers=headers,
            )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"{provider} models HTTP {resp.status_code}: {resp.text[:300]}",
            )
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)[:300]) from e


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
