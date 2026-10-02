#!/usr/bin/env bash
# Windows equivalent: dev.bat (which opens the backend/frontend each in
# their own terminal window and returns immediately; here they run as
# background jobs of this script instead, stopped together with Ctrl+C).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Homebrew's python@3.12 is keg-only - prefer it over the unversioned
# "python3" (which stays whatever the system default is, e.g. Apple's 3.9).
PYTHON_BIN="python3"
command -v python3.12 >/dev/null 2>&1 && PYTHON_BIN="python3.12"

echo "==================================================="
echo "            Remiqora - Development Launcher"
echo "==================================================="
echo ""

# 1. Check & Setup Backend Virtual Environment
if [[ ! -x "backend/.venv/bin/python3" ]]; then
    echo "[Backend Setup] Creating virtual environment..."
    "$PYTHON_BIN" -m venv backend/.venv
    backend/.venv/bin/python3 -m pip install --upgrade pip
    backend/.venv/bin/pip install -r backend/requirements.txt
fi

# 2. Check & Install Frontend Dependencies
if [[ ! -d "frontend/node_modules" ]]; then
    echo "[Frontend Setup] Installing npm dependencies..."
    (cd frontend && npm install)
fi

# 3. Launch Backend
echo "[Launcher] Starting Backend on http://127.0.0.1:9000 ..."
(cd backend && ./run.sh) &
BACKEND_PID=$!

# 4. Launch Frontend Dev Server
echo "[Launcher] Starting Frontend Dev Server on http://localhost:5173 ..."
(cd frontend && npm run dev) &
FRONTEND_PID=$!

cleanup() {
    echo ""
    echo "[Launcher] Stopping servers..."
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 5. Wait for servers to spin up and open browser
echo "[Launcher] Opening browser in 3 seconds..."
sleep 3
open "http://localhost:5173"

echo ""
echo "Both servers are running. Press Ctrl+C to stop."
echo "==================================================="
wait
