"""圆角弹出层基类：无边框 + 透明窗口，圆角与描边由内层面板绘制。

直接在 QDialog 上写 border-radius 会出现「圆角外面还露着方形底色」——
因为窗口本身仍是矩形。这里把窗口设为透明，再自己画一张圆角卡片。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QDialog, QVBoxLayout

from . import theme
from .widgets import rounded_path


class RoundedPopup(QDialog):
    """圆角弹出面板；子控件加进 self.content 布局。"""

    RADIUS = 10
    PADDING = 12

    def __init__(self, parent=None, padding: int = 12, radius: int = 10):
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.PADDING = padding
        self.RADIUS = radius
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(padding, padding, padding, padding)
        self.content.setSpacing(6)
        self.setStyleSheet(
            f"QLabel {{ color: {theme.TEXT}; }}"
            f"QCheckBox {{ color: {theme.TEXT_SOFT}; font-size: 12px; }}"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded_path(rect, float(self.RADIUS))
        painter.fillPath(path, QColor(theme.CARD))
        painter.setPen(QPen(QColor(theme.BORDER), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()
