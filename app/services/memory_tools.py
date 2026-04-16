"""
Memory tools — tools for querying conversation history, inbox, and soul management.

Admin tools: list/read admin conversations, list/read service conversations, read inbox.
Consumer tools: read own conversation only.
Soul write tools: write notes/files to soul/ directory (when memory_subagent_enabled).

Memory subagent can read conversations (always read-only on chat history),
but can write new files within soul/ when memory_subagent_enabled is true.
"""

import os
import sys
import json
import logging
import subprocess
from typing import Optional, List, Dict, Any

from langchain_core.tools import tool

from app.core.security import USERS_DIR, get_user_dir, get_user_conversations_dir

log = logging.getLogger("memory_tools")


# ── Soul config helpers ──────────────────────────────────────────────

DEFAULT_SOUL_CONFIG = {
    "memory_enabled": True,
    "include_consumer_conversations": False,
    "max_recent_messages": 5,
    "memory_subagent_enabled": False,
    "soul_edit_enabled": False,
}


def _soul_dir(user_id: str) -> str:
    """Soul config directory (config.json lives here, NOT agent-accessible)."""
    return os.path.join(get_user_dir(user_id), "soul")


def _soul_content_dir(user_id: str) -> str:
    """Soul content directory (inside filesystem/, agent-accessible)."""
    from app.core.security import get_user_filesystem_dir
    return os.path.join(get_user_filesystem_dir(user_id), "soul")


def get_soul_config(user_id: str) -> Dict[str, Any]:
    path = os.path.join(_soul_dir(user_id), "config.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            return {**DEFAULT_SOUL_CONFIG, **saved}
        except Exception:
            pass
    return dict(DEFAULT_SOUL_CONFIG)


def save_soul_config(user_id: str, config: Dict[str, Any]):
    d = _soul_dir(user_id)
    os.makedirs(d, exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(os.path.join(d, "config.json"), config, ensure_ascii=False, indent=2)


def ensure_soul_dir(user_id: str):
    d = _soul_dir(user_id)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        save_soul_config(user_id, dict(DEFAULT_SOUL_CONFIG))


def _is_junction(path: str) -> bool:
    """Check if a path is a Windows junction point."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
        return attrs != -1 and bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        return False


def _create_link(target: str, link_path: str):
    """Create a symlink (Unix) or junction (Windows) from link_path -> target."""
    if sys.platform == "win32":
        abs_target = os.path.abspath(target)
        subprocess.check_call(
            ["cmd", "/c", "mklink", "/J", link_path, abs_target],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        os.symlink(os.path.relpath(target, os.path.dirname(link_path)), link_path)


def _remove_link(link_path: str):
    """Remove a symlink or junction."""
    if sys.platform == "win32" and _is_junction(link_path):
        subprocess.check_call(
            ["cmd", "/c", "rmdir", link_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        os.remove(link_path)


def _is_link(path: str) -> bool:
    """Check if path is a symlink or junction."""
    return os.path.islink(path) or _is_junction(path)


def sync_soul_symlink(user_id: str):
    """Ensure soul content directory exists inside filesystem/.

    Soul content (notes, files) lives at filesystem/soul/ so the agent's
    filesystem middleware can access it without path-escape issues.
    Config stays at users/{uid}/soul/config.json (not agent-accessible).

    Also migrates legacy data: if the old soul/ directory has content files
    (not just config.json), move them into filesystem/soul/.
    If a legacy symlink/junction exists at filesystem/soul, remove it first.
    """
    from app.core.security import get_user_filesystem_dir
    import shutil

    fs_dir = get_user_filesystem_dir(user_id)
    content_dir = _soul_content_dir(user_id)
    old_soul = _soul_dir(user_id)

    # Remove legacy symlink/junction if present
    link_path = os.path.join(fs_dir, "soul")
    if _is_link(link_path):
        _remove_link(link_path)
        log.info("Removed legacy soul symlink for user %s", user_id)

    config = get_soul_config(user_id)
    enabled = config.get("soul_edit_enabled", False)

    if enabled:
        os.makedirs(content_dir, exist_ok=True)
        # Migrate content from old soul/ dir into filesystem/soul/
        if os.path.isdir(old_soul):
            for name in os.listdir(old_soul):
                if name == "config.json":
                    continue
                src = os.path.join(old_soul, name)
                dst = os.path.join(content_dir, name)
                if not os.path.exists(dst):
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                    log.info("Migrated soul content %s for user %s", name, user_id)


# ── Formatting helpers ───────────────────────────────────────────────

def _format_messages(messages: List[Dict], last_n: int = 20) -> str:
    """Format conversation messages into a readable summary."""
    recent = messages[-last_n:] if len(messages) > last_n else messages
    if not recent:
        return "(无消息)"
    lines = []
    for m in recent:
        role = m.get("role", "unknown")
        ts = m.get("timestamp", "")[:16].replace("T", " ")
        content = m.get("content", "")
        if isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p["text"])
                elif isinstance(p, str):
                    parts.append(p)
            content = " ".join(parts)
        if len(content) > 300:
            content = content[:300] + "…"
        role_label = {"user": "用户", "assistant": "助手"}.get(role, role)
        source = m.get("source", "")
        source_tag = f" ({source})" if source else ""
        lines.append(f"[{ts} {role_label}{source_tag}] {content}")
    return "\n".join(lines)


# ── Short-term memory injection (used by scheduler/inbox) ────────────

def load_recent_admin_messages(user_id: str, conv_id: str,
                               max_n: Optional[int] = None) -> str:
    """Load recent messages from an admin conversation for context injection."""
    if not max_n:
        max_n = get_soul_config(user_id).get("max_recent_messages", 10)
    from app.services.conversations import get_conversation
    conv = get_conversation(user_id, conv_id)
    if not conv or not conv.get("messages"):
        return ""
    return _format_messages(conv["messages"], last_n=max_n)


def load_recent_consumer_messages(admin_id: str, service_id: str,
                                  conv_id: str,
                                  max_n: Optional[int] = None) -> str:
    """Load recent messages from a consumer conversation for context injection."""
    if not max_n:
        max_n = get_soul_config(admin_id).get("max_recent_messages", 5)
    from app.services.published import get_consumer_conversation
    conv = get_consumer_conversation(admin_id, service_id, conv_id)
    if not conv or not conv.get("messages"):
        return ""
    return _format_messages(conv["messages"], last_n=max_n)


def load_recent_inbox(admin_id: str, last_n: int = 3) -> str:
    """Load recent inbox messages for context injection."""
    from app.services.inbox import list_inbox
    msgs = list_inbox(admin_id)[:last_n]
    if not msgs:
        return ""
    lines = []
    for m in msgs:
        ts = m.get("timestamp", "")[:16].replace("T", " ")
        svc = m.get("service_name", "")
        status = m.get("status", "")
        content = m.get("message", "")
        if len(content) > 200:
            content = content[:200] + "…"
        lines.append(f"[{ts} Service:{svc} 状态:{status}] {content}")
    return "\n".join(lines)


# ── Admin memory tools (factory) ─────────────────────────────────────

def create_admin_memory_tools(user_id: str) -> List:
    """Create memory tools for the admin agent's memory subagent."""

    config = get_soul_config(user_id)

    @tool
    def list_conversations(keyword: Optional[str] = None) -> str:
        """列出管理员的对话历史摘要。

        Args:
            keyword: 可选关键词，按标题过滤
        """
        from app.services.conversations import list_conversations as _list_conv
        convs = _list_conv(user_id)
        if keyword:
            convs = [c for c in convs if keyword.lower() in c.get("title", "").lower()]
        if not convs:
            return "没有找到匹配的对话。"
        lines = []
        for c in convs[:20]:
            lines.append(
                f"- [{c['id']}] {c.get('title', '无标题')} "
                f"({c.get('message_count', 0)} 条消息, "
                f"更新于 {c.get('updated_at', '')[:16]})"
            )
        return "\n".join(lines)

    @tool
    def read_conversation(conv_id: str, last_n: int = 20) -> str:
        """读取管理员对话的最近 N 条消息。

        Args:
            conv_id: 对话 ID
            last_n: 最近消息数量，默认 20
        """
        from app.services.conversations import get_conversation as _get_conv
        conv = _get_conv(user_id, conv_id)
        if not conv:
            return f"对话 {conv_id} 不存在。"
        title = conv.get("title", "")
        msgs = conv.get("messages", [])
        header = f"对话「{title}」({len(msgs)} 条消息)：\n"
        return header + _format_messages(msgs, last_n=last_n)

    @tool
    def list_service_conversations(service_id: Optional[str] = None) -> str:
        """列出 Service 的消费者对话列表。

        Args:
            service_id: 指定 Service ID。不填则列出所有 Service 的对话。
        """
        if not config.get("include_consumer_conversations", False):
            return "管理员未开启 Service 对话记忆。请在 Soul 设置中启用。"
        from app.services.published import list_services
        services = list_services(user_id)
        if service_id:
            services = [s for s in services if s["id"] == service_id]
        if not services:
            return "没有找到 Service。"

        results = []
        for svc in services:
            svc_id = svc["id"]
            svc_name = svc.get("name", svc_id)
            conv_base = os.path.join(get_user_dir(user_id), "services", svc_id, "conversations")
            if not os.path.isdir(conv_base):
                continue
            convs = []
            for cdir in os.listdir(conv_base):
                msg_file = os.path.join(conv_base, cdir, "messages.json")
                if os.path.isfile(msg_file):
                    try:
                        with open(msg_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                        convs.append({
                            "id": meta.get("id", cdir),
                            "title": meta.get("title", ""),
                            "msg_count": len(meta.get("messages", [])),
                            "updated": meta.get("updated_at", "")[:16],
                        })
                    except Exception:
                        continue
            if convs:
                convs.sort(key=lambda x: x.get("updated", ""), reverse=True)
                results.append(f"Service「{svc_name}」({svc_id})：")
                for c in convs[:10]:
                    results.append(
                        f"  - [{c['id']}] {c.get('title') or '无标题'} "
                        f"({c['msg_count']} 条, {c['updated']})"
                    )
        return "\n".join(results) if results else "没有找到消费者对话。"

    @tool
    def read_service_conversation(service_id: str, conv_id: str,
                                  last_n: int = 20) -> str:
        """读取消费者对话的最近 N 条消息。

        Args:
            service_id: Service ID
            conv_id: 对话 ID
            last_n: 最近消息数量，默认 20
        """
        if not config.get("include_consumer_conversations", False):
            return "管理员未开启 Service 对话记忆。请在 Soul 设置中启用。"
        from app.services.published import get_consumer_conversation
        conv = get_consumer_conversation(user_id, service_id, conv_id)
        if not conv:
            return f"Service {service_id} 中的对话 {conv_id} 不存在。"
        title = conv.get("title", "")
        msgs = conv.get("messages", [])
        header = f"Service 对话「{title}」({len(msgs)} 条消息)：\n"
        return header + _format_messages(msgs, last_n=last_n)

    @tool
    def read_inbox(last_n: int = 10, status: Optional[str] = None) -> str:
        """读取收件箱消息。

        Args:
            last_n: 最近消息数量，默认 10
            status: 按状态过滤（unread/handled/read），不填则全部
        """
        from app.services.inbox import list_inbox as _list_inbox
        msgs = _list_inbox(user_id, status=status)[:last_n]
        if not msgs:
            return "收件箱为空。" if not status else f"没有状态为「{status}」的消息。"
        lines = []
        for m in msgs:
            ts = m.get("timestamp", "")[:16].replace("T", " ")
            svc = m.get("service_name", "?")
            st = m.get("status", "?")
            content = m.get("message", "")
            if len(content) > 200:
                content = content[:200] + "…"
            resp = m.get("agent_response", "")
            resp_line = f"\n    → Agent 回复: {resp[:100]}" if resp else ""
            lines.append(f"[{ts}] Service「{svc}」({st}): {content}{resp_line}")
        return "\n".join(lines)

    all_tools = [list_conversations, read_conversation,
                  list_service_conversations, read_service_conversation,
                  read_inbox]

    if config.get("memory_subagent_enabled", False):
        soul_root = _soul_content_dir(user_id)

        @tool
        def soul_list(path: str = "") -> str:
            """列出 soul/ 目录下的文件和子目录。

            Args:
                path: 相对于 soul/ 的子路径，空字符串表示 soul 根目录
            """
            from app.core.path_security import safe_join
            target = safe_join(soul_root, path) if path else soul_root
            if not os.path.isdir(target):
                return f"路径不存在: {path}"
            entries = []
            for name in sorted(os.listdir(target)):
                if name == "config.json":
                    continue
                full = os.path.join(target, name)
                kind = "📁" if os.path.isdir(full) else "📄"
                entries.append(f"{kind} {name}")
            return "\n".join(entries) if entries else "(空目录)"

        @tool
        def soul_read(path: str) -> str:
            """读取 soul/ 目录下的一个文件内容。

            Args:
                path: 相对于 soul/ 的文件路径
            """
            from app.core.path_security import safe_join
            target = safe_join(soul_root, path)
            if not os.path.isfile(target):
                return f"文件不存在: {path}"
            try:
                with open(target, "r", encoding="utf-8") as f:
                    return f.read()
            except UnicodeDecodeError:
                return f"无法读取二进制文件: {path}"

        @tool
        def soul_write(path: str, content: str) -> str:
            """在 soul/ 目录下写入或覆盖一个文件。不可修改 config.json。

            Args:
                path: 相对于 soul/ 的文件路径
                content: 文件内容
            """
            from app.core.path_security import safe_join
            if path.strip("/\\") == "config.json":
                return "错误：不允许直接修改 config.json，请使用设置页面。"
            target = safe_join(soul_root, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return f"已写入 soul/{path}"

        @tool
        def soul_delete(path: str) -> str:
            """删除 soul/ 目录下的一个文件。不可删除 config.json。

            Args:
                path: 相对于 soul/ 的文件路径
            """
            import shutil
            from app.core.path_security import safe_join
            if path.strip("/\\") == "config.json":
                return "错误：不允许删除 config.json。"
            target = safe_join(soul_root, path)
            if os.path.isfile(target):
                os.remove(target)
                return f"已删除 soul/{path}"
            elif os.path.isdir(target):
                shutil.rmtree(target)
                return f"已删除目录 soul/{path}"
            return f"路径不存在: {path}"

        all_tools.extend([soul_list, soul_read, soul_write, soul_delete])

    return all_tools


# ── Consumer memory tools (factory) ──────────────────────────────────

def create_consumer_memory_tools(admin_id: str, service_id: str,
                                 conv_id: str) -> List:
    """Create memory tools for a consumer agent's memory subagent.

    The consumer can only read its own conversation history.
    """

    @tool
    def read_my_conversation(last_n: int = 20) -> str:
        """读取当前对话的历史消息。

        Args:
            last_n: 最近消息数量，默认 20
        """
        from app.services.published import get_consumer_conversation
        conv = get_consumer_conversation(admin_id, service_id, conv_id)
        if not conv:
            return "当前对话暂无历史消息。"
        msgs = conv.get("messages", [])
        if not msgs:
            return "当前对话暂无历史消息。"
        return _format_messages(msgs, last_n=last_n)

    return [read_my_conversation]
