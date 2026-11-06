"""日历页：自绘月视图 + 当日日程列表。"""
from __future__ import annotations

from contextlib import suppress
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QDate, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import icons, theme
from .config import PERIOD_TIMES, WEEKDAY_SHORT
from .theme import (
    ACCENT, ACCENT2, BORDER, BORDER_SOFT, CARD, CARD_ALT, DANGER, FAINT, MUTED,
    SUCCESS, TEXT, TEXT_SOFT,
)
from .utils import date_short_cn, event_range, iso, month_label, relative_label, weekday_cn
from .widgets import (
    Card, ConfirmDialog, ElidedLabel, EmptyState, IconButton, Pill, RoundCheck,
    SoftButton, clear_layout, make_scroll,
)


def _monday_of(day: QDate) -> QDate:
    """这一周的周一。"""
    return QDate(day).addDays(1 - int(day.dayOfWeek()))


def _grid_start(month_first: QDate) -> QDate:
    first = QDate(month_first.year(), month_first.month(), 1)
    return first.addDays(1 - first.dayOfWeek())


def _month_range(month_first: QDate) -> tuple[str, str]:
    first = QDate(month_first.year(), month_first.month(), 1)
    last = first.addDays(first.daysInMonth() - 1)
    return iso(first), iso(last)


class MonthGrid(QWidget):
    """六行七列的月历网格。"""

    dateSelected = Signal(QDate)
    dateActivated = Signal(QDate)

    HEADER = 32

    def __init__(self, parent=None):
        super().__init__(parent)
        today = QDate.currentDate()
        self._month = QDate(today.year(), today.month(), 1)
        self._selected = QDate(today)
        self._events: Dict[str, List[Dict[str, Any]]] = {}
        self._hover: Optional[QDate] = None
        self.setMinimumHeight(420)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # -------------------------------------------------- 状态
    def set_month(self, value: QDate) -> None:
        self._month = QDate(value.year(), value.month(), 1)
        self.update()

    def set_selected(self, value: QDate) -> None:
        if value.isValid():
            self._selected = QDate(value)
            self.update()

    def set_events(self, index: Dict[str, List[Dict[str, Any]]]) -> None:
        self._events = index or {}
        self.update()

    def _cell_rect(self, index: int) -> QRectF:
        width = self.width() / 7.0
        height = (self.height() - self.HEADER) / 6.0
        row, col = divmod(index, 7)
        return QRectF(
            col * width + 3,
            self.HEADER + row * height + 3,
            width - 6,
            height - 6,
        )

    def _cell_at(self, pos) -> Optional[QDate]:
        if pos.y() < self.HEADER:
            return None
        width = self.width() / 7.0
        height = (self.height() - self.HEADER) / 6.0
        if width <= 0 or height <= 0:
            return None
        col = int(pos.x() // width)
        row = int((pos.y() - self.HEADER) // height)
        if 0 <= col < 7 and 0 <= row < 6:
            return _grid_start(self._month).addDays(row * 7 + col)
        return None

    # -------------------------------------------------- 绘制
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = self.width() / 7.0
        height = (self.height() - self.HEADER) / 6.0
        if width <= 0 or height <= 0:
            return

        week_font = QFont(self.font())
        week_font.setPointSizeF(8.4)
        week_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(week_font)
        painter.setPen(QColor(MUTED))
        for column, label in enumerate(WEEKDAY_SHORT):
            painter.drawText(
                QRectF(column * width, 0, width, self.HEADER),
                int(Qt.AlignmentFlag.AlignCenter),
                label,
            )

        grid_start = _grid_start(self._month)
        today = QDate.currentDate()
        number_font = QFont(self.font())
        number_font.setPointSizeF(10.2)
        number_font.setWeight(QFont.Weight.DemiBold)

        for index in range(42):
            day = grid_start.addDays(index)
            cell = self._cell_rect(index)
            if cell.height() <= 0:
                continue
            in_month = day.month() == self._month.month() and day.year() == self._month.year()
            selected = day == self._selected
            is_today = day == today
            hovered = day == self._hover
            path = _rounded(cell, 12)

            if selected:
                painter.fillPath(path, theme.appearance().accent_q(0.10))
                painter.setPen(QPen(theme.appearance().accent_q(0.34), 1.1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
            elif hovered:
                painter.fillPath(path, theme.hover_wash(0.06))

            # 日期数字居中显示
            number_rect = QRectF(cell.left(), cell.top() + 7, cell.width(), 28)
            if is_today:
                circle = QRectF(
                    cell.center().x() - 14, cell.top() + 8, 28, 28
                )
                from PySide6.QtGui import QBrush, QLinearGradient

                gradient = QLinearGradient(circle.topLeft(), circle.bottomRight())
                gradient.setColorAt(0.0, theme.appearance().accent_deep_q())
                gradient.setColorAt(1.0, theme.appearance().accent_q())
                painter.fillPath(_rounded(circle, 14), QBrush(gradient))
                painter.setFont(number_font)
                painter.setPen(theme.appearance().on_accent_q())
                # 用字形实际包围盒居中，避免数字看起来偏上
                metrics = QFontMetrics(number_font)
                text = str(day.day())
                tight = metrics.tightBoundingRect(text)
                painter.drawText(
                    QPointF(
                        circle.center().x() - tight.center().x(),
                        circle.center().y() - tight.center().y(),
                    ),
                    text,
                )
            else:
                painter.setFont(number_font)
                if selected:
                    color = QColor(TEXT)
                elif in_month:
                    color = QColor(TEXT_SOFT)
                else:
                    color = QColor(FAINT)
                painter.setPen(color)
                painter.drawText(
                    number_rect,
                    int(Qt.AlignmentFlag.AlignCenter),
                    str(day.day()),
                )

            # 事件用彩色圆点表示，避免小格子里文字拥挤
            events = self._events.get(iso(day), [])
            if events and cell.height() > 54:
                dot = 5.2
                gap = 4.6
                shown = events[:3]
                total = len(shown) * dot + (len(shown) - 1) * gap
                x = cell.center().x() - total / 2
                y = cell.bottom() - 15
                painter.setPen(Qt.PenStyle.NoPen)
                for index, item in enumerate(shown):
                    color = QColor(_event_color(item.get("color")))
                    if not in_month:
                        color.setAlphaF(0.30)
                    painter.setBrush(color)
                    painter.drawEllipse(
                        QRectF(x + index * (dot + gap), y, dot, dot)
                    )
                if len(events) > 3:
                    more_font = QFont(self.font())
                    more_font.setPointSizeF(7.2)
                    more_font.setWeight(QFont.Weight.DemiBold)
                    painter.setFont(more_font)
                    painter.setPen(QColor(MUTED if in_month else FAINT))
                    painter.drawText(
                        QRectF(cell.right() - 30, cell.bottom() - 22, 24, 14),
                        int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                        f"+{len(events) - 3}",
                    )

    # -------------------------------------------------- 交互
    def mouseMoveEvent(self, event):
        cell = self._cell_at(event.position())
        if cell != self._hover:
            self._hover = cell
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if cell is not None
                else Qt.CursorShape.ArrowCursor
            )
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # 点完日期要把键盘焦点留在日历上，否则方向键切日期不会生效
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        cell = self._cell_at(event.position())
        if cell is None:
            return
        if cell.month() != self._month.month() or cell.year() != self._month.year():
            self._month = QDate(cell.year(), cell.month(), 1)
        self._selected = cell
        self.update()
        self.dateSelected.emit(QDate(cell))

    def mouseDoubleClickEvent(self, event):
        cell = self._cell_at(event.position())
        if cell is not None:
            self.dateActivated.emit(QDate(cell))

    def keyPressEvent(self, event):
        key = event.key()
        delta = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Up: -7,
            Qt.Key.Key_Down: 7,
        }.get(key)
        if delta is not None:
            day = self._selected.addDays(delta)
            self._selected = day
            self._month = QDate(day.year(), day.month(), 1)
            self.update()
            self.dateSelected.emit(QDate(day))
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.dateActivated.emit(QDate(self._selected))
            return
        super().keyPressEvent(event)


def _rounded(rect: QRectF, radius: float):
    from .widgets import rounded_path

    return rounded_path(rect, radius)


def _event_color(key: Optional[str]) -> str:
    return theme.event_base(str(key))


class WeekGrid(QWidget):
    """周视图：行=节次（1–14），列=周一到周日，日程按时间落格子。

    比手机端多显示细节：标题、地点、起止时间、分组颜色条；跨节次的日程会占多行。
    """

    dateSelected = Signal(QDate)
    dateActivated = Signal(QDate)
    eventActivated = Signal(int)

    HEADER = 44
    TIME_COL = 62

    def __init__(self, parent=None):
        super().__init__(parent)
        self._monday = _monday_of(QDate.currentDate())
        self._selected = QDate.currentDate()
        self._events: Dict[str, List[Dict[str, Any]]] = {}
        self._periods = [tuple(item) for item in PERIOD_TIMES]
        self.setMinimumHeight(520)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -------------------------------------------------- 状态
    def set_week(self, monday: QDate) -> None:
        self._monday = _monday_of(monday)
        self.update()

    def set_selected(self, value: QDate) -> None:
        if value.isValid():
            self._selected = QDate(value)
            self.update()

    def set_events(self, index: Dict[str, List[Dict[str, Any]]]) -> None:
        self._events = index or {}
        self.update()

    def set_periods(self, periods) -> None:
        self._periods = [tuple(item) for item in (periods or PERIOD_TIMES)]
        self.update()

    def monday(self) -> QDate:
        return QDate(self._monday)

    # -------------------------------------------------- 几何
    def _col_width(self) -> float:
        return max(1.0, (self.width() - self.TIME_COL) / 7.0)

    def _row_height(self) -> float:
        return max(1.0, (self.height() - self.HEADER) / max(1, len(self._periods)))

    def _day_at(self, pos) -> Optional[QDate]:
        if pos.x() < self.TIME_COL or pos.y() < self.HEADER:
            return None
        column = int((pos.x() - self.TIME_COL) // self._col_width())
        if not 0 <= column < 7:
            return None
        return self._monday.addDays(column)

    def _period_at(self, pos) -> Optional[int]:
        if pos.y() < self.HEADER:
            return None
        row = int((pos.y() - self.HEADER) // self._row_height())
        if 0 <= row < len(self._periods):
            return row
        return None

    @staticmethod
    def _minutes(text: str) -> Optional[int]:
        try:
            hour, minute = str(text or "").strip()[:5].split(":")
            return int(hour) * 60 + int(minute)
        except Exception:
            return None

    def _rows_for(self, event: Dict[str, Any]) -> tuple:
        """把一条日程换成 (起始行, 跨几行)。

        节次之间有课间空档（例如第 8 节 15:25–16:10、第 9 节 16:20–17:05），
        「16:15 开始的日程」不落在任何一节**内部**。所以这里不能用
        「开始时间在某节之内」来判行，而是：
        * 起始行 = **第一个还没结束的节次**（finish > start）；
        * 结束行 = **最后一个已经开始的节次**（begin < end）。
        以前用「包含」判断，遇到空档里的时间 first 会是 None，被兜到第 1 行，
        于是下午 16:15 的日程画在了早上第 1 行 —— 用户看到的「时间对不上」就是这个。
        """
        start = self._minutes(event.get("start_time"))
        end = self._minutes(event.get("end_time"))
        if start is None:
            return 0, 1
        spans = []
        for index, (number, begin, finish) in enumerate(self._periods):
            begin_min = self._minutes(begin)
            finish_min = self._minutes(finish)
            if begin_min is None:
                begin_min = 0
            if finish_min is None:
                finish_min = begin_min + 45
            spans.append((index, begin_min, finish_min))

        first = None
        for index, begin_min, finish_min in spans:
            if finish_min > start:
                first = index
                break
        if first is None:
            first = len(spans) - 1          # 比最后一节还晚：贴最后一行

        last = first
        if end is not None:
            for index, begin_min, finish_min in spans:
                if begin_min < end:
                    last = index
                else:
                    break
            last = max(first, last)
        return first, last - first + 1

    # -------------------------------------------------- 绘制
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(CARD))
        column_width = self._col_width()
        row_height = self._row_height()
        today = QDate.currentDate()

        # 表头：周一…周日 + 日期
        head_font = QFont(self.font())
        head_font.setPointSizeF(9.0)
        head_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(head_font)
        for column in range(7):
            day = self._monday.addDays(column)
            x = self.TIME_COL + column * column_width
            is_today = day == today
            if is_today:
                painter.fillPath(
                    _rounded(QRectF(x + 4, 4, column_width - 8, self.HEADER - 8), 10),
                    theme.appearance().accent_q(0.12))
            painter.setPen(QColor(theme.appearance().accent if is_today else MUTED))
            painter.drawText(QRectF(x, 6, column_width, 18),
                             int(Qt.AlignmentFlag.AlignCenter), WEEKDAY_SHORT[column])
            date_font = QFont(head_font)
            date_font.setPointSizeF(8.2)
            painter.setFont(date_font)
            painter.setPen(QColor(theme.appearance().accent if is_today else TEXT_SOFT))
            painter.drawText(QRectF(x, 22, column_width, 18),
                             int(Qt.AlignmentFlag.AlignCenter),
                             "%d/%d" % (day.month(), day.day()))
            painter.setFont(head_font)

        # 时间列 + 网格线
        time_font = QFont(self.font())
        time_font.setPointSizeF(7.8)
        for row, (number, begin, finish) in enumerate(self._periods):
            y = self.HEADER + row * row_height
            painter.setPen(QPen(QColor(BORDER_SOFT), 1.0))
            painter.drawLine(QPointF(self.TIME_COL, y), QPointF(self.width(), y))
            painter.setFont(time_font)
            painter.setPen(QColor(MUTED))
            painter.drawText(QRectF(4, y + 2, 22, 16),
                             int(Qt.AlignmentFlag.AlignCenter), str(number))
            painter.setPen(QColor(FAINT if theme.is_dark() else MUTED))
            # 时间列：行高够就分两行写起止时间，太挤时只写开始时间
            line_height = QFontMetrics(time_font).height()
            room = row_height - 6
            if room >= line_height * 2:
                painter.drawText(QRectF(24, y + 2, self.TIME_COL - 26, room),
                                 int(Qt.AlignmentFlag.AlignLeft) |
                                 int(Qt.AlignmentFlag.AlignTop),
                                 "%s\n%s" % (begin, finish))
            else:
                painter.drawText(QRectF(24, y + 2, self.TIME_COL - 26, 16),
                                 int(Qt.AlignmentFlag.AlignLeft) |
                                 int(Qt.AlignmentFlag.AlignVCenter), begin)

        for column in range(1, 7):
            x = self.TIME_COL + column * column_width
            painter.setPen(QPen(QColor(BORDER_SOFT), 1.0))
            painter.drawLine(QPointF(x, self.HEADER),
                             QPointF(x, self.height()))

        # 说明：这里**不再**给整列铺底色（以前铺的是选中列，用户会问「怎么周三有
        # 浅色底色」）。现在「今天」只用表头那个圆角高亮 + 主题色日期，
        # 选中那天用表头下面一根短横线，整列保持干净。
        offset = self._monday.daysTo(self._selected)
        if 0 <= offset < 7:
            x = self.TIME_COL + offset * column_width
            bar_width = min(26.0, column_width * 0.36)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.appearance().accent_q(0.75))
            painter.drawRoundedRect(
                QRectF(x + (column_width - bar_width) / 2, self.HEADER - 7,
                       bar_width, 3), 1.5, 1.5)

        # 日程块
        label_font = QFont(self.font())
        label_font.setPointSizeF(8.6)
        label_font.setWeight(QFont.Weight.DemiBold)
        meta_font = QFont(self.font())
        meta_font.setPointSizeF(7.6)
        for column in range(7):
            day = self._monday.addDays(column)
            items = self._events.get(iso(day)) or []
            layout = self._day_layout(
                items, column_width, row_height,
                left=self.TIME_COL + column * column_width)
            for block_info in layout:
                item = block_info["item"]
                block = block_info["rect"]
                width = block.width()
                height = block.height()
                x = block.left()
                y = block.top()
                if height < 24 or width < 18:
                    continue
                color = QColor(_event_color(item.get("color")))
                soft, _hover, ink = theme.event_tint(str(item.get("color", "violet")))
                done = bool(item.get("done"))
                painter.fillPath(_rounded(block, 8), QColor(soft))
                painter.setPen(QPen(QColor(theme.mix(ink, QColor(CARD), 0.82).name()), 1.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(_rounded(block, 8))
                painter.fillPath(_rounded(QRectF(x, y, 3.5, height), 1.8), color)

                inner = block.adjusted(7, 4, -3, -3)
                narrow = width < 46
                title = str(item.get("title") or "（未命名）")
                title_font = QFont(label_font)
                title_font.setStrikeOut(done)
                painter.setFont(title_font)
                painter.setPen(QColor("#9AA396" if done else theme.TEXT))
                metrics = QFontMetrics(title_font)
                title_rect = QRectF(inner.left(), inner.top(), inner.width(), 0)
                text = title
                if metrics.horizontalAdvance(text) > title_rect.width():
                    while text and metrics.horizontalAdvance(text + "…") > title_rect.width():
                        text = text[:-1]
                    text = (text + "…") if text else "…"
                detail_lines = 1
                if height >= 46:
                    detail_lines = 2
                if height >= 64:
                    detail_lines = 3
                painter.drawText(QRectF(inner.left(), inner.top(), inner.width(),
                                        metrics.height() * detail_lines),
                                 int(Qt.AlignmentFlag.AlignLeft) |
                                 int(Qt.AlignmentFlag.AlignTop) |
                                 int(Qt.TextFlag.TextWordWrap),
                                 text)
                meta_top = inner.top() + metrics.height() * detail_lines + 2
                painter.setFont(meta_font)
                painter.setPen(QColor(theme.MUTED))
                pieces = []
                start_time = str(item.get("start_time") or "")[:5]
                end_time = str(item.get("end_time") or "")[:5]
                if start_time:
                    pieces.append(f"{start_time}–{end_time}" if end_time else start_time)
                location = str(item.get("location") or "").strip()
                if location:
                    pieces.append(location[:18])
                remaining = inner.bottom() - meta_top
                # 并排之后这一列可能很窄：宽度不够就别画时间，
                # 不然会出现「08:0」「15:25–1」这种半截字
                if (pieces and remaining >= 12 and not narrow
                        and inner.width() >= 62):
                    painter.drawText(
                        QRectF(inner.left(), meta_top, inner.width(),
                               min(remaining, QFontMetrics(meta_font).height() * 2)),
                        int(Qt.AlignmentFlag.AlignLeft) |
                        int(Qt.AlignmentFlag.AlignTop) |
                        int(Qt.TextFlag.TextWordWrap), " · ".join(pieces))

    # -------------------------------------------------- 冲突并列
    def _day_layout(self, items: List[Dict[str, Any]], column_width: float,
                    row_height: float, left: float = 0.0) -> List[Dict[str, Any]]:
        """一天里时间冲突的日程**并排**显示，互相不覆盖。

        * 先按行区间（`_rows_for` 的起止行）把「有交叠」的日程聚成一组；
        * 组内按开始时间排，依次塞进第一个空着的「道」（lane）；
        * 每组占满整列宽度，组内每条 = 列宽 / 道数（留一点缝）。
        没有冲突的日程自然是一组一条，仍然是整宽 —— 跟以前一样。
        """
        blocks: List[Dict[str, Any]] = []
        for item in items or []:
            first, span = self._rows_for(item)
            blocks.append({"item": item, "first": int(first), "span": int(span),
                           "last": int(first) + max(1, int(span)) - 1})
        if not blocks:
            return []
        blocks.sort(key=lambda row: (row["first"], row["last"]))

        # ① 聚成「互相重叠」的组
        groups: List[List[Dict[str, Any]]] = []
        for block in blocks:
            if groups and block["first"] <= max(
                    item["last"] for item in groups[-1]):
                groups[-1].append(block)
            else:
                groups.append([block])

        # ② 组内分道
        gap = 3.0
        for group in groups:
            lanes: List[int] = []          # 每道当前用到第几行
            for block in sorted(group, key=lambda row: (row["first"],
                                                        row["last"])):
                placed = False
                for index, lane_end in enumerate(lanes):
                    if lane_end < block["first"]:
                        lanes[index] = block["last"]
                        block["lane"] = index
                        placed = True
                        break
                if not placed:
                    block["lane"] = len(lanes)
                    lanes.append(block["last"])
            count = max(1, len(lanes))
            usable = max(24.0, column_width - 6.0)
            width = max(18.0, (usable - gap * (count - 1)) / count)
            for block in group:
                block["width"] = width
                block["count"] = count

        result = []
        for block in blocks:
            x = float(left) + 3.0
            lane = int(block.get("lane", 0))
            width = float(block.get("width") or (column_width - 6))
            rect = QRectF(x + lane * (width + gap),
                          self.HEADER + block["first"] * row_height + 3,
                          width,
                          max(20.0, block["span"] * row_height - 6))
            block["rect"] = rect
            result.append(block)
        return result

    # -------------------------------------------------- 交互
    def mousePressEvent(self, event):
        day = self._day_at(event.position())
        if day is None:
            return
        self._selected = QDate(day)
        for item in self._events.get(iso(day)) or []:
            hit = self._block_rect(day, item)
            if hit is not None and hit.contains(event.position()):
                try:
                    self.eventActivated.emit(int(item.get("id") or 0))
                except Exception:
                    pass
                break
        self.dateSelected.emit(QDate(day))
        self.update()

    def mouseDoubleClickEvent(self, event):
        day = self._day_at(event.position())
        if day is not None:
            self.dateActivated.emit(QDate(day))

    def mouseMoveEvent(self, event):
        """冲突并排之后块会变窄，悬停给出完整信息（标题 · 时间 · 地点）。"""
        day = self._day_at(event.position())
        if day is None:
            self.setToolTip("")
            return
        offset = max(0, self._monday.daysTo(day))
        column_width = self._col_width()
        layout = self._day_layout(
            self._events.get(iso(day)) or [], column_width, self._row_height(),
            left=self.TIME_COL + offset * column_width)
        for block in layout:
            if not block["rect"].contains(event.position()):
                continue
            item = block["item"]
            bits = [str(item.get("title") or "")]
            start = str(item.get("start_time") or "")[:5]
            end = str(item.get("end_time") or "")[:5]
            if start:
                bits.append(f"{start}–{end}" if end else start)
            if str(item.get("location") or "").strip():
                bits.append(str(item.get("location")).strip())
            self.setToolTip(" · ".join(bit for bit in bits if bit))
            return
        self.setToolTip("")

    def _block_rect(self, day: QDate, item: Dict[str, Any]):
        """点哪块算哪块 —— 冲突并列之后必须用同一份排版结果。"""
        offset = self._monday.daysTo(day)
        if not 0 <= offset < 7:
            return None
        items = self._events.get(iso(day)) or []
        column_width = self._col_width()
        layout = self._day_layout(
            items, column_width, self._row_height(),
            left=self.TIME_COL + offset * column_width)
        for block in layout:
            same_id = int(block["item"].get("id") or 0) == int(item.get("id") or 0)
            if block["item"] is item or (
                    same_id and block["item"].get("title") == item.get("title")):
                return block["rect"]
        return None


class EventCard(Card):
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    done_toggled = Signal(int, bool)
    view_requested = Signal(int)

    def __init__(self, event: Dict[str, Any], parent=None):
        color = QColor(_event_color(event.get("color")))
        soft, hover, ink = theme.event_tint(str(event.get("color", "violet")))
        border = theme.mix(ink, QColor(theme.CARD), 0.82).name()
        super().__init__(
            parent,
            radius=14,
            fill=soft.name(),
            border=border,
            hoverable=True,
            hover_fill=hover.name(),
            bar=color,
            padding=0,
            spacing=0,
        )
        self.event_data = event  # 不能命名为 event，会覆盖 QObject.event()
        event_id = int(event.get("id", 0))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = self.body
        layout.setContentsMargins(17, 11, 10, 11)
        layout.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(6)
        time_label = QLabel(event_range(event))
        time_font = QFont(self.font())
        time_font.setPointSizeF(8.7)
        time_font.setWeight(QFont.Weight.DemiBold)
        time_label.setFont(time_font)
        time_label.setStyleSheet(f"color: {ink.name()}; background: transparent;")
        self._time_label = time_label
        self._ink = QColor(ink)
        top.addWidget(time_label)
        top.addStretch(1)
        self.check = RoundCheck(bool(event.get("done")), 20, tip="标记为完成")
        self.check.toggled.connect(self._on_done)
        top.addWidget(self.check)
        edit_button = IconButton(icons.EDIT, size=28, glyph_size=12, tip="编辑日程",
                                 color=MUTED, hover_color=TEXT)
        edit_button.clicked.connect(lambda: self.edit_requested.emit(event_id))
        top.addWidget(edit_button)
        delete_button = IconButton(
            icons.DELETE, size=28, glyph_size=12, tip="删除日程",
            color=MUTED, hover_color=DANGER, hover_bg=theme.soft(DANGER, 0.14),
        )
        delete_button.clicked.connect(lambda: self.delete_requested.emit(event_id))
        top.addWidget(delete_button)
        layout.addLayout(top)

        done = bool(event.get("done"))
        title = ElidedLabel(str(event.get("title", "")))
        title_font = QFont(self.font())
        title_font.setPointSizeF(10.2)
        title_font.setWeight(QFont.Weight.DemiBold)
        title_font.setStrikeOut(done)
        title.setFont(title_font)
        title.setStyleSheet(
            f"color: {'#9AA396' if done else TEXT}; background: transparent;"
            f" padding-right: 8px;"
        )
        self._title_label = title
        layout.addWidget(title)

        description = str(event.get("description", "")).strip()
        # 多行描述（教师/地点各一行）压成一行，避免把卡片撑高后被裁掉
        if description:
            description = " · ".join(
                part.strip() for part in description.splitlines() if part.strip())
        if description:
            desc_label = ElidedLabel(description)
            desc_font = QFont(self.font())
            desc_font.setPointSizeF(8.9)
            desc_label.setFont(desc_font)
            desc_label.setStyleSheet(
                f"color: {QColor(FAINT).name() if done else MUTED}; background: transparent;"
            )
            self._desc_label = desc_label
            layout.addWidget(desc_label)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        remind = event.get("remind_minutes")
        if remind is not None:
            labels = {0: "准时提醒", 5: "提前 5 分钟", 15: "提前 15 分钟",
                      30: "提前 30 分钟", 60: "提前 1 小时", 1440: "提前 1 天"}
            reminder = Pill(f"提醒 · {labels.get(remind, f'提前 {remind} 分钟')}",
                            MUTED, "", point_size=7.8)
            chips.addWidget(reminder)
        if done:
            self._done_pill = Pill("已完成", SUCCESS, theme.soft(SUCCESS, 0.14),
                                   point_size=7.8)
            chips.addWidget(self._done_pill)
        chips.addStretch(1)
        layout.addLayout(chips)

    # -------------------------------------------------- 主题
    def on_theme_changed(self) -> None:
        """换主题后重算这张卡片的配色（柔和底色 / 描边 / 文字 / 小标签）。"""
        data = self.event_data
        key = str(data.get("color", "violet"))
        soft, hover, ink = theme.event_tint(key)
        self._ink = QColor(ink)
        self._fill = QColor(soft)
        self._hover_fill = QColor(hover)
        self._border = QColor(theme.mix(ink, QColor(theme.CARD), 0.82).name())
        self._bar = QColor(_event_color(key))
        self._theme_rev = theme.revision()
        done = bool(data.get("done"))
        with suppress(Exception):
            self._time_label.setStyleSheet(
                f"color: {ink.name()}; background: transparent;")
        with suppress(Exception):
            self._title_label.setStyleSheet(
                f"color: {'#9AA396' if done else TEXT}; background: transparent;"
                f" padding-right: 8px;")
        with suppress(Exception):
            self._desc_label.setStyleSheet(
                f"color: {QColor(FAINT).name() if done else MUTED};"
                f" background: transparent;")
        with suppress(Exception):
            self._done_pill.set_colors(SUCCESS, theme.soft(SUCCESS, 0.14))
        self.update()

    def _on_done(self, checked: bool) -> None:
        self.done_toggled.emit(int(self.event_data.get("id", 0)), bool(checked))

    # 单击看详情、双击改详情：用 250ms 的延时区分两者
    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        timer = getattr(self, "_click_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(250)
            timer.timeout.connect(self._emit_view)
            self._click_timer = timer
        timer.start()

    def _emit_view(self) -> None:
        self.view_requested.emit(int(self.event_data.get("id", 0)))

    def mouseDoubleClickEvent(self, event):
        timer = getattr(self, "_click_timer", None)
        if timer is not None:
            timer.stop()
        self.edit_requested.emit(int(self.event_data.get("id", 0)))
        event.accept()


class DayPanel(Card):
    edit_event = Signal(int)
    view_event = Signal(int)
    new_event = Signal(QDate)
    data_changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(
            parent, radius=18, fill=CARD, border=BORDER, padding=16, spacing=12,
        )
        self.store = store
        self._date = QDate.currentDate()
        self.setMinimumWidth(330)

        header = QHBoxLayout()
        header.setSpacing(8)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.date_label = QLabel("")
        self.date_label.setObjectName("H2")
        title_row.addWidget(self.date_label)
        self.today_pill = Pill(
            "今天",
            theme.appearance().accent_q().name(),
            theme.soft(theme.appearance().accent, 0.13),
            point_size=7.8,
        )
        title_row.addWidget(self.today_pill)
        title_row.addStretch(1)
        text_box.addLayout(title_row)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("Muted")
        text_box.addWidget(self.meta_label)
        header.addLayout(text_box, 1)
        add_button = SoftButton("新建", "ghost", glyph=icons.ADD, height=32, point_size=9.0)
        add_button.clicked.connect(lambda: self.new_event.emit(QDate(self._date)))
        header.addWidget(add_button, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(header)
        self.body.addWidget(_divider())

        self.list_scroll, _body, self.list_layout = make_scroll()
        self.body.addWidget(self.list_scroll, 1)

        self.bottom_button = SoftButton("添加日程", "ghost", glyph=icons.ADD, height=36)
        self.bottom_button.clicked.connect(lambda: self.new_event.emit(QDate(self._date)))
        self.body.addWidget(self.bottom_button)

    def set_date(self, value: QDate) -> None:
        self._date = QDate(value)
        self.refresh()

    def date(self) -> QDate:
        return QDate(self._date)

    def refresh(self) -> None:
        events = self.store.events_on(iso(self._date))
        self.date_label.setText(date_short_cn(self._date))
        today = self._date == QDate.currentDate()
        self.today_pill.setVisible(today)
        relative = relative_label(self._date)
        pieces = [weekday_cn(self._date)]
        if relative and not today:
            pieces.append(relative)
        pieces.append(f"{len(events)} 个日程")
        done_count = sum(1 for item in events if item.get("done"))
        if done_count:
            pieces.append(f"{done_count} 个已完成")
        self.meta_label.setText(" · ".join(pieces))

        clear_layout(self.list_layout)
        if not events:
            empty = EmptyState(
                icons.CALENDAR,
                "这一天还没有安排",
                "留白也是一种计划，或点击下方按钮添加一个新日程",
                glyph_size=24,
                circle=68,
            )
            self.list_layout.addWidget(empty)
        else:
            for item in events:
                card = EventCard(item)
                card.edit_requested.connect(self.edit_event)
                card.view_requested.connect(self.view_event)
                card.delete_requested.connect(self._delete_event)
                card.done_toggled.connect(self._toggle_done)
                self.list_layout.addWidget(card)
        self.list_layout.addStretch(1)

    def _toggle_done(self, event_id: int, done: bool) -> None:
        self.store.set_done(event_id, done)
        self.refresh()
        self.data_changed.emit()

    def _delete_event(self, event_id: int) -> None:
        event = self.store.get_event(event_id)
        title = event.get("title", "") if event else ""
        dialog = ConfirmDialog(
            self.window(),
            "删除日程",
            f"确定删除「{title}」吗？删除后无法恢复。",
            confirm_text="删除日程",
        )
        if dialog.exec() and dialog.confirmed:
            self.store.delete_event(event_id)
            self.refresh()
            self.data_changed.emit()


def _divider() -> QWidget:
    from .widgets import Divider

    return Divider()


def apply_group_colors(store, index):
    """把日程的显示颜色替换成所属分组的颜色（同组同色）。"""
    try:
        colors = store.group_colors()
    except Exception:
        return index
    if not colors:
        return index
    for items in index.values():
        for item in items:
            group_id = item.get("group_id")
            if not group_id:
                continue
            color = colors.get(int(group_id))
            if color:
                item["color"] = color
    return index


class CalendarPage(QWidget):
    new_event_requested = Signal(QDate)
    edit_event_requested = Signal(int)
    view_event_requested = Signal(int)
    data_changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        today = QDate.currentDate()
        self._month = QDate(today.year(), today.month(), 1)
        self._selected = QDate(today)
        self._view = "month"
        self._week_monday = _monday_of(today)
        try:
            stored = str(store.get_setting("calendar_view", "month") or "month")
            self._view = "week" if stored == "week" else "month"
        except Exception:
            self._view = "month"

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 18, 26, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.month_title = QLabel(month_label(self._month))
        self.month_title.setObjectName("H1")
        self.month_sub = QLabel("")
        self.month_sub.setObjectName("Muted")
        title_box.addWidget(self.month_title)
        title_box.addWidget(self.month_sub)
        header.addLayout(title_box)
        header.addStretch(1)

        prev_button = IconButton(icons.CHEVRON_LEFT, size=32, glyph_size=11,
                                 color=MUTED, tip="上一页")
        prev_button.clicked.connect(lambda: self._shift_month(-1))
        next_button = IconButton(icons.CHEVRON_RIGHT, size=32, glyph_size=11,
                                 color=MUTED, tip="下一页")
        next_button.clicked.connect(lambda: self._shift_month(1))
        today_button = SoftButton("今天", "ghost", height=32, point_size=9.0)
        today_button.clicked.connect(self._go_today)
        month_nav_host = QWidget()
        month_nav_row = QHBoxLayout(month_nav_host)
        month_nav_row.setContentsMargins(0, 0, 0, 0)
        month_nav_row.setSpacing(4)
        month_nav_row.addWidget(prev_button)
        month_nav_row.addWidget(today_button)
        month_nav_row.addWidget(next_button)
        self._month_nav_host = month_nav_host

        # 月 / 周 两种视图
        view_host = QWidget()
        view_row = QHBoxLayout(view_host)
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(4)
        self.month_view_button = SoftButton("月", "ghost", height=32, min_width=48,
                                            point_size=9.0)
        self.month_view_button.setToolTip("月视图：整月的日历格")
        self.month_view_button.clicked.connect(lambda: self.set_view("month"))
        view_row.addWidget(self.month_view_button)
        self.week_view_button = SoftButton("周", "ghost", height=32, min_width=48,
                                           point_size=9.0)
        self.week_view_button.setToolTip("周视图：按节次排的课程表，能看到每条日程的细节")
        self.week_view_button.clicked.connect(lambda: self.set_view("week"))
        view_row.addWidget(self.week_view_button)
        self._view_host = view_host
        header.addWidget(view_host)
        header.addWidget(month_nav_host)
        new_button = SoftButton("新建日程", "primary", glyph=icons.ADD, height=36,
                                min_width=116)
        new_button.clicked.connect(lambda: self.new_event_requested.emit(QDate(self._selected)))
        header.addSpacing(8)
        header.addWidget(new_button)
        root.addLayout(header)

        # -------------------------------------------------- 重要日倒计时
        self.countdown_host = QWidget()
        self.countdown_row = QHBoxLayout(self.countdown_host)
        self.countdown_row.setContentsMargins(0, 0, 0, 0)
        self.countdown_row.setSpacing(8)
        root.addWidget(self.countdown_host)

        content = QHBoxLayout()
        content.setSpacing(14)

        calendar_card = Card(radius=18, fill=CARD, border=BORDER, padding=12, spacing=4)
        self.grid = MonthGrid()
        self.grid.set_month(self._month)
        self.grid.set_selected(self._selected)
        self.grid.dateSelected.connect(self._on_date_selected)
        self.grid.dateActivated.connect(self.new_event_requested)
        calendar_card.body.addWidget(self.grid, 1)
        hint = QLabel("双击日期可快速新建日程 · 使用方向键切换日期")
        hint.setObjectName("Tiny")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        calendar_card.body.addWidget(hint)
        self._calendar_card = calendar_card
        content.addWidget(calendar_card, 3)

        self.day_panel = DayPanel(store)
        self.day_panel.new_event.connect(self.new_event_requested)
        self.day_panel.edit_event.connect(self.edit_event_requested)
        self.day_panel.view_event.connect(self.view_event_requested)
        self.day_panel.data_changed.connect(self._on_data_changed)
        self._day_panel_container = self.day_panel
        content.addWidget(self.day_panel, 2)
        content_host = QWidget()
        content_host.setLayout(content)
        self._content_host = content_host

        # 周视图：一张卡片里放表格（行=节次，列=周一到周日）
        week_card = Card(radius=18, fill=CARD, border=BORDER, padding=12, spacing=4)
        self.week_grid = WeekGrid()
        self.week_grid.set_week(self._week_monday)
        self.week_grid.set_selected(self._selected)
        self.week_grid.dateSelected.connect(self._on_date_selected)
        self.week_grid.dateActivated.connect(self.new_event_requested)
        self.week_grid.eventActivated.connect(self.view_event_requested)
        week_card.body.addWidget(self.week_grid, 1)
        week_hint = QLabel("点格子看当天日程 · 双击空白处新建 · 点日程块看详情")
        week_hint.setObjectName("Tiny")
        week_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        week_card.body.addWidget(week_hint)
        self._week_card = week_card

        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(content_host)
        self._view_stack.addWidget(week_card)
        root.addWidget(self._view_stack, 1)
        self.refresh()
        self.set_view(self._view)

    # -------------------------------------------------- 导航
    def showEvent(self, event):
        super().showEvent(event)
        # 进入日历时把焦点给日历，方向键立刻可用
        try:
            if not self.grid.hasFocus():
                self.grid.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:
            pass

    def selected_date(self) -> QDate:
        return QDate(self._selected)

    def select_date(self, value: QDate) -> None:
        if not value.isValid():
            return
        self._selected = QDate(value)
        self._month = QDate(value.year(), value.month(), 1)
        self._week_monday = _monday_of(value)
        self.refresh()

    def view(self) -> str:
        """当前是月视图还是周视图。"""
        return getattr(self, "_view", "month")

    def set_view(self, kind: str) -> None:
        """切换月 / 周视图（记住选择）。"""
        self._view = "week" if str(kind) == "week" else "month"
        try:
            self.store.set_setting("calendar_view", self._view)
        except Exception:
            pass
        try:
            self._view_stack.setCurrentIndex(1 if self._view == "week" else 0)
            self.month_view_button.setKind(
                "primary" if self._view == "month" else "ghost")
            self.week_view_button.setKind(
                "primary" if self._view == "week" else "ghost")
        except Exception:
            pass
        self.refresh()

    def _shift_month(self, delta: int) -> None:
        if self.view() == "week":
            self._week_monday = self._week_monday.addDays(7 * int(delta))
            self._selected = self._selected.addDays(7 * int(delta))
            self._month = QDate(self._selected.year(), self._selected.month(), 1)
            self.refresh()
            return
        target = self._month.addMonths(delta)
        day = min(self._selected.day(), target.daysInMonth())
        self._month = QDate(target.year(), target.month(), 1)
        self._selected = QDate(target.year(), target.month(), day)
        self._week_monday = _monday_of(self._selected)
        self.refresh()

    def _go_today(self) -> None:
        today = QDate.currentDate()
        self._month = QDate(today.year(), today.month(), 1)
        self._selected = QDate(today)
        self._week_monday = _monday_of(today)
        self.refresh()

    def _on_date_selected(self, value: QDate) -> None:
        self._selected = QDate(value)
        self._month = QDate(value.year(), value.month(), 1)
        self._week_monday = _monday_of(value)
        self.refresh()

    def _on_data_changed(self) -> None:
        self.refresh()
        self.data_changed.emit()

    # -------------------------------------------------- 窄栏模式
    def set_compact(self, compact: bool) -> None:
        """在右侧窄列里：只留「最近 5 天」清单，月历网格和当日详情都收起来。"""
        compact = bool(compact)
        if getattr(self, "_compact", False) == compact:
            if compact:
                self._refresh_compact()
            return
        self._compact = compact
        try:
            self._calendar_card.setVisible(not compact)
            self.day_panel.setVisible(not compact)
            self._content_host.setVisible(not compact)
            self._month_nav_host.setVisible(not compact)
        except Exception:
            pass
        if compact:
            self._build_compact()
            self.compact_host.setVisible(True)
            try:
                self.month_title.setText("最近 5 天")
                self.month_sub.setText("")
            except Exception:
                pass
            self._refresh_compact()
        else:
            try:
                self.compact_host.setVisible(False)
            except Exception:
                pass
            self.refresh()

    def _build_compact(self) -> None:
        if getattr(self, "compact_host", None) is not None:
            return
        from .widgets import Card, clear_layout, make_scroll

        host = Card(radius=16, fill=CARD, border=BORDER, padding=12, spacing=8)
        self.compact_scroll, _body, self.compact_layout = make_scroll()
        host.body.addWidget(self.compact_scroll, 1)
        layout = self.layout()
        if layout is not None:
            layout.addWidget(host, 1)
        self.compact_host = host

    def _refresh_compact(self) -> None:
        from .widgets import clear_layout

        layout = getattr(self, "compact_layout", None)
        if layout is None:
            return
        clear_layout(layout)
        today = QDate.currentDate()
        for offset in range(5):
            day = today.addDays(offset)
            heading = QLabel("%s · %s" % (
                "今天" if offset == 0 else ("明天" if offset == 1
                                            else weekday_cn(day)),
                day.toString("M-d")))
            heading.setObjectName("Tiny")
            layout.addWidget(heading)
            events = self.store.events_on(iso(day))
            if not events:
                empty = QLabel("没有安排")
                empty.setObjectName("Tiny")
                layout.addWidget(empty)
                continue
            for item in events:
                row = QWidget()
                row.setCursor(Qt.CursorShape.PointingHandCursor)
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(2, 2, 2, 2)
                row_layout.setSpacing(6)
                stamp = QLabel((item.get("start_time") or "")[:5])
                stamp.setObjectName("Tiny")
                stamp.setFixedWidth(42)
                row_layout.addWidget(stamp)
                name = ElidedLabel(str(item.get("title") or ""))
                name.setStyleSheet(
                    f"color: {MUTED if item.get('done') else TEXT}; font-size: 12px;")
                row_layout.addWidget(name, 1)
                row.mouseReleaseEvent = (
                    lambda event, eid=int(item.get("id") or 0):
                    self.edit_event_requested.emit(eid))
                layout.addWidget(row)
        layout.addStretch(1)

    # -------------------------------------------------- 数据
    def refresh(self) -> None:
        grid_start = _grid_start(self._month)
        grid_end = grid_start.addDays(41)
        index = apply_group_colors(
            self.store, self.store.event_index(iso(grid_start), iso(grid_end))
        )
        self.grid.set_month(self._month)
        self.grid.set_selected(self._selected)
        self.grid.set_events(index)

        # 周视图：按这一周（周一→周日）取日程
        try:
            week_start = self._week_monday
            week_end = week_start.addDays(6)
            week_index = apply_group_colors(
                self.store,
                self.store.event_index(iso(week_start), iso(week_end)))
            self.week_grid.set_week(week_start)
            self.week_grid.set_selected(self._selected)
            self.week_grid.set_events(week_index)
        except Exception:
            pass

        month_start, month_end = _month_range(self._month)
        # 课程表是「上课时间」不是待办，不计入本月日程总数
        excluded = []
        try:
            from . import timetable

            gid = timetable.group_id(self.store)
            if gid is not None:
                excluded = [gid]
        except Exception:
            excluded = []
        counts = self.store.counts_between(month_start, month_end, excluded)
        if self.view() == "week":
            self._update_week_header()
        else:
            self.month_title.setText(month_label(self._month))
            if counts["total"]:
                self.month_sub.setText(
                    f"本月 {counts['total']} 个日程 · 已完成 {counts['done']} 个"
                )
            else:
                self.month_sub.setText("本月还没有日程，双击日历空白日期开始规划")

        self.day_panel.set_date(self._selected)
        if getattr(self, "_compact", False):
            self._refresh_compact()
        self.refresh_countdown()

    def _update_week_header(self) -> None:
        """周视图的标题：第几周 + 日期区间 + 本周日程数。"""
        start = self._week_monday
        end = start.addDays(6)
        try:
            week_number = start.weekNumber()[0]
        except Exception:
            week_number = 0
        self.month_title.setText("第 %d 周" % week_number)
        counts = {"total": 0, "done": 0}
        try:
            counts = self.store.counts_between(iso(start), iso(end))
        except Exception:
            pass
        span = "%d/%d – %d/%d" % (start.month(), start.day(), end.month(), end.day())
        if counts.get("total"):
            self.month_sub.setText(
                "%s · 本周 %d 个日程 · 已完成 %d 个"
                % (span, counts["total"], counts.get("done", 0)))
        else:
            self.month_sub.setText("%s · 本周还没有日程" % span)

    # -------------------------------------------------- 重要日倒计时
    def open_important(self, title: str = "", day: str = "") -> None:
        from .important_dialog import ImportantDialog

        dialog = ImportantDialog(self.store, self.window())
        if title or day:
            dialog.prefill(title, day)
        dialog.changed.connect(self.refresh_countdown)
        dialog.changed.connect(self.data_changed.emit)
        dialog.exec()
        self.refresh_countdown()

    def refresh_countdown(self) -> None:
        """顶部倒计时：最近 3 条重要日 + 「＋ 重要日」。"""
        from . import important

        clear_layout(self.countdown_row)
        try:
            items = important.upcoming(self.store, within_days=None, limit=3)
        except Exception:
            items = []
        if items:
            for item in items:
                left = int(item.get("days") or 0)
                text = f"{item.get('title')} {item.get('left')}"
                chip = SoftButton(text, "ghost", glyph=icons.CLOCK, height=30,
                                  point_size=8.6)
                chip.setToolTip(
                    f"{item.get('date')} · 距今天还有 {left} 天（点击管理重要日）")
                chip.clicked.connect(lambda _c=False: self.open_important())
                self.countdown_row.addWidget(chip)
        else:
            empty = QLabel("还没有重要日（期中考试、截止日可以放这里做倒计时）")
            empty.setObjectName("Tiny")
            self.countdown_row.addWidget(empty)
        self.countdown_row.addStretch(1)
        manage = SoftButton("＋ 重要日", "subtle", height=30, min_width=88,
                            point_size=8.6)
        manage.setToolTip("添加/删除重要日（倒计时）")
        manage.clicked.connect(lambda: self.open_important())
        self.countdown_row.addWidget(manage)
