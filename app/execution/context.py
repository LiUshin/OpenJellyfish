"""Scheduler adapter for the executor-independent ledger."""
import os
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from app.execution.store import ExecutionStore, task_key

_current = ContextVar('durable_execution', default=None)
_stores = {}
_lock = threading.RLock()

def get_store():
    from app.core.security import USERS_DIR
    path = os.path.realpath(os.path.join(USERS_DIR, '.scheduler', 'executions.sqlite3'))
    with _lock:
        if path not in _stores:
            _stores[path] = ExecutionStore(path)
        return _stores[path]

def load_task(scope, uid, tid, sid=None):
    from app.services import scheduler_tree as st
    task = st.load_task_or_migrate(scope, uid, tid, sid)
    return get_store().overlay(task_key(scope, uid, sid, tid), task) if task else None

def overlay(scope, uid, sid, task):
    return get_store().overlay(task_key(scope, uid, sid, task['id']), task)

@dataclass
class ExecutionContext:
    store: ExecutionStore
    run: dict
    intents: list = field(default_factory=list)

    def check(self):
        self.store.check_token(self.run['id'], self.run['token'])

    def collect_message(self, payload):
        self.check()
        # Mutable media paths cannot safely be delivered after a restart.
        if payload.get('media'):
            raise PermissionError('Durable media delivery requires an immutable artifact; use text')
        if payload.get('text'):
            self.intents.append(str(payload['text']))

    def deliveries(self, record, task_meta):
        reply = self.run['snapshot'].get('reply_to')
        if not reply or not reply.get('conversation_id') or record['status'] in ('cancelled', 'deferred'):
            return []
        _, uid, sid, _ = __import__('json').loads(self.run['task_key'])
        # Service execution diagnostics belong to the owner Run. Only an
        # explicit, successfully committed send_message may enter a consumer
        # conversation or transport. An agent may intentionally choose silence.
        if sid and (record['status'] != 'success' or not self.intents):
            return []
        target = {**reply, 'admin_id': uid, 'service_id': sid}
        payload = {'text': '\n\n'.join(self.intents) if sid else record['output'],
                   'success': record['status'] == 'success',
                   'task_meta': {**task_meta, 'run_id': self.run['id']}}
        if sid:
            payload['explicit_message'] = True
        result = [{'channel': ch, 'target': target, 'payload': payload} for ch in ('web', 'memory')]
        if reply.get('channel') == 'wechat':
            result.append({'channel': 'wechat', 'target': target, 'payload': payload})
        return result

def current():
    return _current.get()

def merge_runs(scope, uid, sid, tid, legacy):
    rows = get_store().list_runs(task_key(scope, uid, sid, tid))
    durable = []
    from datetime import datetime, timezone
    for row in rows:
        record = row['result'] or {'run_id': row['id'], 'status': row['status'],
            'started_at': datetime.fromtimestamp(row['started_at'] or row['created_at'], timezone.utc).isoformat(),
            'output': row['error'] or '', 'steps': [], 'trigger': row['trigger']}
        record = {**record, 'deliveries': [{k: d[k] for k in ('id','channel','status','attempt','error')}
                    for d in get_store().deliveries(row['id'], uid)]}
        durable.append(record)
    ids = {r['run_id'] for r in durable}
    return sorted([r for r in legacy if r['run_id'] not in ids] + durable,
                  key=lambda r: r.get('started_at') or '')
