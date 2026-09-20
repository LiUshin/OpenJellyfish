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
from datetime import datetime
from types import SimpleNamespace

from fastapi import HTTPException
from langchain_core.messages import AIMessageChunk, ToolMessage

from app.runtime.store import TERMINAL


def external(config):
    return (config.get('runtime_choice') or {}).get('runtime', 'deepagents') in ('codex', 'cursor')


def revision(config):
    keys = ('runtime_choice', 'runtime_binding', 'allowed_docs', 'allowed_scripts',
            'capabilities', 'research_tools', 'system_prompt_version_id', 'user_profile_version_id')
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
    if not svc or not svc.get('published', True) or not external(svc):
        raise HTTPException(403, 'Service 已下线或已更换引擎')
    if revision(svc) != scope['revision'] or svc.get('runtime_binding') != {k: v for k, v in binding.items() if k != 'service_scope'}:
        raise HTTPException(409, 'Service 配置已变更，请重新发送消息')
    if not consumer_conversation_exists(actor_id, scope['service_id'], scope['conversation_id']):
        raise HTTPException(404, 'Service 会话已删除')
    if scope['channel'] in ('web', 'api'):
        keys = list_service_keys(actor_id, scope['service_id'])
        if not any(k['id'] == scope.get('key_id') and k.get('billing', 'hosted') == 'hosted' for k in keys):
            raise HTTPException(403, 'Service Key 已失效或不是托管 Key')
    elif scope['channel'] == 'wechat':
        from app.channels.wechat.session_manager import get_session_manager
        wc = svc.get('wechat_channel', {})
        expiry = wc.get('expires_at')
        if not wc.get('enabled') or (expiry and datetime.fromisoformat(expiry).replace(tzinfo=None) < datetime.now()):
            raise HTTPException(403, '微信渠道已停用或过期')
        current = get_session_manager().get_session(scope.get('wechat_session_id'))
        if not current or (current.admin_id, current.service_id, current.conversation_id) != (actor_id, scope['service_id'], scope['conversation_id']):
            raise HTTPException(403, '微信会话已失效')
    else:
        raise HTTPException(400, '套餐 Service 尚不支持此执行渠道')


def service_authorizer(profiles):
    def authorize(actor_id, binding):
        result = profiles.authorize(actor_id, binding)
        authorize_service(actor_id, binding)
        return result
    return authorize


class RuntimeConsumerAgent:
    """LangChain message stream facade; the native client owns its own harness."""
    def __init__(self, admin_id, service_id, conv_id, *, channel='web', key_id=None, wechat_session_id=None):
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
        binding = {**svc.get('runtime_binding', {}), 'service_scope': scope}
        if 'profile_id' not in binding:
            raise HTTPException(409, '请由 admin 重新保存 Service 的引擎连接')
        self.runtime.runs.authorize(admin_id, binding)
        self.scope = scope
        # The shared Service Key is the existing authorization boundary for web
        # and API. A WeChat conversation retains its dedicated caller identity.
        sessions = self.runtime.store.find('session', actor_id=admin_id, conversation_id=conv_id)
        session = next((s for s in sessions if s['binding'] == binding), None)
        if session is None:
            prompt = _build_consumer_system_prompt(admin_id, svc) + """

## OpenJellyfish 内部 Service
你通过套餐客户端提供本 Service 的回答。只能调用注册的 jellyfish_service_* 工具。
文档白名单、脚本白名单与本对话产物由服务端检查；不得访问宿主、其他对话、账号配置或 admin 长期记忆。
用 read_my_conversation 查询本对话历史；不需要在简单问候前扫描文档或记忆。
原生网页搜索与生图仅在本 Service 启用相应能力时使用；不得调用其他付费模型或生成供应商。
文件只能通过业务工具读写。原生终端与文件修改审批会被拒绝。
原生生图结果自动归档并显示，无需复制文件或返回本机路径。
输出直接流式发送给用户。文件引用使用 <<FILE:/generated/相对路径>>。
"""
            specs = ServiceTools(self.runtime.runs.storage, self.runtime.store, self.runtime.runs.authorize).specifications(binding, admin_id)
            session = self.runtime.runs.create_session(admin_id, binding, conversation_id=conv_id,
                                                       instructions=prompt, dynamic_tools=specs)
            session['instructions_version'] = 3
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
        run = self.runtime.runs.enqueue(self.admin_id, self.session['id'], uuid.uuid4().hex, content, attachments=attachments)
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
                    usage['inputTokens'], usage['outputTokens'], service_id=self.scope['service_id'],
                    conv_id=self.scope['conversation_id'], channel=self.scope['channel'], key_id=self.scope.get('key_id') or '')
