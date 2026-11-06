"""发微信消息窗口：选人 → 预览确认 → 发送（会真的发出去，所以必须确认两次）。"""
from __future__ import annotations

from typing import Any, Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QVBoxLayout,
)

from . import theme, wechat_send
from .widgets import SoftButton


def _colors() -> Dict[str, str]:
    mode = "dark" if theme.effective_mode() == "dark" else "light"
    return theme.PALETTES.get(mode) or theme.PALETTES["light"]


class WechatSendDialog(QDialog):
    """给 agent 用的「发微信给某人」：先预览（只搜索不发送），确认后再发。"""

    def __init__(self, store, parent=None, contact: str = "", text: str = ""):
        super().__init__(parent)
        self.store = store
        self._preview: Dict[str, Any] = {}
        colors = _colors()
        self.setWindowTitle("DanLume · 发微信消息")
        self.resize(560, 470)
        self.setStyleSheet(f"QDialog {{ background: {colors['CARD']}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("发微信消息")
        title.setObjectName("H2")
        root.addWidget(title)
        hint = QLabel(
            "流程：① 填联系人和内容 ② 点「预览会话」（只会搜索并读会话标题，不发消息）"
            "③ 标题对得上再点「发送」。内容通过剪贴板粘贴，中文和表情都不会乱。")
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel("发给")
        label.setObjectName("Small")
        row.addWidget(label)
        self.contact_edit = QLineEdit(contact)
        self.contact_edit.setPlaceholderText("联系人/群名，要写微信里显示的名字")
        self.contact_edit.setFixedHeight(32)
        row.addWidget(self.contact_edit, 1)
        root.addLayout(row)

        self.text_edit = QPlainTextEdit(text)
        self.text_edit.setPlaceholderText("要发的内容…")
        self.text_edit.setFixedHeight(140)
        root.addWidget(self.text_edit)

        self.confirm_box = QCheckBox("我知道点「发送」会真的把消息发出去")
        root.addWidget(self.confirm_box)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        buttons.addWidget(self.status, 1)
        self.preview_button = SoftButton("预览会话", "ghost", height=32,
                                         min_width=96, point_size=8.8)
        self.preview_button.clicked.connect(self.preview)
        buttons.addWidget(self.preview_button)
        self.send_button = SoftButton("发送", "primary", height=32,
                                      min_width=84, point_size=8.8)
        self.send_button.clicked.connect(self.send)
        self.send_button.setEnabled(False)
        buttons.addWidget(self.send_button)
        close_button = SoftButton("关闭", "ghost", height=32, min_width=72,
                                  point_size=8.8)
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        if not wechat_send.available():
            self.preview_button.setEnabled(False)
            self.status.setText("现在用不了：要先打开微信（并且系统有中文 OCR）")

    # -------------------------------------------------- 预览
    def preview(self) -> None:
        name = self.contact_edit.text().strip()
        self.status.setText("正在搜索会话…")
        self.preview_button.setEnabled(False)
        try:
            self._preview = wechat_send.preview(name)
        except Exception as exc:  # noqa: BLE001
            self._preview = {"ok": False, "reason": f"预览失败：{exc}", "title": ""}
        finally:
            self.preview_button.setEnabled(True)
        if self._preview.get("ok"):
            self.status.setText(f"✓ 会话是「{self._preview.get('title')}」，可以发送")
            self.send_button.setEnabled(True)
        else:
            self.status.setText(f"✗ {self._preview.get('reason') or '对不上'}")
            self.send_button.setEnabled(False)

    # -------------------------------------------------- 发送
    def send(self) -> None:
        name = self.contact_edit.text().strip()
        body = self.text_edit.toPlainText().strip()
        if not self._preview.get("ok"):
            self.status.setText("先点「预览会话」确认发给谁")
            return
        if not self.confirm_box.isChecked():
            self.status.setText("勾一下「我知道会真的发出去」")
            return
        if not body:
            self.status.setText("内容是空的")
            return
        self.send_button.setEnabled(False)
        self.status.setText("正在发送…")
        try:
            result = wechat_send.send(
                name, body, expect_title=str(self._preview.get("title") or ""))
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "detail": f"发送失败：{exc}"}
        finally:
            self.send_button.setEnabled(True)
        detail = str(result.get("detail") or "")
        self.status.setText(("✓ " if result.get("ok") else "✗ ") + detail)
        if result.get("ok"):
            self.text_edit.clear()
            self.confirm_box.setChecked(False)
