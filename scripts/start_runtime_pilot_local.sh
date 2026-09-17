#!/bin/bash
# Start the configured pilot on loopback, without background business channels.
set -euo pipefail
cd "$(dirname "$0")/.."

export DISABLE_SCHEDULER=1
export DISABLE_WECHAT_CHANNEL=1
export RESTORE_VENVS_ON_STARTUP=0
export STORAGE_BACKEND=local

backend_pid=''
frontend_pid=''
cleanup() {
    trap - EXIT INT TERM
    [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
    [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 &
backend_pid=$!
(
    cd frontend
    exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3001 --strictPort
) &
frontend_pid=$!
echo 'Jellyfish pilot: http://127.0.0.1:3001/runtime-pilot'
while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
    sleep 1
done
exit 1
