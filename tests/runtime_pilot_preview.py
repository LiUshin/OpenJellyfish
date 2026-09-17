"""Loopback-only offline UI fixture. Never imports app.main or uses real user credentials.

Run after frontend build:
    .venv/bin/python tests/runtime_pilot_preview.py
This deliberately uses FakeAdapter, not Codex inference. All data is temporary.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from app.runtime.pilot import Pilot
from app.routes.runtime_pilot import router, pilot as pilot_dep
from test_runtime_pilot import FakeAdapter, MemoryStorage


class PreviewAdapter(FakeAdapter):
    async def probe(self):
        return {'authenticated': True, 'models': [{'id': 'offline-fixture', 'name': '离线测试替身（不调用模型）'}]}


def main():
    with tempfile.TemporaryDirectory(prefix='jellyfish-ui-fixture-') as temp:
        root = Path(temp)
        p = Pilot('fixture', root / 'pilot', '/unused', root / 'home', MemoryStorage())
        p.claim()
        p.adapter_factory = PreviewAdapter
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[pilot_dep] = lambda: p

        @app.get('/api/auth/me')
        def me():
            return {'user_id': 'fixture', 'username': 'OFFLINE TEST FIXTURE'}

        @app.post('/api/auth/login')
        def login():
            return {'success': True, 'token': 'offline-fixture', 'user_id': 'fixture', 'username': 'OFFLINE TEST FIXTURE'}

        @app.get('/api/settings/preferences')
        def preferences():
            return {'language': 'zh', 'tz_offset_hours': 8}

        @app.get('/api/{path:path}')
        def unknown_api(path: str):
            raise HTTPException(404, 'Fixture API not implemented')

        dist = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')
        app.mount('/media_resources', StaticFiles(directory=dist / 'media_resources'), name='media')

        @app.get('/{path:path}')
        def frontend(path: str):
            return FileResponse(dist / 'index.html')

        @app.on_event('shutdown')
        async def shutdown():
            await p.shutdown()

        with patch('app.services.prompt.get_user_system_prompt', return_value='Offline fixture'), \
             patch('app.services.prompt.build_user_profile_prompt', return_value=''), \
             patch('app.services.preferences.get_tz_offset', return_value=8):
            uvicorn.run(app, host='127.0.0.1', port=8766)


if __name__ == '__main__':
    main()
