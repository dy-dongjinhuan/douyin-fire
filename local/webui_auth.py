"""
抖音自动续火花 - Web 界面用户认证与会员卡密模块
纯标准库实现：PBKDF2 密码哈希 + 内存 session 管理 + 邀请码/会员到期
用户数据保存在 data/users.json，卡密数据保存在 data/codes.json
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

import email_code

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
USERS_FILE = DATA_DIR / "users.json"
CODES_FILE = DATA_DIR / "codes.json"

PBKDF2_ITER, = (120_000,)
SESSION_TTL = 7 * 24 * 3600  # session 有效期 7 天
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fa5]{2,20}$")
PASSWORD_MIN_LEN = 8

# 管理员账号（可选）：仅当显式设置 ADMIN_PASSWORD 环境变量时才预创建管理员账号；
# 未设置时保持 users 为空，交由「首次注册账户自动成为管理员」流程处理。
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")  # None = 不预创建管理员

# 卡类型定义：天数（None 表示永久）
CARD_TYPES = {
    "week":       {"label": "周卡",   "days": 7,   "prefix": "WK"},
    "month":      {"label": "月卡",   "days": 30,  "prefix": "MO"},
    "year":       {"label": "年卡",   "days": 365, "prefix": "YR"},
    "permanent":  {"label": "永久卡", "days": None, "prefix": "PE"},
}

_LOCK = threading.Lock()
# token -> {"username": str, "expires": float}
_SESSIONS: dict[str, dict] = {}


# ---------- 用户存储 ----------

def _load_users() -> dict[str, dict]:
    if not USERS_FILE.exists():
        return {}
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_users(users: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)
    return digest.hex()


def user_count() -> int:
    """已注册用户数（不含管理员占位）"""
    users = _load_users()
    return sum(1 for u in users.values() if u.get("role") != "admin")


def ensure_admin() -> None:
    global ADMIN_PASSWORD
    """启动时按需确保管理员账号存在。

    设计原则（对应「服务器首次部署时，第一个注册账户即管理员、免邮箱验证」）：
    - 仅当显式设置 ADMIN_PASSWORD 环境变量时，才预创建/同步管理员账号
      （默认用户名 admin，可用 ADMIN_USER 覆盖）；
    - 未设置 ADMIN_PASSWORD 时**不预创建**任何管理员，保持 users 为空，
      交由 register() 的「首个注册账户自动成为管理员」流程处理。
    """
    with _LOCK:
        users = _load_users()
        existing = users.get(ADMIN_USER)
        if existing and existing.get("role") == "admin":
            # 环境变量设置了密码则以环境为准，保证部署后密码可旋转
            if ADMIN_PASSWORD is not None:
                salt = bytes.fromhex(existing["salt"])
                if existing.get("hash") != _hash_password(ADMIN_PASSWORD, salt):
                    new_salt = secrets.token_bytes(16)
                    users[ADMIN_USER]["salt"] = new_salt.hex()
                    users[ADMIN_USER]["hash"] = _hash_password(ADMIN_PASSWORD, new_salt)
                    _save_users(users)
                    print("[AUTH] 管理员密码已按环境变量同步更新")
            # 未设置 ADMIN_PASSWORD：保留现有凭据，不做改动
        else:
            if ADMIN_PASSWORD is None:
                # 未提供预置管理员密码：保持 users 为空，
                # 首个通过注册表单创建的账户将自动成为管理员（免邮箱验证、免邀请码）
                return
            salt = secrets.token_bytes(16)
            users[ADMIN_USER] = {
                "salt": salt.hex(),
                "hash": _hash_password(ADMIN_PASSWORD, salt),
                "role": "admin",
                "expires_at": None,  # 管理员永久有效
                "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            _save_users(users)
            print(f"[AUTH] 已按环境变量预创建管理员账号：{ADMIN_USER}")
            _save_users(users)


def register(username: str, password: str, invite_code: str, email: str = "", email_code: str = "") -> tuple[bool, str]:
    """注册新用户，返回 (成功, 消息)。

    规则：
    - **首个注册账户自动成为管理员**（免邮箱验证、免邀请码、永久有效），
      用于服务器首次部署时快速建立管理员账号；
    - 后续账户：需有效邀请码 + 邮箱验证码。
    """
    username = (username or "").strip()
    password = password or ""
    invite_code = (invite_code or "").strip().upper()
    email = (email or "").strip().lower()
    email_code = (email_code or "").strip()
    if not USERNAME_RE.match(username):
        return False, "用户名需为 2-20 位字母、数字、下划线或中文"
    if len(password) < PASSWORD_MIN_LEN:
        return False, f"密码至少 {PASSWORD_MIN_LEN} 位"

    with _LOCK:
        users = _load_users()
        is_first_user = len(users) == 0  # 首个账户：免验证、自动管理员

        if username in users:
            return False, "用户名已被注册"
        if not is_first_user:
            # 后续账户的邮箱校验
            if not email or "@" not in email or len(email) > 120:
                return False, "邮箱格式不正确"
            if find_user_by_email(email):
                return False, "该邮箱已被其他账号绑定"
            # 校验邮箱验证码（必须已通过验证拿到 reset_token，等价于验证码正确）
            ok_code, _ = verify_email_code(email, email_code)
            if not ok_code:
                return False, "邮箱验证码错误或已过期"
            ok, card_type = consume_code(invite_code)
            if not ok:
                return False, "邀请码无效或已被使用"
            expires_at = _calc_expiry(card_type)
        else:
            # 首个账户：免邀请码、免邮箱验证、管理员永久有效
            card_type = "permanent"
            expires_at = None  # 永久

        salt = secrets.token_bytes(16)
        users[username] = {
            "salt": salt.hex(),
            "hash": _hash_password(password, salt),
            "role": "admin" if is_first_user else "user",
            "expires_at": expires_at,
            "card_type": card_type,
            "email": email,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save_users(users)
    if not is_first_user:
        mark_code_used(invite_code, username)
        email_code.clear_after_reset(email)
    return True, "注册成功" + ("（首个账户，已自动设为管理员）" if is_first_user else "，请登录")


def login_by_code(email: str, code: str) -> tuple[bool, str | dict]:
    """邮箱验证码登录：校验验证码后直接登录，返回 (成功, 消息或用户信息)。"""
    email = (email or "").strip().lower()
    code = (code or "").strip()
    if not email:
        return False, "请填写邮箱"
    username = find_user_by_email(email)
    if not username:
        return False, "该邮箱未注册"
    ok_code, _ = verify_email_code(email, code)
    if not ok_code:
        return False, "邮箱验证码错误或已过期"
    email_code.clear_after_reset(email)
    info = get_user(username) or {}
    return True, {"username": username, "role": info.get("role", "user")}



def verify(username: str, password: str) -> bool:
    """校验用户名密码"""
    users = _load_users()
    info = users.get((username or "").strip())
    if not info:
        return False
    try:
        salt = bytes.fromhex(info["salt"])
    except Exception:
        return False
    return hmac.compare_digest(info["hash"], _hash_password(password, salt))


def is_admin(username: str | None) -> bool:
    if not username:
        return False
    users = _load_users()
    info = users.get(username)
    return bool(info and info.get("role") == "admin")


def find_user_by_email(email: str) -> str | None:
    """根据邮箱反查用户名（邮箱唯一）。"""
    email = (email or "").strip().lower()
    if not email:
        return None
    for name, info in _load_users().items():
        if (info.get("email") or "").strip().lower() == email:
            return name
    return None


def send_email_code(email: str, kind: str = "register") -> tuple[bool, str, str | None]:
    """发送邮箱验证码（包装 email_code 模块），返回 (成功, 消息, 验证码明文)。"""
    return email_code.send_code(email, kind)


def verify_email_code(email: str, code: str) -> tuple[bool, str]:
    """校验验证码并返回一次性重置令牌。"""
    return email_code.verify_code(email, code)


def reset_password(email: str, token: str, new_password: str) -> tuple[bool, str]:
    """用邮箱 + 重置令牌重置密码。"""
    email = (email or "").strip().lower()
    new_password = new_password or ""
    if len(new_password) < PASSWORD_MIN_LEN:
        return False, f"密码至少 {PASSWORD_MIN_LEN} 位"
    if not email_code.consume_reset_token(email, token):
        return False, "重置令牌无效或已过期，请重新获取验证码"
    username = find_user_by_email(email)
    if not username:
        return False, "该邮箱未绑定任何账号"
    with _LOCK:
        users = _load_users()
        if username not in users:
            return False, "账号不存在"
        salt = secrets.token_bytes(16)
        users[username]["salt"] = salt.hex()
        users[username]["hash"] = _hash_password(new_password, salt)
        _save_users(users)
    email_code.clear_after_reset(email)
    return True, "密码已重置，请使用新密码登录"


def get_user(username: str) -> dict | None:
    return _load_users().get(username)


def _calc_expiry(card_type: str) -> str | None:
    """根据卡类型计算到期时间（ISO 字符串），永久卡返回 None"""
    conf = CARD_TYPES.get(card_type)
    if not conf or conf["days"] is None:
        return None
    dt = datetime.datetime.now() + datetime.timedelta(days=conf["days"])
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def is_expired(username: str) -> bool:
    """会员是否到期；永久卡永远不过期；管理员不过期"""
    info = get_user(username)
    if not info:
        return True
    if info.get("role") == "admin" or info.get("expires_at") is None:
        return False
    try:
        exp = datetime.datetime.fromisoformat(info["expires_at"])
    except Exception:
        return False
    return datetime.datetime.now() > exp


def clear_douyin_cookie(username: str | None = None) -> None:
    """到期时清空抖音登录凭据（storage-state.json）。

    优先写入空 cookie 实现「清空」效果（避免直接删除触发文件安全策略），
    随后再尝试删除文件；在允许删除的环境下会一并移除。
    支持按用户名清除多租户目录下的 cookie，未指定用户时仅清全局路径。
    """
    paths_to_clear = []

    # 全局路径（兼容旧逻辑）
    global_path = PROJECT_ROOT / "storage-state.json"
    if global_path.exists():
        paths_to_clear.append(global_path)

    # 多租户用户路径
    if username:
        user_dir = PROJECT_ROOT / "data" / "users" / str(username)
        user_state = user_dir / "storage-state.json"
        user_marker = user_dir / "login-confirmed.json"
        if user_state.exists():
            paths_to_clear.append(user_state)
        if user_marker.exists():
            paths_to_clear.append(user_marker)

    for state_path in paths_to_clear:
        try:
            state_path.write_text(json.dumps({"cookies": []}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        try:
            state_path.unlink()
        except Exception:
            pass


# ---------- 邀请码 ----------

def _load_codes() -> dict[str, dict]:
    if not CODES_FILE.exists():
        return {}
    try:
        data = json.loads(CODES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_codes(codes: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CODES_FILE.write_text(json.dumps(codes, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_codes(admin_username: str, card_type: str, count: int) -> tuple[bool, str | list[str]]:
    """管理员生成邀请码，返回 (成功, 消息或卡密列表)"""
    if not is_admin(admin_username):
        return False, "无权限"
    if card_type not in CARD_TYPES:
        return False, "无效的卡类型"
    if not (1 <= count <= 100):
        return False, "数量需在 1-100 之间"
    conf = CARD_TYPES[card_type]
    codes = _load_codes()
    new_codes = []
    for _ in range(count):
        while True:
            code = conf["prefix"] + "-" + secrets.token_hex(4).upper()
            if code not in codes:
                break
        codes[code] = {
            "type": card_type,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "used_by": None,
        }
        new_codes.append(code)
    _save_codes(codes)
    return True, new_codes


def consume_code(code: str) -> tuple[bool, str | None]:
    """核销邀请码，返回 (是否有效, 卡类型)"""
    if not code:
        return False, None
    codes = _load_codes()
    info = codes.get(code)
    if not info or info.get("used_by"):
        return False, None
    info["used_by"] = "pending"  # 注册成功后再落库用户名
    _save_codes(codes)
    return True, info["type"]


def mark_code_used(code: str,  username: str) -> None:
    codes = _load_codes()
    if code in codes:
        codes[code]["used_by"] = username
        _save_codes(codes)


def renew_membership(username: str, code: str) -> tuple[bool, str]:
    """已注册用户用卡密续费/开通时长。返回 (成功, 消息/新到期时间)"""
    code = (code or "").strip().upper()
    if not code:
        return False, "请输入卡密"
    if is_admin(username):
        return False, "管理员无需续费"

    with _LOCK:
        users = _load_users()
        info = users.get(username)
        if not info:
            return False, "用户不存在"
        codes = _load_codes()
        cinfo = codes.get(code)
        if not cinfo or cinfo.get("used_by"):
            return False, "卡密无效或已被使用"
        card_conf = CARD_TYPES.get(cinfo["type"])
        if not card_conf:
            return False, "卡密类型异常"

        now = datetime.datetime.now()
        exp = info.get("expires_at")
        base = now
        if exp:
            try:
                existing = datetime.datetime.fromisoformat(exp)
                if existing > now:
                    base = existing  # 未过期则顺延，不清零
            except Exception:
                base = now
        if card_conf["days"] is None:
            new_exp = None
        else:
            new_exp = (base + datetime.timedelta(days=card_conf["days"])).strftime("%Y-%m-%dT%H:%M:%S")

        info["expires_at"] = new_exp
        info["card_type"] = cinfo["type"]
        users[username] = info
        _save_users(users)
        codes[  code]["used_by"] = username
        _save_codes(codes)

    return True, (new_exp or "永久有效")


def delete_codes(code_list: list[str]) -> int:
    """批量删除卡密，返回成功删除的数量"""
    codes = _load_codes()
    removed = 0
    with _LOCK:
        for code in (code_list or []):
            if code in codes:
                del codes[code]
                removed += 1
        if removed:
            _save_codes(codes)
    return removed


def set_codes_used(code_list: list[str], used: bool) -> int:
    """批量标记卡密为已使用/未使用，返回成功更新的数量。

    used=True  -> used_by 设为占位标记 "已使用"（视为已用，不可再次核销）
    used=False -> used_by 设为 None（恢复为未使用，可重新使用）
    """
    codes = _load_codes()
    updated = 0
    with _LOCK:
        for code in (code_list or []):
            if code in codes:
                codes[code]["used_by"] = ("已使用" if used else None)
                updated += 1
        if updated:
            _save_codes(codes)
    return updated


def list_codes(filters: dict | None = None) -> list[dict]:
    """返回卡密列表，支持筛选。

    filters 可包含：
      - status: "unused" 仅未使用 / "used" 仅已使用（used_by 为空或 pending 视为未使用）
      - type:   卡类型（week/month/year/permanent）
      - date_from / date_to: 创建时间范围（含），格式 "YYYY-MM-DD"
    """
    filters = filters or {}
    status = filters.get("status")
    ctype = filters.get("type")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")

    codes = _load_codes()
    out = []
    for c, v in codes.items():
        used = bool(v.get("used_by") and v.get("used_by") != "pending")
        if status == "unused" and used:
            continue
        if status == "used" and not used:
            continue
        if ctype and v.get("type") != ctype:
            continue
        created = v.get("created_at") or ""
        if date_from and created < date_from + " 00:00:00":
            continue
        if date_to and created > date_to + " 23:59:59":
            continue
        out.append({
            "code": c,
            "type": v.get("type"),
            "type_label": CARD_TYPES.get(v.get("type", ""), {}).get("label", v.get("type")),
            "used_by": v.get("used_by"),
            "created_at": created,
        })
    return out


def list_users() -> list[dict]:
    users = _load_users()
    out = []
    for name, info in users.items():
        exp = info.get("expires_at")
        expired = is_expired(name)
        out.append({
            "username": name,
            "role": info.get("role", "user"),
            "card_type": CARD_TYPES.get(info.get("card_type", ""), {}).get("label", "-"),
            "expires_at": exp,
            "expired": expired,
            "created_at": info.get("created_at"),
        })
    return out


# ---------- session 管理 ----------

def create_session(username: str) -> str:
    """登录成功后创建 session，返回 token"""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        expired = [t for t, s in _SESSIONS.items() if s["expires"] < now]
        for t in expired:
            _SESSIONS.pop(t, None)
        _SESSIONS[token] = {"username": username, "expires": now + SESSION_TTL}
    return token


def get_session_user(token: str | None) -> str | None:
    """根据 token 取当前登录用户名，无效/过期返回 None"""
    if not token:
        return None
    with _LOCK:
        session = _SESSIONS.get(token)
        if not session:
            return None
        if session["expires"] < time.time():
            _SESSIONS.pop(token, None)
            return None
        return session["username"]


def destroy_session(token: str | None) -> None:
    """退出登录"""
    if token:
        with _LOCK:
            _SESSIONS.pop(token, None)


def parse_cookie(header: str | None, name: str) -> str | None:
    """从 Cookie 请求头中解析指定项"""
    if not header:
        return None
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None
