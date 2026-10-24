"""日期选择、时间选择与颜色选择控件。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QDate, QPoint, QRectF, QTime, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from . import icons, theme
from .config import COLOR_ORDER, EVENT_COLORS, REMIND_OPTIONS, WEEKDAY_SHORT
from .theme import ACCENT, ACCENT2, BORDER, FAINT, INPUT_BG, MUTED, TEXT
from .utils import date_cn, month_label
from .widgets import Select, ShadowPanel, SoftButton, icon_font, rounded_path


class MiniCalendar(QWidget):
    """紧凑型月历，用于日期选择弹层。"""

    picked = Signal(QDate)

    HEADER = 40
    WEEK = 24
    CELL_W = 40
    CELL_H = 36
    MARGIN = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self._month = QDate(QDate.currentDate().year(), QDate.currentDate().month(), 1)
        self._selected = QDate.currentDate()
        self._hover: Optional[QDate] = None
        self._hover_arrow: Optional[str] = None
        self.setFixedSize(
            self.MARGIN * 2 + self.CELL_W * 7,
            self.HEADER + self.WEEK + self.CELL_H * 6,
        )
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # -------------------------------------------------- 状态
    def set_date(self, value: QDate) -> None:
        if not value.isValid():
            return
        self._selected = QDate(value)
        self._month = QDate(value.year(), value.month(), 1)
        self.update()

    def month(self) -> QDate:
        return QDate(self._month)

    def _grid_start(self) -> QDate:
        first = QDate(self._month.year(), self._month.month(), 1)
        return first.addDays(1 - first.dayOfWeek())

    def _shift_month(self, delta: int) -> None:
        self._month = self._month.addMonths(delta)
        self.update()

    # -------------------------------------------------- 绘制
    def _arrow_rects(self):
        y = 8
        prev = QRectF(self.width() - 64, y, 26, 26)
        nxt = QRectF(self.width() - 34, y, 26, 26)
        return prev, nxt

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        title_font = QFont(self.font())
        title_font.setPointSizeF(10.5)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(TEXT))
        painter.drawText(
            QRectF(8, 4, self.width() - 80, self.HEADER - 8),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            month_label(self._month),
        )

        prev_rect, next_rect = self._arrow_rects()
        for name, rect, glyph in (
            ("prev", prev_rect, icons.CHEVRON_LEFT),
            ("next", next_rect, icons.CHEVRON_RIGHT),
        ):
            if self._hover_arrow == name:
                painter.fillPath(rounded_path(rect, 8), QColor(0, 0, 0, 12))
            painter.setFont(icon_font(10))
            painter.setPen(QColor(MUTED))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), glyph)

        painter.setFont(icon_font(9))
        painter.setPen(QColor(MUTED))
        for index, label in enumerate(WEEKDAY_SHORT):
            rect = QRectF(
                self.MARGIN + index * self.CELL_W,
                self.HEADER,
                self.CELL_W,
                self.WEEK,
            )
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), label)

        grid_start = self._grid_start()
        number_font = QFont(self.font())
        number_font.setPointSizeF(9.2)
        today = QDate.currentDate()

        for index in range(42):
            day = grid_start.addDays(index)
            row, col = divmod(index, 7)
            cell = QRectF(
                self.MARGIN + col * self.CELL_W + 3,
                self.HEADER + self.WEEK + row * self.CELL_H + 3,
                self.CELL_W - 6,
                self.CELL_H - 6,
            )
            in_month = day.month() == self._month.month() and day.year() == self._month.year()
            is_selected = day == self._selected
            is_today = day == today

            if self._hover == day and not is_selected:
                painter.fillPath(rounded_path(cell, 9), QColor(0, 0, 0, 10))
            if is_selected:
                gradient = QLinearGradient(cell.topLeft(), cell.bottomRight())
                gradient.setColorAt(0.0, theme.appearance().accent_deep_q())
                gradient.setColorAt(1.0, theme.appearance().accent_light_q())
                painter.fillPath(rounded_path(cell, 9), QBrush(gradient))
            elif is_today:
                painter.setPen(QPen(theme.appearance().accent_q(), 1.3))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(rounded_path(cell.adjusted(0.5, 0.5, -0.5, -0.5), 9))

            if is_selected:
                color = theme.appearance().on_accent_q()
            elif is_today:
                color = theme.appearance().accent_q()
            elif in_month:
                color = QColor(TEXT_SOFT)
            else:
                color = QColor(FAINT)
            painter.setFont(number_font)
            painter.setPen(color)
            painter.drawText(cell, int(Qt.AlignmentFlag.AlignCenter), str(day.day()))

    # -------------------------------------------------- 交互
    def _cell_at(self, pos: QPoint) -> Optional[QDate]:
        if pos.y() < self.HEADER + self.WEEK:
            return None
        grid_start = self._grid_start()
        col = int((pos.x() - self.MARGIN) // self.CELL_W)
        row = int((pos.y() - self.HEADER - self.WEEK) // self.CELL_H)
        if 0 <= col < 7 and 0 <= row < 6:
            return grid_start.addDays(row * 7 + col)
        return None

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        prev_rect, next_rect = self._arrow_rects()
        arrow = None
        if prev_rect.contains(event.position()):
            arrow = "prev"
        elif next_rect.contains(event.position()):
            arrow = "next"
        cell = self._cell_at(pos)
        if arrow != self._hover_arrow or cell != self._hover:
            self._hover_arrow = arrow
            self._hover = cell
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = None
        self._hover_arrow = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        prev_rect, next_rect = self._arrow_rects()
        if prev_rect.contains(pos):
            self._shift_month(-1)
            return
        if next_rect.contains(pos):
            self._shift_month(1)
            return
        day = self._cell_at(event.position().toPoint())
        if day is not None:
            self._selected = day
            if day.month() != self._month.month() or day.year() != self._month.year():
                self._month = QDate(day.year(), day.month(), 1)
            self.update()
            self.picked.emit(QDate(day))

    def keyPressEvent(self, event):
        key = event.key()
        delta = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Up: -7,
            Qt.Key.Key_Down: 7,
        }.get(key)
        if delta is not None:
            self._selected = self._selected.addDays(delta)
            self._month = QDate(self._selected.year(), self._selected.month(), 1)
            self.update()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.picked.emit(QDate(self._selected))
            return
        if key == Qt.Key.Key_PageUp:
            self._shift_month(-1)
            return
        if key == Qt.Key.Key_PageDown:
            self._shift_month(1)
            return
        super().keyPressEvent(event)


class CalendarPopup(QWidget):
    """圆角日期弹层。"""

    picked = Signal(QDate)

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        panel = ShadowPanel(self, radius=16, shadow=10, glow=False)
        outer.addWidget(panel)
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(12, 8, 12, 12)
        inner.setSpacing(6)
        self.calendar = MiniCalendar(panel)
        inner.addWidget(self.calendar)
        footer = QHBoxLayout()
        footer.setContentsMargins(4, 0, 4, 0)
        footer.setSpacing(8)
        hint = QLabel("选择日期后自动填入")
        hint.setObjectName("Tiny")
        footer.addWidget(hint)
        footer.addStretch(1)
        today_button = SoftButton("今天", "subtle", height=28, point_size=8.8)
        today_button.clicked.connect(lambda: self._pick(QDate.currentDate()))
        footer.addWidget(today_button)
        inner.addLayout(footer)
        self.calendar.picked.connect(self._pick)

    def set_date(self, value: QDate) -> None:
        self.calendar.set_date(value)

    def _pick(self, value: QDate) -> None:
        self.picked.emit(QDate(value))
        self.close()

    def show_at(self, field: QWidget) -> None:
        self.adjustSize()
        origin = field.mapToGlobal(QPoint(0, field.height() + 8))
        screen = field.screen()
        available = screen.availableGeometry() if screen else self.screen().availableGeometry()
        x = min(max(available.left() + 8, origin.x()), available.right() - self.width() - 8)
        y = origin.y()
        if y + self.height() > available.bottom() - 8:
            y = field.mapToGlobal(QPoint(0, 0)).y() - self.height() - 8
        self.move(x, y)
        self.show()
        self.raise_()
        self.calendar.setFocus()


class DateField(QWidget):
    """看起来像输入框的日期选择控件。"""

    changed = Signal(QDate)

    def __init__(self, value: Optional[QDate] = None, parent=None):
        super().__init__(parent)
        self._date = QDate(value) if value and value.isValid() else QDate.currentDate()
        self._hover = False
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._popup: Optional[CalendarPopup] = None

    def date(self) -> QDate:
        return QDate(self._date)

    def set_date(self, value: QDate) -> None:
        if value.isValid():
            self._date = QDate(value)
            self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_popup()
        super().mousePressEvent(event)

    def open_popup(self) -> None:
        popup = CalendarPopup(self)
        popup.set_date(self._date)
        popup.picked.connect(self._apply)
        self._popup = popup
        popup.show_at(self)

    def _apply(self, value: QDate) -> None:
        self.set_date(value)
        self.changed.emit(QDate(value))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded_path(rect, 10)
        painter.fillPath(path, QColor(theme.appearance().input_hex()))
        border = (
            theme.appearance().accent_q()
            if self.hasFocus()
            else QColor(theme.hover_wash(0.10) if self._hover
                         else theme.appearance().border_hex())
        )
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)

        painter.setFont(icon_font(12))
        painter.setPen(QColor(MUTED))
        painter.drawText(
            QRectF(13, 0, 18, rect.height()), int(Qt.AlignmentFlag.AlignCenter), icons.CALENDAR
        )

        painter.setFont(self.font())
        painter.setPen(QColor(TEXT))
        painter.drawText(
            QRectF(38, 0, rect.width() - 66, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            date_cn(self._date),
        )

        painter.setFont(icon_font(8))
        painter.setPen(QColor(FAINT))
        painter.drawText(
            QRectF(rect.width() - 26, 0, 16, rect.height()),
            int(Qt.AlignmentFlag.AlignCenter),
            icons.CHEVRON_DOWN,
        )

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.open_popup()
            return
        super().keyPressEvent(event)


class TimeField(Select):
    """可输入 / 可下拉的时间选择。"""

    def __init__(self, value: Optional[QTime] = None, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setFixedHeight(38)
        self.setMinimumWidth(96)
        for hour in range(24):
            for minute in range(0, 60, 15):
                self.addItem(f"{hour:02d}:{minute:02d}")
        line = self.lineEdit()
        if line is not None:
            line.setMaxLength(5)
            line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_time(value or QTime.currentTime())
        if line is not None:
            line.editingFinished.connect(self._normalize)

    def set_time(self, value: QTime) -> None:
        if value.isValid():
            self.setEditText(value.toString("HH:mm"))

    def time(self) -> QTime:
        parsed = self._parse(self.currentText())
        return parsed if parsed is not None else QTime(9, 0)

    @staticmethod
    def _parse(text: str) -> Optional[QTime]:
        text = text.strip().replace("：", ":")
        parts = text.split(":")
        if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
            return None
        hour, minute = int(parts[0]), int(parts[1])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return QTime(hour, minute)
        return None

    def _normalize(self) -> None:
        parsed = self._parse(self.currentText())
        if parsed is None:
            self.setEditText("09:00")
        else:
            self.setEditText(parsed.toString("HH:mm"))


class ColorDots(QWidget):
    """日程颜色选择。"""

    changed = Signal(str)

    DOT = 20
    GAP = 28

    def __init__(self, color: str = "violet", parent=None):
        super().__init__(parent)
        self._color = color if color in EVENT_COLORS else "violet"
        self._hover = -1
        self.setFixedHeight(32)
        self.setMinimumWidth(self.GAP * len(COLOR_ORDER))
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        if color in EVENT_COLORS:
            self._color = color
            self.update()

    def _dot_rect(self, index: int) -> QRectF:
        return QRectF(2 + index * self.GAP, 6, self.DOT, self.DOT)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ring = QColor(MUTED)
        ring.setAlphaF(0.55)
        selected_ring = QColor(TEXT)
        selected_ring.setAlphaF(0.75)
        for index, key in enumerate(COLOR_ORDER):
            rect = self._dot_rect(index)
            color = QColor(theme.event_base(key))
            if index == self._hover and key != self._color:
                painter.setPen(QPen(ring, 1.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect.adjusted(-2, -2, 2, 2))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(rect)
            if key == self._color:
                painter.setPen(QPen(selected_ring, 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect.adjusted(-3.4, -3.4, 3.4, 3.4))

    def mouseMoveEvent(self, event):
        index = int((event.position().x() - 2) // self.GAP)
        index = index if 0 <= index < len(COLOR_ORDER) else -1
        if index != self._hover:
            self._hover = index
            self.setToolTip(
                EVENT_COLORS[COLOR_ORDER[index]][1] if index >= 0 else ""
            )
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        index = int((event.position().x() - 2) // self.GAP)
        if 0 <= index < len(COLOR_ORDER):
            self._color = COLOR_ORDER[index]
            self.update()
            self.changed.emit(self._color)


def reminder_combo() -> Select:
    combo = Select()
    combo.setFixedHeight(38)
    for minutes, label in REMIND_OPTIONS:
        combo.addItem(label, minutes)
    for index in range(combo.count()):
        if combo.itemData(index) == 15:
            combo.setCurrentIndex(index)
            break
    return combo
