"""Subscription-backed internal Services, shared by web, API and WeChat.

Service configuration pins the host account generation and admin grant. Native
sessions are scoped to a Service conversation and permission revision, never to
the admin's main chat. No supplier credential is exposed to a Service caller.
"""
import asyncio
import hashlib
import json
import re
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
from langchain_core.messages import AIMessageChunk, ToolMessage

from app.runtime.store import TERMINAL


def external(config):
    return (config.get('runtime_choice') or {}).get('runtime', 'deepagents') in ('codex', 'cursor')


def revision(config):
    keys = ('runtime_choice', 'runtime_binding', 'model', 'allowed_docs', 'allowed_scripts',
            'capabilities', 'research_tools', 'system_prompt_version_id',
            'user_profile_version_id', 'published', 'updated_at')
    return hashlib.sha256(json.dumps({k: config.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()


def configure(admin_id, config):
    """Called only by authenticated admin CRUD; never accept a client binding."""
    choice = config.get('runtime_choice') or {'runtime': 'deepagents'}
    if not external(config):
        config['runtime_binding'] = {}
        return config
    if set(config.get('capabilities', [])) - {'web', 'image', 'humanchat', 'documents'}:
        raise HTTPException(400, '套餐 Service 暂支持文档、脚本、联网、原生生图和微信；请关闭定时任务、语音和视频能力')
    from app.runtime.manager import get_runtime
    runtime = get_runtime()
    binding = runtime.profiles.binding(admin_id, choice.get('profile_id'), choice.get('model'),
                                       'native' if 'image' in config.get('capabilities', []) else 'off')
    if binding['runtime'] != choice['runtime']:
        raise HTTPException(400, '连接与 Service 引擎不一致')
    config['runtime_binding'] = binding
    config['model'] = binding['model']
    return config


def authorize_service(actor_id, binding):
    """Additional, continuously checked policy after normal profile authorization."""
    scope = binding.get('service_scope')
    if scope is None:
        return
    from app.services.published import get_service, list_service_keys, consumer_conversation_exists
    for field in ('service_id', 'conversation_id'):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', scope.get(field, '')):
            raise HTTPException(400, 'Service 会话标识无效')
    svc = get_service(actor_id, scope['service_id'])
    preview = scope.get('channel') == 'admin_test'
    if not svc or (not preview and not svc.get('published', True)) or not external(svc):
        raise HTTPException(403, 'Service 已下线或已更换引擎')
    if revision(svc) != scope['revision'] or svc.get('runtime_binding') != {k: v for k, v in binding.items() if k != 'service_scope'}:
        raise HTTPException(409, 'Service 配置已变更，请重新发送消息')
    if not consumer_conversation_exists(actor_id, scope['service_id'], scope['conversation_id']):
        raise HTTPException(404, 'Service 会话已删除')
    if preview:
        from app.services.conversations import get_conversation_meta
        from app.services.published import get_consumer_conversation
        admin_conv_id = scope.get('admin_conversation_id')
        if not isinstance(admin_conv_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,36}', admin_conv_id):
            raise HTTPException(403, '测试会话归属无效')
        meta = get_conversation_meta(actor_id, admin_conv_id) if admin_conv_id else None
        preview_conv = get_consumer_conversation(actor_id, scope['service_id'], scope['conversation_id'])
        if (not meta or meta.get('test_service_id') != scope['service_id'] or
                meta.get('test_consumer_conversation_id') != scope['conversation_id'] or
                meta.get('test_service_revision') != scope['revision'] or
                not preview_conv or preview_conv.get('source') != 'admin_test' or
                preview_conv.get('admin_conversation_id') != admin_conv_id):
            raise HTTPException(403, '测试模式已关闭或会话不匹配')
    elif scope['channel'] in ('web', 'api'):
        keys = list_service_keys(actor_id, scope['service_id'])
        if not any(k['id'] == scope.get('key_id') and k.get('billing', 'hosted') == 'hosted' for k in keys):
            raise HTTPException(403, 'Service Key 已失效或不是托管 Key')
    elif scope['channel'] == 'wechat':
        from app.channels.wechat.policy import ensure_wechat_session
        ensure_wechat_session(actor_id, scope['service_id'], scope.get('wechat_session_id'),
                             conversation_id=scope['conversation_id'])
    else:
        raise HTTPException(400, '套餐 Service 尚不支持此执行渠道')


def service_authorizer(profiles):
    def authorize(actor_id, binding):
        result = profiles.authorize(actor_id, binding)
        authorize_service(actor_id, binding)
        authorize_scheduler(actor_id, binding)
        return result
    return authorize


def authorize_scheduler(actor_id, binding):
    """Continuously intersect a CLI run with its durable scheduled grant."""
    scope = binding.get('scheduler_scope')
    if scope is None:
        return
    if binding.get('service_scope') or not isinstance(scope, dict):
        raise HTTPException(403, '定时任务运行作用域无效')
    from app.execution.context import ExecutionContext, get_store
    from app.execution.grants import Grant
    from app.services import scheduler_tree as tree
    store = get_store()
    row = store.get(scope.get('run_id'), actor_id)
    if not row or row.get('token') is None:
        raise HTTPException(403, '定时任务授权已失效')
    grant = Grant(ExecutionContext(store, row))
    if grant.scope != 'admin' or grant.uid != actor_id or grant.tid != scope.get('task_id'):
        raise HTTPException(403, '定时任务运行身份不匹配')
    grant.policies()
    task = tree.load_task_or_migrate('admin', actor_id, grant.tid)
    if not task or task.get('revision') != scope.get('revision') or task.get('revision') != grant.snapshot.get('revision'):
        raise HTTPException(409, '定时任务已修改，请等待下次运行')
    saved = grant.snapshot.get('task_config') or {}
    base = {k: v for k, v in binding.items() if k != 'scheduler_scope'}
    if saved.get('runtime_binding') != base or task.get('task_config', {}).get('runtime_binding') != base:
        raise HTTPException(409, '定时任务引擎绑定已更改')
    for capability in ('web', 'image'):
        allowed = capability in grant.saved['capabilities']
        if bool(scope.get(capability)) != allowed:
            raise HTTPException(403, '定时任务能力超出授权')
        if allowed:
            grant.capability(capability)
    return grant


class RuntimeConsumerAgent:
    """LangChain message stream facade; the native client owns its own harness."""
    def __init__(self, admin_id, service_id, conv_id, *, channel='web', key_id=None,
                 wechat_session_id=None, preview_admin_conv_id=None):
        from app.runtime.manager import get_runtime
        from app.services.published import get_service
        from app.services.consumer_agent import _build_consumer_system_prompt
        from app.runtime.consumer_tools import ServiceTools
        self.runtime = get_runtime()
        self.admin_id = admin_id
        self.usage = None
        svc = get_service(admin_id, service_id)
        if not svc:
            raise HTTPException(404, 'Service 不存在')
        scope = {'service_id': service_id, 'conversation_id': conv_id, 'channel': channel,
                 'key_id': key_id, 'wechat_session_id': wechat_session_id, 'revision': revision(svc),
                 'web': bool(svc.get('research_tools') or 'web' in svc.get('capabilities', [])),
                 'image': 'image' in svc.get('capabilities', [])}
        if preview_admin_conv_id:
            scope['admin_conversation_id'] = preview_admin_conv_id
        binding = {**svc.get('runtime_binding', {}), 'service_scope': scope}
        if 'profile_id' not in binding:
            raise HTTPException(409, '请由 admin 重新保存 Service 的引擎连接')
        self.runtime.runs.authorize(admin_id, binding)
        self.scope = scope
        # The shared Service Key is the existing authorization boundary for web
        # and API. A WeChat conversation retains its dedicated caller identity.
        sessions = self.runtime.store.find('session', actor_id=admin_id, conversation_id=conv_id)
        # New tool contracts require a fresh supplier session, otherwise an
        # already-open native thread may keep its old dynamic tool registry.
        session = next((s for s in sessions if s['binding'] == binding and s.get('service_tools_version') == 4), None)
        if session is None:
            prompt = _build_consumer_system_prompt(admin_id, svc) + """

## OpenJellyfish 内部 Service
你通过套餐客户端提供本 Service 的回答。只能调用注册的 jellyfish_service_* 工具。
文档白名单、脚本白名单与本对话产物由服务端检查；不得访问宿主、其他对话、账号配置或 admin 长期记忆。
用 read_my_conversation 查询本对话历史；不需要在简单问候前扫描文档或记忆。
用户需要人工帮助或向管理员反馈时，调用 jellyfish_service_contact_admin；只能提交当前对话的反馈，不能承诺管理员已经阅读或回复。
原生网页搜索与生图仅在本 Service 启用相应能力时使用；不得调用其他付费模型或生成供应商。
文件只能通过业务工具读写。原生终端与文件修改审批会被拒绝。
原生生图结果自动归档并显示，无需复制文件或返回本机路径。
输出直接流式发送给用户。文件引用使用 <<FILE:/generated/相对路径>>。
"""
            if preview_admin_conv_id:
                prompt += ('\n\n## 管理员测试模式\n当前是 Service 预览。contact_admin 只模拟提交，'
                           '不会通知管理员或写入真实收件箱；不得声称已经实际通知。')
            specs = ServiceTools(self.runtime.runs.storage, self.runtime.store, self.runtime.runs.authorize).specifications(binding, admin_id)
            session = self.runtime.runs.create_session(admin_id, binding, conversation_id=conv_id,
                                                       instructions=prompt, dynamic_tools=specs)
            session['instructions_version'] = 3
            session['service_tools_version'] = 4
            self.runtime.store.put('session', session)
        self.session = session

    async def aget_state(self, config):
        # Compatibility with the shared stream/WeChat adapter. Runtime sessions
        # have no LangGraph checkpoints or human approvals to auto-approve.
        return SimpleNamespace(values={'messages': []}, tasks=())

    async def aupdate_state(self, *args, **kwargs):
        raise HTTPException(409, '套餐 Service 不接受 LangGraph 定时任务注入')

    async def astream(self, agent_input, config=None, **kwargs):
        messages = agent_input.get('messages', [])
        if len(messages) != 1 or messages[0].get('role') != 'user':
            raise HTTPException(400, '需要一条用户消息')
        content = messages[0].get('content', '')
        attachments = []
        if isinstance(content, list):
            parts = []
            for block in content:
                if block.get('type') == 'text':
                    parts.append(block.get('text', ''))
                elif block.get('type') == 'image_url':
                    attachments.append({'name': f'image-{len(attachments) + 1}.png', 'data_url': block.get('image_url', {}).get('url')})
                else:
                    raise HTTPException(400, '套餐 Service 消息仅接受文本和 Base64 图片')
            content = '\n'.join(parts)
        run = self.runtime.runs.enqueue(self.admin_id, self.session['id'],
                                        agent_input.get('request_id') or uuid.uuid4().hex,
                                        content, attachments=attachments)
        rid, cursor = run['id'], 0
        finished = False
        try:
            while True:
                events = self.runtime.store.events(rid, cursor)
                for event in events:
                    cursor = event['seq']
                    kind, payload = event['type'], event['payload']
                    msg = None
                    if kind == 'text_delta':
                        msg = AIMessageChunk(content=payload.get('text', ''))
                    elif kind in ('business_tool', 'tool'):
                        name = payload.get('name') or payload.get('kind') or 'tool'
                        name = name.removeprefix('jellyfish_service_')
                        tid = payload.get('item_id') or name
                        if payload.get('status') in ('running', 'inProgress', 'in_progress', 'pending'):
                            msg = AIMessageChunk(content='', tool_call_chunks=[{'name': name, 'args': '{}', 'id': tid, 'index': 0}])
                        elif payload.get('status') in ('completed', 'failed', 'cancelled', 'declined'):
                            msg = ToolMessage(content=(payload.get('result') if kind == 'business_tool' else None) or payload.get('status', ''), name=name, tool_call_id=tid)
                    elif kind == 'artifact_created':
                        msg = AIMessageChunk(content='\n\n<<FILE:' + payload['artifact']['path'] + '>>')
                    elif kind in TERMINAL:
                        finished = True
                        if kind != 'completed':
                            raise HTTPException(502, payload.get('message') or '套餐任务已停止，请检查 Service 和连接授权')
                        return
                    if msg is not None:
                        yield (), (msg, {'langgraph_node': 'service_runtime'})
                await asyncio.sleep(.03)
        finally:
            if not finished:
                await self.runtime.runs.cancel(self.admin_id, rid)
            final = self.runtime.store.get('run', rid)
            usage = final.get('usage') or {}
            usage = usage.get('last') or usage
            if 'inputTokens' in usage and 'outputTokens' in usage:
                self.usage = {'prompt_tokens': usage['inputTokens'], 'completion_tokens': usage['outputTokens'],
                              'total_tokens': usage.get('totalTokens', usage['inputTokens'] + usage['outputTokens'])}
                from app.services.token_usage import record_llm_usage
                record_llm_usage(self.admin_id, final['binding']['runtime'] + ':' + final['binding']['model'],
                    usage['inputTokens'], usage['outputTokens'], service_id=(None if self.scope['channel'] == 'admin_test' else self.scope['service_id']),
                    conv_id=(self.scope.get('admin_conversation_id') or self.scope['conversation_id']),
                    channel=self.scope['channel'], key_id=self.scope.get('key_id') or '',
                    runtime=final['binding']['runtime'])
