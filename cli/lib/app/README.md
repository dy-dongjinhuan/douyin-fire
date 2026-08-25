# 火花续连（本地版）

抖音「自动续火花」本地单机版。**免登录、免注册、永久会员**，下载解压即可使用。

## 快速开始

### Windows
1. 双击 `install.bat`（自动检测 Python → 建 venv → 装依赖 → 装浏览器）
2. 双击 `start.bat` 启动
3. 浏览器打开 http://localhost:8765

### Linux / macOS
```bash
./install.sh
./start.sh
```

> 需要 Python 3.10+。首次运行需执行安装脚本，之后每次只需 `start.bat` / `./start.sh`。

## 本地版特性

- **无需登录**：打开即进入控制台，无登录页/注册页/后台页
- **永久会员**：默认永久有效，无会员提示、无续费入口
- 完整核心功能：好友列表（手动添加）、发送内容（最多 5 条文案）、定时续火花、干跑验证、正式发送、运行日志（增量刷新）
- 抖音登录态：在控制台「账号与登录」上传 Cookie 后即可自动发送

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.10+，标准库 `http.server`（零框架依赖） |
| 浏览器自动化 | Playwright + Chromium（`app/` 模块） |
| 前端 | 原生 HTML/CSS/JavaScript + GSAP 3.12.5（本地自托管） |
| 数据存储 | 本地 JSON 文件（`data/users/<用户>/`） |
| 设计 | 深色 SaaS 风格，抖音红单一强调色 |

## 引用项目

- [Playwright](https://github.com/microsoft/playwright)（MIT）— 浏览器自动化驱动
- [GSAP](https://github.com/greensock/GSAP)（Standard License，免费版）— 前端动效
- [python-dotenv](https://github.com/theskumar/python-dotenv)（BSD）— 环境变量
- [tzdata](https://pypi.org/project/tzdata/)（Apache-2.0）— 时区数据

## 常见问题

**Q：启动后打不开？**
确认 `start.bat`/`start.sh` 与 `gui.py` 在同一目录，且已先运行安装脚本。

**Q：发送失败？**
确认已在「账号与登录」上传有效的抖音登录态 Cookie，并安装了 Playwright Chromium（安装脚本第 4 步）。

**Q：想部署成多人服务器版？**
请使用同仓库的「服务器版」：https://github.com/你的用户名/douyin-fire-server
