@echo off
chcp 65001 >nul
title 火花续连 - 本地版一键安装
echo ============================================
echo   火花续连（本地版） 一键安装
echo   无需注册 / 无需登录 / 永久会员
echo ============================================
echo.
where python >nul 2>nul
if %errorlevel%==0 ( set PY=python ) else (
  where py >nul 2>nul
  if %errorlevel%==0 ( set PY=py ) else (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+ 并勾选 Add to PATH
    pause & exit /b 1
  )
)
echo [1/4] Python: %PY%
%PY% --version
if not exist venv (
  echo [2/4] 创建虚拟环境 venv ...
  %PY% -m venv venv
  if errorlevel 1 ( echo [错误] venv 创建失败 & pause & exit /b 1 )
) else ( echo [2/4] venv 已存在，跳过 )
echo [3/4] 安装依赖 ...
venv\Scripts\pip install -r requirements.txt --disable-pip-version-check -q
if errorlevel 1 ( echo [错误] 依赖安装失败 & pause & exit /b 1 )
echo [4/4] 安装 Playwright Chromium ...
venv\Scripts\python -m playwright install chromium
if errorlevel 1 echo [警告] Chromium 安装失败
echo.
echo 安装完成！运行 start.bat 启动（免登录）
echo 默认地址：http://localhost:8765
pause
