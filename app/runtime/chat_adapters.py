"""Shared chat lifecycle entrypoint with an explicit runtime registry.

The DeepAgents adapter preserves its graph/checkpointer/HITL protocol. Codex uses
the durable external-run service. Neither adapter can select another engine as a
fallback; new providers register here instead of branching product routes.
"""
import asyncio
from contextlib import aclosing
import json
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
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
        run = enqueue_chat(user['user_id'], req.conversation_id, req.request_id, req.message, model=req.model, yolo=bool(req.yolo))
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


class ServiceTestChatAdapter:
    async def start(self, req, user, meta):
        from app.services.service_test import begin_test_turn, end_test_turn
        from app.services.consumer_agent import create_consumer_agent
        from app.services.published import save_consumer_message
        from app.services.conversations import save_message
        from app.routes.chat import _extract_text, _extract_and_save_images
        from app.routes.consumer import _stream_consumer
        from app.services.prompt import stamp_message, expand_file_mentions
        from app.services.token_usage import build_usage_callbacks

        uid, conv_id = user['user_id'], req.conversation_id
        scope = begin_test_turn(uid, conv_id)
        sid, preview_id = scope['service_id'], scope['preview_conversation_id']
        try:
            from app.runtime.consumer import external
            if external(scope['service_config']):
                # RuntimeStore owns a thread-affine SQLite connection. Native
                # Service session construction must stay on the event-loop thread.
                agent = create_consumer_agent(uid, sid, preview_id,
                    channel='admin_test', preview_admin_conv_id=conv_id)
            else:
                agent = await asyncio.to_thread(
                    create_consumer_agent, uid, sid, preview_id,
                    channel='admin_test', preview_admin_conv_id=conv_id)
            message = expand_file_mentions(req.message)
            text = _extract_text(message)
            attachments = _extract_and_save_images(uid, conv_id, message)
            save_consumer_message(uid, sid, preview_id, 'user', text)
            save_message(uid, conv_id, 'user', text, attachments=attachments,
                         test_service_id=sid)
            config = {
                'configurable': {'thread_id': f'svc-{sid}-{preview_id}'},
                'callbacks': build_usage_callbacks(uid, channel='admin_test',
                    conv_id=conv_id, model_hint=scope['service_config'].get('model', '')),
            }
            inner = _stream_consumer(agent,
                {'messages': [{'role': 'user', 'content': stamp_message(message, uid)}],
                 'request_id': req.request_id},
                config, {'admin_id': uid, 'service_id': sid}, preview_id,
                mirror_admin_conv_id=conv_id)
        except BaseException:
            end_test_turn(uid, conv_id)
            raise

        async def stream():
            try:
                async with aclosing(inner):
                    async for event in inner:
                        if scope['cancel_event'].is_set():
                            yield 'data: ' + json.dumps({'type': 'done'}) + '\n\n'
                            break
                        yield event
            finally:
                end_test_turn(uid, conv_id)

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    async def stop(self, req, user, meta):
        from app.services.service_test import cancel_test_turn
        if req.follow_up is not None:
            raise HTTPException(400, '测试模式请停止后发送下一条消息')
        return {'status': 'stopping' if cancel_test_turn(user['user_id'], req.conversation_id)
                else 'not_running'}

    async def resume(self, req, user, meta):
        raise HTTPException(409, 'Service 测试回合没有待审批操作')


CHAT_ADAPTERS = {'deepagents': DeepAgentsChatAdapter(), 'codex': ExternalChatAdapter(), 'cursor': ExternalChatAdapter()}
SERVICE_TEST_ADAPTER = ServiceTestChatAdapter()


async def dispatch(operation, req, user):
    meta = conversation_binding(user['user_id'], req.conversation_id)
    if meta.get('test_service_id'):
        return await getattr(SERVICE_TEST_ADAPTER, operation)(req, user, meta)
    kind = (meta.get('runtime_binding') or {}).get('runtime', 'deepagents')
    adapter = CHAT_ADAPTERS.get(kind)
    if not adapter or (kind in ('codex', 'cursor') and not meta.get('runtime_session_id')):
        raise HTTPException(409, '会话绑定的引擎不可用；不会回退到其他引擎')
    return await getattr(adapter, operation)(req, user, meta)
