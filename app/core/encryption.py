"""
AES-256-GCM encryption for API key storage.

Master key is auto-generated on first use and stored in data/encryption.key.
Can be overridden via ENCRYPTION_KEY env var (hex-encoded 32-byte key).
"""

import os
import base64

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

from app.core.settings import ROOT_DIR

_ENC_PREFIX = "ENC:"
from typing import Optional

_master_key: Optional[bytes] = None


def _get_master_key() -> bytes:
    global _master_key
    if _master_key is not None:
        return _master_key

    env_key = os.getenv("ENCRYPTION_KEY", "")
    if env_key:
        _master_key = bytes.fromhex(env_key)
        return _master_key

    key_file = os.path.join(ROOT_DIR, "data", "encryption.key")
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            _master_key = f.read()
        return _master_key

    _master_key = get_random_bytes(32)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        with open(key_file, "wb") as f:
            f.write(_master_key)
    finally:
        os.umask(old_umask)
    return _master_key


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string, return prefixed base64 token."""
    if not plaintext:
        return ""
    key = _get_master_key()
    cipher = AES.new(key, AES.MODE_GCM)
    ct, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    blob = cipher.nonce + tag + ct  # 16 + 16 + N
    return _ENC_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_value(token: str) -> str:
    """Decrypt a prefixed token back to plaintext."""
    if not token:
        return ""
    if not token.startswith(_ENC_PREFIX):
        return token  # plaintext fallback (migration)
    raw = token[len(_ENC_PREFIX):]
    key = _get_master_key()
    data = base64.b64decode(raw)
    nonce, tag, ct = data[:16], data[16:32], data[32:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ct, tag).decode("utf-8")


def mask_secret(value: str) -> str:
    """Return a masked display version: first 3 + last 4 chars visible."""
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:3] + "***" + value[-4:]
