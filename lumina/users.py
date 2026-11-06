"""本机登录账户与登录状态（和 `accounts.py` 里的邮件账户不是一回事）。

* 账户只存在本机（SQLite `users` 表），**密码不落盘原文**：存的是
  PBKDF2-HMAC-SHA256（随机盐 + 21 万次迭代）的哈希，只能用来「比对」，
  反推不出原密码，界面上也永远不显示；
* 登录时就在本地比对，不联网；
* 记住设备：登录时选了「记住这台设备」，就把「设备指纹 + 到期时间」写进
  `login_sessions`，到期前打开应用不用再输密码；到期后要求重新输入。
  设备指纹 = 安装时生成的一串随机 id + Windows MachineGuid 的哈希，
  库里只存哈希，原始值不落盘；
* 连续输错有冷却（`login_attempts` 表），防止有人拿脚本硬试。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

ALGO = "pbkdf2_sha256"
ITERATIONS = 210_000
SALT_BYTES = 16
MIN_PASSWORD = 6

REMEMBER_KEY = "login_remember_minutes"     # 记住这台设备多久（0 = 不记住）
REQUIRED_KEY = "login_required"             # 是否启用登录（默认启用）
DEFAULT_REMEMBER = 1440                     # 24 小时
REMEMBER_FOREVER = -1                       # 永久绑定（只允许管理员）
REMEMBER_CHOICES = [
    (0, "每次都要输入密码"),
    (1440, "24 小时内记住这台设备"),
    (2880, "48 小时内记住这台设备"),
    (10080, "7 天内记住这台设备"),
]
REMEMBER_ADMIN_CHOICE = (REMEMBER_FOREVER, "永久绑定这台设备（仅管理员）")
MAX_FAILURES = 5
COOLDOWN_SECONDS = 30
HARD_FAILURES = 10
HARD_COOLDOWN = 300

# 普通账户的邮箱域名要求（学校邮箱才能注册；管理员账户不受限）
EMAIL_SUFFIXES = ("@m.fudan.edu.cn", "@fudan.edu.cn")


def email_domain_ok(email: str) -> bool:
    text = str(email or "").strip().lower()
    if "@" not in text:
        return False
    return any(text.endswith(suffix) for suffix in EMAIL_SUFFIXES)


def email_policy_text() -> str:
    return "邮箱必须以 @m.fudan.edu.cn 或 @fudan.edu.cn 结尾（管理员账户不受此限）"


class AccountError(Exception):
    pass


# ------------------------------------------------------------------ 表
USER_COLUMNS = {
    # 老库升级时按需补的列
    "role": "TEXT NOT NULL DEFAULT 'user'",
    "email": "TEXT NOT NULL DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "expires_at": "TEXT NOT NULL DEFAULT ''",
    "receipt": "TEXT NOT NULL DEFAULT ''",
}


def _ensure(store) -> None:
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            pw_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login TEXT
        )"""
    )
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS login_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            device_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            UNIQUE(user_id, device_hash)
        )"""
    )
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS login_attempts (
            username TEXT PRIMARY KEY COLLATE NOCASE,
            failures INTEGER NOT NULL DEFAULT 0,
            last_at TEXT
        )"""
    )
    # 老库补列（role / email / status / expires_at / receipt）
    existing = {row[1] for row in
                store.conn.execute("PRAGMA table_info(users)").fetchall()}
    for name, ddl in USER_COLUMNS.items():
        if name not in existing:
            store.conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
    store.conn.commit()
    _ensure_first_admin(store)


def _ensure_first_admin(store) -> None:
    """第一个「本机自己建的」账户自动是管理员。

    注意：管理员审批后注册进来的账户（带 `receipt`）不算——那些人的管理员在
    他们自己的注册流程里，不在这台机器上。
    """
    try:
        has_admin = store.conn.execute(
            "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if has_admin:
            return
        first = store.conn.execute(
            "SELECT id FROM users WHERE COALESCE(receipt, '') = ''"
            " ORDER BY id LIMIT 1").fetchone()
        if first is None:
            return
        store.conn.execute("UPDATE users SET role = 'admin' WHERE id = ?",
                           (int(first[0]),))
        store.conn.commit()
    except Exception:
        pass


def _now() -> datetime:
    return datetime.now()


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _parse(text: Any) -> Optional[datetime]:
    """时间戳按「日期」或「日期 时间」两种写法都能认（有效期只给日期）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        pass
    try:
        # 只写了日期：当天结束前都算有效
        return datetime.strptime(raw, "%Y-%m-%d").replace(hour=23, minute=59,
                                                          second=59)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ 密码哈希
def hash_password(password: str, salt: Optional[bytes] = None,
                  iterations: int = ITERATIONS) -> str:
    if not password:
        raise AccountError("密码不能为空")
    salt = salt or os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 int(iterations))
    return "%s$%d$%s$%s" % (ALGO, int(iterations), salt.hex(), digest.hex())


def verify_password(password: str, stored: str) -> bool:
    """比对密码（拿不出原文，只能这样比）。"""
    try:
        algo, iterations, salt_hex, digest_hex = str(stored).split("$")
        if algo != ALGO:
            return False
        salt = bytes.fromhex(salt_hex)
        expect = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt,
                                     int(iterations))
    except Exception:
        return False
    return hmac.compare_digest(actual, expect)


# ------------------------------------------------------------------ 账户
def list_users(store) -> List[Dict[str, Any]]:
    _ensure(store)
    rows = store.conn.execute(
        "SELECT id, username, role, email, status, expires_at, created_at,"
        " updated_at, last_login FROM users ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def get_user(store, user_id: int) -> Optional[Dict[str, Any]]:
    _ensure(store)
    row = store.conn.execute(
        "SELECT id, username, role, email, status, expires_at, created_at,"
        " updated_at, last_login FROM users WHERE id = ?",
        (int(user_id),)).fetchone()
    return dict(row) if row else None


def find_user(store, username: str) -> Optional[Dict[str, Any]]:
    _ensure(store)
    row = store.conn.execute(
        "SELECT id, username, role, email, status, expires_at, created_at,"
        " updated_at, last_login FROM users WHERE username = ? COLLATE NOCASE",
        (str(username).strip(),)).fetchone()
    return dict(row) if row else None


def find_by_email(store, email: str) -> Optional[Dict[str, Any]]:
    _ensure(store)
    row = store.conn.execute(
        "SELECT id, username, role, email, status, expires_at FROM users"
        " WHERE email = ? COLLATE NOCASE", (str(email or "").strip(),)).fetchone()
    return dict(row) if row else None


def has_users(store) -> bool:
    return bool(list_users(store))


def admins(store) -> List[Dict[str, Any]]:
    return [item for item in list_users(store) if item.get("role") == "admin"]


def is_admin(user) -> bool:
    return bool(user) and str((user or {}).get("role") or "") == "admin"


def create_user(store, username: str, password: str, email: str = "",
                role: str = "", expires_at: str = "", receipt: str = "",
                status: str = "active") -> int:
    _ensure(store)
    name = str(username or "").strip()
    if not name:
        raise AccountError("用户名不能为空")
    if len(name) > 24:
        raise AccountError("用户名最多 24 个字")
    if len(str(password or "")) < MIN_PASSWORD:
        raise AccountError(f"密码至少 {MIN_PASSWORD} 位")
    if find_user(store, name) is not None:
        raise AccountError(f"已经有名叫「{name}」的账户了")
    mail = str(email or "").strip()
    if mail and "@" not in mail:
        raise AccountError("邮箱格式不正确")
    if mail and find_by_email(store, mail) is not None:
        raise AccountError(f"邮箱「{mail}」已经绑定过别的账户了")
    if not role:
        # 还没有管理员时，第一个账户自动是管理员
        role = "user" if admins(store) else "admin"
    if role != "admin":
        # 普通账户：必须绑定学校邮箱，而且必须经过审批
        if not mail:
            raise AccountError("普通账户必须绑定邮箱：" + email_policy_text())
        if not email_domain_ok(mail):
            raise AccountError("邮箱不合规：" + email_policy_text())
    if not receipt:
        try:
            from . import adminsign

            if adminsign.install_is_client(store):
                raise AccountError(
                    "这份安装包是客户端版（没有管理员私钥），"
                    "必须用「注册账户」向管理员申请，不能直接新建账户")
        except AccountError:
            raise
        except Exception:
            pass
    stamp = _stamp(_now())
    cursor = store.conn.execute(
        "INSERT INTO users(username, pw_hash, created_at, updated_at, role,"
        " email, status, expires_at, receipt) VALUES (?,?,?,?,?,?,?,?,?)",
        (name, hash_password(password), stamp, stamp, role, mail,
         status or "active", str(expires_at or ""), str(receipt or "")),
    )
    store.conn.commit()
    return int(cursor.lastrowid)


def set_email(store, user_id: int, email: str) -> None:
    _ensure(store)
    mail = str(email or "").strip()
    if mail and "@" not in mail:
        raise AccountError("邮箱格式不正确")
    other = find_by_email(store, mail) if mail else None
    if other is not None and int(other["id"]) != int(user_id):
        raise AccountError(f"邮箱「{mail}」已经绑定过别的账户了")
    store.conn.execute("UPDATE users SET email = ?, updated_at = ? WHERE id = ?",
                       (mail, _stamp(_now()), int(user_id)))
    store.conn.commit()


def set_role(store, user_id: int, role: str) -> None:
    _ensure(store)
    value = "admin" if str(role) == "admin" else "user"
    if value == "user":
        left = store.conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND id != ?",
            (int(user_id),)).fetchone()[0]
        if int(left) == 0:
            raise AccountError("至少要留一个管理员")
    store.conn.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                       (value, _stamp(_now()), int(user_id)))
    store.conn.commit()


def set_status(store, user_id: int, status: str) -> None:
    """status: active（可用）/ disabled（停用）。"""
    _ensure(store)
    value = "disabled" if str(status) == "disabled" else "active"
    if value == "disabled":
        user = get_user(store, user_id)
        if user and str(user.get("role")) == "admin" and len(admins(store)) <= 1:
            raise AccountError("这是最后一个管理员，不能停用")
    store.conn.execute("UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                       (value, _stamp(_now()), int(user_id)))
    store.conn.execute("DELETE FROM login_sessions WHERE user_id = ?",
                       (int(user_id),)) if value == "disabled" else None
    store.conn.commit()


def set_expiry(store, user_id: int, expires_at: str) -> None:
    _ensure(store)
    store.conn.execute(
        "UPDATE users SET expires_at = ?, updated_at = ? WHERE id = ?",
        (str(expires_at or ""), _stamp(_now()), int(user_id)))
    store.conn.commit()


def is_expired(user) -> bool:
    text = str((user or {}).get("expires_at") or "").strip()
    if not text:
        return False
    parsed = _parse(text)
    return parsed is not None and parsed <= _now()


def days_left(user) -> int:
    text = str((user or {}).get("expires_at") or "").strip()
    parsed = _parse(text)
    if parsed is None:
        return -1
    return int(round((parsed - _now()).total_seconds() / 86400.0))


def validity_state(user) -> str:
    """给界面用的一句话：有效 / 已过期 / 已停用。"""
    if not user:
        return "没有账户"
    if str(user.get("status") or "active") != "active":
        return "已停用"
    if is_expired(user):
        return "已过期"
    left = days_left(user)
    if left >= 0:
        return f"有效（还剩 {left} 天）"
    return "有效（长期）"


def set_password(store, user_id: int, password: str) -> None:
    _ensure(store)
    if len(str(password or "")) < MIN_PASSWORD:
        raise AccountError(f"密码至少 {MIN_PASSWORD} 位")
    if get_user(store, user_id) is None:
        raise AccountError("找不到这个账户")
    store.conn.execute(
        "UPDATE users SET pw_hash = ?, updated_at = ? WHERE id = ?",
        (hash_password(password), _stamp(_now()), int(user_id)))
    store.conn.commit()


def change_password(store, user_id: int, old_password: str,
                    new_password: str) -> None:
    """改密码前要验旧的（本机也要防一下别人顺手改）。"""
    _ensure(store)
    row = store.conn.execute("SELECT pw_hash FROM users WHERE id = ?",
                             (int(user_id),)).fetchone()
    if row is None:
        raise AccountError("找不到这个账户")
    if not verify_password(old_password, row["pw_hash"]):
        raise AccountError("原密码不对")
    set_password(store, user_id, new_password)


def delete_user(store, user_id: int) -> None:
    _ensure(store)
    store.conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
    store.conn.execute("DELETE FROM login_sessions WHERE user_id = ?",
                       (int(user_id),))
    store.conn.commit()


def reset_all(store) -> None:
    """忘密码时的紧急出口：清空账户与登录状态（日程 / 邮件等数据不动）。"""
    _ensure(store)
    store.conn.execute("DELETE FROM users")
    store.conn.execute("DELETE FROM login_sessions")
    store.conn.execute("DELETE FROM login_attempts")
    store.conn.commit()


# ------------------------------------------------------------------ 冷却
def cooldown_left(store, username: str) -> int:
    """还要等几秒才能再试（0 = 可以试）。"""
    _ensure(store)
    row = store.conn.execute(
        "SELECT failures, last_at FROM login_attempts WHERE username = ?"
        " COLLATE NOCASE", (str(username).strip(),)).fetchone()
    if row is None:
        return 0
    last = _parse(row["last_at"])
    if last is None:
        return 0
    failures = int(row["failures"] or 0)
    if failures >= HARD_FAILURES:
        span = HARD_COOLDOWN
    elif failures >= MAX_FAILURES:
        span = COOLDOWN_SECONDS
    else:
        return 0
    left = (last + timedelta(seconds=span)) - _now()
    return max(0, int(left.total_seconds()))


def _note_failure(store, username: str) -> None:
    _ensure(store)
    name = str(username or "").strip()
    row = store.conn.execute(
        "SELECT failures FROM login_attempts WHERE username = ? COLLATE NOCASE",
        (name,)).fetchone()
    failures = int(row["failures"] or 0) + 1 if row else 1
    stamp = _stamp(_now())
    store.conn.execute(
        "INSERT INTO login_attempts(username, failures, last_at) VALUES (?,?,?)"
        " ON CONFLICT(username) DO UPDATE SET failures = ?, last_at = ?",
        (name, failures, stamp, failures, stamp))
    store.conn.commit()


def _clear_failures(store, username: str) -> None:
    _ensure(store)
    store.conn.execute(
        "DELETE FROM login_attempts WHERE username = ? COLLATE NOCASE",
        (str(username or "").strip(),))
    store.conn.commit()


# ------------------------------------------------------------------ 验证
def check(store, username: str, password: str) -> Dict[str, Any]:
    """本地比对。成功返回账户信息，失败抛 AccountError（原因给人看）。"""
    _ensure(store)
    name = str(username or "").strip()
    if not name:
        raise AccountError("先选一个账户")
    wait = cooldown_left(store, name)
    if wait > 0:
        raise AccountError(f"输错太多次了，等 {wait} 秒再试")
    row = store.conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (name,)).fetchone()
    if row is None:
        _note_failure(store, name)
        raise AccountError("没有这个账户")
    if str(row["status"] or "active") != "active":
        raise AccountError("这个账户已被停用，请联系管理员")
    if not verify_password(password, row["pw_hash"]):
        _note_failure(store, name)
        raise AccountError("密码不对")
    if is_expired(dict(row)):
        raise AccountError("这个账户的使用期限到了，请联系管理员续期")
    _clear_failures(store, name)
    stamp = _stamp(_now())
    store.conn.execute("UPDATE users SET last_login = ? WHERE id = ?",
                       (stamp, int(row["id"])))
    store.conn.commit()
    return {"id": int(row["id"]), "username": row["username"],
            "role": row["role"] or "user", "email": row["email"] or "",
            "status": row["status"] or "active",
            "expires_at": row["expires_at"] or "", "last_login": stamp}


# ------------------------------------------------------------------ 记住设备
def machine_id() -> str:
    """Windows 的 MachineGuid（读不到就退回主机名 + MAC）。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _kind = winreg.QueryValueEx(key, "MachineGuid")
            if value:
                return str(value)
    except Exception:
        pass
    import platform
    import uuid

    return "%s-%s" % (platform.node(), uuid.getnode())


def install_id() -> str:
    """安装时生成一次就不变的一串 id（存在 data/install_id）。"""
    from .config import DATA_DIR

    path = DATA_DIR / "install_id"
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    except Exception:
        pass
    value = secrets.token_hex(16)
    try:
        path.write_text(value, encoding="utf-8")
    except Exception:
        pass
    return value


def device_hash() -> str:
    raw = "%s|%s" % (machine_id(), install_id())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def remember_minutes(store) -> int:
    try:
        return max(0, int(float(store.get_setting(
            REMEMBER_KEY, str(DEFAULT_REMEMBER)) or 0)))
    except (TypeError, ValueError):
        return DEFAULT_REMEMBER


def set_remember_minutes(store, minutes: int) -> int:
    """记住时长；-1 = 永久（管理员绑定这台设备）。"""
    value = int(minutes)
    if value < 0:
        value = REMEMBER_FOREVER
    else:
        value = max(0, value)
    store.set_setting(REMEMBER_KEY, str(value))
    return value


NEVER = "never"          # login_sessions.expires_at 的特殊值：永久绑定这台设备


def remember_device(store, user_id: int, minutes: int) -> None:
    """登录成功后调用：把「这台设备」记 24 / 48 小时。

    `minutes < 0` 表示**永久绑定**：只允许管理员账户这样做
    （管理员就是这台机器的主人，没必要隔几小时输一次密码）。
    """
    _ensure(store)
    minutes = int(minutes)
    if minutes == REMEMBER_FOREVER:
        # 永久绑定（只有管理员可以）
        user = get_user(store, user_id)
        if not is_admin(user):
            raise AccountError("只有管理员账户可以永久绑定设备")
        expires = NEVER
    elif minutes <= 0:
        forget_device(store)
        return
    else:
        expires = _stamp(_now() + timedelta(minutes=minutes))
    device = device_hash()
    # 同一台设备只留最近一次登录的账户
    store.conn.execute("DELETE FROM login_sessions WHERE device_hash = ?",
                       (device,))
    store.conn.execute(
        "INSERT INTO login_sessions(user_id, device_hash, created_at, expires_at)"
        " VALUES (?,?,?,?)",
        (int(user_id), device, _stamp(_now()), expires))
    store.conn.commit()


def is_trusted_device(store, user=None) -> bool:
    """这台设备是不是「管理员永久绑定」了。"""
    _ensure(store)
    row = store.conn.execute(
        "SELECT user_id, expires_at FROM login_sessions WHERE device_hash = ?",
        (device_hash(),)).fetchone()
    if row is None or str(row["expires_at"]) != NEVER:
        return False
    if user is not None:
        return int(row["user_id"]) == int((user or {}).get("id") or 0)
    owner = get_user(store, int(row["user_id"]))
    return is_admin(owner)


def forget_device(store) -> None:
    _ensure(store)
    store.conn.execute("DELETE FROM login_sessions WHERE device_hash = ?",
                       (device_hash(),))
    store.conn.commit()


def current_session(store) -> Optional[Dict[str, Any]]:
    """这台设备上有效的登录状态（附带账户信息）。

    `expires_at == "never"` 是「管理员永久绑定这台设备」，不过期；
    但账户被停用 / 使用期限到了，照样得重新登录。
    """
    _ensure(store)
    row = store.conn.execute(
        "SELECT * FROM login_sessions WHERE device_hash = ?",
        (device_hash(),)).fetchone()
    if row is None:
        return None
    forever = str(row["expires_at"]) == NEVER
    if not forever:
        expires = _parse(row["expires_at"])
        if expires is None or expires <= _now():
            store.conn.execute("DELETE FROM login_sessions WHERE id = ?",
                               (int(row["id"]),))
            store.conn.commit()
            return None
    user = get_user(store, int(row["user_id"]))
    if user is None:
        store.conn.execute("DELETE FROM login_sessions WHERE id = ?",
                           (int(row["id"]),))
        store.conn.commit()
        return None
    # 被停用 / 过期的账户即使「记住设备」了也必须重新登录
    if str(user.get("status") or "active") != "active" or is_expired(user):
        store.conn.execute("DELETE FROM login_sessions WHERE id = ?",
                           (int(row["id"]),))
        store.conn.commit()
        return None
    return {**user, "expires_at": row["expires_at"],
            "remembered_at": row["created_at"], "trusted": forever}


_CURRENT_USER_PROVIDER = None


def set_current_user_provider(provider) -> None:
    """登记「当前登录的账户」从哪来（主窗口/启动流程里设置）。"""
    global _CURRENT_USER_PROVIDER
    _CURRENT_USER_PROVIDER = provider


def current_user(store) -> Optional[Dict[str, Any]]:
    """当前登录的账户：优先问 provider，其次看「记住设备」的会话。"""
    provider = _CURRENT_USER_PROVIDER
    if provider is not None:
        try:
            user = provider()
            if isinstance(user, dict) and user.get("id"):
                return user
        except Exception:
            pass
    return current_session(store)


def is_admin_now(store) -> bool:
    """当前登录的是不是管理员（接口授权、使用限制都要看这个）。"""
    return is_admin(current_user(store))


def login_required(store) -> bool:
    try:
        return (store.get_setting(REQUIRED_KEY, "1") or "1") != "0"
    except Exception:
        return True


def set_login_required(store, required: bool) -> None:
    store.set_setting(REQUIRED_KEY, "1" if required else "0")


def auto_login(store) -> Optional[Dict[str, Any]]:
    """启动时调用：返回应该直接进入的账户（None = 要输密码）。"""
    if not login_required(store):
        return None
    try:
        if not has_users(store):
            return None
    except Exception:
        return None
    return current_session(store)


def session_hours_left(store) -> int:
    """还剩几小时要重新输密码；-1 表示永久绑定（管理员）。"""
    session = current_session(store)
    if session is None:
        return 0
    if session.get("trusted"):
        return -1
    expires = _parse(session.get("expires_at"))
    if expires is None:
        return 0
    left = (expires - _now()).total_seconds() / 3600.0
    return max(0, int(round(left)))


def session_text(store) -> str:
    """一句话描述这台设备的登录状态（设置页和登录窗口都用）。"""
    session = current_session(store)
    if session is None:
        return "这台设备没有记住登录"
    if session.get("trusted"):
        return "已永久绑定这台设备（管理员，不再定时要求输密码）"
    hours = session_hours_left(store)
    return (f"这台设备已记住登录，约 {max(1, hours)} 小时后需要重新输密码"
            f"（{session.get('expires_at', '')} 到期）")
