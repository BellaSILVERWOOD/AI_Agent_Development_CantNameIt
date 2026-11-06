"""给指定的人发微信消息（模拟操作，不碰数据库、不读聊天记录）。

微信 4.x 没有可用的接口，只能在**界面上**做和手一样的事：

    1. 把微信窗口提到前台（ALT 解锁 + SetForegroundWindow）；
    2. Ctrl+F 搜索联系人 → 粘贴名字 → 回车打开会话；
    3. **OCR 读会话标题**，和你想发的人名字比对，不一致就中止（防发错人）；
    4. 点消息输入框 → 粘贴内容 → 回车发送；
    5. OCR 底部区域，确认消息真的出现在会话里。

安全设计：
* `preview()` 只搜索并读标题，**不发任何消息**；
* `send()` 要求调用方先 preview 通过，并把期望标题传进来再校验一次；
* 文本一律用剪贴板粘贴（中文/emoji 都可靠），不逐字敲键盘。

对外接口：
    available()                 -> bool
    preview(name)               -> {"ok", "title", "matched", "reason"}
    send(name, text, expect_title) -> {"ok", "step", "detail"}
    find_window() / activate()
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any, Dict, Optional

from . import wechat_read

VK_MENU = 0x12
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_F = 0x46
VK_V = 0x56
VK_A = 0x41
KEYEVENTF_KEYUP = 0x0002

_user32 = ctypes.WinDLL("user32", use_last_error=True)

# 会话标题所在区域（相对窗口）：右边一大块、最上面一条
TITLE_BOX = (0.30, 0.0, 1.0, 0.13)
# 消息输入框大概位置（相对窗口）：会话区底部中间
INPUT_BOX = (0.62, 0.90)


def available() -> bool:
    """能不能发（有微信窗口 + 能截图 OCR 校验）。"""
    return bool(wechat_read.find_window()) and wechat_read.available()


# ------------------------------------------------------------------ 窗口
def find_window() -> int:
    return wechat_read.find_window()


def activate(hwnd: int = 0) -> bool:
    """把微信提到前台（不抢焦点的话后面按键都会发到别的窗口）。"""
    hwnd = hwnd or find_window()
    if not hwnd:
        return False
    try:
        _user32.keybd_event(VK_MENU, 0, 0, 0)
        _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
        _user32.ShowWindow(wintypes.HWND(hwnd), 9)      # SW_RESTORE
        _user32.SetForegroundWindow(wintypes.HWND(hwnd))
        time.sleep(0.35)
        return _user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _key(vk: int, modifiers=()) -> None:
    for modifier in modifiers:
        _user32.keybd_event(modifier, 0, 0, 0)
    _user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    for modifier in reversed(modifiers):
        _user32.keybd_event(modifier, 0, KEYEVENTF_KEYUP, 0)


def _press_enter() -> None:
    _key(VK_RETURN)


def paste_text(text: str) -> bool:
    """用剪贴板粘贴（中文、emoji 都比模拟键盘可靠）。"""
    try:
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        previous = clipboard.text()
        clipboard.setText(text or "")
        time.sleep(0.08)
        _key(VK_V, (VK_CONTROL,))
        time.sleep(0.15)
        # 还原用户原来的剪贴板内容
        clipboard.setText(previous or "")
        return True
    except Exception:
        return False


def _click_ratio(hwnd: int, x_ratio: float, y_ratio: float) -> None:
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    x = int(left + (right - left) * x_ratio)
    y = int(top + (bottom - top) * y_ratio)
    _user32.SetCursorPos(x, y)
    time.sleep(0.05)
    _user32.mouse_event(0x0002, 0, 0, 0, 0)
    _user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.12)


# ------------------------------------------------------------------ 读标题
def _normalize(text: str) -> str:
    import re

    return re.sub(r"[\s\u3000]+", "", text or "").lower()


def current_chat_title(hwnd: int = 0) -> str:
    """OCR 会话窗口最上面那条（就是对方的名字/群名）。"""
    hwnd = hwnd or find_window()
    if not hwnd:
        return ""
    from pathlib import Path
    import tempfile

    from PIL import Image

    shot = wechat_read._grab_window_bitmap(hwnd)     # noqa: SLF001 - 复用截图
    width, height = shot.size
    box = (int(width * TITLE_BOX[0]), int(height * TITLE_BOX[1]),
           int(width * TITLE_BOX[2]), int(height * TITLE_BOX[3]))
    strip = shot.crop(box)
    strip = strip.resize((strip.width * 2, strip.height * 2), Image.LANCZOS)
    stamp = int(time.time() * 1000)
    png = Path(tempfile.gettempdir()) / f"lumina-wechat-title-{stamp}.png"
    txt = Path(tempfile.gettempdir()) / f"lumina-wechat-title-{stamp}.txt"
    try:
        strip.save(png, "PNG")
        raw = wechat_read._run_ocr(png, txt)         # noqa: SLF001
    except Exception:
        return ""
    finally:
        for path in (png, txt):
            try:
                path.unlink()
            except Exception:
                pass
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    return lines[0] if lines else ""


def _title_matches(want: str, got: str) -> bool:
    left, right = _normalize(want), _normalize(got)
    if not right:
        return False
    if left and (left in right or right in left):
        return True
    # OCR 偶尔错一两个字：按公共字符比例放宽
    common = len(set(left) & set(right))
    return bool(left) and common / max(1, len(set(left))) >= 0.7


# ------------------------------------------------------------------ 主流程
def preview(name: str) -> Dict[str, Any]:
    """只搜索并读标题：确认「要发给谁」，不发消息。"""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "title": "", "matched": False, "reason": "先填联系人"}
    hwnd = find_window()
    if not hwnd:
        return {"ok": False, "title": "", "matched": False,
                "reason": "没找到微信窗口（微信要先打开）"}
    if not activate(hwnd):
        return {"ok": False, "title": "", "matched": False,
                "reason": "微信没能提到前台（可能被别的窗口挡住）"}
    _key(VK_F, (VK_CONTROL,))
    time.sleep(0.35)
    paste_text(name)
    time.sleep(0.7)
    _press_enter()
    time.sleep(0.8)
    title = current_chat_title(hwnd)
    matched = _title_matches(name, title)
    return {
        "ok": matched,
        "title": title,
        "matched": matched,
        "reason": "" if matched else f"打开的会话是「{title or '读不到'}」，和「{name}」对不上，已中止",
    }


def send(name: str, text: str, expect_title: str = "",
         dry_run: bool = False) -> Dict[str, Any]:
    """发一条消息。`expect_title` 是 preview() 读到的标题，发送前再核对一次。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "step": "check", "detail": "内容是空的"}
    hwnd = find_window()
    if not hwnd:
        return {"ok": False, "step": "window", "detail": "没找到微信窗口"}
    if not activate(hwnd):
        return {"ok": False, "step": "focus", "detail": "微信没能提到前台"}

    title = current_chat_title(hwnd)
    want = expect_title or name
    if not _title_matches(want, title):
        return {"ok": False, "step": "title",
                "detail": f"当前会话是「{title or '读不到'}」，不是「{name}」，没有发送"}
    if dry_run:
        return {"ok": True, "step": "dry-run",
                "detail": f"演练：本来会发给「{title}」：{text[:40]}"}

    _click_ratio(hwnd, INPUT_BOX[0], INPUT_BOX[1])
    if not paste_text(text):
        return {"ok": False, "step": "paste", "detail": "粘贴失败"}
    time.sleep(0.25)
    _press_enter()
    time.sleep(0.6)
    # 发送后确认：底部区域能读到内容的一部分
    check = _normalize(text)[:8]
    bottom = _read_bottom(hwnd)
    if check and check in _normalize(bottom):
        return {"ok": True, "step": "sent", "detail": f"已发送给「{title}」"}
    return {"ok": True, "step": "sent-unverified",
            "detail": f"已发给「{title}」，但屏幕上没核对到内容，请自己看一眼"}


def _read_bottom(hwnd: int) -> str:
    from pathlib import Path
    import tempfile

    from PIL import Image

    shot = wechat_read._grab_window_bitmap(hwnd)     # noqa: SLF001
    width, height = shot.size
    strip = shot.crop((int(width * 0.30), int(height * 0.55),
                       width, int(height * 0.92)))
    stamp = int(time.time() * 1000)
    png = Path(tempfile.gettempdir()) / f"lumina-wechat-sent-{stamp}.png"
    txt = Path(tempfile.gettempdir()) / f"lumina-wechat-sent-{stamp}.txt"
    try:
        strip.save(png, "PNG")
        return wechat_read._run_ocr(png, txt)        # noqa: SLF001
    except Exception:
        return ""
    finally:
        for path in (png, txt):
            try:
                path.unlink()
            except Exception:
                pass
