"""
Local filesystem storage — wraps existing os.* logic.

Behaviour is identical to the original code so that STORAGE_BACKEND=local
has zero regression risk.
"""

import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, List, Optional

from app.core.settings import ROOT_DIR
from app.core.path_security import safe_join
from app.storage.base import StorageService, FileEntry

USERS_DIR = os.path.join(ROOT_DIR, "users")


def _fs_root(user_id: str) -> str:
    return os.path.join(USERS_DIR, user_id, "filesystem")


def _consumer_gen_root(admin_id: str, service_id: str, conv_id: str) -> str:
    return os.path.join(
        USERS_DIR, admin_id, "services", service_id,
        "conversations", conv_id, "generated",
    )


def _resolve(root: str, path: str) -> str:
    root = os.path.abspath(root)
    return safe_join(root, path)


class LocalStorageService(StorageService):

    # ── directory listing ──

    def list_dir(self, user_id: str, path: str = "/") -> List[FileEntry]:
        root = _fs_root(user_id)
        full = _resolve(root, path)
        if not os.path.exists(full) or not os.path.isdir(full):
            return []
        items: List[FileEntry] = []
        for name in sorted(os.listdir(full)):
            entry_path = os.path.join(full, name)
            is_dir = os.path.isdir(entry_path)
            rel = "/" + os.path.relpath(entry_path, root).replace("\\", "/")
            try:
                stat = os.stat(entry_path)
                items.append(FileEntry(
                    name=name, path=rel, is_dir=is_dir,
                    size=stat.st_size if not is_dir else 0,
                    modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                ))
            except OSError:
                items.append(FileEntry(name=name, path=rel, is_dir=is_dir))
        return items

    # ── read ──

    def read_text(self, user_id: str, path: str) -> str:
        full = _resolve(_fs_root(user_id), path)
        if not os.path.isfile(full):
            raise FileNotFoundError(path)
        with open(full, "r", encoding="utf-8") as f:
            return f.read()

    def read_bytes(self, user_id: str, path: str) -> bytes:
        full = _resolve(_fs_root(user_id), path)
        if not os.path.isfile(full):
            raise FileNotFoundError(path)
        with open(full, "rb") as f:
            return f.read()

    # ── write ──

    def write_text(self, user_id: str, path: str, content: str) -> None:
        full = _resolve(_fs_root(user_id), path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def write_bytes(self, user_id: str, path: str, data: bytes) -> None:
        full = _resolve(_fs_root(user_id), path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)

    # ── edit ──

    def edit_text(self, user_id: str, path: str, old_string: str, new_string: str) -> None:
        full = _resolve(_fs_root(user_id), path)
        if not os.path.isfile(full):
            raise FileNotFoundError(path)
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        if old_string not in content:
            raise ValueError("未找到要替换的内容")
        content = content.replace(old_string, new_string, 1)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    # ── delete / move ──

    def delete(self, user_id: str, path: str) -> None:
        full = _resolve(_fs_root(user_id), path)
        if not os.path.exists(full):
            raise FileNotFoundError(path)
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            os.remove(full)

    def move(self, user_id: str, source: str, destination: str) -> str:
        root = _fs_root(user_id)
        src = _resolve(root, source)
        dst = _resolve(root, destination)
        if not os.path.exists(src):
            raise FileNotFoundError(source)
        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(src))
        if os.path.exists(dst):
            raise FileExistsError("目标路径已存在")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.rename(src, dst)
        except (PermissionError, OSError):
            if os.path.isdir(src):
                shutil.copytree(src, dst)
                shutil.rmtree(src)
            else:
                shutil.copy2(src, dst)
                for attempt in range(3):
                    try:
                        os.remove(src)
                        break
                    except PermissionError:
                        if attempt < 2:
                            time.sleep(0.2)
        return "/" + os.path.relpath(dst, root).replace("\\", "/")

    # ── queries ──

    def exists(self, user_id: str, path: str) -> bool:
        try:
            full = _resolve(_fs_root(user_id), path)
        except PermissionError:
            return False
        return os.path.exists(full)

    def is_file(self, user_id: str, path: str) -> bool:
        try:
            full = _resolve(_fs_root(user_id), path)
        except PermissionError:
            return False
        return os.path.isfile(full)

    def is_dir(self, user_id: str, path: str) -> bool:
        try:
            full = _resolve(_fs_root(user_id), path)
        except PermissionError:
            return False
        return os.path.isdir(full)

    def makedirs(self, user_id: str, path: str) -> None:
        full = _resolve(_fs_root(user_id), path)
        os.makedirs(full, exist_ok=True)

    # ── user init ──

    def ensure_user_dirs(self, user_id: str) -> None:
        root = _fs_root(user_id)
        for subdir in (
            "docs", "scripts",
            "generated/images", "generated/audio", "generated/videos",
        ):
            os.makedirs(os.path.join(root, subdir), exist_ok=True)

    # ── HTTP response helpers (via base concrete methods) ──

    def _get_real_path(self, user_id: str, path: str) -> str:
        return _resolve(_fs_root(user_id), path)

    def _get_media_url(self, user_id: str, path: str, expires_in: int = 3600) -> Optional[str]:
        return None

    # ── consumer operations ──

    def list_consumer_files(
        self, admin_id: str, service_id: str, conv_id: str,
    ) -> list[dict]:
        gen_dir = _consumer_gen_root(admin_id, service_id, conv_id)
        if not os.path.isdir(gen_dir):
            return []
        files = []
        for root, _dirs, filenames in os.walk(gen_dir):
            for fn in filenames:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, gen_dir).replace("\\", "/")
                files.append({"path": rel, "size": os.path.getsize(full)})
        return files

    def read_consumer_bytes(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> bytes:
        gen_dir = _consumer_gen_root(admin_id, service_id, conv_id)
        full = safe_join(gen_dir, path)
        if not os.path.isfile(full):
            raise FileNotFoundError(path)
        with open(full, "rb") as f:
            return f.read()

    def write_consumer_bytes(
        self, admin_id: str, service_id: str, conv_id: str, path: str, data: bytes,
    ) -> None:
        gen_dir = _consumer_gen_root(admin_id, service_id, conv_id)
        clean = path.lstrip("/").replace("\\", "/")
        full = os.path.join(gen_dir, clean)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)

    def consumer_exists(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> bool:
        gen_dir = _consumer_gen_root(admin_id, service_id, conv_id)
        clean = path.lstrip("/").replace("\\", "/")
        return os.path.exists(os.path.join(gen_dir, clean))

    def _get_consumer_real_path(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> str:
        gen_dir = _consumer_gen_root(admin_id, service_id, conv_id)
        os.makedirs(gen_dir, exist_ok=True)
        return safe_join(gen_dir, path)

    def _get_consumer_media_url(
        self, admin_id: str, service_id: str, conv_id: str,
        path: str, expires_in: int = 3600,
    ) -> Optional[str]:
        return None

    # ── script execution ──

    @contextmanager
    def script_execution(
        self, user_id: str, script_path: str,
    ) -> Generator[dict, None, None]:
        root = _fs_root(user_id)
        scripts_dir = os.path.join(root, "scripts")
        docs_dir = os.path.join(root, "docs")
        gen_dir = os.path.join(root, "generated")
        yield {
            "scripts_dir": scripts_dir,
            "docs_dir": docs_dir,
            "write_dirs": [scripts_dir, gen_dir],
        }

    @contextmanager
    def consumer_script_execution(
        self, admin_id: str, service_id: str, conv_id: str, script_path: str,
    ) -> Generator[dict, None, None]:
        root = _fs_root(admin_id)
        scripts_dir = os.path.join(root, "scripts")
        docs_dir = os.path.join(root, "docs")
        consumer_gen = _consumer_gen_root(admin_id, service_id, conv_id)
        os.makedirs(consumer_gen, exist_ok=True)
        yield {
            "scripts_dir": scripts_dir,
            "docs_dir": docs_dir,
            "write_dirs": [scripts_dir, consumer_gen],
        }
