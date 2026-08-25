# 火花续连（服务器版）

抖音「自动续火花」多用户服务器版。支持注册/登录、会员卡密、管理员后台。

## ⚠️ 重要：首个注册账户 = 管理员

**服务器首次部署后，注册的第一个账户会自动成为管理员**：
- ✅ 免邮箱验证
- ✅ 免邀请码
- ✅ 永久有效（`role=admin`）

第一个管理员建立后，后续账户需**邀请码 + 邮箱验证码**才能注册。

## 快速部署（Linux）

```bash
./server_install.sh    # 检测 Python → 建 venv → 装依赖 → 装浏览器
./server_start.sh      # 监听 0.0.0.0:8765
```

建议用 Nginx 反向代理 + HTTPS：

```nginx
server {
    listen 443 ssl;
    server_name 你的域名;
    ssl_certificate     /path/fullchain.pem;
    ssl_certificate_key /path/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 服务器版功能

- 注册 / 登录 / 找回密码（邮箱验证码）
- 会员卡密系统（周卡/月卡/年卡/永久卡）
- 管理员后台（生成卡密、管理用户）
- 控制台：好友列表、发送内容、定时续火花、干跑验证、正式发送、运行日志
- 多用户数据隔离（`data/users/<用户>/`）

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.10+，标准库 `http.server`（零框架依赖） |
| 浏览器自动化 | Playwright + Chromium（`app/` 模块） |
| 前端 | 原生 HTML/CSS/JavaScript + GSAP 3.12.5（本地自托管） |
| 数据存储 | JSON 文件（`data/users.json`、`data/codes.json`） |
| 部署 | Nginx 反向代理 + HTTPS |

## 引用项目

- [Playwright](https://github.com/microsoft/playwright)（MIT）— 浏览器自动化驱动
- [GSAP](https://github.com/greensock/GSAP)（Standard License，免费版）— 前端动效
- [python-dotenv](https://github.com/theskumar/python-dotenv)（BSD）— 环境变量
- [tzdata](https://pypi.org/project/tzdata/)（Apache-2.0）— 时区数据

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEPLOY_MODE` | `server` | `server`=服务器版 / `local`=本地单机版 |
| `GUI_HOST` | `0.0.0.0` | 监听地址 |
| `GUI_PORT` | `8765` | 监听端口 |
| `SESSION_COOKIE_SECURE` | `0` | HTTPS 下建议设 `1` |
