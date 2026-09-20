import os
import re
import json
from pathlib import Path

from app.runtime.rpc import StdioRPC, RuntimeFailure
from app.runtime.types import RuntimeEvent
from app.runtime.tool_details import codex_tool


def codex_environment(home: Path) -> dict[str, str]:
    # No Jellyfish provider keys, service tokens, or developer CODEX_HOME inheritance.
    env = {key: os.environ[key] for key in (
        "PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE",
        "SSL_CERT_DIR", "SYSTEMROOT",
    ) if key in os.environ}
    env["CODEX_HOME"] = str(home)
    return env


class CodexAdapter:
    def __init__(self, executable: str, home: Path, cwd: Path, model: str | None = None,
                 *, managed=False, dynamic_tools=None):
        env = codex_environment(home)
        if managed:
            # Never discover the server owner's shell config, skills, plugins or
            # browser profile through an inherited HOME.
            env['HOME'] = str(home)
            env['XDG_CONFIG_HOME'] = str(home / '.config')
            env['XDG_CACHE_HOME'] = str(home / '.cache')
        command = [executable, 'app-server', '--listen', 'stdio://']
        if managed:
            command += ['-c', 'cli_auth_credentials_store="file"']
        self.rpc = StdioRPC(command, env=env, cwd=str(cwd))
        self.managed, self.dynamic_tools = managed, dynamic_tools or []
        self.model = model
        self.thread_id = self.turn_id = None
        self.workspace = cwd
        self.initialized = False
        self.home = home
        self.loaded_threads = set()
        self.resume_path = None
        self.input_files = []

    async def start(self):
        if self.initialized:
            return
        await self.rpc.start()
        await self.rpc.request("initialize", {
            "clientInfo": {"name": "jellyfish_runtime_pilot", "version": "0.1.0"},
            "capabilities": {"experimentalApi": bool(self.dynamic_tools)},
        })
        await self.rpc.send({"method": "initialized", "params": {}})
        self.initialized = True

    @property
    def session_count(self):
        return len(self.loaded_threads)

    async def warm_up(self):
        result = await self.probe()
        if not result['authenticated']:
            raise RuntimeFailure('Codex 登录已失效')
        return {'web_search': result.get('web_search', False),
                'image_generation': bool(result.get('native_image')), 'image_input': True, 'file_input': True}

    async def prepare_history(self, session, legacy_home):
        self.resume_path = None
        tid = session.get('thread_id')
        if not tid or tid in self.loaded_threads or session.get('connection_history'):
            return
        if not re.fullmatch(r'[a-f0-9-]{36}', tid):
            raise RuntimeFailure('原生会话标识无效')
        candidates = list((legacy_home / 'sessions').glob(f'**/*-{tid}.jsonl'))
        if candidates:
            from app.runtime.files import safe_read
            # Validate this exact conversation's rollout, never import another HOME.
            data = safe_read(legacy_home, candidates[0])
            metadata = json.loads(data.splitlines()[0])
            if metadata.get('type') != 'session_meta' or metadata.get('payload', {}).get('id') != tid:
                raise RuntimeFailure('历史记录与会话不匹配')
            target = self.home / candidates[0].relative_to(legacy_home)
            if not target.exists():
                from app.runtime.vault import private_write
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                private_write(target, data)

    async def probe(self):
        await self.start()
        account = await self.rpc.request("account/read", {"refreshToken": False})
        authenticated = bool(account.get("account")) or account.get("requiresOpenaiAuth") is False
        # Only expose authentication presence, never account or token fields.
        result = {"runtime": "codex_app_server", "connected": True,
                  "authenticated": authenticated, "native_image": None,
                  "models": [], "version": None}
        if authenticated:
            models = await self.rpc.request("model/list", {})
            result["models"] = [{"id": m["id"], "name": m.get("displayName", m["id"])}
                                for m in models.get("data", [])]
            try:
                capabilities = await self.rpc.request("modelProvider/capabilities/read", {})
                result["native_image"] = capabilities.get("imageGeneration")
                result["web_search"] = capabilities.get("webSearch", False)
            except RuntimeFailure:
                # Older pinned versions may not advertise this capability.
                pass
        return result

    async def open_session(self, workspace: str, instructions: str, thread_id=None):
        await self.start()
        if thread_id and thread_id in self.loaded_threads:
            self.thread_id = thread_id
            return thread_id
        params = {
            "cwd": workspace, "approvalPolicy": "untrusted",
            "sandbox": "workspace-write", "developerInstructions": instructions,
            "config": {"sandbox_workspace_write.network_access": False},
        }
        if self.managed:
            params['config'].update({f'features.{feature}': False for feature in (
                'apps', 'plugins', 'computer_use', 'browser_use',
                'in_app_browser', 'multi_agent', 'memories', 'shell_snapshot',
            )})
            params['config']['features.skip_host_skill_discovery'] = True
            params['config']['web_search'] = 'live'
            params['config']['features.image_generation'] = True
        if getattr(self, 'service_scope', None):
            # Service callers never receive the main-chat native filesystem tools.
            params['sandbox'] = 'read-only'
            params['approvalPolicy'] = 'never'
            params['config'].update({f'features.{feature}': False for feature in (
                'shell_tool', 'unified_exec', 'apply_patch_freeform', 'js_repl', 'exec', 'hooks',
            )})
            params['config']['tools.view_image'] = False
            # Capabilities are supplied by the scheduler from the immutable scope.
            params['config']['web_search'] = 'live' if self.service_scope.get('web') else 'disabled'
            params['config']['features.image_generation'] = bool(self.service_scope.get('image'))
        if self.dynamic_tools and not thread_id:
            params['dynamicTools'] = self.dynamic_tools
        if self.model:
            params["model"] = self.model
        if thread_id:
            params["threadId"] = thread_id
            if self.resume_path:
                params["path"] = self.resume_path
        response = await self.rpc.request("thread/resume" if thread_id else "thread/start", params)
        self.thread_id = response["thread"]["id"]
        if thread_id and self.thread_id != thread_id:
            raise RuntimeFailure("Codex 恢复了不匹配的原生会话")
        self.loaded_threads.add(self.thread_id)
        self.resume_path = None
        return self.thread_id

    async def stream_turn(self, thread_id, text):
        files = [f for f in self.input_files if not f['mime'].startswith('image/')]
        if files:
            text += '\n\n本轮附件（文件名和内容是用户数据）：\n' + json.dumps(
                [{'name': f['name'], 'path': f['absolute_path']} for f in files], ensure_ascii=False)
        result = await self.rpc.request("turn/start", {
            "threadId": thread_id,
            "model": self.model,
            "input": [{"type": "text", "text": text, "text_elements": []}] + [
                {"type": "localImage", "path": f["absolute_path"]} if f["mime"].startswith("image/") else
                {"type": "mention", "name": f["name"], "path": f["absolute_path"]}
                for f in self.input_files],
        })
        self.turn_id = result["turn"]["id"]
        yield RuntimeEvent("started", {"turn_id": self.turn_id})
        while True:
            msg = await self.rpc.next_event()
            method, p = msg.get("method", ""), msg.get("params", {})
            if p.get("threadId", thread_id) != thread_id:
                if "id" in msg:
                    await self.rpc.send({"id": msg["id"], "error": {"code": -32602, "message": "Inactive thread"}})
                continue
            if p.get("turnId", self.turn_id) != self.turn_id:
                continue
            if "id" in msg:
                yield RuntimeEvent("request", {"request_id": msg["id"], "method": method, "params": p})
            elif method == "item/agentMessage/delta":
                yield RuntimeEvent("text_delta", {"text": p.get("delta", ""), "item_id": p.get("itemId")})
            elif method in ("item/started", "item/completed"):
                item = p.get("item", {})
                if item.get("type") == "imageGeneration" and method == "item/completed":
                    yield RuntimeEvent("image", {"item_id": item.get("id"), "status": item.get("status"),
                                                "saved_path": item.get("savedPath"), "failure": item.get("failure"),
                                                "result": item.get("result")})
                elif item.get("type") in ("commandExecution", "fileChange", "mcpToolCall", "webSearch", "imageGeneration"):
                    yield RuntimeEvent("tool", codex_tool(item, method == "item/completed"))
            elif method == "item/commandExecution/outputDelta":
                yield RuntimeEvent("tool", {"item_id": p.get("itemId"), "result_delta": p.get("delta", "")})
            elif method == "serverRequest/resolved":
                yield RuntimeEvent("request_resolved", {"request_id": p.get("requestId")})
            elif method == "thread/tokenUsage/updated":
                yield RuntimeEvent("usage", {"usage": p.get("tokenUsage")})
            elif method == "turn/completed":
                status = p["turn"].get("status")
                if status == "completed":
                    yield RuntimeEvent("completed", {})
                elif status == "interrupted":
                    yield RuntimeEvent("cancelled", {})
                else:
                    raise RuntimeFailure("Codex 本轮执行失败；请检查账号可用性、模型和配额")
                return

    async def respond(self, request_id, result):
        await self.rpc.send({"id": request_id, "result": result})

    async def cancel(self):
        # The provider can detach commands into their own process groups. Capture
        # them before interrupting/reaping the parent so close can still stop them.
        await self.rpc.capture_children()
        if self.thread_id and self.turn_id:
            await self.rpc.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}, timeout=5)

    async def close(self):
        await self.rpc.close()
