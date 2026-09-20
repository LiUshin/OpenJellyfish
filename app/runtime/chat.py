"""Main-chat dispatcher. Legacy DeepAgents conversations retain their pipeline."""
import asyncio
from contextlib import closing
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from app.core.settings import ROOT_DIR
from app.runtime.manager import get_runtime
from app.runtime.policy import enabled
from app.runtime.store import TERMINAL
from app.runtime.business_tools import instructions, specifications


def history(actor_id, session_id):
    # History stays readable with the feature switched off. Opening this read-only
    # connection must not claim the scheduler or mutate crash recovery state.
    root = Path(os.getenv('JELLYFISH_RUNTIME_DATA_DIR', str(Path(ROOT_DIR) / 'data' / 'runtime'))).resolve()
    path = root / 'runtime.sqlite3'
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute("SELECT data FROM records WHERE kind='run' AND json_extract(data,'$.actor_id')=? AND json_extract(data,'$.session_id')=? ORDER BY json_extract(data,'$.created_at')", (actor_id, session_id)).fetchall()
    messages = []
    for raw, in rows:
        run = json.loads(raw)
        messages.append({'role': 'user', 'content': run['message'], 'timestamp': datetime.fromtimestamp(run['created_at']).isoformat(), 'run_id': run['id']})
        if run.get('output') or run['status'] in TERMINAL:
            output = run.get('output', '')
            for item in run.get('artifacts', []):
                output += f"\n\n<<FILE:{item['path']}>>"
            messages.append({'role': 'assistant', 'content': output, 'run_id': run['id'], 'runtime_status': run['status']})
    return messages


def choice(actor_id, requested):
    """Choose the connection and initial model; subsequent turns can select a model."""
    if requested is None:
        if enabled():
            pref = get_runtime().store.get('preference', actor_id)
            if pref:
                requested = pref['choice']
        requested = requested or {'runtime': 'deepagents'}
    if requested['runtime'] == 'deepagents':
        from app.services.model_catalog import get_default_model
        return {'runtime': 'deepagents', 'model': requested.get('model') or get_default_model('llm', user_id=actor_id), 'image_mode': 'configured'}
    if requested['runtime'] not in ('codex', 'cursor'):
        raise HTTPException(400, '此引擎未接入')
    binding = get_runtime().profiles.binding(actor_id, requested.get('profile_id'), requested.get('model'), requested.get('image_mode', 'native'))
    if binding['runtime'] != requested['runtime']:
        raise HTTPException(400, '所选引擎与连接不一致')
    return binding


def bind_conversation(actor_id, conversation, binding, context_paths):
    from app.services.conversations import _write_meta
    meta = {k: v for k, v in conversation.items() if k != 'messages'}
    meta['runtime_binding'] = binding
    if binding['runtime'] in ('codex', 'cursor'):
        runtime = get_runtime()
        session = runtime.runs.create_session(actor_id, binding, instructions=instructions(actor_id, binding['runtime']),
                                              context_paths=context_paths, conversation_id=conversation['id'],
                                              dynamic_tools=specifications())
        meta['runtime_session_id'] = session['id']
    _write_meta(actor_id, conversation['id'], meta)
    return {**meta, 'messages': []}


def conversation_binding(actor_id, conv_id):
    import re
    from app.services.conversations import _load_meta, _migrate_if_needed
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,36}', conv_id):
        raise HTTPException(400, '无效的对话 ID')
    _migrate_if_needed(actor_id, conv_id)
    meta = _load_meta(actor_id, conv_id)
    if not meta:
        raise HTTPException(404, '对话不存在')
    return meta


def enqueue_chat(actor_id, conv_id, request_id, message, model=None, attachments=None, *, yolo=False):
    meta = conversation_binding(actor_id, conv_id)
    if not meta.get('runtime_session_id'):
        raise HTTPException(409, '此会话使用 DeepAgents')
    attachments = list(attachments or [])
    if isinstance(message, list):
        parts = []
        for part in message:
            if part.get('type') == 'text':
                parts.append(part.get('text', ''))
            elif part.get('type') == 'image_url':
                attachments.append({'name': f'image-{len(attachments) + 1}.png',
                                    'data_url': part.get('image_url', {}).get('url')})
            else:
                raise HTTPException(400, '不支持的消息输入类型')
        message = '\n'.join(parts)
    if not isinstance(message, str) or len(message) > 32000 or (not message.strip() and not attachments):
        raise HTTPException(400, '请输入消息或添加附件')
    if not request_id:
        raise HTTPException(400, '外部引擎聊天需要 request_id 以防止重复执行')
    runtime = get_runtime()
    session = runtime.runs.own('session', meta['runtime_session_id'], actor_id)
    if session.get('deleted'):
        raise HTTPException(404, '会话已删除')
    run = runtime.runs.enqueue(actor_id, session['id'], request_id, message, model=model, attachments=attachments, yolo=yolo)
    meta['runtime_binding'] = runtime.store.get('session', session['id'])['binding']
    from app.services.conversations import _write_meta
    runs = runtime.store.find('run', session_id=session['id'], actor_id=actor_id)
    meta['message_count'] = len(runs) * 2
    meta['updated_at'] = datetime.now().isoformat()
    if meta.get('title') == '新对话':
        meta['title'] = message[:30] or attachments[0]['name'][:30]
    _write_meta(actor_id, conv_id, meta)
    return run


def events_response(runtime, actor_id, rid, after=0, legacy=False):
    runtime.runs.own('run', rid, actor_id)

    async def stream():
        cursor = after
        heartbeat = 0
        while True:
            run = runtime.runs.own('run', rid, actor_id)
            for event in runtime.store.events(rid, cursor):
                cursor = event['seq']
                value = event
                if legacy:
                    t, p = event['type'], event['payload']
                    if t == 'text_delta':
                        value = {'type': 'token', 'content': p['text']}
                    elif t == 'failed':
                        value = {'type': 'error', 'content': p.get('message', '执行失败')}
                    elif t in ('completed', 'cancelled'):
                        value = {'type': 'done'}
                    elif t == 'approval_resolved' and p.get('automatic') and p.get('decision') == 'accept':
                        value = {'type': 'auto_approve', 'count': 1, 'actions': [{'name': p['kind'], 'args': {}}]}
                    elif t == 'approval_requested':
                        value = {'type': 'interrupt', 'actions': [{'name': 'runtime_approval', 'args': p['approval']}], 'configs': []}
                    else:
                        continue
                yield f"id: {cursor}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
            if cursor >= run['seq'] and run['status'] in TERMINAL:
                return
            heartbeat += 1
            if heartbeat >= 200:
                yield ': keepalive\n\n'
                heartbeat = 0
            await asyncio.sleep(.05)

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
