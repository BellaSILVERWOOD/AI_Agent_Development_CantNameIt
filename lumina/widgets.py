"""通用 UI 组件：圆角卡片、按钮、开关、弹窗、提示条等。"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
    QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QBrush, QColor, QFont, QFontMetrics,
    QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QRadialGradient,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea, QComboBox, QDialog, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QVBoxLayout, QWidget,
)

from PySide6.QtWidgets import QApplication

from . import icons, theme
from .theme import (
    ACCENT, ACCENT2, ACCENT_DEEP, BORDER, BORDER_SOFT, CARD, CARD_ALT, CARD_HOVER,
    DANGER, FAINT, INPUT_BG, MUTED, SUCCESS, TEXT, TEXT_SOFT, WARNING,
)
from .utils import ext_badge, human_size


# ---------------------------------------------------------------------- 基础绘制
# 图标整体放大 12% 并默认 DemiBold：Fluent 图标在灰度渲染下偏细，看起来发飘
ICON_SCALE = 1.12


def icon_font(size: int = 13, weight: QFont.Weight = QFont.Weight.DemiBold) -> QFont:
    font = QFont(icons.icon_family())
    font.setPixelSize(max(7, int(round(size * ICON_SCALE + 0.5))))
    font.setWeight(weight)
    font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.NoSubpixelAntialias
    )
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _mix(color: QColor, other: QColor, ratio: float) -> QColor:
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        int(color.red() + (other.red() - color.red()) * ratio),
        int(color.green() + (other.green() - color.green()) * ratio),
        int(color.blue() + (other.blue() - color.blue()) * ratio),
        int(color.alpha() + (other.alpha() - color.alpha()) * ratio),
    )


def clear_layout(layout) -> None:
    """删除布局中的所有子项（含嵌套布局）。"""
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child = item.layout()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif child is not None:
            clear_layout(child)


class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(BORDER_SOFT))


class ShadowPanel(QWidget):
    """绘制柔和投影、圆角渐变底与彩色光晕的面板。"""

    def __init__(self, parent=None, radius: float = 20, shadow: int = 16, glow: bool = True,
                 flat: bool = False):
        super().__init__(parent)
        self._radius = radius
        self._shadow = shadow
        self._glow = glow
        self._flat = flat
        self._backdrop = None

    def set_backdrop(self, pixmap) -> None:
        """设置磨砂背景（已模糊的桌面截图）。"""
        self._backdrop = pixmap
        self.update()

    def clear_backdrop(self) -> None:
        self._backdrop = None
        self.update()

    def configure(self, radius: Optional[float] = None, shadow: Optional[int] = None,
                  glow: Optional[bool] = None, flat: Optional[bool] = None) -> None:
        if radius is not None:
            self._radius = float(radius)
        if shadow is not None:
            self._shadow = int(shadow)
        if glow is not None:
            self._glow = bool(glow)
        if flat is not None:
            self._flat = bool(flat)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        shadow = self._shadow
        rect = QRectF(self.rect()).adjusted(shadow, shadow, -shadow, -shadow)
        if rect.width() <= 4 or rect.height() <= 4:
            return

        if shadow > 0:
            for i in range(shadow, 0, -1):
                grow = i * 1.15
                path = rounded_path(
                    rect.adjusted(-grow, -grow + 5, grow, grow + 5), self._radius + grow
                )
                alpha = int(3 + (shadow - i) * 1.05)
                painter.fillPath(path, QColor(24, 30, 22, alpha))

        path = rounded_path(rect, self._radius)
        state = theme.appearance()

        # 磨砂背景：先铺一层「模糊后的桌面」，再叠上半透明主题色
        has_backdrop = self._backdrop is not None and not self._backdrop.isNull()
        if has_backdrop:
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(rect.toRect(), self._backdrop)
            painter.restore()

        top, mid, bottom = state.bg_colors()
        tint = state.shell_alpha()
        alpha = int(round(255 * tint))
        if self._flat:
            # 弹窗 / 提醒用纯平底色（和主窗口同一套表面色），不再拉渐变
            flat = QColor(mid)
            flat.setAlpha(alpha)
            painter.fillPath(path, flat)
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            top_color = QColor(top)
            top_color.setAlpha(alpha)
            mid_color = QColor(mid)
            mid_color.setAlpha(alpha)
            bottom_color = QColor(bottom)
            bottom_color.setAlpha(alpha)
            gradient.setColorAt(0.0, top_color)
            gradient.setColorAt(0.55, mid_color)
            gradient.setColorAt(1.0, bottom_color)
            painter.fillPath(path, QBrush(gradient))

        painter.save()
        painter.setClipPath(path)
        if self._glow:
            accent = state.accent_q(0.10)
            accent_mid = state.accent_q(0.035)
            accent_none = state.accent_q(0.0)
            glow1 = QRadialGradient(
                QPointF(rect.right() - rect.width() * 0.12, rect.top() + rect.height() * 0.06),
                rect.width() * 0.75,
            )
            glow1.setColorAt(0.0, accent)
            glow1.setColorAt(0.55, accent_mid)
            glow1.setColorAt(1.0, accent_none)
            painter.fillRect(rect, QBrush(glow1))

            helper = state.accent2_q(0.07)
            helper_mid = state.accent2_q(0.02)
            helper_none = state.accent2_q(0.0)
            glow2 = QRadialGradient(
                QPointF(rect.left() + rect.width() * 0.18, rect.bottom() + rect.height() * 0.05),
                rect.width() * 0.6,
            )
            glow2.setColorAt(0.0, helper)
            glow2.setColorAt(0.6, helper_mid)
            glow2.setColorAt(1.0, helper_none)
            painter.fillRect(rect, QBrush(glow2))

        # 顶部高光已去掉：亮色描边在磨砂面板上显得突兀
        painter.restore()

        border = QLinearGradient(rect.topLeft(), rect.bottomRight())
        border.setColorAt(0.0, theme.hover_wash(0.13))
        border.setColorAt(0.5, theme.hover_wash(0.08))
        border.setColorAt(1.0, theme.hover_wash(0.11))
        painter.setPen(QPen(QBrush(border), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


_CARD_COLOR_TOKENS = ("CARD", "CARD_ALT", "CARD_HOVER", "INPUT_BG", "BORDER",
                      "BORDER_SOFT")


def _card_color_kind(value: Optional[str]) -> str:
    """把传进来的颜色对应成一个主题令牌名。

    传 None → ""（用卡片底色 card_hex）；传的就是 CARD / BORDER 这类令牌的
    当前值 → 记住令牌名，换主题时照着新值重算；别的颜色 → "custom"。
    这样 `Card(fill=CARD)` 在浅色 / 深色之间切换才真的会变色
    （以前是在构造时把颜色抄死，换主题后日历页一整块还是黑的）。
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    for name in _CARD_COLOR_TOKENS:
        if text == str(getattr(theme, name, "") or "").lower():
            return name
    return "custom"


def _card_color(kind: str, raw: str, fallback) -> str:
    if kind == "":
        return fallback() if callable(fallback) else str(fallback)
    if kind == "custom":
        return raw
    value = getattr(theme, kind, None)
    return str(value or raw)


class Card(QFrame):
    """圆角卡片，可带悬停效果与左侧色条。"""

    def __init__(
        self,
        parent=None,
        radius: float = 16,
        fill: Optional[str] = None,
        border: Optional[str] = None,
        hoverable: bool = False,
        hover_fill: Optional[str] = None,
        bar: Optional[QColor] = None,
        padding: int = 16,
        spacing: int = 8,
    ):
        super().__init__(parent)
        state = theme.appearance()
        self._radius = radius
        self._fill_kind = _card_color_kind(fill)
        self._fill_raw = str(fill or "")
        self._border_kind = _card_color_kind(border)
        self._border_raw = str(border or "")
        self._hover_kind = _card_color_kind(hover_fill) or "CARD_HOVER"
        self._hover_raw = str(hover_fill or "")
        self._fill = QColor(_card_color(self._fill_kind, self._fill_raw,
                                        state.card_hex))
        self._border = QColor(_card_color(self._border_kind, self._border_raw,
                                          state.border_hex))
        self._hoverable = hoverable
        self._hover_fill = QColor(_card_color(self._hover_kind, self._hover_raw,
                                              lambda: theme.CARD_HOVER))
        self._bar = bar
        self._hover = False
        self._theme_rev = theme.revision()
        if hoverable:
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)

    # -------------------------------------------------- 主题
    def on_theme_changed(self) -> None:
        """换主题：把构造时抄下来的卡片配色换成新主题的。"""
        state = theme.appearance()
        self._theme_rev = theme.revision()
        self._fill = QColor(_card_color(self._fill_kind, self._fill_raw,
                                        state.card_hex))
        self._border = QColor(_card_color(self._border_kind, self._border_raw,
                                          state.border_hex))
        self._hover_fill = QColor(_card_color(self._hover_kind, self._hover_raw,
                                              lambda: theme.CARD_HOVER))
        self.update()


    def set_bar(self, color: Optional[QColor]) -> None:
        self._bar = color
        self.update()

    def enterEvent(self, event):
        if self._hoverable:
            self._hover = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._hoverable:
            self._hover = False
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        if getattr(self, "_theme_rev", None) != theme.revision():
            # 主题换过了：先把配色换成新的再画（换主题时被销毁 / 后创建的卡片也靠这里）
            self.on_theme_changed()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded_path(rect, self._radius)
        painter.fillPath(path, self._hover_fill if self._hover else self._fill)
        if self._bar is not None:
            painter.save()
            painter.setClipPath(path)
            bar_rect = QRectF(rect.left(), rect.top(), 4.0, rect.height())
            gradient = QLinearGradient(bar_rect.topLeft(), bar_rect.bottomLeft())
            color = QColor(self._bar)
            gradient.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 240))
            gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 90))
            painter.fillRect(bar_rect, QBrush(gradient))
            painter.restore()
        painter.setPen(QPen(self._border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


class IconBadge(QWidget):
    """圆角方块图标徽标。"""

    def __init__(
        self,
        glyph: str,
        size: int = 34,
        color: str = ACCENT2,
        bg_alpha: float = 0.12,
        radius: float = 10,
        glyph_size: Optional[int] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._glyph = glyph
        self._color = QColor(color)
        self._bg_alpha = bg_alpha
        self._radius = radius
        self._glyph_size = glyph_size or int(size * 0.4)
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = QColor(self._color)
        fill.setAlphaF(self._bg_alpha)
        painter.fillPath(rounded_path(rect, self._radius), fill)
        border = QColor(self._color)
        border.setAlphaF(min(1.0, self._bg_alpha + 0.14))
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(rounded_path(rect, self._radius))
        painter.setFont(icon_font(self._glyph_size))
        painter.setPen(self._color)
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._glyph)


class ElidedLabel(QLabel):
    """超出宽度时自动显示省略号的 QLabel。"""

    def __init__(self, text: str = "", mode=Qt.TextElideMode.ElideRight, parent=None):
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = "" if text is None else str(text)
        self.setToolTip(self._full)
        self._apply()

    def full_text(self) -> str:
        return self._full

    def _apply(self) -> None:
        width = max(0, self.contentsRect().width() - 2)
        if width <= 4:
            super().setText(self._full)
            return
        elided = self.fontMetrics().elidedText(self._full, self._mode, width)
        super().setText(elided)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()


# ---------------------------------------------------------------------- 按钮
class SoftButton(QPushButton):
    """统一风格的按钮：primary / ghost / subtle / danger / success。"""

    KINDS = ("primary", "ghost", "subtle", "danger", "success")

    def __init__(
        self,
        text: str = "",
        kind: str = "primary",
        glyph: Optional[str] = None,
        parent=None,
        height: int = 36,
        radius: float = 11,
        min_width: int = 0,
        point_size: float = 9.6,
    ):
        super().__init__(text, parent)
        self._kind = kind if kind in self.KINDS else "primary"
        self._glyph = glyph
        self._radius = radius
        self._point_size = point_size
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(height)
        if min_width:
            self.setMinimumWidth(min_width)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def setKind(self, kind: str) -> None:
        """切换按钮风格（primary / ghost / subtle …）。"""
        if kind in self.KINDS and kind != self._kind:
            self._kind = kind
            self.update()

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(self._point_size)
        font.setWeight(
            QFont.Weight.DemiBold if self._kind == "primary" else QFont.Weight.Medium
        )
        return font

    def sizeHint(self) -> QSize:
        font = self._font()
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(self.text())
        if self._glyph:
            width += 16 + 7
        width = max(width + 32, self.minimumWidth())
        return QSize(width, self.height())

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.updateGeometry()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(self._radius, rect.height() / 2)
        path = rounded_path(rect, radius)
        enabled = self.isEnabled()
        kind = self._kind

        if not enabled:
            painter.fillPath(path, QColor(CARD_ALT))
            text_color = QColor(FAINT)
            border = QColor(BORDER)
        elif kind == "primary":
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, theme.appearance().accent_deep_q())
            gradient.setColorAt(1.0, theme.appearance().accent_q())
            painter.fillPath(path, QBrush(gradient))
            if self._hover:
                painter.fillPath(path, QColor(255, 255, 255, 34))
            text_color = theme.appearance().on_accent_q()
            border = QColor(0, 0, 0, 18)
        elif kind == "ghost":
            painter.fillPath(path, QColor(CARD if self._hover else CARD_ALT))
            text_color = QColor(TEXT if self._hover else TEXT_SOFT)
            border = QColor(BORDER if self._hover else BORDER_SOFT)
        elif kind == "subtle":
            painter.fillPath(path, QColor(0, 0, 0, 12 if self._hover else 0))
            text_color = QColor(TEXT if self._hover else MUTED)
            border = QColor(0, 0, 0, 0)
        elif kind == "danger":
            painter.fillPath(path, QColor(theme.soft(DANGER, 0.18 if self._hover else 0.10)))
            text_color = QColor(DANGER)
            border = theme.mix(QColor(CARD), QColor(DANGER), 0.30)
        else:  # success
            painter.fillPath(path, QColor(theme.soft(SUCCESS, 0.20 if self._hover else 0.12)))
            text_color = QColor(SUCCESS)
            border = theme.mix(QColor(CARD), QColor(SUCCESS), 0.28)

        if border.alpha() > 0:
            painter.setPen(QPen(border, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        font = self._font()
        painter.setFont(font)
        painter.setPen(text_color)
        metrics = QFontMetrics(font)
        text = self.text()
        icon_width = 16 if self._glyph else 0
        gap = 7 if self._glyph else 0
        content = icon_width + gap + metrics.horizontalAdvance(text)
        start = (rect.width() - content) / 2 + rect.left()
        if self._glyph:
            painter.setFont(icon_font(int(self._point_size * 1.45)))
            painter.drawText(
                QRectF(start, rect.top(), icon_width, rect.height()),
                int(Qt.AlignmentFlag.AlignCenter),
                self._glyph,
            )
            start += icon_width + gap
        painter.setFont(font)
        painter.drawText(
            QRectF(start, rect.top(), metrics.horizontalAdvance(text) + 2, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )


class IconButton(QPushButton):
    """只有图标的小按钮。"""

    def __init__(
        self,
        glyph: str,
        parent=None,
        size: int = 30,
        glyph_size: int = 12,
        color: str = MUTED,
        hover_color: str = TEXT,
        hover_bg: Optional[str] = None,
        radius: float = 8,
        tip: str = "",
    ):
        super().__init__(parent)
        self._glyph = glyph
        self._glyph_size = glyph_size
        self._color = QColor(color)
        self._hover_color = QColor(hover_color)
        self._hover_bg = QColor(hover_bg) if hover_bg else QColor(0, 0, 0, 0)
        self._radius = radius
        self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tip:
            self.setToolTip(tip)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_colors(self, color: str, hover_color: Optional[str] = None, hover_bg: Optional[str] = None):
        self._color = QColor(color)
        if hover_color:
            self._hover_color = QColor(hover_color)
        if hover_bg:
            self._hover_bg = QColor(hover_bg) if hover_bg else QColor(0, 0, 0, 0)
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._hover and self.isEnabled():
            wash = QColor(self._hover_bg)
            if wash.alpha() == 0:
                wash = theme.hover_wash(0.07)
            painter.fillPath(rounded_path(rect, self._radius), wash)
        color = self._hover_color if (self._hover and self.isEnabled()) else self._color
        if not self.isEnabled():
            color = QColor(FAINT)
        painter.setFont(icon_font(self._glyph_size))
        painter.setPen(color)
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._glyph)


class WindowButton(QPushButton):
    """标题栏最小化 / 最大化 / 关闭按钮。"""

    def __init__(self, glyph: str, danger: bool = False, parent=None, tip: str = ""):
        super().__init__(parent)
        self._glyph = glyph
        self._danger = danger
        self._hover = False
        self.setFixedSize(38, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tip:
            self.setToolTip(tip)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._hover:
            bg = QColor(DANGER) if self._danger else theme.hover_wash(0.06)
            painter.fillPath(rounded_path(rect, 7), bg)
        color = QColor("#FFFFFF") if (self._hover and self._danger) else QColor(MUTED)
        painter.setFont(icon_font(10))
        painter.setPen(color)
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._glyph)


class ToggleSwitch(QWidget):
    """带滑动动画的开关。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = True, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._value = 1.0 if checked else 0.0
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, value) -> None:
        self._value = float(value)
        self.update()

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setChecked(self, checked: bool, animate: bool = True) -> None:  # noqa: N802
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._anim.stop()
        if animate:
            self._anim.setStartValue(self._value)
            self._anim.setEndValue(1.0 if checked else 0.0)
            self._anim.start()
        else:
            self._value = 1.0 if checked else 0.0
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 轨道留出 2px 边距，圆钮尺寸由轨道高度推导，保证两端都不越界
        track = QRectF(2, 3, self.width() - 4, self.height() - 6)
        radius = track.height() / 2
        if self._value > 0.01:
            start = theme.appearance().accent_deep_q()
            end = theme.appearance().accent_light_q()
            mix = _mix(start, end, self._value)
            painter.fillPath(rounded_path(track, radius), mix)
        if self._value < 0.99:
            painter.fillPath(
                rounded_path(track, radius), QColor(221, 227, 215, int(255 * (1 - self._value)))
            )

        pad = 2.0
        knob_size = max(6.0, track.height() - 2 * pad)
        travel = max(0.0, track.width() - knob_size - 2 * pad)
        x = track.left() + pad + self._value * travel
        knob = QRectF(x, track.top() + pad, knob_size, knob_size)
        painter.setPen(Qt.PenStyle.NoPen)
        shadow_color = QColor(30, 40, 28, 46)
        painter.setBrush(shadow_color)
        painter.drawEllipse(knob.adjusted(0.0, 1.0, 0.0, 1.0))
        painter.setBrush(QColor(CARD))
        painter.drawEllipse(knob)


class RoundCheck(QWidget):
    """圆形勾选按钮（用于日程完成状态、任务勾选）。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, size: int = 20, parent=None, tip: str = ""):
        super().__init__(parent)
        self._checked = bool(checked)
        self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tip:
            self.setToolTip(tip)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setChecked(self, checked: bool, emit: bool = False) -> None:  # noqa: N802
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self.update()
        if emit:
            self.toggled.emit(checked)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        # 必须自己接住 press：否则 QWidget 默认忽略事件，点击会冒泡到父控件
        # （日程卡片就会收到这一次点击，弹出编辑窗口）
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        pressed = getattr(self, "_pressed", False)
        self._pressed = False
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            if pressed:
                self.setChecked(not self._checked, emit=True)
            event.accept()
            self.update()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.2, 1.2, -1.2, -1.2)
        if self._checked:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, theme.appearance().accent_deep_q())
            gradient.setColorAt(1.0, theme.appearance().accent_light_q())
            painter.fillPath(rounded_path(rect, rect.width() / 2), QBrush(gradient))
            painter.setFont(icon_font(int(rect.width() * 0.5), QFont.Weight.Bold))
            painter.setPen(theme.appearance().on_accent_q())
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), icons.CHECK)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen_color = QColor(FAINT) if self._hover else QColor(BORDER)
            painter.setPen(QPen(pen_color, 1.6))
            painter.drawEllipse(rect)


class NavButton(QWidget):
    """侧边栏导航项。"""

    clicked = Signal()

    def __init__(self, glyph: str, text: str, parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self._text = text
        self._active = False
        self._hover = False
        self._badge = 0
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self.update()

    def set_badge(self, count: int) -> None:
        self._badge = max(0, int(count))
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
        path = rounded_path(rect, 10)
        state = theme.appearance()
        if self._active:
            painter.fillPath(path, QColor(CARD))
            painter.setPen(QPen(QColor(BORDER), 1.0))
            painter.drawPath(path)
            bar = QRectF(rect.left() + 3, rect.center().y() - 8, 3, 16)
            painter.fillPath(rounded_path(bar, 1.5), state.accent_q())
        elif self._hover:
            painter.fillPath(path, theme.hover_wash(0.06))

        glyph_color = state.accent_q() if self._active else QColor(MUTED)
        painter.setFont(icon_font(13))
        painter.setPen(glyph_color)
        painter.drawText(
            QRectF(13, rect.top(), 20, rect.height()),
            int(Qt.AlignmentFlag.AlignCenter),
            self._glyph,
        )

        text_font = QFont(self.font())
        text_font.setPointSizeF(9.6)
        if self._active:
            text_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(text_font)
        painter.setPen(QColor(TEXT) if self._active else QColor(TEXT_SOFT))
        badge_space = 34 if self._badge else 8
        painter.drawText(
            QRectF(42, rect.top(), rect.width() - 42 - badge_space, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )

        if self._badge:
            badge_font = QFont(self.font())
            badge_font.setPointSizeF(7.6)
            badge_font.setWeight(QFont.Weight.DemiBold)
            metrics = QFontMetrics(badge_font)
            label = str(self._badge) if self._badge < 100 else "99+"
            width = max(18, metrics.horizontalAdvance(label) + 10)
            badge = QRectF(rect.right() - width - 9, rect.center().y() - 9, width, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.hover_wash(0.08))
            painter.drawPath(rounded_path(badge, 9))
            painter.setFont(badge_font)
            painter.setPen(QColor(TEXT_SOFT))
            painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), label)


class Pill(QWidget):
    """小标签。"""

    def __init__(
        self,
        text: str,
        fg: str = "",
        bg: str = "",
        parent=None,
        point_size: float = 8.0,
        padding: int = 9,
        height: int = 21,
    ):
        super().__init__(parent)
        self._text = text
        self._fg = QColor(fg)
        self._bg = QColor(bg)
        self._point_size = point_size
        self._padding = padding
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_text(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def set_colors(self, fg: str, bg: str) -> None:
        self._fg = QColor(fg)
        self._bg = QColor(bg)
        self.update()

    def sizeHint(self) -> QSize:
        font = QFont(self.font())
        font.setPointSizeF(self._point_size)
        metrics = QFontMetrics(font)
        return QSize(metrics.horizontalAdvance(self._text) + self._padding * 2, self.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 内缩 0.5px：抗锯齿的边缘正好落在控件边界上会被裁掉，看起来像被切了一刀
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = self._bg
        if not fill.isValid() or fill.alpha() == 0:
            fill = QColor(theme.CARD_ALT)
        painter.fillPath(rounded_path(rect, rect.height() / 2), fill)
        font = QFont(self.font())
        font.setPointSizeF(self._point_size)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        ink = self._fg
        if not ink.isValid() or ink.alpha() == 0:
            ink = QColor(MUTED)
        painter.setPen(ink)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self._text)



class EventDetailPane(Card):
    """中栏单独显示一张日程详情卡片（两个侧栏都关掉时用）。"""

    edit_requested = Signal(int)
    closed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent, radius=16, fill=CARD, border=BORDER, padding=18,
                         spacing=10)
        self.store = store
        self._event = {}

        head = QHBoxLayout()
        head.setSpacing(6)
        tag = QLabel("日程详情")
        tag.setObjectName("Tiny")
        head.addWidget(tag)
        head.addStretch(1)
        self.edit_button = IconButton(icons.EDIT, size=28, glyph_size=12,
                                      tip="编辑这条日程", color=MUTED,
                                      hover_color=TEXT)
        self.edit_button.clicked.connect(self._edit)
        head.addWidget(self.edit_button)
        close_button = IconButton(icons.CLOSE, size=28, glyph_size=12,
                                  tip="关闭这张卡片")
        close_button.clicked.connect(self.closed.emit)
        head.addWidget(close_button)
        self.body.addLayout(head)

        self.title = QLabel("")
        self.title.setWordWrap(True)
        title_font = QFont(self.font())
        title_font.setPixelSize(20)
        title_font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(title_font)
        self.title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self.body.addWidget(self.title)

        self.when = QLabel("")
        self.when.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.body.addWidget(self.when)

        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet(f"color: {TEXT_SOFT}; font-size: 13px;")
        self.body.addWidget(self.desc)
        self.body.addStretch(1)

    def set_event(self, event) -> None:
        self._event = dict(event or {})
        self.title.setText(str(self._event.get("title") or "（无标题）"))
        start = str(self._event.get("start_time") or "")[:5]
        end = str(self._event.get("end_time") or "")[:5]
        when = str(self._event.get("date") or "")
        if start:
            when += f"  {start}-{end}" if end else f"  {start}"
        self.when.setText(when)
        description = str(self._event.get("description") or "").strip()
        self.desc.setText(description or "（没有更多说明）")

    def _edit(self) -> None:
        if self._event:
            self.edit_requested.emit(int(self._event.get("id") or 0))

class ExtBadge(QWidget):
    """文件类型彩色角标。"""

    def __init__(self, name: str, size: int = 38, parent=None):
        super().__init__(parent)
        self._text, self._color = ext_badge(name)
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        color = QColor(self._color)
        bg = QColor(color)
        bg.setAlphaF(0.16)
        painter.fillPath(rounded_path(rect, 9), bg)
        border = QColor(color)
        border.setAlphaF(0.35)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(rounded_path(rect, 9))
        font = QFont(self.font())
        font.setPointSizeF(max(6.2, rect.width() * 0.22))
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._text)


# ---------------------------------------------------------------------- 输入
class SelectPopup(QWidget):
    """自绘的下拉列表：圆角、靠卡片色，不用系统白色弹窗。

    列表长了要能滚：以前弹窗按「项数 × 行高」定高，教学楼有 50 多个选项时
    直接高到屏幕外面去（用户报「复选框太高了，直接出屏幕」）。现在最多显示
    10 行，其余用滚轮 / 拖右侧滚动条看。
    """

    picked = Signal(int)

    ROW_HEIGHT = 30
    PAD = 7          # 四周留白，给投影用；否则投影被窗口裁掉，角上会出现缺一块的黑边
    MAX_ROWS = 10    # 一次最多显示这么多行，多的滚

    def __init__(self, parent=None, current: int = -1, items=None):
        super().__init__(None, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._items = list(items or [])
        self._current = int(current)
        self._hover = -1
        self._rows = max(1, min(len(self._items) or 1, self.MAX_ROWS))
        self._first = 0
        if 0 <= self._current < len(self._items):
            # 当前项尽量出现在可见范围中间
            self._first = max(0, min(self._current - self._rows // 2,
                                     max(0, len(self._items) - self._rows)))
        self._drag = 0.0
        # 弹出时那次鼠标释放会被 Popup 抢到，先记时间，忽略掉它，否则一松手就关
        self._opened_at = time.monotonic()
        metrics = QFontMetrics(self.font())
        width = max((metrics.horizontalAdvance(text) for text in self._items),
                    default=80)
        self.setFixedSize(max(150, width + 40) + 2 * self.PAD + 8,
                          self._rows * self.ROW_HEIGHT + 12 + 2 * self.PAD)

    # -------------------------------------------------- 滚动
    def _limit(self) -> int:
        return max(0, len(self._items) - self._rows)

    def _scroll_by(self, step: int) -> None:
        target = max(0, min(self._limit(), self._first + int(step)))
        if target != self._first:
            self._first = target
            self.update()

    def _track_rect(self) -> QRectF:
        pad = self.PAD
        return QRectF(self.width() - pad - 7, pad + 6, 4,
                      self.height() - 2 * pad - 12)

    def _bar_rect(self) -> QRectF:
        track = self._track_rect()
        if self._limit() <= 0:
            return QRectF()
        ratio = self._rows / float(len(self._items))
        height = max(18.0, track.height() * ratio)
        offset = (track.height() - height) * (self._first / float(self._limit()))
        return QRectF(track.left(), track.top() + offset, track.width(), height)

    def wheelEvent(self, event):
        self._scroll_by(-3 if event.angleDelta().y() > 0 else 3)
        event.accept()

    def _index_at(self, y: float) -> int:
        row = int((y - self.PAD - 6) // self.ROW_HEIGHT)
        if 0 <= row < self._rows:
            index = self._first + row
            if 0 <= index < len(self._items):
                return index
        return -1

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pad = self.PAD
        rect = QRectF(self.rect()).adjusted(pad, pad, -pad, -pad)
        # 投影完整画在留白里，不会被窗口边缘裁掉
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 30, 30))
        painter.drawPath(rounded_path(QRectF(rect).adjusted(0, 2, 0, 3), 10))
        painter.setBrush(QColor(theme.CARD))
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawPath(rounded_path(rect, 10))

        font = QFont(self.font())
        font.setPixelSize(12)
        painter.setFont(font)
        last = min(len(self._items), self._first + self._rows)
        for row, index in enumerate(range(self._first, last)):
            text = self._items[index]
            cell = QRectF(rect.left() + 5, rect.top() + 6 + row * self.ROW_HEIGHT,
                          rect.width() - 14, self.ROW_HEIGHT)
            if index == self._hover:
                painter.setPen(Qt.PenStyle.NoPen)
                wash = QColor(theme.ACCENT)
                wash.setAlphaF(0.14)
                painter.setBrush(wash)
                painter.drawPath(rounded_path(cell, 6))
            elif index == self._current:
                painter.setPen(Qt.PenStyle.NoPen)
                wash = QColor(theme.ACCENT)
                wash.setAlphaF(0.10)
                painter.setBrush(wash)
                painter.drawPath(rounded_path(cell, 6))
            painter.setPen(QPen(QColor(theme.TEXT if index == self._current
                                       else theme.TEXT_SOFT)))
            painter.drawText(cell.adjusted(10, 0, -10, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             text)

        bar = self._bar_rect()
        if not bar.isNull():
            painter.setPen(Qt.PenStyle.NoPen)
            wash = QColor(theme.MUTED)
            wash.setAlphaF(0.35)
            painter.setBrush(wash)
            painter.drawPath(rounded_path(bar, 2))

    def mousePressEvent(self, event):
        bar = self._bar_rect()
        if not bar.isNull() and bar.adjusted(-4, -4, 4, 4).contains(
                event.position()):
            self._drag = event.position().y() - bar.top()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            bar = self._bar_rect()
            track = self._track_rect()
            if not bar.isNull() and self._limit() > 0:
                top = event.position().y() - self._drag
                span = max(1.0, track.height() - bar.height())
                ratio = max(0.0, min(1.0, (top - track.top()) / span))
                self._first = int(round(ratio * self._limit()))
                self.update()
                return
        index = self._index_at(event.position().y())
        if index != self._hover:
            self._hover = index
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if time.monotonic() - self._opened_at < 0.3:
            # 打开下拉框的那一下松手，不算选择
            return
        index = self._index_at(event.position().y())
        if index >= 0:
            self.picked.emit(index)
        self.close()

class Select(QComboBox):
    """带自绘下拉箭头的下拉框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setMaxVisibleItems(14)
        self._caret = QLabel(self)
        self._caret.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._caret.setFont(icon_font(8))
        self._caret.setText(icons.CHEVRON_DOWN)
        self._caret.setStyleSheet("color: #7D877A; background: transparent;")
        self._caret.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._caret.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._caret.setGeometry(self.width() - 28, 0, 22, self.height())

    def showPopup(self) -> None:  # noqa: N802 - Qt 命名
        """用自己画的圆角列表代替系统弹窗。"""
        items = [self.itemText(index) for index in range(self.count())]
        if not items:
            return
        popup = SelectPopup(self, self.currentIndex(), items)
        popup.picked.connect(self._apply_picked)
        popup.picked.connect(lambda _i: popup.close())
        point = self.mapToGlobal(self.rect().bottomLeft())
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = min(point.x(), area.right() - popup.width() - 8)
            y = point.y() + 4
            if y + popup.height() > area.bottom():
                y = self.mapToGlobal(self.rect().topLeft()).y() - popup.height() - 4
            point = point.__class__(int(x), int(y))
        popup.move(point)
        popup.show()
        self._popup = popup

    def _apply_picked(self, index: int) -> None:
        """下拉里点了某一项。

        系统弹窗被换成了自绘的 `SelectPopup`，所以 Qt 不会自己发 `activated`
        （只有原生弹窗才会），这里手动补发一次——不然所有 `activated.connect`
        的处理函数（备忘的字号/样式、其它页面的下拉）都不会被调用。
        """
        if 0 <= int(index) < self.count():
            self.setCurrentIndex(int(index))
            try:
                self.activated[int].emit(int(index))
            except Exception:
                try:
                    self.activated.emit(int(index))
                except Exception:
                    pass


class ClickableLabel(QLabel):
    """能点的文字标签（用量、提示这类）：点一下弹详情。"""

    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class RoundIconButton(QPushButton):
    """实心圆按钮，圆里画一个字符（+、箭头…）。

    * 文字（`text()`）保留，测试和老代码照常用 `setText()` / `text()`；
    * 画的时候只取第一个字符当图标，不画整段文字。
    """

    def __init__(self, glyph: str = "+", size: int = 30, parent=None,
                 fill: str = "", fg: str = "", tip: str = ""):
        super().__init__(parent)
        self.setText(glyph)
        self._size = int(size)
        self._fill = fill
        self._fg = fg
        self._hover = False
        self.setFixedSize(self._size, self._size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        if tip:
            self.setToolTip(tip)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def _colors(self):
        if self._fill:
            fill = QColor(self._fill)
        else:
            # 默认灰色填充：浅色下压暗、深色下提亮，自动跟着主题走
            fill = theme.mix(QColor(theme.CARD), QColor(theme.TEXT), 0.10)
        fg = QColor(self._fg) if self._fg else QColor(theme.TEXT)
        if self._hover and self.isEnabled():
            fill = theme.mix(fill, QColor(theme.TEXT), 0.06)
        if not self.isEnabled():
            fill = theme.mix(QColor(theme.CARD), QColor(theme.TEXT), 0.04)
            fg = QColor(theme.FAINT)
        return fill, fg

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill, fg = self._colors()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(rect)
        glyph = (self.text() or "+")[0]
        font = QFont(self.font())
        font.setPointSizeF(max(9.0, self._size * 0.44))
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(fg)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), glyph)


class RoundSendButton(QPushButton):
    """实心圆形发送按钮：空闲画 ↑，生成时画 ■（停止）。

    `text()` 仍然是「发送 / 停止」—— 老代码和测试都靠它判断状态。
    """

    def __init__(self, size: int = 32, parent=None, tip: str = ""):
        super().__init__(parent)
        self._size = int(size)
        self._running = False
        self._hover = False
        super().setText("发送")
        self.setFixedSize(self._size, self._size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        if tip:
            self.setToolTip(tip)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 命名
        super().setText(text)
        self._running = str(text).strip() == "停止"
        self.update()

    def set_running(self, running: bool) -> None:
        super().setText("停止" if running else "发送")
        self._running = bool(running)
        self.update()

    def is_running(self) -> bool:
        return self._running

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        accent = QColor(theme.appearance().accent)
        fill = accent if self.isEnabled() else theme.mix(QColor(theme.CARD), accent, 0.25)
        if self._hover and self.isEnabled():
            fill = theme.mix(fill, QColor(theme.TEXT), 0.08)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(rect)
        if self._running:
            side = self._size * 0.28
            box = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2,
                         side, side)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(box, 2.5, 2.5)
        else:
            font = QFont(self.font())
            font.setPixelSize(int(self._size * 0.5))
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "\u2191")


class SearchBox(QWidget):
    """带图标的搜索输入框。"""

    textChanged = Signal(str)

    def __init__(self, placeholder: str = "搜索…", parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self._glyph = QLabel(self)
        self._glyph.setFont(icon_font(11))
        self._glyph.setText(icons.SEARCH)
        self._glyph.setStyleSheet("color: #7D877A; background: transparent;")
        self._glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setStyleSheet(
            "QLineEdit { background: transparent; border: none; padding: 0 8px 0 0;"
            " border-radius: 0; }"
        )
        self.edit.textChanged.connect(self.textChanged)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(6)
        layout.addWidget(self._glyph)
        layout.addWidget(self.edit, 1)

    def text(self) -> str:
        return self.edit.text()

    def clear(self) -> None:
        self.edit.clear()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillPath(rounded_path(rect, 10), QColor(theme.appearance().input_hex()))
        painter.setPen(QPen(QColor(theme.appearance().border_hex()), 1.0))
        painter.drawPath(rounded_path(rect, 10))


class FieldBlock(QWidget):
    """标签 + 输入控件。"""

    def __init__(self, label: str, widget: QWidget, hint: str = "", parent=None, stretch: int = 0):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.label = QLabel(label)
        self.label.setObjectName("Small")
        layout.addWidget(self.label)
        layout.addWidget(widget, 1)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("Tiny")
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)


class Banner(QWidget):
    """提示横幅（如未配置 SMTP）。"""

    def __init__(self, glyph: str, title: str, text: str, tone: str = WARNING, parent=None):
        super().__init__(parent)
        self._tone = QColor(tone)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(12)
        self._badge = IconBadge(glyph, size=30, color=tone, bg_alpha=0.16, radius=9, glyph_size=13)
        layout.addWidget(self._badge)
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)
        self._title = QLabel(title)
        self._title.setStyleSheet(f"color: {tone}; font-size: 9.5pt; font-weight: 600;")
        self._text = QLabel(text)
        self._text.setObjectName("Small")
        text_box.addWidget(self._title)
        text_box.addWidget(self._text)
        layout.addLayout(text_box, 1)
        self.action_layout = QHBoxLayout()
        self.action_layout.setSpacing(8)
        layout.addLayout(self.action_layout)

    def add_action(self, widget: QWidget) -> None:
        self.action_layout.addWidget(widget)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = QColor(self._tone)
        fill.setAlphaF(0.09)
        painter.fillPath(rounded_path(rect, 13), fill)
        border = QColor(self._tone)
        border.setAlphaF(0.28)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(rounded_path(rect, 13))


class EmptyState(QWidget):
    """空状态占位。"""

    def __init__(
        self,
        glyph: str,
        title: str,
        subtitle: str,
        parent=None,
        glyph_size: int = 26,
        circle: int = 74,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 24, 10, 24)
        layout.setSpacing(10)
        layout.addStretch(1)
        badge = IconBadge(glyph, size=circle, color=theme.appearance().accent_q().name(),
                          bg_alpha=0.10, radius=circle / 2, glyph_size=glyph_size)
        badge_row = QHBoxLayout()
        badge_row.addStretch(1)
        badge_row.addWidget(badge)
        badge_row.addStretch(1)
        layout.addLayout(badge_row)
        self.title = QLabel(title)
        self.title.setObjectName("H3")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("Muted")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)
        self.action_layout = QHBoxLayout()
        self.action_layout.addStretch(1)
        self.action_layout.addStretch(1)
        layout.addLayout(self.action_layout)
        layout.addStretch(1)

    def add_action(self, widget: QWidget) -> None:
        self.action_layout.insertWidget(1, widget)


class MailBodyView(QWidget):
    """把汇报 HTML 绘制成圆角白色"纸张"的预览控件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = QTextDocument(self)
        self._doc.setDocumentMargin(0)
        self._doc.setDefaultFont(QApplication.font())
        self._doc.setDefaultStyleSheet("body { color: #232A3E; }")
        self._pad = 20
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(80)
        self.setStyleSheet("color: #232A3E;")

    def set_html(self, html: str) -> None:
        self._doc.setHtml(html or "")
        self._reflow()
        self.update()

    def set_plain(self, text: str) -> None:
        self._doc.setPlainText(text or "")
        self._reflow()
        self.update()

    def _reflow(self) -> None:
        width = max(120, self.width() - 2 * self._pad)
        self._doc.setTextWidth(width)
        height = int(self._doc.size().height()) + 2 * self._pad + 2
        if height != self.minimumHeight():
            self.setMinimumHeight(height)
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def sizeHint(self) -> QSize:
        return QSize(420, int(self._doc.size().height()) + 2 * self._pad + 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded_path(rect, 14)
        painter.fillPath(path, QColor(CARD))
        painter.setPen(QPen(QColor(BORDER), 1.0))
        painter.drawPath(path)
        painter.setClipPath(path)
        painter.translate(self._pad, self._pad)
        ctx = self._doc.documentLayout()
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette = self.palette()
        context.palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
        context.palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
        context.palette.setColor(QPalette.ColorRole.Base, QColor(CARD))
        context.clip = QRectF(0, 0, self.width() - 2 * self._pad, self.height() - 2 * self._pad)
        ctx.draw(painter, context)


class SnackBar(QWidget):
    """应用内轻提示，显示在窗口底部中央。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._text = ""
        self._tone = SUCCESS
        self._glyph = icons.CHECK
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(44)
        self.hide()
        self._hide_connected = False
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(170)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    def show_message(self, text: str, tone: str = "success", duration: int = 2800,
                     glyph: Optional[str] = None) -> None:
        self._text = text
        self._tone = {"success": SUCCESS, "info": ACCENT2, "error": DANGER,
                      "warn": WARNING}.get(tone, SUCCESS)
        self._glyph = glyph or {"error": icons.WARNING, "info": icons.INFO}.get(tone, icons.CHECK)
        metrics = QFontMetrics(self.font())
        width = int(metrics.horizontalAdvance(text) * 1.04) + 76
        width = max(220, min(width, max(260, self.parentWidget().width() - 120)))
        self.setFixedWidth(width)
        parent = self.parentWidget()
        self.move((parent.width() - width) // 2, parent.height() - 74)
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self.show()
        self.raise_()
        self._timer.start(duration)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        if not self._hide_connected:
            self._anim.finished.connect(self.hide)
            self._hide_connected = True
        self._anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = QColor(CARD)
        fill.setAlphaF(0.97)
        painter.fillPath(rounded_path(rect, 13), fill)
        painter.setPen(QPen(QColor(BORDER), 1.0))
        painter.drawPath(rounded_path(rect, 13))
        painter.setFont(icon_font(12))
        painter.setPen(QColor(self._tone))
        painter.drawText(QRectF(16, 0, 22, rect.height()), int(Qt.AlignmentFlag.AlignCenter),
                         self._glyph)
        painter.setFont(self.font())
        painter.setPen(QColor(TEXT))
        painter.drawText(
            QRectF(46, 0, rect.width() - 62, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )


# ---------------------------------------------------------------------- 对话框基类
class DragHeader(QWidget):
    """可拖动窗口的标题区域。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            handle = window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)


class BaseDialog(QDialog):
    """无边框、圆角、带投影的模态对话框基类。"""

    MARGIN = 26

    def __init__(
        self,
        parent=None,
        width: int = 640,
        height: int = 560,
        title: str = "",
        subtitle: str = "",
        icon: Optional[str] = None,
        icon_color: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setWindowTitle(title)
        self._positioned = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.MARGIN, self.MARGIN, self.MARGIN, self.MARGIN)
        self.panel = ShadowPanel(self, radius=theme.WINDOW_RADIUS, shadow=16, glow=False,
                                 flat=True)
        outer.addWidget(self.panel)

        self.root = QVBoxLayout(self.panel)
        self.root.setContentsMargins(26, 18, 26, 22)
        self.root.setSpacing(14)

        self.header = DragHeader(self.panel)
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(0, 4, 0, 0)
        header_row.setSpacing(12)
        if icon:
            header_row.addWidget(IconBadge(
                icon, size=34,
                color=icon_color or theme.appearance().accent_q().name(),
                bg_alpha=0.16, radius=10, glyph_size=14,
            ))
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("H2")
        text_box.addWidget(self.title_label)
        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setObjectName("Tiny")
            text_box.addWidget(self.subtitle_label)
        header_row.addLayout(text_box, 1)
        self.close_button = IconButton(icons.CLOSE, size=30, glyph_size=10, tip="关闭 (Esc)")
        self.close_button.clicked.connect(self.reject)
        header_row.addWidget(self.close_button)
        self.root.addWidget(self.header)

        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        self.root.addLayout(self.body, 1)

        self.resize(width + self.MARGIN * 2, height + self.MARGIN * 2)

    def add_footer(self, left_widgets=(), right_widgets=()) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(10)
        for widget in left_widgets:
            footer.addWidget(widget)
        footer.addStretch(1)
        for widget in right_widgets:
            footer.addWidget(widget)
        self.root.addLayout(footer)
        return footer

    def showEvent(self, event):
        super().showEvent(event)
        if not self._positioned:
            self._positioned = True
            parent = self.parentWidget()
            target = None
            if parent is not None:
                target = parent.window().frameGeometry().center() - self.rect().center()
            if target is not None:
                self.move(target)
            screen = self.screen()
            if screen is not None:
                available = screen.availableGeometry()
                x = max(available.left() + 8, min(self.x(), available.right() - self.width() - 8))
                y = max(available.top() + 8, min(self.y(), available.bottom() - self.height() - 8))
                self.move(x, y)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


class ConfirmDialog(BaseDialog):
    """统一样式的确认对话框。"""

    def __init__(
        self,
        parent,
        title: str,
        message: str,
        confirm_text: str = "确认",
        cancel_text: str = "取消",
        danger: bool = True,
    ):
        super().__init__(parent, width=440, height=172, title=title, icon=icons.WARNING,
                         icon_color=QColor(DANGER).name())
        self.confirmed = False
        label = QLabel(message)
        label.setWordWrap(True)
        label.setObjectName("Muted")
        self.body.addWidget(label)
        self.cancel_button = SoftButton(cancel_text, "ghost", height=36, min_width=92)
        self.ok_button = SoftButton(
            confirm_text, "danger" if danger else "primary", height=36, min_width=104
        )
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self._accept)
        self.add_footer([], [self.cancel_button, self.ok_button])

    def _accept(self) -> None:
        self.confirmed = True
        self.accept()


def round_menu(menu):
    """给 QMenu 打开「逐像素透明」，圆角才不会露出白色方角。

    样式表里 `QMenu { background: CARD; border-radius: 8px }` 只画在菜单窗口上，
    而菜单默认是**不透明**的原生弹出窗 —— 圆角外面那圈就是窗口底色（白方块）。
    打开 WA_TranslucentBackground 之后底色是透明的，圆角才是真的圆。
    """
    try:
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        menu.setWindowFlags(menu.windowFlags()
                            | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
    except Exception:
        pass
    return menu


def make_scroll() -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    """创建透明背景、无边框的竖向滚动区域。"""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    body = QWidget()
    body.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(body)
    layout.setContentsMargins(0, 0, 6, 0)
    layout.setSpacing(10)
    area.setWidget(body)
    return area, body, layout


def hspacer() -> QWidget:
    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return widget


def section_header(title: str, hint: str = "", glyph: Optional[str] = None) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    if glyph:
        icon = QLabel()
        icon.setFont(icon_font(11))
        icon.setText(glyph)
        icon.setStyleSheet(f"color: {ACCENT2};")
        layout.addWidget(icon)
    label = QLabel(title)
    label.setObjectName("H3")
    layout.addWidget(label)
    if hint:
        hint_label = QLabel(hint)
        hint_label.setObjectName("Tiny")
        layout.addWidget(hint_label)
    layout.addStretch(1)
    return widget


class PaneSplitter(QWidget):
    """分栏之间的拖拽条：悬停变成左右箭头，按住拖动实时改变宽度。

    只负责收集拖拽（发 `dragged(新宽度)`），真正改哪个栏由主窗口决定。
    """

    dragged = Signal(int)      # 拖拽后的目标宽度（像素）
    released = Signal(int)     # 松手时的最终宽度（用来写设置）
    grabbed = Signal()         # 按下时：外部据此记录起始宽度
    reset_requested = Signal()

    def __init__(self, parent=None, width: int = 8, base: int = 0):
        super().__init__(parent)
        self._base = int(base)
        self._sign = -1      # -1: 往左拖右边变宽；+1: 往右拖右边变宽
        self._start_x = 0
        self._dragging = False
        self._hover = False
        self.setFixedWidth(int(width))
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("拖动可以调整左右两栏的宽度（双击恢复默认）")

    def set_base(self, width: int, invert: bool = True) -> None:
        """拖动基准：`width` 是**右边那一栏**当前的宽度（拖的就是它）。

        默认 `invert=True`：鼠标往左拖 → 右边那栏变宽；往右拖 → 变窄。
        这样分割线一直是跟着鼠标走的（反过来会「鼠标往右、线往左」，很别扭）。
        """
        self._base = int(max(0, width))
        self._sign = -1 if invert else 1

    def is_dragging(self) -> bool:
        return bool(self._dragging)

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
            self._dragging = True
            self._start_x = int(event.globalPosition().x())
            self.grabbed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = int(event.globalPosition().x()) - self._start_x
            self.dragged.emit(self._base + self._sign * delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            delta = int(event.globalPosition().x()) - self._start_x
            self.update()
            self.released.emit(self._base + self._sign * delta)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = theme.appearance().accent_q(0.55 if (self._hover or self._dragging)
                                            else 0.18)
        width = 2 if (self._hover or self._dragging) else 1
        x = self.width() / 2.0
        painter.setPen(QPen(color, width))
        painter.drawLine(int(x), 6, int(x), self.height() - 6)


class HScrollRow(QScrollArea):
    """一行放不下的按钮/选项：横向滚动，而不是叠在一起。"""

    def __init__(self, parent=None, height: int = 34, spacing: int = 6):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.setStyleSheet(
            "QScrollArea { background: transparent; border: 0px; }"
            "QScrollBar:horizontal { height: 6px; background: transparent;"
            " margin: 0px; border: 0px; }"
            "QScrollBar::handle:horizontal { background: rgba(130,140,160,0.45);"
            " border-radius: 3px; min-width: 24px; }"
            "QScrollBar::handle:horizontal:hover {"
            " background: rgba(130,140,160,0.65); }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal"
            " { width: 0px; height: 0px; }"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal"
            " { background: transparent; }")
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self.row = QHBoxLayout(self._host)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(int(spacing))
        self.setWidget(self._host)
        # 内容高度单独记着：出现横向滚动条时要把滚动条的高度让出来，
        # 否则 34px 高的下拉框会被 6px 滚动条压住底部（看起来像被「遮住了」）
        self._content_height = int(height)
        self.setFixedHeight(self._content_height)
        self.horizontalScrollBar().rangeChanged.connect(
            lambda _min, _max: self._sync_height())
        QTimer.singleShot(0, self._sync_height)

    def _sync_height(self) -> None:
        bar = self.horizontalScrollBar()
        extra = 8 if bar.maximum() > 0 else 0
        height = self._content_height + extra
        if self.height() != height:
            self.setFixedHeight(height)

    def content_height(self) -> int:
        return self._content_height

    def wheelEvent(self, event):
        """滚轮也能横向滚（触控板 / 鼠标都顺手）。"""
        delta = event.angleDelta()
        bar = self.horizontalScrollBar()
        step = 48
        if bar.maximum() > 0 and (delta.y() or delta.x()):
            amount = delta.y() or delta.x()
            bar.setValue(bar.value() - (step if amount > 0 else -step))
            event.accept()
            return
        super().wheelEvent(event)

    # 方便当普通布局用
    def addWidget(self, widget, stretch: int = 0):  # noqa: N802
        self.row.addWidget(widget, stretch)

    def addLayout(self, layout, stretch: int = 0):  # noqa: N802
        self.row.addLayout(layout, stretch)

    def addSpacing(self, value: int):  # noqa: N802
        self.row.addSpacing(int(value))

    def addStretch(self, value: int = 1):  # noqa: N802
        self.row.addStretch(int(value))

    def count(self) -> int:
        return self.row.count()

    def scroll_to_end(self) -> None:
        bar = self.horizontalScrollBar()
        bar.setValue(bar.maximum())


class GlassSlider(QSlider):
    """自绘的水平滑块。

    原生 QSlider 用样式表画圆钮时，圆钮在两端会被控件边界裁掉，
    且 hover / 拖动状态没有视觉反馈。这里完全接管绘制：
    轨道 + 渐变填充 + 白色圆钮（带描边与阴影），并保证圆钮始终完整可见。
    """

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._hover = False

    # ------------------------------------------------ 交互状态
    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.update()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(240, 26)

    # ------------------------------------------------ 绘制
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        state = theme.appearance()
        enabled = self.isEnabled()

        knob_radius = 8.5
        track_height = 6.0
        center_y = self.height() / 2.0
        left = knob_radius + 1.0
        right = max(left + 1.0, self.width() - knob_radius - 1.0)
        span = right - left

        # 轨道
        groove = QRectF(left, center_y - track_height / 2.0, span, track_height)
        track_color = QColor(state.border_hex())
        if not enabled:
            track_color.setAlphaF(0.6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawPath(rounded_path(groove, track_height / 2.0))

        # 已填充部分
        minimum, maximum = self.minimum(), self.maximum()
        ratio = 0.0 if maximum == minimum else (self.value() - minimum) / (maximum - minimum)
        ratio = max(0.0, min(1.0, ratio))
        knob_x = left + span * ratio
        if knob_x - left > 0.5:
            fill_rect = QRectF(left, center_y - track_height / 2.0,
                               knob_x - left, track_height)
            gradient = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
            start = state.accent_deep_q() if enabled else QColor(FAINT)
            end = state.accent_light_q() if enabled else QColor(FAINT)
            gradient.setColorAt(0.0, start)
            gradient.setColorAt(1.0, end)
            painter.setBrush(QBrush(gradient))
            painter.drawPath(rounded_path(fill_rect, track_height / 2.0))

        # 悬停 / 焦点光晕
        if enabled and (self._hover or self.isSliderDown() or self.hasFocus()):
            halo = QColor(state.accent)
            halo.setAlphaF(0.18 if self.isSliderDown() else 0.12)
            painter.setBrush(halo)
            painter.drawEllipse(
                QPointF(knob_x, center_y), knob_radius + 4.0, knob_radius + 4.0
            )

        # 圆钮
        painter.setBrush(QColor(30, 40, 28, 46))
        painter.drawEllipse(
            QPointF(knob_x, center_y + 1.1), knob_radius, knob_radius
        )
        painter.setBrush(QColor(CARD))
        ring = state.accent_q() if enabled else QColor(FAINT)
        painter.setPen(QPen(ring, 2.0))
        painter.drawEllipse(QPointF(knob_x, center_y), knob_radius - 1.0, knob_radius - 1.0)
        painter.end()

