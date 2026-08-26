@echo off
setlocal enabledelayedexpansion
title DouyinFire Launcher
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo [ERROR] Not installed yet. Run install.bat first.
    pause
    exit /b 1
)

echo Stopping old instance (if any)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -match 'gui\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 1 /nobreak >nul

if not defined ADMIN_USER set "ADMIN_USER=admin"
if not defined ADMIN_PASSWORD set "ADMIN_PASSWORD=admin"
set "DEPLOY_MODE=server"

echo Starting Douyin Fire Panel...
start "DouyinFire" cmd /c "set GUI_HOST=127.0.0.1&& set GUI_PORT=8765&& set DEPLOY_MODE=server&& set ADMIN_USER=%ADMIN_USER%&& set ADMIN_PASSWORD=%ADMIN_PASSWORD%&& cd /d %CD%&& venv\Scripts\python gui.py"

echo Waiting for service...
timeout /t 4 /nobreak >nul
start http://127.0.0.1:8765/
echo.
echo   URL : http://127.0.0.1:8765/
echo   User: %ADMIN_USER%
echo   Pass: %ADMIN_PASSWORD%
echo.
pause
