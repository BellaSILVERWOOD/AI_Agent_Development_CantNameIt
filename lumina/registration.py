"""注册申请 → 管理员审核 → 6 位验证码 → 设置密码。

流程（跨机器，不依赖服务器）：

1. **申请人**在自己的 DanLume 里点「注册账户」，填用户名 + 邮箱；
   程序把 {用户名, 邮箱, 设备指纹, 随机串, 时间} 用**管理员公钥**加密成一行密文
   （见 `adminsign.seal`），把这一行放进邮件正文，发到管理员的邮箱。
   本地只留一份「待确认」记录（`pending_registrations`），记住随机串。
2. **管理员**在「账户审核」里点「检查新申请」，程序读 IMAP 收件箱里
   `[LUMINA-REG]` 开头的邮件，用本机私钥解密，列出申请人 → 批准（可选有效期天数）。
3. 批准时管理员这边生成 **6 位验证码**，把「验证码 + 已签名回执」发给申请人邮箱；
   同时把该账户登记进管理员本机的 `reg_users` 注册表（用户名/邮箱/状态/到期）。
4. **申请人**把邮件里的验证码和回执粘进注册窗口：程序用随包的公钥验签
   （`adminsign.verify_receipt`），核对用户名/邮箱/随机串，再和 6 位验证码比对，
   全对才允许设置密码；`expires_at` 也一起写进本地账户，用于「是否仍然有效」的判断。

为什么这样设计：
* 全程不需要服务器，也不要求申请人先配好 SMTP —— 发不出去时会把同样的密文
  生成只读文本，让申请人用自己的邮件客户端粘贴发送；
* 回执是管理员私钥签名的，改一个字节就验不过；验证码证明「本人确实收到了那封邮件」；
* 有效性：本地每次登录检查「状态 + 有效期」；到期前两周会在设置页提示续期，
  续期走同一套邮件流程（管理员重新签发回执）。
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import adminsign, users

REQUEST_SUBJECT = "[LUMINA-REG] DanLume 注册申请"
APPROVED_SUBJECT = "[LUMINA-APPROVED] DanLume 注册通过（含验证码与回执）"

#: 收申请时要认的主题前缀。程序改名成 DanLume 之后，新客户端发的是
#: `[DANLUME-REG] DanLume注册申请`，只认 `[LUMINA-REG]` 会把它整封漏掉
#: （现象就是「人家明明发了，我这边刷新不出来」）。所以两代前缀都收。
REQUEST_PREFIXES = ("[LUMINA-REG", "[DANLUME-REG")
#: 给 IMAP 服务器做 SUBJECT 搜索用的短前缀
REQUEST_SEARCH_PREFIXES = ("[LUMINA-", "[DANLUME-")
REGISTRY_EMAIL_KEY = "registry_email"
DEFAULT_REGISTRY_EMAIL = "Lena_Lee2025@outlook.com"
DEFAULT_VALID_DAYS = 180
CODE_LEN = 6


class RegisterError(Exception):
    pass


# ------------------------------------------------------------------ 表
def _ensure(store) -> None:
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS pending_registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE,
            email TEXT NOT NULL,
            nonce TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting'
        )"""
    )
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS reg_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE,
            email TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            issued_at TEXT NOT NULL,
            expires_at TEXT NOT NULL DEFAULT '',
            code_hash TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT ''
        )"""
    )
    # 老用户换了新机器：记下每台设备，用来判断「这是第几台」以及免重复审批的额度
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS reg_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_id INTEGER NOT NULL,
            device TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            UNIQUE(registry_id, device)
        )"""
    )
    store.conn.commit()


def _stamp(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _date_after(days: int) -> str:
    return (datetime.now() + timedelta(days=max(0, int(days)))).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ 设置
def registry_email(store) -> str:
    return (store.get_setting(REGISTRY_EMAIL_KEY, DEFAULT_REGISTRY_EMAIL)
            or DEFAULT_REGISTRY_EMAIL).strip()


def set_registry_email(store, email: str) -> None:
    store.set_setting(REGISTRY_EMAIL_KEY, str(email or "").strip())


# ------------------------------------------------------------------ 验证码邮件用哪个账户发
FUDAN_DOMAINS = ("m.fudan.edu.cn", "fudan.edu.cn")
SENDER_KEY = "approval_sender_id"      # "" = 自动（优先学邮）


def sender_candidates(store) -> List[Dict[str, Any]]:
    """所有可用的发件账户（给界面下拉用）。"""
    from . import accounts

    try:
        return accounts.load(store)
    except Exception:
        return []


def _is_fudan(account: Dict[str, Any]) -> bool:
    sender = str((account or {}).get("sender_email") or "").lower()
    return any(sender.endswith(domain) for domain in FUDAN_DOMAINS)


def set_approval_sender(store, account_id: str) -> None:
    store.set_setting(SENDER_KEY, str(account_id or ""))


def approval_sender_id(store) -> str:
    return (store.get_setting(SENDER_KEY, "") or "").strip()


def pick_sender_account(store) -> Optional[Dict[str, Any]]:
    """验证码邮件用哪个账户发：指定的 → 学邮 → 当前选中 → 第一个。"""
    from . import accounts

    items = sender_candidates(store)
    if not items:
        return None
    wanted = approval_sender_id(store)
    if wanted:
        for item in items:
            if str(item.get("id")) == wanted:
                return item
    for item in items:
        if _is_fudan(item):
            return item
    active = accounts.active(store)
    if active:
        return active
    return items[0]


def default_valid_days(store) -> int:
    try:
        return max(1, int(float(store.get_setting("registry_valid_days",
                                                  str(DEFAULT_VALID_DAYS)))))
    except (TypeError, ValueError):
        return DEFAULT_VALID_DAYS


def set_default_valid_days(store, days: int) -> None:
    store.set_setting("registry_valid_days", str(max(1, int(days))))


# ------------------------------------------------------------------ 老用户 / 新设备
DEVICE_LIMIT_KEY = "reg_device_limit"
DEFAULT_DEVICE_LIMIT = 3      # 每个已批准账户最多自动放行几台设备（0 = 关闭自动放行）
MAX_DEVICE_LIMIT = 20


def device_limit(store) -> int:
    """老用户换新机器时，最多自动放行几台（0 = 一律人工审批）。"""
    try:
        value = int(float(store.get_setting(DEVICE_LIMIT_KEY,
                                            str(DEFAULT_DEVICE_LIMIT))))
    except (TypeError, ValueError):
        return DEFAULT_DEVICE_LIMIT
    return max(0, min(MAX_DEVICE_LIMIT, value))


def set_device_limit(store, count: int) -> int:
    value = max(0, min(MAX_DEVICE_LIMIT, int(count)))
    store.set_setting(DEVICE_LIMIT_KEY, str(value))
    return value


def _registry_row(store, username: str) -> Optional[Dict[str, Any]]:
    _ensure(store)
    row = store.conn.execute(
        "SELECT * FROM reg_users WHERE username = ? COLLATE NOCASE"
        " ORDER BY id DESC LIMIT 1", (str(username or "").strip(),)).fetchone()
    return dict(row) if row else None


def _expired(expires_at: str) -> bool:
    text = str(expires_at or "").strip()
    if not text:
        return False
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date() < datetime.now().date()
    except Exception:
        return False


def registry_devices(store, username: str) -> List[Dict[str, Any]]:
    """这台账户已经登记过哪些设备（新→旧）。"""
    _ensure(store)
    row = _registry_row(store, username)
    if row is None:
        return []
    rows = store.conn.execute(
        "SELECT * FROM reg_devices WHERE registry_id = ? ORDER BY id DESC",
        (int(row["id"]),)).fetchall()
    return [dict(item) for item in rows]


def register_device(store, username: str, device: str,
                    note: str = "") -> bool:
    """记下一条「这个账户在这台设备上登录过」。返回是不是新设备。"""
    _ensure(store)
    row = _registry_row(store, username)
    if row is None:
        return False
    device = str(device or "").strip()
    if not device:
        return False
    try:
        existed = store.conn.execute(
            "SELECT id FROM reg_devices WHERE registry_id = ? AND device = ?",
            (int(row["id"]), device)).fetchone()
        store.conn.execute(
            "INSERT INTO reg_devices (registry_id, device, created_at, note)"
            " VALUES (?,?,?,?) ON CONFLICT(registry_id, device) DO NOTHING",
            (int(row["id"]), device, _stamp(), str(note or "")))
        store.conn.commit()
        return existed is None
    except Exception:
        return False


def known_user(store, username: str, email: str) -> Optional[Dict[str, Any]]:
    """这个「用户名 + 邮箱」是不是已经批准过、还没过期的老用户。"""
    row = _registry_row(store, username)
    if row is None:
        return None
    if str(row.get("status") or "") != "active":
        return None
    if _expired(str(row.get("expires_at") or "")):
        return None
    if str(row.get("email") or "").strip().lower() != str(email or "").strip().lower():
        return None
    return row


def cross_device_status(store, request: Dict[str, Any]) -> Dict[str, Any]:
    """判断这条申请能不能「老用户免审批」。"""
    request = request or {}
    payload = request.get("payload") or request
    username = str(payload.get("username") or request.get("username") or "").strip()
    email = str(payload.get("email") or request.get("email") or "").strip()
    device = str(payload.get("device") or request.get("device") or "").strip()
    limit = device_limit(store)
    info = {"known": False, "device": device, "devices": 0, "limit": limit,
            "approve": False, "reason": "", "expires_at": "", "username": username}
    row = known_user(store, username, email)
    if row is None:
        info["reason"] = "不是已批准的老用户（或邮箱对不上 / 已过期）"
        return info
    info["known"] = True
    info["expires_at"] = str(row.get("expires_at") or "")
    devices = registry_devices(store, username)
    info["devices"] = len(devices)
    same = any(str(item.get("device")) == device for item in devices)
    info["same_device"] = same
    if not device:
        info["reason"] = "申请里没有设备指纹，只能人工审批"
        return info
    if same:
        info["reason"] = "这台设备已经登记过了（重复申请，直接人工处理）"
        return info
    if limit <= 0:
        info["reason"] = "自动放行已关闭（设置为 0 台）"
        return info
    if len(devices) >= limit:
        info["reason"] = "已经登记 %d 台设备，达到上限 %d，需要人工批准" % (
            len(devices), limit)
        return info
    info["approve"] = True
    info["reason"] = "老用户换新设备（第 %d 台，上限 %d）" % (len(devices) + 1, limit)
    return info


def _approval_payload(store, username: str, email: str, device: str,
                      code: str, expires: str, nonce: str) -> Dict[str, Any]:
    return {
        "v": 1,
        "kind": "registration-receipt",
        "username": username,
        "email": email,
        "nonce": str(nonce or ""),
        "device": str(device or ""),
        "code": code,
        "expires_at": expires,
        "issued_at": _stamp(),
        "issuer": users.list_users(store)[0]["username"] if users.list_users(store) else "",
    }


def approve_existing(store, request: Dict[str, Any],
                     note: str = "新设备") -> Dict[str, Any]:
    """老用户换机器：沿用原来的有效期，重新生成验证码并签一份新回执。"""
    _ensure(store)
    payload = (request or {}).get("payload") or (request or {})
    username = str(payload.get("username") or request.get("username") or "").strip()
    email = str(payload.get("email") or request.get("email") or "").strip()
    device = str(payload.get("device") or request.get("device") or "").strip()
    row = known_user(store, username, email)
    if row is None:
        raise RegisterError("这不是已批准的老用户，不能沿用原来的批准")
    if not adminsign.has_private_key(store):
        raise RegisterError("这台机器还没有管理员私钥，没法签发回执")
    expires = str(row.get("expires_at") or "")
    code = new_code()
    receipt = _approval_payload(store, username, email, device, code, expires,
                               str(payload.get("nonce") or request.get("nonce") or ""))
    token = adminsign.sign_receipt(store, receipt)
    store.conn.execute(
        "UPDATE reg_users SET code_hash = ?, note = ? WHERE id = ?",
        (_code_hash(code), ("%s；%s" % (str(row.get("note") or ""), note)).strip("；"),
         int(row["id"])))
    store.conn.commit()
    register_device(store, username, device, note)
    count = len(registry_devices(store, username))
    body = (
        "你的 DanLume 账户在新设备上登录，已自动通过（沿用原来的有效期）。\n\n"
        f"账户：{username}\n"
        f"绑定邮箱：{email}\n"
        f"有效期到：{expires or '长期'}\n"
        f"这是第 {count} 台设备\n\n"
        f"验证码：{code}\n\n"
        "请在注册窗口里填入验证码，并把下面这一整行回执粘进「回执」输入框：\n"
        f"{token}\n"
    )
    return {"username": username, "email": email, "code": code, "receipt": token,
            "expires_at": expires, "subject": APPROVED_SUBJECT, "body": body,
            "device": device, "devices": count, "reused": True}


def auto_approve(store, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """能自动放行就返回批准结果，否则返回 None（交给人工）。"""
    try:
        status = cross_device_status(store, request)
    except Exception:
        return None
    if not status.get("approve"):
        return None
    try:
        return approve_existing(store, request)
    except Exception:
        return None


def auto_approve_inbox(store, nonce: str) -> Optional[Dict[str, Any]]:
    """直连收到申请后立刻试一次自动放行（管理员程序在本机，私钥也在）。

    能放行就把批准结果直接回给客户端，老用户换机器不用等邮件、也不用管理员动手。
    """
    ensure_incoming_schema(store)
    row = store.conn.execute("SELECT * FROM reg_inbox WHERE nonce = ?",
                             (str(nonce or ""),)).fetchone()
    if row is None or int(row["consumed"] or 0):
        return None
    try:
        data = adminsign.unseal(store, str(row["sealed"] or ""))
    except Exception:
        return None
    result = auto_approve(store, {"payload": data, "nonce": str(nonce)})
    if result is not None:
        mark_incoming_consumed(store, str(nonce))
    return result


# ------------------------------------------------------------------ 申请人：发申请
def _request_payload(store, username: str, email: str, nonce: str) -> Dict[str, Any]:
    return {
        "v": 1,
        "kind": "registration-request",
        "username": str(username).strip(),
        "email": str(email).strip(),
        "device": users.device_hash(),
        "nonce": nonce,
        "ts": _stamp(),
        "app": "DanLume",
    }


def _request_body(payload: Dict[str, Any], sealed: str) -> str:
    return (
        "这是一封 DanLume 注册申请（由程序自动生成，请勿直接回复本邮件）。\n\n"
        f"申请账户：{payload['username']}\n"
        f"绑定邮箱：{payload['email']}\n"
        f"设备指纹：{payload['device']}\n"
        f"申请时间：{payload['ts']}\n\n"
        "下面是加密后的申请内容（只有管理员机器的私钥能解开）：\n"
        f"{sealed}\n\n"
        "管理员：在 DanLume 里打开 设置 → 账户与登录 →「账户审核（管理员）」→"
        "「检查新申请」即可看到这条申请。\n"
    )


def seal_preview(store, body: str) -> Dict[str, Any]:
    """收件箱预览用：这封邮件是不是「注册申请」，能解开就给出字段。

    以前邮箱里点开这种邮件，正文就是一大坨 `LUMINA-SEAL1.eyJ…` 的密文
    （用户反馈「邮件内容显示得不对」）。这里把它翻译成人话：
    管理员这台机器能解开就给「申请账户 / 邮箱 / 设备 / 时间」，
    解不开就说明「只有管理员的私钥能解开，去 账户 → 用户审核 里看」。
    """
    text = str(body or "")
    if not adminsign.looks_like_seal(text):
        return {"is_seal": False}
    sealed = adminsign.extract_seal(text) or text.strip()
    try:
        data = adminsign.unseal(store, sealed)
    except Exception as exc:  # noqa: BLE001
        return {"is_seal": True, "ok": False,
                "reason": str(exc)[:80], "head": sealed[:80]}
    return {
        "is_seal": True,
        "ok": True,
        "username": str(data.get("username") or ""),
        "email": str(data.get("email") or ""),
        "device": str(data.get("device") or ""),
        "time": str(data.get("ts") or ""),
        "nonce": str(data.get("nonce") or ""),
    }


def is_registration_subject(subject: str) -> bool:
    """主题是不是注册申请（`[[LUMINA-REG]] …`）。"""
    return "LUMINA-REG" in str(subject or "")


def seal_placeholder(note: str = "") -> str:
    """列表/摘要里替代密文的那句话。"""
    return note or "（加密的注册申请，点开查看）"


def make_request(store, username: str, email: str) -> Dict[str, Any]:
    """生成申请（不发送）。返回 {payload, sealed, subject, body, to, nonce}。"""
    _ensure(store)
    name = str(username or "").strip()
    mail = str(email or "").strip()
    if not name:
        raise RegisterError("先填用户名")
    if "@" not in mail:
        raise RegisterError("邮箱格式不正确（注册要绑定一个能收信的邮箱）")
    if not users.email_domain_ok(mail):
        raise RegisterError("邮箱不合规：" + users.email_policy_text())
    if users.find_user(store, name) is not None:
        raise RegisterError(f"这台电脑上已经有「{name}」这个账户了")
    pub = adminsign.public_pem(store)
    if not pub:
        raise RegisterError(
            "这份程序里没有管理员的公钥（admin_pub.pem），没法加密申请；"
            "请向管理员要一份包含公钥的安装包")
    nonce = secrets.token_hex(12)
    payload = _request_payload(store, name, mail, nonce)
    sealed = adminsign.seal(payload, pub)
    to_addr = registry_email(store)
    return {
        "payload": payload,
        "sealed": sealed,
        "nonce": nonce,
        "to": to_addr,
        "subject": REQUEST_SUBJECT,
        "body": _request_body(payload, sealed),
    }


def remember_pending(store, username: str, email: str, nonce: str) -> int:
    _ensure(store)
    store.conn.execute(
        "DELETE FROM pending_registrations WHERE username = ? COLLATE NOCASE",
        (str(username).strip(),))
    cursor = store.conn.execute(
        "INSERT INTO pending_registrations(username, email, nonce, created_at, status)"
        " VALUES (?,?,?,?, 'waiting')",
        (str(username).strip(), str(email).strip(), str(nonce), _stamp()))
    store.conn.commit()
    return int(cursor.lastrowid)


def pending_for(store, username: str) -> Optional[Dict[str, Any]]:
    _ensure(store)
    row = store.conn.execute(
        "SELECT * FROM pending_registrations WHERE username = ? COLLATE NOCASE"
        " ORDER BY id DESC LIMIT 1", (str(username).strip(),)).fetchone()
    return dict(row) if row else None


def send_request(store, username: str, email: str) -> Dict[str, Any]:
    """生成注册申请，并尽量送到管理员那边。

    送法有两条（用户要求的第 1 点）：
    1. **先走局域网直连**：管理员那边程序开着监听时，POST 到
       `http://<管理员IP>:<端口>/lumina-register`，管理员不用去翻邮箱；
    2. 直连不行再**发邮件**（[LUMINA-REG]），还不行就把正文给用户复制手动发。
    """
    request = make_request(store, username, email)
    remember_pending(store, username, email, request["nonce"])

    direct = post_to_admin(store, request)
    if direct.get("ok"):
        extra = {}
        if direct.get("approved"):
            # 老用户换新机器：管理员那边当场放行了，验证码和回执直接回来了
            extra = {"approved": True, "code": str(direct.get("code") or ""),
                     "receipt": str(direct.get("receipt") or ""),
                     "expires_at": str(direct.get("expires_at") or "")}
        return {**request, "sent": True, "channel": "ip",
                "error": "", "detail": direct.get("detail", ""), **extra}

    sent = False
    error = ""
    try:
        from . import mailer

        cfg = mailer.SmtpConfig.from_store(store)
        if cfg.host and cfg.sender_email:
            message = mailer.build_message(
                request["to"], request["subject"], request["body"],
                "<pre style='white-space:pre-wrap'>" + _escape(request["body"]) + "</pre>",
                [], cfg)
            mailer.send_message(message, cfg)
            sent = True
        else:
            error = "这台电脑还没配置发件邮箱，请把下面的内容用你自己的邮箱发给管理员"
    except Exception as exc:  # noqa: BLE001
        error = f"自动发送失败（{exc}），请把下面的内容用你自己的邮箱发给管理员"
    if sent:
        return {**request, "sent": True, "channel": "email", "error": "",
                "detail": direct.get("error", "")}
    return {**request, "sent": False, "channel": "", "error": error,
            "direct_error": direct.get("error", "")}


# ------------------------------------------------------------------ 局域网直连（不走邮件）
REGISTER_PATH = "/lumina-register"
REGISTER_PORT_KEY = "report_listen_port"


def admin_register_url(store) -> str:
    """把「上报地址」换个路径，就是收注册申请的地址。"""
    from . import usage_report

    base = usage_report.report_url(store) or usage_report.DEFAULT_URL
    try:
        from urllib.parse import urlparse

        parsed = urlparse(base)
        port = parsed.port or usage_report.DEFAULT_PORT
        scheme = parsed.scheme or "http"
        return f"{scheme}://{parsed.hostname}:{port}{REGISTER_PATH}"
    except Exception:
        return "http://127.0.0.1:%d%s" % (usage_report.DEFAULT_PORT, REGISTER_PATH)


def post_to_admin(store, request: Dict[str, Any],
                  timeout: float = 5.0) -> Dict[str, Any]:
    """把加密后的申请直接 POST 给管理员程序（局域网内）。"""
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"缺少 requests：{exc}"}
    url = admin_register_url(store)
    payload = {
        "sealed": request.get("sealed") or "",
        "username": request.get("payload", {}).get("username", ""),
        "email": request.get("payload", {}).get("email", ""),
        "nonce": request.get("nonce", ""),
    }
    if not payload["sealed"]:
        return {"ok": False, "error": "申请内容为空"}
    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"管理员那边连不上（{exc}）"}
    if response.status_code >= 400:
        return {"ok": False, "error": f"管理员那边返回 HTTP {response.status_code}"}
    try:
        data = response.json()
    except Exception:
        data = {}
    if not data.get("ok"):
        return {"ok": False, "error": str(data.get("error") or "管理员那边没有接受")}
    result = {"ok": True, "detail": str(data.get("detail") or "已直接送到管理员程序"),
              "repeat": bool(data.get("repeat"))}
    if data.get("approved"):
        # 老用户换新设备：管理员程序当场批准，回执一并带回来
        result.update({"approved": True,
                       "code": str(data.get("code") or ""),
                       "receipt": str(data.get("receipt") or ""),
                       "expires_at": str(data.get("expires_at") or ""),
                       "detail": str(data.get("detail") or "老用户新设备，已自动通过")})
    return result


def ensure_incoming_schema(store) -> None:
    _ensure(store)
    try:
        store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reg_inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE,
                email TEXT NOT NULL DEFAULT '',
                device TEXT NOT NULL DEFAULT '',
                nonce TEXT NOT NULL UNIQUE,
                sealed TEXT NOT NULL DEFAULT '',
                received_at TEXT NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        store.conn.commit()
    except Exception:
        pass


def store_incoming(store, payload: Dict[str, Any]) -> Dict[str, Any]:
    """管理员侧：收到客户端直接 POST 过来的申请（解密后存起来，等审核）。"""
    ensure_incoming_schema(store)
    sealed = str((payload or {}).get("sealed") or "")
    if not sealed:
        body = str((payload or {}).get("body") or "")
        sealed = adminsign.extract_seal(body)
    if not sealed:
        return {"ok": False, "error": "没有密文"}
    try:
        data = adminsign.unseal(store, sealed)
    except adminsign.SignError as exc:
        return {"ok": False, "error": str(exc)}
    username = str(data.get("username") or "").strip()
    nonce = str(data.get("nonce") or (payload or {}).get("nonce") or "")
    if not username:
        return {"ok": False, "error": "申请里没有用户名"}
    repeat = False
    try:
        store.conn.execute(
            "INSERT INTO reg_inbox(username, email, device, nonce, sealed,"
            " received_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(nonce) DO NOTHING",
            (username, str(data.get("email") or ""),
             str(data.get("device") or ""), nonce, sealed, _stamp()))
        store.conn.commit()
        row = store.conn.execute("SELECT id, consumed FROM reg_inbox WHERE nonce=?",
                                 (nonce,)).fetchone()
        repeat = bool(row and int(row["consumed"] or 0) == 0 and row["id"])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"存不下来：{exc}"}
    handler = _INCOMING_HANDLER
    if handler is not None:
        try:
            handler({"username": username, "email": str(data.get("email") or "")})
        except Exception:
            pass
    return {"ok": True, "username": username,
            "detail": f"收到「{username}」的注册申请", "repeat": repeat}


_INCOMING_HANDLER = None


def set_incoming_handler(handler) -> None:
    """主窗口注册：收到直连申请时弹个提示。"""
    global _INCOMING_HANDLER
    _INCOMING_HANDLER = handler


def list_incoming(store, limit: int = 20,
                  pending_only: bool = False) -> List[Dict[str, Any]]:
    ensure_incoming_schema(store)
    sql = "SELECT * FROM reg_inbox"
    if pending_only:
        sql += " WHERE consumed = 0"
    sql += " ORDER BY id DESC LIMIT ?"
    rows = store.conn.execute(sql, (int(limit),)).fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["username"] = item.get("username") or ""
        item["ts"] = item.get("received_at") or ""
        item["via"] = "网络"
        item["_network"] = True
        item["_already"] = has_registry_entry(store, item["username"])
        items.append(item)
    return items


def mark_incoming_consumed(store, nonce: str) -> None:
    ensure_incoming_schema(store)
    try:
        store.conn.execute("UPDATE reg_inbox SET consumed = 1 WHERE nonce = ?",
                           (str(nonce or ""),))
        store.conn.commit()
    except Exception:
        pass


def collect_all_requests(store, limit: int = 200) -> tuple:
    """把「邮件里的申请」和「直连收到的申请」合并（同一 nonce 只留一条）。"""
    found: List[Dict[str, Any]] = []
    errors: List[str] = []
    try:
        mail_items, mail_errors = collect_requests(store, limit=limit)
        found.extend(mail_items)
        errors.extend(mail_errors)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"读邮件失败：{exc}")
    try:
        net_items = list_incoming(store, limit=limit, pending_only=False)
        found.extend(net_items)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"读网络申请失败：{exc}")
    merged: List[Dict[str, Any]] = []
    seen = set()
    for item in found:
        key = (str(item.get("username") or "").lower(),
               str(item.get("nonce") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged, errors


def _escape(text: str) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# ------------------------------------------------------------------ 管理员：读申请
def _as_prefixes(value) -> Tuple[str, ...]:
    """把「单个前缀」或「一组前缀」统一成元组（None 表示注册申请的全部前缀）。"""
    if value is None:
        return REQUEST_PREFIXES
    if isinstance(value, str):
        return (value,)
    return tuple(one for one in value if one)


def _imap_text_messages(store, limit: int = 200, since: Optional[str] = None,
                        days: int = 30, prefixes=None
                        ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """读所有 IMAP 账户里「标题带 [LUMINA-」的邮件（只用需要联网的地方，调用方放后台线程）。

    以前是把最近 20 封整封拉下来、再逐封重新登录取正文，慢到界面卡住；
    现在直接让服务器按主题过滤（`imap_inbox.fetch_subject_messages`），
    一次连接同时拿头部和正文。

    2026-11 修：**每个账户的失败不再被静默吞掉**。原来 `except Exception: continue`
    会让「密码过期 / 授权码失效 / TLS 失败 / 20 秒超时」全部表现为
    「（没有新的注册申请）」，管理员完全不知道为什么刷不出来。
    """
    from . import imap_inbox

    results: List[Dict[str, Any]] = []
    errors: List[str] = []
    if since is None:
        since = imap_inbox.imap_since(days)
    wanted = _as_prefixes(prefixes)
    # IMAP 的 SUBJECT 搜索用「不含 REG」的短前缀，把两代命名一起捞回来
    search_prefixes = [one.replace("REG", "") if one.endswith("REG") else one
                       for one in wanted]
    for account in imap_inbox.list_accounts(store):
        name = account.get("name") or account.get("user") or f"#{account.get('id')}"
        try:
            summaries = imap_inbox.fetch_subject_messages(
                store, account, limit=limit, since=since,
                prefixes=search_prefixes)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"账户「{name}」读不到：{exc}")
            continue
        for item in summaries:
            subject = str(item.get("subject") or "")
            if not subject.startswith(wanted):
                continue
            results.append({
                "uid": item.get("uid"),
                "subject": subject,
                "from": item.get("from"),
                "date": item.get("date"),
                "folder": item.get("folder") or "INBOX",
                "account": item.get("account_name") or account.get("name") or "",
                "body": str(item.get("body") or item.get("snippet") or ""),
            })
    return results, errors


def request_from_mail_item(store, item: Dict[str, Any]) -> Dict[str, Any]:
    """把一封 [LUMINA-*] 邮件（或 .eml 解析出来的同样结构）解成一条申请。"""
    subject = str(item.get("subject") or "")
    sealed = adminsign.extract_seal(str(item.get("body") or ""))
    if not sealed:
        raise RegisterError("这封邮件里没有找到密文（LUMINA-SEAL1.…）"
                            "：可能正文被改过，或者只是抄送/转发没有原文")
    payload = adminsign.unseal(store, sealed)
    payload["_mail"] = {key: item.get(key) for key in
                        ("uid", "subject", "from", "date", "folder")}
    payload["_already"] = has_registry_entry(store, payload.get("username", ""))
    return payload


def request_from_eml(store, path: str) -> Dict[str, Any]:
    """从本机 .eml 文件里救回一条申请（邮件都翻不到时的兜底）。

    用法：在网页邮箱里把那封 `[LUMINA-REG]` 邮件「另存为 / 下载为 .eml」，
    这里读出来走同一条验签解密路径。
    """
    import email
    import email.policy

    _ensure(store)
    with open(path, "rb") as handle:
        raw = handle.read()
    try:
        message = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as exc:  # noqa: BLE001
        raise RegisterError(f"这个文件不是合法的邮件：{exc}") from exc

    subject = str(message.get("Subject") or "")
    if "[LUMINA-" not in subject:
        raise RegisterError(
            "这封邮件不是 DanLume 的申请（主题里没有 [LUMINA-]）：" + (subject or "（无主题）"))

    body = ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            try:
                text = part.get_content()
            except Exception:
                continue
            if isinstance(text, str) and "LUMINA-SEAL" in text:
                body = text
                break
    else:
        try:
            body = str(message.get_content() or "")
        except Exception:
            body = ""

    item = {
        "uid": "",
        "subject": subject,
        "from": str(message.get("From") or ""),
        "date": str(message.get("Date") or ""),
        "folder": str(path),
        "body": body,
    }
    request = request_from_mail_item(store, item)
    request["via"] = "文件"
    request["_eml"] = str(path)
    return request


def collect_requests(store, limit: int = 200,
                     subject_prefix=None,
                     days: int = 30
                     ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """管理员侧：从收件箱里收申请（默认注册申请），解密后返回。

    `subject_prefix` 可以是单个字符串，也可以是一组前缀；不传就同时认
    `[LUMINA-REG]` 和 `[DANLUME-REG]`（改名后新客户端用的是后者）。
    """
    _ensure(store)
    wanted = _as_prefixes(subject_prefix)
    found: List[Dict[str, Any]] = []
    errors: List[str] = []
    try:
        messages, imap_errors = _imap_text_messages(
            store, limit=limit, days=days, prefixes=wanted)
    except Exception as exc:  # noqa: BLE001
        return [], [f"读收件箱失败：{exc}"]
    errors.extend(imap_errors)
    for item in messages:
        subject = str(item.get("subject") or "")
        if not subject.startswith(wanted):
            continue
        try:
            payload = request_from_mail_item(store, item)
        except adminsign.SignError as exc:
            errors.append(f"{subject[:32]}…：{exc}")
            continue
        except RegisterError as exc:
            errors.append(f"{subject[:32]}…：{exc}")
            continue
        payload["_via_folder"] = item.get("folder") or "INBOX"
        found.append(payload)
    return found, errors


# ------------------------------------------------------------------ 管理员：批准
def new_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(CODE_LEN))


def _code_hash(code: str) -> str:
    return users.hash_password(str(code), iterations=60_000)


def _verify_code(code: str, stored: str) -> bool:
    return users.verify_password(str(code), stored)


def approve(store, request: Dict[str, Any], days: Optional[int] = None,
            note: str = "") -> Dict[str, Any]:
    """批准一条申请：登记注册表 + 生成验证码 + 签回执 + 邮件正文（不发送）。"""
    _ensure(store)
    username = str(request.get("username") or "").strip()
    email = str(request.get("email") or "").strip()
    if not username or "@" not in email:
        raise RegisterError("这条申请缺少用户名或邮箱")
    if not users.email_domain_ok(email):
        raise RegisterError("邮箱不合规，不能批准：" + users.email_policy_text())
    if has_registry_entry(store, username):
        raise RegisterError(f"「{username}」已经在注册表里了")
    if not adminsign.has_private_key(store):
        raise RegisterError("这台机器还没有管理员私钥，没法签发回执"
                            "（先在本机生成一次密钥）")
    valid_days = default_valid_days(store) if days is None else max(1, int(days))
    expires = _date_after(valid_days)
    code = new_code()
    receipt_payload = _approval_payload(
        store, username, email, str(request.get("device") or ""), code, expires,
        str(request.get("nonce") or ""))
    token = adminsign.sign_receipt(store, receipt_payload)
    store.conn.execute(
        "INSERT INTO reg_users(username, email, status, issued_at, expires_at,"
        " code_hash, note) VALUES (?,?,?,?,?,?,?)",
        (username, email, "active", _stamp(), expires, _code_hash(code),
         str(note or "")))
    store.conn.commit()
    register_device(store, username, str(request.get("device") or ""), "首次批准")
    body = (
        "你的 DanLume 注册申请已通过。\n\n"
        f"账户：{username}\n"
        f"绑定邮箱：{email}\n"
        f"有效期到：{expires}\n\n"
        f"验证码：{code}\n\n"
        "请在注册窗口里填入验证码，并把下面这一整行回执粘进「回执」输入框：\n"
        f"{token}\n\n"
        "回执里有管理员私钥的签名，程序会用它确认这条批准没被篡改。\n"
    )
    return {"username": username, "email": email, "code": code, "receipt": token,
            "expires_at": expires, "subject": APPROVED_SUBJECT, "body": body}


def send_approval(store, approval: Dict[str, Any]) -> Dict[str, Any]:
    """把「验证码 + 回执」发给申请人。

    用户要求：**验证码用学邮发**。所以这里不走「当前选中的发件账户」，
    而是用 `pick_sender_account()` 选出来的那一个（默认优先学邮）。
    """
    sent = False
    error = ""
    account = None
    try:
        from . import mailer

        account = pick_sender_account(store)
        cfg = mailer.SmtpConfig.from_account(store, account or {})
        if cfg.is_configured():
            message = mailer.build_message(
                approval["email"], approval["subject"], approval["body"],
                "<pre style='white-space:pre-wrap'>" + _escape(approval["body"]) + "</pre>",
                [], cfg)
            mailer.send_message(message, cfg)
            sent = True
        else:
            error = ("这台电脑还没配置发件邮箱：请手动把这封批准邮件发给申请人"
                     "（设置 → 发件邮箱）")
    except Exception as exc:  # noqa: BLE001
        error = f"自动发送失败（{exc}），请手动把批准内容发给申请人"
    return {**approval, "sent": sent, "error": error,
            "sender": (account or {}).get("sender_email", "")}


# ------------------------------------------------------------------ 注册表
def list_registry(store) -> List[Dict[str, Any]]:
    _ensure(store)
    rows = store.conn.execute(
        "SELECT * FROM reg_users ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def has_registry_entry(store, username: str) -> bool:
    _ensure(store)
    row = store.conn.execute(
        "SELECT 1 FROM reg_users WHERE username = ? COLLATE NOCASE LIMIT 1",
        (str(username).strip(),)).fetchone()
    return row is not None


def set_registry_status(store, username: str, status: str) -> None:
    _ensure(store)
    store.conn.execute(
        "UPDATE reg_users SET status = ? WHERE username = ? COLLATE NOCASE",
        ("disabled" if str(status) == "disabled" else "active", str(username).strip()))
    store.conn.commit()


def delete_registry(store, username: str) -> bool:
    """**注销**：把这条记录从注册表里彻底删掉。

    删掉之后：
    * 这个名字可以重新申请注册（`approve` 不会再报「已经在注册表里了」）；
    * 他那台电脑上已经装好的账户**不会被远程删除**（回执是离线签名，收不回来），
      但下次上报使用情况时会被识别成「未注册的账户」，按提醒 / 警告 / 封号的既有流程处理。
    """
    _ensure(store)
    name = str(username or "").strip()
    if not name:
        raise RegisterError("要注销哪个账户？（没给用户名）")
    row = store.conn.execute(
        "SELECT 1 FROM reg_users WHERE username = ? COLLATE NOCASE LIMIT 1",
        (name,)).fetchone()
    if row is None:
        raise RegisterError(f"注册表里没有「{name}」")
    store.conn.execute("DELETE FROM reg_users WHERE username = ? COLLATE NOCASE",
                       (name,))
    store.conn.commit()
    return True


def renew_registry(store, username: str, days: int) -> str:
    """续期：更新注册表到期日，并重新签发回执（申请人需要重新粘贴一次）。"""
    _ensure(store)
    row = store.conn.execute(
        "SELECT * FROM reg_users WHERE username = ? COLLATE NOCASE",
        (str(username).strip(),)).fetchone()
    if row is None:
        raise RegisterError(f"注册表里没有「{username}」")
    expires = _date_after(days)
    receipt_payload = {
        "v": 1, "kind": "registration-receipt",
        "username": row["username"], "email": row["email"],
        "nonce": "", "device": "", "code": "",
        "expires_at": expires, "issued_at": _stamp(), "renewal": True,
    }
    token = adminsign.sign_receipt(store, receipt_payload)
    store.conn.execute(
        "UPDATE reg_users SET expires_at = ? WHERE id = ?",
        (expires, int(row["id"])))
    store.conn.commit()
    return token


# ------------------------------------------------------------------ 申请人：验证回执
def prepare_verify(store, username: str, receipt_token: str,
                   public_pem_text: str) -> Dict[str, Any]:
    """只验签和绑定关系，不落库（界面先校验，最后一步才建账户）。"""
    payload = adminsign.verify_receipt(receipt_token, public_pem_text)
    name = str(username or "").strip()
    if str(payload.get("kind") or "") != "registration-receipt":
        raise RegisterError("这行回执不是「注册批准」用的")
    if name and str(payload.get("username") or "").lower() != name.lower():
        raise RegisterError(
            f"回执是发给「{payload.get('username')}」的，不是你这个用户名")
    pending = pending_for(store, name)
    if pending is not None and str(payload.get("nonce") or ""):
        if str(payload.get("nonce")) != str(pending.get("nonce")):
            raise RegisterError("回执和这台电脑发出的申请对不上（随机串不一致）")
    return payload


def verify_code(payload: Dict[str, Any], code: str) -> bool:
    return str(payload.get("code") or "") == str(code or "").strip()


def finish_registration(store, payload: Dict[str, Any], password: str) -> int:
    """验证通过后建本地账户（普通用户，带邮箱与有效期）。"""
    _ensure(store)
    username = str(payload.get("username") or "").strip()
    email = str(payload.get("email") or "").strip()
    user_id = users.create_user(
        store, username, password, email=email, role="user",
        expires_at=str(payload.get("expires_at") or ""),
        receipt=json.dumps(payload, ensure_ascii=False),
    )
    store.conn.execute(
        "UPDATE pending_registrations SET status = 'approved'"
        " WHERE username = ? COLLATE NOCASE", (username,))
    store.conn.commit()
    return user_id
