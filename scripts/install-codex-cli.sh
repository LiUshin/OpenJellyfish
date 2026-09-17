#!/bin/sh
# Build-time install of the official npm distribution; no account is imported.
set -eu
version=${1:?Usage: install-codex-cli.sh VERSION}
if ! printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$'; then
    echo 'Use an exact Codex CLI version, for example 0.154.0' >&2
    exit 2
fi
prefix="/opt/codex-cli/$version"
cache=$(mktemp -d)
trap 'rm -rf "$cache"' EXIT
# Include the official platform binary; the pinned package needs no install scripts.
npm install --global --prefix "$prefix" --cache "$cache" \
    --include=optional --ignore-scripts --no-audit --no-fund "@openai/codex@$version"
ln -s "$prefix/bin/codex" /usr/local/bin/codex
codex --version
