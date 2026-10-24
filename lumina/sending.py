"""基于 QThread 的邮件发送与 SMTP 连接测试（不阻塞界面）。"""
from __future__ import annotations

import threading
from typing import Any, Dict, List

from PySide6.QtCore import QObject, Signal

from .mailer import (
    MailError, SmtpConfig, build_message, send_message, test_connection,
)


class MailSender(QObject):
    """在后台线程构建并发送邮件，完成后通过信号回主线程。"""

    finished = Signal(int, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

    def is_busy(self) -> bool:
        return self._busy

    def send(
        self,
        mail_id: int,
        to_addr: str,
        subject: str,
        text: str,
        html_body: str,
        attachments: List[Dict[str, Any]],
        cfg: SmtpConfig,
    ) -> None:
        if self._busy:
            return
        self._busy = True
        attachments = [dict(item) for item in attachments]

        def worker() -> None:
            try:
                message = build_message(to_addr, subject, text, html_body, attachments, cfg)
                send_message(message, cfg)
                self.finished.emit(mail_id, True, "")
            except MailError as exc:
                self.finished.emit(mail_id, False, str(exc))
            except Exception as exc:  # 兜底，避免线程静默崩溃
                self.finished.emit(mail_id, False, f"发送失败：{exc}")
            finally:
                self._busy = False

        threading.Thread(target=worker, name="lumina-mail-sender", daemon=True).start()


class SmtpTester(QObject):
    """测试 SMTP 连通性。"""

    finished = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

    def is_busy(self) -> bool:
        return self._busy

    def test(self, cfg: SmtpConfig) -> None:
        if self._busy:
            return
        self._busy = True

        def worker() -> None:
            try:
                ok, message = test_connection(cfg)
            except Exception as exc:
                ok, message = False, f"测试失败：{exc}"
            finally:
                self._busy = False
            self.finished.emit(ok, message)

        threading.Thread(target=worker, name="lumina-smtp-test", daemon=True).start()
