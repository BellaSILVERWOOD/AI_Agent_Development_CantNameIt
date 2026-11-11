"""服务器账号相关的界面小件：顶部公告横幅、注册弹层、设备管理弹层。

这里只用现有的 QSS / objectName（H3 / Small / Tiny / Muted）和 `theme` 里的
颜色常量，不写内联颜色值，换主题时跟着一起变（横幅的浅色底是按级别用
`theme.soft()` 算出来的 alpha）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPen, QRegularExpressionValidator,
)
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

# 「获取验证码」按下后按钮至少按住这么久（服务器一般回 cooldown=60）
CODE_WAIT_SECONDS = 60


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
    """注册服务器账号（用户名 / 邮箱 / 邮箱验证码 / 口令 / 邀请码）。

    验证码由「获取验证码」按钮现要（`POST /register/request-code`），6 位数字，
    发到上面填的邮箱；按钮按下后按服务器给的 `cooldown` 倒计时，期间不能再点。
    网络请求全走 `account.AsyncCall`（后台线程），界面不会被卡住；服务器回来的
    错误就地显示在 `status` 这一行里，不弹 QMessageBox。

    成功时 `self.payload` 是 `account.register` 的结果，调用方拿它直接登录。
    """

    def __init__(self, store, parent=None):
        super().__init__(
            parent, width=430, height=540,
            title="注册服务器账号",
            subtitle="用户名、邮箱和口令会发到账号服务器；验证码发到邮箱，"
                     "注册成功后直接登录",
            icon=icons.CONTACT,
        )
        self.store = store
        self.payload: Optional[Dict[str, Any]] = None
        self._code_wait = 0                 # 「获取验证码」还剩几秒才能再点
        self._call = account.AsyncCall(self)
        self._call.finished.connect(self._on_finished)
        self._call.failed.connect(self._on_failed)
        self._code_call = account.AsyncCall(self)
        self._code_call.finished.connect(self._on_code_finished)
        self._code_call.failed.connect(self._on_code_failed)
        self._code_timer = QTimer(self)
        self._code_timer.setInterval(1000)
        self._code_timer.timeout.connect(self._tick_code_countdown)
        self.body.setSpacing(9)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("用户名（服务器上唯一）")
        self.name_edit.setMinimumHeight(36)
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("邮箱（验证码发到这里，找回口令也用它）")
        self.email_edit.setMinimumHeight(36)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("6 位数字（点右边「获取验证码」）")
        self.code_edit.setMinimumHeight(36)
        self.code_edit.setMaxLength(6)
        self.code_edit.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^[0-9]{0,6}$"), self))
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("口令（建议 8 位以上）")
        self.password_edit.setMinimumHeight(36)
        self.invite_edit = QLineEdit()
        self.invite_edit.setPlaceholderText("邀请码（服务器开放注册时才需要，可空）")
        self.invite_edit.setMinimumHeight(36)

        self._add_caption("用户名")
        self.body.addWidget(self.name_edit)

        self._add_caption("邮箱")
        email_row = QHBoxLayout()
        email_row.setSpacing(8)
        email_row.addWidget(self.email_edit, 1)
        self.send_code_button = SoftButton(
            "获取验证码", "ghost", height=36, min_width=116, point_size=8.8)
        self.send_code_button.setToolTip("点一下，6 位验证码会发到这个邮箱，10 分钟内有效")
        self.send_code_button.clicked.connect(self.request_code)
        email_row.addWidget(self.send_code_button)
        self.body.addLayout(email_row)

        self._add_caption("邮箱验证码")
        self.body.addWidget(self.code_edit)

        self._add_caption("口令")
        self.body.addWidget(self.password_edit)

        self._add_caption("邀请码")
        self.body.addWidget(self.invite_edit)

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
        self.email_edit.returnPressed.connect(self.request_code)
        self.code_edit.returnPressed.connect(self.submit)
        self.password_edit.returnPressed.connect(self.submit)
        self.invite_edit.returnPressed.connect(self.submit)

    def _add_caption(self, text: str) -> None:
        caption = QLabel(text)
        caption.setObjectName("Small")
        self.body.addWidget(caption)

    # -------------------------------------------------- 获取验证码
    def request_code(self) -> None:
        """「获取验证码」：先本地校验邮箱，再后台发请求（不阻塞界面）。"""
        if self._code_call.is_busy():
            return
        if self._code_wait > 0:
            # 冷却里再点（或在邮箱框里按回车）就别真发了，服务器反正会 429
            self._set_status("验证码刚发过，%d 秒后才能再获取" % self._code_wait,
                             theme.MUTED)
            return
        email = self.email_edit.text().strip()
        if not email:
            self._set_status("先填邮箱地址，验证码会发到这个邮箱", theme.WARNING)
            self.email_edit.setFocus()
            return
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            self._set_status("邮箱要填完整（比如 name@example.com）", theme.WARNING)
            self.email_edit.setFocus()
            return
        self._set_status("正在发送验证码…", theme.MUTED)
        # 先按住按钮起倒计时（防止连点）；请求失败（服务器没配发信 / 网络不通）再解开
        self._start_code_countdown(CODE_WAIT_SECONDS)
        if not self._code_call.run(account.request_email_code, email, self.store):
            self._stop_code_countdown()
            self._set_status("上一次请求还没回来，请稍等", theme.WARNING)

    def _start_code_countdown(self, seconds: int) -> None:
        try:
            self._code_wait = max(1, int(seconds or CODE_WAIT_SECONDS))
        except (TypeError, ValueError):
            self._code_wait = CODE_WAIT_SECONDS
        self.send_code_button.setText("重新获取(%ds)" % self._code_wait)
        self.send_code_button.setEnabled(False)
        self._code_timer.start()

    def _tick_code_countdown(self) -> None:
        if self._code_wait > 0:
            self._code_wait -= 1
        if self._code_wait <= 0:
            self._stop_code_countdown()
            return
        self.send_code_button.setText("重新获取(%ds)" % self._code_wait)

    def _stop_code_countdown(self) -> None:
        try:
            self._code_timer.stop()
        except Exception:
            pass
        self._code_wait = 0
        self.send_code_button.setText("获取验证码")
        self.send_code_button.setEnabled(True)

    def _on_code_finished(self, result: Any) -> None:
        if not isinstance(result, dict):
            self._stop_code_countdown()
            self._set_status("验证码发不出去：服务器返回了看不懂的东西", theme.DANGER)
            return
        if result.get("ok"):
            try:
                cooldown = int(result.get("cooldown") or CODE_WAIT_SECONDS)
            except (TypeError, ValueError):
                cooldown = CODE_WAIT_SECONDS
            self._start_code_countdown(max(1, cooldown))
            self.code_edit.setFocus()
            email = str(result.get("email") or self.email_edit.text().strip())
            if result.get("already_registered"):
                self._set_status(
                    "这个邮箱已经注册过了：换一个邮箱，或者直接用它在登录页登录"
                    "（忘记口令就找管理员）", theme.WARNING)
                return
            minutes = ""
            try:
                expires = int(result.get("expires_in") or 0)
            except (TypeError, ValueError):
                expires = 0
            if expires > 0:
                minutes = "，%d 分钟内有效" % max(1, expires // 60)
            self._set_status(
                "验证码已发到 %s%s，请查收邮件（也看一眼垃圾箱）" % (email, minutes),
                theme.SUCCESS)
            return
        self._stop_code_countdown()
        message = str(result.get("error") or "验证码发不出去")
        if result.get("offline"):
            message = "服务器不可达：%s" % message
        elif str(result.get("code") or "") in ("email_cooldown", "too_many_requests"):
            # 服务器说还要等几秒：按钮也跟着按住，别让人白点
            try:
                wait = int(result.get("retry_after") or 0)
            except (TypeError, ValueError):
                wait = 0
            if wait > 0:
                self._start_code_countdown(wait)
        self._set_status(message, theme.DANGER)

    def _on_code_failed(self, message: str) -> None:
        self._stop_code_countdown()
        self._set_status("验证码发不出去：%s" % message, theme.DANGER)

    # -------------------------------------------------- 提交
    def submit(self) -> None:
        if self._call.is_busy():
            return
        name = self.name_edit.text().strip()
        email = self.email_edit.text().strip()
        password = self.password_edit.text()
        code = self.code_edit.text().strip()
        if not name:
            self._set_status("先填一个用户名", theme.WARNING)
            return
        if not email:
            self._set_status("先填邮箱地址，验证码会发到这个邮箱", theme.WARNING)
            self.email_edit.setFocus()
            return
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            self._set_status("邮箱要填完整（比如 name@example.com）", theme.WARNING)
            self.email_edit.setFocus()
            return
        if len(code) != 6 or not code.isdigit():
            self._set_status(
                "邮箱验证码是 6 位数字：先点「获取验证码」，再去邮件里找", theme.WARNING)
            self.code_edit.setFocus()
            return
        if len(password) < 6:
            self._set_status("口令太短了（至少 6 位）", theme.WARNING)
            return
        self.register_button.setEnabled(False)
        self._set_status("正在注册…", theme.MUTED)
        started = self._call.run(account.register, name, email, password,
                                 self.invite_edit.text().strip(), self.store, code)
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
        code = str(result.get("code") or "")
        if result.get("offline"):
            message = ("服务器不可达：%s\n可以先用「邮件申请注册」走老的注册流程。"
                       % message)
        elif code == "device_limit":
            used = result.get("devices_used")
            limit = result.get("device_limit")
            message = ("登录设备数超过上限（%s/%s）：先在别的设备上退出，"
                       "或者在设置页「设备管理」里踢掉一台。" % (used, limit))
        elif code in ("invalid_code", "code_expired"):
            # 验证码不对 / 过期：把焦点放回验证码框，过期的顺手清掉
            if code == "code_expired":
                self.code_edit.clear()
            self.code_edit.setFocus()
        self._set_status(message, theme.DANGER)

    def _on_failed(self, message: str) -> None:
        self.register_button.setEnabled(True)
        self._set_status("注册失败：%s" % message, theme.DANGER)


# ====================================================================== 设备
class ServerLoginDialog(BaseDialog):
    """设置页里直接登录服务器账号（不用退出本机登录再重开应用）。"""

    def __init__(self, store, parent=None):
        super().__init__(
            parent, width=460, height=330,
            title="登录 DanLume 账号",
            subtitle="登录后本机数据不变；账号、设备管理、服务器公告走服务器",
            icon=icons.LOCK,
        )
        self.store = store
        self.session_payload: Dict[str, Any] = {}
        self._call = account.AsyncCall(self)
        self._call.finished.connect(self._on_finished)
        self._call.failed.connect(self._on_failed)

        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("用户名或邮箱")
        self.account_edit.setFixedHeight(34)
        self.body.addWidget(self.account_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("口令")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setFixedHeight(34)
        self.body.addWidget(self.password_edit)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_edit = QLineEdit()
        self.url_edit.setFixedHeight(32)
        try:
            self.url_edit.setText(account.server_url(store))
        except Exception:
            self.url_edit.setText(account.DEFAULT_SERVER_URL)
        url_row.addWidget(self.url_edit, 1)
        self.store_button = SoftButton("保存地址", "ghost", height=32,
                                       min_width=88, point_size=8.4)
        self.store_button.clicked.connect(self._save_url)
        url_row.addWidget(self.store_button)
        self.body.addLayout(url_row)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        self.cancel_button = SoftButton("取消", "ghost", height=36, min_width=88)
        self.cancel_button.clicked.connect(self.reject)
        self.login_button = SoftButton("登录", "primary", height=36, min_width=104)
        self.login_button.clicked.connect(self.submit)
        self.add_footer([], [self.cancel_button, self.login_button])
        self.password_edit.returnPressed.connect(self.submit)

    def _save_url(self) -> None:
        try:
            account.set_server_url(self.url_edit.text().strip(), self.store)
            self._set_status("服务器地址已保存", theme.MUTED)
        except Exception as exc:  # noqa: BLE001
            self._set_status("保存失败：%s" % exc, theme.DANGER)

    def _set_status(self, text: str, color: str) -> None:
        self.status.setStyleSheet(f"color: {color};")
        self.status.setText(text)

    def submit(self) -> None:
        if self._call.is_busy():
            return
        user = self.account_edit.text().strip()
        password = self.password_edit.text()
        if not user or not password:
            self._set_status("用户名和口令都要填", theme.WARNING)
            return
        self._save_url()
        self.login_button.setEnabled(False)
        self._set_status("正在登录…", theme.MUTED)
        if not self._call.run(account.login, user, password, self.store):
            self.login_button.setEnabled(True)

    def _on_finished(self, result: Any) -> None:
        self.login_button.setEnabled(True)
        if not isinstance(result, dict):
            self._set_status("登录失败：返回内容看不懂", theme.DANGER)
            return
        if not result.get("ok"):
            self._set_status(str(result.get("error") or "登录失败"), theme.DANGER)
            return
        self.session_payload = result
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.login_button.setEnabled(True)
        self._set_status("登录失败：%s" % message, theme.DANGER)


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
