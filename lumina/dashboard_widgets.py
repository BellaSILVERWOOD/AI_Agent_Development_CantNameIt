# -*- coding: utf-8 -*-
"""仪表板的小组件（Apple 风格）：圆角大方一点的卡片 + 彩色图标徽标 + 大号数字。

参考 macOS / iOS 的小组件：一张柔和的圆角卡，左上角一个彩色小徽标 + 一行小标题，
中间是**很大的一行数值**，下面是灰色的补充信息；卡片悬停时底色微微变亮。
所有颜色都走主题令牌（深浅色自动跟随），所以这里不写死任何色值。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from . import theme
from .theme import BORDER_SOFT, CARD, CARD_HOVER, MUTED, TEXT, TEXT_SOFT
from .widgets import Card, icon_font

#: 小组件常用尺寸（Apple 那套 small / medium / large 的感觉）
SMALL_HEIGHT = 138
MEDIUM_HEIGHT = 150


def tone_colors(tone: str) -> tuple:
    """取一组柔和的（底色, 文字色）用于图标徽标。"""
    tone = (tone or "teal").strip() or "teal"
    try:
        soft, _hover, ink = theme.event_tint(tone)
        return soft, ink
    except Exception:
        accent = QColor(theme.appearance().accent)
        return theme.mix(accent, QColor(CARD), 0.85), accent


class IconBadge(QWidget):
    """小圆角方块 + 中间一个图标（小组件左上角那个）。"""

    def __init__(self, glyph: str = "", tone: str = "teal", size: int = 28,
                 parent=None):
        super().__init__(parent)
        self._glyph = glyph or ""
        self._tone = tone
        self._radius = max(6.0, size * 0.32)
        self._glyph_size = max(9, int(size * 0.46))
        self.setFixedSize(QSize(size, size))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def on_theme_changed(self) -> None:
        self.update()

    def paintEvent(self, event):
        soft, ink = tone_colors(self._tone)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(soft)
        painter.drawRoundedRect(rect, self._radius, self._radius)
        if self._glyph:
            painter.setPen(ink)
            painter.setFont(icon_font(self._glyph_size))
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter),
                             self._glyph)


def big_label(text: str = "", size: float = 30.0, color: Optional[str] = None,
              parent=None) -> QLabel:
    """小组件里那个「很大的数值」。"""
    label = QLabel(text, parent)
    font = QFont(label.font())
    font.setPointSizeF(size)
    font.setWeight(QFont.Weight.DemiBold)
    label.setFont(font)
    label.setStyleSheet(f"color: {color or TEXT}; background: transparent;")
    return label


class MetricLine(QWidget):
    """小组件里的一行：彩色小点 + 主文字 + 右边灰色小字。

    带 `url` 的行是**超链接**：手型光标、主题色、悬停加下划线，点一下打开。
    """

    def __init__(self, title: str, meta: str = "", tone: str = "",
                 parent=None):
        super().__init__(parent)
        self._url = ""
        self._on_open = None
        self._title = title
        self._meta_active = False
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(7)
        if tone:
            _soft, ink = tone_colors(tone)
            dot = QLabel("●")
            dot.setStyleSheet(
                f"color: {ink.name()}; font-size: 7px; background: transparent;")
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        self.label = QLabel(title)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(
            f"color: {TEXT}; font-size: 10.5pt; background: transparent;")
        row.addWidget(self.label, 1)
        self.meta = QLabel(meta)
        self.meta.setStyleSheet(
            f"color: {MUTED}; font-size: 9pt; background: transparent;")
        self.meta.setAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.meta, 0, Qt.AlignmentFlag.AlignTop)
        self.meta.setVisible(bool(meta))

    # -------------------------------------------------- 右边那行小字
    def set_meta(self, text: str, active: bool = False) -> "MetricLine":
        """改右边的小字；`active` 用来标「进行中」这种状态（主题色 + 加粗）。"""
        self.meta.setText(str(text or ""))
        self._meta_active = bool(active)
        self.meta.setVisible(bool(text))
        self._paint_meta()
        return self

    def _paint_meta(self) -> None:
        if self._meta_active:
            accent = theme.appearance().accent
            self.meta.setStyleSheet(
                f"color: {accent}; font-size: 9pt; font-weight: 600;"
                f" background: transparent;")
        else:
            self.meta.setStyleSheet(
                f"color: {MUTED}; font-size: 9pt; background: transparent;")

    # -------------------------------------------------- 超链接
    def set_link(self, url: str, on_open=None) -> "MetricLine":
        """把这一行变成超链接（手型光标 + 主题色 + 悬停下划线）。"""
        self._url = str(url or "").strip()
        self._on_open = on_open
        if not self._url:
            return self
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点一下打开：%s" % self._url)
        self._paint_link()
        return self

    def url(self) -> str:
        return self._url

    def _paint_link(self) -> None:
        accent = theme.appearance().accent
        self.label.setStyleSheet(
            f"color: {accent}; font-size: 10.5pt; background: transparent;"
            " text-decoration: underline;")

    def mouseReleaseEvent(self, event):
        try:
            if self._url and event.button() == Qt.MouseButton.LeftButton:
                if callable(self._on_open):
                    self._on_open(self._url)
                else:
                    from .utils import open_external

                    open_external(self._url)
                return
        except Exception:
            pass
        super().mouseReleaseEvent(event)

    def on_theme_changed(self) -> None:
        if self._url:
            self._paint_link()
        else:
            self.label.setStyleSheet(
                f"color: {TEXT}; font-size: 10.5pt; background: transparent;")
        self._paint_meta()


class WidgetCard(Card):
    """Apple 风格的小组件卡片。

    结构：头部（徽标 + 小标题 + 右侧小字）→ 内容区（调用方自由放东西）。
    内容区末尾留了一根弹簧，`add_widget()` 会插在弹簧**前面**，
    所以里面的东西永远从上往下贴，不会被顶到底部。
    """

    def __init__(self, title: str, glyph: str = "", tone: str = "teal",
                 meta: str = "", min_height: int = 0, parent=None):
        super().__init__(parent, radius=22, fill=CARD, border=BORDER_SOFT,
                         hoverable=True, hover_fill=CARD_HOVER, padding=16,
                         spacing=8)
        self._tone = tone
        head = QHBoxLayout()
        head.setSpacing(9)
        self.badge = IconBadge(glyph, tone, 26)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 9.5pt; font-weight: 600;"
            f" background: transparent;")
        head.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self.meta_label = QLabel(meta)
        self.meta_label.setStyleSheet(
            f"color: {MUTED}; font-size: 8.5pt; background: transparent;")
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.meta_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.header = head
        self.body.addLayout(head)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(4)
        self.body.addWidget(self.content, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        if min_height:
            self.setMinimumHeight(min_height)
        self.content_layout.addStretch(1)

    # -------------------------------------------------- 头部
    def set_meta(self, text: str) -> None:
        self.meta_label.setText(text)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def on_theme_changed(self) -> None:
        super().on_theme_changed()
        self.title_label.setStyleSheet(
            f"color: {TEXT_SOFT}; font-size: 9.5pt; font-weight: 600;"
            f" background: transparent;")
        self.meta_label.setStyleSheet(
            f"color: {MUTED}; font-size: 8.5pt; background: transparent;")
        self.badge.on_theme_changed()

    # -------------------------------------------------- 内容
    def add_widget(self, widget: QWidget,
                   align: Qt.AlignmentFlag = Qt.AlignmentFlag(0)) -> QWidget:
        index = max(0, self.content_layout.count() - 1)
        if align:
            self.content_layout.insertWidget(index, widget, 0, align)
        else:
            self.content_layout.insertWidget(index, widget)
        return widget

    def add_stretch(self) -> None:
        self.content_layout.addStretch(1)

    def clear_content(self) -> None:
        while self.content_layout.count() > 1:
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()


class HeroWidget(WidgetCard):
    """带「大号数值 + 右侧两行说明」的组件（今天 / 天气就是这样）。"""

    def __init__(self, title: str, glyph: str = "", tone: str = "teal",
                 meta: str = "", parent=None):
        super().__init__(title, glyph, tone, meta, MEDIUM_HEIGHT, parent)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.value_label = big_label("", 30.0)
        row.addWidget(self.value_label, 0, Qt.AlignmentFlag.AlignBottom)
        column = QVBoxLayout()
        column.setSpacing(1)
        self.sub_label = QLabel("")
        self.sub_label.setStyleSheet(
            f"color: {TEXT}; font-size: 11pt; background: transparent;")
        self.sub_label.setWordWrap(True)
        column.addWidget(self.sub_label)
        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet(
            f"color: {MUTED}; font-size: 9pt; background: transparent;")
        self.hint_label.setWordWrap(True)
        column.addWidget(self.hint_label)
        column.addStretch(1)
        row.addLayout(column, 1)
        host = QWidget()
        inner = QVBoxLayout(host)
        inner.setContentsMargins(0, 2, 0, 0)
        inner.addLayout(row)
        self.add_widget(host)
        self.footer = QLabel("")
        self.footer.setWordWrap(True)
        self.footer.setStyleSheet(
            f"color: {MUTED}; font-size: 9.5pt; background: transparent;")
        self.add_widget(self.footer)

    def on_theme_changed(self) -> None:
        super().on_theme_changed()
        self.value_label.setStyleSheet(
            f"color: {TEXT}; background: transparent;")
        self.sub_label.setStyleSheet(
            f"color: {TEXT}; font-size: 11pt; background: transparent;")
        self.hint_label.setStyleSheet(
            f"color: {MUTED}; font-size: 9pt; background: transparent;")
        self.footer.setStyleSheet(
            f"color: {MUTED}; font-size: 9.5pt; background: transparent;")


class ListWidget(WidgetCard):
    """一列条目的小组件（即将到来 / 教务信息 / 最近作业）。"""

    def __init__(self, title: str, glyph: str = "", tone: str = "teal",
                 meta: str = "", min_height: int = SMALL_HEIGHT, parent=None):
        super().__init__(title, glyph, tone, meta, min_height, parent)
        self.rows = QWidget()
        self.rows_layout = QVBoxLayout(self.rows)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(3)
        self.add_widget(self.rows)

    def clear_rows(self) -> None:
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def add_row(self, title: str, meta: str = "", tone: str = "",
                url: str = "", on_open=None,
                meta_active: bool = False) -> MetricLine:
        line = MetricLine(title, meta, tone)
        if meta_active:
            line.set_meta(meta, active=True)
        if url:
            line.set_link(url, on_open)
        self.rows_layout.addWidget(line)
        return line

    def add_hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color: {MUTED}; font-size: 9pt; background: transparent;")
        self.rows_layout.addWidget(label)
        return label


class SectionLabel(QLabel):
    """分组标题（「今天」「ehall 数据」这种）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        font = QFont(self.font())
        font.setPointSizeF(10.0)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self.setStyleSheet(f"color: {MUTED}; background: transparent;")


class SectionRow(QWidget):
    """一行分组标题：左边名字，右边一句灰色说明。"""

    def __init__(self, title: str, hint: str = "", parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 4, 2, 0)
        row.setSpacing(8)
        self.title = SectionLabel(title)
        row.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        self.hint = QLabel(hint)
        self.hint.setStyleSheet(
            f"color: {MUTED}; font-size: 9pt; background: transparent;")
        row.addWidget(self.hint, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)

    def on_theme_changed(self) -> None:
        self.title.setStyleSheet(f"color: {MUTED}; background: transparent;")
        self.hint.setStyleSheet(
            f"color: {MUTED}; font-size: 9pt; background: transparent;")
