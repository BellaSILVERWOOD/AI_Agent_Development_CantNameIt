"""Segoe Fluent / MDL2 图标字形常量。"""
from __future__ import annotations

import sys

CALENDAR = "\uE787"
MAIL = "\uE715"
SETTINGS = "\uE713"
ADD = "\uE710"
CHEVRON_LEFT = "\uE76B"
CHEVRON_RIGHT = "\uE76C"
CHEVRON_DOWN = "\uE70D"
CLOSE = "\uE8BB"
MINIMIZE = "\uE921"
MAXIMIZE = "\uE922"
RESTORE = "\uE923"
DELETE = "\uE74D"
EDIT = "\uE70F"
CLOCK = "\uE823"
BELL = "\uE7ED"
CHECK = "\uE73E"
SEARCH = "\uE721"
FOLDER = "\uE8B7"
REFRESH = "\uE72C"
SAVE = "\uE74E"
SEND = "\uE724"
ATTACH = "\uE723"
DOCUMENT = "\uE8A5"
PICTURE = "\uE8B9"
WARNING = "\uE7BA"
INFO = "\uE946"
CONTACT = "\uE77B"
UPLOAD = "\uE898"
DOWNLOAD = "\uE896"
FILTER = "\uE71C"
MORE = "\uE712"
EYE = "\uE7B3"
CALENDAR_DAY = "\uE161"
SUN = "\uE706"
MOON = "\uE708"
INBOX = "\uE8A0"
SWAP = "\uE8AB"
COLUMNS = "\uE8A9"
LOCK = "\uE72E"
KEY = "\uE192"

_ICON_FAMILY: str | None = None


def icon_family() -> str:
    """优先使用 Windows 11 的 Segoe Fluent Icons，退回 MDL2。"""
    global _ICON_FAMILY
    if _ICON_FAMILY is None:
        try:
            from PySide6.QtGui import QFontDatabase

            families = set(QFontDatabase.families())
            for candidate in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
                if candidate in families:
                    _ICON_FAMILY = candidate
                    break
            else:
                _ICON_FAMILY = "Segoe MDL2 Assets"
        except Exception:
            _ICON_FAMILY = "Segoe MDL2 Assets"
        if sys.platform != "win32":
            # 非 Windows 环境只用于离屏测试，避免出现方块字
            _ICON_FAMILY = "DejaVu Sans"
    return _ICON_FAMILY
