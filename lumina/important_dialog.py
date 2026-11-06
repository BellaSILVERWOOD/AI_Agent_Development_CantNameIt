"""重要日（倒计时）管理窗口：加一条 / 删一条，顺带显示还差几天。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from . import icons, important, theme
from .widgets import (
    Divider, ElidedLabel, IconButton, Pill, SoftButton, clear_layout, make_scroll,
)

WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class ImportantRow(QWidget):
    """一行：日期 · 标题 · 倒计时 · 删除。"""

    delete_requested = Signal(int)

    def __init__(self, item: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.item = item
        self.setFixedHeight(46)
        colors = self._colors()
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(9)

        day_label = QLabel(important.friendly_day(str(item.get("date") or "")))
        day_label.setObjectName("Small")
        day_label.setFixedWidth(52)
        row.addWidget(day_label)

        title = ElidedLabel(str(item.get("title") or "重要日"))
        title.setStyleSheet(
            f"color: {colors['TEXT']}; background: transparent;")
        row.addWidget(title, 1)

        left = int(item.get("days") or 0)
        tone = theme.ACCENT if left <= 7 else theme.MUTED
        row.addWidget(Pill(item.get("left") or important.label(left), tone,
                           theme.soft(tone, 0.14), point_size=7.8, height=22))

        delete_button = IconButton(
            icons.DELETE, size=26, glyph_size=10, color=theme.MUTED,
            hover_color=theme.DANGER, hover_bg=theme.soft(theme.DANGER, 0.14),
            tip="删除这条重要日")
        delete_button.clicked.connect(
            lambda: self.delete_requested.emit(int(item.get("id") or 0)))
        row.addWidget(delete_button)

    @staticmethod
    def _colors() -> Dict[str, str]:
        mode = "dark" if theme.effective_mode() == "dark" else "light"
        return theme.PALETTES.get(mode) or theme.PALETTES["light"]

    def paintEvent(self, event):
        from PySide6.QtGui import QColor, QPainter, QPen

        from .widgets import rounded_path

        colors = self._colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(0, 1, 0, -1)
        painter.fillPath(rounded_path(rect, 9), QColor(colors["CARD_ALT"]))
        painter.setPen(QPen(QColor(colors["BORDER"]), 1.0))
        painter.drawPath(rounded_path(rect, 9))


class ImportantDialog(QDialog):
    """重要日清单 + 新增表单。"""

    changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("DanLume · 重要日")
        self.resize(560, 520)

        colors = ImportantRow._colors()
        self.setStyleSheet(f"QDialog {{ background: {colors['CARD']}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("重要日")
        title.setObjectName("H2")
        head.addWidget(title)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Muted")
        head.addWidget(self.subtitle)
        head.addStretch(1)
        root.addLayout(head)

        hint = QLabel("期中考试、答辩、作业截止这类日子放这里；日历页顶部会显示倒计时。")
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QHBoxLayout()
        form.setSpacing(8)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("名称，例如：期中考试")
        self.title_edit.setFixedHeight(34)
        form.addWidget(self.title_edit, 3)
        self.date_edit = QLineEdit()
        self.date_edit.setPlaceholderText("日期：11-05 / 11月5日 / 明天 / +14")
        self.date_edit.setFixedHeight(34)
        self.date_edit.returnPressed.connect(self._add)
        form.addWidget(self.date_edit, 2)
        add_button = SoftButton("添加", "primary", height=34, min_width=76)
        add_button.clicked.connect(self._add)
        form.addWidget(add_button)
        root.addLayout(form)

        self.list_card, _body, self.list_layout = make_scroll()
        root.addWidget(self.list_card, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        bottom.addWidget(self.status, 1)
        close_button = SoftButton("关闭", "ghost", height=32, min_width=76)
        close_button.clicked.connect(self.accept)
        bottom.addWidget(close_button)
        root.addLayout(bottom)

        self.reload()

    # -------------------------------------------------- 数据
    def reload(self) -> None:
        clear_layout(self.list_layout)
        items = important.list_all(self.store)
        upcoming = [item for item in items if item["days"] >= 0]
        self.subtitle.setText(
            f"共 {len(items)} 条，接下来还有 {len(upcoming)} 条"
            if items else "还没有重要日")
        if not items:
            empty = QLabel("还没有重要日：上面填个名字和日子，点「添加」就行。")
            empty.setObjectName("Tiny")
            empty.setWordWrap(True)
            self.list_layout.addWidget(empty)
        else:
            for item in items:
                row = ImportantRow(item)
                row.delete_requested.connect(self._delete)
                self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)

    def _add(self) -> None:
        title = self.title_edit.text().strip()
        day_text = self.date_edit.text().strip()
        if not title:
            self.status.setText("先写个名称")
            return
        day = important.parse_day(day_text)
        if not day:
            self.status.setText("日期看不懂：可以写 11-05、11月5日、明天、+14")
            return
        important.add(self.store, title, day)
        self.title_edit.clear()
        self.date_edit.clear()
        self.status.setText(f"已添加：{title} · {important.friendly_day(day)}")
        self.reload()
        self.changed.emit()

    def _delete(self, item_id: int) -> None:
        important.delete(self.store, item_id)
        self.status.setText("已删除")
        self.reload()
        self.changed.emit()

    # -------------------------------------------------- 便捷添加
    def prefill(self, title: str = "", day: str = "") -> None:
        if title:
            self.title_edit.setText(title)
        if day:
            self.date_edit.setText(day)
