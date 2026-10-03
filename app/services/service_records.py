"""Bounded, actor-owned read projection of Service records for admin agents.

The virtual /service-records area never exposes the on-disk Service tree. In
particular, credentials, delivery targets, attachments and raw config are not
part of this projection. Both entrypoints recheck the administrator's switch
after reading, so a concurrent revocation cannot finish a slow read.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import stat
from typing import Any


ROOT = '/service-records'
MAX_OFFSET = 1_000_000
MAX_LIMIT = 20
MAX_OUTPUT_BYTES = 64 * 1024
MAX_CONTENT_OFFSET = 2 * 1024 * 1024
MAX_SCAN_BYTES = 128 * 1024 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024
_SERVICE_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_CASE_ID = re.compile(r'[A-Za-z0-9_-]{1,100}\Z')
_MESSAGE_REF = re.compile(r'[a-f0-9]{64}\Z')


def _authorized(actor_id: str) -> None:
    from app.services.memory_tools import get_soul_config

    if get_soul_config(actor_id).get('service_records_enabled') is not True:
        raise ValueError('Service 记录区未启用')


def _done(actor_id: str, result: dict) -> dict:
    _authorized(actor_id)
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_OUTPUT_BYTES:
        raise ValueError('Service 记录区结果超过 64KB，请缩小读取范围')
    return result


def _page_args(offset: int, limit: int) -> None:
    if (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > MAX_OFFSET or
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > MAX_LIMIT):
        raise ValueError('分页参数超出范围')


def _parts(path: str) -> tuple[str, ...]:
    if not isinstance(path, str) or len(path) > 500 or not path.startswith(ROOT):
        raise ValueError('只允许 Service 记录区路径')
    pieces = path.split('/')
    if (pieces[:2] != ['', 'service-records'] or any(not p or p in ('.', '..') or '\\' in p or
            any(ord(ch) < 32 for ch in p) for p in pieces[2:])):
        raise ValueError('Service 记录区路径无效')
    parts = tuple(pieces[2:])
    if len(parts) > 3:
        raise ValueError('Service 记录区路径无效')
    if parts:
        if len(parts) == 2 and parts[0] == 'inbox' and not _CASE_ID.fullmatch(parts[1]):
            raise ValueError('反馈 ID 无效')
        if parts[0] != 'inbox' and not _SERVICE_ID.fullmatch(parts[0]):
            raise ValueError('Service ID 无效')
        if len(parts) == 3 and parts[1] == 'conversations' and not _SERVICE_ID.fullmatch(parts[2]):
            raise ValueError('会话 ID 无效')
    return parts


def _text(value: Any, maximum: int = 200) -> str:
    return value[:maximum] if isinstance(value, str) else ''


def _content(value: Any, start: int, max_bytes: int) -> dict:
    """Slice by Unicode characters while enforcing a UTF-8 byte budget."""
    if not isinstance(value, str):
        return {'content': '[非文本消息]', 'content_offset': 0,
                'next_content_offset': None, 'content_truncated': False}
    if start > len(value):
        raise ValueError('正文偏移超过消息长度')
    budget, end = max_bytes, start
    while end < len(value):
        width = len(value[end].encode('utf-8'))
        if width > budget:
            break
        budget -= width
        end += 1
    return {'content': value[start:end], 'content_offset': start,
            'next_content_offset': end if end < len(value) else None,
            'content_truncated': end < len(value)}


def _ref(*parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _number(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _owned_service(actor_id: str, service_id: str) -> dict:
    from app.services.published import get_service

    if not _SERVICE_ID.fullmatch(service_id):
        raise ValueError('Service ID 无效')
    service = get_service(actor_id, service_id)
    if not service or service.get('admin_id') != actor_id or service.get('id') != service_id:
        raise ValueError('Service 不存在')
    return service


def _page(path: str, items: list[dict], offset: int, limit: int) -> dict:
    return _window(path, items[offset:offset + limit + 1], offset, limit)


def _window(path: str, items: list[dict], offset: int, limit: int) -> dict:
    page = items[:limit]
    more = len(items) > limit
    next_offset = offset + len(page) if more else None
    return {'path': path, 'items': page, 'offset': offset, 'limit': limit,
            'has_more': more, 'next_offset': next_offset if next_offset is not None and next_offset <= MAX_OFFSET else None,
            'pagination_limit_reached': more and next_offset is not None and next_offset > MAX_OFFSET}


def _reverse_jsonl(path: str):
    """Yield newest JSON objects without materializing the skipped history."""
    if not os.path.isfile(path):
        return
    scanned = 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('记录文件必须是独立普通文件')
        position = stream.seek(0, os.SEEK_END)
        pending = b''
        while position:
            step = min(64 * 1024, position)
            position -= step
            stream.seek(position)
            pending = stream.read(step) + pending
            scanned += step
            if scanned > MAX_SCAN_BYTES:
                raise ValueError('记录扫描超过安全上限')
            lines = pending.split(b'\n')
            pending = lines[0]
            for raw in reversed(lines[1:]):
                if not raw.strip():
                    continue
                if len(raw) > MAX_RECORD_BYTES:
                    raise ValueError('单条记录超过安全上限')
                try:
                    row = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(row, dict):
                    yield row
            if len(pending) > MAX_RECORD_BYTES:
                raise ValueError('单条记录超过安全上限')
        if pending.strip():
            try:
                row = json.loads(pending)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if isinstance(row, dict):
                yield row


def _take_after(records, offset: int, limit: int) -> list[dict]:
    page = []
    for index, record in enumerate(records):
        if index < offset:
            continue
        page.append(record)
        if len(page) > limit:
            break
    return page


def _message(record: dict, start: int = 0, max_content_bytes: int = 2000) -> dict:
    return {'role': _text(record.get('role'), 30),
            **_content(record.get('content'), start, max_content_bytes),
            'message_ref': _ref(record.get('role'), record.get('content'), record.get('timestamp'),
                                record.get('message_id') or record.get('id')),
            'timestamp': _text(record.get('timestamp') or record.get('created_at'), 40),
            'source': _text(record.get('source'), 30),
            'author_type': _text(record.get('author_type'), 40),
            'message_id': _text(record.get('message_id') or record.get('id'), 100)}


def _inbox_summary(case: dict) -> dict:
    case_id = _text(case.get('id'), 100)
    return {'id': case_id, 'path': f'{ROOT}/inbox/{case_id}',
            'service_id': _text(case.get('service_id'), 64),
            'service_name': _text(case.get('service_name'), 200),
            'conversation_id': _text(case.get('conversation_id'), 64),
            'channel': _text(case.get('channel'), 30),
            'timestamp': _text(case.get('timestamp'), 40),
            'status': _text(case.get('status'), 30),
            'case_status': _text(case.get('case_status'), 30),
            'message_excerpt': _text(case.get('message'), 500)}


def list_area(actor_id: str, path: str, offset: int = 0, limit: int = 20) -> dict:
    """List a virtual Service record directory, newest entries first."""
    _authorized(actor_id)
    _page_args(offset, limit)
    parts = _parts(path)

    if not parts:
        from app.services.published import list_services

        items = [{'kind': 'area', 'name': 'inbox', 'path': ROOT + '/inbox'}]
        items.extend({'kind': 'service', 'id': s['id'], 'name': _text(s.get('name'), 200),
                      'path': ROOT + '/' + s['id']}
                     for s in list_services(actor_id)
                     if isinstance(s.get('id'), str) and _SERVICE_ID.fullmatch(s['id'])
                     and s.get('admin_id') == actor_id)
        return _done(actor_id, _page(path, items, offset, limit))

    if parts == ('inbox',):
        from app.services.inbox import list_inbox_summaries

        cases = list_inbox_summaries(actor_id, offset=offset, limit=limit + 1)
        items = [_inbox_summary(c) for c in cases
                 if isinstance(c.get('id'), str) and _CASE_ID.fullmatch(c['id'])]
        return _done(actor_id, _window(path, items, offset, limit))

    service_id = parts[0]
    _owned_service(actor_id, service_id)
    if len(parts) == 1:
        items = [{'kind': 'area', 'name': name, 'path': f'{path}/{name}'}
                 for name in ('conversations', 'usage', 'token-usage')]
        return _done(actor_id, _page(path, items, offset, limit))

    if parts == (service_id, 'conversations'):
        from app.services.published import list_consumer_conversations

        items = [{'id': c['id'], 'path': f'{path}/{c["id"]}',
                  'title': _text(c.get('title'), 200), 'source': _text(c.get('source'), 30),
                  'created_at': _text(c.get('created_at'), 40),
                  'updated_at': _text(c.get('updated_at'), 40),
                  'message_count': _number(c.get('message_count'))}
                 for c in list_consumer_conversations(actor_id, service_id)
                 if isinstance(c.get('id'), str) and _SERVICE_ID.fullmatch(c['id'])]
        return _done(actor_id, _page(path, items, offset, limit))

    if parts == (service_id, 'usage'):
        from app.services.usage_log import _list_month_files

        def recent_usage():
            for month_path in _list_month_files(actor_id, service_id)[:6]:
                yield from _reverse_jsonl(month_path)

        records = _take_after(recent_usage(), offset, limit)
        items = [{'ts': _text(r.get('ts'), 40), 'channel': _text(r.get('channel'), 30),
                  'conv_id': _text(r.get('conv_id'), 64), 'endpoint': _text(r.get('endpoint'), 100),
                  'status_code': _number(r.get('status_code')),
                  'latency_ms': _number(r.get('latency_ms')), 'ok': r.get('ok') is True,
                  'billing': _text(r.get('billing'), 30)} for r in records]
        result = _window(path, items, offset, limit)
        result['window'] = 'latest_six_month_files'
        return _done(actor_id, result)

    raise ValueError('此路径不可列出')


def read_area(actor_id: str, path: str, offset: int = 0, limit: int = 20, *,
              content_offset: int = 0, message_ref: str | None = None) -> dict:
    """Read one feedback, a bounded conversation page, or token aggregates."""
    _authorized(actor_id)
    _page_args(offset, limit)
    if (isinstance(content_offset, bool) or not isinstance(content_offset, int) or
            content_offset < 0 or content_offset > MAX_CONTENT_OFFSET):
        raise ValueError('正文偏移超出范围')
    if message_ref is not None and (not isinstance(message_ref, str) or not _MESSAGE_REF.fullmatch(message_ref)):
        raise ValueError('消息引用无效')
    if content_offset and not message_ref:
        raise ValueError('续读正文需要消息引用')
    parts = _parts(path)

    if len(parts) == 2 and parts[0] == 'inbox':
        if offset:
            raise ValueError('单条反馈不支持 offset')
        from app.services.inbox import get_inbox_message

        case = get_inbox_message(actor_id, parts[1])
        if not case or case.get('id') != parts[1]:
            raise ValueError('反馈不存在')
        current_ref = _ref('inbox', case['id'], case.get('message'))
        if message_ref and message_ref != current_ref:
            raise ValueError('反馈内容已变更，请重新读取')
        result = _inbox_summary(case)
        result['path'] = path
        result['message'] = _content(case.get('message'), content_offset, 32 * 1024)
        result['message_ref'] = current_ref
        result['messages'] = [_message(m, max_content_bytes=1500)
                              for m in (case.get('messages') or [])[-5:] if isinstance(m, dict)]
        return _done(actor_id, result)

    if len(parts) == 3 and parts[1] == 'conversations':
        if (content_offset or message_ref) and limit != 1:
            raise ValueError('续读正文时 limit 必须为 1')
        service_id, _, conv_id = parts
        _owned_service(actor_id, service_id)
        from app.services import published
        from app.core.jsonl_store import safe_load_json

        with published._consumer_guard(actor_id, service_id, conv_id):
            published._migrate_consumer_conv_locked(actor_id, service_id, conv_id)
            preview_meta = safe_load_json(published._conv_meta_path(actor_id, service_id, conv_id))
            if not preview_meta or preview_meta.get('source') == 'admin_test':
                raise ValueError('会话不存在')
            recent = _take_after(_reverse_jsonl(published._conv_msgs_path(actor_id, service_id, conv_id)),
                                 offset, limit)
        page = recent[:limit]
        items: list[dict] = []
        # Long, multibyte messages can exceed a fixed page byte budget. Return
        # fewer entries with an accurate continuation offset instead of leaking
        # an unbounded tool result or making the whole page unreadable.
        for index, message in enumerate(page):
            candidate = _message(message, start=content_offset if limit == 1 else 0,
                                 max_content_bytes=48 * 1024 if limit == 1 else max(1600, 48 * 1024 // limit))
            candidate['message_offset'] = offset + index
            if message_ref and candidate['message_ref'] != message_ref:
                raise ValueError('消息位置已变化，请重新定位后读取')
            proposed = {'path': path, 'items': [*items, candidate], 'offset': offset,
                        'limit': limit, 'has_more': True, 'next_offset': offset + len(items) + 1,
                        'order': 'newest_first'}
            if len(json.dumps(proposed, ensure_ascii=False).encode('utf-8')) > MAX_OUTPUT_BYTES - 1024:
                break
            items.append(candidate)
        if not items and page:
            raise ValueError('单条消息超过 Service 记录区读取上限')
        more = len(recent) > len(items)
        next_offset = offset + len(items) if more else None
        result = {'path': path, 'items': items, 'offset': offset, 'limit': limit,
                  'has_more': more,
                  'next_offset': next_offset if next_offset is not None and next_offset <= MAX_OFFSET else None,
                  'pagination_limit_reached': more and next_offset is not None and next_offset > MAX_OFFSET,
                  'order': 'newest_first'}
        return _done(actor_id, result)

    if len(parts) == 2 and parts[1] == 'token-usage':
        if offset or content_offset:
            raise ValueError('Token 用量汇总不支持 offset')
        service_id = parts[0]
        _owned_service(actor_id, service_id)
        from app.services.token_usage import aggregate_usage

        usage = aggregate_usage(actor_id, months=3, service_id=service_id)
        dims = ('by_model', 'by_provider', 'by_channel', 'by_day', 'by_billing')
        result = {'path': path, 'service_id': service_id,
                  'months_scanned': _number(usage.get('months_scanned')),
                  'total': {k: _number((usage.get('total') or {}).get(k))
                            for k in ('calls', 'input_tokens', 'output_tokens', 'total_tokens')}}
        for dim in dims:
            result[dim] = [{'name': _text(row.get('name'), 100),
                            **{k: _number(row.get(k)) for k in ('calls', 'input_tokens', 'output_tokens', 'total_tokens')}}
                           for row in (usage.get(dim) or [])[:50] if isinstance(row, dict)]
        return _done(actor_id, result)

    raise ValueError('此路径不可读取')
