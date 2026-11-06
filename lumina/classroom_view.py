# -*- coding: utf-8 -*-
"""空教室的时间轴：一行一个教室，横轴是时间；占用画红色块（带课程名），空闲是绿的或空着。

用户要的样子（参考日历周视图）：
* 只选教学楼 → 列出这栋楼所有教室，可用/占用用色块长短表示时长；
* 再选教室 → 只显示这一间；
* 再选时间 → 只显示这个时间段里的安排；
* 只选教学楼 + 时间 → 列出这个时段这栋楼所有教室。

数据来自 ehall 页面的表格（列=节次/时间，行=教室），解析成
``{"headers": [...], "rooms": [{"name": ..., "cells": [{label, occupied}]}]}``。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from . import theme
from .config import PERIOD_TIMES
from .dashboard_widgets import WidgetCard
from .theme import BORDER, BORDER_SOFT, CARD, MUTED, TEXT, TEXT_SOFT
from .widgets import Select, SoftButton

NAME_COL = 132          # 左边的教室名列（「JB100」这种名字要放得下）
#: 一行教室的高度
ROW_HEIGHT = 32
#: 一页显示几间教室（再多就翻页，不截断也不在卡片里上下滚）
PAGE_ROWS = 10
HEADER = 30             # 顶部时间轴的高度
MIN_SLOT = 64           # 每个节次的最小宽度（不够就横向滚动）
#: 卡片里最多显示这么高，剩下的上下滚（30 间教室也不会把仪表板撑爆）
CARD_VIEW_HEIGHT = 360


def period_slots() -> List[Dict[str, Any]]:
    """把 1–14 节做成时间轴上的格子。"""
    slots = []
    for number, begin, finish in PERIOD_TIMES:
        slots.append({"number": int(number), "begin": str(begin),
                      "finish": str(finish)})
    return slots


def _looks_like_time(text: str) -> bool:
    """「08:00-08:45」「1」「第 2 节」「上午」：这些是时间标签，不是教室名。"""
    value = str(text or "").strip()
    if not value:
        return False
    if re.match(r"^\s*(?:第\s*)?\d{1,2}\s*[:：]\s*\d{2}", value):
        return True
    if re.match(r"^\s*(?:第\s*)?\d{1,2}\s*(?:大)?\s*节?\s*$", value):
        return True
    return any(word in value for word in ("上午", "下午", "晚上", "节次", "时间"))


def _soft(color: str, ratio: float) -> QColor:
    """把颜色往卡片底色里调淡：直接用品红/薄荷绿都太跳，用户嫌不美观。"""
    return theme.mix(QColor(color), QColor(CARD), max(0.0, min(1.0, ratio)))


def _occupied_color() -> QColor:
    """占用的块：很淡的玫瑰色，字用深一点的同色系，深浅色主题都好看。"""
    ink = theme.event_tint("rose")[2]
    return _soft(ink.name(), 0.78)


def _occupied_ink() -> QColor:
    return theme.event_tint("rose")[2]


def _free_color() -> QColor:
    """空着的格子：几乎就是卡片底色，只带一点点绿（原来的薄荷绿太扎眼）。"""
    return _soft(theme.SUCCESS, 0.93)


def _align_cells(cells: Any) -> List[Dict[str, Any]]:
    """把一行的格子对齐到 14 个节次上。

    `parse_classroom_grid` 给的格子有时带 `slot`（表头/行首是时间字符串，已经
    换算成节次），有时只是按列顺序排的；统一铺成长度 = 节次数的数组，
    后面按「全局节次下标」取，窗口裁剪（上午/下午）时才不会错位。
    """
    slots = period_slots()
    out: List[Dict[str, Any]] = [{} for _ in slots]
    for index, cell in enumerate(cells or []):
        if not isinstance(cell, dict) or not cell:
            continue
        slot = cell.get("slot", index)
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            slot = index
        if 0 <= slot < len(out):
            out[slot] = cell
    return out


class RoomHoverCard(QWidget):
    """鼠标停在格子上时的小卡片：

    * 有课：🔴 使用中 · 课程名称 · 授课教师 · 课程时间段（还有代码 / 人数）
    * 空闲：🟢 空闲

    不用 Qt 默认 QToolTip：它会被控件自己的内联颜色带偏（深色下变黑底深字），
    而且这里要的是「一行圆点状态 + 几行字段」的卡片，不是一行灰字。
    """

    RADIUS = 12
    PAD = 12

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._busy = True
        self._state = ""
        self._room = ""
        self._rows: List[tuple] = []
        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(self.PAD, self.PAD, self.PAD, self.PAD)
        self._body.setSpacing(3)

    # -------------------------------------------------- 内容
    def set_content(self, room: str, cell: Dict[str, Any],
                    begin: str = "", finish: str = "", period: int = 0) -> None:
        self._busy = bool((cell or {}).get("occupied"))
        self._room = str(room or "")
        self._state = "使用中" if self._busy else "空闲"
        course = str((cell or {}).get("course") or "").strip()
        teacher = str((cell or {}).get("teacher") or "").strip()
        code = str((cell or {}).get("code") or "").strip()
        students = str((cell or {}).get("students") or "").strip()
        kind = " · ".join(bit for bit in
                          (str((cell or {}).get("level") or ""),
                           str((cell or {}).get("kind") or "")) if bit)
        window = "%s–%s" % (begin, finish) if begin and finish else ""
        rows: List[tuple] = []
        if self._busy:
            rows.append(("课程", course or "（有课）"))
            if teacher:
                rows.append(("教师", teacher))
            if window:
                rows.append(("时间", ("第 %d 节 · " % period if period else "")
                             + window))
            meta = " · ".join(bit for bit in (code, students, kind) if bit)
            if meta:
                rows.append(("", meta))
        elif window:
            rows.append(("时间", ("第 %d 节 · " % period if period else "") + window))
        self._rows = rows
        self._rebuild()

    def _clear(self) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _rebuild(self) -> None:
        self._clear()
        soft, _hover, ink = theme.event_tint("rose" if self._busy else "teal")
        colors = {"TEXT": TEXT, "MUTED": MUTED, "TEXT_SOFT": TEXT_SOFT}
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {ink.name()}; font-size: 9px;"
                          f" background: transparent;")
        head.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        state = QLabel(self._state)
        state.setStyleSheet(
            f"color: {ink.name()}; font-size: 9.5pt; font-weight: 600;"
            f" background: transparent;")
        head.addWidget(state, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        room = QLabel(self._room)
        room.setStyleSheet(
            f"color: {colors['MUTED']}; font-size: 8.5pt; background: transparent;")
        head.addWidget(room, 0, Qt.AlignmentFlag.AlignVCenter)
        host = QWidget()
        host.setLayout(head)
        self._body.addWidget(host)
        self._head = host
        self._value_labels: List[QLabel] = []
        for label, value in self._rows:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            if label:
                key = QLabel(label)
                key.setFixedWidth(28)
                key.setStyleSheet(
                    f"color: {colors['MUTED']}; font-size: 8.5pt;"
                    f" background: transparent;")
                row.addWidget(key, 0, Qt.AlignmentFlag.AlignTop)
            text = QLabel(value)
            text.setWordWrap(False)
            weight = ("font-weight: 600; font-size: 10.5pt;"
                      if label == "课程" else "font-size: 9pt;")
            color = colors["TEXT"] if label in ("课程", "教师", "时间") \
                else colors["MUTED"]
            text.setStyleSheet(f"color: {color}; {weight} background: transparent;")
            row.addWidget(text, 1)
            self._value_labels.append(text)
            line = QWidget()
            line.setLayout(row)
            self._body.addWidget(line)
        self._fit()

    def _fit(self) -> None:
        """按内容把卡片撑开：**文字不切**，实在太宽才换行。

        以前宽度被死限在 340px、文字又不换行，「概率论与数理统计」「第 4 节 · 10:50–11:40」
        这种就从右边被切掉了（用户：「这个框……显示完整」）。
        """
        keys = 28 + 8
        widths = []
        head = getattr(self, "_head", None)
        if head is not None:
            widths.append(head.sizeHint().width())
        for label in getattr(self, "_value_labels", []):
            widths.append(label.sizeHint().width() + keys)
        natural = max(widths or [180]) + self.PAD * 2
        limit = 560
        try:
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                limit = max(260, min(560,
                                     int(screen.availableGeometry().width() * 0.7)))
        except Exception:
            pass
        wrap = natural > limit
        for label in getattr(self, "_value_labels", []):
            label.setWordWrap(bool(wrap))
        width = limit if wrap else natural
        self.setFixedWidth(int(max(170, min(width, limit))))
        self.adjustSize()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 30, 40))
        painter.drawRoundedRect(QRectF(rect).adjusted(0, 2, 0, 2),
                                self.RADIUS, self.RADIUS)
        painter.setBrush(QColor(CARD))
        painter.setPen(QPen(QColor(BORDER), 1.0))
        painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)


class ClassroomTimeline(QWidget):
    """时间轴：左侧教室名，右侧一格一节的色块。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots = period_slots()
        self._rooms: List[Dict[str, Any]] = []
        self._slot_from = 0
        self._slot_to = len(self._slots) - 1
        self._legacy = False
        self._hover_card = None
        self._hover_key = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    # -------------------------------------------------- 数据
    def set_rooms(self, rooms: List[Dict[str, Any]]) -> None:
        self._hover_hide()
        clean: List[Dict[str, Any]] = []
        for room in rooms or []:
            if not isinstance(room, dict):
                continue
            clean.append({"name": str(room.get("name") or ""),
                          "cells": _align_cells(room.get("cells"))})
        # 老格式（按时间排行）会把「08:00-08:45」当成教室名：这种不画，
        # 直接提示重新查一次，免得用户又看到「每行都是时间」
        self._legacy = bool(clean) and sum(
            1 for room in clean if _looks_like_time(room["name"])) >= max(
                1, len(clean) // 2)
        self._rooms = clean
        self._fit_height()
        self.update()

    def set_window(self, slot_from: int, slot_to: int) -> None:
        self._slot_from = max(0, min(len(self._slots) - 1, slot_from))
        self._slot_to = max(self._slot_from,
                            min(len(self._slots) - 1, slot_to))
        self.update()

    def rooms(self) -> List[Dict[str, Any]]:
        return list(self._rooms)

    def visible_slots(self) -> List[Dict[str, Any]]:
        return self._slots[self._slot_from:self._slot_to + 1]

    def slot_width(self) -> float:
        count = max(1, self._slot_to - self._slot_from + 1)
        return max(MIN_SLOT, (self.width() - NAME_COL - 4) / count)

    def preferred_width(self) -> int:
        count = max(1, self._slot_to - self._slot_from + 1)
        return int(NAME_COL + count * MIN_SLOT + 8)

    # -------------------------------------------------- 悬停看课程名
    def _cell_at(self, pos) -> tuple:
        """鼠标位置 → (教室, 节次下标)。"""
        if pos.y() < HEADER or pos.x() < NAME_COL:
            return None, -1
        row = int((pos.y() - HEADER) // ROW_HEIGHT)
        if row < 0 or row >= len(self._rooms):
            return None, -1
        width = self.slot_width()
        column = int((pos.x() - NAME_COL) // width)
        slot = self._slot_from + column
        if column < 0 or slot > self._slot_to:
            return None, -1
        return self._rooms[row], slot

    def mouseMoveEvent(self, event):
        room, slot = self._cell_at(event.position())
        if room is None or slot < 0:
            self.setToolTip("")
            self._hover_hide()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        self.setToolTip("")                  # 用自绘卡片，不用系统提示
        cells = room.get("cells") or []
        cell = cells[slot] if slot < len(cells) else {}
        key = (str(room.get("name") or ""), slot)
        if key == self._hover_key and self._hover_card is not None \
                and self._hover_card.isVisible():
            return
        self._hover_key = key
        self._hover_show(room, slot, cell or {}, event)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_hide()
        super().leaveEvent(event)

    # -------------------------------------------------- 悬停卡片
    def _hover_show(self, room: Dict[str, Any], slot: int,
                    cell: Dict[str, Any], event) -> None:
        if self._hover_card is None:
            self._hover_card = RoomHoverCard(self)
        window = self._slots[slot] if 0 <= slot < len(self._slots) else {}
        begin = str(window.get("begin") or "")[:5]
        finish = str(window.get("finish") or "")[:5]
        self._hover_card.set_content(str(room.get("name") or ""), cell,
                                     begin, finish, slot + 1)
        position = event.globalPosition().toPoint()
        screen = QGuiApplication.screenAt(position) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        width, height = self._hover_card.width(), self._hover_card.height()
        x = position.x() + 16
        y = position.y() + 18
        if area is not None:
            if x + width > area.right():
                x = max(area.left() + 6, position.x() - width - 14)
            if y + height > area.bottom():
                y = max(area.top() + 6, position.y() - height - 16)
        self._hover_card.move(int(x), int(y))
        self._hover_card.show()
        self._hover_card.raise_()

    def _hover_hide(self) -> None:
        self._hover_key = None
        if self._hover_card is not None:
            try:
                self._hover_card.hide()
            except Exception:
                pass

    def wheelEvent(self, event):
        """滚轮只滚这个时间轴；滚到顶/底也**不**把外面的页面带着滚。"""
        self._hover_hide()
        board = getattr(self, "_board", None)
        if board is not None and board.handle_wheel(event):
            return
        event.accept()

    def _fit_height(self) -> None:
        self.setFixedHeight(HEADER + max(1, len(self._rooms)) * ROW_HEIGHT + 6)

    # -------------------------------------------------- 绘制
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(CARD))
        if not self._rooms or self._legacy:
            painter.setPen(QColor(MUTED))
            font = QFont(self.font())
            font.setPointSizeF(9.0)
            painter.setFont(font)
            painter.drawText(
                self.rect(), int(Qt.AlignmentFlag.AlignCenter),
                "这版结果是按时间排的老格式，点「查看」重新查一次"
                if self._legacy else "选好教学楼（和教室 / 时间）后点「查看」")
            return

        slots = self.visible_slots()
        slot_w = self.slot_width()
        grid_left = NAME_COL
        grid_top = HEADER
        grid_right = grid_left + slot_w * len(slots)
        grid_bottom = grid_top + len(self._rooms) * ROW_HEIGHT

        # 时间轴表头
        head_font = QFont(self.font())
        head_font.setPointSizeF(8.0)
        painter.setFont(head_font)
        for index, slot in enumerate(slots):
            x = grid_left + index * slot_w
            painter.setPen(QPen(QColor(BORDER_SOFT), 1.0))
            painter.drawLine(QPointF(x, HEADER), QPointF(x, grid_bottom))
            painter.setPen(QColor(MUTED))
            painter.drawText(
                QRectF(x, 2, slot_w, 14),
                int(Qt.AlignmentFlag.AlignCenter), str(slot["number"]))
            painter.drawText(
                QRectF(x, 15, slot_w, 13),
                int(Qt.AlignmentFlag.AlignCenter),
                str(slot["begin"])[:5])

        # 一行一个教室
        for row, room in enumerate(self._rooms):
            y = grid_top + row * ROW_HEIGHT
            if row % 2 == 1:
                painter.fillRect(QRectF(0, y, self.width(), ROW_HEIGHT),
                                 QColor(theme.appearance().accent_q(0.02)))
            # 空闲底色
            painter.fillRect(QRectF(grid_left, y + 2, grid_right - grid_left,
                                    ROW_HEIGHT - 4), _free_color())
            # 教室名
            name_font = QFont(self.font())
            name_font.setPointSizeF(9.2)
            name_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(name_font)
            painter.setPen(QColor(TEXT))
            cells = room.get("cells") or []
            name = str(room.get("name") or "")
            metrics = QFontMetrics(name_font)
            painter.drawText(
                QRectF(8, y, NAME_COL - 12, ROW_HEIGHT),
                int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignVCenter),
                metrics.elidedText(name, Qt.TextElideMode.ElideRight,
                                   NAME_COL - 14))
            # 占用的块（cells 已经按 14 节对齐，这里用全局节次下标取）
            for index in range(len(slots)):
                slot = self._slot_from + index
                cell = cells[slot] if slot < len(cells) else {}
                if not cell or not cell.get("occupied"):
                    continue
                x = grid_left + index * slot_w
                block = QRectF(x + 1.5, y + 4, slot_w - 3, ROW_HEIGHT - 8)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(_occupied_color())
                painter.drawRoundedRect(block, 5.0, 5.0)
                label = str(cell.get("label") or "上课")
                painter.setPen(_occupied_ink())
                small = QFont(self.font())
                small.setPointSizeF(7.8)
                painter.setFont(small)
                metrics = QFontMetrics(small)
                painter.drawText(
                    block.adjusted(5, 0, -5, 0),
                    int(Qt.AlignmentFlag.AlignLeft)
                    | int(Qt.AlignmentFlag.AlignVCenter),
                    metrics.elidedText(label, Qt.TextElideMode.ElideRight,
                                       int(block.width()) - 10))
            painter.setPen(QPen(QColor(BORDER_SOFT), 1.0))
            painter.drawLine(QPointF(0, y + ROW_HEIGHT),
                             QPointF(grid_right, y + ROW_HEIGHT))

        # 外框（注意先把画刷清掉，否则会把整块填成最后一个色块的颜色）
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(BORDER), 1.0))
        painter.drawRoundedRect(QRectF(0.5, 0.5, grid_right - 1, grid_bottom - 1),
                                8, 8)


class ClassroomBoard(QWidget):
    """时间轴 + 横向滚动；卡片里还能限高（教室多的时候上下滚）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._max_height = 0        # >0 时限制自身高度，超出部分上下滚
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.timeline = ClassroomTimeline()
        self.timeline._board = self
        self.scroll.setWidget(self.timeline)
        lay.addWidget(self.scroll)
        # 卡片里宽度很紧，别让滚动区自己撑大
        self.scroll.setMinimumWidth(0)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    # -------------------------------------------------- 滚轮
    def handle_wheel(self, event) -> bool:
        """滚轮在时间轴里消化掉：滚到顶/底也不把外面的页面带着滚。

        以前滚到顶以后事件冒泡给页面，「焦点还在组件里，页面却开始滚了」。
        """
        delta = int(event.angleDelta().y() or event.angleDelta().x() or 0)
        if delta == 0:
            event.accept()
            return True
        step = -delta // 2
        vbar = self.scroll.verticalScrollBar()
        hbar = self.scroll.horizontalScrollBar()
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            hbar.setValue(hbar.value() + step)
        elif vbar.maximum() > vbar.minimum():
            vbar.setValue(vbar.value() + step)
        elif hbar.maximum() > hbar.minimum():
            hbar.setValue(hbar.value() + step)
        event.accept()
        return True

    def wheelEvent(self, event):
        self.handle_wheel(event)

    def set_max_height(self, value: int) -> None:
        """卡片里用：最高 `value` 像素，教室再多也不把整页撑长。"""
        self._max_height = max(0, int(value or 0))
        self._sync_width()
        if self._max_height:
            self.setMaximumHeight(self._max_height)
            self.setMinimumHeight(min(120, self._max_height))
        else:
            self.setMaximumHeight(16777215)
            self.setMinimumHeight(0)

    def set_rooms(self, rooms: List[Dict[str, Any]]) -> None:
        self.timeline.set_rooms(rooms)
        self._sync_width()

    def set_window(self, slot_from: int, slot_to: int) -> None:
        self.timeline.set_window(slot_from, slot_to)
        self._sync_width()

    def _sync_width(self) -> None:
        """时间轴按「节次 × 最小列宽」定宽，超出可视区就横向滚动。

        注意用 `setFixedSize` + `setWidgetResizable(False)`：
        直接把大宽度设成 minimumWidth 会把最小尺寸顶到卡片上，整个仪表板会被撑宽。
        """
        need = max(160, self.timeline.preferred_width())
        height = max(60, self.timeline.height())
        self.scroll.setWidgetResizable(False)
        self.timeline.setFixedSize(need, height)
        viewport = self.scroll.viewport().width()
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn if need > viewport
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_width()


TIME_CHOICES = [
    ("all", "整天（1–14 节）"),
    ("morning", "上午（1–5 节）"),
    ("afternoon", "下午（6–10 节）"),
    ("evening", "晚上（11–14 节）"),
    ("1", "第 1 大节（1–2 节）"),
    ("3", "第 2 大节（3–4 节）"),
    ("5", "第 3 大节（5–6 节）"),
    ("7", "第 4 大节（7–8 节）"),
    ("9", "第 5 大节（9–10 节）"),
]


def time_window(key: str) -> tuple:
    """把「时间」下拉的选择换成节次区间（0 基，含首含尾）。"""
    key = str(key or "all")
    table = {
        "all": (0, 13),
        "morning": (0, 4),
        "afternoon": (5, 9),
        "evening": (10, 13),
    }
    if key in table:
        return table[key]
    try:
        start = int(key)
    except Exception:
        return (0, 13)
    return (max(0, start - 1), min(13, start))


WINDOW_NAMES = {"all": "整天", "morning": "上午（1–5 节）",
                "afternoon": "下午（6–10 节）", "evening": "晚上（11–14 节）"}


def window_summary(rooms: List[Dict[str, Any]], slot_from: int, slot_to: int,
                   key: str = "all") -> str:
    """一句话说清「选中的时段里有多少教室空着」。

    用户的要求：
    * 选了具体时段（上午 / 下午 / 第几节）→ 说**这个时段有多少教室空着**；
    * 选「整天」→ 说**一共多少个时段是空着的**（教室 × 节次）。
    所以两种口径分开算，别只丢一个「N 个占用」。
    """
    rooms = [room for room in (rooms or []) if isinstance(room, dict)]
    if not rooms:
        return "没查到教室"
    low = max(0, int(slot_from))
    high = max(low, int(slot_to))
    slots = list(range(low, high + 1))
    total = len(rooms) * len(slots)
    occupied = 0
    fully_free = 0
    partial_free = 0
    for room in rooms:
        cells = room.get("cells") or []
        busy_here = 0
        free_here = 0
        for slot in slots:
            cell = cells[slot] if slot < len(cells) else {}
            if isinstance(cell, dict) and cell.get("occupied"):
                busy_here += 1
            else:
                free_here += 1
        occupied += busy_here
        if free_here == len(slots):
            fully_free += 1
        if free_here > 0:
            partial_free += 1
    free = total - occupied
    name = WINDOW_NAMES.get(str(key or "all"), "")
    if str(key or "all") == "all":
        return ("%d 间教室 × %d 节 = %d 个时段，空着 %d 个（%d 个在上课）；"
                "整段都空着的教室 %d 间"
                % (len(rooms), len(slots), total, free, occupied, fully_free))
    label = name or ("第 %d–%d 节" % (low + 1, high + 1))
    return ("%s：%d 间教室 × %d 节 = %d 个时段，空着 %d 个；"
            "整段都空着的教室 %d 间，还有空位的 %d 间"
            % (label, len(rooms), len(slots), total, free, fully_free,
               partial_free))


class PagerBar(QWidget):
    """时间轴翻页条：教室多的时候一页 10 间，别再截断 / 内部上下滚。"""

    changed = Signal(int)          # 新页码（0 基）

    def __init__(self, page_rows: int = PAGE_ROWS, parent=None):
        super().__init__(parent)
        self._page_rows = max(1, int(page_rows))
        self._total = 0
        self._page = 0
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.prev_button = SoftButton("上一页", "ghost", height=22, min_width=64,
                                      point_size=8.2)
        self.prev_button.clicked.connect(lambda: self.turn(-1))
        row.addWidget(self.prev_button)
        self.label = QLabel("")
        self.label.setObjectName("Tiny")
        row.addWidget(self.label)
        self.next_button = SoftButton("下一页", "ghost", height=22, min_width=64,
                                      point_size=8.2)
        self.next_button.clicked.connect(lambda: self.turn(1))
        row.addWidget(self.next_button)
        row.addStretch(1)
        self.setVisible(False)

    def page_rows(self) -> int:
        return self._page_rows

    def page(self) -> int:
        return self._page

    def pages(self) -> int:
        return max(1, (self._total + self._page_rows - 1) // self._page_rows)

    def set_total(self, total: int, page: int = 0) -> None:
        self._total = max(0, int(total or 0))
        self._page = max(0, min(int(page or 0), self.pages() - 1))
        self._refresh()

    def turn(self, delta: int) -> None:
        target = max(0, min(self._page + int(delta), self.pages() - 1))
        if target == self._page:
            return
        self._page = target
        self._refresh()
        self.changed.emit(self._page)

    def set_page(self, page: int) -> None:
        self._page = max(0, min(int(page or 0), self.pages() - 1))
        self._refresh()

    def _refresh(self) -> None:
        self.setVisible(self._total > self._page_rows)
        if not self.isVisible():
            return
        start = self._page * self._page_rows + 1
        end = min(self._total, (self._page + 1) * self._page_rows)
        self.label.setText("第 %d / %d 页 · 第 %d–%d 间（共 %d 间）"
                           % (self._page + 1, self.pages(), start, end,
                              self._total))
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page < self.pages() - 1)


class ClassroomPanel(QWidget):
    """校区 → 教学楼 → 教室 三级下拉 + 「查看」 + 时间轴（不再选时段，一律整天）。"""

    query_requested = Signal(str, str, str)      # 校区, 教学楼, 教室

    def __init__(self, store, parent=None, compact: bool = False):
        super().__init__(parent)
        self.store = store
        self._rooms: List[Dict[str, Any]] = []
        self._buildings_by_campus: Dict[str, List[str]] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.campus_box = Select()
        self.campus_box.setFixedHeight(28)
        self.campus_box.setToolTip("先选校区")
        self.campus_box.currentIndexChanged.connect(self._on_campus_changed)
        controls.addWidget(self.campus_box, 1)
        self.building_box = Select()
        self.building_box.setFixedHeight(28)
        self.building_box.setToolTip("再选这个校区里的教学楼")
        self.building_box.currentIndexChanged.connect(self._on_building_changed)
        controls.addWidget(self.building_box, 1)
        self.room_box = Select()
        self.room_box.setFixedHeight(28)
        self.room_box.setToolTip("最后选教室（不选就看整栋楼）")
        controls.addWidget(self.room_box, 1)
        self.view_button = SoftButton("查看", "primary", height=28, min_width=60,
                                      point_size=8.4)
        self.view_button.clicked.connect(self._emit_query)
        controls.addWidget(self.view_button)
        root.addLayout(controls)
        # 选完条件按回车 = 点「查看」（用户要求：别非要自己点）
        for box, tip in ((self.campus_box, "先选校区；选完按回车直接查"),
                         (self.building_box, "再选这个校区里的教学楼；按回车直接查"),
                         (self.room_box, "最后选教室（不选就看整栋楼）；按回车直接查")):
            box.setToolTip(tip)
            box.installEventFilter(self)

        self.board = ClassroomBoard()
        root.addWidget(self.board, 1)
        # 一页 10 间，多的翻页（不截断）
        self.pager = PagerBar(PAGE_ROWS)
        self.pager.changed.connect(lambda _page: self._render_page())
        root.addWidget(self.pager)

        self.status = QLabel("先选校区和教学楼，点「查看」就能看到这栋楼所有教室的空闲情况")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt;")
        root.addWidget(self.status)

    # -------------------------------------------------- 选项（校区 → 教学楼 → 教室）
    def set_campuses(self, mapping: Dict[str, List[str]]) -> None:
        from . import dashboard

        self._buildings_by_campus = {
            str(campus): dashboard.clean_buildings(names)
            for campus, names in (mapping or {}).items()}
        current = self.campus_box.currentData() or ""
        self.campus_box.blockSignals(True)
        self.campus_box.clear()
        for campus in dashboard.CAMPUSES:
            if self._buildings_by_campus.get(campus):
                self.campus_box.addItem(campus, campus)
        for campus in self._buildings_by_campus:
            if campus not in dashboard.CAMPUSES:
                self.campus_box.addItem(campus, campus)
        index = self.campus_box.findData(current)
        self.campus_box.setCurrentIndex(max(0, index))
        self.campus_box.blockSignals(False)
        self._on_campus_changed()

    def _on_campus_changed(self, _index: int = -1) -> None:
        campus = self.campus()
        names = self._buildings_by_campus.get(campus) or []
        current = self.building_box.currentData() or ""
        self.building_box.blockSignals(True)
        self.building_box.clear()
        for name in names:
            self.building_box.addItem(str(name), str(name))
        index = self.building_box.findData(current)
        self.building_box.setCurrentIndex(max(0, index))
        self.building_box.blockSignals(False)
        self._on_building_changed()

    def _on_building_changed(self, _index: int = -1) -> None:
        from . import dashboard

        try:
            rooms = dashboard.empty_room_rooms(self.store, self.building())
        except Exception:
            rooms = []
        self.set_rooms(rooms)

    def set_buildings(self, buildings: List[str]) -> None:
        """兼容老接口：直接给一堆教学楼时，按楼名归到各校区。"""
        from . import dashboard

        mapping: Dict[str, List[str]] = {}
        for name in dashboard.clean_buildings(buildings):
            mapping.setdefault(dashboard.guess_campus(name), []).append(name)
        self.set_campuses(mapping)

    def campus(self) -> str:
        return str(self.campus_box.currentData() or "")

    def set_rooms(self, rooms: List[str]) -> None:
        from . import dashboard

        current = self.room_box.currentData() or ""
        self.room_box.blockSignals(True)
        self.room_box.clear()
        self.room_box.addItem("全部教室", "")
        for name in dashboard.clean_rooms(rooms):
            self.room_box.addItem(str(name), str(name))
        index = self.room_box.findData(current)
        self.room_box.setCurrentIndex(max(0, index))
        self.room_box.blockSignals(False)

    def building(self) -> str:
        return str(self.building_box.currentData() or "")

    def room(self) -> str:
        return str(self.room_box.currentData() or "")

    def time_key(self) -> str:
        """不再选时段了：一律看整天（1–14 节）。"""
        return "all"

    def _emit_query(self) -> None:
        self.query_requested.emit(self.campus(), self.building(), self.room())

    def eventFilter(self, watched, event):
        """选完条件下拉框上按回车 → 直接查（跟点「查看」一模一样）。"""
        try:
            from PySide6.QtCore import QEvent

            if watched in (self.campus_box, self.building_box, self.room_box) \
                    and event.type() == QEvent.Type.KeyPress \
                    and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._emit_query()
                return True
        except Exception:
            pass
        return super().eventFilter(watched, event)

    # -------------------------------------------------- 数据
    def set_loading(self, loading: bool) -> None:
        self.view_button.setEnabled(not loading)
        self.view_button.setText("查询中…" if loading else "查看")

    def apply_result(self, payload: Dict[str, Any]) -> None:
        """把桥查回来的结果铺到时间轴上（一页 10 间，多的翻页）。"""
        self.set_loading(False)
        payload = payload if isinstance(payload, dict) else {}
        rooms = list(payload.get("rooms") or [])
        if not rooms:
            rooms = self._rooms
        self._rooms = rooms
        low, high = 0, 13
        self.board.set_window(low, high)
        self.pager.set_total(len(self._rooms), 0)
        self._render_page()
        building = str(payload.get("building") or self.building())
        room = str(payload.get("room") or "")
        if not payload.get("ok"):
            self.status.setText(str(payload.get("error") or "没查到教室安排"))
            return
        where = " · ".join(bit for bit in (self.campus(), building, room) if bit)
        summary = window_summary(self._rooms, low, high, "all")
        extra = ("；共 %d 页，用下面的「上一页 / 下一页」翻" % self.pager.pages()
                 if self.pager.isVisible() else "")
        self.status.setText("%s：%s（红=上课，浅色=空着）%s"
                            % (where or "查询结果", summary, extra))

    def _render_page(self) -> None:
        rows = max(1, self.pager.page_rows())
        start = self.pager.page() * rows
        page_rooms = self._rooms[start:start + rows]
        # 这一页有几间就多高，别把最后一行切掉
        need = HEADER + max(1, len(page_rooms)) * ROW_HEIGHT + 8
        self.board.setMinimumHeight(need)
        self.board.set_rooms(page_rooms)


class ClassroomWindow(QDialog):
    """放大看时间轴：占屏幕大半，教室多就上下滚、节次多就左右滚。"""

    def __init__(self, rooms: List[Dict[str, Any]], title: str = "",
                 subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("空教室%s" % (" · " + title if title else ""))
        self.setStyleSheet(
            "QDialog { background: %s; } QLabel { background: transparent; }"
            % CARD)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        name = QLabel(title or "空教室")
        font = QFont(name.font())
        font.setPointSizeF(12.0)
        font.setWeight(QFont.Weight.DemiBold)
        name.setFont(font)
        name.setStyleSheet("color: %s;" % TEXT)
        head.addWidget(name)
        hint = QLabel(subtitle)
        hint.setStyleSheet("color: %s; font-size: 9pt;" % MUTED)
        head.addWidget(hint)
        head.addStretch(1)
        root.addLayout(head)
        self.board = ClassroomBoard()
        self.board.set_rooms(rooms)
        root.addWidget(self.board, 1)
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        if area is not None:
            self.resize(max(900, int(area.width() * 0.86)),
                        max(560, int(area.height() * 0.82)))
        else:
            self.resize(1200, 800)

    def on_theme_changed(self) -> None:
        self.setStyleSheet(
            "QDialog { background: %s; } QLabel { background: transparent; }"
            % CARD)
        self.board.timeline.update()


def open_room_window(rooms: List[Dict[str, Any]], title: str = "",
                     subtitle: str = "", parent=None) -> Optional[ClassroomWindow]:
    """弹一个大的时间轴窗口（仪表板里的「放大查看」）。"""
    if not rooms:
        return None
    window = ClassroomWindow(rooms, title, subtitle, parent)
    # 挂在主窗口上，免得被垃圾回收／一闪就没
    if parent is not None:
        try:
            parent._classroom_window = window
        except Exception:
            pass
    window.show()
    window.raise_()
    return window