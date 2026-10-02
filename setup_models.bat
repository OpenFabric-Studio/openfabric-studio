@echo off
setlocal
cd /d "%~dp0"

echo ===================================================
echo   Remiqora - ACE-Step / YuE2 setup (clone+patch)
echo ===================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_models.ps1" %*

endlocal
