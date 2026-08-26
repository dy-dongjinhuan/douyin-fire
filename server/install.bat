@echo off
setlocal enabledelayedexpansion
title DouyinFire Installer
cd /d "%~dp0"

echo ===================================================
echo   Douyin Fire Panel - Windows One-Click Install
echo   Install Dir: %CD%
echo ===================================================
echo.

REM ---------- 1. check python ----------
echo [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo   Please install Python 3.11 or newer from:
    echo   https://www.python.org/downloads/
    echo   IMPORTANT: check "Add Python to PATH" during install,
    echo   then close this window and run install.bat again.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do echo   Python OK: %%v

REM ---------- 2. create venv ----------
echo [2/5] Creating virtual environment...
if not exist venv (
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip -q

REM ---------- 3. install deps ----------
echo [3/5] Installing Python dependencies...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [ERROR] pip install failed. Check network and retry.
    pause
    exit /b 1
)

REM ---------- 4. playwright chromium ----------
echo [4/5] Installing Chromium browser (first run ~150MB, please wait)...
python -m playwright install chromium
if errorlevel 1 (
    echo [ERROR] Chromium install failed. Check network and retry.
    pause
    exit /b 1
)

REM ---------- 5. config ----------
if not exist config.json (
    copy /y config.example.json config.json >nul
    echo       config.json generated (edit friends/messages later in panel)
)

REM ---------- launch ----------
echo [5/5] Starting service...
if not defined ADMIN_USER set "ADMIN_USER=admin"
if not defined ADMIN_PASSWORD set "ADMIN_PASSWORD=admin"
set "DEPLOY_MODE=server"
echo.
echo   URL : http://127.0.0.1:8765/
echo   Mode: SERVER (登录 + 会员)
echo.

start "DouyinFire" cmd /c "set GUI_HOST=127.0.0.1&& set GUI_PORT=8765&& set DEPLOY_MODE=server&& set ADMIN_USER=%ADMIN_USER%&& set ADMIN_PASSWORD=%ADMIN_PASSWORD%&& cd /d %CD%&& venv\Scripts\python gui.py"

echo   Waiting for service...
timeout /t 5 /nobreak >nul
start http://127.0.0.1:8765/
echo.
echo   Done! Browser should open automatically.
echo   Keep this window closed if you want to stop the service later (use stop.bat).
pause
