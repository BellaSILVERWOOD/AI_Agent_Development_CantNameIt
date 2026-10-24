"""右侧 dock：把「最近日程」和「AI」放在同一个可折叠侧栏里，用顶部小标签切换。

* 标签：日程 / AI
* 右侧箭头：整列收起成一条窄栏（记住状态，存 settings 的 dock_open / dock_tab）
* 全屏：把整列铺满窗口（AI 面板的「全屏」按钮走这条路），Esc 退出
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import icons, theme
from .ai_panel import AiPage
from .panel_agenda import COLLAPSED_WIDTH, EXPANDED_WIDTH, AgendaSidebar
from .widgets import IconButton, SoftButton

DOCK_OPEN_KEY = "dock_open"
DOCK_TAB_KEY = "dock_tab"
DOCK_WIDTH_KEY = "dock_width"
MIN_WIDTH = 240
MAX_WIDTH = 720
HANDLE = 7          # 左边框拖拽热区宽度


class RightDock(QWidget):
    """右侧栏：日程 + AI 两个标签。"""

    event_clicked = Signal(int)
    width_changed = Signal(int)

    def __init__(self, store, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.store = store
        self._open = (store.get_setting(DOCK_OPEN_KEY, "1") or "1") != "0"
        tab = (store.get_setting(DOCK_TAB_KEY, "agenda") or "agenda").strip()
        self._tab = tab if tab in ("agenda", "ai") else "agenda"
        try:
            stored = int(float(store.get_setting(DOCK_WIDTH_KEY, "") or EXPANDED_WIDTH))
        except Exception:
            stored = EXPANDED_WIDTH
        self._width = max(MIN_WIDTH, min(MAX_WIDTH, stored))
        self._resizing = False
        self._primary = False
        self._swapped = False
        self._resize_start_x = 0
        self._resize_start_width = self._width
        self.setMouseTracking(True)
        self._fullscreen = False

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 8)
        root.setSpacing(6)

        # 顶部：标签 + 收起
        self.head = QWidget()
        head_row = QHBoxLayout(self.head)
        head_row.setContentsMargins(8, 0, 8, 0)
        head_row.setSpacing(4)
        self.agenda_tab = SoftButton("日程", "subtle", height=26, min_width=56,
                                     point_size=8.4)
        self.agenda_tab.clicked.connect(lambda: self.set_tab("agenda"))
        head_row.addWidget(self.agenda_tab)
        self.ai_tab = SoftButton("AI", "subtle", height=26, min_width=52,
                                 point_size=8.4)
        self.ai_tab.clicked.connect(lambda: self.set_tab("ai"))
        head_row.addWidget(self.ai_tab)
        head_row.addStretch(1)
        self.swap_button = IconButton(icons.SWAP, size=26, glyph_size=11,
                                      tip="与中间栏互换（内容搬到大区域）")
        self.swap_button.clicked.connect(self._request_swap)
        head_row.addWidget(self.swap_button)
        self.collapse_button = IconButton(icons.CHEVRON_RIGHT, size=26, glyph_size=11,
                                          tip="收起成一条窄栏")
        self.collapse_button.clicked.connect(lambda: self.set_open(False))
        head_row.addWidget(self.collapse_button)
        self.close_button = IconButton(icons.CLOSE, size=26, glyph_size=11,
                                       tip="关闭右侧栏（Ctrl+Alt+R 再打开）")
        self.close_button.clicked.connect(self._request_close)
        head_row.addWidget(self.close_button)
        root.addWidget(self.head)

        self.stack = QStackedWidget()
        self.agenda = AgendaSidebar(store, collapsible=False)
        self.agenda.event_clicked.connect(self.event_clicked.emit)
        self.stack.addWidget(self.agenda)
        self.ai = AiPage(store, compact=True)
        self.stack.addWidget(self.ai)
        root.addWidget(self.stack, 1)

        # 收起状态：一条窄栏
        self.rail = QWidget()
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.setSpacing(6)
        self.expand_button = IconButton(icons.CHEVRON_LEFT, size=30, glyph_size=12,
                                        tip="展开右侧栏")
        self.expand_button.clicked.connect(lambda: self.set_open(True))
        rail_layout.addWidget(self.expand_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.rail_badge = QLabel("")
        self.rail_badge.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.rail_badge.setStyleSheet(
            f"color: {theme.ACCENT}; font-size: 12px; font-weight: 600;")
        rail_layout.addWidget(self.rail_badge)
        rail_layout.addStretch(1)
        root.addWidget(self.rail)

        self._apply_state()
        try:
            self.agenda.set_today_only(True)
        except Exception:
            pass
        self.agenda.refresh()

    # -------------------------------------------------- 外观
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawLine(0, 6, 0, self.height() - 6)
        if not self._open:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.translate(18, 176)
            painter.rotate(-90)
            font = QFont(self.font())
            font.setPixelSize(10)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QPen(QColor(theme.MUTED)))
            painter.drawText(0, 0, "日程 · AI")
            painter.restore()

    # -------------------------------------------------- 互换 / 主区域
    def _request_swap(self) -> None:
        window = self.window()
        setter = getattr(window, "swap_panes", None)
        if callable(setter):
            setter()

    def _request_close(self) -> None:
        """整列关掉（不是收成窄栏）：交给主窗口统一排版。"""
        window = self.window()
        setter = getattr(window, "toggle_dock", None)
        if callable(setter):
            setter(False)
        else:
            self.set_open(False)

    def set_swapped(self, swapped: bool) -> None:
        self._swapped = bool(swapped)
        try:
            if self._swapped:
                self.swap_button.setToolTip("换回原来的位置（Ctrl+Shift+L）")
            else:
                self.swap_button.setToolTip(
                    "与中间栏互换：日程/AI 搬到大区域（Ctrl+Shift+L）")
        except Exception:
            pass
        self._apply_state()

    def set_primary(self, primary: bool) -> None:
        """作为主区域（中间）时不固定宽度，跟着窗口伸缩。

        同时在窄栏（右侧）里日程只显示今天，搬到中间才用「最近 n 条 / n 天」。
        """
        self._primary = bool(primary)
        try:
            self.agenda.set_today_only(not self._primary)
        except Exception:
            pass
        self._apply_state()

    def preferred_width(self) -> int:
        return max(MIN_WIDTH, min(MAX_WIDTH, self._width))

    # -------------------------------------------------- 拖宽度
    def _in_handle(self, x: float) -> bool:
        return self._open and not self._fullscreen and 0 <= x <= HANDLE

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._in_handle(event.position().x())):
            self._resizing = True
            self._resize_start_x = event.globalPosition().toPoint().x()
            self._resize_start_width = self.width()
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self._resizing:
            delta = self._resize_start_x - event.globalPosition().toPoint().x()
            width = max(MIN_WIDTH, min(MAX_WIDTH, self._resize_start_width + delta))
            if width != self.width():
                self._width = width
                self.setFixedWidth(width)
                try:
                    self.width_changed.emit(width)
                except Exception:
                    pass
            event.accept()
            return
        self.setCursor(Qt.CursorShape.SizeHorCursor if self._in_handle(x)
                       else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = False
            self.unsetCursor()
            try:
                self.store.set_setting(DOCK_WIDTH_KEY, str(int(self._width)))
            except Exception:
                pass
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if not self._resizing:
            self.unsetCursor()
        super().leaveEvent(event)

    def width(self) -> int:  # noqa: N802 - 方便外部读
        return super().width()

    # -------------------------------------------------- 状态
    def _apply_state(self) -> None:
        # 全屏时把标签条也收起来，只留面板本身
        self.head.setVisible(self._open and not self._fullscreen)
        self.stack.setVisible(self._open)
        self.rail.setVisible(not self._open)
        self.agenda_tab.setKind("primary" if self._tab == "agenda" else "subtle")
        self.ai_tab.setKind("primary" if self._tab == "ai" else "subtle")
        self.stack.setCurrentIndex(0 if self._tab == "agenda" else 1)
        if self._primary and not self._fullscreen:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Expanding)
            try:
                self.width_changed.emit(self.width())
            except Exception:
                pass
            self.update()
            return
        if self._fullscreen:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Expanding)
        elif self._open:
            self.setFixedWidth(max(MIN_WIDTH, min(MAX_WIDTH, self._width)))
        else:
            self.setFixedWidth(COLLAPSED_WIDTH)
        try:
            self.width_changed.emit(self.width())
        except Exception:
            pass
        self.update()

    def reset_width(self) -> None:
        self._width = EXPANDED_WIDTH
        try:
            self.store.set_setting(DOCK_WIDTH_KEY, str(EXPANDED_WIDTH))
        except Exception:
            pass
        self._apply_state()

    def set_open(self, value: bool) -> None:
        value = bool(value)
        if self._open == value:
            return
        self._open = value
        self.store.set_setting(DOCK_OPEN_KEY, "1" if value else "0")
        self._apply_state()
        if value:
            self.refresh()

    def is_open(self) -> bool:
        return self._open

    def set_tab(self, tab: str) -> None:
        if tab not in ("agenda", "ai"):
            return
        self._tab = tab
        self.store.set_setting(DOCK_TAB_KEY, tab)
        if not self._open:
            self.set_open(True)
        else:
            self._apply_state()
        self.refresh()

    def show_ai(self) -> None:
        self.set_tab("ai")

    def show_agenda(self) -> None:
        self.set_tab("agenda")

    def current_tab(self) -> str:
        return self._tab

    def refresh(self) -> None:
        try:
            self.agenda.refresh()
            self.rail_badge.setText(str(self.agenda._today_left()))
        except Exception:
            pass

    # -------------------------------------------------- 全屏
    def set_fullscreen(self, on: bool) -> None:
        self._fullscreen = bool(on)
        if on:
            self._tab = "ai"
            self._open = True
        self._apply_state()

    def is_fullscreen(self) -> bool:
        return self._fullscreen
