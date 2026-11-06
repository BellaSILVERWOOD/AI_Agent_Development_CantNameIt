"""登录窗口：本机账户验证 + 「记住这台设备 24 / 48 小时」。

* 第一次运行（还没有任何账户）→ 变成「创建本机账户」；
* 之后每次启动：选账户 + 输密码，本地比对（不联网）；
* 勾了「记住这台设备」，期内的下次启动直接进入，不再问密码；
* 忘记密码：只能清空账户（日程 / 邮件等数据不动），窗口里给了这个紧急出口。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QStackedWidget, QVBoxLayout, QWidget,
)

from . import account_rescue, accounts, icons, theme, users
from .config import DATA_DIR, data_dir_source, set_data_dir_pointer
from .widgets import BaseDialog, ConfirmDialog, IconButton, Select, SoftButton


class LoginWindow(BaseDialog):
    """返回 Accepted 时，`user` 是登录成功的账户，`remember_minutes` 是记住时长。"""

    def __init__(self, store, parent=None):
        super().__init__(
            parent, width=460, height=520,
            title="登录 DanLume",
            subtitle="账户与密码只存在这台电脑上，不会上传到任何地方",
            icon=icons.LOCK,
        )
        self.store = store
        self.user: Optional[Dict[str, Any]] = None
        self.remember_minutes = 0
        self._show_password = False

        self.stack = QStackedWidget()
        self.body.addWidget(self.stack)
        self.stack.addWidget(self._build_login_page())
        self.stack.addWidget(self._build_create_page())
        self.stack.addWidget(self._build_register_page())

        self.error = QLabel("")
        self.error.setObjectName("Tiny")
        self.error.setWordWrap(True)
        self.body.addWidget(self.error)

        # 没有账户 → 直接进创建页
        self.reload(initial=True)

    # -------------------------------------------------- 两个页面
    def _build_login_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        account_label = QLabel("账户")
        account_label.setObjectName("Small")
        layout.addWidget(account_label)
        # 账号可以直接输入（以前只能从下拉里选，机器上没记录时很别扭）；
        # 下拉里的名字会作为「补全候选」显示。
        account_row = QHBoxLayout()
        account_row.setSpacing(8)
        self.account_edit = QLineEdit()
        self.account_edit.setMinimumHeight(38)
        self.account_edit.setPlaceholderText("输入用户名（也可以点右边选已有账户）")
        self.account_edit.returnPressed.connect(self._submit)
        self.account_edit.textChanged.connect(self._refresh_remember_choices)
        account_row.addWidget(self.account_edit, 1)
        self.account_select = Select()
        self.account_select.setFixedWidth(150)
        self.account_select.setToolTip("这台机器上登录过的账户")
        self.account_select.currentIndexChanged.connect(self._use_selected_account)
        account_row.addWidget(self.account_select)
        layout.addLayout(account_row)

        password_label = QLabel("密码")
        password_label.setObjectName("Small")
        layout.addWidget(password_label)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("输入密码")
        self.password_edit.returnPressed.connect(self._submit)
        row.addWidget(self.password_edit, 1)
        self.eye_button = IconButton(icons.EYE, size=34, glyph_size=13,
                                     tip="显示 / 隐藏密码")
        self.eye_button.clicked.connect(self._toggle_eye)
        row.addWidget(self.eye_button)
        layout.addLayout(row)

        remember_label = QLabel("记住这台设备")
        remember_label.setObjectName("Small")
        layout.addWidget(remember_label)
        self.remember_select = Select()
        for minutes, label in users.REMEMBER_CHOICES:
            self.remember_select.addItem(label, minutes)
        self.remember_select.setMinimumHeight(36)
        layout.addWidget(self.remember_select)

        self.hint_trusted = QLabel("")
        self.hint_trusted.setObjectName("Tiny")
        self.hint_trusted.setWordWrap(True)
        layout.addWidget(self.hint_trusted)

        self.login_button = SoftButton("登录", "primary", height=38, min_width=110,
                                       point_size=9.2)
        self.login_button.clicked.connect(self._submit)
        layout.addWidget(self.login_button)

        links = QHBoxLayout()
        links.setSpacing(10)
        self.add_link = SoftButton("新建账户", "ghost", height=26, min_width=80,
                                   point_size=8.4)
        self.add_link.clicked.connect(lambda: self._set_mode(1))
        links.addWidget(self.add_link)
        self.register_link = SoftButton("注册账户", "ghost", height=26, min_width=80,
                                        point_size=8.4)
        self.register_link.setToolTip("向管理员申请一个新账户（需要管理员批准）")
        self.register_link.clicked.connect(lambda: self._set_mode(2))
        links.addWidget(self.register_link)
        self.forgot_link = SoftButton("忘记密码？", "ghost", height=26, min_width=86,
                                      point_size=8.4)
        self.forgot_link.clicked.connect(self._forgot_password)
        links.addWidget(self.forgot_link)
        links.addStretch(1)
        layout.addLayout(links)

        # 第二排：数据目录 / 找回管理员。这两件事都是「数据还在、但进不去」时用的。
        rescue_row = QHBoxLayout()
        rescue_row.setSpacing(10)
        self.rescue_link = SoftButton("找回管理员账户", "ghost", height=26,
                                      min_width=118, point_size=8.4)
        self.rescue_link.setToolTip(
            "这台电脑上有管理员密钥时可用：重建本机管理员 / 重设管理员密码，数据不动")
        self.rescue_link.clicked.connect(self._rescue_admin)
        self.rescue_link.setVisible(False)
        rescue_row.addWidget(self.rescue_link)
        self.datadir_link = SoftButton("换数据目录…", "ghost", height=26,
                                       min_width=96, point_size=8.4)
        self.datadir_link.setToolTip(
            "程序默认把数据放在自己目录下的 data\\；如果你有另一份数据（比如原来的库），"
            "在这里指过去，重启后就能看到那些账户")
        self.datadir_link.clicked.connect(self._choose_data_dir)
        rescue_row.addWidget(self.datadir_link)
        rescue_row.addStretch(1)
        layout.addLayout(rescue_row)

        # 数据目录提示：报「没有这个账户」时，先看这里用的是哪个库
        self.data_hint = QLabel("")
        self.data_hint.setObjectName("Tiny")
        self.data_hint.setWordWrap(True)
        layout.addWidget(self.data_hint)
        layout.addStretch(1)
        return page

    def _build_create_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        hint = QLabel("这台电脑上还没有账户，先建一个（密码只保存加盐哈希，不保存原文）")
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        name_label = QLabel("用户名")
        name_label.setObjectName("Small")
        layout.addWidget(name_label)
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("例如 lena")
        layout.addWidget(self.new_name)

        mail_label = QLabel("绑定邮箱（注册要用它收验证码）")
        mail_label.setObjectName("Small")
        layout.addWidget(mail_label)
        self.new_email = QLineEdit()
        self.new_email.setPlaceholderText("name@example.com")
        layout.addWidget(self.new_email)

        pw_label = QLabel(f"密码（至少 {users.MIN_PASSWORD} 位）")
        pw_label.setObjectName("Small")
        layout.addWidget(pw_label)
        self.new_password = QLineEdit()
        self.new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password.setPlaceholderText("输入密码")
        self.new_password.returnPressed.connect(self._submit)
        layout.addWidget(self.new_password)

        again_label = QLabel("再输一次")
        again_label.setObjectName("Small")
        layout.addWidget(again_label)
        self.new_password2 = QLineEdit()
        self.new_password2.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password2.setPlaceholderText("重复上面的密码")
        self.new_password2.returnPressed.connect(self._submit)
        layout.addWidget(self.new_password2)

        self.create_button = SoftButton("创建并进入", "primary", height=38,
                                        min_width=130, point_size=9.2)
        self.create_button.clicked.connect(self._submit)
        layout.addWidget(self.create_button)

        self.back_link = SoftButton("返回登录", "ghost", height=26, min_width=80,
                                    point_size=8.4)
        self.back_link.clicked.connect(lambda: self._set_mode(0))
        self.to_register_link = SoftButton("没有账户？去注册", "ghost", height=26,
                                           min_width=132, point_size=8.4)
        self.to_register_link.setToolTip("向管理员申请一个账户（需要批准）")
        self.to_register_link.clicked.connect(lambda: self._set_mode(2))
        back_row = QHBoxLayout()
        back_row.setSpacing(8)
        back_row.addWidget(self.back_link)
        back_row.addWidget(self.to_register_link)
        back_row.addStretch(1)
        layout.addLayout(back_row)
        layout.addStretch(1)
        return page

    # -------------------------------------------------- 注册页
    def _build_register_page(self) -> QWidget:
        from PySide6.QtWidgets import QPlainTextEdit

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        hint = QLabel(
            "注册要管理员批准：填好用户名和邮箱 → 发送注册申请 → "
            "管理员批准后会回一封带「6 位验证码 + 回执」的邮件。")
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.reg_name = QLineEdit()
        self.reg_name.setPlaceholderText("用户名")
        layout.addWidget(self.reg_name)
        self.reg_email = QLineEdit()
        self.reg_email.setPlaceholderText("你的邮箱（验证码会发到这里）")
        layout.addWidget(self.reg_email)

        send_row = QHBoxLayout()
        send_row.setSpacing(8)
        self.send_request_button = SoftButton("发送注册申请", "primary", height=32,
                                              min_width=136, point_size=8.8)
        self.send_request_button.clicked.connect(self._send_request)
        send_row.addWidget(self.send_request_button)
        self.copy_seal_button = SoftButton("复制申请内容", "ghost", height=32,
                                           min_width=120, point_size=8.6)
        self.copy_seal_button.setVisible(False)
        self.copy_seal_button.clicked.connect(self._copy_seal)
        send_row.addWidget(self.copy_seal_button)
        send_row.addStretch(1)
        layout.addLayout(send_row)

        self.reg_status = QLabel("")
        self.reg_status.setObjectName("Tiny")
        self.reg_status.setWordWrap(True)
        layout.addWidget(self.reg_status)

        self.seal_view = QPlainTextEdit()
        self.seal_view.setReadOnly(True)
        self.seal_view.setFixedHeight(64)
        self.seal_view.setVisible(False)
        self.seal_view.setStyleSheet("font-size: 10px;")
        layout.addWidget(self.seal_view)

        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("6 位验证码")
        self.code_edit.setMaxLength(6)
        layout.addWidget(self.code_edit)

        self.receipt_view = QPlainTextEdit()
        self.receipt_view.setPlaceholderText("把邮件里以 LUMINA-RECEIPT1. 开头的那一整行粘到这里")
        self.receipt_view.setFixedHeight(64)
        self.receipt_view.setStyleSheet("font-size: 10px;")
        layout.addWidget(self.receipt_view)

        self.verify_button = SoftButton("验证验证码与回执", "ghost", height=32,
                                        min_width=160, point_size=8.6)
        self.verify_button.clicked.connect(self._verify_registration)
        layout.addWidget(self.verify_button)

        self.reg_password = QLineEdit()
        self.reg_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.reg_password.setPlaceholderText(f"设置密码（至少 {users.MIN_PASSWORD} 位）")
        self.reg_password.setEnabled(False)
        layout.addWidget(self.reg_password)
        self.reg_password2 = QLineEdit()
        self.reg_password2.setEchoMode(QLineEdit.EchoMode.Password)
        self.reg_password2.setPlaceholderText("再输一次密码")
        self.reg_password2.setEnabled(False)
        layout.addWidget(self.reg_password2)

        self.finish_button = SoftButton("完成注册", "primary", height=32,
                                        min_width=120, point_size=8.8)
        self.finish_button.setEnabled(False)
        self.finish_button.clicked.connect(self._finish_registration)
        layout.addWidget(self.finish_button)

        back_row = QHBoxLayout()
        back = SoftButton("返回登录", "ghost", height=26, min_width=80, point_size=8.4)
        back.clicked.connect(lambda: self._set_mode(0))
        back_row.addWidget(back)
        back_row.addStretch(1)
        layout.addLayout(back_row)
        layout.addStretch(1)
        self._approved_payload: Optional[Dict[str, Any]] = None
        return page

    def _send_request(self) -> None:
        from . import registration

        try:
            result = registration.send_request(
                self.store, self.reg_name.text().strip(), self.reg_email.text().strip())
        except registration.RegisterError as exc:
            self._fail(str(exc))
            return
        if result.get("approved"):
            # 老用户换新机器：管理员程序当场批准了，验证码和回执直接填好，走下一步设密码
            try:
                self.code_edit.setText(str(result.get("code") or ""))
                self.receipt_view.setPlainText(str(result.get("receipt") or ""))
            except Exception:
                pass
            self.reg_status.setStyleSheet(f"color: {theme.SUCCESS};")
            self.reg_status.setText(
                "这台机器是新的，但「%s」已经批准过（沿用原来的有效期到 %s）："
                "管理员程序已自动通过，验证码和回执都填好了，直接设密码即可。"
                % (self.reg_name.text().strip() or "这个账户",
                   result.get("expires_at") or "长期"))
            self._verify_registration()
            if self._approved_payload is not None:
                # 验证成功会把提示改成「验证通过…」，这里把「自动通过」也说清楚
                self.reg_status.setText(
                    "这台机器是新的，但「%s」已经批准过：管理员程序已自动通过，"
                    "沿用原来的有效期到 %s。验证码和回执已填好，直接设密码即可。"
                    % (self.reg_name.text().strip() or "这个账户",
                       result.get("expires_at") or "长期"))
            return
        if result.get("sent"):
            self.reg_status.setStyleSheet(f"color: {theme.SUCCESS};")
            channel = str(result.get("channel") or "")
            if channel == "ip":
                self.reg_status.setText(
                    "申请已经**直接送到管理员的程序**里了（局域网直连，不用等邮件）。"
                    "管理员批准后，验证码和回执会发到你的邮箱。")
            else:
                self.reg_status.setText(
                    f"申请已发到管理员邮箱 {result['to']}，等管理员批准后把邮件里的"
                    "验证码和回执填在下面。")
        else:
            self.reg_status.setStyleSheet(f"color: {theme.WARNING};")
            note = str(result.get("error") or "没能自动发送")
            direct = str(result.get("direct_error") or "")
            if direct:
                note += f"（直连管理员也失败：{direct}）"
            self.reg_status.setText(note)
            self.seal_view.setPlainText(str(result.get("body") or ""))
            self.seal_view.setVisible(True)
            self.copy_seal_button.setVisible(True)

    def _copy_seal(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(self.seal_view.toPlainText())
            self.reg_status.setText("申请内容已复制，粘贴到邮件里发给管理员即可")
        except Exception:
            pass

    def _verify_registration(self) -> None:
        from . import adminsign, registration

        token = self.receipt_view.toPlainText().strip()
        code = self.code_edit.text().strip()
        if not token:
            self._fail("把邮件里的回执（LUMINA-RECEIPT1. 开头那一行）粘进来")
            return
        pub = adminsign.public_pem(self.store)
        if not pub:
            self._fail("程序里没有管理员公钥，没法验证回执")
            return
        try:
            payload = registration.prepare_verify(
                self.store, self.reg_name.text().strip(), token, pub)
        except (adminsign.SignError, registration.RegisterError) as exc:
            self._fail(str(exc))
            return
        if not registration.verify_code(payload, code):
            self._fail("验证码不对（看邮件里那 6 位数字）")
            return
        self._approved_payload = payload
        self.reg_password.setEnabled(True)
        self.reg_password2.setEnabled(True)
        self.finish_button.setEnabled(True)
        self.reg_password.setFocus()
        self.reg_status.setStyleSheet(f"color: {theme.SUCCESS};")
        left = str(payload.get("expires_at") or "")
        self.reg_status.setText(
            f"验证通过：账户「{payload.get('username')}」有效期到 {left or '长期'}，"
            "现在设置密码。")

    def _finish_registration(self) -> None:
        from . import registration

        if self._approved_payload is None:
            self._fail("先验证验证码和回执")
            return
        password = self.reg_password.text()
        if password != self.reg_password2.text():
            self._fail("两次输入的密码不一样")
            return
        try:
            user_id = registration.finish_registration(
                self.store, self._approved_payload, password)
        except (registration.RegisterError, users.AccountError) as exc:
            self._fail(str(exc))
            return
        self._enter(user_id, users.remember_minutes(self.store), first_time=True)

    def _register(self) -> None:
        """注册页按回车：没验证就先验证，验证过就提交。"""
        if self._approved_payload is None:
            self._verify_registration()
        else:
            self._finish_registration()

    # -------------------------------------------------- 状态
    def _is_client_build(self) -> bool:
        try:
            from . import adminsign

            return bool(adminsign.install_is_client(self.store))
        except Exception:
            return False

    def reload(self, initial: bool = False) -> None:
        accounts_list = users.list_users(self.store)
        self.account_select.blockSignals(True)
        self.account_select.clear()
        for item in accounts_list:
            self.account_select.addItem(item["username"], int(item["id"]))
        self.account_select.blockSignals(False)
        # 账号输入框：默认填上第一个账户，但用户可以改成任何名字
        try:
            if not self.account_edit.text().strip() and accounts_list:
                self.account_edit.setText(str(accounts_list[0]["username"]))
        except Exception:
            pass
        client = self._is_client_build()
        # 客户端版不能自己建账户，只能申请注册
        try:
            self.add_link.setVisible(not client)
        except Exception:
            pass
        self._refresh_data_hint()
        if not accounts_list:
            # 有管理员密钥却没有账户：留在登录页，用「找回管理员账户」重建，
            # 而不是傻乎乎地让人去「注册账户」等审批。
            if account_rescue.admin_key_available(self.store):
                self.back_link.setVisible(True)
                self._set_mode(0)
                self.error.setStyleSheet(f"color: {theme.WARNING};")
                self.error.setText("这个数据目录里没有账户，但管理员密钥还在："
                                   "点「找回管理员账户」重建一个即可。")
                return
            # 一个账户都没有：管理员版去建，客户端版直接去申请注册
            self.back_link.setVisible(False)
            self._set_mode(2 if client else 1)
            if client:
                self.reg_status.setStyleSheet(f"color: {theme.MUTED};")
                self.reg_status.setText(
                    "这份是客户端版：填用户名 + 学校邮箱 →「发送注册申请」，"
                    "等管理员批准后按邮件里的验证码完成注册。")
            return
        self.back_link.setVisible(True)
        self._refresh_remember_choices()
        if initial:
            self._set_mode(0)
        self.password_edit.setFocus()

    # -------------------------------------------------- 数据目录 / 找回
    def _accounts_summary(self) -> str:
        """一句话说清这个库里有哪些账户（报错时给人看）。"""
        try:
            names = [str(item.get("username")) for item in users.list_users(self.store)]
        except Exception:
            names = []
        if not names:
            return "没有任何账户"
        shown = "、".join(names[:6])
        if len(names) > 6:
            shown += "…（共 %d 个）" % len(names)
        return shown

    def _refresh_data_hint(self) -> None:
        """把「现在用的是哪个数据目录、里面有几个账户」写在窗口里。"""
        try:
            admins = account_rescue.local_admins(self.store)
            names = "、".join(str(item.get("username")) for item in admins) or "无"
            text = ("数据目录 %s（来源：%s）\n管理员：%s"
                    % (DATA_DIR, data_dir_source(), names))
            try:
                self.rescue_link.setVisible(account_rescue.admin_key_available(self.store))
            except Exception:
                pass
            rescue_hint = account_rescue.hint(self.store)
            self.data_hint.setText(text + ("\n" + rescue_hint if rescue_hint else ""))
        except Exception as exc:
            self.data_hint.setText("数据目录读取失败：%s" % exc)

    def _choose_data_dir(self) -> None:
        """让用户指一个已有的数据目录（例如原来那份库），重启后生效。"""
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        start = str(DATA_DIR.parent)
        folder = QFileDialog.getExistingDirectory(self, "选择 DanLume 数据目录", start)
        if not folder:
            return
        candidate = None
        try:
            from pathlib import Path

            path = Path(folder)
            candidate = path if (path / "lumina.db").exists() else path / "data"
        except Exception:
            candidate = None
        if candidate is None or not (candidate / "lumina.db").exists():
            QMessageBox.warning(
                self, "这个目录里没有数据",
                "选中的目录里找不到 lumina.db。\n\n"
                "请选 DanLume 的 data 目录（里面应该有 lumina.db、admin_key.pem 等文件）。")
            return
        try:
            written = set_data_dir_pointer(candidate)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "写不了设置文件", str(exc))
            return
        QMessageBox.information(
            self, "已记录数据目录",
            "下次启动 DanLume 就会用这个数据目录：\n%s\n\n（记录写在 %s）"
            % (candidate, written))
        self._show_data_dir_changed(candidate)

    def _show_data_dir_changed(self, folder) -> None:
        try:
            self.error.setStyleSheet(f"color: {theme.SUCCESS};")
            self.error.setText("数据目录已改为：%s（重启后生效）" % folder)
        except Exception:
            pass

    def _rescue_admin(self) -> None:
        """用本机管理员密钥重建 / 重设管理员账户。"""
        from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

        if not account_rescue.admin_key_available(self.store):
            QMessageBox.warning(
                self, "不能在这里找回",
                "本机数据目录里没有管理员私钥（admin_key.pem），"
                "所以不能绕过注册审批。请让管理员按「注册账户」流程批准。")
            return
        default_name = account_rescue.suggested_username(self.store) or "admin"
        name, ok = QInputDialog.getText(
            self, "找回管理员账户",
            "要用哪个用户名当管理员？（原来是谁就填谁）", QLineEdit.EchoMode.Normal,
            default_name)
        if not ok or not str(name).strip():
            return
        password, ok = QInputDialog.getText(
            self, "设置新密码",
            "新密码（至少 %d 位）：" % users.MIN_PASSWORD, QLineEdit.EchoMode.Password)
        if not ok or not password:
            return
        again, ok = QInputDialog.getText(
            self, "再输一次", "再输一次新密码：", QLineEdit.EchoMode.Password)
        if not ok:
            return
        if password != again:
            QMessageBox.warning(self, "两次不一样", "两次输入的密码不同，请重来一次。")
            return
        try:
            result = account_rescue.rescue(self.store, name, password)
        except users.AccountError as exc:
            QMessageBox.warning(self, "没成功", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "没成功", "出错了：%s" % exc)
            return
        action = "已重建管理员账户" if result.get("action") == "created" else "已重设密码"
        QMessageBox.information(
            self, "好了",
            "%s：%s\n\n现在可以直接用这个用户名和刚设的密码登录。"
            % (action, result.get("username")))
        self.reload()
        try:
            self.account_edit.setText(str(result.get("username") or ""))
            self.password_edit.setFocus()
        except Exception:
            pass

    def _use_selected_account(self, _index: int) -> None:
        """点了下拉里的账户：把名字填进输入框（仍然可以手改）。"""
        name = str(self.account_select.currentText() or "").strip()
        if name:
            try:
                self.account_edit.setText(name)
            except Exception:
                pass
        self._refresh_remember_choices()

    def _refresh_remember_choices(self) -> None:
        """记住时长：管理员多一个「永久绑定这台设备」。"""
        selected_id = self.account_select.currentData()
        typed = ""
        try:
            typed = self.account_edit.text().strip()
        except Exception:
            typed = ""
        account = users.find_user(self.store, typed) if typed else None
        if account is None and selected_id:
            account = users.get_user(self.store, int(selected_id))
        choices = list(users.REMEMBER_CHOICES)
        if users.is_admin(account):
            choices.append(users.REMEMBER_ADMIN_CHOICE)
        remember = users.remember_minutes(self.store)
        self.remember_select.blockSignals(True)
        self.remember_select.clear()
        for minutes, label in choices:
            self.remember_select.addItem(label, minutes)
        index = self.remember_select.findData(remember)
        self.remember_select.setCurrentIndex(max(0, index))
        self.remember_select.blockSignals(False)
        try:
            if users.is_trusted_device(self.store, account):
                self.hint_trusted.setText(
                    "这台设备已经永久绑定管理员账户，直接进就行（换设备或点「退出登录」才需要密码）")
            else:
                self.hint_trusted.setText("")
        except Exception:
            pass

    def _set_mode(self, index: int) -> None:
        self.stack.setCurrentIndex(2 if index == 2 else (1 if index else 0))
        self.error.setText("")
        if index == 2:
            self.reg_name.setFocus()
        elif index:
            self.new_name.setFocus()
        else:
            self.password_edit.clear()
            self.password_edit.setFocus()

    def mode(self) -> int:
        return int(self.stack.currentIndex())

    def _toggle_eye(self) -> None:
        self._show_password = not self._show_password
        self.password_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if self._show_password
            else QLineEdit.EchoMode.Password)
        self.eye_button.setToolTip("隐藏密码" if self._show_password else "显示密码")

    # -------------------------------------------------- 提交
    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.setStyleSheet(f"color: {theme.DANGER};")

    def _submit(self) -> None:
        index = self.stack.currentIndex()
        if index == 1:
            self._create()
        elif index == 2:
            self._register()
        else:
            self._login()

    def _create(self) -> None:
        name = self.new_name.text().strip()
        email = self.new_email.text().strip()
        password = self.new_password.text()
        again = self.new_password2.text()
        if "@" not in email:
            self._fail("先填一个能收信的邮箱（注册要绑定邮箱）")
            return
        if password != again:
            self._fail("两次输入的密码不一样")
            return
        try:
            user_id = users.create_user(self.store, name, password, email=email)
        except users.AccountError as exc:
            self._fail(str(exc))
            return
        self._enter(user_id, users.remember_minutes(self.store), first_time=True)

    def _login(self) -> None:
        username = ""
        try:
            username = self.account_edit.text().strip()
        except Exception:
            username = ""
        if not username:
            username = self.account_select.currentText().strip()
        if not username:
            self._fail("请先输入用户名")
            return
        password = self.password_edit.text()
        if not password:
            self._fail("请输入密码")
            return
        try:
            user = users.check(self.store, username, password)
        except users.AccountError as exc:
            message = str(exc)
            if message == "没有这个账户":
                # 这个报错几乎都是「程序用的是另一个数据目录」，把实情写出来
                message = ("没有这个账户。这个数据目录里现在有：%s\n目录：%s\n"
                           "（不对的话，点下面「换数据目录…」指到原来那份数据）"
                           % (self._accounts_summary(), DATA_DIR))
            self._fail(message)
            self.password_edit.selectAll()
            return
        minutes = self.remember_select.currentData()
        self._enter(int(user["id"]), int(minutes if minutes is not None else 0))

    def _enter(self, user_id: int, minutes: int, first_time: bool = False) -> None:
        user = users.get_user(self.store, user_id)
        if user is None:
            self._fail("这个账户不见了，请重新建一个")
            self.reload()
            return
        try:
            users.set_remember_minutes(self.store, minutes)
            users.remember_device(self.store, user_id, minutes)
        except users.AccountError as exc:
            # 例如普通账户选了「永久绑定」
            self._fail(str(exc))
            return
        except Exception:
            pass
        self.user = user
        self.remember_minutes = int(minutes)
        self.error.setText("")
        self.accept()

    def _forgot_password(self) -> None:
        dialog = ConfirmDialog(
            self,
            title="清空本机账户",
            message=("密码是加盐哈希，找不回来。\n\n"
                     "这里可以清空所有登录账户和「记住设备」状态，"
                     "然后重新创建一个账户；\n"
                     "日程、邮件、备忘等数据都不会被删除。"),
            confirm_text="清空账户",
            danger=True,
        )
        if dialog.exec() != ConfirmDialog.DialogCode.Accepted:
            return
        users.reset_all(self.store)
        self.new_name.clear()
        self.new_password.clear()
        self.new_password2.clear()
        self.reload()
        self._set_mode(1)
        self.error.setText("账户已清空，重新建一个吧")
        self.error.setStyleSheet(f"color: {theme.SUCCESS};")

    # -------------------------------------------------- 键盘
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._submit()
            return
        super().keyPressEvent(event)
