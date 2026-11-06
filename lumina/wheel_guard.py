# -*- coding: utf-8 -*-
"""鼠标滚轮守卫：滚轮**不许**改控件的值。

用户要求：设置里的输入框 / 复选框，滚动页面时不要被意外改掉。以前把鼠标停在
下拉框上滚一下，选项就变了；停在滑杆上滚一下，透明度就变了 —— 很容易误操作。

做法：在 QApplication 上装一个事件过滤器，轮到「会改值的控件」收到 Wheel 事件时
**吞掉**它，并把同一个事件转给外层滚动区（于是页面照常滚动，值不变）。

覆盖的类型：QComboBox（含自定义 Select）、QAbstractSpinBox（含日期/数字框）、
QSlider（含 GlassSlider）、QCheckBox、QRadioButton、QDial、QTabBar（滚轮切标签，
不在设置里但同样容易误触；保留给调用方可选）。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractScrollArea, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDial,
    QRadioButton, QSlider, QWidget,
)

VALUE_TYPES = (QComboBox, QAbstractSpinBox, QSlider, QCheckBox, QRadioButton, QDial)


def is_value_control(widget) -> bool:
    """这个控件会不会被滚轮改值。"""
    return isinstance(widget, VALUE_TYPES)


def _scroll_target(widget, depth: int = 6) -> Optional[QWidget]:
    """往上找能接住滚轮的那个（滚动区的 viewport / 普通父控件）。"""
    parent = widget.parentWidget() if widget is not None else None
    while parent is not None and depth > 0:
        depth -= 1
        if isinstance(parent, QAbstractScrollArea):
            viewport = parent.viewport()
            return viewport if viewport is not None else parent
        if not isinstance(parent, VALUE_TYPES):
            # 普通容器：直接把事件交给它，让它继续往上传给滚动区
            if parent.parentWidget() is None:
                return parent
            parent = parent.parentWidget()
            continue
        parent = parent.parentWidget()
    return None


class WheelGuard(QObject):
    """装到 QApplication 上的事件过滤器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._forwarding = False
        self.blocked = 0      # 调试 / 测试用：拦下了多少次

    def eventFilter(self, obj, event):  # noqa: N802 - Qt 接口
        if event.type() != QEvent.Type.Wheel or self._forwarding:
            return False
        if not is_value_control(obj):
            return False
        self.blocked += 1
        target = _scroll_target(obj)
        if target is not None:
            app = QApplication.instance()
            self._forwarding = True
            try:
                app.sendEvent(target, event)
            finally:
                self._forwarding = False
        return True          # 吞掉：控件自己不会再处理


_GUARD: Optional[WheelGuard] = None


def install(app=None) -> Optional[WheelGuard]:
    """整个应用装一次（重复调用只装一次）。"""
    global _GUARD
    app = app or QApplication.instance()
    if app is None:
        return None
    if _GUARD is not None:
        return _GUARD
    _GUARD = WheelGuard(app)
    app.installEventFilter(_GUARD)
    return _GUARD


def guard() -> Optional[WheelGuard]:
    return _GUARD


def uninstall() -> None:
    """测试用：把过滤器摘掉。"""
    global _GUARD
    app = QApplication.instance()
    if _GUARD is not None and app is not None:
        app.removeEventFilter(_GUARD)
    _GUARD = None
