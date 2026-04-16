"""
Sandbox wrapper — executed by script_runner.py as the *real* entry point
instead of the user script directly.

Usage:
    python _sandbox_wrapper.py
        --allowed-read  /dir1|/dir2
        --allowed-write /dir3
        --script        /path/to/user_script.py
        [-- user_arg1 user_arg2 ...]

The wrapper monkey-patches builtins.open, io.open, and several os.*
functions so that all file I/O is restricted to the allowed directories.
Then it runs the user script via runpy.run_path in a clean namespace.
"""

import argparse
import builtins
import io
import os
import sys
import runpy


# ── Parse arguments ─────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--allowed-read", required=True)
    p.add_argument("--allowed-write", required=True)
    p.add_argument("--script", required=True)
    p.add_argument("rest", nargs="*")
    return p.parse_args()


_args = _parse_args()
_ALLOWED_READ = [os.path.realpath(d) for d in _args.allowed_read.split("|") if d]
_ALLOWED_WRITE = [os.path.realpath(d) for d in _args.allowed_write.split("|") if d]
_SCRIPT_PATH = os.path.realpath(_args.script)
_USER_ARGS = _args.rest

_CASE_INSENSITIVE = sys.platform == "win32"


# ── Python system directories (read-only exemption) ─────────────────
# Allow reading from Python stdlib and site-packages so that
# `import matplotlib` etc. can load their .py/.pyc/.so files.

_PYTHON_READ_ROOTS: list = []
for _p in (sys.prefix, sys.base_prefix, sys.exec_prefix):
    if _p:
        _rp = os.path.realpath(_p)
        if _rp not in _PYTHON_READ_ROOTS:
            _PYTHON_READ_ROOTS.append(_rp)

try:
    import site as _site
    for _sp in _site.getsitepackages():
        _rp = os.path.realpath(_sp)
        if _rp not in _PYTHON_READ_ROOTS:
            _PYTHON_READ_ROOTS.append(_rp)
    _usp = _site.getusersitepackages()
    if _usp:
        _rp = os.path.realpath(_usp)
        if _rp not in _PYTHON_READ_ROOTS:
            _PYTHON_READ_ROOTS.append(_rp)
except Exception:
    pass

# ── System shared directories (read-only exemption) ──────────────────
# Libraries like matplotlib scan system font/SSL/locale directories.
# These are safe to read and not user-data paths.
_SYSTEM_READ_DIRS: list = []
for _d in [
    "/usr/share",               # fonts, locale, mime, misc shared data
    "/usr/local/share",         # locally installed shared data
    "/usr/X11R6",               # X11 fonts (matplotlib font_manager)
    "/etc/fonts",               # fontconfig
    "/etc/ssl",                 # SSL certificates (requests/urllib3)
    "/etc/mime.types",          # MIME type database
    "/System/Library/Fonts",    # macOS system fonts
    "/Library/Fonts",           # macOS user fonts
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
]:
    _rp = os.path.realpath(_d)
    if _rp not in _SYSTEM_READ_DIRS:
        _SYSTEM_READ_DIRS.append(_rp)

# ── Temp directory (library internal writes) ────────────────────────
# Many libraries (matplotlib, pandas, PIL) write temp/cache files internally.
import tempfile as _tempfile
_TEMP_DIR = os.path.realpath(_tempfile.gettempdir())

# Redirect matplotlib config to temp so it doesn't need ~/.matplotlib/
os.environ["MPLCONFIGDIR"] = os.path.join(_TEMP_DIR, "mpl_sandbox")


# ── Path checking ───────────────────────────────────────────────────

def _norm(p: str) -> str:
    return p.lower() if _CASE_INSENSITIVE else p


def _is_within(path: str, roots: list) -> bool:
    rp = _norm(os.path.realpath(path))
    for root in roots:
        nr = _norm(root)
        if rp == nr or rp.startswith(nr + os.sep):
            return True
    return False


def _check_read(path):
    if not _is_within(path, _ALLOWED_READ + _ALLOWED_WRITE + _PYTHON_READ_ROOTS + _SYSTEM_READ_DIRS):
        rp = os.path.realpath(path)
        raise PermissionError(
            f"Sandbox: read access denied — {path} (resolved: {rp})"
        )


def _check_write(path):
    if not _is_within(path, _ALLOWED_WRITE + [_TEMP_DIR]):
        rp = os.path.realpath(path)
        print(f"[Sandbox] DENIED write: path={path} realpath={rp} | cwd={os.getcwd()} | allowed={_ALLOWED_WRITE}", file=sys.stderr)
        raise PermissionError(
            f"Sandbox: write access denied — {path} (resolved: {rp})"
        )


def _check_open_mode(path, mode="r"):
    if any(c in mode for c in "wxa+"):
        _check_write(path)
    else:
        _check_read(path)


# ── Monkey-patches ──────────────────────────────────────────────────

_original_open = builtins.open
_original_io_open = io.open


def _restricted_open(file, mode="r", *a, **kw):
    _check_open_mode(str(file), str(mode))
    return _original_open(file, mode, *a, **kw)


builtins.open = _restricted_open
io.open = _restricted_open

# Patch os functions that enumerate or navigate the filesystem

_original_listdir = os.listdir
_original_scandir = os.scandir


def _restricted_listdir(path="."):
    _check_read(str(path))
    return _original_listdir(path)


def _restricted_scandir(path="."):
    _check_read(str(path))
    return _original_scandir(path)


os.listdir = _restricted_listdir
os.scandir = _restricted_scandir


def _restricted_walk(top, **kw):
    _check_read(str(top))
    return _original_walk(top, **kw)


_original_walk = os.walk
os.walk = _restricted_walk


def _blocked_chdir(*a, **kw):
    raise PermissionError("Sandbox: os.chdir is not allowed")


os.chdir = _blocked_chdir
if hasattr(os, "fchdir"):
    os.fchdir = _blocked_chdir

# Block os.environ access
os.environ = os.environ.copy()  # detach from real env (already filtered by script_runner)


# ── Set up sys.argv for the user script ─────────────────────────────

sys.argv = [_SCRIPT_PATH] + _USER_ARGS

# ── Clean up our own namespace references ───────────────────────────
# This prevents the user script from accessing _original_open etc.
# via module globals.  runpy.run_path runs in its own namespace anyway,
# but we delete them here as defense-in-depth.

del _args, _parse_args

# ── Run the user script ─────────────────────────────────────────────

try:
    runpy.run_path(_SCRIPT_PATH, run_name="__main__")
except SystemExit:
    raise
except PermissionError as e:
    print(f"[Sandbox] {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"[Error] {e}", file=sys.stderr)
    sys.exit(1)
