"""Admin feedback facade. SQLite is authoritative; old JSON is imported once.

Notifications are durable delivery intents, never privileged Agent executions.
The public fields remain compatible with the existing inbox UI and memory tools.
"""
from app.services import service_messaging as messaging


def set_main_loop(loop):
    """Compatibility for older startup callers; tools no longer schedule tasks."""
    return None


def _store(admin_id):
    from app.core.security import USERS_DIR
    store = messaging.get_store()
    store.migrate_legacy(admin_id, USERS_DIR)
    return store


def list_inbox(admin_id, status=None):
    return _store(admin_id).list_cases(admin_id, status)


def list_inbox_summaries(admin_id, offset=0, limit=20):
    """Owner-scoped feedback summaries for the Agent's paged record area."""
    return _store(admin_id).list_case_summaries(admin_id, offset, limit)


def get_inbox_message(admin_id, msg_id):
    return _store(admin_id).get_case(admin_id, msg_id)


def update_inbox_status(admin_id, msg_id, status=None, *, case_status=None):
    return _store(admin_id).update_case(admin_id, msg_id, status=status, case_status=case_status)


def delete_inbox_message(admin_id, msg_id):
    return _store(admin_id).delete_case(admin_id, msg_id)


def count_unread(admin_id):
    return len(list_inbox(admin_id, status='unread'))


def post_to_inbox(admin_id, service_id, conversation_id, message, wechat_session_id=None, *, idempotency_key=None, channel=None):
    result = messaging.post_contact(admin_id, service_id, conversation_id, message,
        wechat_session_id=wechat_session_id, channel=channel, idempotency_key=idempotency_key)
    return {'id': result['id'], 'summary': f"反馈已提交到管理员收件箱（编号：{result['id']}）。通知将按连接状态投递；尚不代表管理员已阅读。"}


def reply_to_inbox(admin_id, msg_id, message, *, idempotency_key):
    _store(admin_id)
    return messaging.reply_to_case(admin_id, msg_id, message, idempotency_key=idempotency_key)


def retry_delivery(admin_id, msg_id, delivery_id, *, allow_unknown=False):
    store = _store(admin_id)
    case = store.get_case(admin_id, msg_id)
    if not case or not any(d['id'] == delivery_id for d in case['deliveries']):
        raise KeyError('反馈投递不存在')
    store.manage_deliveries(admin_id, [delivery_id], allow_unknown=allow_unknown)
    return store.get_case(admin_id, msg_id)
