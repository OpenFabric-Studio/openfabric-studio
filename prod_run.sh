#!/usr/bin/env bash
# Windows equivalent: prod_run.bat
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Homebrew's python@3.12 is keg-only - prefer it over the unversioned
# "python3" (which stays whatever the system default is, e.g. Apple's 3.9).
PYTHON_BIN="python3"
command -v python3.12 >/dev/null 2>&1 && PYTHON_BIN="python3.12"

echo "==================================================="
echo "            Remiqora - Production Launcher"
echo "==================================================="
echo ""

# 1. Check & Setup Backend Virtual Environment
if [[ ! -x "backend/.venv/bin/python3" ]]; then
    echo "[Backend Setup] Creating virtual environment..."
    "$PYTHON_BIN" -m venv backend/.venv
    backend/.venv/bin/python3 -m pip install --upgrade pip
    backend/.venv/bin/pip install -r backend/requirements.txt
fi

# 2. Check & Install Frontend Dependencies if needed
if [[ ! -d "frontend/node_modules" ]]; then
    echo "[Frontend Setup] Installing npm dependencies..."
    (cd frontend && npm install)
fi

# 3. Build Frontend Production Bundle
echo "[Frontend Build] Building production bundle (vue-tsc + vite build)..."
(cd frontend && npm run build)

# 4. Start Production Server on port 9000 (serves API and SPA bundle)
echo ""
echo "[Launcher] Starting production server on http://127.0.0.1:9000 ..."
( sleep 2 && open "http://127.0.0.1:9000" ) &

cd backend
exec .venv/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 9000
