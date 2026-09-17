"""Bounded stdio JSON-RPC transport. Never invokes a shell or logs credentials."""
import asyncio
import contextlib
import json
import os
import signal
import shutil
from app.runtime.rpc_errors import RuntimeFailure, RuntimeUnavailable
from app.runtime.process_tree import ProcessTree


class StdioRPC:
    def __init__(self, command: list[str], *, env: dict[str, str], cwd: str, label="Codex"):
        self.command, self.env, self.cwd = command, env, cwd
        self.label = label
        self.process = None
        self.pending = {}
        self.events = asyncio.Queue(maxsize=512)
        self.counter = 0
        self.reader = self.stderr = None
        self.failure = None
        self.write_lock = asyncio.Lock()
        self.process_tree = None
        self.monitor = None
        self.monitor_error = None
        self.monitor_stop = asyncio.Event()
        self.close_task = None

    async def start(self):
        # Validate before spawning so missing ps cannot strand an untracked CLI.
        if not shutil.which(self.command[0], path=self.env.get('PATH', os.defpath)):
            raise RuntimeUnavailable(
                f'{self.label} CLI 未安装或不可执行。请在运行 Jellyfish 后端的环境中安装；'
                f'Docker 部署需要装在应用容器内，并检查 JELLYFISH_RUNTIME_{self.label.upper()}_BIN。')
        if not shutil.which('ps'):
            raise RuntimeUnavailable('后端缺少进程管理工具 ps；Debian / Ubuntu 容器请安装 procps 后重试。')
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command, cwd=self.cwd, env=self.env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=32 * 1024 * 1024,
                start_new_session=True,
            )
        except OSError as exc:
            raise RuntimeUnavailable(f"无法启动 {self.label} CLI；请检查执行权限、系统架构及运行依赖。") from exc
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._drain_stderr())
        self.process_tree = ProcessTree(self.process.pid)
        self.monitor = asyncio.create_task(self._monitor_children())

    async def _monitor_children(self):
        try:
            while self.process.returncode is None and not self.monitor_stop.is_set():
                await self.capture_children()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.monitor_stop.wait(), .25)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.monitor_error = exc

    async def capture_children(self):
        if self.process_tree:
            await self.process_tree.capture()

    async def _drain_stderr(self):
        # Keep pipes flowing without exposing CLI logs (which can contain auth data).
        while await self.process.stderr.read(8192):
            pass

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                msg = json.loads(line)
                if not isinstance(msg, dict):
                    raise ValueError("JSON-RPC message must be an object")
                if "method" not in msg and "id" in msg:
                    future = self.pending.get(msg["id"])
                    if future and not future.done():
                        if "error" in msg:
                            error = RuntimeFailure(f"{self.label} 协议请求失败（code={msg['error'].get('code', 'unknown')}）")
                            error.provider_error = msg['error']
                            future.set_exception(error)
                        else:
                            future.set_result(msg.get("result", {}))
                else:
                    self.events.put_nowait(msg)
            raise RuntimeFailure(f"{self.label} 进程已退出，未收到完整终态")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failure = RuntimeFailure(str(exc)) if isinstance(exc, RuntimeFailure) else RuntimeFailure(f"{self.label} 事件流无效或超出缓冲上限")
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(self.failure)

    async def send(self, message):
        if self.failure:
            raise self.failure
        async with self.write_lock:
            try:
                self.process.stdin.write((json.dumps(message) + "\n").encode())
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise RuntimeFailure(f"{self.label} 连接已关闭") from exc

    async def request(self, method: str, params: dict, timeout: float = 30):
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeFailure(f"{self.label} 请求超时：{method}") from exc
        finally:
            self.pending.pop(request_id, None)

    async def next_event(self):
        while True:
            if not self.events.empty():
                return self.events.get_nowait()
            if self.failure:
                raise self.failure
            try:
                return await asyncio.wait_for(self.events.get(), .25)
            except asyncio.TimeoutError:
                pass

    async def close(self):
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close())
        cancelled = False
        while True:
            try:
                await asyncio.shield(self.close_task)
                break
            except asyncio.CancelledError:
                if self.close_task.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self):
        if self.monitor:
            self.monitor_stop.set()
            await self.monitor
        cleanup_error = self.monitor_error
        if self.process:
            try:
                await self.capture_children()
                await self.process_tree.terminate()
            except Exception as exc:
                cleanup_error = exc
            # Also terminate inherited command children, including after normal completion.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.process.wait(), 3)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
            await self.process.wait()
        for task in (self.reader, self.stderr):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        if cleanup_error:
            raise RuntimeFailure('无法确认全部执行进程退出；连接需宿主检查') from cleanup_error
