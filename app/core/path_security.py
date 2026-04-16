"""
Unified path security utilities.

Prevents path traversal attacks by ensuring resolved paths stay within
their designated root directories.

On Windows paths are compared case-insensitively (NTFS default);
on Linux/macOS the comparison is case-sensitive.
"""

import os
import sys
from pathlib import Path

_CASE_INSENSITIVE = sys.platform == "win32"


def _normalise(p: str) -> str:
    return p.lower() if _CASE_INSENSITIVE else p


def ensure_within(target: str, root: str) -> str:
    """
    Resolve *target* and verify it is inside (or equal to) *root*.

    Returns the resolved absolute path string on success.
    Raises ``PermissionError`` if the target escapes the root.
    """
    root_resolved = str(Path(root).resolve())
    target_resolved = str(Path(target).resolve())
    nr = _normalise(root_resolved)
    nt = _normalise(target_resolved)
    if nt == nr or nt.startswith(nr + os.sep):
        return target_resolved
    raise PermissionError("Path escapes allowed root directory")


def safe_join(root: str, untrusted: str) -> str:
    """
    Join *root* with an untrusted relative path, then verify the result
    stays within *root*.

    Strips leading slashes / backslashes from *untrusted* and normalises
    separators before joining.
    """
    clean = untrusted.replace("\\", "/").lstrip("/")
    joined = os.path.join(root, clean)
    return ensure_within(joined, root)
