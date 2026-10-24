"""看板娘：浮在桌面上的一只小生物（不是困在窗口里）。

* 独立无边框小窗，永远置顶、不进任务栏、不抢焦点；
* 会呼吸、眨眼、开心跳一下，点一下冒气泡说话；
* 可以拖着在桌面上移动，位置会记住；
* 形象可选：小生物 / 猫咪 / 幽灵 / 自定义图片（png、jpg、gif 都行）；
* 右键弹圆角菜单：说点什么、换个形象、藏起来、回右下角。

设置项：mascot_on、mascot_style、mascot_image、mascot_pos（"x,y"）。
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QMovie, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QVBoxLayout, QWidget,
)

from . import theme
from .widgets import SoftButton, rounded_path

WIDTH = 148
HEIGHT = 168
STYLES = [
    ("egg", "卡通破壳蛋"),
    ("blob", "小生物"),
    ("cat", "猫咪"),
    ("ghost", "幽灵"),
    ("pixel", "像素破壳蛋"),
    ("image", "自定义图片"),
]
STYLE_LABELS = dict(STYLES)


class MascotMenu(QWidget):
    """圆角浮层菜单（QMenu 在 Windows 上圆角会露出方角）。"""

    def __init__(self, mascot: "Mascot"):
        super().__init__(None, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.mascot = mascot

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("MascotCard")
        card.setStyleSheet(
            f"QFrame#MascotCard {{ background: {theme.CARD};"
            f" border: 1px solid {theme.BORDER}; border-radius: 12px; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        for text, handler in (
            ("说点什么", self._say_something),
            ("换个形象…", self._change_style),
            ("回右下角", mascot.reset_position),
            ("先藏起来", self._hide_mascot),
        ):
            button = SoftButton(text, "ghost", height=30, min_width=132, point_size=8.6)
            button.clicked.connect(lambda _c=False, h=handler: h())
            layout.addWidget(button)
        outer.addWidget(card)
        self.adjustSize()

    # -------------------------------------------------- 动作
    def _close_menu(self) -> None:
        self.close()

    def _say_something(self) -> None:
        self.mascot.say(self.mascot._tip())
        self._close_menu()

    def _change_style(self) -> None:
        current = self.mascot.style()
        ids = [item[0] for item in STYLES]
        try:
            index = (ids.index(current) + 1) % len(ids)
        except ValueError:
            index = 0
        self.mascot.set_style(ids[index])
        self._close_menu()

    def _hide_mascot(self) -> None:
        self.mascot.set_visible_setting(False)
        self._close_menu()


class Mascot(QWidget):
    """桌面上的小生物。"""

    def __init__(self, store, parent=None):
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.store = store
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(WIDTH, HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("")

        self._phase = 0.0
        self._blink_countdown = 60
        self._blink = 0
        self._happy = 0.0
        self._hop = 0.0
        self._bubble = ""
        self._bubble_left = 0.0
        self._dragging = False
        self._moved = False
        self._press_pos: Optional[QPoint] = None
        self._press_offset = QPoint()
        self._pixmap: Optional[QPixmap] = None
        self._movie: Optional[QMovie] = None

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

        self._load_style_assets()

    # -------------------------------------------------- 形象
    def style(self) -> str:
        value = (self.store.get_setting("mascot_style", "egg") or "egg").strip()
        return value if value in STYLE_LABELS else "egg"

    def image_path(self) -> Optional[Path]:
        raw = (self.store.get_setting("mascot_image", "") or "").strip()
        if not raw:
            return None
        path = Path(raw)
        return path if path.exists() else None

    def set_style(self, style: str, image: Optional[str] = None) -> None:
        if style not in STYLE_LABELS:
            style = "egg"
        self.store.set_setting("mascot_style", style)
        if image:
            self.store.set_setting("mascot_image", image)
        self._load_style_assets()
        self.say(f"我是{STYLE_LABELS[style]}啦", 2.6)
        self.update()

    def pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择看板娘形象", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if not path:
            return
        self.set_style("image", path)

    def _load_style_assets(self) -> None:
        if self._movie is not None:
            try:
                self._movie.stop()
                self._movie.deleteLater()
            except Exception:
                pass
            self._movie = None
        self._pixmap = None
        if self.style() != "image":
            return
        path = self.image_path()
        if path is None:
            return
        if path.suffix.lower() == ".gif":
            movie = QMovie(str(path))
            if movie.isValid():
                movie.frameChanged.connect(self.update)
                movie.start()
                self._movie = movie
                return
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            self._pixmap = pixmap

    # -------------------------------------------------- 生命周期
    def set_on(self, enabled: bool) -> None:
        if enabled:
            self._restore_position()
            self.show()
            self.raise_()
            if not self._timer.isActive():
                self._timer.start()
        else:
            self.hide()
            self._timer.stop()

    def is_on(self) -> bool:
        return bool(self.isVisible())

    def showEvent(self, event):
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    # -------------------------------------------------- 位置
    def reset_position(self) -> None:
        self.store.set_setting("mascot_pos", "")
        self._restore_position()

    def _restore_position(self) -> None:
        raw = (self.store.get_setting("mascot_pos", "") or "").strip()
        pos = None
        if "," in raw:
            try:
                x, y = (int(part) for part in raw.split(",", 1))
                pos = QPoint(x, y)
            except Exception:
                pos = None
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            valid = False
            if pos is not None:
                for each in QGuiApplication.screens():
                    if each.availableGeometry().adjusted(-40, -40, 40, 40).contains(pos):
                        valid = True
                        break
            if not valid:
                pos = QPoint(
                    area.x() + area.width() - WIDTH - 28,
                    area.y() + area.height() - HEIGHT - 28,
                )
        if pos is not None:
            self.move(pos)

    def _save_position(self) -> None:
        self.store.set_setting("mascot_pos", f"{self.x()},{self.y()}")

    # -------------------------------------------------- 动画
    def _tick(self) -> None:
        self._phase += 0.16
        self._blink_countdown -= 1
        if self._blink_countdown <= 0:
            self._blink = 6
            self._blink_countdown = random.randint(60, 140)
        if self._blink > 0:
            self._blink -= 1
        if self._happy > 0:
            self._happy = max(0.0, self._happy - 0.03)
        if self._hop > 0:
            self._hop = max(0.0, self._hop - 0.045)
        if self._bubble_left > 0:
            self._bubble_left = max(0.0, self._bubble_left - 0.04)
            if self._bubble_left <= 0:
                self._bubble = ""
        self.update()

    def _body_offset(self) -> float:
        bob = math.sin(self._phase * 0.9) * 2.6
        hop = math.sin(self._hop * math.pi) * 12.0 if self._hop > 0 else 0.0
        return bob - hop

    # -------------------------------------------------- 交互
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._press_offset = self._press_pos - self.pos()
            self._dragging = False
            self._moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is None:
            return
        current = event.globalPosition().toPoint()
        if not self._dragging and (current - self._press_pos).manhattanLength() > 5:
            self._dragging = True
        if self._dragging:
            self.move(current - self._press_offset)
            self._moved = True
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._dragging:
            self._save_position()
        else:
            self.say(self._tip())
            self._happy = 1.0
        self._press_pos = None
        self._dragging = False
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self._hop = 1.0
        self._happy = 1.0
        self.say("嘿呀！")
        event.accept()

    def contextMenuEvent(self, event):
        menu = MascotMenu(self)
        menu.adjustSize()
        point = event.globalPos()
        menu.move(point)
        menu.show()

    def set_visible_setting(self, visible: bool) -> None:
        self.store.set_setting("mascot_on", "1" if visible else "0")
        self.set_on(visible)

    def say(self, text: str, seconds: float = 3.2) -> None:
        self._bubble = text or ""
        self._bubble_left = seconds / 0.04

    # -------------------------------------------------- 台词
    def _tip(self) -> str:
        tips: List[str] = []
        try:
            today = date.today().strftime("%Y-%m-%d")
            events = self.store.events_on(today)
            now = datetime.now()
            pending = []
            for item in events:
                if item.get("done"):
                    continue
                try:
                    hour, minute = (int(p) for p in (item.get("end_time") or "23:59")[:5].split(":"))
                    end = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                except Exception:
                    end = None
                if end is not None and end < now:
                    continue
                pending.append(item)
            if pending:
                tips.append(f"今天还有 {len(pending)} 个日程没完成")
            elif events:
                tips.append("今天的日程都完成啦，真棒")
        except Exception:
            pass
        try:
            unread = int(self.store.unread_notice_count() or 0)
            if unread:
                tips.append(f"有 {unread} 条未读通知")
        except Exception:
            pass
        if not 9 <= datetime.now().hour <= 22:
            tips.append("夜深了，早点休息呀")
        tips.extend([
            "戳我一下就好～",
            "记得起来活动一下肩膀",
            "要不要先把今天的周报写了？",
            "喝口水吧！",
            "我把日程都盯着呢，放心",
        ])
        return random.choice(tips)

    # -------------------------------------------------- 绘制
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        cx = self.width() / 2.0
        base = self.height() - 12.0
        dy = self._body_offset()

        if self._bubble:
            self._paint_bubble(painter, 20.0 + dy)

        style = self.style()
        if style == "image":
            self._paint_image(painter, cx, base, dy)
            return
        if style == "ghost":
            self._paint_ghost(painter, cx, base, dy)
            return
        if style == "egg":
            self._paint_egg(painter, cx, base, dy)
            return
        if style == "pixel":
            self._paint_pixel(painter, cx, base, dy)
            return
        self._paint_blob(painter, cx, base, dy, cat=(style == "cat"))

    # -------------------------------------------------- 各形象
    def _pixel_rows(self) -> List[str]:
        """16×16 的像素图：上面是小鸡脑袋，下面是圆底蛋壳（锯齿裂口）。"""
        W, H = 16, 16
        grid = [["." for _ in range(W)] for _ in range(H)]

        def put(row: int, start: int, end: int, ch: str) -> None:
            if 0 <= row < H:
                for x in range(max(0, start), min(W, end + 1)):
                    grid[row][x] = ch

        # 头顶呆毛
        grid[0][7] = "Y"
        grid[0][8] = "Y"
        grid[1][6] = "Y"
        grid[1][7] = "Y"
        # 脑袋
        put(1, 8, 9, "Y")
        for row, span in ((2, (5, 10)), (3, (4, 11)), (4, (4, 11)),
                          (5, (4, 11)), (6, (4, 11)), (7, (5, 10))):
            put(row, span[0], span[1], "Y")

        # 眼睛：2×2，带一个高光点
        for left in (5, 9):
            for row in (4, 5):
                put(row, left, left + 1, "K")
            grid[4][left] = "G"
        if self._blink > 0:
            for left in (5, 9):
                grid[4][left] = "Y"
                grid[4][left + 1] = "Y"
                grid[5][left] = "K"
                grid[5][left + 1] = "K"

        # 腮红
        grid[6][4] = "P"
        grid[6][11] = "P"

        # 小嘴
        if self._happy > 0.15:
            put(6, 6, 9, "O")
            put(7, 7, 8, "O")
        else:
            put(6, 7, 8, "O")

        # 蛋壳：锯齿裂口 + 圆圆的碗底
        for x in range(1, 15):
            if (x - 1) % 3 in (0, 1):
                grid[8][x] = "W"
        for row, span in ((9, (1, 14)), (10, (1, 14)), (11, (2, 13)),
                          (12, (3, 12)), (13, (4, 11)), (14, (5, 10))):
            put(row, span[0], span[1], "W")
        # 壳的描边：每行最左最右各压一个暗格，最底一行整行压暗
        for row in range(8, 15):
            filled = [x for x in range(W) if grid[row][x] == "W"]
            if not filled:
                continue
            grid[row][filled[0]] = "w"
            grid[row][filled[-1]] = "w"
        for x in range(W):
            if grid[14][x] == "W":
                grid[14][x] = "w"
        return ["".join(row) for row in grid]

    def _paint_pixel(self, painter, cx, base, dy) -> None:
        rows = self._pixel_rows()
        height = len(rows)
        width = len(rows[0])
        cell = 6.0
        total_w = width * cell
        left = round(cx - total_w / 2.0)
        top = round(base - height * cell + dy * 0.4)

        colors = {
            "Y": QColor("#FFD95E"),
            "O": QColor("#F5A03C"),
            "K": QColor("#3A2E1E"),
            "G": QColor(255, 255, 255, 235),
            "P": QColor("#FF9DA6"),
            "W": QColor("#FFFDF3"),
            "w": QColor("#EBDCBC"),
        }
        # 影子（像素风也保持方一点）
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 30, 30))
        painter.drawRect(QRectF(left + cell, base - cell, total_w - cell * 2, cell))

        for row_index, row in enumerate(rows):
            for col_index, char in enumerate(row):
                color = colors.get(char)
                if color is None:
                    continue
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(QRectF(left + col_index * cell,
                                        top + row_index * cell, cell, cell))

    def _paint_egg(self, painter, cx, base, dy) -> None:
        """破壳小鸡：下面半个蛋壳，上面探出一只毛茸茸的小鸡。"""
        shell_w = 82.0
        shell_top = base - 44 + dy * 0.35
        left = cx - shell_w / 2.0
        right = cx + shell_w / 2.0

        # 影子
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 30, 30))
        painter.drawEllipse(QRectF(cx - 32, base - 8, 64, 13))

        chick_cy = shell_top - 20
        # 小鸡身体
        body = QPainterPath()
        body.addEllipse(QRectF(cx - 27, chick_cy - 27, 54, 54))
        chick_gradient = QLinearGradient(cx, chick_cy - 27, cx, chick_cy + 27)
        chick_gradient.setColorAt(0.0, QColor("#FFE38A"))
        chick_gradient.setColorAt(1.0, QColor("#F7C64B"))
        painter.setBrush(chick_gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(body)

        # 头顶呆毛
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#E9A93A"), 2.2, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        tuft = QPainterPath()
        tuft.moveTo(cx, chick_cy - 27)
        tuft.quadTo(cx - 4, chick_cy - 37, cx - 10, chick_cy - 33)
        painter.drawPath(tuft)
        tuft2 = QPainterPath()
        tuft2.moveTo(cx + 1, chick_cy - 27)
        tuft2.quadTo(cx + 6, chick_cy - 36, cx + 11, chick_cy - 31)
        painter.drawPath(tuft2)

        # 眼睛
        blink_scale = 0.16 if self._blink > 0 else 1.0
        eye_h = 9.0 * blink_scale
        ink = QColor(48, 38, 24)
        for sign in (-1, 1):
            ex = cx + sign * 10 - 3.2
            painter.setBrush(ink)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(ex, chick_cy - 5 - eye_h / 2, 6.4, eye_h))
            if blink_scale > 0.5:
                painter.setBrush(QColor(255, 255, 255, 210))
                painter.drawEllipse(QRectF(ex + 1.8, chick_cy - 5 - eye_h / 2 + 1.6, 2.4, 2.4))

        # 腮红
        painter.setBrush(QColor(255, 150, 150, 70))
        painter.drawEllipse(QRectF(cx - 25, chick_cy + 2, 10, 6))
        painter.drawEllipse(QRectF(cx + 15, chick_cy + 2, 10, 6))

        # 小嘴（开心时张嘴）
        beak = QColor("#F5A03C")
        painter.setBrush(beak)
        painter.setPen(Qt.PenStyle.NoPen)
        if self._happy > 0.15:
            mouth = QPainterPath()
            mouth.moveTo(cx - 6, chick_cy + 6)
            mouth.lineTo(cx + 6, chick_cy + 6)
            mouth.lineTo(cx, chick_cy + 14)
            mouth.closeSubpath()
            painter.drawPath(mouth)
        else:
            painter.drawEllipse(QRectF(cx - 5.5, chick_cy + 5, 11, 7))

        # 蛋壳（下半部分，锯齿裂口）
        shell = QPainterPath()
        teeth = 5
        step = shell_w / (teeth * 2)
        shell.moveTo(left, shell_top + 4)
        x = left
        up = True
        for _ in range(teeth * 2):
            shell.lineTo(x + step / 2, shell_top - (7 if up else 1))
            shell.lineTo(x + step, shell_top + 4)
            x += step
            up = not up
        shell.lineTo(right, base - 26)
        # 圆圆的碗底：两侧收弧，底部平滑
        shell.cubicTo(right + 3, base - 6, right - 12, base + 4, cx, base + 4)
        shell.cubicTo(left + 12, base + 4, left - 3, base - 6, left, base - 26)
        shell.closeSubpath()

        shell_gradient = QLinearGradient(cx, shell_top, cx, base)
        shell_gradient.setColorAt(0.0, QColor("#FFFDF6"))
        shell_gradient.setColorAt(1.0, QColor("#F1E4C9"))
        painter.setBrush(shell_gradient)
        painter.setPen(QPen(QColor("#E0D2B4"), 1.4))
        painter.drawPath(shell)

        # 壳上的小裂纹
        painter.setPen(QPen(QColor("#D8C7A4"), 1.2))
        painter.drawLine(QPointF(cx + 12, shell_top + 16), QPointF(cx + 18, shell_top + 24))
        painter.drawLine(QPointF(cx + 18, shell_top + 24), QPointF(cx + 13, shell_top + 30))
        painter.drawLine(QPointF(cx - 20, shell_top + 20), QPointF(cx - 14, shell_top + 28))

    def _accent_pair(self):
        state = theme.appearance()
        return state.accent_light_q(), state.accent_deep_q()

    def _paint_blob(self, painter, cx, base, dy, cat=False) -> None:
        width, height = 88.0, 92.0
        top = base - height + dy
        left = cx - width / 2.0
        light, deep = self._accent_pair()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 30, 34))
        painter.drawEllipse(QRectF(cx - 34, base - 8, 68, 14))

        if cat:
            painter.setBrush(deep.lighter(106))
            for sign in (-1, 1):
                ear = QPainterPath()
                ear_x = cx + sign * 24
                ear.moveTo(ear_x - 13, top + 30)
                ear.lineTo(ear_x, top + 2)
                ear.lineTo(ear_x + 13, top + 30)
                ear.closeSubpath()
                painter.drawPath(ear)

        body = self._body_path(left, top, width, height)
        gradient = QLinearGradient(left, top, left, top + height)
        gradient.setColorAt(0.0, light.lighter(112))
        gradient.setColorAt(1.0, deep)
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(body)

        gloss = QLinearGradient(left, top, left, top + height * 0.55)
        gloss.setColorAt(0.0, QColor(255, 255, 255, 96))
        gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(gloss)
        painter.drawPath(body)

        if not cat:
            ear_y = top + 6 + dy * 0.2
            painter.setBrush(deep.lighter(108))
            painter.drawEllipse(QRectF(left + 8, ear_y - 6, 18, 18))
            painter.drawEllipse(QRectF(left + width - 26, ear_y - 6, 18, 18))

        self._paint_face(painter, cx, top, height, cat=cat)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(deep.darker(108))
        painter.drawEllipse(QRectF(cx - 26, base - 12, 20, 11))
        painter.drawEllipse(QRectF(cx + 6, base - 12, 20, 11))
        if cat:
            tail = QPainterPath()
            tail.moveTo(cx + 36, base - 30)
            tail.cubicTo(cx + 62, base - 34, cx + 62, base - 6, cx + 42, base - 2)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(deep.lighter(104), 7, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap))
            painter.drawPath(tail)

    def _paint_face(self, painter, cx, top, height, cat=False) -> None:
        eye_y = top + height * 0.46
        blink_scale = 0.14 if self._blink > 0 else 1.0
        eye_h = 11.0 * blink_scale
        eye_w = 8.0 if not cat else 9.0
        ink = QColor(28, 32, 40)
        for sign in (-1, 1):
            ex = cx + sign * 17 - eye_w / 2
            painter.setBrush(ink)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(ex, eye_y - eye_h / 2, eye_w, eye_h))
            if blink_scale > 0.5:
                painter.setBrush(QColor(255, 255, 255, 220))
                painter.drawEllipse(QRectF(ex + 2.2, eye_y - eye_h / 2 + 2.0, 3.0, 3.0))

        painter.setBrush(QColor(255, 128, 148, 58))
        painter.drawEllipse(QRectF(cx - 33, eye_y + 13, 12, 7))
        painter.drawEllipse(QRectF(cx + 21, eye_y + 13, 12, 7))

        mouth_y = eye_y + 20
        if self._happy > 0.15:
            smile = QPainterPath()
            smile.moveTo(cx - 7, mouth_y - 1)
            smile.quadTo(cx, mouth_y + 8, cx + 7, mouth_y - 1)
            smile.closeSubpath()
            painter.setBrush(ink)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(smile)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ink, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(cx - 7, mouth_y - 4, 14, 9), 200 * 16, 140 * 16)

        if cat:
            painter.setPen(QPen(QColor(255, 255, 255, 150), 1.4))
            for sign in (-1, 1):
                for offset in (-4, 2):
                    painter.drawLine(
                        QPointF(cx + sign * 20, eye_y + 12 + offset),
                        QPointF(cx + sign * 42, eye_y + 6 + offset + (4 if sign > 0 else 6)),
                    )

    def _paint_ghost(self, painter, cx, base, dy) -> None:
        width, height = 86.0, 96.0
        top = base - height + dy
        left = cx - width / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 30, 30))
        painter.drawEllipse(QRectF(cx - 32, base - 8, 64, 13))

        path = QPainterPath()
        path.moveTo(left, top + height * 0.45)
        path.cubicTo(left, top - 4, left + width, top - 4, left + width, top + height * 0.45)
        path.lineTo(left + width, base - 10)
        waves = 4
        step = width / (waves * 2)
        x = left + width
        y = base - 10
        for index in range(waves * 2):
            target_x = x - step
            path.quadTo(x - step / 2, y + (10 if index % 2 == 0 else -2), target_x, y)
            x = target_x
        path.lineTo(left, top + height * 0.45)
        path.closeSubpath()

        light, deep = self._accent_pair()
        gradient = QLinearGradient(left, top, left, base)
        gradient.setColorAt(0.0, QColor(255, 255, 255, 244))
        gradient.setColorAt(0.6, QColor(light.red(), light.green(), light.blue(), 232))
        gradient.setColorAt(1.0, QColor(deep.red(), deep.green(), deep.blue(), 214))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
        self._paint_face(painter, cx, top + 8, height - 8)

    def _paint_image(self, painter, cx, base, dy) -> None:
        frame = None
        if self._movie is not None:
            frame = self._movie.currentPixmap()
        elif self._pixmap is not None:
            frame = self._pixmap
        if frame is None or frame.isNull():
            painter.setPen(QPen(QColor(theme.MUTED)))
            font = QFont(self.font())
            font.setPixelSize(11)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "右键选一张图片")
            return
        limit = QRectF(8, 8, self.width() - 16, self.height() - 26)
        ratio = min(limit.width() / frame.width(), limit.height() / frame.height())
        size = frame.size() * ratio
        target = QRectF(cx - size.width() / 2.0, base - size.height() + dy * 0.4,
                        size.width(), size.height())
        painter.drawPixmap(target, frame, QRectF(frame.rect()))

    def _body_path(self, left: float, top: float, width: float, height: float) -> QPainterPath:
        path = QPainterPath()
        top_y = top + height * 0.14
        path.moveTo(left + width * 0.5, top_y)
        path.cubicTo(left + width * 0.98, top + height * 0.16,
                     left + width * 1.02, top + height * 0.92,
                     left + width * 0.5, top + height * 0.98)
        path.cubicTo(left - width * 0.02, top + height * 0.92,
                     left + width * 0.02, top + height * 0.16,
                     left + width * 0.5, top_y)
        return path

    # -------------------------------------------------- 气泡
    def _paint_bubble(self, painter, top: float) -> None:
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.Medium)
        metrics = QFontMetrics(font)
        lines = self._wrap_text(metrics, self._bubble, self.width() - 28, 2)
        width = max(metrics.horizontalAdvance(line) for line in lines) + 20
        width = min(self.width() - 6, width)
        height = 12 + 15 * len(lines)
        x = (self.width() - width) / 2.0
        y = max(2.0, top - height - 10)

        fade = 1.0
        if self._bubble_left < 12:
            fade = max(0.0, self._bubble_left / 12.0)
        painter.setOpacity(fade)
        rect = QRectF(x, y, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 30, 26))
        painter.drawPath(rounded_path(QRectF(rect).adjusted(0, 2, 0, 4), 10))
        painter.setBrush(QColor(theme.CARD))
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawPath(rounded_path(rect, 10))

        tail = QPainterPath()
        tail.moveTo(self.width() / 2 - 5, y + height - 1)
        tail.lineTo(self.width() / 2, y + height + 6)
        tail.lineTo(self.width() / 2 + 5, y + height - 1)
        tail.closeSubpath()
        painter.setBrush(QColor(theme.CARD))
        painter.drawPath(tail)

        painter.setFont(font)
        painter.setPen(QPen(QColor(theme.TEXT)))
        for index, line in enumerate(lines):
            painter.drawText(
                QRectF(rect.left() + 10, rect.top() + 6 + 15 * index,
                       rect.width() - 20, 15),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        painter.setOpacity(1.0)

    @staticmethod
    def _wrap_text(metrics: QFontMetrics, text: str, max_width: float,
                   max_lines: int) -> List[str]:
        lines: List[str] = []
        current = ""
        for char in text:
            candidate = current + char
            if metrics.horizontalAdvance(candidate) > max_width and current:
                lines.append(current)
                current = char
                if len(lines) >= max_lines:
                    break
            else:
                current = candidate
        if current and len(lines) < max_lines:
            lines.append(current)
        if not lines:
            lines = [text[:12]]
        if len(lines) == max_lines:
            lines[-1] = metrics.elidedText(lines[-1], Qt.TextElideMode.ElideRight,
                                           int(max_width))
        return lines
