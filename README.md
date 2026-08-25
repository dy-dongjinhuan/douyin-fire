# 火花续连（Douyin Fire）

抖音「自动续火花」工具。一个基于 Playwright 的抖音网页端自动化方案，提供**两种部署形态**，按需取用：

| 版本   | 目录        | 适用场景          | 登录       | 会员             |
| ---- | --------- | ------------- | -------- | -------------- |
| 本地版  | `local/`  | 个人单机自用        | **无需登录** | 默认**永久会员**     |
| 服务器版 | `server/` | 多用户 / SaaS 分发 | 注册 / 登录  | 卡密会员（周/月/年/永久） |

> 两个目录是**同一套代码**，通过 `DEPLOY_MODE` 环境变量切换形态，无需维护两套逻辑。

---

## ⚠️ 两种版本的核心差异（部署前必读）

### 服务器版：首次部署的第一个注册账户 = 管理员

- 当 `data/users.json` 为空（即**服务器第一次部署**）时，**第一个通过注册表单创建的账户会自动成为管理员**：
  - ✅ **免邮箱验证**
  - ✅ **免邀请码**
  - ✅ **永久有效**（`role=admin`）
- 管理员建立后，后续账户注册才需要 **邀请码 + 邮箱验证码**。
- 若你希望通过环境变量预置管理员（跳过注册流程），设置 `ADMIN_USER` + `ADMIN_PASSWORD` 即可；**不设置**则完全交由「首个注册账户即管理员」流程。

### 本地版：无需登录

- 本地版**没有登录页、注册页、后台页、续费页**。
- 打开即进入控制台，默认**永久会员**，无任何会员 / 到期提示。
- 适合个人单机使用，下载解压后一键启动。

---

## 技术栈

| 层      | 技术                                                                  |
| ------ | ------------------------------------------------------------------- |
| 后端     | Python 3.10+，标准库 `http.server`（零框架依赖，单文件 `gui.py`）                  |
| 浏览器自动化 | Playwright + Chromium（`app/` 模块，负责抖音网页端自动操作）                        |
| 前端     | 原生 HTML / CSS / JavaScript + GSAP 3.12.5（**本地自托管**，避免外链 CDN 被墙导致白屏） |
| 数据存储   | 本地 JSON 文件（`data/users.json`、`data/codes.json`）                     |
| 部署     | 本地直接运行 / Nginx 反向代理 + HTTPS                                         |

## 引用的项目 / 开源依赖

- [Playwright](https://github.com/microsoft/playwright)（MIT）— 浏览器自动化驱动
- [GSAP](https://github.com/greensock/GSAP)（Standard "No Charge" License，可免费用于本项目）— 前端动效
- [python-dotenv](https://github.com/theskumar/python-dotenv)（BSD）— 环境变量读取
- [tzdata](https://pypi.org/project/tzdata/)（Apache-2.0）— 时区数据

> 抖音相关操作基于抖音网页端 DOM 结构，非官方 API；本项目仅用于个人账号的自动化维护。

---

## 快速开始

### 本地版（一键启动，免登录）

```bash
cd local
./install.sh        # Windows：双击 install.bat（自动建 venv → 装依赖 → 装浏览器）
./start.sh          # 浏览器打开 http://localhost:8765
```

### 本地版（npx 一键启动，免登录）

对标 `npx @deepseek-ai/dsh web` 的「一条命令下载即用」体验，无需手动下载解压：

```bash
npx douyin-fire          # 自动解包 → 建虚拟环境 → 装依赖 → 启动并打开浏览器
npx douyin-fire web --port 9000 --no-browser
```

实现原理与可发布方式见 [`cli/README.md`](./cli/README.md)。本地版同样免登录、免后台、免续费页，默认永久会员。

### 服务器版（首个注册账户即管理员）

```bash
cd server
./server_install.sh # 检测 Python → 建 venv → 装依赖 → 装浏览器
./server_start.sh   # 监听 0.0.0.0:8765，建议前置 Nginx + HTTPS
```

启动后打开站点，**注册的第一个账户即为管理员**。

> 各目录下均有独立 `README.md`，含更详细的部署与配置说明。

---

## 环境变量（服务器版）

| 变量                      | 默认        | 说明                                |
| ----------------------- | --------- | --------------------------------- |
| `DEPLOY_MODE`           | `server`  | `server` = 服务器版 / `local` = 本地单机版 |
| `GUI_HOST`              | `0.0.0.0` | 监听地址                              |
| `GUI_PORT`              | `8765`    | 监听端口                              |
| `ADMIN_USER`            | `admin`   | 预置管理员用户名（需配合 `ADMIN_PASSWORD`）    |
| `ADMIN_PASSWORD`        | 空         | 设置后预创建管理员；不设置则由首个注册账户成为管理员        |
| `SESSION_COOKIE_SECURE` | `0`       | HTTPS 下建议设为 `1`                   |

---

## 免责声明

本项目仅供个人学习与技术研究。使用请确保遵守抖音平台规则与所在地相关法律法规，勿用于任何违规用途。
