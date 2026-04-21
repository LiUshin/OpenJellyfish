"""
Python 脚本执行器（安全加固版）

功能：
- 在用户的 scripts/ 目录下执行 Python 脚本
- 支持传入参数
- 超时控制
- 捕获 stdout/stderr

安全措施：
- realpath 解析符号链接，防止路径穿越
- 显式拒绝符号链接文件
- AST 静态分析，阻止危险调用（os.system, subprocess, exec 等）
- 环境变量白名单，不泄露 API 密钥等敏感信息
- 进程资源限制（内存 1GB、子进程/线程 256 个）
- BLAS / OpenMP 线程数限制（OPENBLAS_NUM_THREADS 等环境变量强制注入）
- 全局执行排队（BoundedSemaphore，默认并发 4，超时 180s 返回繁忙）
"""

import os
import sys
import ast
import time
import logging
import subprocess
import platform
import threading
from typing import Dict, Any, Optional, List, Set

_log = logging.getLogger("script_runner")


# 默认使用当前 venv 的 Python 解释器；per-user venv overrides via parameter
PYTHON_EXECUTABLE = sys.executable

# 默认超时时间（秒）
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120

# 最大输出长度
MAX_OUTPUT_LENGTH = 50000

# ==================== 安全配置 ====================

# 环境变量白名单：只有这些 key 会传给子进程
_ENV_WHITELIST = {
    # Cross-platform
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PYTHONIOENCODING",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    # Unix
    "HOME",
    "USER",
    "TMPDIR",
    # Windows
    "USERPROFILE",
    "USERNAME",
    "TMP",
    "TEMP",
    "SYSTEMROOT",
    "COMSPEC",
}

# BLAS / OpenMP 线程数限制：避免单脚本启动数十个工作线程占满 CPU 与 NPROC 配额
# numpy/pandas/sklearn 默认按 CPU 核数创建工作线程，在共享服务器上需要主动收敛
_BLAS_THREAD_ENV = {
    "OPENBLAS_NUM_THREADS": "2",
    "OMP_NUM_THREADS": "2",
    "MKL_NUM_THREADS": "2",
    "NUMEXPR_NUM_THREADS": "2",
    "VECLIB_MAXIMUM_THREADS": "2",  # macOS Accelerate framework
}

# 危险模块黑名单：脚本中不允许 import 这些
_DANGEROUS_MODULES: Set[str] = {
    "subprocess",
    "shutil",
    "ctypes",
    "importlib",
    "code",
    "codeop",
    "compileall",
    "py_compile",
    "zipimport",
    "ftplib",
    "smtplib",
    "telnetlib",
    "xmlrpc",
    "multiprocessing",
    "signal",
    "socket",
    "http.server",
    "socketserver",
    "webbrowser",
    "pathlib",
    "pickle",
    "shelve",
    "tempfile",
    "runpy",
    "gc",
    "inspect",
    "dis",
    "tracemalloc",
    "_io",
    "_thread",
    "threading",
    "concurrent",
    "asyncio",
    # os / subprocess 的底层 C 模块：合法库基本不直接 import（都走 os/subprocess），
    # 但它们是 os.* 运行时补丁的绕过入口——import posix; posix.system(...) 就跳过了沙箱
    "posix",
    "nt",
    "_posixsubprocess",
}

# 危险函数调用黑名单（AST 层硬拦）：attribute 调用形式 (module.func)
#
# 设计原则：这里只拦"绝对危险、无合法用例"的函数，给用户手写/AI 生成时立即
# 反馈错误信息。文件读写类（remove/rename/mkdir/listdir 等）交给运行时层的
# 路径白名单检查（_sandbox_wrapper.py），允许合法场景使用。
_DANGEROUS_CALLS: Set[str] = {
    # 进程执行 / fork / 发信号——没有合法用例
    "os.system",
    "os.popen",
    "os.exec",
    "os.execl",
    "os.execle",
    "os.execlp",
    "os.execlpe",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
    "os.spawn",
    "os.spawnl",
    "os.spawnle",
    "os.spawnlp",
    "os.spawnlpe",
    "os.spawnv",
    "os.spawnve",
    "os.spawnvp",
    "os.spawnvpe",
    "os.posix_spawn",
    "os.posix_spawnp",
    "os.fork",
    "os.forkpty",
    "os.kill",
    "os.killpg",
    # 目录切换——沙箱路径白名单以调用时的 cwd 为基准，禁止
    "os.chdir",
    "os.fchdir",
    # 身份 / 权限变更
    "os.chown",
    "os.fchown",
    "os.lchown",
    "os.setuid",
    "os.setgid",
    "os.seteuid",
    "os.setegid",
    "os.setreuid",
    "os.setregid",
    "os.chroot",
    # 内置危险函数
    "builtins.exec",
    "builtins.eval",
    "builtins.compile",
    "builtins.__import__",
}

# 顶层危险函数名（直接调用形式）
_DANGEROUS_BUILTINS: Set[str] = {
    "exec",
    "eval",
    "compile",
    "__import__",
    "breakpoint",
    "globals",
    "locals",
    "vars",
    "delattr",
    "setattr",
    "getattr",
}

# 内存限制 (1024 MB) — 给 pandas / matplotlib 留余量，仍能防 OOM
_MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024

# 最大子进程/线程数 (256) — RLIMIT_NPROC 对 Linux 也包含 pthread (LWP)，
# numpy/pandas/sklearn 等科学计算库需要数十至上百线程才能正常初始化 OpenBLAS
_MAX_NPROC = 256

# ==================== 全局执行排队 ====================
#
# 用 BoundedSemaphore 控制同时运行的脚本子进程数量，避免多个 admin / 定时任务
# 并发时把服务器资源打爆。排队中的脚本只占一个 Python 线程（极轻量），不消耗
# CPU/内存/NPROC 配额。
#
# 当前规模 (admin ≤ 20, uvicorn workers=1) 进程内 semaphore 足够；如未来扩容到
# 多 worker，需要替换为 cross-process 队列 (Redis / file lock)。
SCRIPT_CONCURRENCY = max(1, int(os.environ.get("SCRIPT_CONCURRENCY", "4")))
SCRIPT_QUEUE_TIMEOUT = max(1, int(os.environ.get("SCRIPT_QUEUE_TIMEOUT", "180")))

_SCRIPT_SEMAPHORE = threading.BoundedSemaphore(SCRIPT_CONCURRENCY)
_SCRIPT_STATS_LOCK = threading.Lock()
_active_count = 0
_pending_count = 0


def get_script_runtime_stats() -> Dict[str, int]:
    """Return current script execution stats (for debug/metrics)."""
    with _SCRIPT_STATS_LOCK:
        return {
            "active": _active_count,
            "pending": _pending_count,
            "concurrency": SCRIPT_CONCURRENCY,
        }


# ==================== AST 静态分析 ====================

def _check_script_safety(file_path: str) -> Optional[str]:
    """
    对脚本做 AST 静态分析，检查是否包含危险调用。

    Returns:
        None 表示安全，否则返回拒绝原因字符串。
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        return f"无法读取脚本: {e}"

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as e:
        return f"脚本语法错误: {e}"

    for node in ast.walk(tree):
        # 检查 import 语句
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_module = alias.name.split(".")[0]
                if alias.name in _DANGEROUS_MODULES or top_module in _DANGEROUS_MODULES:
                    return f"禁止导入模块: {alias.name}"

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_module = node.module.split(".")[0]
                if node.module in _DANGEROUS_MODULES or top_module in _DANGEROUS_MODULES:
                    return f"禁止导入模块: {node.module}"

        # 检查函数调用
        elif isinstance(node, ast.Call):
            func = node.func

            # 直接调用：exec(...), eval(...) 等
            if isinstance(func, ast.Name) and func.id in _DANGEROUS_BUILTINS:
                return f"禁止调用: {func.id}()"

            # 属性调用：os.system(...), os.popen(...) 等
            if isinstance(func, ast.Attribute):
                call_chain = _resolve_attr_chain(func)
                if call_chain and call_chain in _DANGEROUS_CALLS:
                    return f"禁止调用: {call_chain}()"

            # 动态构造的调用：obj[key](...), f()() 等
            # 拦截如 os.__dict__["system"](...) / getattr_like(...)()
            # 这类绕过 Name / Attribute 静态黑名单的调用方式
            if isinstance(func, (ast.Subscript, ast.Call)):
                return "禁止通过下标或嵌套调用动态构造函数调用"

        # 检查危险属性访问
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr == "environ":
                return "禁止访问 os.environ（环境变量已受限）"
            if node.attr in ("__builtins__", "__loader__", "__spec__",
                             "__import__", "__subclasses__", "__globals__",
                             "__code__", "__func__",
                             # 补充：__dict__ 可直接通过 os.__dict__['system'] 绕过黑名单；
                             # __mro__/__bases__ 是类层次追溯的入口，用于追到 subprocess 等
                             "__dict__", "__mro__", "__bases__"):
                return f"禁止访问 {node.attr}"

        # 检查通过下标访问 __builtins__
        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == "__builtins__":
                return "禁止访问 __builtins__"

        # 检查直接引用 __builtins__
        elif isinstance(node, ast.Name):
            if node.id in ("__builtins__", "__loader__", "__spec__"):
                return f"禁止引用 {node.id}"

    return None


def _resolve_attr_chain(node: ast.Attribute) -> Optional[str]:
    """将 ast.Attribute 链解析为字符串，如 os.path.join -> 'os.path.join'"""
    parts: List[str] = [node.attr]
    current = node.value
    depth = 0
    while depth < 10:  # 防止无限循环
        if isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        elif isinstance(current, ast.Name):
            parts.append(current.id)
            break
        else:
            return None
        depth += 1
    parts.reverse()
    return ".".join(parts)


# ==================== 环境变量构建 ====================

def _build_safe_env() -> Dict[str, str]:
    """构建安全的环境变量字典，只包含白名单中的 key。"""
    safe_env: Dict[str, str] = {}
    for key in _ENV_WHITELIST:
        val = os.environ.get(key)
        if val is not None:
            safe_env[key] = val
    # 强制设置编码
    safe_env["PYTHONIOENCODING"] = "utf-8"
    # 注入 BLAS / OpenMP 线程数限制（用户脚本环境无法覆盖）
    safe_env.update(_BLAS_THREAD_ENV)
    return safe_env


# ==================== 资源限制 ====================

def _get_preexec_fn():
    """
    返回 preexec_fn，用于在子进程启动前设置资源限制。
    仅在 Linux/macOS 上有效；Windows 上返回 None。
    """
    if platform.system() == "Windows":
        return None

    def _set_limits():
        try:
            import resource
            # 限制虚拟内存
            resource.setrlimit(
                resource.RLIMIT_AS,
                (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES),
            )
            # 限制子进程数量
            resource.setrlimit(
                resource.RLIMIT_NPROC,
                (_MAX_NPROC, _MAX_NPROC),
            )
        except (ImportError, ValueError, OSError):
            pass  # 平台不支持则跳过

    return _set_limits


# ==================== 错误返回辅助 ====================

def _error_result(error_msg: str) -> Dict[str, Any]:
    """构造统一的错误返回格式。"""
    return {
        "success": False,
        "stdout": "",
        "stderr": "",
        "exit_code": -1,
        "error": error_msg,
    }


# ==================== 主函数 ====================

_SANDBOX_WRAPPER = os.path.join(os.path.dirname(__file__), "_sandbox_wrapper.py")


def run_script(
    script_path: str,
    scripts_dir: str,
    input_data: Optional[str] = None,
    args: Optional[list] = None,
    timeout: int = DEFAULT_TIMEOUT,
    allowed_read_dirs: Optional[List[str]] = None,
    allowed_write_dirs: Optional[List[str]] = None,
    python_executable: Optional[str] = None,
) -> Dict[str, Any]:
    """
    执行 Python 脚本（安全加固版）

    Args:
        script_path: 脚本相对路径（相对于 scripts_dir），例如 "hello.py"
        scripts_dir: 脚本根目录的绝对路径
        input_data: 传给 stdin 的输入数据
        args: 命令行参数列表
        timeout: 超时时间（秒），最大 120s
        allowed_read_dirs: 额外允许读取的目录列表（scripts_dir 自动包含）
        allowed_write_dirs: 额外允许写入的目录列表（默认仅 scripts_dir）

    Returns:
        {
            "success": bool,
            "stdout": str,
            "stderr": str,
            "exit_code": int,
            "error": str | None
        }
    """
    # 1. 安全检查：限制超时
    timeout = min(timeout, MAX_TIMEOUT)

    # 2. 路径解析：用 realpath 解析符号链接，防止路径穿越
    clean_path = script_path.replace("\\", "/").lstrip("/")
    abs_scripts_dir = os.path.realpath(scripts_dir)
    full_path = os.path.realpath(os.path.join(abs_scripts_dir, clean_path))

    # 3. 路径必须在 scripts_dir 内（大小写敏感性适配平台）
    _ci = platform.system() == "Windows"
    _fp = full_path.lower() if _ci else full_path
    _sd = abs_scripts_dir.lower() if _ci else abs_scripts_dir
    if not (_fp.startswith(_sd + os.sep) or _fp == _sd):
        return _error_result("脚本路径超出允许范围")

    # 4. 显式拒绝符号链接（即使 realpath 已解析，仍作为纵深防御）
    original_path = os.path.normpath(os.path.join(abs_scripts_dir, clean_path))
    if os.path.islink(original_path):
        return _error_result("不允许执行符号链接文件")

    # 5. 只允许 .py 文件
    if not full_path.endswith(".py"):
        return _error_result("只能执行 .py 文件")

    # 6. 检查脚本是否存在
    if not os.path.isfile(full_path):
        return _error_result(f"脚本不存在: {clean_path}")

    # 7. AST 静态分析：检查脚本内容是否包含危险调用
    safety_issue = _check_script_safety(full_path)
    if safety_issue:
        return _error_result(f"脚本安全检查未通过: {safety_issue}")

    # 8. 构建允许的目录列表
    read_dirs = [abs_scripts_dir]
    if allowed_read_dirs:
        read_dirs.extend([os.path.realpath(d) for d in allowed_read_dirs])
    write_dirs = allowed_write_dirs if allowed_write_dirs else [abs_scripts_dir]
    write_dirs = [os.path.realpath(d) for d in write_dirs]

    # 9. 构建命令 — 通过 sandbox wrapper 执行
    py_exe = python_executable or PYTHON_EXECUTABLE
    cmd = [
        py_exe, _SANDBOX_WRAPPER,
        "--allowed-read", "|".join(read_dirs),
        "--allowed-write", "|".join(write_dirs),
        "--script", full_path,
    ]
    _log.info("Sandbox cmd: --allowed-write %s | cwd=%s", "|".join(write_dirs), scripts_dir)
    if args:
        cmd.append("--")
        cmd.extend([str(a) for a in args])

    # 10. 排队执行（受全局 BoundedSemaphore 保护）
    global _active_count, _pending_count
    with _SCRIPT_STATS_LOCK:
        _pending_count += 1
        snapshot_active = _active_count
        snapshot_pending = _pending_count

    _log.info(
        "[script_runner] acquire start  active=%d pending=%d cap=%d  script=%s",
        snapshot_active, snapshot_pending, SCRIPT_CONCURRENCY, clean_path,
    )

    wait_started = time.monotonic()
    acquired = _SCRIPT_SEMAPHORE.acquire(timeout=SCRIPT_QUEUE_TIMEOUT)
    wait_elapsed = time.monotonic() - wait_started

    if not acquired:
        with _SCRIPT_STATS_LOCK:
            _pending_count -= 1
            ahead = max(0, _pending_count)  # 超时后剩下的等待者
            running = _active_count
        _log.warning(
            "[script_runner] queue timeout after %ds  active=%d pending=%d  script=%s",
            SCRIPT_QUEUE_TIMEOUT, running, ahead, clean_path,
        )
        return _error_result(
            f"脚本执行队列繁忙，已等待 {SCRIPT_QUEUE_TIMEOUT} 秒。"
            f"当前 {running} 个脚本运行中，前面还有 {ahead} 个等待。请稍后重试。"
        )

    with _SCRIPT_STATS_LOCK:
        _pending_count -= 1
        _active_count += 1
        snapshot_active = _active_count
        snapshot_pending = _pending_count

    _log.info(
        "[script_runner] acquire ok     active=%d pending=%d  wait=%.2fs  script=%s",
        snapshot_active, snapshot_pending, wait_elapsed, clean_path,
    )

    exec_started = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=scripts_dir,
            env=_build_safe_env(),
            preexec_fn=_get_preexec_fn(),
        )

        stdout = result.stdout[:MAX_OUTPUT_LENGTH] if result.stdout else ""
        stderr = result.stderr[:MAX_OUTPUT_LENGTH] if result.stderr else ""

        if result.returncode != 0 and stderr:
            _log.warning("Script failed (exit %d): %s", result.returncode, stderr[:500])

        return {
            "success": result.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.returncode,
            "error": None,
        }

    except subprocess.TimeoutExpired:
        return _error_result(f"脚本执行超时（{timeout}秒）")
    except Exception as e:
        return _error_result(f"执行错误: {str(e)}")
    finally:
        exec_elapsed = time.monotonic() - exec_started
        _SCRIPT_SEMAPHORE.release()
        with _SCRIPT_STATS_LOCK:
            _active_count -= 1
            snapshot_active = _active_count
            snapshot_pending = _pending_count
        _log.info(
            "[script_runner] release        active=%d pending=%d  exec=%.2fs  script=%s",
            snapshot_active, snapshot_pending, exec_elapsed, clean_path,
        )
