"""
Per-user backup / restore service.

Lets each authenticated user export their own slice of `users/{user_id}/`
to a ZIP file, and import that ZIP back (either replacing or merging with
their current state).

Module map (relative paths inside `users/{user_id}/`):
    filesystem    → filesystem/  (docs, scripts, generated, soul content)
    conversations → conversations/  (admin chat history + attachments)
    services      → services/  (published service config + per-service tasks/conversations)
    tasks         → tasks/  (admin scheduled tasks)
    settings      → preferences.json, subagents.json, capability_prompts.json,
                    system_prompt.json, system_prompt_versions/,
                    user_profile.json, user_profile_versions/, soul/
    api_keys      → api_keys.json (decrypted to PLAINTEXT in the export and
                    re-encrypted with the destination master key on import,
                    so backups are portable across machines).

Always excluded:
    venv/, __pycache__/, .pytest_cache/, *.pyc, .DS_Store, Thumbs.db,
    api_keys.json (unless user explicitly opts in)

Media exclusion (when `include_media=False`):
    *.png, *.jpg, *.jpeg, *.gif, *.webp, *.bmp, *.svg,
    *.mp4, *.mov, *.webm, *.mkv, *.avi,
    *.mp3, *.wav, *.ogg, *.m4a, *.aac, *.silk, *.amr,
    inside any `generated/` or `query_appendix/images/` subdirectory.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from app.core.security import get_user_dir
from app.core.encryption import decrypt_value, encrypt_value
from app.core.user_api_keys import _SECRET_FIELDS, _URL_FIELDS, ALL_FIELDS

# ── Module map ─────────────────────────────────────────────────────

MODULE_PATHS: Dict[str, List[str]] = {
    "filesystem":    ["filesystem"],
    "conversations": ["conversations"],
    "services":      ["services"],
    "tasks":         ["tasks"],
    "settings": [
        "preferences.json",
        "subagents.json",
        "capability_prompts.json",
        "system_prompt.json",
        "system_prompt_versions",
        "user_profile.json",
        "user_profile_versions",
        "soul",
    ],
    # api_keys handled specially — see _export_api_keys / _import_api_keys
    "api_keys": ["api_keys.json"],
}

ALL_MODULES = list(MODULE_PATHS.keys())

# Always excluded from backups (regardless of module).
ALWAYS_SKIP_DIRS = {
    "venv", ".venv",
    "__pycache__", ".pytest_cache",
    ".git", ".hg", ".svn",
    "node_modules", "target",
}
ALWAYS_SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
ALWAYS_SKIP_SUFFIXES = {".pyc", ".pyo"}

MEDIA_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico",
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v",
    ".mp3", ".wav", ".ogg", ".m4a", ".aac", ".silk", ".amr", ".flac",
}
MEDIA_DIR_NAMES = {"generated", "images"}

# Manifest written into the ZIP as `_jellyfishbot_backup.json` so importer
# can validate origin + version.
MANIFEST_NAME = "_jellyfishbot_backup.json"
MANIFEST_VERSION = 1


# ── Helpers ────────────────────────────────────────────────────────

def _is_media_file(rel_path: str) -> bool:
    """Return True if `rel_path` looks like binary media inside a media dir."""
    suffix = os.path.splitext(rel_path)[1].lower()
    if suffix not in MEDIA_SUFFIXES:
        return False
    parts = {p.lower() for p in rel_path.replace("\\", "/").split("/")}
    return bool(parts & MEDIA_DIR_NAMES)


def _should_skip_path(rel_components: List[str]) -> bool:
    """Skip caches, venv, scm metadata anywhere in the tree."""
    for c in rel_components:
        if c in ALWAYS_SKIP_DIRS:
            return True
    return False


def _should_skip_file(name: str) -> bool:
    if name in ALWAYS_SKIP_FILES:
        return True
    suffix = os.path.splitext(name)[1].lower()
    return suffix in ALWAYS_SKIP_SUFFIXES


def _iter_module_files(
    user_dir: str,
    module: str,
    include_media: bool,
) -> Iterable[Tuple[str, str]]:
    """Yield `(absolute_path, archive_name)` pairs for a single module.

    `archive_name` is the path inside the ZIP (always forward-slashed).
    """
    if module == "api_keys":
        return  # handled separately via _export_api_keys
    rel_targets = MODULE_PATHS.get(module, [])
    for rel in rel_targets:
        abs_target = os.path.join(user_dir, rel)
        if not os.path.exists(abs_target):
            continue
        if os.path.isfile(abs_target):
            if _should_skip_file(os.path.basename(abs_target)):
                continue
            yield abs_target, rel.replace("\\", "/")
            continue
        # Directory — walk it.
        for dirpath, dirnames, filenames in os.walk(abs_target):
            dirnames[:] = [d for d in dirnames if d not in ALWAYS_SKIP_DIRS]
            for fname in filenames:
                if _should_skip_file(fname):
                    continue
                abs_path = os.path.join(dirpath, fname)
                rel_inside = os.path.relpath(abs_path, user_dir).replace("\\", "/")
                if not include_media and _is_media_file(rel_inside):
                    continue
                yield abs_path, rel_inside


def estimate_module_sizes(
    user_id: str,
    modules: List[str],
    include_media: bool,
    include_api_keys: bool,
) -> Dict[str, Dict[str, int]]:
    """Compute uncompressed `{module: {file_count, total_bytes}}` (preview)."""
    user_dir = get_user_dir(user_id)
    out: Dict[str, Dict[str, int]] = {}
    for mod in modules:
        if mod == "api_keys":
            keys_path = os.path.join(user_dir, "api_keys.json")
            present = os.path.isfile(keys_path) and include_api_keys
            out[mod] = {
                "file_count": 1 if present else 0,
                "total_bytes": os.path.getsize(keys_path) if present else 0,
            }
            continue
        fc = 0
        tb = 0
        for abs_path, _arch in _iter_module_files(user_dir, mod, include_media):
            try:
                tb += os.path.getsize(abs_path)
                fc += 1
            except OSError:
                pass
        out[mod] = {"file_count": fc, "total_bytes": tb}
    return out


# ── Export ─────────────────────────────────────────────────────────

@dataclass
class ExportResult:
    zip_path: str
    file_count: int
    total_bytes: int
    skipped: List[str] = field(default_factory=list)
    modules: List[str] = field(default_factory=list)


def _export_api_keys_into_zip(zf: zipfile.ZipFile, user_id: str) -> bool:
    """Write a PLAINTEXT api_keys.json into the ZIP.

    Plaintext is safe inside the ZIP only because the user is responsible
    for the destination of this file. We do this so the backup is portable
    across machines (the ENC: tokens are encrypted with the source machine's
    master key in `data/encryption.key`, which is NOT in the backup).
    """
    keys_path = os.path.join(get_user_dir(user_id), "api_keys.json")
    if not os.path.isfile(keys_path):
        return False
    try:
        with open(keys_path, "r", encoding="utf-8") as f:
            stored = json.load(f)
    except Exception:
        return False
    plain: Dict[str, str] = {}
    for field_name, value in stored.items():
        if not value or not isinstance(value, str):
            continue
        if field_name in _SECRET_FIELDS:
            try:
                plain[field_name] = decrypt_value(value)
            except Exception:
                # Couldn't decrypt — skip rather than leak ENC: ciphertext
                # that the destination machine can't read.
                continue
        elif field_name in _URL_FIELDS:
            plain[field_name] = value
    payload = {
        "_warning": "PLAINTEXT API KEYS — keep this backup ZIP private!",
        "keys": plain,
    }
    zf.writestr(
        "api_keys.PLAINTEXT.json",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )
    return True


def export_user_data(
    user_id: str,
    modules: List[str],
    include_media: bool = True,
    include_api_keys: bool = False,
) -> ExportResult:
    """Build a ZIP file in a temp directory and return its absolute path.

    Caller is responsible for streaming + deleting the file.
    """
    user_dir = get_user_dir(user_id)
    if not os.path.isdir(user_dir):
        raise FileNotFoundError(f"user dir does not exist: {user_dir}")

    selected = [m for m in modules if m in MODULE_PATHS]
    if not selected:
        raise ValueError("no valid modules selected")

    fd, zip_path = tempfile.mkstemp(prefix=f"jfb-export-{user_id}-", suffix=".zip")
    os.close(fd)

    file_count = 0
    total_bytes = 0
    skipped: List[str] = []

    with zipfile.ZipFile(
        zip_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as zf:
        # Manifest first so it's easy to peek without unpacking.
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "kind": "jellyfishbot-user-backup",
            "user_id": user_id,
            "created_at_unix": int(time.time()),
            "modules": selected,
            "include_media": include_media,
            "include_api_keys": include_api_keys,
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))

        for mod in selected:
            if mod == "api_keys":
                if include_api_keys and _export_api_keys_into_zip(zf, user_id):
                    file_count += 1
                continue
            for abs_path, archive_name in _iter_module_files(user_dir, mod, include_media):
                try:
                    zf.write(abs_path, arcname=archive_name)
                    file_count += 1
                    total_bytes += os.path.getsize(abs_path)
                except (OSError, PermissionError) as e:
                    skipped.append(f"{archive_name} ({e})")

    return ExportResult(
        zip_path=zip_path,
        file_count=file_count,
        total_bytes=total_bytes,
        skipped=skipped,
        modules=selected,
    )


# ── Import ─────────────────────────────────────────────────────────

@dataclass
class ImportResult:
    mode: str
    files_written: int = 0
    files_skipped: int = 0
    api_keys_imported: int = 0
    backup_snapshot_path: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def _safe_extract_target(user_dir: str, archive_name: str) -> Optional[str]:
    """Resolve `archive_name` to an absolute path under `user_dir`, refusing
    any path that would escape (zip-slip protection)."""
    archive_name = archive_name.replace("\\", "/").lstrip("/")
    if not archive_name or archive_name.endswith("/"):
        return None
    target = os.path.normpath(os.path.join(user_dir, archive_name))
    if not (target == user_dir or target.startswith(user_dir + os.sep)):
        return None
    return target


def _module_for_path(rel_path: str) -> Optional[str]:
    """Reverse-lookup which module a relative path belongs to."""
    parts = rel_path.replace("\\", "/").split("/")
    head = parts[0]
    for mod, paths in MODULE_PATHS.items():
        for p in paths:
            if head == p or head == p.split("/")[0]:
                return mod
    return None


def _wipe_modules(user_dir: str, modules: List[str]) -> str:
    """Move the selected modules' files to a side-snapshot dir and return its path.

    Used by overwrite-mode imports so we can recover if extraction fails or the
    user changes their mind.
    """
    snapshot_dir = user_dir + f".pre-restore-{int(time.time())}"
    os.makedirs(snapshot_dir, exist_ok=True)

    for mod in modules:
        for rel in MODULE_PATHS.get(mod, []):
            src = os.path.join(user_dir, rel)
            if not os.path.exists(src):
                continue
            dst = os.path.join(snapshot_dir, rel)
            os.makedirs(os.path.dirname(dst) or snapshot_dir, exist_ok=True)
            shutil.move(src, dst)
    return snapshot_dir


def _import_api_keys_from_zip(zf: zipfile.ZipFile, user_id: str) -> int:
    """Re-encrypt the plaintext api_keys.PLAINTEXT.json with the local master key."""
    name = "api_keys.PLAINTEXT.json"
    if name not in zf.namelist():
        return 0
    try:
        payload = json.loads(zf.read(name).decode("utf-8"))
    except Exception:
        return 0
    plain = payload.get("keys") or {}
    if not isinstance(plain, dict):
        return 0
    keys_path = os.path.join(get_user_dir(user_id), "api_keys.json")
    current: Dict[str, str] = {}
    if os.path.isfile(keys_path):
        try:
            with open(keys_path, "r", encoding="utf-8") as f:
                current = json.load(f)
        except Exception:
            current = {}
    imported = 0
    for k, v in plain.items():
        if k not in ALL_FIELDS or not v:
            continue
        if k in _SECRET_FIELDS:
            try:
                current[k] = encrypt_value(v)
                imported += 1
            except Exception:
                continue
        elif k in _URL_FIELDS:
            current[k] = v.rstrip("/")
            imported += 1
    os.makedirs(os.path.dirname(keys_path), exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(keys_path, current, ensure_ascii=False, indent=2)
    return imported


def import_user_data(
    user_id: str,
    zip_bytes: bytes,
    mode: str = "merge",
    modules_filter: Optional[List[str]] = None,
) -> ImportResult:
    """Restore a user's data from a backup ZIP.

    `mode`:
        - "merge"    : extract files but skip any that already exist on disk.
        - "overwrite": move existing modules to a side snapshot, then extract.
                       Returns `backup_snapshot_path` so the caller can hint
                       at recovery.

    `modules_filter`: optional list — only import these modules even if more
    are present in the ZIP.
    """
    if mode not in ("merge", "overwrite"):
        raise ValueError(f"invalid mode: {mode}")

    user_dir = get_user_dir(user_id)
    os.makedirs(user_dir, exist_ok=True)

    bio = io.BytesIO(zip_bytes)
    if not zipfile.is_zipfile(bio):
        raise ValueError("uploaded file is not a valid ZIP")
    bio.seek(0)

    result = ImportResult(mode=mode)

    with zipfile.ZipFile(bio, mode="r") as zf:
        # Validate manifest if present (don't require it — be lenient with
        # backups produced manually).
        manifest_modules: Optional[List[str]] = None
        if MANIFEST_NAME in zf.namelist():
            try:
                m = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
                if m.get("kind") != "jellyfishbot-user-backup":
                    result.warnings.append("manifest 'kind' mismatch — proceeding anyway")
                manifest_modules = list(m.get("modules") or []) or None
            except Exception:
                result.warnings.append("manifest unreadable — proceeding without it")

        # Decide which modules to actually extract.
        present_modules = set(manifest_modules or ALL_MODULES)
        if modules_filter:
            requested = set(modules_filter) & present_modules
        else:
            requested = present_modules
        # Detect api_keys presence regardless of manifest.
        if "api_keys.PLAINTEXT.json" in zf.namelist():
            requested.add("api_keys")

        # Overwrite mode → snapshot the to-be-replaced trees first.
        if mode == "overwrite":
            wipe_targets = [m for m in requested if m != "api_keys"]
            result.backup_snapshot_path = _wipe_modules(user_dir, wipe_targets)

        # Stream-extract entries.
        for info in zf.infolist():
            name = info.filename
            if name == MANIFEST_NAME:
                continue
            if name == "api_keys.PLAINTEXT.json":
                continue  # handled below
            if info.is_dir():
                continue
            rel = name.replace("\\", "/")
            mod = _module_for_path(rel)
            if mod is None or mod not in requested:
                result.files_skipped += 1
                continue
            target = _safe_extract_target(user_dir, rel)
            if target is None:
                result.warnings.append(f"unsafe path skipped: {rel}")
                continue
            if mode == "merge" and os.path.exists(target):
                result.files_skipped += 1
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info, "r") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, length=64 * 1024)
            result.files_written += 1

        # Re-encrypt and merge api_keys last, after everything else is on disk.
        if "api_keys" in requested and "api_keys.PLAINTEXT.json" in zf.namelist():
            result.api_keys_imported = _import_api_keys_from_zip(zf, user_id)

    return result
