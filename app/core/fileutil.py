"""Atomic file-write helpers.

``atomic_json_save`` writes JSON to a temporary file in the same directory,
then uses ``os.replace`` (atomic on both POSIX and Windows) to swap it into
place.  If the process crashes or the disk fills up mid-write, the original
file is left intact.
"""

import json
import os
import tempfile
from typing import Any


def atomic_json_save(path: str, data: Any, **kwargs) -> None:
    """Atomically write *data* as JSON to *path*.

    Keyword arguments are forwarded to ``json.dump``
    (e.g. ``ensure_ascii=False, indent=2``).
    """
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **kwargs)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
