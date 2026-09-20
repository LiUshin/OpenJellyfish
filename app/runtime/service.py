"""Durable, bounded local runs. Browser lifetime never owns execution lifetime."""
import asyncio
import contextlib
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from app.runtime.files import collect_files, import_documents
from app.runtime.rpc import RuntimeFailure
from app.runtime.store import TERMINAL


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

    def enqueue(self, actor_id, sid, request_id, message, model=None, attachments=None, *, yolo=False):
        if self.closed:
            raise HTTPException(503, '运行服务正在停止')
        session = self.own('session', sid, actor_id)
        binding = {**session['binding'], **({'model': model} if model is not None else {})}
        self.authorize(actor_id, binding)
        # Per-turn consent belongs to admin chat, never to a shared connection
        # or a Service visitor. Snapshot it before enqueueing and retries.
        yolo = yolo is True and not bool(binding.get('service_scope'))
        from app.runtime.media import decode_inputs, fingerprint, save_inputs
        inputs = decode_inputs(attachments or [])
        if not isinstance(message, str) or len(message) > 32000 or (not message.strip() and not inputs):
            raise HTTPException(400, '请输入消息或添加附件')
        prior = self.store.find('run', actor_id=actor_id, request_id=request_id)
        if prior:
            if prior[0]['session_id'] != sid or prior[0]['message'] != message or fingerprint(prior[0].get('attachments', [])) != fingerprint(inputs) or (model is not None and prior[0]['binding']['model'] != model) or bool(prior[0].get('yolo')) != yolo:
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

    async def _request(self, session, state, payload):
        self.authorize(session['actor_id'], session['binding'])
        method, params, req_id = payload['method'], payload['params'], payload['request_id']
        adapter = state['adapter']
        if method == 'item/tool/call' and self.tool_bridge:
            result = await self.tool_bridge(session, state['run'], params)
            self.authorize(session['actor_id'], session['binding'])
            await adapter.respond(req_id, result)
            return
        if method == 'item/permissions/requestApproval':
            await adapter.respond(req_id, {'permissions': {}, 'scope': 'turn'})
            return
        if method not in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
            await adapter.rpc.send({'id': req_id, 'error': {'code': -32601, 'message': 'Unsupported request'}})
            self._emit(state, 'notice', {'message': '当前运行服务未支持此类请求，已拒绝'})
            return
        if session['binding'].get('service_scope'):
            offered = params.get('availableDecisions') or ['decline']
            await adapter.respond(req_id, {'decision': 'decline' if 'decline' in offered else 'cancel'})
            self._emit(state, 'notice', {'message': 'Service 仅允许已发布的业务工具，已拒绝原生命令或文件修改'})
            return
        workspace = self.backend.workspace(session).resolve()
        allowed = ['accept', 'decline']
        changes = state['changes'].get(params.get('itemId'))
        if method == 'item/fileChange/requestApproval':
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
        if state['run'].get('yolo'):
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
                   'command': params.get('command'), 'reason': params.get('reason'), 'changes': changes}
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
                async with self.backend.execution(session) as adapter:
                    state['adapter'] = adapter
                    adapter.model = run['binding']['model']
                    adapter.reusable = False
                    run['client_reused'] = getattr(adapter, 'reused', False)
                    run['client_acquired_at'] = time.time()
                    workspace = self.backend.workspace(session)
                    from app.runtime.media import input_files
                    adapter.input_files = input_files(workspace, run.get('attachments', []))
                    adapter.dynamic_tools = session.get('dynamic_tools', [])
                    adapter.service_scope = session['binding'].get('service_scope')
                    updated_instructions = not adapter.service_scope and session.get('instructions_version', 1) < 3
                    if updated_instructions and self.tool_bridge:
                        from app.runtime.business_tools import instructions
                        session['instructions'] = instructions(session['actor_id'], session['binding']['runtime'])
                    if self.tool_bridge:
                        async def tool_call(params):
                            if state['finalizing'] or state['adapter'] is not adapter or state['cancel']:
                                raise HTTPException(409, '本轮工具调用已结束')
                            self.authorize(session['actor_id'], session['binding'])
                            return await self.tool_bridge(session, run, params)
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

                    async def read():
                        try:
                            async for event in adapter.stream_turn(session['thread_id'], run['message']):
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
                        session['instructions_version'] = 3
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
        root = f"/generated/runtime/{session['id']}/{run['id']}"
        lock_id = f"runtime-{run['id']}"
        wl.register_process(lock_id, session['actor_id'], kind='runtime', label='Agent 产物归档')
        try:
            if not wl.try_acquire(lock_id, [root]).ok:
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
                    await asyncio.to_thread(self.storage.write_bytes, session['actor_id'], path, data)
                artifact = {'id': new_id(), 'name': rel, 'path': path, 'sha256': sha, 'mime': mime,
                            'size': len(data), 'native_image': native, 'run_id': run['id']}
                session['artifacts'].append(artifact)
                session['baseline'][rel] = sha
                run['artifacts'].append(artifact)
                self.store.put('session', session)
                self._emit(state, 'artifact_created', {'artifact': artifact})
        finally:
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
