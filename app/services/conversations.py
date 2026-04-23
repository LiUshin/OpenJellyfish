"""Admin conversation storage.

Storage layout (since 2026-04-23):

    users/{uid}/conversations/{conv_id}/
        meta.json          - {id, title, created_at, updated_at, message_count}
                             rewritten on every save (small file, atomic)
        messages.jsonl     - one message per line, append-only
        query_appendix/    - user-uploaded attachments (unchanged)
        .legacy.json       - the original single-file conversation JSON,
                             kept for one-shot backup after lazy migration

Legacy layout (still readable; auto-migrated on first touch):

    users/{uid}/conversations/{conv_id}.json   - whole conversation in one
                                                  JSON object with messages[]

The migration is **lazy**: the first call to ``get_conversation`` /
``save_message`` / ``list_conversations`` for a given conv_id triggers
``_migrate_if_needed`` which splits the old file into ``meta.json`` +
``messages.jsonl`` and renames the original to
``{conv_id}/.legacy.json`` (inside the new directory).  No restart, no
manual scripts.

Why this layout?
- ``save_message`` was previously O(N) per write (read full + json.dump
  full).  Now it's O(1): one ``json.dumps`` + one ``write`` to the
  message file, plus a tiny ``meta.json`` rewrite.
- ``list_conversations`` no longer parses every conversation just to
  compute ``message_count`` — it reads the cached value from each
  ``meta.json``.
- Multimodal base64 images stay in the messages stream (callers should
  preferentially store them under ``query_appendix/`` and reference by
  path, but historic blobs still load fine).
"""

import os
import json
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

from app.core.security import get_user_conversations_dir
from app.core.path_security import safe_join
from app.core.fileutil import atomic_json_save
from app.core.jsonl_store import (
    append_jsonl,
    append_jsonl_many,
    read_jsonl,
    read_jsonl_tail,
    safe_load_json,
)


# ── path helpers ────────────────────────────────────────────────────

def _conv_dir(user_id: str, conv_id: str) -> str:
    return os.path.join(get_user_conversations_dir(user_id), conv_id)


def _meta_path(user_id: str, conv_id: str) -> str:
    return os.path.join(_conv_dir(user_id, conv_id), "meta.json")


def _msgs_path(user_id: str, conv_id: str) -> str:
    return os.path.join(_conv_dir(user_id, conv_id), "messages.jsonl")


def _legacy_inside(user_id: str, conv_id: str) -> str:
    """Backed-up legacy file inside the new directory."""
    return os.path.join(_conv_dir(user_id, conv_id), ".legacy.json")


def _legacy_sibling(user_id: str, conv_id: str) -> str:
    """The original sibling-file location (pre-migration)."""
    return os.path.join(get_user_conversations_dir(user_id), f"{conv_id}.json")


# ── lazy migration ──────────────────────────────────────────────────

def _migrate_if_needed(user_id: str, conv_id: str) -> None:
    """If a legacy {conv_id}.json sibling file exists and the new
    {conv_id}/meta.json doesn't, split it.

    Idempotent.  Cheap when nothing to do (one ``os.path.isfile`` call).
    Failures during migration are swallowed so a corrupted legacy file
    doesn't break new conversation creation; the legacy file stays in
    place for manual recovery.
    """
    sibling = _legacy_sibling(user_id, conv_id)
    if not os.path.isfile(sibling):
        return
    if os.path.isfile(_meta_path(user_id, conv_id)):
        return  # someone already migrated, leave legacy file alone

    try:
        with open(sibling, "r", encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, json.JSONDecodeError):
        return

    messages = old.get("messages") or []
    meta = {
        "id": old.get("id", conv_id),
        "title": old.get("title", "新对话"),
        "created_at": old.get("created_at", datetime.now().isoformat()),
        "updated_at": old.get("updated_at", datetime.now().isoformat()),
        "message_count": len(messages),
    }

    os.makedirs(_conv_dir(user_id, conv_id), exist_ok=True)
    msgs_path = _msgs_path(user_id, conv_id)
    if not os.path.isfile(msgs_path):
        try:
            append_jsonl_many(msgs_path, messages)
        except OSError:
            return
    atomic_json_save(_meta_path(user_id, conv_id), meta,
                     ensure_ascii=False, indent=2)

    legacy_dst = _legacy_inside(user_id, conv_id)
    try:
        os.replace(sibling, legacy_dst)
    except OSError:
        pass


# ── meta sidecar ────────────────────────────────────────────────────

def _write_meta(user_id: str, conv_id: str, meta: Dict[str, Any]) -> None:
    atomic_json_save(_meta_path(user_id, conv_id), meta,
                     ensure_ascii=False, indent=2)


def _load_meta(user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    return safe_load_json(_meta_path(user_id, conv_id))


# ── public API ──────────────────────────────────────────────────────

def list_conversations(user_id: str) -> List[Dict[str, Any]]:
    """Return summary list (id/title/created/updated/message_count).

    Reads only meta.json sidecars — never opens messages.jsonl, so a
    user with thousands of conversations still loads instantly.
    Legacy single-file conversations are migrated on first sight.
    """
    conv_root = get_user_conversations_dir(user_id)
    os.makedirs(conv_root, exist_ok=True)

    seen: set = set()
    out: List[Dict[str, Any]] = []

    # Migrate any legacy sibling .json files first.
    for entry in os.listdir(conv_root):
        if entry.endswith(".json") and os.path.isfile(os.path.join(conv_root, entry)):
            conv_id = entry[:-5]
            _migrate_if_needed(user_id, conv_id)

    for entry in os.listdir(conv_root):
        full = os.path.join(conv_root, entry)
        if not os.path.isdir(full):
            continue
        meta = _load_meta(user_id, entry)
        if not meta:
            continue
        out.append({
            "id": meta.get("id", entry),
            "title": meta.get("title", "新对话"),
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
            "message_count": meta.get("message_count", 0),
        })
        seen.add(entry)
    out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return out


def create_conversation(user_id: str, title: str = "新对话") -> Dict[str, Any]:
    conv_root = get_user_conversations_dir(user_id)
    os.makedirs(conv_root, exist_ok=True)
    conv_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()

    os.makedirs(_conv_dir(user_id, conv_id), exist_ok=True)
    meta = {
        "id": conv_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
    }
    _write_meta(user_id, conv_id, meta)
    # Touch messages.jsonl so directory has both files even if user
    # never sends a message (avoids meta-without-jsonl edge cases).
    msgs = _msgs_path(user_id, conv_id)
    if not os.path.isfile(msgs):
        open(msgs, "ab").close()

    return {**meta, "messages": []}


def get_conversation(user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    """Return the full conversation dict ({id, title, ..., messages: [...]}).

    Triggers lazy migration if a legacy sibling JSON is found.  Reading
    a long conversation still pulls all messages into memory — callers
    that only need the most recent N should use ``get_recent_messages``.
    """
    _migrate_if_needed(user_id, conv_id)
    meta = _load_meta(user_id, conv_id)
    if not meta:
        return None
    messages = read_jsonl(_msgs_path(user_id, conv_id))
    out = dict(meta)
    out["messages"] = messages
    return out


def get_recent_messages(user_id: str, conv_id: str,
                        last_n: int) -> List[Dict[str, Any]]:
    """Efficient tail-read for short-term-memory injection.  Avoids
    loading the whole conversation when only the last few are needed."""
    _migrate_if_needed(user_id, conv_id)
    return read_jsonl_tail(_msgs_path(user_id, conv_id), last_n)


def delete_conversation(user_id: str, conv_id: str) -> bool:
    import shutil

    conv_root = get_user_conversations_dir(user_id)
    target_dir = os.path.join(conv_root, conv_id)
    sibling = _legacy_sibling(user_id, conv_id)

    removed = False
    if os.path.isdir(target_dir):
        try:
            shutil.rmtree(target_dir)
            removed = True
        except OSError:
            pass
    if os.path.isfile(sibling):
        try:
            os.remove(sibling)
            removed = True
        except OSError:
            pass
    return removed


def save_message(user_id: str, conv_id: str, role: str, content: str,
                 tool_calls: list = None, attachments: list = None,
                 blocks: list = None):
    """Append one message and refresh meta.json.

    Replaces the old read-full-rewrite-full pattern: one append to
    messages.jsonl + one tiny meta.json atomic write.  Total IO is
    independent of conversation length.
    """
    _migrate_if_needed(user_id, conv_id)
    os.makedirs(_conv_dir(user_id, conv_id), exist_ok=True)

    now = datetime.now().isoformat()
    msg: Dict[str, Any] = {
        "role": role,
        "content": content,
        "timestamp": now,
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if attachments:
        msg["attachments"] = attachments
    if blocks:
        msg["blocks"] = blocks

    append_jsonl(_msgs_path(user_id, conv_id), msg)

    meta = _load_meta(user_id, conv_id) or {
        "id": conv_id,
        "title": "新对话",
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
    }
    meta["message_count"] = int(meta.get("message_count", 0)) + 1
    meta["updated_at"] = now
    if meta.get("title", "新对话") == "新对话" and role == "user":
        meta["title"] = content[:30] + ("..." if len(content) > 30 else "")
    _write_meta(user_id, conv_id, meta)


# ── attachment helpers (unchanged behaviour) ────────────────────────

def get_attachment_dir(user_id: str, conv_id: str) -> str:
    return os.path.join(_conv_dir(user_id, conv_id), "query_appendix")


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
