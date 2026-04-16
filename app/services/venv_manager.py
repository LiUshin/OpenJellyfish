"""
Per-user Python virtual environment manager.

Each admin gets an isolated venv under users/{user_id}/venv/.
The venv inherits system site-packages (--system-site-packages) so all
pre-installed scientific libs (numpy, pandas, etc.) are available without
re-install. Users can add extra packages via pip.

Package list is persisted in users/{user_id}/venv/requirements.txt so
that on service restart we can verify and restore missing packages.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from typing import Dict, Any, List, Optional

from app.core.security import get_user_dir

log = logging.getLogger("venv_manager")

VENV_DIR_NAME = "venv"
REQUIREMENTS_FILE = "requirements.txt"
_VENV_CREATION_LOCK: Dict[str, asyncio.Lock] = {}


def _venv_dir(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), VENV_DIR_NAME)


def _requirements_path(user_id: str) -> str:
    return os.path.join(_venv_dir(user_id), REQUIREMENTS_FILE)


def _get_venv_python(user_id: str) -> str:
    vdir = _venv_dir(user_id)
    if sys.platform == "win32":
        return os.path.join(vdir, "Scripts", "python.exe")
    return os.path.join(vdir, "bin", "python")


def _get_venv_pip(user_id: str) -> str:
    vdir = _venv_dir(user_id)
    if sys.platform == "win32":
        return os.path.join(vdir, "Scripts", "pip.exe")
    return os.path.join(vdir, "bin", "pip")


def venv_exists(user_id: str) -> bool:
    return os.path.isfile(_get_venv_python(user_id))


def get_user_python(user_id: str) -> str:
    """Return the Python executable for a user.
    Falls back to sys.executable if venv doesn't exist yet.
    """
    venv_py = _get_venv_python(user_id)
    if os.path.isfile(venv_py):
        return venv_py
    return sys.executable


def _get_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _VENV_CREATION_LOCK:
        _VENV_CREATION_LOCK[user_id] = asyncio.Lock()
    return _VENV_CREATION_LOCK[user_id]


async def ensure_venv(user_id: str) -> str:
    """Create user venv if it doesn't exist. Returns the venv python path.

    Uses --system-site-packages so pre-installed libs are inherited.
    Thread-safe via per-user asyncio lock.
    """
    if venv_exists(user_id):
        return _get_venv_python(user_id)

    lock = _get_lock(user_id)
    async with lock:
        if venv_exists(user_id):
            return _get_venv_python(user_id)

        vdir = _venv_dir(user_id)
        log.info("Creating venv for user %s at %s", user_id, vdir)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "venv", "--system-site-packages", vdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            log.error("Failed to create venv for %s: %s", user_id, err)
            raise RuntimeError(f"Failed to create venv: {err}")

        log.info("Venv created for user %s", user_id)
        return _get_venv_python(user_id)


async def install_package(user_id: str, package: str) -> Dict[str, Any]:
    """Install a pip package into the user's venv."""
    await ensure_venv(user_id)
    pip = _get_venv_pip(user_id)

    log.info("Installing package '%s' for user %s", package, user_id)
    proc = await asyncio.create_subprocess_exec(
        pip, "install", package,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")

    if proc.returncode == 0:
        _sync_requirements(user_id)
        return {"success": True, "output": out}
    return {"success": False, "error": err, "output": out}


async def uninstall_package(user_id: str, package: str) -> Dict[str, Any]:
    """Uninstall a pip package from the user's venv."""
    if not venv_exists(user_id):
        return {"success": False, "error": "Venv does not exist"}
    pip = _get_venv_pip(user_id)

    log.info("Uninstalling package '%s' for user %s", package, user_id)
    proc = await asyncio.create_subprocess_exec(
        pip, "uninstall", "-y", package,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")

    if proc.returncode == 0:
        _sync_requirements(user_id)
        return {"success": True, "output": out}
    return {"success": False, "error": err, "output": out}


def list_packages(user_id: str) -> List[Dict[str, str]]:
    """List installed packages (user-level only, excluding system site-packages)."""
    if not venv_exists(user_id):
        return []

    python = _get_venv_python(user_id)
    try:
        result = subprocess.run(
            [python, "-m", "pip", "list", "--format=json", "--not-required",
             "--exclude-editable"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        log.warning("Failed to list packages for %s: %s", user_id, e)
    return []


def list_all_packages(user_id: str) -> List[Dict[str, str]]:
    """List ALL installed packages including system site-packages."""
    python = get_user_python(user_id)
    try:
        result = subprocess.run(
            [python, "-m", "pip", "list", "--format=json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        log.warning("Failed to list all packages for %s: %s", user_id, e)
    return []


def _sync_requirements(user_id: str):
    """Freeze user-installed packages to requirements.txt for restore on restart."""
    if not venv_exists(user_id):
        return
    pip = _get_venv_pip(user_id)
    try:
        result = subprocess.run(
            [pip, "freeze", "--local"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            req_path = _requirements_path(user_id)
            with open(req_path, "w", encoding="utf-8") as f:
                f.write(result.stdout)
    except Exception as e:
        log.warning("Failed to sync requirements for %s: %s", user_id, e)


async def restore_venv(user_id: str) -> bool:
    """Restore a user's venv from their requirements.txt if the venv exists
    but packages might be missing (e.g., after a container rebuild).

    Returns True if restoration was performed.
    """
    req_path = _requirements_path(user_id)
    if not os.path.isfile(req_path):
        return False

    with open(req_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return False

    await ensure_venv(user_id)
    pip = _get_venv_pip(user_id)

    log.info("Restoring packages for user %s from requirements.txt", user_id)
    proc = await asyncio.create_subprocess_exec(
        pip, "install", "-r", req_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace")
        log.error("Failed to restore packages for %s: %s", user_id, err)
        return False

    log.info("Packages restored for user %s", user_id)
    return True


async def restore_all_venvs():
    """Scan all user directories and restore venvs that have requirements.txt.

    Called during application startup.
    """
    from app.core.settings import ROOT_DIR
    users_dir = os.path.join(ROOT_DIR, "users")
    if not os.path.isdir(users_dir):
        return

    tasks = []
    for entry in os.listdir(users_dir):
        user_dir = os.path.join(users_dir, entry)
        if not os.path.isdir(user_dir):
            continue
        req_path = os.path.join(user_dir, VENV_DIR_NAME, REQUIREMENTS_FILE)
        if os.path.isfile(req_path):
            tasks.append(restore_venv(entry))

    if tasks:
        log.info("Restoring venvs for %d users...", len(tasks))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        restored = sum(1 for r in results if r is True)
        errors = sum(1 for r in results if isinstance(r, Exception))
        log.info("Venv restore complete: %d restored, %d errors", restored, errors)
