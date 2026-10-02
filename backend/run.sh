#!/usr/bin/env bash
# Windows equivalent: run.bat
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Homebrew's python@3.12 is keg-only - prefer it over the unversioned
# "python3" (which stays whatever the system default is, e.g. Apple's 3.9).
PYTHON_BIN="python3"
command -v python3.12 >/dev/null 2>&1 && PYTHON_BIN="python3.12"

if [[ ! -x ".venv/bin/python3" ]]; then
    echo "[Setup] Creating virtual environment..."
    "$PYTHON_BIN" -m venv .venv
    .venv/bin/python3 -m pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

echo "Starting Remiqora backend on http://127.0.0.1:9000"
exec .venv/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 9000
