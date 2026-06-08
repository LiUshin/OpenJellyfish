"""消费者媒体/下载短期签名 token。

service 消费者用 sk-svc- 主 key 鉴权 API（走 Authorization header）。但浏览器加载
`<img src>` / `<a download>` / `<iframe src>` 等无法携带自定义 header，必须把凭证放在
URL query 里。直接把 sk-svc- 主 key 放进 URL 不安全（会进 Referer / 日志 / 历史，且一旦
泄露等于交出该 service 全部会话）。

这里签发**短期、且仅绑定单一 (admin_id, service_id, conv_id) 的签名 token**：
- 仅能访问该会话 generated/ 下的文件，越权无效；
- 有过期时间，泄露窗口有限；
- 不暴露 sk-svc- 主 key。

实现用标准库 hmac-sha256，无需引入 PyJWT / itsdangerous。token 格式：``<body>.<sig>``，
body 是 base64url(JSON payload)，sig 是 base64url(HMAC-SHA256(secret, body))。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional

from app.core.settings import ROOT_DIR

# 默认有效期：6 小时。媒体 token 只绑定单一会话的 generated 文件，风险低；给足时长
# 避免长对话中途失效导致图片/下载 401（前端在新建会话时会重新取 token）。
_DEFAULT_TTL = 6 * 3600

_SECRET_FILE = os.path.join(ROOT_DIR, "data", ".consumer_media_secret")
_cached_secret: Optional[bytes] = None


def _get_secret() -> bytes:
    """获取签名密钥：优先环境变量 CONSUMER_MEDIA_SECRET，否则持久化一份随机密钥到
    data/.consumer_media_secret（持久化以保证重启后已签发 token 仍有效）。"""
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret

    env = os.environ.get("CONSUMER_MEDIA_SECRET")
    if env:
        _cached_secret = env.encode("utf-8")
        return _cached_secret

    try:
        if os.path.isfile(_SECRET_FILE):
            with open(_SECRET_FILE, "r", encoding="utf-8") as f:
                s = f.read().strip()
            if s:
                _cached_secret = s.encode("utf-8")
                return _cached_secret
    except OSError:
        pass

    s = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(_SECRET_FILE), exist_ok=True)
        with open(_SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(s)
    except OSError:
        # 写不进盘也不致命：本进程内仍用内存里的密钥，只是重启后旧 token 失效。
        pass
    _cached_secret = s.encode("utf-8")
    return _cached_secret


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def create_media_token(
    admin_id: str, service_id: str, conv_id: str, ttl: int = _DEFAULT_TTL,
) -> str:
    """为 (admin_id, service_id, conv_id) 签发一个短期媒体 token。"""
    payload = {
        "a": admin_id,
        "s": service_id,
        "c": conv_id,
        "exp": int(time.time()) + int(ttl),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = _b64e(raw)
    sig = _b64e(hmac.new(_get_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_media_token(token: str) -> Optional[Dict[str, Any]]:
    """校验 token，返回 {admin_id, service_id, conv_id}，无效/过期返回 None。"""
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    expected = _b64e(
        hmac.new(_get_secret(), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, TypeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return {
        "admin_id": payload.get("a"),
        "service_id": payload.get("s"),
        "conv_id": payload.get("c"),
    }
