"""Independent delivery worker: external sends are never coupled to an agent retry."""
import asyncio
import logging
import json
from app.services.scheduler_policy import validate_reply

log = logging.getLogger(__name__)


def _has_consumer_intent(result, payload):
    if result.get('status') != 'success':
        return False
    if payload.get('explicit_message') is True:
        return True
    # Pre-marker records require the actual message, not just a queued step:
    # old send_message('') also logged delivery_queued and then fell back to
    # private commentary. Truncated or malformed legacy evidence is ambiguous.
    texts = []
    for step in result.get('steps', []):
        if not isinstance(step, dict) or step.get('type') != 'tool_result' or step.get('tool') != 'send_message':
            continue
        try:
            message = json.loads(step['result_preview'])
        except (KeyError, TypeError, ValueError):
            return False
        if not isinstance(message, dict):
            return False
        text = message.get('text')
        if isinstance(text, str) and text:
            texts.append(text)
    return bool(texts) and '\n\n'.join(texts) == payload.get('text')


def authorize_target(target):
    from app.core.security import _load_users
    uid, sid = target['admin_id'], target.get('service_id')
    owner = _load_users().get(uid)
    if not owner or owner.get('disabled'):
        raise PermissionError('Delivery owner is unavailable')
    validate_reply(target, uid, sid, check_session=False)
    if sid:
        from app.channels.wechat.policy import ensure_service_active, ensure_wechat_session
        from fastapi import HTTPException
        from app.services.published import get_consumer_conversation
        try:
            ensure_service_active(uid, sid)
            if target.get('channel') == 'wechat':
                ensure_wechat_session(uid, sid, target.get('session_id', ''),
                                      conversation_id=target.get('conversation_id'))
        except HTTPException as exc:
            raise PermissionError(str(exc.detail)) from exc
        conv = get_consumer_conversation(uid, sid, target['conversation_id'])
        if not conv or conv.get('source') == 'admin_test':
            raise PermissionError('Delivery service or conversation no longer exists')
    else:
        from app.services.conversations import get_conversation
        if not get_conversation(uid, target['conversation_id']):
            raise PermissionError('Delivery conversation no longer exists')


async def deliver_one(store, delivery):
    target, payload, channel = delivery['target'], delivery['payload'], delivery['channel']
    sending = False
    try:
        authorize_target(target)
        run = store.get(delivery['run_id'])
        scope, owner, service, tid = json.loads(run['task_key'])
        if service:
            result = run.get('result') or {}
            # Older versions persisted raw diagnostics as consumer deliveries.
            # Preserve old legitimate sends only when their committed Run
            # proves a send_message was collected; never release an old error.
            if not _has_consumer_intent(result, payload):
                raise PermissionError('无法确认已提交的消费者消息内容；已取消投递，请管理员检查运行记录后重新发送')
        from app.execution.context import load_task
        current = load_task(scope, owner, tid, service)
        if not current or current.get('reply_to') != run['snapshot'].get('reply_to'):
            raise PermissionError('Delivery target has been revoked')
        uid, sid, conv = target['admin_id'], target.get('service_id'), target['conversation_id']
        if channel == 'web':
            from app.services.scheduler import _build_scheduled_task_block
            block = _build_scheduled_task_block(payload['task_meta'], payload['text'], success=payload['success'])
            if sid:
                from app.services.published import save_consumer_message
                save_consumer_message(uid, sid, conv, 'assistant', payload['text'], blocks=[block], event_id=delivery['id'])
            else:
                from app.services.conversations import save_message
                save_message(uid, conv, 'assistant', payload['text'], blocks=[block], event_id=delivery['id'])
        elif channel == 'memory':
            from app.services.scheduled_inject import project_delivery
            await project_delivery(target, payload, delivery['sequence'])
        elif channel == 'wechat':
            from app.services.scheduler import _resolve_wechat_client
            client, to_user, context_token = _resolve_wechat_client(target, owner_id=uid, owner_service_id=sid)
            if not client or not to_user or not context_token:
                raise ConnectionError('Delivery channel is not connected')
            # From this point an exception cannot prove that the remote side did not receive it.
            sending = True
            receipt = await client.send_text(to_user, payload['text'], context_token)
            if not isinstance(receipt, dict) or receipt.get('ret') != 0:
                raise RuntimeError('Transport did not acknowledge delivery')
        else:
            raise PermissionError('Unsupported delivery channel')
        store.delivery_done(delivery, 'delivered')
    except asyncio.CancelledError:
        store.delivery_done(delivery, 'unknown' if sending else 'retry_wait', 'Delivery interrupted')
        raise
    except BlockingIOError:
        store.defer_delivery(delivery)
    except PermissionError as exc:
        store.delivery_done(delivery, 'unknown' if sending else 'cancelled', str(exc))
    except Exception as exc:
        store.delivery_done(delivery, 'unknown' if sending else 'retry_wait', str(exc))


async def delivery_loop(store):
    while True:
        delivery = store.claim_delivery()
        if delivery:
            try:
                async with asyncio.timeout(120):
                    await deliver_one(store, delivery)
            except TimeoutError:
                log.warning("Delivery timed out: %s", delivery["id"])
        else:
            await asyncio.sleep(1)
