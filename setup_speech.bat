@echo off
setlocal
cd /d "%~dp0"
if not defined NODE_BIN set "NODE_BIN=node"
"%NODE_BIN%" desktop\scripts\setup-feature.js speech %*
exit /b %errorlevel%
