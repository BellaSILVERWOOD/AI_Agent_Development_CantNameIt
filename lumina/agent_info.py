"""给 agent 的「只读信息」：系统概况、应用状态、备忘搜索、用量统计、工具自查。

都是低风险只读工具（用户逐个批准过 A1–A6）：

* `time_now`   —— 精确时间（提示词里只给日期，算「还有几天」时用）
* `sys_info`   —— CPU / 内存 / 磁盘 / 电池 / 系统版本 / 本机 IP
* `app_state`  —— 今天日程数、未读通知、邮件数、备忘数
* `memo_search`—— 在本地备忘里搜关键词
* `usage_stats`—— 本机 API 用量（次数 / token / 时长）
* `tool_help`  —— 现在能用哪些工具、怎么用

全部只读：不改任何数据，也不碰文件。取不到的项一律返回「未知」，不抛异常。
"""
from __future__ import annotations

import platform
import re
import shutil
import sys
from datetime import datetime
from typing import Any, Dict, List

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


# ------------------------------------------------------------------ A1 时间
def time_now() -> Dict[str, Any]:
    moment = datetime.now()
    return {
        "ok": True,
        "date": moment.strftime("%Y-%m-%d"),
        "time": moment.strftime("%H:%M:%S"),
        "weekday": WEEKDAYS[moment.weekday()],
        "timezone": datetime.now().astimezone().tzname() or "本地时区",
        "epoch": int(moment.timestamp()),
        "summary": (f"现在是 {moment.strftime('%Y-%m-%d %H:%M')} "
                    f"{WEEKDAYS[moment.weekday()]}"),
    }


# ------------------------------------------------------------------ A2 系统概况
def _windows_memory() -> Dict[str, Any]:
    """用 Win32 API 读内存（不依赖 psutil）。"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {}
        return {
            "total_gb": round(status.ullTotalPhys / (1024 ** 3), 1),
            "available_gb": round(status.ullAvailPhys / (1024 ** 3), 1),
            "used_percent": int(status.dwMemoryLoad),
        }
    except Exception:
        return {}


def _windows_battery() -> Dict[str, Any]:
    try:
        import ctypes

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("Reserved1", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return {}
        percent = int(status.BatteryLifePercent)
        return {
            "percent": percent if 0 <= percent <= 100 else -1,
            "plugged": status.ACLineStatus == 1,
            "charging": bool(status.BatteryFlag & 8),
        }
    except Exception:
        return {}


def sys_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "ok": True,
        "system": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cpu_count": 0,
    }
    try:
        import os

        info["cpu_count"] = os.cpu_count() or 0
    except Exception:
        pass
    try:
        usage = shutil.disk_usage(str(__import__("pathlib").Path.home().anchor))
        info["disk"] = {
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
        }
    except Exception:
        info["disk"] = {}
    memory = _windows_memory()
    if memory:
        info["memory"] = memory
    battery = _windows_battery()
    if battery and battery.get("percent", -1) >= 0:
        info["battery"] = battery
    try:
        from . import context_info

        info["local_ip"] = context_info.local_ip()
        info["hostname"] = context_info.hostname()
    except Exception:
        info["local_ip"] = "未知"
        info["hostname"] = "未知"
    bits = [info["system"]]
    if info.get("cpu_count"):
        bits.append(f"{info['cpu_count']} 核")
    if memory:
        bits.append(f"内存 {memory.get('used_percent')}%"
                    f"（剩 {memory.get('available_gb')}GB）")
    if info.get("disk"):
        bits.append(f"磁盘剩 {info['disk'].get('free_gb')}GB")
    if info.get("battery"):
        battery_text = f"电量 {info['battery'].get('percent')}%"
        battery_text += "（已插电）" if info["battery"].get("plugged") else "（用电池）"
        bits.append(battery_text)
    info["summary"] = "；".join(bits) + f"；IP {info.get('local_ip')}"
    return info


# ------------------------------------------------------------------ A3 应用状态
def app_state(store) -> Dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    data: Dict[str, Any] = {"ok": True, "date": today}
    try:
        events = store.events_on(today)
        data["events_today"] = len(events)
        data["events_done"] = sum(1 for item in events if item.get("done"))
        left = [item for item in events if not item.get("done")]
        data["next_event"] = (f"{left[0].get('start_time','')[:5]} "
                             f"{left[0].get('title','')}") if left else ""
    except Exception:
        data["events_today"] = -1
    try:
        data["unread_notices"] = int(store.unread_notice_count())
    except Exception:
        data["unread_notices"] = -1
    try:
        counts = store.mail_counts()
        data["mails"] = {"total": int(counts.get("total") or 0),
                         "draft": int(counts.get("draft") or 0),
                         "failed": int(counts.get("failed") or 0),
                         "bounced": int(counts.get("bounced") or 0)}
    except Exception:
        data["mails"] = {}
    try:
        data["memos"] = len(store.list_memos())
    except Exception:
        data["memos"] = -1
    try:
        from . import users

        current = users.current_user(store)
        data["user"] = str((current or {}).get("username") or "")
        data["role"] = str((current or {}).get("role") or "")
    except Exception:
        pass
    parts = [f"今天 {data.get('events_today')} 条日程"
             f"（已完成 {data.get('events_done', 0)} 条）"]
    if data.get("unread_notices", -1) >= 0:
        parts.append(f"未读通知 {data['unread_notices']} 条")
    if data.get("mails"):
        parts.append(f"邮件 {data['mails'].get('total')} 封")
    if data.get("memos", -1) >= 0:
        parts.append(f"备忘 {data['memos']} 条")
    data["summary"] = "；".join(parts)
    return data


# ------------------------------------------------------------------ A4 备忘搜索
def memo_search(store, keyword: str = "", limit: int = 10) -> Dict[str, Any]:
    text = str(keyword or "").strip()
    if not text:
        return {"ok": False, "summary": "要给个关键词"}
    try:
        memos = store.list_memos()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"读不到备忘：{exc}"}
    hits: List[Dict[str, Any]] = []
    needle = text.lower()
    for item in memos:
        title = str(item.get("title") or "")
        body = re.sub(r"<[^>]+>", " ", str(item.get("html") or ""))
        body = re.sub(r"\s+", " ", body.replace("&nbsp;", " "))
        where = ""
        if needle in title.lower():
            where = "标题"
        elif needle in body.lower():
            where = "正文"
        if not where:
            continue
        index = body.lower().find(needle)
        snippet = body[max(0, index - 40): index + len(text) + 60].strip() if index >= 0 else body[:100]
        hits.append({"title": title or "（无标题）", "match": where,
                     "snippet": snippet[:200],
                     "updated_at": item.get("updated_at") or ""})
        if len(hits) >= max(1, min(50, int(limit or 10))):
            break
    return {"ok": True, "keyword": text, "matches": hits, "count": len(hits),
            "summary": (f"在备忘里找到 {len(hits)} 条包含「{text}」的内容"
                        if hits else f"备忘里没有「{text}」")}


# ------------------------------------------------------------------ B4 窗口列表
def window_list(limit: int = 15) -> Dict[str, Any]:
    """列出当前可见的窗口标题（隐私敏感，默认关，用户批准后才用）。"""
    windows: List[Dict[str, Any]] = []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        class _Info:
            def __init__(self) -> None:
                self.titles: List[Dict[str, Any]] = []

        info = _Info()
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                        wintypes.LPARAM)

        def callback(hwnd, _lparam):  # noqa: ANN001
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = int(user32.GetWindowTextLengthW(hwnd))
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = str(buffer.value or "").strip()
                if not title:
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                info.titles.append({"title": title[:120], "pid": int(pid.value)})
            except Exception:
                return True
            return True

        user32.EnumWindows(WNDENUMPROC(callback), 0)
        windows = info.titles[:max(1, min(50, int(limit or 15)))]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"读不到窗口列表：{exc}"}
    return {"ok": True, "windows": windows, "count": len(windows),
            "summary": (f"当前有 {len(windows)} 个可见窗口："
                        + "、".join(item["title"] for item in windows[:5]))
            if windows else "当前没有可见窗口"}


# ------------------------------------------------------------------ A5 用量统计
def usage_stats(store) -> Dict[str, Any]:
    from . import usage_report

    data: Dict[str, Any] = {"ok": True}
    try:
        info = usage_report.snapshot(store)
        for key in ("used_seconds", "requests", "api_calls", "ttft", "tok_s", "day"):
            data[key] = info.get(key)
    except Exception:
        pass
    try:
        data["interval_minutes"] = usage_report.interval_minutes(store)
        data["daily_limit_hours"] = usage_report.daily_limit_hours(store)
        data["last_report_at"] = store.get_setting("report_last_at", "") or ""
    except Exception:
        pass
    seconds = float(data.get("used_seconds") or 0)
    data["summary"] = (
        f"今天用了 {seconds / 3600:.1f} 小时；API 调用 {data.get('api_calls', 0)} 次，"
        f"普通请求 {data.get('requests', 0)} 次")
    return data


# ------------------------------------------------------------------ A6 工具自查
def tool_help(store=None) -> Dict[str, Any]:
    from . import ai_tools

    items = []
    for spec in ai_tools.catalog(store):
        items.append({
            "name": spec["name"],
            "label": spec["label"],
            "desc": spec["desc"],
            "risk": spec["risk"],
            "enabled": spec["enabled"],
        })
    enabled = [item for item in items if item["enabled"]]
    return {
        "ok": True,
        "tools": items,
        "enabled_count": len(enabled),
        "summary": "现在可用：" + "、".join(item["label"] for item in enabled[:12]),
    }
