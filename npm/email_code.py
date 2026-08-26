"""
抖音自动续火花 - 邮箱验证码模块（注册校验 + 找回密码）

发信使用 Resend SMTP 中继（smtp.resend.com:465，登录账号固定为 resend，密码即 Resend API Key）。
配置全部从环境变量 / .env 读取，部署时填写、不写死在代码里：
  - RESEND_API_KEY   Resend 的 API Key（必填，形如 re_xxxx，同时作为 SMTP 登录密码）
  - MAIL_FROM        发件人地址（默认 noreply@dongdongclub.shop）
  - MAIL_FROM_NAME   发件人显示名（默认「抖音自动续火花」）

若未配置 RESEND_API_KEY，本模块不会崩溃，而是把所有验证码打印到服务端日志（开发/联调模式）。
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import threading
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

# 支持从项目根目录的 .env 读取密钥（不进代码仓库）。
# 环境变量优先，其次 .env 文件。
def _load_dotenv() -> None:
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        env_path = os.path.join(base, ".env")
        if not os.path.exists(env_path):
            return
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

_load_dotenv()

CODE_TTL = 10 * 60          # 验证码有效期 10 分钟
CODE_RESEND = 60            # 同一邮箱最小重发间隔 60 秒
_MAX_FAIL = 5               # 验证码最多尝试次数
_RESEND_SMTP_HOST = "smtp.resend.com"
_RESEND_SMTP_PORT = 465
_RESEND_SMTP_USER = "resend"

_LOCK = threading.Lock()
# email -> { code, expires, last_sent, fails, reset_token, reset_token_exp }
_STORE: dict[str, dict] = {}


def _cfg() -> dict:
    return {
        "api_key": os.environ.get("RESEND_API_KEY", ""),
        "sender": os.environ.get("MAIL_FROM", "") or "noreply@dongdongclub.shop",
        "sender_name": os.environ.get("MAIL_FROM_NAME", "") or "抖音自动续火花",
    }


def mail_configured() -> bool:
    return bool(_cfg()["api_key"])


def _send_mail(to_addr: str, subject: str, text_body: str, html_body: str | None = None) -> bool:
    c = _cfg()
    if not mail_configured():
        print(f"[MAIL-LOG-MODE] 收件人={to_addr} 主题={subject} 内容={text_body}")
        return True  # 日志模式下视为发送成功，便于联调
    try:
        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        else:
            msg = MIMEText(text_body, "plain", "utf-8")
        msg["From"] = formataddr((c["sender_name"], c["sender"]))
        msg["To"] = to_addr
        msg["Subject"] = Header(subject, "utf-8")
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            _RESEND_SMTP_HOST, _RESEND_SMTP_PORT, timeout=15, context=ctx
        ) as s:
            s.login(_RESEND_SMTP_USER, c["api_key"])
            s.sendmail(c["sender"], [to_addr], msg.as_string().encode("utf-8"))
        return True
    except Exception as exc:  # 发信失败不应让接口崩溃
        print(f"[MAIL-ERROR] Resend SMTP 发送失败 to={to_addr} err={exc}")
        return False


def _build_html(code: str, purpose: str, ttl_min: int) -> str:
    """生成验证码邮件的 HTML 排版（深色卡片风格）。"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#0e0e13;font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0e0e13;padding:24px 0;">
    <tr><td align="center">
      <div style="width:100%;max-width:420px;background:#16161d;border:1px solid #2a2a36;border-radius:16px;padding:32px 28px;box-shadow:0 16px 48px rgba(0,0,0,.5);">
        <div style="font-size:18px;font-weight:700;color:#ffffff;margin-bottom:6px;">抖音自动续火花</div>
        <div style="font-size:13px;color:#8a8a96;margin-bottom:24px;">安全验证码</div>
        <div style="font-size:14px;color:#cfcfd8;line-height:1.6;margin-bottom:18px;">
          你正在使用 <b style="color:#a78bfa;">{purpose}</b> 功能，下面是你的邮箱验证码：
        </div>
        <div style="background:#1f1f2b;border:1px dashed #6c5ce7;border-radius:12px;padding:20px;text-align:center;margin-bottom:18px;">
          <span style="font-size:34px;font-weight:800;letter-spacing:8px;color:#ffffff;font-family:'Courier New',monospace;">{code}</span>
        </div>
        <div style="font-size:13px;color:#8a8a96;line-height:1.6;">
          该验证码 <b style="color:#cfcfd8;">{ttl_min} 分钟</b> 内有效，请勿转发或泄露给他人。若非本人操作，请忽略此邮件。
        </div>
        <div style="margin-top:26px;padding-top:16px;border-top:1px solid #2a2a36;font-size:12px;color:#5f5f6b;">
          本邮件由系统自动发送，请勿直接回复。
        </div>
      </div>
    </td></tr>
  </table>
</body>
</html>"""


def send_code(email: str, kind: str = "register") -> tuple[bool, str, str | None]:
    """生成并发送验证码。kind 用于邮件文案区分（register/login/reset）。
    返回 (成功, 消息, 验证码明文)。
    - 已配置真实发信（Resend SMTP）：code 返回 None（不暴露，验证码发到邮箱）。
    - 未配置发信（开发/联调模式）：code 原样返回，由前端直接展示，便于联调。
    """
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 120:
        return False, "邮箱格式不正确", None
    with _LOCK:
        now = time.time()
        rec = _STORE.get(email)
        if rec and now - rec.get("last_sent", 0) < CODE_RESEND:
            return False, f"请求过于频繁，请 {int(CODE_RESEND - (now - rec['last_sent']))} 秒后再试", None
        import secrets
        code = "".join(secrets.choice("0123456789") for _ in range(6))
        _STORE[email] = {
            "code": code,
            "expires": now + CODE_TTL,
            "last_sent": now,
            "fails": 0,
        }
    subject = "抖音自动续火花 - 邮箱验证码"
    purpose = {"register": "注册账号", "login": "验证码登录", "reset": "找回密码"}.get(kind, "验证邮箱")
    text = f"您的验证码是：{code}（{CODE_TTL // 60} 分钟内有效，请勿泄露给他人）"
    html = _build_html(code, purpose, CODE_TTL // 60)
    ok = _send_mail(email, subject, text, html)
    if ok:
        if mail_configured():
            return True, "验证码已发送，请查收邮箱", None
        return True, "验证码已生成（开发模式，请查看页面显示）", code
    return False, "邮件发送失败，请稍后重试", None


def verify_code(email: str, code: str) -> tuple[bool, str]:
    """校验验证码，验证成功后返回一次性 reset_token 用于改密码。"""
    email = (email or "").strip().lower()
    code = (code or "").strip()
    with _LOCK:
        rec = _STORE.get(email)
        if not rec:
            return False, "请先获取验证码"
        if rec["expires"] < time.time():
            _STORE.pop(email, None)
            return False, "验证码已过期，请重新获取"
        if rec.get("fails", 0) >= _MAX_FAIL:
            _STORE.pop(email, None)
            return False, "验证码错误次数过多，请重新获取"
        if rec["code"] != code:
            rec["fails"] = rec.get("fails", 0) + 1
            return False, "验证码错误"
        import secrets
        reset_token = secrets.token_urlsafe(24)
        rec["reset_token"] = reset_token
        rec["reset_token_exp"] = time.time() + 10 * 60
        return True, reset_token


def consume_reset_token(email: str, token: str) -> bool:
    """校验重置令牌是否有效（改密码前调用）。"""
    email = (email or "").strip().lower()
    token = (token or "").strip()
    with _LOCK:
        rec = _STORE.get(email)
        if not rec:
            return False
        if rec.get("reset_token") != token:
            return False
        if rec.get("reset_token_exp", 0) < time.time():
            return False
        return True


def clear_after_reset(email: str) -> None:
    """改密成功后清理该邮箱的验证码与令牌。"""
    with _LOCK:
        _STORE.pop(email, None)
