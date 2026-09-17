"""Local encrypted credential storage, outside every user document/download tree."""
import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from Crypto.Cipher import AES


def private_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def identity(data: bytes) -> str:
    if len(data) > 128 * 1024:
        raise ValueError('登录缓存过大')
    auth = json.loads(data)
    tokens = auth.get('tokens') or {}
    if auth.get('auth_mode') != 'chatgpt' or not all(tokens.get(k) for k in ('account_id', 'access_token', 'refresh_token', 'id_token')):
        raise ValueError('需要个人 ChatGPT 登录缓存')
    # This digest is only a change detector for provider-issued credentials. It is
    # never used to authenticate a Jellyfish user or authorize a request.
    body = tokens['id_token'].split('.')[1]
    subject = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))).get('sub')
    if not subject:
        raise ValueError('登录缓存缺少账号主体')
    return hashlib.sha256((str(tokens['account_id']) + '\0' + str(subject)).encode()).hexdigest()


class CredentialVault:
    def __init__(self, root: Path):
        self.root = root / 'credentials'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        path = self.root / 'master.key'
        if not path.exists():
            private_write(path, secrets.token_bytes(32))
        self.key = path.read_bytes()
        if len(self.key) != 32:
            raise RuntimeError('Runtime credential master key is invalid')

    def path(self, profile_id):
        if len(profile_id) != 32 or any(c not in '0123456789abcdef' for c in profile_id):
            raise ValueError('Invalid profile ID')
        return self.root / (profile_id + '.enc')

    def write(self, profile_id, data, *, validator=identity):
        validator(data)
        cipher = AES.new(self.key, AES.MODE_GCM)
        cipher.update(profile_id.encode())
        encrypted, tag = cipher.encrypt_and_digest(data)
        private_write(self.path(profile_id), cipher.nonce + tag + encrypted)

    def read(self, profile_id):
        blob = self.path(profile_id).read_bytes()
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=blob[:16])
        cipher.update(profile_id.encode())
        return cipher.decrypt_and_verify(blob[32:], blob[16:32])

    def delete(self, profile_id):
        self.path(profile_id).unlink(missing_ok=True)
