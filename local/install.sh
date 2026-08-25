#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "============================================"
echo "  火花续连（本地版）一键安装"
echo "  无需注册 / 无需登录 / 永久会员"
echo "============================================"
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
echo "[1/4] Python: $($PY --version 2>&1)"
if [ ! -d venv ]; then echo "[2/4] 创建 venv ..."; $PY -m venv venv; fi
. venv/bin/activate
echo "[3/4] 安装依赖 ..."
pip install -r requirements.txt --disable-pip-version-check -q
echo "[4/4] 安装 Playwright Chromium ..."
python -m playwright install chromium || echo "[警告] Chromium 安装失败"
echo ""
echo "安装完成！启动：./start.sh（免登录）"
