#!/usr/bin/env python3
"""
JellyfishBot Version Manager

Syncs version across tauri.conf.json, Cargo.toml, package.json.
Optionally creates a git tag.

Usage:
  python scripts/version.py show              # display current version
  python scripts/version.py bump patch        # 1.0.0 → 1.0.1
  python scripts/version.py bump minor        # 1.0.0 → 1.1.0
  python scripts/version.py bump major        # 1.0.0 → 2.0.0
  python scripts/version.py set 2.3.4         # set explicit version
  python scripts/version.py tag               # git tag v{current_version}
  python scripts/version.py tag --push        # tag + push to remote
"""

import json
import re
import subprocess
import sys
from pathlib import Path

TAURI_DIR = Path(__file__).resolve().parent.parent
CONF_PATH = TAURI_DIR / "src-tauri" / "tauri.conf.json"
CARGO_PATH = TAURI_DIR / "src-tauri" / "Cargo.toml"
PKG_PATH = TAURI_DIR / "package.json"


def _read_version() -> str:
    """Read version from Cargo.toml (source of truth)."""
    for line in CARGO_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    return "0.0.0"


def _write_version(version: str):
    """Write version to Cargo.toml and package.json.

    tauri.conf.json has no version field — Tauri reads from Cargo.toml.
    """
    # Cargo.toml — only the [package] section version
    cargo = CARGO_PATH.read_text(encoding="utf-8")
    cargo = re.sub(
        r'^version\s*=\s*"[^"]*"',
        f'version = "{version}"',
        cargo,
        count=1,
        flags=re.MULTILINE,
    )
    CARGO_PATH.write_text(cargo, encoding="utf-8")

    # package.json
    with open(PKG_PATH, encoding="utf-8") as f:
        pkg = json.load(f)
    pkg["version"] = version
    with open(PKG_PATH, "w", encoding="utf-8") as f:
        json.dump(pkg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _bump(part: str) -> str:
    current = _read_version()
    parts = list(map(int, current.split(".")))
    while len(parts) < 3:
        parts.append(0)

    if part == "major":
        parts = [parts[0] + 1, 0, 0]
    elif part == "minor":
        parts = [parts[0], parts[1] + 1, 0]
    elif part == "patch":
        parts = [parts[0], parts[1], parts[2] + 1]
    else:
        print(f"Unknown part: {part} (use major/minor/patch)")
        sys.exit(1)

    return ".".join(map(str, parts))


def main():
    if len(sys.argv) < 2:
        print(f"Current version: {_read_version()}")
        print("Usage: version.py [show|bump|set|tag] ...")
        return

    cmd = sys.argv[1]

    if cmd == "show":
        print(_read_version())

    elif cmd == "bump":
        if len(sys.argv) < 3:
            print("Usage: version.py bump [major|minor|patch]")
            sys.exit(1)
        new_ver = _bump(sys.argv[2])
        _write_version(new_ver)
        print(f"Version bumped to {new_ver}")
        print(f"  [ok] Cargo.toml\n  [ok] package.json")

    elif cmd == "set":
        if len(sys.argv) < 3:
            print("Usage: version.py set X.Y.Z")
            sys.exit(1)
        version = sys.argv[2]
        if not re.match(r"^\d+\.\d+\.\d+$", version):
            print(f"Invalid version format: {version} (expected X.Y.Z)")
            sys.exit(1)
        old = _read_version()
        _write_version(version)
        print(f"Version set: {old} -> {version}")
        print(f"  [ok] Cargo.toml\n  [ok] package.json")

    elif cmd == "tag":
        version = _read_version()
        tag = f"v{version}"
        push = "--push" in sys.argv

        subprocess.run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], check=True)
        print(f"Created tag: {tag}")

        if push:
            subprocess.run(["git", "push", "origin", tag], check=True)
            print(f"Pushed tag: {tag}")

    else:
        print(f"Unknown command: {cmd}")
        print("Usage: version.py [show|bump|set|tag]")
        sys.exit(1)


if __name__ == "__main__":
    main()
