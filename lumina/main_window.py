"""主窗口：无边框半透明外壳、侧边栏与页面导航。"""
from __future__ import annotations

import os
import sys
import time
from typing import Optional

from PySide6.QtCore import (
    QAbstractAnimation, QDate, QEasingCurve, QPoint, QPropertyAnimation, QRect,
    QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence,
    QLinearGradient, QPainter, QPainterPath, QPen, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from . import icons, theme, win_effects
from .config import APP_TITLE, APP_VERSION
from .dialogs import ComposeDialog, EventDialog
from .mailer import SmtpConfig
from .page_calendar import CalendarPage
from .page_latex import LatexPage
from .page_docs import DocsPage
from .page_ehall import EhallPage
from .page_lit import LiteraturePage
from .page_homework import HomeworkPage
from .page_memos import MemosPage
from .page_notices import NoticesPage
from .page_mailbox import MailboxPage
from .page_outbox import OutboxPage
from .page_settings import SettingsPage
from .right_dock import RightDock
from .web_panel import set_tabs_enabled
from .mascot import Mascot
from .focus import AwayToast, FocusMonitor, FocusStartMenu, FocusTimer
from .llm_local import LocalModelRunner
from .notices import NoticeWatcher, make_notice_toast
from .reminders import ReminderCenter
from .theme import (
    ACCENT, ACCENT2, BORDER, CARD, DANGER, FAINT, MUTED, SIDEBAR_BG, SUCCESS,
    TEXT, TEXT_SOFT, WARNING, make_app_icon,
)
from .utils import iso, weekday_cn
from .widgets import (
    Card, EventDetailPane, IconButton, NavButton, PaneSplitter, ShadowPanel,
    SnackBar, SoftButton, WindowButton,
    icon_font, rounded_path, round_menu,
)


def hard_exit(store=None, code: int = 0) -> None:
    """兜底硬退出。

    QtWebEngine 在关窗时偶发死锁（Chromium 子进程不响应），表现为「点了关闭但窗口
    一直不消失、进程还在吃 CPU」。数据都写完就直接结束进程，不做优雅拆除。
    """
    try:
        if store is not None:
            store.close()
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


class LogoMark(QWidget):
    def __init__(self, size: int = 30, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        state = theme.appearance()
        gradient.setColorAt(0.0, state.accent_deep_q())
        gradient.setColorAt(1.0, state.accent2_q())
        path = rounded_path(rect, rect.width() * 0.3)
        painter.fillPath(path, QBrush(gradient))
        painter.setFont(icon_font(int(rect.width() * 0.46), QFont.Weight.DemiBold))
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), icons.CALENDAR)


class StatusChip(QWidget):
    """标题栏上的邮箱状态小胶囊。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = "邮箱未配置"
        self._color = QColor(WARNING)
        self._hover = False
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("点击前往设置")
        self._apply_width()

    def _apply_width(self) -> None:
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium)
        self.setFont(font)
        width = QFontMetrics(font).horizontalAdvance(self._text) + 38
        self.setFixedWidth(max(104, width))

    def set_status(self, text: str, color: str, tooltip: str = "") -> None:
        self._text = text
        self._color = QColor(color)
        self.setToolTip(tooltip or "点击前往设置")
        self._apply_width()
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded_path(rect, rect.height() / 2)
        fill = QColor(self._color)
        fill.setAlphaF(0.16 if self._hover else 0.11)
        painter.fillPath(path, fill)
        border = QColor(self._color)
        border.setAlphaF(0.36)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)
        dot = QRectF(rect.left() + 11, rect.center().y() - 3, 6, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(dot)
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(TEXT_SOFT))
        painter.drawText(
            QRectF(rect.left() + 22, rect.top(), rect.width() - 28, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )


class TitleBar(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__(window.shell)
        self._window = window
        self.setFixedHeight(54)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 10, 0)
        layout.setSpacing(9)
        self.sidebar_button = IconButton(icons.CHEVRON_LEFT, size=30, glyph_size=12,
                                         tip="收起左侧导航")
        self.sidebar_button.clicked.connect(lambda: window.toggle_sidebar())
        layout.addWidget(self.sidebar_button)
        layout.addWidget(LogoMark(30))
        brand = QLabel("DanLume")
        brand_font = QFont(self.font())
        brand_font.setPointSizeF(11.6)
        brand_font.setWeight(QFont.Weight.DemiBold)
        brand.setFont(brand_font)
        brand.setStyleSheet(f"color: {TEXT}; background: transparent;")
        layout.addWidget(brand)
        separator = QLabel("·")
        separator.setStyleSheet(f"color: {FAINT}; background: transparent;")
        layout.addWidget(separator)
        tagline = QLabel("日程与工作汇报")
        tagline.setObjectName("Tiny")
        layout.addWidget(tagline)
        layout.addStretch(1)

        dark = theme.is_dark()
        # 用文字而不是图标：这两个开关是「左/右那一栏显示不显示」，图标容易看不懂
        # 注意：按「位置」而不是「角色」——互换布局后右边那一栏是页面，左边那栏是日程/AI
        self.middle_button = SoftButton("中栏", "ghost", height=26, min_width=44,
                                        point_size=8.4)
        self.middle_button.setToolTip("显示 / 隐藏左边这一栏（Ctrl+Alt+M）")
        self.middle_button.clicked.connect(lambda: window.toggle_pane_slot("left"))
        layout.addWidget(self.middle_button)
        self.right_button = SoftButton("右栏", "ghost", height=26, min_width=44,
                                       point_size=8.4)
        self.right_button.setToolTip("显示 / 隐藏右边这一栏（Ctrl+Alt+R）")
        self.right_button.clicked.connect(lambda: window.toggle_pane_slot("right"))
        layout.addWidget(self.right_button)
        layout.addSpacing(4)

        self.appearance_button = IconButton(
            icons.SUN if dark else icons.MOON,
            size=30,
            glyph_size=13,
            tip="切换到浅色外观" if dark else "切换到深色外观",
        )
        self.appearance_button.clicked.connect(window.toggle_appearance)
        layout.addWidget(self.appearance_button)
        layout.addSpacing(4)

        self.status_chip = StatusChip()
        self.status_chip.clicked.connect(lambda: window.navigate(8))
        layout.addWidget(self.status_chip)
        layout.addSpacing(6)

        self.min_button = WindowButton(icons.MINIMIZE, tip="最小化")
        self.min_button.clicked.connect(window.showMinimized)
        self.max_button = WindowButton(icons.MAXIMIZE, tip="最大化")
        self.max_button.clicked.connect(window.toggle_maximize)
        self.close_button = WindowButton(
            icons.CLOSE, danger=True,
            tip=("收进托盘（右键托盘图标可退出）"
                 if getattr(window, "tray_available", lambda: False)()
                 else "关闭"))
        self.close_button.clicked.connect(window.close)
        layout.addWidget(self.min_button)
        layout.addWidget(self.max_button)
        layout.addWidget(self.close_button)

    def set_maximized(self, maximized: bool) -> None:
        self.max_button.set_glyph(icons.RESTORE if maximized else icons.MAXIMIZE)
        self.max_button.setToolTip("还原" if maximized else "最大化")

    def set_pane_states(self, left: bool, right: bool) -> None:
        """左边那栏 / 右边那栏的按钮：开着的显示 ✕（点了就是关），关着的只显示名字。

        `left` / `right` 说的是**屏幕上的位置**，不是「中栏 / 右栏」这两个角色：
        互换布局后左边是日程/AI、右边是页面，这里的 ✕ 关的也得跟着换。
        """
        self.middle_button.setKind("subtle" if left else "ghost")
        self.middle_button.setText("中栏 ✕" if left else "中栏")
        self.middle_button.setToolTip(
            "关闭左边这一栏（Ctrl+Alt+M）" if left else "打开左边这一栏（Ctrl+Alt+M）")
        self.right_button.setKind("subtle" if right else "ghost")
        self.right_button.setText("右栏 ✕" if right else "右栏")
        self.right_button.setToolTip(
            "关闭右边这一栏（Ctrl+Alt+R）" if right else "打开右边这一栏（Ctrl+Alt+R）")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if getattr(self._window, "_maximized", False):
                return
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._window.toggle_maximize()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(
            QRectF(0, self.height() - 1, self.width(), 1),
            QColor(0, 0, 0, 10),
        )


class Sidebar(QWidget):
    new_event = Signal()
    new_mail = Signal()
    focus_toggle = Signal()
    ai_requested = Signal()
    vpn_requested = Signal()
    navigate = Signal(int)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setFixedWidth(214)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 14)
        layout.setSpacing(3)

        layout.addWidget(self._section("导航"))
        self.nav_dashboard = NavButton(icons.INFO, "仪表板")
        self.nav_calendar = NavButton(icons.CALENDAR, "日历")
        self.nav_mail = NavButton(icons.MAIL, "邮箱")
        self.nav_notices = NavButton(icons.BELL, "通知")
        self.nav_memos = NavButton(icons.DOCUMENT, "备忘")
        self.nav_homework = NavButton(icons.DOWNLOAD, "作业")
        self.nav_ehall = NavButton(icons.FOLDER, "eHall")
        self.nav_classroom = NavButton(icons.CALENDAR, "空教室")
        self.nav_docs = NavButton(icons.DOCUMENT, "文档")
        self.nav_lit = NavButton(icons.FOLDER, "文献库")
        self.nav_latex = NavButton(icons.EDIT, "公式")
        self.nav_ai = NavButton(icons.INFO, "AI")
        self.nav_ai.clicked.connect(self.ai_requested.emit)
        self.nav_settings = NavButton(icons.SETTINGS, "设置")
        self._nav_items = [self.nav_dashboard, self.nav_calendar, self.nav_mail,
                           self.nav_notices, self.nav_memos, self.nav_homework,
                           self.nav_ehall, self.nav_classroom, self.nav_docs,
                           self.nav_lit, self.nav_latex, self.nav_ai,
                           self.nav_settings]
        # AI 不是页面（在右栏里），所以单独映射页面下标；
        # 仪表板放在页面下标 10，导航里排第一；空教室是页面 11
        page_map = [10, 0, 1, 2, 3, 4, 5, 11, 6, 9, 7, None, 8]
        for position, item in enumerate(self._nav_items):
            if page_map[position] is not None:
                item.clicked.connect(
                    lambda idx=page_map[position]: self.navigate.emit(idx))
            layout.addWidget(item)
        layout.addSpacing(12)

        layout.addWidget(self._section("快捷操作"))
        self.new_event_button = SoftButton("新建日程", "primary", glyph=icons.ADD, height=38)
        self.new_event_button.clicked.connect(self.new_event)
        self.new_mail_button = SoftButton("新邮件", "ghost", glyph=icons.MAIL, height=38)
        self.new_mail_button.clicked.connect(self.new_mail)
        layout.addWidget(self.new_event_button)
        layout.addWidget(self.new_mail_button)
        self.focus_button = SoftButton("专注模式", "ghost", glyph=icons.CLOCK, height=38)
        self.focus_button.setToolTip("开始专注：桌面会出现悬浮计时器，离开太久会提醒你")
        self.focus_button.clicked.connect(self.focus_toggle)
        layout.addWidget(self.focus_button)
        layout.addStretch(1)

        self.today_card = Card(radius=14, fill=CARD, border=BORDER, padding=12,
                               spacing=5)
        self.today_card.setFixedHeight(104)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.day_number = QLabel("1")
        day_font = QFont(self.font())
        day_font.setPointSizeF(24)
        day_font.setWeight(QFont.Weight.DemiBold)
        self.day_number.setFont(day_font)
        self.day_number.setStyleSheet(f"color: {TEXT}; background: transparent;")
        top.addWidget(self.day_number)
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        self.month_label = QLabel("")
        self.month_label.setObjectName("Small")
        self.weekday_label = QLabel("")
        self.weekday_label.setObjectName("Tiny")
        text_box.addWidget(self.month_label)
        text_box.addWidget(self.weekday_label)
        top.addLayout(text_box, 1)
        self.today_card.body.addLayout(top)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Tiny")
        self.today_card.body.addWidget(self.count_label)
        layout.addWidget(self.today_card)
        self.set_active(0)

    @staticmethod
    def _section(text: str) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 4)
        label = QLabel(text)
        label.setObjectName("Tiny")
        layout.addWidget(label)
        layout.addStretch(1)
        return widget

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        # 边栏底色：左下角跟随窗口圆角，其余直角
        path = QPainterPath()
        radius = theme.WINDOW_RADIUS
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left() + radius, rect.bottom())
        path.arcTo(QRectF(rect.left(), rect.bottom() - radius * 2, radius * 2,
                          radius * 2), 270, -90)
        path.closeSubpath()
        painter.fillPath(path, QColor(SIDEBAR_BG))
        divider = QColor(theme.TEXT)
        divider.setAlphaF(0.10)
        painter.fillRect(
            QRectF(self.width() - 1, 0, 1, self.height()),
            divider,
        )

    #: 导航位置：0 仪表板（页面 10）· 1 日历 · 2 邮箱 · 3 通知 · 4 备忘 · 5 作业
    #:           6 eHall · 7 空教室（页面 11）· 8 文档 · 9 文献库（页面 9）·
    #:           10 公式（页面 7）· 11 AI（右侧栏）· 12 设置（页面 8）
    #: 页面下标 → 导航项位置（导航里多了「仪表板」「空教室」「文献库」，位置会跟页面下标错开）
    PAGE_TO_NAV = {10: 0,       # 仪表板（页面 10）排在最上面
                   0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6,
                   11: 7,       # 空教室是页面 11，排在 eHall 后面
                   6: 8,        # 文档
                   9: 9,        # 文献库
                   7: 10,       # 公式
                   8: 12}       # 设置
    #: AI 不是页面（在右侧 dock 里）
    NAV_AI = 11
    #: 以前侧栏有个「VPN / 代理」按钮，用户说去掉；VPN 现在只在 设置 → 其他 里配。
    NAV_VPN = -1

    def set_active(self, index: int) -> None:
        for position, item in enumerate(self._nav_items):
            item.set_active(position == index)

    def set_active_page(self, page_index: int) -> None:
        """按页面下标点亮导航项（导航顺序和页面下标不一定一致）。"""
        self.set_active(self.PAGE_TO_NAV.get(int(page_index), 0))

    def refresh_counts(self) -> None:
        counts = self.store.mail_counts()
        self.nav_mail.set_badge(counts.get("draft", 0))
        today = QDate.currentDate()
        events = self.store.events_on(iso(today))
        done = sum(1 for item in events if item.get("done"))
        self.day_number.setText(str(today.day()))
        self.month_label.setText(f"{today.month()} 月")
        self.weekday_label.setText(weekday_cn(today))
        if events:
            self.count_label.setText(f"{len(events)} 个日程 · {done} 已完成")
        else:
            self.count_label.setText("今天还没有安排")


class MainWindow(QWidget):
    fullscreen_changed = Signal(bool)
    deadlines_synced = Signal(dict)      # eLearning 截止时间写进日程之后

    def __init__(self, store, parent=None, initial_page: int = 10,
                 initial_date: Optional[QDate] = None, show_welcome: bool = True):
        """initial_page 默认 10 = 仪表板（打开应用先看今天要做什么）。"""
        super().__init__(parent)
        self.store = store
        # 提示气泡改成自己画（Qt 默认的会继承控件颜色，深色下就是「黑底深字」）
        try:
            from . import tooltip

            tooltip.install(QApplication.instance())
        except Exception:
            pass
        self._margin = 18
        self._maximized = False
        self._restore_geometry: Optional[QRect] = None
        self._fade_done = False
        self._closed = False
        self._reloading = False
        self._glass_active = False
        self._fs_widget = None
        self._sidebar_open = (store.get_setting("sidebar_open", "1") or "1") != "0"
        self._chrome_hidden = False
        self._backdrop_timer = QTimer(self)
        self._backdrop_timer.setInterval(400)
        self._backdrop_timer.setSingleShot(True)
        self._backdrop_timer.timeout.connect(self._refresh_backdrop)
        self._backdrop_poll = QTimer(self)
        self._backdrop_poll.setInterval(8000)   # 别太勤：抓屏和 Chromium 合成容易互相卡
        self._backdrop_poll.timeout.connect(self._refresh_backdrop)
        self._backdrop_busy = False
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(make_app_icon())
        # 系统托盘：点右上角「关闭」收进托盘，右键托盘图标才有「退出」
        self._setup_tray()
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        # 分层窗口里的 Chromium 原生子窗口会被 Windows 判成“全透明区域”，
        # 结果是点击直接穿透到下面的程序。所以默认改成不透明窗口。
        self._translucent = (
            (store.get_setting("window_translucent", "0") or "0") == "1")
        if self._translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        else:
            self._margin = 0
        self.setMinimumSize(1024, 680)
        self.resize(1280, 816)
        self.setMouseTracking(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self._margin, self._margin, self._margin, self._margin)
        outer.setSpacing(0)
        self.shell = ShadowPanel(self, radius=theme.WINDOW_RADIUS, shadow=18, glow=False)
        outer.addWidget(self.shell)

        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.title_bar = TitleBar(self)
        shell_layout.addWidget(self.title_bar)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.sidebar = Sidebar(store)
        content_layout.addWidget(self.sidebar)

        # 多标签页必须在**建页面之前**打开：网页面板是在页面构造函数里建出来的，
        # 以前放在后面才 set_tabs_enabled()，所以开关打开了也没用（重启也一样）。
        try:
            set_tabs_enabled(
                (self.store.get_setting("browser_tabs", "0") or "0") == "1")
        except Exception:
            pass
        self.pages = QStackedWidget()
        self.calendar_page = CalendarPage(store)
        self.mailbox_page = MailboxPage(store)
        self.outbox_page = self.mailbox_page.outbox
        self.notices_page = NoticesPage(store)
        self.memos_page = MemosPage(store)
        self.homework_page = HomeworkPage(store)
        # eLearning 的登录态只能从运行中的浏览器里取（磁盘上的 Cookies 库被 Chromium 锁死）
        try:
            from . import elearning

            elearning.set_profile_provider(
                lambda: self.homework_page.web.profile())
        except Exception:
            pass
        self.ehall_page = EhallPage(store)
        from .page_classroom import ClassroomPage

        self.classroom_page = ClassroomPage(store)
        self.docs_page = DocsPage(store)
        self.lit_page = LiteraturePage(store)
        self.lit_page.ask_endnote_install.connect(self._ask_endnote_install)
        self.latex_page = LatexPage(store)
        self.settings_page = SettingsPage(store)
        # 仪表板放在最后（页面下标 10），左侧导航里排在最上面
        from .page_dashboard import DashboardPage

        self.dashboard_page = DashboardPage(store)
        try:
            self.dashboard_page.open_ehall.connect(self._open_ehall_url)
            self.dashboard_page.open_homework.connect(lambda: self.navigate(4))
            self.dashboard_page.open_homework_url.connect(self._open_homework_url)
            self.dashboard_page.open_classroom.connect(lambda: self.navigate(11))
        except Exception:
            pass
        # 每天把 eLearning 的作业截止时间写进日程（已有的不重复加）
        try:
            self.deadlines_synced.connect(self._on_deadlines_synced)
            self._deadlines_timer = QTimer(self)
            self._deadlines_timer.setInterval(60 * 60 * 1000)   # 每小时看一眼
            self._deadlines_timer.timeout.connect(self._maybe_sync_deadlines)
            self._deadlines_timer.start()
            QTimer.singleShot(15000, self._maybe_sync_deadlines)
        except Exception:
            pass
        # 仪表板上的 ehall 卡片：借 ehall 网页面板的登录态，在后台取数
        try:
            from .ehall_bridge import EhallBridge

            self.ehall_bridge = EhallBridge(
                store, lambda: self.ehall_page.web.profile(), self)
            self.dashboard_page.attach_bridge(self.ehall_bridge)
            # 空教室页的时间轴也用同一个桥（信号是「校区, 教学楼, 教室」）
            try:
                self.classroom_page.query_requested.connect(
                    lambda campus, building, room:
                    self.ehall_bridge.classroom_query(building, room, "all",
                                                      campus=campus))
                self.ehall_bridge.classroom_ready.connect(
                    self.classroom_page.apply_result)
            except Exception:
                pass
        except Exception:
            self.ehall_bridge = None
        for page in (self.calendar_page, self.mailbox_page, self.notices_page,
                     self.memos_page, self.homework_page, self.ehall_page,
                     self.docs_page, self.latex_page, self.settings_page,
                     self.lit_page, self.dashboard_page, self.classroom_page):
            self.pages.addWidget(page)
        # 中栏可以是「页面」也可以是「一张日程详情卡片」
        self.middle_stack = QStackedWidget()
        self.middle_stack.addWidget(self.pages)
        self.middle_detail = EventDetailPane(store)
        self.middle_detail.edit_requested.connect(self.open_event_dialog)
        self.middle_detail.closed.connect(self.hide_middle_detail)
        self.middle_stack.addWidget(self.middle_detail)
        content_layout.addWidget(self.middle_stack, 1)
        # 右侧 dock：日程 + AI 两个标签，可折叠 / 可全屏
        self.dock = RightDock(store)
        self.content_layout = content_layout
        self.dock.event_clicked.connect(self.open_event_dialog)
        self.dock.agenda.edit_requested.connect(self.open_event_dialog)
        self.dock.width_changed.connect(self._on_agenda_width)
        self.agenda = self.dock.agenda
        content_layout.addWidget(self.dock)
        # 中栏 / 右栏之间的拖拽条（悬停变左右箭头，拖动实时改宽度）
        self.pane_splitter = PaneSplitter(base=self.dock.width())
        self.pane_splitter.grabbed.connect(self._on_pane_split_begin)
        self.pane_splitter.dragged.connect(self._on_pane_split_drag)
        self.pane_splitter.released.connect(self._on_pane_split_finish)
        self.pane_splitter.reset_requested.connect(self._on_pane_split_reset)
        content_layout.addWidget(self.pane_splitter)
        # 尾巴上的占位：两栏都关掉、只剩左栏时把多余宽度吸走，
        # 这样左栏还贴在左边，不会飘到中间去
        self.pane_tail = QWidget()
        self.pane_tail.setMinimumWidth(0)
        self.pane_tail.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Preferred)
        self.pane_tail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        content_layout.addWidget(self.pane_tail)
        shell_layout.addWidget(content, 1)

        self.snackbar = SnackBar(self.shell)
        # 看板娘（独立桌面窗口，最小化主窗口也留在桌面上）
        self.mascot = Mascot(store)
        # 专注模式：悬浮计时器 + 离开提醒
        self.focus_timer = FocusTimer(store)
        self.focus_timer.clicked.connect(self.focus_timer.toggle_pause)
        # 让 agent 的 pomodoro_start 工具能真的把专注计时开起来（GUI 线程）
        try:
            from . import agent_write

            agent_write.set_pomodoro_handler(self._start_focus_from_agent)
        except Exception:
            pass
        # agent 的 library_open：在应用内的网页里打开数据库搜索页
        try:
            from . import agent_web

            agent_web.set_open_handler(self.open_web_url)
        except Exception:
            pass
        # agent 的 mail_send：弹「可编辑 + 确认」的窗口（用户要求能改）
        try:
            from . import agent_mail

            agent_mail.set_compose_handler(self._compose_from_agent)
        except Exception:
            pass
        # agent 搜到文献时，**自动出现在右侧「文献」面板里**（用户要求列在边栏）
        try:
            from . import literature

            literature.set_results_handler(self._on_literature_results)
        except Exception:
            pass
        # 「文献库」页上的搜索框只过滤本机文献；点「联网检索」把关键词送到右侧「文献」
        try:
            self.lit_page.set_online_search_handler(self.dock.search_literature)
        except Exception:
            pass
        # 客户端直接连过来送注册申请时，弹个提示（不用一直去翻邮箱）
        try:
            from . import registration

            registration.set_incoming_handler(self._on_register_incoming)
        except Exception:
            pass
        self.focus_monitor = FocusMonitor(store, self.focus_timer, self)
        self.focus_monitor.away_alert.connect(self._on_away_alert)
        self.focus_timer.finished.connect(self._on_focus_finished)
        # 本地模型：启动/停止/健康检查
        self.llm_runner = LocalModelRunner(store, self)
        self._on_agenda_width(self.agenda.width())
        self._wire()
        self._shortcuts()
        self.refresh_all()
        if initial_date is not None:
            self.calendar_page.select_date(initial_date)
        try:
            self._middle_visible = (
                (self.store.get_setting("pane_middle", "1") or "1") != "0")
            self._dock_visible = (
                (self.store.get_setting("pane_dock", "1") or "1") != "0")
            self._apply_panes()
        except Exception:
            self._middle_visible = True
            self._dock_visible = True
        try:
            self._panes_swapped = (
                (self.store.get_setting("layout_swapped", "0") or "0") == "1")
            self._apply_pane_layout()
        except Exception:
            self._panes_swapped = False
        # 互换后用户自己拖出来的页面栏宽度（0 = 用默认）
        try:
            self._middle_user_width = int(
                float(self.store.get_setting("middle_width", "0") or 0))
        except Exception:
            self._middle_user_width = 0
        # 启动时的 navigate 只是选页面，别把用户关掉的中栏又叫回来
        self._booting = True
        self.navigate(initial_page)
        self._booting = False

        self.reminders = ReminderCenter(store, self)
        self.reminders.data_changed.connect(self.refresh_all)
        self.reminders.start()

        # Agent 盯邮箱：指定发件人的新邮件 → 识别待办 → 等用户确认
        try:
            from .mail_watch import MailWatcher

            self.mail_watch = MailWatcher(store, self)
            self.mail_watch.found.connect(self._on_mail_watch_found)
            self.mail_watch.start()
        except Exception:
            self.mail_watch = None

        # 教务通知监控
        self._notice_toasts = []
        self.notice_watcher = NoticeWatcher(store, self)
        self.notice_watcher.new_notices.connect(self._on_new_notices)
        self.notice_watcher.start()

        # 使用情况上报（不走邮件）；管理员这份程序顺便起一个小监听
        self._usage_started = time.time()
        self.usage_server = None
        try:
            from . import usage_report

            # AI 面板每完成一次调用就记一笔（TTFT / 出字速度）
            panel = getattr(getattr(self.dock, "ai", None), "api_panel", None)
            if panel is not None:
                panel.ai_metrics.connect(self._on_ai_metrics)
            self._usage_timer = QTimer(self)
            self._usage_timer.setInterval(usage_report.interval_minutes(store) * 60_000)
            self._usage_timer.timeout.connect(self._report_usage)
            self._usage_timer.start()
            QTimer.singleShot(20_000, self._report_usage)
            from . import adminsign

            if not adminsign.install_is_client(store):
                server = usage_report.UsageServer(store)
                if server.start():
                    self.usage_server = server
        except Exception:
            pass

        if show_welcome:
            QTimer.singleShot(900, self._welcome)

    # -------------------------------------------------- 使用情况上报
    def _on_ai_metrics(self, metrics: Dict[str, Any]) -> None:
        """每完成一次 API 调用就记一笔（时长 + TTFT + 出字速度）。"""
        try:
            from . import usage_report

            usage_report.mark_activity(
                store=self.store,
                requests=1,
                api_calls=1,
                ttft=(metrics or {}).get("ttft"),
                tok_s=(metrics or {}).get("tokens_per_second"))
        except Exception:
            pass

    def _report_usage(self) -> None:
        """后台线程发一份上报，回来后按管理员的处置动作提示 / 锁定。

        省开销的两点：
        * 只把「窗口真的露在外面」的时间算使用时长（最小化 / 隐藏时不算），
          这样数字不变时 `usage_report.send` 会直接跳过，不发请求；
        * 但每 3 个间隔（默认 30 分钟）强发一次心跳，管理员那边照样能看出在线。
        """
        import threading

        elapsed = max(0.0, time.time() - getattr(self, "_usage_started", time.time()))
        self._usage_started = time.time()
        try:
            active = bool(self.isVisible()) and not self.isMinimized()
        except Exception:
            active = True
        seconds = elapsed if active else 0.0

        def work() -> None:
            try:
                from . import usage_report

                usage_report.mark_activity(store=self.store, seconds=seconds)
                interval = usage_report.interval_minutes(self.store) * 60
                last_ok = float(getattr(self, "_usage_last_ok", 0.0) or 0.0)
                force = (time.time() - last_ok) > interval * 3
                result = usage_report.send(self.store, force=force)
                if result.get("ok"):
                    self._usage_last_ok = time.time()
                text = usage_report.apply_action(self.store, result)
                if text:
                    self._ui_notice(text)
            except Exception:
                pass

        threading.Thread(target=work, name="lumina-usage-report", daemon=True).start()

    def _on_register_incoming(self, info) -> None:
        """客户端直接 POST 过来的注册申请：提醒管理员去审核（不用翻邮箱）。"""
        name = str((info or {}).get("username") or "（未知）")
        try:
            self.snackbar.show_message(
                f"收到注册申请：{name}（网络直连）→ 设置 → 账户审核 → 检查新申请")
        except Exception:
            pass

    def _on_literature_results(self, papers) -> None:
        """agent 搜到文献：直接铺到右侧「文献」面板，并切过去让用户勾选。"""
        items = [item for item in (papers or []) if item.get("id")]
        if not items:
            return
        try:
            self.dock.lit.set_papers(items)
            self.dock.set_tab("lit")
            self.snackbar.show_message(
                f"找到 {len(items)} 篇相关文献：在右侧「文献」里勾选后可一键下载 / 引用")
        except Exception:
            pass
        try:
            self.notices_page.refresh_counts() if hasattr(
                self.notices_page, "refresh_counts") else None
        except Exception:
            pass

    def _compose_from_agent(self, payload) -> Optional[Dict[str, Any]]:
        """agent 要发信：弹一个**可编辑**的确认窗口，返回用户改后的内容。"""
        from PySide6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout

        from .widgets import BaseDialog, SoftButton

        data = dict(payload or {})
        try:
            dialog = BaseDialog(self, width=620, height=440, title="确认这封邮件",
                                subtitle="AI 起草的邮件，你可以先改再发",
                                icon=icons.MAIL)
            body = QVBoxLayout()
            body.setSpacing(6)
            body.addWidget(QLabel("收件人"))
            to_edit = QLineEdit(str(data.get("to") or ""))
            body.addWidget(to_edit)
            body.addWidget(QLabel("主题"))
            subject_edit = QLineEdit(str(data.get("subject") or ""))
            body.addWidget(subject_edit)
            body.addWidget(QLabel("正文"))
            text_edit = QPlainTextEdit(str(data.get("text") or ""))
            text_edit.setMinimumHeight(180)
            body.addWidget(text_edit)
            dialog.body.addLayout(body)

            cancel = SoftButton("取消", "ghost", height=34, min_width=80)
            send = SoftButton("发送", "primary", height=34, min_width=100)
            answer: Dict[str, Any] = {}

            def do_send() -> None:
                answer["value"] = {
                    "to": to_edit.text().strip(),
                    "subject": subject_edit.text().strip(),
                    "text": text_edit.toPlainText(),
                }
                dialog.accept()

            cancel.clicked.connect(dialog.reject)
            send.clicked.connect(do_send)
            dialog.add_footer([], [cancel, send])
            dialog.exec()
            return answer.get("value")
        except Exception:
            return None

    def _open_ehall_url(self, url: str = "") -> None:
        """仪表板里的 ehall 入口：切到 eHall 页并打开指定地址。"""
        self.navigate(5)
        target = str(url or "").strip()
        if not target:
            return
        try:
            self.ehall_page.web.load(target)
        except Exception:
            pass

    def _open_homework_url(self, url: str = "") -> None:
        """仪表板里的作业链接：切到「作业」页（eLearning）并打开这个地址。"""
        self.navigate(4)
        target = str(url or "").strip()
        if not target:
            return
        try:
            self.homework_page.web.load(target)
        except Exception:
            pass

    # -------------------------------------------------- eLearning 截止 → 日程
    def _maybe_sync_deadlines(self, force: bool = False) -> None:
        """隔一天把 dash 里的作业截止写进日程（后台线程，别卡界面）。"""
        import os

        from . import elearn_schedule

        if os.environ.get("LUMINA_SMOKE"):
            return                     # 测试不联网
        if not elearn_schedule.should_sync(self.store, force=force):
            return
        import threading

        def work() -> None:
            try:
                result = elearn_schedule.sync(self.store, force=True)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "added": 0, "skipped": 0,
                          "error": "写日程失败：%s" % exc}
            try:
                self.deadlines_synced.emit(result)
            except Exception:
                pass

        threading.Thread(target=work, name="lumina-elearn-calendar",
                         daemon=True).start()

    def _on_deadlines_synced(self, result) -> None:
        """写完之后刷新日历页，并在作业页说明结果。"""
        from . import elearn_schedule

        result = dict(result or {})
        note = elearn_schedule.summary(result)
        try:
            if result.get("added"):
                self.calendar_page.reload()
        except Exception:
            pass
        try:
            self.homework_page.subtitle.setText(note)
        except Exception:
            pass

    def _ask_endnote_install(self, hint) -> None:
        """没装 EndNote：问用户要不要现在去 ehall 装（用户要求）。"""
        message = str((hint or {}).get("message") or "")
        ask = str((hint or {}).get("ask") or "要不要现在带你到 ehall？")
        try:
            from .widgets import ConfirmDialog

            dialog = ConfirmDialog(self, "要装 EndNote 吗？",
                                   f"{message}\n\n{ask}", "带我去 ehall",
                                   "先不用", danger=False)
            dialog.exec()
            if dialog.confirmed:
                url = str((hint or {}).get("software") or "https://ehall.fudan.edu.cn/")
                self.open_web_url(url)
                self.snackbar.show_message("已在 eHall 网页里打开，登录后到「正版软件」下载安装")
        except Exception as exc:  # noqa: BLE001
            try:
                self.snackbar.show_message(f"打开 ehall 失败：{exc}")
            except Exception:
                pass

    def open_web_url(self, url: str) -> bool:
        """给 agent 的 library_open 用：在应用内的 eHall 网页里打开一个地址。

        需要登录 / 人机验证的数据库页面就这样交给用户自己过验证。
        """
        text = str(url or "").strip()
        if not text.lower().startswith(("http://", "https://")):
            return False
        try:
            index = self.pages.indexOf(self.ehall_page)
            if index < 0:
                return False
            self.navigate(index)
            panel = getattr(self.ehall_page, "web", None)
            if panel is None:
                return False
            panel.ensure_view()
            panel.load(text)
            return True
        except Exception:
            return False

    def _start_focus_from_agent(self, minutes) -> bool:
        """agent 的 pomodoro_start 工具调它：在 GUI 线程里真的开始专注计时。"""
        try:
            value = float(minutes or 25)

            def start() -> None:
                try:
                    self.focus_timer.start(value)
                except Exception:
                    self.focus_timer.start()

            QTimer.singleShot(0, start)
            return True
        except Exception:
            return False

    def _ui_notice(self, text: str) -> None:
        try:
            self.snackbar.show_message(text)
        except Exception:
            pass

    # -------------------------------------------------- 连接
    def _wire(self) -> None:
        self.sidebar.navigate.connect(self.navigate)
        self.sidebar.new_event.connect(self._new_event)
        self.sidebar.new_mail.connect(lambda: self.open_compose(None))
        self.sidebar.focus_toggle.connect(self.toggle_focus)
        self.sidebar.ai_requested.connect(self.show_ai_panel)
        self.sidebar.vpn_requested.connect(self.show_vpn_settings)
        self.calendar_page.new_event_requested.connect(self._new_event_on)
        self.calendar_page.edit_event_requested.connect(self.open_event_dialog)
        self.calendar_page.view_event_requested.connect(self.show_event_detail)
        self.calendar_page.data_changed.connect(self.refresh_all)
        self.mailbox_page.compose_requested.connect(self.open_compose)
        self.mailbox_page.request_settings.connect(lambda: self.navigate(8))
        self.mailbox_page.data_changed.connect(self.refresh_all)
        self.homework_page.open_document.connect(self.open_document)
        self.settings_page.notify.connect(self.snackbar.show_message)
        self.settings_page.settings_saved.connect(self._on_settings_saved)
        self.settings_page.appearance_changed.connect(self.reload_interface)
        self.fullscreen_changed.connect(self._on_fullscreen_changed)

    def _shortcuts(self) -> None:
        self._shortcut_refs = []
        entries = [
            ("Ctrl+N", self._new_event),
            ("Ctrl+Shift+N", lambda: self.open_compose(None)),
            ("Ctrl+1", lambda: self.navigate(0)),
            ("Ctrl+2", lambda: self.navigate(1)),
            ("Ctrl+3", lambda: self.navigate(2)),
            ("Ctrl+4", lambda: self.navigate(3)),
            ("Ctrl+5", lambda: self.navigate(4)),
            ("Ctrl+6", lambda: self.navigate(5)),
            ("Ctrl+7", lambda: self.navigate(6)),
            ("Ctrl+8", lambda: self.navigate(7)),
            ("Ctrl+9", lambda: self.navigate(8)),
            ("Ctrl+0", lambda: self.navigate(9)),      # 文献库
            ("Ctrl+Shift+A", self.show_ai_panel),
            ("Ctrl+Shift+G", self.open_agent),
            ("Ctrl+Shift+L", self.swap_panes),
            ("Ctrl+R", self.refresh_all),
            ("Esc", self._on_escape),
            # 按位置开合左右两栏（和标题栏那两个 ✕ 一致）
            ("Ctrl+Alt+M", lambda: self.toggle_pane_slot("left")),
            ("Ctrl+Alt+R", lambda: self.toggle_pane_slot("right")),
        ]
        for sequence, handler in entries:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)
            self._shortcut_refs.append(shortcut)

    # -------------------------------------------------- 导航
    def navigate(self, index: int) -> None:
        index = max(0, min(self.pages.count() - 1, int(index)))
        self.middle_stack.setCurrentWidget(self.pages)
        self.pages.setCurrentIndex(index)
        self.sidebar.set_active_page(index)
        # 切到文献库就顺手刷一下（下载/导出之后能看到最新状态）
        if index == 9:
            try:
                self.lit_page.reload()
            except Exception:
                pass
        # 切到仪表板就把天气/日程刷新一遍（缓存 10 分钟，不会频繁联网）
        if index == 10:
            try:
                self.dashboard_page.refresh()
            except Exception:
                pass
        # 点导航就把中栏叫回来（否则被关掉之后看起来像“界面不见了”）
        # 但启动时的那一次不算，否则关掉中栏后重启又自己冒出来
        if (not getattr(self, "_booting", False)
                and not getattr(self, "_middle_visible", True)):
            self._middle_visible = True
            self._apply_panes()
            self._save_pane_state()

    def _on_agenda_width(self, width: int) -> None:
        """右侧栏展开/收起（看板娘现在是独立桌面窗口，不受影响）。"""
        self._agenda_width = int(width)

    # -------------------------------------------------- 分栏拖拽
    def _on_pane_split_begin(self) -> None:
        """按下拖拽条：记下起始宽度，整段拖拽都以它为基准。

        拖的永远是**右边那一栏**（默认布局是日程 / AI，互换后是页面）：
        鼠标往右 → 右边那栏变窄 → 分割线跟着鼠标往右走；往左则反过来。
        """
        if getattr(self, "_panes_swapped", False):
            self._split_origin = int(self.middle_stack.width())
        else:
            self._split_origin = int(self.dock.width())
        self.pane_splitter.set_base(self._split_origin)

    def _on_pane_split_drag(self, target: int) -> None:
        """拖拽中：实时改宽度，不写设置。"""
        if getattr(self, "_panes_swapped", False):
            try:
                self.middle_stack.setFixedWidth(max(260, min(900, int(target))))
            except Exception:
                return
            return
        self.dock.set_user_width(int(target))

    def _on_pane_split_finish(self, target: int) -> None:
        """松手：固定住最终宽度，并把设置写回去。"""
        if getattr(self, "_panes_swapped", False):
            width = max(260, min(900, int(target)))
            self._middle_user_width = width
            try:
                self.middle_stack.setFixedWidth(width)
                self.store.set_setting("middle_width", str(width))
            except Exception:
                pass
            return
        self.dock.set_user_width(int(target), save=True)

    def _on_pane_split_reset(self) -> None:
        """双击拖拽条：恢复默认宽度。"""
        try:
            if getattr(self, "_panes_swapped", False):
                self._middle_user_width = 0
                self.store.set_setting("middle_width", "0")
                self._apply_panes()
            else:
                self.dock.reset_width()   # 回到默认宽度并写回设置
                self._apply_panes()
        except Exception:
            pass
        self._on_pane_split_begin()

    def refresh_all(self) -> None:
        if self._closed:
            return
        # 过了结束时间的日程自动完成
        try:
            if (self.store.get_setting("auto_done_enabled", "1") or "1") == "1":
                self.store.auto_complete_past()
        except Exception:
            pass
        self.calendar_page.refresh()
        self.mailbox_page.refresh()
        try:
            self.notices_page.reload()
        except Exception:
            pass
        try:
            self.agenda.refresh()
        except Exception:
            pass
        self.sidebar.refresh_counts()
        self._refresh_status()

    def _refresh_status(self) -> None:
        cfg = SmtpConfig.from_store(self.store)
        if cfg.is_configured():
            self.title_bar.status_chip.set_status(
                "邮箱已连接", SUCCESS,
                f"SMTP：{cfg.host}:{cfg.port} · 点击前往设置",
            )
        else:
            self.title_bar.status_chip.set_status(
                "邮箱未配置", WARNING, "尚未配置发件邮箱 · 点击前往设置"
            )

    # -------------------------------------------------- 日程详情（右侧栏）
    def show_event_detail(self, event_id: int) -> None:
        """单击日程卡片：按当前面板状态决定卡片落在哪。

        * 中栏和右栏都在 / 只有中栏 / 只有右栏 → 都放到**右侧边栏**；
        * 两个都关着 → 先在中栏打开一张卡片（左+中占满），之后再开就回到右侧边栏。
        """
        try:
            event = self.store.get_event(int(event_id))
        except Exception:
            event = None
        if event is None:
            return
        middle = bool(getattr(self, "_middle_visible", True))
        dock = bool(getattr(self, "_dock_visible", True))
        if not middle and not dock:
            # 情况 3：什么都没有 → 在中栏打开
            self.middle_stack.setCurrentWidget(self.middle_detail)
            self.show_middle_detail(event)
            return
        # 情况 1/2：右侧边栏打开（如果关着就重新打开），详情放里面
        self._dock_visible = True
        self._apply_panes()
        try:
            self.dock.show_agenda()
            self.dock.agenda.show_event_detail(event)
        except Exception:
            pass
    # -------------------------------------------------- 日程
    def _new_event(self) -> None:
        if self.pages.currentIndex() == 0:
            date = self.calendar_page.selected_date()
        else:
            date = QDate.currentDate()
        self.open_event_dialog(date=date)

    def _new_event_on(self, date: QDate) -> None:
        self.open_event_dialog(date=date)

    def open_event_dialog(self, event_id: Optional[int] = None,
                          date: Optional[QDate] = None) -> None:
        event = None
        if event_id:
            event = self.store.get_event(int(event_id))
            if event is None:
                self.snackbar.show_message("这条日程已经不存在了", "warn")
                self.refresh_all()
                return
        dialog = EventDialog(self.store, self, event=event, date=date)
        dialog.saved.connect(lambda _id: self._after_change("日程已保存"))
        dialog.deleted.connect(lambda _id: self._after_change("日程已删除"))
        dialog.exec()
        self.refresh_all()

    def _after_change(self, message: str) -> None:
        self.refresh_all()
        self.snackbar.show_message(message, "success")

    # -------------------------------------------------- 邮件
    def open_compose(self, mail=None) -> None:
        dialog = ComposeDialog(self.store, self, mail=mail)
        dialog.sent.connect(self._on_mail_sent)
        dialog.draft_saved.connect(self._on_draft_saved)
        dialog.request_settings.connect(lambda: self.navigate(8))
        dialog.exec()
        self.refresh_all()

    def _on_mail_sent(self, mail_id: int) -> None:
        self.refresh_all()
        self.navigate(1)
        self.mailbox_page.select_mail(mail_id)
        self.snackbar.show_message("邮件已发送，收件人将收到汇报内容", "success", 3200)

    def _on_draft_saved(self, mail_id: int) -> None:
        self.refresh_all()
        self.navigate(1)
        self.mailbox_page.select_mail(mail_id)
        self.snackbar.show_message("草稿已保存到邮箱", "info", 2600)

    def _on_settings_saved(self) -> None:
        self.refresh_all()
        watcher = getattr(self, "mail_watch", None)
        if watcher is not None:
            try:
                watcher.restart()
            except Exception:
                pass

    # -------------------------------------------------- 盯邮箱
    def _on_mail_watch_found(self, mails, items) -> None:
        """Agent 盯到邮件里的待办：弹确认窗口，用户确认才写日程。"""
        try:
            from .agent_dialog import AgentDialog
            from .mail_watch import consume

            dialog = AgentDialog(self.store, self)
            subjects = "、".join(
                str(mail.get("subject") or "")[:24] for mail in (mails or [])[:3])
            dialog.present_items(items, note=f"来自邮箱：{subjects}")
            dialog.changed.connect(self.refresh_all)
            dialog.exec()
            consume(self.store, list(mails or []))
        except Exception:
            pass


    # -------------------------------------------------- 磨砂玻璃
    def _apply_glass(self) -> None:
        """按当前外观设置给窗口加「磨砂」效果。

        做法是自己抓屏 + 高斯近似模糊（不依赖 DWM Acrylic）：抓取窗口背后的桌面
        （窗口自身先用 WDA_EXCLUDEFROMCAPTURE 排除在截屏外），模糊后当作外壳背景，
        再叠上半透明主题色。

        关键点：**不透明窗口也能用**。窗口不开 WA_TranslucentBackground（不开分层窗口），
        就不会出现「内嵌网页点击穿透到其它程序」的毛病；玻璃感完全靠这张模糊截图 +
        主题色，圆角和投影交给 DWM（`set_round_corners` / `enable_system_shadow`）。
        """
        state = theme.appearance()
        self._glass_active = bool(state.glass)
        self._sync_frame()
        self._apply_window_frame()
        if self._glass_active:
            self._refresh_backdrop()
            if not self._backdrop_timer.isActive():
                self._backdrop_timer.start(300)
        else:
            self._backdrop_timer.stop()
            try:
                self._backdrop_poll.stop()
            except Exception:
                pass
            self.shell.clear_backdrop()

    def _apply_window_frame(self) -> None:
        """圆角 / 系统投影：不透明窗口用 DWM 的，半透明窗口靠自己的阴影画。"""
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        if getattr(self, "_translucent", False):
            win_effects.set_round_corners(hwnd, False)
            win_effects.enable_system_shadow(hwnd, False)
            return
        win_effects.set_round_corners(hwnd, not self._maximized)
        win_effects.enable_system_shadow(hwnd, bool(self._glass_active)
                                         and not self._maximized)
        # 不透明窗口不该是分层窗口（分层 + Chromium 原生子窗口会点击穿透）
        self._keep_opaque()

    def _fade_running(self) -> bool:
        """启动淡入动画是否还在跑（跑着的时候窗口必须是分层的）。

        加一个 1 秒的硬上限：动画万一没收到 finished（隐藏窗口 / 远程桌面等），
        也不至于永远不敢把窗口恢复成「不透明」。
        """
        try:
            started = float(getattr(self, "_fade_started_at", 0.0) or 0.0)
            if started and time.monotonic() - started > 1.0:
                return False
            animation = getattr(self, "_fade_anim", None)
            if animation is None:
                return False
            return animation.state() == QAbstractAnimation.State.Running
        except Exception:
            return False

    def _keep_opaque(self) -> None:
        """不透明窗口：确保不透明度是 1、并且没有残留的分层样式。

        Qt 给窗口做 `setWindowOpacity` 动画时会加上 WS_EX_LAYERED，动画结束
        不一定去掉；分层窗口 + Chromium 原生子窗口会出现「点击穿透到别的程序」，
        所以这里反复兜一下（便宜且幂等）。
        """
        if getattr(self, "_translucent", False) or self._fade_running():
            return
        try:
            if float(self.windowOpacity()) < 0.999:
                self.setWindowOpacity(1.0)
        except Exception:
            pass
        try:
            win_effects.clear_layered(int(self.winId()))
        except Exception:
            pass

    def _refresh_backdrop(self) -> None:
        """抓取窗口背后的桌面并做模糊，作为磨砂背景。

        注意：抓屏在 GUI 线程跑，必须防重入——上一次还没抓完（窗口刚拖过、
        Chromium 正在合成）就不要再抓，否则会互相卡住，界面看起来像死掉。
        """
        self._keep_opaque()
        if not self._glass_active:
            return
        if not self.isVisible() or self.isMinimized():
            return
        if getattr(self, "_backdrop_busy", False):
            return
        try:
            if QApplication.activeModalWidget() is not None:
                return          # 弹窗挡着，没必要抓
        except Exception:
            pass
        self._backdrop_busy = True
        try:
            self._grab_backdrop()
        finally:
            self._backdrop_busy = False

    def _grab_backdrop(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geometry = self.geometry()
        if geometry.width() < 16 or geometry.height() < 16:
            return
        # 抓屏前把自己从截屏里排除，抓完立刻恢复——这样系统/第三方截图仍然能拍到窗口
        hwnd = 0
        try:
            hwnd = int(self.winId())
            win_effects.exclude_from_capture(hwnd, True)
        except Exception:
            hwnd = 0
        try:
            shot = screen.grabWindow(0)
        finally:
            if hwnd:
                try:
                    win_effects.exclude_from_capture(hwnd, False)
                except Exception:
                    pass
        if shot.isNull():
            return
        ratio = float(shot.devicePixelRatio() or 1.0)
        crop = QRect(
            int(geometry.x() * ratio),
            int(geometry.y() * ratio),
            max(1, int(geometry.width() * ratio)),
            max(1, int(geometry.height() * ratio)),
        )
        cropped = shot.copy(crop)
        if cropped.isNull():
            cropped = shot
        radius = float(theme.appearance().blur)
        if radius < 1.0:
            # 模糊度调到 0：只保留透明度，不铺模糊背景
            self.shell.clear_backdrop()
            return
        # 先缩小再模糊：模糊本来就看不清楚细节，缩一半能省 4 倍像素量
        small = cropped
        try:
            small = cropped.scaled(
                max(1, cropped.width() // 2), max(1, cropped.height() // 2),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        except Exception:
            small = cropped
        self.shell.set_backdrop(
            win_effects.blur_pixmap(small, max(2.0, radius * 0.55), 2)
        )

    def _queue_backdrop_refresh(self) -> None:
        if self._glass_active and not getattr(self, "_reloading", False):
            self._backdrop_timer.start(400)

    def _sync_frame(self) -> None:
        """统一维护外边距 / 圆角 / 阴影。

        * 不透明窗口（默认）：外壳铺满整个窗口，不要外边距、圆角、投影 ——
          否则投影会画在窗口内部，四周出现一圈深色带，底部内容也被挤掉；
        * 半透明窗口：浮起卡片 + 外边距 + 柔和阴影（可选，代价是网页点不动）。
        """
        if not getattr(self, "_translucent", True):
            self.layout().setContentsMargins(0, 0, 0, 0)
            self.shell.configure(radius=0, shadow=0, glow=False)
            return
        if self._maximized:
            margin, radius, shadow = 0, 0.0, 0
        elif self._glass_active:
            margin, radius, shadow = 0, theme.WINDOW_RADIUS, 0
        else:
            margin, radius, shadow = self._margin, theme.WINDOW_RADIUS, 18
        self.layout().setContentsMargins(margin, margin, margin, margin)
        self.shell.configure(radius=radius, shadow=shadow, glow=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_fade_done", False) and not getattr(self, "_reloading", False):
            # 缩放时只重排外壳和圆角，磨砂背景延后 400ms 再抓（别每帧抓屏）
            try:
                self._apply_window_frame()
            except Exception:
                pass
            self._queue_backdrop_refresh()

    def moveEvent(self, event):
        super().moveEvent(event)
        # 拖动窗口时不重抓背景：模糊图是静态的，看不出错位，抓多了反而卡

    def reload_interface(self, message: str = "外观已更新") -> None:
        """应用新外观。

        以前是「新建一个 MainWindow 再关掉旧的」，但那样会同时存在两套 QtWebEngine
        视图（同一个 profile），保存设置时容易整个进程闪退，所以改成**原地重刷**：
        重新应用全局样式表 + 各页面重绘 + 重排外壳，不销毁窗口。
        """
        self.preview_appearance(message)

    def preview_alpha(self, alpha: float) -> None:
        """只重绘窗口来预览不透明度（拖滑块时用，别做重活）。

        注意不能只 `shell.update()`：父控件重绘会把子控件盖掉，出现「文字重叠」
        那种花屏；让顶层窗口整体 update，Qt 会把子控件一起重画。
        """
        try:
            theme.set_alpha(alpha)
            self.update()
        except Exception:
            pass

    def _nudge_webviews(self) -> None:
        """外观/排版变了以后，让内嵌网页的原生子窗口重画一次（不然可能一片空白）。"""
        holders = [getattr(self, name, None) for name in
                   ("homework_page", "ehall_page", "docs_page")]
        dock = getattr(self, "dock", None)
        if dock is not None:
            holders.append(getattr(dock, "ai", None))
        for holder in holders:
            web = getattr(holder, "web", None)
            handler = getattr(web, "_nudge_view", None)
            if callable(handler):
                try:
                    handler()
                except Exception:
                    pass

    def preview_appearance(self, message: str = "", refresh_pages: bool = True) -> None:
        """就地应用当前外观设置（不重建窗口，可反复调用）。

        `refresh_pages=False` 时只重刷主题与外壳，不重读各页面的数据 ——
        设置页做「实时预览」时用这个，免得把用户正在填的表单重置掉。
        """
        if getattr(self, "_reloading", False):
            return
        self._reloading = True
        try:
            # 重建期间别抓屏（抓屏 + Chromium 合成一起跑容易互相卡）
            self._backdrop_busy = True
            try:
                self._backdrop_timer.stop()
                self._backdrop_poll.stop()
            except Exception:
                pass
            theme.apply_theme(QApplication.instance())
            self._apply_glass()
            try:
                self.shell.update()
                self.update()
            except Exception:
                pass
            # 各页面里缓存的颜色：能刷的就刷一遍
            pages = (self.calendar_page, self.mailbox_page, self.notices_page,
                     self.memos_page, self.homework_page, self.ehall_page,
                     self.docs_page, self.latex_page, self.settings_page)
            for page in (pages if refresh_pages else ()):
                for method in ("refresh", "reload"):
                    handler = getattr(page, method, None)
                    if callable(handler):
                        try:
                            handler()
                        except Exception:
                            pass
                        break
            if message:
                try:
                    self.snackbar.show_message(message, "success", 2400)
                except Exception:
                    pass
            if refresh_pages:
                # 实时预览（refresh_pages=False）不碰内嵌网页：那一步会去动 Chromium，
                # 拖滑杆时没必要，也容易把界面拖慢
                self._nudge_webviews()
        finally:
            self._backdrop_busy = False
            self._reloading = False
            # 外观稳定后再补一张新的磨砂背景
            try:
                self._backdrop_timer.start(400)
            except Exception:
                pass

    def toggle_appearance(self) -> None:
        """在浅色 / 深色之间切换并立即生效。"""
        target = theme.toggle_mode()
        theme.save_to_store(self.store)
        theme.apply_theme(QApplication.instance())
        self.reload_interface(
            "已切换到深色外观" if target == "dark" else "已切换到浅色外观"
        )

    # -------------------------------------------------- 面板全屏
    def _on_escape(self) -> None:
        if self._fs_widget is not None or getattr(self, "_chrome_hidden", False):
            self.set_panel_fullscreen(False)
            for page in (getattr(self, "docs_page", None),
                         getattr(getattr(self, "homework_page", None), "web", None),
                         getattr(getattr(getattr(self, "dock", None), "ai", None),
                                 "web", None)):
                setter = getattr(page, "sync_fullscreen_state", None)
                if callable(setter):
                    try:
                        setter(False)
                    except Exception:
                        pass

    def set_panel_fullscreen(self, on: bool, widget=None) -> None:
        """面板全屏：把标题栏 / 左导航 / 另一侧都收起来，Esc 退出。

        两种目标：
          * 页面里面板（文档阅读器、作业内嵌网页）→ 藏掉左右栏，页面自己铺满；
          * 右侧 dock 里的 AI → 藏掉页面，让 dock 变成整窗宽度。
        """
        try:
            self._fs_widget = widget if on else None
            dock_target = bool(on and widget is not None
                               and self._widget_in_dock(widget))
            if on:
                self.title_bar.setVisible(False)
                self.sidebar.setVisible(False)
                if dock_target:
                    self.pages.setVisible(False)
                    self.dock.setVisible(True)
                    self.dock.set_fullscreen(True)
                else:
                    self.pages.setVisible(True)
                    self.dock.setVisible(False)
                    self.dock.set_fullscreen(False)
                try:
                    self.mascot.hide()
                except Exception:
                    pass
                self._chrome_hidden = True
            else:
                self.dock.set_fullscreen(False)
                self.pages.setVisible(True)
                self._chrome_hidden = False
                self.set_chrome_visible(True)
            self.fullscreen_changed.emit(bool(on))
        except Exception:
            pass

    def _widget_in_dock(self, widget) -> bool:
        dock = getattr(self, "dock", None)
        node = widget
        while node is not None:
            if node is dock:
                return True
            try:
                node = node.parentWidget()
            except Exception:
                return False
        return False

    def set_chrome_visible(self, visible: bool) -> None:
        for widget in (getattr(self, "title_bar", None),
                       getattr(self, "sidebar", None),
                       getattr(self, "dock", None)):
            if widget is None:
                continue
            try:
                widget.setVisible(bool(visible))
            except Exception:
                pass
        if visible:
            # 退出全屏时恢复用户自己的折叠选择
            try:
                self.sidebar.setVisible(bool(getattr(self, "_sidebar_open", True)))
            except Exception:
                pass
            try:
                self.mascot.setVisible(
                    (self.store.get_setting("mascot_on", "1") or "1") != "0")
            except Exception:
                pass
        else:
            try:
                self.mascot.hide()
            except Exception:
                pass
        self._chrome_hidden = not visible

    # -------------------------------------------------- 专注模式
    def toggle_focus(self) -> None:
        if self.focus_monitor.is_running():
            self._end_focus()
            return
        # 先让用户选「不定时」还是「定时多少分钟」
        try:
            menu = FocusStartMenu(self)
            menu.chosen.connect(self.start_focus)
            anchor = self.sidebar.focus_button.mapToGlobal(
                self.sidebar.focus_button.rect().topRight())
            menu.show_at(QPoint(anchor.x() + 8, anchor.y()))
            self._focus_menu = menu
        except Exception:
            self.start_focus(None)

    def start_focus(self, minutes=None) -> None:
        try:
            self.focus_timer.start(minutes)
        except Exception:
            pass
        self.focus_monitor.start()
        try:
            self.sidebar.focus_button.setText("结束专注")
        except Exception:
            pass
        mode = f"定时 {int(minutes)} 分钟" if minutes else "不定时"
        limit = self.focus_monitor.away_minutes()
        self.snackbar.show_message(
            f"专注模式开始（{mode}）· 离开超过 {limit} 分钟会提醒你",
            "info", 4600)
        try:
            if self.mascot.is_on():
                self.mascot.say(
                    f"{int(minutes)} 分钟，冲刺！" if minutes else "开始专注啦", 2.8)
        except Exception:
            pass

    def _on_focus_finished(self, minutes: int) -> None:
        """定时模式到点了。"""
        try:
            self.focus_monitor.stop()
            self.focus_timer.stop()
        except Exception:
            pass
        try:
            self.sidebar.focus_button.setText("专注模式")
        except Exception:
            pass
        text = f"{minutes} 分钟专注完成，起来活动一下吧"
        self.snackbar.show_message(text, "success", 6000)
        try:
            if self.mascot.is_on():
                self.mascot._hop = 1.0
                self.mascot.say("时间到啦，休息一下！", 6.0)
        except Exception:
            pass

    def _end_focus(self) -> None:
        seconds = int(self.focus_timer.seconds)
        try:
            self.focus_monitor.stop()
            self.focus_timer.stop()
        except Exception:
            pass
        try:
            self.sidebar.focus_button.setText("专注模式")
        except Exception:
            pass
        self.snackbar.show_message(
            f"本次专注 {seconds // 60} 分 {seconds % 60} 秒，休息一下吧", "success", 4200)

    def _on_away_alert(self, minutes: int) -> None:
        text = f"你已经离开 {minutes} 分钟了，回来继续专注吧"
        try:
            toast = AwayToast(minutes)
            toast.back_requested.connect(
                lambda: self._focus_back(toast))
            toast.stop_requested.connect(self._end_focus)
            toast.show_at_corner()
            self._focus_toast = toast
        except Exception:
            pass
        try:
            if self.mascot.is_on():
                self.mascot._hop = 1.0
                self.mascot.say(text, 6.0)
        except Exception:
            pass
        try:
            self.snackbar.show_message(text, "warn", 5200)
        except Exception:
            pass

    def _focus_back(self, toast) -> None:
        try:
            if self.isMinimized():
                self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    # -------------------------------------------------- 面板显隐
    def toggle_middle(self, value=None) -> None:
        if value is None:
            value = not self.middle_stack.isVisible()
        self._middle_visible = bool(value)
        self._apply_panes()
        self._save_pane_state()

    def toggle_dock(self, value=None) -> None:
        if value is None:
            value = not self.dock.isVisible()
        self._dock_visible = bool(value)
        self._apply_panes()
        self._save_pane_state()

    # -------------------------------------------------- 按「位置」开合（标题栏两个按钮）
    def toggle_pane_slot(self, slot: str, value=None) -> None:
        """按屏幕位置开关某一栏。

        * 默认布局：左边是页面（中栏），右边是日程 / AI（右栏）；
        * 互换布局：左边是日程 / AI，右边是页面。

        标题栏那两个「中栏 ✕ / 右栏 ✕」说的就是屏幕上的位置，
        所以互换之后它们作用的对象也要跟着换（否则点右边那个 ✕ 反而关掉了左边）。
        """
        swapped = bool(getattr(self, "_panes_swapped", False))
        left = str(slot).lower().startswith("l")
        # 左边那栏默认是页面；互换后左边是日程 / AI
        if left:
            if swapped:
                self.toggle_dock(value)
            else:
                self.toggle_middle(value)
        else:
            if swapped:
                self.toggle_middle(value)
            else:
                self.toggle_dock(value)

    def _save_pane_state(self) -> None:
        try:
            self.store.update_settings({
                "pane_middle": "1" if self._middle_visible else "0",
                "pane_dock": "1" if self._dock_visible else "0",
            })
        except Exception:
            pass

    def _relayout_panes(self) -> None:
        """中栏 / 右栏 / 互换 的唯一排版入口。

        规则：
          * 两栏都在 → 宽的那栏给 stretch，另一栏固定宽度；
          * 只有中栏 → 中栏占满；
          * 只有右栏 → 右栏占满（primary 模式，随窗口伸缩）；
          * 都关掉 → 什么都不显示（只剩背景）。
        """
        layout = getattr(self, "content_layout", None)
        if layout is None:
            return
        middle = bool(getattr(self, "_middle_visible", True))
        dock_visible = bool(getattr(self, "_dock_visible", True))
        swapped = bool(getattr(self, "_panes_swapped", False))

        # 1) 顺序：默认 [sidebar, middle, splitter, dock]；
        #    互换后 [sidebar, dock, splitter, middle]
        try:
            layout.removeWidget(self.middle_stack)
            layout.removeWidget(self.pages)
            layout.removeWidget(self.dock)
            layout.removeWidget(self.pane_splitter)
            layout.removeWidget(self.pane_tail)
        except Exception:
            pass
        if swapped:
            layout.insertWidget(1, self.dock)
            layout.insertWidget(2, self.pane_splitter)
            layout.insertWidget(3, self.middle_stack)
        else:
            layout.insertWidget(1, self.middle_stack)
            layout.insertWidget(2, self.pane_splitter)
            layout.insertWidget(3, self.dock)
        try:
            layout.addWidget(self.pane_tail)   # 永远排在最后
        except Exception:
            pass

        # 2) 显隐（两栏都在才有拖拽条）
        try:
            self.middle_stack.setVisible(middle)
            self.dock.setVisible(dock_visible)
            self.pane_splitter.setVisible(bool(middle and dock_visible))
        except Exception:
            pass

        # 3) 宽度与拉伸
        try:
            for index in range(layout.count()):
                layout.setStretch(index, 0)
        except Exception:
            pass
        wide = None
        if middle and dock_visible:
            wide = self.dock if swapped else self.middle_stack
        elif middle:
            wide = self.middle_stack
        elif dock_visible:
            wide = self.dock
        if wide is not None:
            try:
                for index in range(layout.count()):
                    if layout.itemAt(index).widget() is wide:
                        layout.setStretch(index, 1)
                        break
            except Exception:
                pass
        else:
            # 只剩左栏：多余宽度全给尾巴占位，左栏保持贴左
            try:
                for index in range(layout.count()):
                    if layout.itemAt(index).widget() is getattr(self, "pane_tail", None):
                        layout.setStretch(index, 1)
                        break
            except Exception:
                pass

        try:
            # 右栏只有当它自己独占时才用「主区域」模式
            # 右栏独占时、或互换后右栏在中间时，都用「主区域」模式伸展
            self.dock.set_primary(bool(dock_visible and (not middle or swapped)))
        except Exception:
            pass
        try:
            if swapped and dock_visible and middle:
                # 互换后页面缩到右侧窄列（用户拖过就按拖出来的宽度）
                user_width = int(getattr(self, "_middle_user_width", 0) or 0)
                if user_width >= 260:
                    self.middle_stack.setFixedWidth(min(900, user_width))
                else:
                    self.middle_stack.setMinimumWidth(260)
                    self.middle_stack.setMaximumWidth(
                        max(360, self.dock.preferred_width()))
            else:
                self.middle_stack.setMinimumWidth(0)
                self.middle_stack.setMaximumWidth(16777215)
        except Exception:
            pass

        # 4) 页面被挤到右侧窄列时，日程页改「最近 5 天」清单
        try:
            narrow = bool(swapped and middle and dock_visible)
            self.calendar_page.set_compact(narrow)
        except Exception:
            pass

        # 5) 顶部两个开关的图标（按屏幕位置传：互换后左边是日程 / AI）
        try:
            if swapped:
                self.title_bar.set_pane_states(dock_visible, middle)
            else:
                self.title_bar.set_pane_states(middle, dock_visible)
        except Exception:
            pass

        # 6) 排版变了 → 拖拽条的基准跟着刷新
        try:
            if middle and dock_visible and not self.pane_splitter.is_dragging():
                self._on_pane_split_begin()
        except Exception:
            pass

    def _apply_panes(self) -> None:
        """按两个开关决定谁占空间；都关掉就只剩背景。"""
        self._relayout_panes()
    # -------------------------------------------------- 中间栏里的日程卡片
    def show_middle_detail(self, event: Dict[str, Any]) -> None:
        self._middle_visible = True
        self.middle_detail.set_event(event)
        self.middle_stack.setCurrentWidget(self.middle_detail)
        self._apply_panes()

    def hide_middle_detail(self) -> None:
        self.middle_stack.setCurrentWidget(self.pages)
        self._middle_visible = False
        self._apply_panes()
        self._save_pane_state()

    # -------------------------------------------------- 布局互换
    def swap_panes(self, value=None) -> None:
        """把中间栏（页面）和右侧栏（日程/AI）互换位置与宽度。"""
        if value is None:
            value = not bool(getattr(self, "_panes_swapped", False))
        self._panes_swapped = bool(value)
        try:
            self.store.set_setting("layout_swapped", "1" if value else "0")
        except Exception:
            pass
        self._apply_pane_layout()
        if hasattr(self, "dock"):
            try:
                self.dock.set_swapped(self._panes_swapped)
            except Exception:
                pass

    def _apply_pane_layout(self) -> None:
        """互换状态变化时重新排版。"""
        self._relayout_panes()
    # -------------------------------------------------- VPN 设置入口
    def show_vpn_settings(self) -> None:
        """左侧「VPN / 代理」：跳到设置页并滚到 VPN 卡片。"""
        try:
            self._middle_visible = True
            self._apply_panes()
        except Exception:
            pass
        self.navigate(8)
        try:
            self.settings_page.scroll_to_vpn()
        except Exception:
            pass

    # -------------------------------------------------- AI 面板（右侧 dock）
    def show_ai_panel(self) -> None:
        """左侧导航点「AI」：打开右侧 dock 的 AI 标签（必要时把右栏叫回来）。"""
        try:
            if not getattr(self, "_dock_visible", True):
                self._dock_visible = True
                self._apply_panes()
                self._save_pane_state()
            self.dock.show_ai()
            self.sidebar.set_active(self.sidebar.NAV_AI)
        except Exception:
            pass

    # -------------------------------------------------- AI：API 对话 / 设置入口
    def show_ai_api(self) -> None:
        """把 AI 的「API 服务」对话窗格放到中间（点设置里的「配置 API 对话」走这里）。

        做法：打开右栏的 AI 标签、切到 API 页，然后把中栏收起来，让 AI 铺满中间区域。
        再点左边导航（或标题栏「中栏」）就把页面叫回来。
        """
        try:
            if not getattr(self, "_dock_visible", True):
                self._dock_visible = True
                self._apply_panes()
                self._save_pane_state()
            self.dock.show_ai()
            ai = getattr(self.dock, "ai", None)
            if ai is not None:
                open_api = getattr(ai, "open_api_chat", None)
                if callable(open_api):
                    open_api()          # 切到「API 服务」标签
                else:
                    ai.set_tab(0)
            self.sidebar.set_active(self.sidebar.NAV_AI)
            if (not getattr(self, "_panes_swapped", False)
                    and getattr(self, "_middle_visible", True)):
                # 收掉中栏 → 右栏变成主区域铺满，就是「中栏变成 API 对话窗格」
                self.toggle_middle(False)
        except Exception:
            pass

    def open_llm_settings(self) -> None:
        """跳到「设置 → AI 接口」并滚动过去。"""
        self.navigate(8)
        page = getattr(self, "settings_page", None)
        for name in ("scroll_to_llm", "scroll_to_ai"):
            handler = getattr(page, name, None)
            if callable(handler):
                try:
                    handler()
                    return
                except Exception:
                    pass
        scroller = getattr(page, "scroll_to_card", None)
        if callable(scroller):
            try:
                scroller("llm")
            except Exception:
                pass

    def open_agent(self) -> None:
        """简洁的对话窗口：读微信消息 → 识别要加日程的事 → 确认后写进日历。"""
        try:
            from .agent_dialog import AgentDialog

            dialog = AgentDialog(self.store, self)
            dialog.changed.connect(self.refresh_all)
            dialog.exec()
        except Exception as exc:  # noqa: BLE001
            try:
                self.snackbar.show_message(f"Agent 打不开：{exc}")
            except Exception:
                pass

    # -------------------------------------------------- 左侧导航
    def toggle_sidebar(self, value=None) -> None:
        if value is None:
            value = not bool(getattr(self, "_sidebar_open", True))
        self._sidebar_open = bool(value)
        try:
            self.store.set_setting("sidebar_open", "1" if self._sidebar_open else "0")
        except Exception:
            pass
        if getattr(self, "_chrome_hidden", False):
            return
        try:
            self.sidebar.setVisible(self._sidebar_open)
            self.title_bar.sidebar_button.set_glyph(
                icons.CHEVRON_LEFT if self._sidebar_open else icons.CHEVRON_RIGHT)
            self.title_bar.sidebar_button.setToolTip(
                "收起左侧导航" if self._sidebar_open else "展开左侧导航")
        except Exception:
            pass

    def _on_fullscreen_changed(self, on: bool) -> None:
        sync = getattr(self.docs_page, "sync_fullscreen_state", None)
        if callable(sync):
            try:
                sync(on)
            except Exception:
                pass
        for holder in (self.homework_page, getattr(getattr(self, "dock", None), "ai", None)):
            web = getattr(holder, "web", None)
            sync = getattr(web, "sync_fullscreen_state", None)
            if callable(sync):
                try:
                    sync(on)
                except Exception:
                    pass
        sync = getattr(getattr(getattr(self, "dock", None), "ai", None),
                       "sync_fullscreen_state", None)
        if callable(sync):
            try:
                sync(on)
            except Exception:
                pass

    def open_document(self, path: str) -> None:
        try:
            self.docs_page.open_path(Path(path))
        except Exception:
            pass
        self.navigate(6)

    # -------------------------------------------------- 通知
    def _on_new_notices(self, items: list) -> None:
        if not items:
            return
        try:
            self.snackbar.show_message(f"教务有 {len(items)} 条新通知", "info", 4200)
        except Exception:
            pass
        try:
            toast = make_notice_toast(items, self)
            toast.destroyed.connect(lambda: self._notice_toasts.remove(toast)
                                    if toast in self._notice_toasts else None)
            self._notice_toasts.append(toast)
            while len(self._notice_toasts) > 2:
                self._notice_toasts.pop(0).close()
            toast.show()
            toast.raise_()
        except Exception:
            pass
        try:
            self.notices_page.reload()
        except Exception:
            pass
        try:
            self.settings_page._refresh_notice_list()
        except Exception:
            pass

    # -------------------------------------------------- 欢迎语
    def _welcome(self) -> None:
        if self._closed or not self.isVisible():
            return
        cfg = SmtpConfig.from_store(self.store)
        events = self.store.events_on(iso(QDate.currentDate()))
        if not cfg.is_configured():
            self.snackbar.show_message(
                "欢迎使用 DanLume · 先在「设置」中配置发件邮箱即可发送汇报",
                "warn", 4600,
            )
        elif events:
            self.snackbar.show_message(
                f"欢迎回来 · 今天有 {len(events)} 个日程等待完成", "info", 3200
            )
        else:
            self.snackbar.show_message("欢迎回来 · 今天还没有安排", "info", 2600)

    # -------------------------------------------------- 窗口状态
    def toggle_maximize(self) -> None:
        if self._maximized:
            self._maximized = False
            if self._restore_geometry is not None:
                self.setGeometry(self._restore_geometry)
            self.title_bar.set_maximized(False)
        else:
            self._restore_geometry = self.geometry()
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is None:
                return
            self._maximized = True
            self.setGeometry(screen.availableGeometry())
            self.title_bar.set_maximized(True)
        self._apply_glass()

    def _edges_at(self, pos: QPoint) -> Qt.Edge:
        if self._maximized:
            return Qt.Edge(0)
        # 磨砂模式下窗口没有外边距，仍保留一圈用于拖动缩放的热区
        margin = max(6, self._margin) if self._glass_active else self._margin
        edges = Qt.Edge(0)
        if pos.x() <= margin:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - margin:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= margin:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, event):
        edges = self._edges_at(event.position().toPoint())
        if edges:
            if (edges & Qt.Edge.LeftEdge and edges & Qt.Edge.TopEdge) or (
                edges & Qt.Edge.RightEdge and edges & Qt.Edge.BottomEdge
            ):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif (edges & Qt.Edge.RightEdge and edges & Qt.Edge.TopEdge) or (
                edges & Qt.Edge.LeftEdge and edges & Qt.Edge.BottomEdge
            ):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(event.position().toPoint())
            if edges:
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemResize(edges)
                    return
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)

    def _after_fade_in(self) -> None:
        """淡入结束：把窗口的不透明度定死为 1，并去掉可能残留的分层样式。"""
        try:
            self.setWindowOpacity(1.0)
        except Exception:
            pass
        self._apply_window_frame()
        try:
            self.update()
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        if not self._fade_done:
            self._fade_done = True
            self._fade_started_at = time.monotonic()
            self.setWindowOpacity(0.0)
            animation = QPropertyAnimation(self, b"windowOpacity", self)
            animation.setDuration(220)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.finished.connect(self._after_fade_in)
            animation.start()
            self._fade_anim = animation
        self._apply_glass()
        try:
            self.mascot.set_on(
                (self.store.get_setting("mascot_on", "1") or "1") != "0")
        except Exception:
            pass
        try:
            self.toggle_sidebar(self._sidebar_open)
        except Exception:
            pass

    # -------------------------------------------------- 系统托盘
    def _build_tray_menu(self):
        """托盘右键菜单：显示主窗口 / 退出（单独拆出来，方便没托盘时也能建）。"""
        menu = round_menu(QMenu(self))
        self._tray_show_action = menu.addAction("显示主窗口")
        self._tray_show_action.triggered.connect(self.show_from_tray)
        menu.addSeparator()
        self._tray_quit_action = menu.addAction("退出 DanLume")
        self._tray_quit_action.triggered.connect(self.quit_from_tray)
        self._tray_menu = menu
        return menu

    def _setup_tray(self) -> None:
        """系统托盘：点右上角「关闭」是收进托盘，右键托盘图标才有「退出」。"""
        self._tray = None
        self._tray_available = False
        self._tray_hint_shown = False
        self._quitting = False
        try:
            menu = self._build_tray_menu()
        except Exception:
            menu = None
        if os.environ.get("LUMINA_SMOKE"):
            return                      # 测试 / 冒烟：不建托盘，免得退出流程被改
        try:
            from PySide6.QtWidgets import QSystemTrayIcon

            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            tray = QSystemTrayIcon(self)
            try:
                tray.setIcon(self.windowIcon())
            except Exception:
                pass
            tray.setToolTip("%s · 还在后台运行" % APP_TITLE.split("·")[0].strip())
            if menu is not None:
                tray.setContextMenu(menu)
            tray.activated.connect(self._on_tray_activated)
            tray.show()
            self._tray = tray
            self._tray_available = True
        except Exception as exc:  # noqa: BLE001
            self._tray_available = False
            try:
                from . import diag

                diag._say("托盘不可用：%s" % exc)
            except Exception:
                pass

    def tray_available(self) -> bool:
        return bool(getattr(self, "_tray_available", False))

    def tray_on_close(self) -> bool:
        """点关闭时是不是该收进托盘（设置里可以关掉这个行为）。"""
        try:
            return (self.store.get_setting("tray_on_close", "1") or "1") == "1"
        except Exception:
            return True

    def _on_tray_activated(self, reason) -> None:
        try:
            from PySide6.QtWidgets import QSystemTrayIcon

            if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                          QSystemTrayIcon.ActivationReason.DoubleClick):
                self.show_from_tray()
        except Exception:
            pass

    def hide_to_tray(self) -> None:
        """把窗口收进托盘（不退出程序）。"""
        if not self.tray_available():
            return
        try:
            self._saved_geometry = self.geometry()
        except Exception:
            self._saved_geometry = None
        self.hide()
        try:
            self.mascot.set_on(False)
        except Exception:
            pass
        tray = getattr(self, "_tray", None)
        if tray is not None and not getattr(self, "_tray_hint_shown", False):
            self._tray_hint_shown = True
            try:
                from PySide6.QtWidgets import QSystemTrayIcon

                tray.showMessage(
                    "DanLume 还在后台跑",
                    "点托盘图标可以把窗口叫回来；右键图标 → 退出 DanLume。",
                    QSystemTrayIcon.MessageIcon.Information, 4000)
            except Exception:
                pass

    def show_from_tray(self) -> None:
        """从托盘把窗口叫回来。"""
        try:
            if self.isMinimized():
                self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        try:
            if getattr(self, "_maximized", False) or (
                    getattr(self, "_saved_geometry", None) is not None
                    and self._maximized):
                self.showMaximized()
        except Exception:
            pass
        try:
            self.mascot.set_on(
                (self.store.get_setting("mascot_on", "1") or "1") != "0")
        except Exception:
            pass
        try:
            self._apply_glass()
        except Exception:
            pass

    def quit_from_tray(self) -> None:
        """真正退出（托盘右键菜单里那一项）。"""
        self._quitting = True
        try:
            if self._tray is not None:
                self._tray.hide()
        except Exception:
            pass
        try:
            self.show()
        except Exception:
            pass
        self.close()

    def closeEvent(self, event):
        # 点关闭 → 收进托盘（除非用户关掉了这个行为，或者托盘不可用）
        if (not getattr(self, "_quitting", False)
                and self.tray_available() and self.tray_on_close()):
            try:
                event.ignore()
            except Exception:
                pass
            self.hide_to_tray()
            return
        self._closed = True

        for timer in (getattr(self, "_backdrop_timer", None),
                      getattr(self, "_backdrop_poll", None)):
            try:
                timer.stop()
            except Exception:
                pass
        try:
            self.reminders.stop()
        except Exception:
            pass
        try:
            self.notice_watcher.stop()
        except Exception:
            pass
        for toast in list(getattr(self, "_notice_toasts", [])):
            toast.close()
        try:
            self.mascot.close()
        except Exception:
            pass
        try:
            self.focus_monitor.stop()
            self.focus_timer.close()
        except Exception:
            pass
        self.snackbar.hide()
        self._shutdown_web()
        super().closeEvent(event)
        # 兜底：1.2 秒内还没退干净就直接结束进程（避免 Chromium 关闭死锁）
        QTimer.singleShot(1200, lambda: hard_exit(self.store))

    def _shutdown_web(self) -> None:
        """拆掉所有内嵌浏览器（否则退出会卡住）。"""
        # 后台取数桥挂在 ehall 的 profile 上，得赶在 profile 之前收掉
        bridge = getattr(self, "ehall_bridge", None)
        shutdown = getattr(bridge, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass
        candidates = [
            getattr(self.homework_page, "web", None),
            getattr(self.dock.ai, "web", None),
            getattr(self.ehall_page, "web", None),
            getattr(self.notices_page, "web", None),
            getattr(getattr(self, "classroom_page", None), "web", None),
            getattr(getattr(self, "settings_page", None), "web", None),
        ]
        for panel in candidates:
            if panel is None:
                continue
            shutdown = getattr(panel, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
                    pass
