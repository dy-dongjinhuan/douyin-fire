#!/bin/bash
# 后台启动 douyin-fire（通用版，不依赖绝对路径）
cd "$(dirname "$0")"
exec "$(dirname "$0")/venv/bin/python" "$(dirname "$0")/gui.py" >> "$(dirname "$0")/gui.log" 2>&1
