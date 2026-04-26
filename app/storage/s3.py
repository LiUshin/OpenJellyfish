"""
S3 storage service — implements StorageService for S3-compatible backends.

Supports AWS S3, MinIO, Cloudflare R2, Alibaba OSS (S3-compatible mode), etc.
"""

import logging
import os
import posixpath
import shutil
import tempfile
from contextlib import contextmanager
from typing import Generator, List, Optional

from app.storage.base import StorageService, FileEntry
from app.storage.config import get_s3_config

_log = logging.getLogger("storage.s3")

_client = None


def _get_client():
    global _client
    if _client is None:
        import boto3
        cfg = get_s3_config()
        kwargs = {"region_name": cfg.region}
        if cfg.endpoint_url:
            kwargs["endpoint_url"] = cfg.endpoint_url
        if cfg.access_key_id:
            kwargs["aws_access_key_id"] = cfg.access_key_id
            kwargs["aws_secret_access_key"] = cfg.secret_access_key
        _client = boto3.client("s3", **kwargs)
    return _client


class S3StorageService(StorageService):

    def __init__(self):
        cfg = get_s3_config()
        self._bucket = cfg.bucket
        self._prefix = cfg.prefix

    def _key(self, user_id: str, path: str) -> str:
        clean = path.lstrip("/").replace("\\", "/")
        parts = [p for p in [self._prefix, user_id, "fs", clean] if p]
        return "/".join(parts)

    def _consumer_key(self, admin_id: str, service_id: str, conv_id: str, path: str) -> str:
        clean = path.lstrip("/").replace("\\", "/")
        parts = [p for p in [self._prefix, admin_id, "svc", service_id, conv_id, "gen", clean] if p]
        return "/".join(parts)

    def _consumer_prefix(self, admin_id: str, service_id: str, conv_id: str) -> str:
        parts = [p for p in [self._prefix, admin_id, "svc", service_id, conv_id, "gen"] if p]
        return "/".join(parts) + "/"

    # ── directory listing ──

    def list_dir(self, user_id: str, path: str = "/") -> List[FileEntry]:
        client = _get_client()
        prefix = self._key(user_id, path)
        if not prefix.endswith("/"):
            prefix += "/"
        items: List[FileEntry] = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix, Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                dir_name = cp["Prefix"][len(prefix):].rstrip("/")
                if dir_name:
                    rel = "/" + posixpath.join(path.strip("/"), dir_name) if path.strip("/") else "/" + dir_name
                    items.append(FileEntry(name=dir_name, path=rel, is_dir=True))
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key == prefix:
                    continue
                name = key[len(prefix):]
                if "/" in name:
                    continue
                rel = "/" + posixpath.join(path.strip("/"), name) if path.strip("/") else "/" + name
                items.append(FileEntry(
                    name=name, path=rel, is_dir=False,
                    size=obj.get("Size", 0),
                    modified_at=obj["LastModified"].isoformat() if obj.get("LastModified") else "",
                ))
        items.sort(key=lambda e: e.name)
        return items

    # ── read ──

    def read_text(self, user_id: str, path: str) -> str:
        return self.read_bytes(user_id, path).decode("utf-8")

    def read_bytes(self, user_id: str, path: str) -> bytes:
        client = _get_client()
        key = self._key(user_id, path)
        try:
            resp = client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except client.exceptions.NoSuchKey:
            raise FileNotFoundError(path)

    # ── write ──

    def write_text(self, user_id: str, path: str, content: str) -> None:
        self.write_bytes(user_id, path, content.encode("utf-8"))

    def write_bytes(self, user_id: str, path: str, data: bytes) -> None:
        client = _get_client()
        key = self._key(user_id, path)
        client.put_object(Bucket=self._bucket, Key=key, Body=data)

    # ── edit ──

    def edit_text(self, user_id: str, path: str, old_string: str, new_string: str) -> None:
        content = self.read_text(user_id, path)
        if old_string not in content:
            raise ValueError("未找到要替换的内容")
        self.write_text(user_id, path, content.replace(old_string, new_string, 1))

    # ── delete ──

    def delete(self, user_id: str, path: str) -> None:
        client = _get_client()
        key = self._key(user_id, path)
        dir_prefix = key if key.endswith("/") else key + "/"
        keys_to_delete = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=dir_prefix):
            for obj in page.get("Contents", []):
                keys_to_delete.append({"Key": obj["Key"]})
        try:
            client.head_object(Bucket=self._bucket, Key=key)
            keys_to_delete.append({"Key": key})
        except Exception:
            pass
        if not keys_to_delete:
            raise FileNotFoundError(path)
        for i in range(0, len(keys_to_delete), 1000):
            batch = keys_to_delete[i:i + 1000]
            client.delete_objects(Bucket=self._bucket, Delete={"Objects": batch})

    # ── move ──

    def move(self, user_id: str, source: str, destination: str) -> str:
        result = self._copy_or_move(user_id, source, destination, delete_source=True)
        return result

    def copy(self, user_id: str, source: str, destination: str) -> str:
        return self._copy_or_move(user_id, source, destination, delete_source=False)

    def _copy_or_move(
        self, user_id: str, source: str, destination: str, *, delete_source: bool,
    ) -> str:
        client = _get_client()
        src_key = self._key(user_id, source)
        dst_key = self._key(user_id, destination)
        user_prefix = "/".join([p for p in [self._prefix, user_id, "fs"] if p]) + "/"

        is_dir = self.is_dir(user_id, source)
        if is_dir:
            src_prefix = src_key.rstrip("/") + "/"
            # Resolve destination: if it points at an existing dir (or ends with /),
            # nest the source basename inside it.
            if dst_key.endswith("/") or self.is_dir(user_id, destination):
                base = posixpath.basename(source.rstrip("/")) or "copy"
                dst_prefix = dst_key.rstrip("/") + "/" + base + "/"
            else:
                dst_prefix = dst_key.rstrip("/") + "/"
            if not delete_source and (
                dst_prefix == src_prefix or dst_prefix.startswith(src_prefix)
            ):
                raise ValueError("不能把文件夹复制/移动到它自己里面")
            # Block overwrite.
            existing = client.list_objects_v2(
                Bucket=self._bucket, Prefix=dst_prefix, MaxKeys=1,
            )
            if existing.get("KeyCount", 0) > 0:
                raise FileExistsError("目标路径已存在")
            paginator = client.get_paginator("list_objects_v2")
            keys_to_delete: list[dict] = []
            for page in paginator.paginate(Bucket=self._bucket, Prefix=src_prefix):
                for obj in page.get("Contents", []):
                    src_obj_key = obj["Key"]
                    rel = src_obj_key[len(src_prefix):]
                    new_key = dst_prefix + rel
                    client.copy_object(
                        Bucket=self._bucket,
                        CopySource={"Bucket": self._bucket, "Key": src_obj_key},
                        Key=new_key,
                    )
                    if delete_source:
                        keys_to_delete.append({"Key": src_obj_key})
            if delete_source and keys_to_delete:
                for i in range(0, len(keys_to_delete), 1000):
                    batch = keys_to_delete[i:i + 1000]
                    client.delete_objects(
                        Bucket=self._bucket, Delete={"Objects": batch},
                    )
            clean = dst_prefix.rstrip("/")
            if clean.startswith(user_prefix):
                clean = clean[len(user_prefix):]
            return "/" + clean
        # Single file.
        if dst_key.endswith("/") or self.is_dir(user_id, destination):
            dst_key = dst_key.rstrip("/") + "/" + posixpath.basename(source.rstrip("/"))
        if self.is_file(user_id, "/" + dst_key[len(user_prefix):]) if dst_key.startswith(user_prefix) else False:
            raise FileExistsError("目标路径已存在")
        client.copy_object(
            Bucket=self._bucket,
            CopySource={"Bucket": self._bucket, "Key": src_key},
            Key=dst_key,
        )
        if delete_source:
            client.delete_object(Bucket=self._bucket, Key=src_key)
        clean = dst_key[len(user_prefix):] if dst_key.startswith(user_prefix) else dst_key
        return "/" + clean

    def walk_files(
        self, user_id: str, path: str,
    ) -> Generator[tuple[str, bytes], None, None]:
        client = _get_client()
        if self.is_file(user_id, path):
            yield posixpath.basename(path.rstrip("/")), self.read_bytes(user_id, path)
            return
        prefix = self._key(user_id, path).rstrip("/") + "/"
        paginator = client.get_paginator("list_objects_v2")
        found = False
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                found = True
                key = obj["Key"]
                rel = key[len(prefix):]
                if not rel:
                    continue
                resp = client.get_object(Bucket=self._bucket, Key=key)
                yield rel, resp["Body"].read()
        if not found:
            raise FileNotFoundError(path)

    # ── queries ──

    def exists(self, user_id: str, path: str) -> bool:
        client = _get_client()
        key = self._key(user_id, path)
        try:
            client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            pass
        prefix = key if key.endswith("/") else key + "/"
        resp = client.list_objects_v2(Bucket=self._bucket, Prefix=prefix, MaxKeys=1)
        return resp.get("KeyCount", 0) > 0

    def is_file(self, user_id: str, path: str) -> bool:
        client = _get_client()
        try:
            client.head_object(Bucket=self._bucket, Key=self._key(user_id, path))
            return True
        except Exception:
            return False

    def is_dir(self, user_id: str, path: str) -> bool:
        client = _get_client()
        prefix = self._key(user_id, path)
        if not prefix.endswith("/"):
            prefix += "/"
        resp = client.list_objects_v2(Bucket=self._bucket, Prefix=prefix, MaxKeys=1)
        return resp.get("KeyCount", 0) > 0

    def makedirs(self, user_id: str, path: str) -> None:
        pass  # S3 doesn't need explicit directory creation

    # ── user init ──

    def ensure_user_dirs(self, user_id: str) -> None:
        pass  # S3 doesn't need explicit directory creation

    # ── HTTP response helpers ──

    def _get_real_path(self, user_id: str, path: str) -> str:
        raise RuntimeError(
            "_get_real_path() is not available in S3 mode. "
            "This should never be called — file_response() uses _get_media_url() first."
        )

    def _get_media_url(self, user_id: str, path: str, expires_in: int = 3600) -> Optional[str]:
        client = _get_client()
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": self._key(user_id, path)},
            ExpiresIn=expires_in,
        )

    # ── consumer operations ──

    def list_consumer_files(
        self, admin_id: str, service_id: str, conv_id: str,
    ) -> list[dict]:
        client = _get_client()
        prefix = self._consumer_prefix(admin_id, service_id, conv_id)
        files = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key == prefix:
                    continue
                rel = key[len(prefix):]
                files.append({"path": rel, "size": obj.get("Size", 0)})
        return files

    def read_consumer_bytes(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> bytes:
        client = _get_client()
        key = self._consumer_key(admin_id, service_id, conv_id, path)
        try:
            resp = client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except Exception:
            raise FileNotFoundError(path)

    def write_consumer_bytes(
        self, admin_id: str, service_id: str, conv_id: str, path: str, data: bytes,
    ) -> None:
        client = _get_client()
        key = self._consumer_key(admin_id, service_id, conv_id, path)
        client.put_object(Bucket=self._bucket, Key=key, Body=data)

    def consumer_exists(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> bool:
        client = _get_client()
        key = self._consumer_key(admin_id, service_id, conv_id, path)
        try:
            client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False

    def _get_consumer_real_path(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> str:
        raise RuntimeError("_get_consumer_real_path() is not available in S3 mode.")

    def _get_consumer_media_url(
        self, admin_id: str, service_id: str, conv_id: str,
        path: str, expires_in: int = 3600,
    ) -> Optional[str]:
        client = _get_client()
        key = self._consumer_key(admin_id, service_id, conv_id, path)
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    # ── script execution ──

    @contextmanager
    def script_execution(
        self, user_id: str, script_path: str,
    ) -> Generator[dict, None, None]:
        tmp_root = tempfile.mkdtemp(prefix="jfb_script_")
        try:
            tmp_scripts = os.path.join(tmp_root, "scripts")
            tmp_docs = os.path.join(tmp_root, "docs")
            tmp_gen = os.path.join(tmp_root, "generated")
            os.makedirs(tmp_scripts, exist_ok=True)
            os.makedirs(tmp_docs, exist_ok=True)
            os.makedirs(tmp_gen, exist_ok=True)

            clean = script_path.replace("\\", "/").lstrip("/")
            try:
                script_bytes = self.read_bytes(user_id, f"/scripts/{clean}")
            except FileNotFoundError:
                yield {"scripts_dir": tmp_scripts, "docs_dir": tmp_docs,
                       "write_dirs": [tmp_scripts, tmp_gen], "error": f"脚本不存在: {clean}"}
                return
            local_script = os.path.join(tmp_scripts, clean)
            os.makedirs(os.path.dirname(local_script), exist_ok=True)
            with open(local_script, "wb") as f:
                f.write(script_bytes)

            yield {
                "scripts_dir": tmp_scripts,
                "docs_dir": tmp_docs,
                "write_dirs": [tmp_scripts, tmp_gen],
            }

            self._upload_generated(tmp_gen,
                                    lambda rel, data: self.write_bytes(user_id, f"/generated/{rel}", data))
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    @contextmanager
    def consumer_script_execution(
        self, admin_id: str, service_id: str, conv_id: str, script_path: str,
    ) -> Generator[dict, None, None]:
        tmp_root = tempfile.mkdtemp(prefix="jfb_script_")
        try:
            tmp_scripts = os.path.join(tmp_root, "scripts")
            tmp_docs = os.path.join(tmp_root, "docs")
            tmp_gen = os.path.join(tmp_root, "generated")
            os.makedirs(tmp_scripts, exist_ok=True)
            os.makedirs(tmp_docs, exist_ok=True)
            os.makedirs(tmp_gen, exist_ok=True)

            clean = script_path.replace("\\", "/").lstrip("/")
            try:
                script_bytes = self.read_bytes(admin_id, f"/scripts/{clean}")
            except FileNotFoundError:
                yield {"scripts_dir": tmp_scripts, "docs_dir": tmp_docs,
                       "write_dirs": [tmp_scripts, tmp_gen], "error": f"脚本不存在: {clean}"}
                return
            local_script = os.path.join(tmp_scripts, clean)
            os.makedirs(os.path.dirname(local_script), exist_ok=True)
            with open(local_script, "wb") as f:
                f.write(script_bytes)

            yield {
                "scripts_dir": tmp_scripts,
                "docs_dir": tmp_docs,
                "write_dirs": [tmp_scripts, tmp_gen],
            }

            self._upload_generated(tmp_gen,
                                    lambda rel, data: self.write_consumer_bytes(
                                        admin_id, service_id, conv_id, rel, data))
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    @staticmethod
    def _upload_generated(tmp_gen: str, write_fn):
        """Walk tmp_gen and call write_fn(rel_path, data) for each file."""
        for dirpath, _, filenames in os.walk(tmp_gen):
            for fname in filenames:
                local_file = os.path.join(dirpath, fname)
                rel = os.path.relpath(local_file, tmp_gen).replace("\\", "/")
                with open(local_file, "rb") as f:
                    write_fn(rel, f.read())
