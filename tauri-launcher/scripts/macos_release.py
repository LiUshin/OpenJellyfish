"""macOS release integrity checks. Ad-hoc integrity is NOT Gatekeeper approval."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import os
import subprocess
import tempfile

MACHO_MAGIC = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}


def macho_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as source:
                if source.read(4) in MACHO_MAGIC:
                    yield path


def run(*args: str):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=300).stdout


def sign_resources(root: Path):
    # Tauri signs the outer app, but Resources includes Python extension modules
    # and Node that are not Tauri sidecars. Seal each Mach-O before the outer seal.
    identity = os.environ.get("APPLE_SIGNING_IDENTITY", "-") or "-"
    if identity != "-":
        raise RuntimeError("Developer ID builds need the documented nested signing/notarization setup; ad-hoc builder cannot silently substitute it")
    count = 0
    for path in macho_files(root):
        run("codesign", "--force", "--sign", "-", str(path))
        run("codesign", "--verify", "--strict", str(path))
        count += 1
    if not count:
        raise RuntimeError("No embedded Mach-O runtimes found")
    print(f"Ad-hoc signed {count} embedded Mach-O files (not notarized)")


def verify_signatures(app: Path, arch: str):
    run("codesign", "--verify", "--deep", "--strict", str(app))
    count = 0
    for path in macho_files(app):
        run("codesign", "--verify", "--strict", str(path))
        run("lipo", str(path), "-verify_arch", arch)
        count += 1
    if not count:
        raise RuntimeError("No Mach-O files in application")
    return count


def verify_app(app: Path, arch: str):
    count = verify_signatures(app, arch)
    resources = app / "Contents/Resources"
    # Only import bundled dependencies, never start services or write user data.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    subprocess.run([
        str(resources / "python/bin/python3"), "-B", "-c",
        "import ssl, sqlite3, pydantic_core; from Crypto.Cipher import AES; print('Bundled Python OK')",
    ], check=True, env=env, cwd=resources, timeout=300)
    run(str(resources / "node/bin/node"), "-e", "require('crypto').randomBytes(16); console.log('Bundled Node OK')")
    # Running the bundled interpreters must not alter the application seal.
    run("codesign", "--verify", "--deep", "--strict", str(app))
    print(f"Verified app seal, {count} Mach-O signatures/architectures and bundled runtimes")


def verify_dmg(dmg: Path, arch: str, notarized: bool = False):
    run("hdiutil", "verify", str(dmg))
    # macOS reports /private/var/... even if TMPDIR uses /var/... . Detach by
    # canonical mountpoint, not by comparing differently spelled plist paths.
    temporary = Path(tempfile.mkdtemp(prefix="ojf-dmg-check-")).resolve()
    mount = temporary / "mount"
    mount.mkdir()
    try:
        run("hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen",
            "-mountpoint", str(mount), str(dmg))
        try:
            apps = list(mount.glob("*.app"))
            if len(apps) != 1:
                raise RuntimeError(f"Expected one app in DMG, found {len(apps)}")
            verify_app(apps[0], arch)
            if notarized:
                run("xcrun", "stapler", "validate", str(apps[0]))
                run("spctl", "--assess", "--type", "execute", "--verbose=2", str(apps[0]))
            else:
                print("NOTICE: ad-hoc only; Gatekeeper user approval is still required.")
        finally:
            run("hdiutil", "detach", str(mount))
    finally:
        # Never recursively remove a mountpoint if detaching fails.
        try:
            mount.rmdir()
            temporary.rmdir()
        except OSError:
            pass
    digest = hashlib.sha256()
    with dmg.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    checksum = dmg.with_suffix(dmg.suffix + ".sha256")
    checksum.write_text(f"{digest.hexdigest()}  {dmg.name}\n", encoding="utf-8")
    print(f"Verified DMG; checksum written to {checksum}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dmg", type=Path)
    parser.add_argument("--arch", required=True, choices=["arm64", "x86_64"])
    parser.add_argument("--require-notarized", action="store_true")
    args = parser.parse_args()
    try:
        verify_dmg(args.dmg.resolve(), args.arch, args.require_notarized)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Failed: {exc.cmd}\n{exc.stdout}\n{exc.stderr}") from exc
