#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -d venv ]; then echo "[错误] 请先运行 ./server_install.sh"; exit 1; fi
. venv/bin/activate
export DEPLOY_MODE=server
export GUI_HOST=0.0.0.0
export GUI_PORT="${GUI_PORT:-8765}"
echo "============================================"
echo "  火花续连（服务器版）启动中..."
echo "  监听 0.0.0.0:${GUI_PORT}（建议 Nginx+HTTPS 反代）"
echo "  首个注册账户 = 管理员"
echo "  Ctrl+C 停止"
echo "============================================"
python gui.py
