"""
Local disk cache in front of S3-compatible storage.

Only used by app/storage/s3.py — local mode never touches this module.

Two tiers:

1. **Metadata cache** (in-process, short TTL) — results of ``head_object`` and
   ``list_objects_v2``. Kills the request storm an agent turn produces when it
   repeatedly calls ls / exists / read on the same paths within a few seconds.

2. **Content cache** (on-disk, ETag-keyed, size-capped LRU) — object bytes.
   Because the cache key includes the ETag, a stale entry can never be served
   as fresh: either the ETag matches (content is byte-identical) or we miss.

Beyond the metadata TTL we keep a long-lived ETag *hint* per key so the next
read can issue a conditional GET (``If-None-Match``). A 304 costs one round
trip with no body — for a 50MB document that is the difference between 50MB
and ~200 bytes of transfer.

Objects are stored read-only (0444) because script sandboxes hardlink them
into their working directory.
"""

import errno
import hashlib
import logging
import os
import shutil
import tempfile
import threading
import time
from typing import Any, Callable, Optional

from app.core.settings import ROOT_DIR

_log = logging.getLogger("storage.cache")

_CACHE_ROOT = os.path.join(ROOT_DIR, "data", "s3cache")
_OBJECTS_DIR = os.path.join(_CACHE_ROOT, "objects")
_SCRATCH_DIR = os.path.join(_CACHE_ROOT, "scratch")

_MISSING = object()  # negative cache marker


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw, 10)
    except ValueError:
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def cache_enabled() -> bool:
    return _env_flag("S3_CACHE_ENABLED", True)


def meta_ttl() -> float:
    return float(_env_int("S3_META_TTL_SECONDS", 5))


def max_cache_bytes() -> int:
    return _env_int("S3_CACHE_MAX_MB", 2048) * 1024 * 1024


def max_cached_file_bytes() -> int:
    """Objects larger than this are streamed through without being cached."""
    return _env_int("S3_CACHE_MAX_FILE_MB", 64) * 1024 * 1024


# ──────────────────────────── metadata cache ────────────────────────────

_meta_lock = threading.RLock()
_meta: dict[Any, tuple[float, Any]] = {}
_etag_hint: dict[str, str] = {}

_ETAG_HINT_CAP = 50_000


def meta_get(cache_key: Any) -> Any:
    """Return the cached value, ``_MISSING`` for a cached negative, or None."""
    if not cache_enabled():
        return None
    now = time.monotonic()
    with _meta_lock:
        entry = _meta.get(cache_key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < now:
            _meta.pop(cache_key, None)
            return None
        return value


def meta_put(cache_key: Any, value: Any) -> None:
    if not cache_enabled():
        return
    with _meta_lock:
        _meta[cache_key] = (time.monotonic() + meta_ttl(), value)


def meta_missing() -> Any:
    return _MISSING


def is_missing(value: Any) -> bool:
    return value is _MISSING


def invalidate_key(key: str) -> None:
    """Drop metadata for one object plus any listing that could contain it."""
    with _meta_lock:
        _meta.pop(("head", key), None)
        _etag_hint.pop(key, None)
        stale = [
            ck for ck in _meta
            if ck[0] in ("list", "listr") and key.startswith(ck[1])
        ]
        for ck in stale:
            _meta.pop(ck, None)


def invalidate_prefix(prefix: str) -> None:
    """Drop everything at or below a prefix (used after recursive ops)."""
    with _meta_lock:
        stale = [
            ck for ck in _meta
            if (ck[0] == "head" and ck[1].startswith(prefix))
            or (ck[0] in ("list", "listr") and (
                ck[1].startswith(prefix) or prefix.startswith(ck[1])
            ))
        ]
        for ck in stale:
            _meta.pop(ck, None)
        for k in [k for k in _etag_hint if k.startswith(prefix)]:
            _etag_hint.pop(k, None)


def get_etag_hint(key: str) -> Optional[str]:
    with _meta_lock:
        return _etag_hint.get(key)


def set_etag_hint(key: str, etag: str) -> None:
    if not etag:
        return
    with _meta_lock:
        if len(_etag_hint) >= _ETAG_HINT_CAP:
            _etag_hint.clear()
        _etag_hint[key] = etag


# ──────────────────────────── content cache ────────────────────────────

_size_lock = threading.RLock()
_total_bytes: Optional[int] = None  # lazily computed on first use


def _normalize_etag(etag: str) -> str:
    return (etag or "").strip('"').replace("/", "_")


def _object_path(key: str, etag: str) -> str:
    digest = hashlib.sha256(
        f"{key}\n{_normalize_etag(etag)}".encode("utf-8")
    ).hexdigest()
    return os.path.join(_OBJECTS_DIR, digest[:2], digest)


def _ensure_total_bytes() -> int:
    global _total_bytes
    with _size_lock:
        if _total_bytes is None:
            total = 0
            for dirpath, _dirs, filenames in os.walk(_OBJECTS_DIR):
                for fn in filenames:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, fn))
                    except OSError:
                        pass
            _total_bytes = total
        return _total_bytes


def _account(delta: int) -> None:
    global _total_bytes
    with _size_lock:
        if _total_bytes is not None:
            _total_bytes = max(0, _total_bytes + delta)


def _evict_if_needed() -> None:
    """Trim the cache to 90% of the cap, oldest-touched first."""
    cap = max_cache_bytes()
    if _ensure_total_bytes() <= cap:
        return
    target = int(cap * 0.9)
    entries: list[tuple[float, int, str]] = []
    for dirpath, _dirs, filenames in os.walk(_OBJECTS_DIR):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, full))
    entries.sort()
    freed = 0
    over = _ensure_total_bytes() - target
    for _mtime, size, full in entries:
        if freed >= over:
            break
        try:
            os.chmod(full, 0o644)
            os.remove(full)
        except OSError:
            continue
        freed += size
        _account(-size)
    _log.info("s3cache evicted %.1f MB", freed / 1024 / 1024)


def object_path_if_cached(key: str, etag: str) -> Optional[str]:
    """Return the on-disk path for this exact (key, etag), touching its LRU
    timestamp. Returns None on a miss."""
    if not cache_enabled() or not etag:
        return None
    path = _object_path(key, etag)
    if not os.path.isfile(path):
        return None
    try:
        os.utime(path, None)
    except OSError:
        pass
    return path


def read_cached(key: str, etag: str) -> Optional[bytes]:
    path = object_path_if_cached(key, etag)
    if path is None:
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def store_bytes(key: str, etag: str, data: bytes) -> Optional[str]:
    if not cache_enabled() or not etag:
        return None
    if len(data) > max_cached_file_bytes():
        return None
    path = _object_path(key, etag)
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o444)
        os.replace(tmp, path)
    except OSError as e:
        _log.debug("s3cache store failed for %s: %s", key, e)
        return None
    _account(len(data))
    _evict_if_needed()
    set_etag_hint(key, etag)
    return path


def store_via_download(
    key: str, etag: str, size: int, download: Callable[[str], None],
) -> Optional[str]:
    """Download straight into the cache (never buffering in memory) and return
    the cached path. Returns None when the object is too large to cache."""
    if not cache_enabled() or not etag or size > max_cached_file_bytes():
        return None
    path = _object_path(key, etag)
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp")
    os.close(fd)
    try:
        download(tmp)
        actual = os.path.getsize(tmp)
        os.chmod(tmp, 0o444)
        os.replace(tmp, path)
    except Exception as e:
        try:
            os.chmod(tmp, 0o644)
            os.remove(tmp)
        except OSError:
            pass
        _log.debug("s3cache download failed for %s: %s", key, e)
        raise e
    _account(actual)
    _evict_if_needed()
    set_etag_hint(key, etag)
    return path


def materialize(cached_path: str, dest: str, *, writable: bool = False) -> None:
    """Place cached content at ``dest``.

    Read-only consumers get a hardlink (free); anything that may be written to
    gets a real copy so the sandbox cannot corrupt the shared cache entry.
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if writable:
        shutil.copyfile(cached_path, dest)
        os.chmod(dest, 0o644)
        return
    try:
        os.link(cached_path, dest)
    except OSError as e:
        # EXDEV (different filesystem) / EPERM (hardlinks disabled) / EMLINK
        if e.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK):
            _log.debug("hardlink failed for %s: %s", dest, e)
        shutil.copyfile(cached_path, dest)


def new_scratch_dir(prefix: str = "run_") -> str:
    """Create a temp dir on the same filesystem as the cache so that
    ``materialize`` can hardlink instead of copy."""
    os.makedirs(_SCRATCH_DIR, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=_SCRATCH_DIR)


def stats() -> dict:
    return {
        "enabled": cache_enabled(),
        "root": _CACHE_ROOT,
        "bytes": _ensure_total_bytes(),
        "max_bytes": max_cache_bytes(),
        "meta_entries": len(_meta),
        "etag_hints": len(_etag_hint),
    }
