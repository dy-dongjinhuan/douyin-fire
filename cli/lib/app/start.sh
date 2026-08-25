#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -d venv ]; then echo "[错误] 请先运行 ./install.sh"; exit 1; fi
. venv/bin/activate
export DEPLOY_MODE=local
echo "============================================"
echo "  火花续连（本地版）启动中..."
echo "  http://localhost:8765    Ctrl+C 停止"
echo "============================================"
python gui.py
