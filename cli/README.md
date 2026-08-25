# 火花续连 · 本地版 CLI（npx 一键启动）

把「火花续连」本地版打包成 npm 包，对标 `npx @deepseek-ai/dsh web` 的「一条命令下载即用」体验。

```bash
# 本地版：免登录、免后台、免续费页，默认永久会员，无任何会员提示
npx douyin-fire
# 或显式指定
npx douyin-fire web
```

首次运行会自动完成：解包程序 → 创建 Python 虚拟环境 → 安装依赖 →（下载 Playwright Chromium）→ 启动本地服务并打开浏览器。
之后再次运行几乎秒开（依赖已缓存）。

## 命令与选项

| 命令/选项 | 说明 |
|---|---|
| `web`（默认） | 启动本地 Web 控制台 |
| `--no-browser` | 启动后不自动打开浏览器 |
| `--port <n>` | 指定端口（默认 8765） |
| `--reset` | 清空运行目录并重新解包 |
| `--help` / `-h` | 查看帮助 |
| `--version` / `-v` | 查看版本 |

```bash
npx douyin-fire web --port 9000 --no-browser
```

## 工作原理

- 程序本体（Python + Playwright）随 npm 包一起分发在 `lib/app/`，无需运行时再联网拉代码。
- 首次运行解包到用户目录（默认 `~/.douyin-fire/`），并自动建虚拟环境、装依赖；升级 npm 包时只会增量更新代码，**保留你的 `config.json` 与 `data/`**。
- 可用环境变量 `DOUYIN_FIRE_HOME` 自定义运行目录（便于多实例 / 便携）。

## 前置要求

- **Node.js ≥ 16**（用于运行本 CLI）
- **Python 3.10+**（CLI 会自动建虚拟环境；Windows 安装 Python 时务必勾选 “Add python.exe to PATH”）
- 网络（首次需下载 Python 依赖与 Chromium 浏览器，约 150MB）

## 发布到 npm（owner 操作）

```bash
npm login
# 若 douyin-fire 名称被占用，先改 package.json 的 name（如 @你的名/douyin-fire）
npm publish --access public
```

发布后，任何用户即可 `npx douyin-fire` 直接使用。

## 本地自测（未发布时）

```bash
cd cli
npm install -g .      # 或 npx .
douyin-fire
```

## 技术栈

| 层 | 技术 |
|---|---|
| 启动器 | Node.js（零依赖，单文件 `bin/douyin-fire.js`） |
| 后端 | Python 3.10+，`http.server`（零框架，单文件 `gui.py`） |
| 浏览器自动化 | Playwright + Chromium |
| 前端 | 原生 HTML/CSS/JS + GSAP 3.12.5（本地自托管，避免 CDN 被墙白屏） |
| 数据存储 | 本地 JSON |

完整项目与服务器版见仓库根目录：[../README.md](../README.md)
