"""Single-scheduler SQLite state. Transactions include event and run transitions."""
import json
import os
import sqlite3
import time
from pathlib import Path

TERMINAL = {'completed', 'failed', 'cancelled'}


class RuntimeStore:
    def __init__(self, root: Path):
        # Keep disabled Runtime imports compatible with non-POSIX installations.
        import fcntl
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        self.root = root
        self.guard = (root / 'scheduler.lock').open('a+')
        try:
            fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.guard.close()
            raise RuntimeError('Runtime requires exactly one scheduler process per data directory')
        self.db = sqlite3.connect(root / 'runtime.sqlite3')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS records (
                kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(kind, id));
            CREATE UNIQUE INDEX IF NOT EXISTS request_once ON records (
                json_extract(data,'$.actor_id'), json_extract(data,'$.request_id'))
                WHERE kind='run';
            CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(run_id, seq));
        ''')
        os.chmod(root / 'runtime.sqlite3', 0o600)
        # A server crash does not prove its child processes stopped. Fence profiles
        # until the host operator verifies them; never automatically replay effects.
        for profile in self.all('profile'):
            if profile.get('active_lease'):
                profile['recovery_required'] = True
                self.put('profile', profile)
        for run in self.all('run'):
            if run['status'] not in TERMINAL:
                profile = self.get('profile', run['binding']['profile_id'])
                if profile and run['status'] != 'queued':
                    profile['recovery_required'] = True
                    self.put('profile', profile)
                run['error'] = '服务重启中断；需在宿主确认旧执行已退出'
                self.emit(run, 'failed', {'message': run['error']}, status='failed')
        for login in self.all('login'):
            if login['status'] == 'pending':
                login.update(status='expired', challenge=None, cleanup_required=True)
                profile = self.get('profile', login['profile_id'])
                if profile:
                    profile['recovery_required'] = True
                    self.put('profile', profile)
                self.put('login', login)

    def _put(self, kind, row):
        self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',
                        (kind, row['id'], json.dumps(row, ensure_ascii=False)))

    def put(self, kind, row):
        with self.db:
            self._put(kind, row)
        return row

    def get(self, kind, record_id):
        row = self.db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, record_id)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM records WHERE kind=?', (kind,))]

    def find(self, kind, **fields):
        return [row for row in self.all(kind) if all(row.get(k) == v for k, v in fields.items())]

    def emit(self, run, event_type, payload=None, *, status=None):
        run['seq'] = run.get('seq', 0) + 1
        run['updated_at'] = time.time()
        if status:
            run['status'] = status
        if status in TERMINAL:
            run['pending'] = None
            run['finished_at'] = time.time()
        from app.runtime.blocks import append_event
        append_event(run.setdefault('blocks', [{'type': 'text', 'content': run['output']}] if run.get('output') else []), event_type, payload or {})
        event = {'seq': run['seq'], 'type': event_type, 'payload': payload or {}, 'at': time.time()}
        with self.db:
            self._put('run', run)
            self.db.execute('INSERT INTO events VALUES (?,?,?)',
                            (run['id'], event['seq'], json.dumps(event, ensure_ascii=False)))
        return event

    def events(self, run_id, after=0, limit=512):
        return [json.loads(row[0]) for row in self.db.execute(
            'SELECT data FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?', (run_id, after, limit))]

    def close(self):
        self.db.close()
        self.guard.close()
