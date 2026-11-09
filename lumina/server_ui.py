"""服务器账号相关的界面小件：顶部公告横幅、注册弹层、设备管理弹层。

这里只用现有的 QSS / objectName（H3 / Small / Tiny / Muted）和 `theme` 里的
颜色常量，不写内联颜色值，换主题时跟着一起变（横幅的浅色底是按级别用
`theme.soft()` 算出来的 alpha）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from . import account, icons, theme
from .widgets import (
    BaseDialog, ElidedLabel, IconBadge, IconButton, Pill, SoftButton,
    clear_layout,
)

LEVEL_TONES = {
    "info": lambda: theme.ACCENT2,
    "warning": lambda: theme.WARNING,
    "critical": lambda: theme.DANGER,
}
LEVEL_LABELS = {"info": "公告", "warning": "重要", "critical": "紧急"}


def level_tone(level: str) -> str:
    """公告级别 → 颜色（info 用强调色浅底，warning / critical 用醒目色）。"""
    picker = LEVEL_TONES.get(str(level or "info").strip().lower())
    try:
        return picker() if picker else theme.ACCENT2
    except Exception:
        return theme.ACCENT2


def level_label(level: str) -> str:
    return LEVEL_LABELS.get(str(level or "info").strip().lower(), "公告")


# ====================================================================== 横幅
class ServerBanner(QWidget):
    """主窗口顶部的服务器公告横幅：5 秒自动收起，点一下去「通知」页。

    进度条式的浅色底（info 最淡、critical 最扎眼）都按级别从 theme 里算，
    不自带颜色常量。
    """

    opened = Signal()
    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tone = QColor(theme.ACCENT2)
        self._level = "info"
        self.setVisible(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(46)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 8, 0)
        layout.setSpacing(10)

        self._badge = IconBadge(icons.BELL, size=26, color=theme.ACCENT2,
                                bg_alpha=0.18, radius=8, glyph_size=12)
        layout.addWidget(self._badge)

        self._pill = Pill("公告", theme.ACCENT2, theme.soft(theme.ACCENT2, 0.16),
                          point_size=7.8)
        layout.addWidget(self._pill)

        self._title = ElidedLabel("")
        title_font = QFont(self.font())
        title_font.setPointSizeF(9.6)
        title_font.setWeight(QFont.Weight.DemiBold)
        self._title.setFont(title_font)
        layout.addWidget(self._title, 0)

        self._body = ElidedLabel("")
        self._body.setObjectName("Small")
        layout.addWidget(self._body, 1)

        self.close_button = IconButton(icons.CLOSE, size=24, glyph_size=9,
                                       tip="收起这条公告（「通知」页里还能看）")
        self.close_button.clicked.connect(self._on_close)
        layout.addWidget(self.close_button)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    # -------------------------------------------------- 对外
    def show_announcement(self, item: Dict[str, Any],
                          timeout_ms: int = 5000) -> None:
        """弹一条公告；`timeout_ms<=0` 表示不自动收起。"""
        if not isinstance(item, dict):
            return
        level = str(item.get("level") or "info").strip().lower()
        tone = level_tone(level)
        self._level = level
        self._tone = QColor(tone)
        try:
            self._badge._color = QColor(tone)          # IconBadge 只在 init 里着色
            self._badge.update()
        except Exception:
            pass
        try:
            self._pill.set_text(level_label(level))
            self._pill.set_colors(tone, theme.soft(tone, 0.16))
        except Exception:
            pass
        self._title.setText(str(item.get("title") or "服务器公告"))
        body = str(item.get("body") or "").replace("\n", " ").strip()
        self._body.setText(body)
        self._body.setVisible(bool(body))
        self.setToolTip(body or self._title.full_text())
        self.setVisible(True)
        self.update()
        if timeout_ms > 0:
            self._timer.start(int(timeout_ms))

    def hide_now(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass
        self.setVisible(False)

    def level(self) -> str:
        return self._level

    # -------------------------------------------------- 事件
    def _on_close(self) -> None:
        self.hide_now()
        try:
            self.dismissed.emit()
        except Exception:
            pass

    def _on_timeout(self) -> None:
        self.hide_now()
        try:
            self.dismissed.emit()
        except Exception:
            pass

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.hide_now()
            try:
                self.opened.emit()
            except Exception:
                pass
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        from .widgets import rounded_path

        fill = QColor(self._tone)
        fill.setAlphaF(0.14 if self._level == "info" else 0.18)
        painter.fillPath(rounded_path(rect, 12), fill)
        border = QColor(self._tone)
        border.setAlphaF(0.32)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(rounded_path(rect, 12))


# ====================================================================== 注册
class ServerRegisterDialog(BaseDialog):
    """注册服务器账号（用户名 / 邮箱 / 口令 / 邀请码）。

    成功时 `self.payload` 是 `account.register` 的结果，调用方拿它直接登录。
    """

    def __init__(self, store, parent=None):
        super().__init__(
            parent, width=430, height=420,
            title="注册服务器账号",
            subtitle="用户名、邮箱和口令会发到账号服务器；成功后会直接登录",
            icon=icons.CONTACT,
        )
        self.store = store
        self.payload: Optional[Dict[str, Any]] = None
        self._call = account.AsyncCall(self)
        self._call.finished.connect(self._on_finished)
        self._call.failed.connect(self._on_failed)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("用户名（服务器上唯一）")
        self.name_edit.setMinimumHeight(36)
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("邮箱（找回密码 / 管理员联系用）")
        self.email_edit.setMinimumHeight(36)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("口令（建议 8 位以上）")
        self.password_edit.setMinimumHeight(36)
        self.invite_edit = QLineEdit()
        self.invite_edit.setPlaceholderText("邀请码（服务器开放注册时才需要，可空）")
        self.invite_edit.setMinimumHeight(36)

        for label, widget in (("用户名", self.name_edit), ("邮箱", self.email_edit),
                              ("口令", self.password_edit),
                              ("邀请码", self.invite_edit)):
            caption = QLabel(label)
            caption.setObjectName("Small")
            self.body.addWidget(caption)
            self.body.addWidget(widget)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)
        self.body.addStretch(1)

        self.cancel_button = SoftButton("取消", "ghost", height=36, min_width=88)
        self.cancel_button.clicked.connect(self.reject)
        self.register_button = SoftButton("注册并登录", "primary", height=36,
                                          min_width=118)
        self.register_button.clicked.connect(self.submit)
        self.add_footer([], [self.cancel_button, self.register_button])
        self.email_edit.returnPressed.connect(self.submit)
        self.password_edit.returnPressed.connect(self.submit)
        self.invite_edit.returnPressed.connect(self.submit)

    # -------------------------------------------------- 提交
    def submit(self) -> None:
        if self._call.is_busy():
            return
        name = self.name_edit.text().strip()
        email = self.email_edit.text().strip()
        password = self.password_edit.text()
        if not name:
            self._set_status("先填一个用户名", theme.WARNING)
            return
        if "@" not in email:
            self._set_status("邮箱要填完整（比如 name@example.com）", theme.WARNING)
            return
        if len(password) < 6:
            self._set_status("口令太短了（至少 6 位）", theme.WARNING)
            return
        self.register_button.setEnabled(False)
        self._set_status("正在注册…", theme.MUTED)
        started = self._call.run(account.register, name, email, password,
                                 self.invite_edit.text().strip(), self.store)
        if not started:
            self.register_button.setEnabled(True)

    def _set_status(self, text: str, color: str) -> None:
        self.status.setStyleSheet(f"color: {color};")
        self.status.setText(text)

    def _on_finished(self, result: Any) -> None:
        self.register_button.setEnabled(True)
        if not isinstance(result, dict):
            self._set_status("注册失败：服务器返回了看不懂的东西", theme.DANGER)
            return
        if result.get("ok"):
            self.payload = result
            self._set_status("注册成功，正在登录…", theme.SUCCESS)
            self.accept()
            return
        message = str(result.get("error") or "注册失败")
        if result.get("offline"):
            message = ("服务器不可达：%s\n可以先用「邮件申请注册」走老的注册流程。"
                       % message)
        elif str(result.get("code") or "") == "device_limit":
            used = result.get("devices_used")
            limit = result.get("device_limit")
            message = ("登录设备数超过上限（%s/%s）：先在别的设备上退出，"
                       "或者在设置页「设备管理」里踢掉一台。" % (used, limit))
        self._set_status(message, theme.DANGER)

    def _on_failed(self, message: str) -> None:
        self.register_button.setEnabled(True)
        self._set_status("注册失败：%s" % message, theme.DANGER)


# ====================================================================== 设备
class ServerDevicesDialog(BaseDialog):
    """设备管理：列出登录过的设备，可以把某一台踢下线。"""

    def __init__(self, store, parent=None):
        super().__init__(
            parent, width=520, height=440,
            title="设备管理",
            subtitle="这些设备正在用同一个服务器账号登录；踢掉后那台要重新登录",
            icon=icons.LOCK,
        )
        self.store = store
        self._devices: List[Dict[str, Any]] = []
        self._list_call = account.AsyncCall(self)
        self._list_call.finished.connect(self._on_loaded)
        self._list_call.failed.connect(self._on_failed)
        self._kick_call = account.AsyncCall(self)
        self._kick_call.finished.connect(self._on_kicked)
        self._kick_call.failed.connect(self._on_failed)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.count_label = QLabel("设备数 —")
        self.count_label.setObjectName("Small")
        head.addWidget(self.count_label, 1)
        self.refresh_button = SoftButton("刷新", "ghost", height=30, min_width=74,
                                        glyph=icons.REFRESH, point_size=8.6)
        self.refresh_button.clicked.connect(self.reload)
        head.addWidget(self.refresh_button)
        self.body.addLayout(head)

        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(6)
        self.body.addLayout(self.list_box, 1)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        self.close_footer = SoftButton("关闭", "ghost", height=34, min_width=88)
        self.close_footer.clicked.connect(self.accept)
        self.add_footer([], [self.close_footer])

        self.reload()

    # -------------------------------------------------- 数据
    def reload(self) -> None:
        if self._list_call.is_busy():
            return
        self.refresh_button.setEnabled(False)
        self._set_status("正在获取设备列表…", theme.MUTED)
        if not self._list_call.run(account.devices, self.store):
            self.refresh_button.setEnabled(True)

    def _on_loaded(self, result: Any) -> None:
        self.refresh_button.setEnabled(True)
        if not isinstance(result, dict):
            self._set_status("设备列表读不出来", theme.DANGER)
            return
        if not result.get("ok"):
            self._set_status(str(result.get("error") or "设备列表读不出来"), theme.DANGER)
            return
        payload = result.get("data") if isinstance(result.get("data"), dict) else {}
        devices = result.get("devices")
        if not isinstance(devices, list):
            devices = payload.get("devices") if isinstance(payload.get("devices"),
                                                            list) else []
        self._devices = [item for item in devices if isinstance(item, dict)]
        session = account.session(self.store)
        used = session.get("devices_used") or payload.get("devices_used") or len(self._devices)
        limit = session.get("device_limit") or payload.get("device_limit") or "—"
        self.count_label.setText("设备数 %s / %s" % (used, limit))
        self._render()
        if not self._devices:
            self._set_status("服务器上没有登记设备（或者列表是空的）", theme.MUTED)
        else:
            self._set_status("", theme.MUTED)

    def _on_failed(self, message: str) -> None:
        self.refresh_button.setEnabled(True)
        self._set_status("设备列表读不出来：%s" % message, theme.DANGER)

    # -------------------------------------------------- 渲染
    def _render(self) -> None:
        clear_layout(self.list_box)
        if not self._devices:
            empty = QLabel("还没有设备")
            empty.setObjectName("Muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_box.addWidget(empty)
            return
        for item in self._devices:
            self.list_box.addWidget(self._make_row(item))
        self.list_box.addStretch(1)

    def _make_row(self, item: Dict[str, Any]) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)
        layout.addWidget(IconBadge(icons.CONTACT, size=26, color=theme.ACCENT,
                                   bg_alpha=0.12, radius=8, glyph_size=11))
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        name = str(item.get("name") or "未命名设备")
        title = QLabel(name)
        title.setObjectName("Small")
        text_box.addWidget(title)
        platform_name = str(item.get("platform") or "")
        seen = str(item.get("last_seen_at") or "")[:19].replace("T", " ")
        meta = QLabel(" · ".join(part for part in (platform_name,
                                                   ("最后活跃 " + seen) if seen else "")
                                 if part) or "—")
        meta.setObjectName("Tiny")
        text_box.addWidget(meta)
        layout.addLayout(text_box, 1)
        kick = SoftButton("踢出", "danger", height=28, min_width=62, point_size=8.4)
        kick.setToolTip("让这台设备下线（它下次要用就得重新登录）")
        device_id = item.get("id")
        kick.clicked.connect(lambda _=False, did=device_id: self._kick(did))
        layout.addWidget(kick)
        return row

    # -------------------------------------------------- 踢出
    def _kick(self, device_id) -> None:
        if device_id in (None, ""):
            return
        from .widgets import ConfirmDialog

        dialog = ConfirmDialog(
            self, title="踢出这台设备？",
            message="对方会被要求重新登录；如果你自己就在那台设备上，会立刻断开。",
            confirm_text="踢出", danger=True)
        if dialog.exec() != ConfirmDialog.DialogCode.Accepted:
            return
        if self._list_call.is_busy():
            return
        self._set_status("正在踢出…", theme.MUTED)
        if not self._kick_call.run(account.kick_device, device_id, self.store):
            self._set_status("上一个操作还没完成", theme.WARNING)
            return

    def _on_kicked(self, result: Any) -> None:
        if not isinstance(result, dict):
            self._set_status("踢出失败：服务器返回了看不懂的东西", theme.DANGER)
            return
        if not result.get("ok"):
            self._set_status(str(result.get("error") or "踢出失败"), theme.DANGER)
            return
        self._set_status("已踢出这台设备", theme.SUCCESS)
        self.reload()

    def _set_status(self, text: str, color: str) -> None:
        self.status.setStyleSheet(f"color: {color};")
        self.status.setText(text)
