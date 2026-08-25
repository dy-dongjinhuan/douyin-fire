"""
抖音自动续火花 - 可视化界面后端
启动后访问 http://localhost:8765
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

# ===== 服务器监听配置（可通过环境变量覆盖，便于服务器部署）=====
HOST = os.environ.get("GUI_HOST", "127.0.0.1")
PORT = int(os.environ.get("GUI_PORT", "8765"))
# 部署模式：server=服务器版（登录/注册/会员/后台，首账户自动管理员）
#           local =本地单机版（免登录、免会员、无后台/续费）
DEPLOY_MODE = os.environ.get("DEPLOY_MODE", "server").strip().lower()
# 仅在 HTTPS 反代前才建议开启 Secure（否则本地 http 登录会失效）
SESSION_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") in ("1", "true", "yes")
MAX_BODY_BYTES = 1 * 1024 * 1024  # 请求体上限 1MB，防滥用

# 确保能 import app 模块
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Web 前端目录：兼容两种布局
#   ① 源码布局：PROJECT_ROOT/webui（webui 是 gui.py 的子目录）
#   ② 发布布局：PROJECT_ROOT.parent/webui（webui 与 gui.py 平级）
_WEBUI_CANDIDATES = [
    PROJECT_ROOT / "webui",
    PROJECT_ROOT.parent / "webui",
]
WEBUI_DIR = next((p for p in _WEBUI_CANDIDATES if p.is_dir()), _WEBUI_CANDIDATES[0])

from app.config import ConfigError, load_settings, load_task
from app.history import AlreadyRunningError
from app.main import run as run_sender, RunStopped

import webui_auth

SESSION_COOKIE = "session_token"

# 全局状态
STATE = {
    "login_status": "unknown",  # unknown | logged_in | not_logged_in | logging_in
    "run_status": "idle",  # idle | running | success | failed
    "run_message": "",
    "run_id": 0,  # 每次运行自增，用于前端判断「结果弹窗是否已弹过」
    "config": {},
    "logs": [],
    "schedule": {"enabled": False, "time": "00:00"},
}
STATE_LOCK = threading.Lock()

# asyncio loop 线程
LOOP: asyncio.AbstractEventLoop | None = None
LOOP_THREAD: threading.Thread | None = None

# 停止信号：运行任务时由 do_run 创建并绑定到事件循环；前端点击「停止」时置位，
# app/main.run() 在 30 秒等待与每个好友处理前检测，置位即优雅终止。
STOP_EVENT: asyncio.Event | None = None


# 简单的内存级登录限流：IP -> 最近请求时间戳列表
_RATE_LIMIT: dict[str, list[float]] = {}
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_WINDOW = 60.0  # 秒
_RATE_MAX = 20       # 窗口内最大尝试次数

# ---- IP 失败封禁（防暴破，仅对非管理员账号生效）----
_FAIL_COUNT: dict[str, list[float]] = {}   # ip -> 失败时间戳列表
_BAN_IPS: dict[str, float] = {}            # ip -> 解封时间戳（epoch）
_FAIL_WINDOW = 10 * 60.0   # 失败计数窗口：10 分钟
_FAIL_MAX = 10             # 窗口内失败达到此次数即封禁
_BAN_DURATION = 5 * 60.0   # 封禁时长：5 分钟


def _auth_rate_limited(client_ip: str) -> bool:
    """登录/注册接口频率限流：同一 IP 在窗口内请求过多则返回 True（被限流）"""
    now = time.time()
    with _RATE_LIMIT_LOCK:
        times = _RATE_LIMIT.get(client_ip, [])
        times = [t for t in times if now - t < _RATE_WINDOW]
        if len(times) >= _RATE_MAX:
            _RATE_LIMIT[client_ip] = times
            return True
        times.append(now)
        _RATE_LIMIT[client_ip] = times
    return False


def _auth_banned(client_ip: str):
    """返回 None 表示未封禁；否则返回剩余封禁秒数（float）"""
    now = time.time()
    with _RATE_LIMIT_LOCK:
        until = _BAN_IPS.get(client_ip)
        if until is None:
            return None
        if now >= until:
            del _BAN_IPS[client_ip]
            _FAIL_COUNT.pop(client_ip, None)
            return None
        return until - now


def _auth_record_fail(client_ip: str) -> float | None:
    """记录一次登录/注册失败。若触发封禁返回剩余封禁秒数，否则返回 None。"""
    now = time.time()
    with _RATE_LIMIT_LOCK:
        until = _BAN_IPS.get(client_ip)
        if until is not None and now < until:
            return until - now
        fails = _FAIL_COUNT.get(client_ip, [])
        fails = [t for t in fails if now - t < _FAIL_WINDOW]
        fails.append(now)
        _FAIL_COUNT[client_ip] = fails
        if len(fails) >= _FAIL_MAX:
            _BAN_IPS[client_ip] = now + _BAN_DURATION
            return _BAN_DURATION
        return None


def init_loop() -> None:
    """启动全局 asyncio loop 线程"""
    global LOOP, LOOP_THREAD
    LOOP = asyncio.new_event_loop()

    def run_loop() -> None:
        asyncio.set_event_loop(LOOP)
        LOOP.run_forever()

    LOOP_THREAD = threading.Thread(target=run_loop, daemon=True)
    LOOP_THREAD.start()


def run_async(coro: Any) -> asyncio.Future:
    """在全局 loop 中执行异步任务"""
    if LOOP is None:
        raise RuntimeError("asyncio loop 未初始化")
    return asyncio.run_coroutine_threadsafe(coro, LOOP)


def load_config_safe() -> dict[str, Any]:
    """安全加载 config.json"""
    config_path = PROJECT_ROOT / "config.json"
    if not config_path.exists():
        return {"friends": [], "messages": []}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {"friends": [], "messages": []}


def save_config(config: dict[str, Any]) -> None:
    """保存 config.json"""
    config_path = PROJECT_ROOT / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def load_schedule() -> dict[str, Any]:
    """加载定时任务配置"""
    path = PROJECT_ROOT / "schedule.json"
    if not path.exists():
        return {"enabled": False, "time": "00:00"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False, "time": "00:00"}


def save_schedule(data: dict[str, Any]) -> None:
    """保存定时任务配置"""
    path = PROJECT_ROOT / "schedule.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_users() -> list[str]:
    """列出 data/users 下所有用户目录名"""
    users_dir = PROJECT_ROOT / "data" / "users"
    if not users_dir.exists():
        return []
    return [d.name for d in users_dir.iterdir() if d.is_dir()]


def scheduler_loop() -> None:
    """定时任务后台线程：每 30 秒检查一次是否到达设定时间，对所有已登录租户触发续火花。

    注意：登录态按用户隔离存放在 data/users/<user>/login-confirmed.json，
    因此必须遍历各租户逐一判定，而非仅看全局 login-confirmed.json。
    """
    last_triggered = ""
    while True:
        try:
            schedule = load_schedule()
            if schedule.get("enabled"):
                target_time = schedule.get("time", "00:00")
                now = datetime.now()
                current_hm = now.strftime("%H:%M")
                trigger_key = f"{now.strftime('%Y-%m-%d')}_{current_hm}"
                if current_hm == target_time and last_triggered != trigger_key:
                    # 遍历所有已登录租户，逐个触发（多租户隔离）
                    triggered_any = False
                    for user in list_users():
                        try:
                            if check_login_status(user) == "logged_in" and has_valid_storage_state(user):
                                run_async(do_run(False, user))
                                triggered_any = True
                                print(f"[SCHED] 已为租户 {user} 触发定时续火花")
                        except Exception as exc:
                            print(f"[SCHED] 租户 {user} 触发失败: {exc}")
                    if triggered_any:
                        last_triggered = trigger_key
        except Exception:
            pass
        time.sleep(30)


def check_login_status(user: str | None = None) -> str:
    """检查登录状态（按用户隔离）：基于登录确认标记，不依赖 Playwright cookie 文件"""
    marker_path = user_login_marker_path(user) if user else (PROJECT_ROOT / "login-confirmed.json")
    if marker_path.exists():
        try:
            d = json.loads(marker_path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("confirmed"):
                return "logged_in"
        except Exception:
            pass
    return "not_logged_in"


def user_data_dir(user: str) -> Path:
    """每用户独立数据目录：data/users/<user>/"""
    d = PROJECT_ROOT / "data" / "users" / user
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_storage_path(user: str) -> Path:
    """每用户抖音登录态 storage-state.json 路径（Playwright 标准格式，仅由上传接口写入）"""
    return user_data_dir(user) / "storage-state.json"


def user_login_marker_path(user: str) -> Path:
    """每用户登录确认标记文件路径（真机登录回调写入，仅表示「用户已在真机完成登录」）"""
    return user_data_dir(user) / "login-confirmed.json"


def has_valid_storage_state(user: str | None = None) -> bool:
    """检查是否存在有效的 Playwright 格式 storage-state.json（cookies 必须是数组）"""
    state_path = user_storage_path(user) if user else (PROJECT_ROOT / "storage-state.json")
    if not state_path.exists():
        return False
    try:
        d = json.loads(state_path.read_text(encoding="utf-8"))
        # Playwright 要求 cookies 是数组
        cookies = d.get("cookies")
        return isinstance(cookies, list)
    except Exception:
        return False


def user_config_path(user: str) -> Path:
    """每用户任务配置 config.json 路径（好友列表各自独立）"""
    return user_data_dir(user) / "config.json"


def ensure_user_config(user: str) -> None:
    """新用户首次使用时，从全局模板复制配置，但好友列表强制清空为空数组。"""
    cfg = user_config_path(user)
    if cfg.exists():
        return
    template = PROJECT_ROOT / "config.json"
    if template.exists():
        try:
            raw = json.loads(template.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    else:
        raw = {}
    # 新用户默认好友列表为空
    raw["friends"] = []
    cfg.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config_safe(user: str | None = None) -> dict[str, Any]:
    """安全加载当前用户的 config.json；未指定用户时退回全局（兼容）。"""
    if user:
        ensure_user_config(user)
        path = user_config_path(user)
    else:
        path = PROJECT_ROOT / "config.json"
    if not path.exists():
        return {"friends": [], "messages": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"friends": [], "messages": []}


def save_config(config: dict[str, Any], user: str | None = None) -> None:
    """保存当前用户的 config.json；禁止写入全局共享文件以防跨账号串数据"""
    if not user:
        raise ValueError("save_config 必须指定 user，禁止写入全局 config.json")
    path = user_config_path(user)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def read_logs() -> list[str]:
    """读取最新日志"""
    log_path = PROJECT_ROOT / "artifacts" / "run.log"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        return lines[-100:]  # 最后 100 行
    except Exception:
        return []


def update_state(user: str | None = None) -> None:
    """更新状态；登录态与配置均按当前用户隔离计算，不共享全局缓存。"""
    with STATE_LOCK:
        # 登录态必须按用户独立判断（多租户隔离），不能沿用全局缓存，
        # 否则 A 用户登录后 B 用户也会显示"已登录"。
        if user:
            status = check_login_status(user)
            # 该用户有未消费的登录 token（已点获取登录链接、尚未回传）视为登录中
            if status != "logged_in" and any(
                t.get("user") == user and not t.get("used")
                for t in LOGIN_TOKENS.values()
            ):
                status = "logging_in"
        else:
            status = check_login_status()
        STATE["login_status"] = status
        STATE["config"] = load_config_safe(user)
        STATE["logs"] = read_logs()
        STATE["schedule"] = load_schedule()


# ===== 方案 C：用户真机登录 + 登录态回传（服务器不开无头浏览器，规避抖音风控）=====
# 设计：用户点「获取登录链接」→ 服务器生成一次性 token → 用户在自己的手机/电脑浏览器
# 打开 /login/<token> 完成抖音登录 → 该页把登录态（cookie）POST 回 /api/login-callback/<token>
# → 服务器按用户隔离落盘 storage-state.json。全程不经过服务器无头浏览器，从源头避开滑块验证。
import secrets as _secrets

LOGIN_TOKENS: dict[str, dict[str, Any]] = {}   # token -> {user, created_at, used, saved, state_path, source, saved_at}
LOGIN_TOKENS_LOCK = threading.Lock()
LOGIN_TOKEN_EXPIRE = 300  # 一次性登录链接 5 分钟有效
LOGIN_CURRENT_USER: str | None = None          # 兼容变量（保留引用，不再驱动无头流程）


def create_login_token(user: str) -> str:
    """为指定用户生成一个一次性登录 token，返回 token 字符串。"""
    global LOGIN_CURRENT_USER
    LOGIN_CURRENT_USER = user
    token = _secrets.token_urlsafe(16)
    with LOGIN_TOKENS_LOCK:
        LOGIN_TOKENS[token] = {
            "user": user,
            "created_at": time.time(),
            "used": False,
            "saved": False,
            "state_path": None,
            "source": None,
            "saved_at": None,
        }
    return token


def get_login_token(token: str) -> dict | None:
    """读取 token 信息（不消费）。"""
    with LOGIN_TOKENS_LOCK:
        return LOGIN_TOKENS.get(token)


def consume_login_token(token: str, cookies: Any = None) -> dict | None:
    """消费一次性登录 token：把真机回传的抖音登录态按用户隔离落盘。
    cookies 可以是：
      - 一个标准 Playwright storage-state dict（含 cookies 数组 + origins），直接作为 storage-state.json 使用；
      - 或任意非空的 cookie 载体（dict / list），统一封装成 {"cookies": [...]} 再落盘；
      - 或空（仅回传确认标记，不含真实 Cookie）。
    无论哪种情况，都会额外写一个 login-confirmed.json 标记供登录态判断使用。"""
    with LOGIN_TOKENS_LOCK:
        info = LOGIN_TOKENS.get(token)
        if not info or info.get("used"):
            return None
        user = info["user"]

        # 1) 写登录确认标记（始终写，用于"已登录"判断）
        marker_path = user_login_marker_path(user) if user else (PROJECT_ROOT / "login-confirmed.json")
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": "user_device_login",
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": user,
            "confirmed": True,
        }
        marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # 2) 若回传了真实 Cookie，直接落盘为该用户的 storage-state.json（Playwright 标准格式）
        state_path = None
        if cookies:
            state_path = user_storage_path(user) if user else (PROJECT_ROOT / "storage-state.json")
            state_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(cookies, dict) and ("cookies" in cookies or "origins" in cookies):
                # 已经是标准 storage-state 结构，原样保存
                storage = cookies
            elif isinstance(cookies, list):
                # 纯 cookies 数组，封装成标准结构
                storage = {"cookies": cookies, "origins": []}
            elif isinstance(cookies, dict):
                # 形如 {"cookies": [...]} 或不规范结构，规范化提取
                raw = cookies.get("cookies") if "cookies" in cookies else cookies
                storage = {"cookies": raw if isinstance(raw, list) else [raw], "origins": []}
            else:
                storage = {"cookies": [], "origins": []}
            state_path.write_text(json.dumps(storage, ensure_ascii=False, indent=2), encoding="utf-8")

        info["used"] = True
        info["saved"] = True
        info["state_path"] = str(state_path) if state_path else str(marker_path)
        info["source"] = "user_device_login"
        info["saved_at"] = payload["saved_at"]
    with STATE_LOCK:
        STATE["login_status"] = "logged_in"
        STATE["run_message"] = "登录态已通过真机回传更新" + ("（已写入 Cookie）" if state_path else "（仅确认，需上传 Cookie 才能发送）")
    print(f"[LOGIN] 用户 {user} 真机登录态已回传 -> marker={marker_path}" + (f" storage={state_path}" if state_path else ""))
    return info


# 真机登录中间页（用户在自己的设备浏览器打开）。生产环境应嵌入抖音官方登录，
# 这里提供一个通用中间页：引导登录后通过 /api/login-callback/<token> 回传。
LOGIN_INTERMEDIATE_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>抖音登录（请在本人设备完成）</title>
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:480px;margin:40px auto;padding:0 16px;text-align:center;color:#222}
 .box{border:1px solid #eee;border-radius:12px;padding:24px;background:#fafafa;margin-top:16px}
 button{background:#fe2c55;color:#fff;border:0;border-radius:8px;padding:13px 22px;font-size:15px;cursor:pointer;margin:8px;width:100%}
 .ghost{background:#fff;color:#fe2c55;border:1px solid #fe2c55}
 .hint{color:#888;font-size:13px;line-height:1.7}
 .step{text-align:left;margin:12px 0;font-size:14px}
 .num{display:inline-block;width:22px;height:22px;line-height:22px;background:#fe2c55;color:#fff;border-radius:50%;text-align:center;margin-right:8px;font-size:13px}
  #msg{margin-top:14px;font-weight:500}
  .embed{border:1px solid #fe2c5533;border-radius:10px;background:#fff5f7;padding:14px;margin:12px 0;text-align:left}
  .embed-title{font-size:14px;font-weight:600;color:#fe2c55;margin-bottom:6px}
  .embed-url{font-size:13px;color:#444;background:#fff;border:1px dashed #fe2c5555;border-radius:6px;padding:8px;word-break:break-all;font-family:monospace}
  .embed-tip{font-size:12px;color:#b45309;line-height:1.6;margin:8px 0}
  .embed-actions{display:flex;gap:8px;margin-top:8px}
  .embed-actions button{margin:0;font-size:14px;padding:11px 14px}
  .embed-actions .ghost{flex:0 0 auto;width:auto}
 </style></head>
<body>
<h2>请在<b>本设备</b>登录抖音</h2>
<p class="hint">这是你自己的手机/电脑浏览器，登录不会触发服务器风控。<br>
请按以下步骤操作，登录成功后回传登录态即可。</p>

<div class="box">
  <div class="step"><span class="num">1</span>在<b>新标签页</b>打开下方抖音个人主页并完成登录（扫码或短信验证）：</div>

  <!-- 内嵌登录入口卡片：抖音禁止被 iframe 嵌入，故采用“入口卡片 + 新标签打开”方式 -->
  <div class="embed">
    <div class="embed-title">📲 抖音登录入口</div>
    <div class="embed-url" id="douyinUrl">https://www.douyin.com/user/self</div>
    <div class="embed-tip">💡 <b>建议使用电脑浏览器，或手机浏览器切换「电脑 UA / 桌面版网站」打开</b>，以确保登录页正常显示并完成扫码。</div>
    <div class="embed-actions">
      <button onclick="openDouyin()">在新标签打开并登录</button>
      <button class="ghost" onclick="copyLink()">复制链接</button>
    </div>
  </div>

  <div class="step"><span class="num">2</span>登录成功后，<b>返回本页面</b>点击下方按钮，把登录态回传给服务器：</div>
  <button class="ghost" onclick="reportLogin()">我已完成登录，回传登录态</button>
  <div class="step" style="margin-top:16px;border-top:1px dashed #ddd;padding-top:12px;">
    <b>📦 想让服务器直接帮你发消息？（可选）</b><br>
    浏览器出于安全限制，回传无法自动读取抖音的 Cookie。若希望服务器自动发送，
    请在此选择你从抖音导出的 <code>storage-state.json</code> 文件，回传时会一并上传：
    <div style="margin-top:8px">
      <input type="file" id="ssFile" accept=".json,application/json" style="width:100%">
      <p id="fileHint" class="hint" style="text-align:left;margin:6px 0 0"></p>
    </div>
  </div>
  <p id="msg" class="hint"></p>
</div>

<script>
const TOKEN = "__TOKEN__";
const USER = "__USER__";
const DOUYIN_LOGIN_URL = "https://www.douyin.com/user/self";
let douyinWin = null;
function openDouyin(){
  douyinWin = window.open(DOUYIN_LOGIN_URL, "_blank", "noopener");
  if(!douyinWin){ document.getElementById("msg").textContent = "浏览器拦截了弹窗，请允许弹窗或手动复制上方链接在浏览器打开登录"; }
}
function copyLink(){
  const url = DOUYIN_LOGIN_URL;
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(url).then(()=>{
      document.getElementById("msg").style.color = "#16a34a";
      document.getElementById("msg").textContent = "✅ 链接已复制，请在浏览器中粘贴打开并登录";
    }).catch(()=>{ window.prompt("复制下方链接：", url); });
  } else {
    window.prompt("复制下方链接：", url);
  }
}
document.getElementById("ssFile").addEventListener("change", function(e){
  const f = e.target.files[0];
  const hint = document.getElementById("fileHint");
  if(!f){ hint.textContent = ""; return; }
  hint.textContent = "已选择文件：" + f.name + "（将随回传一并上传）";
});
async function reportLogin(){
  const msg = document.getElementById("msg");
  msg.textContent = "正在回传登录态...";
  msg.style.color = "#888";
  // 组装请求体：confirmed 始终为 true；若用户选了 storage-state.json 则一并上传
  let cookies = null;
  const fileInput = document.getElementById("ssFile");
  if(fileInput && fileInput.files && fileInput.files[0]){
    try {
      const text = await fileInput.files[0].text();
      const parsed = JSON.parse(text);
      cookies = parsed;  // 期望为 Playwright storage-state 结构（含 cookies 数组）
    } catch(err){
      msg.style.color = "#d97706";
      msg.textContent = "⚠️ 选择的文件不是合法的 JSON：" + err.message;
      return;
    }
  }
  try {
    const r = await fetch("/api/login-callback/" + TOKEN, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ user: USER, confirmed: true, cookies: cookies })
    });
    const d = await r.json();
    if (d.ok) {
      msg.style.color = "#16a34a";
      let tip = d.message || "登录态已保存";
      if(d.has_cookie){ tip += "（已取得 Cookie，可直接发送消息）"; }
      else { tip += "（仅确认登录；如需自动发送，请重新选择 storage-state.json 文件一起上传）"; }
      msg.textContent = "✅ " + tip + " 可关闭此页并返回主页面查看登录状态。";
      if(douyinWin && !douyinWin.closed){ try{ douyinWin.close(); }catch(e){} }
    } else {
      msg.style.color = "#d97706";
      msg.textContent = "⚠️ " + (d.error || "回传失败");
    }
  } catch(err){
    msg.style.color = "#d97706";
    msg.textContent = "网络错误: " + err.message;
  }
}
</script>
</body></html>"""


def start_login_headless(user: str | None = None) -> str:
    """兼容旧调用：方案 C 下不再开无头浏览器，改为提示使用登录链接方式。"""
    return ("请使用「获取登录链接」按钮，在您的设备浏览器中完成抖音登录并回传登录态"
            "（服务器无头登录已被抖音风控拦截，已切换为真机登录方案）。")


def get_login_qr() -> dict:
    """兼容旧接口：方案 C 下不再生成二维码图片，返回未就绪。"""
    return {"qr": None, "updated_at": 0.0, "ready": False,
            "message": "已切换为真机登录方案，请使用登录链接"}


def mark_login_complete() -> str:
    """兼容接口：方案 C 下登录由回传自动完成。"""
    with STATE_LOCK:
        if STATE["login_status"] == "logged_in":
            return "已登录"
    return "请通过登录链接在真机完成登录"


def _append_run_log(msg: str) -> None:
    """把一条消息追加写入运行日志文件，供前端「运行日志」面板展示。

    即使任务在真正进入 run() 之前就失败（例如缺少登录态、配置错误），
    也能在日志面板里看到原因，而不是一直显示「暂无日志」。
    """
    try:
        log_path = PROJECT_ROOT / "artifacts" / "run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} ERROR {msg}\n")
    except Exception:
        pass


async def do_run(dry_run: bool, user: str | None = None) -> None:
    """执行发送流程（按当前用户加载独立的登录态与配置，实现多租户隔离）"""
    global STOP_EVENT
    with STATE_LOCK:
        STATE["run_id"] = STATE.get("run_id", 0) + 1
        STATE["run_status"] = "running"
        STATE["run_message"] = "正在运行..." if not dry_run else "正在干跑验证..."

    # 为本次运行创建停止信号（绑定当前事件循环）；前端「停止」置位后 run() 会优雅退出
    STOP_EVENT = asyncio.Event()

    try:
        # 检查是否有有效的 Playwright 格式 storage-state（登录确认 ≠ 有 Cookie）
        if not has_valid_storage_state(user):
            msg = ("缺少有效的抖音 Cookie 文件。请先在您的设备浏览器登录抖音，"
                   "然后使用「上传登录态」功能（或开发者工具导出 Cookie）上传 storage-state.json 文件。"
                   "仅完成「回传登录态」确认是不够的，服务器需要真实的浏览器 Cookie 才能发送消息。")
            with STATE_LOCK:
                STATE["run_status"] = "failed"
                STATE["run_message"] = msg
            _append_run_log(msg)
            print(f"[RUN] 用户 {user} 缺少有效 Playwright storage state，中止执行")
            return

        # 按用户隔离：把该用户的 storage-state 与 config 路径透传给发送器
        _state = str(user_storage_path(user)) if user else None
        _cfg = str(user_config_path(user)) if user else None
        exit_code = await run_sender(
            dry_run=dry_run,
            storage_state_path=_state,
            task_config_path_override=_cfg,
            stop_event=STOP_EVENT,
        )

        with STATE_LOCK:
            if exit_code == 0:
                STATE["run_status"] = "success"
                STATE["run_message"] = "运行成功" if not dry_run else "干跑验证成功"
            else:
                STATE["run_status"] = "failed"
                STATE["run_message"] = f"运行失败，退出码 {exit_code}"
    except AlreadyRunningError as exc:
        with STATE_LOCK:
            STATE["run_status"] = "failed"
            STATE["run_message"] = f"已有任务在运行: {exc}"
    except ConfigError as exc:
        with STATE_LOCK:
            STATE["run_status"] = "failed"
            STATE["run_message"] = f"配置错误: {exc}"
        _append_run_log(f"配置错误: {exc}")
    except RunStopped as exc:
        with STATE_LOCK:
            STATE["run_status"] = "success"
            STATE["run_message"] = "已手动停止，剩余好友未发送"
        _append_run_log("已手动停止，剩余好友未发送")
        print(f"[RUN] 用户已手动停止发送任务: {exc}")
    except Exception as exc:
        with STATE_LOCK:
            STATE["run_status"] = "failed"
            STATE["run_message"] = f"运行异常: {exc}"
        _append_run_log("运行异常: " + traceback.format_exc())
    finally:
        # 不论正常结束/异常/停止，都清空停止信号，避免影响下次运行
        STOP_EVENT = None


async def enumerate_mutual_friends(user: str) -> list[str]:
    """尽力从抖音「互关」列表识别互关好友昵称（best-effort）。
    安全护栏：
      1) 必须先登录抖音（login_status=logged_in 且有有效 storage-state），否则返回空——
         否则匿名访问抖音会落到用户自己的主页，侧栏里的「我的喜欢/我的收藏/我的作品」
         等会被误识别为好友。
      2) 任何环节失败均返回空列表，由前端回退到手动填写。
    """
    from pathlib import Path as _P
    import re as _re
    # 前置校验：未登录抖音绝不允许做自动识别（否则必抓回主页 UI 文字当好友）
    try:
        if check_login_status(user) != "logged_in" or not has_valid_storage_state(user):
            return []
    except Exception:
        return []
    sp = user_storage_path(user)
    if not sp or not _P(sp).exists():
        return []
    try:
        from playwright.async_api import async_playwright
        names: list[str] = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    storage_state=str(sp), viewport={"width": 1100, "height": 900}
                )
                page = await context.new_page()
                await page.goto("https://www.douyin.com/user/following",
                                wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3500)
                for _ in range(6):
                    cand = await page.evaluate("""() => {
                        const out = [];
                        document.querySelectorAll('a[href*="/user/"]').forEach(a => {
                            const t = a.getAttribute('title') || a.textContent;
                            if (t && t.trim().length > 0 && t.trim().length < 30) out.push(t.trim());
                        });
                        document.querySelectorAll('[class*="name"],[class*="Name"]').forEach(e => {
                            const t = e.textContent;
                            if (t && t.trim().length > 0 && t.trim().length < 30) out.push(t.trim());
                        });
                        return out;
                    }""")
                    for n in cand:
                        if n not in names:
                            names.append(n)
                    await page.mouse.wheel(0, 1200)
                    await page.wait_for_timeout(800)
            finally:
                await browser.close()
        # 严格的非好友噪声过滤（针对抖音主页/侧栏/推荐位常见的 UI 文字）
        bad_keywords = {
            "我", "我的", "主页", "关注", "粉丝", "获赞", "作品", "推荐", "为你推荐",
            "直播", "首页", "朋友", "朋友推荐", "消息", "私密", "私密加锁中", "加锁中",
            "前后再看", "观看历史", "扫一扫", "设置", "更多", "评论", "分享", "举报",
            "娱乐", "知识", "游戏", "二次元", "音乐", "美食", "体育", "旅游", "动物",
            "生活", "时尚", "亲子", "情感", "文化", "同城", "商城", "经验", "热点",
            "点赞", "不喜欢", "不感兴趣", "换一换", "查看更多", "展开", "收起",
            "立即下载", "打开抖音", "搜索", "我的主页", "我的关注",
            "我的粉丝", "我的喜欢", "我的收藏", "我的作品", "我的赞",
            "喜欢的作品", "收藏的作品", "我的订单", "钱包", "客服", "反馈",
            "添加朋友",
        }
        number_re = _re.compile(r"^[\d.,]+\s*(万|天|个|w|d)?$")  # 纯数字 / 数字+单位
        starts_bad = ("我的", "Ta的", "看我的", "换一换")
        substr_bad = ("我的", "私密", "观看历史", "前后", "加锁", "为你推荐", "朋友推荐", "Ta的")
        seen = dict.fromkeys(names)
        clean: list[str] = []
        for n in seen:
            n = (n or "").strip()
            if not n or len(n) > 16:  # 空 / 过长（好友昵称一般 < 16 字）
                continue
            if number_re.match(n):  # 纯数字或数字+万/天/个
                continue
            if any(n.startswith(p) for p in starts_bad):  # "我的Xxx" / "Ta的Xxx"
                continue
            if n in bad_keywords or any(k in n for k in substr_bad):
                continue
            clean.append(n)
        return clean[:200]
    except Exception:
        return []


class GUIHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def send_json(self, data: Any, status: int = 200, set_cookie: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def send_html(self, html: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        # 禁止缓存页面，确保前端改动即时生效（含静态 HTML 与 Nginx 反代层）
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def serve_static(self, rel_path: str) -> None:
        """公开提供 webui/ 下的静态资源（CSS/JS/图片/字体），无需登录。
        仅允许白名单扩展名，并做目录穿越防护，避免暴露项目外文件。"""
        _STATIC_EXT = {".css", ".js", ".png", ".jpg", ".jpeg", ".gif",
                       ".ico", ".svg", ".woff", ".woff2", ".ttf"}
        _MIME = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".ico": "image/x-icon", ".svg": "image/svg+xml",
            ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
        }
        clean = rel_path.lstrip("/")
        if not clean or ".." in clean or clean.startswith("/"):
            self.send_response(403); self.end_headers(); return
        full = WEBUI_DIR / clean
        # 目录穿越防护：解析后的真实路径必须仍在 webui 目录内
        try:
            full.resolve().relative_to((WEBUI_DIR).resolve())
        except Exception:
            self.send_response(403); self.end_headers(); return
        if not full.exists() or not full.is_file():
            self.send_response(404); self.end_headers(); return
        ext = full.suffix.lower()
        if ext not in _STATIC_EXT:
            self.send_response(403); self.end_headers(); return
        try:
            data = full.read_bytes()
        except Exception:
            self.send_response(500); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(ext, "application/octet-stream"))
        self.send_header("X-Content-Type-Options", "nosniff")
        # 静态资源也禁缓存，确保前端改动即时生效
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def current_user(self) -> str | None:
        """当前登录用户（基于 session cookie），未登录返回 None。
        本地版（DEPLOY_MODE=local）：免登录，返回虚拟用户 local。"""
        if DEPLOY_MODE == "local":
            return "local"
        token = webui_auth.parse_cookie(self.headers.get("Cookie"), SESSION_COOKIE)
        return webui_auth.get_session_user(token)

    def auth_status(self) -> tuple[str | None, str]:
        """返回 (用户名, 状态)。状态: ok | need_login | expired
        注意：expired 仍返回用户名（保留 Web 会话），便于用户在页面内续费；
        但会清空抖音凭据，且工具接口会拒绝过期账户，确保到期无法使用。
        本地版（DEPLOY_MODE=local）：永远 ok，默认永久会员。
        """
        if DEPLOY_MODE == "local":
            return "local", "ok"
        token = webui_auth.parse_cookie(self.headers.get("Cookie"), SESSION_COOKIE)
        user = webui_auth.get_session_user(token)
        if not user:
            return None, "need_login"
        if webui_auth.is_expired(user):
            webui_auth.clear_douyin_cookie(user)  # 到期清空抖音凭据，工具不可用
            return user, "expired"
        return user, "ok"

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("请求体过大")
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if DEPLOY_MODE == "local":
            # ---- 本地版：免登录，无官网/登录/后台，直接进控制台 ----
            if path.startswith("/login") or path == "/admin" or path == "/home":
                self.redirect("/app")
                return
            if path == "/":
                html_path = WEBUI_DIR / "index.html"
                self.send_html(html_path.read_text(encoding="utf-8"))
                return
            if path == "/app":
                html_path = WEBUI_DIR / "index.html"
                self.send_html(html_path.read_text(encoding="utf-8"))
                return
        if path in ("/login", "/login.html") or path.startswith("/login/"):
            # 方案 C：/login/<token> 为真机登录中间页（用户在自己的设备浏览器打开）
            if path.startswith("/login/"):
                token = path.split("/login/", 1)[1].strip()
                info = get_login_token(token)
                if not info:
                    self.send_html("<h2 style='text-align:center;margin-top:40px'>登录链接无效或已过期</h2>"
                                   "<p style='text-align:center'>请返回主页面重新获取登录链接。</p>")
                    return
                if info.get("used"):
                    self.send_html("<h2 style='text-align:center;margin-top:40px'>该登录链接已使用</h2>"
                                   "<p style='text-align:center'>请返回主页面查看登录状态。</p>")
                    return
                page = LOGIN_INTERMEDIATE_PAGE.replace("__TOKEN__", token).replace("__USER__", info.get("user") or "")
                self.send_html(page)
                return
            # 普通 /login：Web 登录页（账号体系）
            if self.current_user():
                self.redirect("/app")
                return
            html_path = WEBUI_DIR / "login.html"
            self.send_html(html_path.read_text(encoding="utf-8"))
        elif path == "/admin":
            user, st = self.auth_status()
            if st != "ok":
                self.redirect("/login" + ("?expired=1" if st == "expired" else ""))
                return
            if not webui_auth.is_admin(user):
                self.send_json({"error": "无权限"}, 403)
                return
            html_path = WEBUI_DIR / "admin.html"
            self.send_html(html_path.read_text(encoding="utf-8"))
        elif path == "/":
            # 官网主页（公开，无需登录）；控制台迁移至 /app
            html_path = WEBUI_DIR / "home.html"
            if html_path.exists():
                self.send_html(html_path.read_text(encoding="utf-8"))
            else:
                self.redirect("/login")
        elif path == "/app":
            user, st = self.auth_status()
            if st == "need_login":
                self.redirect("/login" + ("?expired=1" if st == "expired" else ""))
                return
            html_path = WEBUI_DIR / "index.html"
            self.send_html(html_path.read_text(encoding="utf-8"))
        elif path == "/send-code":
            # 公开的验证码发送工具页（无需登录），用于测试邮件发信链路
            html_path = WEBUI_DIR / "send_code.html"
            self.send_html(html_path.read_text(encoding="utf-8"))
        elif path == "/home":
            # 官网主页（公开，无需登录）
            html_path = WEBUI_DIR / "home.html"
            if html_path.exists():
                self.send_html(html_path.read_text(encoding="utf-8"))
            else:
                self.redirect("/")
        elif path.lower().endswith((".css", ".js", ".png", ".jpg", ".jpeg",
                                    ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf")):
            # 静态资源公开提供（CSS/JS/图片等），无需登录
            self.serve_static(path)
        else:
            user, st = self.auth_status()
            if st != "ok":
                self.send_json({"error": "未登录", "need_login": True, "expired": st == "expired"}, 401)
                return
            if path == "/api/state":
                update_state(user)
                with STATE_LOCK:
                    state = dict(STATE)
                    state["mode"] = DEPLOY_MODE  # local / server，前端据此隐藏会员/续费/后台/退出
                    state["username"] = user
                    info = webui_auth.get_user(user) or {}
                    if DEPLOY_MODE == "local":
                        # 本地版：默认永久会员，无过期
                        state["role"] = "admin"
                        state["expires_at"] = None
                        state["login_status"] = "not_logged_in"  # 本地无需登录抖音凭据提示
                    else:
                        state["role"] = info.get("role", "user")
                        state["expires_at"] = info.get("expires_at")
                    self.send_json(state)
            elif path == "/api/config":
                self.send_json(load_config_safe(user))
            elif path == "/api/schedule":
                self.send_json(load_schedule())
            elif path == "/api/logs":
                self.send_json({"logs": read_logs()})
            elif path == "/api/admin/codes":
                if not webui_auth.is_admin(user):
                    self.send_json({"error": "无权限"}, 403)
                    return
                # 支持后台筛选：status（unused/used）、type（week/month/year/permanent）、
                # date_from/date_to（创建时间范围，格式 YYYY-MM-DD）。
                q = parse_qs(parsed.query)
                filters = {
                    "status": (q.get("status") or [""])[0] or None,
                    "type": (q.get("type") or [""])[0] or None,
                    "date_from": (q.get("date_from") or [""])[0] or None,
                    "date_to": (q.get("date_to") or [""])[0] or None,
                }
                self.send_json({"codes": webui_auth.list_codes(filters)})
            elif path == "/api/admin/users":
                if not webui_auth.is_admin(user):
                    self.send_json({"error": "无权限"}, 403)
                    return
                self.send_json({"users": webui_auth.list_users()})
            elif path == "/api/login-qr":
                # 返回无头登录流程生成的二维码（base64 PNG），供手机/远程扫码
                self.send_json(get_login_qr())
            elif path == "/api/login-status":
                # 返回当前用户的登录状态，前端轮询：logging_in / logged_in / not_logged_in
                status = check_login_status(user)
                with STATE_LOCK:
                    # 方案 C：若有未消费的登录 token 且尚未回传，视为 logging_in
                    if status != "logged_in" and any(
                        t.get("user") == user and not t.get("used")
                        for t in LOGIN_TOKENS.values()
                    ):
                        status = "logging_in"
                    self.send_json({"login_status": status})
            elif path == "/api/cookie-info":
                # 只读：返回当前用户已保存的抖音 Cookie 数量与保存时间，便于前端确认持久化状态
                info = {"count": 0, "saved_at": None}
                try:
                    sp = user_storage_path(user) if user else (PROJECT_ROOT / "storage-state.json")
                    if sp and sp.exists():
                        import json as _json
                        try:
                            _d = _json.loads(sp.read_text(encoding="utf-8"))
                            _c = _d.get("cookies") if isinstance(_d, dict) else None
                            if isinstance(_c, list):
                                info["count"] = len(_c)
                        except Exception:
                            pass
                        info["saved_at"] = datetime.fromtimestamp(sp.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
                self.send_json(info)
            else:
                self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        # 经 Nginx 反代时，真实客户端 IP 在 X-Real-IP / X-Forwarded-For 中；
        # 直接取 client_address 会得到 127.0.0.1，导致所有用户被当成同一 IP 误封。
        _xff = self.headers.get("X-Forwarded-For", "")
        _real = self.headers.get("X-Real-IP", "")
        if _real:
            client_ip = _real.strip()
        elif _xff:
            client_ip = _xff.split(",")[0].strip()
        else:
            client_ip = self.client_address[0]

        try:
            # 认证接口做限流，防止暴力破解 / 刷接口
            if path in ("/api/auth/login", "/api/auth/register"):
                ban = _auth_banned(client_ip)
                if ban is not None:
                    self.send_json(
                        {"error": f"该 IP 已被临时锁定，请 {int(ban // 60) + 1} 分钟后再试"}, 429
                    )
                    return
                if _auth_rate_limited(client_ip):
                    self.send_json({"error": "请求过于频繁，请稍后再试"}, 429)
                    return
            # ---- 方案 C 真机登录回传（无需 Web session，靠一次性 token 鉴权）----
            if path.startswith("/api/login-callback/"):
                token = path.split("/api/login-callback/", 1)[1].strip()
                info = get_login_token(token)
                if not info:
                    self.send_json({"error": "token 无效或已过期"}, 404)
                    return
                if info.get("used"):
                    self.send_json({"error": "该登录链接已使用"}, 400)
                    return
                try:
                    body = self.read_body()
                except Exception:
                    body = {}
                # 真机登录回传：confirmed 表示用户已在自己设备完成抖音登录；
                # 若附带 cookies（Playwright storage-state）则一并落盘为该用户 Cookie，否则仅记录确认信号。
                cookies = body.get("cookies")
                if cookies is not None and not isinstance(cookies, (dict, list)):
                    cookies = None
                result = consume_login_token(token, cookies)
                if not result:
                    self.send_json({"error": "token 消费失败"}, 400)
                    return
                has_cookie = result.get("state_path", "").endswith("storage-state.json")
                self.send_json({
                    "ok": True,
                    "message": "登录态已保存，抖音登录成功",
                    "state_path": result.get("state_path"),
                    "has_cookie": has_cookie,
                })
                return

            # ---- 认证接口（无需登录） ----
            if path == "/api/auth/login":
                body = self.read_body()
                username = str(body.get("username", ""))
                password = str(body.get("password", ""))
                if webui_auth.verify(username, password):
                    token = webui_auth.create_session(username.strip())
                    secure = "; Secure" if SESSION_SECURE else ""
                    cookie = (
                        f"{SESSION_COOKIE}={token}; Path=/; Max-Age={webui_auth.SESSION_TTL}; "
                        f"HttpOnly; SameSite=Lax{secure}"
                    )
                    info = webui_auth.get_user(username.strip()) or {}
                    self.send_json(
                        {"ok": True, "username": username.strip(), "role": info.get("role", "user")},
                        set_cookie=cookie,
                    )
                else:
                    # 管理员账号失败不计入 IP 封禁（管理员用强密码，封了无法登录）
                    if webui_auth.is_admin(username.strip()):
                        self.send_json({"error": "用户名或密码错误"}, 401)
                        return
                    # 非管理员：记录失败（可能触发封禁）
                    remain = _auth_record_fail(client_ip)
                    if remain is not None:
                        self.send_json(
                            {"error": f"密码错误次数过多，该 IP 已被锁定 5 分钟"}, 429
                        )
                    else:
                        self.send_json({"error": "用户名或密码错误"}, 401)
                return
            elif path == "/api/auth/login-by-code":
                body = self.read_body()
                ok, result = webui_auth.login_by_code(
                    str(body.get("email", "")), str(body.get("code", ""))
                )
                if ok:
                    username = result["username"]
                    token = webui_auth.create_session(username)
                    secure = "; Secure" if SESSION_SECURE else ""
                    cookie = (
                        f"{SESSION_COOKIE}={token}; Path=/; Max-Age={webui_auth.SESSION_TTL}; "
                        f"HttpOnly; SameSite=Lax{secure}"
                    )
                    self.send_json(
                        {"ok": True, "username": username, "role": result["role"]},
                        set_cookie=cookie,
                    )
                else:
                    self.send_json({"error": result}, 401)
                return
            elif path == "/api/auth/register":
                body = self.read_body()
                ok, message = webui_auth.register(
                    str(body.get("username", "")),
                    str(body.get("password", "")),
                    str(body.get("invite_code", "")),
                    str(body.get("email", "")),
                    str(body.get("email_code", "")),
                )
                if ok:
                    self.send_json({"ok": True, "message": message})
                else:
                    # 注册失败也计入封禁（防止批量试邀请码/撞库）
                    remain = _auth_record_fail(client_ip)
                    if remain is not None:
                        self.send_json(
                            {"error": "注册失败次数过多，该 IP 已被锁定 5 分钟"}, 429
                        )
                    else:
                        self.send_json({"error": message}, 400)
                return
            elif path == "/api/auth/logout":
                token = webui_auth.parse_cookie(self.headers.get("Cookie"), SESSION_COOKIE)
                webui_auth.destroy_session(token)
                secure = "; Secure" if SESSION_SECURE else ""
                cookie = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure}"
                self.send_json({"ok": True}, set_cookie=cookie)
                return
            # ---- 校验邮箱验证码（注册 / 验证码登录预校验 / 找回密码） ----
            elif path == "/api/auth/verify-email-code":
                body = self.read_body()
                ok, result = webui_auth.verify_email_code(
                    str(body.get("email", "")), str(body.get("code", ""))
                )
                self.send_json({"ok": ok, "message": result} if ok else {"error": result}, 200 if ok else 400)
                return
            # ---- 用邮箱 + 重置令牌重置密码（找回密码第二步） ----
            elif path == "/api/auth/reset-password":
                body = self.read_body()
                ok, message = webui_auth.reset_password(
                    str(body.get("email", "")),
                    str(body.get("reset_token", "")),
                    str(body.get("new_password", "")),
                )
                self.send_json({"ok": ok, "message": message} if ok else {"error": message}, 200 if ok else 400)
                return
            # ---- 发送邮箱验证码（注册校验 / 验证码登录） ----
            elif path == "/api/auth/send-email-code":
                body = self.read_body()
                email = str(body.get("email", ""))
                kind = str(body.get("kind", "register"))
                ok, message, code = webui_auth.send_email_code(email, kind)
                if ok:
                    # 开发模式下 code 非空，前端直接展示；生产模式为 None
                    self.send_json({"ok": True, "message": message, "code": code}, 200)
                else:
                    self.send_json({"error": message}, 400)
                return
            # ---- 续费：允许过期用户调用（用于重新开通） ----
            elif path == "/api/auth/renew":
                user = self.current_user()
                if not user:
                    self.send_json({"error": "未登录", "need_login": True}, 401)
                    return
                body = self.read_body()
                ok, message = webui_auth.renew_membership(user, str(body.get("code", "")))
                if ok:
                    self.send_json({"ok": True, "message": message, "expires_at": webui_auth.get_user(user).get("expires_at")})
                else:
                    self.send_json({"error": message}, 400)
            # ---- 退出抖音登录：删除当前用户的 Cookie 与登录标记 ----
            if path == "/api/login-logout":
                _u = self.current_user()
                if not _u:
                    self.send_json({"error": "未登录", "need_login": True}, 401)
                    return
                removed = []
                for p in (user_storage_path(_u), user_login_marker_path(_u)):
                    try:
                        if p.exists():
                            p.unlink()
                            removed.append(p.name)
                    except Exception:
                        pass
                with STATE_LOCK:
                    # 仅在该用户确实无 Cookie 时把全局登录态重置为未登录，避免影响其他用户
                    STATE["login_status"] = "not_logged_in"
                    STATE["run_message"] = "已退出抖音登录（Cookie 已删除）"
                self.send_json({"ok": True, "message": "已退出抖音登录，Cookie 已删除", "removed": removed})
                return
            # ---- 以下接口均需登录 ----
            if not self.current_user():
                self.send_json({"error": "未登录", "need_login": True}, 401)
                return
            user = self.current_user()  # 统一获取当前登录用户，供各接口做多租户隔离
            if not user:
                # 任何写操作都必须基于已登录用户，绝不落到全局共享文件（杜绝跨账号串数据）
                self.send_json({"error": "未登录或会话失效，请重新登录", "need_login": True}, 401)
                return
            if path == "/api/config":
                config = self.read_body()
                save_config(config, user)
                self.send_json({"ok": True})
            elif path == "/api/schedule":
                schedule = self.read_body()
                save_schedule(schedule)
                with STATE_LOCK:
                    STATE["schedule"] = schedule
                self.send_json({"ok": True, "message": "定时配置已保存"})
            elif path == "/api/friends/sync":
                # 尽力从抖音识别互关好友；失败则回退手动
                # 预校验：未登录抖音时直接拒收，避免抓到主页 UI 文字当好友
                if check_login_status(user) != "logged_in" or not has_valid_storage_state(user):
                    self.send_json({
                        "ok": False, "manual": True,
                        "error": "请先在「账号与登录」中上传抖音登录态（Cookie-Editor 导出的 JSON 或 storage-state.json），然后再同步互关好友。也可手动粘贴好友昵称，每行一个。"
                    })
                    return
                try:
                    fut = run_async(enumerate_mutual_friends(user))
                    friends = fut.result(timeout=120)
                    if friends:
                        cfg = load_config_safe(user)
                        cfg["friends"] = friends
                        save_config(cfg, user)
                        self.send_json({"ok": True, "friends": friends})
                    else:
                        self.send_json({
                            "ok": False, "manual": True,
                            "error": "未能从抖音识别到互关好友，请手动粘贴好友昵称（每行一个）。"
                        })
                except Exception as _e:
                    self.send_json({"ok": False, "manual": True, "error": "同步失败：" + str(_e)})
            elif path in ("/api/login", "/api/login-start", "/api/login-complete", "/api/login-upload", "/api/run"):
                # 到期账户禁止驱动抖音相关操作，仅允许续费
                if webui_auth.is_expired(self.current_user()):
                    self.send_json({"error": "会员已到期，请先续费", "expired": True}, 403)
                    return
                if path in ("/api/login", "/api/login-start"):
                    # 方案 C：生成一次性登录 token，返回登录链接供用户在真机打开
                    token = create_login_token(user)
                    host_hdr = self.headers.get("Host") or f"{HOST}:{PORT}"
                    scheme = "https" if SESSION_SECURE else "http"
                    login_url = f"{scheme}://{host_hdr}/login/{token}"
                    self.send_json({
                        "ok": True,
                        "token": token,
                        "login_url": login_url,
                        "expire_seconds": LOGIN_TOKEN_EXPIRE,
                        "message": "请在您的手机/电脑浏览器打开登录链接，完成抖音登录后回传",
                    })
                elif path == "/api/login-complete":
                    message = mark_login_complete()
                    self.send_json({"ok": True, "message": message})
                elif path == "/api/login-upload":
                    # 接收抖音 Cookie 并落盘为 Playwright 标准 storage-state.json。
                    # 兼容两种来源：
                    #   1) Cookie-Editor 导出的裸数组 [ {name,value,domain,...}, ... ]
                    #   2) Playwright 标准 storage-state 对象 { "cookies": [...], "origins": [...] }
                    #   前端 injectCookie() 统一以 { "cookies": <上述任一> } 提交，后端再做归一化。
                    try:
                        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                        text = raw.decode("utf-8", errors="replace").strip()
                        if not text:
                            self.send_json({"error": "未接收到内容"}, 400)
                            return
                        payload = json.loads(text)
                        # 归一化为 storage_state dict
                        if isinstance(payload, list):
                            # Cookie-Editor 裸数组
                            storage_state = {"cookies": payload, "origins": []}
                        elif isinstance(payload, dict):
                            inner = payload.get("cookies")
                            if isinstance(inner, list):
                                # 标准 storage-state 或 {cookies:[...]}
                                storage_state = payload
                            elif isinstance(inner, dict) and "cookies" in inner:
                                # 嵌套一层：{cookies: {cookies:[...], origins:[...]}}
                                storage_state = inner
                            else:
                                self.send_json({"error": "格式错误：无法识别的 Cookie 结构。"
                                               "请使用 Cookie-Editor 的『Export as JSON』，或 Playwright 导出的 storage state。"}, 400)
                                return
                        else:
                            self.send_json({"error": "格式错误：内容必须是 JSON 数组或对象"}, 400)
                            return
                        # 归一化 sameSite：Cookie-Editor 导出的 "no_restriction" / "unspecified"
                        # 等取值会被 Playwright 的 storage_state 拒绝（仅接受 Strict|Lax|None），
                        # 不处理会导致 browser.new_context 抛异常、干跑/发送运行异常。
                        _SAMESITE_MAP = {
                            "no_restriction": "None",
                            "no-restriction": "None",
                            "unspecified": "Lax",
                            "": "Lax",
                            "strict": "Strict",
                            "lax": "Lax",
                            "none": "None",
                        }
                        for _c in storage_state.get("cookies", []):
                            if not isinstance(_c, dict):
                                continue
                            _ss = str(_c.get("sameSite", "")).strip().lower()
                            if _ss in ("strict", "lax", "none"):
                                _c["sameSite"] = _ss.capitalize()
                            elif _ss in _SAMESITE_MAP:
                                _c["sameSite"] = _SAMESITE_MAP[_ss]
                            else:
                                # 非法/未知值：删除该字段，让 Playwright 使用默认值
                                _c.pop("sameSite", None)
                        cookies = storage_state.get("cookies")
                        if not isinstance(cookies, list) or not cookies:
                            self.send_json({"error": "格式错误：cookies 必须是非空数组。"}, 400)
                            return
                        # 按当前用户隔离落盘（防止路径穿越：只允许写入该用户目录）
                        _u = user
                        state_path = user_storage_path(_u) if _u else (PROJECT_ROOT / "storage-state.json")
                        state_path.parent.mkdir(parents=True, exist_ok=True)
                        state_path.write_text(
                            json.dumps(storage_state, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                        # 写入登录确认标记：check_login_status() 依赖该文件判定 logged_in，
                        # 否则仅改内存 STATE 在重启/重新判定时会回退为未登录。
                        marker_path = user_login_marker_path(_u) if _u else (PROJECT_ROOT / "login-confirmed.json")
                        marker_path.parent.mkdir(parents=True, exist_ok=True)
                        from datetime import datetime
                        marker_path.write_text(json.dumps({
                            "confirmed": True,
                            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "count": len(cookies),
                            "source": "manual_inject",
                        }, ensure_ascii=False, indent=2), encoding="utf-8")
                        with STATE_LOCK:
                            STATE["login_status"] = "logged_in"
                            STATE["run_message"] = "登录态（Cookie）已通过上传更新，可以执行发送"
                        self.send_json({"ok": True, "message": "Cookie 文件已保存，可以开始发送消息"})
                    except json.JSONDecodeError:
                        self.send_json({"error": "不是合法的 JSON"}, 400)
                    except Exception as exc:  # noqa: BLE001
                        self.send_json({"error": "保存失败: " + str(exc)}, 500)
                elif path == "/api/run":
                    body = self.read_body()
                    dry_run = body.get("dry_run", False)
                    run_async(do_run(dry_run, user))
                    self.send_json({"ok": True, "message": "任务已启动"})
                elif path == "/api/run/stop":
                    if STOP_EVENT is not None and LOOP is not None:
                        try:
                            LOOP.call_soon_threadsafe(STOP_EVENT.set)
                        except Exception:
                            STOP_EVENT.set()
                        self.send_json({"ok": True, "message": "已发送停止指令"})
                    else:
                        self.send_json({"ok": True, "message": "当前没有运行中的任务"})
            elif path == "/api/admin/generate":
                if not webui_auth.is_admin(self.current_user()):
                    self.send_json({"error": "无权限"}, 403)
                    return
                try:
                    body = self.read_body()
                    count = int(body.get("count", 1))
                except (ValueError, TypeError):
                    self.send_json({"error": "数量需为整数"}, 400)
                    return
                ok, result = webui_auth.generate_codes(
                    self.current_user(), str(body.get("card_type", "")), count
                )
                if ok:
                    self.send_json({"ok": True, "codes": result})
                else:
                    self.send_json({"error": result}, 400)
            elif path == "/api/admin/codes/delete":
                if not webui_auth.is_admin(self.current_user()):
                    self.send_json({"error": "无权限"}, 403)
                    return
                try:
                    body = self.read_body()
                except Exception:
                    self.send_json({"error": "请求体解析失败"}, 400)
                    return
                codes = body.get("codes")
                if not isinstance(codes, list) or not codes:
                    self.send_json({"error": "codes 需为非空数组"}, 400)
                    return
                deleted = webui_auth.delete_codes(codes)
                self.send_json({"ok": True, "deleted": deleted})
            elif path == "/api/admin/codes/mark":
                if not webui_auth.is_admin(self.current_user()):
                    self.send_json({"error": "无权限"}, 403)
                    return
                try:
                    body = self.read_body()
                except Exception:
                    self.send_json({"error": "请求体解析失败"}, 400)
                    return
                codes = body.get("codes")
                if not isinstance(codes, list) or not codes:
                    self.send_json({"error": "codes 需为非空数组"}, 400)
                    return
                used = bool(body.get("used"))
                updated = webui_auth.set_codes_used(codes, used)
                self.send_json({"ok": True, "updated": updated})
            else:
                self.send_json({"error": "not found"}, 404)
        except ValueError as exc:
            if str(exc) == "请求体过大":
                self.send_json({"error": "请求体过大"}, 413)
            else:
                self.send_json({"error": str(exc)}, 500)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


def main() -> None:
    init_loop()
    webui_auth.ensure_admin()  # 首次运行创建管理员账号

    port = PORT
    server = ThreadingHTTPServer((HOST, port), GUIHandler)
    url_host = "localhost" if HOST in ("127.0.0.1", "0.0.0.0") else HOST
    print(f"抖音自动续火花 - 可视化界面")
    print(f"监听地址: {HOST}:{port}")
    if HOST in ("127.0.0.1", "localhost"):
        print(f"请在浏览器中打开: http://localhost:{port}")
    if webui_auth.user_count() == 0:
        print(f"首次使用：请先在页面中注册账号")
    print(f"按 Ctrl+C 停止服务器")

    # 启动定时任务线程
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()

    # 仅在本机模式自动打开浏览器（服务器部署时无桌面，跳过）
    if HOST in ("127.0.0.1", "localhost"):
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止...")
        server.shutdown()


if __name__ == "__main__":
    main()
