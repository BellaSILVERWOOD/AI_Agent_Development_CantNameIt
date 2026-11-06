"""VPN / 代理：让内嵌网页和后台请求都走代理（复旦 SSL VPN 或任意 HTTP/SOCKS5）。

用法分两层：
1. **Qt 层**（内嵌网页、QtWebEngine 的请求）：用 `QNetworkProxy.setApplicationProxy` 全局生效；
2. **requests 层**（eLearning 抓取、AI 对话）：由 `requests_proxies()` 提供 proxies 字典。

设置项：
    proxy_enabled ("0"/"1")、proxy_kind ("http"/"socks5")、proxy_host、proxy_port、
    proxy_user、proxy_password（加密存在 secrets 里）、vpn_url（复旦 VPN 地址）
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .store import Store

DEFAULT_VPN_URL = "https://vpn.fudan.edu.cn"
PROXY_KINDS = [("http", "HTTP 代理"), ("socks5", "SOCKS5 代理")]


# ------------------------------------------------------------------ 设置读写
def settings(store) -> Dict[str, Any]:
    def get(key: str, default: str = "") -> str:
        return (store.get_setting(key, default) or default).strip()

    try:
        port = int(float(get("proxy_port", "8080") or 8080))
    except Exception:
        port = 8080
    return {
        "enabled": get("proxy_enabled", "0") == "1",
        "kind": get("proxy_kind", "http") or "http",
        "host": get("proxy_host", ""),
        "port": port,
        "user": get("proxy_user", ""),
        "password": store.get_secret("proxy_password", "") or "",
        "vpn_url": get("vpn_url", DEFAULT_VPN_URL) or DEFAULT_VPN_URL,
    }


def save(store: Store, values: Dict[str, Any]) -> None:
    payload = {
        "proxy_enabled": "1" if values.get("enabled") else "0",
        "proxy_kind": values.get("kind") or "http",
        "proxy_host": (values.get("host") or "").strip(),
        "proxy_port": str(int(values.get("port") or 8080)),
        "proxy_user": (values.get("user") or "").strip(),
        "vpn_url": (values.get("vpn_url") or DEFAULT_VPN_URL).strip(),
    }
    store.update_settings(payload)
    password = (values.get("password") or "").strip()
    if password:
        store.set_secret("proxy_password", password)


# ------------------------------------------------------------------ 地址字符串
def proxy_url(cfg: Dict[str, Any]) -> Optional[str]:
    if not cfg.get("enabled") or not cfg.get("host"):
        return None
    scheme = "socks5" if cfg.get("kind") == "socks5" else "http"
    auth = ""
    if cfg.get("user"):
        auth = cfg["user"]
        if cfg.get("password"):
            auth += ":" + cfg["password"]
        auth += "@"
    return f"{scheme}://{auth}{cfg['host']}:{int(cfg.get('port') or 8080)}"


def requests_proxies(store: Store) -> Optional[Dict[str, str]]:
    url = proxy_url(settings(store))
    if not url:
        return None
    return {"http": url, "https": url}


# ------------------------------------------------------------------ Qt 层
def apply_qt_proxy(store: Store) -> bool:
    """把代理装到 Qt 全局（内嵌网页会跟着走）。返回是否启用了代理。"""
    try:
        from PySide6.QtNetwork import QNetworkProxy
    except Exception:
        return False
    cfg = settings(store)
    url = proxy_url(cfg)
    if not url:
        QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        return False
    try:
        kind = (QNetworkProxy.ProxyType.Socks5Proxy if cfg.get("kind") == "socks5"
                else QNetworkProxy.ProxyType.HttpProxy)
        proxy = QNetworkProxy(kind, cfg.get("host", ""), int(cfg.get("port") or 8080))
        if cfg.get("user"):
            proxy.setUser(cfg["user"])
            proxy.setPassword(cfg.get("password", ""))
        QNetworkProxy.setApplicationProxy(proxy)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 连通性测试
TEST_TARGETS = [
    ("eLearning", "https://elearning.fudan.edu.cn/login"),
    ("教务处", "https://jwc.fudan.edu.cn/"),
]


def probe(store: Store, timeout: float = 6.0) -> Dict[str, Any]:
    """测一下走当前设置能不能连到校内服务。"""
    import requests

    cfg = settings(store)
    proxies = requests_proxies(store)
    results = []
    for name, url in TEST_TARGETS:
        started = time.time()
        try:
            response = requests.get(url, timeout=timeout, proxies=proxies,
                                    allow_redirects=True)
            cost = (time.time() - started) * 1000
            results.append({"name": name, "ok": response.status_code < 500,
                            "status": response.status_code, "ms": int(cost)})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": name, "ok": False, "status": 0,
                            "error": str(exc)[:120]})
    ok = all(item.get("ok") for item in results) if results else False
    mode = "代理" if proxies else "直连"
    return {"ok": ok, "mode": mode, "results": results,
            "proxy": proxy_url(cfg) or "", "vpn_url": cfg["vpn_url"]}


def summary(store: Store) -> str:
    cfg = settings(store)
    url = proxy_url(cfg)
    if not url:
        return "当前是直连（没有启用代理）"
    kind = "SOCKS5" if cfg.get("kind") == "socks5" else "HTTP"
    return f"当前走 {kind} 代理：{cfg['host']}:{cfg['port']}"
