# douyin-fire（本地部署版 / npm）

抖音续火花管理面板 · **本地一键部署版**。通过 npm 安装后，自动创建 Python 虚拟环境、安装依赖、下载 Chromium，并启动可视化后台。

> 官网 / 文档：https://github.com/dy-dongjinhuan/douyin-fire
> 服务器部署版、使用说明、功能列表见上方 GitHub 仓库。

## 安装

```bash
npm install -g douyin-fire
# 或临时体验：
npx douyin-fire
```

## 使用

```bash
douyin-fire            # 启动面板（默认 http://127.0.0.1:8765 ，免登录本地模式 DEPLOY_MODE=local）
douyin-fire stop       # 停止服务
douyin-fire --port 80  # 指定端口（80 需管理员权限）
```

首次运行会自动：
1. 创建 `.venv` 虚拟环境
2. `pip install -r requirements.txt`
3. 下载 Chromium 浏览器（约 150MB，需联网访问 `cdn.playwright.dev`）
4. 启动 `gui.py` 并自动打开浏览器

## 前提
- 已安装 **Python 3.11+** 并加入 PATH（https://www.python.org/downloads/）
- Windows 10/11 64 位 或 macOS / Linux

## 使用流程
1. 浏览器打开 `http://127.0.0.1:8765/`
2. 进入【个人中心】，扫码登录抖音或粘贴 Cookie（也可用配套的「抖音 Cookie 获取工具」）
3. 在【控制台】配置好友与消息，点击「开始」即可自动续火花

## 环境变量
复制 `.env.example` 为 `.env` 可配置邮件验证码、钉钉通知、管理员账号等。

## License
MIT
