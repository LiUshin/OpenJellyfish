"""Local execution scratch files; product input/output always uses StorageService."""
import hashlib
import io
import os
import stat
from pathlib import Path, PurePosixPath

from PIL import Image

MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 50 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def import_documents(storage, user_id: str, workspace: Path, paths: list[str]):
    baseline = {}
    total = 0
    for raw in dict.fromkeys(paths):
        path = PurePosixPath(raw)
        if not raw.startswith('/docs/') or '..' in path.parts or '\\' in raw:
            raise ValueError("导入文件必须位于 /docs/ 内")
        entries = storage.list_dir(user_id, str(path.parent))
        entry = next((e for e in entries if e.name == path.name and not e.is_dir), None)
        if entry is None or entry.size > MAX_FILE:
            raise ValueError("导入文件不存在或超过 20 MB")
        data = storage.read_bytes(user_id, raw)
        total += len(data)
        if len(data) > MAX_FILE or total > MAX_TOTAL:
            raise ValueError("导入文件超过大小限制（单文件 20 MB，总量 50 MB）")
        rel = raw.lstrip('/')
        dest = workspace / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        baseline[rel] = digest(data)
    return baseline


def safe_read(workspace: Path, path: Path) -> bytes:
    root = workspace.resolve()
    if path.is_absolute():
        try:
            path = root / path.relative_to(workspace.absolute())
        except ValueError:
            pass
    else:
        path = root / path
    # Reject symlinks at every component, including links pointing inside the workspace.
    rel = path.relative_to(root)
    current = root
    for part in rel.parts:
        if part in ('..', '.'):
            raise ValueError("产物路径越界")
        current = current / part
        if current.is_symlink():
            raise ValueError("产物不可为符号链接")
    path.resolve().relative_to(root)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
            raise ValueError("产物不是独立普通文件或超过 20 MB")
        data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise ValueError("产物超过 20 MB")
        return data


def image_mime(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as img:
        fmt = img.format
        if img.width * img.height > 40_000_000:
            raise ValueError('图片超过 4000 万像素')
        img.verify()
    if fmt not in ('PNG', 'JPEG', 'WEBP', 'GIF'):
        raise ValueError("不支持的图片格式")
    return {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp', 'GIF': 'image/gif'}[fmt]


def collect_files(workspace: Path, baseline: dict, image_paths: list[str]):
    workspace = workspace.resolve()
    native = set()
    for value in image_paths:
        path = Path(value)
        data = safe_read(workspace, path)
        image_mime(data)
        native.add(path.relative_to(workspace.resolve()).as_posix())
    output, total, seen = [], 0, 0
    for parent, dirs, files in os.walk(workspace, followlinks=False):
        dirs[:] = [name for name in dirs if not name.startswith('.') and not (Path(parent) / name).is_symlink()]
        for name in files:
            seen += 1
            if seen > 1000:
                raise ValueError("工作区超过 1000 个文件，请缩小任务范围")
            path = Path(parent) / name
            if name.startswith('.'):
                continue
            data = safe_read(workspace, path)
            total += len(data)
            if total > MAX_TOTAL:
                raise ValueError("工作区产物超过 50 MB")
            rel = path.relative_to(workspace).as_posix()
            sha = digest(data)
            if baseline.get(rel) == sha:
                continue
            mime = 'application/octet-stream'
            if rel in native or path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
                mime = image_mime(data)
            output.append((rel, data, sha, mime, rel in native))
    return output
