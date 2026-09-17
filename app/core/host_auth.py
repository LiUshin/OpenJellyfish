"""Host-console credentials, independent of every Jellyfish admin account."""
import os
import secrets
import stat
import tempfile
from pathlib import Path

HOST_ID = 'host:owner'  # Not a valid registered user ID.


def key_path():
    # Key-only CLI commands must work without importing backend dependencies
    # or creating checkpoint directories. Docker places this in its data volume.
    root = Path(__file__).resolve().parents[2]
    configured = os.environ.get('JELLYFISH_SUPERADMIN_KEY_FILE', '').strip()
    return root / (configured or 'config/superadmin.key')


def read_key():
    path = key_path()
    # Do not follow a substituted key file or accept a publicly readable key.
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'r') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or (os.name != 'nt' and info.st_mode & 0o077):
            raise ValueError('超管 key 文件权限应为 600')
        key = stream.read(256).strip()
    if not key.startswith('jf_host_') or len(key) != 72:
        raise ValueError('超管 key 文件无效')
    return key


def ensure_key(*, rotate=False):
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rotate:
        try:
            return read_key()
        except FileNotFoundError:
            pass
    key = 'jf_host_' + secrets.token_urlsafe(48)
    if rotate:
        fd, name = tempfile.mkstemp(prefix='.superadmin-', suffix='.key', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(key + '\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    else:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return read_key()
        with os.fdopen(fd, 'w') as stream:
            stream.write(key + '\n'); stream.flush(); os.fsync(stream.fileno())
    return key
