@echo off
setlocal
cd /d "%~dp0"

echo ===================================================
echo             Remiqora - Production Launcher
echo ===================================================
echo.

:: 1. Check & Setup Backend Virtual Environment
if not exist "backend\.venv\Scripts\python.exe" (
    echo [Backend Setup] Creating virtual environment...
    python -m venv backend\.venv
    call "backend\.venv\Scripts\python.exe" -m pip install --upgrade pip
    call "backend\.venv\Scripts\pip.exe" install -r backend\requirements.txt
)

:: 2. Check & Install Frontend Dependencies if needed
if not exist "frontend\node_modules" (
    echo [Frontend Setup] Installing npm dependencies...
    cd /d "%~dp0frontend"
    call npm install
    cd /d "%~dp0"
)

:: 3. Build Frontend Production Bundle
echo [Frontend Build] Building production bundle (vue-tsc + vite build)...
cd /d "%~dp0frontend"
call npm run build
if %ERRORLEVEL% neq 0 (
    echo [Error] Frontend build failed!
    pause
    exit /b %ERRORLEVEL%
)
cd /d "%~dp0"

:: 4. Start Production Server on port 9000 (serves API and SPA bundle)
echo.
echo [Launcher] Starting production server on http://127.0.0.1:9000 ...
timeout /t 2 /nobreak >nul
start http://127.0.0.1:9000

cd /d "%~dp0backend"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 9000
endlocal
