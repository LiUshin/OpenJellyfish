import os
import json
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

from app.core.security import get_user_conversations_dir
from app.core.path_security import safe_join


def list_conversations(user_id: str) -> List[Dict[str, Any]]:
    conv_dir = get_user_conversations_dir(user_id)
    os.makedirs(conv_dir, exist_ok=True)
    conversations = []
    for filename in os.listdir(conv_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(conv_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    conv = json.load(f)
                conversations.append({
                    "id": conv.get("id", filename.replace(".json", "")),
                    "title": conv.get("title", "新对话"),
                    "created_at": conv.get("created_at", ""),
                    "updated_at": conv.get("updated_at", ""),
                    "message_count": len(conv.get("messages", [])),
                })
            except (json.JSONDecodeError, OSError):
                continue
    conversations.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return conversations


def create_conversation(user_id: str, title: str = "新对话") -> Dict[str, Any]:
    conv_dir = get_user_conversations_dir(user_id)
    os.makedirs(conv_dir, exist_ok=True)
    conv_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conv = {"id": conv_id, "title": title, "created_at": now, "updated_at": now, "messages": []}
    from app.core.fileutil import atomic_json_save
    atomic_json_save(os.path.join(conv_dir, f"{conv_id}.json"), conv, ensure_ascii=False, indent=2)
    return conv


def get_conversation(user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    filepath = os.path.join(get_user_conversations_dir(user_id), f"{conv_id}.json")
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_conversation(user_id: str, conv_id: str) -> bool:
    filepath = os.path.join(get_user_conversations_dir(user_id), f"{conv_id}.json")
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False


def save_message(user_id: str, conv_id: str, role: str, content: str,
                 tool_calls: list = None, attachments: list = None,
                 blocks: list = None):
    filepath = os.path.join(get_user_conversations_dir(user_id), f"{conv_id}.json")
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            conv = json.load(f)
    else:
        conv = {"id": conv_id, "title": "新对话", "created_at": datetime.now().isoformat(), "messages": []}
    msg: Dict[str, Any] = {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if attachments:
        msg["attachments"] = attachments
    if blocks:
        msg["blocks"] = blocks
    conv["messages"].append(msg)
    conv["updated_at"] = datetime.now().isoformat()
    if conv["title"] == "新对话" and role == "user":
        conv["title"] = content[:30] + ("..." if len(content) > 30 else "")
    from app.core.fileutil import atomic_json_save
    atomic_json_save(filepath, conv, ensure_ascii=False, indent=2)


def get_attachment_dir(user_id: str, conv_id: str) -> str:
    return os.path.join(get_user_conversations_dir(user_id), conv_id, "query_appendix")


def save_attachment(user_id: str, conv_id: str, rel_path: str, data: bytes) -> str:
    """Save an attachment file and return the relative path."""
    att_dir = get_attachment_dir(user_id, conv_id)
    clean = rel_path.lstrip("/").replace("\\", "/")
    full = os.path.join(att_dir, clean)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)
    return clean


def get_attachment_path(user_id: str, conv_id: str, rel_path: str) -> str:
    """Return absolute path for an attachment, with path traversal protection."""
    att_dir = get_attachment_dir(user_id, conv_id)
    return safe_join(att_dir, rel_path)
