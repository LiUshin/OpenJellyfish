"""
S3 storage service — implements StorageService for S3-compatible backends.

Supports AWS S3, MinIO, Cloudflare R2, Alibaba OSS (S3-compatible mode), etc.

Reads go through app/storage/cache.py: metadata calls are cached in-process for
a few seconds and object bytes are cached on disk keyed by ETag, so a repeated
read costs at most one conditional GET (304) and often nothing at all.
"""

import logging
import os
import posixpath
import shutil
from contextlib import contextmanager
from typing import Callable, Generator, List, Optional

from app.storage import cache
from app.storage.base import StorageService, FileEntry
from app.storage.config import get_s3_config, sandbox_budget_bytes

_log = logging.getLogger("storage.s3")

_client = None

_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchBucket", "NotFound", "404"}

# Flipped off permanently the first time a backend rejects a conditional GET,
# so an implementation without If-None-Match support costs one failed request
# rather than one per read.
_conditional_get_ok = True


def _get_client():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config as BotoConfig

        cfg = get_s3_config()
        kwargs = {"region_name": cfg.region}
        if cfg.endpoint_url:
            kwargs["endpoint_url"] = cfg.endpoint_url
        if cfg.access_key_id:
            kwargs["aws_access_key_id"] = cfg.access_key_id
            kwargs["aws_secret_access_key"] = cfg.secret_access_key

        common: dict = {"retries": {"max_attempts": 3, "mode": "standard"}}
        if cfg.addressing_style:
            common["s3"] = {"addressing_style": cfg.addressing_style}
        try:
            boto_cfg = BotoConfig(
                request_checksum_calculation=cfg.checksum_mode,
                response_checksum_validation=cfg.checksum_mode,
                **common,
            )
        except TypeError:
            # botocore < 1.36 predates the flexible-checksum options.
            boto_cfg = BotoConfig(**common)

        _client = boto3.client("s3", config=boto_cfg, **kwargs)
    return _client


def _client_error():
    from botocore.exceptions import ClientError
    return ClientError


def _status_of(exc) -> int:
    return (
        getattr(exc, "response", {})
        .get("ResponseMetadata", {})
        .get("HTTPStatusCode", 0)
    )


def _is_not_found(exc) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in _NOT_FOUND_CODES or _status_of(exc) == 404


def _clean_etag(raw) -> str:
    return (raw or "").strip('"')


def _snapshot_tree(root: str) -> dict[str, tuple[int, int]]:
    """Record (size, mtime_ns) per relative path so we can detect writes."""
    snap: dict[str, tuple[int, int]] = {}
    for dirpath, _dirs, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace("\\", "/")
            snap[rel] = (st.st_size, st.st_mtime_ns)
    return snap


class S3StorageService(StorageService):

    def __init__(self):
        cfg = get_s3_config()
        self._bucket = cfg.bucket
        self._prefix = cfg.prefix

    def _key(self, user_id: str, path: str) -> str:
        clean = path.lstrip("/").replace("\\", "/")
        parts = [p for p in [self._prefix, user_id, "fs", clean] if p]
        return "/".join(parts)

    def _user_prefix(self, user_id: str) -> str:
        return "/".join([p for p in [self._prefix, user_id, "fs"] if p]) + "/"

    def _consumer_key(self, admin_id: str, service_id: str, conv_id: str, path: str) -> str:
        clean = path.lstrip("/").replace("\\", "/")
        parts = [p for p in [self._prefix, admin_id, "svc", service_id, conv_id, "gen", clean] if p]
        return "/".join(parts)

    def _consumer_prefix(self, admin_id: str, service_id: str, conv_id: str) -> str:
        parts = [p for p in [self._prefix, admin_id, "svc", service_id, conv_id, "gen"] if p]
        return "/".join(parts) + "/"

    # ──────────────── cached S3 primitives ────────────────

    def _head(self, key: str) -> Optional[dict]:
        """Return {'etag','size','last_modified'} or None. Negative-cached."""
        cached = cache.meta_get(("head", key))
        if cached is not None:
            return None if cache.is_missing(cached) else cached
        try:
            resp = _get_client().head_object(Bucket=self._bucket, Key=key)
        except Exception:
            cache.meta_put(("head", key), cache.meta_missing())
            return None
        info = {
            "etag": _clean_etag(resp.get("ETag")),
            "size": resp.get("ContentLength", 0),
            "last_modified": (
                resp["LastModified"].isoformat() if resp.get("LastModified") else ""
            ),
        }
        cache.meta_put(("head", key), info)
        cache.set_etag_hint(key, info["etag"])
        return info

    def _list_delim(self, prefix: str) -> tuple[list[str], list[dict]]:
        """One level of listing: (common prefixes, objects)."""
        cached = cache.meta_get(("list", prefix))
        if cached is not None:
            return cached
        dirs: list[str] = []
        files: list[dict] = []
        paginator = _get_client().get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=self._bucket, Prefix=prefix, Delimiter="/",
        ):
            for cp in page.get("CommonPrefixes", []):
                dirs.append(cp["Prefix"])
            for obj in page.get("Contents", []):
                files.append(self._obj_summary(obj))
        result = (dirs, files)
        cache.meta_put(("list", prefix), result)
        return result

    def _list_recursive(self, prefix: str) -> list[dict]:
        cached = cache.meta_get(("listr", prefix))
        if cached is not None:
            return cached
        objects: list[dict] = []
        paginator = _get_client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                objects.append(self._obj_summary(obj))
        cache.meta_put(("listr", prefix), objects)
        return objects

    @staticmethod
    def _obj_summary(obj: dict) -> dict:
        return {
            "Key": obj["Key"],
            "Size": obj.get("Size", 0),
            "ETag": _clean_etag(obj.get("ETag")),
            "LastModified": (
                obj["LastModified"].isoformat() if obj.get("LastModified") else ""
            ),
        }

    def _prefix_has_objects(self, key: str) -> bool:
        prefix = key if key.endswith("/") else key + "/"
        dirs, files = self._list_delim(prefix)
        return bool(dirs or files)

    def _get_object_bytes(self, key: str) -> bytes:
        """Read an object, serving from the local cache whenever possible."""
        client = _get_client()
        if not cache.cache_enabled():
            try:
                return client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
            except Exception as e:
                if _is_not_found(e):
                    raise FileNotFoundError(key)
                raise

        head = cache.meta_get(("head", key))
        if cache.is_missing(head):
            raise FileNotFoundError(key)
        if head:
            hit = cache.read_cached(key, head.get("etag", ""))
            if hit is not None:
                return hit

        global _conditional_get_ok
        hint = cache.get_etag_hint(key) if _conditional_get_ok else None
        params = {"Bucket": self._bucket, "Key": key}
        if hint:
            params["IfNoneMatch"] = f'"{hint}"'
        try:
            resp = client.get_object(**params)
        except _client_error() as e:
            if _status_of(e) == 304 and hint:
                hit = cache.read_cached(key, hint)
                if hit is not None:
                    cache.meta_put(("head", key), {"etag": hint, "size": len(hit)})
                    return hit
                # Cache entry was evicted between the hint and the read.
                resp = client.get_object(Bucket=self._bucket, Key=key)
            elif _is_not_found(e):
                cache.meta_put(("head", key), cache.meta_missing())
                raise FileNotFoundError(key)
            elif hint:
                # Some S3-compatible backends reject If-None-Match instead of
                # ignoring it. Stop using conditional GETs and retry plainly.
                _conditional_get_ok = False
                _log.warning(
                    "backend rejected a conditional GET (%s); disabling them", e,
                )
                resp = client.get_object(Bucket=self._bucket, Key=key)
            else:
                raise
        except Exception as e:
            if _is_not_found(e):
                cache.meta_put(("head", key), cache.meta_missing())
                raise FileNotFoundError(key)
            raise

        data = resp["Body"].read()
        etag = _clean_etag(resp.get("ETag"))
        cache.meta_put(("head", key), {"etag": etag, "size": len(data)})
        cache.store_bytes(key, etag, data)
        return data

    def _put_object_bytes(self, key: str, data: bytes) -> None:
        resp = _get_client().put_object(Bucket=self._bucket, Key=key, Body=data)
        cache.invalidate_key(key)
        etag = _clean_etag(resp.get("ETag"))
        if etag:
            cache.meta_put(("head", key), {"etag": etag, "size": len(data)})
            cache.store_bytes(key, etag, data)

    def _upload_local_file(self, key: str, local_path: str) -> None:
        """Multipart streaming upload — never buffers the file in memory."""
        _get_client().upload_file(local_path, self._bucket, key)
        cache.invalidate_key(key)

    def _delete_keys(self, keys: list[str]) -> None:
        """Batch delete, degrading to per-object deletes if unsupported.

        DeleteObjects is one of the few operations that always carries an
        integrity header, so it is the first thing to break on S3-compatible
        backends even when everything else works.
        """
        client = _get_client()
        for i in range(0, len(keys), 1000):
            batch = keys[i:i + 1000]
            try:
                client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": k} for k in batch]},
                )
            except Exception as e:
                _log.warning(
                    "batch delete failed (%s); falling back to per-object delete", e,
                )
                for k in batch:
                    try:
                        client.delete_object(Bucket=self._bucket, Key=k)
                    except Exception as inner:
                        _log.error("failed to delete %s: %s", k, inner)

    # ── directory listing ──

    def list_dir(self, user_id: str, path: str = "/") -> List[FileEntry]:
        prefix = self._key(user_id, path)
        if not prefix.endswith("/"):
            prefix += "/"
        dirs, files = self._list_delim(prefix)
        base = path.strip("/")
        items: List[FileEntry] = []
        for raw in dirs:
            dir_name = raw[len(prefix):].rstrip("/")
            if not dir_name:
                continue
            rel = "/" + posixpath.join(base, dir_name) if base else "/" + dir_name
            items.append(FileEntry(name=dir_name, path=rel, is_dir=True))
        for obj in files:
            key = obj["Key"]
            if key == prefix:
                continue
            name = key[len(prefix):]
            if not name or "/" in name:
                continue
            rel = "/" + posixpath.join(base, name) if base else "/" + name
            items.append(FileEntry(
                name=name, path=rel, is_dir=False,
                size=obj["Size"], modified_at=obj["LastModified"],
            ))
        items.sort(key=lambda e: e.name)
        return items

    # ── read ──

    def read_text(self, user_id: str, path: str) -> str:
        return self.read_bytes(user_id, path).decode("utf-8")

    def read_bytes(self, user_id: str, path: str) -> bytes:
        try:
            return self._get_object_bytes(self._key(user_id, path))
        except FileNotFoundError:
            raise FileNotFoundError(path)

    # ── write ──

    def write_text(self, user_id: str, path: str, content: str) -> None:
        self.write_bytes(user_id, path, content.encode("utf-8"))

    def write_bytes(self, user_id: str, path: str, data: bytes) -> None:
        self._put_object_bytes(self._key(user_id, path), data)

    # ── edit ──

    def edit_text(self, user_id: str, path: str, old_string: str, new_string: str) -> None:
        content = self.read_text(user_id, path)
        if old_string not in content:
            raise ValueError("未找到要替换的内容")
        self.write_text(user_id, path, content.replace(old_string, new_string, 1))

    # ── delete ──

    def delete(self, user_id: str, path: str) -> None:
        key = self._key(user_id, path)
        dir_prefix = key if key.endswith("/") else key + "/"
        keys_to_delete = [o["Key"] for o in self._list_recursive(dir_prefix)]
        if self._head(key) is not None:
            keys_to_delete.append(key)
        if not keys_to_delete:
            raise FileNotFoundError(path)
        self._delete_keys(keys_to_delete)
        cache.invalidate_prefix(key)

    # ── move / copy ──

    def move(self, user_id: str, source: str, destination: str) -> str:
        return self._copy_or_move(user_id, source, destination, delete_source=True)

    def copy(self, user_id: str, source: str, destination: str) -> str:
        return self._copy_or_move(user_id, source, destination, delete_source=False)

    def _copy_or_move(
        self, user_id: str, source: str, destination: str, *, delete_source: bool,
    ) -> str:
        client = _get_client()
        src_key = self._key(user_id, source)
        dst_key = self._key(user_id, destination)
        user_prefix = self._user_prefix(user_id)

        if self.is_dir(user_id, source):
            src_prefix = src_key.rstrip("/") + "/"
            # If the destination points at an existing dir (or ends with /),
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
            if self._list_recursive(dst_prefix):
                raise FileExistsError("目标路径已存在")
            keys_to_delete: list[str] = []
            for obj in self._list_recursive(src_prefix):
                src_obj_key = obj["Key"]
                new_key = dst_prefix + src_obj_key[len(src_prefix):]
                client.copy_object(
                    Bucket=self._bucket,
                    CopySource={"Bucket": self._bucket, "Key": src_obj_key},
                    Key=new_key,
                )
                if delete_source:
                    keys_to_delete.append(src_obj_key)
            if delete_source and keys_to_delete:
                self._delete_keys(keys_to_delete)
                cache.invalidate_prefix(src_prefix)
            cache.invalidate_prefix(dst_prefix)
            clean = dst_prefix.rstrip("/")
            if clean.startswith(user_prefix):
                clean = clean[len(user_prefix):]
            return "/" + clean

        # Single file.
        if dst_key.endswith("/") or self.is_dir(user_id, destination):
            dst_key = dst_key.rstrip("/") + "/" + posixpath.basename(source.rstrip("/"))
        if self._head(dst_key) is not None:
            raise FileExistsError("目标路径已存在")
        client.copy_object(
            Bucket=self._bucket,
            CopySource={"Bucket": self._bucket, "Key": src_key},
            Key=dst_key,
        )
        cache.invalidate_key(dst_key)
        if delete_source:
            client.delete_object(Bucket=self._bucket, Key=src_key)
            cache.invalidate_key(src_key)
        clean = dst_key[len(user_prefix):] if dst_key.startswith(user_prefix) else dst_key
        return "/" + clean

    def walk_files(
        self, user_id: str, path: str,
    ) -> Generator[tuple[str, bytes], None, None]:
        if self.is_file(user_id, path):
            yield posixpath.basename(path.rstrip("/")), self.read_bytes(user_id, path)
            return
        prefix = self._key(user_id, path).rstrip("/") + "/"
        objects = self._list_recursive(prefix)
        if not objects:
            raise FileNotFoundError(path)
        for obj in objects:
            rel = obj["Key"][len(prefix):]
            if not rel:
                continue
            yield rel, self._get_object_bytes(obj["Key"])

    # ── queries ──

    def exists(self, user_id: str, path: str) -> bool:
        key = self._key(user_id, path)
        return self._head(key) is not None or self._prefix_has_objects(key)

    def is_file(self, user_id: str, path: str) -> bool:
        return self._head(self._key(user_id, path)) is not None

    def is_dir(self, user_id: str, path: str) -> bool:
        return self._prefix_has_objects(self._key(user_id, path))

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
        return _get_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": self._key(user_id, path)},
            ExpiresIn=expires_in,
        )

    # ── consumer operations ──

    def list_consumer_files(
        self, admin_id: str, service_id: str, conv_id: str,
    ) -> list[dict]:
        prefix = self._consumer_prefix(admin_id, service_id, conv_id)
        files = []
        for obj in self._list_recursive(prefix):
            rel = obj["Key"][len(prefix):]
            if not rel:
                continue
            files.append({"path": rel, "size": obj["Size"]})
        return files

    def read_consumer_bytes(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> bytes:
        key = self._consumer_key(admin_id, service_id, conv_id, path)
        try:
            return self._get_object_bytes(key)
        except FileNotFoundError:
            raise FileNotFoundError(path)

    def write_consumer_bytes(
        self, admin_id: str, service_id: str, conv_id: str, path: str, data: bytes,
    ) -> None:
        self._put_object_bytes(
            self._consumer_key(admin_id, service_id, conv_id, path), data,
        )

    def consumer_exists(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> bool:
        key = self._consumer_key(admin_id, service_id, conv_id, path)
        return self._head(key) is not None

    def _get_consumer_real_path(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> str:
        raise RuntimeError("_get_consumer_real_path() is not available in S3 mode.")

    def _get_consumer_media_url(
        self, admin_id: str, service_id: str, conv_id: str,
        path: str, expires_in: int = 3600,
    ) -> Optional[str]:
        key = self._consumer_key(admin_id, service_id, conv_id, path)
        return _get_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    # ── script execution ──

    def _materialize_tree(
        self, prefix_key: str, dest: str, *, writable: bool, budget: int,
    ) -> tuple[int, list[str]]:
        """Reproduce an S3 prefix as a real directory tree under `dest`.

        Cached objects are hardlinked (read-only) or copied (writable); only
        cache misses hit the network. Returns (bytes_used, skipped_rel_paths).
        """
        client = _get_client()
        prefix = prefix_key.rstrip("/") + "/"
        dest_real = os.path.realpath(dest)
        used = 0
        skipped: list[str] = []

        for obj in self._list_recursive(prefix):
            key = obj["Key"]
            rel = key[len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            target = os.path.realpath(os.path.join(dest, *rel.split("/")))
            if target != dest_real and not target.startswith(dest_real + os.sep):
                _log.warning("skipping key with traversal-looking name: %s", key)
                continue
            size = obj["Size"]
            if used + size > budget:
                skipped.append(rel)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                cached = cache.object_path_if_cached(key, obj["ETag"])
                if cached is None:
                    cached = cache.store_via_download(
                        key, obj["ETag"], size,
                        lambda tmp, k=key: client.download_file(self._bucket, k, tmp),
                    )
                if cached is None:
                    # Too large for the cache — stream straight into the sandbox.
                    client.download_file(self._bucket, key, target)
                else:
                    cache.materialize(cached, target, writable=writable)
            except Exception as e:
                _log.warning("failed to materialize %s: %s", key, e)
                skipped.append(rel)
                continue
            used += size

        if skipped:
            _log.warning(
                "%d file(s) under %s not materialized (budget %d MB): %s",
                len(skipped), prefix, budget // 1024 // 1024, skipped[:10],
            )
        return used, skipped

    def _upload_tree(
        self, local_dir: str, key_for: Callable[[str], str],
        *, baseline: Optional[dict[str, tuple[int, int]]] = None,
    ) -> int:
        """Upload every file under `local_dir` (streaming, multipart).

        When `baseline` is given, only files that are new or changed relative
        to that snapshot are uploaded.
        """
        count = 0
        for dirpath, _dirs, filenames in os.walk(local_dir):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, local_dir).replace("\\", "/")
                if baseline is not None:
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    if baseline.get(rel) == (st.st_size, st.st_mtime_ns):
                        continue
                try:
                    self._upload_local_file(key_for(rel), full)
                    count += 1
                except Exception as e:
                    _log.error("failed to upload %s: %s", rel, e)
        return count

    @contextmanager
    def _sandbox(
        self,
        scripts_prefix: str,
        docs_prefix: str,
        script_rel: str,
        gen_key_for: Callable[[str], str],
        scripts_key_for: Optional[Callable[[str], str]],
    ) -> Generator[dict, None, None]:
        """Shared implementation for admin and consumer script execution.

        Materializes scripts/ + docs/ locally, hands the dirs to the caller, and
        uploads whatever landed in generated/ afterwards — including when the
        script raised, timed out, or was cancelled.
        """
        root = cache.new_scratch_dir("jfb_script_")
        tmp_scripts = os.path.join(root, "scripts")
        tmp_docs = os.path.join(root, "docs")
        tmp_gen = os.path.join(root, "generated")
        for d in (tmp_scripts, tmp_docs, tmp_gen):
            os.makedirs(d, exist_ok=True)

        ctx = {
            "scripts_dir": tmp_scripts,
            "docs_dir": tmp_docs,
            "write_dirs": [tmp_scripts, tmp_gen],
        }
        scripts_baseline: Optional[dict[str, tuple[int, int]]] = None
        try:
            script_key = f"{scripts_prefix.rstrip('/')}/{script_rel}"
            if self._head(script_key) is None:
                ctx["error"] = f"脚本不存在: {script_rel}"
                yield ctx
                return

            budget = sandbox_budget_bytes()
            # scripts/ is writable inside the sandbox, so it gets real copies
            # rather than hardlinks into the shared cache. docs/ gets its own
            # budget so a large scripts tree cannot starve it.
            self._materialize_tree(
                scripts_prefix, tmp_scripts, writable=True, budget=budget,
            )
            self._materialize_tree(
                docs_prefix, tmp_docs, writable=False, budget=budget,
            )
            # The script being run is exempt from the budget — without it there
            # is nothing to execute.
            local_script = os.path.join(tmp_scripts, *script_rel.split("/"))
            if not os.path.isfile(local_script):
                os.makedirs(os.path.dirname(local_script), exist_ok=True)
                _get_client().download_file(self._bucket, script_key, local_script)
            scripts_baseline = _snapshot_tree(tmp_scripts)
            yield ctx
        finally:
            try:
                self._upload_tree(tmp_gen, gen_key_for)
                if scripts_key_for is not None and scripts_baseline is not None:
                    self._upload_tree(
                        tmp_scripts, scripts_key_for, baseline=scripts_baseline,
                    )
            except Exception:
                _log.exception("failed to persist script output back to S3")
            shutil.rmtree(root, ignore_errors=True)

    @contextmanager
    def script_execution(
        self, user_id: str, script_path: str,
    ) -> Generator[dict, None, None]:
        clean = script_path.replace("\\", "/").lstrip("/")
        with self._sandbox(
            scripts_prefix=self._key(user_id, "/scripts"),
            docs_prefix=self._key(user_id, "/docs"),
            script_rel=clean,
            gen_key_for=lambda rel: self._key(user_id, f"/generated/{rel}"),
            scripts_key_for=lambda rel: self._key(user_id, f"/scripts/{rel}"),
        ) as ctx:
            yield ctx

    @contextmanager
    def consumer_script_execution(
        self, admin_id: str, service_id: str, conv_id: str, script_path: str,
    ) -> Generator[dict, None, None]:
        clean = script_path.replace("\\", "/").lstrip("/")
        # Consumers may never write back into the admin's scripts/ tree.
        with self._sandbox(
            scripts_prefix=self._key(admin_id, "/scripts"),
            docs_prefix=self._key(admin_id, "/docs"),
            script_rel=clean,
            gen_key_for=lambda rel: self._consumer_key(
                admin_id, service_id, conv_id, rel,
            ),
            scripts_key_for=None,
        ) as ctx:
            yield ctx
