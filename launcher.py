#!/usr/bin/env python3
"""
JellyfishBot Cross-Platform Launcher

Handles:
  - Detection and cleanup of old JellyfishBot instances
  - Automatic free port discovery
  - Starting backend (FastAPI/uvicorn) and frontend (Express)
  - Clean shutdown on exit (SIGINT / SIGTERM / Ctrl+C)

Works on Windows, macOS, and Linux.

Usage:
  python launcher.py                 # production mode (Express serves dist/)
  python launcher.py --dev           # dev mode (Vite dev server)
  python launcher.py --backend-only  # backend only
  python launcher.py --port 9000     # specify backend port
"""

import argparse
import atexit
import datetime as _dt
import os
import platform
import signal
import socket
import subprocess
import sys
import threading
import time

IS_WIN = platform.system() == "Windows"


def _strip_extended_prefix(p: str) -> str:
    r"""Strip Windows ``\\?\`` extended-length path prefix.

    Defense-in-depth against the Tauri launcher passing a ``\\?\C:\...``
    path via ``JELLYFISH_PYTHON`` / ``JELLYFISH_NODE``. If the prefix
    reaches a Python subprocess it propagates into ``sys.executable`` and
    every ``__file__`` in ``site-packages``, which then breaks
    ``pycryptodome``'s ``os.path.isfile()`` lookup for native ``.pyd``
    modules (see Test 4 vs Test 5 in
    ``tauri-launcher/scripts/verify_extended_path_bug.ps1``).
    """
    if not p or not IS_WIN:
        return p
    if p.startswith("\\\\?\\"):
        rest = p[4:]
        # \\?\UNC\server\share -> \\server\share
        if rest.startswith("UNC\\"):
            return "\\\\" + rest[4:]
        return rest
    return p


SCRIPT_DIR = _strip_extended_prefix(os.path.dirname(os.path.abspath(__file__)))

BACKEND_DEFAULT_PORT = 8000
FRONTEND_DEFAULT_PORT = 3000

PROCESS_MARKERS = ["uvicorn", "jellyfishbot", "app.main:app"]
FRONTEND_MARKERS = ["server.js", "vite"]

_children = []  # list of subprocess.Popen
_log_files = []  # list of open log file handles, closed on cleanup
_log_threads = []  # list of tee threads
_shutting_down = False


# ── Logging ───────────────────────────────────────────────────────

def _ensure_logs_dir() -> str:
    """Create and return the project logs directory.

    Logs are written next to launcher.py (i.e. inside ``project_dir``)
    so the Tauri front-end can open ``project_dir/logs/`` directly via
    ``open_logs_dir`` without needing a separate path lookup.
    """
    logs_dir = os.path.join(SCRIPT_DIR, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


def _open_log_file(name: str):
    """Open ``logs/{name}-YYYYMMDD.log`` in append mode (UTF-8, line-buffered)."""
    logs_dir = _ensure_logs_dir()
    today = _dt.datetime.now().strftime("%Y%m%d")
    path = os.path.join(logs_dir, f"{name}-{today}.log")
    fh = open(path, "a", encoding="utf-8", buffering=1, errors="replace")
    fh.write(
        f"\n{'=' * 60}\n"
        f"--- session start at {_dt.datetime.now().isoformat(timespec='seconds')} ---\n"
        f"{'=' * 60}\n"
    )
    fh.flush()
    return path, fh


def _tee_pipe_to(fh, src):
    """Background thread: read lines from ``src`` (subprocess pipe) and
    mirror them into both the log file ``fh`` and our own stdout.

    When launched by Tauri there is no real console attached, so the
    stdout write is a no-op; when launched manually the user still sees
    everything in their terminal as before.
    """
    try:
        for raw in iter(src.readline, b""):
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                line = repr(raw)
            try:
                fh.write(line)
                fh.flush()
            except Exception:
                pass
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except Exception:
                pass
    finally:
        try:
            src.close()
        except Exception:
            pass


# ── Port utilities ────────────────────────────────────────────────

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_free_port(start: int, max_tries: int = 50) -> int:
    for offset in range(max_tries):
        port = start + offset
        if not is_port_in_use(port):
            return port
    raise RuntimeError(f"No free port found starting from {start}")


# ── Process detection / cleanup ───────────────────────────────────

def _get_pid_on_port(port: int) -> list[dict]:
    """Return list of {pid, name, cmdline} for processes listening on port."""
    results = []
    try:
        if IS_WIN:
            out = subprocess.check_output(
                ["netstat", "-ano", "-p", "TCP"],
                text=True, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and f":{port}" in parts[1] and "LISTENING" in line:
                    pid = int(parts[-1])
                    if pid > 0:
                        name = _get_process_name_win(pid)
                        results.append({"pid": pid, "name": name, "cmdline": ""})
        else:
            out = subprocess.check_output(
                ["lsof", "-i", f"TCP:{port}", "-sTCP:LISTEN", "-Fp"],
                text=True, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                if line.startswith("p"):
                    pid = int(line[1:])
                    name = _get_process_name_unix(pid)
                    results.append({"pid": pid, "name": name, "cmdline": ""})
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass
    return results


def _get_process_name_win(pid: int) -> str:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True, stderr=subprocess.DEVNULL,
        )
        parts = out.strip().strip('"').split('","')
        return parts[0] if parts else f"pid:{pid}"
    except Exception:
        return f"pid:{pid}"


def _get_process_name_unix(pid: int) -> str:
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "comm="],
            text=True, stderr=subprocess.DEVNULL,
        )
        return out.strip() or f"pid:{pid}"
    except Exception:
        return f"pid:{pid}"


def _is_jellyfish_process(info: dict) -> bool:
    name = (info.get("name", "") + " " + info.get("cmdline", "")).lower()
    return any(m in name for m in ["uvicorn", "python", "node", "jellyfishbot"])


def detect_old_instances(backend_port: int, frontend_port: int) -> list[dict]:
    """Find JellyfishBot-like processes on our target ports."""
    found = []
    for port in [backend_port, frontend_port]:
        procs = _get_pid_on_port(port)
        for p in procs:
            p["port"] = port
            found.append(p)
    return found


def kill_process(pid: int):
    try:
        if IS_WIN:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    except Exception:
        pass


def prompt_kill_old(instances: list[dict]) -> bool:
    """Show old instances and ask user whether to kill them."""
    print("\n⚠️  检测到以下进程正在占用 JellyfishBot 端口：")
    for inst in instances:
        print(f"   端口 {inst['port']} → PID {inst['pid']} ({inst['name']})")

    try:
        answer = input("\n是否终止这些进程以继续启动？[Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False

    if answer in ("", "y", "yes"):
        for inst in instances:
            print(f"   终止 PID {inst['pid']}...")
            kill_process(inst['pid'])
        time.sleep(1)
        return True
    return False


# ── Startup ───────────────────────────────────────────────────────

def wait_for_backend(port: int, timeout: int = 60) -> bool:
    print(f"   等待后端就绪 (port {port})...", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        if is_port_in_use(port):
            print(" ✓")
            return True
        print(".", end="", flush=True)
        time.sleep(1)
    print(" ✗ 超时")
    return False


def _resolve_python() -> str:
    """Resolve Python executable: JELLYFISH_PYTHON env > sys.executable."""
    custom = os.environ.get("JELLYFISH_PYTHON")
    if custom and os.path.isfile(custom):
        return _strip_extended_prefix(os.path.abspath(custom))
    return _strip_extended_prefix(sys.executable)


def _resolve_node() -> str:
    """Resolve Node executable: JELLYFISH_NODE env > system node."""
    custom = os.environ.get("JELLYFISH_NODE")
    if custom and os.path.isfile(custom):
        return _strip_extended_prefix(os.path.abspath(custom))
    return "node"


def _get_local_ip() -> str | None:
    """Get LAN IP via UDP socket probe (no actual traffic sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip != "127.0.0.1" else None
    except Exception:
        return None


def _spawn_with_log(cmd: list[str], cwd: str, env: dict, log_name: str) -> subprocess.Popen:
    """Spawn a subprocess whose stdout+stderr are tee'd into both the
    daily log file and our own stdout via a background thread."""
    log_path, fh = _open_log_file(log_name)
    fh.write(f"--- exec: {' '.join(cmd)}\n--- cwd:  {cwd}\n")
    fh.flush()
    print(f"   日志 → {log_path}")

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )

    t = threading.Thread(target=_tee_pipe_to, args=(fh, proc.stdout), daemon=True)
    t.start()

    _children.append(proc)
    _log_files.append(fh)
    _log_threads.append(t)
    return proc


def start_backend(port: int, dev: bool = False) -> subprocess.Popen:
    python = _resolve_python()
    cmd = [
        python, "-m", "uvicorn", "app.main:app",
        "--host", "0.0.0.0",
        "--port", str(port),
    ]
    if dev:
        cmd.append("--reload")

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return _spawn_with_log(cmd, SCRIPT_DIR, env, "backend")


def start_frontend(frontend_port: int, backend_port: int, dev: bool = False) -> subprocess.Popen:
    env = {
        **os.environ,
        "FRONTEND_PORT": str(frontend_port),
        "API_TARGET": f"http://localhost:{backend_port}",
    }

    frontend_dir = os.path.join(SCRIPT_DIR, "frontend")

    if dev:
        cmd = ["npx", "vite", "--port", str(frontend_port)]
        if IS_WIN:
            cmd = ["npx.cmd", "vite", "--port", str(frontend_port)]
    else:
        node_cmd = _resolve_node()
        cmd = [node_cmd, "server.js"]

    return _spawn_with_log(cmd, frontend_dir, env, "frontend")


# ── Shutdown ──────────────────────────────────────────────────────

def cleanup():
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    print("\n🛑 正在关闭 JellyfishBot...")
    for proc in _children:
        try:
            if proc.poll() is None:
                if IS_WIN:
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGTERM)
        except Exception:
            pass

    deadline = time.time() + 5
    for proc in _children:
        remaining = max(0, deadline - time.time())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass

    for fh in _log_files:
        try:
            fh.write(
                f"--- session end at {_dt.datetime.now().isoformat(timespec='seconds')} ---\n"
            )
            fh.close()
        except Exception:
            pass
    _log_files.clear()
    print("   已关闭所有进程。")


def signal_handler(signum, frame):
    cleanup()
    sys.exit(0)


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="JellyfishBot Launcher")
    parser.add_argument("--port", type=int, default=BACKEND_DEFAULT_PORT,
                        help=f"Backend port (default: {BACKEND_DEFAULT_PORT})")
    parser.add_argument("--frontend-port", type=int, default=FRONTEND_DEFAULT_PORT,
                        help=f"Frontend port (default: {FRONTEND_DEFAULT_PORT})")
    parser.add_argument("--dev", action="store_true",
                        help="Dev mode (uvicorn --reload + vite dev server)")
    parser.add_argument("--backend-only", action="store_true",
                        help="Start backend only, no frontend")
    parser.add_argument("--skip-check", action="store_true",
                        help="Skip old instance detection")
    args = parser.parse_args()

    backend_port = args.port
    frontend_port = args.frontend_port

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup)

    print("=" * 50)
    print("  🪼 JellyfishBot Launcher")
    print("=" * 50)

    # 1. Detect old instances
    if not args.skip_check:
        old = detect_old_instances(backend_port, frontend_port)
        if old:
            if not prompt_kill_old(old):
                print("用户取消，尝试使用其他端口...")
                backend_port = find_free_port(backend_port + 1)
                if not args.backend_only:
                    frontend_port = find_free_port(frontend_port + 1)

    # 2. Find free ports (if defaults are occupied)
    if is_port_in_use(backend_port):
        old_port = backend_port
        backend_port = find_free_port(backend_port + 1)
        print(f"   端口 {old_port} 已占用，后端使用 {backend_port}")
    if not args.backend_only and is_port_in_use(frontend_port):
        old_port = frontend_port
        frontend_port = find_free_port(frontend_port + 1)
        print(f"   端口 {old_port} 已占用，前端使用 {frontend_port}")

    # 3. Start backend
    print(f"\n[1/2] 启动后端 (port {backend_port})...")
    backend_proc = start_backend(backend_port, dev=args.dev)

    if not wait_for_backend(backend_port, timeout=60):
        print("❌ 后端启动失败，请检查日志。")
        cleanup()
        sys.exit(1)

    # 4. Start frontend
    if not args.backend_only:
        print(f"[2/2] 启动前端 (port {frontend_port})...")
        frontend_proc = start_frontend(frontend_port, backend_port, dev=args.dev)
    else:
        frontend_proc = None

    # 5. Print summary
    lan_ip = _get_local_ip()
    print()
    print("=" * 50)
    print("  🪼 JellyfishBot 已启动！")
    print(f"     前端: http://localhost:{frontend_port}" if not args.backend_only else "     前端: 未启动")
    print(f"     后端: http://localhost:{backend_port}")
    if lan_ip and not args.backend_only:
        print(f"     局域网: http://{lan_ip}:{frontend_port}")
    print(f"     模式: {'开发' if args.dev else '生产'}")
    print("  按 Ctrl+C 退出")
    print("=" * 50)
    print()

    # 6. Wait for processes
    try:
        while True:
            if backend_proc.poll() is not None:
                print(f"⚠️  后端进程退出 (code {backend_proc.returncode})")
                break
            if frontend_proc and frontend_proc.poll() is not None:
                print(f"⚠️  前端进程退出 (code {frontend_proc.returncode})")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    cleanup()


if __name__ == "__main__":
    main()
