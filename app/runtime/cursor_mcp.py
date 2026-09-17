"""Ephemeral, loopback-only MCP bridge to the same actor-bound business tools."""
import secrets
from aiohttp import web
from fastapi import HTTPException


class CursorMCP:
    def __init__(self, tools, call):
        self.tools, self.call = tools, call
        self.token = secrets.token_urlsafe(32)
        self.runner = None
        self.active = False

    async def start(self):
        app = web.Application(client_max_size=65536)
        app.router.add_post('/mcp', self.handle)
        self.runner = web.AppRunner(app, access_log=None, shutdown_timeout=5)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.active = True
        return {'type': 'http', 'name': 'jellyfish', 'url': f'http://127.0.0.1:{port}/mcp',
                'headers': [{'name': 'Authorization', 'value': 'Bearer ' + self.token}]}

    async def handle(self, request):
        if not self.active or request.headers.get('Origin') or not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + self.token):
            raise web.HTTPForbidden()
        try:
            message = await request.json()
            if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                raise ValueError()
            method, params = message.get('method'), message.get('params', {})
            if not isinstance(params, dict):
                raise ValueError()
            if 'id' not in message:
                return web.Response(status=202)
            if method == 'initialize':
                result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'jellyfish', 'version': '0.1'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': [{k: t[k] for k in ('name', 'description', 'inputSchema')} for t in self.tools]}
            elif method == 'tools/call':
                if params.get('name') not in {t['name'] for t in self.tools}:
                    raise ValueError()
                response = await self.call({'tool': params['name'], 'arguments': params.get('arguments', {})})
                result = {'content': [{'type': 'text', 'text': i['text']} for i in response['contentItems']],
                          'isError': not response['success']}
            else:
                return web.json_response({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Unsupported method'}})
            return web.json_response({'jsonrpc': '2.0', 'id': message['id'], 'result': result}, headers={'Cache-Control': 'no-store'})
        except HTTPException:
            return web.json_response({'jsonrpc': '2.0', 'id': message.get('id'),
                                      'error': {'code': -32001, 'message': 'Runtime authorization no longer valid'}})
        except (ValueError, KeyError, TypeError):
            raise web.HTTPBadRequest()

    async def close(self):
        self.active = False
        if self.runner:
            await self.runner.cleanup()
