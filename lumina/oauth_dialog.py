"""微软 OAuth2 授权窗口（设备代码流程）。

流程：点「开始授权」→ 窗口显示一段 **8 位代码** 和一个网址 → 用户在浏览器里
输入代码、登录、点「允许」→ 程序自动拿到 refresh token，窗口变成「已授权」。
"""
from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from . import icons, oauth2, theme
from .utils import open_external
from .widgets import BaseDialog, SoftButton


class _Worker(QObject):
    status = Signal(str)
    done = Signal(str)          # 空串 = 成功
    code_ready = Signal(dict)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            info = oauth2.start_device_flow(self.store)
            self.code_ready.emit(info)
            oauth2.poll_device_flow(
                self.store, info["device_code"], info["interval"],
                info["expires_in"], should_stop=lambda: self._stop,
                on_tick=self.status.emit)
            self.done.emit("")
        except oauth2.OAuthError as exc:
            self.done.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.done.emit(f"授权失败：{exc}")


class OAuthDialog(BaseDialog):
    """微软账号授权（Outlook / Hotmail 发信用）。"""

    def __init__(self, store, parent=None):
        super().__init__(
            parent, width=470, height=330,
            title="微软授权（OAuth2）",
            subtitle="发信不再用密码，改用微软的授权码；只需要授权一次",
            icon=icons.LOCK,
        )
        self.store = store
        self.worker: Optional[_Worker] = None
        self.authorized = False

        client_row = QHBoxLayout()
        client_row.setSpacing(8)
        label = QLabel("客户端 ID")
        label.setObjectName("Small")
        client_row.addWidget(label)
        self.client_edit = QLineEdit(oauth2.client_id(store))
        self.client_edit.setPlaceholderText("Azure 门户 → 应用注册 → 应用程序(客户端) ID")
        client_row.addWidget(self.client_edit, 1)
        save_button = SoftButton("保存", "ghost", height=30, min_width=64,
                                 point_size=8.6)
        save_button.clicked.connect(self._save_client)
        client_row.addWidget(save_button)
        self.body.addLayout(client_row)

        tenant_row = QHBoxLayout()
        tenant_row.setSpacing(8)
        tenant_label = QLabel("租户")
        tenant_label.setObjectName("Small")
        tenant_row.addWidget(tenant_label)
        self.tenant_edit = QLineEdit(oauth2.tenant(store))
        self.tenant_edit.setPlaceholderText("个人账号填 consumers；学校/公司账号填租户名或 ID")
        tenant_row.addWidget(self.tenant_edit, 1)
        tenant_save = SoftButton("保存", "ghost", height=30, min_width=64,
                                 point_size=8.6)
        tenant_save.clicked.connect(self._save_tenant)
        tenant_row.addWidget(tenant_save)
        self.body.addLayout(tenant_row)

        self.code_label = QLabel("")
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setStyleSheet("font-size: 22px; font-weight: 700;"
                                      " letter-spacing: 3px;")
        self.body.addWidget(self.code_label)

        self.hint = QLabel(
            "点「开始授权」→ 浏览器里输入上面的代码并登录 → 点「允许」，这里会自动完成。")
        self.hint.setObjectName("Tiny")
        self.hint.setWordWrap(True)
        self.body.addWidget(self.hint)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        self.start_button = SoftButton("开始授权", "primary", height=34,
                                       min_width=104, point_size=8.8)
        self.start_button.clicked.connect(self._start)
        self.open_button = SoftButton("打开授权网页", "ghost", height=34,
                                      min_width=120, point_size=8.8)
        self.open_button.clicked.connect(self._open_page)
        self.open_button.setEnabled(False)
        self.clear_button = SoftButton("清除授权", "ghost", height=34,
                                       min_width=92, point_size=8.8)
        self.clear_button.clicked.connect(self._clear)
        close_button = SoftButton("关闭", "ghost", height=34, min_width=80,
                                  point_size=8.8)
        close_button.clicked.connect(self.reject)
        self.add_footer([self.clear_button],
                        [self.open_button, self.start_button, close_button])
        self._url = ""
        self._refresh_status()

    # -------------------------------------------------- 设置
    def _save_client(self) -> None:
        oauth2.set_client_id(self.store, self.client_edit.text())
        self._info("客户端 ID 已保存")

    def _save_tenant(self) -> None:
        oauth2.set_tenant(self.store, self.tenant_edit.text())
        self._info("租户已保存")

    def _refresh_status(self) -> None:
        if oauth2.has_refresh_token(self.store):
            self.status.setStyleSheet(f"color: {theme.SUCCESS};")
            self.status.setText("已授权：以后发信会自动续期，不用再点授权。")
        elif not oauth2.configured(self.store):
            self.status.setStyleSheet(f"color: {theme.MUTED};")
            self.status.setText("还没有客户端 ID：先填一个（Azure 门户免费注册）。")
        else:
            self.status.setText("还没有授权，点「开始授权」。")

    def _info(self, text: str) -> None:
        self.status.setStyleSheet(f"color: {theme.SUCCESS};")
        self.status.setText(text)

    def _warn(self, text: str) -> None:
        self.status.setStyleSheet(f"color: {theme.DANGER};")
        self.status.setText(text)

    # -------------------------------------------------- 授权
    def _start(self) -> None:
        if self.worker is not None:
            return
        oauth2.set_client_id(self.store, self.client_edit.text())
        oauth2.set_tenant(self.store, self.tenant_edit.text())
        if not oauth2.configured(self.store):
            self._warn("先填客户端 ID")
            return
        self.start_button.setEnabled(False)
        self.status.setText("正在向微软申请设备码…")
        worker = _Worker(self.store, self)
        worker.code_ready.connect(self._on_code)
        worker.status.connect(lambda text: self.status.setText(text))
        worker.done.connect(self._on_done)
        self.worker = worker
        threading.Thread(target=worker.run, name="lumina-oauth", daemon=True).start()

    def _on_code(self, info: dict) -> None:
        self.code_label.setText(info.get("user_code", ""))
        self._url = str(info.get("verification_uri") or "")
        self.open_button.setEnabled(bool(self._url))
        self.hint.setText(
            "在浏览器里打开下面的网址，输入上面的代码，登录并点「允许」：\n"
            f"{self._url}")
        try:
            QApplication.clipboard().setText(info.get("user_code", ""))
        except Exception:
            pass
        self._open_page()

    def _open_page(self) -> None:
        if self._url:
            __import__("lumina.utils", fromlist=["open_external"]).open_external(self._url)

    def _on_done(self, error: str) -> None:
        self.start_button.setEnabled(True)
        self.worker = None
        if error:
            self._warn(error)
            return
        self.authorized = True
        self._refresh_status()
        self.hint.setText("授权完成，可以关掉这个窗口去发信了。")

    def _clear(self) -> None:
        oauth2.clear(self.store)
        self.code_label.setText("")
        self._refresh_status()
        self._info("已清除授权（下次发信要重新授权）")

    def reject(self) -> None:
        if self.worker is not None:
            self.worker.stop()
        super().reject()
