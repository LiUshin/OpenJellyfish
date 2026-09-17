"""Validated, conversation-scoped input files and native generated images."""
import base64
import binascii
import re
import uuid
from pathlib import Path
from fastapi import HTTPException
from app.runtime.files import safe_read, digest, image_mime, MAX_FILE

MAX_INPUT_FILE = 8 * 1024 * 1024
MAX_INPUT_TOTAL = 12 * 1024 * 1024


def decode_inputs(items):
    if not isinstance(items, list) or len(items) > 5:
        raise HTTPException(400, '每条消息最多 5 个附件')
    output, total = [], 0
    for item in items:
        name, value = item.get('name'), item.get('data_url')
        if not isinstance(name, str) or not name or len(name.encode('utf-8')) > 180 or any(c in name for c in '/\\\x00\r\n') or name in ('.', '..'):
            raise HTTPException(400, '附件名称无效')
        if not isinstance(value, str) or len(value) > (MAX_INPUT_FILE + 2) // 3 * 4 + 150:
            raise HTTPException(400, '单个附件不能超过 8 MB')
        match = re.fullmatch(r'data:([\w.+-]+/[\w.+-]+)?;base64,([A-Za-z0-9+/=]*)', value)
        if not match:
            raise HTTPException(400, '附件必须使用本地文件的 Base64 data URL')
        try:
            data = base64.b64decode(match[2], validate=True)
            mime = match[1] or 'application/octet-stream'
            if mime.startswith('image/'):
                mime = image_mime(data)
        except (ValueError, binascii.Error, OSError):
            raise HTTPException(400, '附件或图片格式无效')
        total += len(data)
        if len(data) > MAX_INPUT_FILE or total > MAX_INPUT_TOTAL:
            raise HTTPException(400, '附件限制：单个 8 MB，每条消息合计 12 MB')
        output.append({'name': name, 'mime': mime, 'data': data, 'sha256': digest(data), 'size': len(data)})
    return output


def fingerprint(items):
    return [{k: f[k] for k in ('name', 'mime', 'sha256', 'size')} for f in items]


def save_inputs(workspace, run_id, items):
    if not items:
        return []
    directory = workspace / '.jellyfish-inputs'
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise HTTPException(409, '附件目录无效')
    directory.mkdir(exist_ok=True, mode=0o700)
    directory = directory / run_id
    directory.mkdir(mode=0o700)
    result = []
    for f in items:
        aid = uuid.uuid4().hex
        path = directory / (aid + '-' + f['name'])
        with path.open('xb') as stream:
            stream.write(f['data'])
        path.chmod(0o600)
        result.append({**{k: f[k] for k in ('name', 'mime', 'sha256', 'size')}, 'id': aid,
                       'path': path.relative_to(workspace).as_posix()})
    return result


def input_files(workspace, attachments):
    result = []
    for item in attachments:
        path = workspace / item['path']
        if digest(safe_read(workspace, path)) != item['sha256']:
            raise HTTPException(409, '附件已被修改，请重新上传')
        result.append({**item, 'absolute_path': str(path)})
    return result


def native_image(workspace, payload, native_home=None, thread_id=None):
    workspace = workspace.resolve()
    if payload.get('failure') or payload.get('status') in ('failed', 'cancelled'):
        return None
    data = None
    path = payload.get('saved_path')
    if path:
        path = Path(path)
        path = path if path.is_absolute() else workspace / path
        try:
            path.relative_to(workspace)
        except ValueError:
            if native_home is None or not thread_id:
                raise ValueError('图片路径不属于当前会话')
            # Codex saves native images in CODEX_HOME/generated_images/thread_id.
            # Accept that exact conversation subtree, never the whole account HOME.
            path.relative_to(native_home / 'generated_images' / thread_id)
            data = safe_read(native_home, path)
        else:
            image_mime(safe_read(workspace, path))
            return str(path)
    if data is None:
        encoded = payload.get('result')
        if not isinstance(encoded, str) or not encoded or len(encoded) > (MAX_FILE + 2) // 3 * 4 + 100:
            return None
        if encoded.startswith('data:'):
            encoded = encoded.split(',', 1)[-1]
        data = base64.b64decode(encoded, validate=True)
    mime = image_mime(data)
    directory = workspace / 'generated'
    if directory.is_symlink():
        raise ValueError('生成目录不能为链接')
    directory.mkdir(exist_ok=True, mode=0o700)
    suffix = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'}[mime]
    path = directory / (uuid.uuid4().hex + '.' + suffix)
    with path.open('xb') as stream:
        stream.write(data)
    return str(path)
