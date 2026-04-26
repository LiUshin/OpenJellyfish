"""
Agent factory — creates and caches per-user deepagents instances.
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

import aiosqlite
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from deepagents import create_deep_agent

from app.core.settings import ROOT_DIR, DEFAULT_MODEL, CHECKPOINT_DB
from app.core.security import get_user_filesystem_dir, get_user_dir
from app.storage import create_agent_backend

# ==================== Checkpointer ====================

_checkpointer: AsyncSqliteSaver = None  # type: ignore[assignment]


async def init_checkpointer():
    global _checkpointer
    if _checkpointer is not None:
        return
    conn = await aiosqlite.connect(CHECKPOINT_DB)
    # WAL 模式允许并发读（多路由/多 bridge 同时流式读取 checkpoint），
    # 并减少写入阻塞读取的概率；对单机多客户端场景尤其有益。
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.commit()
    except Exception:
        # PRAGMA 失败不应阻塞启动（极少数文件系统不支持 WAL，如部分网络盘）
        pass
    _checkpointer = AsyncSqliteSaver(conn)
    await _checkpointer.setup()


# ==================== Model resolution ====================

THINKING_MODEL_CONFIG = {
    # Opus 4.7: 新 API 仅支持 adaptive thinking（设 budget_tokens 会 400）；
    # 同时 temperature/top_p/top_k 不接受非默认值，所以 params 里不能传采样参数。
    "anthropic:claude-opus-4-7-thinking": {
        "base_model": "anthropic:claude-opus-4-7",
        "params": {"thinking": {"type": "adaptive"}, "max_tokens": 32000},
    },
    "anthropic:claude-opus-4-6-thinking": {
        "base_model": "anthropic:claude-opus-4-6",
        "params": {"thinking": {"type": "enabled", "budget_tokens": 16000}, "max_tokens": 32000},
    },
    "anthropic:claude-sonnet-4-6-thinking": {
        "base_model": "anthropic:claude-sonnet-4-6",
        "params": {"thinking": {"type": "enabled", "budget_tokens": 10000}, "max_tokens": 16000},
    },
    "anthropic:claude-haiku-4-5-thinking": {
        "base_model": "anthropic:claude-haiku-4-5-20251001",
        "params": {"thinking": {"type": "enabled", "budget_tokens": 5000}, "max_tokens": 16000},
    },
    "anthropic:claude-sonnet-4-5-thinking": {
        "base_model": "anthropic:claude-sonnet-4-5-20250929",
        "params": {"thinking": {"type": "enabled", "budget_tokens": 10000}, "max_tokens": 16000},
    },
    "openai:gpt-5.4": {
        "base_model": "openai:gpt-5.4",
        "params": {
            "use_responses_api": True,
            "model_kwargs": {"reasoning": {"effort": "high", "summary": "auto"}},
        },
    },
}


def _get_default_model(user_id: Optional[str] = None) -> str:
    """默认 LLM 解析顺序：用户偏好（capability_defaults.llm）> agent_config.json > DEFAULT_MODEL。

    历史调用点没传 user_id（如 inbox / scheduler 顶层），此时跳过用户偏好直接读全局配置；
    传了 user_id 则优先尊重用户在设置页选择的默认 LLM。
    """
    if user_id:
        try:
            from app.services.model_catalog import get_default_model
            mid = get_default_model("llm", user_id=user_id)
            if mid:
                return mid
        except Exception:
            pass
    config_path = os.path.join(ROOT_DIR, "config", "agent_config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("main_agent", {}).get("model", DEFAULT_MODEL)
    return DEFAULT_MODEL


def _get_llm_config(model_id: str, user_id: Optional[str] = None):
    """Return (api_key, base_url_or_None) for a model's provider.

    用于内置「prefix == LangChain provider」的两家（openai / anthropic）。
    其他厂商（kimi 等 OpenAI-compat）走 _resolve_model 内的特例分支。
    """
    from app.core.api_config import get_openai_llm_config, get_anthropic_llm_config
    if model_id.startswith("openai:"):
        return get_openai_llm_config(user_id=user_id)
    if model_id.startswith("anthropic:"):
        return get_anthropic_llm_config(user_id=user_id)
    return "", None


def _resolve_model(model_id: str, user_id: Optional[str] = None):
    # Kimi（Moonshot）走 OpenAI-compat：去掉 kimi: 前缀，强制 model_provider=openai，
    # api_key/base_url 来自 get_provider_credentials("kimi")。
    if model_id.startswith("kimi:"):
        from app.core.api_config import get_provider_credentials
        creds = get_provider_credentials("kimi", user_id=user_id)
        if not creds.get("api_key"):
            raise RuntimeError("未配置 Kimi（Moonshot）API Key（设置页 → Kimi）")
        bare_model = model_id.split(":", 1)[1]
        return init_chat_model(
            model=bare_model,
            model_provider="openai",
            api_key=creds["api_key"],
            base_url=creds["base_url"],
        )

    # MiniMax LLM 走 Anthropic-compat（官方推荐，tool-use 原生）。
    # endpoint: https://api.minimax.io/anthropic
    if model_id.startswith("minimax:"):
        from app.core.api_config import get_provider_credentials
        creds = get_provider_credentials("minimax", user_id=user_id)
        if not creds.get("api_key"):
            raise RuntimeError("未配置 MiniMax API Key（设置页 → MiniMax）")
        bare_model = model_id.split(":", 1)[1]
        return init_chat_model(
            model=bare_model,
            model_provider="anthropic",
            api_key=creds["api_key"],
            base_url="https://api.minimax.io/anthropic",
        )

    api_key, base_url = _get_llm_config(model_id, user_id=user_id)
    extra_kwargs: Dict[str, Any] = {}
    if base_url:
        extra_kwargs["base_url"] = base_url
    if api_key:
        extra_kwargs["api_key"] = api_key

    if model_id in THINKING_MODEL_CONFIG:
        cfg = THINKING_MODEL_CONFIG[model_id]
        return init_chat_model(model=cfg["base_model"], **cfg["params"], **extra_kwargs)

    if extra_kwargs:
        return init_chat_model(model=model_id, **extra_kwargs)
    return model_id


# ==================== Agent cache ====================

_agent_cache: Dict[str, Any] = {}


def clear_agent_cache(user_id: Optional[str] = None):
    if user_id:
        keys = [k for k in _agent_cache if k.startswith(f"{user_id}::")]
        for k in keys:
            del _agent_cache[k]
    else:
        _agent_cache.clear()


# ==================== User agent ====================

def create_user_agent(
    user_id: str,
    model: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    username: Optional[str] = None,
) -> Any:
    from app.services.tools import (
        create_run_script_tool, create_ai_gen_tools, create_send_message_tool,
        create_web_tools, create_schedule_tool, create_manage_scheduled_tasks_tool,
        create_publish_service_task_tool, create_list_files_sorted_tool,
        create_move_file_tool,
        propose_plan, CAPABILITY_PROMPTS, PLAN_MODE_PROMPT,
    )
    from app.services.document_tools import create_document_tools
    from app.services.prompt import (
        get_user_system_prompt, build_user_profile_prompt,
        get_resolved_capability_prompt,
    )
    from app.services.subagents import build_subagents_for_agent

    if not model:
        model = _get_default_model(user_id=user_id)
    if not capabilities:
        capabilities = []

    cap_key = ",".join(sorted(capabilities)) if capabilities else "none"
    date_key = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"{user_id}::{model}::{cap_key}::{date_key}"

    if cache_key in _agent_cache:
        return _agent_cache[cache_key]

    from app.storage import get_storage_service
    from app.services.memory_tools import sync_soul_symlink
    storage = get_storage_service()
    storage.ensure_user_dirs(user_id)
    sync_soul_symlink(user_id)

    fs_dir = get_user_filesystem_dir(user_id)

    from app.services.preferences import get_tz_offset
    tz_hours = get_tz_offset(user_id)
    user_tz = timezone(timedelta(hours=tz_hours))
    user_now = datetime.now(user_tz)
    today_str = user_now.strftime("%Y年%m月%d日")
    user_profile_prompt = build_user_profile_prompt(user_id)
    system_prompt = get_user_system_prompt(user_id)
    system_prompt = system_prompt.replace("{today}", today_str)
    if "{user_profile_context}" in system_prompt:
        system_prompt = system_prompt.replace("{user_profile_context}", user_profile_prompt)
    elif user_profile_prompt:
        system_prompt += "\n\n" + user_profile_prompt

    def _cap(key: str) -> str:
        return get_resolved_capability_prompt(user_id, key, CAPABILITY_PROMPTS)

    for cap in capabilities:
        if cap in CAPABILITY_PROMPTS:
            system_prompt += "\n" + _cap(cap)

    backend = create_agent_backend(root_dir=fs_dir, user_id=user_id)

    tools = [
        create_run_script_tool(user_id),
        create_list_files_sorted_tool(user_id),
        create_move_file_tool(user_id),
    ]
    # Document parsing tools (read_document + view_pdf_page_or_image) — always
    # injected. They are read-only and operate inside fs_dir; the docs capability
    # prompt is unconditionally appended below so the agent knows when to use them.
    tools.extend(create_document_tools(user_id))
    if capabilities:
        ai_tools = create_ai_gen_tools(user_id)
        tool_map = {"image": ai_tools[0], "speech": ai_tools[1], "video": ai_tools[2]}
        for cap in capabilities:
            if cap in tool_map:
                tools.append(tool_map[cap])
    if "humanchat" in capabilities:
        tools.append(create_send_message_tool())

    tools.extend(create_web_tools(user_id=user_id))
    tools.append(create_schedule_tool(user_id))
    tools.append(create_manage_scheduled_tasks_tool(user_id))
    tools.append(create_publish_service_task_tool(user_id))
    if "web" not in capabilities:
        system_prompt += "\n" + _cap("web")
    system_prompt += "\n" + _cap("scheduler")
    system_prompt += "\n" + _cap("service_broadcast")
    # documents tool is always injected (read_document + view_pdf_page_or_image),
    # so unconditionally append its prompt; user can still override via
    # capability_prompts.json if they want to customise wording.
    system_prompt += "\n" + _cap("documents")

    from app.services.memory_tools import get_soul_config
    soul_config = get_soul_config(user_id)
    if soul_config.get("memory_subagent_enabled"):
        system_prompt += "\n" + _cap("memory_subagent")
    if soul_config.get("soul_edit_enabled"):
        system_prompt += "\n" + _cap("soul_edit")

    tools.append(propose_plan)

    subagents = build_subagents_for_agent(user_id)
    resolved_model = _resolve_model(model, user_id=user_id)

    agent = create_deep_agent(
        model=resolved_model,
        system_prompt=system_prompt,
        backend=backend,
        tools=tools,
        subagents=subagents,
        checkpointer=_checkpointer,
        name=username or user_id,
        interrupt_on={
            "write_file": {"allowed_decisions": ["approve", "edit", "reject"]},
            "edit_file": {"allowed_decisions": ["approve", "edit", "reject"]},
            "propose_plan": {"allowed_decisions": ["approve", "edit", "reject"]},
        },
    )

    _agent_cache[cache_key] = agent
    return agent


# ==================== Batch agent ====================

def create_batch_agent(
    user_id: str,
    model: str,
    prompt_content: str,
    capabilities: Optional[List[str]] = None,
) -> Any:
    """Create an agent for batch/scheduled execution.

    When capabilities is provided, the agent gets the corresponding tools
    (web, image, speech, video, etc.) — enabling full task-chain execution
    from skill/task documents.
    """
    from app.services.tools import (
        create_run_script_tool, create_ai_gen_tools, create_web_tools,
    )
    from app.services.document_tools import create_read_document_tool
    from app.storage import get_storage_service

    if capabilities is None:
        capabilities = []

    storage = get_storage_service()
    storage.ensure_user_dirs(user_id)
    fs_dir = get_user_filesystem_dir(user_id)

    backend = create_agent_backend(root_dir=fs_dir, user_id=user_id)
    resolved_model = _resolve_model(model, user_id=user_id)

    tools = [create_run_script_tool(user_id)]

    # read_document only (no view_pdf_page_or_image): batch agent processes
    # row-by-row data and shouldn't be making per-row vision calls — too slow
    # and too expensive. If a single batch row truly needs vision the user
    # can fall back to run_script + pypdfium2.
    tools.append(create_read_document_tool(user_id))

    tools.extend(create_web_tools(user_id=user_id))

    # AI generation tools — filtered by capabilities
    if capabilities:
        ai_tools = create_ai_gen_tools(user_id)
        tool_map = {"image": ai_tools[0], "speech": ai_tools[1], "video": ai_tools[2]}
        for cap in capabilities:
            if cap in tool_map:
                tools.append(tool_map[cap])

    return create_deep_agent(
        model=resolved_model,
        system_prompt=prompt_content,
        backend=backend,
        tools=tools,
        checkpointer=_checkpointer,
        name=f"batch-{user_id}",
    )
