@echo off
title DouyinFire Stopper
echo Stopping Douyin Fire Panel...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -match 'gui\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 1 /nobreak >nul
echo Stopped (if any instance was running).
pause
