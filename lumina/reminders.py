"""桌面提醒：定时检查日程并在屏幕右下角弹出提醒卡片。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import icons, theme
from .config import REMIND_LABELS
from .theme import ACCENT2, DANGER, FAINT, MUTED, TEXT, WARNING
from .utils import date_short_cn, event_range, relative_label, weekday_cn
from .widgets import ElidedLabel, IconBadge, IconButton, ShadowPanel, SoftButton


class ReminderToast(QWidget):
    """无边框圆角提醒卡片。"""

    completed = Signal(int)
    snoozed = Signal(int)
    closed = Signal()

    WIDTH = 392
    HEIGHT = 190

    def __init__(self, event: Dict[str, Any], parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.event_data = event  # 不能命名为 event，会覆盖 QObject.event()
        self.event_id = int(event.get("id", 0))
        self._closing = False
        self._dismissed = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        panel = ShadowPanel(self, radius=theme.WINDOW_RADIUS, shadow=12, glow=False,
                            flat=True)
        outer.addWidget(panel)

        layout = QVBoxLayout(panel)
        # 12px 是外壳阴影区，再加 6px 呼吸感，避免内容贴着圆角
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(11)
        top.addWidget(IconBadge(icons.BELL, size=36,
                                color=theme.appearance().accent2_q().name(),
                                bg_alpha=0.16, radius=11, glyph_size=15))
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        title = ElidedLabel(str(event.get("title", "日程提醒")))
        title_font = QFont(self.font())
        title_font.setPointSizeF(10.2)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        text_box.addWidget(title)
        meta = ElidedLabel(self._meta_text(event))
        meta.setObjectName("Tiny")
        text_box.addWidget(meta)
        top.addLayout(text_box, 1)
        close_button = IconButton(icons.CLOSE, size=26, glyph_size=9, tip="关闭提醒")
        close_button.clicked.connect(self._dismiss)
        top.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        description = str(event.get("description", "")).strip()
        desc_label = QLabel(description or "该日程即将开始，可以现在标记完成或稍后提醒。")
        desc_label.setObjectName("Small")
        desc_label.setWordWrap(True)
        desc_font = QFont(self.font())
        desc_font.setPixelSize(12)
        desc_label.setFont(desc_font)
        # 高度按两行行距算，避免第二行被裁掉半截（看起来像一条虚线）
        desc_label.setFixedHeight(QFontMetrics(desc_font).lineSpacing() * 2 + 2)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(desc_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        done_button = SoftButton("标记完成", "success", height=32, point_size=9.0,
                                 glyph=icons.CHECK)
        done_button.clicked.connect(self._complete)
        snooze_button = SoftButton("稍后 10 分钟", "ghost", height=32, point_size=9.0,
                                   glyph=icons.CLOCK)
        snooze_button.clicked.connect(self._snooze)
        buttons.addWidget(done_button)
        buttons.addWidget(snooze_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._auto_close = QTimer(self)
        self._auto_close.setSingleShot(True)
        self._auto_close.timeout.connect(self._dismiss)
        self._auto_close.start(45000)
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_anim.setDuration(200)

    @staticmethod
    def _meta_text(event: Dict[str, Any]) -> str:
        day = QDate_from_string(str(event.get("date", "")))
        pieces = []
        if day is not None:
            relative = relative_label(day)
            if relative:
                pieces.append(relative)
            else:
                pieces.append(date_short_cn(day))
        pieces.append(event_range(event))
        minutes = event.get("remind_minutes")
        if minutes is not None:
            pieces.append(REMIND_LABELS.get(int(minutes), f"提前 {minutes} 分钟"))
        return " · ".join(piece for piece in pieces if piece)

    # -------------------------------------------------- 行为
    def show_toast(self) -> None:
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._opacity_anim.stop()
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(1.0)
        self._opacity_anim.start()

    def enterEvent(self, event):
        self._auto_close.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._auto_close.start(12000)
        super().leaveEvent(event)

    def _complete(self) -> None:
        self.completed.emit(self.event_id)
        self._dismiss()

    def _snooze(self) -> None:
        self.snoozed.emit(self.event_id)
        self._dismiss()

    def _dismiss(self) -> None:
        if getattr(self, "_dismissed", False):
            return
        self._dismissed = True
        try:
            self._auto_close.stop()
        except Exception:
            pass
        try:
            self._opacity_anim.stop()
        except Exception:
            pass
        self.close()

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            try:
                self.completed.disconnect()
            except Exception:
                pass
            try:
                self.closed.emit()
            except Exception:
                pass
        super().closeEvent(event)


class DepartureToast(QWidget):
    """「该出发了」提醒卡片。"""

    dismissed = Signal()
    snoozed = Signal(int, int)     # event_id, minutes

    WIDTH = 392
    HEIGHT = 196

    def __init__(self, event: Dict[str, Any], travel: str, leave_at: str,
                 parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.event_data = event
        self.event_id = int(event.get("id", 0))
        self._closing = False
        self._dismissed = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        panel = ShadowPanel(self, radius=theme.WINDOW_RADIUS, shadow=12, glow=False,
                            flat=True)
        outer.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(11)
        top.addWidget(IconBadge(icons.CLOCK, size=36,
                                color=theme.appearance().accent_q().name(),
                                bg_alpha=0.16, radius=11, glyph_size=15))
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        title = ElidedLabel("该出发了 · " + str(event.get("title", "")))
        title_font = QFont(self.font())
        title_font.setPixelSize(14)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        text_box.addWidget(title)
        meta = QLabel(f"{leave_at} 出发 · {event_range(event)} 上课")
        meta.setObjectName("Tiny")
        text_box.addWidget(meta)
        top.addLayout(text_box, 1)
        close_button = IconButton(icons.CLOSE, size=26, glyph_size=9, tip="关闭")
        close_button.clicked.connect(self._dismiss)
        top.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        travel_label = QLabel(travel)
        travel_label.setObjectName("Small")
        travel_label.setWordWrap(True)
        travel_label.setFixedHeight(QFontMetrics(self.font()).lineSpacing() * 2 + 4)
        layout.addWidget(travel_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        ok_button = SoftButton("知道了", "primary", height=32, point_size=9.0,
                               glyph=icons.CHECK)
        ok_button.clicked.connect(self._dismiss)
        snooze_button = SoftButton("晚 5 分钟再看", "ghost", height=32, point_size=9.0,
                                   glyph=icons.CLOCK)
        snooze_button.clicked.connect(lambda: self._snooze(5))
        buttons.addWidget(ok_button)
        buttons.addWidget(snooze_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._auto_close = QTimer(self)
        self._auto_close.setSingleShot(True)
        self._auto_close.timeout.connect(self._dismiss)
        self._auto_close.start(30000)

    def show_toast(self) -> None:
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        animation = QPropertyAnimation(self, b"windowOpacity", self)
        animation.setDuration(200)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.start()
        self._anim = animation

    def enterEvent(self, event):
        self._auto_close.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._auto_close.start(9000)
        super().leaveEvent(event)

    def _snooze(self, minutes: int) -> None:
        self.snoozed.emit(self.event_id, minutes)
        self._dismiss()

    def _dismiss(self) -> None:
        if getattr(self, "_dismissed", False):
            return
        self._dismissed = True
        try:
            self._auto_close.stop()
        except Exception:
            pass
        try:
            self._anim.stop()
        except Exception:
            pass
        self.close()

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            self.dismissed.emit()
        super().closeEvent(event)


def QDate_from_string(text: str):
    from PySide6.QtCore import QDate

    value = QDate.fromString(text, "yyyy-MM-dd")
    return value if value.isValid() else None


class ReminderCenter(QObject):
    """管理提醒轮询与堆叠的提醒窗口。"""

    data_changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._toasts: List[ReminderToast] = []
        self._timer = QTimer(self)
        self._timer.setInterval(20000)
        self._timer.timeout.connect(self.check)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._timer.start()
        self._depart_timer = QTimer(self)
        self._depart_timer.setInterval(30000)
        self._depart_timer.timeout.connect(self.check_departure)
        self._depart_timer.start()
        QTimer.singleShot(1500, self.check)
        QTimer.singleShot(4000, self.check_departure)

    def stop(self) -> None:
        self._timer.stop()
        if getattr(self, "_depart_timer", None) is not None:
            self._depart_timer.stop()
        for toast in list(self._toasts):
            toast.close()
        for toast in list(getattr(self, "_depart_toasts", [])):
            toast.close()

    def check(self) -> None:
        if self.store.get_setting("reminders_enabled", "1") != "1":
            return
        try:
            due = self.store.pending_reminders(datetime.now())
        except Exception:
            return
        for event in due:
            try:
                self.store.mark_notified(int(event["id"]))
            except Exception:
                continue
            self._show(event)

    def _show(self, event: Dict[str, Any]) -> None:
        toast = ReminderToast(event)
        toast.completed.connect(self._on_completed)
        toast.snoozed.connect(self._on_snoozed)
        toast.closed.connect(lambda: self._on_closed(toast))
        self._toasts.append(toast)
        while len(self._toasts) > 4:
            oldest = self._toasts.pop(0)
            oldest.close()
        self._relayout()
        toast.show_toast()
        if self.store.get_setting("reminder_sound", "1") == "1":
            QApplication.beep()

    def _on_completed(self, event_id: int) -> None:
        try:
            self.store.set_done(event_id, True)
        except Exception:
            pass
        self.data_changed.emit()

    def _on_snoozed(self, event_id: int) -> None:
        try:
            self.store.snooze(event_id, datetime.now() + timedelta(minutes=10))
        except Exception:
            pass
        self.data_changed.emit()

    def _on_closed(self, toast: ReminderToast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._relayout()
        toast.deleteLater()

    # -------------------------------------------------- 出发提醒
    def _departure_settings(self) -> Dict[str, Any]:
        store = self.store
        mode = (store.get_setting("departure_mode", "walk") or "walk").lower()
        if mode not in ("walk", "bike"):
            mode = "walk"
        try:
            buffer = float(store.get_setting("departure_buffer", "2") or "2")
        except ValueError:
            buffer = 2.0
        speeds = {}
        for key in ("walk", "bike"):
            try:
                value = float(store.get_setting(f"travel_speed_{key}", "") or 0)
            except ValueError:
                value = 0.0
            if value > 0:
                speeds[key] = value
        return {
            "enabled": (store.get_setting("departure_enabled", "1") or "1") == "1",
            "mode": mode,
            "buffer": max(0.0, min(30.0, buffer)),
            "speeds": speeds,
        }

    def check_departure(self) -> None:
        """到「该出发」的时间点时弹提醒（每天每个日程只提醒一次）。"""
        from datetime import datetime

        from . import geo

        settings = self._departure_settings()
        if not settings["enabled"]:
            return
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if getattr(self, "_depart_date", "") != today:
            self._depart_date = today
            self._depart_done = set()
        if getattr(self, "_depart_done", None) is None:
            self._depart_done = set()
        if self._depart_date != today:
            return

        try:
            events = self.store.events_on(today)
        except Exception:
            return

        location = None
        for event in events:
            if event.get("done"):
                continue
            event_id = int(event.get("id", 0))
            if event_id in self._depart_done:
                continue
            room = geo.parse_location_from_description(str(event.get("description", "")))
            if not room:
                continue
            place = geo.match_place(room)
            if not place:
                continue
            start = str(event.get("start_time") or "")
            if not start:
                continue
            try:
                start_h, start_m = [int(part) for part in start.split(":")[:2]]
            except ValueError:
                continue
            start_minutes = start_h * 60 + start_m
            now_minutes = now.hour * 60 + now.minute
            if now_minutes >= start_minutes:
                continue
            if location is None:
                location = geo.current_location(self.store)
            est = geo.estimate(location, place, speeds=settings["speeds"])
            mode = settings["mode"]
            item = (est.get("modes") or {}).get(mode) or next(
                iter((est.get("modes") or {}).values()), None)
            if not item:
                continue
            leave_at = geo.departure_time(start, item["minutes"], settings["buffer"])
            if not leave_at:
                continue
            leave_h, leave_m = [int(part) for part in leave_at.split(":")[:2]]
            if now_minutes < leave_h * 60 + leave_m:
                continue
            self._depart_done.add(event_id)
            travel = geo.format_estimate(est)
            if location.get("source") == "ip":
                travel += "（按 IP 城市级定位估算）"
            self._show_departure(event, travel, leave_at)

    def _show_departure(self, event: Dict[str, Any], travel: str, leave_at: str) -> None:
        if getattr(self, "_depart_toasts", None) is None:
            self._depart_toasts: List[DepartureToast] = []
        toast = DepartureToast(event, travel, leave_at)
        toast.snoozed.connect(self._on_depart_snooze)
        toast.dismissed.connect(lambda: self._on_departure_closed(toast))
        self._depart_toasts.append(toast)
        while len(self._depart_toasts) > 3:
            self._depart_toasts.pop(0).close()
        self._relayout_departures()
        toast.show_toast()
        if self.store.get_setting("reminder_sound", "1") == "1":
            QApplication.beep()

    def _on_depart_snooze(self, event_id: int, minutes: int) -> None:
        # 稍后再看：把这次提醒从「已提醒」里摘掉，等下个时间窗再判断一次
        if getattr(self, "_depart_done", None) is not None:
            self._depart_done.discard(event_id)
        QTimer.singleShot(max(1, minutes) * 60 * 1000,
                          lambda: self._depart_done and self._depart_done.discard(event_id))

    def _on_departure_closed(self, toast) -> None:
        if toast in getattr(self, "_depart_toasts", []):
            self._depart_toasts.remove(toast)
        self._relayout_departures()
        toast.deleteLater()

    def _relayout_departures(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.right() - DepartureToast.WIDTH - 18
        toasts = getattr(self, "_depart_toasts", [])
        offset = len(self._toasts) * (ReminderToast.HEIGHT + 10)
        for index, toast in enumerate(toasts):
            y = (area.bottom() - DepartureToast.HEIGHT - 18
                 - offset - index * (DepartureToast.HEIGHT + 10))
            toast.move(x, y)

    def _relayout(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.right() - ReminderToast.WIDTH - 18
        for index, toast in enumerate(self._toasts):
            y = area.bottom() - ReminderToast.HEIGHT - 18 - index * (ReminderToast.HEIGHT + 10)
            toast.move(x, y)
