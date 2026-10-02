@echo off
setlocal
cd /d "%~dp0"

echo ===================================================
echo             Remiqora - Development Launcher
echo ===================================================
echo.

:: 1. Check & Setup Backend Virtual Environment
if not exist "backend\.venv\Scripts\python.exe" (
    echo [Backend Setup] Creating virtual environment...
    python -m venv backend\.venv
    call "backend\.venv\Scripts\python.exe" -m pip install --upgrade pip
    call "backend\.venv\Scripts\pip.exe" install -r backend\requirements.txt
)

:: 2. Check & Install Frontend Dependencies
if not exist "frontend\node_modules" (
    echo [Frontend Setup] Installing npm dependencies...
    cd /d "%~dp0frontend"
    call npm install
    cd /d "%~dp0"
)

:: 3. Launch Backend
echo [Launcher] Starting Backend on http://127.0.0.1:9000 ...
start "Remiqora Backend" cmd /c "cd /d "%~dp0backend" && call run.bat"

:: 4. Launch Frontend Dev Server
echo [Launcher] Starting Frontend Dev Server on http://localhost:5173 ...
start "Remiqora Frontend" cmd /c "cd /d "%~dp0frontend" && npm run dev"

:: 5. Wait for servers to spin up and open browser
echo [Launcher] Opening browser in 3 seconds...
timeout /t 3 /nobreak >nul
start http://localhost:5173

echo.
echo Both servers are running.
echo To stop, close the respective terminal windows.
echo ===================================================
endlocal
