#!/bin/sh
# Build-time install only. Never imports an account or authenticates.
set -eu
version=${1:?Usage: install-cursor-cli.sh VERSION}
case "$version" in
    *[!a-zA-Z0-9._-]*|'') echo 'Invalid Cursor CLI version' >&2; exit 2 ;;
esac
case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) echo 'Unsupported Cursor CLI architecture' >&2; exit 2 ;;
esac
dest="/opt/cursor-agent/$version"
archive=$(mktemp)
trap 'rm -f "$archive"' EXIT
# Same versioned distribution used by https://cursor.com/install.
curl --fail --show-error --silent --location --retry 3 --connect-timeout 20 --max-time 300 \
    "https://downloads.cursor.com/lab/$version/linux/$arch/agent-cli-package.tar.gz" -o "$archive"
mkdir -p "$dest"
tar -xzf "$archive" --strip-components=1 -C "$dest"
chmod -R a+rX "$dest"
ln -s "$dest/cursor-agent" /usr/local/bin/cursor-agent
ln -s "$dest/cursor-agent" /usr/local/bin/agent
cursor-agent --version
