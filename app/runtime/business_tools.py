"""Small actor-bound bridge. No model-supplied tenant IDs or credential access."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field
from app.runtime.files import digest


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Document(Empty):
    path: str = Field(min_length=1, max_length=500)


class Directory(Empty):
    path: str = Field(default='/docs', max_length=500)


class AreaDirectory(Directory):
    offset: int = Field(default=0, ge=0, le=1000000)
    limit: int = Field(default=20, ge=1, le=20)


class AreaDocument(Document):
    offset: int = Field(default=0, ge=0, le=1000000)
    limit: int = Field(default=20, ge=1, le=20)
    content_offset: int = Field(default=0, ge=0, le=2 * 1024 * 1024)
    message_ref: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class MemoryWrite(Empty):
    content: str = Field(max_length=8000)
    previous_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ProjectBriefWrite(Empty):
    content: str = Field(max_length=65536)


class DocumentWrite(Document):
    content: str = Field(max_length=65536)
    overwrite: bool = False


class ServiceDocument(Document):
    service_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')


class ServiceMessage(Empty):
    service_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    conversation_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    message: str = Field(min_length=1, max_length=16000)
    inbox_id: str | None = Field(default=None, pattern=r'^inbox_[A-Za-z0-9_-]{1,94}$')


class ScheduledWrite(Document):
    content: str = Field(max_length=65536)


SCHEDULED_TOOLS = {
    'jellyfish_scheduled_list_files': (Directory, '列出本次定时任务授权目录中的文件，最多 200 项。'),
    'jellyfish_scheduled_read_file': (Document, '读取本次定时任务授权路径的 UTF-8 文件，最多 256KB。'),
    'jellyfish_scheduled_write_file': (ScheduledWrite, '写入本次定时任务授权路径的 UTF-8 文件，最多 64KB。'),
}


def scheduler_specifications():
    return [{'type': 'function', 'name': name, 'description': desc,
             'inputSchema': schema.model_json_schema(), 'deferLoading': False}
            for name, (schema, desc) in SCHEDULED_TOOLS.items()]


TOOLS = {
    'jellyfish_list_documents': (AreaDirectory, '列出当前 admin 的 /docs 文档目录，或已启用的虚拟 /service-records Service 记录区；后者支持 offset/limit 分页。'),
    'jellyfish_read_document': (AreaDocument, '读取当前 admin 的 /docs UTF-8 文档（最多 256KB），或已启用的虚拟 /service-records 反馈、会话消息及 Token 用量。记录区支持 offset/limit 分页；长消息设 limit=1，按返回的 next_content_offset 续读，并原样传回 message_ref。'),
    'jellyfish_write_document': (DocumentWrite, '把 UTF-8 文本持久保存到当前 admin 的 /docs 文档库，最多 64KB。默认只新建；同内容重试安全。若要替换内容不同的已有文件，显式设置 overwrite=true。工作目录 docs/ 中的副本不会自动回写文档库。'),
    'jellyfish_read_memory': (Empty, '读取当前 admin 的长期记忆与 sha256，用于更新前合并。'),
    'jellyfish_update_memory': (MemoryWrite, '更新当前 admin 的长期记忆；先读取并合并旧记忆，传入原 sha256。锁定时拒绝。'),
    'jellyfish_write_project_brief': (ProjectBriefWrite, '完整替换当前管理员对话所属项目的 Markdown brief；项目由会话确定，不能指定其他项目。先合并现有内容再写入。'),
    'jellyfish_list_services': (Empty, '列出当前 admin 的服务名称、ID 和文档范围，不返回服务凭据。'),
    'jellyfish_read_service_document': (ServiceDocument, '读取当前 admin 所有、且在指定服务文档范围内的文档。'),
    'jellyfish_send_service_message': (ServiceMessage, '以当前管理员身份向指定 Service 用户会话回复文字。传入 service_id、conversation_id、完整 message；回复收件箱反馈时另传 inbox_id。提交后返回持久投递队列状态，不能据此声称用户已收到。'),
}


def specifications():
    return [{'type': 'function', 'name': name, 'description': desc,
             'inputSchema': schema.model_json_schema(), 'deferLoading': False}
            for name, (schema, desc) in TOOLS.items()]


def docs_path(raw, directory=False):
    path = PurePosixPath(raw)
    if '\\' in raw or '..' in path.parts or any(p.startswith('.') for p in path.parts if p != '/'):
        raise ValueError('只允许文档目录内的路径')
    value = str(path)
    if not value.startswith('/docs/') and not (directory and value == '/docs'):
        raise ValueError('只允许 /docs 内的路径')
    return value


class BusinessTools:
    def __init__(self, storage, authorize, store):
        self.storage, self.authorize, self.store = storage, authorize, store

    def read(self, actor_id, raw):
        path = docs_path(raw)
        parent, name = str(PurePosixPath(path).parent), PurePosixPath(path).name
        entry = next((e for e in self.storage.list_dir(actor_id, parent) if e.name == name and not e.is_dir), None)
        if not entry or entry.size > 256 * 1024:
            raise ValueError('文档不存在或超过 256KB')
        data = self.storage.read_bytes(actor_id, path)
        if len(data) > 256 * 1024:
            raise ValueError('文档超过 256KB')
        return data.decode('utf-8')

    def write_document(self, actor_id, args):
        from uuid import uuid4
        from app.services import workspace_lock as wl

        path = docs_path(args.path)
        if args.path.endswith('/') or any(c in args.path for c in ('\x00', '\r', '\n')):
            raise ValueError('文档路径必须是 /docs 内的文件')
        encoded = args.content.encode('utf-8')
        if len(encoded) > 65536:
            raise ValueError('文档单次写入不能超过 64KB')
        sha = digest(encoded)
        owner = 'runtime-doc-' + uuid4().hex
        wl.register_process(owner, actor_id, kind='runtime', label='文档写入')
        try:
            if not wl.try_acquire(owner, [path]).ok:
                raise PermissionError('文档路径被其他进程锁定')

            # Local storage resolves paths before writing. Reject symlinked
            # components inside /docs before even reading the destination; a
            # symlink to another location under this admin must not bypass the
            # document-only boundary.
            from app.storage.local import LocalStorageService, _fs_root
            if isinstance(self.storage, LocalStorageService):
                current = Path(_fs_root(actor_id))
                for part in path.lstrip('/').split('/'):
                    current = current / part
                    if current.is_symlink():
                        raise PermissionError('文档路径不能包含符号链接')

            parent, name = str(PurePosixPath(path).parent), PurePosixPath(path).name
            entry = next((e for e in self.storage.list_dir(actor_id, parent) if e.name == name), None)
            if entry and entry.is_dir:
                raise ValueError('文档路径是目录')
            if entry is None and self.storage.exists(actor_id, path):
                raise ValueError('文档路径已被占用')
            if entry:
                if entry.size <= 65536 and self.storage.read_bytes(actor_id, path) == encoded:
                    return {'path': path, 'size': len(encoded), 'sha256': sha,
                            'created': False, 'updated': False}
                if not args.overwrite:
                    raise FileExistsError('文档已存在且内容不同；如需替换，请设置 overwrite=true')
            self.storage.write_text_durable(actor_id, path, args.content)
            return {'path': path, 'size': len(encoded), 'sha256': sha,
                    'created': entry is None, 'updated': entry is not None}
        finally:
            wl.unregister_process(owner)

    async def __call__(self, session, run, params):
        if session['binding'].get('scheduler_scope'):
            return await ScheduledBusinessTools(self.storage, self.store, self.authorize)(session, run, params)
        if session['binding'].get('service_scope'):
            from app.runtime.consumer_tools import ServiceTools
            return await ServiceTools(self.storage, self.store, self.authorize)(session, run, params)
        actor_id = session['actor_id']
        self.authorize(actor_id, session['binding'])
        from uuid import uuid4
        call_id = params.get('callId')
        if call_id is None:
            call_id = uuid4().hex
        name = params.get('tool')
        schema = TOOLS.get(name)
        if not schema:
            return self.result('此业务工具未开放', False)
        try:
            if name == 'jellyfish_write_project_brief' and run.get('channel') == 'voice':
                raise PermissionError('语音运行不能写入项目 brief')
            args = schema[0].model_validate(params.get('arguments', {}))
            if name == 'jellyfish_write_document':
                encoded = args.content.encode('utf-8')
                event_input = {'path': docs_path(args.path), 'size': len(encoded), 'sha256': digest(encoded)}
            elif name == 'jellyfish_send_service_message':
                event_input = {'service_id': args.service_id, 'conversation_id': args.conversation_id,
                               'inbox_id': args.inbox_id, 'message': args.message}
            else:
                event_input = args.model_dump()
            self.store.emit(run, 'business_tool', {'item_id': call_id, 'name': name, 'status': 'running', 'input': event_input})
            # Keep business effects behind the actor-bound bridge. Durable
            # Service replies use the same run/call identity on retries.
            if name == 'jellyfish_send_service_message':
                call_id = params.get('callId')
                if (isinstance(call_id, bool) or not isinstance(call_id, (str, int))
                        or not str(call_id) or len(str(call_id)) > 128
                        or not session.get('conversation_id') or not run.get('id')):
                    raise ValueError('发送回复要求管理员对话与稳定的工具调用 ID')
                approved_target = params.get('approved_target')
                if not isinstance(approved_target, dict):
                    raise PermissionError('发送回复需要经过管理员确认或 YOLO 授权')
                from app.services.service_messaging import send_service_message
                sent = send_service_message(actor_id, args.service_id, args.conversation_id,
                                            args.message, inbox_id=args.inbox_id,
                                            idempotency_key=f"cli:{run['id']}:{call_id}",
                                            expected_target=approved_target)
                value = {'message_id': sent['message']['id'],
                         'deliveries': [{'id': item['id'], 'channel': item['channel'], 'status': item['status']}
                                        for item in sent['deliveries']],
                         'summary': '回复已提交到投递队列；请查看投递状态，不能据此认定用户已收到。'}
            else:
                value = self.invoke(actor_id, name, args,
                                    conversation_id=session.get('conversation_id'))
            self.store.emit(run, 'business_tool', {'item_id': call_id, 'name': name, 'status': 'completed', 'result': value})
            return self.result(value, True)
        except Exception as exc:
            area = (name in ('jellyfish_list_documents', 'jellyfish_read_document') and
                    isinstance(params.get('arguments'), dict) and
                    str(params['arguments'].get('path', '')).startswith('/service-records'))
            if name == 'jellyfish_send_service_message':
                message = f'回复未提交：{str(exc)[:200]}' if isinstance(exc, (ValueError, KeyError, PermissionError)) else '回复未提交：请检查会话归属与投递服务状态。'
            elif name == 'jellyfish_write_document' and isinstance(exc, FileExistsError):
                message = '文档已存在且内容不同；如需替换，请显式设置 overwrite=true。'
            else:
                message = ('Service 记录区读取失败：请检查权限和路径；结果过长时缩小 limit 或调整 offset/content_offset。'
                           if area else '工具拒绝执行：检查路径、大小、作用域、记忆锁或版本；没有访问其他 admin 的数据。')
            self.store.emit(run, 'business_tool', {'item_id': call_id, 'name': name, 'status': 'failed', 'result': message})
            return self.result(message, False)

    @staticmethod
    def result(value, success):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return {'contentItems': [{'type': 'inputText', 'text': text}], 'success': success}

    def invoke(self, actor_id, name, args, *, conversation_id=None):
        from app.services.prompt import get_agent_notes, is_agent_notes_locked, set_agent_notes
        if name == 'jellyfish_list_documents':
            if args.path.startswith('/service-records'):
                from app.services.service_records import list_area
                return list_area(actor_id, args.path, getattr(args, 'offset', 0), getattr(args, 'limit', 20))
            return [{'name': e.name, 'is_dir': e.is_dir, 'size': e.size}
                    for e in self.storage.list_dir(actor_id, docs_path(args.path, True))][:200]
        if name == 'jellyfish_read_document':
            if args.path.startswith('/service-records'):
                from app.services.service_records import read_area
                return read_area(actor_id, args.path, getattr(args, 'offset', 0), getattr(args, 'limit', 20),
                                 content_offset=getattr(args, 'content_offset', 0),
                                 message_ref=getattr(args, 'message_ref', None))
            return self.read(actor_id, args.path)
        if name == 'jellyfish_write_document':
            return self.write_document(actor_id, args)
        if name == 'jellyfish_read_memory':
            notes = get_agent_notes(actor_id)
            return {'content': notes, 'sha256': digest(notes.encode()), 'locked': is_agent_notes_locked(actor_id)}
        if name == 'jellyfish_update_memory':
            if is_agent_notes_locked(actor_id) or digest(get_agent_notes(actor_id).encode()) != args.previous_sha256:
                raise ValueError('记忆已锁定或更新，请重新读取')
            set_agent_notes(actor_id, args.content)
            return {'updated': True, 'sha256': digest(args.content.encode())}
        if name == 'jellyfish_write_project_brief':
            from app.services.project_context import write_current_project_brief
            return write_current_project_brief(actor_id, conversation_id, args.content)
        from app.services.published import list_services, get_service
        if name == 'jellyfish_list_services':
            return [{'id': s['id'], 'name': s.get('name', s['id']), 'allowed_docs': s.get('allowed_docs', [])}
                    for s in list_services(actor_id)][:100]
        if name == 'jellyfish_read_service_document':
            service = get_service(actor_id, args.service_id)
            if not service or service.get('admin_id') != actor_id:
                raise ValueError('服务不存在')
            path = docs_path(args.path)
            relative = path[len('/docs/'):]
            allowed = service.get('allowed_docs') or []
            if not any(p == '*' or relative == p.strip('/') or relative.startswith(p.strip('/') + '/')
                       for p in allowed if p.strip('/')):
                raise ValueError('文档未向此服务开放')
            return self.read(actor_id, path)
        raise ValueError('Unsupported tool')


class ScheduledBusinessTools:
    """Only the durable scheduler grant decides which paths a CLI can touch."""
    def __init__(self, storage, store, authorize):
        self.storage, self.store, self.authorize = storage, store, authorize

    async def __call__(self, session, run, params):
        from app.runtime.consumer import authorize_scheduler
        from app.services import workspace_lock as wl
        actor, binding = session['actor_id'], session['binding']
        grant = authorize_scheduler(actor, binding)
        self.authorize(actor, binding)
        name = params.get('tool')
        schema = SCHEDULED_TOOLS.get(name)
        if not schema:
            return BusinessTools.result('此业务工具未向定时任务开放', False)
        try:
            args = schema[0].model_validate(params.get('arguments', {}))
            clean = grant.path(args.path, write=name.endswith('_write_file'))
            path = '/' + clean
            if name.endswith('_list_files'):
                rows = []
                for entry in self.storage.list_dir(actor, path)[:200]:
                    try:
                        grant.path(entry.path)
                    except PermissionError:
                        continue
                    rows.append({'name': entry.name, 'path': entry.path,
                                 'is_dir': entry.is_dir, 'size': entry.size})
                value = rows
            elif name.endswith('_read_file'):
                data = self.storage.read_bytes(actor, path)
                if len(data) > 256 * 1024:
                    raise ValueError('文件超过 256KB')
                value = data.decode('utf-8')
            else:
                call_id = params.get('callId')
                if not isinstance(call_id, (str, int)) or not str(call_id) or len(str(call_id)) > 128:
                    raise ValueError('持久化写入要求稳定的工具调用 ID')
                encoded = args.content.encode('utf-8')
                if len(encoded) > 65536:
                    raise ValueError('定时任务单次写入不能超过 64KB')
                owner = 'scheduled-' + grant.run['id']
                process = wl.get_process(owner)
                if not process or process.user_id != actor or not wl.is_write_allowed(owner, path):
                    raise PermissionError('定时任务未持有此路径的工作区写锁')
                effect_id = f"cli:{grant.run['id']}:{call_id}"
                grant.execution.store.effect_start(grant.run['id'], grant.run['token'], effect_id,
                    {'kind': 'write_file', 'path': path, 'sha256': digest(encoded)})
                grant.path(args.path, write=True)
                self.authorize(actor, binding)
                self.storage.write_text_durable(actor, path, args.content)
                grant.execution.store.effect_done(grant.run['id'], grant.run['token'], effect_id,
                                                  {'path': path, 'size': len(encoded)})
                value = {'path': path, 'written': True}
            self.authorize(actor, binding)
            self.store.emit(run, 'business_tool', {'name': name, 'status': 'completed'})
            return BusinessTools.result(value, True)
        except (ValueError, OSError, PermissionError, UnicodeError) as exc:
            self.store.emit(run, 'business_tool', {'name': name, 'status': 'failed', 'result': str(exc)[:200]})
            return BusinessTools.result('工具拒绝执行：路径、授权、大小或调用 ID 无效。', False)


def instructions(actor_id, runtime='codex', *, project_brief_write=True,
                 document_write_available=True, service_message_available=True):
    from app.services.prompt import get_user_system_prompt, build_user_profile_prompt
    from app.services.preferences import get_tz_offset
    profile = build_user_profile_prompt(actor_id)[:16000]
    user_now = datetime.now(timezone(timedelta(hours=get_tz_offset(actor_id))))
    today = f"{user_now.year:04d}年{user_now.month:02d}月{user_now.day:02d}日"
    prompt = get_user_system_prompt(actor_id)[:32000].replace('{today}', today)
    if '{user_profile_context}' in prompt:
        prompt = prompt.replace('{user_profile_context}', profile)
    elif profile:
        prompt += '\n\n' + profile
    # User prompt and profile are actor-owned. The adapter capabilities below are
    # authoritative even if a legacy prompt describes unavailable tools.
    parts = [
        prompt,
        f'你正在 OpenJellyfish 的 {runtime} 运行环境内。遵守当前用户的系统提示和偏好。',
        '可使用注册的 jellyfish_* 业务工具，以及客户端实际提供的网页搜索、生图和文件/命令工具；不要声称拥有旧提示中未注册的工具。',
        '当前工作目录是该用户本会话的副本，导入文档位于其中的 docs/。/docs 是 Jellyfish 文档库的业务工具虚拟路径。'
        '修改或新建工作目录里的 docs/ 文件只会归档为会话产物，不会自动保存到文档库。'
        '原生文件和命令工具仅在当前工作目录内执行，不访问其他目录或账号配置。',
        '长期记忆使用 jellyfish_read_memory 和 jellyfish_update_memory；写入前保留旧信息并遵守锁。',
        '管理员启用 Service 记录区后，可用 jellyfish_list_documents / jellyfish_read_document 的 /service-records 虚拟路径只读查看反馈、消费者对话和用量。长消息用 limit=1 读取，随后用同一消息 offset、返回的 next_content_offset 和 message_ref 续读；不要访问宿主上的原始 Service 文件。',
        '网页搜索后使用普通 Markdown 来源链接。原生生图结果由 Jellyfish 自动收集到本聊天 generated/ 并展示；生图完成后无需用命令复制文件，也不需要输出本机绝对路径图片链接。图片附件直接作为视觉输入；普通文件位于本工作区附件路径，应读取内容。语音、视频、发布、定时任务与消费者服务执行不在本运行能力内；不得转用其他付费供应商。',
    ]
    if project_brief_write:
        parts.append('若当前管理员对话属于项目，可用 jellyfish_write_project_brief 完整更新该项目的 Markdown brief；工具只能修改当前对话所属项目，写入前保留已有事实。')
    if document_write_available:
        parts.append('需要把 UTF-8 文本保存进文档库 /docs 时，调用 jellyfish_write_document 并传入完整的 /docs/文件路径和内容；默认只创建新文件，覆盖已有不同内容须显式设置 overwrite=true。工具成功返回后才可称文件已保存到文档库。')
    else:
        parts.append('当前原生会话没有文档库写入工具；工作目录中的 docs/ 文件只是会话副本，不要称它已保存到文档库。')
    if service_message_available:
        parts.append('要以管理员身份直接回复某个 Service 用户会话，调用 jellyfish_send_service_message，传入 service_id、conversation_id、要发送的完整 message；若是回复收件箱反馈，再传 inbox_id。工具成功只表示回复已进入持久投递队列；查看投递状态后才能判断是否送达。不要将普通聊天输出当成已发送给 Service 用户。')
    else:
        parts.append('当前原生会话没有直接回复 Service 用户的业务工具；不要声称聊天输出已发送给 Service 用户。')
    return '\n\n'.join(parts)
