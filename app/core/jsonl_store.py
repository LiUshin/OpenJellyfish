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
from typing import Any, Dict, Iterable, List, Optional


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def append_jsonl(path: str, record: Dict[str, Any]) -> None:
    """Append a single JSON record as one line.

    Creates the parent directory if needed.  Uses ``ensure_ascii=False``
    so Chinese stays human-readable when the file is inspected manually.
    """
    _ensure_dir(path)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with open(path, "ab") as f:
        f.write(line.encode("utf-8"))
        f.write(b"\n")


def append_jsonl_many(path: str, records: Iterable[Dict[str, Any]]) -> int:
    """Append many records in one open()/write() — used by migration."""
    _ensure_dir(path)
    count = 0
    buf = io.BytesIO()
    for rec in records:
        buf.write(json.dumps(rec, ensure_ascii=False, default=str).encode("utf-8"))
        buf.write(b"\n")
        count += 1
    if count == 0:
        return 0
    with open(path, "ab") as f:
        f.write(buf.getvalue())
    return count


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
