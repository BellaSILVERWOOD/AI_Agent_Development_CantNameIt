"""发件邮箱设置框（未注册用户也能用）。

为什么单独做一个：**注册本身要靠发件邮箱**（把加密后的申请发到管理员邮箱），
而设置页是登录后才能进的。所以未注册的「游客模式」和注册页里都要能配发件邮箱。

保存方式跟设置页一致：`accounts` 列表 + `accounts.set_password`（DPAPI 加密）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from . import accounts, icons, theme
from .mailer import SmtpConfig, SmtpTester  # noqa: F401  (SmtpTester 由 sending 提供)
from .widgets import BaseDialog, Select, SoftButton

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

#: 常见邮箱服务：填了域名就自动带出服务器
PRESETS = [
    {"match": "m.fudan.edu.cn", "name": "复旦学邮（腾讯企业邮）",
     "host": "smtp.exmail.qq.com", "port": 465, "security": "ssl"},
    {"match": "fudan.edu.cn", "name": "复旦邮箱", "host": "smtp.fudan.edu.cn",
     "port": 465, "security": "ssl"},
    {"match": "outlook.com", "name": "Outlook", "host": "smtp.office365.com",
     "port": 587, "security": "starttls"},
    {"match": "hotmail.com", "name": "Outlook", "host": "smtp.office365.com",
     "port": 587, "security": "starttls"},
    {"match": "qq.com", "name": "QQ 邮箱", "host": "smtp.qq.com",
     "port": 465, "security": "ssl"},
    {"match": "163.com", "name": "网易 163", "host": "smtp.163.com",
     "port": 465, "security": "ssl"},
    {"match": "126.com", "name": "网易 126", "host": "smtp.126.com",
     "port": 465, "security": "ssl"},
    {"match": "gmail.com", "name": "Gmail", "host": "smtp.gmail.com",
     "port": 465, "security": "ssl"},
    {"match": "aliyun.com", "name": "阿里邮箱", "host": "smtp.aliyun.com",
     "port": 465, "security": "ssl"},
]

SECURITY_OPTIONS = [("SSL/TLS（465）", "ssl"), ("STARTTLS（587）", "starttls"),
                    ("不加密（25）", "none")]


def preset_for(email: str) -> Optional[Dict[str, Any]]:
    text = str(email or "").lower()
    for item in PRESETS:
        if text.endswith(item["match"]):
            return item
    return None


class _TestWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, cfg: SmtpConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self) -> None:
        try:
            from .sending import SmtpTester  # noqa: F401  避免循环导入

            from .mailer import test_connection

            ok, message = test_connection(self.cfg)
        except Exception as exc:  # noqa: BLE001
            ok, message = False, str(exc)
        self.done.emit(bool(ok), str(message))


class SmtpSetupDialog(BaseDialog):
    """填发件邮箱 → 测试 → 保存。"""

    def __init__(self, store, parent=None):
        super().__init__(parent, width=560, height=470, title="发件邮箱设置",
                         subtitle="注册申请、汇报邮件都从这里发出去", icon=icons.MAIL)
        self.store = store
        self._worker: Optional[_TestWorker] = None

        current = self._current_account()
        body = QVBoxLayout()
        body.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        email_label = QLabel("发件邮箱")
        email_label.setObjectName("Small")
        row.addWidget(email_label)
        self.email_edit = QLineEdit(str(current.get("sender_email") or ""))
        self.email_edit.setPlaceholderText("例如 yourname@m.fudan.edu.cn")
        self.email_edit.editingFinished.connect(self._apply_preset)
        row.addWidget(self.email_edit, 1)
        body.addLayout(row)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_label = QLabel("发件人名称")
        name_label.setObjectName("Small")
        name_row.addWidget(name_label)
        self.name_edit = QLineEdit(str(current.get("sender_name") or ""))
        self.name_edit.setPlaceholderText("显示在收件人那里，可留空")
        name_row.addWidget(self.name_edit, 1)
        body.addLayout(name_row)

        host_row = QHBoxLayout()
        host_row.setSpacing(8)
        host_label = QLabel("服务器")
        host_label.setObjectName("Small")
        host_row.addWidget(host_label)
        self.host_edit = QLineEdit(str(current.get("host") or ""))
        self.host_edit.setPlaceholderText("smtp.exmail.qq.com")
        host_row.addWidget(self.host_edit, 2)
        port_label = QLabel("端口")
        port_label.setObjectName("Small")
        host_row.addWidget(port_label)
        self.port_edit = QLineEdit(str(current.get("port") or 465))
        self.port_edit.setFixedWidth(70)
        host_row.addWidget(self.port_edit)
        self.security_select = Select()
        self.security_select.setFixedWidth(150)
        for text, value in SECURITY_OPTIONS:
            self.security_select.addItem(text, value)
        index = self.security_select.findData(str(current.get("security") or "ssl"))
        if index >= 0:
            self.security_select.setCurrentIndex(index)
        host_row.addWidget(self.security_select)
        body.addLayout(host_row)

        pwd_row = QHBoxLayout()
        pwd_row.setSpacing(8)
        pwd_label = QLabel("密码 / 授权码")
        pwd_label.setObjectName("Small")
        pwd_row.addWidget(pwd_label)
        has_password = bool(current.get("id")) and bool(
            accounts.password(store, str(current.get("id"))))
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText(
            "已保存（不可查看），留空表示不修改" if has_password else
            "填邮箱密码或授权码（只加密存在本机）")
        pwd_row.addWidget(self.password_edit, 1)
        body.addLayout(pwd_row)

        hint = QLabel("提示：QQ / 腾讯企业邮要先在网页版里开启 SMTP 并拿到「授权码」，"
                      "用它当密码；复旦学邮的服务器是 smtp.exmail.qq.com。")
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        body.addWidget(hint)
        self.body.addLayout(body)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        self.test_button = SoftButton("测试连接", "ghost", height=34, min_width=100)
        self.test_button.clicked.connect(self._test)
        cancel = SoftButton("取消", "ghost", height=34, min_width=80)
        cancel.clicked.connect(self.reject)
        save = SoftButton("保存", "primary", height=34, min_width=96)
        save.clicked.connect(self._save)
        self.add_footer([self.test_button], [cancel, save])
        self.saved = False
        self._apply_preset()

    # -------------------------------------------------- 数据
    def _current_account(self) -> Dict[str, Any]:
        try:
            active = accounts.active(self.store)
            if active:
                return dict(active)
            items = accounts.load(self.store)
            if items:
                return dict(items[0])
        except Exception:
            pass
        return {}

    def _config(self) -> SmtpConfig:
        try:
            port = int(self.port_edit.text().strip() or 465)
        except (TypeError, ValueError):
            port = 465
        email = self.email_edit.text().strip()
        return SmtpConfig(
            host=self.host_edit.text().strip(),
            port=max(1, min(65535, port)),
            security=str(self.security_select.currentData() or "ssl"),
            user=self.host_edit.text().strip() and email,
            password=self.password_edit.text(),
            sender_name=self.name_edit.text().strip(),
            sender_email=email,
        )

    def _apply_preset(self) -> None:
        preset = preset_for(self.email_edit.text())
        if preset is None:
            return
        if not self.host_edit.text().strip():
            self.host_edit.setText(preset["host"])
            self.port_edit.setText(str(preset["port"]))
            index = self.security_select.findData(preset["security"])
            if index >= 0:
                self.security_select.setCurrentIndex(index)

    # -------------------------------------------------- 动作
    def _test(self) -> None:
        cfg = self._config()
        if not cfg.sender_email:
            self.status.setText("先填发件邮箱")
            return
        if not cfg.host:
            self.status.setText("先填服务器地址")
            return
        existing = self._current_account()
        if not cfg.password and existing.get("id"):
            cfg.password = accounts.password(self.store, str(existing["id"]))
        if not cfg.password:
            self.status.setText("先填密码 / 授权码")
            return
        self.status.setText("正在测试连接…")
        self.test_button.setEnabled(False)
        worker = _TestWorker(cfg, self)
        worker.done.connect(self._on_tested)
        worker.done.connect(worker.deleteLater)
        worker.start()
        self._worker = worker

    def _on_tested(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self.status.setStyleSheet(f"color: {theme.SUCCESS if ok else theme.DANGER};")
        self.status.setText(("连接正常：" if ok else "连不上：") + message)

    def _save(self) -> None:
        email = self.email_edit.text().strip()
        host = self.host_edit.text().strip()
        if not email:
            self.status.setText("发件邮箱不能空")
            return
        if not EMAIL_RE.match(email):
            self.status.setText("发件邮箱格式不对")
            return
        if not host:
            self.status.setText("服务器地址不能空")
            return
        try:
            port = int(self.port_edit.text().strip() or 465)
        except (TypeError, ValueError):
            port = 465
        security = str(self.security_select.currentData() or "ssl")
        try:
            items = accounts.load(self.store)
            current = accounts.active(self.store)
            target = None
            for item in items:
                if current and item.get("id") == current.get("id"):
                    target = item
                    break
            if target is None and items:
                target = items[0]
            if target is None:
                target = accounts.blank_account(email)
                items.append(target)
            target.update({
                "sender_email": email,
                "sender_name": self.name_edit.text().strip(),
                "host": host,
                "port": max(1, min(65535, port)),
                "security": security,
                "user": email,
                "label": target.get("label") or email,
            })
            accounts.save(self.store, items, active_id=str(target["id"]))
            password = self.password_edit.text()
            if password:
                accounts.set_password(self.store, str(target["id"]), password)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"保存失败：{exc}")
            return
        self.saved = True
        self.accept()
