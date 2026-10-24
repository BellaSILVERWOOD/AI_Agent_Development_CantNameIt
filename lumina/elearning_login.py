"""eLearning 登录窗：可见的内嵌浏览器，登录后点「读取我的课程」。"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from . import elearning


def make_login_dialog(parent=None, on_ready: Optional[Callable[[], None]] = None):
    """返回一个对话框：里面是 eLearning 登录页，登录后点按钮关闭。"""
    try:
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception as exc:  # noqa: BLE001
        raise elearning.ElearningError(f"当前环境缺少 QtWebEngine：{exc}") from exc

    from . import fdu, theme
    from .widgets import SoftButton

    dialog = QDialog(parent)
    dialog.setWindowTitle("登录 eLearning（Canvas）")
    dialog.resize(1080, 800)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(8)

    tip = QLabel(
        "在下面用统一身份认证登录 eLearning。登录成功后点右下角「读取我的课程」；"
        "登录态保存在程序的 data/browser 目录里，下次一般不用再登录。"
    )
    tip.setWordWrap(True)
    tip.setObjectName("Small")
    layout.addWidget(tip)

    storage = fdu.browser_dir("elearning")
    profile = QWebEngineProfile(elearning.PROFILE_NAME, dialog)
    profile.setPersistentStoragePath(str(storage))
    profile.setCachePath(str(storage / "cache"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    view = QWebEngineView(dialog)
    page = QWebEnginePage(profile, view)
    view.setPage(page)
    view.setUrl(QUrl(elearning.BASE_URL + "/"))
    def _cleanup(_code: int = 0) -> None:
        try:
            page.deleteLater()
        except Exception:
            pass

    dialog.finished.connect(_cleanup)
    layout.addWidget(view, 1)

    status = QLabel("等待登录…")
    status.setObjectName("Tiny")
    layout.addWidget(status)

    row = QHBoxLayout()
    row.addStretch(1)
    ok_button = SoftButton("读取我的课程", "primary", height=34, min_width=132)
    cancel_button = SoftButton("关闭", "ghost", height=34, min_width=84)
    cancel_button.clicked.connect(dialog.reject)

    def _ready() -> None:
        status.setText("正在验证登录状态…")
        if on_ready is not None:
            try:
                on_ready(view)
            except Exception as exc:  # noqa: BLE001
                status.setText(f"还没登录成功：{exc}")
                return
        dialog.accept()

    ok_button.clicked.connect(_ready)
    row.addWidget(ok_button)
    row.addWidget(cancel_button)
    layout.addLayout(row)

    dialog.setStyleSheet(
        f"QDialog {{ background: {theme.appearance().card_hex()}; }}"
        f"QLabel {{ color: {theme.appearance().accent_deep_q().name()}; }}"
    )
    return dialog
