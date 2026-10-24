"""设置页：SMTP、默认收件人、提醒与数据位置。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIntValidator, QLinearGradient, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QCheckBox, QPlainTextEdit,
    QApplication, QColorDialog, QFileDialog, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QSlider, QVBoxLayout, QWidget,
)

from . import accounts, fdu, icons, theme, timetable
from .config import APP_VERSION, DATA_DIR, SMTP_SECURITY
from .mailer import SmtpConfig
from .sending import SmtpTester
from .theme import (
    ACCENT, ACCENT2, BORDER, CARD, DANGER, FAINT, MUTED, SUCCESS, TEXT, TEXT_SOFT,
    WARNING,
)
from .inputs import ColorDots
from .widgets import (
    Card, FieldBlock, GlassSlider, IconBadge, IconButton, Select, SoftButton,
    ToggleSwitch, clear_layout, rounded_path,
)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# AI 接口预设：选一下就把地址和模型名填好，Key 还是用户自己填
LLM_PRESETS: List[Dict[str, str]] = [
    {"id": "local", "name": "本地模型",
     "base": "http://127.0.0.1:8080/v1", "model": ""},
    {"id": "deepseek", "name": "DeepSeek（官方 API）",
     "base": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
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

        self.settings_scroll, _body, self.content = make_scroll()
        self.content.setSpacing(14)
        root.addWidget(self.settings_scroll, 1)

        self.content.addWidget(self._build_appearance_card())
        self.content.addWidget(self._build_smtp_card())
        self.content.addWidget(self._build_imap_card())
        self.content.addWidget(self._build_uis_card())
        self.content.addWidget(self._build_timetable_card())
        self.content.addWidget(self._build_groups_card())
        self.content.addWidget(self._build_recipient_card())
        self.content.addWidget(self._build_dirs_card())
        self.content.addWidget(self._build_notice_card())
        self.content.addWidget(self._build_location_card())
        self.content.addWidget(self._build_reminder_card())
        self.content.addWidget(self._build_focus_card())
        self.content.addWidget(self._build_llm_card())
        self.content.addWidget(self._build_prompts_card())
        self.content.addWidget(self._build_vpn_card())
        self.content.addWidget(self._build_refs_card())
        self.content.addWidget(self._build_data_card())
        self.content.addStretch(1)

        self.refresh()

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

    # -------------------------------------------------- 外观
    def _build_appearance_card(self) -> Card:
        card = Card(radius=18, fill=CARD, border=BORDER, padding=20, spacing=14)
        card.body.addLayout(self._section_head(
            icons.EYE, "外观",
            "浅色 / 深色、强调色与窗口通透度，保存设置后立即生效",
        ))

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


        font_row = QHBoxLayout()
        font_row.setSpacing(10)
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
        font_row.addLayout(font_text, 2)

        self.font_preset_select = Select()
        self.font_preset_select.setFixedWidth(216)
        self.font_preset_select.setToolTip("字体搭配")
        for preset in theme.FONT_PRESETS:
            self.font_preset_select.addItem(preset["name"], preset["id"])
        self.font_preset_select.addItem("自定义（下方手动选择）", "custom")
        self.font_preset_select.currentIndexChanged.connect(self._on_font_preset)
        font_row.addWidget(self.font_preset_select)

        self.font_zh_select = Select()
        self.font_zh_select.setFixedWidth(190)
        self.font_zh_select.setToolTip("中文字体")
        self.font_zh_select.currentIndexChanged.connect(self._on_font_changed)
        font_row.addWidget(self.font_zh_select)
        self.font_en_select = Select()
        self.font_en_select.setFixedWidth(190)
        self.font_en_select.setToolTip("西文字体")
        self.font_en_select.currentIndexChanged.connect(self._on_font_changed)
        font_row.addWidget(self.font_en_select)
        self.font_reset_button = SoftButton("恢复默认", "subtle", height=30, point_size=8.8)
        self.font_reset_button.clicked.connect(self._reset_fonts)
        font_row.addWidget(self.font_reset_button)
        card.body.addLayout(font_row)

        self._refresh_appearance_ui()
        return card

    def _on_glass_toggled(self, checked: bool) -> None:
        self._glass = bool(checked)

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

    def _on_font_preset(self, _index: int) -> None:
        preset_id = self.font_preset_select.currentData() or "apple"
        for preset in theme.FONT_PRESETS:
            if preset["id"] == preset_id:
                self._font_zh = preset["zh"]
                self._font_en = preset["en"]
                self._fill_font_selects()
                return

    def _reset_fonts(self) -> None:
        self._font_zh = ""
        self._font_en = ""
        self._sync_font_preset()
        self._fill_font_selects()

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

    def _choose_accent(self, accent: str, accent2: str) -> None:
        self._accent = accent
        self._accent2 = accent2
        self._refresh_appearance_ui()

    def _pick_custom_accent(self) -> None:
        color = QColorDialog.getColor(
            QColor(self._accent), self, "选择强调色"
        )
        if color.isValid():
            self._accent = color.name()
            self._accent2 = theme.harmonize_accent(self._accent)
            self._refresh_appearance_ui()

    def _choose_bg(self, preset_id: str) -> None:
        self._bg = preset_id
        self._refresh_appearance_ui()

    def _restore_appearance(self) -> None:
        self._accent = theme.DEFAULT_APPEARANCE["accent"]
        self._accent2 = theme.DEFAULT_APPEARANCE["accent2"]
        self._bg = theme.DEFAULT_APPEARANCE["bg"]
        self._alpha = theme.DEFAULT_APPEARANCE["alpha"]
        self._glass = theme.DEFAULT_APPEARANCE["glass"]
        self._blur = theme.DEFAULT_APPEARANCE["blur"]
        self._mode = theme.DEFAULT_APPEARANCE["mode"]
        self._refresh_appearance_ui()

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
        self.alpha_slider.setValue(int(round(self._alpha * 100)))
        self.alpha_value.setText(f"{int(round(self._alpha * 100))}%")
        if hasattr(self, "blur_slider"):
            self.blur_slider.setValue(int(round(self._blur)))
            self.blur_value.setText(f"{int(round(self._blur))} px")
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
        return card

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

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.import_ics_button = SoftButton("导入 .ics 课表", "ghost", glyph=icons.ADD,
                                            height=34, min_width=132, point_size=8.8)
        self.import_ics_button.clicked.connect(self._import_timetable)
        actions.addWidget(self.import_ics_button)
        self.detect_danxi_button = SoftButton("查找 DanXi", "ghost", height=34,
                                              min_width=104, point_size=8.8)
        self.detect_danxi_button.clicked.connect(self._detect_danxi)
        actions.addWidget(self.detect_danxi_button)
        actions.addStretch(1)
        card.body.addLayout(actions)

        # ---- 复旦教务直连（内嵌浏览器登录后抓取）----
        fdu_row = QHBoxLayout()
        fdu_row.setSpacing(8)
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
        self.term_start_edit.setFixedWidth(120)
        fdu_row.addWidget(self.term_start_edit)
        fdu_row.addStretch(1)
        card.body.addLayout(fdu_row)

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
            cfg.get("llm_base_url", ""))
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
        has_api_key = bool(self.store.get_secret("llm_api_key", ""))
        self.llm_api_edit = QLineEdit()
        self.llm_api_edit.setPlaceholderText(
            "已保存（不可查看），留空表示不修改" if has_api_key else "本地模型留空")
        self.llm_api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_api_edit.setFixedWidth(220)
        key_row.addWidget(self.llm_api_edit)
        card.body.addLayout(key_row)

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

    # -------------------------------------------------- AI 接口预设
    @staticmethod
    def _guess_llm_preset(base_url: str) -> str:
        url = (base_url or "").lower()
        for item in LLM_PRESETS:
            if item["id"] != "custom" and item["base"] and item["base"].lower() == url:
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

    def _save_llm_settings(self) -> None:
        try:
            values = self._collect_llm_settings()
            self.store.update_settings(values)
            key = self.llm_api_edit.text().strip()
            if key:
                self.store.set_secret("llm_api_key", key)
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

            QDesktopServices.openUrl(QUrl(url))
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
        self._load_prompt_text()
        return card

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

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(prompts.ensure())))
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

            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            pass

    # -------------------------------------------------- 文献
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
        """多标签页开关：下次启动生效（要重建浏览器控件）。"""
        self.store.set_setting("browser_tabs", "1" if checked else "0")
        try:
            from . import web_panel

            web_panel.set_tabs_enabled(bool(checked))
        except Exception:
            pass
        self.notify.emit(
            "多标签页已开启，重新打开软件后生效" if checked
            else "多标签页已关闭，重新打开软件后生效")

    def _focus_minutes(self) -> int:
        try:
            value = int(float(self.focus_minutes_edit.text().strip() or "5"))
        except Exception:
            value = 5
        return max(1, min(120, value))

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
        auto_row.addWidget(self.auto_done_switch)
        card.body.addLayout(auto_row)
        return card

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
            f"Lumina v{APP_VERSION} · 本地优先的日程与工作汇报助手。"
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
    def refresh(self) -> None:
        store = self.store
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
