"""专注模式：小浮窗计时器 + 离开提醒 + 白名单。

* 点侧边栏「专注模式」开始，桌面上会出现一个悬浮计时器（可拖动）；
* 每 5 秒看一眼前台窗口：只要前台不是 DanLume、也不在白名单里，就开始累计「离开时间」；
* 离开超过阈值（默认 5 分钟）就弹提醒，提醒后会安静一阵，避免连环弹窗；
* 白名单可以填进程名（chrome.exe）或窗口标题里的关键词（canvas.fudan.edu.cn）。

设置项：focus_away_enabled、focus_away_minutes、focus_whitelist、focus_pos
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme
from .widgets import rounded_path

TICK_MS = 1000
CHECK_MS = 5000
DEFAULT_AWAY_MINUTES = 5
WINDOW_W = 154
WINDOW_H = 58


# ------------------------------------------------------------------ 前台窗口
def _foreground() -> Tuple[int, str, str]:
    """返回 (pid, 进程名, 窗口标题)。"""
    if sys.platform != "win32":
        return (0, "", "")
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return (0, "", "")
    try:
        hwnd = user32.GetForegroundWindow()
    except Exception:
        return (0, "", "")
    if not hwnd:
        return (0, "", "")
    title = ""
    try:
        length = int(user32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value or ""
    except Exception:
        title = ""
    pid = 0
    name = ""
    try:
        pid_value = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_value))
        pid = int(pid_value.value)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                size = wintypes.DWORD(1024)
                path_buf = ctypes.create_unicode_buffer(1024)
                if kernel32.QueryFullProcessImageNameW(
                        handle, 0, path_buf, ctypes.byref(size)):
                    name = (path_buf.value or "").rsplit("\\", 1)[-1]
            finally:
                kernel32.CloseHandle(handle)
    except Exception:
        pass
    return (pid, name, title)


def whitelist_entries(store) -> List[str]:
    raw = store.get_setting("focus_whitelist", "") or ""
    items = []
    for line in raw.replace("，", ",").replace("\n", ",").split(","):
        text = line.strip().lower()
        if text:
            items.append(text)
    return items


# ------------------------------------------------------------------ 浮窗
class FocusTimer(QWidget):
    """桌面上的小计时器：支持「不定时」和「定时倒计时」两种模式。"""

    clicked = Signal()
    paused_changed = Signal(bool)
    finished = Signal(int)      # 定时模式到点了，参数是原本设定的分钟数

    def __init__(self, store, parent=None):
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.store = store
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(WINDOW_W, WINDOW_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("左键暂停 / 继续，右键更多操作")

        self._seconds = 0.0
        self._duration = 0.0        # 0 表示不定时
        self._running = False
        self._paused = False
        self._away = 0.0
        self._finished_sent = False
        self._press_pos: Optional[QPoint] = None
        self._press_offset = None
        self._moved = False

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_MS)
        self._tick_timer.timeout.connect(self._tick)

    # -------------------------------------------------- 状态
    @property
    def seconds(self) -> float:
        return self._seconds

    @property
    def paused(self) -> bool:
        return self._paused

    def is_timed(self) -> bool:
        return self._duration > 0

    def duration_minutes(self) -> int:
        return int(round(self._duration / 60.0)) if self._duration else 0

    def remaining(self) -> float:
        return max(0.0, self._duration - self._seconds) if self.is_timed() else 0.0

    def start(self, minutes: Optional[float] = None) -> None:
        """minutes 为空 = 不定时；给了分钟数就是倒计时。"""
        self._duration = float(minutes) * 60.0 if minutes else 0.0
        self._seconds = 0.0
        self._away = 0.0
        self._paused = False
        self._running = True
        self._finished_sent = False
        self._restore_position()
        self.show()
        self.raise_()
        if not self._tick_timer.isActive():
            self._tick_timer.start()
        self.update()

    def extend(self, minutes: float) -> None:
        """定时模式延长 N 分钟。"""
        if not self.is_timed():
            return
        self._duration += float(minutes) * 60.0
        self._finished_sent = False
        self.update()

    def stop(self) -> None:
        self._running = False
        self._tick_timer.stop()
        self.hide()

    def is_running(self) -> bool:
        return self._running

    def toggle_pause(self) -> None:
        if not self._running:
            return
        self._paused = not self._paused
        self.paused_changed.emit(self._paused)
        self.update()

    def set_away(self, seconds: float) -> None:
        self._away = max(0.0, seconds)
        self.update()

    def _tick(self) -> None:
        if not self._running or self._paused:
            return
        self._seconds += TICK_MS / 1000.0
        if self.is_timed() and not self._finished_sent:
            if self._seconds >= self._duration:
                self._seconds = self._duration
                self._finished_sent = True
                self.update()
                self.finished.emit(self.duration_minutes())
                return
        self.update()

    # -------------------------------------------------- 位置
    def _restore_position(self) -> None:
        raw = (self.store.get_setting("focus_pos", "") or "").strip()
        if "," in raw:
            try:
                x, y = (int(part) for part in raw.split(",", 1))
                self.move(x, y)
                return
            except Exception:
                pass
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.x() + 36, area.y() + 36)

    def _save_position(self) -> None:
        self.store.set_setting("focus_pos", f"{self.x()},{self.y()}")

    # -------------------------------------------------- 交互
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._press_offset = self._press_pos - self.pos()
            self._moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is None or self._press_offset is None:
            return
        current = event.globalPosition().toPoint()
        if (current - self._press_pos).manhattanLength() > 5:
            self._moved = True
        if self._moved:
            self.move(current - self._press_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._moved:
            self._save_position()
        else:
            self.clicked.emit()
        self._press_pos = None
        event.accept()

    def contextMenuEvent(self, event):
        menu = FocusMenu(self)
        menu.adjustSize()
        menu.move(event.globalPos())
        menu.show()

    # -------------------------------------------------- 绘制
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 30, 30))
        painter.drawPath(rounded_path(QRectF(rect).adjusted(0, 2, 0, 4), 14))

        card = QColor(theme.CARD)
        card.setAlpha(244)
        painter.setBrush(card)
        border = QColor(theme.BORDER)
        if self._away > 30:
            border = QColor(theme.WARNING)
        painter.setPen(QPen(border, 1.2))
        painter.drawPath(rounded_path(rect, 14))

        # 左侧小圆点：运行=强调色，暂停=灰
        dot_color = QColor(theme.MUTED if self._paused else theme.ACCENT)
        if self.is_timed() and self._finished_sent:
            dot_color = QColor(theme.SUCCESS)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot_color)
        painter.drawEllipse(QRectF(rect.left() + 13, rect.top() + 12, 9, 9))

        # 定时模式显示剩余，不定时显示已经专注多久
        if self.is_timed():
            total = int(round(self.remaining()))
        else:
            total = int(self._seconds)
        text = f"{total // 60:02d}:{total % 60:02d}"
        title_font = QFont(self.font())
        title_font.setPixelSize(18)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QPen(QColor(theme.TEXT)))
        painter.drawText(QRectF(rect.left() + 30, rect.top() + 5, rect.width() - 40, 21),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

        # 第二行：状态说明
        hint_font = QFont(self.font())
        hint_font.setPixelSize(10)
        painter.setFont(hint_font)
        painter.setPen(QPen(QColor(theme.MUTED)))
        if self._paused:
            hint = "已暂停"
        elif self.is_timed() and self._finished_sent:
            hint = "时间到"
        elif self.is_timed():
            hint = f"定时 {self.duration_minutes()} 分钟"
        else:
            hint = "专注中（不定时）"
        if self._away > 30 and not self._paused and not self._finished_sent:
            hint = f"离开 {int(self._away // 60)} 分钟"
        painter.drawText(QRectF(rect.left() + 30, rect.top() + 24,
                                rect.width() - 40, 13),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, hint)

        # 第三行：进度条（只在定时模式画）
        if self.is_timed() and self._duration > 0:
            track = QRectF(rect.left() + 30, rect.top() + 40,
                           rect.width() - 42, 3.5)
            painter.setPen(Qt.PenStyle.NoPen)
            track_bg = QColor(theme.BORDER)
            track_bg.setAlpha(160)
            painter.setBrush(track_bg)
            painter.drawPath(rounded_path(track, 2))
            ratio = min(1.0, max(0.0, self._seconds / self._duration))
            if ratio > 0.01:
                bar = QRectF(track.left(), track.top(),
                             max(3.0, track.width() * ratio), track.height())
                painter.setBrush(QColor(theme.ACCENT if not self._finished_sent
                                        else theme.SUCCESS))
                painter.drawPath(rounded_path(bar, 2))


# ------------------------------------------------------------------ 菜单
class FocusMenu(QWidget):
    """计时器的圆角右键菜单。"""

    def __init__(self, timer_window: "FocusTimer"):
        super().__init__(None, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        from PySide6.QtWidgets import QFrame, QVBoxLayout

        from .widgets import SoftButton

        self.timer_window = timer_window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("FocusCard")
        card.setStyleSheet(
            f"QFrame#FocusCard {{ background: {theme.CARD};"
            f" border: 1px solid {theme.BORDER}; border-radius: 12px; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        actions = [("继续专注" if timer_window.paused else "暂停一下", self._toggle)]
        if timer_window.is_timed():
            actions.append(("延长 10 分钟", self._extend))
        actions.extend([
            ("结束专注", self._stop),
            ("回左上角", self._reset),
        ])
        for text, handler in actions:
            button = SoftButton(text, "ghost", height=30, min_width=124, point_size=8.6)
            button.clicked.connect(lambda _c=False, h=handler: h())
            layout.addWidget(button)
        outer.addWidget(card)

    def _extend(self) -> None:
        self.timer_window.extend(10)
        self.close()

    def _toggle(self) -> None:
        self.timer_window.toggle_pause()
        self.close()

    def _stop(self) -> None:
        self.timer_window.stop()
        self.close()

    def _reset(self) -> None:
        self.timer_window.store.set_setting("focus_pos", "")
        self.timer_window._restore_position()
        self.close()


# ------------------------------------------------------------------ 启动菜单
class FocusStartMenu(QWidget):
    """开始专注前先选模式：不定时 / 25 / 45 / 60 / 自定义。"""

    chosen = Signal(object)      # None = 不定时；数字 = 分钟

    OPTIONS = [("不定时（一直计时）", None), ("25 分钟", 25),
               ("45 分钟", 45), ("60 分钟", 60)]

    def __init__(self, parent=None):
        super().__init__(None, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        from PySide6.QtWidgets import QFrame, QVBoxLayout

        from .widgets import SoftButton

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("FocusStartCard")
        card.setStyleSheet(
            f"QFrame#FocusStartCard {{ background: {theme.CARD};"
            f" border: 1px solid {theme.BORDER}; border-radius: 12px; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        for text, minutes in self.OPTIONS:
            button = SoftButton(text, "ghost", height=30, min_width=168, point_size=8.6)
            button.clicked.connect(
                lambda _checked=False, value=minutes: self._pick(value))
            layout.addWidget(button)
        custom = SoftButton("自定义分钟数…", "ghost", height=30, min_width=168,
                            point_size=8.6)
        custom.clicked.connect(self._pick_custom)
        layout.addWidget(custom)
        outer.addWidget(card)

    def _pick(self, minutes) -> None:
        self.chosen.emit(minutes)
        self.close()

    def _pick_custom(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        value, ok = QInputDialog.getInt(self, "自定义专注时长", "多少分钟？",
                                        30, 1, 480, 5)
        if ok:
            self.chosen.emit(int(value))
        else:
            self.close()

    def show_at(self, global_pos) -> None:
        self.adjustSize()
        self.move(global_pos)
        self.show()


# ------------------------------------------------------------------ 提醒
class AwayToast(QWidget):
    """离开太久的提醒浮窗（置顶、不抢焦点）。"""

    back_requested = Signal()
    stop_requested = Signal()

    def __init__(self, minutes: int, parent=None):
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.minutes = minutes
        self.setFixedSize(320, 132)

        from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

        from .widgets import SoftButton

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)
        self.title = QLabel("回到专注吧")
        self.title.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 13px; font-weight: 600;")
        self.body = QLabel(f"你已经在别处待了 {minutes} 分钟，回来继续写作业？")
        self.body.setObjectName("Muted")
        self.body.setWordWrap(True)
        outer.addWidget(self.title)
        outer.addWidget(self.body)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.stop_button = SoftButton("结束专注", "ghost", height=30, min_width=96,
                                      point_size=8.6)
        self.stop_button.clicked.connect(self._stop)
        row.addWidget(self.stop_button)
        self.back_button = SoftButton("继续专注", "primary", height=30, min_width=96,
                                      point_size=8.6)
        self.back_button.clicked.connect(self._back)
        row.addWidget(self.back_button)
        outer.addLayout(row)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 30, 36))
        painter.drawPath(rounded_path(QRectF(rect).adjusted(0, 2, 0, 4), 14))
        card = QColor(theme.CARD)
        card.setAlpha(248)
        painter.setBrush(card)
        painter.setPen(QPen(QColor(theme.WARNING), 1.2))
        painter.drawPath(rounded_path(rect, 14))

    def _back(self) -> None:
        self.back_requested.emit()
        self.close()

    def _stop(self) -> None:
        self.stop_requested.emit()
        self.close()

    def show_at_corner(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.x() + area.width() - self.width() - 24,
                      area.y() + area.height() - self.height() - 24)
        self.show()
        self.raise_()


# ------------------------------------------------------------------ 监控
class FocusMonitor(QObject):
    """盯着前台窗口，离开太久就发出提醒。"""

    away_alert = Signal(int)          # 离开了多少分钟
    focus_changed = Signal(bool)      # 是否在专注（前台在 DanLume 或白名单）

    def __init__(self, store, timer_window: FocusTimer, parent=None):
        super().__init__(parent)
        self.store = store
        self.timer_window = timer_window
        self._running = False
        self._away_seconds = 0.0
        self._quiet_until = 0.0
        self._focused = True

        self._check_timer = QTimer(self)
        self._check_timer.setInterval(CHECK_MS)
        self._check_timer.timeout.connect(self.check)

    # -------------------------------------------------- 控制
    def start(self) -> None:
        self._running = True
        self._away_seconds = 0.0
        self._quiet_until = 0.0
        self._focused = True
        if not self._check_timer.isActive():
            self._check_timer.start()

    def stop(self) -> None:
        self._running = False
        self._check_timer.stop()
        self._away_seconds = 0.0
        self.timer_window.set_away(0.0)

    def is_running(self) -> bool:
        return self._running

    def away_seconds(self) -> float:
        return self._away_seconds

    # -------------------------------------------------- 设置
    def away_enabled(self) -> bool:
        return (self.store.get_setting("focus_away_enabled", "1") or "1") != "0"

    def away_minutes(self) -> int:
        try:
            value = int(float(self.store.get_setting(
                "focus_away_minutes", str(DEFAULT_AWAY_MINUTES)) or DEFAULT_AWAY_MINUTES))
        except Exception:
            value = DEFAULT_AWAY_MINUTES
        return max(1, min(120, value))

    # -------------------------------------------------- 判断
    def is_focused_now(self) -> bool:
        pid, name, title = _foreground()
        if pid and pid == os.getpid():
            return True
        haystack = f"{name} {title}".lower()
        for entry in whitelist_entries(self.store):
            if entry and entry in haystack:
                return True
        return False

    def check(self) -> None:
        if not self._running or self.timer_window.paused:
            return
        focused = self.is_focused_now()
        if focused != self._focused:
            self._focused = focused
            self.focus_changed.emit(focused)

        if focused:
            self._away_seconds = 0.0
            self.timer_window.set_away(0.0)
            return

        self._away_seconds += CHECK_MS / 1000.0
        self.timer_window.set_away(self._away_seconds)
        if not self.away_enabled():
            return
        limit = self.away_minutes() * 60
        now = time.time()
        if self._away_seconds >= limit and now >= self._quiet_until:
            self.away_alert.emit(int(self._away_seconds // 60))
            # 提醒过之后安静一会儿，别连环弹
            self._quiet_until = now + limit
            self._away_seconds = 0.0
