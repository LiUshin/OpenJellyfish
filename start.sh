#!/bin/bash
# JellyfishBot Docker / Server startup script
# Supports dynamic ports via BACKEND_PORT / FRONTEND_PORT env vars
set -e

BACKEND_PORT=${BACKEND_PORT:-8000}
FRONTEND_PORT=${FRONTEND_PORT:-3000}

echo "========================================="
echo "  JellyfishBot Starting..."
echo "  Backend port:  $BACKEND_PORT"
echo "  Frontend port: $FRONTEND_PORT"
echo "========================================="

mkdir -p /app/users

# Start FastAPI backend
echo "[1/2] Starting FastAPI backend on port $BACKEND_PORT..."
cd /app
python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$BACKEND_PORT" \
    --workers ${UVICORN_WORKERS:-1} \
    --log-level $(echo ${LOG_LEVEL:-info} | tr '[:upper:]' '[:lower:]') &
BACKEND_PID=$!

# Wait for backend to be ready
echo "  Waiting for backend to be ready..."
for i in $(seq 1 60); do
    if curl -s "http://localhost:$BACKEND_PORT/docs" > /dev/null 2>&1; then
        echo "  Backend is ready!"
        break
    fi
    sleep 1
done

# Start Express frontend
echo "[2/2] Starting Express frontend on port $FRONTEND_PORT..."
cd /app/frontend
FRONTEND_PORT="$FRONTEND_PORT" API_TARGET="http://localhost:$BACKEND_PORT" node server.js &
FRONTEND_PID=$!

echo "========================================="
echo "  JellyfishBot is running!"
echo "  Frontend: http://localhost:$FRONTEND_PORT"
echo "  API:      http://localhost:$BACKEND_PORT"
echo "========================================="

# Graceful shutdown
cleanup() {
    echo "Shutting down..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
    wait
}
trap cleanup SIGTERM SIGINT

# Wait for either process to exit
wait -n $BACKEND_PID $FRONTEND_PID
echo "A process exited, shutting down..."
cleanup
