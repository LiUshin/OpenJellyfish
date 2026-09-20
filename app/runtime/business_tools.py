"""Small actor-bound bridge. No model-supplied tenant IDs or credential access."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field
from app.runtime.files import digest


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Document(Empty):
    path: str = Field(min_length=1, max_length=500)


class Directory(Empty):
    path: str = Field(default='/docs', max_length=500)


class MemoryWrite(Empty):
    content: str = Field(max_length=8000)
    previous_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ServiceDocument(Document):
    service_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')


TOOLS = {
    'jellyfish_list_documents': (Directory, '列出当前 admin 的文档目录，最多 200 项。'),
    'jellyfish_read_document': (Document, '读取当前 admin 的 UTF-8 文档（最多 256KB）。虚拟路径 /docs/...。'),
    'jellyfish_read_memory': (Empty, '读取当前 admin 的长期记忆与 sha256，用于更新前合并。'),
    'jellyfish_update_memory': (MemoryWrite, '更新当前 admin 的长期记忆；先读取并合并旧记忆，传入原 sha256。锁定时拒绝。'),
    'jellyfish_list_services': (Empty, '列出当前 admin 的服务名称、ID 和文档范围，不返回服务凭据。'),
    'jellyfish_read_service_document': (ServiceDocument, '读取当前 admin 所有、且在指定服务文档范围内的文档。'),
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

    async def __call__(self, session, run, params):
        if session['binding'].get('service_scope'):
            from app.runtime.consumer_tools import ServiceTools
            return await ServiceTools(self.storage, self.store, self.authorize)(session, run, params)
        actor_id = session['actor_id']
        self.authorize(actor_id, session['binding'])
        from uuid import uuid4
        call_id = params.get('callId') or uuid4().hex
        name = params.get('tool')
        schema = TOOLS.get(name)
        if not schema:
            return self.result('此业务工具未开放', False)
        try:
            args = schema[0].model_validate(params.get('arguments', {}))
            self.store.emit(run, 'business_tool', {'item_id': call_id, 'name': name, 'status': 'running', 'input': args.model_dump()})
            # These bounded metadata/file operations run in the scheduler thread so
            # permission checks and memory compare/write cannot interleave.
            value = self.invoke(actor_id, name, args)
            self.store.emit(run, 'business_tool', {'item_id': call_id, 'name': name, 'status': 'completed', 'result': value})
            return self.result(value, True)
        except Exception:
            self.store.emit(run, 'business_tool', {'item_id': call_id, 'name': name, 'status': 'failed', 'result': '工具拒绝执行：检查路径、大小、作用域、记忆锁或版本；没有访问其他 admin 的数据。'})
            return self.result('工具拒绝执行：检查路径、大小、作用域、记忆锁或版本；没有访问其他 admin 的数据。', False)

    @staticmethod
    def result(value, success):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return {'contentItems': [{'type': 'inputText', 'text': text}], 'success': success}

    def invoke(self, actor_id, name, args):
        from app.services.prompt import get_agent_notes, is_agent_notes_locked, set_agent_notes
        if name == 'jellyfish_list_documents':
            return [{'name': e.name, 'is_dir': e.is_dir, 'size': e.size}
                    for e in self.storage.list_dir(actor_id, docs_path(args.path, True))][:200]
        if name == 'jellyfish_read_document':
            return self.read(actor_id, args.path)
        if name == 'jellyfish_read_memory':
            notes = get_agent_notes(actor_id)
            return {'content': notes, 'sha256': digest(notes.encode()), 'locked': is_agent_notes_locked(actor_id)}
        if name == 'jellyfish_update_memory':
            if is_agent_notes_locked(actor_id) or digest(get_agent_notes(actor_id).encode()) != args.previous_sha256:
                raise ValueError('记忆已锁定或更新，请重新读取')
            set_agent_notes(actor_id, args.content)
            return {'updated': True, 'sha256': digest(args.content.encode())}
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


def instructions(actor_id, runtime='codex'):
    from app.services.prompt import get_user_system_prompt, build_user_profile_prompt
    from app.services.preferences import get_tz_offset
    profile = build_user_profile_prompt(actor_id)[:16000]
    today = datetime.now(timezone(timedelta(hours=get_tz_offset(actor_id)))).strftime('%Y年%m月%d日')
    prompt = get_user_system_prompt(actor_id)[:32000].replace('{today}', today)
    if '{user_profile_context}' in prompt:
        prompt = prompt.replace('{user_profile_context}', profile)
    elif profile:
        prompt += '\n\n' + profile
    # User prompt and profile are actor-owned. The adapter capabilities below are
    # authoritative even if a legacy prompt describes unavailable tools.
    return '\n\n'.join([
        prompt,
        f'你正在 OpenJellyfish 的 {runtime} 运行环境内。遵守当前用户的系统提示和偏好。',
        '可使用注册的 jellyfish_* 业务工具，以及客户端实际提供的网页搜索、生图和文件/命令工具；不要声称拥有旧提示中未注册的工具。',
        '当前工作目录是该用户本会话的副本。/docs 是业务工具的虚拟路径；导入文件在工作目录的 docs/。'
        '仅在当前工作目录内读写执行，不访问其他目录或账号配置。新文件在结束后归档到当前用户产物。',
        '长期记忆使用 jellyfish_read_memory 和 jellyfish_update_memory；写入前保留旧信息并遵守锁。'
        '网页搜索后使用普通 Markdown 来源链接。原生生图结果由 Jellyfish 自动收集到本聊天 generated/ 并展示；生图完成后无需用命令复制文件，也不需要输出本机绝对路径图片链接。图片附件直接作为视觉输入；普通文件位于本工作区附件路径，应读取内容。语音、视频、发布、定时任务与消费者服务执行不在本运行能力内；不得转用其他付费供应商。',
    ])
