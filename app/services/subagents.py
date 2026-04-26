import os
import json
import uuid
from typing import Optional, Dict, Any, List

from app.core.security import get_user_dir

SUBAGENTS_FILE = "subagents.json"

SHARED_TOOL_NAMES = {
    "run_script", "move_file",
    "web_search", "web_fetch",
    "generate_image", "generate_speech", "generate_video",
    "schedule_task", "manage_scheduled_tasks",
    "publish_service_task", "send_message",
}

MEMORY_TOOL_NAMES = {
    "list_conversations", "read_conversation",
    "list_service_conversations", "read_service_conversation",
    "read_inbox",
    "soul_list", "soul_read", "soul_write", "soul_delete",
}

DEFAULT_SUBAGENTS = [
    {
        "id": "deep-research",
        "name": "deep-research",
        "description": "深度研究助手：适合需要大量搜索、多步分析的复杂问题。它会查阅投研报告、市场数据，综合分析后返回精炼摘要，避免污染主对话上下文。",
        "system_prompt": (
            "你是一个专业的深度研究助手。你的职责是：\n\n"
            "1. 将研究问题拆解为多个搜索查询\n"
            "2. 综合所有信息，输出结构化的研究摘要\n\n"
            "输出格式：\n"
            "- 核心结论（2-3 句话）\n"
            "- 关键发现（要点列表）\n"
            "- 数据支撑（引用数据来源）\n"
            "- 风险提示\n\n"
            "每个结论必须引用来源：(Source: \"报告标题\", 机构, 日期)\n"
            "保持回复在 800 字以内。"
        ),
        "tools": ["run_script", "web_search", "web_fetch"],
        "enabled": True,
        "builtin": True,
    },
    {
        "id": "memory",
        "name": "memory",
        "description": (
            "对话记忆助手：查询历史对话、检索收件箱、跨对话检索、管理 soul 笔记。"
            "当你需要回忆之前和用户/Service 聊了什么、收件箱反馈了什么时，委托给它。"
            "当 memory_subagent_enabled 开启时，它还可以在 soul/ 中创建和管理笔记文件。"
        ),
        "system_prompt": (
            "你是对话记忆助手。你的职责是从历史对话和收件箱中检索信息并提供简洁摘要。\n\n"
            "工作原则：\n"
            "1. 先用 list_conversations 了解有哪些对话，再用 read_conversation 查看具体内容\n"
            "2. 输出简洁的摘要，突出关键信息和时间线\n"
            "3. 标注消息来源（用户/助手/工具调用/管理员指令）\n"
            "4. 如有 Service 对话权限，可用 list_service_conversations / read_service_conversation 查看\n"
            "5. 用 read_inbox 查看收件箱中 Service Agent 的反馈\n"
            "6. 如有 soul 写入工具，可用 soul_write 保存重要笔记、总结到 soul/ 目录\n"
            "   - soul/ 目录是你的「灵魂笔记本」，用来存放长期记忆、洞察、重要决策记录\n"
            "   - 不要修改 config.json\n"
            "   - 可以用子目录组织内容，如 notes/、summaries/、insights/\n\n"
            "保持回复在 500 字以内。如果信息量大，优先提取要点。"
        ),
        "tools": [
            "list_conversations", "read_conversation",
            "list_service_conversations", "read_service_conversation",
            "read_inbox",
            "soul_list", "soul_read", "soul_write", "soul_delete",
        ],
        "enabled": True,
        "builtin": True,
    },
]


def _get_subagents_path(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), SUBAGENTS_FILE)


def list_user_subagents(user_id: str) -> List[Dict[str, Any]]:
    path = _get_subagents_path(user_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return list(DEFAULT_SUBAGENTS)


def save_user_subagents(user_id: str, subagents: List[Dict[str, Any]]):
    from app.services.agent import clear_agent_cache
    from app.core.fileutil import atomic_json_save
    user_dir = get_user_dir(user_id)
    os.makedirs(user_dir, exist_ok=True)
    atomic_json_save(os.path.join(user_dir, SUBAGENTS_FILE), subagents, ensure_ascii=False, indent=2)
    clear_agent_cache(user_id)


def get_user_subagent(user_id: str, subagent_id: str) -> Optional[Dict[str, Any]]:
    for sa in list_user_subagents(user_id):
        if sa.get("id") == subagent_id:
            return sa
    return None


def add_user_subagent(user_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    subagents = list_user_subagents(user_id)
    if not config.get("id"):
        config["id"] = uuid.uuid4().hex[:8]
    config.setdefault("enabled", True)
    config.setdefault("builtin", False)
    subagents.append(config)
    save_user_subagents(user_id, subagents)
    return config


def update_user_subagent(user_id: str, subagent_id: str, updates: Dict[str, Any]) -> bool:
    subagents = list_user_subagents(user_id)
    for sa in subagents:
        if sa.get("id") == subagent_id:
            sa.update(updates)
            sa["id"] = subagent_id
            save_user_subagents(user_id, subagents)
            return True
    return False


def delete_user_subagent(user_id: str, subagent_id: str) -> bool:
    subagents = list_user_subagents(user_id)
    new_list = [sa for sa in subagents if sa.get("id") != subagent_id]
    if len(new_list) == len(subagents):
        return False
    save_user_subagents(user_id, new_list)
    return True


def build_subagent_tools(user_id: str, tool_names: List[str]) -> list:
    from app.services.tools import (
        create_run_script_tool, create_ai_gen_tools, create_web_tools,
        create_schedule_tool, create_manage_scheduled_tasks_tool,
        create_publish_service_task_tool,
        create_send_message_tool, create_move_file_tool,
    )

    tool_map: Dict[str, Any] = {}
    needed = set(tool_names)

    if "run_script" in needed:
        tool_map["run_script"] = create_run_script_tool(user_id)

    if "move_file" in needed:
        tool_map["move_file"] = create_move_file_tool(user_id)

    if needed & {"web_search", "web_fetch"}:
        for t in create_web_tools(user_id=user_id):
            tool_map[t.name] = t

    if needed & {"generate_image", "generate_speech", "generate_video"}:
        ai_tools = create_ai_gen_tools(user_id)
        tool_map["generate_image"] = ai_tools[0]
        tool_map["generate_speech"] = ai_tools[1]
        tool_map["generate_video"] = ai_tools[2]

    if "schedule_task" in needed:
        tool_map["schedule_task"] = create_schedule_tool(user_id)

    if "manage_scheduled_tasks" in needed:
        tool_map["manage_scheduled_tasks"] = create_manage_scheduled_tasks_tool(user_id)

    if "publish_service_task" in needed:
        tool_map["publish_service_task"] = create_publish_service_task_tool(user_id)

    if "send_message" in needed:
        tool_map["send_message"] = create_send_message_tool()

    if needed & MEMORY_TOOL_NAMES:
        from app.services.memory_tools import create_admin_memory_tools
        for t in create_admin_memory_tools(user_id):
            tool_map[t.name] = t

    return [tool_map[name] for name in tool_names if name in tool_map]


def build_subagents_for_agent(user_id: str) -> Optional[List[Dict[str, Any]]]:
    configs = list_user_subagents(user_id)
    enabled = [c for c in configs if c.get("enabled", True)]
    if not enabled:
        return None
    subagents = []
    for cfg in enabled:
        tools = build_subagent_tools(user_id, cfg.get("tools", []))
        sa: Dict[str, Any] = {
            "name": cfg["name"],
            "description": cfg["description"],
            "system_prompt": cfg["system_prompt"],
            "tools": tools,
        }
        if cfg.get("model"):
            sa["model"] = cfg["model"]
        subagents.append(sa)
    return subagents if subagents else None
