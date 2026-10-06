"""Durable, bounded local runs. Browser lifetime never owns execution lifetime."""
import asyncio
import contextlib
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from app.runtime.files import collect_files, digest, import_documents
from app.runtime.rpc import RuntimeFailure
from app.runtime.store import TERMINAL

MAX_CHAT_MESSAGE_CHARS = 32000


def new_id():
    return uuid.uuid4().hex


class RunService:
    def __init__(self, store, policy, backend, authorize, *, storage=None, tool_bridge=None):
        self.store, self.policy, self.backend, self.authorize = store, policy, backend, authorize
        self.storage, self.tool_bridge = storage, tool_bridge
        self.active = {}
        self.closed = False
        self.changed = asyncio.Event()
        self.dispatcher = None

    def start(self):
        if not self.dispatcher:
            self.dispatcher = asyncio.create_task(self._schedule())

    def own(self, kind, record_id, actor_id):
        row = self.store.get(kind, record_id)
        if not row or row['actor_id'] != actor_id:
            raise HTTPException(404, '记录不存在')
        return row

    def create_session(self, actor_id, binding, *, instructions='', context_paths=None, conversation_id=None,
                       dynamic_tools=None):
        self.authorize(actor_id, binding)
        session = {'id': new_id(), 'actor_id': actor_id, 'data_owner_id': actor_id,
                   'binding': dict(binding), 'conversation_id': conversation_id,
                   'created_at': time.time(), 'thread_id': None, 'instructions_sent': False, 'baseline': {}, 'artifacts': [],
                   'instructions': instructions, 'dynamic_tools': dynamic_tools or []}
        workspace = self.backend.workspace(session)
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        if context_paths:
            session['baseline'] = import_documents(self.storage, actor_id, workspace, context_paths)
        self.store.put('session', session)
        return session

    def enqueue(self, actor_id, sid, request_id, message, model=None, attachments=None, *, yolo=False,
                channel='web'):
        if self.closed:
            raise HTTPException(503, '运行服务正在停止')
        session = self.own('session', sid, actor_id)
        binding = {**session['binding'], **({'model': model} if model is not None else {})}
        self.authorize(actor_id, binding)
        # Per-turn consent belongs to admin chat, never to a shared connection
        # or a Service visitor. Snapshot it before enqueueing and retries.
        yolo = yolo is True and not bool(binding.get('service_scope') or binding.get('scheduler_scope'))
        from app.runtime.media import decode_inputs, fingerprint, save_inputs
        inputs = decode_inputs(attachments or [])
        if not isinstance(message, str) or len(message) > MAX_CHAT_MESSAGE_CHARS or (not message.strip() and not inputs):
            raise HTTPException(400, '请输入消息或添加附件')
        prior = self.store.find('run', actor_id=actor_id, request_id=request_id)
        if prior:
            if prior[0]['session_id'] != sid or prior[0]['message'] != message or fingerprint(prior[0].get('attachments', [])) != fingerprint(inputs) or (model is not None and prior[0]['binding']['model'] != model) or bool(prior[0].get('yolo')) != yolo or prior[0].get('channel', 'web') != channel:
                raise HTTPException(409, '同一 request_id 不可用于不同请求')
            return prior[0]
        unfinished = [r for r in self.store.all('run') if r['status'] not in TERMINAL]
        if any(r['session_id'] == sid for r in unfinished):
            raise HTTPException(409, '此会话已有待完成任务')
        queued = [r for r in unfinished if r['status'] == 'queued']
        if len(queued) >= self.policy.max_queued or sum(r['actor_id'] == actor_id for r in queued) >= self.policy.max_queued_per_actor:
            raise HTTPException(429, '等待队列已满，请稍后重试')
        run = {'id': new_id(), 'session_id': sid, 'actor_id': actor_id, 'data_owner_id': actor_id,
               'binding': binding, 'request_id': request_id, 'message': message,
               'channel': channel,
               'status': 'queued', 'seq': 0, 'pending': None, 'created_at': time.time(),
               'output': '', 'usage': None, 'artifacts': [], 'yolo': yolo}
        run['attachments'] = save_inputs(self.backend.workspace(session), run['id'], inputs)
        session['binding'] = dict(binding)
        self.store.put('session', session)
        self.store.emit(run, 'queued', {'run_id': run['id']}, status='queued')
        self.changed.set()
        self.start()
        return run

    async def _schedule(self):
        while not self.closed:
            self.changed.clear()
            # Recheck permissions on queued AND active runs (including disabled users).
            for run in self.store.all('run'):
                if run['status'] in TERMINAL:
                    continue
                try:
                    self.authorize(run['actor_id'], run['binding'])
                except Exception:
                    await self.cancel(run['actor_id'], run['id'])
            if hasattr(self.backend, 'reap'):
                await self.backend.reap(self.authorize)
            busy = {v['run']['binding']['profile_id'] for v in self.active.values()}
            queued = sorted(self.store.find('run', status='queued'), key=lambda r: r['created_at'])
            for run in queued:
                if len(self.active) >= self.policy.max_running:
                    break
                pid = run['binding']['profile_id']
                if pid in busy:
                    continue
                session = self.store.get('session', run['session_id'])
                session['binding'] = dict(run['binding'])  # This run's immutable model selection.
                state = {'run': run, 'adapter': None, 'answer': None, 'cancel': False, 'finalizing': False, 'changes': {}}
                self.store.emit(run, 'starting', {}, status='starting')
                self.active[run['id']] = state
                busy.add(pid)
                state['task'] = asyncio.create_task(self._execute(session, state))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.changed.wait(), .5)

    def _emit(self, state, kind, payload=None, status=None):
        return self.store.emit(state['run'], kind, payload, status=status)

    def _document_snapshot(self, actor_id, path):
        """Read the approved target state without holding a workspace lock."""
        if self.storage is None:
            raise ValueError('文档存储不可用')
        # The storage root check prevents escaping the admin's filesystem, but
        # an in-root symlink could still cross from /docs to another area.
        from app.storage.local import LocalStorageService, _fs_root
        if isinstance(self.storage, LocalStorageService):
            current = Path(_fs_root(actor_id))
            for part in path.lstrip('/').split('/'):
                current = current / part
                if current.is_symlink():
                    raise ValueError('文档路径不能包含符号链接')
        target = Path(path)
        entries = self.storage.list_dir(actor_id, str(target.parent))
        entry = next((row for row in entries if row.name == target.name), None)
        if entry is None:
            if self.storage.exists(actor_id, path):
                raise ValueError('文档路径已被占用')
            return None, ''
        if entry.is_dir or entry.size > 256 * 1024:
            raise ValueError('文档是目录或超过可审核的 256KB 上限')
        data = self.storage.read_bytes(actor_id, path)
        if len(data) > 256 * 1024:
            raise ValueError('文档超过可审核的 256KB 上限')
        preview = data[:2048].decode('utf-8', errors='replace')
        if len(data) > 2048:
            preview += '\n…（仅预览前 2048 字节）'
        return digest(data), preview

    async def _call_business_tool(self, session, state, params):
        """Apply one admin write approval before either CLI reaches the bridge."""
        if (params.get('tool') != 'jellyfish_write_document'
                or session['binding'].get('service_scope')
                or session['binding'].get('scheduler_scope')):
            return await self.tool_bridge(session, state['run'], params)

        from app.runtime.business_tools import BusinessTools, DocumentWrite, docs_path
        from pydantic import ValidationError
        failure = lambda message: BusinessTools.result(message, False)
        try:
            args = DocumentWrite.model_validate(params.get('arguments', {}))
            path = docs_path(args.path)
            if args.path.endswith('/') or any(c in args.path for c in ('\x00', '\r', '\n')):
                raise ValueError('文档路径必须是 /docs 内的文件')
            encoded = args.content.encode('utf-8')
            if len(encoded) > 65536:
                raise ValueError('文档单次写入不能超过 64KB')
            before, old_preview = self._document_snapshot(session['actor_id'], path)
            if before is not None and not args.overwrite:
                # A matching file still goes through approval: it might change
                # before the bridge checks its idempotent-write condition.
                current = self.storage.read_bytes(session['actor_id'], path)
                if current != encoded:
                    return failure('文档已存在且内容不同；如需替换，请设置 overwrite=true。')
        except (ValidationError, ValueError, OSError, UnicodeError) as exc:
            return failure(f'文档写入请求无效：{str(exc)[:200]}')

        if state.get('answer') is not None or state['run'].get('pending'):
            return failure('已有待处理的审批；请稍后重试文档写入。')
        self.authorize(session['actor_id'], session['binding'])
        kind = 'business_tool/jellyfish_write_document'
        if state['run'].get('yolo'):
            self._emit(state, 'approval_resolved', {
                'approval_id': new_id(), 'kind': kind, 'decision': 'accept', 'automatic': True,
            }, status='running')
        else:
            pending = {
                'id': new_id(), 'kind': kind, 'allowed': ['accept', 'decline'],
                'command': f'保存文档库文件：{path}',
                'reason': '请核对文件路径、原内容预览和拟写入的完整内容。',
                'changes': [{'path': path, 'old_text': old_preview, 'new_text': args.content}],
            }
            future = asyncio.get_running_loop().create_future()
            state['run']['pending'] = pending
            state['answer'] = future
            self._emit(state, 'approval_requested', {'approval': pending}, status='waiting_approval')
            decision = None
            try:
                try:
                    decision = await asyncio.wait_for(future, self.policy.approval_timeout)
                except asyncio.TimeoutError:
                    decision = 'decline'
            finally:
                state['run']['pending'] = None
                if state.get('answer') is future:
                    state['answer'] = None
                # A cancelled MCP HTTP call must not leave an actionable
                # approval behind if the native turn keeps running.
                if state['run']['status'] in TERMINAL:
                    self.store.put('run', state['run'])
                else:
                    self._emit(state, 'approval_resolved', {
                        'approval_id': pending['id'], 'kind': kind,
                        'decision': decision or 'decline',
                    }, status='running')
            if decision != 'accept':
                return failure('本次文档写入已拒绝或审批超时，文件未保存。')

        if state['cancel'] or state['finalizing']:
            return failure('本轮运行已结束，文件未保存。')
        self.authorize(session['actor_id'], session['binding'])
        try:
            after, _ = self._document_snapshot(session['actor_id'], path)
        except (ValueError, OSError, UnicodeError):
            return failure('审批期间文档状态发生变化，文件未保存；请重新读取并重试。')
        if after != before:
            return failure('审批期间文档状态发生变化，文件未保存；请重新读取并重试。')
        safe_params = {**params, 'arguments': {**args.model_dump(), 'path': path}}
        return await self.tool_bridge(session, state['run'], safe_params)

    async def _request(self, session, state, payload):
        self.authorize(session['actor_id'], session['binding'])
        method, params, req_id = payload['method'], payload['params'], payload['request_id']
        adapter = state['adapter']
        if method == 'item/tool/call' and self.tool_bridge:
            if session['binding'].get('scheduler_scope'):
                params = {**params, 'callId': str(req_id)}
            result = await self._call_business_tool(session, state, params)
            self.authorize(session['actor_id'], session['binding'])
            await adapter.respond(req_id, result)
            return
        if method == 'item/permissions/requestApproval':
            await adapter.respond(req_id, {'permissions': {}, 'scope': 'turn'})
            return
        is_plan = method == 'plan/requestApproval' and session['binding'].get('runtime') == 'cursor'
        if method not in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval') and not is_plan:
            await adapter.rpc.send({'id': req_id, 'error': {'code': -32601, 'message': 'Unsupported request'}})
            self._emit(state, 'notice', {'message': '当前运行服务未支持此类请求，已拒绝'})
            return
        if session['binding'].get('service_scope') or session['binding'].get('scheduler_scope'):
            offered = params.get('availableDecisions') or ['decline']
            await adapter.respond(req_id, {'decision': 'decline' if 'decline' in offered else 'cancel'})
            self._emit(state, 'notice', {'message': '受限运行不支持计划审批' if is_plan else '受限运行仅允许授权的业务工具，已拒绝原生命令或文件修改'})
            return
        if state.get('answer') is not None or state['run'].get('pending'):
            offered = params.get('availableDecisions') or ['decline']
            decision = 'decline' if 'decline' in offered else 'cancel'
            await adapter.respond(req_id, {'decision': decision})
            self._emit(state, 'notice', {'message': '已有待处理的审批，本次操作已拒绝；请稍后重试'})
            return
        workspace = self.backend.workspace(session).resolve()
        allowed = ['accept', 'decline']
        changes = state['changes'].get(params.get('itemId'))
        if is_plan:
            # A plan is a separate human decision, not an executable command.
            # Keep title and body distinct in the durable pending record.
            title = str(params.get('title') or '')[:500]
            plan = str(params.get('plan') or '')[:32000]
        elif method == 'item/fileChange/requestApproval':
            if not changes:
                allowed = ['decline']
            for change in changes or []:
                try:
                    p = Path(change['path'])
                    (p if p.is_absolute() else workspace / p).resolve().relative_to(workspace)
                except (KeyError, ValueError):
                    allowed = ['decline']
        elif not params.get('command'):
            allowed = ['decline']
        if not is_plan:
            for key in ('cwd', 'grantRoot'):
                if params.get(key):
                    try:
                        Path(params[key]).resolve().relative_to(workspace)
                    except ValueError:
                        allowed = ['decline']
            if params.get('additionalPermissions') or params.get('networkApprovalContext'):
                allowed = ['decline']
        decisions = {d: d for d in allowed}
        if params.get('availableDecisions'):
            offered = params['availableDecisions']
            if 'decline' not in offered and 'cancel' in offered:
                decisions['decline'] = 'cancel'
            allowed = [d for d in allowed if decisions[d] in offered]
        if not allowed:
            raise RuntimeFailure('审批选项不受支持')
        if state['run'].get('yolo') and not is_plan:
            # Reuse the same authorization and workspace checks as manual
            # approval. YOLO skips the human wait; it grants no extra access.
            decision = 'accept' if 'accept' in allowed else 'decline'
            self.authorize(session['actor_id'], session['binding'])
            await adapter.respond(req_id, {'decision': decisions[decision]})
            self._emit(state, 'approval_resolved', {
                'approval_id': new_id(), 'kind': method, 'decision': decision, 'automatic': True,
            }, status='running')
            if decision == 'decline':
                self._emit(state, 'notice', {'message': 'YOLO 已拒绝超出当前权限或无法核实的操作'})
            return
        pending = {'id': new_id(), 'kind': method, 'allowed': allowed,
                   'command': ('计划：' + title) if is_plan else params.get('command'),
                   'reason': plan if is_plan else params.get('reason'),
                   'changes': None if is_plan else changes}
        if is_plan:
            pending.update(title=title, plan=plan)
        state['run']['pending'] = pending
        state['answer'] = asyncio.get_running_loop().create_future()
        self._emit(state, 'approval_requested', {'approval': pending}, status='waiting_approval')
        try:
            decision = await asyncio.wait_for(state['answer'], self.policy.approval_timeout)
            self.authorize(session['actor_id'], session['binding'])
            await adapter.respond(req_id, {'decision': decisions[decision]})
            state['run']['pending'] = None
            self._emit(state, 'approval_resolved', {'approval_id': pending['id'], 'decision': decision}, status='running')
        finally:
            state['answer'] = None

    async def _execute(self, session, state):
        run = state['run']
        terminal, failure = 'failed', '执行未完成'
        buffer = []
        last_flush = time.monotonic()

        def flush():
            nonlocal last_flush
            if buffer:
                text = ''.join(buffer)
                run['output'] += text
                if run.get('first_token_at') is None:
                    run['first_token_at'] = time.time()
                self._emit(state, 'text_delta', {'text': text})
                buffer.clear()
            last_flush = time.monotonic()

        try:
            async with asyncio.timeout(self.policy.run_timeout):
                self.authorize(session['actor_id'], session['binding'])
                admin_scope = (bool(session.get('conversation_id'))
                               and not session['binding'].get('service_scope')
                               and not session['binding'].get('scheduler_scope'))
                updated_instructions = (not (session['binding'].get('service_scope')
                                             or session['binding'].get('scheduler_scope'))
                                        and session.get('instructions_version', 1) < 5
                                        and bool(self.tool_bridge))
                if admin_scope and self.tool_bridge:
                    from app.runtime.business_tools import specifications
                    previous = {tool.get('name') for tool in session.get('dynamic_tools', [])}
                    # Dynamic tools are registered only at Codex thread/start.
                    # A live old native thread cannot acquire the new bridge
                    # via thread/resume or turn/start. Preserve its history and
                    # mark the unavailable capability instead of claiming it.
                    existing_codex = (session['binding'].get('runtime') == 'codex'
                                      and bool(session.get('thread_id')))
                    session['project_brief_write_available'] = (
                        not existing_codex or 'jellyfish_write_project_brief' in previous)
                    session['document_write_available'] = (
                        not existing_codex or 'jellyfish_write_document' in previous)
                    if not existing_codex:
                        # Cursor's connection backend restarts an idle ACP client
                        # when this session's MCP tools change, then session/load
                        # registers the new bridge against its native history.
                        session['dynamic_tools'] = specifications()
                    if (updated_instructions and existing_codex
                            and not session['document_write_available']
                            and not session.get('document_write_notice_sent')):
                        self._emit(state, 'notice', {'message':
                            '这条 Codex 会话无法追加文档库写入工具；如需写入 /docs，请新建 Codex 对话。'})
                        session['document_write_notice_sent'] = True
                        self.store.put('session', session)
                if updated_instructions:
                    from app.runtime.business_tools import instructions
                    session['instructions'] = instructions(
                        session['actor_id'], session['binding']['runtime'],
                        project_brief_write=session.get('project_brief_write_available', False),
                        document_write_available=session.get('document_write_available', False),
                    )
                async with self.backend.execution(session) as adapter:
                    state['adapter'] = adapter
                    adapter.model = run['binding']['model']
                    adapter.image_mode = run['binding'].get('image_mode', 'native')
                    adapter.reusable = False
                    run['client_reused'] = getattr(adapter, 'reused', False)
                    run['client_acquired_at'] = time.time()
                    workspace = self.backend.workspace(session)
                    from app.runtime.media import input_files
                    adapter.input_files = input_files(workspace, run.get('attachments', []))
                    adapter.dynamic_tools = session.get('dynamic_tools', [])
                    adapter.service_scope = (session['binding'].get('service_scope') or
                                             session['binding'].get('scheduler_scope'))
                    if self.tool_bridge:
                        async def tool_call(params):
                            if state['finalizing'] or state['adapter'] is not adapter or state['cancel']:
                                raise HTTPException(409, '本轮工具调用已结束')
                            self.authorize(session['actor_id'], session['binding'])
                            return await self._call_business_tool(session, state, params)
                        adapter.tool_call = tool_call
                    instructions_sent = session.get('instructions_sent', bool(session['thread_id']))
                    session['thread_id'] = await adapter.open_session(str(workspace), session['instructions'], session['thread_id'])
                    self.store.put('session', session)
                    if hasattr(adapter, 'instructions_pending'):
                        adapter.instructions_pending = not instructions_sent or updated_instructions
                    run['prepare_timings'] = dict(getattr(adapter, 'prepare_timings', {}))
                    run['started_at'] = time.time()
                    process = getattr(getattr(adapter, 'rpc', None), 'process', None)
                    if process:
                        run['process_pid'] = process.pid
                    self._emit(state, 'running', {'started_at': run['started_at'], 'client_acquired_at': run['client_acquired_at']}, status='running')
                    # A separate reader keeps the 60ms flush deadline even if the
                    # provider pauses after a small delta. Backpressure is bounded.
                    queue = asyncio.Queue(maxsize=128)

                    # Admin project context is read at execution time, not
                    # session creation or enqueue time. A moved conversation or
                    # edited brief takes effect on the next actual turn. Keep
                    # the persisted run.message untouched for history, retry
                    # identity, and search.
                    provider_message = run['message']
                    adapter.project_context = ''
                    if (session.get('conversation_id')
                            and not session['binding'].get('service_scope')
                            and not session['binding'].get('scheduler_scope')
                            and run.get('channel') != 'voice'):
                        from app.services.project_context import context_for_conversation
                        from app.services.service_test import review_context
                        project_context = (context_for_conversation(
                            session['actor_id'], session['conversation_id']) +
                            review_context(session['actor_id'], session['conversation_id']))
                        if project_context:
                            if session['binding'].get('runtime') == 'codex':
                                # The current Codex app-server has a per-turn
                                # untrusted context field, separate from the
                                # persistent user input.
                                adapter.project_context = project_context
                            else:
                                # Cursor ACP exposes only session/prompt text.
                                # Older copies may remain in native history;
                                # the 5k ceiling applies to this turn's copy.
                                provider_message = project_context + provider_message

                    async def read():
                        try:
                            async for event in adapter.stream_turn(session['thread_id'], provider_message):
                                await queue.put(event)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            await queue.put(exc)
                        await queue.put(None)

                    reader = asyncio.create_task(read())
                    try:
                        while True:
                            try:
                                event = await asyncio.wait_for(queue.get(), .06)
                            except asyncio.TimeoutError:
                                flush()
                                continue
                            if event is None:
                                break
                            if isinstance(event, BaseException):
                                raise event
                            if event.type == 'text_delta':
                                buffer.append(event.payload.get('text', ''))
                                if len(run['output']) + sum(map(len, buffer)) > 2_000_000:
                                    raise RuntimeFailure('输出超过本轮大小限制')
                                if run.get('first_token_at') is None or time.monotonic() - last_flush >= .06 or sum(map(len, buffer)) >= 1024:
                                    flush()
                                continue
                            flush()
                            if event.type == 'request':
                                await self._request(session, state, event.payload)
                            elif event.type in ('completed', 'cancelled'):
                                terminal = event.type
                                if terminal == 'cancelled':
                                    failure = '执行已停止'
                            elif event.type == 'image':
                                if run['binding'].get('image_mode', 'native') == 'off':
                                    raise RuntimeFailure('当前会话未开启原生生图')
                                scope = session['binding'].get('service_scope') or session['binding'].get('scheduler_scope')
                                if scope and not scope.get('image'):
                                    raise RuntimeFailure('当前运行未获得生图授权')
                                from app.runtime.media import native_image
                                try:
                                    path = native_image(workspace, event.payload,
                                        getattr(adapter, 'home', None) if session['binding']['runtime'] == 'codex' else None,
                                        session['thread_id'])
                                except (ValueError, OSError):
                                    raise RuntimeFailure('原生图片未能安全归档，请让引擎保存到本聊天工作区')
                                if path:
                                    state.setdefault('image_paths', []).append(path)
                                self._emit(state, 'tool', {'item_id': event.payload.get('item_id'),
                                    'kind': 'imageGeneration', 'status': 'completed' if path else 'failed'})
                                if not path:
                                    self._emit(state, 'notice', {'message': '客户端未返回可用图片；请检查模型或账号的生图能力'})
                            else:
                                if run['seq'] >= 50000:
                                    raise RuntimeFailure('事件数量超过本轮限制')
                                if (event.type == 'tool' and event.payload.get('kind') == 'imageGeneration'
                                        and run['binding'].get('image_mode', 'native') == 'off'):
                                    raise RuntimeFailure('当前会话未开启原生生图')
                                if event.type == 'tool' and event.payload.get('changes'):
                                    state['changes'][event.payload['item_id']] = event.payload['changes']
                                    # An exact workspace-relative key lets the UI link only archived
                                    # files, without treating a provider's host path as a storage path.
                                    for change in event.payload['changes']:
                                        source = Path(change.get('path', ''))
                                        source = source if source.is_absolute() else workspace / source
                                        try:
                                            change['workspace_path'] = source.resolve().relative_to(workspace.resolve()).as_posix()
                                        except ValueError:
                                            pass
                                if event.type == 'usage':
                                    run['usage'] = event.payload.get('usage')
                                self._emit(state, event.type, event.payload)
                    finally:
                        reader.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await reader
                    flush()
                    adapter.reusable = terminal == 'completed' and not state['cancel']
                    if adapter.reusable:
                        session['instructions_sent'] = True
                        session['instructions_version'] = 5
                        session['connection_history'] = session.get('connection_history', False) or hasattr(self.backend, 'connection_home')
                        self.store.put('session', session)
                    adapter.tool_call = None
                state['adapter'] = None
        except asyncio.CancelledError:
            terminal, failure = 'cancelled', '执行已停止'
        except TimeoutError:
            terminal, failure = 'failed', '执行或审批等待超时'
        except (HTTPException, RuntimeFailure) as exc:
            terminal, failure = 'failed', str(exc.detail if isinstance(exc, HTTPException) else exc)
        except Exception:
            terminal, failure = 'failed', '运行协议或执行后端失败，请检查连接状态'
        finally:
            state['finalizing'] = True
            flush()
            if state['cancel']:
                terminal = 'cancelled'
            if terminal == 'completed' and self.storage:
                try:
                    self.authorize(session['actor_id'], session['binding'])
                    await self._archive(session, state)
                except Exception:
                    terminal, failure = 'failed', '产物归档未完成；源文件已保留在运行工作区'
            run['error'] = failure if terminal != 'completed' else None
            self._emit(state, terminal, {'message': failure} if terminal != 'completed' else {}, status=terminal)
            self.active.pop(run['id'], None)
            self.changed.set()

    async def _archive(self, session, state):
        from app.services import workspace_lock as wl
        run = state['run']
        workspace = self.backend.workspace(session)
        files = await asyncio.to_thread(collect_files, workspace, session['baseline'], state.get('image_paths', []))
        has_native_image = any(native for *_item, native in files)
        if session['binding'].get('image_mode', 'native') == 'off' and has_native_image:
            raise RuntimeFailure('当前会话未开启原生生图')
        scope = session['binding'].get('service_scope') or session['binding'].get('scheduler_scope')
        if scope and not scope.get('image') and has_native_image:
            raise RuntimeFailure('当前运行未获得生图授权')
        scheduler_scope = session['binding'].get('scheduler_scope')
        if scheduler_scope and any(not native for _rel, _data, _sha, _mime, native in files):
            raise RuntimeFailure('定时任务不接受未授权的原生文件写入')
        root = f"/generated/runtime/{session['id']}/{run['id']}"
        lock_id = f"runtime-{run['id']}"
        if not scheduler_scope:
            wl.register_process(lock_id, session['actor_id'], kind='runtime', label='Agent 产物归档')
        try:
            if scheduler_scope:
                owner = 'scheduled-' + scheduler_scope['run_id']
                process = wl.get_process(owner)
                if not process or process.user_id != session['actor_id'] or not wl.is_write_allowed(owner, root):
                    raise RuntimeFailure('定时任务没有产物目录的写锁')
            elif not wl.try_acquire(lock_id, [root]).ok:
                raise RuntimeFailure('产物目录被占用')
            for rel, data, sha, mime, native in files:
                if state['cancel']:
                    raise RuntimeFailure('归档已取消')
                path = root + '/' + rel
                self.authorize(session['actor_id'], session['binding'])
                scope = session['binding'].get('service_scope')
                if scope:
                    await asyncio.to_thread(self.storage.write_consumer_bytes, session['actor_id'], scope['service_id'],
                                            scope['conversation_id'], path.removeprefix('/generated/'), data)
                else:
                    if scheduler_scope:
                        from app.runtime.consumer import authorize_scheduler
                        grant = authorize_scheduler(session['actor_id'], session['binding'])
                        if not scheduler_scope.get('image'):
                            raise RuntimeFailure('定时任务未获得生图授权')
                        grant.path(path, write=True)
                        effect_id = f"cli:{grant.run['id']}:artifact:{run['id']}:{rel}"
                        grant.execution.store.effect_start(grant.run['id'], grant.run['token'], effect_id,
                            {'kind': 'native_image', 'path': path, 'sha256': sha})
                        self.authorize(session['actor_id'], session['binding'])
                    if scheduler_scope:
                        write = asyncio.create_task(asyncio.to_thread(
                            self.storage.write_bytes_durable, session['actor_id'], path, data))
                        try:
                            await asyncio.shield(write)
                        except asyncio.CancelledError:
                            await asyncio.shield(write)
                            raise
                    else:
                        await asyncio.to_thread(self.storage.write_bytes, session['actor_id'], path, data)
                    if scheduler_scope:
                        grant.execution.store.effect_done(grant.run['id'], grant.run['token'], effect_id,
                            {'path': path, 'sha256': sha})
                artifact = {'id': new_id(), 'name': rel, 'path': path, 'sha256': sha, 'mime': mime,
                            'size': len(data), 'native_image': native, 'run_id': run['id']}
                session['artifacts'].append(artifact)
                session['baseline'][rel] = sha
                run['artifacts'].append(artifact)
                self.store.put('session', session)
                self._emit(state, 'artifact_created', {'artifact': artifact})
        finally:
            if not scheduler_scope:
                wl.unregister_process(lock_id)

    def approve(self, actor_id, rid, approval_id, decision):
        run = self.own('run', rid, actor_id)
        self.authorize(actor_id, run['binding'])
        state = self.active.get(rid)
        pending = run.get('pending')
        if not state or not pending or pending['id'] != approval_id or not state['answer'] or state['answer'].done():
            raise HTTPException(409, '审批已失效或已处理')
        if decision not in pending['allowed']:
            raise HTTPException(400, '不支持的审批选项')
        state['answer'].set_result(decision)

    async def cancel(self, actor_id, rid):
        run = self.own('run', rid, actor_id)
        if run['status'] in TERMINAL:
            return
        state = self.active.get(rid)
        if not state:
            self.store.emit(run, 'cancelled', {'message': '已取消排队'}, status='cancelled')
            self.changed.set()
            return
        if not state.get('stop_task'):
            state['cancel'] = True
            state['stop_task'] = asyncio.create_task(self._stop(state, rid))
        # A disconnected/cancelled HTTP caller cannot interrupt process cleanup.
        await asyncio.shield(state['stop_task'])
        if hasattr(self.backend, 'release'):
            await self.backend.release(session_id=run['session_id'])

    async def _stop(self, state, rid):
        answer = state.get('answer')
        if answer is not None and not answer.done():
            answer.cancel()
        if state['adapter'] and not state['finalizing']:
            with contextlib.suppress(Exception):
                await state['adapter'].cancel()
        if not state['finalizing']:
            state['task'].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state['task']
        if rid in self.active:
            self.store.emit(state['run'], 'cancelled', {}, status='cancelled')
            self.active.pop(rid, None)
            self.changed.set()

    async def cancel_profile(self, profile_id, actor_id=None):
        for run in self.store.all('run'):
            if run['status'] not in TERMINAL and run['binding']['profile_id'] == profile_id and (actor_id is None or run['actor_id'] == actor_id):
                await self.cancel(run['actor_id'], run['id'])
        if hasattr(self.backend, 'release'):
            await self.backend.release(profile_id=profile_id, actor_id=actor_id)

    async def shutdown(self):
        self.closed = True
        if self.dispatcher:
            self.dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.dispatcher
        for run in self.store.all('run'):
            if run['status'] not in TERMINAL:
                await self.cancel(run['actor_id'], run['id'])

        if hasattr(self.backend, 'shutdown'):
            await self.backend.shutdown()
