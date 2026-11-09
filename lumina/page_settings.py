"""设置页：SMTP、默认收件人、提醒与数据位置。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIntValidator, QLinearGradient, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QCheckBox, QPlainTextEdit,
    QApplication, QColorDialog, QFileDialog, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSlider,
    QVBoxLayout, QWidget,
)

from . import accounts, fdu, icons, theme, timetable
from .utils import open_external
from .config import APP_VERSION, DATA_DIR, SMTP_SECURITY
from .mailer import SmtpConfig
from .sending import SmtpTester
from .theme import (
    ACCENT, ACCENT2, BORDER, BORDER_SOFT, CARD, CARD_ALT, CARD_HOVER, DANGER,
    FAINT, MUTED, SUCCESS, TEXT, TEXT_SOFT, WARNING,
)
from .inputs import ColorDots
from .widgets import (
    Card, FieldBlock, GlassSlider, HScrollRow, IconBadge, IconButton, NavButton,
    Select, SoftButton, ToggleSwitch, clear_layout, rounded_path,
)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# AI 接口预设：选一下就把地址和模型名填好，Key 还是用户自己填
LLM_PRESETS: List[Dict[str, str]] = [
    {"id": "local", "name": "本地模型",
     "base": "http://127.0.0.1:8080/v1", "model": ""},
    {"id": "deepseek", "name": "DeepSeek 对话（deepseek-chat）",
     "base": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    {"id": "deepseek-r1", "name": "DeepSeek R1（会「思考」）",
     "base": "https://api.deepseek.com/v1", "model": "deepseek-reasoner"},
    {"id": "doubao", "name": "豆包（火山方舟）",
     "base": "https://ark.cn-beijing.volces.com/api/v3", "model": ""},
    {"id": "kimi", "name": "Kimi（月之暗面）",
     "base": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    {"id": "custom", "name": "自定义（自己填）", "base": "", "model": ""},
]


class AccentSwatch(QWidget):
    """可点击的强调色圆点。"""

    clicked = Signal()

    def __init__(self, accent: str, accent2: str, name: str, selected: bool = False,
                 custom: bool = False, parent=None):
        super().__init__(parent)
        self._accent = QColor(accent)
        self._accent2 = QColor(accent2)
        self._selected = selected
        self._custom = custom
        self._hover = False
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(name)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_selected(self, selected: bool) -> None:
        if self._selected != selected:
            self._selected = selected
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

    def set_colors(self, accent: str, accent2: str) -> None:
        self._accent = QColor(accent)
        self._accent2 = QColor(accent2)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        ring = QColor(theme.TEXT)
        ring.setAlphaF(0.55 if self._hover else 0.30)
        if self._selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.TEXT), 1.6))
            painter.drawEllipse(rect.adjusted(-3.2, -3.2, 3.2, 3.2))
        if self._custom:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            colors = ["#5856D6", "#64D2FF", "#FFCC00", "#FF2D55", "#007AFF"]
            for index, value in enumerate(colors):
                gradient.setColorAt(index / (len(colors) - 1), QColor(value))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawEllipse(rect)
            painter.setPen(QPen(QColor(theme.CARD), 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, self._accent)
            gradient.setColorAt(1.0, self._accent2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawEllipse(rect)
            if self._hover:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(ring, 1.2))
                painter.drawEllipse(rect)


class BackgroundChip(QWidget):
    """背景色调预览卡片。"""

    clicked = Signal()

    def __init__(self, preset: Dict[str, str], selected: bool = False, parent=None):
        super().__init__(parent)
        self._preset = preset
        self._selected = selected
        self._hover = False
        self.setFixedSize(116, 62)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(preset["name"])
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_selected(self, selected: bool) -> None:
        if self._selected != selected:
            self._selected = selected
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
        path = rounded_path(rect, 11)
        if theme.is_dark():
            # 深色模式下统一是 Apple 深色底，预览只保留预设的轻微色相
            base = QColor("#232326")
            top = theme.mix(base, QColor(self._preset["top"]), 0.22)
            mid = theme.mix(base, QColor(self._preset["mid"]), 0.22)
            bottom = theme.mix(base, QColor(self._preset["bottom"]), 0.18)
        else:
            top = QColor(self._preset["top"])
            mid = QColor(self._preset["mid"])
            bottom = QColor(self._preset["bottom"])
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(0.55, mid)
        gradient.setColorAt(1.0, bottom)
        painter.fillPath(path, QBrush(gradient))
        painter.save()
        painter.setClipPath(path)
        label_strip = QRectF(rect.left(), rect.bottom() - 22, rect.width(), 22)
        strip = QColor(theme.CARD)
        strip.setAlphaF(0.92)
        painter.fillRect(label_strip, strip)
        painter.restore()
        if self._selected:
            painter.setPen(QPen(theme.appearance().accent_q(), 1.8))
        else:
            painter.setPen(QPen(theme.hover_wash(0.10), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(QColor(theme.TEXT) if self._selected else QColor(theme.TEXT_SOFT))
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold if self._selected else QFont.Weight.Normal)
        painter.setFont(font)
        painter.drawText(
            QRectF(rect.left() + 9, rect.bottom() - 22, rect.width() - 16, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._preset["name"],
        )
        if self._selected:
            dot = QRectF(rect.right() - 17, rect.top() + 9, 7, 7)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.appearance().accent_q())
            painter.drawEllipse(dot)


class ModeCard(QWidget):
    """浅色 / 深色 / 自动 的三张小预览卡（对齐 macOS「外观」设置）。"""

    clicked = Signal(str)

    def __init__(self, mode_id: str, name: str, selected: bool = False, parent=None):
        super().__init__(parent)
        self._mode_id = mode_id
        self._name = name
        self._selected = selected
        self._hover = False
        self.setFixedSize(104, 72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip(name)

    def mode_id(self) -> str:
        return self._mode_id

    def set_selected(self, selected: bool) -> None:
        if self._selected != selected:
            self._selected = selected
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
            self.clicked.emit(self._mode_id)
        super().mouseReleaseEvent(event)

    # ---- 绘制 ----
    def _window_scene(self, painter: QPainter, dark: bool) -> None:
        """画一扇迷你窗口：左边栏 + 内容区。"""
        shell = "#2C2C2E" if dark else "#FFFFFF"
        sidebar = "#3A3A3C" if dark else "#F1F1F4"
        content = "#1C1C1E" if dark else "#FBFBFD"
        rows = "#48484A" if dark else "#E4E4E9"
        accent = theme.appearance().accent_q()

        body = QRectF(10, 9, 84, 42)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(shell))
        painter.drawPath(rounded_path(body, 5))
        # 边栏
        painter.save()
        clip = rounded_path(body, 5)
        painter.setClipPath(clip)
        painter.setBrush(QColor(sidebar))
        painter.drawRect(QRectF(body.left(), body.top(), 26, body.height()))
        painter.setBrush(QColor(content))
        painter.drawRect(QRectF(body.left() + 27, body.top(), body.width() - 27,
                                body.height()))
        # 边栏条目
        for index in range(3):
            row = QRectF(body.left() + 4, body.top() + 5 + index * 8, 18, 4.5)
            painter.setBrush(accent if index == 0 else QColor(rows))
            painter.drawPath(rounded_path(row, 2.2))
        # 内容区
        for index in range(2):
            row = QRectF(body.left() + 32, body.top() + 7 + index * 11,
                         body.width() - 40, 7)
            painter.setBrush(QColor(rows))
            painter.drawPath(rounded_path(row, 2.5))
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded_path(rect, 9)

        # 选中的底色：浅色用卡片的浅灰，深色用更亮一点的表面
        accent = theme.appearance().accent_q()
        if self._selected:
            fill = QColor(accent)
            fill.setAlphaF(0.14)
        elif self._hover:
            fill = QColor(theme.FAINT)
            fill.setAlphaF(0.14)
        else:
            fill = QColor(theme.CARD_ALT)
        painter.fillPath(path, fill)

        if self._mode_id == "dark":
            self._window_scene(painter, True)
        elif self._mode_id == "light":
            self._window_scene(painter, False)
        else:
            # 自动：左浅右深，居中过渡
            painter.save()
            painter.setClipPath(path)
            x = rect.center().x()
            warm = QLinearGradient(x - 14, 0, x + 14, 0)
            left = QColor(theme.PALETTES["light"]["WINDOW_TOP"])
            right = QColor(theme.PALETTES["dark"]["WINDOW_BOTTOM"])
            warm.setColorAt(0.0, left)
            warm.setColorAt(1.0, right)
            painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 10), warm)
            painter.restore()
            self._window_scene(painter, False)
            painter.save()
            painter.setClipRect(QRectF(x, 0, rect.width() - x, rect.height()))
            self._window_scene(painter, True)
            painter.restore()

        if self._selected:
            painter.setPen(QPen(accent, 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold if self._selected else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT if self._selected else theme.MUTED))
        painter.drawText(
            QRectF(rect.left(), rect.bottom() - 15, rect.width(), 13),
            int(Qt.AlignmentFlag.AlignCenter),
            self._name,
        )


class ContactRow(QWidget):
    removed = Signal(int)

    def __init__(self, contact: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.contact = contact
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 8, 5)
        layout.setSpacing(10)
        name = QLabel(contact.get("name") or "未命名")
        name.setStyleSheet(f"color: {TEXT}; font-size: 9.4pt; font-weight: 600;")
        layout.addWidget(name)
        email = QLabel(str(contact.get("email", "")))
        email.setObjectName("Small")
        layout.addWidget(email, 1)
        delete_button = IconButton(icons.DELETE, size=26, glyph_size=10, tip="删除联系人")
        delete_button.clicked.connect(
            lambda: self.removed.emit(int(self.contact.get("id", 0)))
        )
        layout.addWidget(delete_button)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillPath(rounded_path(rect, 10), QColor(CARD_ALT))
        painter.setPen(QPen(QColor(BORDER_SOFT), 1.0))
        painter.drawPath(rounded_path(rect, 10))


class SettingsPage(QWidget):
    settings_saved = Signal()
    appearance_changed = Signal()
    notify = Signal(str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._tester = SmtpTester(self)
        self._tester.finished.connect(self._on_test_finished)
        self._contacts: List[Dict[str, Any]] = []
        # 服务器账户卡：网络请求都走后台线程（测连接 / 拉设备列表）
        from . import account as account_module

        self._server_call = account_module.AsyncCall(self)
        self._server_call.finished.connect(self._on_server_call_finished)
        self._server_call.failed.connect(self._on_server_call_failed)
        self._server_mode = ""
        self._server_stream_state = ""

        state = theme.appearance()
        self._accent = state.accent
        self._accent2 = state.accent2
        self._bg = state.bg
        self._alpha = state.alpha
        self._glass = state.glass
        self._blur = state.blur
        self._mode = state.mode
        current_fonts = theme.fonts()
        self._font_zh = current_fonts.get("zh", "")
        self._font_en = current_fonts.get("en", "")
        self._accounts: List[Dict[str, Any]] = accounts.load(store)
        self._active_account = accounts.active_id(store)
        self._loading_account = False
        # 外观实时预览：点选类的立刻应用，滑杆这类连续变化走这个定时器节流
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(120)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._preview_appearance)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 18, 26, 20)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("设置")
        title.setObjectName("H1")
        subtitle = QLabel("邮件服务器、常用收件人与提醒偏好，全部保存在本机")
        subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.save_button = SoftButton("保存设置", "primary", glyph=icons.SAVE, height=36,
                                      min_width=118)
        self.save_button.clicked.connect(self.save)
        header.addWidget(self.save_button)
        root.addLayout(header)

        from .widgets import make_scroll

        # -------------------------------------------------- 左右两栏
        # 左边是分类导航（常规 / 邮件 / 日程 / AI / 其他），右边只显示当前分类，
        # 顶部有一个大标题 + 一句说明 —— 跟现代设置页一个样子。
        self._sections = [
            {"key": "general", "title": "常规", "glyph": icons.SUN,
             "subtitle": "外观、账户、提醒与界面交互",
             "cards": ["appearance", "tray", "account", "server", "reminder",
                       "focus", "uis"]},
            {"key": "dashboard", "title": "仪表板", "glyph": icons.INFO,
             "subtitle": "校车方向与 ehall 各服务的数据来源",
             "cards": ["dashsrc"]},
            {"key": "mail", "title": "邮件", "glyph": icons.MAIL,
             "subtitle": "发件邮箱、收件箱、联系人与教务通知",
             "cards": ["smtp", "imap", "recipient", "notice", "dirs"]},
            {"key": "schedule", "title": "日程", "glyph": icons.CALENDAR,
             "subtitle": "课程表、日程分组与出发提醒",
             "cards": ["timetable", "groups", "location"]},
            {"key": "ai", "title": "AI", "glyph": icons.INFO,
             "subtitle": "模型接口、多套 API 配置与提示词",
             "cards": ["llm", "prompts"]},
            {"key": "misc", "title": "其他", "glyph": icons.FOLDER,
             "subtitle": "网络代理、文献与本地数据",
             "cards": ["vpn", "refs", "data"]},
        ]
        self._current_section = "general"

        body_row = QHBoxLayout()
        body_row.setSpacing(14)

        nav_panel = QWidget()
        nav_panel.setFixedWidth(190)
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(4)
        # 「返回应用」：跟参考图一样放在最上面，点了回日历
        self.back_button = NavButton(icons.CHEVRON_LEFT, "返回应用")
        self.back_button.clicked.connect(self._back_to_app)
        nav_layout.addWidget(self.back_button)
        nav_layout.addSpacing(4)
        self.nav_search = QLineEdit()
        self.nav_search.setPlaceholderText("搜索设置（比如 vpn、字体、key）")
        self.nav_search.setFixedHeight(34)
        self.nav_search.setToolTip(
            "搜的是**设置项本身**：卡片标题、字段名、说明文字都能搜到\n"
            "点结果会跳到对应分类并滚到那张卡片")
        self.nav_search.textChanged.connect(lambda _text: self._filter_nav())
        nav_layout.addWidget(self.nav_search)
        # 搜索结果（平时藏着；有搜索词时列出来，点一下跳过去）
        self.search_results = QListWidget()
        self.search_results.setObjectName("SearchResults")
        self.search_results.setVisible(False)
        self.search_results.setMaximumWidth(190)     # 别盖到右边的设置内容上
        self.search_results.itemClicked.connect(self._on_search_pick)
        self.search_results.setStyleSheet(
            f"QListWidget#SearchResults {{ background: {CARD};"
            f" border: 1px solid {BORDER}; border-radius: 10px;"
            f" padding: 4px; outline: none; }}"
            f"QListWidget#SearchResults::item {{ padding: 6px 8px;"
            f" border-radius: 6px; color: {TEXT}; }}"
            f"QListWidget#SearchResults::item:hover {{ background: {CARD_HOVER}; }}"
            f"QListWidget#SearchResults::item:selected {{"
            f" background: {ACCENT}; color: #FFFFFF; }}")
        nav_layout.addWidget(self.search_results)
        self._search_layout = nav_layout
        nav_layout.addSpacing(6)
        self._nav_buttons: Dict[str, NavButton] = {}
        for section in self._sections:
            button = NavButton(section["glyph"], section["title"])
            button.clicked.connect(
                lambda _=False, key=section["key"]: self._select_section(key))
            self._nav_buttons[section["key"]] = button
            nav_layout.addWidget(button)
        nav_layout.addStretch(1)
        body_row.addWidget(nav_panel)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.page_title = QLabel("常规")
        self.page_title.setObjectName("H1")
        self.page_subtitle = QLabel("")
        self.page_subtitle.setObjectName("Muted")
        right.addWidget(self.page_title)
        right.addWidget(self.page_subtitle)
        self.settings_scroll, _body, self.content = make_scroll()
        self.content.setSpacing(14)
        right.addWidget(self.settings_scroll, 1)
        body_row.addLayout(right, 1)
        root.addLayout(body_row, 1)

        # -------------------------------------------------- 所有卡片（建一次，按分类显隐）
        self._card_widgets: Dict[str, QWidget] = {
            "appearance": self._build_appearance_card(),
            "dashsrc": self._build_dashsrc_card(),
            "account": self._build_account_card(),
            "server": self._build_server_card(),
            "smtp": self._build_smtp_card(),
            "imap": self._build_imap_card(),
            "uis": self._build_uis_card(),
            "timetable": self._build_timetable_card(),
            "groups": self._build_groups_card(),
            "recipient": self._build_recipient_card(),
            "dirs": self._build_dirs_card(),
            "notice": self._build_notice_card(),
            "location": self._build_location_card(),
            "reminder": self._build_reminder_card(),
            "focus": self._build_focus_card(),
            "llm": self._build_llm_card(),
            "prompts": self._build_prompts_card(),
            "vpn": self._build_vpn_card(),
            "refs": self._build_refs_card(),
            "tray": self._build_tray_card(),
            "data": self._build_data_card(),
        }
        for widget in self._card_widgets.values():
            self.content.addWidget(widget)
        self.content.addStretch(1)
        self._build_search_index()
        self._select_section("general")

        self.refresh()
        # 到这里控件都建好、值也回填完了，之后的改动才算「用户真的在改」
        self._preview_ready = True

    # -------------------------------------------------- 分类导航
    def _back_to_app(self) -> None:
        """回到主界面（日历）。"""
        window = self.window()
        navigate = getattr(window, "navigate", None)
        if callable(navigate):
            navigate(0)

    def _select_section(self, key: str) -> None:
        """切换左边分类：只显示属于它的卡片。"""
        section = next((item for item in self._sections if item["key"] == key),
                       self._sections[0])
        self._current_section = section["key"]
        for name, button in self._nav_buttons.items():
            try:
                button.set_active(name == section["key"])
            except Exception:
                pass
        shown = set(section["cards"])
        for name, widget in getattr(self, "_card_widgets", {}).items():
            try:
                widget.setVisible(name in shown)
            except Exception:
                pass
        self.page_title.setText(section["title"])
        self.page_subtitle.setText(section.get("subtitle") or "")
        if section["key"] == "dashboard":
            # 地址可能在别处被改过（比如「空教室」页的「设为首页」），进来时重读一次
            self._refresh_dashsrc_edits()
        try:
            self.settings_scroll.verticalScrollBar().setValue(0)
        except Exception:
            pass

    def section(self) -> str:
        """当前显示的分类（测试 / 外部跳转用）。"""
        return getattr(self, "_current_section", "general")

    def show_section(self, key: str) -> None:
        """外部跳转：例如从 AI 面板跳到「AI」分类。"""
        if any(item["key"] == key for item in getattr(self, "_sections", [])):
            self._select_section(key)

    def _filter_nav(self) -> None:
        """搜索框：按文字过滤左边的分类项，**并且列出匹配的设置卡片**。

        以前只拿「分类标题 + 副标题」去匹配，所以搜 vpn / 字体 / Key 什么都看不到，
        看着像假的。现在按卡片（标题、字段名、说明、提示）建了索引，能直接搜到项。
        """
        text = ""
        try:
            text = self.nav_search.text().strip()
        except Exception:
            text = ""
        lowered = text.lower()
        matches = self.search_matches(text)
        # 分类：只要里面有匹配的卡片就留下
        keep_sections = {item["section"] for item in matches}
        for section in getattr(self, "_sections", []):
            button = self._nav_buttons.get(section["key"])
            if button is None:
                continue
            try:
                if not text or not matches:
                    # 没搜索词 / 一个都没搜到：分类照常全显示，别让人以为页面空了
                    button.setVisible(True)
                else:
                    button.setVisible(section["key"] in keep_sections)
            except Exception:
                pass
        self._show_search_results(text, matches)

    # -------------------------------------------------- 搜索
    #: 常见英文 / 缩写 → 中文（搜 proxy 也能找到「代理」，反之亦然）
    SEARCH_SYNONYMS = {
        "vpn": "代理 vpn", "proxy": "代理", "key": "密钥 key api",
        "api": "接口 api key 模型", "token": "token 用量", "mail": "邮件 邮箱",
        "smtp": "发件 smtp", "imap": "收件 imap", "font": "字体",
        "theme": "外观 主题", "dark": "深色 深色模式", "light": "浅色",
        "blur": "模糊 磨砂", "tray": "托盘", "traffic": "校车 班车",
        "password": "密码", "font size": "字号", "ai": "ai 接口 模型",
    }

    def _build_search_index(self) -> None:
        """给每张设置卡片建一条索引（标题 + 所有文字 + 占位提示 + 按钮）。"""
        self._search_index: List[Dict[str, Any]] = []
        for section in getattr(self, "_sections", []):
            for name in section.get("cards") or []:
                widget = (getattr(self, "_card_widgets", {}) or {}).get(name)
                if widget is None:
                    continue
                title, haystack = self._describe_card(widget)
                entry = {
                    "card": name,
                    "section": section["key"],
                    "section_title": section["title"],
                    "title": title or name,
                    "widget": widget,
                    "text": haystack.lower(),
                }
                self._search_index.append(entry)

    @staticmethod
    def _describe_card(widget) -> "tuple[str, str]":
        """一张卡片：取它的标题（优先 H3）和所有能搜的文字。"""
        pieces: List[str] = []
        title = ""
        for label in widget.findChildren(QLabel):
            text = str(label.text() or "").strip()
            if not text:
                continue
            if not title and label.objectName() in ("H3", "H2", "H4"):
                title = text
            pieces.append(text)
        for edit in widget.findChildren(QLineEdit):
            pieces.append(str(edit.placeholderText() or ""))
            pieces.append(str(edit.toolTip() or ""))
        for button in widget.findChildren(QWidget):
            tip = str(button.toolTip() or "")
            if tip:
                pieces.append(tip)
        return title, "\n".join(pieces)

    def search_matches(self, text: str) -> List[Dict[str, Any]]:
        """按关键词找设置卡片（空的搜索词返回空列表）。"""
        lowered = str(text or "").strip().lower()
        if not lowered:
            return []
        words = [word for word in re.split(r"\s+", lowered) if word]
        extras = [word for word in self.SEARCH_SYNONYMS.get(lowered, "").split()
                  if word]
        results: List[Dict[str, Any]] = []
        for entry in getattr(self, "_search_index", []):
            haystack = entry["text"]
            hit = all(word in haystack for word in words)
            if not hit and len(words) == 1:
                # 同义词：搜 proxy 也认「代理」，搜 key 也认「密钥」
                hit = any(word in haystack for word in extras)
            if hit:
                results.append(entry)
        # 「标题里就有这个词」的排前面
        results.sort(key=lambda row: (lowered not in str(row["title"]).lower(),
                                      len(str(row["title"]))))
        return results

    def _show_search_results(self, text: str,
                             matches: List[Dict[str, Any]]) -> None:
        """把匹配结果显示在搜索框下面（点一下跳过去）。"""
        box = getattr(self, "search_results", None)
        if box is None:
            return
        box.clear()
        if not text:
            box.setVisible(False)
            return
        if not matches:
            item = QListWidgetItem("没找到相关设置")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            box.addItem(item)
            box.setFixedHeight(38)
            box.setVisible(True)
            return
        for entry in matches[:16]:
            item = QListWidgetItem("%s　·　%s" % (entry["title"],
                                                 entry["section_title"]))
            item.setData(Qt.ItemDataRole.UserRole, entry["card"])
            item.setToolTip("在「%s」分类里：%s" % (entry["section_title"],
                                                 entry["title"]))
            box.addItem(item)
        rows = min(len(matches), 8)
        box.setFixedHeight(rows * 30 + 10)
        box.setVisible(True)

    def _on_search_pick(self, item: "QListWidgetItem") -> None:
        """点了搜到的设置：切到那个分类、滚到那张卡片。"""
        card = item.data(Qt.ItemDataRole.UserRole)
        entry = next((row for row in self._search_index
                      if row["card"] == card), None)
        if entry is None:
            return
        self._select_section(entry["section"])
        self.page_subtitle.setText("已定位到「%s」" % entry["title"])
        target = entry["widget"]
        QTimer.singleShot(60, lambda: self._scroll_to_card(target))

    def _scroll_to_card(self, widget) -> None:
        try:
            self.settings_scroll.ensureWidgetVisible(widget, 0, 20)
        except Exception:
            pass

    # -------------------------------------------------- 卡片
    @staticmethod
    def _section_head(glyph: str, title: str, subtitle: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(IconBadge(glyph, size=34,
                                color=theme.appearance().accent2_q().name(),
                                bg_alpha=0.12, radius=10, glyph_size=14))
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("H3")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("Tiny")
        subtitle_label.setWordWrap(True)
        text_box.addWidget(title_label)
        text_box.addWidget(subtitle_label)
        row.addLayout(text_box, 1)
        return row

    # -------------------------------------------------- 账户与登录
    def _build_account_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=12)
        card.body.addLayout(self._section_head(
            icons.CONTACT, "账户与登录",
            "账户只存在这台电脑；密码保存的是加盐哈希（PBKDF2），任何界面都看不到原文",
        ))

        row = QHBoxLayout()
        row.setSpacing(12)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        self.account_label = QLabel("—")
        self.account_label.setStyleSheet(
            f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        self.account_hint = QLabel("")
        self.account_hint.setObjectName("Tiny")
        self.account_hint.setWordWrap(True)
        text_box.addWidget(self.account_label)
        text_box.addWidget(self.account_hint)
        row.addLayout(text_box, 1)
        self.logout_button = SoftButton("在这台设备上退出登录", "ghost", height=32,
                                        min_width=168, point_size=8.6)
        self.logout_button.setToolTip("清除「记住这台设备」，下次打开应用要重新输密码")
        self.logout_button.clicked.connect(self._logout_device)
        row.addWidget(self.logout_button)
        self.admin_button = SoftButton("账户审核（管理员）", "ghost", height=32,
                                       min_width=150, point_size=8.6)
        self.admin_button.setToolTip("批准注册申请、管理注册表、导出管理员公钥")
        self.admin_button.clicked.connect(self._open_admin_dialog)
        row.addWidget(self.admin_button)
        card.body.addLayout(row)

        remember_row = QHBoxLayout()
        remember_row.setSpacing(8)
        remember_label = QLabel("记住这台设备")
        remember_label.setObjectName("Tiny")
        remember_row.addWidget(remember_label)
        from . import users

        self.remember_select = Select()
        self.remember_select.setFixedWidth(216)
        for minutes, label in users.REMEMBER_CHOICES:
            self.remember_select.addItem(label, minutes)
        self.remember_select.currentIndexChanged.connect(self._on_remember_changed)
        remember_row.addWidget(self.remember_select)
        self.bind_button = SoftButton("绑定这台设备（以后不用输密码）", "ghost",
                                      height=32, min_width=216, point_size=8.6)
        self.bind_button.setToolTip("只有管理员能这么做：把这台电脑记为可信设备，"
                                    "以后打开应用不再定时要求输密码")
        self.bind_button.clicked.connect(self._bind_device)
        remember_row.addWidget(self.bind_button)
        self.remember_hint = QLabel("到期后打开应用要重新输一次密码")
        self.remember_hint.setObjectName("Tiny")
        remember_row.addWidget(self.remember_hint)
        remember_row.addStretch(1)
        card.body.addLayout(remember_row)

        # 改密码
        pw_title = QLabel("修改密码")
        pw_title.setObjectName("Tiny")
        card.body.addWidget(pw_title)
        # 分栏变窄时这一排放不下 → 横向滚动
        pw_row = HScrollRow(height=40, spacing=8)
        self.old_password = QLineEdit()
        self.old_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.old_password.setPlaceholderText("原密码")
        self.old_password.setFixedWidth(170)
        pw_row.addWidget(self.old_password)
        self.new_password = QLineEdit()
        self.new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password.setPlaceholderText("新密码（至少 6 位）")
        self.new_password.setFixedWidth(190)
        pw_row.addWidget(self.new_password)
        self.new_password2 = QLineEdit()
        self.new_password2.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password2.setPlaceholderText("再输一次新密码")
        self.new_password2.setFixedWidth(170)
        pw_row.addWidget(self.new_password2)
        self.change_password_button = SoftButton("保存新密码", "primary", height=32,
                                                 min_width=110, point_size=8.6)
        self.change_password_button.clicked.connect(self._change_password)
        pw_row.addWidget(self.change_password_button)
        card.body.addWidget(pw_row)

        self.account_status = QLabel("")
        self.account_status.setObjectName("Tiny")
        self.account_status.setWordWrap(True)
        card.body.addWidget(self.account_status)
        return card

    def _current_user(self):
        from . import users

        window = self.window()
        user = getattr(window, "current_user", None)
        if user:
            return user
        return users.current_session(self.store)

    def _refresh_account_card(self) -> None:
        from . import users

        if not hasattr(self, "account_label"):
            return
        user = self._current_user()
        if user:
            role = "管理员" if users.is_admin(user) else "普通用户"
            self.account_label.setText(
                f"当前账户：{user.get('username', '')}（{role}）")
            try:
                self.admin_button.setVisible(bool(users.is_admin(user)))
            except Exception:
                pass
        else:
            self.account_label.setText("当前账户：—")
            try:
                self.admin_button.setVisible(False)
            except Exception:
                pass
        session = users.current_session(self.store)
        validity = users.validity_state(user) if user else ""
        trusted = bool(session and session.get("trusted"))
        if session:
            self.account_hint.setText(f"{validity} · {users.session_text(self.store)}")
        elif users.login_required(self.store):
            self.account_hint.setText(
                f"{validity} · 这台设备没有记住登录，下次打开应用要输密码")
        else:
            self.account_hint.setText(f"{validity} · 登录已关闭（不需要密码）")
        # 记住时长：管理员多一个「永久绑定这台设备」
        choices = list(users.REMEMBER_CHOICES)
        if users.is_admin(user):
            choices.append(users.REMEMBER_ADMIN_CHOICE)
        minutes = users.remember_minutes(self.store)
        self.remember_select.blockSignals(True)
        self.remember_select.clear()
        for value, label in choices:
            self.remember_select.addItem(label, value)
        index = self.remember_select.findData(minutes)
        self.remember_select.setCurrentIndex(max(0, index))
        self.remember_select.blockSignals(False)
        try:
            admin = bool(users.is_admin(user))
            self.bind_button.setVisible(admin)
            self.bind_button.setEnabled(admin and not trusted)
            self.bind_button.setText(
                "已永久绑定这台设备" if trusted else "绑定这台设备（以后不用输密码）")
        except Exception:
            pass

    def _on_remember_changed(self) -> None:
        from . import users

        minutes = int(self.remember_select.currentData() or 0)
        users.set_remember_minutes(self.store, minutes)
        user = self._current_user()
        try:
            if minutes <= 0:
                users.forget_device(self.store)
            elif user:
                users.remember_device(self.store, int(user["id"]), minutes)
        except users.AccountError as exc:
            self.account_status.setStyleSheet(f"color: {DANGER};")
            self.account_status.setText(str(exc))
            self._refresh_account_card()
            return
        self._refresh_account_card()
        self.account_status.setStyleSheet(f"color: {SUCCESS};")
        self.account_status.setText(
            "已永久绑定这台设备，以后不用再输密码"
            if minutes < 0 else
            ("已记住这台设备" if minutes > 0 else "已改成每次都要输密码"))

    def _bind_device(self) -> None:
        """管理员把当前这台设备记成可信设备：不再定时要求输密码。"""
        from . import users

        user = self._current_user()
        if not users.is_admin(user):
            self.account_status.setStyleSheet(f"color: {DANGER};")
            self.account_status.setText("只有管理员账户可以绑定设备")
            return
        try:
            users.set_remember_minutes(self.store, users.REMEMBER_FOREVER)
            users.remember_device(self.store, int(user["id"]),
                                  users.REMEMBER_FOREVER)
        except users.AccountError as exc:
            self.account_status.setStyleSheet(f"color: {DANGER};")
            self.account_status.setText(str(exc))
            return
        self._refresh_account_card()
        self.account_status.setStyleSheet(f"color: {SUCCESS};")
        self.account_status.setText(
            f"已把这台设备绑定到管理员「{user.get('username', '')}」："
            "以后打开应用不用再输密码（换设备或点「在这台设备上退出登录」才需要）")

    def _change_password(self) -> None:
        from . import users

        user = self._current_user()
        if not user:
            self.account_status.setStyleSheet(f"color: {DANGER};")
            self.account_status.setText("没有登录账户，改不了密码")
            return
        if self.new_password.text() != self.new_password2.text():
            self.account_status.setStyleSheet(f"color: {DANGER};")
            self.account_status.setText("两次输入的新密码不一样")
            return
        try:
            users.change_password(self.store, int(user["id"]),
                                  self.old_password.text(), self.new_password.text())
        except users.AccountError as exc:
            self.account_status.setStyleSheet(f"color: {DANGER};")
            self.account_status.setText(str(exc))
            return
        self.old_password.clear()
        self.new_password.clear()
        self.new_password2.clear()
        self.account_status.setStyleSheet(f"color: {SUCCESS};")
        self.account_status.setText("密码已更新（下次登录用新密码）")

    def _logout_device(self) -> None:
        from . import users

        users.forget_device(self.store)
        self._refresh_account_card()
        self.account_status.setStyleSheet(f"color: {MUTED};")
        self.account_status.setText("已退出登录：下次打开应用要重新输密码")

    def _open_admin_dialog(self) -> None:
        from . import users

        user = self._current_user()
        if not users.is_admin(user):
            self.account_status.setStyleSheet(f"color: {DANGER};")
            self.account_status.setText("只有管理员能审批注册申请")
            return
        from .admin_dialog import AdminDialog

        dialog = AdminDialog(self.store, self.window())
        dialog.exec()
        self._refresh_account_card()

    # -------------------------------------------------- 服务器账户
    def _build_server_card(self) -> Card:
        from . import account as account_module

        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=12)
        card.body.addLayout(self._section_head(
            icons.INFO, "服务器账户",
            "自家账号服务器（默认 %s）：登录状态、设备管理与公告通道"
            % account_module.DEFAULT_SERVER_URL,
        ))

        url_row = HScrollRow(height=38, spacing=8)
        self.server_url_edit = QLineEdit()
        self.server_url_edit.setPlaceholderText(account_module.DEFAULT_SERVER_URL)
        self.server_url_edit.setMinimumWidth(260)
        self.server_url_edit.setFixedHeight(34)
        self.server_url_edit.setToolTip("账号服务器地址（例如 https://43.108.99.38.sslip.io）")
        self.server_url_edit.editingFinished.connect(self._save_server_url)
        url_row.addWidget(self.server_url_edit)
        self.server_test_button = SoftButton("测试连接", "ghost", height=32,
                                             min_width=104, point_size=8.6)
        self.server_test_button.setToolTip("GET /api/v1/health：看服务器在不在线、版本和延迟")
        self.server_test_button.clicked.connect(self._test_server_connection)
        url_row.addWidget(self.server_test_button)
        self.server_save_button = SoftButton("保存地址", "ghost", height=32,
                                             min_width=96, point_size=8.6)
        self.server_save_button.clicked.connect(self._save_server_url)
        url_row.addWidget(self.server_save_button)
        card.body.addWidget(url_row)

        self.server_status_label = QLabel("还没测过连接")
        self.server_status_label.setObjectName("Tiny")
        self.server_status_label.setWordWrap(True)
        card.body.addWidget(self.server_status_label)

        self.server_account_label = QLabel("服务器账号：未登录")
        self.server_account_label.setStyleSheet(
            f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        card.body.addWidget(self.server_account_label)
        self.server_detail_label = QLabel("")
        self.server_detail_label.setObjectName("Tiny")
        self.server_detail_label.setWordWrap(True)
        card.body.addWidget(self.server_detail_label)

        button_row = HScrollRow(height=38, spacing=8)
        self.server_devices_button = SoftButton("设备管理…", "ghost", height=32,
                                                min_width=104, point_size=8.6)
        self.server_devices_button.setToolTip("列出这台账号登录过的设备，可以把某一台踢下线")
        self.server_devices_button.clicked.connect(self._open_server_devices)
        button_row.addWidget(self.server_devices_button)
        self.server_refresh_button = SoftButton("刷新状态", "ghost", height=32,
                                                min_width=96, point_size=8.6,
                                                glyph=icons.REFRESH)
        self.server_refresh_button.setToolTip("重新向服务器要一次设备列表（也会更新设备数）")
        self.server_refresh_button.clicked.connect(self._refresh_server_devices)
        button_row.addWidget(self.server_refresh_button)
        self.server_logout_button = SoftButton("退出登录", "danger", height=32,
                                               min_width=96, point_size=8.6)
        self.server_logout_button.setToolTip("清掉这台电脑上保存的服务器令牌（服务器上的账号不动）")
        self.server_logout_button.clicked.connect(self._server_logout)
        button_row.addWidget(self.server_logout_button)
        card.body.addWidget(button_row)

        self.server_stream_label = QLabel("公告通道：未连接")
        self.server_stream_label.setObjectName("Tiny")
        self.server_stream_label.setWordWrap(True)
        card.body.addWidget(self.server_stream_label)

        self.server_note_label = QLabel("")
        self.server_note_label.setObjectName("Tiny")
        self.server_note_label.setWordWrap(True)
        card.body.addWidget(self.server_note_label)
        return card

    def refresh_server_card(self) -> None:
        """回填服务器账户卡（登录状态 / 设备数 / 地址）。"""
        from . import account as account_module

        if not hasattr(self, "server_url_edit"):
            return
        try:
            url = account_module.server_url(self.store)
        except Exception:
            url = account_module.DEFAULT_SERVER_URL
        if not self.server_url_edit.text().strip():
            self.server_url_edit.setText(url)
        session = {}
        try:
            session = account_module.session(self.store)
        except Exception:
            session = {}
        logged_in = bool(session.get("logged_in"))
        if logged_in:
            used = session.get("devices_used") or 0
            limit = session.get("device_limit") or 0
            self.server_account_label.setText(
                "服务器账号：%s（已登录）" % (session.get("username") or "—"))
            self.server_detail_label.setText(
                "设备数 %s/%s · 令牌到期 %s"
                % (used, limit if limit else "?", session.get("expires_at") or "未记录"))
            if session.get("insecure"):
                self.server_note_label.setStyleSheet(f"color: {WARNING};")
                self.server_note_label.setText(
                    "提示：服务器证书与地址不匹配（sslip.io 的通配证书），"
                    "已降级为不校验证书；口令走加密连接传输，但仍建议让管理员换成"
                    "匹配域名的证书。")
            else:
                self.server_note_label.setText("")
        else:
            self.server_account_label.setText("服务器账号：未登录")
            self.server_detail_label.setText(
                "在登录窗口点「服务器账号登录」即可；没登录时服务器公告不会推送。")
            self.server_note_label.setText("")
        for widget in (getattr(self, "server_devices_button", None),
                       getattr(self, "server_refresh_button", None),
                       getattr(self, "server_logout_button", None)):
            try:
                widget.setEnabled(logged_in)
            except Exception:
                pass
        self._refresh_server_stream_label(session.get("logged_in"))

    def set_server_stream_state(self, state: str) -> None:
        """主窗口的公告通道状态变化时更新这行小字。"""
        self._server_stream_state = str(state or "")
        try:
            self._refresh_server_stream_label(None)
        except Exception:
            pass

    def _refresh_server_stream_label(self, logged_in) -> None:
        label = getattr(self, "server_stream_label", None)
        if label is None:
            return
        if logged_in is None:
            try:
                from . import account as account_module

                logged_in = bool(account_module.session(self.store).get("logged_in"))
            except Exception:
                logged_in = False
        state = self._server_stream_state
        if not logged_in:
            label.setText("公告通道：未连接（没登录服务器账号）")
            return
        text = {
            "ws": "公告通道：WebSocket 实时连接中",
            "connecting": "公告通道：正在连接 WebSocket…",
            "poll": "公告通道：轮询模式（每 20 秒一次）",
            "offline": "公告通道：离线，正在按 1s → 60s 退避重连…",
            "unsupported": "公告通道：这个服务器版本还没有 /announcements 接口",
            "off": "公告通道：已断开",
        }.get(state, "公告通道：连接中…")
        label.setText(text)

    def _save_server_url(self) -> None:
        from . import account as account_module

        try:
            value = account_module.set_server_url(self.store, self.server_url_edit.text())
        except Exception as exc:  # noqa: BLE001
            self.server_status_label.setStyleSheet(f"color: {DANGER};")
            self.server_status_label.setText("地址存不下来：%s" % exc)
            return
        self.server_url_edit.setText(value)
        self.server_status_label.setStyleSheet(f"color: {MUTED};")
        self.server_status_label.setText("服务器地址已保存：%s" % value)

    def _test_server_connection(self) -> None:
        from . import account as account_module

        if self._server_call.is_busy():
            return
        url = self.server_url_edit.text().strip()
        try:
            account_module.set_server_url(self.store, url)
        except Exception:
            pass
        self.server_test_button.setEnabled(False)
        self.server_status_label.setStyleSheet(f"color: {MUTED};")
        self.server_status_label.setText("正在测试连接…")
        self._server_mode = "health"
        if not self._server_call.run(account_module.health, url, self.store):
            self.server_test_button.setEnabled(True)

    def _on_server_call_finished(self, result) -> None:
        mode = self._server_mode
        self._server_mode = ""
        if mode == "health":
            self.server_test_button.setEnabled(True)
            ok = False
            info: Dict[str, Any] = {}
            if isinstance(result, tuple) and len(result) == 2:
                ok, info = bool(result[0]), (result[1] or {})
            elif isinstance(result, dict):
                ok = bool(result.get("ok"))
                info = result
            if ok:
                bits = ["在线"]
                if info.get("version"):
                    bits.append("版本 %s" % info["version"])
                if info.get("latency_ms") is not None:
                    bits.append("延迟 %s ms" % info["latency_ms"])
                try:
                    uptime = int(info.get("uptime_seconds") or 0)
                except (TypeError, ValueError):
                    uptime = 0
                if uptime:
                    bits.append("已运行 %d 分钟" % (uptime // 60))
                message = " · ".join(bits)
                if info.get("insecure"):
                    message += "（证书未校验）"
                self.server_status_label.setStyleSheet(f"color: {SUCCESS};")
                self.server_status_label.setText(message)
            else:
                reason = str(info.get("error") or "服务器不可达")
                tone = WARNING if info.get("offline") else DANGER
                self.server_status_label.setStyleSheet(f"color: {tone};")
                self.server_status_label.setText("离线 · %s" % reason)
            return
        if mode == "devices":
            self.server_refresh_button.setEnabled(True)
            if isinstance(result, dict) and result.get("ok"):
                devices = result.get("devices")
                if not isinstance(devices, list):
                    payload = result.get("data") or {}
                    devices = payload.get("devices") if isinstance(
                        payload.get("devices"), list) else []
                try:
                    self.store.set_setting(
                        "server_devices_used",
                        str(result.get("devices_used") or len(devices)))
                except Exception:
                    pass
                self.server_status_label.setStyleSheet(f"color: {SUCCESS};")
                self.server_status_label.setText(
                    "设备列表已刷新：%d 台" % len(devices))
                self.refresh_server_card()
            else:
                message = str((result or {}).get("error") or "拿不到设备列表")
                self.server_status_label.setStyleSheet(f"color: {DANGER};")
                self.server_status_label.setText(message)

    def _on_server_call_failed(self, message: str) -> None:
        self.server_test_button.setEnabled(True)
        self.server_refresh_button.setEnabled(True)
        self.server_status_label.setStyleSheet(f"color: {DANGER};")
        self.server_status_label.setText("出错了：%s" % message)

    def _refresh_server_devices(self) -> None:
        from . import account as account_module

        if self._server_call.is_busy():
            return
        self.server_refresh_button.setEnabled(False)
        self.server_status_label.setStyleSheet(f"color: {MUTED};")
        self.server_status_label.setText("正在向服务器要设备列表…")
        self._server_mode = "devices"
        if not self._server_call.run(account_module.devices, self.store):
            self.server_refresh_button.setEnabled(True)

    def _open_server_devices(self) -> None:
        from .server_ui import ServerDevicesDialog

        dialog = ServerDevicesDialog(self.store, self.window())
        dialog.exec()
        self.refresh_server_card()

    def _server_logout(self) -> None:
        """退出服务器账号：停公告连接 + 清本机令牌（不动服务器上的账号）。"""
        from . import account as account_module
        from .widgets import ConfirmDialog

        dialog = ConfirmDialog(
            self, title="退出服务器账号？",
            message="这台电脑上保存的服务器令牌会被清掉，公告也会立刻断开；\n"
                    "服务器上的账号和别的设备不受影响。",
            confirm_text="退出登录", danger=True)
        if dialog.exec() != ConfirmDialog.DialogCode.Accepted:
            return
        window = self.window()
        stop = getattr(window, "stop_server_stream", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        result = account_module.logout(self.store)
        self._server_stream_state = "off"
        if result.get("ok"):
            self.server_status_label.setStyleSheet(f"color: {MUTED};")
            self.server_status_label.setText("已退出登录服务器账号")
        else:
            self.server_status_label.setStyleSheet(f"color: {DANGER};")
            self.server_status_label.setText(str(result.get("error") or "退出失败"))
        self.refresh_server_card()

    # -------------------------------------------------- 外观
    def _build_appearance_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.EYE, "外观",
            "浅色 / 深色、强调色与窗口通透度 —— 改一下这里立刻就变，并且自动保存",
        ))
        live_hint = QLabel("改这里的所有东西都会马上生效（不用点右上角「保存设置」），"
                           "也会立刻写进设置，下次打开还是这个样子。")
        live_hint.setObjectName("Tiny")
        live_hint.setWordWrap(True)
        live_hint.setStyleSheet(f"color: {MUTED};")
        card.body.addWidget(live_hint)

        # 外观：浅色 / 深色 / 自动（对齐 macOS 外观设置的三张预览图）
        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        mode_text = QVBoxLayout()
        mode_text.setSpacing(2)
        mode_label = QLabel("显示模式")
        mode_label.setStyleSheet(
            f"color: {TEXT}; font-size: 13px; font-weight: 600;"
        )
        mode_desc = QLabel("自动会在浅色与深色之间跟随系统设置")
        mode_desc.setObjectName("Tiny")
        mode_text.addWidget(mode_label)
        mode_text.addWidget(mode_desc)
        mode_row.addLayout(mode_text, 1)
        self._mode_cards: List[tuple] = []
        for mode_id in ("auto", "light", "dark"):
            mode_card = ModeCard(mode_id, theme.MODE_SHORT[mode_id])
            mode_card.clicked.connect(self._choose_mode)
            self._mode_cards.append((mode_card, mode_id))
            mode_row.addWidget(mode_card)
        card.body.addLayout(mode_row)

        accent_row = QHBoxLayout()
        accent_row.setSpacing(7)
        self._accent_swatches: List[tuple] = []
        for preset in theme.ACCENT_PRESETS:
            swatch = AccentSwatch(preset["accent"], preset["accent2"], preset["name"])
            swatch.clicked.connect(
                lambda a=preset["accent"], b=preset["accent2"]: self._choose_accent(a, b)
            )
            self._accent_swatches.append((swatch, preset["accent"], preset["accent2"]))
            accent_row.addWidget(swatch)
        self._custom_swatch = AccentSwatch(
            self._accent, self._accent2, "自定义颜色", custom=True
        )
        self._custom_swatch.clicked.connect(self._pick_custom_accent)
        accent_row.addWidget(self._custom_swatch)
        accent_row.addStretch(1)
        restore = SoftButton("恢复默认配色", "subtle", height=30, point_size=8.8)
        restore.clicked.connect(self._restore_appearance)
        accent_row.addWidget(restore)
        accent_block = QVBoxLayout()
        accent_block.setSpacing(7)
        accent_label = QLabel("强调色")
        accent_label.setObjectName("Small")
        accent_block.addWidget(accent_label)
        accent_block.addLayout(accent_row)
        card.body.addLayout(accent_block)

        browser_row = QHBoxLayout()
        browser_row.setSpacing(10)
        browser_text = QVBoxLayout()
        browser_text.setSpacing(2)
        browser_label = QLabel("浏览器风格")
        browser_label.setStyleSheet(
            f"color: {TEXT}; font-size: 13px; font-weight: 600;")
        browser_desc = QLabel("应用内网页（eLearning 等）用浅色还是深色")
        browser_desc.setObjectName("Tiny")
        browser_text.addWidget(browser_label)
        browser_text.addWidget(browser_desc)
        browser_row.addLayout(browser_text, 1)
        self.browser_theme_select = Select()
        self.browser_theme_select.setFixedWidth(190)
        for key, label in theme.BROWSER_THEME_LABELS.items():
            self.browser_theme_select.addItem(label, key)
        self.browser_theme_select.currentIndexChanged.connect(
            lambda _index: self._preview_appearance())
        browser_row.addWidget(self.browser_theme_select)
        card.body.addLayout(browser_row)

        bg_block = QVBoxLayout()
        bg_block.setSpacing(7)
        bg_label = QLabel("背景色调")
        bg_label.setObjectName("Small")
        bg_block.addWidget(bg_label)
        bg_hint = QLabel(
            "深色模式统一使用 Apple 深色背景；这里的色调在浅色模式下生效"
        )
        bg_hint.setObjectName("Tiny")
        bg_block.addWidget(bg_hint)
        bg_row = QHBoxLayout()
        bg_row.setSpacing(9)
        self._bg_chips: List[tuple] = []
        for preset in theme.BG_PRESETS:
            chip = BackgroundChip(preset)
            chip.clicked.connect(
                lambda preset_id=preset["id"]: self._choose_bg(preset_id)
            )
            self._bg_chips.append((chip, preset["id"]))
            bg_row.addWidget(chip)
        bg_row.addStretch(1)
        bg_block.addLayout(bg_row)
        card.body.addLayout(bg_block)

        alpha_block = QHBoxLayout()
        alpha_block.setSpacing(12)
        alpha_text = QVBoxLayout()
        alpha_text.setSpacing(2)
        alpha_label = QLabel("窗口不透明度")
        alpha_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        alpha_desc = QLabel(
            "100% 完全不透明；越低背后越透。想看到磨砂玻璃就把这条调低一些"
            f"（最低 {int(theme.ALPHA_MIN * 100)}%，再低界面就看不清了）")
        alpha_desc.setObjectName("Tiny")
        alpha_desc.setWordWrap(True)
        alpha_text.addWidget(alpha_label)
        alpha_text.addWidget(alpha_desc)
        alpha_block.addLayout(alpha_text, 1)
        self.alpha_slider = GlassSlider()
        self.alpha_slider.setRange(int(theme.ALPHA_MIN * 100), 100)
        self.alpha_slider.setFixedWidth(260)
        self.alpha_slider.valueChanged.connect(self._on_alpha_changed)
        self.alpha_slider.sliderReleased.connect(self._on_alpha_released)
        alpha_block.addWidget(self.alpha_slider)
        self.alpha_value = QLabel(f"{int(theme.DEFAULT_APPEARANCE['alpha'] * 100)}%")
        self.alpha_value.setFixedWidth(42)
        self.alpha_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.alpha_value.setObjectName("Small")
        alpha_block.addWidget(self.alpha_value)
        card.body.addLayout(alpha_block)

        blur_block = QHBoxLayout()
        blur_block.setSpacing(12)
        blur_text = QVBoxLayout()
        blur_text.setSpacing(2)
        blur_label = QLabel("背景模糊度")
        blur_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        blur_desc = QLabel("背景模糊的强度；0 表示不模糊（只保留透明度）。建议 16–32")
        blur_desc.setObjectName("Tiny")
        blur_text.addWidget(blur_label)
        blur_text.addWidget(blur_desc)
        blur_block.addLayout(blur_text, 1)
        self.blur_slider = GlassSlider()
        self.blur_slider.setRange(0, 60)
        self.blur_slider.setFixedWidth(260)
        self.blur_slider.valueChanged.connect(self._on_blur_changed)
        blur_block.addWidget(self.blur_slider)
        self.blur_value = QLabel("24 px")
        self.blur_value.setFixedWidth(52)
        self.blur_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.blur_value.setObjectName("Small")
        blur_block.addWidget(self.blur_value)
        card.body.addLayout(blur_block)

        glass_row = QHBoxLayout()
        glass_row.setSpacing(12)
        glass_text = QVBoxLayout()
        glass_text.setSpacing(2)
        glass_label = QLabel("磨砂玻璃")
        glass_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        glass_desc = QLabel(
            "开启后窗口背景是「模糊后的桌面」（截屏模糊 + 主题色叠加）。"
            "窗口本身还是不透明的，所以内嵌网页照常能点，不会穿透"
        )
        glass_desc.setObjectName("Tiny")
        glass_desc.setWordWrap(True)
        glass_text.addWidget(glass_label)
        glass_text.addWidget(glass_desc)
        glass_row.addLayout(glass_text, 1)
        self.glass_switch = ToggleSwitch(self._glass)
        self.glass_switch.toggled.connect(self._on_glass_toggled)
        glass_row.addWidget(self.glass_switch)
        card.body.addLayout(glass_row)

        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(12)
        tabs_text = QVBoxLayout()
        tabs_text.setSpacing(2)
        tabs_label = QLabel("浏览器多标签页")
        tabs_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        tabs_desc = QLabel(
            "开启后内嵌网页（作业 / eHall / AI）顶部会出现标签页栏：可以像 Edge 一样"
            "新建、切换、关闭标签页，每个标签页各自独立")
        tabs_desc.setObjectName("Tiny")
        tabs_desc.setWordWrap(True)
        tabs_text.addWidget(tabs_label)
        tabs_text.addWidget(tabs_desc)
        tabs_row.addLayout(tabs_text, 1)
        self.browser_tabs_switch = ToggleSwitch(
            (self.store.get_setting("browser_tabs", "0") or "0") == "1")
        self.browser_tabs_switch.toggled.connect(self._on_browser_tabs_toggled)
        tabs_row.addWidget(self.browser_tabs_switch)
        card.body.addLayout(tabs_row)

        translucent_row = QHBoxLayout()
        translucent_row.setSpacing(12)
        translucent_text = QVBoxLayout()
        translucent_text.setSpacing(2)
        translucent_label = QLabel("半透明窗口（高级，不推荐）")
        translucent_label.setStyleSheet(
            f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        translucent_desc = QLabel(
            "只有想让桌面真正透过来时才需要开。注意：半透明窗口（分层窗口）会让内嵌网页"
            "（作业 / eHall / AI）的点击穿透到其他程序——磨砂和圆角现在不透明窗口也能用，"
            "所以一般不用开这个。改完要重启软件")
        translucent_desc.setObjectName("Tiny")
        translucent_desc.setWordWrap(True)
        translucent_text.addWidget(translucent_label)
        translucent_text.addWidget(translucent_desc)
        translucent_row.addLayout(translucent_text, 1)
        self.translucent_switch = ToggleSwitch(
            (self.store.get_setting("window_translucent", "0") or "0") == "1")
        self.translucent_switch.toggled.connect(self._on_translucent_toggled)
        translucent_row.addWidget(self.translucent_switch)
        card.body.addLayout(translucent_row)

        mascot_row = QHBoxLayout()
        mascot_row.setSpacing(12)
        mascot_text = QVBoxLayout()
        mascot_text.setSpacing(2)
        mascot_label = QLabel("看板娘")
        mascot_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        mascot_desc = QLabel(
            "在窗口右下角养一只小生物：会呼吸、会眨眼，点一下陪你说话，可以拖着换位置"
        )
        mascot_desc.setObjectName("Tiny")
        mascot_desc.setWordWrap(True)
        mascot_text.addWidget(mascot_label)
        mascot_text.addWidget(mascot_desc)
        mascot_row.addLayout(mascot_text, 1)
        self.mascot_switch = ToggleSwitch(
            (self.store.get_setting("mascot_on", "1") or "1") != "0")
        self.mascot_switch.toggled.connect(self._on_mascot_toggled)
        mascot_row.addWidget(self.mascot_switch)
        card.body.addLayout(mascot_row)

        mascot_style_row = QHBoxLayout()
        mascot_style_row.setSpacing(8)
        shape_label = QLabel("形象")
        shape_label.setObjectName("Small")
        mascot_style_row.addWidget(shape_label)
        self.mascot_style_select = Select()
        self.mascot_style_select.setFixedWidth(150)
        from .mascot import STYLES

        for style_id, style_name in STYLES:
            self.mascot_style_select.addItem(style_name, style_id)
        self.mascot_style_select.currentIndexChanged.connect(self._on_mascot_style)
        mascot_style_row.addWidget(self.mascot_style_select)
        self.mascot_image_button = SoftButton("选一张图片", "ghost", height=30,
                                              min_width=110, point_size=8.4)
        self.mascot_image_button.setToolTip("支持 png / jpg / webp / gif")
        self.mascot_image_button.clicked.connect(self._on_mascot_image)
        mascot_style_row.addWidget(self.mascot_image_button)
        mascot_style_row.addStretch(1)
        card.body.addLayout(mascot_style_row)
        self._sync_mascot_controls()


        font_text = QVBoxLayout()
        font_text.setSpacing(2)
        font_label = QLabel("字体")
        font_label.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 600;")
        font_desc = QLabel(
            "默认使用鸿蒙黑体（HarmonyOS Sans SC），中文与西文同一套字形；也可另选搭配"
        )
        font_desc.setObjectName("Tiny")
        font_desc.setWordWrap(True)
        font_text.addWidget(font_label)
        font_text.addWidget(font_desc)
        card.body.addLayout(font_text)

        # 字体选择：分栏变窄时横向滚动，别把说明挤成一条
        font_row = HScrollRow(height=58, spacing=10)

        def _labelled(title: str, widget) -> QWidget:
            """给下拉框加一行可见的小标题（截图里三个框光秃秃的，看不出谁是谁）。"""
            box = QWidget()
            column = QVBoxLayout(box)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)
            caption = QLabel(title)
            caption.setObjectName("Tiny")
            column.addWidget(caption)
            column.addWidget(widget)
            return box

        self.font_preset_select = Select()
        self.font_preset_select.setFixedWidth(216)
        self.font_preset_select.setToolTip("字体搭配")
        for preset in theme.FONT_PRESETS:
            self.font_preset_select.addItem(preset["name"], preset["id"])
        self.font_preset_select.addItem("自定义（下方手动选择）", "custom")
        self.font_preset_select.currentIndexChanged.connect(self._on_font_preset)
        font_row.addWidget(_labelled("字体搭配（整套预设）",
                                     self.font_preset_select))

        self.font_zh_select = Select()
        self.font_zh_select.setFixedWidth(190)
        self.font_zh_select.setToolTip("中文字体")
        self.font_zh_select.currentIndexChanged.connect(self._on_font_changed)
        font_row.addWidget(_labelled("中文字体", self.font_zh_select))
        self.font_en_select = Select()
        self.font_en_select.setFixedWidth(190)
        self.font_en_select.setToolTip("西文字体")
        self.font_en_select.currentIndexChanged.connect(self._on_font_changed)
        font_row.addWidget(_labelled("西文字体", self.font_en_select))
        self.font_reset_button = SoftButton("恢复默认", "subtle", height=30, point_size=8.8)
        self.font_reset_button.clicked.connect(self._reset_fonts)
        font_row.addWidget(self.font_reset_button)
        card.body.addWidget(font_row)

        self._refresh_appearance_ui()
        return card

    def _on_glass_toggled(self, checked: bool) -> None:
        self._glass = bool(checked)
        # 磨砂玻璃开了/关了要马上能看到（不用等「保存设置」）
        self._preview_appearance()

    def _on_mascot_toggled(self, checked: bool) -> None:
        """看板娘开关：立即生效（不用等保存）。"""
        self.store.set_setting("mascot_on", "1" if checked else "0")
        window = self.window()
        mascot = getattr(window, "mascot", None)
        if mascot is not None:
            try:
                mascot.set_on(bool(checked))
            except Exception:
                pass
            if checked:
                try:
                    mascot.say("我来啦！", 2.4)
                except Exception:
                    pass
        self.notify.emit("看板娘已出现" if checked else "看板娘先去睡了")

    def _mascot(self):
        return getattr(self.window(), "mascot", None)

    def _sync_mascot_controls(self) -> None:
        style = (self.store.get_setting("mascot_style", "blob") or "blob").strip()
        select = getattr(self, "mascot_style_select", None)
        if select is not None:
            select.blockSignals(True)
            index = select.findData(style)
            select.setCurrentIndex(max(0, index))
            select.blockSignals(False)
        button = getattr(self, "mascot_image_button", None)
        if button is not None:
            button.setEnabled(style == "image")

    def _on_mascot_style(self, _index: int) -> None:
        style = self.mascot_style_select.currentData() or "blob"
        self.store.set_setting("mascot_style", style)
        mascot = self._mascot()
        if mascot is not None:
            try:
                mascot.set_style(style)
            except Exception:
                pass
        self.mascot_image_button.setEnabled(style == "image")
        if style == "image":
            self._on_mascot_image()

    def _on_mascot_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择看板娘形象", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if not path:
            return
        self.store.set_setting("mascot_style", "image")
        self.store.set_setting("mascot_image", path)
        mascot = self._mascot()
        if mascot is not None:
            try:
                mascot.set_style("image", path)
            except Exception:
                pass
        self._sync_mascot_controls()

    def _on_blur_changed(self, value: int) -> None:
        self._blur = float(value)
        self.blur_value.setText(f"{value} px")
        # 拖滑杆时数值每变一点都会触发：节流之后再重画背景
        self._preview_soon()

    # ---- 字体 ----
    @staticmethod
    def _font_choices() -> tuple:
        """返回 (中文字体列表, 西文字体列表)，按是否支持简体中文区分。"""
        from PySide6.QtGui import QFontDatabase

        families = sorted(QFontDatabase.families())
        cjk = QFontDatabase.WritingSystem.SimplifiedChinese
        zh, en = [], []
        for family in families:
            try:
                supports = cjk in QFontDatabase.writingSystems(family)
            except Exception:
                supports = False
            (zh if supports else en).append(family)
        return zh, en

    def _fill_font_selects(self) -> None:
        zh_list, en_list = self._font_choices()
        for widget, items, current, default_label in (
            (self.font_zh_select, zh_list, self._font_zh, "默认（鸿蒙黑体）"),
            (self.font_en_select, en_list, self._font_en, "默认（鸿蒙黑体）"),
        ):
            widget.blockSignals(True)
            widget.clear()
            widget.addItem(default_label, "")
            for name in items:
                widget.addItem(name, name)
            index = widget.findData(current)
            widget.setCurrentIndex(index if index >= 0 else 0)
            widget.blockSignals(False)
        self._sync_font_preset()

    def _on_font_changed(self, _index: int) -> None:
        self._font_zh = self.font_zh_select.currentData() or ""
        self._font_en = self.font_en_select.currentData() or ""
        self._sync_font_preset()
        self._preview_appearance()

    def _on_font_preset(self, _index: int) -> None:
        preset_id = self.font_preset_select.currentData() or "apple"
        for preset in theme.FONT_PRESETS:
            if preset["id"] == preset_id:
                self._font_zh = preset["zh"]
                self._font_en = preset["en"]
                self._fill_font_selects()
                self._preview_appearance()
                return

    def _reset_fonts(self) -> None:
        self._font_zh = ""
        self._font_en = ""
        self._sync_font_preset()
        self._fill_font_selects()
        self._preview_appearance()

    def _sync_font_preset(self) -> None:
        if not hasattr(self, "font_preset_select"):
            return
        target = "custom"
        for preset in theme.FONT_PRESETS:
            if preset["zh"] == self._font_zh and preset["en"] == self._font_en:
                target = preset["id"]
                break
        index = self.font_preset_select.findData(target)
        self.font_preset_select.blockSignals(True)
        self.font_preset_select.setCurrentIndex(index if index >= 0 else 0)
        self.font_preset_select.blockSignals(False)

    def _choose_mode(self, mode_id: str) -> None:
        self._mode = mode_id
        self._refresh_appearance_ui()
        self._preview_appearance()

    def _choose_accent(self, accent: str, accent2: str) -> None:
        self._accent = accent
        self._accent2 = accent2
        self._refresh_appearance_ui()
        self._preview_appearance()

    def _pick_custom_accent(self) -> None:
        color = QColorDialog.getColor(
            QColor(self._accent), self, "选择强调色"
        )
        if color.isValid():
            self._accent = color.name()
            self._accent2 = theme.harmonize_accent(self._accent)
            self._refresh_appearance_ui()
            self._preview_appearance()

    def _choose_bg(self, preset_id: str) -> None:
        self._bg = preset_id
        self._refresh_appearance_ui()
        self._preview_appearance()

    def _restore_appearance(self) -> None:
        self._accent = theme.DEFAULT_APPEARANCE["accent"]
        self._accent2 = theme.DEFAULT_APPEARANCE["accent2"]
        self._bg = theme.DEFAULT_APPEARANCE["bg"]
        self._alpha = theme.DEFAULT_APPEARANCE["alpha"]
        self._glass = theme.DEFAULT_APPEARANCE["glass"]
        self._blur = theme.DEFAULT_APPEARANCE["blur"]
        self._mode = theme.DEFAULT_APPEARANCE["mode"]
        self._refresh_appearance_ui()
        self._preview_appearance()

    # -------------------------------------------------- 外观实时预览
    def _preview_appearance(self, message: str = "") -> None:
        """把外观控件里「现在这些值」立刻套到界面上，并顺手存下来。

        以前这些只在点「保存设置」时才生效，换强调色/深浅色得先保存才能看到，
        现在点一下就变、滑杆一拖就变（滑杆那种连续变化走 `_preview_soon()` 节流）。
        只重刷主题，不重读各页面的数据，所以不会把正在填的表单清掉。
        """
        try:
            self._preview_timer.stop()
        except Exception:
            pass
        if not hasattr(self, "alpha_slider"):
            return          # 控件还没建完（构造期间滑块初始化会触发）
        if not getattr(self, "_preview_ready", False):
            return          # 建页面 / 首次回填阶段不做预览，免得启动就白刷一遍主题
        try:
            theme.set_appearance(self._accent, self._accent2, self._bg, self._alpha,
                                 self._glass, self._blur, self._mode)
            theme.set_fonts(self._font_zh, self._font_en)
            select = getattr(self, "browser_theme_select", None)
            if select is not None:
                theme.set_browser_theme(select.currentData() or "follow")
            theme.save_to_store(self.store)
        except Exception:
            pass
        self._refresh_appearance_ui()
        window = self.window()
        handler = getattr(window, "preview_appearance", None)
        if callable(handler):
            try:
                handler(message, refresh_pages=False)
                return
            except TypeError:
                try:
                    handler(message)
                    return
                except Exception:
                    pass
            except Exception:
                pass
        try:
            theme.apply_theme(QApplication.instance())
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass

    def _preview_soon(self) -> None:
        """连续变化（模糊度滑杆）用：120ms 内的多次改动只应用一次。"""
        if not getattr(self, "_preview_ready", False):
            return
        timer = getattr(self, "_preview_timer", None)
        if timer is None:
            self._preview_appearance()
            return
        if not timer.isActive():
            timer.start(120)

    def _on_alpha_changed(self, value: int) -> None:
        self._alpha = value / 100.0
        self.alpha_value.setText(f"{value}%")
        # 轻量预览：只改不透明度并重绘外壳，不重新套样式表（那样拖起来会卡）
        alpha = theme.set_alpha(self._alpha)
        self._alpha = alpha
        window = self.window()
        handler = getattr(window, "preview_alpha", None)
        if callable(handler):
            try:
                handler(alpha)
            except Exception:
                pass

    def _on_alpha_released(self) -> None:
        """松开滑块后再补一次完整重绘，避免拖动过程留下残影。"""
        try:
            theme.save_to_store(self.store)      # 松手即保存，别等「保存设置」
        except Exception:
            pass
        try:
            window = self.window()
            if window is not None:
                window.update()
            self.update()
        except Exception:
            pass

    def _refresh_appearance_ui(self) -> None:
        if not hasattr(self, "alpha_slider"):
            return
        if hasattr(self, "_mode_cards"):
            for mode_card, mode_id in self._mode_cards:
                mode_card.set_selected(mode_id == self._mode)
        matched = False
        for swatch, accent, _accent2 in self._accent_swatches:
            is_selected = accent.lower() == self._accent.lower()
            matched = matched or is_selected
            swatch.set_selected(is_selected)
        self._custom_swatch.set_colors(self._accent, self._accent2)
        self._custom_swatch.set_selected(not matched)
        for chip, preset_id in self._bg_chips:
            chip.set_selected(preset_id == self._bg)
        # 回填滑杆时别触发预览（否则每建一次设置页都白刷一遍主题）
        for widget in (getattr(self, "alpha_slider", None),
                       getattr(self, "blur_slider", None)):
            if widget is not None:
                widget.blockSignals(True)
        self.alpha_slider.setValue(int(round(self._alpha * 100)))
        self.alpha_value.setText(f"{int(round(self._alpha * 100))}%")
        if hasattr(self, "blur_slider"):
            self.blur_slider.setValue(int(round(self._blur)))
            self.blur_value.setText(f"{int(round(self._blur))} px")
        for widget in (getattr(self, "alpha_slider", None),
                       getattr(self, "blur_slider", None)):
            if widget is not None:
                widget.blockSignals(False)
        if hasattr(self, "glass_switch"):
            self.glass_switch.blockSignals(True)
            self.glass_switch.setChecked(bool(self._glass), animate=False)
            self.glass_switch.blockSignals(False)

    def _build_smtp_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.MAIL, "发件邮箱（SMTP）",
            "可配置多个发件邮箱，随时切换；当前选中的账户用于发送汇报",
        ))

        account_row = QHBoxLayout()
        account_row.setSpacing(8)
        self.account_select = Select()
        self.account_select.currentIndexChanged.connect(self._on_account_selected)
        account_row.addWidget(self.account_select, 1)
        self.add_account_button = SoftButton("新增账户", "ghost", glyph=icons.ADD,
                                            height=34, min_width=104, point_size=8.8)
        self.add_account_button.clicked.connect(self._add_account)
        account_row.addWidget(self.add_account_button)
        self.delete_account_button = SoftButton("删除", "ghost", height=34,
                                               min_width=72, point_size=8.8)
        self.delete_account_button.clicked.connect(self._delete_account)
        account_row.addWidget(self.delete_account_button)
        account_label = QLabel("发件邮箱账户")
        account_label.setObjectName("Small")
        card.body.addWidget(account_label)
        card.body.addLayout(account_row)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("smtp.qq.com")
        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("465")
        self.port_edit.setValidator(QIntValidator(1, 65535, self))
        self.security_select = Select()
        for key, label in SMTP_SECURITY:
            self.security_select.addItem(label, key)
        self.security_select.currentIndexChanged.connect(self._on_security_changed)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("登录账号，通常与发件邮箱一致")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("邮箱授权码 / 应用专用密码")
        password_wrap = QWidget()
        password_row = QHBoxLayout(password_wrap)
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.setSpacing(6)
        password_row.addWidget(self.password_edit, 1)
        # 密码只可修改、不可查看：不再提供“显示密码”按钮
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("显示在收件人邮箱里的发件人名称")
        self.sender_edit = QLineEdit()
        self.sender_edit.setPlaceholderText("name@example.com")

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        grid.addWidget(FieldBlock("服务器地址", self.host_edit, stretch=2), 0, 0)
        grid.addWidget(FieldBlock("端口", self.port_edit), 0, 1)
        grid.addWidget(FieldBlock("加密方式", self.security_select), 1, 0)
        grid.addWidget(FieldBlock("登录账号", self.user_edit), 1, 1)
        grid.addWidget(FieldBlock("密码 / 授权码", password_wrap), 2, 0)
        grid.addWidget(FieldBlock("发件人名称", self.name_edit), 2, 1)
        grid.addWidget(FieldBlock("发件邮箱", self.sender_edit), 3, 0)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        card.body.addLayout(grid)

        hint = QLabel(
            "提示：QQ、163 等邮箱需要在网页版设置中开启 SMTP 服务，并使用「授权码」而不是登录密码；"
            "Gmail 使用「应用专用密码」。密码通过 Windows DPAPI 加密后保存在本地数据库。"
        )
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        card.body.addWidget(hint)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.test_button = SoftButton("测试连接", "ghost", height=34, min_width=104)
        self.test_button.clicked.connect(self._test_connection)
        controls.addWidget(self.test_button)
        self.test_status = QLabel("")
        self.test_status.setObjectName("Small")
        controls.addWidget(self.test_status, 1)
        card.body.addLayout(controls)

        # ---------------- OAuth2（Outlook / 微软账户发信必须用它）
        oauth_row = QHBoxLayout()
        oauth_row.setSpacing(10)
        oauth_text = QVBoxLayout()
        oauth_text.setSpacing(2)
        oauth_label = QLabel("用 OAuth2 登录（Outlook / 微软账户）")
        oauth_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        oauth_desc = QLabel(
            "微软从 2024 年起关停了 outlook.com / hotmail.com 的「账号密码 + 应用密码」发信，"
            "只能用 OAuth2。点右边「微软授权」走一次设备代码流程即可（不需要填密码）。")
        oauth_desc.setObjectName("Tiny")
        oauth_desc.setWordWrap(True)
        oauth_text.addWidget(oauth_label)
        oauth_text.addWidget(oauth_desc)
        oauth_row.addLayout(oauth_text, 1)
        self.oauth_switch = ToggleSwitch(
            (self.store.get_setting("smtp_oauth", "0") or "0") == "1")
        self.oauth_switch.toggled.connect(self._on_smtp_oauth_toggled)
        oauth_row.addWidget(self.oauth_switch)
        self.oauth_button = SoftButton("微软授权", "ghost", height=32,
                                       min_width=96, point_size=8.6)
        self.oauth_button.clicked.connect(self._open_oauth_dialog)
        oauth_row.addWidget(self.oauth_button)
        card.body.addLayout(oauth_row)

        self.oauth_status = QLabel("")
        self.oauth_status.setObjectName("Tiny")
        self.oauth_status.setWordWrap(True)
        card.body.addWidget(self.oauth_status)
        self._refresh_oauth_status()
        return card

    # -------------------------------------------------- 发件的 OAuth2
    def _on_smtp_oauth_toggled(self, checked: bool) -> None:
        self.store.set_setting("smtp_oauth", "1" if checked else "0")
        self._refresh_oauth_status()

    def _open_oauth_dialog(self) -> None:
        from .oauth_dialog import OAuthDialog

        dialog = OAuthDialog(self.store, self.window())
        dialog.exec()
        self._refresh_oauth_status()

    def _refresh_oauth_status(self) -> None:
        from . import oauth2

        if not hasattr(self, "oauth_status"):
            return
        enabled = (self.store.get_setting("smtp_oauth", "0") or "0") == "1"
        parts = ["OAuth2：已启用" if enabled else "OAuth2：未启用"]
        if not oauth2.configured(self.store):
            parts.append("还没填客户端 ID（点「微软授权」里面填）")
        elif oauth2.has_refresh_token(self.store):
            parts.append("已授权（发信会自动续期）")
        else:
            parts.append("还没授权（点「微软授权」走一次流程）")
        self.oauth_status.setText("；".join(parts))

    # -------------------------------------------------- 课程表（可选）
    def _build_timetable_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=12)
        card.body.addLayout(self._section_head(
            icons.CALENDAR, "课程表（可选）",
            "从 DanXi（旦夕）或教务系统导出的 .ics 课表导入日程；不导入也不影响其它功能",
        ))

        enable_row = QHBoxLayout()
        enable_row.setSpacing(12)
        enable_text = QVBoxLayout()
        enable_text.setSpacing(2)
        enable_label = QLabel("启用课程表导入")
        enable_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        enable_desc = QLabel("开启后才能导入课表；导入的课程会出现在日历里，可单独编辑或删除")
        enable_desc.setObjectName("Tiny")
        enable_desc.setWordWrap(True)
        enable_text.addWidget(enable_label)
        enable_text.addWidget(enable_desc)
        enable_row.addLayout(enable_text, 1)
        self.timetable_switch = ToggleSwitch(False)
        self.timetable_switch.toggled.connect(self._on_timetable_toggled)
        enable_row.addWidget(self.timetable_switch)
        card.body.addLayout(enable_row)

        # 分栏变窄时这两排按钮放不下 → 横向滚动（不挤在一起）
        actions = HScrollRow(height=40, spacing=8)
        self.import_ics_button = SoftButton("导入 .ics 课表", "ghost", glyph=icons.ADD,
                                            height=34, min_width=132, point_size=8.8)
        self.import_ics_button.clicked.connect(self._import_timetable)
        actions.addWidget(self.import_ics_button)
        self.detect_danxi_button = SoftButton("查找 DanXi", "ghost", height=34,
                                              min_width=104, point_size=8.8)
        self.detect_danxi_button.clicked.connect(self._detect_danxi)
        actions.addWidget(self.detect_danxi_button)
        actions.addStretch(1)
        card.body.addWidget(actions)

        # ---- 复旦教务直连（内嵌浏览器登录后抓取）----
        fdu_row = HScrollRow(height=40, spacing=8)
        self.fdu_button = SoftButton("登录教务抓取课表", "primary", height=34,
                                     min_width=158, point_size=8.8)
        self.fdu_button.clicked.connect(self._sync_from_fdu)
        fdu_row.addWidget(self.fdu_button)
        self.exam_button = SoftButton("导入考试安排", "ghost", height=34,
                                      min_width=128, point_size=8.8)
        self.exam_button.setToolTip(
            "从教务读「我的考试」，加进日历（分组「考试」），并可同时设为重要日倒计时")
        self.exam_button.clicked.connect(self._import_exams)
        fdu_row.addWidget(self.exam_button)
        term_label = QLabel("学期第一周周一")
        term_label.setObjectName("Tiny")
        fdu_row.addWidget(term_label)
        self.term_start_edit = QLineEdit()
        self.term_start_edit.setPlaceholderText("例如 2026-09-07")
        self.term_start_edit.setFixedWidth(132)
        fdu_row.addWidget(self.term_start_edit)
        fdu_row.addStretch(1)
        card.body.addWidget(fdu_row)

        self.timetable_status = QLabel("")
        self.timetable_status.setObjectName("Small")
        self.timetable_status.setWordWrap(True)
        card.body.addWidget(self.timetable_status)

        hint = QLabel(
            "说明：DanXi（旦夕）是网络客户端，课表数据在它的服务器上，本机没有可直接读取的课表文件，"
            "所以这里通过「日历文件」对接：在 DanXi 里导出课表为 .ics（或从教务系统导出），再在本页选择该文件即可。"
        )
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        card.body.addWidget(hint)
        return card

    def _refresh_timetable_ui(self) -> None:
        if not hasattr(self, "timetable_switch"):
            return
        enabled = self.store.get_setting("timetable_enabled", "0") == "1"
        self.timetable_switch.blockSignals(True)
        self.timetable_switch.setChecked(enabled, animate=False)
        self.timetable_switch.blockSignals(False)
        self.import_ics_button.setEnabled(enabled)
        self.detect_danxi_button.setEnabled(enabled)
        self.fdu_button.setEnabled(enabled)
        self._set_timetable_status(
            self.store.get_setting("timetable_status", ""), MUTED
        )

    def _on_timetable_toggled(self, checked: bool) -> None:
        self.import_ics_button.setEnabled(bool(checked))
        self.detect_danxi_button.setEnabled(bool(checked))
        self.fdu_button.setEnabled(bool(checked))
        # 拨一下就存下来，不用等「保存设置」
        try:
            self.store.set_setting("timetable_enabled", "1" if checked else "0")
        except Exception:
            pass

    def _set_timetable_status(self, message: str, color: str) -> None:
        if not hasattr(self, "timetable_status"):
            return
        self.timetable_status.setText(message)
        self.timetable_status.setStyleSheet(f"color: {color}; font-size: 9pt;")

    def _import_timetable(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择课表文件", "", "日历文件 (*.ics);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            events = timetable.read_ics(path)
        except Exception as exc:  # noqa: BLE001 - 读取失败要提示用户
            self._set_timetable_status(f"读取失败：{exc}", DANGER)
            return
        rows = timetable.flatten(events)
        if not rows:
            self._set_timetable_status("这个文件里没有找到可导入的课程", WARNING)
            return
        added, skipped = timetable.import_rows(self.store, rows)
        message = f"已导入 {added} 节课"
        if skipped:
            message += f"，跳过 {skipped} 节重复课程"
        self.store.set_setting("timetable_status", message)
        self._set_timetable_status(message, SUCCESS if added else MUTED)
        self.notify.emit(message)
        if added:
            self.settings_saved.emit()

    def _detect_danxi(self) -> None:
        found = timetable.detect_danxi()
        if found is None:
            self._set_timetable_status(
                "没有找到 DanXi 安装目录；也可以直接用「登录教务抓取课表」", WARNING
            )
            return
        self._set_timetable_status(
            f"已找到 DanXi：{found}。课表在它的服务器上，请用「登录教务抓取课表」直接同步",
            MUTED,
        )

    # ---- 复旦教务直连 ----
    def _first_monday(self) -> date:
        text = self.term_start_edit.text().strip() if hasattr(self, "term_start_edit") else ""
        for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, pattern).date()
            except (TypeError, ValueError):
                continue
        today = date.today()
        return today - timedelta(days=today.weekday())

    def _sync_from_fdu(self) -> None:
        def _status(text: str, tone: str) -> None:
            color = {"ok": SUCCESS, "error": DANGER}.get(tone, MUTED)
            self._set_timetable_status(text, color)

        try:
            dialog = fdu.make_browser_dialog(
                self, on_data=self._on_fdu_data, on_status=_status
            )
        except fdu.FduError as exc:
            self._set_timetable_status(str(exc), DANGER)
            return
        dialog.exec()

    def _import_exams(self) -> None:
        """从教务读考试安排：浏览器登录 → 读取 → 确认后写日历 + 重要日。"""
        from .exam_dialog import ExamImportDialog

        dialog = ExamImportDialog(self.store, self.window())
        dialog.imported.connect(self._on_exams_imported)
        dialog.exec()

    def _on_exams_imported(self, added: int) -> None:
        if added <= 0:
            return
        message = f"已导入 {added} 场考试，可以在日历里看到"
        self._set_timetable_status(message, SUCCESS)
        self.notify.emit(message)
        self.settings_saved.emit()

    def _on_fdu_data(self, semester: str, payload: str, term_start: str = "") -> None:
        try:
            activities = fdu.parse_print_data(payload)
        except fdu.FduError as exc:
            self._set_timetable_status(str(exc), DANGER)
            return
        # 教务页面上写着「学年起始日期」，直接用它算第一周周一，省得手填
        if term_start:
            try:
                first_day = date.fromisoformat(term_start)
                monday = fdu.first_monday_from(first_day)
                self.term_start_edit.setText(monday.isoformat())
            except ValueError:
                monday = self._first_monday()
        else:
            monday = self._first_monday()
        rows = fdu.to_rows(activities, monday)
        if not rows:
            self._set_timetable_status("课表为空，可能学期还没开始", WARNING)
            return
        group_id = fdu.ensure_course_group(self.store)
        added, skipped = fdu.import_rows(self.store, rows, group_id=group_id)
        message = f"已从教务导入 {added} 条课程安排"
        if skipped:
            message += f"，跳过 {skipped} 条重复"
        self.store.set_setting("timetable_status", message)
        self._set_timetable_status(message, SUCCESS if added else MUTED)
        self.notify.emit(message)
        if added:
            self.settings_saved.emit()

    def _build_recipient_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=12)
        card.body.addLayout(self._section_head(
            icons.CONTACT, "收件人与常用联系人",
            "新建汇报时会自动填入默认收件邮箱，也可以在下拉列表中选择联系人",
        ))
        self.default_to_edit = QLineEdit()
        self.default_to_edit.setPlaceholderText("boss@example.com")
        card.body.addWidget(FieldBlock("默认收件邮箱", self.default_to_edit))

        # 邮件签名：附在每封信末尾
        self.signature_edit = QPlainTextEdit()
        self.signature_edit.setFixedHeight(88)
        self.signature_edit.setPlaceholderText(
            "例如：\n你的名字\n某某大学 某某学院\n学号@m.example.edu.cn"
        )
        signature_block = QVBoxLayout()
        signature_block.setSpacing(4)
        signature_label = QLabel("邮件签名")
        signature_label.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 12px; font-weight: 600;"
        )
        signature_hint = QLabel("每封邮件的正文末尾都会自动附上（汇报邮件和常规邮件都生效）")
        signature_hint.setObjectName("Tiny")
        signature_block.addWidget(signature_label)
        signature_block.addWidget(self.signature_edit)
        signature_block.addWidget(signature_hint)
        card.body.addLayout(signature_block)

        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {BORDER};")
        card.body.addWidget(divider)

        head = QHBoxLayout()
        label = QLabel("常用联系人")
        label.setObjectName("Small")
        head.addWidget(label)
        head.addStretch(1)
        self.contact_status = QLabel("")
        self.contact_status.setObjectName("Tiny")
        head.addWidget(self.contact_status)
        card.body.addLayout(head)

        self.contacts_layout = QVBoxLayout()
        self.contacts_layout.setSpacing(6)
        card.body.addLayout(self.contacts_layout)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.contact_name_edit = QLineEdit()
        self.contact_name_edit.setPlaceholderText("姓名（可选）")
        self.contact_name_edit.setFixedWidth(150)
        self.contact_email_edit = QLineEdit()
        self.contact_email_edit.setPlaceholderText("邮箱地址")
        add_button = SoftButton("添加联系人", "ghost", glyph=icons.ADD, height=34,
                                point_size=9.0)
        add_button.clicked.connect(self._add_contact)
        add_row.addWidget(self.contact_name_edit)
        add_row.addWidget(self.contact_email_edit, 1)
        add_row.addWidget(add_button)
        card.body.addLayout(add_row)
        return card

    # -------------------------------------------------- 教务通知
    def _build_notice_card(self) -> Card:
        from . import notices

        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=12)
        card.body.addLayout(self._section_head(
            icons.BELL, "教务通知监控",
            "定时抓取教务处通知，有新的会弹窗提醒（默认每 3 小时一次）",
        ))

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        text1 = QVBoxLayout()
        text1.setSpacing(2)
        title1 = QLabel("开启监控")
        title1.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        desc1 = QLabel("关闭后不再抓取，已有通知仍然保留")
        desc1.setObjectName("Tiny")
        text1.addWidget(title1)
        text1.addWidget(desc1)
        row1.addLayout(text1, 1)
        self.notice_switch = ToggleSwitch(True)
        row1.addWidget(self.notice_switch)
        card.body.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        interval_label = QLabel("抓取间隔")
        interval_label.setObjectName("Small")
        row2.addWidget(interval_label)
        self.notice_interval_edit = QLineEdit()
        self.notice_interval_edit.setFixedWidth(70)
        self.notice_interval_edit.setToolTip("单位：小时，可填 0.5–48")
        row2.addWidget(self.notice_interval_edit)
        hour_label = QLabel("小时")
        hour_label.setObjectName("Small")
        row2.addWidget(hour_label)
        row2.addSpacing(10)

        source_label = QLabel("来源")
        source_label.setObjectName("Small")
        row2.addWidget(source_label)
        self.notice_source_boxes = {}
        for source in notices.SOURCES:
            box = QCheckBox(source["name"])
            box.setChecked(source["id"] in notices.DEFAULT_SOURCES)
            self.notice_source_boxes[source["id"]] = box
            row2.addWidget(box)
        row2.addStretch(1)

        self.notice_refresh_button = SoftButton("立即抓取", "ghost", height=32,
                                                min_width=100, point_size=8.8)
        self.notice_refresh_button.clicked.connect(self._refresh_notices)
        row2.addWidget(self.notice_refresh_button)
        card.body.addLayout(row2)

        self.notice_status = QLabel("")
        self.notice_status.setObjectName("Tiny")
        self.notice_status.setWordWrap(True)
        card.body.addWidget(self.notice_status)

        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {BORDER};")
        card.body.addWidget(divider)

        head = QHBoxLayout()
        head.setSpacing(8)
        list_label = QLabel("最近通知")
        list_label.setObjectName("Small")
        head.addWidget(list_label)
        head.addStretch(1)
        self.notice_read_button = SoftButton("全部标为已读", "subtle", height=28,
                                             min_width=110, point_size=8.4)
        self.notice_read_button.clicked.connect(self._mark_notices_read)
        head.addWidget(self.notice_read_button)
        card.body.addLayout(head)

        self.notice_list_layout = QVBoxLayout()
        self.notice_list_layout.setSpacing(4)
        card.body.addLayout(self.notice_list_layout)
        return card

    def _load_notice_settings(self) -> None:
        from . import notices

        store = self.store
        self.notice_switch.blockSignals(True)
        self.notice_switch.setChecked(
            (store.get_setting("notice_enabled", "1") or "1") == "1", animate=False)
        self.notice_switch.blockSignals(False)
        self.notice_interval_edit.setText(
            store.get_setting("notice_interval_hours",
                              str(notices.DEFAULT_INTERVAL_HOURS))
            or str(notices.DEFAULT_INTERVAL_HOURS))
        saved = (store.get_setting("notice_sources", ",".join(notices.DEFAULT_SOURCES))
                 or "")
        picked = [item for item in saved.split(",") if item]
        for source_id, box in self.notice_source_boxes.items():
            box.setChecked(source_id in picked)
        self._refresh_notice_list()

    def _save_notice_settings(self) -> None:
        picked = [source_id for source_id, box in self.notice_source_boxes.items()
                  if box.isChecked()]
        self.store.update_settings({
            "notice_enabled": "1" if self.notice_switch.isChecked() else "0",
            "notice_interval_hours": self.notice_interval_edit.text().strip() or "3",
            "notice_sources": ",".join(picked),
        })

    def _refresh_notice_list(self) -> None:
        from .widgets import clear_layout

        clear_layout(self.notice_list_layout)
        rows = self.store.list_notices(8)
        if not rows:
            empty = QLabel("还没有抓取过通知")
            empty.setObjectName("Tiny")
            self.notice_list_layout.addWidget(empty)
        for row in rows:
            line = QLabel(
                f'<a href="{row["url"]}" style="color:{ACCENT};'
                f' text-decoration:none">{row["title"]}</a>'
                f' <span style="color:{FAINT}">· {row["fetched_at"][:16]}</span>'
            )
            line.setOpenExternalLinks(True)
            line.setWordWrap(True)
            line.setStyleSheet("font-size: 12px;")
            self.notice_list_layout.addWidget(line)
        last = self.store.get_setting("notice_last_run", "") or "还没有抓取过"
        unread = self.store.unread_notice_count()
        self.notice_status.setText(f"上次抓取：{last} · 未读 {unread} 条")

    def _refresh_notices(self) -> None:
        from . import notices

        self._save_dir_settings()
        self._save_notice_settings()
        self.notice_status.setText("正在抓取通知…")
        self.notice_refresh_button.setEnabled(False)
        try:
            new_items = notices.sync(self.store, self)
        except Exception as exc:  # noqa: BLE001
            self.notice_status.setText(f"抓取失败：{exc}")
            self.notice_refresh_button.setEnabled(True)
            return
        self.notice_refresh_button.setEnabled(True)
        self._refresh_notice_list()
        if new_items:
            self.notify.emit(f"抓到 {len(new_items)} 条新通知")
        else:
            self.notify.emit("没有新通知")

    def _mark_notices_read(self) -> None:
        self.store.mark_notices_read()
        self._refresh_notice_list()

    def _build_focus_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.CLOCK, "专注模式",
            "开始专注后桌面会出现一个悬浮计时器；离开本软件太久会提醒你（白名单里的网页或软件不算离开）",
        ))

        row = QHBoxLayout()
        row.setSpacing(12)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title = QLabel("离开提醒")
        title.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        desc = QLabel("专注中如果切到别的软件太久，就弹一个提醒把你拉回来")
        desc.setObjectName("Tiny")
        desc.setWordWrap(True)
        text_box.addWidget(title)
        text_box.addWidget(desc)
        row.addLayout(text_box, 1)
        self.focus_switch = ToggleSwitch(
            (self.store.get_setting("focus_away_enabled", "1") or "1") != "0")
        row.addWidget(self.focus_switch)
        card.body.addLayout(row)

        minutes_row = QHBoxLayout()
        minutes_row.setSpacing(8)
        minutes_label = QLabel("离开多久算走神")
        minutes_label.setObjectName("Small")
        minutes_row.addWidget(minutes_label)
        self.focus_minutes_edit = QLineEdit(
            (self.store.get_setting("focus_away_minutes", "5") or "5"))
        self.focus_minutes_edit.setFixedWidth(64)
        self.focus_minutes_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        minutes_row.addWidget(self.focus_minutes_edit)
        minutes_unit = QLabel("分钟")
        minutes_unit.setObjectName("Tiny")
        minutes_row.addWidget(minutes_unit)
        minutes_row.addStretch(1)
        card.body.addLayout(minutes_row)

        whitelist_label = QLabel("白名单（每行一个：进程名或窗口标题关键词）")
        whitelist_label.setObjectName("Small")
        card.body.addWidget(whitelist_label)
        self.focus_whitelist_edit = QPlainTextEdit(
            self.store.get_setting("focus_whitelist", "") or "")
        self.focus_whitelist_edit.setPlaceholderText(
            "chrome.exe\ncanvas.fudan.edu.cn\n腾讯会议")
        self.focus_whitelist_edit.setFixedHeight(78)
        card.body.addWidget(self.focus_whitelist_edit)
        return card

    def _scan_wifi(self) -> None:
        from . import wifi_scan

        result = wifi_scan.scan_summary()
        if result.get("ok"):
            self.loc_precision_status.setText(
                f"扫到 {result.get('count', 0)} 个 Wi-Fi，可以点「以当前位置校准」记录这个位置")
        else:
            self.loc_precision_status.setText(
                result.get("error") or "扫描 Wi-Fi 失败")

    def _open_location_settings(self) -> None:
        from . import positioning

        positioning.open_settings()

    def _calibrate_location(self) -> None:
        from . import geo, location_win, positioning

        name, ok = QInputDialog.getText(
            self, "校准当前位置",
            "给这个位置起个名字（例如：光华楼 H3401 门口）：")
        if not ok or not (name or "").strip():
            return
        lat = lon = None
        win = location_win.position(timeout=18.0)
        if win.get("ok"):
            lat, lon = win.get("lat"), win.get("lon")
        else:
            place = geo.place_by_id(self.location_place_select.currentData() or "")
            if place:
                lat, lon = place["lat"], place["lon"]
        if lat is None or lon is None:
            self.loc_precision_status.setText(
                win.get("error") or "拿不到坐标：先打开定位服务，或在上面选一个常用地点")
            return
        result = positioning.calibrate(self.store, name.strip(), float(lat), float(lon))
        if result.get("ok"):
            self.loc_precision_status.setText(
                f"已校准「{result.get('name')}」，记录了 {result.get('prints')} 个 AP")
            self._refresh_location_points()
        else:
            self.loc_precision_status.setText(result.get("error") or "校准失败")

    def _delete_location_point(self, point_id: int) -> None:
        from . import positioning

        positioning.delete_point(self.store, int(point_id))
        self._refresh_location_points()
        self.loc_precision_status.setText("已删除这个校准点")

    def _refresh_location_points(self) -> None:
        from . import positioning

        layout = getattr(self, "loc_points_layout", None)
        if layout is None:
            return
        clear_layout(layout)
        try:
            items = positioning.points(self.store)
        except Exception:
            items = []
        if not items:
            hint = QLabel("还没有校准点：打开定位服务后，站在要记的地方点「以当前位置校准」")
            hint.setObjectName("Tiny")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            return
        for item in items[:12]:
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            label = QLabel(f"{item.get('name')} · {item.get('aps', 0)} 个 AP")
            label.setObjectName("Small")
            row.addWidget(label, 1)
            remove = SoftButton("删除", "ghost", height=26, min_width=58, point_size=8.4)
            remove.clicked.connect(
                lambda _checked=False, pid=int(item["id"]): self._delete_location_point(pid))
            row.addWidget(remove)
            layout.addWidget(holder)

    # -------------------------------------------------- 本地模型
    def _build_llm_card(self) -> Card:
        from . import llm_local

        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.INFO, "AI 接口",
            "用本地模型，或者填 DeepSeek / 豆包 / Kimi 的 API Key。"
            "AI 面板的「对话」和 「Agent」都用这里的配置",
        ))

        cfg = llm_local.settings(self.store)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(10)
        preset_label = QLabel("接口服务")
        preset_label.setObjectName("Small")
        preset_row.addWidget(preset_label)
        self.llm_preset_select = Select()
        self.llm_preset_select.setFixedWidth(220)
        for item in LLM_PRESETS:
            self.llm_preset_select.addItem(item["name"], item["id"])
        preset_id = self.store.get_setting("llm_preset", "") or self._guess_llm_preset(
            cfg.get("llm_base_url", ""), self.store.get_setting("llm_model_name", ""))
        self.llm_preset_select.blockSignals(True)
        index = self.llm_preset_select.findData(preset_id)
        self.llm_preset_select.setCurrentIndex(max(0, index))
        self.llm_preset_select.blockSignals(False)
        self.llm_preset_select.currentIndexChanged.connect(self._on_llm_preset_changed)
        preset_row.addWidget(self.llm_preset_select)
        self.llm_preset_hint = QLabel("")
        self.llm_preset_hint.setObjectName("Tiny")
        preset_row.addWidget(self.llm_preset_hint, 1)
        card.body.addLayout(preset_row)

        # ---- 多套 API 配置：接口地址 + 模型 + Key 可以存好几套，随时切换 ----
        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)
        profile_label = QLabel("API 配置")
        profile_label.setObjectName("Small")
        profile_row.addWidget(profile_label)
        self.llm_profile_select = Select()
        self.llm_profile_select.setFixedWidth(200)
        self.llm_profile_select.setToolTip(
            "同一个程序里可以存多套接口（比如「内网 Qwen」「DeepSeek 官方」），"
            "选了哪个，AI 面板的对话和 Agent 就用哪个")
        profile_row.addWidget(self.llm_profile_select)
        self.llm_profile_name_edit = QLineEdit()
        self.llm_profile_name_edit.setFixedWidth(150)
        self.llm_profile_name_edit.setPlaceholderText("这套配置的名字")
        self.llm_profile_name_edit.setToolTip("改完点下面的「保存设置」就会记住")
        profile_row.addWidget(self.llm_profile_name_edit)
        self.llm_profile_new_button = SoftButton("另存为新配置", "ghost", height=30,
                                                min_width=118, point_size=8.6)
        self.llm_profile_new_button.setToolTip(
            "把下面填的地址 / 模型 / Key 存成一套新的配置，同时切换到它")
        self.llm_profile_new_button.clicked.connect(self._save_profile_as_new)
        profile_row.addWidget(self.llm_profile_new_button)
        self.llm_profile_delete_button = SoftButton("删除这套", "ghost", height=30,
                                                    min_width=92, point_size=8.6)
        self.llm_profile_delete_button.setToolTip("删掉当前这套配置（最后一套不能删）")
        self.llm_profile_delete_button.clicked.connect(self._delete_llm_profile)
        profile_row.addWidget(self.llm_profile_delete_button)
        profile_row.addStretch(1)
        card.body.addLayout(profile_row)
        self._reload_llm_profiles()

        runtime_row = QHBoxLayout()
        runtime_row.setSpacing(10)
        runtime_label = QLabel("运行时")
        runtime_label.setObjectName("Small")
        runtime_row.addWidget(runtime_label)
        self.llm_runtime_select = Select()
        self.llm_runtime_select.setFixedWidth(330)
        for item in llm_local.RUNTIMES:
            self.llm_runtime_select.addItem(item["name"], item["id"])
        index = self.llm_runtime_select.findData(cfg.get("llm_runtime", "llamacpp"))
        self.llm_runtime_select.setCurrentIndex(max(0, index))
        runtime_row.addWidget(self.llm_runtime_select)
        runtime_row.addStretch(1)
        card.body.addLayout(runtime_row)

        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        model_label = QLabel("模型路径")
        model_label.setObjectName("Small")
        model_row.addWidget(model_label)
        self.llm_model_edit = QLineEdit(cfg.get("llm_model_path", ""))
        self.llm_model_edit.setPlaceholderText(
            "llama.cpp：xxx.gguf 文件；SGLang：模型目录")
        model_row.addWidget(self.llm_model_edit, 1)
        pick_model = SoftButton("选择…", "ghost", height=30, min_width=78, point_size=8.6)
        pick_model.clicked.connect(self._pick_llm_model)
        model_row.addWidget(pick_model)
        card.body.addLayout(model_row)

        bin_row = QHBoxLayout()
        bin_row.setSpacing(8)
        bin_label = QLabel("运行时目录")
        bin_label.setObjectName("Small")
        bin_row.addWidget(bin_label)
        self.llm_bin_edit = QLineEdit(cfg.get("llm_bin_dir", ""))
        self.llm_bin_edit.setPlaceholderText("llama-server.exe 所在目录（留空则用 PATH）")
        bin_row.addWidget(self.llm_bin_edit, 1)
        pick_bin = SoftButton("选择…", "ghost", height=30, min_width=78, point_size=8.6)
        pick_bin.clicked.connect(self._pick_llm_bin_dir)
        bin_row.addWidget(pick_bin)
        card.body.addLayout(bin_row)

        params_row = QHBoxLayout()
        params_row.setSpacing(8)
        for label_text, attr, placeholder in (
            ("端口", "llm_port_edit", "8080"),
            ("上下文", "llm_ctx_edit", "8192"),
            ("GPU 层数", "llm_gpu_edit", "0"),
        ):
            label = QLabel(label_text)
            label.setObjectName("Small")
            params_row.addWidget(label)
            edit = QLineEdit()
            edit.setFixedWidth(84)
            edit.setPlaceholderText(placeholder)
            setattr(self, attr, edit)
            params_row.addWidget(edit)
        params_row.addStretch(1)
        card.body.addLayout(params_row)
        self.llm_port_edit.setText(cfg.get("llm_port", "8080"))
        self.llm_ctx_edit.setText(cfg.get("llm_ctx", "8192"))
        self.llm_gpu_edit.setText(cfg.get("llm_gpu_layers", "0"))

        extra_row = QHBoxLayout()
        extra_row.setSpacing(8)
        extra_label = QLabel("额外参数")
        extra_label.setObjectName("Small")
        extra_row.addWidget(extra_label)
        self.llm_extra_edit = QLineEdit(cfg.get("llm_extra_args", ""))
        self.llm_extra_edit.setPlaceholderText("例如 --threads 8（可留空）")
        extra_row.addWidget(self.llm_extra_edit, 1)
        card.body.addLayout(extra_row)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        url_label = QLabel("接口地址")
        url_label.setObjectName("Small")
        url_row.addWidget(url_label)
        self.llm_base_edit = QLineEdit(
            cfg.get("llm_base_url", "http://127.0.0.1:8080/v1"))
        self.llm_base_edit.setPlaceholderText("http://127.0.0.1:8080/v1")
        url_row.addWidget(self.llm_base_edit, 1)
        card.body.addLayout(url_row)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        key_label = QLabel("模型名")
        key_label.setObjectName("Small")
        key_row.addWidget(key_label)
        self.llm_model_name_edit = QLineEdit(
            self.store.get_setting("llm_model_name", ""))
        self.llm_model_name_edit.setPlaceholderText(
            "本地模型可留空；DeepSeek API 填 deepseek-chat")
        key_row.addWidget(self.llm_model_name_edit, 1)
        api_label = QLabel("API Key")
        api_label.setObjectName("Small")
        key_row.addWidget(api_label)
        has_api_key = self._has_llm_key()
        self.llm_api_edit = QLineEdit()
        self.llm_api_edit.setPlaceholderText(
            "已保存（不可查看），留空表示不修改" if has_api_key
            else "这套还没填 Key（本地模型可留空）")
        self.llm_api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_api_edit.setFixedWidth(220)
        key_row.addWidget(self.llm_api_edit)
        card.body.addLayout(key_row)

        # 思考模式：真的关掉思考（不是把思考过程藏起来）
        think_row = QHBoxLayout()
        think_row.setSpacing(8)
        think_label = QLabel("思考")
        think_label.setObjectName("Small")
        think_row.addWidget(think_label)
        self.llm_think_select = Select()
        self.llm_think_select.setFixedWidth(168)
        self.llm_think_select.setToolTip(
            "「关闭思考」是在请求里就让模型别思考（按服务自动挑对参数），"
            "不是把思考过程藏起来；遇到关不掉的推理模型会提示你换模型")
        try:
            from . import ai_thinking

            for mode_id in ai_thinking.MODES:
                self.llm_think_select.addItem(
                    ai_thinking.MODE_LABELS.get(mode_id, mode_id), mode_id)
            current = ai_thinking.mode(self.store)
            index = self.llm_think_select.findData(current)
            if index >= 0:
                self.llm_think_select.setCurrentIndex(index)
            self.llm_think_select.currentIndexChanged.connect(
                self._on_llm_think_changed)
            self.llm_think_hint = QLabel(self._llm_think_hint_text())
        except Exception:
            self.llm_think_hint = QLabel("")
        self.llm_think_hint.setObjectName("Tiny")
        self.llm_think_hint.setWordWrap(True)
        think_row.addWidget(self.llm_think_select)
        think_row.addWidget(self.llm_think_hint, 1)
        card.body.addLayout(think_row)

        # ---- 本地资源（沙盒）：AI 能不能读写这台机器上的文件 ----
        local_head = QHBoxLayout()
        local_head.setSpacing(8)
        local_label = QLabel("本地资源")
        local_label.setObjectName("Small")
        local_head.addWidget(local_label)
        self.local_mode_select = Select()
        self.local_mode_select.setFixedWidth(210)
        self.local_mode_select.setToolTip(
            "关闭：AI 完全碰不到本地文件\n"
            "只读：可以列目录、读文件、按内容搜\n"
            "读写：还能新建/覆盖文件（仍然只在下面这些目录里）")
        try:
            from . import ai_local

            for mode_id in ai_local.MODES:
                self.local_mode_select.addItem(
                    ai_local.MODE_LABELS.get(mode_id, mode_id), mode_id)
            index = self.local_mode_select.findData(ai_local.mode(self.store))
            if index >= 0:
                self.local_mode_select.setCurrentIndex(index)
            self.local_mode_select.currentIndexChanged.connect(
                self._on_local_mode_changed)
        except Exception:
            pass
        local_head.addWidget(self.local_mode_select)
        self.local_open_button = SoftButton("打开工作区", "ghost", height=30,
                                            min_width=104, point_size=8.6)
        self.local_open_button.setToolTip("在文件管理器里打开 AI 的工作区目录")
        self.local_open_button.clicked.connect(self._open_agent_workspace)
        local_head.addWidget(self.local_open_button)
        self.local_add_button = SoftButton("添加目录…", "ghost", height=30,
                                           min_width=104, point_size=8.6)
        self.local_add_button.setToolTip("把别的文件夹也允许给 AI 用（比如某个项目目录）")
        self.local_add_button.clicked.connect(self._add_local_root)
        local_head.addWidget(self.local_add_button)
        self.local_log_button = SoftButton("操作记录", "ghost", height=30,
                                           min_width=96, point_size=8.6)
        self.local_log_button.setToolTip("看看 AI 到底碰过哪些文件（每次都有记录）")
        self.local_log_button.clicked.connect(self._show_local_audit)
        local_head.addWidget(self.local_log_button)
        local_head.addStretch(1)
        card.body.addLayout(local_head)
        self.local_roots_label = QLabel(self._local_roots_text())
        self.local_roots_label.setObjectName("Tiny")
        self.local_roots_label.setWordWrap(True)
        card.body.addWidget(self.local_roots_label)

        self.local_direct_button = SoftButton("直接访问设置…", "ghost", height=30,
                                              min_width=124, point_size=8.6)
        self.local_direct_button.setToolTip(
            "白名单里的目录默认「每次操作要你确认」；在这里勾上的目录可以免确认"
            "（理解成「本次工作的本地源」）")
        self.local_direct_button.clicked.connect(self._edit_local_direct)
        direct_row = QHBoxLayout()
        direct_row.addWidget(self.local_direct_button)
        direct_row.addStretch(1)
        card.body.addLayout(direct_row)

        # ---- 工具审核：每个 skill/tool 都能被用户单独关掉 ----
        audit_head = QLabel("工具审核")
        audit_head.setStyleSheet(
            f"color: {TEXT}; font-size: 10pt; font-weight: 600; margin-top: 6px;")
        card.body.addWidget(audit_head)
        audit_desc = QLabel(
            "下面是 AI 能用的全部工具。低风险默认开，中高风险默认关；"
            "关掉之后模型连这个工具都不会看到。改动立刻生效。")
        audit_desc.setObjectName("Tiny")
        audit_desc.setWordWrap(True)
        card.body.addWidget(audit_desc)
        self.tool_switches: Dict[str, Any] = {}
        try:
            from . import ai_tools

            for item in ai_tools.catalog(self.store):
                row = QHBoxLayout()
                row.setSpacing(8)
                text_box = QVBoxLayout()
                text_box.setSpacing(1)
                name_label = QLabel(f"{item['label']}（{item['name']}）")
                name_label.setStyleSheet(
                    f"color: {TEXT}; font-size: 9.4pt; font-weight: 600;")
                desc_label = QLabel(item["desc"])
                desc_label.setObjectName("Tiny")
                desc_label.setWordWrap(True)
                text_box.addWidget(name_label)
                text_box.addWidget(desc_label)
                row.addLayout(text_box, 1)
                risk_label = QLabel(item["risk_label"])
                risk_label.setObjectName("Tiny")
                risk_label.setStyleSheet(
                    f"color: {WARNING if item['risk'] != 'low' else MUTED};")
                risk_label.setFixedWidth(64)
                row.addWidget(risk_label)
                switch = ToggleSwitch(bool(item["enabled"]))
                switch.toggled.connect(
                    lambda checked, name=item["name"]:
                    self._on_tool_toggled(name, checked))
                self.tool_switches[item["name"]] = switch
                row.addWidget(switch)
                card.body.addLayout(row)
        except Exception:
            pass

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        # 「配置 API」：把内置的 API 对话窗格放到中间（配了云端 Key 时这是主按钮）
        self.llm_api_button = SoftButton("配置 API 对话", "primary", height=32,
                                         min_width=136, point_size=8.8)
        self.llm_api_button.setToolTip("在中栏打开内置的 API 对话窗格（流式回答）")
        self.llm_api_button.clicked.connect(self._open_api_chat)
        action_row.addWidget(self.llm_api_button)
        self.llm_start_button = SoftButton("启动本地模型", "ghost", height=32,
                                           min_width=126, point_size=8.8)
        self.llm_start_button.clicked.connect(self._start_local_model)
        action_row.addWidget(self.llm_start_button)
        self.llm_stop_button = SoftButton("停止", "ghost", height=32, min_width=72,
                                          point_size=8.8)
        self.llm_stop_button.clicked.connect(self._stop_local_model)
        action_row.addWidget(self.llm_stop_button)
        self.llm_webui_button = SoftButton("打开 Web UI", "ghost", height=32,
                                           min_width=104, point_size=8.8)
        self.llm_webui_button.clicked.connect(self._open_local_webui)
        action_row.addWidget(self.llm_webui_button)
        self.llm_test_button = SoftButton("测试接口", "ghost", height=32,
                                          min_width=88, point_size=8.8)
        self.llm_test_button.setToolTip("按上面的地址和 Key 请求一次，确认能不能用")
        self.llm_test_button.clicked.connect(self._test_llm_connection)
        action_row.addWidget(self.llm_test_button)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        # 管理员模型接口：需要授权（每次最多 5 小时）
        grant_row = HScrollRow(height=40, spacing=8)
        self.grant_button = SoftButton("申请模型接口权限", "ghost", height=32,
                                       min_width=150, point_size=8.6)
        self.grant_button.setToolTip(
            "用管理员的模型接口（如 10.137.44.215）需要先发申请，"
            "管理员批准后把邮件里的授权粘回来")
        self.grant_button.clicked.connect(self._request_api_grant)
        grant_row.addWidget(self.grant_button)
        self.grant_paste_button = SoftButton("粘贴授权", "ghost", height=32,
                                             min_width=104, point_size=8.6)
        self.grant_paste_button.clicked.connect(self._paste_api_grant)
        grant_row.addWidget(self.grant_paste_button)
        self.grant_label = QLabel("")
        self.grant_label.setObjectName("Tiny")
        self.grant_label.setWordWrap(True)
        grant_row.addWidget(self.grant_label)
        grant_row.addStretch(1)
        card.body.addWidget(grant_row)

        self.llm_status = QLabel("")
        self.llm_status.setObjectName("Tiny")
        self.llm_status.setWordWrap(True)
        card.body.addWidget(self.llm_status)
        try:
            self._on_llm_preset_changed(0, apply_fields=False)
        except Exception:
            pass
        try:
            runner = getattr(self.window(), "llm_runner", None)
            if runner is not None:
                runner.status_changed.connect(
                    lambda text, tone="info": self.llm_status.setText(text))
        except Exception:
            pass
        return card

    def _llm_runner(self):
        return getattr(self.window(), "llm_runner", None)

    # -------------------------------------------------- 模型接口授权
    def _refresh_grant_label(self) -> None:
        from . import api_grant, users

        if not hasattr(self, "grant_label"):
            return
        try:
            base = (self.llm_base_edit.text() if hasattr(self, "llm_base_edit")
                    else self.store.get_setting("llm_base_url", "") or "")
        except Exception:
            base = ""
        if not api_grant.is_admin_endpoint(self.store, base):
            self.grant_label.setText("当前接口不需要授权（不是管理员的模型地址）")
            return
        try:
            admin = users.is_admin_now(self.store)
        except Exception:
            admin = False
        if admin:
            self.grant_label.setStyleSheet("")
            self.grant_label.setText(
                "管理员使用自己的模型接口，不需要申请、也没有 5 小时限制")
            for button in (self.grant_button, self.grant_paste_button):
                try:
                    button.setVisible(False)
                except Exception:
                    pass
            return
        for button in (self.grant_button, self.grant_paste_button):
            try:
                button.setVisible(True)
            except Exception:
                pass
        state = api_grant.grant_state(self.store, base)
        self.grant_label.setText(state["text"])

    def _request_api_grant(self) -> None:
        from . import api_grant

        try:
            result = api_grant.send_request(self.store, api_grant.GRANT_MAX_HOURS)
        except api_grant.GrantError as exc:
            self._set_grant_status(str(exc), DANGER)
            return
        if result.get("sent"):
            self._set_grant_status(
                f"申请已发到 {result['to']}，请等管理员批准（批准后把邮件里的授权粘回来）",
                SUCCESS)
        else:
            self._set_grant_status(
                f"{result.get('error')}；申请内容已复制到剪贴板，直接粘贴发送即可",
                WARNING)
            try:
                QApplication.clipboard().setText(str(result.get("body") or ""))
            except Exception:
                pass

    def _paste_api_grant(self) -> None:
        from . import adminsign, api_grant

        text, ok = QInputDialog.getMultiLineText(
            self, "粘贴模型接口授权", "把邮件里以 LUMINA-GRANT1. 开头那一行粘进来：")
        if not ok or not text.strip():
            return
        token = api_grant.extract_grant(text) or text.strip()
        pub = adminsign.public_pem(self.store)
        try:
            payload = api_grant.save_grant(self.store, token, pub)
        except api_grant.GrantError as exc:
            self._set_grant_status(str(exc), DANGER)
            return
        self._set_grant_status(
            f"授权已生效：{payload.get('hours', '?')} 小时，到 "
            f"{payload.get('expires_at')} 为止", SUCCESS)
        self._refresh_grant_label()

    def _set_grant_status(self, text: str, color: str) -> None:
        self.grant_label.setStyleSheet(f"color: {color};")
        self.grant_label.setText(text)

    # -------------------------------------------------- AI 接口预设
    @staticmethod
    def _guess_llm_preset(base_url: str, model_name: str = "") -> str:
        url = (base_url or "").lower()
        name = (model_name or "").lower()
        for item in LLM_PRESETS:
            if item["id"] == "custom" or not item["base"]:
                continue
            if item["base"].lower() != url:
                continue
            if not item["model"] or not name or item["model"].lower() == name:
                return item["id"]
        if "deepseek" in url:
            return "deepseek"
        if "volces" in url or "ark." in url:
            return "doubao"
        if "moonshot" in url:
            return "kimi"
        if "127.0.0.1" in url or "localhost" in url:
            return "local"
        return "custom"

    def _on_llm_preset_changed(self, _index: int, apply_fields: bool = True) -> None:
        preset_id = self.llm_preset_select.currentData() or "custom"
        preset = next((item for item in LLM_PRESETS if item["id"] == preset_id), None)
        if preset is None or preset_id == "custom":
            self.llm_preset_hint.setText("自己填地址和模型名")
            return
        base = getattr(self, "llm_base_edit", None)
        model = getattr(self, "llm_model_name_edit", None)
        if apply_fields and base is not None and preset["base"]:
            base.setText(preset["base"])
        if apply_fields and model is not None:
            model.setText(preset["model"])
            model.setPlaceholderText(
                "填模型名" if preset_id != "local" else "本地模型可留空")
        tips = {
            "local": "先填模型路径并「启动本地模型」",
            "deepseek": "API Key 在 platform.deepseek.com 申请",
            "deepseek-r1": "R1 才会返回「思考过程」，比 chat 慢一点，适合推理题",
            "doubao": "模型名填方舟里的接入点 ID（ep-…）",
            "kimi": "API Key 在 platform.moonshot.cn 申请",
        }
        self.llm_preset_hint.setText(tips.get(preset_id, ""))

        # 云端接口：主按钮变成「配置 API 对话」，本地那套按钮收起来
        is_local = preset_id == "local"
        try:
            self.llm_start_button.setVisible(is_local)
            self.llm_stop_button.setVisible(is_local)
            self.llm_webui_button.setVisible(is_local)
            self.llm_api_button.setKind("ghost" if is_local else "primary")
            self.llm_api_button.setText(
                "配置 API 对话" if not is_local else "API 对话（用接口）")
            if is_local:
                hint = "本地模型模式：先填模型路径，再点「启动本地模型」"
            else:
                hint = ("云端接口模式：点「测试接口」确认能通，"
                        "再点「配置 API 对话」在中栏聊天")
            if apply_fields or not self.llm_status.text().strip():
                self.llm_status.setText(hint)
            self._llm_mode_hint = hint
        except Exception:
            pass

    def _llm_think_hint_text(self) -> str:
        """思考模式下面那句说明：会按当前服务和模型解释「关得掉吗」。"""
        try:
            from . import ai_thinking

            model = (self.store.get_setting("llm_model_name", "") or "").strip()
            base = (self.store.get_setting("llm_base_url", "") or "").strip()
            note = ai_thinking.note(self.store, model, base)
            if note:
                return note
            return {
                "off": "下次请求就不让模型思考（更快、更省 token）",
                "on": "要求模型先思考（有的模型默认不思考）",
                "auto": "不额外传参数，让模型自己决定",
            }.get(ai_thinking.mode(self.store), "")
        except Exception:
            return ""

    def _on_llm_think_changed(self, _index: int) -> None:
        try:
            from . import ai_thinking

            mode_id = self.llm_think_select.currentData() or "auto"
            ai_thinking.set_mode(self.store, str(mode_id))
            hint = self._llm_think_hint_text()
            self.llm_think_hint.setText(hint)
            if str(mode_id) == "off":
                self.notify.emit(hint or "已关闭思考：请求里就不让模型思考了")
            elif str(mode_id) == "on":
                self.notify.emit(hint or "已要求模型思考")
            else:
                self.notify.emit("思考交回模型自己决定")
        except Exception:
            pass

    def _local_roots_text(self) -> str:
        """本地资源下面那行小字：现在允许哪些目录。"""
        try:
            from . import ai_local

            info = ai_local.summary(self.store)
            if not info["enabled"]:
                return ("现在是「关闭」档：AI 不会读写任何本地文件。"
                        "要让它能看某个目录，把上面切成「只读」或「读写」。")
            roots = "；".join(info["roots"])
            extra = "" if info["can_write"] else "（只读档：不能改文件）"
            return f"允许的目录：{roots}{extra}"
        except Exception:
            return ""

    def _refresh_local_roots_label(self) -> None:
        label = getattr(self, "local_roots_label", None)
        if label is not None:
            label.setText(self._local_roots_text())

    def _on_local_mode_changed(self, _index: int) -> None:
        try:
            from . import ai_local

            mode_id = self.local_mode_select.currentData() or "off"
            ai_local.set_mode(self.store, str(mode_id))
            self._refresh_local_roots_label()
            self.notify.emit({
                "off": "本地资源已关闭：AI 不再读写本地文件",
                "read": "本地资源已设为「只读」：AI 只能看，不能改",
                "write": "本地资源已设为「读写」：AI 可以在允许的目录里新建/修改文件",
            }.get(str(mode_id), "本地资源设置已更新"))
        except Exception:
            pass

    def _open_agent_workspace(self) -> None:
        try:
            from . import ai_local

            path = ai_local.workspace(self.store)
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            open_external(str(path), local=True)
            self.notify.emit(f"已打开工作区：{path}")
        except Exception as exc:  # noqa: BLE001
            self.notify.emit(f"打不开工作区：{exc}")

    def _add_local_root(self) -> None:
        try:
            from . import ai_local

            folder = QFileDialog.getExistingDirectory(
                self, "选择允许 AI 访问的目录", str(Path.home()))
            if not folder:
                return
            roots = ai_local.add_root(self.store, folder)
            self._refresh_local_roots_label()
            if ai_local.mode(self.store) == "off":
                index = self.local_mode_select.findData("read")
                if index >= 0:
                    self.local_mode_select.setCurrentIndex(index)
            self.notify.emit(f"已加入允许目录（共 {len(roots)} 个）：{folder}")
        except Exception as exc:  # noqa: BLE001
            self.notify.emit(f"添加目录失败：{exc}")

    def _show_local_audit(self) -> None:
        """把最近的文件操作记录显示出来（谁在什么时候动了哪个文件）。"""
        try:
            from . import ai_local

            lines = ai_local.audit_lines(self.store, limit=60)
            if not lines:
                self.notify.emit("还没有任何本地文件操作记录")
                return
            action_names = {"list": "列目录", "read": "读", "write": "写",
                            "search": "搜索"}
            text = "\n".join(
                f"{item.get('at','')}  {action_names.get(item.get('action'), item.get('action'))}"
                f"  {item.get('path','')}"
                + ("" if item.get("ok") else f"  ✗ {item.get('note','')}")
                for item in lines[-30:])
            from PySide6.QtWidgets import QDialog

            dialog = QDialog(self)
            dialog.setWindowTitle("AI 的本地文件操作记录")
            layout = QVBoxLayout(dialog)
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setPlainText(text)
            view.setMinimumSize(640, 420)
            layout.addWidget(view)
            close = SoftButton("关闭", "ghost", height=32, min_width=88)
            close.clicked.connect(dialog.accept)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(close)
            layout.addLayout(row)
            dialog.exec()
        except Exception as exc:  # noqa: BLE001
            self.notify.emit(f"看不了记录：{exc}")

    def _on_tool_toggled(self, name: str, checked: bool) -> None:
        """工具审核：单个工具开关（立刻生效，模型那边下一轮就看不到它了）。"""
        try:
            from . import ai_tools

            ai_tools.set_tool_enabled(self.store, name, bool(checked))
            label = ai_tools.LABELS.get(name, name)
            self.notify.emit(f"「{label}」已{'打开' if checked else '关掉'}")
        except Exception as exc:  # noqa: BLE001
            self.notify.emit(f"改工具开关失败：{exc}")

    def _edit_local_direct(self) -> None:
        """哪些目录可以「直接访问」（工作区外的目录默认每次都要确认）。"""
        try:
            from . import ai_local

            roots = ai_local.roots(self.store)
            if not roots:
                self.notify.emit("还没有允许的目录，先点「添加目录…」")
                return
            from PySide6.QtWidgets import QDialog

            dialog = QDialog(self)
            dialog.setWindowTitle("哪些目录可以直接访问？")
            layout = QVBoxLayout(dialog)
            hint = QLabel("勾上的目录：AI 读写不用问你。没勾的：每次操作都会弹窗确认。"
                          "内置工作区默认就是直接访问。")
            hint.setWordWrap(True)
            hint.setObjectName("Tiny")
            layout.addWidget(hint)
            switches: List[Any] = []
            for root in roots:
                row = QHBoxLayout()
                row.setSpacing(8)
                is_workspace = str(root) == str(ai_local.workspace(self.store))
                label = QLabel(str(root) + ("（内置工作区）" if is_workspace else ""))
                label.setWordWrap(True)
                row.addWidget(label, 1)
                switch = ToggleSwitch(bool(ai_local.is_direct(self.store, root)))
                if is_workspace:
                    switch.setEnabled(False)
                row.addWidget(switch)
                switches.append((root, switch))
                layout.addLayout(row)
            buttons = QHBoxLayout()
            buttons.addStretch(1)
            cancel = SoftButton("取消", "ghost", height=32, min_width=80)
            save = SoftButton("保存", "primary", height=32, min_width=88)
            cancel.clicked.connect(dialog.reject)

            def save_choices() -> None:
                for root, switch in switches:
                    try:
                        ai_local.set_direct(self.store, str(root),
                                            bool(switch.isChecked()))
                    except Exception:
                        continue
                self._refresh_local_roots_label()
                dialog.accept()
                self.notify.emit("「直接访问」设置已保存（没勾的目录以后每次都会问你）")

            save.clicked.connect(save_choices)
            buttons.addWidget(cancel)
            buttons.addWidget(save)
            layout.addLayout(buttons)
            dialog.exec()
        except Exception as exc:  # noqa: BLE001
            self.notify.emit(f"设置直接访问失败：{exc}")

    def _open_api_chat(self) -> None:
        """在中栏打开内置的 API 对话窗格。"""
        window = self.window()
        handler = getattr(window, "show_ai_api", None)
        if callable(handler):
            try:
                handler()
                return
            except Exception:
                pass
        self.notify.emit("打开 AI 面板失败：窗口还不支持 API 对话")

    def _test_llm_connection(self) -> None:
        if not hasattr(self, "_llm_tester"):
            from .ai_chat import LlmTester

            self._llm_tester = LlmTester(self.store, self)
            self._llm_tester.finished.connect(self._on_llm_test_done)
        if self._llm_tester.is_busy():
            return
        self._save_llm_settings()  # 先存，测试用的就是界面上的地址和 Key
        self.llm_test_button.setEnabled(False)
        self.llm_status.setText("正在测试接口…")
        self._llm_tester.test()

    def _on_llm_test_done(self, ok: bool, message: str) -> None:
        try:
            self.llm_test_button.setEnabled(True)
            self.llm_status.setText(("✓ " if ok else "✗ ") + message)
        except Exception:
            pass

    def _collect_llm_settings(self) -> Dict[str, str]:
        preset = getattr(self, "llm_preset_select", None)
        return {
            "llm_preset": (preset.currentData() if preset is not None else "") or "",
            "llm_runtime": self.llm_runtime_select.currentData() or "llamacpp",
            "llm_model_path": self.llm_model_edit.text().strip(),
            "llm_bin_dir": self.llm_bin_edit.text().strip(),
            "llm_port": self.llm_port_edit.text().strip() or "8080",
            "llm_ctx": self.llm_ctx_edit.text().strip() or "8192",
            "llm_gpu_layers": self.llm_gpu_edit.text().strip() or "0",
            "llm_extra_args": self.llm_extra_edit.text().strip(),
            "llm_base_url": self.llm_base_edit.text().strip() or "http://127.0.0.1:8080/v1",
            "llm_model_name": self.llm_model_name_edit.text().strip(),
        }

    def _reload_llm_profiles(self) -> None:
        """把库里的配置列表填进下拉（保持当前选中）。"""
        from . import llm_profiles

        box = getattr(self, "llm_profile_select", None)
        if box is None:
            return
        try:
            llm_profiles.migrate(self.store)
            items = llm_profiles.list_profiles(self.store)
            active = llm_profiles.active_id(self.store)
        except Exception:
            items, active = [], None
        box.blockSignals(True)
        box.clear()
        for item in items:
            box.addItem(str(item.get("label") or item.get("name") or "配置"),
                        int(item["id"]))
        if active is not None:
            index = box.findData(int(active))
            if index >= 0:
                box.setCurrentIndex(index)
        box.blockSignals(False)
        try:
            box.currentIndexChanged.connect(self._on_llm_profile_selected)
        except Exception:
            pass
        profile = None
        try:
            profile = llm_profiles.active(self.store)
        except Exception:
            profile = None
        if profile is not None:
            name_edit = getattr(self, "llm_profile_name_edit", None)
            if name_edit is not None:
                name_edit.setText(str(profile.get("name") or ""))

    def _has_llm_key(self) -> bool:
        """这一套配置到底存没存 Key：**先看当前配置**，再看老字段。

        以前只看老字段 `llm_api_key`，而设置页里换了「API 配置」之后老字段
        可能还没投影过来 —— 用户明明填了 Key，这里却显示「这套没填 Key」。
        """
        try:
            from . import llm_profiles

            pid = llm_profiles.active_id(self.store)
            if pid and llm_profiles.secret(self.store, pid):
                return True
        except Exception:
            pass
        try:
            return bool(self.store.get_secret("llm_api_key", ""))
        except Exception:
            return False

    def _refresh_key_placeholder(self) -> None:
        """按「这一套有没有 Key」更新输入框的灰字提示。"""
        edit = getattr(self, "llm_api_edit", None)
        if edit is None:
            return
        edit.setPlaceholderText(
            "已保存（不可查看），留空表示不修改" if self._has_llm_key()
            else "这套还没填 Key（本地模型可留空）")

    def _on_llm_profile_selected(self, _index: int) -> None:
        """设置页里换一套配置：切成当前，并把它的值填到下面的输入框。"""
        from . import llm_profiles

        box = getattr(self, "llm_profile_select", None)
        if box is None:
            return
        profile_id = box.currentData()
        if profile_id is None:
            return
        try:
            profile = llm_profiles.set_active(self.store, int(profile_id))
        except Exception as exc:  # noqa: BLE001
            self.notify.emit("切换配置失败：%s" % exc)
            return
        if profile is None:
            return
        for attr, value in (("llm_base_edit", profile.get("base_url")),
                            ("llm_model_name_edit", profile.get("model")),
                            ("llm_profile_name_edit", profile.get("name"))):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setText(str(value or ""))
        api_edit = getattr(self, "llm_api_edit", None)
        if api_edit is not None:
            has = bool(profile.get("has_key"))
            api_edit.clear()
            api_edit.setPlaceholderText(
                "已保存（不可查看），留空表示不修改" if has
                else "这套还没填 Key（本地模型可留空）")
        self.notify.emit("已切换到「%s」" % (profile.get("label") or profile.get("name")))

    def _save_profile_as_new(self) -> None:
        """把当前输入框里的地址 / 模型 / Key 存成一套新配置并切过去。"""
        from . import llm_profiles

        wanted = self.llm_profile_name_edit.text().strip()
        # 先保存界面上的值，但**别**把当前这套改名成新名字（那是给新配置用的）
        self._save_llm_settings(sync_name=False)
        try:
            profile = llm_profiles.active(self.store)
            name = wanted
            if not name or (profile is not None and name == str(profile.get("name"))):
                name = ""
            pid = llm_profiles.create_profile(
                self.store, name,
                self.llm_base_edit.text().strip(),
                self.llm_model_name_edit.text().strip(),
                self.llm_api_edit.text().strip() or
                (llm_profiles.secret(self.store, profile["id"]) if profile else ""),
                self.llm_preset_select.currentData() or "")
            llm_profiles.set_active(self.store, pid)
        except Exception as exc:  # noqa: BLE001
            self.notify.emit("另存失败：%s" % exc)
            return
        self._reload_llm_profiles()
        self.notify.emit("已存成新配置，并切换到它")

    def _delete_llm_profile(self) -> None:
        from . import llm_profiles
        from .widgets import ConfirmDialog

        box = getattr(self, "llm_profile_select", None)
        if box is None or box.currentData() is None:
            return
        pid = int(box.currentData())
        name = box.currentText()
        dialog = ConfirmDialog(
            self, title="删除 API 配置",
            message="删除「%s」？\n\n只删这套配置（地址 / 模型 / Key），"
                    "对话记录和日程都不受影响。" % name,
            confirm_text="删除", danger=True)
        if dialog.exec() != ConfirmDialog.DialogCode.Accepted:
            return
        try:
            ok = llm_profiles.delete_profile(self.store, pid)
        except Exception as exc:  # noqa: BLE001
            self.notify.emit("删除失败：%s" % exc)
            return
        if not ok:
            self.notify.emit("至少要保留一套配置，删不了最后一套")
            return
        self._reload_llm_profiles()
        profile = llm_profiles.active(self.store)
        if profile is not None:
            for attr, value in (("llm_base_edit", profile.get("base_url")),
                                ("llm_model_name_edit", profile.get("model"))):
                widget = getattr(self, attr, None)
                if widget is not None:
                    widget.setText(str(value or ""))
        self.notify.emit("已删除，当前用「%s」" % (box.currentText() or ""))

    def _save_llm_settings(self, sync_name: bool = True) -> None:
        try:
            values = self._collect_llm_settings()
            self.store.update_settings(values)
            key = self.llm_api_edit.text().strip()
            if key:
                self.store.set_secret("llm_api_key", key)
            # 面板上可能刚把「模型名 / 地址」改成别的：同步进当前这套配置
            from . import llm_profiles

            name_edit = getattr(self, "llm_profile_name_edit", None)
            name = name_edit.text().strip() if (sync_name and name_edit is not None) else ""
            llm_profiles.sync_from_legacy(self.store, name)
            if key:
                # 刚填的 Key：清掉输入框，灰字换成「已保存（不可查看）」
                try:
                    self.llm_api_edit.clear()
                except Exception:
                    pass
            self._refresh_key_placeholder()
        except Exception:
            pass

    def _pick_llm_model(self) -> None:
        runtime = self.llm_runtime_select.currentData() or "llamacpp"
        if runtime == "llamacpp":
            path, _ = QFileDialog.getOpenFileName(
                self, "选择 GGUF 模型", "", "GGUF 模型 (*.gguf);;所有文件 (*.*)")
        else:
            path = QFileDialog.getExistingDirectory(self, "选择模型目录")
        if path:
            self.llm_model_edit.setText(path)
            self._save_llm_settings()

    def _pick_llm_bin_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 llama-server 所在目录")
        if path:
            self.llm_bin_edit.setText(path)
            self._save_llm_settings()

    def _start_local_model(self) -> None:
        runner = self._llm_runner()
        if runner is None:
            self.llm_status.setText("本地模型启动器不可用")
            return
        self._save_llm_settings()
        self.llm_status.setText("正在启动…")
        runner.start()

    def _stop_local_model(self) -> None:
        runner = self._llm_runner()
        if runner is not None:
            runner.stop()
        self.llm_status.setText("已停止")

    def _open_local_webui(self) -> None:
        base = (self.llm_base_edit.text().strip() or "http://127.0.0.1:8080/v1")
        url = base.replace("/v1", "").rstrip("/")
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            open_external(url)
        except Exception:
            pass

    # -------------------------------------------------- 提示词
    def _build_prompts_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.EDIT, "提示词",
            "所有提示词都存在 data/prompts.json 里（不写死在代码）；这里改完点保存就写进文件",
        ))

        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        pick_label = QLabel("要编辑")
        pick_label.setObjectName("Small")
        pick_row.addWidget(pick_label)
        self.prompt_select = Select()
        self.prompt_select.setMinimumWidth(240)
        for key, title in self._prompt_options():
            self.prompt_select.addItem(title, key)
        self.prompt_select.currentIndexChanged.connect(self._load_prompt_text)
        pick_row.addWidget(self.prompt_select)
        pick_row.addStretch(1)
        card.body.addLayout(pick_row)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setFixedHeight(124)
        card.body.addWidget(self.prompt_edit)

        prompt_actions = QHBoxLayout()
        prompt_actions.setSpacing(8)
        save_prompt = SoftButton("保存提示词", "primary", height=32, min_width=110,
                                 point_size=8.8)
        save_prompt.clicked.connect(self._save_prompt_text)
        prompt_actions.addWidget(save_prompt)
        reset_prompt = SoftButton("恢复默认", "ghost", height=32, min_width=92,
                                  point_size=8.8)
        reset_prompt.clicked.connect(self._reset_prompt_text)
        prompt_actions.addWidget(reset_prompt)
        open_file = SoftButton("打开配置文件", "ghost", height=32, min_width=112,
                               point_size=8.8)
        open_file.clicked.connect(self._open_prompts_file)
        prompt_actions.addWidget(open_file)
        prompt_actions.addStretch(1)
        card.body.addLayout(prompt_actions)

        self.prompt_status = QLabel("")
        self.prompt_status.setObjectName("Tiny")
        self.prompt_status.setWordWrap(True)
        card.body.addWidget(self.prompt_status)

        # ---------------- 每次发送前补「时间 + IP」
        ctx_row = QHBoxLayout()
        ctx_row.setSpacing(10)
        ctx_text = QVBoxLayout()
        ctx_text.setSpacing(2)
        ctx_label = QLabel("在系统提示词最前面加上「今天的日期 + 本机 IP」")
        ctx_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        ctx_desc = QLabel("每次发请求都现算（日期不会过期）；公网 IP 要联网查，默认不开")
        ctx_desc.setObjectName("Tiny")
        ctx_desc.setWordWrap(True)
        ctx_text.addWidget(ctx_label)
        ctx_text.addWidget(ctx_desc)
        ctx_row.addLayout(ctx_text, 1)
        self.prompt_context_switch = ToggleSwitch(
            (self.store.get_setting("prompt_prepend_context", "1") or "1") == "1")
        self.prompt_context_switch.toggled.connect(self._on_prompt_context_toggled)
        ctx_row.addWidget(self.prompt_context_switch)
        card.body.addLayout(ctx_row)

        public_row = QHBoxLayout()
        public_row.setSpacing(10)
        public_label = QLabel("顺便查公网 IP（需要联网，结果缓存 1 小时）")
        public_label.setObjectName("Tiny")
        public_row.addWidget(public_label, 1)
        self.prompt_public_switch = ToggleSwitch(
            (self.store.get_setting("prompt_public_ip", "0") or "0") == "1")
        self.prompt_public_switch.toggled.connect(self._on_prompt_public_toggled)
        public_row.addWidget(self.prompt_public_switch)
        card.body.addLayout(public_row)

        # ---------------- 高级：用程序生成提示词
        gen_row = QHBoxLayout()
        gen_row.setSpacing(10)
        gen_text = QVBoxLayout()
        gen_text.setSpacing(2)
        gen_label = QLabel("用程序现场生成提示词（高级）")
        gen_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        self.prompt_gen_desc = QLabel(
            "指定一个 .py / .exe / .bat：程序从 stdin 读到一行 JSON 上下文"
            "（时间、IP、主机名、模型……），把要追加的提示词写到 stdout。"
            "C / C++ 编成 exe 也能用；超时或报错不影响聊天。")
        self.prompt_gen_desc.setObjectName("Tiny")
        self.prompt_gen_desc.setWordWrap(True)
        gen_text.addWidget(gen_label)
        gen_text.addWidget(self.prompt_gen_desc)
        gen_row.addLayout(gen_text, 1)
        self.prompt_gen_switch = ToggleSwitch(
            (self.store.get_setting("prompt_gen_enabled", "0") or "0") == "1")
        self.prompt_gen_switch.toggled.connect(self._on_prompt_gen_toggled)
        gen_row.addWidget(self.prompt_gen_switch)
        card.body.addLayout(gen_row)

        gen_pick = HScrollRow(height=40, spacing=8)
        self.prompt_gen_path = QLineEdit()
        self.prompt_gen_path.setPlaceholderText("生成程序路径，例如 data/prompt_gen_example.py")
        self.prompt_gen_path.setText(self.store.get_setting("prompt_gen_command", "") or "")
        self.prompt_gen_path.setMinimumWidth(280)
        gen_pick.addWidget(self.prompt_gen_path)
        gen_browse = SoftButton("选择…", "ghost", height=32, min_width=76,
                                point_size=8.6)
        gen_browse.clicked.connect(self._pick_prompt_gen)
        gen_pick.addWidget(gen_browse)
        gen_save = SoftButton("保存", "primary", height=32, min_width=64,
                              point_size=8.6)
        gen_save.clicked.connect(self._save_prompt_gen)
        gen_pick.addWidget(gen_save)
        gen_test = SoftButton("试运行", "ghost", height=32, min_width=76,
                              point_size=8.6)
        gen_test.clicked.connect(self._test_prompt_gen)
        gen_pick.addWidget(gen_test)
        gen_sample = SoftButton("写个示例脚本", "ghost", height=32, min_width=112,
                                point_size=8.6)
        gen_sample.clicked.connect(self._write_prompt_gen_sample)
        gen_pick.addWidget(gen_sample)
        gen_pick.addStretch(1)
        card.body.addWidget(gen_pick)

        self.prompt_gen_status = QLabel("")
        self.prompt_gen_status.setObjectName("Tiny")
        self.prompt_gen_status.setWordWrap(True)
        card.body.addWidget(self.prompt_gen_status)
        self._refresh_prompt_gen_status()

        self._load_prompt_text()
        return card

    # -------------------------------------------------- 时间 / IP / 生成程序
    def _on_prompt_context_toggled(self, checked: bool) -> None:
        self.store.set_setting("prompt_prepend_context", "1" if checked else "0")
        self.prompt_status.setText(
            "已开启：每次发送都会在系统提示词最前面写上今天的日期和本机 IP"
            if checked else "已关闭：系统提示词里不再带日期与 IP")

    def _on_prompt_public_toggled(self, checked: bool) -> None:
        self.store.set_setting("prompt_public_ip", "1" if checked else "0")
        self.prompt_status.setText(
            "已开启：会顺带查一次公网 IP（缓存 1 小时）" if checked
            else "已关闭：只写本机局域网 IP")

    def _on_prompt_gen_toggled(self, checked: bool) -> None:
        self.store.set_setting("prompt_gen_enabled", "1" if checked else "0")
        self._refresh_prompt_gen_status()

    def _pick_prompt_gen(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择提示词生成程序", "",
            "程序 (*.py *.exe *.bat *.cmd);;Python (*.py);;所有文件 (*.*)")
        if path:
            self.prompt_gen_path.setText(path)
            self._save_prompt_gen()

    def _save_prompt_gen(self) -> None:
        from . import prompt_gen

        value = self.prompt_gen_path.text().strip()
        prompt_gen.set_command(self.store, value)
        self._refresh_prompt_gen_status()
        self.prompt_gen_status.setStyleSheet(f"color: {SUCCESS};")
        self.prompt_gen_status.setText("已保存生成程序：" + (value or "（空）"))

    def _test_prompt_gen(self) -> None:
        from . import prompt_gen

        prompt_gen.set_command(self.store, self.prompt_gen_path.text().strip())
        was_enabled = prompt_gen.enabled(self.store)
        prompt_gen.set_enabled(self.store, True)
        try:
            text, error = prompt_gen.generate(self.store, {"scene": "test"})
        finally:
            prompt_gen.set_enabled(self.store, was_enabled)
        if error:
            self.prompt_gen_status.setStyleSheet(f"color: {DANGER};")
            self.prompt_gen_status.setText(error)
            return
        self.prompt_gen_status.setStyleSheet(f"color: {SUCCESS};")
        preview = text if len(text) <= 300 else text[:300] + "…"
        self.prompt_gen_status.setText("生成成功，会追加到系统提示词里：\n" + preview)

    def _write_prompt_gen_sample(self) -> None:
        from . import prompt_gen

        try:
            path = prompt_gen.write_sample(self.store)
        except prompt_gen.PromptGenError as exc:
            self.prompt_gen_status.setStyleSheet(f"color: {DANGER};")
            self.prompt_gen_status.setText(str(exc))
            return
        self.prompt_gen_path.setText(str(path))
        prompt_gen.set_command(self.store, str(path))
        self.prompt_gen_status.setStyleSheet(f"color: {SUCCESS};")
        self.prompt_gen_status.setText(
            f"示例已写到 {path}；点「试运行」看看输出，满意就打开开关。")

    def _refresh_prompt_gen_status(self) -> None:
        from . import prompt_gen

        path = prompt_gen.command(self.store)
        if not prompt_gen.enabled(self.store):
            self.prompt_gen_status.setText("生成程序：未启用")
            return
        if not path:
            self.prompt_gen_status.setText("生成程序：已启用，但还没选程序")
            return
        last_error = self.store.get_setting("prompt_gen_last_error", "") or ""
        if last_error:
            self.prompt_gen_status.setStyleSheet(f"color: {WARNING};")
            self.prompt_gen_status.setText("上次生成有问题：" + last_error)
        else:
            self.prompt_gen_status.setText(f"生成程序：已启用（{path}）")

    @staticmethod
    def _prompt_options() -> List[tuple]:
        return [
            ("system:assistant", "系统提示词 · 通用助手"),
            ("system:weekly_report", "系统提示词 · 周报"),
            ("system:homework", "系统提示词 · 作业摘要"),
            ("system:latex", "系统提示词 · LaTeX"),
            ("scene:weekly_report", "场景 · 生成周报"),
            ("scene:homework_summary", "场景 · 作业摘要"),
        ]

    def _prompt_key(self) -> str:
        return self.prompt_select.currentData() or "system:assistant"

    def _load_prompt_text(self, _index: int = -1) -> None:
        from . import prompts

        key = self._prompt_key()
        data = prompts.load()
        kind, name = key.split(":", 1)
        if kind == "system":
            value = (data.get("system") or {}).get(name, "")
        else:
            value = ((data.get("scenes") or {}).get(name) or {}).get("prompt", "")
        self.prompt_edit.setPlainText(str(value))
        self.prompt_status.setText(f"当前编辑：{key} · 文件 {prompts.path()}")

    def _save_prompt_text(self) -> None:
        from . import prompts

        key = self._prompt_key()
        kind, name = key.split(":", 1)
        text = self.prompt_edit.toPlainText().strip()
        if kind == "system":
            ok = prompts.save({"system": {name: text}})
        else:
            data = prompts.load()
            scenes = dict(data.get("scenes") or {})
            item = dict(scenes.get(name) or {"title": name, "enabled": True})
            item["prompt"] = text
            scenes[name] = item
            ok = prompts.save({"scenes": scenes})
        self.prompt_status.setText(
            f"已保存到 {prompts.path()}" if ok else "保存失败，检查一下文件权限")

    def _reset_prompt_text(self) -> None:
        from . import prompts

        key = self._prompt_key()
        kind, name = key.split(":", 1)
        if kind == "system":
            self.prompt_edit.setPlainText(
                (prompts.DEFAULTS.get("system") or {}).get(name, ""))
        else:
            item = (prompts.DEFAULTS.get("scenes") or {}).get(name) or {}
            self.prompt_edit.setPlainText(item.get("prompt", ""))
        self.prompt_status.setText("已填回默认内容，点「保存提示词」才会生效")

    def _open_prompts_file(self) -> None:
        from . import prompts

        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            open_external(str(prompts.ensure()), local=True)
        except Exception:
            pass

    # -------------------------------------------------- 定位到某张卡片
    def scroll_to_vpn(self) -> None:
        widget = getattr(self, "proxy_switch", None)
        if widget is None:
            return
        try:
            self.settings_scroll.ensureWidgetVisible(widget, 0, 120)
        except Exception:
            pass

    def scroll_to_llm(self) -> None:
        """滚到「AI 接口」卡片（主窗口从 AI 面板跳过来用）。"""
        widget = getattr(self, "llm_preset_select", None)
        if widget is None:
            return
        try:
            self.settings_scroll.ensureWidgetVisible(widget, 0, 140)
        except Exception:
            pass

    # -------------------------------------------------- 统一身份认证
    def _build_uis_card(self) -> Card:
        from . import uis_login

        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.CONTACT, "统一身份认证（UIS）",
            "复旦换成了新版统一身份认证（id.fudan.edu.cn），后台直连登录已经不可用。"
            "现在保存的学号密码会**在内嵌网页的登录页里自动填好**，你点一下「登录」即可"
            "（有二次验证就手动过一次）。密码用 Windows DPAPI 加密保存，界面只能改、看不到原值",
        ))

        row = QHBoxLayout()
        row.setSpacing(8)
        user_label = QLabel("学号")
        user_label.setObjectName("Small")
        row.addWidget(user_label)
        self.uis_user_edit = QLineEdit(self.store.get_setting("uis_username", ""))
        self.uis_user_edit.setPlaceholderText("例如 22307110000")
        row.addWidget(self.uis_user_edit, 1)
        pass_label = QLabel("密码")
        pass_label.setObjectName("Small")
        row.addWidget(pass_label)
        has_password = bool(self.store.get_secret("uis_password", ""))
        self.uis_pass_edit = QLineEdit()
        self.uis_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.uis_pass_edit.setPlaceholderText(
            "已保存（不可查看），留空表示不修改" if has_password else "UIS 密码")
        self.uis_pass_edit.setFixedWidth(240)
        row.addWidget(self.uis_pass_edit)
        card.body.addLayout(row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        save_button = SoftButton("保存账号", "primary", height=32, min_width=104,
                                 point_size=8.8)
        save_button.clicked.connect(self._save_uis_credentials)
        actions.addWidget(save_button)
        test_button = SoftButton("检查自动填充", "ghost", height=32, min_width=112,
                                 point_size=8.8)
        test_button.setToolTip("检查账号密码和自动填充开关（真正的登录在网页里完成）")
        test_button.clicked.connect(self._test_uis_login)
        actions.addWidget(test_button)
        clear_button = SoftButton("清除密码", "ghost", height=32, min_width=96,
                                  point_size=8.8)
        clear_button.clicked.connect(self._clear_uis_password)
        actions.addWidget(clear_button)
        actions.addStretch(1)
        card.body.addLayout(actions)

        autofill_row = QHBoxLayout()
        autofill_row.setSpacing(12)
        autofill_text = QVBoxLayout()
        autofill_text.setSpacing(2)
        autofill_label = QLabel("网页自动填充账号密码")
        autofill_label.setStyleSheet(
            f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        autofill_desc = QLabel(
            "打开 eHall / 作业页时，如果跳到统一身份认证的登录页，"
            "会自动把学号和密码填进去，你只需要点「登录」")
        autofill_desc.setObjectName("Tiny")
        autofill_desc.setWordWrap(True)
        autofill_text.addWidget(autofill_label)
        autofill_text.addWidget(autofill_desc)
        autofill_row.addLayout(autofill_text, 1)
        self.uis_autofill_switch = ToggleSwitch(
            (self.store.get_setting("uis_autofill", "1") or "1") == "1")
        self.uis_autofill_switch.toggled.connect(self._on_uis_autofill_toggled)
        autofill_row.addWidget(self.uis_autofill_switch)
        card.body.addLayout(autofill_row)

        self.uis_status = QLabel(
            "账号密码已保存：打开 eHall / 作业页时会自动填好登录框"
            if has_password else
            "还没有配置（也可以继续在网页里手动登录）")
        self.uis_status.setObjectName("Tiny")
        self.uis_status.setWordWrap(True)
        card.body.addWidget(self.uis_status)
        return card

    def _on_uis_autofill_toggled(self, checked: bool) -> None:
        self.store.set_setting("uis_autofill", "1" if checked else "0")
        self.uis_status.setText(
            "已打开自动填充：遇到统一身份认证页会自动填账号密码"
            if checked else "已关闭自动填充：登录页需要自己输入")

    def _save_uis_credentials(self) -> None:
        from . import uis_login

        username = self.uis_user_edit.text().strip()
        password = self.uis_pass_edit.text()
        if not username:
            self.uis_status.setText("先填学号")
            return
        uis_login.save(self.store, username, password)
        self.uis_pass_edit.clear()
        has_password = bool(self.store.get_secret("uis_password", ""))
        self.uis_pass_edit.setPlaceholderText(
            "已保存（不可查看），留空表示不修改" if has_password else "UIS 密码")
        self.uis_status.setText(
            "已保存（密码加密存储，界面上看不到原值）" if has_password
            else "学号已保存，但还没有密码")

    def _clear_uis_password(self) -> None:
        from . import uis_login

        uis_login.clear_password(self.store)
        self.uis_pass_edit.clear()
        self.uis_pass_edit.setPlaceholderText("UIS 密码")
        self.uis_status.setText("已清除保存的 UIS 密码")

    def _test_uis_login(self) -> None:
        from . import uis_login

        if self.uis_pass_edit.text().strip():
            self._save_uis_credentials()
        try:
            result = uis_login.test(self.store)
        except Exception as exc:  # noqa: BLE001
            self.uis_status.setText(f"检查失败：{exc}")
            return
        if result.get("ok"):
            self.uis_status.setText(
                result.get("message") or result.get("error") or "账号密码已保存")
        else:
            self.uis_status.setText(f"还差一步：{result.get('error')}")

    # -------------------------------------------------- 收件箱（IMAP）
    def _build_imap_card(self) -> Card:
        from . import imap_inbox

        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.INBOX, "收件箱（IMAP）",
            "可以添加多个邮箱账户，收件箱里合并显示；密码同样加密保存、界面不可查看",
        ))

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.imap_name_edit = QLineEdit()
        self.imap_name_edit.setPlaceholderText("备注名，如 学校邮箱")
        self.imap_host_edit = QLineEdit()
        self.imap_host_edit.setPlaceholderText("imap.example.com")
        self.imap_port_edit = QLineEdit("993")
        self.imap_port_edit.setFixedWidth(80)
        self.imap_security_select = Select()
        for key, label in imap_inbox.SECURITY:
            self.imap_security_select.addItem(label, key)
        self.imap_user_edit = QLineEdit()
        self.imap_user_edit.setPlaceholderText("登录账号")
        self.imap_pass_edit = QLineEdit()
        self.imap_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.imap_pass_edit.setPlaceholderText("密码 / 授权码")

        grid.addWidget(FieldBlock("备注", self.imap_name_edit), 0, 0)
        grid.addWidget(FieldBlock("服务器", self.imap_host_edit, stretch=2), 0, 1)
        grid.addWidget(FieldBlock("端口", self.imap_port_edit), 0, 2)
        grid.addWidget(FieldBlock("加密", self.imap_security_select), 1, 0)
        grid.addWidget(FieldBlock("账号", self.imap_user_edit, stretch=2), 1, 1)
        grid.addWidget(FieldBlock("密码", self.imap_pass_edit), 1, 2)
        card.body.addLayout(grid)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        add_button = SoftButton("添加账户", "primary", height=32, min_width=104,
                                point_size=8.8)
        add_button.clicked.connect(self._add_imap_account)
        actions.addWidget(add_button)
        test_button = SoftButton("测试连接", "ghost", height=32, min_width=96,
                                 point_size=8.8)
        test_button.clicked.connect(self._test_imap_account)
        actions.addWidget(test_button)
        actions.addStretch(1)
        card.body.addLayout(actions)

        self.imap_status = QLabel("")
        self.imap_status.setObjectName("Tiny")
        self.imap_status.setWordWrap(True)
        card.body.addWidget(self.imap_status)

        self.imap_layout = QVBoxLayout()
        self.imap_layout.setSpacing(4)
        card.body.addLayout(self.imap_layout)
        self._refresh_imap_accounts()
        return card

    def _imap_form(self) -> Dict[str, object]:
        try:
            port = int(float(self.imap_port_edit.text().strip() or 993))
        except Exception:
            port = 993
        return {
            "name": self.imap_name_edit.text().strip(),
            "host": self.imap_host_edit.text().strip(),
            "port": port,
            "security": self.imap_security_select.currentData() or "ssl",
            "user": self.imap_user_edit.text().strip(),
            "password": self.imap_pass_edit.text(),
        }

    def _refresh_imap_accounts(self) -> None:
        from . import imap_inbox

        layout = getattr(self, "imap_layout", None)
        if layout is None:
            return
        clear_layout(layout)
        accounts = imap_inbox.list_accounts(self.store)
        if not accounts:
            hint = QLabel("还没有添加收件账户")
            hint.setObjectName("Tiny")
            layout.addWidget(hint)
            return
        for account in accounts:
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            has_password = bool(imap_inbox.password(self.store, int(account["id"])))
            label = QLabel("%s · %s%s" % (
                account.get("name") or account.get("host"),
                account.get("user") or "",
                "" if has_password else "（未保存密码）"))
            label.setObjectName("Small")
            row.addWidget(label, 1)
            remove = SoftButton("删除", "ghost", height=26, min_width=58,
                                point_size=8.4)
            remove.clicked.connect(
                lambda _c=False, aid=int(account["id"]): self._delete_imap_account(aid))
            row.addWidget(remove)
            layout.addWidget(holder)

    def _add_imap_account(self) -> None:
        from . import imap_inbox

        values = self._imap_form()
        if not values["host"] or not values["user"]:
            self.imap_status.setText("服务器和账号都要填")
            return
        account_id = imap_inbox.add_account(
            self.store, str(values["name"] or values["host"]), str(values["host"]),
            int(values["port"]), str(values["security"]), str(values["user"]),
            str(values["password"] or ""))
        self.imap_pass_edit.clear()
        self.imap_status.setText(f"已添加账户 #{account_id}")
        self._refresh_imap_accounts()

    def _test_imap_account(self) -> None:
        from . import imap_inbox

        values = self._imap_form()
        if not values["host"] or not values["user"]:
            self.imap_status.setText("服务器和账号都要填")
            return
        self.imap_status.setText("正在连接…")
        result = imap_inbox.test_account(
            {"host": values["host"], "port": values["port"],
             "security": values["security"], "user": values["user"]},
            str(values["password"] or ""))
        if result.get("ok"):
            self.imap_status.setText(
                f"连接成功，收件箱共 {result.get('total', 0)} 封")
        else:
            self.imap_status.setText(f"连接失败：{result.get('error')}")

    def _delete_imap_account(self, account_id: int) -> None:
        from . import imap_inbox

        imap_inbox.delete_account(self.store, int(account_id))
        self._refresh_imap_accounts()
        self.imap_status.setText("已删除该账户")

    # -------------------------------------------------- VPN / 代理
    def _build_vpn_card(self) -> Card:
        from . import vpn

        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.INFO, "VPN / 代理",
            "校内服务（eLearning、教务处）在家连不上时挂一个代理；"
            "复旦 SSL VPN 可以先在应用里打开登录，再把它的本地代理端口填到下面",
        ))

        cfg = vpn.settings(self.store)

        enable_row = QHBoxLayout()
        enable_row.setSpacing(12)
        enable_text = QVBoxLayout()
        enable_text.setSpacing(2)
        enable_label = QLabel("使用代理")
        enable_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        enable_desc = QLabel("开启后内嵌网页和后台抓取都会走这个代理")
        enable_desc.setObjectName("Tiny")
        enable_desc.setWordWrap(True)
        enable_text.addWidget(enable_label)
        enable_text.addWidget(enable_desc)
        enable_row.addLayout(enable_text, 1)
        self.proxy_switch = ToggleSwitch(bool(cfg.get("enabled")))
        enable_row.addWidget(self.proxy_switch)
        card.body.addLayout(enable_row)

        kind_row = QHBoxLayout()
        kind_row.setSpacing(8)
        kind_label = QLabel("类型")
        kind_label.setObjectName("Small")
        kind_row.addWidget(kind_label)
        self.proxy_kind_select = Select()
        self.proxy_kind_select.setFixedWidth(150)
        for kind_id, kind_name in vpn.PROXY_KINDS:
            self.proxy_kind_select.addItem(kind_name, kind_id)
        index = self.proxy_kind_select.findData(cfg.get("kind", "http"))
        self.proxy_kind_select.setCurrentIndex(max(0, index))
        kind_row.addWidget(self.proxy_kind_select)

        host_label = QLabel("地址")
        host_label.setObjectName("Small")
        kind_row.addWidget(host_label)
        self.proxy_host_edit = QLineEdit(cfg.get("host", ""))
        self.proxy_host_edit.setPlaceholderText("127.0.0.1")
        kind_row.addWidget(self.proxy_host_edit, 1)
        port_label = QLabel("端口")
        port_label.setObjectName("Small")
        kind_row.addWidget(port_label)
        self.proxy_port_edit = QLineEdit(str(cfg.get("port", 8080)))
        self.proxy_port_edit.setFixedWidth(80)
        kind_row.addWidget(self.proxy_port_edit)
        card.body.addLayout(kind_row)

        auth_row = QHBoxLayout()
        auth_row.setSpacing(8)
        user_label = QLabel("用户名")
        user_label.setObjectName("Small")
        auth_row.addWidget(user_label)
        self.proxy_user_edit = QLineEdit(cfg.get("user", ""))
        self.proxy_user_edit.setPlaceholderText("可选")
        auth_row.addWidget(self.proxy_user_edit, 1)
        pass_label = QLabel("密码")
        pass_label.setObjectName("Small")
        auth_row.addWidget(pass_label)
        self.proxy_pass_edit = QLineEdit()
        self.proxy_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        has_proxy_pass = bool(self.store.get_secret("proxy_password", ""))
        self.proxy_pass_edit.setPlaceholderText(
            "已保存，留空表示不修改" if has_proxy_pass else "可选")
        self.proxy_pass_edit.setFixedWidth(180)
        auth_row.addWidget(self.proxy_pass_edit)
        card.body.addLayout(auth_row)

        vpn_row = QHBoxLayout()
        vpn_row.setSpacing(8)
        vpn_label = QLabel("复旦 VPN 地址")
        vpn_label.setObjectName("Small")
        vpn_row.addWidget(vpn_label)
        self.vpn_url_edit = QLineEdit(cfg.get("vpn_url", vpn.DEFAULT_VPN_URL))
        vpn_row.addWidget(self.vpn_url_edit, 1)
        open_vpn = SoftButton("打开登录页", "ghost", height=30, min_width=104,
                              point_size=8.6)
        open_vpn.clicked.connect(self._open_vpn_page)
        vpn_row.addWidget(open_vpn)
        card.body.addLayout(vpn_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        save_proxy = SoftButton("保存并应用", "primary", height=32, min_width=112,
                                point_size=8.8)
        save_proxy.clicked.connect(self._apply_proxy_settings)
        action_row.addWidget(save_proxy)
        test_proxy = SoftButton("测试连通", "ghost", height=32, min_width=96,
                                point_size=8.8)
        test_proxy.clicked.connect(self._test_proxy)
        action_row.addWidget(test_proxy)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        self.proxy_status = QLabel(vpn.summary(self.store))
        self.proxy_status.setObjectName("Tiny")
        self.proxy_status.setWordWrap(True)
        card.body.addWidget(self.proxy_status)
        return card

    def _collect_proxy(self) -> Dict[str, object]:
        try:
            port = int(float(self.proxy_port_edit.text().strip() or 8080))
        except Exception:
            port = 8080
        return {
            "enabled": self.proxy_switch.isChecked(),
            "kind": self.proxy_kind_select.currentData() or "http",
            "host": self.proxy_host_edit.text().strip(),
            "port": port,
            "user": self.proxy_user_edit.text().strip(),
            "password": self.proxy_pass_edit.text().strip(),
            "vpn_url": self.vpn_url_edit.text().strip(),
        }

    def _apply_proxy_settings(self) -> None:
        from . import vpn

        vpn.save(self.store, self._collect_proxy())
        vpn.apply_qt_proxy(self.store)
        self.proxy_status.setText(vpn.summary(self.store))
        self.notify.emit("代理设置已生效")

    def _test_proxy(self) -> None:
        from . import vpn

        self._apply_proxy_settings()
        self.proxy_status.setText("正在测试…")
        try:
            result = vpn.probe(self.store)
        except Exception as exc:  # noqa: BLE001
            self.proxy_status.setText(f"测试失败：{exc}")
            return
        parts = []
        for item in result.get("results", []):
            if item.get("ok"):
                parts.append(f"{item['name']} {item.get('ms', 0)}ms")
            else:
                parts.append(f"{item['name']} 失败")
        state = "通" if result.get("ok") else "不通"
        self.proxy_status.setText(
            f"{state}（{result.get('mode')}）· " + " · ".join(parts))

    def _open_vpn_page(self) -> None:
        url = (self.vpn_url_edit.text().strip() or "https://vpn.fudan.edu.cn")
        dock = getattr(self.window(), "dock", None)
        try:
            if dock is not None:
                dock.show_ai()
                dock.ai.web.load(url)
                return
        except Exception:
            pass
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            open_external(url)
        except Exception:
            pass

    # -------------------------------------------------- 文献
    # -------------------------------------------------- 仪表板数据来源
    def _build_dashsrc_card(self) -> Card:
        from . import dashboard

        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=12)
        card.body.addLayout(self._section_head(
            icons.INFO, "ehall 数据来源",
            "仪表板上的校园卡、校车、刷锻、图书馆、待办、空教室都按这里的地址取数",
        ))
        tip = QLabel(
            "用法：在「eHall」页里登录一次，进到对应服务（比如空闲教室查询），"
            "把地址栏的地址复制到下面这一栏，保存，然后回仪表板点「同步 ehall」。"
            "留空的项就不同步。取到的是那个页面上的内容，登录态和 eHall 网页共用。")
        tip.setObjectName("Tiny")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {MUTED};")
        card.body.addWidget(tip)

        self._ehall_url_edits = {}
        for spec in dashboard.EHALL_CARDS:
            row = QHBoxLayout()
            row.setSpacing(10)
            name = QLabel(spec["title"])
            name.setFixedWidth(104)
            name.setStyleSheet(f"color: {TEXT}; font-size: 10pt;")
            row.addWidget(name)
            edit = QLineEdit()
            edit.setPlaceholderText(str(spec.get("hint") or "这个服务的页面地址"))
            edit.setFixedHeight(32)
            try:
                edit.setText(dashboard.source_url(self.store, spec["id"]))
            except Exception:
                pass
            self._ehall_url_edits[spec["id"]] = edit
            row.addWidget(edit, 1)
            go = SoftButton("去 eHall", "ghost", height=30, min_width=76,
                            point_size=8.4)
            go.clicked.connect(lambda _=False, cid=spec["id"]: self._goto_service(cid))
            row.addWidget(go)
            card.body.addLayout(row)

        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {BORDER};")
        card.body.addWidget(divider)

        bus_row = QHBoxLayout()
        bus_row.setSpacing(10)
        bus_label = QLabel("校车方向")
        bus_label.setFixedWidth(104)
        bus_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt;")
        bus_row.addWidget(bus_label)
        directions = []
        try:
            directions = dashboard.bus_directions(self.store)
        except Exception:
            directions = []
        self.bus_dir_edit_a = QLineEdit()
        self.bus_dir_edit_a.setPlaceholderText("例如 邯郸 → 江湾")
        self.bus_dir_edit_a.setFixedHeight(32)
        self.bus_dir_edit_b = QLineEdit()
        self.bus_dir_edit_b.setPlaceholderText("例如 江湾 → 邯郸")
        self.bus_dir_edit_b.setFixedHeight(32)
        if len(directions) > 0:
            self.bus_dir_edit_a.setText(directions[0])
        if len(directions) > 1:
            self.bus_dir_edit_b.setText(directions[1])
        bus_row.addWidget(self.bus_dir_edit_a, 1)
        bus_row.addWidget(self.bus_dir_edit_b, 1)
        card.body.addLayout(bus_row)

        # 空教室：教学楼列表（仪表板那个组件先选楼、再选教室）
        room_row = QHBoxLayout()
        room_row.setSpacing(10)
        room_label = QLabel("教学楼列表")
        room_label.setFixedWidth(104)
        room_label.setStyleSheet(f"color: {TEXT}; font-size: 10pt;")
        room_row.addWidget(room_label)
        self.building_edit = QLineEdit()
        self.building_edit.setPlaceholderText(
            "逗号分隔，例如：二教, 三教, 四教, 五教, 六教, 光华西, 江湾")
        self.building_edit.setFixedHeight(32)
        self.building_edit.setToolTip(
            "仪表板「空教室」组件里「教学楼」下拉的选项；留空就用默认那一套")
        try:
            self.building_edit.setText(
                ", ".join(dashboard.empty_room_buildings(self.store)))
        except Exception:
            pass
        room_row.addWidget(self.building_edit, 1)
        card.body.addLayout(room_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        save = SoftButton("保存", "primary", height=34, min_width=92,
                          point_size=8.8)
        save.clicked.connect(self._save_dashboard_sources)
        buttons.addWidget(save)
        copy_button = SoftButton("看抓到的原文", "ghost", height=34, min_width=116,
                                 point_size=8.8)
        copy_button.clicked.connect(self._refresh_ehall_debug)
        buttons.addWidget(copy_button)
        buttons.addStretch(1)
        card.body.addLayout(buttons)

        self.dashboard_status = QLabel("")
        self.dashboard_status.setObjectName("Tiny")
        self.dashboard_status.setWordWrap(True)
        card.body.addWidget(self.dashboard_status)

        self.ehall_debug_label = QLabel("")
        self.ehall_debug_label.setObjectName("Tiny")
        self.ehall_debug_label.setWordWrap(True)
        self.ehall_debug_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ehall_debug_label.setStyleSheet(f"color: {MUTED};")
        card.body.addWidget(self.ehall_debug_label)
        return card

    def _refresh_dashsrc_edits(self) -> None:
        """把地址输入框和校车方向按设置里的当前值重填一遍。"""
        from . import dashboard

        for card_id, edit in getattr(self, "_ehall_url_edits", {}).items():
            try:
                edit.setText(dashboard.source_url(self.store, card_id))
            except Exception:
                pass
        try:
            directions = dashboard.bus_directions(self.store)
            self.bus_dir_edit_a.setText(directions[0] if directions else "")
            self.bus_dir_edit_b.setText(directions[1] if len(directions) > 1 else "")
        except Exception:
            pass
        try:
            edit = getattr(self, "building_edit", None)
            if edit is not None:
                edit.setText(", ".join(dashboard.empty_room_buildings(self.store)))
        except Exception:
            pass

    def _goto_service(self, card_id: str) -> None:
        """从设置里直接跳去 eHall（打开当前填的地址，或 ehall 首页）。"""
        from . import dashboard

        window = self.window()
        navigate = getattr(window, "navigate", None)
        if not callable(navigate):
            return
        url = ""
        edit = getattr(self, "_ehall_url_edits", {}).get(card_id)
        if edit is not None:
            url = (edit.text() or "").strip()
        url = url or dashboard.EHALL_HOME
        navigate(5)
        try:
            window.ehall_page.web.load(url)
        except Exception:
            pass

    def _save_dashboard_sources(self) -> None:
        from . import dashboard

        for card_id, edit in getattr(self, "_ehall_url_edits", {}).items():
            url = (edit.text() or "").strip()
            try:
                self.store.set_setting("ehall_json_%s" % card_id, url)
            except Exception:
                pass
        try:
            dashboard.set_bus_directions(self.store, [
                (self.bus_dir_edit_a.text() or "").strip(),
                (self.bus_dir_edit_b.text() or "").strip(),
            ])
        except Exception:
            pass
        try:
            building_edit = getattr(self, "building_edit", None)
            if building_edit is not None:
                raw = (building_edit.text() or "").replace("，", ",")
                dashboard.set_empty_room_buildings(
                    self.store, [item.strip() for item in raw.split(",")
                                 if item.strip()])
        except Exception:
            pass
        self.dashboard_status.setText("已保存。回仪表板点「同步 ehall」就能取数了。")
        window = self.window()
        page = getattr(window, "dashboard_page", None)
        if page is not None:
            try:
                page.refresh()
            except Exception:
                pass

    def _refresh_ehall_debug(self) -> None:
        from . import dashboard, ehall_bridge

        lines = []
        for spec in dashboard.EHALL_CARDS:
            cached = ehall_bridge.cache_get(self.store, spec["id"]) or {}
            age = ehall_bridge.cache_age_text(self.store, spec["id"])
            head = "【%s】%s" % (spec["title"], age or "还没同步")
            lines.append(head)
            if cached.get("error"):
                lines.append("    问题：%s" % cached["error"])
            sample = str(cached.get("text") or "").strip()
            if sample:
                lines.append("    原文：%s" % sample[:280].replace("\n", " "))
        self.ehall_debug_label.setText("\n".join(lines) or "还没有抓到任何内容。")

    def _build_refs_card(self) -> Card:
        from . import refs

        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.DOCUMENT, "文献",
            "导入 BibTeX / RIS 或按 DOI 抓取，再导出 .ris / .bib —— EndNote、Zotero 都能直接吃",
        ))

        import_row = QHBoxLayout()
        import_row.setSpacing(8)
        import_bib = SoftButton("导入 BibTeX", "ghost", height=30, min_width=110,
                                point_size=8.6)
        import_bib.clicked.connect(lambda: self._import_refs("bib"))
        import_row.addWidget(import_bib)
        import_ris = SoftButton("导入 RIS", "ghost", height=30, min_width=100,
                                point_size=8.6)
        import_ris.clicked.connect(lambda: self._import_refs("ris"))
        import_row.addWidget(import_ris)
        import_row.addStretch(1)
        card.body.addLayout(import_row)

        doi_row = QHBoxLayout()
        doi_row.setSpacing(8)
        doi_label = QLabel("DOI")
        doi_label.setObjectName("Small")
        doi_row.addWidget(doi_label)
        self.doi_edit = QLineEdit()
        self.doi_edit.setPlaceholderText("10.1090/noti2201 或 https://doi.org/…")
        doi_row.addWidget(self.doi_edit, 1)
        fetch_doi = SoftButton("抓取并添加", "ghost", height=30, min_width=110,
                               point_size=8.6)
        fetch_doi.clicked.connect(self._fetch_doi)
        doi_row.addWidget(fetch_doi)
        card.body.addLayout(doi_row)

        export_row = QHBoxLayout()
        export_row.setSpacing(8)
        export_ris = SoftButton("导出 RIS（EndNote）", "primary", height=30,
                                min_width=160, point_size=8.6)
        export_ris.clicked.connect(lambda: self._export_refs("ris"))
        export_row.addWidget(export_ris)
        export_bib = SoftButton("导出 BibTeX", "ghost", height=30, min_width=120,
                                point_size=8.6)
        export_bib.clicked.connect(lambda: self._export_refs("bib"))
        export_row.addWidget(export_bib)
        export_row.addStretch(1)
        card.body.addLayout(export_row)

        self.refs_status = QLabel("")
        self.refs_status.setObjectName("Tiny")
        self.refs_status.setWordWrap(True)
        card.body.addWidget(self.refs_status)

        self.refs_layout = QVBoxLayout()
        self.refs_layout.setSpacing(4)
        card.body.addLayout(self.refs_layout)
        self._refresh_refs()
        return card

    def _refresh_refs(self) -> None:
        from . import refs

        layout = getattr(self, "refs_layout", None)
        if layout is None:
            return
        clear_layout(layout)
        items = refs.list_refs(self.store)
        if not items:
            hint = QLabel("还没有文献：导入 BibTeX / RIS，或者填个 DOI 抓一下")
            hint.setObjectName("Tiny")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            return
        for item in items[:12]:
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            title = str(item.get("title") or "（无标题）")[:56]
            year = str(item.get("year") or "")
            label = QLabel(f"{title} · {year}" if year else title)
            label.setObjectName("Small")
            row.addWidget(label, 1)
            remove = SoftButton("删除", "ghost", height=26, min_width=58, point_size=8.4)
            remove.clicked.connect(
                lambda _c=False, rid=int(item["id"]): self._delete_ref(rid))
            row.addWidget(remove)
            layout.addWidget(holder)
        if getattr(self, "refs_status", None) is not None:
            self.refs_status.setText(f"库里共 {len(items)} 条文献")

    def _delete_ref(self, ref_id: int) -> None:
        from . import refs

        refs.delete(self.store, int(ref_id))
        self._refresh_refs()

    def _import_refs(self, fmt: str) -> None:
        from . import refs

        filt = "BibTeX (*.bib)" if fmt == "bib" else "RIS (*.ris)"
        path, _ = QFileDialog.getOpenFileName(self, "选择文献文件", "", filt)
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self.refs_status.setText(f"读取失败：{exc}")
            return
        entries = refs.parse_bibtex(text) if fmt == "bib" else refs.parse_ris(text)
        count = refs.add_many(self.store, entries)
        self.refs_status.setText(f"导入 {count} 条（跳过 {len(entries) - count} 条重复）")
        self._refresh_refs()

    def _fetch_doi(self) -> None:
        from . import refs

        doi = self.doi_edit.text().strip()
        if not doi:
            self.refs_status.setText("先填一个 DOI")
            return
        self.refs_status.setText("正在向 Crossref 查询…")
        try:
            entry = refs.fetch_doi(doi)
        except Exception as exc:  # noqa: BLE001
            self.refs_status.setText(f"抓取失败：{exc}")
            return
        refs.add(self.store, entry)
        self.doi_edit.clear()
        self.refs_status.setText(f"已添加：{entry.get('title', '')[:40]}")
        self._refresh_refs()

    def _export_refs(self, fmt: str) -> None:
        from . import refs, settings_dirs

        target_dir = settings_dirs.downloads_dir(self.store)
        name = "lumina-refs.ris" if fmt == "ris" else "lumina-refs.bib"
        path = Path(target_dir) / name
        try:
            written = refs.export(self.store, path, fmt)
        except Exception as exc:  # noqa: BLE001
            self.refs_status.setText(f"导出失败：{exc}")
            return
        self.refs_status.setText(f"已导出：{written}")

    def _on_translucent_toggled(self, checked: bool) -> None:
        self.store.set_setting("window_translucent", "1" if checked else "0")
        self.notify.emit(
            "已切换半透明窗口，重启软件后生效" if checked
            else "已切回不透明窗口（内嵌网页才能点），重启后生效")

    def _on_browser_tabs_toggled(self, checked: bool) -> None:
        """多标签页开关：当场套到每个网页面板上（网页不会重新加载）。"""
        self.store.set_setting("browser_tabs", "1" if checked else "0")
        try:
            from . import web_panel

            web_panel.set_tabs_enabled(bool(checked))
        except Exception:
            pass
        self.notify.emit("多标签页已开启：作业 / eHall / AI 网页顶部多了一排标签"
                         if checked else "多标签页已关闭（只留一个网页）")

    def _focus_minutes(self) -> int:
        try:
            value = int(float(self.focus_minutes_edit.text().strip() or "5"))
        except Exception:
            value = 5
        return max(1, min(120, value))

    def _build_tray_card(self) -> Card:
        """窗口行为：点关闭是「收进托盘」还是「直接退出」。"""
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.SWAP, "窗口行为",
            "点右上角「关闭」时收进系统托盘；要真退出就右键托盘图标选「退出 DanLume」",
        ))
        row = QHBoxLayout()
        row.setSpacing(12)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title = QLabel("关闭时收进托盘")
        title.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        desc = QLabel("关闭后程序仍在后台（提醒、通知监控照常）；"
                      "右键托盘图标可以「显示主窗口 / 退出」")
        desc.setObjectName("Tiny")
        desc.setWordWrap(True)
        text_box.addWidget(title)
        text_box.addWidget(desc)
        row.addLayout(text_box, 1)
        self.tray_switch = ToggleSwitch(
            (self.store.get_setting("tray_on_close", "1") or "1") == "1")
        self.tray_switch.toggled.connect(self._on_tray_toggled)
        row.addWidget(self.tray_switch)
        card.body.addLayout(row)
        return card

    def _on_tray_toggled(self, checked: bool) -> None:
        try:
            self.store.set_setting("tray_on_close", "1" if checked else "0")
        except Exception:
            return
        self.notify.emit(
            "关闭时会收进托盘，右键托盘图标可以退出"
            if checked else "关闭窗口就直接退出程序")

    def _build_reminder_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.BELL, "日程提醒",
            "每 20 秒检查一次即将开始的日程，并在桌面右下角弹出提醒",
        ))

        row = QHBoxLayout()
        row.setSpacing(12)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title = QLabel("桌面提醒")
        title.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        desc = QLabel("关闭后日程仍会保留提醒时间，但不再弹窗")
        desc.setObjectName("Tiny")
        text_box.addWidget(title)
        text_box.addWidget(desc)
        row.addLayout(text_box, 1)
        self.reminder_switch = ToggleSwitch(True)
        self.reminder_switch.toggled.connect(self._apply_switches_now)
        row.addWidget(self.reminder_switch)
        card.body.addLayout(row)

        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {BORDER};")
        card.body.addWidget(divider)

        sound_row = QHBoxLayout()
        text_box2 = QVBoxLayout()
        text_box2.setSpacing(2)
        sound_title = QLabel("提醒提示音")
        sound_title.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        sound_desc = QLabel("弹窗出现时播放系统提示音")
        sound_desc.setObjectName("Tiny")
        text_box2.addWidget(sound_title)
        text_box2.addWidget(sound_desc)
        sound_row.addLayout(text_box2, 1)
        self.sound_switch = ToggleSwitch(True)
        self.sound_switch.toggled.connect(self._apply_switches_now)
        sound_row.addWidget(self.sound_switch)
        card.body.addLayout(sound_row)

        auto_row = QHBoxLayout()
        text_box3 = QVBoxLayout()
        text_box3.setSpacing(2)
        auto_title = QLabel("过期自动完成")
        auto_title.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        auto_desc = QLabel("结束时间已过的日程自动打勾，不用手动点")
        auto_desc.setObjectName("Tiny")
        text_box3.addWidget(auto_title)
        text_box3.addWidget(auto_desc)
        auto_row.addLayout(text_box3, 1)
        self.auto_done_switch = ToggleSwitch(True)
        self.auto_done_switch.toggled.connect(self._apply_switches_now)
        auto_row.addWidget(self.auto_done_switch)
        card.body.addLayout(auto_row)
        return card

    def _apply_switches_now(self, *_args) -> None:
        """提醒 / 提示音 / 过期自动完成：拨一下就生效，不用再点「保存设置」。

        注意只在**值真的变了**的时候才通知外面，否则 `refresh()` 回填开关时
        会再触发一次、外面刷新又回填，来回打转。
        """
        if not all(hasattr(self, name) for name in
                   ("reminder_switch", "sound_switch", "auto_done_switch")):
            return
        wanted = {
            "reminders_enabled": "1" if self.reminder_switch.isChecked() else "0",
            "reminder_sound": "1" if self.sound_switch.isChecked() else "0",
            "auto_done_enabled": "1" if self.auto_done_switch.isChecked() else "0",
        }
        try:
            changed = any((self.store.get_setting(key, "") or "") != value
                          for key, value in wanted.items())
            self.store.update_settings(wanted)
        except Exception:
            return
        if changed:
            try:
                self.settings_saved.emit()
            except Exception:
                pass

    # -------------------------------------------------- 定位与出发提醒
    # -------------------------------------------------- 目录
    def _build_dirs_card(self) -> Card:
        from . import settings_dirs

        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=12)
        card.body.addLayout(self._section_head(
            icons.FOLDER, "目录",
            "程序会往这些地方写文件；留空则用默认位置（data/ 下面）",
        ))

        self.dir_fields = {}
        specs = [
            ("dir_downloads", "作业 / 附件下载目录", settings_dirs.DEFAULT_DOWNLOADS),
            ("dir_latex_build", "LaTeX 编译目录", settings_dirs.DEFAULT_LATEX_BUILD),
            ("dir_latex_math", "公式缓存目录", settings_dirs.DEFAULT_LATEX_MATH),
        ]
        for key, label_text, default_rel in specs:
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(label_text)
            label.setObjectName("Small")
            label.setFixedWidth(150)
            row.addWidget(label)
            edit = QLineEdit()
            edit.setPlaceholderText(f"默认：data/{default_rel}")
            edit.setFixedHeight(32)
            row.addWidget(edit, 1)
            browse = SoftButton("选择…", "ghost", height=32, min_width=72, point_size=8.6)
            browse.clicked.connect(lambda _c=False, e=edit: self._browse_dir(e))
            row.addWidget(browse)
            open_button = SoftButton("打开", "subtle", height=32, min_width=64,
                                     point_size=8.6)
            open_button.clicked.connect(
                lambda _c=False, k=key: self._open_dir(k))
            row.addWidget(open_button)
            self.dir_fields[key] = edit
            card.body.addLayout(row)

        hint = QLabel("数据目录（数据库所在）：" )
        hint.setObjectName("Tiny")
        card.body.addWidget(hint)
        data_label = QLabel(str(settings_dirs.data_dir()))
        data_label.setObjectName("Tiny")
        data_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.body.addWidget(data_label)
        return card

    def _browse_dir(self, edit: QLineEdit) -> None:
        from PySide6.QtWidgets import QFileDialog

        current = edit.text().strip()
        start = current or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择目录", start)
        if path:
            edit.setText(path)

    def _open_dir(self, key: str) -> None:
        import os
        from . import settings_dirs

        mapping = {
            "dir_downloads": settings_dirs.downloads_dir,
            "dir_latex_build": settings_dirs.latex_build_dir,
            "dir_latex_math": settings_dirs.latex_math_dir,
        }
        target = mapping[key](self.store)
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(target))
        except Exception:
            self.notify.emit("无法打开目录")

    def _load_dir_settings(self) -> None:
        for key, edit in getattr(self, "dir_fields", {}).items():
            edit.setText(self.store.get_setting(key, "") or "")

    def _save_dir_settings(self) -> None:
        from . import settings_dirs

        values = {key: edit.text().strip() for key, edit in self.dir_fields.items()}
        self.store.update_settings(values)
        settings_dirs.apply_to_modules(self.store)

    def _build_location_card(self) -> Card:
        from . import geo

        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.CLOCK, "定位与出发提醒",
            "根据当前位置算到教学楼的步行 / 骑行时间，到点提醒你该出发了",
        ))

        # 位置来源
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        text1 = QVBoxLayout()
        text1.setSpacing(2)
        label1 = QLabel("当前位置")
        label1.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        desc1 = QLabel("默认按「常用地点 + IP」估算；打开定位服务并校准 Wi-Fi 指纹后可以精确到米（见下面「精确定位」）")
        desc1.setObjectName("Tiny")
        desc1.setWordWrap(True)
        text1.addWidget(label1)
        text1.addWidget(desc1)
        row1.addLayout(text1, 1)

        self.location_mode_select = Select()
        self.location_mode_select.setFixedWidth(190)
        self.location_mode_select.addItem("自动（手动优先）", "auto")
        self.location_mode_select.addItem("只用常用地点", "manual")
        self.location_mode_select.addItem("只用 IP 定位", "ip")
        self.location_mode_select.currentIndexChanged.connect(
            lambda _i: self._refresh_location_ui())
        row1.addWidget(self.location_mode_select)
        card.body.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        place_label = QLabel("常用地点")
        place_label.setObjectName("Small")
        row2.addWidget(place_label)
        self.location_place_select = Select()
        self.location_place_select.setMinimumWidth(220)
        for place in geo.CAMPUS_PLACES:
            self.location_place_select.addItem(place["name"], place["id"])
        self.location_place_select.addItem("自定义经纬度…", "custom")
        self.location_place_select.currentIndexChanged.connect(
            lambda _i: self._refresh_location_ui())
        row2.addWidget(self.location_place_select)
        self.location_lat_edit = QLineEdit()
        self.location_lat_edit.setPlaceholderText("纬度 31.3006")
        self.location_lat_edit.setFixedWidth(120)
        row2.addWidget(self.location_lat_edit)
        self.location_lon_edit = QLineEdit()
        self.location_lon_edit.setPlaceholderText("经度 121.5030")
        self.location_lon_edit.setFixedWidth(120)
        row2.addWidget(self.location_lon_edit)
        self.location_test_button = SoftButton("立即定位", "ghost", height=32,
                                               min_width=96, point_size=8.8)
        self.location_test_button.clicked.connect(self._test_location)
        row2.addWidget(self.location_test_button)
        row2.addStretch(1)
        card.body.addLayout(row2)

        self.location_status = QLabel("")
        self.location_status.setObjectName("Tiny")
        self.location_status.setWordWrap(True)
        card.body.addWidget(self.location_status)

        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {BORDER};")
        card.body.addWidget(divider)

        # ---------------- 精确定位（Wi-Fi 指纹，米级）
        precision_row = QHBoxLayout()
        precision_row.setSpacing(12)
        precision_text = QVBoxLayout()
        precision_text.setSpacing(2)
        precision_label = QLabel("精确定位（米级）")
        precision_label.setStyleSheet(
            f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        precision_desc = QLabel(
            "Windows 把「扫描 Wi-Fi」也算作位置数据：打开定位服务后，可以记录当前位置的 Wi-Fi "
            "指纹，之后站到同一处就能认出你在哪间教室（误差几米）")
        precision_desc.setObjectName("Tiny")
        precision_desc.setWordWrap(True)
        precision_text.addWidget(precision_label)
        precision_text.addWidget(precision_desc)
        precision_row.addLayout(precision_text, 1)
        self.location_windows_switch = ToggleSwitch(
            (self.store.get_setting("location_windows", "1") or "1") != "0")
        self.location_windows_switch.setToolTip("允许使用 Windows 系统定位")
        self.location_windows_switch.toggled.connect(
            lambda value: self.store.set_setting(
                "location_windows", "1" if value else "0"))
        precision_row.addWidget(self.location_windows_switch)
        card.body.addLayout(precision_row)

        precision_buttons = QHBoxLayout()
        precision_buttons.setSpacing(8)
        self.loc_scan_button = SoftButton("扫描 Wi-Fi", "ghost", height=32,
                                          min_width=104, point_size=8.8)
        self.loc_scan_button.clicked.connect(self._scan_wifi)
        precision_buttons.addWidget(self.loc_scan_button)
        self.loc_calibrate_button = SoftButton("以当前位置校准", "ghost", height=32,
                                               min_width=130, point_size=8.8)
        self.loc_calibrate_button.clicked.connect(self._calibrate_location)
        precision_buttons.addWidget(self.loc_calibrate_button)
        self.loc_settings_button = SoftButton("打开定位设置", "ghost", height=32,
                                              min_width=118, point_size=8.8)
        self.loc_settings_button.clicked.connect(self._open_location_settings)
        precision_buttons.addWidget(self.loc_settings_button)
        precision_buttons.addStretch(1)
        card.body.addLayout(precision_buttons)

        self.loc_precision_status = QLabel("")
        self.loc_precision_status.setObjectName("Tiny")
        self.loc_precision_status.setWordWrap(True)
        card.body.addWidget(self.loc_precision_status)

        self.loc_points_layout = QVBoxLayout()
        self.loc_points_layout.setSpacing(4)
        card.body.addLayout(self.loc_points_layout)
        self._refresh_location_points()

        divider2 = QWidget()
        divider2.setFixedHeight(1)
        divider2.setStyleSheet(f"background: {BORDER};")
        card.body.addWidget(divider2)

        # 出发提醒开关
        row3 = QHBoxLayout()
        row3.setSpacing(12)
        text3 = QVBoxLayout()
        text3.setSpacing(2)
        label3 = QLabel("出发提醒")
        label3.setStyleSheet(f"color: {TEXT}; font-size: 10pt; font-weight: 600;")
        desc3 = QLabel("按课程「地点」估算路程，在上课前弹出「该出发了」")
        desc3.setObjectName("Tiny")
        desc3.setWordWrap(True)
        text3.addWidget(label3)
        text3.addWidget(desc3)
        row3.addLayout(text3, 1)
        self.departure_switch = ToggleSwitch(True)
        row3.addWidget(self.departure_switch)
        card.body.addLayout(row3)

        # 出行方式 + 速度 + 缓冲
        row4 = QHBoxLayout()
        row4.setSpacing(10)
        mode_label = QLabel("主要出行方式")
        mode_label.setObjectName("Small")
        row4.addWidget(mode_label)
        self.departure_mode_select = Select()
        self.departure_mode_select.setFixedWidth(120)
        self.departure_mode_select.addItem("步行", "walk")
        self.departure_mode_select.addItem("骑行", "bike")
        row4.addWidget(self.departure_mode_select)

        speed_walk_label = QLabel("步行速度")
        speed_walk_label.setObjectName("Small")
        row4.addWidget(speed_walk_label)
        self.speed_walk_edit = QLineEdit()
        self.speed_walk_edit.setFixedWidth(78)
        self.speed_walk_edit.setToolTip("米 / 分钟，默认 80（约 4.8 km/h）")
        row4.addWidget(self.speed_walk_edit)

        speed_bike_label = QLabel("骑行速度")
        speed_bike_label.setObjectName("Small")
        row4.addWidget(speed_bike_label)
        self.speed_bike_edit = QLineEdit()
        self.speed_bike_edit.setFixedWidth(78)
        self.speed_bike_edit.setToolTip("米 / 分钟，默认 200（约 12 km/h）")
        row4.addWidget(self.speed_bike_edit)

        buffer_label = QLabel("提前缓冲（分钟）")
        buffer_label.setObjectName("Small")
        row4.addWidget(buffer_label)
        self.departure_buffer_edit = QLineEdit()
        self.departure_buffer_edit.setFixedWidth(60)
        row4.addWidget(self.departure_buffer_edit)
        row4.addStretch(1)
        card.body.addLayout(row4)
        return card

    def _refresh_location_ui(self) -> None:
        mode = self.location_mode_select.currentData() or "auto"
        custom = (self.location_place_select.currentData() or "") == "custom"
        manual_ok = mode in ("auto", "manual")
        self.location_place_select.setEnabled(manual_ok)
        self.location_lat_edit.setVisible(custom)
        self.location_lon_edit.setVisible(custom)
        self.location_lat_edit.setEnabled(custom)
        self.location_lon_edit.setEnabled(custom)

    def _test_location(self) -> None:
        from . import geo

        if (self.location_place_select.currentData() or "") == "custom":
            self.store.update_settings({
                "location_lat": self.location_lat_edit.text().strip(),
                "location_lon": self.location_lon_edit.text().strip(),
            })
        self.store.update_settings({
            "location_mode": self.location_mode_select.currentData() or "auto",
            "location_place": self.location_place_select.currentData() or "",
        })
        location = geo.current_location(self.store)
        sample = geo.match_place("H3401")
        est = geo.estimate(location, sample)
        text = (f"当前位置：{location.get('name') or '未知'}"
                f"（{location.get('source')}） · 到光华楼 {geo.format_estimate(est)}")
        self.location_status.setText(text)
        self.location_status.setStyleSheet(f"color: {SUCCESS};")

    def _build_data_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=12)
        card.body.addLayout(self._section_head(
            icons.DOCUMENT, "数据与关于",
            "所有日程与邮件记录都保存在本机 SQLite 数据库中",
        ))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.data_path_edit = QLineEdit(str(DATA_DIR))
        self.data_path_edit.setReadOnly(True)
        row.addWidget(self.data_path_edit, 1)
        open_button = SoftButton("打开文件夹", "ghost", glyph=icons.FOLDER, height=34,
                                 point_size=9.0)
        open_button.clicked.connect(self._open_data_dir)
        row.addWidget(open_button)
        card.body.addLayout(row)
        about = QLabel(
            f"DanLume v{APP_VERSION} · 本地优先的日程与工作汇报助手。"
            "邮件通过你配置的 SMTP 服务器直接发送，不经过任何第三方服务。"
        )
        about.setObjectName("Tiny")
        about.setWordWrap(True)
        card.body.addWidget(about)
        return card

    # -------------------------------------------------- 日程分组
    def _build_groups_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=12)
        card.body.addLayout(self._section_head(
            icons.FILTER, "日程分组",
            "同一分组的日程在日历里使用同一个颜色；新建日程时可以选择分组",
        ))
        self.groups_layout = QVBoxLayout()
        self.groups_layout.setSpacing(6)
        card.body.addLayout(self.groups_layout)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.group_name_edit = QLineEdit()
        self.group_name_edit.setPlaceholderText("新分组名称，例如「课程」「小组作业」")
        self.group_color_dots = ColorDots()
        add_button = SoftButton("添加分组", "ghost", glyph=icons.ADD, height=34,
                                min_width=104, point_size=8.8)
        add_button.clicked.connect(self._add_group)
        add_row.addWidget(self.group_name_edit, 1)
        add_row.addWidget(self.group_color_dots)
        add_row.addWidget(add_button)
        card.body.addLayout(add_row)
        return card

    def _refresh_groups(self) -> None:
        if not hasattr(self, "groups_layout"):
            return
        clear_layout(self.groups_layout)
        groups = self.store.list_groups()
        if not groups:
            hint = QLabel("还没有分组；添加后新建日程时就能选到")
            hint.setObjectName("Tiny")
            self.groups_layout.addWidget(hint)
            return
        for group in groups:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(
                f"background: {theme.event_color(group['color']).name()};"
                " border-radius: 7px;"
            )
            name = QLabel(group["name"])
            name.setObjectName("Small")
            rename = IconButton(icons.EDIT, size=28, glyph_size=10, tip="重命名分组")
            rename.clicked.connect(
                lambda _checked=False, gid=int(group["id"]): self._rename_group(gid)
            )
            recolor = IconButton(icons.EYE, size=28, glyph_size=10, tip="修改分组颜色（同步组内日程）")
            recolor.clicked.connect(
                lambda _checked=False, gid=int(group["id"]): self._recolor_group(gid)
            )
            remove = IconButton(icons.DELETE, size=28, glyph_size=10, tip="删除分组")
            remove.clicked.connect(
                lambda _checked=False, gid=int(group["id"]): self._delete_group(gid)
            )
            layout.addWidget(swatch)
            layout.addWidget(name, 1)
            layout.addWidget(rename)
            layout.addWidget(recolor)
            layout.addWidget(remove)
            self.groups_layout.addWidget(row)

    def _add_group(self) -> None:
        name = self.group_name_edit.text().strip()
        if not name:
            self.notify.emit("请先填写分组名称")
            return
        self.store.add_group(name, self.group_color_dots.color())
        self.group_name_edit.clear()
        self._refresh_groups()
        self.notify.emit(f"已添加分组「{name}」")

    def _rename_group(self, group_id: int) -> None:
        from PySide6.QtWidgets import QInputDialog

        current = next((g["name"] for g in self.store.list_groups() if int(g["id"]) == group_id), "")
        name, ok = QInputDialog.getText(self, "重命名分组", "分组名称：", text=current)
        if not ok or not name.strip():
            return
        self.store.update_group(group_id, name=name.strip())
        self._refresh_groups()
        self.settings_saved.emit()
        self.notify.emit(f"分组已重命名为「{name.strip()}」")

    def _recolor_group(self, group_id: int) -> None:
        from PySide6.QtWidgets import QInputDialog

        from .config import COLOR_ORDER, EVENT_COLORS

        labels = [EVENT_COLORS[key][1] for key in COLOR_ORDER]
        label, ok = QInputDialog.getItem(
            self, "修改分组颜色", "选择颜色（组内日程会一起改色）：", labels, 0, False
        )
        if not ok or label not in labels:
            return
        color = COLOR_ORDER[labels.index(label)]
        self.store.update_group(group_id, color=color)
        self._refresh_groups()
        self.settings_saved.emit()
        self.notify.emit(f"分组颜色已更新为「{label}」，组内日程同步改色")

    def _delete_group(self, group_id: int) -> None:
        self.store.delete_group(group_id)
        self._refresh_groups()
        self.settings_saved.emit()
        self.notify.emit("分组已删除，组内日程改为无分组")

    # -------------------------------------------------- 数据加载 / 保存
    def showEvent(self, event):
        # 每次打开设置都把「账户与登录」卡片刷新一遍（登录状态可能刚变）
        try:
            self._refresh_account_card()
        except Exception:
            pass
        try:
            self._refresh_grant_label()
        except Exception:
            pass
        super().showEvent(event)

    def refresh(self) -> None:
        store = self.store
        try:
            self._refresh_grant_label()
        except Exception:
            pass
        self._refresh_account_card()
        self._accounts = accounts.load(store)
        self._active_account = accounts.active_id(store)
        self._refresh_account_select()
        self._load_account_form()
        self.default_to_edit.setText(store.get_setting("default_to", ""))
        self.signature_edit.setPlainText(store.get_setting("mail_signature", "") or "")
        self.reminder_switch.setChecked(store.get_setting("reminders_enabled", "1") == "1",
                                        animate=False)
        self.sound_switch.setChecked(store.get_setting("reminder_sound", "1") == "1",
                                     animate=False)
        browser_value = theme.browser_theme()
        browser_index = self.browser_theme_select.findData(browser_value)
        self.browser_theme_select.setCurrentIndex(max(0, browser_index))
        self.auto_done_switch.setChecked(
            store.get_setting("auto_done_enabled", "1") == "1", animate=False)
        state = theme.appearance()
        self._accent = state.accent
        self._accent2 = state.accent2
        self._bg = state.bg
        self._alpha = state.alpha
        self._glass = state.glass
        self._blur = state.blur
        self._mode = state.mode
        current_fonts = theme.fonts()
        self._font_zh = current_fonts.get("zh", "")
        self._font_en = current_fonts.get("en", "")
        self._fill_font_selects()
        self._refresh_appearance_ui()
        self._load_dir_settings()
        self._load_notice_settings()
        self._load_location_settings()
        self._refresh_contacts()
        self._refresh_timetable_ui()
        self._refresh_groups()
        try:
            self.refresh_server_card()
        except Exception:
            pass

    # -------------------------------------------------- 发件邮箱账户
    def _refresh_account_select(self) -> None:
        self.account_select.blockSignals(True)
        self.account_select.clear()
        for item in self._accounts:
            label = item.get("label") or item.get("sender_email") or item.get("host") or "账户"
            self.account_select.addItem(label, item["id"])
        index = self.account_select.findData(self._active_account)
        if index < 0 and self.account_select.count():
            index = 0
            self._active_account = self.account_select.itemData(0)
        if index >= 0:
            self.account_select.setCurrentIndex(index)
        self.account_select.blockSignals(False)
        self.delete_account_button.setEnabled(self.account_select.count() > 1)

    def _current_account(self) -> Optional[Dict[str, Any]]:
        for item in self._accounts:
            if item.get("id") == self._active_account:
                return item
        return None

    def _load_account_form(self) -> None:
        account = self._current_account()
        self._loading_account = True
        try:
            if account is None:
                self.host_edit.clear()
                self.port_edit.setText("465")
                self.security_select.setCurrentIndex(0)
                self.user_edit.clear()
                self.password_edit.clear()
                self.name_edit.clear()
                self.sender_edit.clear()
                return
            self.host_edit.setText(account.get("host", ""))
            self.port_edit.setText(str(account.get("port") or 465))
            security = account.get("security", "ssl") or "ssl"
            for index in range(self.security_select.count()):
                if self.security_select.itemData(index) == security:
                    self.security_select.setCurrentIndex(index)
                    break
            self.user_edit.setText(account.get("user", ""))
            saved = accounts.password(self.store, account["id"])
            self.password_edit.clear()
            self.password_edit.setPlaceholderText(
                "已保存（不可查看），留空表示不修改" if saved
                else "邮箱授权码 / 应用专用密码")
            self.name_edit.setText(account.get("sender_name", ""))
            self.sender_edit.setText(account.get("sender_email", ""))
        finally:
            self._loading_account = False

    def _collect_account(self) -> None:
        """把表单内容写回正在编辑的账户（切换账户 / 保存前调用）。"""
        account = self._current_account()
        if account is None:
            return
        try:
            port = int(self.port_edit.text().strip() or "465")
        except ValueError:
            port = 465
        sender = self.sender_edit.text().strip()
        account["host"] = self.host_edit.text().strip()
        account["port"] = max(1, min(65535, port))
        account["security"] = self.security_select.currentData() or "ssl"
        account["user"] = self.user_edit.text().strip()
        account["sender_name"] = self.name_edit.text().strip()
        account["sender_email"] = sender
        if sender:
            account["label"] = sender
        elif account["host"] and not account.get("label"):
            account["label"] = account["host"]

    def _on_account_selected(self, index: int) -> None:
        if self._loading_account or index < 0:
            return
        self._collect_account()
        self._active_account = self.account_select.itemData(index) or ""
        self._load_account_form()

    def _add_account(self) -> None:
        self._collect_account()
        account = accounts.blank_account()
        self._accounts.append(account)
        self._active_account = account["id"]
        self._refresh_account_select()
        self._load_account_form()
        self.account_select.setFocus()
        self.notify.emit("已新增发件邮箱，请填写服务器与账号信息")

    def _delete_account(self) -> None:
        if len(self._accounts) <= 1:
            self.notify.emit("至少保留一个发件邮箱")
            return
        account = self._current_account()
        if account is None:
            return
        label = account.get("label") or "该账户"
        self._accounts = [item for item in self._accounts if item.get("id") != account["id"]]
        self._active_account = self._accounts[0]["id"] if self._accounts else ""
        accounts.save(self.store, self._accounts, self._active_account)
        self._refresh_account_select()
        self._load_account_form()
        self.notify.emit(f"已删除 {label}")

    # -------------------------------------------------- 定位设置读写
    def _load_location_settings(self) -> None:
        store = self.store
        for select, key, default in (
            (self.location_mode_select, "location_mode", "auto"),
            (self.location_place_select, "location_place", "south_area"),
            (self.departure_mode_select, "departure_mode", "walk"),
        ):
            value = store.get_setting(key, default) or default
            index = select.findData(value)
            select.blockSignals(True)
            select.setCurrentIndex(index if index >= 0 else 0)
            select.blockSignals(False)
        self.location_lat_edit.setText(store.get_setting("location_lat", "") or "")
        self.location_lon_edit.setText(store.get_setting("location_lon", "") or "")
        self.speed_walk_edit.setText(store.get_setting("travel_speed_walk", "80") or "80")
        self.speed_bike_edit.setText(store.get_setting("travel_speed_bike", "200") or "200")
        self.departure_buffer_edit.setText(store.get_setting("departure_buffer", "2") or "2")
        self.departure_switch.blockSignals(True)
        self.departure_switch.setChecked(
            (store.get_setting("departure_enabled", "1") or "1") == "1", animate=False
        )
        self.departure_switch.blockSignals(False)
        self._refresh_location_ui()

    def _save_location_settings(self) -> None:
        self.store.update_settings({
            "location_mode": self.location_mode_select.currentData() or "auto",
            "location_place": self.location_place_select.currentData() or "",
            "location_lat": self.location_lat_edit.text().strip(),
            "location_lon": self.location_lon_edit.text().strip(),
            "departure_enabled": "1" if self.departure_switch.isChecked() else "0",
            "departure_mode": self.departure_mode_select.currentData() or "walk",
            "travel_speed_walk": self.speed_walk_edit.text().strip() or "80",
            "travel_speed_bike": self.speed_bike_edit.text().strip() or "200",
            "departure_buffer": self.departure_buffer_edit.text().strip() or "2",
        })

    def _refresh_contacts(self) -> None:
        self._contacts = self.store.list_contacts()
        clear_layout(self.contacts_layout)
        if not self._contacts:
            hint = QLabel("还没有常用联系人，添加后可在新建汇报时快速选择")
            hint.setObjectName("Tiny")
            self.contacts_layout.addWidget(hint)
            return
        for contact in self._contacts:
            row = ContactRow(contact)
            row.removed.connect(self._delete_contact)
            self.contacts_layout.addWidget(row)

    def _delete_contact(self, contact_id: int) -> None:
        self.store.delete_contact(contact_id)
        self._refresh_contacts()
        self.notify.emit("联系人已删除")

    def _add_contact(self) -> None:
        email = self.contact_email_edit.text().strip()
        name = self.contact_name_edit.text().strip()
        if not EMAIL_RE.match(email):
            self.contact_status.setText("请输入有效邮箱")
            self.contact_status.setStyleSheet(f"color: {DANGER}; font-size: 8pt;")
            return
        self.store.add_contact(name, email)
        self.contact_name_edit.clear()
        self.contact_email_edit.clear()
        self.contact_status.setText("已添加")
        self.contact_status.setStyleSheet(f"color: {SUCCESS}; font-size: 8pt;")
        self._refresh_contacts()
        self.notify.emit("联系人已添加")

    def save(self) -> None:
        port_text = self.port_edit.text().strip() or "465"
        try:
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.notify.emit("端口需要是 1–65535 之间的数字")
            return
        sender_email = self.sender_edit.text().strip()
        if sender_email and not EMAIL_RE.match(sender_email):
            self.notify.emit("发件邮箱格式不正确")
            return
        default_to = self.default_to_edit.text().strip()
        if default_to and not EMAIL_RE.match(default_to):
            self.notify.emit("默认收件邮箱格式不正确")
            return

        # 把表单写回当前账户，并保存全部账户
        self._collect_account()
        accounts.save(self.store, self._accounts, self._active_account)
        active = self._current_account()
        if active is not None and self.password_edit.text().strip():
            accounts.set_password(self.store, active["id"], self.password_edit.text())
            # 同步旧版单账户字段，保证其它模块与旧数据兼容
            self.store.update_settings({
                "smtp_host": active.get("host", ""),
                "smtp_port": str(active.get("port", 465)),
                "smtp_security": active.get("security", "ssl"),
                "smtp_user": active.get("user", ""),
                "sender_name": active.get("sender_name", ""),
                "sender_email": active.get("sender_email", ""),
            })
        self.store.update_settings({
            "default_to": default_to,
            "mail_signature": self.signature_edit.toPlainText().strip(),
            "reminders_enabled": "1" if self.reminder_switch.isChecked() else "0",
            "reminder_sound": "1" if self.sound_switch.isChecked() else "0",
            "auto_done_enabled": "1" if self.auto_done_switch.isChecked() else "0",
            "timetable_enabled": "1" if self.timetable_switch.isChecked() else "0",
            "focus_away_enabled": "1" if getattr(
                self, "focus_switch", None) is not None
                and self.focus_switch.isChecked() else "0",
            "focus_away_minutes": str(self._focus_minutes()),
            "focus_whitelist": self.focus_whitelist_edit.toPlainText().strip(),
        })

        self._save_notice_settings()
        self._save_location_settings()
        fonts_before = (theme.fonts().get("zh", ""), theme.fonts().get("en", ""))
        previous = (
            theme.appearance().accent.lower(),
            theme.appearance().accent2.lower(),
            theme.appearance().bg,
            round(theme.appearance().alpha, 2),
            theme.appearance().glass,
            round(theme.appearance().blur, 1),
            theme.effective_mode(),
        )
        theme.set_appearance(self._accent, self._accent2, self._bg, self._alpha,
                             self._glass, self._blur, self._mode)
        theme.set_fonts(self._font_zh, self._font_en)
        theme.set_browser_theme(self.browser_theme_select.currentData() or "follow")
        theme.save_to_store(self.store)
        try:
            self.appearance_changed.emit()
        except Exception:
            pass
        current = (
            theme.appearance().accent.lower(),
            theme.appearance().accent2.lower(),
            theme.appearance().bg,
            round(theme.appearance().alpha, 2),
            theme.appearance().glass,
            round(theme.appearance().blur, 1),
            theme.effective_mode(),
        )
        fonts_after = (theme.fonts().get("zh", ""), theme.fonts().get("en", ""))
        appearance_changed = previous != current or fonts_before != fonts_after
        theme.apply_theme(QApplication.instance())

        self.notify.emit("设置已保存")
        if appearance_changed:
            self.appearance_changed.emit()
        else:
            self.settings_saved.emit()

    # -------------------------------------------------- 其他
    def _on_security_changed(self, _index: int) -> None:
        defaults = {"ssl": "465", "starttls": "587", "none": "25"}
        key = self.security_select.currentData() or "ssl"
        current = self.port_edit.text().strip()
        if current in ("", "465", "587", "25"):
            self.port_edit.setText(defaults.get(key, "465"))

    def _toggle_password(self) -> None:
        if self.password_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.password_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

    def _current_config(self) -> SmtpConfig:
        try:
            port = int(self.port_edit.text().strip() or "465")
        except ValueError:
            port = 465
        return SmtpConfig(
            host=self.host_edit.text().strip(),
            port=port,
            security=self.security_select.currentData() or "ssl",
            user=self.user_edit.text().strip(),
            password=self.password_edit.text(),
            sender_name=self.name_edit.text().strip(),
            sender_email=self.sender_edit.text().strip(),
            account_id=self._active_account,
            account_label=self.account_select.currentText(),
        )

    def _test_connection(self) -> None:
        if self._tester.is_busy():
            return
        cfg = self._current_config()
        if not cfg.host:
            self._set_test_status("请先填写服务器地址", WARNING)
            return
        if not cfg.sender_email:
            self._set_test_status("请先填写发件邮箱", WARNING)
            return
        self.test_button.setEnabled(False)
        self._set_test_status("正在连接服务器…", theme.appearance().accent2_q().name())
        self._tester.test(cfg)

    def _on_test_finished(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self._set_test_status(message, SUCCESS if ok else DANGER)

    def _set_test_status(self, message: str, color: str) -> None:
        self.test_status.setText(message)
        self.test_status.setStyleSheet(f"color: {color}; font-size: 9pt;")

    def _open_data_dir(self) -> None:
        try:
            os.startfile(str(DATA_DIR))
        except Exception:
            self.notify.emit("无法打开数据文件夹")
