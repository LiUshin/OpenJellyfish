"""
Consumer agent factory — creates per-conversation agents for published services.

Key differences from admin agent:
- Filesystem: admin's docs (read-only), conversation-specific generated/ (writable)
- Tools: filtered by service config (allowed_scripts, capabilities)
- No HITL interrupts on writes
- Memory subagent: read-only access to own conversation history only
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Any, Dict

from langchain_core.tools import tool

from app.core.security import get_user_filesystem_dir
from app.core.path_security import safe_join
from app.services.published import (
    get_service, get_consumer_generated_dir,
)


def _build_consumer_system_prompt(
    admin_id: str,
    service_config: Dict[str, Any],
) -> str:
    """Build system prompt for consumer agent from admin's prompt config."""
    from app.services.prompt import (
        get_user_system_prompt, get_prompt_version,
        get_profile_version, build_user_profile_prompt,
    )

    version_id = service_config.get("system_prompt_version_id")
    if version_id:
        version = get_prompt_version(admin_id, version_id)
        if version:
            base_prompt = version["content"]
        else:
            base_prompt = get_user_system_prompt(admin_id)
    else:
        base_prompt = get_user_system_prompt(admin_id)

    profile_version_id = service_config.get("user_profile_version_id")
    if profile_version_id:
        pv = get_profile_version(admin_id, profile_version_id)
        if pv and pv.get("content", "").strip():
            profile_context = (
                "\n## 个性规则\n"
                "以下是当前用户定义的个性化规则。你必须根据这些规则定制你的回复风格、"
                "内容深度、用语习惯，以及生成的语音、文字、视频、图像等所有输出内容。\n\n"
                f"{pv['content']}"
            )
        else:
            profile_context = build_user_profile_prompt(admin_id)
    else:
        profile_context = build_user_profile_prompt(admin_id)

    from app.services.preferences import get_tz_offset
    tz_hours = get_tz_offset(admin_id)
    user_tz = timezone(timedelta(hours=tz_hours))
    user_now = datetime.now(user_tz)
    today_str = user_now.strftime("%Y年%m月%d日")
    base_prompt = base_prompt.replace("{today}", today_str)
    base_prompt = base_prompt.replace("{user_profile_context}", profile_context)

    consumer_notice = (
        "\n\n## 重要约束\n"
        "- /docs/ 目录中的文件是只读的，请勿尝试修改\n"
        "- 你生成的内容（图片、音频、视频等）保存在 /generated/ 目录\n"
    )
    return base_prompt + consumer_notice


def _create_consumer_read_tools(admin_id: str, allowed_docs: List[str]):
    """Create read-only file tools scoped to admin's docs."""
    fs_dir = get_user_filesystem_dir(admin_id)
    docs_dir = os.path.join(fs_dir, "docs")

    def _is_allowed(path: str) -> bool:
        if not allowed_docs or allowed_docs == ["*"]:
            return True
        norm = path.lstrip("/").replace("\\", "/")
        for pattern in allowed_docs:
            if pattern == "*":
                return True
            if norm.startswith(pattern.lstrip("/")):
                return True
        return False

    @tool
    def ls(path: str = "/") -> str:
        """列出目录内容（只读文件系统）。

        Args:
            path: 目录路径，/ 为根目录
        """
        clean = path.lstrip("/").replace("\\", "/")
        if clean.startswith("generated"):
            return "generated/ 目录不可通过此工具浏览"
        try:
            target = safe_join(docs_dir, clean) if clean else docs_dir
        except PermissionError:
            return "路径超出允许范围"
        if not os.path.isdir(target):
            return f"目录不存在: {path}"
        entries = []
        for name in sorted(os.listdir(target)):
            full = os.path.join(target, name)
            rel = os.path.join(clean, name).replace("\\", "/") if clean else name
            if not _is_allowed(rel):
                continue
            suffix = "/" if os.path.isdir(full) else ""
            entries.append(f"{name}{suffix}")
        return "\n".join(entries) if entries else "(空目录)"

    @tool
    def read_file(path: str) -> str:
        """读取文件内容（只读）。

        Args:
            path: 文件路径，如 /welcome.md
        """
        clean = path.lstrip("/").replace("\\", "/")
        if not _is_allowed(clean):
            return "无权限访问该文件"
        try:
            full = safe_join(docs_dir, clean)
        except PermissionError:
            return "路径超出允许范围"
        if not os.path.isfile(full):
            return f"文件不存在: {path}"
        try:
            with open(full, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"读取失败: {e}"

    @tool
    def list_files_sorted(
        path: str = "/",
        order_by: str = "modified",
        desc: bool = True,
        limit: int = 50,
    ) -> str:
        """列出 docs/ 目录文件并按指定字段排序，返回带大小和修改时间的明细。

        Args:
            path: 目录路径，/ 为根目录
            order_by: 排序字段，可选 "name" / "modified" / "size"，默认 "modified"
            desc: 是否倒序（True=新→旧 / 大→小 / Z→A），默认 True
            limit: 最多返回多少条，默认 50，上限 500
        """
        from app.services.tools import (
            _SORT_KEYS, _format_size, _format_mtime_short,
        )
        from datetime import datetime as _dt

        order_by = (order_by or "modified").lower().strip()
        if order_by not in _SORT_KEYS:
            return f"order_by 只能是 name / modified / size，收到: {order_by}"
        limit = max(1, min(int(limit or 50), 500))

        clean = path.lstrip("/").replace("\\", "/")
        if clean.startswith("generated"):
            return "generated/ 目录不可通过此工具浏览"
        try:
            target = safe_join(docs_dir, clean) if clean else docs_dir
        except PermissionError:
            return "路径超出允许范围"
        if not os.path.isdir(target):
            return f"目录不存在: {path}"

        rows = []
        for name in os.listdir(target):
            full = os.path.join(target, name)
            rel = os.path.join(clean, name).replace("\\", "/") if clean else name
            if not _is_allowed(rel):
                continue
            try:
                st = os.stat(full)
                is_dir = os.path.isdir(full)
                rows.append({
                    "name": name,
                    "is_dir": is_dir,
                    "size": 0 if is_dir else st.st_size,
                    "modified_at": _dt.fromtimestamp(st.st_mtime).isoformat(),
                })
            except OSError:
                continue
        if not rows:
            return f"(空目录或全部被权限过滤: {path})"

        key_map = {
            "name": lambda r: r["name"].lower(),
            "modified": lambda r: r["modified_at"],
            "mtime": lambda r: r["modified_at"],
            "size": lambda r: r["size"],
        }
        rows.sort(key=key_map[order_by], reverse=bool(desc))
        truncated = len(rows) > limit
        rows = rows[:limit]

        lines = [f"{'类型':4} {'大小':>8}  {'修改时间':16}  名称"]
        for r in rows:
            kind = "DIR " if r["is_dir"] else "FILE"
            size = "-" if r["is_dir"] else _format_size(r["size"])
            mt = _format_mtime_short(r["modified_at"])
            lines.append(f"{kind:4} {size:>8}  {mt:16}  {r['name']}{'/' if r['is_dir'] else ''}")
        if truncated:
            lines.append(f"... (仅显示前 {limit} 条)")
        return "\n".join(lines)

    return [ls, read_file, list_files_sorted]


def _create_consumer_gen_tools(admin_id: str, service_id: str, conv_id: str, capabilities: List[str]):
    """Create AI generation tools that write to the conversation's generated/ dir."""
    from app.storage import get_storage_service

    gen_dir = get_consumer_generated_dir(admin_id, service_id, conv_id)
    conv_dir = os.path.dirname(gen_dir)

    def _consumer_write(rel_path: str, data: bytes):
        """Write generated content to consumer conversation directory."""
        clean = rel_path.lstrip("/").replace("\\", "/")
        if clean.startswith("generated/"):
            clean = clean[len("generated/"):]
        get_storage_service().write_consumer_bytes(admin_id, service_id, conv_id, clean, data)

    tools = []

    if "image" in capabilities:
        from app.services.ai_tools import generate_image as _gen_image_impl

        @tool
        def generate_image(
            prompt: str,
            size: str = "1024x1024",
            quality: str = "auto",
            filename: Optional[str] = None,
        ) -> str:
            """使用 AI 生成图片。

            Args:
                prompt: 图片描述
                size: 尺寸 1024x1024/1536x1024/1024x1536/auto
                quality: 质量 low/medium/high/auto
                filename: 自定义文件名
            """
            result = _gen_image_impl(prompt, conv_dir, size=size, quality=quality,
                                     filename=filename, user_id=admin_id,
                                     write_func=_consumer_write)
            if result["success"]:
                return f"图片已生成：{result['path']}"
            return result["message"]

        tools.append(generate_image)

    if "speech" in capabilities:
        from app.services.ai_tools import generate_speech as _gen_speech_impl

        @tool
        def generate_speech(
            text: str,
            voice: str = "alloy",
            speed: float = 1.0,
            filename: Optional[str] = None,
        ) -> str:
            """将文本转换为语音。

            Args:
                text: 文本内容（最大 4096 字符）
                voice: 声音 alloy/echo/fable/onyx/nova/shimmer
                speed: 语速 0.25-4.0
                filename: 自定义文件名
            """
            result = _gen_speech_impl(text, conv_dir, voice=voice, speed=speed, filename=filename,
                                      user_id=admin_id, write_func=_consumer_write)
            if result["success"]:
                return f"语音已生成：{result['path']}"
            return result["message"]

        tools.append(generate_speech)

    if "video" in capabilities:
        from app.services.ai_tools import generate_video as _gen_video_impl

        @tool
        def generate_video(
            prompt: str,
            seconds: int = 4,
            size: str = "1280x720",
            filename: Optional[str] = None,
        ) -> str:
            """使用 AI 生成视频。

            Args:
                prompt: 视频描述
                seconds: 时长 4/8/12 秒
                size: 分辨率 1280x720/720x1280
                filename: 自定义文件名
            """
            result = _gen_video_impl(prompt, conv_dir, seconds=seconds, size=size, filename=filename,
                                     user_id=admin_id, write_func=_consumer_write)
            if result["success"]:
                return f"视频已生成：{result['path']}"
            return result["message"]

        tools.append(generate_video)

    return tools


def _create_consumer_script_tools(
    admin_id: str,
    service_id: str,
    conv_id: str,
    allowed_scripts: List[str],
):
    """Create script execution tools filtered by allowed_scripts."""
    if not allowed_scripts:
        return []

    from app.services.script_runner import run_script as _run_script_impl
    from app.storage import get_storage_service

    def _script_allowed(script_path: str) -> bool:
        norm = script_path.replace("\\", "/").lstrip("/")
        for pattern in allowed_scripts:
            if pattern == "*":
                return True
            if norm == pattern.lstrip("/") or norm.startswith(pattern.rstrip("/") + "/"):
                return True
        return False

    @tool
    def run_script(
        script_path: str,
        script_args: Optional[List[str]] = None,
        input_data: Optional[str] = None,
        timeout: int = 30,
    ) -> str:
        """执行预配置的 Python 脚本。

        Args:
            script_path: 脚本路径
            script_args: 命令行参数
            input_data: stdin 输入
            timeout: 超时秒数
        """
        if not _script_allowed(script_path):
            return f"无权执行此脚本: {script_path}"

        from app.services.venv_manager import get_user_python
        storage = get_storage_service()
        with storage.consumer_script_execution(admin_id, service_id, conv_id, script_path) as ctx:
            if "error" in ctx:
                return f"执行失败: {ctx['error']}"
            result = _run_script_impl(
                script_path=script_path,
                scripts_dir=ctx["scripts_dir"],
                input_data=input_data,
                args=script_args,
                timeout=timeout,
                allowed_read_dirs=[ctx["docs_dir"]],
                allowed_write_dirs=ctx["write_dirs"],
                python_executable=get_user_python(admin_id),
            )
        if result["error"]:
            return f"执行失败: {result['error']}"
        parts = []
        if result["stdout"]:
            parts.append(f"输出:\n{result['stdout']}")
        if result["stderr"]:
            parts.append(f"错误输出:\n{result['stderr']}")
        parts.append(f"退出码: {result['exit_code']}")
        return "\n".join(parts) if parts else "脚本执行完成（无输出）"

    return [run_script]


_consumer_agent_cache: Dict[str, Any] = {}


def create_consumer_agent(
    admin_id: str,
    service_id: str,
    conv_id: str,
    wechat_session_id: Optional[str] = None,
    extra_capabilities: Optional[List[str]] = None,
    channel: str = "web",
) -> Any:
    """Create (or return cached) agent for a consumer conversation.

    extra_capabilities: additional capabilities to inject (e.g. ["humanchat"]
    for scheduled tasks that need send_message).
    channel: invocation context. One of:
        - "web"       — consumer 直连 SSE（/api/v1/chat），agent 输出直接流给浏览器，
                        send_message 工具在该上下文下**不**注入（即便 humanchat capability
                        启用），避免 ghost 调用与无意义的工具事件泄露给消费者。
        - "wechat"    — 通过 iLink 反向投递到微信用户，需要 send_message。
        - "scheduler" — 定时任务推送，也需要 send_message。
    """
    from langchain.chat_models import init_chat_model
    from deepagents import create_deep_agent
    from app.services.agent import _resolve_model, _checkpointer
    from app.storage import create_consumer_backend

    svc_config = get_service(admin_id, service_id)
    if not svc_config:
        raise ValueError(f"Service {service_id} not found")

    extra_suffix = f"::+{','.join(sorted(extra_capabilities))}" if extra_capabilities else ""
    ws_suffix = f"::{wechat_session_id}" if wechat_session_id else ""
    ch_suffix = f"::ch={channel}" if channel and channel != "web" else ""
    cache_key = f"consumer::{admin_id}::{service_id}::{conv_id}{ws_suffix}{extra_suffix}{ch_suffix}"
    if cache_key in _consumer_agent_cache:
        return _consumer_agent_cache[cache_key]

    gen_dir = get_consumer_generated_dir(admin_id, service_id, conv_id)
    os.makedirs(gen_dir, exist_ok=True)

    backend = create_consumer_backend(admin_id, service_id, conv_id, gen_dir)

    system_prompt = _build_consumer_system_prompt(admin_id, svc_config)

    capabilities = list(svc_config.get("capabilities", []))
    if extra_capabilities:
        for cap in extra_capabilities:
            if cap not in capabilities:
                capabilities.append(cap)
    allowed_docs = svc_config.get("allowed_docs", ["*"])
    allowed_scripts = svc_config.get("allowed_scripts", ["*"])
    research_tools = svc_config.get("research_tools", False)

    tools = []
    tools.extend(_create_consumer_read_tools(admin_id, allowed_docs))
    tools.extend(_create_consumer_gen_tools(admin_id, service_id, conv_id, capabilities))
    tools.extend(_create_consumer_script_tools(admin_id, service_id, conv_id, allowed_scripts))

    if research_tools or "web" in capabilities:
        from app.services.tools import create_web_tools, CAPABILITY_PROMPTS as _CP
        tools.extend(create_web_tools(user_id=admin_id))
        system_prompt += "\n" + _CP["web"]

    if "scheduler" in capabilities:
        from app.services.tools import (
            create_service_schedule_tool, create_service_manage_tasks_tool,
            CAPABILITY_PROMPTS as _CP2,
        )
        tools.append(create_service_schedule_tool(
            admin_id, service_id, conv_id,
            wechat_session_id=wechat_session_id,
        ))
        tools.append(create_service_manage_tasks_tool(
            admin_id, service_id, conv_id,
        ))
        system_prompt += "\n" + _CP2["service_scheduler"]

    # send_message 仅对反向投递渠道（wechat / scheduler）有意义；
    # web 直连 SSE 时 agent 的 token 已经流给浏览器，再调 send_message 既无投递目标
    # 也会产生让消费者困惑的工具事件。
    if "humanchat" in capabilities and channel != "web":
        from app.services.tools import create_send_message_tool, CAPABILITY_PROMPTS as _CP3
        system_prompt += "\n" + _CP3["humanchat"]
        tools.append(create_send_message_tool())

    # contact_admin is always available for consumer agents
    from app.services.tools import create_contact_admin_tool, CAPABILITY_PROMPTS as _CP4
    tools.append(create_contact_admin_tool(
        admin_id, service_id, conv_id,
        wechat_session_id=wechat_session_id,
    ))
    system_prompt += "\n" + _CP4["contact_admin"]

    # Memory subagent — consumer can only read its own conversation
    from app.services.memory_tools import create_consumer_memory_tools
    consumer_memory_tools = create_consumer_memory_tools(admin_id, service_id, conv_id)
    consumer_subagents = [
        {
            "name": "memory",
            "description": (
                "对话记忆助手：查询当前对话的历史消息。"
                "当你需要回忆之前和用户聊了什么时，委托给它。"
            ),
            "system_prompt": (
                "你是对话记忆助手。你的职责是从当前对话历史中检索信息并提供简洁摘要。\n\n"
                "工作原则：\n"
                "1. 用 read_my_conversation 查看历史消息\n"
                "2. 输出简洁的摘要，突出关键信息和时间线\n"
                "3. 标注消息来源（用户/助手/工具调用/管理员指令）\n\n"
                "保持回复在 500 字以内。"
            ),
            "tools": consumer_memory_tools,
        },
    ]

    model_id = svc_config.get("model", "anthropic:claude-sonnet-4-5-20250929")
    resolved_model = _resolve_model(model_id, user_id=admin_id)

    agent = create_deep_agent(
        model=resolved_model,
        system_prompt=system_prompt,
        backend=backend,
        tools=tools,
        subagents=consumer_subagents,
        checkpointer=_checkpointer,
        name=f"svc-{service_id}-{conv_id}",
    )

    _consumer_agent_cache[cache_key] = agent
    return agent


def clear_consumer_cache(admin_id: str = None, service_id: str = None):
    prefix = "consumer::"
    if admin_id:
        prefix += f"{admin_id}::"
        if service_id:
            prefix += f"{service_id}::"
    keys = [k for k in _consumer_agent_cache if k.startswith(prefix)]
    for k in keys:
        del _consumer_agent_cache[k]
