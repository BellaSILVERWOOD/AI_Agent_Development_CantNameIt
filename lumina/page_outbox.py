"""发件箱页：汇报邮件列表与内容预览。"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import icons, theme
from .config import MAIL_STATUS_LABELS, MAIL_STATUS_TONES
from .mailer import SmtpConfig
from .theme import (
    ACCENT, ACCENT2, BORDER, BORDER_SOFT, CARD, CARD_ALT, DANGER, FAINT, MUTED, TEXT,
    WARNING,
)
from .utils import human_size
from .widgets import (
    Banner, Card, ConfirmDialog, ElidedLabel, EmptyState, ExtBadge, IconButton,
    MailBodyView, Pill, SearchBox, SoftButton, clear_layout, make_scroll,
    rounded_path,
)


def _format_time(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        moment = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return f"{moment.month}月{moment.day}日 {moment.hour:02d}:{moment.minute:02d}"
    except (TypeError, ValueError):
        return str(value)[5:16]


class MailRow(QWidget):
    clicked = Signal(int)

    def __init__(self, mail: Dict[str, Any], selected: bool = False, parent=None):
        super().__init__(parent)
        self.mail = mail
        self._selected = selected
        self._hover = False
        self.setFixedHeight(78)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        status = str(mail.get("status", "draft"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(8)
        color = theme.mail_tone(status)
        bg = theme.soft(color, 0.13)
        top.addWidget(Pill(MAIL_STATUS_LABELS.get(status, "草稿"), color, bg, point_size=7.8))
        subject = ElidedLabel(str(mail.get("subject", "（无主题）")))
        subject_font = QFont(self.font())
        subject_font.setPointSizeF(9.4)
        subject_font.setWeight(QFont.Weight.DemiBold)
        subject.setFont(subject_font)
        subject.setStyleSheet(f"color: {TEXT}; background: transparent;")
        top.addWidget(subject, 1)
        time_label = QLabel(_format_time(mail.get("sent_at") or mail.get("created_at")))
        time_label.setObjectName("Tiny")
        top.addWidget(time_label)
        layout.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        to_label = ElidedLabel(f"致 {mail.get('to_addr', '') or '未填写收件人'}")
        to_label.setObjectName("Small")
        bottom.addWidget(to_label, 1)
        attachments = mail.get("attachments") or []
        if attachments:
            bottom.addWidget(
                Pill(f"{len(attachments)} 个附件", MUTED, theme.CARD_ALT, point_size=7.6)
            )
        layout.addLayout(bottom)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(int(self.mail.get("id", 0)))
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded_path(rect, 11)
        if self._selected:
            painter.fillPath(path, theme.appearance().accent_q(0.08))
            painter.setPen(QPen(theme.appearance().accent_q(0.38), 1.0))
            painter.drawPath(path)
        else:
            painter.fillPath(path, QColor(CARD))
            if self._hover:
                painter.fillPath(path, theme.hover_wash(0.05))
            painter.setPen(QPen(QColor(BORDER), 1.0))
            painter.drawPath(path)


class MailAttachmentRow(QWidget):
    def __init__(self, info: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.info = info
        self.setFixedHeight(46)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(9)
        layout.addWidget(ExtBadge(str(info.get("name", "")), 30))
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(0)
        name = ElidedLabel(str(info.get("name", "")))
        name.setStyleSheet(f"color: {TEXT}; font-size: 9pt; background: transparent;")
        meta = QLabel(human_size(info.get("size", 0)))
        meta.setObjectName("Tiny")
        text_box.addWidget(name)
        text_box.addWidget(meta)
        layout.addLayout(text_box, 1)
        folder = IconButton(icons.FOLDER, size=26, glyph_size=11, tip="在文件夹中显示")
        folder.clicked.connect(self._open_folder)
        layout.addWidget(folder)

    def _open_folder(self) -> None:
        path = str(self.info.get("path", ""))
        if os.path.exists(path):
            try:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            except Exception:
                pass

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillPath(rounded_path(rect, 9), QColor(CARD_ALT))
        painter.setPen(QPen(QColor(BORDER_SOFT), 1.0))
        painter.drawPath(rounded_path(rect, 9))


class OutboxPage(QWidget):
    compose_requested = Signal(object)
    request_settings = Signal()
    data_changed = Signal()

    def __init__(self, store, parent=None, embedded: bool = False):
        super().__init__(parent)
        self.store = store
        self.embedded = bool(embedded)
        self._mails: List[Dict[str, Any]] = []
        self._selected_id: Optional[int] = None

        root = QVBoxLayout(self)
        if self.embedded:
            root.setContentsMargins(0, 0, 0, 0)
        else:
            root.setContentsMargins(26, 18, 26, 20)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("发件箱")
        title.setObjectName("H1")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.search = SearchBox("搜索主题 / 收件人 / 内容")
        self.search.setFixedWidth(248)
        self.search.textChanged.connect(lambda _text: self.refresh())
        if not self.embedded:
            header.addWidget(self.search)
        new_button = SoftButton("新邮件", "primary", glyph=icons.MAIL, height=36,
                                min_width=118)
        new_button.clicked.connect(lambda: self.compose_requested.emit(None))
        self.inbox_button = SoftButton("收件箱", "ghost", height=36,
                                      glyph=icons.INBOX, min_width=104,
                                      point_size=8.8)
        self.inbox_button.clicked.connect(self.open_inbox)
        if not self.embedded:
            header.addWidget(new_button)
            header.addWidget(self.inbox_button)
            root.addLayout(header)
        else:
            # 嵌进「邮箱」页时，标题/搜索/按钮都由外层页面提供，只保留内容
            new_button.hide()

        self.banner = Banner(
            icons.WARNING,
            "尚未配置发件邮箱",
            "填写 SMTP 服务器与授权码后，就可以把汇报邮件直接发送出去",
        )
        settings_button = SoftButton("去设置", "ghost", height=30, point_size=9.0)
        settings_button.clicked.connect(self.request_settings)
        self.banner.add_action(settings_button)
        root.addWidget(self.banner)

        content = QHBoxLayout()
        content.setSpacing(14)

        list_card = Card(radius=18, fill=CARD, border=BORDER, padding=10, spacing=8)
        self.list_card = list_card
        list_card.setMinimumWidth(360)
        list_header = QHBoxLayout()
        list_header.setContentsMargins(6, 4, 6, 0)
        list_title = QLabel("邮件记录")
        list_title.setObjectName("H3")
        list_header.addWidget(list_title)
        list_header.addStretch(1)
        self.list_count = QLabel("")
        self.list_count.setObjectName("Tiny")
        list_header.addWidget(self.list_count)
        list_card.body.addLayout(list_header)
        self.list_scroll, _body, self.list_layout = make_scroll()
        list_card.body.addWidget(self.list_scroll, 1)
        content.addWidget(list_card, 4)

        self.preview_card = Card(radius=18, fill=CARD, border=BORDER, padding=16,
                                 spacing=12)
        content.addWidget(self.preview_card, 6)
        root.addLayout(content, 1)
        self.refresh()

    # -------------------------------------------------- 刷新
    def set_query(self, text: str) -> None:
        """由外层「邮箱」页的搜索框驱动（嵌进页面时用）。"""
        text = text or ""
        if self.search.edit.text() == text:
            return
        self.search.edit.blockSignals(True)
        self.search.edit.setText(text)
        self.search.edit.blockSignals(False)
        self.refresh()

    def open_inbox(self) -> None:
        """打开收件箱（多个 IMAP 账户合并显示）。"""
        from .inbox_dialog import InboxDialog

        dialog = InboxDialog(self.store, self.window())
        dialog.refresh()
        dialog.exec()

    def refresh(self) -> None:
        query = self.search.text().strip()
        self._mails = self.store.list_mails(query)
        ids = [int(item["id"]) for item in self._mails]
        if self._selected_id not in ids:
            self._selected_id = ids[0] if ids else None

        counts = self.store.mail_counts()
        parts = [f"共 {counts['total']} 封"]
        if counts["draft"]:
            parts.append(f"草稿 {counts['draft']}")
        if counts["sent"]:
            parts.append(f"已发送 {counts['sent']}")
        if counts["failed"]:
            parts.append(f"失败 {counts['failed']}")
        if counts.get("bounced"):
            parts.append(f"已退信 {counts['bounced']}")
        self.subtitle.setText(" · ".join(parts) if counts["total"] else
                              "选择完成的任务与成果文件，一键发送汇报邮件")
        self.list_count.setText(f"{len(self._mails)} 封" if self._mails else "")

        clear_layout(self.list_layout)
        if not self._mails:
            if query:
                empty = EmptyState(icons.SEARCH, "没有匹配的邮件", "换一个关键词试试")
            else:
                empty = EmptyState(icons.MAIL, "还没有邮件",
                                   "点「新邮件」写信：可以勾选任务与成果自动生成一封汇总邮件，"
                                   "也可以写普通邮件")
            self.list_layout.addWidget(empty)
        else:
            for mail in self._mails:
                row = MailRow(mail, selected=int(mail["id"]) == self._selected_id)
                row.clicked.connect(self._select_mail)
                self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)

        try:
            cfg = SmtpConfig.from_store(self.store)
            self.banner.setVisible(not cfg.is_configured())
        except Exception:
            self.banner.setVisible(True)

        mail = next(
            (item for item in self._mails if int(item["id"]) == self._selected_id), None
        )
        self._build_preview(mail)

    def _select_mail(self, mail_id: int) -> None:
        if mail_id != self._selected_id:
            self._selected_id = mail_id
            self.refresh()

    def select_mail(self, mail_id: int) -> None:
        """由主窗口在发送 / 保存草稿后调用，定位到指定邮件。"""
        self._selected_id = int(mail_id)
        self.refresh()

    # -------------------------------------------------- 预览
    def _build_preview(self, mail: Optional[Dict[str, Any]]) -> None:
        layout = self.preview_card.body
        clear_layout(layout)
        if mail is None:
            empty = EmptyState(
                icons.MAIL,
                "选择一封邮件",
                "左侧列表中的邮件会显示在这里，包含正文与附件清单",
            )
            layout.addWidget(empty, 1)
            return

        header = QHBoxLayout()
        header.setSpacing(10)
        text_box = QVBoxLayout()
        text_box.setSpacing(4)
        subject = QLabel(str(mail.get("subject", "（无主题）")))
        subject.setWordWrap(True)
        subject_font = QFont(self.font())
        subject_font.setPointSizeF(13.5)
        subject_font.setWeight(QFont.Weight.DemiBold)
        subject.setFont(subject_font)
        subject.setStyleSheet(f"color: {TEXT}; background: transparent;")
        text_box.addWidget(subject)
        meta = QLabel(self._meta_text(mail))
        meta.setObjectName("Muted")
        meta.setWordWrap(True)
        text_box.addWidget(meta)
        header.addLayout(text_box, 1)

        status = str(mail.get("status", "draft"))
        tone = theme.mail_tone(status)
        bg = theme.soft(tone, 0.13)
        header.addWidget(Pill(MAIL_STATUS_LABELS.get(status, "草稿"), tone, bg,
                              point_size=8.4, height=23),
                         0, Qt.AlignmentFlag.AlignTop)

        if status == "sent":
            resend = SoftButton("再次发送", "ghost", glyph=icons.REFRESH, height=32,
                                point_size=9.0)
        else:
            resend = SoftButton("继续编辑", "ghost", glyph=icons.EDIT, height=32,
                                point_size=9.0)
        resend.clicked.connect(lambda: self.compose_requested.emit(mail))
        header.addWidget(resend, 0, Qt.AlignmentFlag.AlignTop)

        delete_button = IconButton(
            icons.DELETE, size=32, glyph_size=11, color=MUTED,
            hover_color=DANGER, hover_bg=theme.soft(DANGER, 0.14), tip="删除这封邮件",
        )
        delete_button.clicked.connect(lambda: self._delete_mail(mail))
        header.addWidget(delete_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        error = str(mail.get("error", "")).strip()
        if error and status == "bounced":
            error_label = QLabel(f"退信原因：{error}")
            error_label.setObjectName("Error")
            error_label.setWordWrap(True)
            layout.addWidget(error_label)
        elif error and status == "failed":
            error_label = QLabel(f"发送失败：{error}")
            error_label.setObjectName("Error")
            error_label.setWordWrap(True)
            layout.addWidget(error_label)

        layout.addWidget(_divider())

        scroll, _body, body_layout = make_scroll()
        body_layout.setSpacing(12)
        view = MailBodyView()
        if str(mail.get("html", "")).strip():
            view.set_html(str(mail["html"]))
        else:
            view.set_plain(str(mail.get("text", "")))
        body_layout.addWidget(view)

        attachments = mail.get("attachments") or []
        if attachments:
            section = QWidget()
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(6)
            heading = QLabel(f"附件 · {len(attachments)} 个")
            heading.setObjectName("Small")
            section_layout.addWidget(heading)
            for item in attachments:
                section_layout.addWidget(MailAttachmentRow(item))
            body_layout.addWidget(section)
        body_layout.addStretch(1)
        layout.addWidget(scroll, 1)

    def _meta_text(self, mail: Dict[str, Any]) -> str:
        pieces = [f"致 {mail.get('to_addr', '') or '未填写收件人'}"]
        status = str(mail.get("status", "draft"))
        moment = mail.get("sent_at") or mail.get("created_at")
        label = {"sent": "发送于", "failed": "发送失败于", "draft": "创建于",
                 "bounced": "被退信于"}.get(status, "")
        if moment:
            pieces.append(f"{label} {_format_time(moment)}")
        attachments = mail.get("attachments") or []
        if attachments:
            pieces.append(f"{len(attachments)} 个附件")
        tasks = mail.get("tasks") or []
        if tasks:
            pieces.append(f"{len(tasks)} 项任务")
        return " · ".join(pieces)

    def _delete_mail(self, mail: Dict[str, Any]) -> None:
        dialog = ConfirmDialog(
            self.window(),
            "删除邮件",
            "确定删除这封邮件记录吗？本操作只删除本地记录，不会撤回已发送的邮件。",
            confirm_text="删除记录",
        )
        if dialog.exec() and dialog.confirmed:
            self.store.delete_mail(int(mail["id"]))
            self._selected_id = None
            self.refresh()
            self.data_changed.emit()


def _divider() -> QWidget:
    from .widgets import Divider

    return Divider()
