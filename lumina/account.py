"""自家账号服务器（FastAPI，`/api/v1/*`）的客户端。

定位：这是「服务器账号」这条路，和本机的 `users.py`（离线、只在本机比对密码）
是两条并行的路。老路（客户端 RSA 信封 → 邮件给管理员 → 管理员签发，见
`registration.py` / `adminsign.py`）**保留**，服务器不可达时可以退回去。

约定（很重要，改这个文件前先看一眼）：

* **不抛异常给界面**。所有函数都返回 dict：
  `{"ok", "code", "error", "status", "offline", "insecure", "data", ...}`
  其中 `offline=True` 专指「网络根本到不了服务器」（DNS / 拒绝连接 / 超时 /
  证书），界面据此提示「服务器不可达」并给退路；
* 令牌只经 `secure.py`（Windows 上就是 DPAPI）加密后落到 settings：
  `server_token` / `server_refresh_token`（前缀 `dpapi:`）；
* 其余非机密信息走普通 settings：`server_url`、`server_username`、
  `server_account_id`、`device_fingerprint`、`last_announcement_id`。

TLS 说明（别改回去）：服务器用 Let's Encrypt 证书，SAN 就是
`43.108.99.38.sslip.io`，链是完整的。麻烦在于这台机器上的 Python/OpenSSL：
`ssl.create_default_context()` 自带的主机名校验会把这个名字误判成 IP，报
`CERTIFICATE_VERIFY_FAILED ... IP address mismatch`。所以这里的规矩是：

* **证书链永远校验**（`verify_mode=CERT_REQUIRED`，只用系统根证书）；
* **主机名由本文件自己校验**：`check_hostname=False` 关掉 Python 的，握手成功
  后从同一个连接取对端证书，按 `subjectAltName`（没有 SAN 才退回 `commonName`）
  比对请求的主机名（见 `_cert_matches_host`）；
* 比对不过就是**失败**（`code=tls_error`），请求一个字节都不发出去；
* **没有**任何「把 verify 关掉再试一次」的降级路径。`SETTING_TLS_INSECURE`
  只是老数据里可能还留着 `1`，代码不再读它；结果里的 `insecure` 恒为 `False`。
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import platform
import secrets
import socket
import ssl
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote, urlencode, urlsplit

from . import secure

try:                                  # PySide6 一定有；这里只是让纯脚本导入也能跑
    from PySide6.QtCore import QObject, Signal
except Exception:                     # pragma: no cover
    class QObject:                    # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

    class Signal:                     # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

DEFAULT_SERVER_URL = "https://43.108.99.38.sslip.io"
API_PREFIX = "/api/v1"
HEALTH_TIMEOUT = 5.0
REQUEST_TIMEOUT = 10.0
USER_AGENT = "DanLume/%s" % "1.0.0"

# ---------------------------------------------------------------- settings 键
SETTING_URL = "server_url"
SETTING_USERNAME = "server_username"
SETTING_ACCOUNT_ID = "server_account_id"
SETTING_FINGERPRINT = "device_fingerprint"
SETTING_DEVICES_USED = "server_devices_used"
SETTING_DEVICE_LIMIT = "server_device_limit"
SETTING_EXPIRES_AT = "server_token_expires_at"
# 老版本用它记「已经降级为不校验证书」；现在只保留键名做数据兼容，代码不再读它
SETTING_TLS_INSECURE = "server_tls_insecure"
SETTING_LAST_ANNOUNCEMENT = "last_announcement_id"      # 公告游标（server_feed 用）
SECRET_TOKEN = "server_token"
SECRET_REFRESH = "server_refresh_token"

# 本机 `users` 表里标记「这是服务器账号镜像」的回执值：
# 带 receipt 的账户不会被当成本机管理员（见 users._ensure_first_admin）
RECEIPT_SERVER = "server-account"

_STORE = None                    # bind() 绑定的默认数据层（界面层可以不传 store）
_FALLBACK_FP = ""                 # 没 bind store 时的指纹（进程内只生成一次）


# ================================================================ 数据层绑定
def bind(store) -> None:
    """把一个 Store 绑成默认数据层；之后各接口可以不显式传 store。"""
    global _STORE
    _STORE = store


def bound_store():
    return _STORE


def _resolve(store=None):
    return store if store is not None else _STORE


# ================================================================ 地址 / 设备
def normalize_url(url: str) -> str:
    """把用户填的地址整理成 `scheme://host[:port]`（去掉尾斜杠和误粘的 /api/v1）。"""
    text = str(url or "").strip()
    if not text:
        return DEFAULT_SERVER_URL
    if "://" not in text:
        text = "https://" + text
    text = text.rstrip("/")
    lowered = text.lower()
    index = lowered.find(API_PREFIX)
    if index > 0:
        text = text[:index]
    return text.rstrip("/") or DEFAULT_SERVER_URL


def server_url(store=None) -> str:
    target = _resolve(store)
    if target is None:
        return DEFAULT_SERVER_URL
    try:
        return normalize_url(target.get_setting(SETTING_URL, "") or DEFAULT_SERVER_URL)
    except Exception:
        return DEFAULT_SERVER_URL


def set_server_url(store, url: str) -> str:
    """记住服务器地址（下次登录 / 公告连接都用它），返回整理后的地址。"""
    value = normalize_url(url)
    if store is not None:
        try:
            store.set_setting(SETTING_URL, value)
        except Exception:
            pass
    return value


def device_name() -> str:
    """上报给服务器的设备名（认得出是哪台电脑就行）。"""
    try:
        host = platform.node() or ""
    except Exception:
        host = ""
    text = str(host).strip() or "DanLume 客户端"
    return text[:40]


def platform_tag() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform or "unknown")


def device_fingerprint(store=None) -> str:
    """设备指纹：本机第一次需要时生成一次随机 id，之后一直用它。"""
    global _FALLBACK_FP
    target = _resolve(store)
    if target is None:
        if not _FALLBACK_FP:
            _FALLBACK_FP = "lumina-" + secrets.token_hex(16)
        return _FALLBACK_FP
    try:
        value = str(target.get_setting(SETTING_FINGERPRINT, "") or "").strip()
    except Exception:
        value = ""
    if value:
        return value
    value = "lumina-" + secrets.token_hex(16)
    try:
        target.set_setting(SETTING_FINGERPRINT, value)
    except Exception:
        pass
    return value


# ================================================================ 结果封装
def _result(
    ok: bool = False,
    code: str = "",
    error: str = "",
    status: int = 0,
    offline: bool = False,
    insecure: bool = False,
    data: Optional[Dict[str, Any]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ok": bool(ok),
        "code": code or ("ok" if ok else "error"),
        "error": "" if ok else str(error or ""),
        "status": int(status or 0),
        "offline": bool(offline),
        "insecure": bool(insecure),
        "data": dict(data or {}),
    }
    payload.update(extra)
    return payload


_CODE_TEXT = {
    "offline": "服务器不可达（网络不通、地址不对，或者被防火墙拦了）",
    "tls_error": "服务器证书校验不过，连接被拒",
    "invalid_credentials": "账号或口令不正确",
    "unauthorized": "登录已失效，请重新登录服务器账号",
    "register_closed": "这个服务器现在不开放注册（要为邀请码，或者找管理员开通）",
    "invite_invalid": "邀请码不对或者已经用过了",
    "invite_required": "注册需要邀请码",
    "device_limit": "登录设备数超过上限",
    "username_taken": "这个用户名已经被占用了",
    "email_taken": "这个邮箱已经注册过了",
    "banned": "这个账号已被停用，请联系管理员",
    "not_found": "服务器上没有这个接口（服务端版本可能比较旧）",
    "validation_error": "填的内容服务器不认（检查一下用户名 / 邮箱 / 口令长度）",
    "no_session": "这台设备还没有登录服务器账号",
    "not_verified": "邮箱还没验证",
    "server_error": "服务器内部出错",
    "disabled": "账号或设备已被停用",
}


def _server_message(data: Dict[str, Any]) -> str:
    """服务器自己给的中文说明（有就优先用）。"""
    if not isinstance(data, dict):
        return ""
    for key in ("message", "detail", "msg", "error_description"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:            # FastAPI 校验错误
            first = value[0]
            if isinstance(first, dict):
                text = str(first.get("msg") or "").strip()
                if text:
                    return text
    return ""


def _message(status: int, data: Dict[str, Any]) -> str:
    code = str((data or {}).get("error") or "").strip()
    base = _CODE_TEXT.get(code, "")
    server_text = _server_message(data)
    if base and server_text and server_text != base:
        return "%s：%s" % (base, server_text)
    if base:
        return base
    if server_text:
        return server_text
    if status >= 500:
        return "%s（HTTP %d）" % (_CODE_TEXT["server_error"], status)
    if status:
        return "服务器返回 HTTP %d" % status
    return "请求失败"


def _decode(raw: bytes) -> Dict[str, Any]:
    """按 UTF-8 解 JSON（不看 Content-Type 里的 charset：没写时会被猜成别的编码）。"""
    raw = raw or b""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {"raw": raw[:1500].decode("utf-8", "replace")}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


# ================================================================ TLS 校验
# 这里的规矩（别改回去）：**证书链永远校验**，主机名自己校验，不匹配就失败。
# 起因：这台机器上的 Python/OpenSSL 把 `43.108.99.38.sslip.io` 误判成 IP，
# `create_default_context()` 自带的 hostname 校验会报 `IP address mismatch`；
# 老代码因此把 verify 降成 False 并缓存下来 —— 那是把整个 TLS 关掉，不能要。
# 现在：`check_hostname=False` + `verify_mode=CERT_REQUIRED`（链照校），握手
# 成功后从同一个 socket 取对端证书，按 SAN / CN 自己比对主机名。
_SSL_LOCK = threading.Lock()
_SSL_CONTEXT: Optional[ssl.SSLContext] = None


def _tls_context() -> ssl.SSLContext:
    """校验证书链、但不由 OpenSSL 校验主机名的 context（进程内复用一份）。"""
    global _SSL_CONTEXT
    with _SSL_LOCK:
        if _SSL_CONTEXT is not None:
            return _SSL_CONTEXT
        context = ssl.create_default_context()      # 系统根证书 + 安全默认选项
        context.check_hostname = False              # 主机名交给 _cert_matches_host
        context.verify_mode = ssl.CERT_REQUIRED     # 证书链必须校验通过
        try:
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            pass
        try:
            context.load_default_certs()
        except Exception:
            pass
        _SSL_CONTEXT = context
        return context


def _ip_literal(host: str):
    """主机是 IP 字面量就返回 ip 对象，否则 None。"""
    try:
        return ipaddress.ip_address(str(host or "").strip().strip("[]"))
    except ValueError:
        return None


def _match_dns_name(pattern: str, host: str) -> bool:
    """DNS 名字比对：大小写不敏感；`*.example.com` 只允许匹配一层标签。"""
    pattern = str(pattern or "").strip().rstrip(".").lower()
    host = str(host or "").strip().rstrip(".").lower()
    if not pattern or not host:
        return False
    if pattern == host:
        return True
    if not pattern.startswith("*."):
        return False
    suffix = pattern[2:]
    if not suffix or "*" in suffix:
        return False
    head, dot, tail = host.partition(".")
    return bool(head) and bool(dot) and tail == suffix


def _cert_names(cert: Optional[dict]) -> Tuple[list, list, list]:
    """拆出证书里的 (DNS 名字, IP 名字, commonName)。"""
    dns: list = []
    ips: list = []
    common: list = []
    if not isinstance(cert, dict):
        return dns, ips, common
    for entry in cert.get("subjectAltName") or ():
        try:
            kind, value = entry
        except (TypeError, ValueError):
            continue
        kind = str(kind or "").strip().lower()
        value = str(value or "").strip()
        if not value:
            continue
        if kind == "dns":
            dns.append(value)
        elif kind in ("ip address", "ipaddress", "ip"):
            ips.append(value)
    for rdn in cert.get("subject") or ():
        for key, value in rdn or ():
            if str(key or "").strip().lower() in ("commonname", "cn"):
                text = str(value or "").strip()
                if text:
                    common.append(text)
    return dns, ips, common


def _cert_matches_host(cert: Optional[dict], host: str) -> Tuple[bool, str]:
    """对端证书是不是这个主机（host 可能是 DNS 名，也可能是 IP 字面量）。

    返回 `(是否匹配, 不匹配时的中文原因)`。IP 主机只认 `IP Address` 那一项，
    DNS 主机只认 `DNS` 那一项；只有完全没写 SAN 的老证书才回退到 commonName。
    """
    host = str(host or "").strip().strip("[]").rstrip(".").lower()
    ip = _ip_literal(host)
    dns, ips, common = _cert_names(cert)
    if dns or ips:
        if ip is not None:
            for value in ips:
                if _ip_literal(value) == ip:
                    return True, ""
        else:
            for value in dns:
                if _match_dns_name(value, host):
                    return True, ""
        return False, "证书里的名字（%s）跟请求的主机 %s 对不上" % (
            ", ".join(dns + ips) or "（空）", host)
    # 没有 SAN 才退回 commonName（很老的证书才这样）
    if not common:
        return False, "证书里既没有 subjectAltName 也没有 commonName"
    for value in common:
        if ip is not None:
            if _ip_literal(value) == ip:
                return True, ""
        elif _match_dns_name(value, host):
            return True, ""
    return False, "证书里的 commonName（%s）跟请求的主机 %s 对不上" % (
        ", ".join(common), host)


def _base_parts(base: str) -> Tuple[str, str, int, str]:
    """拆 `scheme://host[:port][/path]`（path 前缀保留，兼容反代挂在根下的地址）。"""
    parsed = urlsplit(str(base or ""))
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, int(port), (parsed.path or "").rstrip("/")


def _request(
    store,
    method: str,
    path: str,
    json_body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    token: str = "",
    timeout: float = REQUEST_TIMEOUT,
    base_url: str = "",
) -> Dict[str, Any]:
    """发一个请求，返回 {status, data, offline, insecure, error}（不抛异常）。

    HTTPS：先握手（证书链此刻已由 OpenSSL 按系统根证书校验过，CERT_REQUIRED），
    再从**同一个连接**取对端证书自己比对主机名；名字对不上就直接失败，一个字节
    的请求都不会发出去，也不重试、不降级。明文 HTTP 走同一条路径，只是不握手、
    不做名字校验（测试用的 `http://` 地址照旧可用）。
    `insecure` 恒为 `False`：这个客户端已经没有任何「不校验证书」的模式。
    """
    base = normalize_url(base_url) if base_url else server_url(store)
    scheme, host, port, prefix = _base_parts(base)
    insecure = False                       # 恒为 False，保留字段只为兼容界面
    if scheme not in ("http", "https") or not host:
        return {"status": 0, "data": {}, "offline": True, "insecure": insecure,
                "code": "offline", "error": "服务器地址不对：%s" % base}
    target = prefix + str(path or "")
    query = urlencode(params or {}, doseq=True)
    if query:
        target += ("&" if "?" in target else "?") + query
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = "Bearer " + token
    body: Optional[bytes] = None
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection = None
    status = 0
    raw = b""
    try:
        if scheme == "https":
            connection = http.client.HTTPSConnection(
                host, port, timeout=timeout, context=_tls_context())
            connection.connect()           # 证书链在这一步校验（CERT_REQUIRED）
            matched, reason = _cert_matches_host(connection.sock.getpeercert(), host)
            if not matched:
                return {"status": 0, "data": {}, "offline": True,
                        "insecure": insecure, "code": "tls_error",
                        "error": "%s（%s）" % (_CODE_TEXT["tls_error"], reason)}
        else:
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
        connection.request(method, target, body=body, headers=headers)
        reply = connection.getresponse()
        status = int(getattr(reply, "status", 0) or 0)
        raw = reply.read() or b""
    except ssl.SSLCertVerificationError as exc:
        return {"status": 0, "data": {}, "offline": True, "insecure": insecure,
                "code": "tls_error",
                "error": "%s（证书链校验不过：%s）" % (
                    _CODE_TEXT["tls_error"], str(exc)[:160])}
    except ssl.SSLError as exc:
        return {"status": 0, "data": {}, "offline": True, "insecure": insecure,
                "code": "tls_error",
                "error": "%s（%s）" % (_CODE_TEXT["tls_error"], str(exc)[:160])}
    except (socket.timeout, TimeoutError):
        return {"status": 0, "data": {}, "offline": True, "insecure": insecure,
                "code": "offline", "error": _CODE_TEXT["offline"]}
    except (http.client.HTTPException, OSError) as exc:
        return {"status": 0, "data": {}, "offline": True, "insecure": insecure,
                "code": "offline",
                "error": "%s（%s）" % (_CODE_TEXT["offline"], str(exc)[:160])}
    except Exception as exc:      # 兜底：界面永远不该被异常打到
        return {"status": 0, "data": {}, "offline": True, "insecure": insecure,
                "code": "offline", "error": "请求失败：%s" % str(exc)[:160]}
    finally:
        try:
            if connection is not None:
                connection.close()
        except Exception:
            pass
    return {
        "status": status,
        "data": _decode(raw),
        "offline": False,
        "insecure": insecure,
        "code": "",
        "error": "",
    }


# ================================================================ 结果整形
def _session_extras(data: Dict[str, Any]) -> Dict[str, Any]:
    """把登录 / 注册响应里的会话信息摊平到结果顶层（界面好取）。"""
    data = data or {}
    account_info = data.get("account")
    if not isinstance(account_info, dict):
        account_info = {}
    extras: Dict[str, Any] = {
        "token": str(data.get("token") or ""),
        "refresh_token": str(data.get("refresh_token") or ""),
        "expires_in": int(data.get("expires_in") or 0),
        "account": account_info,
    }
    for key in ("devices_used", "device_limit"):
        if data.get(key) is not None:
            try:
                extras[key] = int(data[key])
            except (TypeError, ValueError):
                pass
    if isinstance(data.get("devices"), list):
        extras["devices"] = data["devices"]
    if isinstance(data.get("items"), list):
        extras["items"] = data["items"]
    return extras


def _from_reply(reply: Dict[str, Any], success: Tuple[int, ...] = (200,)) -> Dict[str, Any]:
    status = int(reply.get("status") or 0)
    data = reply.get("data") or {}
    insecure = bool(reply.get("insecure"))
    if reply.get("offline"):
        code = str(reply.get("code") or "offline")
        return _result(
            code=code,
            error=str(reply.get("error") or _CODE_TEXT.get(code) or _CODE_TEXT["offline"]),
            status=status, offline=True, insecure=insecure, data=data,
        )
    if status in success:
        return _result(ok=True, code="ok", status=status, insecure=insecure,
                       data=data, **_session_extras(data))
    code = str(data.get("error") or "").strip() or ("http_%d" % status if status else "error")
    extras: Dict[str, Any] = {}
    if code == "device_limit" or status == 409:
        for key in ("devices", "devices_used", "device_limit"):
            if data.get(key) is not None:
                extras[key] = data[key]
    return _result(ok=False, code=code, error=_message(status, data), status=status,
                   insecure=insecure, data=data, **extras)


def _secret(store, key: str) -> str:
    target = _resolve(store)
    if target is None:
        return ""
    try:
        return secure.unprotect(target.get_setting(key, "") or "")
    except Exception:
        return ""


def _set_secret(store, key: str, value: str) -> None:
    target = _resolve(store)
    if target is None:
        return
    try:
        target.set_setting(key, secure.protect(value) if value else "")
    except Exception:
        pass


# ================================================================ 会话
def session(store=None) -> Dict[str, Any]:
    """本机保存的服务器登录状态（没有网络请求）。"""
    target = _resolve(store)
    base = server_url(target)
    data: Dict[str, Any] = {
        "logged_in": False, "username": "", "account_id": "", "email": "",
        "token": "", "server_url": base, "expires_at": "",
        "devices_used": 0, "device_limit": 0,
        # 恒为 False：这个客户端不再有「不校验证书」的模式。老数据里残留的
        # `server_tls_insecure=1` 只是历史记录，不再影响任何请求。
        "insecure": False,
    }
    if target is None:
        return data
    token = _secret(target, SECRET_TOKEN)
    data["token"] = token
    data["logged_in"] = bool(token)
    try:
        data["username"] = str(target.get_setting(SETTING_USERNAME, "") or "")
        data["account_id"] = str(target.get_setting(SETTING_ACCOUNT_ID, "") or "")
        data["expires_at"] = str(target.get_setting(SETTING_EXPIRES_AT, "") or "")
        data["devices_used"] = int(float(target.get_setting(SETTING_DEVICES_USED, "0") or 0))
        data["device_limit"] = int(float(target.get_setting(SETTING_DEVICE_LIMIT, "0") or 0))
    except Exception:
        pass
    return data


def save_session(store, result: Dict[str, Any]) -> bool:
    """把登录 / 注册 / 刷新结果里的令牌落盘（加密），返回是否成功。"""
    target = _resolve(store)
    if target is None or not isinstance(result, dict) or not result.get("ok"):
        return False
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    token = str(result.get("token") or data.get("token") or "")
    refresh_token = str(result.get("refresh_token") or data.get("refresh_token") or "")
    account_info = result.get("account") or data.get("account") or {}
    if not isinstance(account_info, dict):
        account_info = {}
    values: Dict[str, Any] = {SETTING_URL: server_url(target)}
    try:
        expires_in = int(result.get("expires_in") or data.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    if expires_in > 0:
        values[SETTING_EXPIRES_AT] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(time.time() + expires_in))
    if account_info.get("username"):
        values[SETTING_USERNAME] = str(account_info["username"])
    if account_info.get("id") is not None:
        values[SETTING_ACCOUNT_ID] = str(account_info["id"])
    for key, setting in (("devices_used", SETTING_DEVICES_USED),
                         ("device_limit", SETTING_DEVICE_LIMIT)):
        if result.get(key) is not None:
            values[setting] = str(result[key])
    if not token:
        return False
    try:
        _set_secret(target, SECRET_TOKEN, token)
        if refresh_token:
            _set_secret(target, SECRET_REFRESH, refresh_token)
        target.update_settings(values)
    except Exception:
        return False
    return True


def clear_session(store=None) -> bool:
    """清掉本机保存的令牌与账户信息（不联网）。"""
    target = _resolve(store)
    if target is None:
        return False
    try:
        target.set_setting(SECRET_TOKEN, "")
        target.set_setting(SECRET_REFRESH, "")
        target.update_settings({
            SETTING_USERNAME: "", SETTING_ACCOUNT_ID: "", SETTING_EXPIRES_AT: "",
            SETTING_DEVICES_USED: "0", SETTING_DEVICE_LIMIT: "0",
        })
    except Exception:
        return False
    return True


# ================================================================ 公开接口
def health(base_url: str = "", store=None,
           timeout: float = HEALTH_TIMEOUT) -> Tuple[bool, Dict[str, Any]]:
    """`GET /health`：返回 (是否在线, 详情)。

    详情里带 `latency_ms` / `version` / `uptime_seconds` / `offline` / `error`，
    设置页的「测试连接」直接拿来显示。
    """
    started = time.time()
    base = normalize_url(base_url) if base_url else server_url(store)
    reply = _request(store, "GET", API_PREFIX + "/health", timeout=timeout,
                     base_url=base)
    latency_ms = int(round((time.time() - started) * 1000))
    info: Dict[str, Any] = {
        "ok": False, "online": False, "offline": bool(reply.get("offline")),
        "status": int(reply.get("status") or 0), "latency_ms": latency_ms,
        "version": "", "uptime_seconds": 0, "url": base,
        "insecure": bool(reply.get("insecure")), "error": "",
    }
    if reply.get("offline"):
        info["error"] = str(reply.get("error") or _CODE_TEXT["offline"])
        return False, info
    data = reply.get("data") or {}
    ok = int(reply.get("status") or 0) == 200 and bool(data.get("ok", True))
    info["ok"] = ok
    info["online"] = ok
    info["version"] = str(data.get("version") or "")
    try:
        info["uptime_seconds"] = int(data.get("uptime_seconds") or 0)
    except (TypeError, ValueError):
        info["uptime_seconds"] = 0
    if not ok:
        info["error"] = _message(int(reply.get("status") or 0), data)
    return ok, info


def register(username: str, email: str, password: str, invite_code: Optional[str] = None,
             store=None) -> Dict[str, Any]:
    """`POST /register`：成功时顺手把会话落盘（= 注册完就是登录状态）。"""
    body = {
        "username": str(username or "").strip(),
        "email": str(email or "").strip(),
        "password": str(password or ""),
        "device_fingerprint": device_fingerprint(store),
        "device_name": device_name(),
        "platform": platform_tag(),
    }
    code = str(invite_code or "").strip()
    if code:
        body["invite_code"] = code
    reply = _request(store, "POST", API_PREFIX + "/register", json_body=body)
    result = _from_reply(reply, success=(200, 201))
    if result.get("ok"):
        result["session_saved"] = save_session(store, result)
    return result


def login(account: str, password: str, store=None) -> Dict[str, Any]:
    """`POST /login`：用户名或邮箱 + 口令。设备超限时 `code` 是 `device_limit`。"""
    body = {
        "username_or_email": str(account or "").strip(),
        "password": str(password or ""),
        "device_fingerprint": device_fingerprint(store),
        "device_name": device_name(),
        "platform": platform_tag(),
    }
    reply = _request(store, "POST", API_PREFIX + "/login", json_body=body)
    result = _from_reply(reply, success=(200,))
    if result.get("ok"):
        result["session_saved"] = save_session(store, result)
    return result


def refresh(store=None) -> Dict[str, Any]:
    """`POST /refresh`：用 refresh_token 换新的 access token。"""
    target = _resolve(store)
    token = _secret(target, SECRET_REFRESH)
    if not token:
        return _result(code="no_session", error=_CODE_TEXT["no_session"])
    reply = _request(target, "POST", API_PREFIX + "/refresh",
                     json_body={"refresh_token": token})
    result = _from_reply(reply, success=(200,))
    if result.get("ok"):
        data = result.get("data") or {}
        # 刷新响应可能只给 token、也可能带 refresh_token，都按同一套落盘
        if not result.get("token"):
            result["token"] = str(data.get("token") or "")
        if not result.get("refresh_token"):
            result["refresh_token"] = token
        result["session_saved"] = save_session(target, result)
    return result


def me(store=None) -> Dict[str, Any]:
    """`GET /me`（Bearer）。"""
    return _authed(store, "GET", "/me")


def devices(store=None) -> Dict[str, Any]:
    """`GET /devices`（Bearer）。结果的 `devices` 是摊平后的列表。"""
    result = _authed(store, "GET", "/devices")
    if result.get("ok") and not isinstance(result.get("devices"), list):
        payload = result.get("data") or {}
        result["devices"] = payload.get("devices") if isinstance(
            payload.get("devices"), list) else []
    return result


def kick_device(device_id, store=None) -> Dict[str, Any]:
    """`POST /devices/kick`（Bearer）：把某台设备踢下线。"""
    wanted = str(device_id or "").strip()
    if not wanted:
        return _result(code="validation_error", error="没有指定要踢掉的设备")
    body: Dict[str, Any] = {"device_id": wanted}
    try:
        if str(device_id).isdigit():
            body["device_id"] = int(device_id)
    except Exception:
        body["device_id"] = wanted
    result = _authed(store, "POST", "/devices/kick", json_body=body)
    if result.get("ok"):
        result["device_id"] = body["device_id"]
    return result


def logout(store=None) -> Dict[str, Any]:
    """退出服务器账号：只清本机令牌（不删服务器上的账号）。"""
    target = _resolve(store)
    if target is None:
        return _result(ok=True, code="ok")
    if not clear_session(target):
        return _result(code="error", error="清不掉本机保存的服务器令牌")
    return _result(ok=True, code="ok")


def announcements(since: int = 0, store=None) -> Dict[str, Any]:
    """`GET /announcements?since=<id>`（公开接口）。服务器没这个接口时 `code=not_found`。"""
    try:
        cursor = int(since or 0)
    except (TypeError, ValueError):
        cursor = 0
    reply = _request(store, "GET", API_PREFIX + "/announcements",
                     params={"since": max(0, cursor)})
    result = _from_reply(reply, success=(200,))
    if result.get("ok") and not isinstance(result.get("items"), list):
        payload = result.get("data") or {}
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        result["items"] = items
    return result


def stream_url(store=None) -> str:
    """`GET /stream` 的 WebSocket 地址（带 access token）。"""
    base = server_url(store)
    token = _secret(store, SECRET_TOKEN)
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return "%s%s/stream?token=%s" % (base, API_PREFIX, quote(token, safe=""))


# ================================================================ 带鉴权调用
def _authed(store, method: str, path: str, json_body: Optional[Dict[str, Any]] = None,
            retry: bool = True) -> Dict[str, Any]:
    """带 Bearer 的调用；401 时自动 refresh 一次再重试。"""
    target = _resolve(store)
    token = _secret(target, SECRET_TOKEN)
    if not token:
        return _result(code="no_session", error=_CODE_TEXT["no_session"])
    reply = _request(target, method, API_PREFIX + path, json_body=json_body,
                     token=token)
    if int(reply.get("status") or 0) == 401 and retry:
        refreshed = refresh(target)
        if refreshed.get("ok"):
            new_token = _secret(target, SECRET_TOKEN)
            if new_token:
                reply = _request(target, method, API_PREFIX + path,
                                 json_body=json_body, token=new_token)
    result = _from_reply(reply, success=(200, 201, 204))
    if not result.get("ok") and int(result.get("status") or 0) == 401:
        _set_secret(target, SECRET_TOKEN, "")
        result["error"] = _CODE_TEXT["unauthorized"]
        result["code"] = "unauthorized"
    return result


# ================================================================ 本机账户镜像
def ensure_local_user(store, username: str, email: str = "") -> Optional[int]:
    """服务器账号登录成功后，在本机 `users` 表里补一条对应账户，返回它的 id。

    为什么需要：主界面的「当前账户」、记住设备、管理员判定都读本机 `users`。
    这条记录的口令是随机串的哈希（**本机口令登录不了**，只能走服务器登录），
    `receipt` 标成 `server-account`，因此不会被当成本机管理员。
    """
    from . import users

    name = str(username or "").strip()
    if not name:
        return None
    try:
        existing = users.find_user(store, name)
    except Exception:
        existing = None
    if existing:
        user_id = int(existing["id"])
        mail = str(email or "").strip()
        if mail and not str(existing.get("email") or "").strip():
            try:
                store.conn.execute("UPDATE users SET email = ? WHERE id = ?",
                                   (mail, user_id))
                store.conn.commit()
            except Exception:
                pass
        return user_id
    for attempt in (0, 1):
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            pw_hash = users.hash_password(secrets.token_hex(18))
            cursor = store.conn.execute(
                "INSERT INTO users(username, pw_hash, created_at, updated_at, role,"
                " email, status, expires_at, receipt) VALUES (?,?,?,?,?,?,?,?,?)",
                (name, pw_hash, stamp, stamp, "user", str(email or "").strip(),
                 "active", "", RECEIPT_SERVER),
            )
            store.conn.commit()
            return int(cursor.lastrowid)
        except Exception:
            if attempt == 0:
                # 表可能还没建（从没建过本机账户的机器）：让它先建表再试一次
                try:
                    users.list_users(store)
                except Exception:
                    pass
                continue
            return None
    return None


# ================================================================ 后台线程调用
class AsyncCall(QObject):
    """在后台线程里跑一个阻塞调用，结果用信号回主线程（界面不卡）。"""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

    def is_busy(self) -> bool:
        return self._busy

    def run(self, func: Callable[..., Any], *args, **kwargs) -> bool:
        """跑 `func(*args, **kwargs)`；已经有任务在跑就返回 False。"""
        if self._busy:
            return False
        self._busy = True

        def worker() -> None:
            try:
                result = func(*args, **kwargs)
            except Exception as exc:      # 兜底：线程里炸了也要回到界面
                self._busy = False
                self.failed.emit(str(exc))
                return
            self._busy = False
            self.finished.emit(result)

        threading.Thread(target=worker, name="lumina-account", daemon=True).start()
        return True
