@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [Setup] Creating virtual environment...
    python -m venv .venv
    call ".venv\Scripts\python.exe" -m pip install --upgrade pip
    call ".venv\Scripts\pip.exe" install -r requirements.txt
)

echo Starting OpenFabric backend on http://127.0.0.1:9000
set "UVICORN_RELOAD="
".venv\Scripts\python.exe" -m uvicorn app.main:app --workers 1 --host 127.0.0.1 --port 9000
endlocal
