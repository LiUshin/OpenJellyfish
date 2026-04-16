"""
S3Backend — implements deepagents BackendProtocol for S3-compatible storage.

Follows the official S3-style outline from:
https://docs.langchain.com/oss/python/deepagents/backends#use-a-virtual-filesystem
"""

import fnmatch
import logging
import posixpath
import re
from datetime import datetime, timezone
from typing import List, Optional

from app.storage.config import get_s3_config

_log = logging.getLogger("storage.s3_backend")


def _get_client():
    import boto3
    cfg = get_s3_config()
    kwargs = {"region_name": cfg.region}
    if cfg.endpoint_url:
        kwargs["endpoint_url"] = cfg.endpoint_url
    if cfg.access_key_id:
        kwargs["aws_access_key_id"] = cfg.access_key_id
        kwargs["aws_secret_access_key"] = cfg.secret_access_key
    return boto3.client("s3", **kwargs)


def _get_async_client():
    import aioboto3
    cfg = get_s3_config()
    kwargs = {"region_name": cfg.region}
    if cfg.endpoint_url:
        kwargs["endpoint_url"] = cfg.endpoint_url
    if cfg.access_key_id:
        kwargs["aws_access_key_id"] = cfg.access_key_id
        kwargs["aws_secret_access_key"] = cfg.secret_access_key
    return aioboto3.Session().client("s3", **kwargs)


class S3Backend:
    """BackendProtocol implementation backed by S3.

    External storage: files_update is always None (no LangGraph state updates).
    """

    def __init__(self, bucket: str, prefix: str = ""):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = _get_client()
        return self._client

    def _key(self, path: str) -> str:
        clean = path.lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{clean}"
        return clean

    def _path_from_key(self, key: str) -> str:
        base = self.prefix + "/" if self.prefix else ""
        if key.startswith(base):
            return "/" + key[len(base):]
        return "/" + key

    # ── list ──

    def _list_keys(self, prefix: str):
        """Yield all object summaries under a prefix."""
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            yield from page.get("Contents", [])

    def ls_info(self, path: str) -> list:
        from deepagents.backends.utils import FileInfo

        s3_prefix = self._key(path)
        if not s3_prefix.endswith("/"):
            s3_prefix += "/"

        entries = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=s3_prefix, Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                dir_path = self._path_from_key(cp["Prefix"].rstrip("/"))
                entries.append(FileInfo(path=dir_path, is_dir=True))
            for obj in page.get("Contents", []):
                if obj["Key"] == s3_prefix:
                    continue
                name = obj["Key"][len(s3_prefix):]
                if "/" in name:
                    continue
                file_path = self._path_from_key(obj["Key"])
                entries.append(FileInfo(
                    path=file_path,
                    is_dir=False,
                    size=obj.get("Size", 0),
                    modified_at=obj["LastModified"].isoformat() if obj.get("LastModified") else None,
                ))
        entries.sort(key=lambda e: e.path)
        return entries

    def ls(self, path: str) -> str:
        infos = self.ls_info(path)
        if not infos:
            return f"Error: Directory '{path}' not found or empty"
        lines = []
        for fi in infos:
            name = posixpath.basename(fi.path)
            suffix = "/" if fi.is_dir else ""
            lines.append(f"{name}{suffix}")
        return "\n".join(lines)

    # ── read ──

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        key = self._key(file_path)
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
            raw = resp["Body"].read()
        except Exception:
            return f"Error: File '{file_path}' not found"

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            import base64
            return f"[Binary file, base64-encoded]\n{base64.b64encode(raw).decode()}"

        lines = text.split("\n")
        selected = lines[offset:offset + limit]
        numbered = [f"{i + offset + 1:6d}|{line}" for i, line in enumerate(selected)]
        result = "\n".join(numbered)
        total = len(lines)
        if offset + limit < total:
            result += f"\n... ({total - offset - limit} more lines)"
        return result

    # ── write ──

    def write(self, file_path: str, content: str):
        from deepagents.backends.protocol import WriteResult

        key = self._key(file_path)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content.encode("utf-8"))
        return WriteResult(path=file_path, files_update=None)

    # ── edit ──

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        from deepagents.backends.protocol import EditResult

        key = self._key(file_path)
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
            content = resp["Body"].read().decode("utf-8")
        except Exception:
            return EditResult(error=f"File '{file_path}' not found")

        if old_string not in content:
            return EditResult(error=f"String not found in '{file_path}'")

        if replace_all:
            occurrences = content.count(old_string)
            new_content = content.replace(old_string, new_string)
        else:
            count = content.count(old_string)
            if count > 1:
                return EditResult(error=f"Found {count} occurrences of the string. Use replace_all=True or provide a more specific string.")
            occurrences = 1
            new_content = content.replace(old_string, new_string, 1)

        self.client.put_object(Bucket=self.bucket, Key=key, Body=new_content.encode("utf-8"))
        return EditResult(path=file_path, files_update=None, occurrences=occurrences)

    # ── grep ──

    def grep_raw(self, pattern: str, path: Optional[str] = None, glob_pattern: Optional[str] = None) -> list | str:
        from deepagents.backends.utils import GrepMatch

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Invalid regex pattern: {e}"

        prefix = self._key(path or "/")
        if not prefix.endswith("/"):
            prefix += "/"

        matches = []
        for obj in self._list_keys(prefix):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            file_path = self._path_from_key(key)

            if glob_pattern and not fnmatch.fnmatch(file_path, glob_pattern):
                continue

            try:
                resp = self.client.get_object(Bucket=self.bucket, Key=key)
                content = resp["Body"].read().decode("utf-8")
            except (UnicodeDecodeError, Exception):
                continue

            for i, line in enumerate(content.split("\n"), 1):
                if regex.search(line):
                    matches.append(GrepMatch(path=file_path, line=i, text=line))

            if len(matches) > 500:
                break

        return matches

    # ── glob ──

    def glob_info(self, pattern: str, path: str = "/") -> list:
        from deepagents.backends.utils import FileInfo

        prefix = self._key(path)
        if not prefix.endswith("/"):
            prefix += "/"

        results = []
        for obj in self._list_keys(prefix):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            file_path = self._path_from_key(key)
            if fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(posixpath.basename(file_path), pattern):
                results.append(FileInfo(
                    path=file_path,
                    is_dir=False,
                    size=obj.get("Size", 0),
                    modified_at=obj["LastModified"].isoformat() if obj.get("LastModified") else None,
                ))
        results.sort(key=lambda e: e.path)
        return results

    def glob(self, pattern: str, path: str = "/") -> str:
        infos = self.glob_info(pattern, path)
        if not infos:
            return "No matches found"
        return "\n".join(fi.path for fi in infos)

    # ── async variants (delegate to sync for now) ──

    async def als_info(self, path: str) -> list:
        return self.ls_info(path)

    async def als(self, path: str) -> str:
        return self.ls(path)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        return self.read(file_path, offset, limit)

    async def awrite(self, file_path: str, content: str):
        return self.write(file_path, content)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        return self.edit(file_path, old_string, new_string, replace_all)

    async def agrep_raw(self, pattern: str, path: Optional[str] = None, glob_pattern: Optional[str] = None):
        return self.grep_raw(pattern, path, glob_pattern)

    async def aglob_info(self, pattern: str, path: str = "/") -> list:
        return self.glob_info(pattern, path)

    async def aglob(self, pattern: str, path: str = "/") -> str:
        return self.glob(pattern, path)

    # upload / download (required by BackendProtocol)

    def upload_files(self, files: list, path: str = "/") -> str:
        for f in files:
            name = f.get("name", "unnamed")
            content = f.get("content", "")
            file_path = posixpath.join(path, name) if path != "/" else f"/{name}"
            self.write(file_path, content)
        return f"Uploaded {len(files)} file(s) to {path}"

    async def aupload_files(self, files: list, path: str = "/") -> str:
        return self.upload_files(files, path)

    def download_files(self, paths: list) -> list:
        results = []
        for p in paths:
            key = self._key(p)
            try:
                resp = self.client.get_object(Bucket=self.bucket, Key=key)
                data = resp["Body"].read()
                results.append({"path": p, "content": data.decode("utf-8"), "encoding": "utf-8"})
            except Exception:
                results.append({"path": p, "error": f"File '{p}' not found"})
        return results

    async def adownload_files(self, paths: list) -> list:
        return self.download_files(paths)
