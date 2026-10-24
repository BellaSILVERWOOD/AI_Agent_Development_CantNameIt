"""日历页：自绘月视图 + 当日日程列表。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QDate, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from . import icons, theme
from .config import WEEKDAY_SHORT
from .theme import (
    ACCENT, ACCENT2, BORDER, CARD, CARD_ALT, DANGER, FAINT, MUTED, SUCCESS, TEXT,
    TEXT_SOFT,
)
from .utils import date_short_cn, event_range, iso, month_label, relative_label, weekday_cn
from .widgets import (
    Card, ConfirmDialog, ElidedLabel, EmptyState, IconButton, Pill, RoundCheck,
    SoftButton, clear_layout, make_scroll,
)


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
            chips.addWidget(Pill("已完成", SUCCESS, theme.soft(SUCCESS, 0.14), point_size=7.8))
        chips.addStretch(1)
        layout.addLayout(chips)

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
                                 color=MUTED, tip="上个月")
        prev_button.clicked.connect(lambda: self._shift_month(-1))
        next_button = IconButton(icons.CHEVRON_RIGHT, size=32, glyph_size=11,
                                 color=MUTED, tip="下个月")
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

        root.addWidget(content_host, 1)
        self.refresh()

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
        self.refresh()

    def _shift_month(self, delta: int) -> None:
        target = self._month.addMonths(delta)
        day = min(self._selected.day(), target.daysInMonth())
        self._month = QDate(target.year(), target.month(), 1)
        self._selected = QDate(target.year(), target.month(), day)
        self.refresh()

    def _go_today(self) -> None:
        today = QDate.currentDate()
        self._month = QDate(today.year(), today.month(), 1)
        self._selected = QDate(today)
        self.refresh()

    def _on_date_selected(self, value: QDate) -> None:
        self._selected = QDate(value)
        self._month = QDate(value.year(), value.month(), 1)
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
