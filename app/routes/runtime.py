"""Authenticated runtime management; credentials never cross the HTTP boundary."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from app.deps import get_current_user, get_host_user
from app.runtime.manager import get_runtime
from app.runtime.policy import DeploymentPolicy
from app.runtime.rpc import RuntimeFailure, RuntimeUnavailable

router = APIRouter(prefix='/api/runtime', tags=['runtime'])
management_router = APIRouter(prefix='/api/superadmin/runtime', tags=['superadmin'])


class NewProfile(BaseModel):
    runtime: Literal['codex', 'cursor'] = 'codex'
    name: str = Field(default='我的 Codex', min_length=1, max_length=80)


class LoginStart(BaseModel):
    mode: Literal['chatgpt', 'chatgptDeviceCode', 'cursorBrowser'] = 'chatgptDeviceCode'


class GrantRequest(BaseModel):
    actor_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    models: list[str] = Field(min_length=1, max_length=100)


class InputAttachment(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    data_url: str = Field(max_length=11200000)


class TurnRequest(BaseModel):
    conversation_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,36}$')
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{16,80}$')
    message: str = Field(default='', max_length=32000)
    attachments: list[InputAttachment] = Field(default_factory=list, max_length=5)
    yolo: bool = False
    model: str | None = Field(default=None, min_length=1, max_length=300)


class ApprovalRequest(BaseModel):
    approval_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    decision: Literal['accept', 'decline']


@router.get('/capabilities')
async def capabilities(user=Depends(get_current_user)):
    return DeploymentPolicy.from_env().public(user['user_id'])


@router.get('/profiles')
async def profiles(user=Depends(get_current_user), runtime=Depends(get_runtime)):
    return runtime.profiles.list(user['user_id'])


@management_router.post('/profiles')
async def create_profile(req: NewProfile, user=Depends(get_host_user), runtime=Depends(get_runtime)):
    p = runtime.profiles.create(user['user_id'], req.name, req.runtime)
    return runtime.profiles.public(p, user['user_id'])


@management_router.post('/profiles/{pid}/login')
async def start_login(pid: str, req: LoginStart, user=Depends(get_host_user), runtime=Depends(get_runtime)):
    try:
        return await runtime.profiles.start_login(user['user_id'], pid, req.mode)
    except RuntimeUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeFailure as exc:
        raise HTTPException(502, str(exc))


@management_router.get('/profiles/{pid}/login/{lid}')
async def login_status(pid: str, lid: str, user=Depends(get_host_user), runtime=Depends(get_runtime)):
    runtime.profiles.managed(user['user_id'], pid)
    attempt = runtime.store.get('login', lid)
    if not attempt or attempt['profile_id'] != pid or attempt['actor_id'] != user['user_id']:
        raise HTTPException(404, '登录尝试不存在')
    return runtime.profiles.public_login(attempt)


@management_router.delete('/profiles/{pid}/login/{lid}')
async def cancel_login(pid: str, lid: str, user=Depends(get_host_user), runtime=Depends(get_runtime)):
    await runtime.profiles.cancel_login(user['user_id'], pid, lid)
    return {'status': 'cancelled'}


@management_router.post('/profiles/{pid}/probe')
async def probe(pid: str, user=Depends(get_host_user), runtime=Depends(get_runtime)):
    try:
        return await runtime.profiles.probe(user['user_id'], pid)
    except RuntimeUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeFailure as exc:
        raise HTTPException(502, str(exc))


@management_router.delete('/profiles/{pid}/connection')
async def disconnect(pid: str, user=Depends(get_host_user), runtime=Depends(get_runtime)):
    return await runtime.profiles.disconnect(user['user_id'], pid)


@management_router.get('/admins')
async def admins(user=Depends(get_host_user), runtime=Depends(get_runtime)):
    runtime.profiles.owner(user['user_id'])
    from app.core.security import _load_users
    return [{'id': uid, 'username': info.get('username', uid)} for uid, info in _load_users().items()
            if uid != user['user_id'] and not info.get('disabled')]


@management_router.get('/profiles/{pid}/grants')
async def grants(pid: str, user=Depends(get_host_user), runtime=Depends(get_runtime)):
    runtime.profiles.managed(user['user_id'], pid)
    return runtime.store.find('grant', profile_id=pid)


@management_router.put('/profiles/{pid}/grants')
async def grant(pid: str, req: GrantRequest, user=Depends(get_host_user), runtime=Depends(get_runtime)):
    g = runtime.profiles.grant(user['user_id'], pid, req.actor_id, req.models)
    # Updating an allowlist is a new grant version; finish invalidating older runs
    # before reporting that the authorization change has completed.
    await runtime.runs.cancel_profile(pid, req.actor_id)
    return g


@management_router.delete('/profiles/{pid}/grants/{gid}')
async def revoke(pid: str, gid: str, user=Depends(get_host_user), runtime=Depends(get_runtime)):
    return await runtime.profiles.revoke(user['user_id'], pid, gid)


@router.get('/usage')
async def usage(user=Depends(get_current_user), runtime=Depends(get_runtime)):
    # User-attributed usage does not expand the supplier account quota.
    return [{'run_id': r['id'], 'profile_id': r['binding']['profile_id'], 'status': r['status'],
             'created_at': r['created_at'], 'usage': r.get('usage')}
            for r in runtime.store.find('run', actor_id=user['user_id'])][-100:]


@router.get('/preferences')
async def preferences(user=Depends(get_current_user), runtime=Depends(get_runtime)):
    record = runtime.store.get('preference', user['user_id'])
    return record['choice'] if record else {'runtime': 'deepagents'}


from app.schemas.requests import RuntimeChoice


@router.put('/preferences')
async def update_preferences(req: RuntimeChoice, user=Depends(get_current_user), runtime=Depends(get_runtime)):
    from app.runtime.chat import choice
    selected = req.model_dump()
    choice(user['user_id'], selected)
    runtime.store.put('preference', {'id': user['user_id'], 'choice': selected})
    return selected


@router.get('/sessions/{sid}')
async def session(sid: str, user=Depends(get_current_user), runtime=Depends(get_runtime)):
    s = runtime.runs.own('session', sid, user['user_id'])
    if s.get('deleted'):
        raise HTTPException(404, '会话已删除')
    return {**{k: s.get(k) for k in ('id', 'binding', 'created_at', 'conversation_id', 'artifacts')},
            'runs': sorted(runtime.store.find('run', session_id=sid, actor_id=user['user_id']), key=lambda r: r['created_at'])}


@router.post('/turns')
async def turn(req: TurnRequest, user=Depends(get_current_user), runtime=Depends(get_runtime)):
    from app.runtime.chat import enqueue_chat
    return enqueue_chat(user['user_id'], req.conversation_id, req.request_id, req.message, model=req.model, attachments=[a.model_dump() for a in req.attachments], yolo=req.yolo)


@router.get('/runs/{rid}/events')
async def events(rid: str, after: int = Query(default=0, ge=0), user=Depends(get_current_user), runtime=Depends(get_runtime)):
    from app.runtime.chat import events_response
    return events_response(runtime, user['user_id'], rid, after)


@router.post('/runs/{rid}/approve')
async def approve(rid: str, req: ApprovalRequest, user=Depends(get_current_user), runtime=Depends(get_runtime)):
    runtime.runs.approve(user['user_id'], rid, req.approval_id, req.decision)
    return {'status': 'accepted'}


@router.post('/runs/{rid}/cancel')
async def cancel(rid: str, user=Depends(get_current_user), runtime=Depends(get_runtime)):
    await runtime.runs.cancel(user['user_id'], rid)
    return runtime.runs.own('run', rid, user['user_id'])


@router.get('/sessions/{sid}/artifacts/{aid}')
async def artifact(sid: str, aid: str, user=Depends(get_current_user), runtime=Depends(get_runtime)):
    from urllib.parse import quote
    from app.runtime.files import digest
    s = runtime.runs.own('session', sid, user['user_id'])
    item = next((a for a in s['artifacts'] if a['id'] == aid), None)
    if not item:
        raise HTTPException(404, '产物不存在')
    data = runtime.runs.storage.read_bytes(user['user_id'], item['path'])
    if digest(data) != item['sha256']:
        raise HTTPException(409, '产物内容已被修改')
    return Response(data, media_type=item['mime'], headers={
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(item['name'].split('/')[-1])}",
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
    })


@router.get('/sessions/{sid}/inputs/{aid}')
async def input_attachment(sid: str, aid: str, user=Depends(get_current_user), runtime=Depends(get_runtime)):
    from urllib.parse import quote
    from app.runtime.files import digest, safe_read
    s = runtime.runs.own('session', sid, user['user_id'])
    if s.get('deleted'):
        raise HTTPException(404, '会话已删除')
    items = [a for r in runtime.store.find('run', session_id=sid, actor_id=user['user_id']) for a in r.get('attachments', [])]
    item = next((a for a in items if a['id'] == aid), None)
    if not item:
        raise HTTPException(404, '附件不存在')
    try:
        data = safe_read(runtime.backend.workspace(s), runtime.backend.workspace(s) / item['path'])
    except (ValueError, OSError):
        raise HTTPException(409, '附件不可读取')
    if digest(data) != item['sha256']:
        raise HTTPException(409, '附件内容已被修改')
    return Response(data, media_type=item['mime'], headers={
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(item['name'])}",
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
    })


@management_router.get('/capabilities')
async def host_capabilities(user=Depends(get_host_user)):
    return DeploymentPolicy.from_env().public(user['user_id'])


@management_router.get('/profiles')
async def host_profiles(user=Depends(get_host_user), runtime=Depends(get_runtime)):
    return runtime.profiles.list(user['user_id'])
