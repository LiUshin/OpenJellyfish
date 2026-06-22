"""LiveKit 实时语音插件 —— 令牌签发。

本模块只用标准库,**不引入 livekit-api 依赖**,让 Core 与语音 Worker 解耦:

1. ``mint_livekit_token`` —— 签发 LiveKit 客户端接入令牌(标准 JWT / HS256,
   用 ``LIVEKIT_API_SECRET`` 签名)。浏览器拿它加入 room;agent worker 用
   ``LIVEKIT_API_KEY/SECRET`` 自行注册,无需本令牌。

2. ``create_bridge_token`` / ``verify_bridge_token`` —— Core 与 Worker 之间的
   **窄而稳的桥接凭证**(HMAC-SHA256 签名,绑定 ``(admin_id, conv_id, model, caps)``)。
   Core 把它塞进浏览器 LiveKit 令牌的 ``metadata`` 声明,Worker 加入 room 后从
   参与者 metadata 读出并回调 Core(``/api/voice/live/session`` 与
   ``/api/voice/live/delegate``)。这样 Worker 无需接触管理员真实登录 token,
   且越权(改 admin_id/conv_id)会因签名失效而被拒。

设计取舍见 .cursorrules「LiveKit 语音插件」。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, List, Optional

from app.core.settings import ROOT_DIR

# 浏览器接入令牌默认有效期:2 小时(足够一通长对话;过期前端重新取)。
_LK_TOKEN_TTL = 2 * 3600
# 桥接令牌默认有效期:6 小时(覆盖整通会话生命周期)。
_BRIDGE_TTL = 6 * 3600

_BRIDGE_SECRET_FILE = os.path.join(ROOT_DIR, "data", ".voice_bridge_secret")
_cached_bridge_secret: Optional[bytes] = None


# ── base64url 编解码(无填充)────────────────────────────────────────

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ── LiveKit 接入令牌(标准 JWT / HS256)─────────────────────────────

def mint_livekit_token(
    api_key: str,
    api_secret: str,
    *,
    room: str,
    identity: str,
    name: str = "",
    metadata: str = "",
    ttl: int = _LK_TOKEN_TTL,
    can_publish: bool = True,
    can_subscribe: bool = True,
    can_publish_data: bool = True,
) -> str:
    """签发一个 LiveKit 客户端接入令牌(JWT,HS256)。

    LiveKit 服务端用 ``api_secret`` 验签;``iss`` 必须是对应的 ``api_key``。
    ``metadata`` 会成为该参与者的 metadata,可被房间内其他参与者(含 agent)读取——
    我们用它携带桥接令牌。
    """
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: Dict[str, Any] = {
        "iss": api_key,
        "sub": identity,
        "nbf": now - 5,
        "exp": now + int(ttl),
        "name": name or identity,
        "video": {
            "room": room,
            "roomJoin": True,
            "canPublish": can_publish,
            "canSubscribe": can_subscribe,
            "canPublishData": can_publish_data,
        },
    }
    if metadata:
        payload["metadata"] = metadata

    signing_input = (
        _b64e(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )
    sig = hmac.new(api_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64e(sig)}"


# ── 桥接令牌(Core ↔ Worker 内部凭证)───────────────────────────────

def _get_bridge_secret() -> bytes:
    """桥接令牌签名密钥:优先 ``VOICE_BRIDGE_SECRET`` 环境变量,否则持久化随机密钥。

    Core 与 Worker 必须共享同一密钥。生产中应通过环境变量显式注入(写进
    docker-compose / .env),否则各自进程会各自生成不同的随机密钥导致验签失败。
    """
    global _cached_bridge_secret
    if _cached_bridge_secret is not None:
        return _cached_bridge_secret

    env = os.environ.get("VOICE_BRIDGE_SECRET")
    if env:
        _cached_bridge_secret = env.encode("utf-8")
        return _cached_bridge_secret

    try:
        if os.path.isfile(_BRIDGE_SECRET_FILE):
            with open(_BRIDGE_SECRET_FILE, "r", encoding="utf-8") as f:
                s = f.read().strip()
            if s:
                _cached_bridge_secret = s.encode("utf-8")
                return _cached_bridge_secret
    except OSError:
        pass

    s = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(_BRIDGE_SECRET_FILE), exist_ok=True)
        with open(_BRIDGE_SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(s)
    except OSError:
        pass
    _cached_bridge_secret = s.encode("utf-8")
    return _cached_bridge_secret


def create_bridge_token(
    admin_id: str,
    conv_id: str,
    *,
    model: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    ttl: int = _BRIDGE_TTL,
) -> str:
    """为一通语音会话签发桥接令牌(绑定 admin_id/conv_id/model/caps)。"""
    payload = {
        "a": admin_id,
        "c": conv_id,
        "m": model or "",
        "caps": list(capabilities or []),
        "exp": int(time.time()) + int(ttl),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = _b64e(raw)
    sig = _b64e(hmac.new(_get_bridge_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_bridge_token(token: str) -> Optional[Dict[str, Any]]:
    """校验桥接令牌,返回 ``{admin_id, conv_id, model, capabilities}``;无效/过期返回 None。"""
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    expected = _b64e(
        hmac.new(_get_bridge_secret(), body.encode("ascii"), hashlib.sha256).digest()
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
        "conv_id": payload.get("c"),
        "model": payload.get("m") or None,
        "capabilities": list(payload.get("caps") or []),
    }


# ── 配置探活 ────────────────────────────────────────────────────────

def livekit_server_config() -> Dict[str, str]:
    """读取 LiveKit 服务端配置(供 Core 签发令牌与前端连接)。

    返回 ``{api_key, api_secret, url}``;缺失项为空串。``url`` 是浏览器用的
    公网 wss 地址(``LIVEKIT_URL`` / ``LIVEKIT_WS_URL``)。
    """
    return {
        "api_key": os.environ.get("LIVEKIT_API_KEY", ""),
        "api_secret": os.environ.get("LIVEKIT_API_SECRET", ""),
        "url": os.environ.get("LIVEKIT_URL", "") or os.environ.get("LIVEKIT_WS_URL", ""),
    }


def is_livekit_configured() -> bool:
    cfg = livekit_server_config()
    return bool(cfg["api_key"] and cfg["api_secret"] and cfg["url"])
