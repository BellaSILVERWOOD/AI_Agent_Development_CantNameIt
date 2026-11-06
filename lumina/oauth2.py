"""Outlook / 微软账户的 OAuth2（XOAUTH2）登录。

背景：微软从 2024 年起关掉了 outlook.com / hotmail.com 的「账号密码 + 应用密码」
发信（服务器回 535 5.7.139 `basic authentication is disabled`），只剩 OAuth2。

这里用**设备代码流程**（device code），不需要域名、不需要回调端口，最适合桌面程序：

1. `start_device_flow()` 拿到 `user_code` 和网址，让用户去网页上输入一次；
2. 程序轮询 `poll_device_flow()`，用户点完「允许」就拿到 `refresh_token`；
3. 以后发信前用 `access_token()` 换一个短期 token（缓存、到期自动续）；
4. SMTP 用 `AUTH XOAUTH2` 登录（`sasl_string()`），不再需要密码。

前提：在 Azure 门户注册一个应用（支持「个人 Microsoft 账户」），拿到 **客户端 ID**；
Scope 用 `https://outlook.office.com/SMTP.Send offline_access`。
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

BASE_KEY = "oauth_base"
TENANT_KEY = "oauth_tenant"
CLIENT_KEY = "oauth_client_id"
SCOPE_KEY = "oauth_scope"
REFRESH_SECRET = "oauth_refresh_token"
ACCESS_KEY = "oauth_access_token"
ACCESS_AT_KEY = "oauth_access_at"
EXPIRES_KEY = "oauth_access_expires"

DEFAULT_BASE = "https://login.microsoftonline.com"
DEFAULT_TENANT = "consumers"
DEFAULT_SCOPE = "https://outlook.office.com/SMTP.Send offline_access"
REFRESH_MARGIN = 120          # 提前两分钟续期


class OAuthError(Exception):
    pass


# ------------------------------------------------------------------ 设置
def _base(store) -> str:
    return (store.get_setting(BASE_KEY, DEFAULT_BASE) or DEFAULT_BASE).rstrip("/")


def tenant(store) -> str:
    return (store.get_setting(TENANT_KEY, DEFAULT_TENANT) or DEFAULT_TENANT).strip()


def client_id(store) -> str:
    return (store.get_setting(CLIENT_KEY, "") or "").strip()


def scope(store) -> str:
    return (store.get_setting(SCOPE_KEY, DEFAULT_SCOPE) or DEFAULT_SCOPE).strip()


def set_client_id(store, value: str) -> None:
    store.set_setting(CLIENT_KEY, str(value or "").strip())


def set_tenant(store, value: str) -> None:
    store.set_setting(TENANT_KEY, str(value or DEFAULT_TENANT).strip())


def configured(store) -> bool:
    return bool(client_id(store))


def has_refresh_token(store) -> bool:
    try:
        return bool(store.get_secret(REFRESH_SECRET, ""))
    except Exception:
        return False


def endpoint(store, name: str) -> str:
    return f"{_base(store)}/{tenant(store)}/oauth2/v2.0/{name}"


# ------------------------------------------------------------------ HTTP
def _post(store, name: str, data: Dict[str, str], timeout: float = 20.0) -> Dict[str, Any]:
    """POST 到微软的 OAuth 端点（测试时把 `oauth_base` 指到假服务器也能用）。"""
    import requests

    url = endpoint(store, name)
    try:
        response = requests.post(url, data=data, timeout=timeout,
                                 headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        raise OAuthError(f"连不上微软登录服务：{exc}") from exc
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code >= 400 and "error" not in payload:
        raise OAuthError(f"登录服务返回 HTTP {response.status_code}："
                         f"{response.text[:160]}")
    return payload


# ------------------------------------------------------------------ 设备代码流程
def start_device_flow(store) -> Dict[str, Any]:
    """返回 user_code / verification_uri / device_code / interval / expires_in。"""
    cid = client_id(store)
    if not cid:
        raise OAuthError("还没有填「客户端 ID」：先到 Azure 门户注册一个应用再填进来")
    payload = _post(store, "devicecode", {"client_id": cid, "scope": scope(store)})
    if payload.get("error"):
        raise OAuthError(_describe(payload))
    if not payload.get("user_code") or not payload.get("device_code"):
        raise OAuthError("登录服务没有返回设备码（检查客户端 ID / 租户）")
    return {
        "user_code": str(payload.get("user_code")),
        "verification_uri": str(payload.get("verification_uri")
                                or payload.get("verification_url") or ""),
        "device_code": str(payload.get("device_code")),
        "interval": int(payload.get("interval") or 5),
        "expires_in": int(payload.get("expires_in") or 900),
        "message": str(payload.get("message") or ""),
    }


def _store_tokens(store, payload: Dict[str, Any]) -> None:
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    expires = int(payload.get("expires_in") or 3600)
    if access:
        store.set_setting(ACCESS_KEY, access)
        store.set_setting(ACCESS_AT_KEY, str(int(time.time())))
        store.set_setting(EXPIRES_KEY, str(expires))
    if refresh:
        store.set_secret(REFRESH_SECRET, refresh)


def poll_device_flow(store, device_code: str, interval: int = 5,
                     expires_in: int = 900, should_stop=None,
                     on_tick=None) -> Dict[str, Any]:
    """轮询直到用户完成授权（或超时 / 被取消）。"""
    cid = client_id(store)
    deadline = time.time() + max(10, int(expires_in))
    wait = max(2, int(interval))
    while time.time() < deadline:
        if should_stop is not None and should_stop():
            raise OAuthError("已取消授权")
        payload = _post(store, "token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": cid,
            "device_code": device_code,
        })
        error = str(payload.get("error") or "")
        if not error:
            _store_tokens(store, payload)
            return payload
        if error == "authorization_pending":
            if on_tick is not None:
                try:
                    on_tick("等待你在网页上点「允许」…")
                except Exception:
                    pass
        elif error == "slow_down":
            wait += 5
        else:
            raise OAuthError(_describe(payload))
        time.sleep(wait)
    raise OAuthError("授权超时了，请重新点一次「开始授权」")


def access_token(store, force: bool = False) -> str:
    """拿一个可用的 access token（缓存里没过期就直接用，否则用 refresh token 续）。"""
    if not force:
        cached = store.get_setting(ACCESS_KEY, "") or ""
        try:
            issued = float(store.get_setting(ACCESS_AT_KEY, "0") or 0)
            lifetime = float(store.get_setting(EXPIRES_KEY, "3600") or 3600)
        except (TypeError, ValueError):
            issued, lifetime = 0.0, 3600.0
        if cached and time.time() < issued + lifetime - REFRESH_MARGIN:
            return cached
    refresh = ""
    try:
        refresh = store.get_secret(REFRESH_SECRET, "")
    except Exception:
        refresh = ""
    if not refresh:
        raise OAuthError("还没有授权的 refresh token：先点「开始微软授权」")
    payload = _post(store, "token", {
        "grant_type": "refresh_token",
        "client_id": client_id(store),
        "refresh_token": refresh,
        "scope": scope(store),
    })
    if payload.get("error"):
        raise OAuthError(_describe(payload))
    if not payload.get("access_token"):
        raise OAuthError("续期失败：登录服务没有返回 access_token")
    _store_tokens(store, payload)
    return str(payload["access_token"])


def clear(store) -> None:
    for key in (ACCESS_KEY, ACCESS_AT_KEY, EXPIRES_KEY):
        try:
            store.set_setting(key, "")
        except Exception:
            pass
    try:
        store.set_secret(REFRESH_SECRET, "")
    except Exception:
        pass


def _describe(payload: Dict[str, Any]) -> str:
    error = str(payload.get("error") or "未知错误")
    detail = str(payload.get("error_description") or "")[:200]
    hints = {
        "invalid_client": "客户端 ID 不对，或者应用类型不支持个人账户",
        "unauthorized_client": "这个应用没有开通设备代码流程（在 Azure 里启用「允许公共客户端流」）",
        "invalid_grant": "授权已失效，需要重新授权",
        "expired_token": "设备码过期了，请重新开始授权",
        "authorization_declined": "你在网页上点了拒绝",
    }
    hint = hints.get(error, "")
    return f"OAuth 失败：{error}" + (f"（{hint}）" if hint else "") + \
           (f"：{detail}" if detail else "")


# ------------------------------------------------------------------ SASL
def sasl_string(user: str, token: str) -> str:
    """XOAUTH2 的 base64 串（SMTP / IMAP 都用这一个）。"""
    raw = f"user={user}\x01auth=Bearer {token}\x01\x01".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")
