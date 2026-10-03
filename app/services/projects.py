"""User-owned groups for admin conversations and their Markdown briefs."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.fileutil import atomic_json_save
from app.core.jsonl_store import safe_load_json
from app.core.path_security import safe_join
from app.core.security import get_user_dir
from app.services.conversations import (
    create_conversation,
    get_conversation,
    list_conversations,
    set_conversation_project,
)


_ID_PATTERN = re.compile(r"^[a-f0-9]{12,32}$")
_project_lock = threading.RLock()
MAX_BRIEF_BYTES = 1024 * 1024


def _validate_id(project_id: str) -> None:
    if not isinstance(project_id, str) or not _ID_PATTERN.fullmatch(project_id):
        raise ValueError("无效的项目 ID")


def _root(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), "projects")


def _project_dir(user_id: str, project_id: str) -> str:
    _validate_id(project_id)
    return safe_join(_root(user_id), project_id)


def _meta_path(user_id: str, project_id: str) -> str:
    return os.path.join(_project_dir(user_id, project_id), "meta.json")


def _brief_path(user_id: str, project_id: str) -> str:
    return os.path.join(_project_dir(user_id, project_id), "brief.md")


def _name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("项目名称不能为空")
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("项目名称不能为空")
    if len(cleaned) > 120:
        raise ValueError("项目名称最多 120 个字符")
    return cleaned


def list_projects(user_id: str) -> List[Dict[str, Any]]:
    root = _root(user_id)
    if not os.path.isdir(root):
        return []
    counts: Dict[str, int] = {}
    for conv in list_conversations(user_id):
        pid = conv.get("project_id")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    projects = []
    for entry in os.listdir(root):
        if not _ID_PATTERN.fullmatch(entry):
            continue
        meta = get_project(user_id, entry)
        if meta:
            projects.append({**meta, "conversation_count": counts.get(entry, 0)})
    projects.sort(key=lambda value: value.get("updated_at", ""), reverse=True)
    return projects


def create_project(user_id: str, name: str) -> Dict[str, Any]:
    cleaned = _name(name)
    with _project_lock:
        project_id = uuid.uuid4().hex[:16]
        while os.path.exists(_project_dir(user_id, project_id)):
            project_id = uuid.uuid4().hex[:16]
        path = _project_dir(user_id, project_id)
        os.makedirs(path)
        now = datetime.now().isoformat()
        meta = {"id": project_id, "name": cleaned,
                "created_at": now, "updated_at": now}
        atomic_json_save(_meta_path(user_id, project_id), meta,
                         ensure_ascii=False, indent=2)
        _atomic_write_text(_brief_path(user_id, project_id), "")
        return meta


def get_project(user_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    path = _meta_path(user_id, project_id)
    meta = safe_load_json(path)
    if not isinstance(meta, dict) or meta.get("id") != project_id:
        return None
    return meta


def update_project(user_id: str, project_id: str, name: str) -> Optional[Dict[str, Any]]:
    cleaned = _name(name)
    with _project_lock:
        meta = get_project(user_id, project_id)
        if meta is None:
            return None
        meta["name"] = cleaned
        meta["updated_at"] = datetime.now().isoformat()
        atomic_json_save(_meta_path(user_id, project_id), meta,
                         ensure_ascii=False, indent=2)
        return meta


def read_project_brief(user_id: str, project_id: str) -> str:
    if get_project(user_id, project_id) is None:
        raise FileNotFoundError("项目不存在")
    try:
        with open(_brief_path(user_id, project_id), "r", encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return ""


def _atomic_write_text(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_project_brief(user_id: str, project_id: str, content: str) -> str:
    if not isinstance(content, str):
        raise ValueError("项目摘要必须是文本")
    if len(content.encode("utf-8")) > MAX_BRIEF_BYTES:
        raise ValueError("项目摘要最多 1 MiB")
    with _project_lock:
        meta = get_project(user_id, project_id)
        if meta is None:
            raise FileNotFoundError("项目不存在")
        _atomic_write_text(_brief_path(user_id, project_id), content)
        meta["updated_at"] = datetime.now().isoformat()
        atomic_json_save(_meta_path(user_id, project_id), meta,
                         ensure_ascii=False, indent=2)
    return content


def assign_conversation_project(user_id: str, conv_id: str,
                                project_id: Optional[str]) -> Optional[Dict[str, Any]]:
    with _project_lock:
        if project_id is not None and get_project(user_id, project_id) is None:
            raise FileNotFoundError("项目不存在")
        return set_conversation_project(user_id, conv_id, project_id)


def create_project_conversation(user_id: str, title: str,
                                project_id: str) -> Dict[str, Any]:
    """Create while holding the same lock used by project deletion."""
    with _project_lock:
        if get_project(user_id, project_id) is None:
            raise FileNotFoundError("项目不存在")
        return create_conversation(user_id, title, project_id)


def delete_project(user_id: str, project_id: str) -> bool:
    with _project_lock:
        if get_project(user_id, project_id) is None:
            return False
        for conv in list_conversations(user_id):
            if conv.get("project_id") == project_id:
                set_conversation_project(user_id, conv["id"], None)
        shutil.rmtree(_project_dir(user_id, project_id))
        return True


def _visible_text(content: Any) -> str:
    """Only top-level user-visible text; never inspect tool or image payloads."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )
    return ""


def _visible_assistant_blocks(blocks: Any) -> List[str]:
    """Text cards rendered in assistant replies, excluding process details."""
    if not isinstance(blocks, list):
        return []
    return [block["content"] for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
            and isinstance(block.get("content"), str)]


def _excerpt(content: str, at: int, width: int = 180) -> str:
    start = max(0, at - width // 3)
    end = min(len(content), start + width)
    result = re.sub(r"\s+", " ", content[start:end]).strip()
    return ("…" if start else "") + result + ("…" if end < len(content) else "")


def search_project(user_id: str, project_id: str, query: str,
                   limit: int = 30) -> Dict[str, Any]:
    """Scan one project's visible admin chat text, returning one hit per chat."""
    if get_project(user_id, project_id) is None:
        raise FileNotFoundError("项目不存在")
    needle = query.strip().casefold()
    if not needle:
        return {"results": [], "total": 0}
    if len(needle) > 200:
        raise ValueError("搜索词最多 200 个字符")
    limit = min(max(int(limit), 1), 50)
    results: List[Dict[str, Any]] = []
    total = 0
    for summary in list_conversations(user_id):
        if summary.get("project_id") != project_id:
            continue
        conv_id = summary["id"]
        title = summary.get("title") or "新对话"
        title_match = title.casefold().find(needle)
        hit: Optional[Dict[str, Any]] = None
        if title_match >= 0:
            hit = {"conversation_id": conv_id, "title": title,
                   "snippet": _excerpt(title, title_match), "role": "title",
                   "message_index": None}
        else:
            conv = get_conversation(user_id, conv_id)
            for index, message in enumerate((conv or {}).get("messages", [])):
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role not in ("user", "assistant"):
                    continue
                candidates = [_visible_text(message.get("content"))]
                if role == "assistant":
                    candidates.extend(_visible_assistant_blocks(message.get("blocks")))
                for content in candidates:
                    match_at = content.casefold().find(needle)
                    if match_at >= 0:
                        hit = {"conversation_id": conv_id, "title": title,
                               "snippet": _excerpt(content, match_at),
                               "role": role, "message_index": index}
                        break
                if hit is not None:
                    break
        if hit is not None:
            total += 1
            if len(results) < limit:
                results.append(hit)
    return {"results": results, "total": total}
