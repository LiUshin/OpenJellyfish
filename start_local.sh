#!/bin/bash
# JellyfishBot Local Launcher (macOS / Linux)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================="
echo "  JellyfishBot Local Launcher"
echo "========================================="
echo

# Find Python
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo "[ERROR] Python not found. Please install Python 3.10+"
    echo "  macOS:  brew install python@3.11"
    echo "  Linux:  sudo apt install python3"
    exit 1
fi

$PYTHON launcher.py "$@"
