#!/usr/bin/env python3
"""
JellyfishBot Release Builder

Downloads embedded Python & Node.js, stages project files,
pre-installs Python dependencies, and runs Tauri build
to produce platform installers (.exe / .dmg).

Usage:
  python scripts/build.py                       # build for current platform
  python scripts/build.py --version 1.2.0       # set version then build
  python scripts/build.py --no-frontend          # skip frontend rebuild
  python scripts/build.py --no-pip               # skip pip pre-install
  python scripts/build.py --clean                # wipe staging dir first
  python scripts/build.py --target aarch64-apple-darwin   # cross-compile hint

Requires: Rust toolchain, Node.js (for Tauri CLI)
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Versions ──────────────────────────────────────────────────────

PYTHON_VERSION = "3.12.7"
PYTHON_BUILD_TAG = "20241016"
NODE_VERSION = "20.18.0"

# ── Paths ─────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
TAURI_DIR = SCRIPT_DIR.parent                     # tauri-launcher/
PROJECT_ROOT = TAURI_DIR.parent                    # semi-deep-agent/
STAGE_DIR = TAURI_DIR / "bundle-resources"
CACHE_DIR = TAURI_DIR / ".cache"

# ── Platform Detection ────────────────────────────────────────────

def detect_target() -> str:
    machine = platform.machine().lower()
    system = platform.system().lower()

    arch_map = {
        "x86_64": "x86_64", "amd64": "x86_64",
        "arm64": "aarch64", "aarch64": "aarch64",
    }
    arch = arch_map.get(machine, machine)

    if system == "darwin":
        return f"{arch}-apple-darwin"
    elif system == "windows":
        return "x86_64-pc-windows-msvc"
    else:
        return f"{arch}-unknown-linux-gnu"


def python_download_url(target: str) -> str:
    """python-build-standalone install_only tarball URL."""
    base = "https://github.com/indygreg/python-build-standalone/releases/download"
    tag = PYTHON_BUILD_TAG
    ver = PYTHON_VERSION
    # Map Rust target triple → python-build-standalone triple
    triple_map = {
        "aarch64-apple-darwin": "aarch64-apple-darwin",
        "x86_64-apple-darwin": "x86_64-apple-darwin",
        "x86_64-pc-windows-msvc": "x86_64-pc-windows-msvc",
        "x86_64-unknown-linux-gnu": "x86_64-unknown-linux-gnu",
        "aarch64-unknown-linux-gnu": "aarch64-unknown-linux-gnu",
    }
    triple = triple_map.get(target, target)
    return f"{base}/{tag}/cpython-{ver}+{tag}-{triple}-install_only.tar.gz"


def node_download_url(target: str) -> tuple[str, str]:
    """Returns (url, archive_ext) for Node.js prebuilt binary."""
    base = f"https://nodejs.org/dist/v{NODE_VERSION}"
    if "apple-darwin" in target:
        arch = "arm64" if "aarch64" in target else "x64"
        return f"{base}/node-v{NODE_VERSION}-darwin-{arch}.tar.gz", ".tar.gz"
    elif "windows" in target:
        return f"{base}/node-v{NODE_VERSION}-win-x64.zip", ".zip"
    else:
        arch = "arm64" if "aarch64" in target else "x64"
        return f"{base}/node-v{NODE_VERSION}-linux-{arch}.tar.gz", ".tar.gz"


# ── Download Helpers ──────────────────────────────────────────────

def download(url: str, dest: Path):
    """Download file with progress indicator."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"   [ok] cached: {dest.name}")
        return
    print(f"   downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        urllib.request.urlretrieve(url, str(tmp))
        tmp.rename(dest)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def extract_tar(archive: Path, dest: Path):
    print(f"   解压 {archive.name} → {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        tf.extractall(dest)


def extract_zip(archive: Path, dest: Path):
    print(f"   解压 {archive.name} → {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)


# ── Version Management ────────────────────────────────────────────

def set_version(version: str):
    """Update version in Cargo.toml and package.json.

    tauri.conf.json has no version field — Tauri reads from Cargo.toml.
    """
    # Cargo.toml (source of truth for Tauri)
    cargo_path = TAURI_DIR / "src-tauri" / "Cargo.toml"
    lines = cargo_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("version = "):
            lines[i] = f'version = "{version}"'
            break
    cargo_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"   Cargo.toml -> {version}")

    # package.json
    pkg_path = TAURI_DIR / "package.json"
    with open(pkg_path, encoding="utf-8") as f:
        pkg = json.load(f)
    pkg["version"] = version
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"   package.json -> {version}")


def get_version() -> str:
    cargo_path = TAURI_DIR / "src-tauri" / "Cargo.toml"
    for line in cargo_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    return "0.0.0"


# ── Stage Project Files ──────────────────────────────────────────

BACKEND_DIRS = ["app", "config"]
BACKEND_FILES = [
    "launcher.py", "requirements.txt",
]

FRONTEND_FILES_TO_COPY = ["server.js", "package.json"]

EXCLUDE_PATTERNS = {
    "__pycache__", ".pyc", ".pyo", ".DS_Store", "Thumbs.db",
    ".git", ".env", "node_modules", ".pytest_cache", ".mypy_cache",
}


def should_exclude(path: Path) -> bool:
    for part in path.parts:
        if part in EXCLUDE_PATTERNS:
            return True
        for pat in EXCLUDE_PATTERNS:
            if part.endswith(pat):
                return True
    return False


def copy_tree(src: Path, dst: Path):
    """Copy directory tree, skipping excluded patterns."""
    for item in src.rglob("*"):
        if should_exclude(item.relative_to(src)):
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def stage_project(target: str):
    """Copy project files into staging directory."""
    print("\n[2/5] 暂存项目文件...")

    # Backend
    for d in BACKEND_DIRS:
        src = PROJECT_ROOT / d
        if src.is_dir():
            copy_tree(src, STAGE_DIR / d)
            print(f"   [ok] {d}/")

    for f in BACKEND_FILES:
        src = PROJECT_ROOT / f
        if src.is_file():
            shutil.copy2(src, STAGE_DIR / f)
            print(f"   [ok] {f}")

    # .env.example as template
    env_example = PROJECT_ROOT / ".env.test"
    if env_example.exists():
        shutil.copy2(env_example, STAGE_DIR / ".env.example")
        print("   [ok] .env.example")


def stage_frontend(skip_build: bool):
    """Build and stage the frontend."""
    print("\n[3/5] 构建前端...")
    frontend_dir = PROJECT_ROOT / "frontend"

    if not skip_build:
        subprocess.run(
            ["npm", "ci"], cwd=frontend_dir, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=IS_WINDOWS,
        )
        subprocess.run(
            ["npm", "run", "build"], cwd=frontend_dir, check=True,
            shell=IS_WINDOWS,
        )
        print("   [ok] npm build complete")

    # Copy dist/
    dist_src = frontend_dir / "dist"
    if not dist_src.exists():
        raise FileNotFoundError(f"Frontend dist not found: {dist_src}")

    dist_dst = STAGE_DIR / "frontend" / "dist"
    if dist_dst.exists():
        shutil.rmtree(dist_dst)
    shutil.copytree(dist_src, dist_dst)
    print("   [ok] frontend/dist/")

    # Copy server.js and package.json
    for f in FRONTEND_FILES_TO_COPY:
        src = frontend_dir / f
        if src.exists():
            shutil.copy2(src, STAGE_DIR / "frontend" / f)

    # Install production deps for Express
    prod_pkg = STAGE_DIR / "frontend"
    subprocess.run(
        ["npm", "install", "--omit=dev"], cwd=prod_pkg, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=IS_WINDOWS,
    )
    print("   [ok] frontend production deps installed")


# ── Download & Stage Runtimes ─────────────────────────────────────

def stage_python(target: str, skip_pip: bool):
    """Download embedded Python and pre-install deps."""
    print("\n[1/5] 准备 Python 运行时...")
    url = python_download_url(target)
    archive_name = url.rsplit("/", 1)[1]
    archive_path = CACHE_DIR / archive_name

    download(url, archive_path)

    python_stage = STAGE_DIR / "python"
    if python_stage.exists():
        shutil.rmtree(python_stage)

    # Extract — python-build-standalone puts files under python/
    tmp_extract = CACHE_DIR / "python_extract"
    if tmp_extract.exists():
        shutil.rmtree(tmp_extract)
    extract_tar(archive_path, tmp_extract)

    # The archive extracts to python/install/ — flatten to just python/
    extracted = tmp_extract / "python"
    if not extracted.exists():
        # Some archives extract to python/install
        for child in tmp_extract.iterdir():
            if child.is_dir():
                extracted = child
                break

    shutil.copytree(extracted, python_stage)
    shutil.rmtree(tmp_extract)
    print(f"   [ok] Python {PYTHON_VERSION} staged")

    # Ensure pip
    python_exe = _find_python_exe(python_stage, target)
    if not python_exe:
        print("   [warn] Python executable not found in staged dir")
        return

    subprocess.run(
        [str(python_exe), "-m", "ensurepip", "--upgrade"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    # Pre-install requirements
    if not skip_pip:
        print("   downloading pip install requirements.txt...")
        req = STAGE_DIR / "requirements.txt"
        if not req.exists():
            req = PROJECT_ROOT / "requirements.txt"
        result = subprocess.run(
            [str(python_exe), "-m", "pip", "install", "-r", str(req),
             "--no-warn-script-location", "-q"],
            cwd=str(STAGE_DIR),
        )
        if result.returncode == 0:
            print("   [ok] Python dependencies installed")
        else:
            print("   [warn] pip install had errors (non-fatal)")


def _find_python_exe(python_dir: Path, target: str) -> Path | None:
    if "windows" in target:
        for name in ["python.exe", "python3.exe"]:
            p = python_dir / name
            if p.exists():
                return p
    else:
        for name in ["python3", "python"]:
            p = python_dir / "bin" / name
            if p.exists():
                return p
    return None


def stage_node(target: str):
    """Download Node.js prebuilt binary."""
    print("\n[4/5] 准备 Node.js 运行时...")
    url, ext = node_download_url(target)
    archive_name = url.rsplit("/", 1)[1]
    archive_path = CACHE_DIR / archive_name

    download(url, archive_path)

    node_stage = STAGE_DIR / "node"
    if node_stage.exists():
        shutil.rmtree(node_stage)

    tmp_extract = CACHE_DIR / "node_extract"
    if tmp_extract.exists():
        shutil.rmtree(tmp_extract)

    if ext == ".zip":
        extract_zip(archive_path, tmp_extract)
    else:
        extract_tar(archive_path, tmp_extract)

    # Node archives extract to node-v{ver}-{platform}-{arch}/
    for child in tmp_extract.iterdir():
        if child.is_dir() and child.name.startswith("node-"):
            shutil.copytree(child, node_stage)
            break

    shutil.rmtree(tmp_extract)
    print(f"   [ok] Node.js {NODE_VERSION} staged")


# ── Tauri Build ───────────────────────────────────────────────────

def tauri_build(target: str):
    """Run Tauri build."""
    print("\n[5/5] Tauri build...")

    # Install Tauri CLI if not present
    subprocess.run(
        ["npm", "install"], cwd=TAURI_DIR, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=IS_WINDOWS,
    )

    cmd = ["npx", "tauri", "build"]
    # Only add --target for cross-compilation; skip for native builds
    native = detect_target()
    if target != native:
        cmd += ["--target", target]

    result = subprocess.run(cmd, cwd=TAURI_DIR, shell=IS_WINDOWS)
    if result.returncode != 0:
        print(f"\n[error] Tauri build failed (exit code {result.returncode})")
        print("   Staged resources:")
        for p in sorted(STAGE_DIR.rglob("*")):
            if p.is_file():
                print(f"     {p.relative_to(STAGE_DIR)}")
        sys.exit(result.returncode)
    print("   [ok] Tauri build complete")

    # Print output location
    bundle_dir = TAURI_DIR / "src-tauri" / "target"
    if target != native:
        bundle_dir = bundle_dir / target
    bundle_dir = bundle_dir / "release" / "bundle"

    if bundle_dir.exists():
        print(f"\n[output] {bundle_dir}")
        for item in bundle_dir.rglob("*"):
            if item.is_file() and item.suffix in (".dmg", ".exe", ".msi", ".deb", ".AppImage"):
                size_mb = item.stat().st_size / (1024 * 1024)
                print(f"   → {item.name}  ({size_mb:.1f} MB)")


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="JellyfishBot Release Builder")
    parser.add_argument("--version", help="Set version before building (e.g., 1.2.0)")
    parser.add_argument("--target", help="Rust target triple (auto-detected if omitted)")
    parser.add_argument("--no-frontend", action="store_true", help="Skip frontend rebuild")
    parser.add_argument("--no-pip", action="store_true", help="Skip pip pre-install")
    parser.add_argument("--clean", action="store_true", help="Clean staging directory first")
    parser.add_argument("--stage-only", action="store_true", help="Stage files only, skip Tauri build")
    args = parser.parse_args()

    target = args.target or detect_target()
    print("JellyfishBot Release Builder")
    print(f"   Target:  {target}")
    print(f"   Version: {get_version()}")

    if args.version:
        print(f"\n[0] 更新版本号 → {args.version}")
        set_version(args.version)

    if args.clean and STAGE_DIR.exists():
        print(f"\n   清理 {STAGE_DIR}...")
        shutil.rmtree(STAGE_DIR)

    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    stage_python(target, skip_pip=args.no_pip)
    stage_project(target)
    stage_frontend(skip_build=args.no_frontend)
    stage_node(target)

    if not args.stage_only:
        tauri_build(target)

    print("\n[done] Build complete!")


if __name__ == "__main__":
    main()
