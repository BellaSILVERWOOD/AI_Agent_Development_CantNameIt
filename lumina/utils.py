"""日期、文本与文件小工具。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, Tuple

from PySide6.QtCore import QDate

from .config import WEEKDAY_CN

WEEKDAYS_SHORT = ["一", "二", "三", "四", "五", "六", "日"]

EXT_STYLES: Dict[str, Tuple[str, str]] = {
    "pdf": ("PDF", "#F0555F"),
    "doc": ("DOC", "#4E86F7"),
    "docx": ("DOC", "#4E86F7"),
    "xls": ("XLS", "#3BB273"),
    "xlsx": ("XLS", "#3BB273"),
    "csv": ("CSV", "#3BB273"),
    "ppt": ("PPT", "#F08A3C"),
    "pptx": ("PPT", "#F08A3C"),
    "png": ("IMG", "#A06CF7"),
    "jpg": ("IMG", "#A06CF7"),
    "jpeg": ("IMG", "#A06CF7"),
    "gif": ("IMG", "#A06CF7"),
    "webp": ("IMG", "#A06CF7"),
    "bmp": ("IMG", "#A06CF7"),
    "svg": ("IMG", "#A06CF7"),
    "zip": ("ZIP", "#C9A227"),
    "rar": ("RAR", "#C9A227"),
    "7z": ("7Z", "#C9A227"),
    "txt": ("TXT", "#8A94AD"),
    "md": ("MD", "#8A94AD"),
}


def iso(value: QDate) -> str:
    return value.toString("yyyy-MM-dd")


def to_qdate(value: str) -> QDate:
    parsed = QDate.fromString(value, "yyyy-MM-dd")
    return parsed if parsed.isValid() else QDate.currentDate()


def month_label(value: QDate) -> str:
    return f"{value.year()} 年 {value.month()} 月"


def date_cn(value: QDate, weekday: bool = True) -> str:
    base = f"{value.year()}年{value.month()}月{value.day()}日"
    if weekday:
        base += " " + WEEKDAY_CN[value.dayOfWeek() - 1]
    return base


def date_short_cn(value: QDate) -> str:
    return f"{value.month()} 月 {value.day()} 日"


def relative_label(value: QDate) -> str:
    delta = QDate.currentDate().daysTo(value)
    return {0: "今天", 1: "明天", 2: "后天", -1: "昨天"}.get(delta, "")


def weekday_cn(value: QDate) -> str:
    return WEEKDAY_CN[value.dayOfWeek() - 1]


def event_range(event: Dict) -> str:
    start = event.get("start_time", "")
    end = event.get("end_time", "")
    if end and end != start:
        return f"{start} – {end}"
    return str(start)


def task_moment(event: Dict) -> str:
    qdate = to_qdate(str(event.get("date", "")))
    return f"{qdate.month()}月{qdate.day()}日 {event.get('start_time', '')}".strip()


def file_ext(name: str) -> str:
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return suffix[:4]


def ext_badge(name: str) -> Tuple[str, str]:
    suffix = file_ext(name)
    return EXT_STYLES.get(suffix, (suffix.upper() or "FILE", "#5D6A8C"))


def plural_events(count: int) -> str:
    return f"{count} 个日程"


def human_size(num: int | float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{value:.0f} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_tokens_m(num: int | float) -> str:
    """token 数一律按 M（百万）显示：1234567 → 1.23M，12345 → 0.012M。

    用户要求「token 数用 M 计算」，所以这里不再混用 k / 个。
    0 显示 0M；很小但非零的值保留更多小数，免得四舍五入成 0M。
    """
    try:
        value = float(num or 0)
    except (TypeError, ValueError):
        return "—"
    if value <= 0:
        return "0M"
    millions = value / 1_000_000.0
    # 四舍五入到 3 位之后已经够 1M 的（比如 999_999），也按 1.00M 显示，
    # 免得出现 “1.000M” 这种看着别扭的数
    if round(millions, 3) >= 1:
        return f"{millions:.2f}M"
    if millions >= 0.01:
        return f"{millions:.3f}M"
    return f"{millions:.4f}M"


def truncate(text: str, limit: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def local_datetime(value: str) -> str:
    """把接口给的 ISO 时间换成**本机时区**的「YYYY-MM-DD HH:MM」。

    为什么要转：Canvas 的 `due_at` 是 UTC（`2026-10-29T15:59:00Z`），直接截断会显示成
    15:59，实际截止是本地 23:59 —— 差 8 小时，作业看着像早就过期了。
    没有时区标记的（已经本地化过的字符串）原样保留，只把 T 换成空格。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return text.replace("T", " ")[:16]
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()          # 换成系统本地时区
    return parsed.strftime("%Y-%m-%d %H:%M")


def open_external(target: str, local: bool = False) -> bool:
    """打开外部链接 / 文件 / 目录，**测试和探针里一律不弹**。

    为什么要这层：调试脚本里调 `QDesktopServices.openUrl` 会真的交给 Windows，
    路径不存在时资源管理器会打开「库 / Libraries」，一跑测试就堆一屏窗口。
    `LUMINA_SMOKE`（测试）或 `LUMINA_NO_OPEN`（探针）置位时直接返回 False。
    """
    import os

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    if os.environ.get("LUMINA_NO_OPEN") or os.environ.get("LUMINA_SMOKE"):
        return False
    text = str(target or "").strip()
    if not text:
        return False
    url = QUrl.fromLocalFile(text) if local else QUrl(text)
    if not url.isValid():
        return False
    try:
        return bool(QDesktopServices.openUrl(url))
    except Exception:
        return False
