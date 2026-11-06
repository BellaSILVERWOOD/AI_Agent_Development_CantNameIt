# -*- coding: utf-8 -*-
"""自己画的主题化提示气泡（替代 Qt 默认 QToolTip）。

为什么不用 QToolTip：它的样式虽然写在全局样式表里，但**会继承所在控件的内联
样式表颜色**（祖先 `color: #1D1D1F` 一压，深色下就是「黑底深字」），
用户看到的就是那句「这个黑框你是来幽默的吗」。这里统一接管：
`app.installEventFilter()` 拦下 ToolTip 事件，用一张跟着主题走的圆角小卡片显示，
配色和普通卡片一样（`CARD_ALT / TEXT / BORDER`），不是「黑框」。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from . import theme

TIP_PAD_X = 10
TIP_PAD_Y = 7
TIP_RADIUS = 8
TIP_MAX_WIDTH = 520


class TipLabel(QWidget):
    """一张只画文字的圆角小卡片（不接收鼠标、不抢焦点）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._text = str(text or "")
        font = QFont(self.font())
        font.setPointSizeF(9.0)
        self.setFont(font)
        metrics = QFontMetrics(font)
        lines = self._text.splitlines() or [""]
        widest = max((metrics.horizontalAdvance(line) for line in lines),
                     default=40)
        width = min(TIP_MAX_WIDTH + TIP_PAD_X * 2,
                    widest + TIP_PAD_X * 2 + 2)
        height = metrics.height() * max(1, len(lines)) + TIP_PAD_Y * 2
        self.resize(max(48, int(width)), max(24, int(height)))

    def text(self) -> str:
        return self._text

    def colors(self) -> tuple:
        """（底色, 文字色）—— 和普通卡片一样，不是「黑框」。"""
        return str(theme.CARD_ALT), str(theme.TEXT)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # 一点点投影，在浅色背景上不糊
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 30, 36))
        painter.drawRoundedRect(QRectF(rect).adjusted(0, 2, 0, 2),
                                TIP_RADIUS, TIP_RADIUS)
        painter.setBrush(QColor(theme.CARD_ALT))
        painter.setPen(QPen(QColor(theme.BORDER), 1.0))
        painter.drawRoundedRect(rect, TIP_RADIUS, TIP_RADIUS)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            rect.adjusted(TIP_PAD_X, TIP_PAD_Y, -TIP_PAD_X, -TIP_PAD_Y),
            int(Qt.AlignmentFlag.AlignLeft)
            | int(Qt.AlignmentFlag.AlignTop)
            | int(Qt.TextFlag.TextWordWrap),
            self._text)


_TIP: Optional[TipLabel] = None


def current() -> Optional[TipLabel]:
    """当前正在显示的气泡（测试用）。"""
    return _TIP


def hide_tip() -> None:
    global _TIP
    if _TIP is None:
        return
    try:
        _TIP.hide()
        _TIP.deleteLater()
    except Exception:
        pass
    _TIP = None


def show_tip(text: str, global_pos, parent=None) -> Optional[TipLabel]:
    """在鼠标右下角显示一条提示（会自动躲开屏幕边缘）。"""
    global _TIP
    text = str(text or "").strip()
    if not text:
        return None
    hide_tip()
    tip = TipLabel(text, parent)
    point = global_pos
    screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
    area = screen.availableGeometry() if screen is not None else None
    x = point.x() + 14
    y = point.y() + 18
    if area is not None:
        if x + tip.width() > area.right():
            x = max(area.left() + 6, point.x() - tip.width() - 12)
        if y + tip.height() > area.bottom():
            y = max(area.top() + 6, point.y() - tip.height() - 14)
    tip.move(int(x), int(y))
    tip.show()
    tip.raise_()
    _TIP = tip
    return tip


class _TipFilter(QObject):
    """全局拦 ToolTip 事件：自己画卡片；**没有文字就什么都不显示**。

    不再把事件交回 Qt：Qt 原生提示会继承控件颜色，深色下就是「黑底黑字」，
    用户宁可什么都不弹，也不要那个黑框。
    """

    def _text_for(self, obj, event) -> str:
        try:
            text = str(obj.toolTip() or "")
        except Exception:
            text = ""
        if text:
            return text
        # 列表 / 表格的提示写在模型里，事件目标是 viewport（控件本身没有文字）
        try:
            from PySide6.QtWidgets import QAbstractItemView

            view = obj if isinstance(obj, QAbstractItemView) else obj.parent()
            if isinstance(view, QAbstractItemView) and view.model() is not None:
                index = view.indexAt(event.pos())
                if index.isValid():
                    data = view.model().data(index, Qt.ItemDataRole.ToolTipRole)
                    if data:
                        return str(data)
        except Exception:
            pass
        return ""

    def eventFilter(self, obj, event):
        try:
            kind = event.type()
        except Exception:
            return False
        if kind == QEvent.Type.ToolTip:
            text = self._text_for(obj, event)
            if text.strip():
                show_tip(text, event.globalPos())
            else:
                hide_tip()
            return True
        if kind in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress,
                    QEvent.Type.Wheel, QEvent.Type.Hide,
                    QEvent.Type.WindowDeactivate, QEvent.Type.KeyPress):
            hide_tip()
        return False


_FILTER: Optional[_TipFilter] = None


def install(app=None) -> None:
    """装一次就够了（重复调用无副作用）。"""
    global _FILTER
    app = app or QApplication.instance()
    if app is None or _FILTER is not None:
        return
    _FILTER = _TipFilter(app)
    app.installEventFilter(_FILTER)


def uninstall() -> None:
    global _FILTER
    app = QApplication.instance()
    if app is not None and _FILTER is not None:
        try:
            app.removeEventFilter(_FILTER)
        except Exception:
            pass
    _FILTER = None
    hide_tip()
