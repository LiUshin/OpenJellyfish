"""
Local filesystem storage — wraps existing os.* logic.

Behaviour is identical to the original code so that STORAGE_BACKEND=local
has zero regression risk.
"""

import logging
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, List, Optional

from app.core.settings import ROOT_DIR
from app.core.path_security import safe_join
from app.storage.base import StorageService, FileEntry
from app.storage.config import sandbox_budget_bytes

_log = logging.getLogger("storage.local")

USERS_DIR = os.path.join(ROOT_DIR, "users")


def _fs_root(user_id: str) -> str:
    return os.path.join(USERS_DIR, user_id, "filesystem")


def _new_scratch_dir(user_id: str) -> str:
    """Per-run scratch dir.

    Lives under the owning user's directory so it is on the same mount as
    users/ — hardlinking docs into it would fail with EXDEV from anywhere
    else (in Docker, /app/data and /app/users are separate bind mounts).
    Not part of any backup module, so it never ends up in an export.
    """
    base = os.path.join(USERS_DIR, user_id, ".scratch")
    os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix="run_", dir=base)


def _replicate_tree(src: str, dest: str, *, writable: bool, budget: int) -> None:
    """Reproduce `src` under `dest`, capped at `budget` bytes.

    Read-only trees are hardlinked (free); writable ones are real copies so the
    sandbox cannot modify the original.
    """
    os.makedirs(dest, exist_ok=True)
    if not os.path.isdir(src):
        return
    used = 0
    skipped: list[str] = []
    for dirpath, _dirs, filenames in os.walk(src):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                continue  # never follow links out of the tree
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            rel = os.path.relpath(full, src)
            if used + size > budget:
                skipped.append(rel)
                continue
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                if writable:
                    shutil.copy2(full, target)
                else:
                    os.link(full, target)
            except OSError:
                try:
                    shutil.copy2(full, target)
                except OSError as e:
                    _log.warning("failed to materialize %s: %s", rel, e)
                    continue
            used += size
    if skipped:
        _log.warning(
            "%d file(s) under %s not materialized (budget %d MB): %s",
            len(skipped), src, budget // 1024 // 1024, skipped[:10],
        )


def _drain_tree(src: str, dest: str) -> None:
    """Move everything under `src` into `dest`, overwriting collisions."""
    if not os.path.isdir(src):
        return
    for dirpath, _dirs, filenames in os.walk(src):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            target = os.path.join(dest, os.path.relpath(full, src))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                if os.path.exists(target):
                    os.remove(target)  # os.rename does not overwrite on Windows
                shutil.move(full, target)
            except OSError as e:
                _log.error("failed to persist script output %s: %s", fn, e)


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

    def copy(self, user_id: str, source: str, destination: str) -> str:
        root = _fs_root(user_id)
        src = _resolve(root, source)
        dst = _resolve(root, destination)
        if not os.path.exists(src):
            raise FileNotFoundError(source)
        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(src.rstrip(os.sep)))
        if os.path.exists(dst):
            raise FileExistsError("目标路径已存在")
        # Block copying a dir into itself or any descendant.
        if os.path.isdir(src):
            src_canon = os.path.realpath(src)
            dst_canon = os.path.realpath(os.path.dirname(dst) or root)
            if dst_canon == src_canon or dst_canon.startswith(src_canon + os.sep):
                raise ValueError("不能把文件夹复制到它自己里面")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return "/" + os.path.relpath(dst, root).replace("\\", "/")

    def walk_files(
        self, user_id: str, path: str,
    ) -> Generator[tuple[str, bytes], None, None]:
        full = _resolve(_fs_root(user_id), path)
        if not os.path.exists(full):
            raise FileNotFoundError(path)
        if os.path.isfile(full):
            with open(full, "rb") as f:
                yield os.path.basename(full), f.read()
            return
        for root_dir, _dirs, filenames in os.walk(full):
            for fn in filenames:
                file_full = os.path.join(root_dir, fn)
                rel = os.path.relpath(file_full, full).replace("\\", "/")
                with open(file_full, "rb") as f:
                    yield rel, f.read()

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
        """Consumer scripts run against a scratch replica of the admin's
        workspace, never the real one.

        `run_script` always makes `scripts_dir` the cwd and adds it to the read
        whitelist, so handing over the admin's real scripts/ would let any
        consumer-triggered script overwrite the admin's scripts. The scratch
        layout also makes `../docs/x.csv` and `../generated/out.png` resolve
        the same way they do for admin runs and under the S3 backend.
        """
        admin_root = _fs_root(admin_id)
        consumer_gen = _consumer_gen_root(admin_id, service_id, conv_id)
        os.makedirs(consumer_gen, exist_ok=True)

        root = _new_scratch_dir(admin_id)
        tmp_scripts = os.path.join(root, "scripts")
        tmp_docs = os.path.join(root, "docs")
        tmp_gen = os.path.join(root, "generated")
        try:
            budget = sandbox_budget_bytes()
            _replicate_tree(
                os.path.join(admin_root, "scripts"), tmp_scripts,
                writable=True, budget=budget,
            )
            _replicate_tree(
                os.path.join(admin_root, "docs"), tmp_docs,
                writable=False, budget=budget,
            )
            os.makedirs(tmp_gen, exist_ok=True)
            # The script being run is exempt from the budget.
            clean = script_path.replace("\\", "/").lstrip("/")
            local_script = os.path.join(tmp_scripts, *clean.split("/"))
            origin = os.path.join(admin_root, "scripts", *clean.split("/"))
            if not os.path.isfile(local_script) and os.path.isfile(origin):
                os.makedirs(os.path.dirname(local_script), exist_ok=True)
                shutil.copy2(origin, local_script)
            yield {
                "scripts_dir": tmp_scripts,
                "docs_dir": tmp_docs,
                "write_dirs": [tmp_scripts, tmp_gen],
            }
        finally:
            try:
                _drain_tree(tmp_gen, consumer_gen)
            except Exception:
                _log.exception("failed to persist consumer script output")
            shutil.rmtree(root, ignore_errors=True)
