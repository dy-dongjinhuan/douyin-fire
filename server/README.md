# douyin-fire · 服务器部署版（VPS / 公网服务器）

把本目录上传到你的 Linux 服务器（如 `/www/wwwroot/douyin-fire`），即可作为**生产环境**运行：
后台进程常驻 + Nginx / 宝塔反向代理 + 域名 + HTTPS + 账号登录。

> 本地 Windows 一键版请见同级 `local/` 目录；npm 版见仓库 README。

## 一、上传代码
```bash
# 本地（打包后 scp）
scp -r server/ root@你的服务器IP:/www/wwwroot/douyin-fire
```

## 二、一键安装依赖并启动
```bash
cd /www/wwwroot/douyin-fire
bash setup.sh
```
`setup.sh` 会自动：检测系统装 Python3 → 建 venv → 装依赖 → 下载 Chromium → 后台启动 `gui.py`。

启动后默认监听 `127.0.0.1:8765`，管理员账号见 `server.log`：
- 默认管理员用户名：`dengjiehua`
- 若未设置环境变量 `ADMIN_PASSWORD`，首次启动会在日志里**随机生成**一个管理员密码，请妥善保存。

## 三、配置反向代理（Nginx）
参考 `nginx.conf.example`，将域名反向代理到 `127.0.0.1:8765`：
```bash
# 放到 /etc/nginx/conf.d/douyin-fire.conf 后
nginx -t && systemctl reload nginx
```
并在你的 DNS / 服务商处：域名 A 记录指向服务器公网 IP，申请免费 SSL 证书（Let's Encrypt / 宝塔），强制 HTTPS。

## 四、用 systemd 守护进程（推荐）
```bash
cp douyin-fire.service /etc/systemd/system/
# 编辑里边 ADMIN_PASSWORD 等环境变量后：
systemctl daemon-reload
systemctl enable --now douyin-fire
journalctl -u douyin-fire -f
```
`douyin-fire.service` 默认 `DEPLOY_MODE=server`（开启登录）。如仍想免登录，把 `Environment=DEPLOY_MODE=server` 改为 `local`。

## 五、手动启停
```bash
bash start_gui.sh      # 后台启动（日志写入 gui.log）
pkill -f 'gui\.py'     # 停止
```

## 六、配置任务（config.json）
首次启动若没有 `config.json`，复制 `config.example.json` 为 `config.json`，
在后台【控制台】也能直接修改好友、消息、发送间隔、AI 文案等。

## 目录说明
| 文件/目录 | 作用 |
|---|---|
| `gui.py` | 主程序（HTTP 服务 + 任务调度） |
| `webui_auth.py` | 登录 / 用户 / 激活码 / 会员 |
| `app/` | 业务逻辑（抖音自动化、浏览器、发送、AI 文案…） |
| `webui/` | 前端页面（控制台 / 管理 / 登录） |
| `setup.sh` / `start_gui.sh` | 安装 / 启动 |
| `nginx.conf.example` | 反向代理示例 |
| `douyin-fire.service` | systemd 服务示例 |
| `config.example.json` | 任务配置模板 |
| `.env.example` | 环境变量模板 |
| `data/` | 运行时数据（用户、登录态、日志），**定期备份** |

## 防火墙
放行 80/443（对外），`8765` 仅本地/内网，无需对外暴露。

## 免责声明
本工具用于个人自动化需求。请遵守抖音平台规则及相关法律法规，因使用本工具产生的账号风控、封禁等风险由使用者自行承担。
