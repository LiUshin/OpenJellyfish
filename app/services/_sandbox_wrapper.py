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
# 大量第三方库（matplotlib 字体扫描、requests SSL CA、PIL 图像 codec、
# locale/timezone/mime 等）会在 import 阶段就遍历系统目录。把这些路径
# 加进白名单后，沙盒仍然只允许「读」，写依然走 _ALLOWED_WRITE。
# 用户家目录里的敏感文件（~/.ssh、~/.aws、~/Library/Application Support
# 等）不在白名单里，依旧受保护。
_SYSTEM_READ_DIRS: list = []
for _d in [
    # Unix-like 系统库根目录
    "/usr",                     # 含 /usr/share, /usr/local, /usr/X11, /usr/lib 等
    "/etc",                     # 配置文件（fontconfig, ssl, mime 等）
    "/opt",                     # /opt/X11, /opt/homebrew, /opt/local 等
    "/bin", "/sbin",            # 偶尔被库 stat 检测
    "/lib", "/lib64",           # 共享库（Linux）
    "/var/cache/fontconfig",    # fontconfig 字体缓存（Linux）
    # macOS 系统/库目录
    "/Library",
    "/System",
    "/Applications",            # 偶尔被字体管理器扫描
    "/private/etc",             # macOS 上 /etc 实际指向这里
    "/private/var/folders",     # 系统级临时目录的 realpath
    # 用户家目录里少数已知的「字体/字典/locale」类公开数据，仅供扫描
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
    os.path.expanduser("~/Library/Fonts"),
]:
    _rp = os.path.realpath(_d)
    if _rp not in _SYSTEM_READ_DIRS:
        _SYSTEM_READ_DIRS.append(_rp)

# ── Temp directory (library internal writes) ────────────────────────
# Many libraries (matplotlib, pandas, PIL) write temp/cache files internally.
import tempfile as _tempfile
_TEMP_DIR = os.path.realpath(_tempfile.gettempdir())

# matplotlib 配置/缓存目录（沙盒内可写）。预先 mkdir 并设环境变量，
# 否则 font_manager 第一次启动会去家目录写缓存被拦截。
_MPL_CFG_DIR = os.path.join(_TEMP_DIR, "mpl_sandbox")
try:
    os.makedirs(_MPL_CFG_DIR, exist_ok=True)
except OSError:
    pass
os.environ["MPLCONFIGDIR"] = _MPL_CFG_DIR
# 强制无头后端，避免脚本里 plt.show() / 后端探测触发 Tk/Qt/Cocoa 调用。
os.environ.setdefault("MPLBACKEND", "Agg")
# 让 fontconfig / PIL 等也把缓存写到沙盒可写目录，减少越权读写。
os.environ.setdefault("FONTCONFIG_PATH", "/etc/fonts")
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(_TEMP_DIR, "xdg_cache_sandbox"))
try:
    os.makedirs(os.environ["XDG_CACHE_HOME"], exist_ok=True)
except OSError:
    pass


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


# 防递归 guard：os.path.realpath 内部会调 os.readlink；而我们 patch 过的
# os.readlink 又会调 _check_read → _is_within → os.path.realpath …
# 用 threading.local 标记「正在检查中」，递归进来直接放行（外层调用兜底）。
import threading as _threading
_check_guard = _threading.local()


def _is_read_allowed(path) -> bool:
    # 递归调用（来自 realpath → patched readlink → _check_read）：
    # 直接放行，由最外层的检查统一兜底，避免无限递归。
    if getattr(_check_guard, "busy", False):
        return True
    _check_guard.busy = True
    try:
        return _is_within(
            path,
            _ALLOWED_READ + _ALLOWED_WRITE + _PYTHON_READ_ROOTS + _SYSTEM_READ_DIRS,
        )
    finally:
        _check_guard.busy = False


def _safe_realpath(path) -> str:
    """计算 realpath 但不触发 patched readlink 的递归检查；失败回退到原始 path。"""
    if getattr(_check_guard, "busy", False):
        return str(path)
    _check_guard.busy = True
    try:
        return os.path.realpath(path)
    except Exception:
        return str(path)
    finally:
        _check_guard.busy = False


def _check_read(path):
    if not _is_read_allowed(path):
        raise PermissionError(
            f"Sandbox: read access denied — {path} (resolved: {_safe_realpath(path)})"
        )


def _is_write_allowed(path) -> bool:
    if getattr(_check_guard, "busy", False):
        return True
    _check_guard.busy = True
    try:
        return _is_within(path, _ALLOWED_WRITE + [_TEMP_DIR])
    finally:
        _check_guard.busy = False


def _check_write(path):
    if not _is_write_allowed(path):
        rp = _safe_realpath(path)
        print(
            f"[Sandbox] DENIED write: path={path} realpath={rp} | "
            f"cwd={os.getcwd()} | allowed={_ALLOWED_WRITE}",
            file=sys.stderr,
        )
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
_original_walk = os.walk


# 关键设计：listdir / scandir / walk 是「扫描型」调用，第三方库
# （matplotlib font_manager, PIL, locale 等）经常会主动遍历一堆系统
# 目录。如果对越权路径直接抛 PermissionError，整个 import 链就挂了。
# 折中策略：越权时静默返回空集，让库以为「目录是空的」从而跳过，而
# 不影响真正要保护的「打开文件读内容」（_check_read 在 open/os.open
# 等场景下仍然会硬抛错）。

def _restricted_listdir(path="."):
    if not _is_read_allowed(path):
        return []
    return _original_listdir(path)


class _EmptyScandir:
    """模拟 os.scandir() 的上下文管理器，返回空迭代器。"""
    def __iter__(self):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        return None


def _restricted_scandir(path="."):
    if not _is_read_allowed(path):
        return _EmptyScandir()
    return _original_scandir(path)


def _restricted_walk(top, **kw):
    if not _is_read_allowed(top):
        return iter(())
    return _original_walk(top, **kw)


os.listdir = _restricted_listdir
os.scandir = _restricted_scandir
os.walk = _restricted_walk


def _blocked_chdir(*a, **kw):
    raise PermissionError("Sandbox: os.chdir is not allowed")


os.chdir = _blocked_chdir
if hasattr(os, "fchdir"):
    os.fchdir = _blocked_chdir


# ── 绝对危险函数：无合法用例，一律阻断 ──────────────────────────────
# 覆盖 os.* 上的引用；由于 os.__dict__[name] 和 os.name 指向同一对象，
# 下标访问 os.__dict__["system"] 也会拿到阻断版本（AST 已禁用 __dict__，
# 此处为运行时纵深防御：即使静态检查因新绕过失效，调用仍然抛错）。

def _make_blocked(func_name: str):
    def _blocked(*a, **kw):
        raise PermissionError(
            f"Sandbox: os.{func_name} is not allowed (dangerous: may spawn process / modify ownership / bypass isolation)"
        )
    _blocked.__name__ = f"_blocked_{func_name}"
    return _blocked


_ABSOLUTELY_DANGEROUS = [
    "system", "popen",
    "execl", "execle", "execlp", "execlpe",
    "execv", "execve", "execvp", "execvpe",
    "execveat", "fexecve",
    "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "posix_spawn", "posix_spawnp",
    "fork", "forkpty",
    "kill", "killpg",
    "chown", "fchown", "lchown",
    "setuid", "setgid", "seteuid", "setegid",
    "setreuid", "setregid", "setresuid", "setresgid",
    "chroot",
    "pipe", "pipe2",
    "dup", "dup2",
]
for _name in _ABSOLUTELY_DANGEROUS:
    if hasattr(os, _name):
        setattr(os, _name, _make_blocked(_name))


# ── 条件危险函数：走写入白名单 ────────────────────────────────────
# remove / rename / mkdir 等写文件操作，合法脚本（图表输出、整理文件）
# 会用到。走 _check_write 即可，路径必须在 allowed_write 内。

def _wrap_write1(name: str):
    """包装单路径写操作：os.remove(path) / os.mkdir(path) 等"""
    orig = getattr(os, name, None)
    if orig is None:
        return

    def _wrapped(path, *a, **kw):
        _check_write(str(path))
        return orig(path, *a, **kw)

    _wrapped.__name__ = f"_restricted_{name}"
    setattr(os, name, _wrapped)


def _wrap_write2(name: str):
    """包装双路径写操作：os.rename(src, dst) / os.link(src, dst) 等"""
    orig = getattr(os, name, None)
    if orig is None:
        return

    def _wrapped(src, dst, *a, **kw):
        _check_write(str(src))
        _check_write(str(dst))
        return orig(src, dst, *a, **kw)

    _wrapped.__name__ = f"_restricted_{name}"
    setattr(os, name, _wrapped)


for _n in ("remove", "unlink", "rmdir", "removedirs",
           "mkdir", "makedirs",
           "chmod", "fchmod", "lchmod",
           "truncate",
           "mkfifo", "mknod",
           "utime"):
    _wrap_write1(_n)

for _n in ("rename", "renames", "replace",
           "link", "symlink"):
    _wrap_write2(_n)


# ── 低层 FD / 链接读取：也走白名单 ─────────────────────────────────

_original_os_open = os.open


def _restricted_os_open(path, flags, *a, **kw):
    # 粗粒度：任何带写标志位的 os.open 都走写检查
    _WRITE_FLAGS = (
        getattr(os, "O_WRONLY", 0) | getattr(os, "O_RDWR", 0)
        | getattr(os, "O_CREAT", 0) | getattr(os, "O_APPEND", 0)
        | getattr(os, "O_TRUNC", 0) | getattr(os, "O_EXCL", 0)
    )
    if flags & _WRITE_FLAGS:
        _check_write(str(path))
    else:
        _check_read(str(path))
    return _original_os_open(path, flags, *a, **kw)


os.open = _restricted_os_open

if hasattr(os, "readlink"):
    _original_readlink = os.readlink

    def _restricted_readlink(path, *a, **kw):
        _check_read(str(path))
        return _original_readlink(path, *a, **kw)

    os.readlink = _restricted_readlink


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
