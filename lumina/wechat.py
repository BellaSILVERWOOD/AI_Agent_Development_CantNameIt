"""微信消息读取（Windows）。

两种方式：

1. **直接读取**（默认关，在 Agent 窗口里勾「允许直接读取微信」）：
   微信 4.x 用 UI Automation 读不到文字（进程 Weixin.exe，自绘窗口，UIA 里只有空 Pane），
   所以改成「截当前聊天窗口 → Windows 自带 OCR 识别文字」，见 `wechat_read.py`。
   不抢焦点、窗口在后台也能截；缺点是截图识别会有错字，重要内容要自己核一下。

2. **剪贴板**（永远可用）：
   在微信里选中消息 → 右键「复制」或 Ctrl+C → 点「读微信」。
   微信复制出来的文本形如每段「昵称 时:分」换行「内容」，逐段解析即可。

对外接口：
    running()             -> bool   微信是否在运行
    direct_available()    -> bool   能不能直读（PowerShell + 中文 OCR 引擎）
    grab_current_chat()   -> str    截当前聊天窗口识别文字（失败抛 RuntimeError）
    clipboard_text()      -> str    剪贴板里的文本
    grab_via_uia()        -> str    旧名字，等于 grab_current_chat()（兼容）
    uia_available()       -> bool   旧名字，等于 direct_available()（兼容）
    split_blocks(text)    -> List[str]
    window_titles()       -> List[str]
"""
from __future__ import annotations

import re
from typing import List

from . import wechat_read

PROCESS_HINTS = ("WeChat", "Weixin")
WINDOW_HINTS = ("微信", "WeChat", "Weixin")

_HEADER = re.compile(r"^\s*\S.{0,23}?\s+\d{1,2}[:：]\d{2}\s*$")


def running() -> bool:
    """微信是否开着（按窗口标题判断，不用进程权限）。"""
    for title in window_titles():
        if any(hint in title for hint in WINDOW_HINTS):
            return True
    return False


def window_titles() -> List[str]:
    try:
        import win32gui
    except Exception:
        return []
    found: List[str] = []

    def collect(handle, _result):
        try:
            if win32gui.IsWindowVisible(handle):
                title = win32gui.GetWindowText(handle)
                if title:
                    found.append(title)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        return found
    return found


def clipboard_text() -> str:
    try:
        from PySide6.QtWidgets import QApplication

        return QApplication.clipboard().text() or ""
    except Exception:
        return ""


# ------------------------------------------------------------------ 直接读取
def direct_available() -> bool:
    """能不能直读（PowerShell + 系统中文 OCR）。"""
    try:
        return bool(wechat_read.available())
    except Exception:
        return False


def grab_current_chat() -> str:
    """截当前聊天窗口 → OCR。失败抛 RuntimeError，调用方自己退回剪贴板。"""
    return wechat_read.grab_current_chat()


# 兼容旧名字
def uia_available() -> bool:
    return direct_available()


def grab_via_uia() -> str:
    return grab_current_chat()


# ------------------------------------------------------------------ 文本解析
def split_blocks(text: str) -> List[str]:
    """把微信复制出来的文本切成一条条消息。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    lines = [line.rstrip() for line in text.split("\n")]
    blocks: List[str] = []
    current: List[str] = []
    for line in lines:
        if _HEADER.match(line):
            if current:
                blocks.append("\n".join(current).strip())
            current = []
            continue
        if not line.strip():
            continue
        current.append(line.strip())
    if current:
        blocks.append("\n".join(current).strip())
    if not blocks:
        blocks = [chunk.strip() for chunk in re.split(r"\n{2,}", text) if chunk.strip()]
    return [block for block in blocks if block]
