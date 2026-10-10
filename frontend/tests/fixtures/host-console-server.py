"""Isolated visual QA server. Serves built UI with synthetic host data only.
Build frontend first, then: python3 frontend/tests/fixtures/host-console-server.py
Preview http://127.0.0.1:3161/superadmin with key preview-only.
No credentials, user directories, or provider network requests are used.
"""
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DIST = Path(__file__).resolve().parents[2] / 'dist'
PROFILES = [
    dict(id='demo-codex', name='演示 · 团队 Codex', runtime='codex', status='ready', can_manage=True, source='owner_shared', recovery_required=False, auth_generation=1, worker={'status':'ready'}, capabilities={'web_search':True,'image_input':True,'file_input':True}, models=[{'id':'demo-reasoning','name':'Demo Reasoning'},{'id':'demo-fast','name':'Demo Fast'}]),
    dict(id='demo-cursor', name='演示 · 研究团队 Cursor', runtime='cursor', status='disconnected', can_manage=True, source='owner_shared', recovery_required=False, auth_generation=1, models=[]),
]
GRANTS = {'demo-codex':[dict(id='grant-demo',actor_id='demo-admin',models=['demo-reasoning'],enabled=True,auth_generation=1,version=1)]}
ADMINS = [dict(id='demo-admin',username='演示维护者'),dict(id='demo-reviewer',username='Demo Reviewer')]

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(DIST),**kwargs)
    def log_message(self,*args): pass
    def json(self,value,status=200):
        body=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        path=urlparse(self.path).path
        if path.startswith('/api/'):
            if self.headers.get('Authorization')!='Bearer preview-only':return self.json({'detail':'Preview only: use key preview-only'},401)
            if path.endswith('/capabilities'):return self.json(dict(enabled=True,available=True,can_manage_connections=True,reason=None,access_mode='trusted_shared',execution_backend='local'))
            if path.endswith('/profiles'):return self.json(PROFILES)
            if path.endswith('/admins'):return self.json(ADMINS)
            if path.endswith('/grants'):return self.json(GRANTS.get(path.split('/')[-2],[]))
            return self.json({'detail':'Not part of the isolated preview'},404)
        if path in ['/superadmin','/superadmin/']:self.path='/index.html'
        return super().do_GET()
    def do_PUT(self):
        if self.headers.get('Authorization')!='Bearer preview-only':return self.json({},401)
        parts=urlparse(self.path).path.split('/')
        if parts[-1]!='grants':return self.json({},404)
        data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
        pid=parts[-2]; rows=GRANTS.setdefault(pid,[])
        row=next((r for r in rows if r['actor_id']==data['actor_id']),None)
        if row is None:row=dict(id='grant-'+data['actor_id'],actor_id=data['actor_id'],version=0);rows.append(row)
        row.update(models=data['models'],enabled=True,auth_generation=1,version=row['version']+1)
        return self.json(row)
    def do_POST(self): return self.json({'detail':'Provider operations are disabled in this preview'},409)
    def do_DELETE(self): return self.json({'detail':'Destructive operations are disabled in this preview'},409)

if __name__=='__main__':
    if not (DIST/'index.html').is_file():raise SystemExit('Run npm run build in frontend first.')
    print('Synthetic host-console preview: http://127.0.0.1:3161/superadmin — key: preview-only',flush=True)
    ThreadingHTTPServer(('127.0.0.1',3161),Handler).serve_forever()
