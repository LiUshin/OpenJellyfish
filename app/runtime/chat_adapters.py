"""Shared chat lifecycle entrypoint with an explicit runtime registry.

The DeepAgents adapter preserves its graph/checkpointer/HITL protocol. Codex uses
the durable external-run service. Neither adapter can select another engine as a
fallback; new providers register here instead of branching product routes.
"""
from fastapi import HTTPException
from app.runtime.chat import conversation_binding, enqueue_chat, events_response
from app.runtime.manager import get_runtime
from app.runtime.store import TERMINAL


class DeepAgentsChatAdapter:
    async def start(self, req, user, meta):
        from app.routes.chat import _deepagents_chat
        model = (meta.get('runtime_binding') or {}).get('model')
        if model and not req.model:
            req.model = model
        if req.model and meta.get('runtime_binding'):
            from app.services.conversations import _write_meta
            meta['runtime_binding']['model'] = req.model
            _write_meta(user['user_id'], req.conversation_id, meta)
        return await _deepagents_chat(req, user)

    async def stop(self, req, user, meta):
        from app.routes.chat import _deepagents_stop
        return await _deepagents_stop(req, user)

    async def resume(self, req, user, meta):
        from app.routes.chat import _deepagents_resume
        model = (meta.get('runtime_binding') or {}).get('model')
        if model and not req.model:
            req.model = model
        return await _deepagents_resume(req, user)


class ExternalChatAdapter:
    async def start(self, req, user, meta):
        run = enqueue_chat(user['user_id'], req.conversation_id, req.request_id, req.message, model=req.model)
        return events_response(get_runtime(), user['user_id'], run['id'], legacy=True)

    async def stop(self, req, user, meta):
        if req.follow_up is not None:
            raise HTTPException(400, '外部引擎请停止后发送下一条消息')
        runtime = get_runtime()
        for run in runtime.store.find('run', session_id=meta['runtime_session_id'], actor_id=user['user_id']):
            if run['status'] not in TERMINAL:
                await runtime.runs.cancel(user['user_id'], run['id'])
        return {'status': 'stopped'}

    async def resume(self, req, user, meta):
        raise HTTPException(409, '外部引擎审批请使用带 run_id 和 approval_id 的运行接口')


CHAT_ADAPTERS = {'deepagents': DeepAgentsChatAdapter(), 'codex': ExternalChatAdapter(), 'cursor': ExternalChatAdapter()}


async def dispatch(operation, req, user):
    meta = conversation_binding(user['user_id'], req.conversation_id)
    kind = (meta.get('runtime_binding') or {}).get('runtime', 'deepagents')
    adapter = CHAT_ADAPTERS.get(kind)
    if not adapter or (kind in ('codex', 'cursor') and not meta.get('runtime_session_id')):
        raise HTTPException(409, '会话绑定的引擎不可用；不会回退到其他引擎')
    return await getattr(adapter, operation)(req, user, meta)
