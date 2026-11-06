"""给系统提示词补上「当前时间 + IP」这类每次都变的信息。

* 本机 IP 用 UDP socket 探一下（不发包，不联网也能拿到局域网地址）；
* 公网 IP 默认**不查**（要联网、还慢），需要才在设置里打开，并缓存一小时；
* 一律不抛异常：拿不到就写「未知」，绝不能让聊天因为 IP 失败而报错。
"""
from __future__ import annotations

import socket
from datetime import datetime
from typing import Dict, Optional

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
PUBLIC_IP_KEY = "public_ip"
PUBLIC_IP_AT_KEY = "public_ip_at"
PUBLIC_IP_TTL = 3600


def local_ip() -> str:
    """局域网 IP（连一个不存在的地址只为让系统选出口网卡，不真的发包）。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(0.2)
            sock.connect(("10.255.255.255", 1))
            return str(sock.getsockname()[0])
        finally:
            sock.close()
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "未知"


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "未知"


def public_ip(store=None, enable: bool = False) -> str:
    """公网 IP：设置里开了才查，结果缓存 1 小时。"""
    if store is None or not enable:
        return ""
    cached = store.get_setting(PUBLIC_IP_KEY, "") or ""
    try:
        stamp = float(store.get_setting(PUBLIC_IP_AT_KEY, "0") or 0)
    except (TypeError, ValueError):
        stamp = 0.0
    now = datetime.now().timestamp()
    if cached and now - stamp < PUBLIC_IP_TTL:
        return cached
    try:
        import requests

        data = requests.get("https://api.ipify.org?format=json", timeout=3.5).json()
        value = str(data.get("ip") or "").strip()
        if value:
            store.update_settings({PUBLIC_IP_KEY: value,
                                   PUBLIC_IP_AT_KEY: str(now)})
        return value
    except Exception:
        return cached


def now_text(moment: Optional[datetime] = None) -> str:
    moment = moment or datetime.now()
    return "%s %s" % (moment.strftime("%Y-%m-%d %H:%M"), WEEKDAYS[moment.weekday()])


def date_text(moment: Optional[datetime] = None) -> str:
    """只要日期（喂给模型的系统提示词里不要几点几分）。"""
    moment = moment or datetime.now()
    return "%s %s" % (moment.strftime("%Y-%m-%d"), WEEKDAYS[moment.weekday()])


def context_block(store=None, with_public_ip: bool = False) -> str:
    """拼成「最前面那一小段」。

    注意：这里**只写日期**，不写具体时刻——提示词里塞了「现在几点几分」，
    模型就会对着一个可能已经过期的时间回答（而且每次请求都变，缓存也用不上）。
    需要精确时刻的地方（提示词生成程序的 stdin JSON）另走 `context_dict()`。
    """
    lines = ["今天的日期：%s" % date_text(),
             "本机 IP：%s（主机名 %s）" % (local_ip(), hostname())]
    if with_public_ip:
        public = public_ip(store, enable=True)
        if public:
            lines.append("公网 IP：%s" % public)
    return "\n".join(lines)


def context_dict(store=None, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """给「提示词生成程序」用的上下文（JSON 里就是这些字段）。"""
    moment = datetime.now()
    data = {
        "now": moment.strftime("%Y-%m-%d %H:%M:%S"),
        "date": moment.strftime("%Y-%m-%d"),
        "time": moment.strftime("%H:%M"),
        "weekday": WEEKDAYS[moment.weekday()],
        "local_ip": local_ip(),
        "hostname": hostname(),
        "year": str(moment.year),
        "month": "%02d" % moment.month,
        "day": "%02d" % moment.day,
    }
    if extra:
        data.update({str(k): str(v) for k, v in extra.items()})
    return data
