"""JSONL append-only storage helpers.

This module is the shared backbone for the JSONL-based "flow-style" data
introduced in 2026-04-23 (replacing the per-message full-rewrite of
conversation JSONs and per-step rewrites of scheduler task JSONs).

Design notes
------------
* Append uses ``open(path, "ab")`` + a single ``write`` of UTF-8 encoded
  ``json.dumps(...) + "\\n"``.  We **don't** ``fsync`` each line — the
  cost defeats the whole point of switching to JSONL.  Crash safety here
  is the same as ordinary log files: the most recent unsynced lines may
  be lost on hard power-cut, but the rest of the file (and other
  conversations) stay intact.  The sibling ``meta.json`` IS written via
  ``atomic_json_save`` so the metadata can never be corrupted.
* A line that fails ``json.loads`` is silently skipped on read — this
  keeps a single garbled write from killing the whole conversation.
* ``read_jsonl_tail`` does a real ``seek``-from-end scan so getting the
  last N messages from a 100 MB conversation doesn't pull the whole file
  into memory.  Useful for short-term-memory injection in scheduler /
  inbox prompts.
"""

from __future__ import annotations

import io
import json
import os
try:
    import fcntl
except ImportError:  # Preserve ordinary chat storage on Windows too.
    fcntl = None
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


@contextmanager
def _writer_lock(path):
    _ensure_dir(path)
    with open(path + '.lock', 'ab') as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:
            import msvcrt
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)
            else:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _append_records(path, records, *, durable=False, event_id=None):
    with _writer_lock(path):
        with open(path, 'a+b') as f:
            if event_id:
                f.seek(0)
                for line in f:
                    try:
                        if json.loads(line).get('event_id') == event_id:
                            os.fsync(f.fileno())
                            parent = os.open(os.path.dirname(path) or '.', os.O_RDONLY)
                            try:
                                os.fsync(parent)
                            finally:
                                os.close(parent)
                            return False
                    except (ValueError, AttributeError):
                        pass
            f.seek(0, os.SEEK_END)
            if f.tell():
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b'\n':
                    f.write(b'\n')  # isolate a torn final record from new data
            for record in records:
                f.write((json.dumps(record, ensure_ascii=False, default=str) + '\n').encode('utf-8'))
            if durable:
                f.flush()
                os.fsync(f.fileno())
        if durable:
            fd = os.open(os.path.dirname(path) or '.', os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    return True


def append_jsonl(path: str, record: Dict[str, Any]) -> None:
    _append_records(path, [record])


def append_jsonl_once(path: str, record: Dict[str, Any], event_id: str) -> bool:
    """Durable projection with an event marker in the same fsynced record."""
    return _append_records(path, [{**record, 'event_id': event_id}], durable=True, event_id=event_id)


def append_jsonl_many(path: str, records: Iterable[Dict[str, Any]]) -> int:
    records = list(records)
    if records:
        _append_records(path, records)
    return len(records)


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    """Read every line as JSON, dropping malformed entries."""
    if not os.path.isfile(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def count_jsonl_lines(path: str) -> int:
    """Count non-empty lines without parsing each one."""
    if not os.path.isfile(path):
        return 0
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            n += chunk.count(b"\n")
    # Handle missing trailing newline (treat last partial line as one record
    # if the file is non-empty).
    if os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                n += 1
    return n


def read_jsonl_tail(path: str, last_n: int) -> List[Dict[str, Any]]:
    """Return the last ``last_n`` parsed records without reading the
    whole file into memory.

    Walks backwards from EOF in 64 KB chunks accumulating until at least
    ``last_n + 1`` newlines are seen, then parses just the trailing
    portion.  For huge files this is O(last_n) instead of O(file_size).
    """
    if last_n <= 0 or not os.path.isfile(path):
        return []
    size = os.path.getsize(path)
    if size == 0:
        return []
    chunk = 64 * 1024
    needed = last_n + 1
    with open(path, "rb") as f:
        buf = b""
        pos = size
        while pos > 0 and buf.count(b"\n") < needed:
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
    text = buf.decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    out: List[Dict[str, Any]] = []
    for ln in lines[-last_n:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def rewrite_jsonl(path: str, records: List[Dict[str, Any]]) -> None:
    with _writer_lock(path):
        _rewrite_jsonl_locked(path, records)


def _rewrite_jsonl_locked(path: str, records: List[Dict[str, Any]]) -> None:
    """Atomically rewrite the whole file (used for delete / cap ops).

    Goes through ``atomic_json_save``-style temp+rename so a crash mid
    rewrite doesn't lose the previous content.
    """
    import tempfile

    _ensure_dir(path)
    dir_name = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, default=str).encode("utf-8"))
                f.write(b"\n")
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def safe_load_json(path: str) -> Optional[Dict[str, Any]]:
    """Load JSON or return None on missing / corrupt — convenience used
    by meta.json sidecars where falling back to a rebuild is acceptable."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
