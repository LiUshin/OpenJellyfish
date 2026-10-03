"""Transactional run ledger. Task files remain a versioned configuration input.

For an unchanged task revision, the database cursor wins over the file export.
A new file revision is an explicit edit and starts a new scheduling generation.
No external operation is ever performed inside a database transaction.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

TERMINAL = ('success', 'error', 'timeout', 'cancelled', 'interrupted', 'deferred', 'blocked')
ACTIVE = ('queued', 'running', 'cancel_requested')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def task_key(scope, uid, sid, tid):
    return encode([scope, uid, sid, tid])


class Conflict(ValueError):
    pass


class ExecutionStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False, isolation_level=None)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS task_cursors (
                task_key TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                state TEXT NOT NULL, blocked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, task_key TEXT NOT NULL, uid TEXT NOT NULL,
                occurrence TEXT NOT NULL UNIQUE, request_id TEXT,
                trigger TEXT NOT NULL, snapshot TEXT NOT NULL,
                status TEXT NOT NULL, token TEXT, created_at REAL NOT NULL,
                started_at REAL, finished_at REAL, result TEXT, error TEXT,
                UNIQUE(uid, request_id));
            CREATE INDEX IF NOT EXISTS runs_task ON runs(task_key, created_at DESC);
            CREATE INDEX IF NOT EXISTS runs_queue ON runs(status, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_task ON runs(task_key)
                WHERE status IN ('queued','running','cancel_requested');
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id,seq);
            CREATE TABLE IF NOT EXISTS deliveries (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, uid TEXT NOT NULL,
                channel TEXT NOT NULL, target TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, token TEXT, error TEXT,
                UNIQUE(run_id,channel));
            CREATE TABLE IF NOT EXISTS effects (
                run_id TEXT NOT NULL, operation_id TEXT NOT NULL, operation TEXT NOT NULL,
                status TEXT NOT NULL, receipt TEXT, PRIMARY KEY(run_id,operation_id));
            CREATE TABLE IF NOT EXISTS spawn_reservations (
                id TEXT PRIMARY KEY, uid TEXT NOT NULL, root_key TEXT NOT NULL,
                created_at REAL NOT NULL);
        ''')
        os.chmod(path, 0o600)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def _event(self, db, rid, kind, payload=None):
        db.execute('INSERT INTO events(run_id,kind,payload,at) VALUES(?,?,?,?)',
                   (rid, kind, encode(payload or {}), time.time()))

    @staticmethod
    def _run(row):
        if row is None:
            return None
        result = dict(row)
        result['snapshot'] = json.loads(result['snapshot'])
        result['result'] = json.loads(result['result']) if result['result'] else None
        return result

    def get(self, rid, uid=None):
        with self.lock:
            row = self.db.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
            return self._run(row) if row and (uid is None or row['uid'] == uid) else None

    def list_runs(self, key, limit=50):
        with self.lock:
            return [self._run(r) for r in self.db.execute(
                'SELECT * FROM runs WHERE task_key=? ORDER BY created_at DESC LIMIT ?', (key, min(limit, 100)))]

    def queued(self, limit=128):
        with self.lock:
            return [self._run(r) for r in self.db.execute(
                "SELECT * FROM runs WHERE status='queued' ORDER BY created_at LIMIT ?", (limit,))]

    def overlay(self, key, task):
        """Recover the committed scheduling cursor even if file export crashed."""
        task = {k: v for k, v in task.items() if k != 'recovery_required'}
        with self.lock:
            row = self.db.execute('SELECT * FROM task_cursors WHERE task_key=?', (key,)).fetchone()
        if row and row['revision'] == task.get('revision', 0):
            task = {**task, **json.loads(row['state'])}
        if row and row['blocked']:
            task = {**task, 'recovery_required': True, 'next_run_at': None}
        return task

    @staticmethod
    def _configuration(snapshot):
        keys = ('id', 'revision', 'task_type', 'task_config', 'reply_to', 'schedule_type',
                'schedule', 'tz_offset_hours')
        return {k: snapshot.get(k) for k in keys}

    def submit(self, key, uid, snapshot, *, manual=False, request_id=None,
               max_pending=128, per_owner=32, total_limit=10000):
        revision = snapshot.get('revision', 0)
        request_id = request_id if manual else None
        if manual and not request_id:
            request_id = uuid.uuid4().hex
        occurrence = ('manual:' + uid + ':' + request_id if manual else
                      encode([key, revision, snapshot.get('next_run_at')]))
        if len(request_id or '') > 128:
            raise ValueError('request_id exceeds 128 characters')
        # Runtime config is immutable once accepted; no credentials in snapshot.
        snapshot = {k: v for k, v in snapshot.items() if k != 'runs'}
        with self.transaction() as db:
            prior = db.execute('SELECT * FROM runs WHERE occurrence=? OR (uid=? AND request_id=?)',
                               (occurrence, uid, request_id)).fetchone()
            if prior:
                if prior['task_key'] != key or self._configuration(json.loads(prior['snapshot'])) != self._configuration(snapshot):
                    raise Conflict('request_id or occurrence already identifies a different configuration')
                return self._run(prior)
            cursor = db.execute('SELECT * FROM task_cursors WHERE task_key=?', (key,)).fetchone()
            if cursor and cursor['blocked']:
                raise Conflict('Previous execution needs recovery review')
            if db.execute("SELECT 1 FROM runs WHERE task_key=? AND status IN ('queued','running','cancel_requested')", (key,)).fetchone():
                raise Conflict('Task already has an unfinished run')
            pending = db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running','cancel_requested')").fetchone()[0]
            owned = db.execute("SELECT COUNT(*) FROM runs WHERE uid=? AND status IN ('queued','running','cancel_requested')", (uid,)).fetchone()[0]
            total = db.execute('SELECT COUNT(*) FROM runs WHERE task_key=?', (key,)).fetchone()[0]
            if pending >= max_pending or owned >= per_owner or total >= total_limit:
                raise Conflict('Execution budget or queue capacity exhausted')
            rid = 'run_' + uuid.uuid4().hex
            db.execute('INSERT INTO runs(id,task_key,uid,occurrence,request_id,trigger,snapshot,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (rid, key, uid, occurrence, request_id, 'manual' if manual else 'scheduled', encode(snapshot), 'queued', time.time()))
            self._event(db, rid, 'queued')
            return self._run(db.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone())

    def claim(self, rid):
        with self.transaction() as db:
            token = uuid.uuid4().hex
            changed = db.execute("UPDATE runs SET status='running',token=?,started_at=? WHERE id=? AND status='queued'",
                                 (token, time.time(), rid)).rowcount
            if not changed:
                return None
            self._event(db, rid, 'running')
            return self._run(db.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone())

    def check_token(self, rid, token):
        with self.lock:
            row = self.db.execute('SELECT status,token FROM runs WHERE id=?', (rid,)).fetchone()
        if not row or row['token'] != token or row['status'] != 'running':
            raise PermissionError('Execution lease revoked or cancellation requested')

    def request_cancel(self, rid, uid):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM runs WHERE id=? AND uid=?', (rid, uid)).fetchone()
            if not row:
                return None
            if row['status'] in ACTIVE:
                status = 'cancelled' if row['status'] == 'queued' else 'cancel_requested'
                db.execute('UPDATE runs SET status=?,finished_at=? WHERE id=?',
                           (status, time.time() if status == 'cancelled' else None, rid))
                self._event(db, rid, status)
            return self._run(db.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone())

    def finish(self, rid, token, result, cursor=None, deliveries=()):
        """Result, cursor and outbound intents share one commit boundary."""
        with self.transaction() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
            if not row or row['token'] != token or row['status'] not in ('running', 'cancel_requested'):
                raise Conflict('Stale execution cannot finalize this run')
            result = dict(result)
            if result.get('status') not in TERMINAL:
                raise ValueError('Invalid terminal run status')
            if row['status'] == 'cancel_requested':
                result['status'] = 'cancelled'
                deliveries = ()
            uncertain = db.execute("SELECT 1 FROM effects WHERE run_id=? AND status IN ('started','unknown')", (rid,)).fetchone()
            if uncertain:
                result['status'] = 'interrupted'
                result['output'] = 'An operation has no receipt; review external effects before retrying'
                deliveries = ()
                db.execute("UPDATE effects SET status='unknown' WHERE run_id=? AND status='started'", (rid,))
                snap = json.loads(row['snapshot'])
                db.execute('INSERT INTO task_cursors(task_key,revision,state,blocked) VALUES(?,?,?,1) '
                           'ON CONFLICT(task_key) DO UPDATE SET blocked=1',
                           (row['task_key'], snap.get('revision', 0), '{}'))
            db.execute('UPDATE runs SET status=?,result=?,finished_at=? WHERE id=?',
                       (result['status'], encode(result), time.time(), rid))
            if cursor is not None:
                revision, state = cursor
                db.execute('INSERT INTO task_cursors(task_key,revision,state) VALUES(?,?,?) '
                           'ON CONFLICT(task_key) DO UPDATE SET revision=excluded.revision,state=excluded.state',
                           (row['task_key'], revision, encode(state)))
            for delivery in deliveries:
                did = 'delivery_' + hashlib.sha256(f"{rid}:{delivery['channel']}".encode()).hexdigest()[:24]
                db.execute('INSERT INTO deliveries(id,run_id,uid,channel,target,payload,status) VALUES(?,?,?,?,?,?,?)',
                           (did, rid, row['uid'], delivery['channel'], encode(delivery['target']), encode(delivery['payload']), 'pending'))
            self._event(db, rid, result['status'])

    def _interrupt(self, db, row, error):
        db.execute("UPDATE runs SET status='interrupted',finished_at=?,error=?,token=NULL WHERE id=?", (time.time(), error, row['id']))
        snap = json.loads(row['snapshot'])
        db.execute('INSERT INTO task_cursors(task_key,revision,state,blocked) VALUES(?,?,?,1) '
                   'ON CONFLICT(task_key) DO UPDATE SET blocked=1',
                   (row['task_key'], snap.get('revision', 0), encode({'next_run_at': None})))
        db.execute("UPDATE effects SET status='unknown' WHERE run_id=? AND status='started'", (row['id'],))
        self._event(db, row['id'], 'interrupted', {'error': error})

    def interrupt(self, rid, token, error):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=? AND token=? AND status IN ('running','cancel_requested')", (rid, token)).fetchone()
            if row:
                self._interrupt(db, row, error)

    def recover(self):
        """Called only after acquiring the host owner lock. Never replay effects."""
        with self.transaction() as db:
            interrupted = db.execute("SELECT * FROM runs WHERE status IN ('running','cancel_requested')").fetchall()
            for row in interrupted:
                self._interrupt(db, row, 'Host restarted during execution; verify external effects before resuming')
            db.execute("UPDATE deliveries SET status=CASE WHEN channel IN ('web','memory') THEN 'retry_wait' ELSE 'unknown' END,token=NULL,error='Delivery worker restarted' WHERE status='delivering'")
            return len(interrupted)

    def resolve_recovery(self, key, uid, note, rid=None):
        if not note.strip():
            raise ValueError('A recovery review note is required')
        with self.transaction() as db:
            row = db.execute('SELECT * FROM runs WHERE task_key=? AND uid=? ORDER BY created_at DESC LIMIT 1', (key, uid)).fetchone()
            if not row:
                raise ValueError('Task run not found')
            if rid and row['id'] != rid:
                raise Conflict('Only the most recent interrupted run can be reviewed')
            db.execute('UPDATE task_cursors SET blocked=0 WHERE task_key=?', (key,))
            self._event(db, row['id'], 'recovery_reviewed', {'note': note[:1000]})

    def spawn_budget(self, uid, root_key, limit, *, reserve=False):
        with self.transaction() as db:
            now = time.time()
            # Old reservations are irrelevant to the rolling budget.
            db.execute('DELETE FROM spawn_reservations WHERE root_key=? AND created_at<=?', (root_key, now-3600))
            count, oldest = db.execute('SELECT COUNT(*),MIN(created_at) FROM spawn_reservations WHERE root_key=?', (root_key,)).fetchone()
            allowed = count < limit
            reservation = None
            if reserve and allowed:
                reservation = uuid.uuid4().hex
                db.execute('INSERT INTO spawn_reservations VALUES(?,?,?,?)', (reservation, uid, root_key, now))
                count += 1
                oldest = oldest or now
            return {'allowed': allowed, 'current': count, 'reset_at': oldest+3600 if oldest else now,
                    'reservation': reservation}

    def spawn_chains(self, uid):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT DISTINCT root_key FROM spawn_reservations WHERE uid=? AND created_at>?', (uid, time.time()-3600))]

    def reserve_spawn(self, uid, root_key, limit):
        result = self.spawn_budget(uid, root_key, limit, reserve=True)
        if not result['allowed']:
            raise Conflict('Persistent automation spawn budget exhausted')
        return result['reservation']

    def effect_start(self, rid, token, operation_id, operation):
        with self.transaction() as db:
            self.check_token(rid, token)
            row = db.execute('SELECT * FROM effects WHERE run_id=? AND operation_id=?', (rid, operation_id)).fetchone()
            if row:
                raise Conflict('Operation already started; automatic replay is prohibited')
            db.execute('INSERT INTO effects VALUES(?,?,?,?,NULL)', (rid, operation_id, encode(operation), 'started'))

    def effect_done(self, rid, token, operation_id, receipt):
        with self.transaction() as db:
            self.check_token(rid, token)
            changed = db.execute("UPDATE effects SET status='completed',receipt=? WHERE run_id=? AND operation_id=? AND status='started'", (encode(receipt), rid, operation_id)).rowcount
            if not changed:
                raise Conflict('Operation is not awaiting a receipt')

    def effects(self, rid, uid):
        if not self.get(rid, uid):
            return []
        with self.lock:
            return [dict(r) | {'operation': json.loads(r['operation']),
                              'receipt': json.loads(r['receipt']) if r['receipt'] else None}
                    for r in self.db.execute('SELECT * FROM effects WHERE run_id=?', (rid,))]

    def deliveries(self, rid, uid):
        with self.lock:
            rows = self.db.execute('SELECT * FROM deliveries WHERE run_id=? AND uid=? ORDER BY id', (rid, uid)).fetchall()
        return [dict(r) | {'target': json.loads(r['target']), 'payload': json.loads(r['payload'])} for r in rows]

    def claim_delivery(self):
        with self.transaction() as db:
            row = db.execute("SELECT d.rowid AS sequence,d.* FROM deliveries d WHERE d.status IN ('pending','retry_wait') AND d.next_attempt<=? AND (d.channel!='memory' OR NOT EXISTS (SELECT 1 FROM deliveries p WHERE p.channel='memory' AND json_extract(p.target,'$.admin_id')=json_extract(d.target,'$.admin_id') AND json_extract(p.target,'$.service_id') IS json_extract(d.target,'$.service_id') AND json_extract(p.target,'$.conversation_id')=json_extract(d.target,'$.conversation_id') AND p.rowid<d.rowid AND p.status NOT IN ('delivered','cancelled'))) ORDER BY d.rowid LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE deliveries SET status='delivering',token=?,attempt=attempt+1 WHERE id=?", (token, row['id']))
            return dict(row) | {'token': token, 'attempt': row['attempt']+1, 'target': json.loads(row['target']), 'payload': json.loads(row['payload'])}

    def delivery_done(self, delivery, status, error=None):
        with self.transaction() as db:
            if status == 'retry_wait' and delivery['attempt'] >= 5:
                status = 'failed'
            changed = db.execute('UPDATE deliveries SET status=?,error=?,next_attempt=? WHERE id=? AND token=? AND status=\'delivering\'',
                       (status, error, time.time()+min(3600, 5*2**min(delivery['attempt'], 10)), delivery['id'], delivery['token'])).rowcount
            if not changed:
                raise Conflict('Stale delivery cannot finalize')
            self._event(db, delivery['run_id'], 'delivery_'+status, {'delivery_id': delivery['id'], 'error': error})

    def defer_delivery(self, delivery):
        """An active conversation is backpressure, not a failed projection attempt."""
        with self.transaction() as db:
            db.execute("UPDATE deliveries SET status='retry_wait',attempt=MAX(0,attempt-1),next_attempt=? WHERE id=? AND token=? AND status='delivering'",
                       (time.time()+5, delivery['id'], delivery['token']))

    def retry_delivery(self, did, uid):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM deliveries WHERE id=? AND uid=?', (did, uid)).fetchone()
            if not row:
                raise ValueError('Delivery not found')
            if row['status'] not in ('failed', 'retry_wait'):
                raise Conflict('Only definitely unsent deliveries can be retried; unknown requires investigation')
            db.execute("UPDATE deliveries SET status='pending',attempt=0,next_attempt=0 WHERE id=?", (did,))

    def events(self, rid, uid, after=0):
        if not self.get(rid, uid):
            return []
        with self.lock:
            return [dict(r) | {'payload': json.loads(r['payload'])} for r in self.db.execute(
                'SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 500', (rid, after))]

    def close(self):
        with self.lock:
            self.db.close()
