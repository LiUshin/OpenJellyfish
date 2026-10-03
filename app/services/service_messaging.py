"""Durable Service feedback and messages, independent of Agent/Run execution.

The scheduler ledger and these prefixed tables share a SQLite database, not a
Run lifecycle. Message creation and delivery intents commit together. Local
history is an idempotent projection; a remote attempt without a receipt becomes
unknown and is never retried automatically.
"""
import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
_STORES = {}
_STORES_LOCK = threading.RLock()
_worker = None
_MAX_TEXT = 16000
_LEASE_SECONDS = 150


class MessagingConflict(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise ValueError(f'消息必须为 1–{_MAX_TEXT} 个字符')
    return value.strip()


def _scheduled(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError('scheduled_at 必须为带时区的 ISO 时间') from exc


def _key(value):
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200):
        raise ValueError('idempotency_key 必须为 1–200 个字符')
    return value or uuid.uuid4().hex


class MessageStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.migrated = set()
        self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sm_cases (
                id TEXT NOT NULL, owner TEXT NOT NULL, service TEXT NOT NULL,
                conversation TEXT NOT NULL, record TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,id));
            CREATE INDEX IF NOT EXISTS sm_cases_owner ON sm_cases(owner,deleted);
            CREATE INDEX IF NOT EXISTS sm_cases_owner_time ON sm_cases(
                owner, deleted, json_extract(record, '$.timestamp') DESC, id DESC);
            CREATE TABLE IF NOT EXISTS sm_broadcasts (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sm_messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL, service TEXT NOT NULL, conversation TEXT NOT NULL,
                case_id TEXT, broadcast_id TEXT, visible INTEGER NOT NULL, record TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS sm_messages_conversation ON sm_messages(owner,service,conversation,seq);
            CREATE INDEX IF NOT EXISTS sm_messages_case ON sm_messages(owner,case_id,seq);
            CREATE TABLE IF NOT EXISTS sm_message_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL, service TEXT NOT NULL, conversation TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS sm_message_events_scope ON sm_message_events(owner,service,conversation,seq);
            CREATE TABLE IF NOT EXISTS sm_deliveries (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, message_id TEXT NOT NULL,
                channel TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                token TEXT, claimed_at REAL, error TEXT, receipt TEXT, updated_at TEXT NOT NULL,
                UNIQUE(message_id,channel));
            CREATE INDEX IF NOT EXISTS sm_delivery_queue ON sm_deliveries(status,next_attempt);
            CREATE TABLE IF NOT EXISTS sm_requests (
                owner TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                result TEXT NOT NULL, PRIMARY KEY(owner,request_key));
        ''')
        os.chmod(self.path, 0o600)

    def close(self):
        with self.lock:
            self.db.close()

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

    def _prior(self, db, owner, key, payload):
        fingerprint = hashlib.sha256(_json(payload).encode()).hexdigest()
        prior = db.execute('SELECT * FROM sm_requests WHERE owner=? AND request_key=?', (owner, key)).fetchone()
        if prior:
            if prior['fingerprint'] != fingerprint:
                raise MessagingConflict('该幂等键已用于不同请求')
            return json.loads(prior['result']), fingerprint
        return None, fingerprint

    @staticmethod
    def _remember(db, owner, key, fingerprint, result):
        db.execute('INSERT INTO sm_requests VALUES(?,?,?,?)', (owner, key, fingerprint, _json(result)))

    @staticmethod
    def _message(db, owner, service, conversation, content, *, author='admin', purpose='reply', case_id=None,
                 broadcast_id=None, visible=True, mid=None, created_at=None):
        record = {'id': mid or 'msg_' + uuid.uuid4().hex, 'conversation_id': conversation,
                  'service_id': service, 'role': 'assistant' if author == 'admin' else 'user',
                  'author_type': author, 'purpose': purpose, 'content': content,
                  'created_at': created_at or _now(), 'case_id': case_id, 'broadcast_id': broadcast_id}
        cursor = db.execute('INSERT INTO sm_messages(id,owner,service,conversation,case_id,broadcast_id,visible,record) VALUES(?,?,?,?,?,?,?,?)',
                            (record['id'], owner, service, conversation, case_id, broadcast_id, int(visible), _json(record)))
        return {**record, 'seq': cursor.lastrowid}

    @staticmethod
    def _delivery(db, owner, message, channel, target, available_at=0):
        db.execute('INSERT INTO sm_deliveries(id,owner,message_id,channel,target,status,updated_at,next_attempt) VALUES(?,?,?,?,?,?,?,?)',
                   ('delivery_' + uuid.uuid4().hex, owner, message['id'], channel, _json(target), 'pending', _now(), available_at))

    def create_case(self, owner, service, conversation, content, target, service_name, *, request_key, admin_target):
        payload = ['contact', service, conversation, content, target]
        with self.transaction() as db:
            prior, fingerprint = self._prior(db, owner, request_key, payload)
            if prior:
                return prior['case_id']
            cid = 'inbox_' + uuid.uuid4().hex
            now = _now()
            record = {'id': cid, 'service_id': service, 'service_name': service_name, 'conversation_id': conversation,
                      'channel': target['source'], 'target': target, 'message': content, 'timestamp': now,
                      'status': 'unread', 'case_status': 'open', 'read_at': None, 'handled_by': None,
                      'agent_response': None, 'wechat_session_id': target.get('session_id', ''),
                      'wechat_user_id': target.get('recipient_id', '')}
            db.execute('INSERT INTO sm_cases(id,owner,service,conversation,record) VALUES(?,?,?,?,?)',
                       (cid, owner, service, conversation, _json(record)))
            self._message(db, owner, service, conversation, content, author='consumer', purpose='contact', case_id=cid, visible=False)
            notification = self._message(db, owner, service, conversation,
                f'Service「{service_name}」收到新反馈\n反馈编号：{cid}\n{content}\n\n请在收件箱查看并回复，或发送：\n回复 {cid}：你的回复正文',
                author='system', purpose='admin_notification', case_id=cid, visible=False)
            self._delivery(db, owner, notification, 'admin_wechat', admin_target)
            self._remember(db, owner, request_key, fingerprint, {'case_id': cid})
            return cid

    def reply(self, owner, cid, content, target, *, request_key):
        with self.transaction() as db:
            prior, fingerprint = self._prior(db, owner, request_key, ['reply', cid, content])
            if prior:
                return prior['message_id']
            row = db.execute('SELECT * FROM sm_cases WHERE id=? AND owner=? AND deleted=0', (cid, owner)).fetchone()
            if not row:
                raise KeyError('反馈不存在')
            record = json.loads(row['record'])
            # The caller validates the frozen target. Never accept a replacement recipient.
            if target != record['target']:
                raise MessagingConflict('反馈目标已变更')
            msg = self._message(db, owner, row['service'], row['conversation'], content, case_id=cid)
            self._queue_consumer(db, owner, msg, target)
            record.update(case_status='replied', status='read', read_at=record.get('read_at') or _now(), handled_by='manual')
            db.execute('UPDATE sm_cases SET record=? WHERE id=? AND owner=?', (_json(record), cid, owner))
            self._remember(db, owner, request_key, fingerprint, {'message_id': msg['id']})
            return msg['id']

    def create_broadcast(self, owner, service, targets, content, *, request_key, scheduled_at=None):
        with self.transaction() as db:
            payload = ['notice', service, content, sorted(targets, key=lambda t: t['conversation_id']), scheduled_at]
            prior, fingerprint = self._prior(db, owner, request_key, payload)
            if prior:
                return prior['broadcast_id']
            bid = 'broadcast_' + uuid.uuid4().hex
            record = {'id': bid, 'service_id': service, 'content': content, 'created_at': _now(), 'recipient_count': len(targets), 'scheduled_at': scheduled_at}
            db.execute('INSERT INTO sm_broadcasts VALUES(?,?,?)', (bid, owner, _json(record)))
            for target in targets:
                msg = self._message(db, owner, service, target['conversation_id'], content, purpose='notice', broadcast_id=bid)
                self._queue_consumer(db, owner, msg, target, available_at=datetime.fromisoformat(scheduled_at).timestamp() if scheduled_at else 0)
            self._remember(db, owner, request_key, fingerprint, {'broadcast_id': bid})
            return bid

    def _queue_consumer(self, db, owner, msg, target, available_at=0):
        self._delivery(db, owner, msg, 'web', target, available_at)
        if target['source'] == 'wechat':
            self._delivery(db, owner, msg, 'wechat', target, available_at)

    @staticmethod
    def _public_delivery(row):
        item = dict(row)
        for k in ('owner', 'target', 'token', 'claimed_at', 'next_attempt'):
            item.pop(k, None)
        item['receipt'] = json.loads(item['receipt']) if item.get('receipt') else None
        return item

    def _deliveries(self, db, owner, ids):
        if not ids:
            return []
        placeholders = ','.join('?' for _ in ids)
        return [self._public_delivery(r) for r in db.execute(
            f'SELECT * FROM sm_deliveries WHERE owner=? AND message_id IN ({placeholders}) ORDER BY rowid', (owner, *ids))]

    def get_case(self, owner, cid):
        with self.lock:
            row = self.db.execute('SELECT record FROM sm_cases WHERE id=? AND owner=? AND deleted=0', (cid, owner)).fetchone()
            if not row:
                return None
            record = json.loads(row['record'])
            messages = [dict(json.loads(r['record']), seq=r['seq']) for r in self.db.execute(
                'SELECT seq,record FROM sm_messages WHERE owner=? AND case_id=? ORDER BY seq', (owner, cid))]
            deliveries = self._deliveries(self.db, owner, [m['id'] for m in messages])
            record.pop('target', None)
            return {**record, 'messages': [m for m in messages if m['purpose'] != 'admin_notification'],
                    'notification': next((d for d in deliveries if d['channel'] == 'admin_wechat'), None), 'deliveries': deliveries}

    def case_target(self, owner, cid):
        with self.lock:
            row = self.db.execute('SELECT record FROM sm_cases WHERE id=? AND owner=? AND deleted=0', (cid, owner)).fetchone()
            return json.loads(row['record'])['target'] if row else None

    def list_cases(self, owner, status=None):
        with self.lock:
            ids = [r['id'] for r in self.db.execute('SELECT id FROM sm_cases WHERE owner=? AND deleted=0', (owner,))]
            cases = [self.get_case(owner, cid) for cid in ids]
        return sorted((c for c in cases if c and (not status or c['status'] == status)), key=lambda c: c['timestamp'], reverse=True)

    def list_case_summaries(self, owner, offset=0, limit=20):
        """Read one owner's inbox page without hydrating messages or deliveries."""
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError('offset 无效')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('limit 无效')
        with self.lock:
            rows = self.db.execute('''
                SELECT record FROM sm_cases WHERE owner=? AND deleted=0
                ORDER BY json_extract(record, '$.timestamp') DESC, id DESC
                LIMIT ? OFFSET ?
            ''', (owner, limit, offset)).fetchall()
        allowed = ('id', 'service_id', 'service_name', 'conversation_id', 'channel',
                   'timestamp', 'status', 'case_status', 'message')
        return [{key: case.get(key) for key in allowed}
                for row in rows for case in [json.loads(row['record'])]]

    def update_case(self, owner, cid, *, status=None, case_status=None):
        if status not in (None, 'unread', 'read', 'handled') or case_status not in (None, 'open', 'acknowledged', 'resolved'):
            raise ValueError('反馈状态无效；replied 只能由回复操作产生')
        with self.transaction() as db:
            row = db.execute('SELECT record FROM sm_cases WHERE id=? AND owner=? AND deleted=0', (cid, owner)).fetchone()
            if not row:
                return None
            record = json.loads(row['record'])
            if status:
                record['status'] = status
                record['read_at'] = None if status == 'unread' else _now()
                if status == 'handled':
                    record['case_status'] = 'resolved'
                elif status == 'read' and record.get('case_status') == 'open':
                    record['case_status'] = 'acknowledged'
            if case_status:
                record['case_status'] = case_status
                record['status'] = 'handled' if case_status == 'resolved' else 'read'
                record['read_at'] = record.get('read_at') or _now()
            record['handled_by'] = 'manual'
            db.execute('UPDATE sm_cases SET record=? WHERE id=? AND owner=?', (_json(record), cid, owner))
        return self.get_case(owner, cid)

    def delete_case(self, owner, cid):
        with self.transaction() as db:
            result = db.execute('UPDATE sm_cases SET deleted=1 WHERE id=? AND owner=? AND deleted=0', (cid, owner)).rowcount
            if result:
                db.execute("UPDATE sm_deliveries SET status='cancelled',error='反馈已删除',updated_at=? WHERE owner=? AND status IN ('pending','retry_wait') AND message_id IN (SELECT id FROM sm_messages WHERE case_id=? AND owner=?)", (_now(), owner, cid, owner))
            return bool(result)

    def get_message(self, owner, mid):
        with self.lock:
            row = self.db.execute('SELECT * FROM sm_messages WHERE id=? AND owner=?', (mid, owner)).fetchone()
            return dict(json.loads(row['record']), seq=row['seq']) if row else None

    def list_events(self, owner, service, conversation, after=0, limit=100):
        limit = max(1, min(int(limit), 200))
        after = max(0, int(after))
        with self.lock:
            rows = list(self.db.execute("""SELECT e.seq,m.record FROM sm_message_events e JOIN sm_messages m ON m.id=e.message_id
                WHERE e.owner=? AND e.service=? AND e.conversation=? AND e.seq>? ORDER BY e.seq LIMIT ?""", (owner, service, conversation, after, limit + 1)))
        selected = rows[:limit]
        events = [{'id': json.loads(r['record'])['id'], 'seq': r['seq'], 'type': 'message', 'conversation_id': conversation,
                   'message': dict(json.loads(r['record']), seq=r['seq'])} for r in selected]
        return {'events': events, 'next_cursor': events[-1]['seq'] if events else after, 'has_more': len(rows) > limit}

    def get_broadcast(self, owner, bid):
        with self.lock:
            row = self.db.execute('SELECT record FROM sm_broadcasts WHERE id=? AND owner=?', (bid, owner)).fetchone()
            if not row:
                return None
            msgs = [dict(json.loads(r['record']), seq=r['seq']) for r in self.db.execute('SELECT seq,record FROM sm_messages WHERE broadcast_id=? AND owner=? ORDER BY seq', (bid, owner))]
            deliveries = self._deliveries(self.db, owner, [m['id'] for m in msgs])
            return {**json.loads(row['record']), 'messages': msgs, 'deliveries': deliveries}

    def list_broadcasts(self, owner, service=None, limit=50):
        with self.lock:
            rows = list(self.db.execute("SELECT id FROM sm_broadcasts WHERE owner=? AND (? IS NULL OR json_extract(record,'$.service_id')=?) ORDER BY rowid DESC LIMIT ?", (owner, service, service, max(1, min(limit, 100)))))
            result = [self.get_broadcast(owner, r['id']) for r in rows]
        return result

    def manage_deliveries(self, owner, ids, *, cancel=False, allow_unknown=False):
        with self.transaction() as db:
            rows = [db.execute('SELECT * FROM sm_deliveries WHERE id=? AND owner=?', (did, owner)).fetchone() for did in ids]
            if any(row is None for row in rows):
                raise KeyError('投递不存在')
            if not cancel and not allow_unknown and any(row['status'] == 'unknown' for row in rows):
                raise MessagingConflict('投递结果未知，重发可能重复；需明确确认 allow_unknown')
            for row in rows:
                if cancel and row['status'] in ('pending', 'retry_wait'):
                    db.execute("UPDATE sm_deliveries SET status='cancelled',error='管理员取消',updated_at=? WHERE id=?", (_now(), row['id']))
                elif not cancel and row['status'] in ('retry_wait', 'unknown'):
                    db.execute("UPDATE sm_deliveries SET status='pending',next_attempt=0,token=NULL,error=NULL,updated_at=? WHERE id=?", (_now(), row['id']))

    def claim(self):
        with self.transaction() as db:
            # Leases avoid stealing work from another process on startup. Remote
            # attempts are unknown after expiry; local history is idempotent.
            db.execute("UPDATE sm_deliveries SET status=CASE WHEN channel='web' THEN 'retry_wait' ELSE 'unknown' END,token=NULL,error='进程中断，投递回执缺失',updated_at=? WHERE status='inflight' AND claimed_at<?", (_now(), time.time() - _LEASE_SECONDS))
            row = db.execute("SELECT * FROM sm_deliveries WHERE status IN ('pending','retry_wait') AND next_attempt<=? ORDER BY rowid LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE sm_deliveries SET status='inflight',token=?,claimed_at=?,attempt=attempt+1,updated_at=? WHERE id=?", (token, time.time(), _now(), row['id']))
            result = dict(row)
            result.update(token=token, status='inflight', attempt=row['attempt'] + 1, target=json.loads(row['target']))
            result['message'] = self.get_message(row['owner'], row['message_id'])
            return result

    def check_claim(self, delivery):
        with self.lock:
            row = self.db.execute("SELECT claimed_at FROM sm_deliveries WHERE id=? AND token=? AND status='inflight'", (delivery['id'], delivery['token'])).fetchone()
            if not row or row['claimed_at'] < time.time() - _LEASE_SECONDS:
                raise PermissionError('投递租约已失效')

    def bind_admin(self, delivery, target):
        with self.transaction() as db:
            changed = db.execute("UPDATE sm_deliveries SET target=? WHERE id=? AND token=? AND status='inflight'", (_json(target), delivery['id'], delivery['token'])).rowcount
            if not changed:
                raise PermissionError('投递租约已失效')
        delivery['target'] = target

    def finish(self, delivery, status, error=None, receipt=None):
        with self.transaction() as db:
            changed = db.execute("UPDATE sm_deliveries SET status=?,error=?,receipt=?,token=NULL,next_attempt=?,updated_at=? WHERE id=? AND token=? AND status='inflight'",
                (status, error, _json(receipt) if receipt is not None else None, time.time() + min(300, 2 ** min(delivery['attempt'], 8)), _now(), delivery['id'], delivery['token'])).rowcount
            if changed and status == 'delivered' and delivery['channel'] == 'web':
                msg = delivery['message']
                db.execute('INSERT OR IGNORE INTO sm_message_events(message_id,owner,service,conversation) VALUES(?,?,?,?)',
                    (msg['id'], delivery['owner'], msg['service_id'], msg['conversation_id']))

    def migrate_legacy(self, owner, root):
        with self.lock:
            if owner in self.migrated:
                return
            directory = Path(root) / owner / 'inbox'
            if directory.is_dir():
                for path in directory.glob('inbox_*.json'):
                    try:
                        old = json.loads(path.read_text(encoding='utf-8'))
                        cid = old['id']
                        if not isinstance(cid, str) or not old.get('service_id') or not old.get('conversation_id'):
                            continue
                        source = 'wechat' if old.get('wechat_session_id') else 'unknown'
                        target = {'owner_id': owner, 'service_id': old['service_id'], 'conversation_id': old['conversation_id'],
                                  'source': source, 'session_id': old.get('wechat_session_id', ''), 'recipient_id': old.get('wechat_user_id', '')}
                        record = {'handled_by': None, 'agent_response': None, **old, 'timestamp': str(old.get('timestamp') or _now()), 'service_name': old.get('service_name') or old['service_id'], 'message': str(old.get('message') or ''), 'target': target, 'channel': source, 'legacy_status': old.get('status'), 'legacy_imported': True,
                                  'case_status': 'open' if old.get('status') == 'unread' else 'acknowledged',
                                  'read_at': None if old.get('status') == 'unread' else old.get('timestamp'),
                                  'status': 'unread' if old.get('status') == 'unread' else 'read'}
                        with self.transaction() as db:
                            changed = db.execute('INSERT OR IGNORE INTO sm_cases(id,owner,service,conversation,record) VALUES(?,?,?,?,?)',
                                (cid, owner, old['service_id'], old['conversation_id'], _json(record))).rowcount
                            if changed:
                                self._message(db, owner, old['service_id'], old['conversation_id'], old.get('message', ''), author='consumer', purpose='contact', case_id=cid, visible=False, created_at=old.get('timestamp'))
                        # No replay of historical WeChat notifications: old JSON
                        # cannot establish whether those messages already arrived.
                    except (ValueError, OSError, KeyError, TypeError):
                        log.warning('Cannot import legacy inbox file %s', path.name)
            self.migrated.add(owner)


def get_store():
    from app.core.security import USERS_DIR
    path = os.path.realpath(os.path.join(USERS_DIR, '.scheduler', 'executions.sqlite3'))
    with _STORES_LOCK:
        if path not in _STORES:
            _STORES[path] = MessageStore(path)
        return _STORES[path]


def _owner(owner):
    from app.core.security import _load_users
    user = _load_users().get(owner)
    if not user or user.get('disabled'):
        raise PermissionError('管理员账号已不可用')


def _consumer_target(owner, service, conversation, *, wechat_session_id=None, channel=None):
    _owner(owner)
    from app.channels.wechat.policy import ensure_service_active, ensure_wechat_session
    from app.services.published import get_consumer_conversation
    ensure_service_active(owner, service)
    conv = get_consumer_conversation(owner, service, conversation)
    if not conv or conv.get('source') == 'admin_test':
        raise KeyError('Service 会话不存在')
    source = channel or conv.get('source', 'web')
    if source not in ('web', 'api', 'wechat'):
        source = 'web'
    target = {'owner_id': owner, 'service_id': service, 'conversation_id': conversation, 'source': source}
    if wechat_session_id or source == 'wechat':
        from app.channels.wechat.session_manager import get_session_manager
        manager = get_session_manager()
        if wechat_session_id:
            session = manager.get_session(wechat_session_id)
        else:
            matches = [s for s in manager.list_sessions(service_id=service, admin_id=owner) if s.conversation_id == conversation]
            session = matches[0] if len(matches) == 1 else None
        if not session or (session.admin_id, session.service_id, session.conversation_id) != (owner, service, conversation):
            raise PermissionError('微信会话不属于当前反馈目标')
        ensure_wechat_session(owner, service, session.session_id, conversation_id=conversation)
        target.update(source='wechat', session_id=session.session_id, recipient_id=session.from_user_id)
    return target


def _authorize_target(target):
    if target['source'] == 'unknown':
        from app.services.published import get_consumer_conversation
        conv = get_consumer_conversation(target['owner_id'], target['service_id'], target['conversation_id'])
        if conv and conv.get('source') == 'wechat':
            raise PermissionError('历史反馈缺少可验证的微信绑定，不能推测收件人')
    current = _consumer_target(target['owner_id'], target['service_id'], target['conversation_id'],
                              wechat_session_id=target.get('session_id'), channel=target['source'])
    if target.get('session_id') != current.get('session_id') or target.get('recipient_id') != current.get('recipient_id'):
        raise PermissionError('目标会话已重新绑定，请重新选择收件对象')


def _admin_target(owner):
    from app.channels.wechat.admin_router import _get_session
    session = _get_session(owner)
    target = {'owner_id': owner}
    if session and session.get('connected'):
        target.update(conversation_id=session.get('conversation_id'), recipient_id=session.get('from_user_id', ''))
    return target


def post_contact(owner_id, service_id, conversation_id, text, *, wechat_session_id=None, channel=None, idempotency_key=None):
    content = _text(text)
    target = _consumer_target(owner_id, service_id, conversation_id, wechat_session_id=wechat_session_id, channel=channel)
    from app.services.published import get_service
    service = get_service(owner_id, service_id)
    store = get_store()
    cid = store.create_case(owner_id, service_id, conversation_id, content, target, service.get('name', service_id),
                            request_key=_key(idempotency_key), admin_target=_admin_target(owner_id))
    result = store.get_case(owner_id, cid)
    if result is None:
        raise MessagingConflict('该反馈已删除，不能重复提交旧请求')
    return result


def reply_to_case(owner_id, case_id, text, *, idempotency_key):
    store = get_store()
    target = store.case_target(owner_id, case_id)
    if target is None:
        raise KeyError('反馈不存在')
    _authorize_target(target)
    mid = store.reply(owner_id, case_id, _text(text), target, request_key=_key(idempotency_key))
    case = store.get_case(owner_id, case_id)
    return {'case': case, 'message': store.get_message(owner_id, mid),
            'deliveries': [d for d in case['deliveries'] if d['message_id'] == mid]}


def create_notice_broadcast(owner_id, service_id, conversation_ids, text, *, idempotency_key, scheduled_at=None):
    if not isinstance(conversation_ids, (list, tuple)) or not conversation_ids or len(conversation_ids) > 500:
        raise ValueError('请指定 1–500 个会话')
    targets = [_consumer_target(owner_id, service_id, cid) for cid in sorted(set(conversation_ids))]
    bid = get_store().create_broadcast(owner_id, service_id, targets, _text(text), request_key=_key(idempotency_key), scheduled_at=_scheduled(scheduled_at))
    return get_store().get_broadcast(owner_id, bid)


def get_notice_broadcast(owner_id, broadcast_id):
    return get_store().get_broadcast(owner_id, broadcast_id)


def list_notice_broadcasts(owner_id, service_id=None, limit=50):
    return get_store().list_broadcasts(owner_id, service_id, limit)


def cancel_notice_broadcast(owner_id, broadcast_id):
    item = get_notice_broadcast(owner_id, broadcast_id)
    if item is None:
        raise KeyError('广播不存在')
    get_store().manage_deliveries(owner_id, [d['id'] for d in item['deliveries']], cancel=True)
    return get_notice_broadcast(owner_id, broadcast_id)


def retry_notice_broadcast(owner_id, broadcast_id, *, allow_unknown=False):
    item = get_notice_broadcast(owner_id, broadcast_id)
    if item is None:
        raise KeyError('广播不存在')
    get_store().manage_deliveries(owner_id, [d['id'] for d in item['deliveries']], allow_unknown=allow_unknown)
    return get_notice_broadcast(owner_id, broadcast_id)


def list_conversation_events(owner_id, service_id, conversation_id, *, after=0, limit=100):
    # The HTTP adapter must additionally authenticate the consumer Service Key;
    # this function never upgrades it to an admin or another Service identity.
    _owner(owner_id)
    from app.channels.wechat.policy import ensure_service_active
    from app.services.published import get_consumer_conversation
    ensure_service_active(owner_id, service_id)
    conv = get_consumer_conversation(owner_id, service_id, conversation_id)
    if not conv or conv.get('source') == 'admin_test':
        raise KeyError('Service 会话不存在')
    return get_store().list_events(owner_id, service_id, conversation_id, after, limit)


async def deliver_one(store, delivery):
    sending = False
    try:
        target, msg, channel = delivery['target'], delivery['message'], delivery['channel']
        _owner(delivery['owner'])
        if msg.get('case_id') and store.get_case(delivery['owner'], msg['case_id']) is None:
            raise PermissionError('反馈已删除')
        if channel == 'admin_wechat':
            from app.channels.wechat.admin_router import _get_session
            session = _get_session(delivery['owner'])
            if not session or not session.get('connected'):
                raise ConnectionError('管理员微信尚未连接')
            if target.get('conversation_id') and target['conversation_id'] != session.get('conversation_id'):
                raise PermissionError('管理员微信连接已更换')
            if target.get('recipient_id') and target['recipient_id'] != session.get('from_user_id'):
                raise PermissionError('管理员微信收件人已更换')
            client, recipient, context = session.get('client'), session.get('from_user_id'), session.get('context_token')
            if not client or not recipient or not context:
                raise ConnectionError('请先向管理员微信发送一条消息以建立投递上下文')
            # Once a recipient is resolved, never silently retarget on rescan.
            if not target.get('recipient_id'):
                store.bind_admin(delivery, {**target, 'conversation_id': session['conversation_id'], 'recipient_id': recipient})
        else:
            _authorize_target(target)
            if channel == 'web':
                store.check_claim(delivery)
                from app.services.published import save_consumer_message
                save_consumer_message(delivery['owner'], msg['service_id'], msg['conversation_id'], 'assistant', msg['content'],
                    event_id=msg['id'], metadata={k: msg[k] for k in ('author_type', 'case_id', 'broadcast_id')})
                store.finish(delivery, 'delivered')
                return
            if channel != 'wechat':
                raise PermissionError('不支持的消息投递渠道')
            from app.channels.wechat.session_manager import get_session_manager
            manager = get_session_manager()
            session = manager.get_session(target['session_id'])
            client = manager.get_client(target['session_id'])
            recipient, context = session.from_user_id, session.context_token
            if not client or not recipient or not context:
                raise ConnectionError('微信连接尚不可投递')
        store.check_claim(delivery)
        sending = True
        receipt = await client.send_text(recipient, msg['content'], context)
        from app.channels.wechat.client import ensure_ilink_success
        ensure_ilink_success(receipt, require_ack=True)
        # Do not persist protocol tokens or arbitrary provider data in receipts.
        store.finish(delivery, 'delivered', receipt={'ret': 0})
    except asyncio.CancelledError:
        store.finish(delivery, 'unknown' if sending else 'retry_wait', '投递被中断')
        raise
    except Exception as exc:
        from fastapi import HTTPException
        revoked = isinstance(exc, (PermissionError, KeyError, FileNotFoundError)) or isinstance(exc, HTTPException) and exc.status_code in (400, 401, 403, 404, 409, 410)
        explicit_rejection = bool(getattr(exc, 'explicit_rejection', False))
        status = 'cancelled' if revoked or explicit_rejection else 'unknown' if sending else 'retry_wait'
        store.finish(delivery, status, str(exc)[:500])


async def _delivery_loop(store):
    while True:
        try:
            delivery = store.claim()
            if delivery:
                async with asyncio.timeout(120):
                    await deliver_one(store, delivery)
            else:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Service message delivery worker failed; will retry')
            await asyncio.sleep(2)


def start_worker():
    global _worker
    if os.getenv('DISABLE_SERVICE_MESSAGING', '').lower() in ('1', 'true', 'yes', 'on'):
        return
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_delivery_loop(get_store()))


async def stop_worker():
    global _worker
    if _worker is not None:
        _worker.cancel()
        try:
            await _worker
        except asyncio.CancelledError:
            pass
        _worker = None
