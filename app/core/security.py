import os
import json
import uuid
import hashlib
import secrets
from datetime import datetime
from typing import Optional, Dict, Any

from app.core.settings import ROOT_DIR

try:
    import bcrypt
    USE_BCRYPT = True
except ImportError:
    USE_BCRYPT = False

USERS_DIR = os.path.join(ROOT_DIR, "users")
USERS_JSON = os.path.join(USERS_DIR, "users.json")
REG_KEYS_JSON = os.path.join(ROOT_DIR, "config", "registration_keys.json")


def _ensure_dirs():
    os.makedirs(USERS_DIR, exist_ok=True)


def _load_users() -> Dict[str, Any]:
    _ensure_dirs()
    if os.path.exists(USERS_JSON):
        with open(USERS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_users(users: Dict[str, Any]):
    _ensure_dirs()
    from app.core.fileutil import atomic_json_save
    atomic_json_save(USERS_JSON, users, ensure_ascii=False, indent=2)


def _hash_password(password: str) -> str:
    if USE_BCRYPT:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"sha256:{salt}:{hashed}"


def _verify_password(password: str, hashed: str) -> bool:
    if USE_BCRYPT and not hashed.startswith("sha256:"):
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    if hashed.startswith("sha256:"):
        _, salt, expected = hashed.split(":")
        actual = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return actual == expected
    return False


def _create_user_dirs(user_id: str):
    from app.storage import get_storage_service
    from app.services.memory_tools import ensure_soul_dir

    user_dir = os.path.join(USERS_DIR, user_id)
    os.makedirs(os.path.join(user_dir, "conversations"), exist_ok=True)

    ensure_soul_dir(user_id)

    storage = get_storage_service()
    storage.ensure_user_dirs(user_id)

    if not storage.exists(user_id, "/docs/README.md"):
        readme_path = os.path.join(ROOT_DIR, "docs", "README.md")
        if os.path.isfile(readme_path):
            with open(readme_path, "r", encoding="utf-8") as f:
                readme_content = f.read()
        else:
            readme_content = (
                "# 欢迎使用 JellyfishBot\n\n"
                "这是你的个人文档目录。你可以：\n\n"
                "- 上传、创建、编辑文档\n"
                "- 在 scripts/ 目录编写和运行 Python 脚本\n"
                "- 通过聊天让 Agent 帮你操作文件\n"
            )
        storage.write_text(user_id, "/docs/README.md", readme_content)

    if not storage.exists(user_id, "/scripts/hello.py"):
        storage.write_text(user_id, "/scripts/hello.py",
                           '"""示例脚本 - Hello World"""\n'
                           'import sys\n\n'
                           'name = sys.argv[1] if len(sys.argv) > 1 else "World"\n'
                           'print(f"Hello, {name}!")\n')


def _load_reg_keys() -> Dict[str, Any]:
    if os.path.exists(REG_KEYS_JSON):
        with open(REG_KEYS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"keys": []}


def _save_reg_keys(data: Dict[str, Any]):
    from app.core.fileutil import atomic_json_save
    atomic_json_save(REG_KEYS_JSON, data, ensure_ascii=False, indent=2)


def _validate_reg_key(reg_key: str) -> Dict[str, Any]:
    if not reg_key:
        return {"valid": False, "error": "注册码不能为空"}
    data = _load_reg_keys()
    reg_key_upper = reg_key.strip().upper()
    for i, entry in enumerate(data.get("keys", [])):
        if entry["key"] == reg_key_upper:
            if entry["used"]:
                return {"valid": False, "error": "该注册码已被使用"}
            return {"valid": True, "index": i}
    return {"valid": False, "error": "无效的注册码"}


def _mark_reg_key_used(index: int, username: str):
    data = _load_reg_keys()
    data["keys"][index]["used"] = True
    data["keys"][index]["used_by"] = username
    data["keys"][index]["used_at"] = datetime.now().isoformat()
    _save_reg_keys(data)


def register(username: str, password: str, reg_key: str = "") -> Dict[str, Any]:
    key_result = _validate_reg_key(reg_key)
    if not key_result["valid"]:
        return {"success": False, "error": key_result["error"]}
    if not username or not password:
        return {"success": False, "error": "用户名和密码不能为空"}
    if len(username) < 2:
        return {"success": False, "error": "用户名至少 2 个字符"}
    if len(password) < 4:
        return {"success": False, "error": "密码至少 4 个字符"}

    users = _load_users()
    for uid, info in users.items():
        if info.get("username") == username:
            return {"success": False, "error": "用户名已存在"}

    user_id = str(uuid.uuid4())[:8]
    token = secrets.token_hex(24)
    users[user_id] = {
        "username": username,
        "password_hash": _hash_password(password),
        "token": token,
        "created_at": datetime.now().isoformat(),
        "reg_key": reg_key.strip().upper(),
    }
    _save_users(users)
    _create_user_dirs(user_id)
    _mark_reg_key_used(key_result["index"], username)
    return {"success": True, "user_id": user_id, "username": username, "token": token}


def login(username: str, password: str) -> Dict[str, Any]:
    users = _load_users()
    for uid, info in users.items():
        if info.get("username") == username:
            if _verify_password(password, info["password_hash"]):
                new_token = secrets.token_hex(24)
                users[uid]["token"] = new_token
                users[uid]["last_login"] = datetime.now().isoformat()
                _save_users(users)
                return {"success": True, "user_id": uid, "username": username, "token": new_token}
            return {"success": False, "error": "密码错误"}
    return {"success": False, "error": "用户不存在"}


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    users = _load_users()
    for uid, info in users.items():
        if info.get("token") == token:
            return {"user_id": uid, "username": info["username"]}
    return None


def get_user_dir(user_id: str) -> str:
    return os.path.join(USERS_DIR, user_id)


def get_user_filesystem_dir(user_id: str) -> str:
    return os.path.join(USERS_DIR, user_id, "filesystem")


def get_user_conversations_dir(user_id: str) -> str:
    return os.path.join(USERS_DIR, user_id, "conversations")


def get_user_voice_transcripts_dir(user_id: str) -> str:
    return os.path.join(USERS_DIR, user_id, "voice_transcripts")
