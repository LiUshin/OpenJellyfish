"""Loopback-only UI review fixture; no production imports, credentials or inference.
Build frontend, then python3 tests/uiux/preview.py. Login with any nonempty test
username/password. All writes are held in memory and reset on restart.
Supported fixture controls: /__fixture/reset, /__fixture/fail-next-conversation.
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import json
from email.parser import BytesParser
from email.policy import default
import time
import uuid

DIST = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
DOCUMENT = '# 客户服务指南\n\n## 回答之前\n先确认用户的问题与适用范围，引用文档中的依据。\n\n## 交付标准\n- 清楚说明结论\n- 标出不确定的部分\n- 给出可执行的下一步\n'
FILES = {'/客户服务指南.md': DOCUMENT, '/交付检查清单.md': '# 交付检查清单\n\n- [x] 文档已准备\n- [ ] 验证回答\n- [ ] 检查服务访问范围\n'}
CONVS = [{'id': 'fixture-guide', 'title': '把服务经验整理成可复用的文档', 'created_at': '2026-09-19T09:00:00', 'updated_at': '2026-09-19T09:05:00', 'message_count': 2}, {'id': 'fixture-long', 'title': '发布前检查：知识文档、回答依据与交付边界的完整核对', 'created_at': '2026-09-18T09:00:00', 'updated_at': '2026-09-18T09:05:00', 'message_count': 2}]
MESSAGES = {c['id']: [{'role': 'user', 'content': '请帮我整理客户服务文档，并说明下一步如何验证。'}, {'role': 'assistant', 'content': '## 文档已经准备好\n\n我将服务经验分成了**回答规则、知识依据和交付标准**。\n\n你可以打开 [客户服务指南.md](/客户服务指南.md) 继续编辑，然后用一个真实问题检查回答是否有依据。\n\n### 建议的下一步\n1. 补充一个常见问题。\n2. 对照原文核对答案。\n3. 确认访问范围后再交付。\n\n> 此处是离线 UI 测试数据，没有调用模型。'}] for c in CONVS}
STATE = {'fail_next': False, 'language': 'zh', 'no_model': False, 'fail_path': '', 'fail_write': False, 'tz_offset_hours': 8, 'rules': '回答使用中文，先给结论，再说明依据。\n涉及客户资料时，先确认使用范围。', 'agent_notes': '偏好简洁的结构化交付。', 'agent_locked': False}

VOICE = {'enabled': True, 'greeting': '你好，我是 Jellyfish。', 'system_prompt': '保持简洁、自然，先确认需求。', 'routing_policy': '文档与复杂任务交给后台处理。', 'fillers': {'delegating': ['我来查一下资料。'], 'tool_running': ['正在处理。'], 'long_task': ['还在处理，请稍等。']}, 'interruption': {'allow_interruptions': True, 'min_interruption_words': 2}, 'providers': {'stt': 'openai', 'stt_model': 'gpt-4o-mini-transcribe', 'llm_model': 'fixture', 'tts': 'openai', 'tts_model': 'gpt-4o-mini-tts', 'tts_voice': 'alloy'}}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def log_message(self, fmt, *args):
        if args and ('404' in str(args) or '500' in str(args)):
            super().log_message(fmt, *args)

    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or '{}')

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p == '/__fixture/fail-next-conversation':
            STATE['fail_next'] = True
            return self.send_json({'fixture': True})
        if p == '/__fixture/no-model':
            STATE['no_model'] = True
            return self.send_json({'fixture': True})
        if p == '/__fixture/fail-settings':
            STATE['fail_path'] = q.get('path', [''])[0]
            return self.send_json({'fixture': True})
        if p == '/__fixture/fail-save':
            STATE['fail_write'] = True
            return self.send_json({'fixture': True})
        if p == '/__fixture/reset':
            STATE.update(fail_next=False, no_model=False, fail_path='', fail_write=False)
            return self.send_json({'fixture': True})
        if STATE['fail_path'] and p == STATE['fail_path']:
            STATE['fail_path'] = ''
            return self.send_json({'detail': 'Offline fixture: temporary settings failure'}, 503)
        routes = {
            '/api/runtime/capabilities': {'enabled': False, 'available': False, 'can_manage_connections': False, 'reason': 'Offline fixture', 'access_mode': 'disabled', 'execution_backend': 'local'},
            '/api/runtime/profiles': [], '/api/runtime/preferences': {'runtime': 'deepagents'},
            '/api/auth/me': {'user_id': 'ui-review', 'username': 'UI Review'},
            '/api/preferences': {'language': STATE['language'], 'tz_offset_hours': STATE['tz_offset_hours']},
            '/api/settings/api-keys/status': {'has_llm': not STATE['no_model'], 'has_openai': False, 'has_anthropic': False},
            '/api/v1/models': {'billing': 'hosted', 'default_model': 'fixture', 'models': [{'id': 'fixture', 'name': 'Offline preview', 'provider': 'fixture'}], 'allowed_hosts': {}},
            '/api/models': {'models': [] if STATE['no_model'] else [{'id': 'fixture', 'name': '离线预览 · 无模型调用', 'provider': 'openai'}], 'default': '' if STATE['no_model'] else 'fixture'},
            '/api/chat/streaming-status': {'streaming': [], 'interrupted': []},
            '/api/conversations': CONVS,
            '/api/workspace/locks': {'processes': []},
            '/api/inbox/unread-count': {'count': 0},
            '/api/user-profile': {'profile': {'name': 'UI Review', 'custom_notes': STATE['rules']}},
            '/api/user-profile/agent-notes': {'content': STATE['agent_notes'], 'locked': STATE['agent_locked']},
            '/api/system-prompt': {'prompt': DOCUMENT, 'is_default': True},
            '/api/user-profile/versions': [], '/api/system-prompt/versions': [],
            '/api/packages': {'packages': [{'name': 'requests', 'version': '2.32.0'}, {'name': 'pandas', 'version': '2.2.0'}], 'venv_ready': True},
            '/api/inbox': {'messages': [], 'unread_count': 0},
            '/api/backup/modules': {'modules': [{'id': 'filesystem', 'label': '工作区文件'}, {'id': 'conversations', 'label': '对话'}, {'id': 'settings', 'label': '设置'}], 'default_selected': ['filesystem', 'conversations', 'settings']},
            '/api/voice/live/status': {'configured': False}, '/api/voice/live/config': VOICE,
            '/api/usage/summary': {'total': {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}, 'months_scanned': 3, 'by_model': [], 'by_service': [], 'by_key': [], 'by_provider': [], 'by_channel': [], 'by_day': []},
            '/api/admin/wechat/session': {'connected': False},
            '/api/scheduler': [], '/api/scheduler/services/all': [],
            '/api/settings/aggregators/openrouter/enabled-models': {'models': []},
            '/api/settings/aggregators/siliconflow/enabled-models': {'models': []},
            '/api/settings/aggregators/openrouter/remote-models': {'data': []},
            '/api/settings/aggregators/siliconflow/remote-models': {'data': []},
            '/api/services': [], '/api/subagents': {'subagents': [{'id': 'review-assistant', 'name': '交付检查助手', 'description': '检查文档是否包含结论、依据与下一步。此条为离线预览数据。', 'system_prompt': '检查交付完整性。', 'tools': ['read_file'], 'enabled': True}, {'id': 'research-assistant', 'name': '资料整理助手', 'description': '把已有资料整理成可复用的结构。', 'system_prompt': '整理资料。', 'tools': [], 'enabled': False}], 'available_tools': ['read_file']},
            '/api/settings/api-keys': {}, '/api/models/visibility': {'models': [], 'hidden': []},
        }
        if p.startswith(('/api/conversations/', '/api/v1/conversations/')):
            if STATE['fail_next']:
                STATE['fail_next'] = False
                return self.send_json({'detail': '离线测试：暂时无法加载对话，请重试。'}, 503)
            cid = p.rsplit('/', 1)[-1]
            conv = next((c for c in CONVS if c['id'] == cid), None)
            return self.send_json({**conv, 'messages': MESSAGES.get(cid, [])} if conv else {'detail': 'Not found'}, 200 if conv else 404)
        if p == '/api/files':
            return self.send_json([{'name': path[1:], 'path': path, 'is_dir': False, 'size': len(value.encode())} for path, value in FILES.items()] if q.get('path', ['/'])[0] == '/' else [])
        if p == '/api/files/read':
            return self.send_json({'content': FILES.get(q.get('path', [''])[0], '')})
        if p == '/api/files/index':
            return self.send_json({'entries': [{'path': path, 'name': path[1:], 'is_dir': False} for path in FILES]})
        if p in routes:
            return self.send_json(routes[p])
        if p.startswith('/api/'):
            return self.send_json({'detail': 'Not implemented by offline UI fixture: ' + p}, 404)
        if not Path(self.translate_path(p)).is_file():
            self.path = '/index.html'
        return super().do_GET()

    def do_POST(self):
        p = urlparse(self.path).path
        if p == '/api/backup/preview':
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            part = BytesParser(policy=default).parsebytes(('Content-Type: ' + self.headers.get('Content-Type', '') + '\r\n\r\n').encode() + raw)
            fields = {item.get_param('name', header='content-disposition'): item.get_content() for item in part.iter_parts()}
            selected = str(fields.get('modules', '')).split(',')
            return self.send_json({'modules': {name: {'file_count': 2, 'total_bytes': 1024} for name in selected if name}, 'total_file_count': 2 * len(selected), 'total_uncompressed_bytes': 1024 * len(selected)})
        data = self.body()
        if p in ('/api/auth/login' , '/api/auth/register'):
            return self.send_json({'token': 'offline-ui-fixture', 'user_id': 'ui-review', 'username': data.get('username', 'UI Review')})
        if p in ('/api/conversations', '/api/v1/conversations'):
            conv = {'id': 'fixture-' + uuid.uuid4().hex[:8], 'title': data.get('title') or '新对话', 'created_at': '2026-09-19T10:00:00', 'updated_at': '2026-09-19T10:00:00'}
            CONVS.insert(0, conv)
            MESSAGES[conv['id']] = []
            return self.send_json(conv)
        if p == '/api/files/write':
            FILES[data['path']] = data['content']
            return self.send_json({'success': True})
        if p in ('/api/chat', '/api/v1/chat'):
            cid = data['conversation_id']
            MESSAGES.setdefault(cid, []).append({'role': 'user', 'content': str(data['message'])})
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            answer = '这是离线预览回复。\n\n打开 **客户服务指南.md**，可以核对和编辑文档。此流程用于验证界面与 API 协议，不代表真实模型结果。'
            try:
                for token in [answer[i:i+5] for i in range(0, len(answer), 5)]:
                    self.wfile.write(('data: ' + json.dumps({'type': 'token', 'content': token}, ensure_ascii=False) + '\n\n').encode())
                    self.wfile.flush()
                    time.sleep(0.18)
                MESSAGES[cid].append({'role': 'assistant', 'content': answer})
                self.wfile.write(b'data: {"type":"done"}\n\n')
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if p == '/api/chat/stop':
            return self.send_json({'success': True})
        return self.send_json({'detail': 'Unsupported fixture write'}, 404)

    def do_PUT(self):
        p = urlparse(self.path).path
        data = self.body()
        if STATE['fail_write']:
            STATE['fail_write'] = False
            return self.send_json({'detail': 'Offline fixture: save failed'}, 503)
        if p == '/api/user-profile':
            STATE['rules'] = data.get('custom_notes', '')
            return self.send_json({'success': True})
        if p == '/api/user-profile/agent-notes':
            STATE['agent_notes'] = data.get('content', '')
            STATE['agent_locked'] = data.get('locked', False)
            return self.send_json({'success': True})
        if p == '/api/voice/live/config':
            VOICE.update(data)
            return self.send_json(VOICE)
        if p == '/api/preferences':
            STATE.update({k: v for k, v in data.items() if k in ('language', 'tz_offset_hours')})
            return self.send_json(data)
        return self.send_json({'detail': 'Unsupported fixture write'}, 404)

    def do_DELETE(self):
        p = urlparse(self.path).path
        if p.startswith(('/api/conversations/', '/api/v1/conversations/')):
            cid = p.rsplit('/', 1)[-1]
            CONVS[:] = [c for c in CONVS if c['id'] != cid]
            MESSAGES.pop(cid, None)
            return self.send_json({'success': True})
        return self.send_json({'detail': 'Unsupported fixture write'}, 404)

if __name__ == '__main__':
    print('OFFLINE UI FIXTURE — http://127.0.0.1:8767 — no real credentials or models', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8767), Handler).serve_forever()
