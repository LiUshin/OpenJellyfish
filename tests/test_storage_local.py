"""
Local storage backend regression tests — run with:

    .venv/bin/python tests/test_storage_local.py

Covers the contract that consumer_agent._norm_script_path now relies on
(storage.is_file instead of a raw os.path.realpath check) plus the local
script sandbox, so local mode cannot silently drift from S3 mode.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["STORAGE_BACKEND"] = "local"

from app.storage import local as localmod  # noqa: E402

_failures: list[str] = []
_passes = 0


def check(cond, label):
    global _passes
    if cond:
        _passes += 1
    else:
        _failures.append(label)
        print(f"  FAIL: {label}")


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def main():
    tmp = tempfile.mkdtemp(prefix="localtest_")
    localmod.USERS_DIR = os.path.join(tmp, "users")
    fs = os.path.join(localmod.USERS_DIR, "alice", "filesystem")

    write(os.path.join(fs, "scripts", "run.py"), "print('hi')\n")
    write(os.path.join(fs, "scripts", "scripts", "nested.py"), "print('nested')\n")
    write(os.path.join(fs, "docs", "a.txt"), "hello")
    os.makedirs(os.path.join(fs, "generated"), exist_ok=True)
    write(os.path.join(tmp, "secret.txt"), "do not read")

    svc = localmod.LocalStorageService()

    # ── the is_file contract _norm_script_path depends on ──
    check(svc.is_file("alice", "/scripts/run.py"),
          "is_file true for an existing script")
    check(svc.is_file("alice", "/scripts/scripts/nested.py"),
          "is_file true for a literal nested scripts/ path")
    check(not svc.is_file("alice", "/scripts/scripts/run.py"),
          "is_file false when only the stripped path exists")
    check(not svc.is_file("alice", "/scripts/ghost.py"),
          "is_file false for a missing script")
    # Traversal must return False rather than raising — _norm_script_path
    # treats an exception and a miss the same way, but callers elsewhere do not.
    check(not svc.is_file("alice", "/scripts/../../../secret.txt"),
          "is_file false (no raise) for a traversal attempt")

    # ── basic ops ──
    check(svc.read_text("alice", "/docs/a.txt") == "hello", "read_text works")
    check(svc.is_dir("alice", "/docs"), "is_dir true for a real dir")
    check(not svc.is_dir("alice", "/docs/a.txt"), "is_dir false for a file")
    names = sorted(e.name for e in svc.list_dir("alice", "/"))
    check(names == ["docs", "generated", "scripts"], f"list_dir root: {names}")

    # ── sandbox yields the real dirs and survives an exception ──
    with svc.script_execution("alice", "run.py") as ctx:
        check(os.path.isfile(os.path.join(ctx["scripts_dir"], "run.py")),
              "local sandbox exposes the real scripts dir")
        check(os.path.isfile(os.path.join(ctx["docs_dir"], "a.txt")),
              "local sandbox exposes the real docs dir")
    try:
        with svc.script_execution("alice", "run.py") as ctx:
            write(os.path.join(ctx["write_dirs"][1], "partial.txt"), "half done")
            raise TimeoutError("boom")
    except TimeoutError:
        pass
    check(os.path.isfile(os.path.join(fs, "generated", "partial.txt")),
          "local generated output survives a failing script")

    # ── consumer sandbox is a scratch replica, never the admin's workspace ──
    conv_gen_dir = os.path.join(
        localmod.USERS_DIR, "alice", "services", "svc1",
        "conversations", "c1", "generated",
    )
    with svc.consumer_script_execution("alice", "svc1", "c1", "run.py") as ctx:
        check(os.path.realpath(ctx["scripts_dir"])
              != os.path.realpath(os.path.join(fs, "scripts")),
              "consumer scripts_dir is not the admin's real scripts dir")
        check(os.path.isfile(os.path.join(ctx["scripts_dir"], "run.py")),
              "consumer sandbox has the script")
        check(os.path.isfile(os.path.join(ctx["docs_dir"], "a.txt")),
              "consumer sandbox has the admin docs")
        # cwd-relative conventions must match admin runs and the S3 backend.
        rel_doc = os.path.join(ctx["scripts_dir"], "..", "docs", "a.txt")
        check(os.path.isfile(rel_doc), "../docs resolves inside the sandbox")
        write(os.path.join(ctx["scripts_dir"], "..", "generated", "chart.png"), "png")
        # Simulate a malicious script writing next to itself.
        write(os.path.join(ctx["scripts_dir"], "run.py"), "print('pwned')\n")
        write(os.path.join(ctx["scripts_dir"], "backdoor.py"), "evil\n")

    check(os.path.isfile(os.path.join(conv_gen_dir, "chart.png")),
          "consumer output lands in the conversation dir")
    with open(os.path.join(fs, "scripts", "run.py")) as f:
        check(f.read() == "print('hi')\n",
              "consumer script cannot overwrite an admin script")
    check(not os.path.exists(os.path.join(fs, "scripts", "backdoor.py")),
          "consumer script cannot plant a new file in admin scripts/")
    scratch = os.path.join(localmod.USERS_DIR, "alice", ".scratch")
    check(os.listdir(scratch) == [], "scratch dir is emptied after the run")

    # Consumer output must survive a failing script, same as admin/S3.
    try:
        with svc.consumer_script_execution("alice", "svc1", "c1", "run.py") as ctx:
            write(os.path.join(ctx["write_dirs"][1], "partial.png"), "half")
            raise TimeoutError("boom")
    except TimeoutError:
        pass
    check(os.path.isfile(os.path.join(conv_gen_dir, "partial.png")),
          "consumer output survives a failing script")

    # Docs are hardlinked, so a large corpus costs nothing per run.
    with svc.consumer_script_execution("alice", "svc1", "c1", "run.py") as ctx:
        linked = os.path.join(ctx["docs_dir"], "a.txt")
        check(os.stat(linked).st_ino == os.stat(os.path.join(fs, "docs", "a.txt")).st_ino,
              "docs are hardlinked rather than copied")

    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{_passes} passed, {len(_failures)} failed")
    if _failures:
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
