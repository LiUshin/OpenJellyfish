"""Isolated real-API UI fixture. No models, credentials, or external transports.

Run from the repository root: python -m uvicorn tests.service_messaging_preview:app --port 3120
All state is temporary. This intentionally bypasses admin login only in this fixture.
"""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core import security
from app.storage import local
from app.deps import get_current_user
from app.services import published, service_messaging as messaging
from app.channels.wechat.session_manager import WeChatSession, WeChatSessionManager
from app.routes.consumer import router as consumer_router
from app.routes.services import router as services_router
from app.routes.inbox import router as inbox_router

_temp = tempfile.TemporaryDirectory(prefix='jellyfish-message-preview-')
security.USERS_DIR = _temp.name
local.USERS_DIR = _temp.name
owner = {'user_id': 'preview', 'username': 'Preview', 'disabled': False}
security._load_users = lambda: {'preview': owner}
manager = WeChatSessionManager()
patch('app.channels.wechat.session_manager.get_session_manager', return_value=manager).start()
svc = published.create_service('preview', {'name': 'Service 消息验收', 'published': True, 'capabilities': ['humanchat'], 'allowed_docs': [], 'wechat_channel': {'enabled': True}})
key = published.create_service_key('preview', svc['id'])
conversations = [published.create_consumer_conversation('preview', svc['id'], title=label, source=source) for source, label in [('web', '网页售后会话'), ('api', 'API 接入会话'), ('wechat', '微信售后会话')]]
for conv in conversations:
    published.save_consumer_message('preview', svc['id'], conv['id'], 'user', '请管理员确认我的售后问题。')
session = WeChatSession(session_id='preview_wechat', service_id=svc['id'], admin_id='preview', conversation_id=conversations[2]['id'], bot_token='fixture-only', ilink_user_id='fixture-user', ilink_bot_id='fixture-bot', base_url='https://never.invalid', from_user_id='fixture-recipient', context_token='fixture-context')
manager._sessions[session.session_id] = session
client = SimpleNamespace(send_text=AsyncMock(return_value={'ret': 0}))
manager._clients[session.session_id] = client
case = messaging.post_contact('preview', svc['id'], conversations[0]['id'], '请管理员确认售后处理时间。', channel='web')
app = FastAPI()
app.dependency_overrides[get_current_user] = lambda: owner
app.include_router(consumer_router)
app.include_router(services_router)
app.include_router(inbox_router)

@app.middleware('http')
async def no_models(request: Request, call_next):
    if request.url.path in ('/api/chat', '/api/v1/chat', '/api/v1/chat/completions'):
        return JSONResponse({'detail': 'Fixture disables model execution'}, status_code=409)
    return await call_next(request)

@app.on_event('startup')
async def start():
    messaging.start_worker()

@app.on_event('shutdown')
async def stop():
    await messaging.stop_worker()
    _temp.cleanup()

@app.get('/__fixture')
async def fixture():
    return {'service': svc, 'key': key['key'], 'conversations': conversations, 'case': case, 'wechat_sends': client.send_text.await_count}

@app.get('/api/auth/me')
async def me(): return owner

@app.get('/api/preferences')
async def preferences(): return {'tz_offset_hours': 8}

@app.get('/api/models')
async def models(): return {'models': [], 'default': ''}

@app.get('/api/runtime/capabilities')
async def runtime(): return {'enabled': False, 'available': False}

@app.get('/api/runtime/preferences')
async def runtime_preferences(): return {'runtime': 'deepagents'}

@app.get('/api/wc/{service_id}/sessions')
async def sessions(): return []

@app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT'])
async def unrelated(path: str):
    if path.endswith('preferences'): return {}
    if 'streaming' in path: return {'streaming': [], 'interrupted': []}
    if 'interrupt' in path: return {'has_interrupt': False}
    if path.endswith('api-keys'): return {}
    return []

dist = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

@app.get('/service-chat.html')
async def consumer_page():
    html = (dist / 'service-chat.html').read_text()
    config = {'service_id': svc['id'], 'service_name': svc['name']}
    return HTMLResponse(html.replace('<!-- SVC_INJECT -->', '<script>window.__SVC__=' + json.dumps(config) + '</script>'))

@app.get('/{path:path}')
async def admin_page(path: str):
    file = dist / path
    if path and file.is_file() and file.resolve().is_relative_to(dist): return FileResponse(file)
    return FileResponse(dist / 'index.html')
