#!/bin/bash
# 一键部署脚本（自动识别 CentOS / Ubuntu / Debian）
# 用法：把本文件传到服务器后，在终端执行  bash setup.sh
set -e

cd "$(dirname "$0")"
echo "当前目录: $(pwd)"
echo "============================================"

# ---------- 1. 装 Python（按系统自动选包管理器） ----------
echo "[1/5] 检测系统并安装 Python3 ..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y python3 python3-venv python3-pip
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-pip python3-virtualenv
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 python3-pip python3-virtualenv
else
  echo "错误：未找到 apt-get/yum/dnf，请手动安装 python3 后重试"
  exit 1
fi

# ---------- 2. 虚拟环境 + Python 依赖 ----------
echo "[2/5] 创建虚拟环境并安装依赖 ..."
rm -rf venv
python3 -m venv venv
. venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ---------- 3. 安装 Chromium 浏览器 ----------
echo "[3/5] 安装 Playwright Chromium（下载浏览器，请耐心等）..."
playwright install-deps chromium || {
  echo "  [提示] install-deps 失败，尝试手动补依赖..."
  if command -v yum >/dev/null 2>&1; then
    yum install -y alsa-lib atk at-spi2-atk cups-libs gtk3 libdrm libxkbcommon \
      libXcomposite libXcursor libXdamage libXext libXi libXrandr libXScrnSaver \
      libXtst pango nspr nss mesa-libgbm || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y alsa-lib atk at-spi2-atk cups-libs gtk3 libdrm libxkbcommon \
      libXcomposite libXcursor libXdamage libXext libXi libXrandr \
      libXScrnSaver libXtst pango nspr nss mesa-libgbm || true
  fi
}
playwright install chromium

# ---------- 4. 启动服务 ----------
echo "[4/5] 启动 Web 服务 ..."
pkill -f "python gui.py" 2>/dev/null || true
nohup python gui.py > server.log 2>&1 &
sleep 4

# ---------- 5. 打印管理员密码 ----------
echo "[5/5] 部署完成，读取管理员信息 ..."
echo "管理员账号: dengjiehua"
echo "--------------------------------------------"
grep -iE "AUTH|已生成随机管理员密码|监听|listening|8765" server.log || echo "（未直接看到密码，请手动执行: grep AUTH server.log）"
echo "--------------------------------------------"
echo "后端已在 127.0.0.1:8765 运行。接下来请在宝塔面板配置反向代理："
echo "  网站 -> 添加站点(域名填 154.37.214.185) -> 反向代理(目标 http://127.0.0.1:8765)"
echo "  -> SSL(自签/免费证书) -> 强制 HTTPS"
echo "然后浏览器打开 https://154.37.214.185/  （证书警告点继续访问）"
