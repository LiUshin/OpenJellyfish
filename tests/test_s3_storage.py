"""
S3 storage backend tests — run with:

    .venv/bin/python tests/test_s3_storage.py

Uses an in-memory fake S3 client (no network, no MinIO) so the ETag cache,
sandbox materialization and upload-on-failure paths can be asserted exactly,
including how many S3 calls each operation costs.
"""

import hashlib
import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["STORAGE_BACKEND"] = "s3"
os.environ["S3_BUCKET"] = "test-bucket"
os.environ["S3_PREFIX"] = "oj"

from botocore.exceptions import ClientError  # noqa: E402

from app.storage import cache  # noqa: E402
from app.storage import s3 as s3mod  # noqa: E402

_failures: list[str] = []
_passes = 0


def check(cond, label):
    global _passes
    if cond:
        _passes += 1
    else:
        _failures.append(label)
        print(f"  FAIL: {label}")


def _err(code, status):
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "op",
    )


class FakePaginator:
    def __init__(self, store):
        self._store = store

    def paginate(self, Bucket=None, Prefix="", Delimiter=None, **_kw):
        self._store.calls["list"] += 1
        contents = []
        common = set()
        for key in sorted(self._store.objects):
            if not key.startswith(Prefix):
                continue
            rest = key[len(Prefix):]
            if Delimiter and Delimiter in rest:
                common.add(Prefix + rest.split(Delimiter, 1)[0] + Delimiter)
                continue
            data = self._store.objects[key]
            contents.append({
                "Key": key,
                "Size": len(data),
                "ETag": f'"{self._store.etag(data)}"',
                "LastModified": None,
            })
        yield {
            "Contents": contents,
            "CommonPrefixes": [{"Prefix": p} for p in sorted(common)],
        }


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls = {"get": 0, "get304": 0, "head": 0, "put": 0,
                      "list": 0, "download": 0, "upload": 0}

    @staticmethod
    def etag(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def put_object(self, Bucket=None, Key=None, Body=b""):
        self.calls["put"] += 1
        self.objects[Key] = Body
        return {"ETag": f'"{self.etag(Body)}"'}

    def head_object(self, Bucket=None, Key=None):
        self.calls["head"] += 1
        if Key not in self.objects:
            raise _err("404", 404)
        data = self.objects[Key]
        return {"ETag": f'"{self.etag(data)}"', "ContentLength": len(data),
                "LastModified": None}

    def get_object(self, Bucket=None, Key=None, IfNoneMatch=None):
        self.calls["get"] += 1
        if Key not in self.objects:
            raise _err("NoSuchKey", 404)
        data = self.objects[Key]
        tag = self.etag(data)
        if IfNoneMatch and IfNoneMatch.strip('"') == tag:
            self.calls["get304"] += 1
            raise _err("304", 304)
        return {"ETag": f'"{tag}"', "Body": io.BytesIO(data),
                "ContentLength": len(data)}

    def delete_object(self, Bucket=None, Key=None):
        self.objects.pop(Key, None)

    def delete_objects(self, Bucket=None, Delete=None):
        for item in (Delete or {}).get("Objects", []):
            self.objects.pop(item["Key"], None)

    def copy_object(self, Bucket=None, CopySource=None, Key=None):
        self.objects[Key] = self.objects[CopySource["Key"]]

    def get_paginator(self, _name):
        return FakePaginator(self)

    def download_file(self, bucket, key, filename):
        self.calls["download"] += 1
        with open(filename, "wb") as f:
            f.write(self.objects[key])

    def upload_file(self, filename, bucket, key):
        self.calls["upload"] += 1
        with open(filename, "rb") as f:
            self.objects[key] = f.read()


def reset(fake, cache_root):
    """Point the cache at a scratch dir and clear all in-process state."""
    s3mod._client = fake
    cache._CACHE_ROOT = cache_root
    cache._OBJECTS_DIR = os.path.join(cache_root, "objects")
    cache._SCRATCH_DIR = os.path.join(cache_root, "scratch")
    cache._meta.clear()
    cache._etag_hint.clear()
    cache._total_bytes = None
    os.makedirs(cache._OBJECTS_DIR, exist_ok=True)


K = "oj/alice/fs"


def config_checks():
    """Endpoint-driven defaults for R2 and other S3-compatible backends."""
    from app.storage import config as cfgmod

    def resolve(**env):
        for k in ("S3_ENDPOINT_URL", "S3_REGION", "S3_CHECKSUM_MODE",
                  "S3_ADDRESSING_STYLE"):
            os.environ.pop(k, None)
        os.environ.update(env)
        cfgmod._S3_CONFIG = None
        return cfgmod.get_s3_config()

    aws = resolve()
    check(aws.region == "us-east-1", "AWS default region")
    check(aws.checksum_mode == "when_supported",
          "AWS keeps boto3 default integrity checksums")

    r2 = resolve(S3_ENDPOINT_URL="https://abc123.r2.cloudflarestorage.com")
    check(r2.region == "auto", "R2 endpoint implies region=auto")
    check(r2.checksum_mode == "when_required",
          "custom endpoint implies conservative checksums")

    minio = resolve(S3_ENDPOINT_URL="http://minio:9000",
                    S3_ADDRESSING_STYLE="path")
    check(minio.region == "us-east-1", "non-R2 endpoint keeps the default region")
    check(minio.addressing_style == "path", "addressing style is configurable")

    forced = resolve(S3_ENDPOINT_URL="https://abc.r2.cloudflarestorage.com",
                     S3_REGION="wnam", S3_CHECKSUM_MODE="when_supported")
    check(forced.region == "wnam", "explicit region overrides the R2 default")
    check(forced.checksum_mode == "when_supported",
          "explicit checksum mode overrides the endpoint heuristic")

    resolve()
    cfgmod._S3_CONFIG = None


def main():
    tmp = tempfile.mkdtemp(prefix="s3test_")
    fake = FakeS3()
    reset(fake, os.path.join(tmp, "cache"))
    svc = s3mod.S3StorageService()

    # ── basic round trip ──
    svc.write_text("alice", "/docs/a.txt", "hello")
    check(fake.objects[f"{K}/docs/a.txt"] == b"hello", "write_text stores object")
    check(svc.read_text("alice", "/docs/a.txt") == "hello", "read_text round trip")

    # A write primes the cache, so the read that follows costs zero GETs.
    check(fake.calls["get"] == 0, "read after write served from cache (0 GET)")

    # ── cache: repeated reads within the TTL cost nothing ──
    cache._meta.clear()  # simulate TTL expiry, keep the ETag hint
    svc.read_bytes("alice", "/docs/a.txt")
    check(fake.calls["get304"] == 1, "expired metadata revalidates with a 304")
    before = fake.calls["get"]
    for _ in range(5):
        svc.read_bytes("alice", "/docs/a.txt")
    check(fake.calls["get"] == before, "reads inside TTL cost 0 GET")

    # ── cache invalidation on overwrite ──
    svc.write_text("alice", "/docs/a.txt", "updated")
    check(svc.read_text("alice", "/docs/a.txt") == "updated", "overwrite invalidates cache")

    # ── listing / queries ──
    svc.write_text("alice", "/docs/sub/b.txt", "bee")
    names = sorted(e.name for e in svc.list_dir("alice", "/docs"))
    check(names == ["a.txt", "sub"], f"list_dir mixes files and dirs: {names}")
    check(svc.is_file("alice", "/docs/a.txt"), "is_file true for object")
    check(not svc.is_file("alice", "/docs/sub"), "is_file false for prefix")
    check(svc.is_dir("alice", "/docs/sub"), "is_dir true for prefix")
    check(svc.exists("alice", "/docs/sub"), "exists true for prefix")
    check(not svc.exists("alice", "/docs/nope.txt"), "exists false for missing")

    try:
        svc.read_bytes("alice", "/docs/nope.txt")
        check(False, "missing read raises FileNotFoundError")
    except FileNotFoundError:
        check(True, "missing read raises FileNotFoundError")

    # ── script sandbox: docs are actually materialized ──
    svc.write_text("alice", "/scripts/run.py", "print('hi')\n")
    svc.write_text("alice", "/scripts/lib/helper.py", "X = 1\n")
    with svc.script_execution("alice", "run.py") as ctx:
        check("error" not in ctx, "existing script has no error")
        check(os.path.isfile(os.path.join(ctx["scripts_dir"], "run.py")),
              "script materialized")
        check(os.path.isfile(os.path.join(ctx["scripts_dir"], "lib", "helper.py")),
              "sibling script materialized (cross-import works)")
        docs_a = os.path.join(ctx["docs_dir"], "a.txt")
        check(os.path.isfile(docs_a), "docs materialized into sandbox")
        with open(docs_a) as f:
            check(f.read() == "updated", "materialized doc has current content")
        check(os.path.isfile(os.path.join(ctx["docs_dir"], "sub", "b.txt")),
              "nested docs materialized")
        with open(os.path.join(ctx["write_dirs"][1], "out.csv"), "w") as f:
            f.write("a,b\n")
    check(fake.objects.get(f"{K}/generated/out.csv") == b"a,b\n",
          "generated file uploaded on clean exit")

    # ── second run: cached objects, no re-download ──
    downloads_before = fake.calls["download"]
    with svc.script_execution("alice", "run.py") as ctx:
        check(os.path.isfile(os.path.join(ctx["docs_dir"], "a.txt")),
              "docs materialized on second run")
    check(fake.calls["download"] == downloads_before,
          "second run re-uses the local cache (0 downloads)")

    # ── the bug that mattered: output survives a failing script ──
    try:
        with svc.script_execution("alice", "run.py") as ctx:
            with open(os.path.join(ctx["write_dirs"][1], "partial.txt"), "w") as f:
                f.write("half done")
            raise TimeoutError("script timed out")
    except TimeoutError:
        pass
    check(fake.objects.get(f"{K}/generated/partial.txt") == b"half done",
          "generated file uploaded even when the script raises")

    # ── scripts/ writeback parity with local mode ──
    with svc.script_execution("alice", "run.py") as ctx:
        with open(os.path.join(ctx["scripts_dir"], "made.py"), "w") as f:
            f.write("# generated\n")
    check(fake.objects.get(f"{K}/scripts/made.py") == b"# generated\n",
          "script-created file in scripts/ is written back")

    # ── unchanged scripts are not re-uploaded ──
    uploads_before = fake.calls["upload"]
    with svc.script_execution("alice", "run.py"):
        pass
    check(fake.calls["upload"] == uploads_before,
          "untouched scripts are not re-uploaded")

    # ── missing script still reports an error ──
    with svc.script_execution("alice", "ghost.py") as ctx:
        check("error" in ctx, "missing script yields an error")

    # ── consumer sandbox writes into the conversation, not admin scripts ──
    with svc.consumer_script_execution("alice", "svc1", "c1", "run.py") as ctx:
        check(os.path.isfile(os.path.join(ctx["docs_dir"], "a.txt")),
              "consumer sandbox gets admin docs")
        with open(os.path.join(ctx["scripts_dir"], "evil.py"), "w") as f:
            f.write("nope")
        with open(os.path.join(ctx["write_dirs"][1], "chart.png"), "wb") as f:
            f.write(b"\x89PNG")
    check(fake.objects.get("oj/alice/svc/svc1/c1/gen/chart.png") == b"\x89PNG",
          "consumer output lands in the conversation prefix")
    check(f"{K}/scripts/evil.py" not in fake.objects,
          "consumer cannot write back into the admin scripts tree")

    # ── large objects bypass the cache but still reach the sandbox ──
    os.environ["S3_CACHE_MAX_FILE_MB"] = "0"
    cache._meta.clear()
    big = b"x" * (1024 * 64)
    svc.write_bytes("alice", "/docs/big.bin", big)
    with svc.script_execution("alice", "run.py") as ctx:
        target = os.path.join(ctx["docs_dir"], "big.bin")
        check(os.path.isfile(target) and os.path.getsize(target) == len(big),
              "oversized object streamed straight into the sandbox")
    os.environ.pop("S3_CACHE_MAX_FILE_MB")

    # ── budget cap ──
    os.environ["SANDBOX_MAX_MB"] = "0"
    cache._meta.clear()
    with svc.script_execution("alice", "run.py") as ctx:
        check(not os.path.isfile(os.path.join(ctx["docs_dir"], "a.txt")),
              "docs skipped once the sandbox budget is exhausted")
        check(os.path.isfile(os.path.join(ctx["scripts_dir"], "run.py")),
              "the script being run is exempt from the budget")
    os.environ.pop("SANDBOX_MAX_MB")

    # ── move / delete keep the cache honest ──
    cache._meta.clear()
    svc.write_text("alice", "/docs/m.txt", "movable")
    svc.move("alice", "/docs/m.txt", "/docs/moved.txt")
    check(svc.read_text("alice", "/docs/moved.txt") == "movable", "move keeps content")
    check(not svc.is_file("alice", "/docs/m.txt"), "move removes the source")
    svc.delete("alice", "/docs/moved.txt")
    check(not svc.exists("alice", "/docs/moved.txt"), "delete removes the object")

    svc.write_text("alice", "/docs/tree/x.txt", "1")
    svc.write_text("alice", "/docs/tree/y.txt", "2")
    svc.delete("alice", "/docs/tree")
    check(not svc.exists("alice", "/docs/tree"), "recursive delete clears the prefix")

    # ── zip streaming ──
    svc.write_text("alice", "/docs/z/one.txt", "1")
    svc.write_text("alice", "/docs/z/two.txt", "2")
    got = dict(svc.walk_files("alice", "/docs/z"))
    check(got == {"one.txt": b"1", "two.txt": b"2"}, f"walk_files yields tree: {got}")

    # ── S3-compatible backends: batch delete may be unsupported ──
    svc.write_text("alice", "/docs/bd/one.txt", "1")
    svc.write_text("alice", "/docs/bd/two.txt", "2")
    original_batch = fake.delete_objects
    singles = []

    def refuse_batch(**_kw):
        raise _err("InvalidRequest", 400)

    def count_single(Bucket=None, Key=None):
        singles.append(Key)
        fake.objects.pop(Key, None)

    fake.delete_objects = refuse_batch
    fake.delete_object = count_single
    svc.delete("alice", "/docs/bd")
    fake.delete_objects = original_batch
    check(len(singles) == 2 and not svc.exists("alice", "/docs/bd"),
          f"batch delete falls back to per-object deletes: {singles}")

    # ── S3-compatible backends: If-None-Match may be rejected ──
    cache._meta.clear()
    original_get = fake.get_object
    rejected = {"n": 0}

    def refuse_conditional(Bucket=None, Key=None, IfNoneMatch=None):
        if IfNoneMatch:
            rejected["n"] += 1
            raise _err("NotImplemented", 501)
        return original_get(Bucket=Bucket, Key=Key)

    fake.get_object = refuse_conditional
    svc.write_text("alice", "/docs/cond.txt", "payload")
    cache._meta.clear()
    check(svc.read_text("alice", "/docs/cond.txt") == "payload",
          "read still works when conditional GET is rejected")
    cache._meta.clear()
    svc.read_text("alice", "/docs/cond.txt")
    check(rejected["n"] == 1,
          f"conditional GETs are disabled after one rejection (tried {rejected['n']}x)")
    fake.get_object = original_get
    s3mod._conditional_get_ok = True

    shutil.rmtree(tmp, ignore_errors=True)
    config_checks()

    print(f"\n{_passes} passed, {len(_failures)} failed")
    if _failures:
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
