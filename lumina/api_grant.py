"""使用管理员模型接口（`10.137.44.215:8080` 这类）的授权：邮件申请 + 签名授权 + 到期重申请。

* 管理员地址写在设置里（`admin_api_hosts`，逗号分隔）。只有打到这些地址时才需要授权；
  自己填 DeepSeek / 豆包这种公网接口不受影响；
* 授权有效期 = min(管理员给的窗口, 5 小时)（`GRANT_MAX_HOURS`）；到期后再用必须重新申请；
* 授权凭据是管理员私钥签名的回执（`LUMINA-GRANT1.`），本机用随包公钥验签，
  改一个字节或改到期时间都验不过。
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from . import adminsign, users

DEFAULT_HOSTS = "10.137.44.215"
HOSTS_KEY = "admin_api_hosts"
GRANT_MAX_HOURS = 5
GRANT_PREFIX = "LUMINA-GRANT1."
API_SUBJECT = "[LUMINA-API] DanLume 模型接口使用申请"
API_SUBJECT_APPROVED = "[LUMINA-API-OK] 模型接口使用申请已批准"


class GrantError(Exception):
    pass


# ------------------------------------------------------------------ 设置
def admin_hosts(store) -> List[str]:
    raw = store.get_setting(HOSTS_KEY, DEFAULT_HOSTS) or DEFAULT_HOSTS
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def set_admin_hosts(store, text: str) -> None:
    store.set_setting(HOSTS_KEY, str(text or "").strip())


def is_admin_endpoint(store, base_url: str) -> bool:
    try:
        host = urlparse(str(base_url or "")).hostname or ""
    except Exception:
        host = ""
    if not host:
        return False
    return host in admin_hosts(store)


# ------------------------------------------------------------------ 本机授权的状态
def _grants_table(store) -> None:
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS api_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT 'admin-api',
            token TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )"""
    )
    store.conn.commit()


def _now() -> datetime:
    return datetime.now()


def _parse(text: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(text), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def current_grant(store) -> Optional[Dict[str, Any]]:
    _grants_table(store)
    row = store.conn.execute(
        "SELECT * FROM api_grants ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    expires = _parse(row["expires_at"])
    if expires is None or expires <= _now():
        return None
    return {"expires_at": row["expires_at"],
            "seconds_left": int((expires - _now()).total_seconds()),
            "issued_at": row["issued_at"], "token": row["token"]}


def grant_state(store, base_url: str = "") -> Dict[str, Any]:
    """给界面用的一句话状态。"""
    grant = current_grant(store)
    if grant is None:
        return {"ok": False, "reason": "没有有效的使用授权",
                "text": "需要授权：申请后管理员批准才能用这个模型接口"}
    left = grant["seconds_left"]
    hours, minutes = divmod(max(0, left) // 60, 60)
    return {"ok": True, "expires_at": grant["expires_at"],
            "seconds_left": left,
            "text": f"授权有效，还剩 {hours} 小时 {minutes} 分钟（{grant['expires_at']} 到期）"}


def save_grant(store, token: str, public_pem_text: str) -> Dict[str, Any]:
    """把邮件里收到的授权回执存下来（验签 + 看是否过期）。"""
    payload = verify_grant(token, public_pem_text)
    expires = str(payload.get("expires_at") or "")
    if _parse(expires) is None:
        raise GrantError("授权里的到期时间看不懂")
    if _parse(expires) <= _now():
        raise GrantError("这份授权已经过期了，请重新申请")
    _grants_table(store)
    store.conn.execute(
        "INSERT INTO api_grants(username, scope, token, issued_at, expires_at)"
        " VALUES (?,?,?,?,?)",
        (str(payload.get("username") or ""), "admin-api", str(token),
         str(payload.get("issued_at") or _now().strftime("%Y-%m-%d %H:%M:%S")),
         expires))
    store.conn.commit()
    return payload


def revoke(store) -> None:
    _grants_table(store)
    store.conn.execute("DELETE FROM api_grants")
    store.conn.commit()


def require(store, base_url: str) -> None:
    """发请求前调用：管理员的接口必须授权没过期。

    * **管理员本人不受限**：这些限制是给普通用户定的，管理员用自己的接口不需要申请；
    * 这里**不看**「是否启用登录」——关掉登录开关也不能顺手把接口授权绕过去。
    """
    if not is_admin_endpoint(store, base_url):
        return
    try:
        if users.is_admin_now(store):
            return
    except Exception:
        pass
    if current_grant(store) is None:
        raise GrantError(
            "这个模型接口需要管理员授权（每次最多 5 小时）："
            "到 设置 → AI 接口 →「申请模型接口权限」发一封申请，"
            "批准后把邮件里的授权粘回来即可")


# ------------------------------------------------------------------ 签名（管理员）
def sign_grant(store, payload: Dict[str, Any]) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    body = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).decode("ascii")
    signature = adminsign.private_key(store).sign(
        body.encode("ascii"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return GRANT_PREFIX + body + "." + base64.urlsafe_b64encode(signature).decode()


def verify_grant(token: str, public_pem_text: str) -> Dict[str, Any]:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    text = str(token or "").strip()
    if not text.startswith(GRANT_PREFIX):
        raise GrantError("这不是模型接口授权（缺少 LUMINA-GRANT1 标记）")
    rest = text[len(GRANT_PREFIX):]
    if "." not in rest:
        raise GrantError("授权格式不对（少了签名）")
    body, signature = rest.rsplit(".", 1)
    try:
        pub = serialization.load_pem_public_key(public_pem_text.encode("ascii"))
    except Exception as exc:  # noqa: BLE001
        raise GrantError(f"公钥读不出来：{exc}") from exc
    try:
        pub.verify(
            base64.urlsafe_b64decode(signature.encode("ascii")),
            body.encode("ascii"),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256())
    except Exception as exc:  # noqa: BLE001
        raise GrantError("授权签名不对（改过，或者不是这位管理员签发的）") from exc
    try:
        return json.loads(base64.urlsafe_b64decode(body.encode("ascii")).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise GrantError(f"授权内容读不出来：{exc}") from exc


def extract_grant(text: str) -> str:
    for line in str(text or "").replace("\r", "").split("\n"):
        line = line.strip()
        if line.startswith(GRANT_PREFIX):
            return line
    return ""


# ------------------------------------------------------------------ 申请（申请人）
def make_request(store, hours: int = GRANT_MAX_HOURS, username: str = "") -> Dict[str, Any]:
    name = str(username or "").strip()
    if not name:
        users_list = users.list_users(store)
        name = users_list[0]["username"] if users_list else ""
    pub = adminsign.public_pem(store)
    if not pub:
        raise GrantError("程序里没有管理员公钥，没法加密申请")
    if not name:
        raise GrantError("先登录一个账户再申请")
    hours = max(1, min(GRANT_MAX_HOURS, int(hours or GRANT_MAX_HOURS)))
    # 带上申请人的邮箱：管理员批准后要**自动把授权邮件发给他**，
    # 以前 payload 里没有 email，管理员那边只能手动转发。
    email = ""
    try:
        current = users.current_user(store)
        email = str((current or {}).get("email") or "")
        if not email:
            users_list = users.list_users(store)
            email = str((users_list[0] or {}).get("email") or "") if users_list else ""
    except Exception:
        email = ""
    payload = {
        "v": 1, "kind": "api-grant-request",
        "username": name,
        "email": email,
        "device": users.device_hash(),
        "hosts": admin_hosts(store),
        "hours": hours,
        "ts": _now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    sealed = adminsign.seal(payload, pub)
    body = (
        "这是一封 DanLume「使用管理员模型接口」的申请（程序自动生成）。\n\n"
        f"申请人：{name}\n"
        f"设备指纹：{payload['device']}\n"
        f"申请的接口：{', '.join(payload['hosts'])}\n"
        f"申请时长：{hours} 小时（上限，管理员可再缩短）\n"
        f"申请时间：{payload['ts']}\n\n"
        "加密后的内容：\n"
        f"{sealed}\n\n"
        "管理员：DanLume → 设置 → 账户与登录 →「账户审核（管理员）」→「检查 API 申请」。\n"
    )
    return {"payload": payload, "sealed": sealed, "body": body,
            "subject": API_SUBJECT, "to": _registry_email(store)}


def _registry_email(store) -> str:
    from . import registration

    return registration.registry_email(store)


def send_request(store, hours: int = GRANT_MAX_HOURS, username: str = "") -> Dict[str, Any]:
    request = make_request(store, hours, username)
    sent = False
    error = ""
    try:
        from . import mailer

        cfg = mailer.SmtpConfig.from_store(store)
        if cfg.host and cfg.sender_email:
            message = mailer.build_message(
                request["to"], request["subject"], request["body"],
                "<pre style='white-space:pre-wrap'>"
                + request["body"].replace("&", "&amp;").replace("<", "&lt;")
                + "</pre>", [], cfg)
            mailer.send_message(message, cfg)
            sent = True
        else:
            error = "这台电脑还没配置发件邮箱，请把下面的内容用你的邮箱发给管理员"
    except Exception as exc:  # noqa: BLE001
        error = f"自动发送失败（{exc}），请把下面的内容用你的邮箱发给管理员"
    return {**request, "sent": sent, "error": error}


# ------------------------------------------------------------------ 批准（管理员）
def approve(store, request: Dict[str, Any], hours: Optional[int] = None) -> Dict[str, Any]:
    """批准一条 API 申请：签发授权，到期 = min(管理者给的时长, 5 小时)。"""
    username = str(request.get("username") or "").strip()
    if not username:
        raise GrantError("这条申请缺少申请人")
    if not adminsign.has_private_key(store):
        raise GrantError("这台机器没有管理员私钥，没法签发授权")
    limit = GRANT_MAX_HOURS
    try:
        asked = int(request.get("hours") or limit)
    except (TypeError, ValueError):
        asked = limit
    wanted = min(limit, max(1, int(hours or asked)))
    expires = (_now() + timedelta(hours=wanted)).strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "v": 1, "kind": "api-grant",
        "username": username,
        "email": str(request.get("email") or ""),
        "device": str(request.get("device") or ""),
        "hosts": request.get("hosts") or admin_hosts(store),
        "expires_at": expires,
        "issued_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
        "hours": wanted,
    }
    token = sign_grant(store, payload)
    body = (
        "你的 DanLume 模型接口使用申请已批准。\n\n"
        f"申请人：{username}\n"
        f"可用接口：{', '.join(payload['hosts'])}\n"
        f"授权时长：{wanted} 小时（到 {expires} 为止）\n\n"
        "把下面这一整行授权粘回 DanLume（设置 → AI 接口 →「粘贴授权」）：\n"
        f"{token}\n\n"
        "到期后需要重新申请。\n"
    )
    return {"username": username, "email": str(request.get("email") or ""),
            "token": token, "expires_at": expires, "hours": wanted,
            "subject": API_SUBJECT_APPROVED, "body": body}


def send_approval(store, approval: Dict[str, Any]) -> Dict[str, Any]:
    sent = False
    error = ""
    sender = ""
    try:
        from . import mailer, registration

        # 和「验证码」一样：用管理员选定的发件账户（默认优先学邮）
        account = registration.pick_sender_account(store)
        cfg = mailer.SmtpConfig.from_account(store, account or {})
        sender = cfg.sender_email
        if cfg.is_configured() and "@" in str(approval.get("email") or ""):
            message = mailer.build_message(
                approval["email"], approval["subject"], approval["body"],
                "<pre style='white-space:pre-wrap'>"
                + approval["body"].replace("&", "&amp;").replace("<", "&lt;")
                + "</pre>", [], cfg)
            mailer.send_message(message, cfg)
            sent = True
        else:
            error = "没法自动发信（没有发件邮箱或不知道对方邮箱）：请手动把授权发给申请人"
    except Exception as exc:  # noqa: BLE001
        error = f"自动发送失败（{exc}）：请手动把授权发给申请人"
    return {**approval, "sent": sent, "error": error, "sender": sender}


# ------------------------------------------------------------------ 管理员读申请
def collect_requests(store):
    from . import registration

    # 和注册申请一样：改名前后两代主题前缀都收（[LUMINA-API] / [DANLUME-API]）
    return registration.collect_requests(
        store, subject_prefix=("[LUMINA-API", "[DANLUME-API"))
