"""Single-admin Codex pilot. Durable events, isolated scratch workspace, explicit opt-in."""
import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import HTTPException

from app.core.fileutil import atomic_json_save
from app.core.security import get_user_dir
from app.runtime.codex import CodexAdapter
from app.runtime.files import import_documents, collect_files
from app.runtime.rpc import RuntimeFailure
from app.services import workspace_lock as wl
from app.storage import get_storage_service

TERMINAL = {'completed', 'failed', 'cancelled'}
ID = re.compile(r'^[a-f0-9]{32}$')


class Pilot:
    def __init__(self, user_id, root: Path, executable: str, home: Path, storage=None):
        self.user_id, self.root, self.executable, self.home = user_id, root.resolve(), executable, home.resolve()
        self.storage = storage or get_storage_service()
        self.active = {}
        self.adapter_factory = CodexAdapter
        self.lease = None

    def claim(self):
        # One owning server process. Refuse a second worker instead of corrupting run state.
        import fcntl
        self.root.mkdir(parents=True, exist_ok=True)
        self.lease = open(self.root / '.worker.lock', 'a')
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lease.close()
            self.lease = None
            raise HTTPException(503, 'Codex 试验台已由另一个服务进程接管；请使用单 worker')
        for path in self.root.glob('*/runs/*.json'):
            run = json.loads(path.read_text())
            if run['status'] not in TERMINAL:
                sid = path.parent.parent.name
                session = self.read_session(sid)
                session['recovery_required'] = True
                self.save_session(session)
                run['process_status'] = 'unknown_after_restart'
                self.emit(sid, run, 'failed', {'message': '服务在执行中重启，旧进程状态未确认。请新建会话；历史和工作区已保留'})

    def session_dir(self, sid):
        if not ID.fullmatch(sid):
            raise HTTPException(400, '无效的会话 ID')
        return self.root / sid

    def read_session(self, sid):
        path = self.session_dir(sid) / 'session.json'
        if not path.is_file():
            raise HTTPException(404, '会话不存在')
        return json.loads(path.read_text())

    def read_run(self, sid, rid):
        self.read_session(sid)
        if not ID.fullmatch(rid):
            raise HTTPException(400, '无效的运行 ID')
        path = self.session_dir(sid) / 'runs' / f'{rid}.json'
        if not path.is_file():
            raise HTTPException(404, '运行不存在')
        return json.loads(path.read_text())

    def save_session(self, session):
        atomic_json_save(str(self.session_dir(session['id']) / 'session.json'), session, ensure_ascii=False)

    def profile_key(self):
        return hashlib.sha256(f'{self.executable}\n{self.home}'.encode()).hexdigest()

    def create_session(self, model, paths):
        sid = uuid.uuid4().hex
        folder = self.session_dir(sid)
        workspace = folder / 'workspace'
        workspace.mkdir(parents=True)
        try:
            baseline = import_documents(self.storage, self.user_id, workspace, paths)
            from app.services.prompt import get_user_system_prompt, build_user_profile_prompt
            from app.services.preferences import get_tz_offset
            from datetime import datetime, timezone, timedelta
            today = datetime.now(timezone(timedelta(hours=get_tz_offset(self.user_id)))).strftime('%Y年%m月%d日')
            prompt = get_user_system_prompt(self.user_id).replace('{today}', today)
            profile = build_user_profile_prompt(self.user_id)
            prompt = prompt.replace('{user_profile_context}', profile) if '{user_profile_context}' in prompt else prompt + '\n' + profile
            instructions = (
                'You are executing a Jellyfish administrator pilot in a dedicated scratch workspace. '
                'The following Jellyfish document is context; adapt its tool names to your native tools. '
                'Virtual /docs paths mean ./docs relative to cwd, never the OS root. '
                'Use only the supplied workspace. Imported documents are copies; edits are archived as outputs. '
                'Jellyfish scheduler, messaging and custom subagents are unavailable in this pilot. '
                'For image requests use native image generation if available, save images within cwd; '
                'if unavailable report the limitation and do not invoke a separately billed image API.\n\n' + prompt
            )
            session = {'id': sid, 'runtime': 'codex_app_server', 'model': model, 'created_at': time.time(),
                       'thread_id': None, 'profile': self.profile_key(), 'instructions': instructions,
                       'baseline': baseline, 'imports': paths, 'artifacts': [], 'runs': []}
            self.save_session(session)
            return self.public_session(session)
        except Exception:
            shutil.rmtree(folder)
            raise

    def public_session(self, session):
        return {k: v for k, v in session.items() if k not in ('instructions', 'baseline', 'profile', 'thread_id')}

    def list_sessions(self):
        return sorted([self.public_session(json.loads(p.read_text())) for p in self.root.glob('*/session.json')],
                      key=lambda s: s['created_at'], reverse=True)

    async def probe(self):
        if self.active:
            raise HTTPException(409, '请在当前任务结束后检测连接')
        self.home.mkdir(parents=True, exist_ok=True)
        adapter = self.adapter_factory(self.executable, self.home, self.root)
        try:
            return await adapter.probe()
        finally:
            await adapter.close()

    def emit(self, sid, run, kind, payload):
        encoded_size = len(json.dumps(payload).encode())
        if kind not in TERMINAL and (run.get('bytes', 0) + encoded_size > 8 * 1024 * 1024 or run['seq'] >= 10000):
            raise RuntimeFailure('本轮事件超过缓冲上限，执行已停止')
        run['bytes'] = run.get('bytes', 0) + encoded_size
        run['seq'] += 1
        if kind in TERMINAL:
            run['status'] = kind
            run['pending'] = None
        event = {'schema_version': 1, 'run_id': run['id'], 'seq': run['seq'], 'type': kind, **payload}
        folder = self.session_dir(sid) / 'runs'
        folder.mkdir(exist_ok=True)
        with (folder / f"{run['id']}.jsonl").open('a') as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        atomic_json_save(str(folder / f"{run['id']}.json"), run, ensure_ascii=False)

    def events(self, sid, rid, after=0):
        run = self.read_run(sid, rid)
        path = self.session_dir(sid) / 'runs' / f'{rid}.jsonl'
        result = []
        if path.exists():
            for line in path.read_text().splitlines():
                event = json.loads(line)
                if event['seq'] > after:
                    result.append(event)
        return run, result

    def start_turn(self, sid, rid, message):
        session = self.read_session(sid)
        if not ID.fullmatch(rid):
            raise HTTPException(400, 'request_id 必须是无连字符 UUID')
        if (self.session_dir(sid) / 'runs' / f'{rid}.json').exists():
            old = self.read_run(sid, rid)
            if old['message'] != message:
                raise HTTPException(409, 'request_id 已用于另一条消息')
            return old
        if self.active:
            raise HTTPException(409, '试验台同时只能执行一项任务')
        if session.get('recovery_required'):
            raise HTTPException(409, '此会话曾在执行中重启，旧进程状态未确认；请新建会话')
        if session['profile'] != self.profile_key():
            raise HTTPException(409, 'Codex 身份目录或程序已变更，请新建会话')
        run = {'id': rid, 'status': 'running', 'seq': 0, 'message': message, 'pending': None}
        session['runs'].append(rid)
        self.save_session(session)
        self.emit(sid, run, 'user_message', {'text': message})
        state = {'run': run, 'adapter': None, 'task': None, 'cancel': False, 'answer': None,
                 'finalizing': False, 'changes': {}}
        self.active[(sid, rid)] = state
        state['task'] = asyncio.create_task(self._execute(session, state))
        return run

    async def _request(self, session, state, payload):
        method, params, req_id = payload['method'], payload['params'], payload['request_id']
        adapter = state['adapter']
        workspace = self.session_dir(session['id']) / 'workspace'
        if method == 'item/permissions/requestApproval':
            await adapter.respond(req_id, {'permissions': {}, 'scope': 'turn'})
            return
        if method not in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
            # Do not leave unsupported blocking RPC requests hanging.
            await adapter.rpc.send({'id': req_id, 'error': {'code': -32601, 'message': 'Unsupported by Jellyfish pilot'}})
            self.emit(session['id'], state['run'], 'notice', {'message': f'当前试验台未支持：{method}'})
            return
        allowed = ['accept', 'decline']
        changes = state['changes'].get(params.get('itemId'))
        if method == 'item/fileChange/requestApproval':
            if not changes:
                allowed = ['decline']
            for change in changes or []:
                try:
                    path = Path(change['path'])
                    (path if path.is_absolute() else workspace / path).resolve().relative_to(workspace.resolve())
                except (ValueError, KeyError):
                    allowed = ['decline']
        elif not params.get('command'):
            allowed = ['decline']
        for field in ('cwd', 'grantRoot'):
            if params.get(field):
                try:
                    Path(params[field]).resolve().relative_to(workspace.resolve())
                except ValueError:
                    allowed = ['decline']
        if params.get('additionalPermissions') or params.get('networkApprovalContext'):
            allowed = ['decline']
        offered = params.get('availableDecisions')
        if offered:
            allowed = [v for v in allowed if v in offered]
        if not allowed:
            raise RuntimeFailure('Codex 请求的审批选项不受试验台支持')
        request_key = uuid.uuid4().hex
        pending = {'id': request_key, 'kind': method, 'allowed': allowed,
                   'command': params.get('command'), 'reason': params.get('reason'),
                   'cwd': params.get('cwd'), 'item_id': params.get('itemId'), 'changes': changes}
        state['run']['pending'] = pending
        state['answer'] = asyncio.get_running_loop().create_future()
        self.emit(session['id'], state['run'], 'approval_requested', {'approval': pending})
        try:
            async with asyncio.timeout(300):
                while not state['answer'].done():
                    if getattr(adapter.rpc, 'failure', None):
                        raise adapter.rpc.failure
                    await asyncio.wait({state['answer']}, timeout=.5)
                decision = state['answer'].result()
            await adapter.respond(req_id, {'decision': decision})
            state['run']['pending'] = None
            self.emit(session['id'], state['run'], 'approval_resolved', {'approval_id': request_key, 'decision': decision})
        except asyncio.TimeoutError as exc:
            raise RuntimeFailure('审批等待超过 5 分钟，本轮已停止') from exc
        finally:
            state['answer'] = None

    async def _execute(self, session, state):
        sid, run = session['id'], state['run']
        workspace = self.session_dir(sid) / 'workspace'
        owner = f"runtime-{run['id']}"
        adapter = self.adapter_factory(self.executable, self.home, workspace, session['model'])
        state['adapter'] = adapter
        terminal, failure, images = 'failed', '执行未完成', []
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            async with asyncio.timeout(1800):
                session['thread_id'] = await adapter.open_session(str(workspace), session['instructions'], session['thread_id'])
                self.save_session(session)
                async for event in adapter.stream_turn(session['thread_id'], run['message']):
                    if event.type == 'request':
                        await self._request(session, state, event.payload)
                    elif event.type == 'image':
                        if event.payload['failure'] or event.payload['status'] != 'completed' or not event.payload['saved_path']:
                            raise RuntimeFailure('原生生图未返回可归档的成功文件；没有回退到独立 API')
                        images.append(event.payload['saved_path'])
                        self.emit(sid, run, 'image_generated', {'item_id': event.payload['item_id']})
                    elif event.type in ('completed', 'cancelled'):
                        terminal = event.type
                    else:
                        if event.type == 'tool' and event.payload.get('changes'):
                            state['changes'][event.payload['item_id']] = event.payload['changes']
                        self.emit(sid, run, event.type, event.payload)
        except asyncio.CancelledError:
            terminal, failure = 'cancelled', '用户停止了本轮执行'
        except TimeoutError:
            failure = '执行超过 30 分钟，已停止'
        except RuntimeFailure as exc:
            failure = str(exc)
        except Exception:
            failure = '执行或协议处理失败，请检查服务端配置'
        finally:
            state['finalizing'] = True
            try:
                await adapter.close()
            except Exception:
                terminal, failure = 'failed', '无法确认执行进程已退出，请检查宿主机并新建会话'
                session['recovery_required'] = True
                self.save_session(session)
            try:
                if terminal == 'completed' and not state['cancel']:
                    files = await asyncio.to_thread(collect_files, workspace, session['baseline'], images)
                    archive_root = f"/generated/runtime/{sid}/{run['id']}"
                    wl.register_process(owner, self.user_id, kind='runtime', label='Codex 产物归档')
                    if not wl.try_acquire(owner, [archive_root]).ok:
                        raise RuntimeFailure('产物归档目录被锁定，文件保留在试验工作区')
                    for rel, data, sha, mime, native in files:
                        if state['cancel']:
                            break
                        storage_path = archive_root + '/' + rel
                        await asyncio.to_thread(self.storage.write_bytes, self.user_id, storage_path, data)
                        artifact = {'id': uuid.uuid4().hex, 'run_id': run['id'], 'name': rel,
                                    'path': storage_path, 'sha256': sha, 'mime': mime,
                                    'size': len(data), 'native_image': native}
                        session['artifacts'].append(artifact)
                        session['baseline'][rel] = sha
                        self.save_session(session)
                        self.emit(sid, run, 'artifact_created', {'artifact': artifact})
            except (ValueError, OSError, RuntimeFailure) as exc:
                terminal, failure = 'failed', f'产物归档失败：{exc}'
            except Exception:
                terminal, failure = 'failed', '产物归档失败，源文件已保留'
            finally:
                wl.unregister_process(owner)
                if state['cancel'] and not session.get('recovery_required'):
                    terminal = 'cancelled'
                self.emit(sid, run, terminal, {'message': failure} if terminal != 'completed' else {})
                self.active.pop((sid, run['id']), None)

    def approve(self, sid, rid, approval_id, decision):
        self.read_run(sid, rid)
        state = self.active.get((sid, rid))
        pending = state['run'].get('pending') if state else None
        if not pending or pending['id'] != approval_id or not state['answer'] or state['answer'].done():
            raise HTTPException(409, '审批已失效或已处理')
        if decision not in pending['allowed']:
            raise HTTPException(400, '不支持的审批选项')
        state['answer'].set_result(decision)

    async def cancel(self, sid, rid):
        self.read_run(sid, rid)
        state = self.active.get((sid, rid))
        if not state:
            return
        if state['cancel']:
            with contextlib.suppress(asyncio.CancelledError):
                await state['task']
            return
        state['cancel'] = True
        if state['adapter']:
            with contextlib.suppress(Exception):
                await state['adapter'].cancel()
        if not state['task'].done() and not state['finalizing']:
            state['task'].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state['task']
        # A task cancelled before its coroutine first runs never enters its finally.
        if (sid, rid) in self.active:
            self.emit(sid, state['run'], 'cancelled', {'message': '用户停止了本轮执行'})
            self.active.pop((sid, rid), None)

    async def shutdown(self):
        for sid, rid in list(self.active):
            await self.cancel(sid, rid)
        if self.lease:
            self.lease.close()


_pilot = None
_initialization_lock = threading.Lock()


def get_pilot(user_id):
    with _initialization_lock:
        return _get_pilot(user_id)


def _get_pilot(user_id):
    global _pilot
    if os.getenv('JELLYFISH_CODEX_PILOT') != '1' or os.getenv('JELLYFISH_CODEX_ADMIN_ID') != user_id:
        raise HTTPException(403, 'Codex 试验台尚未向此管理员开放')
    if sys.platform not in ('darwin', 'linux'):
        raise HTTPException(503, '首版试验台仅支持 macOS / Linux')
    if _pilot is None:
        root = Path(get_user_dir(user_id)).resolve() / 'runtime_pilot'
        executable = shutil.which(os.getenv('JELLYFISH_CODEX_BIN', 'codex'))
        if not executable:
            raise HTTPException(503, '未找到 Codex CLI，请配置 JELLYFISH_CODEX_BIN')
        home = Path(os.getenv('JELLYFISH_CODEX_HOME', str(root / 'codex-home'))).expanduser().resolve()
        _pilot = Pilot(user_id, root, executable, home)
        try:
            _pilot.claim()
        except Exception:
            _pilot = None
            raise
    if _pilot.user_id != user_id:
        raise HTTPException(403, '试验台管理员配置已变更，请重启服务')
    return _pilot
