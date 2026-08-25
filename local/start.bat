@echo off
chcp 65001 >nul
title 火花续连 - 本地版
echo ============================================
echo   火花续连（本地版）启动中...
echo   免登录 · 永久会员
echo   http://localhost:8765
echo   （关闭本窗口即停止）
echo ============================================
if not exist venv\Scripts\python.exe (
  echo [错误] 未找到虚拟环境，请先运行 install.bat
  pause & exit /b 1
)
set DEPLOY_MODE=local
venv\Scripts\python gui.py
pause
