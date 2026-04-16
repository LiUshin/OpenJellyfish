"""
Rate limiter for WeChat channel — per-user message frequency,
global session cap, and QR generation throttling.
"""

import time
import logging
from collections import defaultdict
from typing import Tuple

log = logging.getLogger("wechat.ratelimit")

# per-user message: max N messages in M seconds
_MSG_LIMIT = 10
_MSG_WINDOW = 60  # seconds

# QR generation: max N per IP/service in M seconds
_QR_LIMIT = 5
_QR_WINDOW = 60

_user_msg_log: dict[str, list[float]] = defaultdict(list)
_qr_gen_log: dict[str, list[float]] = defaultdict(list)


def _prune(timestamps: list[float], window: float) -> list[float]:
    cutoff = time.monotonic() - window
    return [t for t in timestamps if t > cutoff]


def check_message_rate(session_id: str) -> Tuple[bool, str]:
    """
    Check if a session is allowed to process another message.
    Returns (allowed, reason).
    """
    now = time.monotonic()
    ts = _prune(_user_msg_log[session_id], _MSG_WINDOW)
    _user_msg_log[session_id] = ts

    if len(ts) >= _MSG_LIMIT:
        remaining = int(_MSG_WINDOW - (now - ts[0]))
        return False, f"消息太频繁，请 {remaining} 秒后再试"

    ts.append(now)
    return True, "ok"


def check_qr_rate(service_id: str) -> Tuple[bool, str]:
    """
    Check if QR code generation is allowed for this service.
    Returns (allowed, reason).
    """
    now = time.monotonic()
    ts = _prune(_qr_gen_log[service_id], _QR_WINDOW)
    _qr_gen_log[service_id] = ts

    if len(ts) >= _QR_LIMIT:
        remaining = int(_QR_WINDOW - (now - ts[0]))
        return False, f"二维码生成太频繁，请 {remaining} 秒后再试"

    ts.append(now)
    return True, "ok"


def cleanup_stale_entries():
    """Remove entries older than their windows. Called periodically."""
    now = time.monotonic()
    for key in list(_user_msg_log.keys()):
        _user_msg_log[key] = _prune(_user_msg_log[key], _MSG_WINDOW)
        if not _user_msg_log[key]:
            del _user_msg_log[key]
    for key in list(_qr_gen_log.keys()):
        _qr_gen_log[key] = _prune(_qr_gen_log[key], _QR_WINDOW)
        if not _qr_gen_log[key]:
            del _qr_gen_log[key]
