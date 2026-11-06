"""账户审核（管理员）：注册邮箱、密钥、批准申请、注册表（停用 / 续期）。

这一页只给管理员看（普通用户看不到入口）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout,
    QWidget,
)

from . import adminsign, icons, registration, theme, users
from .widgets import BaseDialog, Select, SoftButton


class _ApproveWorker(QThread):
    """后台把「验证码 + 回执」邮件发出去（SMTP 也可能慢，同样别冻界面）。"""

    done = Signal(bool, str)        # sent, error

    def __init__(self, store, approval, parent=None, api: bool = False):
        super().__init__(parent)
        self.store = store
        self.approval = approval
        self.api = bool(api)

    def run(self) -> None:
        try:
            if self.api:
                from . import api_grant

                result = api_grant.send_approval(self.store, self.approval)
            else:
                result = registration.send_approval(self.store, self.approval)
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, str(exc))
            return
        self.done.emit(bool(result.get("sent")), str(result.get("error") or ""))


class _CollectWorker(QThread):
    """在后台读收件箱（IMAP 很慢，放 GUI 线程里会把界面冻住）。"""

    done = Signal(list, list)       # requests, errors
    failed = Signal(str)

    def __init__(self, store, prefix: str = "[LUMINA-REG]", parent=None):
        super().__init__(parent)
        self.store = store
        self.prefix = prefix

    def run(self) -> None:
        try:
            if self.prefix.startswith("[LUMINA-API]"):
                from . import api_grant

                found, errors = api_grant.collect_requests(self.store)
            else:
                # 注册申请：邮件 + 局域网直连（客户端直接 POST 过来的）一起收
                found, errors = registration.collect_all_requests(self.store)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.done.emit(list(found or []), list(errors or []))


class AdminDialog(BaseDialog):
    """管理员用来审批注册申请、管理注册表。"""

    def __init__(self, store, parent=None):
        super().__init__(
            parent, width=620, height=620,
            title="账户审核（管理员）",
            subtitle="注册申请走邮件：申请人用你的公钥加密发过来，你批准后回验证码 + 签名回执",
            icon=icons.CONTACT,
        )
        self.store = store
        self._requests: List[Dict[str, Any]] = []
        self._registry: List[Dict[str, Any]] = []
        self._collect_worker = None
        self._api_worker = None
        self._approve_worker = None
        self._last_reply: Dict[str, str] = {}

        # ---------------- 邮箱与有效期
        row = QHBoxLayout()
        row.setSpacing(8)
        mail_label = QLabel("注册邮箱")
        mail_label.setObjectName("Tiny")
        row.addWidget(mail_label)
        self.mail_edit = QLineEdit(registration.registry_email(store))
        self.mail_edit.setMinimumWidth(260)
        row.addWidget(self.mail_edit, 1)
        days_label = QLabel("默认有效期")
        days_label.setObjectName("Tiny")
        row.addWidget(days_label)
        self.days_edit = QLineEdit(str(registration.default_valid_days(store)))
        self.days_edit.setFixedWidth(64)
        row.addWidget(self.days_edit)
        devices_label = QLabel("新设备自动通过")
        devices_label.setObjectName("Tiny")
        devices_label.setToolTip(
            "老用户换新机器时，每账户最多几台设备可以自动通过（0 = 一律人工审批）。"
            "自动通过会沿用原来的有效期，不重新算天数。")
        row.addWidget(devices_label)
        self.devices_edit = QLineEdit(str(registration.device_limit(store)))
        self.devices_edit.setFixedWidth(48)
        self.devices_edit.setToolTip(devices_label.toolTip())
        row.addWidget(self.devices_edit)
        save_button = SoftButton("保存", "primary", height=30, min_width=64,
                                 point_size=8.6)
        save_button.clicked.connect(self._save_settings)
        row.addWidget(save_button)
        self.body.addLayout(row)

        # ---------------- 验证码邮件用哪个邮箱发（用户要求：用学邮）
        sender_row = QHBoxLayout()
        sender_row.setSpacing(8)
        sender_label = QLabel("回信邮箱")
        sender_label.setObjectName("Tiny")
        sender_row.addWidget(sender_label)
        self.sender_select = Select()
        self.sender_select.setMinimumWidth(240)
        self.sender_select.setToolTip(
            "「验证码 + 回执」这封回信用哪个发件邮箱发；默认优先学邮")
        self.sender_select.addItem("自动（优先学邮）", "")
        for item in registration.sender_candidates(store):
            self.sender_select.addItem(
                f"{item.get('sender_email') or item.get('label')}", str(item.get("id")))
        current_sender = registration.approval_sender_id(store)
        index = self.sender_select.findData(current_sender)
        if index >= 0:
            self.sender_select.setCurrentIndex(index)
        self.sender_select.currentIndexChanged.connect(self._on_sender_changed)
        sender_row.addWidget(self.sender_select)
        self.sender_hint = QLabel(self._sender_hint())
        self.sender_hint.setObjectName("Tiny")
        self.sender_hint.setWordWrap(True)
        sender_row.addWidget(self.sender_hint, 1)
        self.body.addLayout(sender_row)
        self.network_hint = QLabel(self._network_hint())
        self.network_hint.setObjectName("Tiny")
        self.network_hint.setWordWrap(True)
        self.body.addWidget(self.network_hint)

        # ---------------- 密钥
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.key_label = QLabel("")
        self.key_label.setObjectName("Tiny")
        self.key_label.setWordWrap(True)
        key_row.addWidget(self.key_label, 1)
        self.gen_key_button = SoftButton("生成密钥", "ghost", height=30, min_width=88,
                                         point_size=8.6)
        self.gen_key_button.clicked.connect(self._generate_key)
        key_row.addWidget(self.gen_key_button)
        self.copy_pub_button = SoftButton("复制公钥", "ghost", height=30, min_width=88,
                                          point_size=8.6)
        self.copy_pub_button.clicked.connect(self._copy_public)
        key_row.addWidget(self.copy_pub_button)
        self.body.addLayout(key_row)

        # ---------------- 申请列表
        head = QHBoxLayout()
        head.setSpacing(8)
        requests_label = QLabel("注册申请")
        requests_label.setObjectName("Small")
        head.addWidget(requests_label)
        head.addStretch(1)
        self.check_button = SoftButton("检查新申请", "primary", height=30,
                                       min_width=110, point_size=8.6)
        self.check_button.setToolTip(
            "读管理员的收件箱（IMAP），找 [LUMINA-REG] 邮件"
            "（会一起扫垃圾邮件文件夹，并只看最近 30 天）")
        self.check_button.clicked.connect(self._check_requests)
        head.addWidget(self.check_button)
        self.import_eml_button = SoftButton("从 .eml 导入", "ghost", height=30,
                                            min_width=104, point_size=8.6)
        self.import_eml_button.setToolTip(
            "邮件翻不到时的兜底：在网页邮箱把那封 [LUMINA-REG] 邮件另存为 .eml，"
            "这里读出来走同一条验签解密")
        self.import_eml_button.clicked.connect(self._import_eml)
        head.addWidget(self.import_eml_button)
        self.approve_button = SoftButton("批准选中", "ghost", height=30, min_width=96,
                                         point_size=8.6)
        self.approve_button.clicked.connect(self._approve)
        head.addWidget(self.approve_button)
        self.copy_reply_button = SoftButton("复制回信全文", "ghost", height=30,
                                            min_width=118, point_size=8.6)
        self.copy_reply_button.setToolTip(
            "把「收件人 + 主题 + 正文」整封复制下来：邮箱发不出去时可以贴到别的邮箱里发")
        self.copy_reply_button.clicked.connect(self._copy_last_reply)
        self.copy_reply_button.setEnabled(False)
        head.addWidget(self.copy_reply_button)
        self.body.addLayout(head)

        self.request_select = Select()
        self.request_select.setMinimumHeight(34)
        self.body.addWidget(self.request_select)
        self.request_view = QPlainTextEdit()
        self.request_view.setReadOnly(True)
        self.request_view.setFixedHeight(74)
        self.request_view.setStyleSheet("font-size: 11px;")
        self.body.addWidget(self.request_view)

        # ---------------- 模型接口授权申请
        api_head = QHBoxLayout()
        api_head.setSpacing(8)
        api_label = QLabel("模型接口申请")
        api_label.setObjectName("Small")
        api_head.addWidget(api_label)
        api_head.addStretch(1)
        self.api_check_button = SoftButton("检查 API 申请", "ghost", height=30,
                                           min_width=118, point_size=8.6)
        self.api_check_button.setToolTip("读收件箱里的 [LUMINA-API] 邮件")
        self.api_check_button.clicked.connect(self._check_api_requests)
        api_head.addWidget(self.api_check_button)
        self.api_approve_button = SoftButton("批准（≤5 小时）", "ghost", height=30,
                                             min_width=128, point_size=8.6)
        self.api_approve_button.clicked.connect(self._approve_api)
        api_head.addWidget(self.api_approve_button)
        self.api_copy_button = SoftButton("复制回信全文", "ghost", height=30,
                                          min_width=118, point_size=8.6)
        self.api_copy_button.setToolTip(
            "把这条授权回信整封复制下来（邮箱发不出去时手动发给同学）")
        self.api_copy_button.clicked.connect(self._copy_last_reply)
        self.api_copy_button.setEnabled(False)
        api_head.addWidget(self.api_copy_button)
        self.body.addLayout(api_head)

        self.api_select = Select()
        self.api_select.setMinimumHeight(34)
        self.body.addWidget(self.api_select)

        # ---------------- 注册表
        reg_head = QHBoxLayout()
        reg_head.setSpacing(8)
        reg_label = QLabel("注册表（批准过的账户）")
        reg_label.setObjectName("Small")
        reg_head.addWidget(reg_label)
        reg_head.addStretch(1)
        self.registry_select = Select()
        self.registry_select.setFixedWidth(200)
        reg_head.addWidget(self.registry_select)
        self.renew_button = SoftButton("续期", "ghost", height=30, min_width=64,
                                       point_size=8.6)
        self.renew_button.setToolTip("按默认有效期重新签发回执，申请人粘贴后即可延期")
        self.renew_button.clicked.connect(self._renew)
        reg_head.addWidget(self.renew_button)
        self.disable_button = SoftButton("停用 / 启用", "ghost", height=30,
                                         min_width=96, point_size=8.6)
        self.disable_button.clicked.connect(self._toggle_disabled)
        reg_head.addWidget(self.disable_button)
        self.delete_button = SoftButton("注销", "ghost", height=30, min_width=64,
                                        point_size=8.6)
        self.delete_button.setToolTip(
            "从注册表里彻底删掉（这个名字可以重新申请）；只想临时拦住就用「停用」")
        self.delete_button.clicked.connect(self._delete_registry)
        reg_head.addWidget(self.delete_button)
        self.body.addLayout(reg_head)

        self.registry_view = QPlainTextEdit()
        self.registry_view.setReadOnly(True)
        self.registry_view.setFixedHeight(84)
        self.registry_view.setStyleSheet("font-size: 11px;")
        self.body.addWidget(self.registry_view)

        # ---------------- 使用上报（局域网直连，不走邮件）
        report_head = QHBoxLayout()
        report_head.setSpacing(8)
        report_label = QLabel("使用上报（客户端直连你这台机器）")
        report_label.setObjectName("Small")
        report_head.addWidget(report_label)
        report_head.addStretch(1)
        self.report_refresh_button = SoftButton("刷新", "ghost", height=28,
                                               min_width=64, point_size=8.4)
        self.report_refresh_button.clicked.connect(self._refresh_reports)
        report_head.addWidget(self.report_refresh_button)
        self.body.addLayout(report_head)

        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setFixedHeight(112)
        self.report_view.setStyleSheet("font-size: 11px;")
        self.body.addWidget(self.report_view)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        close_button = SoftButton("关闭", "ghost", height=32, min_width=88)
        close_button.clicked.connect(self.accept)
        self.add_footer([], [close_button])

        self.refresh()

    # -------------------------------------------------- 刷新
    def refresh(self) -> None:
        fingerprint = adminsign.public_fingerprint(self.store)
        if adminsign.has_private_key(self.store):
            self.key_label.setText(
                f"管理员密钥：已就绪（公钥指纹 {fingerprint}）；"
                "把程序根目录的 admin_pub.pem 随安装包发给申请人")
            self.gen_key_button.setText("重新生成")
        else:
            self.key_label.setText("管理员密钥：这台机器还没有（点「生成密钥」）")
            self.gen_key_button.setText("生成密钥")
        self._reload_registry()
        try:
            self._refresh_reports()
        except Exception:
            pass

    # -------------------------------------------------- 使用上报
    def _refresh_reports(self) -> None:
        from . import usage_report

        lines = []
        server = getattr(self.window(), "usage_server", None)
        if server is not None and getattr(server, "running", False):
            lines.append(f"接收状态：正在监听 0.0.0.0:{server.port}"
                         f"（客户端填 http://<这台机器的IP>:{server.port}/lumina-usage）")
        else:
            lines.append("接收状态：没有监听（客户端上报会失败；重启应用会自动起）")
        lines.append("上报地址设置：" + (usage_report.report_url(self.store) or "（空）"))
        lines.append(f"上报间隔：每 {usage_report.interval_minutes(self.store)} 分钟；"
                     f"当天时长上限 {usage_report.daily_limit_hours(self.store)} 小时")
        lines.append("省流：这段时间没有新使用会自动跳过，最迟每 "
                     f"{usage_report.interval_minutes(self.store) * 3} 分钟发一次心跳；"
                     f"记录只留最近 {usage_report.KEEP_DAYS} 天"
                     f"（每人最多 {usage_report.KEEP_ROWS_PER_USER} 条）")
        reports = usage_report.recent_reports(self.store, limit=8)
        lines.append("")
        if reports:
            lines.append("最近上报：")
            for item in reports:
                hours = item["used_seconds"] / 3600.0
                lines.append(
                    f"· {item['received_at']}  {item['username']}  "
                    f"用时 {hours:.1f}h  请求 {item['requests']}"
                    f"  API {item['api_calls']}  TTFT {item['ttft']:.2f}s"
                    f"  {item['tok_s']:.1f} tok/s"
                    + (f"  ⚠ {item['note']}" if item["note"] else ""))
        else:
            lines.append("还没有收到上报（对方要能连到这台机器的端口：同一局域网 + 防火墙放行）")
        flags = usage_report.violations(self.store)
        if flags:
            lines.append("")
            lines.append("违规累计（1 次提醒 / 2-3 次警告 / 4 次封号）：")
            for item in flags:
                lines.append(f"· {item['username']}  第 {item['count']} 次"
                             f"{' 已封号' if item['banned'] else ''}"
                             f"  {item['last_reason']}")
        self.report_view.setPlainText("\n".join(lines))

    def _reload_registry(self) -> None:
        self._registry = registration.list_registry(self.store)
        self.registry_select.clear()
        lines = []
        for item in self._registry:
            state = "停用" if item["status"] == "disabled" else "有效"
            self.registry_select.addItem(f"{item['username']}（{state}）",
                                         item["username"])
            lines.append(f"· {item['username']}  {item['email']}  "
                         f"{state}  到期 {item['expires_at'] or '长期'}")
        self.registry_view.setPlainText("\n".join(lines) or "（还没有批准过任何账户）")

    # -------------------------------------------------- 设置
    def _save_settings(self) -> None:
        mail = self.mail_edit.text().strip()
        if "@" not in mail:
            self._warn("注册邮箱格式不对")
            return
        registration.set_registry_email(self.store, mail)
        try:
            days = int(self.days_edit.text().strip() or 180)
        except ValueError:
            days = 180
        registration.set_default_valid_days(self.store, days)
        try:
            devices = int(self.devices_edit.text().strip() or 0)
        except ValueError:
            devices = registration.DEFAULT_DEVICE_LIMIT
        devices = registration.set_device_limit(self.store, devices)
        self.devices_edit.setText(str(devices))
        self._info(f"已保存：申请发到 {mail}，默认有效期 {days} 天，"
                   f"老用户新设备自动通过上限 {devices} 台"
                   + ("（关闭）" if devices == 0 else ""))

    def _generate_key(self) -> None:
        try:
            adminsign.generate_keypair(self.store)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"生成失败：{exc}")
            return
        self.refresh()
        self._info("密钥已生成。记住：只把 admin_pub.pem（公钥）发给别人，"
                   "admin_key.pem 千万别外传")

    def _copy_public(self) -> None:
        pem = adminsign.public_pem(self.store)
        if not pem:
            self._warn("还没有公钥，先生成密钥")
            return
        QApplication.clipboard().setText(pem)
        self._info("公钥已复制")

    # -------------------------------------------------- 申请
    def _network_hint(self) -> str:
        """网络直连接收状态（客户端「直接把申请送过来」用的地址）。"""
        try:
            from . import context_info, usage_report

            port = usage_report.listen_port(self.store)
            url = f"http://{context_info.local_ip()}:{port}{registration.REGISTER_PATH}"
            server = getattr(self.window(), "usage_server", None)
            running = bool(server is not None and getattr(server, "running", False))
            if running:
                return f"网络直连：{url}（正在监听，客户端会把申请直接送到这里）"
            return (f"网络直连：{url}（现在没在监听，重启应用会自动起；"
                    "没监听时客户端会改发邮件）")
        except Exception:
            return ""

    def _sender_hint(self) -> str:
        try:
            account = registration.pick_sender_account(self.store)
        except Exception:
            account = None
        if not account:
            return "还没有配置发件邮箱 → 验证码发不出去，只能用「复制回信全文」手动发"
        return f"实际会用：{account.get('sender_email') or account.get('label')}"

    def _on_sender_changed(self, _index: int) -> None:
        try:
            registration.set_approval_sender(
                self.store, str(self.sender_select.currentData() or ""))
            self.sender_hint.setText(self._sender_hint())
            self._info(f"回信邮箱已切换：{self._sender_hint()}")
        except Exception as exc:  # noqa: BLE001
            self._warn(f"切换回信邮箱失败：{exc}")

    def _worker_alive(self, attr: str):
        """取出后台线程对象；已经被 deleteLater 删掉的就置空。

        坑：`worker.done.connect(worker.deleteLater)` 会把 C++ 对象删掉，但 Python
        这边（`self._collect_worker`）还握着包装对象。之后再调 `isRunning()` 就是
        访问已释放的内存 —— 表现为关窗口 / 再点一次检查时进程直接 0xC0000005 崩掉。
        """
        worker = getattr(self, attr, None)
        if worker is None:
            return None
        try:
            from shiboken6 import isValid

            alive = bool(isValid(worker))
        except Exception:
            alive = True
        if not alive:
            try:
                setattr(self, attr, None)
            except Exception:
                pass
            return None
        return worker

    def _check_requests(self) -> None:
        """后台读收件箱（别再把界面冻住），回来再填列表。"""
        running = self._worker_alive("_collect_worker")
        if running is not None and running.isRunning():
            return
        self.status.setText("正在读收件箱…（后台进行，界面不会卡）")
        self.check_button.setEnabled(False)
        worker = _CollectWorker(self.store, "[LUMINA-REG]", self)
        worker.done.connect(self._on_requests_ready)
        worker.failed.connect(self._on_collect_failed)
        worker.done.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.start()
        self._collect_worker = worker

    def _on_collect_failed(self, message: str) -> None:
        self.status.setText(f"读收件箱失败：{message}")
        try:
            self.check_button.setEnabled(True)
        except Exception:
            pass

    def _on_requests_ready(self, found: list, errors: list) -> None:
        self.check_button.setEnabled(True)
        self._requests = list(found)
        self._render_requests(errors)

    def _render_requests(self, errors: list = None) -> None:
        """按 self._requests 重建下拉框和说明（导入 .eml 之后也要刷一遍）。"""
        errors = [str(one) for one in (errors or [])]
        network = sum(1 for item in self._requests if item.get("_network"))
        self.request_select.clear()
        lines = []
        for item in self._requests:
            mark = "（已批准过）" if item.get("_already") else ""
            via = "网络" if item.get("_network") else "邮件"
            folder = str(item.get("_via_folder") or "")
            folder_note = ""
            if folder and folder.upper() != "INBOX" and not item.get("_network"):
                folder_note = f" · 在「{folder}」里找到"
            # 老用户换机器：标出「第几台设备」，管理员一眼能看出这是同一个人换设备
            device_note = ""
            try:
                status = registration.cross_device_status(self.store, item)
                if status.get("known"):
                    item["_device_note"] = status
                    device_note = (" · 老用户第 %d 台设备（上限 %d）%s"
                                   % (status.get("devices", 0) + 1,
                                      status.get("limit", 0),
                                      "，可自动通过" if status.get("approve") else ""))
            except Exception:
                pass
            self.request_select.addItem(
                f"[{via}] {item.get('username', '')} · {item.get('email', '')}"
                f"{mark}{device_note}", item.get("username", ""))
            lines.append(f"· [{via}] {item.get('username')}  {item.get('email')}  "
                         f"设备 {str(item.get('device', ''))[:10]}…  {item.get('ts', '')}"
                         f"{folder_note}"
                         f"{'  ← 老用户新设备' if device_note else ''}")
        if errors:
            lines.append("")
            lines.append("读申请时的问题（这几条要人工看一下）：")
            lines += ["· " + one for one in errors[:8]]
            if len(errors) > 8:
                lines.append(f"· …… 另外还有 {len(errors) - 8} 条")
        self.request_view.setPlainText("\n".join(lines) or "（没有新的注册申请）")
        self.status.setText(
            f"收到 {len(self._requests)} 条申请"
            + (f"（其中 {network} 条是网络直连送来的）" if network else "")
            + (f"，{len(errors)} 条有问题" if errors else ""))

    def _import_eml(self) -> None:
        """从本机 .eml 文件里救一条申请（邮件被垃圾箱吞掉 / 被删掉时的兜底）。"""
        from PySide6.QtWidgets import QFileDialog

        path, _selected = QFileDialog.getOpenFileName(
            self, "选择那封申请邮件（.eml）", "",
            "邮件文件 (*.eml);;所有文件 (*)")
        if not path:
            return
        try:
            request = registration.request_from_eml(self.store, path)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"这个文件读不出申请：{exc}")
            return
        key = (str(request.get("username") or "").lower(),
               str(request.get("nonce") or ""))
        self._requests = [one for one in self._requests
                          if (str(one.get("username") or "").lower(),
                              str(one.get("nonce") or "")) != key]
        self._requests.insert(0, request)
        self._render_requests()
        self.status.setText(
            "已从文件读入 1 条申请：%s（可以点「批准选中」）"
            % request.get("username", ""))

    def _approve(self) -> None:
        username = self.request_select.currentData()
        request = next((item for item in self._requests
                        if item.get("username") == username), None)
        if request is None:
            self._warn("先点「检查新申请」，再选一个申请")
            return
        try:
            if request.get("_already"):
                # 注册表里已有这个账户：老用户换新设备 / 重复申请 → 沿用原有效期重签回执
                approval = registration.approve_existing(self.store, request,
                                                         note="管理员批准新设备")
            else:
                approval = registration.approve(self.store, request)
        except (registration.RegisterError, adminsign.SignError) as exc:
            self._warn(str(exc))
            return
        lines = [
            f"已批准：{approval['username']}（{approval['email']}）",
            f"验证码：{approval['code']}",
            f"有效期到：{approval['expires_at']}",
        ]
        if approval.get("reused"):
            lines.insert(0, "这是老用户的新设备：沿用原来的有效期（第 %s 台设备）"
                         % approval.get("devices", "?"))
        lines += [
            "",
            "回执（申请人粘贴用）：",
            approval["receipt"],
            "",
            "完整批准邮件正文：",
            approval["body"],
        ]
        self.request_view.setPlainText("\n".join(lines))
        try:
            if request.get("_network") and request.get("nonce"):
                registration.mark_incoming_consumed(self.store,
                                                    str(request["nonce"]))
        except Exception:
            pass
        self._last_reply = {"kind": "注册通过", "to": approval["email"],
                            "subject": approval["subject"],
                            "body": approval["body"]}
        try:
            self.copy_reply_button.setEnabled(True)
        except Exception:
            pass
        self._reload_registry()
        # 邮件放到后台发，界面立刻能继续操作（验证码已经显示在上面了）
        self.status.setText(f"已批准，正在后台发送批准邮件…（验证码 {approval['code']}）")
        self.approve_button.setEnabled(False)
        worker = _ApproveWorker(self.store, approval, self)
        worker.done.connect(self._on_approval_sent)
        worker.done.connect(worker.deleteLater)
        worker.start()
        self._approve_worker = worker

    def _on_approval_sent(self, sent: bool, error: str) -> None:
        try:
            self.approve_button.setEnabled(True)
        except Exception:
            pass
        if sent:
            self._info("批准邮件已发出（验证码就在上面的文本框里）")
        else:
            self._warn(f"{error or '发送失败'}；点「复制回信全文」手动发给同学")

    # -------------------------------------------------- 模型接口授权
    def _check_api_requests(self) -> None:
        """后台读收件箱里的 API 申请（同样不冻界面）。"""
        running = self._worker_alive("_api_worker")
        if running is not None and running.isRunning():
            return
        self.status.setText("正在读收件箱（API 申请）…（后台进行）")
        self.api_check_button.setEnabled(False)
        worker = _CollectWorker(self.store, "[LUMINA-API]", self)
        worker.done.connect(self._on_api_requests_ready)
        worker.failed.connect(self._on_api_failed)
        worker.done.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.start()
        self._api_worker = worker

    def _on_api_failed(self, message: str) -> None:
        self.status.setText(f"读收件箱失败：{message}")
        try:
            self.api_check_button.setEnabled(True)
        except Exception:
            pass

    def _on_api_requests_ready(self, found: list, errors: list) -> None:
        from . import api_grant

        self.api_check_button.setEnabled(True)
        self._api_requests = list(found)
        self.api_select.clear()
        lines = []
        for item in self._api_requests:
            hours = item.get("hours", api_grant.GRANT_MAX_HOURS)
            self.api_select.addItem(
                f"{item.get('username', '')} · {hours}h · {item.get('ts', '')}",
                item.get("username", ""))
            lines.append(f"· {item.get('username')} 申请 {hours} 小时  "
                         f"{item.get('ts', '')}")
        self.request_view.setPlainText(
            "\n".join(lines) or "（没有新的 API 申请）"
            + ("\n" + "\n".join(errors) if errors else ""))
        self.status.setText(f"收到 {len(self._api_requests)} 条 API 申请"
                            + (f"，{len(errors)} 条有问题" if errors else ""))

    def _approve_api(self) -> None:
        from . import api_grant

        username = self.api_select.currentData()
        request = next((item for item in getattr(self, "_api_requests", [])
                        if item.get("username") == username), None)
        if request is None:
            self._warn("先点「检查 API 申请」再选一条")
            return
        try:
            approval = api_grant.approve(self.store, request)
        except api_grant.GrantError as exc:
            self._warn(str(exc))
            return
        self.request_view.setPlainText("\n".join([
            f"已批准：{approval['username']}　{approval['hours']} 小时",
            f"到期：{approval['expires_at']}",
            "",
            "授权（申请人粘贴用）：",
            approval["token"],
            "",
            approval["body"],
        ]))
        self._last_reply = {"kind": "模型接口授权", "to": str(approval.get("email") or ""),
                            "subject": str(approval.get("subject") or ""),
                            "body": str(approval.get("body") or "")}
        try:
            self.copy_reply_button.setEnabled(True)
            self.api_copy_button.setEnabled(True)
        except Exception:
            pass
        # 发信也放后台（邮箱异常时界面不会卡住，内容已经在上面的文本框里）
        self.status.setText("已批准，正在后台发送授权邮件…")
        self.api_approve_button.setEnabled(False)
        worker = _ApproveWorker(self.store, approval, self, api=True)
        worker.done.connect(self._on_api_sent)
        worker.done.connect(worker.deleteLater)
        worker.start()
        self._approve_worker = worker

    def _on_api_sent(self, sent: bool, error: str) -> None:
        try:
            self.api_approve_button.setEnabled(True)
        except Exception:
            pass
        if sent:
            self._info("接口授权邮件已发出（授权字符串已在上面的文本框里）")
        else:
            self._warn(f"{error or '发送失败'}；点「复制回信全文」手动发给同学")

    # -------------------------------------------------- 复制回信全文
    def _copy_last_reply(self) -> str:
        """把「收件人 + 主题 + 正文」整封复制下来（邮箱发不出去时手动转发）。"""
        reply = dict(getattr(self, "_last_reply", {}) or {})
        body = str(reply.get("body") or "").strip()
        if not body:
            self._warn("还没有可复制的回信：先批准一条申请")
            return ""
        lines = [
            f"收件人：{reply.get('to') or '（对方邮箱）'}",
            f"主题：{reply.get('subject') or '（无主题）'}",
            "",
            "（以下为邮件正文）",
            "",
            body,
        ]
        text = "\n".join(lines)
        try:
            QApplication.clipboard().setText(text)
            self._info(f"{reply.get('kind') or '回信'}全文已复制（{len(text)} 字），"
                       "可以直接粘到任意邮箱里发给同学")
        except Exception as exc:  # noqa: BLE001
            self._warn(f"复制失败：{exc}")
        return text

    # -------------------------------------------------- 注册表
    def _renew(self) -> None:
        username = self.registry_select.currentData()
        if not username:
            self._warn("注册表里还没有账户")
            return
        try:
            token = registration.renew_registry(
                self.store, str(username), registration.default_valid_days(self.store))
        except (registration.RegisterError, adminsign.SignError) as exc:
            self._warn(str(exc))
            return
        self.request_view.setPlainText(
            f"「{username}」已续期，把下面这行回执发给对方（对方粘贴后生效）：\n\n{token}")
        self._reload_registry()
        self._info(f"「{username}」已续期")

    def _toggle_disabled(self) -> None:
        username = self.registry_select.currentData()
        item = next((row for row in self._registry
                     if row["username"] == username), None)
        if item is None:
            self._warn("先选一个账户")
            return
        target = "active" if item["status"] == "disabled" else "disabled"
        registration.set_registry_status(self.store, str(username), target)
        self._reload_registry()
        self._info(f"「{username}」已{'启用' if target == 'active' else '停用'}")

    def _delete_registry(self) -> None:
        """注销：从注册表里彻底删掉（删完这个名字可以重新申请）。"""
        username = self.registry_select.currentData()
        item = next((row for row in self._registry
                     if row["username"] == username), None)
        if item is None:
            self._warn("先选一个账户")
            return
        from .widgets import ConfirmDialog

        dialog = ConfirmDialog(
            self, "注销这个账户？",
            f"要注销「{username}」（{item.get('email') or '没有邮箱'}）吗？\n\n"
            "· 注销后这个名字可以重新申请注册；\n"
            "· 但他电脑上已经装好的账户不会被远程删除（回执是离线签名，收不回来），\n"
            "  下次上报使用情况时会被当成「未注册」，走提醒 / 警告 / 封号那套流程；\n"
            "· 只想临时拦住他的话，用「停用 / 启用」更合适。",
            "注销", "取消", danger=True)
        dialog.exec()
        if not dialog.confirmed:
            return
        try:
            registration.delete_registry(self.store, str(username))
        except registration.RegisterError as exc:
            self._warn(str(exc))
            return
        self._reload_registry()
        self._info(f"「{username}」已注销（现在是未注册状态，可以重新申请）")

    # -------------------------------------------------- 提示
    def closeEvent(self, event):
        """关窗时等后台线程收尾，避免线程还在跑而对话框已经销毁。"""
        for attr in ("_collect_worker", "_api_worker", "_approve_worker"):
            worker = self._worker_alive(attr)
            if worker is not None and worker.isRunning():
                try:
                    worker.wait(1500)
                except Exception:
                    pass
        super().closeEvent(event)

    def _info(self, text: str) -> None:
        self.status.setStyleSheet(f"color: {theme.SUCCESS};")
        self.status.setText(text)

    def _warn(self, text: str) -> None:
        self.status.setStyleSheet(f"color: {theme.DANGER};")
        self.status.setText(text)
