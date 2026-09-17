import asyncio
import json
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.deps import get_current_user
from app.runtime.files import digest
from app.runtime.pilot import get_pilot, TERMINAL
from app.runtime.rpc import RuntimeFailure

router = APIRouter(prefix='/api/runtime-pilot', tags=['runtime-pilot'])


class NewSession(BaseModel):
    model: str | None = Field(default=None, max_length=120)
    context_paths: list[str] = Field(default_factory=list, max_length=20)


class NewTurn(BaseModel):
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    message: str = Field(min_length=1, max_length=32000)


class Decision(BaseModel):
    approval_id: str
    decision: Literal['accept', 'decline']


def pilot(user=Depends(get_current_user)):
    return get_pilot(user['user_id'])


@router.get('/status')
async def status(p=Depends(pilot)):
    return {'runtime': 'codex_app_server', 'enabled': True, 'scope': 'single_admin_scratch',
            'active': [{'session_id': sid, 'run_id': rid} for sid, rid in p.active]}


@router.post('/probe')
async def probe(p=Depends(pilot)):
    try:
        return await p.probe()
    except RuntimeFailure as exc:
        raise HTTPException(503, str(exc))


@router.get('/sessions')
async def sessions(p=Depends(pilot)):
    return p.list_sessions()


@router.post('/sessions')
async def create_session(req: NewSession, p=Depends(pilot)):
    try:
        return await asyncio.to_thread(p.create_session, req.model, req.context_paths)
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc))


@router.get('/sessions/{sid}')
async def session(sid: str, p=Depends(pilot)):
    s = p.public_session(p.read_session(sid))
    return {**s, 'runs': [p.read_run(sid, rid) for rid in s['runs']]}


@router.post('/sessions/{sid}/turns')
async def turn(sid: str, req: NewTurn, p=Depends(pilot)):
    return p.start_turn(sid, req.request_id, req.message)


@router.get('/sessions/{sid}/runs/{rid}/events')
async def events(sid: str, rid: str, after: int = Query(default=0, ge=0), p=Depends(pilot)):
    p.read_run(sid, rid)

    async def stream():
        cursor = after
        while True:
            run, items = p.events(sid, rid, cursor)
            for item in items:
                cursor = item['seq']
                yield f"id: {cursor}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            if run['status'] in TERMINAL:
                return
            yield ': keepalive\n\n'
            await asyncio.sleep(.3)

    return StreamingResponse(stream(), media_type='text/event-stream', headers={
        'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
    })


@router.post('/sessions/{sid}/runs/{rid}/approve')
async def approve(sid: str, rid: str, req: Decision, p=Depends(pilot)):
    p.approve(sid, rid, req.approval_id, req.decision)
    return {'status': 'accepted'}


@router.post('/sessions/{sid}/runs/{rid}/cancel')
async def cancel(sid: str, rid: str, p=Depends(pilot)):
    await p.cancel(sid, rid)
    return p.read_run(sid, rid)


@router.get('/sessions/{sid}/artifacts/{aid}')
async def artifact(sid: str, aid: str, p=Depends(pilot)):
    session = p.read_session(sid)
    item = next((a for a in session['artifacts'] if a['id'] == aid), None)
    if not item:
        raise HTTPException(404, '产物不存在')
    data = await asyncio.to_thread(p.storage.read_bytes, p.user_id, item['path'])
    if digest(data) != item['sha256']:
        raise HTTPException(409, '产物已被修改，与归档记录不一致')
    mode = 'inline' if item['mime'].startswith('image/') else 'attachment'
    return Response(data, media_type=item['mime'], headers={
        'Content-Disposition': f"{mode}; filename*=UTF-8''{quote(item['name'].split('/')[-1])}",
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
        'Content-Security-Policy': "default-src 'none'; sandbox",
    })
