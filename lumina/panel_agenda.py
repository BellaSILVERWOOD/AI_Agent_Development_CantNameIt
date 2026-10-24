"""右侧「最近日程」侧栏：可以按「最近 n 条」或「最近 n 天」看日程，能折叠。

设置都存进 store：
  agenda_open / agenda_mode / agenda_count / agenda_days
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from PySide6.QtCore import QDate, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from . import icons, theme
from .widgets import (
    Card, IconButton, SoftButton, clear_layout, icon_font, make_scroll, rounded_path,
)

EXPANDED_WIDTH = 252
COLLAPSED_WIDTH = 46
ROW_HEIGHT = 50


def _qdate(value: str) -> Optional[QDate]:
    parsed = QDate.fromString(value or "", "yyyy-MM-dd")
    return parsed if parsed.isValid() else None


def _date_label(value: str) -> str:
    parsed = _qdate(value)
    if parsed is None:
        return value or "未定日期"
    today = QDate.currentDate()
    week = "一二三四五六日"[parsed.dayOfWeek() - 1]
    stamp = parsed.toString("M-d")
    if parsed == today:
        return f"今天 · {stamp} 周{week}"
    if parsed == today.addDays(1):
        return f"明天 · {stamp} 周{week}"
    return f"{stamp} 周{week}"


def _clock(value: str) -> str:
    text = (value or "").strip()
    return text[:5] if len(text) >= 5 else text


def _end_datetime(event: Dict) -> Optional[datetime]:
    parsed = _qdate(event.get("date", ""))
    if parsed is None:
        return None
    try:
        hour, minute = (int(p) for p in _clock(event.get("end_time", "")) .split(":"))
    except Exception:
        hour, minute = 23, 59
    return datetime(parsed.year(), parsed.month(), parsed.day(), hour, minute)


# ------------------------------------------------------------------ 单行
class AgendaRow(QWidget):
    """一条日程：左侧色条 + 时间 + 标题，点击打开编辑窗口。"""

    clicked = Signal(int)

    def __init__(self, event: Dict, primary: str = "count", parent=None):
        super().__init__(parent)
        self.item = event
        self._primary = primary
        self._hover = False
        self.setFixedHeight(ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # -------------------------------------------------- 交互
    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit(int(self.item.get("id") or 0))
            return
        super().mouseReleaseEvent(event)

    # -------------------------------------------------- 绘制
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0, 1, -2, -1)
        if self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.hover_wash(0.07))
            painter.drawPath(rounded_path(rect, 9))

        done = bool(self.item.get("done"))
        bar = QColor(theme.event_color(self.item.get("color", "violet")))
        if done:
            bar.setAlpha(110)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bar)
        painter.drawPath(rounded_path(
            QRectF(rect.left() + 4, rect.top() + 10, 3, rect.height() - 20), 1.5))

        left = rect.left() + 16
        width = max(40, rect.width() - 26)

        # 第一行：时间（或日期）
        base = QFont(self.font())
        base.setPixelSize(10)
        base.setWeight(QFont.Weight.Medium)
        painter.setFont(base)
        painter.setPen(QPen(QColor(theme.MUTED if not done else theme.FAINT)))
        start = _clock(self.item.get("start_time", ""))
        end = _clock(self.item.get("end_time", ""))
        if self._primary == "days":
            top = f"{start}–{end}" if start or end else "全天"
        else:
            top = f"{_date_label(self.item.get('date', ''))}  {start}"
        metrics = QFontMetrics(base)
        painter.drawText(int(left), int(rect.top() + 17),
                         metrics.elidedText(top, Qt.TextElideMode.ElideRight, width))

        # 第二行：标题
        title_font = QFont(self.font())
        title_font.setPixelSize(12)
        title_font.setWeight(QFont.Weight.Medium)
        font = title_font
        if done:
            font.setStrikeOut(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor(theme.FAINT if done else theme.TEXT)))
        metrics = QFontMetrics(font)
        painter.drawText(int(left), int(rect.top() + 34),
                         metrics.elidedText(str(self.item.get("title") or "（无标题）"),
                                            Qt.TextElideMode.ElideRight, width))


class _DateHeading(QWidget):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self.setFixedHeight(26)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(theme.MUTED)))
        painter.drawText(8, 17, self._text)


# ------------------------------------------------------------------ 侧栏
class AgendaSidebar(QWidget):
    """右侧边栏：最近 n 条 / 最近 n 天。"""

    event_clicked = Signal(int)
    edit_requested = Signal(int)
    width_changed = Signal(int)

    def __init__(self, store, parent=None, collapsible: bool = True):
        super().__init__(parent)
        self.store = store
        self._collapsible = bool(collapsible)
        self._open = (store.get_setting("agenda_open", "1") or "1") != "0"
        self._today_only = False
        mode = (store.get_setting("agenda_mode", "count") or "count").strip()
        self._mode = mode if mode in ("count", "days") else "count"
        self._count = self._read_int("agenda_count", 6, 1, 30)
        self._days = self._read_int("agenda_days", 7, 1, 60)

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._build()
        self._apply_open_state()
        self.refresh()

    # -------------------------------------------------- 构建
    def _read_int(self, key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(float(self.store.get_setting(key, str(default)) or default))
        except Exception:
            value = default
        return max(low, min(high, value))

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 10, 8, 10)
        root.setSpacing(8)

        # 折叠状态下的窄条
        self.rail = QWidget()
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.setSpacing(6)
        self.expand_button = IconButton(icons.CHEVRON_LEFT, size=30, glyph_size=12,
                                        tip="展开「最近日程」")
        self.expand_button.clicked.connect(lambda: self.set_open(True))
        rail_layout.addWidget(self.expand_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.rail_badge = QLabel("")
        self.rail_badge.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.rail_badge.setObjectName("Tiny")
        self.rail_badge.setStyleSheet(
            f"color: {theme.ACCENT}; font-size: 12px; font-weight: 600;")
        self.rail_badge.setToolTip("今天还没结束的日程")
        rail_layout.addWidget(self.rail_badge)
        rail_layout.addStretch(1)
        root.addWidget(self.rail)

        # 展开状态下的内容
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(4)
        self.title = QLabel("最近日程")
        self.title.setObjectName("PanelTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.collapse_button = IconButton(icons.CHEVRON_RIGHT, size=26, glyph_size=11,
                                          tip="收起侧栏")
        self.collapse_button.clicked.connect(lambda: self.set_open(False))
        head.addWidget(self.collapse_button)
        body.addLayout(head)

        # 视角切换
        switch = QHBoxLayout()
        switch.setSpacing(4)
        self.count_button = SoftButton("", "subtle", height=26, min_width=64,
                                       point_size=8.4)
        self.days_button = SoftButton("", "subtle", height=26, min_width=64,
                                      point_size=8.4)
        self.count_button.clicked.connect(lambda: self.set_mode("count"))
        self.days_button.clicked.connect(lambda: self.set_mode("days"))
        switch.addWidget(self.count_button)
        switch.addWidget(self.days_button)
        switch.addStretch(1)
        body.addLayout(switch)

        # n 的加减
        stepper = QHBoxLayout()
        stepper.setSpacing(2)
        self.minus_button = IconButton(icons.CHEVRON_LEFT, size=22, glyph_size=9,
                                       tip="少一些")
        self.minus_button.clicked.connect(lambda: self._step(-1))
        self.value_label = QLabel("")
        self.value_label.setObjectName("Tiny")
        self.value_label.setMinimumWidth(52)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.plus_button = IconButton(icons.CHEVRON_RIGHT, size=22, glyph_size=9,
                                      tip="多一些")
        self.plus_button.clicked.connect(lambda: self._step(1))
        stepper.addStretch(1)
        stepper.addWidget(self.minus_button)
        stepper.addWidget(self.value_label)
        stepper.addWidget(self.plus_button)
        stepper.addStretch(1)
        body.addLayout(stepper)

        self.scroll, _host, self.list_layout = make_scroll()
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(self.scroll, 1)

        self.detail_card = self._build_detail_card()
        body.insertWidget(1, self.detail_card)

        self.empty = QLabel("接下来没有日程")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.empty)

        root.addWidget(self.body, 1)

    # -------------------------------------------------- 外观
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawLine(self.width() - 1, 6, self.width() - 1, self.height() - 6)
        if not self._open and self._collapsible:
            # 折叠后竖排说明，避免右边只剩一个孤零零的箭头
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.translate(self.width() - 16, 176)
            painter.rotate(-90)
            font = QFont(self.font())
            font.setPixelSize(10)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QPen(QColor(theme.MUTED)))
            painter.drawText(0, 0, "最近日程")
            painter.restore()

    # -------------------------------------------------- 详情
    def show_event_detail(self, event: Dict) -> None:
        """单击日程卡片时，在侧栏顶部显示这一条的详情。"""
        self._detail_event = dict(event or {})
        self.detail_title.setText(str(event.get("title") or "（无标题）"))
        when = f"{_date_label(event.get('date', ''))}  {_clock(event.get('start_time', ''))}"
        end = _clock(event.get("end_time", ""))
        if end:
            when += f"-{end}"
        self.detail_when.setText(when)
        description = str(event.get("description") or "").strip()
        self.detail_desc.setText(description or "（没有更多说明）")
        self.detail_card.setVisible(True)

    def hide_event_detail(self) -> None:
        self._detail_event = None
        self.detail_card.setVisible(False)

    def _edit_detail(self) -> None:
        event = getattr(self, "_detail_event", None)
        if event:
            self.edit_requested.emit(int(event.get("id") or 0))

    def _build_detail_card(self) -> QWidget:
        holder = Card(radius=12, fill=theme.CARD_ALT, border=theme.BORDER,
                      padding=10, spacing=6)
        head = QHBoxLayout()
        head.setSpacing(4)
        label = QLabel("日程详情")
        label.setObjectName("Tiny")
        head.addWidget(label)
        head.addStretch(1)
        edit = IconButton(icons.EDIT, size=24, glyph_size=10, tip="编辑这条日程")
        edit.clicked.connect(self._edit_detail)
        head.addWidget(edit)
        close = IconButton(icons.CLOSE, size=24, glyph_size=10, tip="关闭详情")
        close.clicked.connect(self.hide_event_detail)
        head.addWidget(close)
        holder.body.addLayout(head)

        self.detail_title = QLabel("")
        self.detail_title.setWordWrap(True)
        title_font = QFont(self.font())
        title_font.setPixelSize(12)
        title_font.setWeight(QFont.Weight.DemiBold)
        self.detail_title.setFont(title_font)
        self.detail_title.setStyleSheet(f"color: {theme.TEXT};")
        holder.body.addWidget(self.detail_title)

        self.detail_when = QLabel("")
        self.detail_when.setObjectName("Tiny")
        holder.body.addWidget(self.detail_when)

        self.detail_desc = QLabel("")
        self.detail_desc.setWordWrap(True)
        self.detail_desc.setStyleSheet(
            f"color: {theme.TEXT_SOFT}; font-size: 11px;")
        holder.body.addWidget(self.detail_desc)
        holder.setVisible(False)
        return holder

    # -------------------------------------------------- 窄栏模式
    def set_today_only(self, value: bool) -> None:
        """在右侧窄栏里只显示「今天的日程」，模式开关也一并隐藏。"""
        value = bool(value)
        if value == self._today_only:
            return
        self._today_only = value
        for widget in (getattr(self, "count_button", None),
                       getattr(self, "days_button", None),
                       getattr(self, "value_label", None),
                       getattr(self, "minus_button", None),
                       getattr(self, "plus_button", None)):
            if widget is not None:
                try:
                    widget.setVisible(not value)
                except Exception:
                    pass
        self.refresh()

    def is_today_only(self) -> bool:
        return self._today_only

    # -------------------------------------------------- 状态
    def set_open(self, value: bool) -> None:
        if self._open == bool(value):
            return
        self._open = bool(value)
        self.store.set_setting("agenda_open", "1" if self._open else "0")
        self._apply_open_state()
        if self._open:
            self.refresh()

    def _apply_open_state(self) -> None:
        if not self._collapsible:
            # 放进右侧 dock 时由 dock 统一控制宽度/收起；内部一律按“展开”处理，
            # 否则 refresh() 会因为 _open=False 而不生成任何行（右侧栏就空了）
            self._open = True
            self.rail.setVisible(False)
            self.body.setVisible(True)
            try:
                self.collapse_button.setVisible(False)
            except Exception:
                pass
            self.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Expanding)
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            try:
                self.width_changed.emit(self.width())
            except Exception:
                pass
            return
        self.rail.setVisible(not self._open)
        self.body.setVisible(self._open)
        self.setFixedWidth(EXPANDED_WIDTH if self._open else COLLAPSED_WIDTH)
        try:
            self.width_changed.emit(self.width())
        except Exception:
            pass

    def set_mode(self, mode: str) -> None:
        if mode not in ("count", "days") or mode == self._mode:
            return
        self._mode = mode
        self.store.set_setting("agenda_mode", mode)
        self.refresh()

    def _step(self, delta: int) -> None:
        if self._mode == "count":
            self._count = max(1, min(30, self._count + delta))
            self.store.set_setting("agenda_count", str(self._count))
        else:
            self._days = max(1, min(60, self._days + delta))
            self.store.set_setting("agenda_days", str(self._days))
        self.refresh()

    # -------------------------------------------------- 数据
    def _upcoming(self, limit: int) -> List[Dict]:
        today = date.today()
        start = today.strftime("%Y-%m-%d")
        end = (today + timedelta(days=120)).strftime("%Y-%m-%d")
        now = datetime.now()
        picked: List[Dict] = []
        for item in self.store.events_between(start, end):
            finished = _end_datetime(item)
            if finished is not None and finished < now:
                continue
            picked.append(item)
            if len(picked) >= limit:
                break
        return picked

    def _in_days(self, days: int) -> Dict[str, List[Dict]]:
        today = date.today()
        start = today.strftime("%Y-%m-%d")
        end = (today + timedelta(days=days - 1)).strftime("%Y-%m-%d")
        grouped: Dict[str, List[Dict]] = {}
        for item in self.store.events_between(start, end):
            grouped.setdefault(item["date"], []).append(item)
        return grouped

    def refresh(self) -> None:
        clear_layout(self.list_layout)

        self.count_button.setText("按条")
        self.days_button.setText("按天")
        self.count_button.setKind("primary" if self._mode == "count" else "subtle")
        self.days_button.setKind("primary" if self._mode == "days" else "subtle")
        self.value_label.setText(
            f"{self._count} 条" if self._mode == "count" else f"{self._days} 天")
        self.minus_button.setToolTip("少一些")
        self.plus_button.setToolTip("多一些")

        rows = 0
        if self._today_only:
            today = date.today().strftime("%Y-%m-%d")
            items = self.store.events_on(today)
            self.list_layout.addWidget(_DateHeading(_date_label(today)))
            if items:
                rows += 1
                for item in items:
                    row = AgendaRow(item, "days")
                    row.clicked.connect(self.event_clicked.emit)
                    self.list_layout.addWidget(row)
                    rows += 1
            self.list_layout.addStretch(1)
            self.empty.setVisible(len(items) == 0)
            self.rail_badge.setText(str(self._today_left()))
            return
        if self._open or not self._collapsible:
            if self._mode == "count":
                for item in self._upcoming(self._count):
                    row = AgendaRow(item, "count")
                    row.clicked.connect(self.event_clicked.emit)
                    self.list_layout.addWidget(row)
                    rows += 1
            else:
                grouped = self._in_days(self._days)
                for day in sorted(grouped):
                    heading = _DateHeading(_date_label(day))
                    self.list_layout.addWidget(heading)
                    rows += 1
                    for item in grouped[day]:
                        row = AgendaRow(item, "days")
                        row.clicked.connect(self.event_clicked.emit)
                        self.list_layout.addWidget(row)
                        rows += 1
            self.list_layout.addStretch(1)
        self.empty.setVisible(self._open and rows == 0)
        self.rail_badge.setText(str(self._today_left()))

    def _today_left(self) -> int:
        """折叠态小角标：今天还没结束的日程条数。"""
        now = datetime.now()
        today = date.today().strftime("%Y-%m-%d")
        total = 0
        try:
            for item in self.store.events_on(today):
                finished = _end_datetime(item)
                if finished is not None and finished < now:
                    continue
                total += 1
        except Exception:
            return 0
        return total
