"""Cursor ACP adapter; optional official CLI, private file auth, no API-key fallback."""
import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import sys
import subprocess
import shutil
import time
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from app.runtime.rpc import StdioRPC, RuntimeFailure, RuntimeUnavailable
from app.runtime.types import RuntimeEvent
from app.runtime.vault import private_write


def credential_path(home):
    return Path(home) / ('.cursor' if sys.platform == 'darwin' else '.config/cursor') / 'auth.json'


def cursor_identity(data):
    if len(data) > 128 * 1024:
        raise ValueError('登录缓存过大')
    auth = json.loads(data)
    if not isinstance(auth, dict) or not isinstance(auth.get('accessToken'), str) or not isinstance(auth.get('refreshToken'), str) or not auth.get('accessToken') or not auth.get('refreshToken') or auth.get('apiKey') or auth.get('bedrockCredentials'):
        raise ValueError('需要 Cursor 浏览器登录缓存')
    try:
        part = auth['accessToken'].split('.')[1]
        body = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    except (ValueError, IndexError) as exc:
        raise ValueError('Cursor 登录缓存格式无效') from exc
    if not isinstance(body, dict) or not isinstance(body.get('sub'), str) or not body['sub']:
        raise ValueError('登录缓存缺少账号主体')
    # Change detector only. Supplier authenticates the token; Jellyfish never uses
    # unverified JWT claims to authorize its own users.
    return hashlib.sha256(('cursor\0' + body['sub']).encode()).hexdigest()


def cursor_environment(home):
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if k in os.environ}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'), XDG_CACHE_HOME=str(home / '.cache'),
               CURSOR_CONFIG_DIR=str(home / '.cursor'), CURSOR_DATA_DIR=str(home / '.cursor'),
               AGENT_CLI_CREDENTIAL_STORE='file', NO_OPEN_BROWSER='1', DIRENV_DISABLE='1',
               CURSOR_AGENT_DISABLE_DEBUG_LOG='1', NO_COLOR='1')
    return env


class CursorRPC(StdioRPC):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, label='Cursor')

    async def send(self, message):
        await super().send({'jsonrpc': '2.0', **message})


class LoginProcess(CursorRPC):
    """Reuse the proven process-tree lifecycle, parsing only the login challenge."""
    async def _read(self):
        size = 0
        try:
            while line := await self.process.stdout.readline():
                size += len(line)
                if size > 65536:
                    raise RuntimeFailure('Cursor 登录输出超过限制')
                for url in re.findall(r'https://[^\s\x1b]+', line.decode(errors='replace')):
                    parsed = urlsplit(url)
                    if parsed.hostname in ('cursor.com', 'www.cursor.com') and parsed.path == '/loginDeepControl':
                        self.events.put_nowait({'url': url})
            code = await self.process.wait()
            self.events.put_nowait({'exit_code': code})
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failure = RuntimeFailure('Cursor 登录进程失败')


class CursorAdapter:
    def __init__(self, executable, home, cwd, model=None, *, managed=True, dynamic_tools=None):
        self.executable, self.home, self.workspace = executable, Path(home), Path(cwd)
        self.env = cursor_environment(self.home)
        self.model, self.dynamic_tools = model, dynamic_tools or []
        self.rpc = CursorRPC([executable, '--disable-project-configs', 'acp'], env=self.env, cwd=str(cwd))
        self.thread_id = None
        self.tool_call = None
        self.mcp = None
        self.acp_started = False
        self.instructions = ''
        self.permission_options = {}
        self.workspace_prepared = False
        self.instructions_pending = True
        self.configured_model = None
        self.loaded_threads = set()
        self.bridges = {}
        self.input_files = []
        self.agent_capabilities = {}
        self.prepare_timings = {}

    async def start(self):
        # Authentication chooses either the browser-login process or ACP. Neither
        # starts implicitly; a probe must never trigger interactive authentication.
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def login_start(self):
        if not shutil.which('git', path=self.env.get('PATH', os.defpath)):
            raise RuntimeUnavailable('后端缺少 Git；Cursor 需要 Git 初始化独立工作区。Docker 部署请在应用镜像中安装 git。')
        self.rpc = LoginProcess([self.executable, '--disable-project-configs', 'login'], env=self.env, cwd=str(self.workspace))
        await self.rpc.start()
        try:
            async with asyncio.timeout(30):
                while True:
                    event = await self.rpc.next_event()
                    if 'url' in event:
                        return {'loginId': 'cursor-browser', 'authUrl': event['url']}
                    if 'exit_code' in event:
                        raise RuntimeFailure('Cursor 未返回登录地址，请检查 CLI 版本、运行依赖及服务器到 Cursor 的网络连接')
        except TimeoutError as exc:
            raise RuntimeFailure('等待 Cursor 登录地址超时（30 秒）；请检查服务器到 Cursor 的网络连接后重试。') from exc

    async def login_wait(self):
        while True:
            event = await self.rpc.next_event()
            if 'exit_code' in event:
                if event['exit_code'] != 0:
                    raise RuntimeFailure('Cursor 登录未完成')
                # Close login before launching ACP; never two credential writers.
                await self.rpc.close()
                self.rpc = CursorRPC([self.executable, '--disable-project-configs', 'acp'], env=self.env, cwd=str(self.workspace))
                return

    def _prepare_workspace(self):
        if self.workspace_prepared:
            return
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Cursor discovers project MCP/rules by walking to the nearest .git.
        # A dedicated repository prevents an app checkout above data/ becoming
        # the agent's project. Empty templates never install host Git hooks.
        git = self.workspace / '.git'
        if git.is_symlink() or (git.exists() and not git.is_dir()):
            raise RuntimeFailure('Cursor 工作区不能引用外部 Git 目录')
        if not git.exists():
            try:
                subprocess.run(['git', '-c', 'init.templateDir=', 'init', '--quiet', str(self.workspace)],
                               env=self.env, check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeFailure('无法初始化 Cursor 独立工作区，请检查 Git 安装') from exc
        # Managed sessions do not import executable project/user extensions.
        # Refuse discovered configuration rather than deleting user artifacts.
        blocked = ('.cursor/mcp.json', '.cursor/hooks.json', '.cursor/plugins',
                   '.cursor/managed/active-team-hooks/hooks.json', '.claude/settings.json',
                   '.claude/settings.local.json', '.claude/plugins', '.mcp.json')
        for root in {self.workspace, self.home}:
            for relative in blocked:
                path = root / relative
                if path.exists() or path.is_symlink():
                    raise RuntimeFailure('Cursor 工作区存在未托管的 MCP、插件或 hooks 配置，请由宿主检查')
        enterprise = Path('/Library/Application Support/Cursor/hooks.json' if sys.platform == 'darwin' else '/etc/cursor/hooks.json')
        if enterprise.exists() or enterprise.is_symlink():
            raise RuntimeFailure('此宿主配置了 Cursor 全局 hooks，当前托管运行不支持')
        self.workspace_prepared = True

    async def _start_acp(self):
        if self.acp_started:
            return
        try:
            cursor_identity(credential_path(self.home).read_bytes())
        except (ValueError, OSError, KeyError, IndexError) as exc:
            raise RuntimeFailure('Cursor 尚未登录，请在设置中连接') from exc
        self._prepare_workspace()
        # Reset permissions on every credential checkout; no permanent approvals.
        existing = self.home / '.cursor/cli-config.json'
        self.account = (json.loads(existing.read_text()).get('authInfo') or {}) if existing.exists() else {}
        config = {'version': 1, 'editor': {'vimMode': False}, 'approvalMode': 'allowlist',
                  'permissions': {'allow': [], 'deny': []},
                  'autoAcceptWebSearch': True}
        private_write(self.home / '.cursor/cli-config.json', json.dumps(config).encode())
        await self.rpc.start()
        response = await self.rpc.request('initialize', {
            'protocolVersion': 1, 'clientCapabilities': {'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False},
            'clientInfo': {'name': 'jellyfish', 'version': '0.1'},
        })
        if response.get('protocolVersion') != 1 or not response.get('agentCapabilities', {}).get('loadSession'):
            raise RuntimeFailure('Cursor ACP 版本不兼容或不支持续话')
        self.agent_capabilities = response.get('agentCapabilities', {})
        # A real browser login has already populated the private file cache.
        await self.rpc.request('authenticate', {'methodId': 'cursor_login'}, timeout=60)
        self.acp_started = True

    @property
    def session_count(self):
        return len(self.loaded_threads)

    async def warm_up(self):
        await self._start_acp()
        # Initialize supplier shared services without creating a dummy chat.
        await self.rpc.request('cursor/list_available_models', {}, timeout=90)
        return {'web_search': True, 'image_generation': True,
                'image_input': bool(self.agent_capabilities.get('promptCapabilities', {}).get('image')),
                'file_input': True}

    async def prepare_history(self, session, legacy_home):
        tid = session.get('thread_id')
        if not tid or tid in self.loaded_threads:
            return
        if not re.fullmatch(r'[a-f0-9-]{36}', tid):
            raise RuntimeFailure('原生会话标识无效')
        target = self.home / '.cursor/acp-sessions' / tid
        source = legacy_home / '.cursor/acp-sessions' / tid
        if (target / 'store.db').exists() or not source.exists():
            return
        from app.runtime.files import safe_read
        meta = safe_read(legacy_home, source / 'meta.json')
        safe_read(legacy_home, source / 'store.db')
        target.mkdir(parents=True, mode=0o700)
        # SQLite backup includes committed WAL records. Do not copy login or
        # user/project configuration while moving this one conversation.
        try:
            with contextlib.closing(sqlite3.connect((source / 'store.db').as_uri() + '?mode=ro', uri=True)) as old:
                with contextlib.closing(sqlite3.connect(target / 'store.db')) as new:
                    old.backup(new)
            private_write(target / 'meta.json', meta)
        except BaseException:
            (target / 'store.db').unlink(missing_ok=True)
            raise

    async def account_models(self):
        await self._start_acp()
        response = await self.rpc.request('session/new', {'cwd': str(self.workspace), 'mcpServers': []}, timeout=60)
        models = self._models(response)
        info = self.account
        return {'email': info.get('email'), 'planType': 'Cursor'}, models

    @staticmethod
    def _models(response):
        models = response.get('models', {}).get('availableModels', [])
        result = [{'id': m['modelId'], 'name': m.get('name', m['modelId'])} for m in models if isinstance(m.get('modelId'), str)]
        if not result:
            raise RuntimeFailure('Cursor 未返回可用模型，请检查账号套餐或 CLI 版本')
        return result

    async def _prepare_request(self, method, params, **kwargs):
        started = time.monotonic()
        try:
            return await self.rpc.request(method, params, **kwargs)
        finally:
            self.prepare_timings[method] = round(time.monotonic() - started, 4)

    async def open_session(self, workspace, instructions, thread_id=None):
        self.prepare_timings = {}
        started = time.monotonic()
        await self._start_acp()
        self.prepare_timings['client_ready'] = round(time.monotonic() - started, 4)
        self.instructions = instructions
        self.workspace_prepared = False
        self._prepare_workspace()
        if thread_id and thread_id in self.loaded_threads:
            self.thread_id = thread_id
            if self.configured_model != self.model:
                await self._prepare_request('session/set_model', {'sessionId': thread_id, 'modelId': self.model})
                self.configured_model = self.model
            return thread_id
        # Loaded sessions already contain the original instructions in history.
        self.instructions_pending = not bool(thread_id)
        servers = []
        if self.dynamic_tools:
            if not self.tool_call:
                raise RuntimeFailure('业务工具桥未就绪')
            from app.runtime.cursor_mcp import CursorMCP
            session_key = getattr(self, 'session_key', str(self.workspace))
            async def call(params):
                if not self.tool_call or getattr(self, 'session_key', str(self.workspace)) != session_key:
                    from fastapi import HTTPException
                    raise HTTPException(409, '没有正在进行的对话轮次')
                return await self.tool_call(params)
            self.mcp = CursorMCP(self.dynamic_tools, call)
            self.bridges[session_key] = self.mcp
            servers = [await self.mcp.start()]
        params = {'cwd': workspace, 'mcpServers': servers}
        if thread_id:
            params['sessionId'] = thread_id
        result = await self._prepare_request('session/load' if thread_id else 'session/new', params, timeout=60)
        self.thread_id = thread_id or result['sessionId']
        self.loaded_threads.add(self.thread_id)
        if self.model not in {m['id'] for m in self._models(result)}:
            raise RuntimeFailure('Cursor 绑定模型已不可用；不会切换模型')
        # Cursor can return a persisted picker selection before its model manager
        # has applied that variant. Trust it only after this client configured it.
        if self.configured_model != self.model or result.get('models', {}).get('currentModelId') != self.model:
            await self._prepare_request('session/set_model', {'sessionId': self.thread_id, 'modelId': self.model})
        self.configured_model = self.model
        if result.get('modes', {}).get('currentModeId') != 'agent':
            await self._prepare_request('session/set_mode', {'sessionId': self.thread_id, 'modeId': 'agent'})
        # session/load replays historical updates before its response. History is
        # already durable in Jellyfish; do not append it to the new run.
        while not self.rpc.events.empty():
            old = self.rpc.events.get_nowait()
            if 'id' in old:
                await self.rpc.send({'id': old['id'], 'error': {'code': -32601, 'message': 'Unsupported during session load'}})
        return self.thread_id

    async def stream_turn(self, thread_id, text):
        content = (self.instructions + '\n\n当前用户消息：\n' if self.instructions_pending else '') + text
        prompt = [{'type': 'text', 'text': content}]
        for f in self.input_files:
            if f['mime'].startswith('image/'):
                from app.runtime.files import safe_read
                prompt.append({'type': 'image', 'mimeType': f['mime'],
                               'data': base64.b64encode(safe_read(self.workspace, Path(f['absolute_path']))).decode()})
            else:
                prompt.append({'type': 'resource_link', 'uri': Path(f['absolute_path']).as_uri(),
                               'name': f['name'], 'mimeType': f['mime']})
        task = asyncio.create_task(self.rpc.request('session/prompt', {'sessionId': thread_id, 'prompt': prompt}, timeout=86400))
        event_task = asyncio.create_task(self.rpc.next_event())
        yield RuntimeEvent('started', {'turn_id': thread_id})
        try:
            while True:
                done, _ = await asyncio.wait({task, event_task}, return_when=asyncio.FIRST_COMPLETED)
                if event_task in done:
                    msg = event_task.result()
                    event_task = asyncio.create_task(self.rpc.next_event())
                    method, p = msg.get('method', '').removeprefix('_'), msg.get('params', {})
                    if p.get('sessionId', thread_id) != thread_id:
                        if 'id' in msg:
                            await self.rpc.send({'id': msg['id'], 'error': {'code': -32602, 'message': 'Wrong session'}})
                        continue
                    if 'id' in msg:
                        async for event in self.request_events(msg):
                            yield event
                    elif method == 'session/update':
                        update = p.get('update', {})
                        kind = update.get('sessionUpdate')
                        if kind == 'agent_message_chunk' and update.get('content', {}).get('type') == 'text':
                            chunk = update['content']['text']
                            # CLI 2026.09.15 emits backend errors as a dedicated
                            # text chunk yet returns stopReason=end_turn. Do not
                            # report this vendor error envelope as a success.
                            if chunk.startswith('\n\nError: ') or chunk in {
                                '\n\nPlease sign in to continue', '\n\nUpgrade your plan to continue',
                                '\n\nAdd a payment method to continue', '\n\nCheck your settings to continue',
                            }:
                                raise RuntimeFailure('Cursor 上游请求失败，请检查网络、账号与配额后重试')
                            yield RuntimeEvent('text_delta', {'text': chunk})
                        elif kind in ('tool_call', 'tool_call_update'):
                            yield RuntimeEvent('tool', {'item_id': update.get('toolCallId'), 'kind': update.get('kind'),
                                                        'status': update.get('status'), 'command': update.get('title')})
                    elif method == 'cursor/generate_image':
                        yield RuntimeEvent('image', {'item_id': p.get('toolCallId'), 'saved_path': p.get('filePath')})
                # Give the event reader one scheduling turn to drain messages
                # preceding the prompt response, including a last text chunk.
                await asyncio.sleep(0)
                if task.done() and self.rpc.events.empty() and not event_task.done():
                    result = task.result()
                    reason = result.get('stopReason')
                    if reason not in ('end_turn', 'cancelled'):
                        raise RuntimeFailure('Cursor 本轮未正常完成，请检查模型与配额')
                    if reason == 'end_turn':
                        self.instructions_pending = False
                    yield RuntimeEvent('cancelled' if reason == 'cancelled' else 'completed', {})
                    return
        finally:
            for pending in (task, event_task):
                if not pending.done():
                    pending.cancel()
                with contextlib.suppress(asyncio.CancelledError, RuntimeFailure):
                    await pending

    async def request_events(self, msg):
        request_id, method, params = msg['id'], msg.get('method', '').removeprefix('_'), msg.get('params', {})
        if method == 'cursor/ask_question':
            await self.respond(request_id, {'outcome': {'outcome': 'skipped', 'reason': 'Ask the user in a normal chat message.'}})
            yield RuntimeEvent('notice', {'message': '引擎的问题将通过聊天继续确认'})
            return
        if method in ('cursor/update_todos', 'cursor/task'):
            await self.respond(request_id, {})
            return
        if method == 'cursor/generate_image':
            await self.respond(request_id, {})
            yield RuntimeEvent('image', {'item_id': params.get('toolCallId'), 'saved_path': params.get('filePath')})
            return
        if method == 'cursor/create_plan':
            self.permission_options[request_id] = {'plan': True}
            yield RuntimeEvent('request', {'request_id': request_id,
                'method': 'item/commandExecution/requestApproval',
                'params': {'command': 'Cursor 计划：' + str(params.get('name', ''))[:500],
                           'reason': str(params.get('plan') or params.get('overview') or '')[:32000]}})
            return
        if method != 'session/request_permission':
            yield RuntimeEvent('request', {'request_id': request_id, 'method': method, 'params': params})
            return
        options = params.get('options') or []
        allow = next((o['optionId'] for o in options if o.get('kind') == 'allow_once' and isinstance(o.get('optionId'), str)), None)
        reject = next((o['optionId'] for o in options if o.get('kind') == 'reject_once' and isinstance(o.get('optionId'), str)), None)
        self.permission_options[request_id] = {'accept': allow, 'decline': reject}
        tool = params.get('toolCall') or {}
        kind, title = tool.get('kind'), str(tool.get('title') or '')[:8000]
        normalized = {'itemId': tool.get('toolCallId'), 'availableDecisions': ['accept', 'decline'] if allow else ['decline']}
        if kind == 'edit':
            changes = [{'path': c['path'], 'diff': str(c.get('newText', ''))[:32000]}
                       for c in tool.get('content', []) if c.get('type') == 'diff' and isinstance(c.get('path'), str)]
            if not changes:
                changes = [{'path': loc['path']} for loc in tool.get('locations', []) if isinstance(loc.get('path'), str)]
            yield RuntimeEvent('tool', {'item_id': tool.get('toolCallId'), 'kind': 'fileChange', 'changes': changes})
            normalized['reason'] = title
            normalized_method = 'item/fileChange/requestApproval'
        else:
            normalized_method = 'item/commandExecution/requestApproval'
            business_titles = {value for t in self.dynamic_tools for value in
                               (f"jellyfish: {t['name']}", f"jellyfish-{t['name']}: {t['name']}")}
            normalized['command'] = title if kind in ('execute', 'search', 'fetch') or (kind == 'other' and title in business_titles) else None
            normalized['reason'] = '\n'.join([title, *(str(c.get('content', {}).get('text', '')) for c in tool.get('content', []) if c.get('type') == 'content')])[:32000]
        yield RuntimeEvent('request', {'request_id': request_id, 'method': normalized_method, 'params': normalized})

    async def respond(self, request_id, result):
        options = self.permission_options.pop(request_id, None)
        if options is not None:
            decision = result.get('decision')
            if options.get('plan'):
                result = {'outcome': {'outcome': 'accepted' if decision == 'accept' else 'rejected'}}
            else:
                option = options.get(decision)
                result = {'outcome': {'outcome': 'selected', 'optionId': option} if option else {'outcome': 'cancelled'}}
        await self.rpc.send({'id': request_id, 'result': result})

    async def cancel(self):
        await self.rpc.capture_children()
        if self.thread_id:
            await self.rpc.send({'method': 'session/cancel', 'params': {'sessionId': self.thread_id}})

    async def close(self):
        try:
            await self.rpc.close()
        finally:
            for bridge in self.bridges.values():
                await bridge.close()
            self.bridges.clear()
