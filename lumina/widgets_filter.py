"""多选筛选控件：按钮 + 弹出勾选列表（Qt 没有现成的多选下拉）。"""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import icons, theme


class _OptionRow(QWidget):
    """一行可点选的选项：圆形勾选 + 文字。"""

    def __init__(self, key: str, label: str, checked: bool, parent=None):
        super().__init__(parent)
        from .widgets import RoundCheck

        self.key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)
        self.check = RoundCheck(checked, 18)
        layout.addWidget(self.check)
        self.label = QLabel(label)
        self.label.setStyleSheet(f"color: {theme.TEXT_SOFT}; font-size: 12px;")
        layout.addWidget(self.label, 1)
        self.setFixedHeight(30)

    def is_checked(self) -> bool:
        return self.check.isChecked()

    def set_checked(self, checked: bool) -> None:
        self.check.setChecked(checked)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.check.setChecked(not self.check.isChecked())
        super().mouseReleaseEvent(event)


class MultiSelectFilter(QPushButton):
    """显示「来源：全部 / 已选 N 项」，点击弹出可勾选的列表。

    items: [(key, label), ...]；changed 回调带 key 列表（空列表 = 全部）。
    """

    changed = Signal(list)

    def __init__(self, items: Iterable[Tuple[str, str]], placeholder: str = "来源",
                 parent=None):
        super().__init__(parent)
        self._items: List[Tuple[str, str]] = list(items)
        self._selected: List[str] = []
        self._placeholder = placeholder
        self._popup: Optional[QDialog] = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(32)
        self.setStyleSheet(
            f"QPushButton {{ background: {theme.CARD}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 8px;"
            " padding: 4px 12px; text-align: left; }"
            f"QPushButton:hover {{ border-color: {theme.INPUT_HOVER_BORDER}; }}"
        )
        self.clicked.connect(self._open)
        self._sync_text()

    # -------------------------------------------------- 对外
    def selected(self) -> List[str]:
        return list(self._selected)

    def set_items(self, items: Iterable[Tuple[str, str]]) -> None:
        self._items = list(items)
        valid = {key for key, _label in self._items}
        self._selected = [key for key in self._selected if key in valid]
        self._sync_text()

    def set_selected(self, keys: Iterable[str]) -> None:
        self._selected = [key for key in keys]
        self._sync_text()

    # -------------------------------------------------- 内部
    def _sync_text(self) -> None:
        if not self._selected:
            text = f"{self._placeholder}：全部"
        elif len(self._selected) == 1:
            label = dict(self._items).get(self._selected[0], self._selected[0])
            text = f"{self._placeholder}：{label}"
        else:
            text = f"{self._placeholder}：已选 {len(self._selected)} 项"
        self.setText(text + "  ▾")

    def _open(self) -> None:
        from .popup import RoundedPopup

        popup = RoundedPopup(self.window(), padding=12, radius=10)
        self._popup = popup
        layout = popup.content

        head = QLabel(self._placeholder)
        head.setObjectName("H3")
        layout.addWidget(head)

        boxes = {}
        for key, label in self._items:
            row_widget = _OptionRow(key, label, key in self._selected)
            boxes[key] = row_widget
            layout.addWidget(row_widget)

        row = QHBoxLayout()
        row.addStretch(1)
        all_button = QPushButton("全部")
        all_button.setFlat(True)
        all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        all_button.clicked.connect(
            lambda: [box.set_checked(True) for box in boxes.values()])
        none_button = QPushButton("清空")
        none_button.setFlat(True)
        none_button.setCursor(Qt.CursorShape.PointingHandCursor)
        none_button.clicked.connect(
            lambda: [box.set_checked(False) for box in boxes.values()])
        ok_button = QPushButton("确定")
        ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_button.setStyleSheet(
            f"QPushButton {{ background: {theme.ACCENT}; color: #FFFFFF;"
            " border: none; border-radius: 7px; padding: 5px 16px; }")
        ok_button.clicked.connect(lambda: self._apply(boxes, popup))
        row.addWidget(all_button)
        row.addWidget(none_button)
        row.addSpacing(8)
        row.addWidget(ok_button)
        layout.addLayout(row)

        popup.adjustSize()
        origin = self.mapToGlobal(QPoint(0, self.height() + 4))
        popup.move(origin)
        popup.exec()

    def _apply(self, boxes: dict, popup: QDialog) -> None:
        self._selected = [key for key, box in boxes.items() if box.is_checked()]
        self._sync_text()
        popup.accept()
        self.changed.emit(self.selected())
