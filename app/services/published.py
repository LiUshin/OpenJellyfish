"""
Service publishing — CRUD for published services and their API keys.
"""

import os
import json
import uuid
import hashlib
import secrets
from datetime import datetime
from typing import Optional, Dict, Any, List

from app.core.security import get_user_dir, get_user_filesystem_dir


# ── paths ────────────────────────────────────────────────────────────

def _services_dir(admin_id: str) -> str:
    return os.path.join(get_user_dir(admin_id), "services")


def _service_dir(admin_id: str, service_id: str) -> str:
    return os.path.join(_services_dir(admin_id), service_id)


def _config_path(admin_id: str, service_id: str) -> str:
    return os.path.join(_service_dir(admin_id, service_id), "config.json")


def _keys_path(admin_id: str, service_id: str) -> str:
    return os.path.join(_service_dir(admin_id, service_id), "keys.json")


def _conv_dir(admin_id: str, service_id: str, conv_id: str) -> str:
    return os.path.join(_service_dir(admin_id, service_id), "conversations", conv_id)


# ── Service CRUD ─────────────────────────────────────────────────────

def create_service(admin_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    service_id = "svc_" + uuid.uuid4().hex[:8]
    svc_dir = _service_dir(admin_id, service_id)
    os.makedirs(svc_dir, exist_ok=True)
    os.makedirs(os.path.join(svc_dir, "conversations"), exist_ok=True)

    now = datetime.now().isoformat()
    config = {
        "id": service_id,
        "admin_id": admin_id,
        **data,
        "created_at": now,
        "updated_at": now,
    }
    from app.core.fileutil import atomic_json_save
    atomic_json_save(_config_path(admin_id, service_id), config, ensure_ascii=False, indent=2)
    atomic_json_save(_keys_path(admin_id, service_id), {"keys": []}, indent=2)

    return config


def list_services(admin_id: str) -> List[Dict[str, Any]]:
    svc_root = _services_dir(admin_id)
    if not os.path.isdir(svc_root):
        return []
    services = []
    for name in os.listdir(svc_root):
        cfg_path = os.path.join(svc_root, name, "config.json")
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                services.append(json.load(f))
    services.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return services


def get_service(admin_id: str, service_id: str) -> Optional[Dict[str, Any]]:
    cfg = _config_path(admin_id, service_id)
    if not os.path.isfile(cfg):
        return None
    with open(cfg, "r", encoding="utf-8") as f:
        return json.load(f)


def update_service(admin_id: str, service_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    config = get_service(admin_id, service_id)
    if not config:
        return None
    config.update({k: v for k, v in updates.items() if v is not None})
    config["updated_at"] = datetime.now().isoformat()
    from app.core.fileutil import atomic_json_save
    atomic_json_save(_config_path(admin_id, service_id), config, ensure_ascii=False, indent=2)
    return config


def delete_service(admin_id: str, service_id: str) -> bool:
    import shutil
    svc_dir = _service_dir(admin_id, service_id)
    if not os.path.isdir(svc_dir):
        return False
    shutil.rmtree(svc_dir)
    return True


# ── API Key management ───────────────────────────────────────────────

def _hash_key(raw_key: str) -> str:
    return "sha256:" + hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _verify_key_hash(raw_key: str, stored_hash: str) -> bool:
    if stored_hash.startswith("sha256:"):
        return stored_hash == _hash_key(raw_key)
    return False


def create_service_key(admin_id: str, service_id: str, name: str = "default") -> Optional[Dict[str, Any]]:
    keys_file = _keys_path(admin_id, service_id)
    if not os.path.isfile(keys_file):
        return None

    raw_key = "sk-svc-" + secrets.token_hex(24)
    prefix = raw_key[:12]
    key_id = "key_" + uuid.uuid4().hex[:6]

    with open(keys_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    entry = {
        "id": key_id,
        "prefix": prefix,
        "key_hash": _hash_key(raw_key),
        "name": name,
        "created_at": datetime.now().isoformat(),
        "last_used_at": None,
    }
    data["keys"].append(entry)

    from app.core.fileutil import atomic_json_save
    atomic_json_save(keys_file, data, ensure_ascii=False, indent=2)

    return {"id": key_id, "key": raw_key, "prefix": prefix, "name": name}


def list_service_keys(admin_id: str, service_id: str) -> List[Dict[str, Any]]:
    keys_file = _keys_path(admin_id, service_id)
    if not os.path.isfile(keys_file):
        return []
    with open(keys_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [
        {"id": k["id"], "prefix": k["prefix"], "name": k["name"],
         "created_at": k["created_at"], "last_used_at": k.get("last_used_at")}
        for k in data["keys"]
    ]


def delete_service_key(admin_id: str, service_id: str, key_id: str) -> bool:
    keys_file = _keys_path(admin_id, service_id)
    if not os.path.isfile(keys_file):
        return False
    with open(keys_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    before = len(data["keys"])
    data["keys"] = [k for k in data["keys"] if k["id"] != key_id]
    if len(data["keys"]) == before:
        return False
    from app.core.fileutil import atomic_json_save
    atomic_json_save(keys_file, data, ensure_ascii=False, indent=2)
    return True


def verify_service_key(raw_key: str) -> Optional[Dict[str, Any]]:
    """
    Scan all users/services to find a matching key.
    Returns {"admin_id", "service_id", "key_id", "service_config"} or None.
    """
    from app.core.security import USERS_DIR
    if not os.path.isdir(USERS_DIR):
        return None
    for admin_id in os.listdir(USERS_DIR):
        svc_root = os.path.join(USERS_DIR, admin_id, "services")
        if not os.path.isdir(svc_root):
            continue
        for svc_name in os.listdir(svc_root):
            keys_file = os.path.join(svc_root, svc_name, "keys.json")
            if not os.path.isfile(keys_file):
                continue
            with open(keys_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for entry in data.get("keys", []):
                if _verify_key_hash(raw_key, entry.get("key_hash", "")):
                    entry["last_used_at"] = datetime.now().isoformat()
                    from app.core.fileutil import atomic_json_save
                    atomic_json_save(keys_file, data, ensure_ascii=False, indent=2)
                    cfg_file = os.path.join(svc_root, svc_name, "config.json")
                    svc_config = {}
                    if os.path.isfile(cfg_file):
                        with open(cfg_file, "r", encoding="utf-8") as f:
                            svc_config = json.load(f)
                    return {
                        "admin_id": admin_id,
                        "service_id": svc_name,
                        "key_id": entry["id"],
                        "service_config": svc_config,
                    }
    return None


# ── Consumer conversation helpers ────────────────────────────────────

def create_consumer_conversation(admin_id: str, service_id: str, title: str = "") -> Dict[str, Any]:
    conv_id = uuid.uuid4().hex[:10]
    conv_path = _conv_dir(admin_id, service_id, conv_id)
    os.makedirs(os.path.join(conv_path, "generated", "images"), exist_ok=True)
    os.makedirs(os.path.join(conv_path, "generated", "audio"), exist_ok=True)
    os.makedirs(os.path.join(conv_path, "generated", "videos"), exist_ok=True)

    now = datetime.now().isoformat()
    meta = {"id": conv_id, "title": title, "created_at": now, "updated_at": now, "messages": []}
    from app.core.fileutil import atomic_json_save
    atomic_json_save(os.path.join(conv_path, "messages.json"), meta, ensure_ascii=False, indent=2)
    return meta


def get_consumer_conversation(admin_id: str, service_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    msg_file = os.path.join(_conv_dir(admin_id, service_id, conv_id), "messages.json")
    if not os.path.isfile(msg_file):
        return None
    with open(msg_file, "r", encoding="utf-8") as f:
        return json.load(f)


def save_consumer_message(admin_id: str, service_id: str, conv_id: str,
                          role: str, content: str, tool_calls: list = None,
                          attachments: list = None, blocks: list = None):
    conv_path = _conv_dir(admin_id, service_id, conv_id)
    msg_file = os.path.join(conv_path, "messages.json")
    if os.path.isfile(msg_file):
        with open(msg_file, "r", encoding="utf-8") as f:
            conv = json.load(f)
    else:
        os.makedirs(conv_path, exist_ok=True)
        conv = {"id": conv_id, "title": "", "created_at": datetime.now().isoformat(), "messages": []}

    msg: Dict[str, Any] = {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if attachments:
        msg["attachments"] = attachments
    if blocks:
        msg["blocks"] = blocks
    conv["messages"].append(msg)
    conv["updated_at"] = datetime.now().isoformat()
    if not conv.get("title") and role == "user":
        conv["title"] = content[:30] + ("..." if len(content) > 30 else "")

    from app.core.fileutil import atomic_json_save
    atomic_json_save(msg_file, conv, ensure_ascii=False, indent=2)


def get_consumer_attachment_dir(admin_id: str, service_id: str, conv_id: str) -> str:
    return os.path.join(_conv_dir(admin_id, service_id, conv_id), "query_appendix")


def save_consumer_attachment(admin_id: str, service_id: str, conv_id: str,
                             rel_path: str, data: bytes) -> str:
    """Save a consumer attachment file and return the relative path."""
    att_dir = get_consumer_attachment_dir(admin_id, service_id, conv_id)
    clean = rel_path.lstrip("/").replace("\\", "/")
    full = os.path.join(att_dir, clean)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)
    return clean


def get_consumer_generated_dir(admin_id: str, service_id: str, conv_id: str) -> str:
    return os.path.join(_conv_dir(admin_id, service_id, conv_id), "generated")
