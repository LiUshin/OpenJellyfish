"""Small scheduled agent with explicit, checked tools and no ambient chat backend."""
import json
import hashlib
from dataclasses import asdict
from langchain.tools import tool, ToolRuntime
from app.execution.grants import Grant


def build_tools(grant):
    from app.storage import get_storage_service
    storage = get_storage_service()

    def read(path):
        path = grant.path(path)
        if grant.sid and path.startswith('generated/'):
            return storage.read_consumer_bytes(grant.uid, grant.sid, grant.conv, path[10:]).decode('utf-8')
        return storage.read_text(grant.uid, path)

    @tool
    async def read_file(file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """Read UTF-8 text within the granted directories, with line pagination."""
        lines = read(file_path).splitlines()
        return '\n'.join(lines[max(0, offset):max(0, offset)+min(2000, max(1, limit))])[:100000]

    @tool
    async def ls(path: str) -> str:
        """List a granted directory. Service documents are readable by their published paths."""
        path = grant.path(path)
        if grant.sid and path.startswith('generated/'):
            entries = storage.list_consumer_files(grant.uid, grant.sid, grant.conv)
            return json.dumps([e for e in entries if str(e.get('path', '')).startswith(path[10:])][:200], ensure_ascii=False)
        entries = storage.list_dir(grant.uid, path)
        visible = []
        for entry in entries:
            try:
                grant.path(entry.path)
                visible.append(asdict(entry))
            except PermissionError:
                pass
        return json.dumps(visible[:200], ensure_ascii=False)

    def write(path, content, operation_id):
        path = grant.path(path, write=True)
        if len(content.encode('utf-8')) > 1024*1024:
            raise ValueError('Scheduled file writes are limited to 1 MiB')
        if not grant.sid:
            from app.services.workspace_lock import check_write
            conflict = check_write('/' + path)
            if conflict:
                raise PermissionError(conflict)
        execution = grant.execution
        execution.store.effect_start(grant.run['id'], grant.run['token'], operation_id,
                                      {'kind': 'write_file', 'path': path})
        # Synchronous operation: no cancellation acknowledgement can race this operation.
        if grant.sid:
            storage.write_consumer_bytes_durable(grant.uid, grant.sid, grant.conv, path[10:], content.encode('utf-8'))
        else:
            storage.write_text_durable(grant.uid, path, content)
        execution.store.effect_done(grant.run['id'], grant.run['token'], operation_id, {'path': path, 'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()})
        return 'Saved: ' + path

    @tool
    async def write_file(file_path: str, content: str, runtime: ToolRuntime) -> str:
        """Write UTF-8 text within granted write directories."""
        return write(file_path, content, runtime.tool_call_id)

    @tool
    async def edit_file(file_path: str, old_string: str, new_string: str, runtime: ToolRuntime) -> str:
        """Replace one unambiguous text occurrence in a readable and writable file."""
        content = read(file_path)
        if content.count(old_string) != 1:
            raise ValueError('Expected exactly one matching occurrence')
        return write(file_path, content.replace(old_string, new_string, 1), runtime.tool_call_id)

    @tool
    async def send_message(message: str) -> str:
        """Prepare text for delivery after the run commits; this does not send immediately."""
        grant.capability('humanchat')
        return json.dumps({'text': message}, ensure_ascii=False)

    @tool
    async def spawn_child_task(task: dict, runtime: ToolRuntime) -> str:
        """Create a child schedule with a subset of this task's permissions and the same recipient."""
        from app.services.scheduler import create_child_task, get_current_task_context
        task = grant.child(task)
        grant.execution.store.effect_start(grant.run['id'], grant.run['token'], runtime.tool_call_id, {'kind': 'spawn'})
        child = create_child_task(get_current_task_context(), task)
        grant.execution.store.effect_done(grant.run['id'], grant.run['token'], runtime.tool_call_id, {'task_id': child['id']})
        return json.dumps({'task_id': child['id']})

    tools = [read_file, ls, write_file, edit_file, send_message]
    if grant.sid:
        from app.services.tools import create_contact_admin_tool
        recipient = grant.snapshot.get('reply_to') or {}

        @tool
        async def contact_admin(message: str, runtime: ToolRuntime) -> str:
            """Record a request for this Service's owner, bound to the current consumer conversation."""
            grant.policies()
            contact = create_contact_admin_tool(grant.uid, grant.sid, grant.conv,
                wechat_session_id=recipient.get('session_id') if recipient.get('channel') == 'wechat' else None,
                idempotency_key=f"{grant.run['id']}:{runtime.tool_call_id}")
            grant.execution.store.effect_start(grant.run['id'], grant.run['token'], runtime.tool_call_id,
                                                {'kind': 'contact_admin', 'conversation_id': grant.conv})
            result = await contact.ainvoke({'message': message})
            grant.execution.store.effect_done(grant.run['id'], grant.run['token'], runtime.tool_call_id,
                                               {'recorded': True})
            return result

        tools.append(contact_admin)
    if 'scheduler' in grant.saved['capabilities']:
        tools.append(spawn_child_task)
    return tools


def create_scheduled_agent(model=None):
    from langchain.agents import create_agent
    from app.services.agent import _resolve_model, _get_default_model, _checkpointer
    grant = Grant()
    grant.policies()
    unsupported = set(grant.saved['capabilities']) & {'web', 'image'}
    if unsupported:
        raise PermissionError('DeepAgents 定时任务没有这些能力的授权适配：' + ', '.join(sorted(unsupported)))
    for capability in grant.saved['capabilities']:
        grant.capability(capability)
    if not model and grant.sid:
        from app.services.published import get_service
        model = get_service(grant.uid, grant.sid).get('model')
    model = model or _get_default_model(grant.uid)
    service_prompt = ''
    if grant.sid:
        from app.services.published import get_service
        from app.services.consumer_agent import _build_consumer_system_prompt
        service = get_service(grant.uid, grant.sid)
        # Preserve published persona/profile while describing only adapters
        # actually available to this restricted execution.
        service_prompt = _build_consumer_system_prompt(grant.uid, {**service, 'allowed_scripts': []}) + '\n\n'
    return create_agent(model=_resolve_model(model, user_id=grant.uid), tools=build_tools(grant),
        system_prompt=(service_prompt + '执行保存的定时任务。文件路径相对用户文件区；服务任务只能读取发布的 docs，'
                       '并写入自身对话的 generated。send_message 会在本次运行保存后投递。'
                       '本次仅提供列出的受限工具，不提供脚本、CLI、联网和媒体生成。'
                       '普通文本是管理员可见的执行说明，不会发给消费者；确有需要才调用 send_message。'
                       '无法完成时明确报告；工具拒绝不代表操作成功。当前授权：' + json.dumps(grant.saved, ensure_ascii=False)),
        checkpointer=_checkpointer)
