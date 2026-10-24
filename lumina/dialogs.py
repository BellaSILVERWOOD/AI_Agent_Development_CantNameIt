"""日程编辑对话框与汇报邮件编写对话框。"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QDate, QRectF, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QSizePolicy,
    QTextEdit, QVBoxLayout, QWidget,
)

from . import accounts, icons, theme
from .config import ATTACHMENT_FILTER, MAX_FILE_MB, REMIND_OPTIONS
from .inputs import ColorDots, DateField, TimeField, reminder_combo
from .mailer import (
    MailError, SmtpConfig, build_report, report_subject, validate_attachment,
)
from .sending import MailSender
from .theme import (
    ACCENT, ACCENT2, BORDER, BORDER_SOFT, CARD, CARD_ALT, CARD_HOVER, DANGER, FAINT,
    MUTED, SUCCESS, TEXT, TEXT_SOFT, WARNING,
)
from .utils import date_short_cn, event_range, human_size, month_label, task_moment, truncate
from .widgets import (
    BaseDialog, Card, ConfirmDialog, ElidedLabel, ExtBadge, FieldBlock, IconButton,
    Pill, RoundCheck, Select, SoftButton, ToggleSwitch, icon_font, make_scroll,
    rounded_path,
)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _inline(label_text: str, widget: QWidget, width: int = 68, top: bool = False) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    label = QLabel(label_text)
    label.setObjectName("Small")
    label.setFixedWidth(width)
    if top:
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        label.setContentsMargins(0, 9, 0, 0)
    else:
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(label)
    layout.addWidget(widget, 1)
    return row


# ---------------------------------------------------------------------- 日程编辑
class EventDialog(BaseDialog):
    saved = Signal(int)
    deleted = Signal(int)

    def __init__(self, store, parent=None, event: Optional[Dict[str, Any]] = None,
                 date: Optional[QDate] = None):
        self.store = store
        self.event_data = event  # 注意：不能命名为 event，会覆盖 QObject.event()
        self.editing = event is not None
        super().__init__(
            parent,
            width=600,
            height=480,
            title="编辑日程" if self.editing else "新建日程",
            subtitle="修改后提醒时间会自动重新计算" if self.editing else "设置时间与提醒，让计划准时发生",
            icon=icons.CALENDAR,
        )

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：项目周会、完成季度汇报材料")
        self.title_edit.setFixedHeight(38)
        self.title_edit.setMaxLength(80)
        body = self.body
        body.setSpacing(12)
        body.addWidget(FieldBlock("日程标题", self.title_edit))

        self.date_field = DateField(date or QDate.currentDate())
        self.start_field = TimeField(QTime(9, 0))
        self.end_field = TimeField(QTime(10, 0))
        time_row = QHBoxLayout()
        time_row.setSpacing(10)
        time_row.addWidget(FieldBlock("日期", self.date_field), 3)
        time_row.addWidget(FieldBlock("开始", self.start_field), 2)
        arrow = QLabel("→")
        arrow.setObjectName("Muted")
        arrow.setFixedWidth(18)
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setContentsMargins(0, 20, 0, 0)
        time_row.addWidget(arrow)
        time_row.addWidget(FieldBlock("结束", self.end_field), 2)
        body.addLayout(time_row)

        self.remind_combo = reminder_combo()
        self.color_dots = ColorDots()
        extras = QHBoxLayout()
        extras.setSpacing(16)
        extras.addWidget(FieldBlock("提醒", self.remind_combo), 3)
        self.group_select = Select()
        self.group_select.currentIndexChanged.connect(self._on_group_changed)
        extras.addWidget(FieldBlock("分组", self.group_select), 2)
        extras.addWidget(FieldBlock("颜色", self.color_dots), 2)
        body.addLayout(extras)
        self._reload_groups()

        self.desc_edit = QTextEdit()
        self.desc_edit.setObjectName("Note")
        self.desc_edit.setPlaceholderText("补充说明（可选）：会议目标、交付物、参与人等")
        self.desc_edit.setFixedHeight(104)
        body.addWidget(FieldBlock("备注", self.desc_edit))
        body.addStretch(1)

        self.error_label = QLabel("")
        self.error_label.setObjectName("Error")
        self.error_label.setFixedHeight(18)
        body.addWidget(self.error_label)

        self.cancel_button = SoftButton("取消", "ghost", height=38, min_width=96)
        self.save_button = SoftButton("保存日程", "primary", height=38, min_width=120,
                                      glyph=icons.CHECK)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self._save)

        left_widgets = []
        if self.editing:
            self.delete_button = SoftButton("删除", "danger", height=38, min_width=92,
                                            glyph=icons.DELETE)
            self.delete_button.clicked.connect(self._delete)
            left_widgets.append(self.delete_button)
        self.add_footer(left_widgets, [self.cancel_button, self.save_button])

        if self.editing:
            self._populate(event)
        else:
            self._default_times(QDate(date) if date else QDate.currentDate())

        self.title_edit.setFocus()
        QTimer.singleShot(0, self._reset_title_selection)

    def _reset_title_selection(self) -> None:
        self.title_edit.deselect()
        self.title_edit.setCursorPosition(len(self.title_edit.text()))

    # -------------------------------------------------- 初始化
    def _default_times(self, day: QDate) -> None:
        now = datetime.now()
        if day == QDate.currentDate():
            hour = min(23, now.hour + 1)
            start = QTime(hour, 0 if now.minute < 45 else 30)
        else:
            start = QTime(9, 0)
        self.start_field.set_time(start)
        self.end_field.set_time(start.addSecs(3600))
        self.date_field.set_date(day)

    def _populate(self, event: Dict[str, Any]) -> None:
        self.title_edit.setText(str(event.get("title", "")))
        self.desc_edit.setPlainText(str(event.get("description", "")))
        day = QDate.fromString(str(event.get("date", "")), "yyyy-MM-dd")
        self.date_field.set_date(day if day.isValid() else QDate.currentDate())
        start = QTime.fromString(str(event.get("start_time", "09:00")), "HH:mm")
        end = QTime.fromString(str(event.get("end_time", "10:00")), "HH:mm")
        self.start_field.set_time(start if start.isValid() else QTime(9, 0))
        self.end_field.set_time(end if end.isValid() else QTime(10, 0))
        self.color_dots.set_color(str(event.get("color", "violet")))
        self._reload_groups(event.get("group_id"))
        self._on_group_changed(0)
        minutes = event.get("remind_minutes")
        for index in range(self.remind_combo.count()):
            if self.remind_combo.itemData(index) == minutes:
                self.remind_combo.setCurrentIndex(index)
                break

    # -------------------------------------------------- 交互
    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)

    # -------------------------------------------------- 分组
    def _reload_groups(self, selected=None) -> None:
        self.group_select.blockSignals(True)
        self.group_select.clear()
        self.group_select.addItem("无分组", None)
        for group in self.store.list_groups():
            self.group_select.addItem(group["name"], int(group["id"]))
        self.group_select.addItem("＋ 新建分组…", "new")
        index = self.group_select.findData(selected)
        self.group_select.setCurrentIndex(index if index >= 0 else 0)
        self.group_select.blockSignals(False)

    def _current_group_id(self):
        data = self.group_select.currentData()
        return int(data) if isinstance(data, int) else None

    def _on_group_changed(self, _index: int) -> None:
        data = self.group_select.currentData()
        if data == "new":
            self._create_group()
            return
        colors = self.store.group_colors()
        group_id = self._current_group_id()
        if group_id is not None and group_id in colors:
            self.color_dots.set_color(colors[group_id])
            self.color_dots.setEnabled(False)
        else:
            self.color_dots.setEnabled(True)

    def _create_group(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "新建分组", "分组名称：")
        if not ok or not name.strip():
            self._reload_groups(self._current_group_id())
            return
        group_id = self.store.add_group(name.strip(), self.color_dots.color())
        self._reload_groups(group_id)
        self._on_group_changed(0)

    def _save(self) -> None:
        title = self.title_edit.text().strip()
        if not title:
            self._set_error("请先填写日程标题")
            self.title_edit.setFocus()
            return
        start = self.start_field.time()
        end = self.end_field.time()
        if end <= start:
            self._set_error("结束时间需要晚于开始时间")
            return
        day = self.date_field.date()
        try:
            if self.editing:
                event_id = int(self.event_data["id"])
                self.store.update_event(
                    event_id,
                    title=title,
                    description=self.desc_edit.toPlainText().strip(),
                    date=day.toString("yyyy-MM-dd"),
                    start_time=start.toString("HH:mm"),
                    end_time=end.toString("HH:mm"),
                    remind_minutes=self.remind_combo.currentData(),
                    color=self.color_dots.color(),
                    group_id=self._current_group_id(),
                )
            else:
                event_id = self.store.add_event(
                    title=title,
                    description=self.desc_edit.toPlainText().strip(),
                    date=day.toString("yyyy-MM-dd"),
                    start_time=start.toString("HH:mm"),
                    end_time=end.toString("HH:mm"),
                    remind_minutes=self.remind_combo.currentData(),
                    color=self.color_dots.color(),
                    group_id=self._current_group_id(),
                )
        except Exception as exc:
            self._set_error(f"保存失败：{exc}")
            return
        self.saved.emit(event_id)
        self.accept()

    def _delete(self) -> None:
        if not self.editing:
            return
        dialog = ConfirmDialog(
            self,
            "删除日程",
            f"确定删除「{self.event_data.get('title', '')}」吗？删除后无法恢复。",
            confirm_text="删除日程",
        )
        if dialog.exec() and dialog.confirmed:
            self.store.delete_event(int(self.event_data["id"]))
            self.deleted.emit(int(self.event_data["id"]))
            self.accept()


# ---------------------------------------------------------------------- 邮件编写
class DropZone(QWidget):
    clicked = Signal()
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = False
        self._dragging = False
        self.setFixedHeight(86)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)

    def set_drag_active(self, active: bool) -> None:
        if self._dragging != active:
            self._dragging = active
            self.update()

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
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.set_drag_active(True)

    def dragLeaveEvent(self, event):
        self.set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.set_drag_active(False)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = rounded_path(rect, 12)
        state = theme.appearance()
        if self._dragging:
            painter.fillPath(path, state.accent_q(0.09))
        elif self._hover:
            painter.fillPath(path, QColor("#FFFFFF"))
        else:
            painter.fillPath(path, QColor(CARD_ALT))
        pen = QPen(
            state.accent_q() if (self._hover or self._dragging) else QColor(BORDER),
            1.2,
        )
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.setFont(icon_font(17))
        painter.setPen(state.accent_q() if (self._hover or self._dragging) else QColor(MUTED))
        painter.drawText(
            QRectF(0, 12, rect.width(), 22), int(Qt.AlignmentFlag.AlignCenter), icons.UPLOAD
        )
        body_font = QFont(self.font())
        body_font.setPointSizeF(9.0)
        painter.setFont(body_font)
        painter.setPen(QColor(TEXT_SOFT))
        painter.drawText(
            QRectF(0, 38, rect.width(), 18),
            int(Qt.AlignmentFlag.AlignCenter),
            "点击选择文件，或将文件拖拽到这里",
        )
        hint_font = QFont(self.font())
        hint_font.setPointSizeF(8.0)
        painter.setFont(hint_font)
        painter.setPen(QColor(FAINT))
        painter.drawText(
            QRectF(0, 58, rect.width(), 16),
            int(Qt.AlignmentFlag.AlignCenter),
            f"支持图片 / PDF / Word / Excel / PPT / 压缩包 · 单个 ≤ {MAX_FILE_MB} MB",
        )


class TaskPickRow(QWidget):
    toggled = Signal()

    def __init__(self, task: Dict[str, Any], checked: bool = False, parent=None):
        super().__init__(parent)
        self.task = task
        self._hover = False
        self._checked = bool(checked)
        self.setFixedHeight(50)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)
        self.check = RoundCheck(checked, 18)
        self.check.toggled.connect(self._on_check)
        layout.addWidget(self.check)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        self.title_label = ElidedLabel(str(task.get("title", "")))
        self.title_label.setStyleSheet(
            f"color: {TEXT}; font-size: 9.4pt; font-weight: 500; background: transparent;"
        )
        meta = QLabel(task_moment(task))
        meta.setObjectName("Tiny")
        text_box.addWidget(self.title_label)
        text_box.addWidget(meta)
        layout.addLayout(text_box, 1)

        done = bool(task.get("done"))
        self.tag = Pill(
            "已完成" if done else "计划中",
            SUCCESS if done else MUTED,
            theme.soft(SUCCESS, 0.14) if done else theme.CARD_ALT,
        )
        layout.addWidget(self.tag)
        self._apply_check_style()

    def is_checked(self) -> bool:
        return self.check.isChecked()

    def set_checked(self, checked: bool) -> None:
        self.check.setChecked(checked)
        self._apply_check_style()
        self.update()

    def _on_check(self, checked):
        self._apply_check_style()
        self.update()
        self.toggled.emit()

    def _apply_check_style(self) -> None:
        checked = self.check.isChecked()
        self.title_label.setStyleSheet(
            f"color: {TEXT if checked else TEXT_SOFT}; font-size: 9.4pt;"
            f" font-weight: {'600' if checked else '400'}; background: transparent;"
        )

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.check.setChecked(not self.check.isChecked(), emit=True)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded_path(rect, 9)
        if self.check.isChecked():
            painter.fillPath(path, theme.appearance().accent_q(0.09))
            painter.setPen(QPen(theme.appearance().accent_q(0.30), 1.0))
            painter.drawPath(path)
        elif self._hover:
            painter.fillPath(path, QColor(0, 0, 0, 8))


class AttachmentRow(QWidget):
    removed = Signal(str)

    def __init__(self, info: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.info = info
        self.setFixedHeight(52)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(10)
        layout.addWidget(ExtBadge(str(info.get("name", "")), 34))
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        name = ElidedLabel(str(info.get("name", "")))
        name.setStyleSheet(f"color: {TEXT_SOFT}; font-size: 9.2pt; background: transparent;")
        meta = QLabel(f"{info.get('size_text', human_size(info.get('size', 0)))}")
        meta.setObjectName("Tiny")
        text_box.addWidget(name)
        text_box.addWidget(meta)
        layout.addLayout(text_box, 1)
        folder_btn = IconButton(icons.FOLDER, size=26, glyph_size=11, tip="在文件夹中显示")
        folder_btn.clicked.connect(self._open_folder)
        layout.addWidget(folder_btn)
        remove_btn = IconButton(icons.CLOSE, size=26, glyph_size=9, tip="移除附件")
        remove_btn.clicked.connect(lambda: self.removed.emit(str(info.get("path", ""))))
        layout.addWidget(remove_btn)

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
        painter.fillPath(rounded_path(rect, 10), QColor(CARD_ALT))
        painter.setPen(QPen(QColor(BORDER_SOFT), 1.0))
        painter.drawPath(rounded_path(rect, 10))


class ComposeDialog(BaseDialog):
    sent = Signal(int)
    draft_saved = Signal(int)
    request_settings = Signal()

    def __init__(self, store, parent=None, mail: Optional[Dict[str, Any]] = None):
        self.store = store
        self.mail = mail
        self.mail_id: Optional[int] = None
        self._subject_dirty = False
        self._sending = False
        self._send_dots = 0
        self._attachments: List[Dict[str, Any]] = []
        self._task_rows: List[TaskPickRow] = []
        self._missing_attachments = 0

        if mail is not None and mail.get("status") in ("draft", "failed"):
            self.mail_id = int(mail["id"])

        super().__init__(
            parent,
            width=944,
            height=648,
            title="编辑汇报邮件" if mail else "新建汇报邮件",
            subtitle="勾选完成的任务，或上传成果文件，一键发送给指定邮箱",
            icon=icons.MAIL,
        )
        self.setAcceptDrops(True)

        self.recipient = Select()
        self.recipient.setEditable(True)
        self.recipient.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.recipient.setFixedHeight(38)
        self.recipient.setMinimumWidth(320)
        contacts = self.store.list_contacts()
        for contact in contacts:
            label = f"{contact['name']} · {contact['email']}" if contact["name"] else contact["email"]
            self.recipient.addItem(label, contact["email"])
        self.recipient.activated.connect(self._on_recipient_pick)
        line = self.recipient.lineEdit()
        if line is not None:
            line.setPlaceholderText("name@company.com")
        default_to = self.store.get_setting("default_to", "").strip()
        self.recipient.setEditText(default_to)
        self.body.addWidget(_inline("收件人", self.recipient))

        # 发件邮箱快捷切换（与设置页共享账户列表）
        self.sender_select = Select()
        self.sender_select.setFixedHeight(38)
        self.sender_select.setMinimumWidth(320)
        self._accounts = accounts.load(self.store)
        active_id = accounts.active_id(self.store)
        for account in self._accounts:
            label = account.get("label") or account.get("sender_email") or account.get("host")
            self.sender_select.addItem(label, account.get("id"))
        index = self.sender_select.findData(active_id)
        if index >= 0:
            self.sender_select.setCurrentIndex(index)
        if self._accounts:
            self.body.addWidget(_inline("发件邮箱", self.sender_select))

        self.mode_select = Select()
        self.mode_select.addItem("汇报邮件（勾选任务 + 上传成果）", "report")
        self.mode_select.addItem("常规邮件（自由主题与正文）", "normal")
        self.mode_select.currentIndexChanged.connect(self._on_mode_changed)
        self.body.addWidget(_inline("邮件类型", self.mode_select))


        label_row = QHBoxLayout()
        label_row.setSpacing(8)
        heading = QLabel("汇报内容")
        heading.setObjectName("H3")
        hint = QLabel("选择要写进邮件的已完成任务，和 / 或上传成果文件")
        hint.setObjectName("Tiny")
        self.section_heading = heading
        self.section_hint = hint
        label_row.addWidget(heading)
        label_row.addWidget(hint)
        label_row.addStretch(1)
        self.body.addLayout(label_row)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.task_card = self._build_task_card()
        self.result_card = self._build_result_card()
        cards.addWidget(self.task_card, 1)
        cards.addWidget(self.result_card, 1)
        self.body.addLayout(cards)

        self.subject_edit = QLineEdit()
        self.subject_edit.setFixedHeight(36)
        self.subject_edit.setPlaceholderText("自动生成，也可以手动修改")
        self.subject_edit.textEdited.connect(self._on_subject_edited)
        regenerate = IconButton(icons.REFRESH, size=30, glyph_size=12, tip="重新生成主题")
        regenerate.clicked.connect(self._regenerate_subject)
        subject_row = _inline("邮件主题", self.subject_edit)
        subject_row.layout().addWidget(regenerate)
        self.body.addWidget(subject_row)

        self.note_edit = QTextEdit()
        self.note_edit.setObjectName("Note")
        self.note_edit.setFixedHeight(54)
        self.note_edit.setPlaceholderText("补充说明（可选）：会以「补充说明」段落写入邮件正文")
        self.note_row = _inline("补充说明", self.note_edit, top=True)
        self.note_label = self.note_row.findChildren(QLabel)[0]
        self.body.addWidget(self.note_row)
        self.body.addStretch(1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Small")
        self.settings_button = SoftButton("前往设置", "ghost", height=32, point_size=9.0)
        self.settings_button.clicked.connect(self._go_settings)
        self.settings_button.hide()
        self.draft_button = SoftButton("保存草稿", "ghost", height=38, min_width=104)
        self.send_button = SoftButton("发送邮件", "primary", height=38, min_width=132,
                                      glyph=icons.SEND)
        self.draft_button.clicked.connect(self._save_draft)
        self.send_button.clicked.connect(self._send)
        self.add_footer(
            [self.status_label, self.settings_button],
            [self.draft_button, self.send_button],
        )

        self._send_timer = QTimer(self)
        self._send_timer.setInterval(420)
        self._send_timer.timeout.connect(self._tick_sending)
        self.mail_sender = MailSender(self)
        self.mail_sender.finished.connect(self._on_sent)

        self._load_tasks()
        if mail:
            self._populate(mail)
        self._update_counts()
        self._refresh_subject()
        self._auto_subject = self.subject_edit.text()
        if self._missing_attachments:
            self._status(
                f"有 {self._missing_attachments} 个附件已不存在，已自动忽略", WARNING
            )

    # -------------------------------------------------- 卡片构建
    def _build_task_card(self) -> Card:
        card = Card(radius=16, fill=CARD, border=BORDER, padding=14, spacing=10)
        card.setFixedHeight(300)
        header = QHBoxLayout()
        header.setSpacing(10)
        self.tasks_switch = ToggleSwitch(True)
        header.addWidget(self.tasks_switch, 0, Qt.AlignmentFlag.AlignTop)
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        title = QLabel("我完成了什么任务")
        title.setObjectName("H3")
        desc = QLabel("勾选要汇报的日程，自动生成清单")
        desc.setObjectName("Tiny")
        text_box.addWidget(title)
        text_box.addWidget(desc)
        header.addLayout(text_box, 1)
        self.task_count = Pill("已选 0 项", MUTED, theme.CARD_ALT)
        header.addWidget(self.task_count, 0, Qt.AlignmentFlag.AlignTop)
        card.body.addLayout(header)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(6)
        scroll, _body, layout = make_scroll()
        scroll.setMinimumHeight(120)
        self.task_layout = layout
        container_layout.addWidget(scroll)
        card.body.addWidget(container, 1)
        self._tasks_container = container

        self.tasks_switch.toggled.connect(self._on_tasks_toggle)
        return card

    def _build_result_card(self) -> Card:
        card = Card(radius=16, fill=CARD, border=BORDER, padding=14, spacing=10)
        card.setFixedHeight(300)
        header = QHBoxLayout()
        header.setSpacing(10)
        self.results_switch = ToggleSwitch(True)
        header.addWidget(self.results_switch, 0, Qt.AlignmentFlag.AlignTop)
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        title = QLabel("我完成的结果")
        title.setObjectName("H3")
        desc = QLabel("上传图片、PDF、Word 等成果文件")
        desc.setObjectName("Tiny")
        text_box.addWidget(title)
        text_box.addWidget(desc)
        header.addLayout(text_box, 1)
        self.result_count = Pill("0 个附件", MUTED, theme.CARD_ALT)
        header.addWidget(self.result_count, 0, Qt.AlignmentFlag.AlignTop)
        card.body.addLayout(header)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(6)
        self.drop_zone = DropZone()
        self.drop_zone.clicked.connect(self._pick_files)
        self.drop_zone.files_dropped.connect(self.add_files)
        container_layout.addWidget(self.drop_zone)
        scroll, _body, layout = make_scroll()
        scroll.setMinimumHeight(96)
        self.attach_layout = layout
        container_layout.addWidget(scroll, 1)
        card.body.addWidget(container, 1)
        self._results_container = container

        self.results_switch.toggled.connect(self._on_results_toggle)
        return card

    # -------------------------------------------------- 数据加载
    def _load_tasks(self) -> None:
        chosen = set()
        stored_tasks: List[Dict[str, Any]] = []
        if self.mail:
            stored_tasks = list(self.mail.get("tasks") or [])
            chosen = {(str(t.get("date", "")), str(t.get("title", ""))) for t in stored_tasks}
        candidates = self.store.task_candidates()
        seen = set()
        for task in candidates:
            key = (str(task.get("date", "")), str(task.get("title", "")))
            seen.add(key)
            if self.mail is not None:
                checked = key in chosen
            else:
                # 新邮件默认一项都不勾，由用户自己挑要写进汇报的日程
                checked = False
            self._add_task_row(task, checked)
        for task in stored_tasks:
            key = (str(task.get("date", "")), str(task.get("title", "")))
            if key not in seen:
                self._add_task_row({**task, "done": True, "_stored": True}, True)
        if not self._task_rows:
            empty = QLabel("近 30 天还没有可汇报的日程\n可以先在日历中创建并标记完成")
            empty.setObjectName("Tiny")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(110)
            self.task_layout.addWidget(empty)
        self.task_layout.addStretch(1)

    def _add_task_row(self, task: Dict[str, Any], checked: bool) -> None:
        row = TaskPickRow(task, checked)
        row.toggled.connect(self._on_task_toggled)
        self._task_rows.append(row)
        self.task_layout.addWidget(row)

    def _populate(self, mail: Dict[str, Any]) -> None:
        self.recipient.setEditText(str(mail.get("to_addr", "")))
        self.subject_edit.setText(str(mail.get("subject", "")))
        self._subject_dirty = True
        self.note_edit.setPlainText(str(mail.get("note", "")))
        for item in mail.get("attachments") or []:
            path = str(item.get("path", ""))
            if not path or not os.path.isfile(path):
                self._missing_attachments += 1
                continue
            try:
                info = validate_attachment(path)
            except MailError:
                self._missing_attachments += 1
                continue
            self._attachments.append(info)
        if self._attachments:
            self._refresh_attachments()

    # -------------------------------------------------- 任务 / 附件交互
    def _on_task_toggled(self) -> None:
        self._update_counts()
        self._refresh_subject()

    def _on_tasks_toggle(self, checked: bool) -> None:
        self._set_section_enabled(self._tasks_container, checked)
        self.task_count.setEnabled(checked)
        self._update_counts()
        self._refresh_subject()

    def _on_results_toggle(self, checked: bool) -> None:
        self._set_section_enabled(self._results_container, checked)
        self.result_count.setEnabled(checked)
        self._update_counts()
        self._refresh_subject()

    @staticmethod
    def _set_section_enabled(container: QWidget, enabled: bool) -> None:
        container.setEnabled(enabled)
        effect = container.graphicsEffect()
        if effect is None:
            from PySide6.QtWidgets import QGraphicsOpacityEffect

            effect = QGraphicsOpacityEffect(container)
            container.setGraphicsEffect(effect)
        effect.setOpacity(1.0 if enabled else 0.42)

    def _selected_tasks(self) -> List[Dict[str, Any]]:
        tasks = []
        for row in self._task_rows:
            if row.is_checked():
                task = row.task
                tasks.append({
                    "id": task.get("id"),
                    "title": task.get("title", ""),
                    "date": task.get("date", ""),
                    "start_time": task.get("start_time", ""),
                    "end_time": task.get("end_time", ""),
                    "description": task.get("description", ""),
                })
        tasks.sort(key=lambda item: (item["date"], item["start_time"]))
        return tasks

    def _pick_files(self) -> None:
        files, _filter = QFileDialog.getOpenFileNames(self, "选择成果文件", "", ATTACHMENT_FILTER)
        if files:
            self.add_files(files)

    def add_files(self, paths: List[str]) -> None:
        added = 0
        for path in paths:
            try:
                info = validate_attachment(path)
            except MailError as exc:
                self._status(str(exc), DANGER)
                continue
            if any(item["path"] == info["path"] for item in self._attachments):
                continue
            self._attachments.append(info)
            added += 1
        if added:
            self._refresh_attachments()
            self._update_counts()
            self._refresh_subject()
            if not self.results_switch.isChecked():
                self.results_switch.setChecked(True)
            self._status(f"已添加 {added} 个附件", SUCCESS)

    def _refresh_attachments(self) -> None:
        while self.attach_layout.count():
            item = self.attach_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for info in self._attachments:
            row = AttachmentRow(info)
            row.removed.connect(self._remove_attachment)
            self.attach_layout.addWidget(row)
        self.attach_layout.addStretch(1)

    def _remove_attachment(self, path: str) -> None:
        self._attachments = [item for item in self._attachments if item["path"] != path]
        self._refresh_attachments()
        self._update_counts()
        self._refresh_subject()

    # -------------------------------------------------- 主题与计数
    def _on_subject_edited(self, _text: str) -> None:
        self._subject_dirty = True

    def _regenerate_subject(self) -> None:
        self._subject_dirty = False
        self._refresh_subject()

    def _counts(self) -> tuple[int, int]:
        task_count = len(self._selected_tasks()) if self.tasks_switch.isChecked() else 0
        attach_count = len(self._attachments) if self.results_switch.isChecked() else 0
        return task_count, attach_count

    def _update_counts(self) -> None:
        task_count, attach_count = self._counts()
        self.task_count.set_text(f"已选 {task_count} 项")
        if attach_count:
            total = sum(int(item.get("size", 0)) for item in self._attachments)
            self.result_count.set_text(f"{attach_count} 个 · {human_size(total)}")
        else:
            self.result_count.set_text("0 个附件")

    def _signature(self) -> str:
        """设置里的邮件签名（纯文本）。"""
        try:
            return (self.store.get_setting("mail_signature", "") or "").strip()
        except Exception:
            return ""

    def _refresh_subject(self) -> None:
        if self._subject_dirty:
            return
        task_count, attach_count = self._counts()
        self.subject_edit.setText(report_subject(task_count, attach_count))

    def _on_recipient_pick(self, index: int) -> None:
        email = self.recipient.itemData(index)
        if email:
            self.recipient.setEditText(str(email))

    # -------------------------------------------------- 状态
    def _status(self, text: str, color: str = MUTED) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-size: 9pt;")

    def _clear_status(self) -> None:
        self._status("", MUTED)
        self.settings_button.hide()

    def _go_settings(self) -> None:
        self.request_settings.emit()
        self.reject()

    # -------------------------------------------------- 邮件类型
    def mode(self) -> str:
        data = self.mode_select.currentData() if hasattr(self, "mode_select") else "report"
        return data or "report"

    def _on_mode_changed(self, _index: int) -> None:
        normal = self.mode() == "normal"
        if hasattr(self, "task_card"):
            # 常规邮件不需要勾选任务，但保留「附件」卡片
            self.task_card.setVisible(not normal)
            self.result_card.setVisible(True)
        title = (("编辑邮件" if getattr(self, "mail", None) else "新建邮件")
                 if normal else
                 ("编辑汇报邮件" if getattr(self, "mail", None) else "新建汇报邮件"))
        self.setWindowTitle(title)
        if hasattr(self, "title_label"):
            self.title_label.setText(title)
        if hasattr(self, "section_heading"):
            self.section_heading.setText("附件" if normal else "汇报内容")
        if hasattr(self, "subtitle_label"):
            self.subtitle_label.setText(
                "写一封普通邮件，可附带文件；正文末尾会自动加上签名" if normal
                else "勾选完成的任务，或上传成果文件，一键发送给指定邮箱"
            )
        if hasattr(self, "note_label"):
            self.note_label.setText("正文" if normal else "补充说明")
        if hasattr(self, "section_hint"):
            self.section_hint.setText(
                "可以直接添加要随邮件一起发送的文件" if normal
                else "选择要写进邮件的已完成任务，和 / 或上传成果文件"
            )
        if normal:
            # 常规模式不预填主题：把自动生成的那条清掉
            current = self.subject_edit.text().strip()
            if not current or current == (getattr(self, "_auto_subject", "") or "").strip():
                self.subject_edit.clear()
            self.subject_edit.setPlaceholderText("邮件主题（留空则用正文首行）")
            self.note_edit.setPlaceholderText("邮件正文")
            self.note_edit.setFixedHeight(150)
        else:
            self.subject_edit.setPlaceholderText("自动生成，也可以手动修改")
            self.note_edit.setPlaceholderText("补充说明（可选）：会以「补充说明」段落写入邮件正文")
            self.note_edit.setFixedHeight(54)
            self._refresh_subject()

    def _build_payload(self) -> Dict[str, Any]:
        signature = self._signature()
        if self.mode() == "normal":
            body_text = self.note_edit.toPlainText().strip()
            subject = self.subject_edit.text().strip()
            if not subject and body_text:
                # 没写主题：跟邮件客户端一样，取正文首行
                first_line = body_text.splitlines()[0].strip()
                subject = first_line[:60]
            if signature:
                body_text = body_text + "\n\n-- \n" + signature
            html = "".join(
                "<p>" + line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                + "</p>"
                for line in body_text.splitlines()
            ) or "<p></p>"
            return {
                "to_addr": self.recipient.currentText().strip(),
                "subject": subject,
                "html": f"<div style='font-family:Segoe UI,Microsoft YaHei;line-height:1.7'>{html}</div>",
                "text": body_text,
                "tasks": [],
                "attachments": list(self._attachments),
                "note": self.note_edit.toPlainText().strip(),
            }
        include_tasks = self.tasks_switch.isChecked()
        include_results = self.results_switch.isChecked()
        tasks = self._selected_tasks() if include_tasks else []
        attachments = list(self._attachments) if include_results else []
        auto_subject, html_body, text_body = build_report(
            tasks,
            attachments,
            self.note_edit.toPlainText(),
            include_tasks,
            include_results,
        )
        subject = self.subject_edit.text().strip() or auto_subject
        if signature:
            text_body = text_body.rstrip() + "\n\n-- \n" + signature
            escaped = "".join(
                "<div>" + line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                + "</div>"
                for line in signature.splitlines()
            )
            html_body = (html_body
                         + "<div style='margin-top:18px;border-top:1px solid #DCDCE2;"
                           "padding-top:10px;color:#6E6E73;font-size:13px'>"
                         + escaped + "</div>")
        return {
            "to_addr": self.recipient.currentText().strip(),
            "subject": subject,
            "html": html_body,
            "text": text_body,
            "tasks": tasks,
            "attachments": attachments,
            "note": self.note_edit.toPlainText().strip(),
        }

    # -------------------------------------------------- 保存 / 发送
    def _validate(self) -> Optional[str]:
        payload = self._build_payload()
        if not EMAIL_RE.match(payload["to_addr"]):
            return "请填写有效的收件邮箱地址"
        if self.mode() == "normal":
            if not payload["text"].strip():
                return "请填写邮件正文"
            total = sum(int(item.get("size", 0)) for item in payload["attachments"])
            if total > 25 * 1024 * 1024:
                return "附件总大小超过 25 MB，请减少附件后再发送"
            return None
        if not payload["tasks"] and not payload["attachments"]:
            return "请至少选择一项任务，或上传一个成果附件"
        if payload["attachments"]:
            total = sum(int(item.get("size", 0)) for item in payload["attachments"])
            if total > 25 * 1024 * 1024:
                return "附件总大小超过 25 MB，请减少附件后再发送"
            for item in payload["attachments"]:
                if not os.path.isfile(item["path"]):
                    return f"附件「{item['name']}」已被移动或删除"
        return None

    def _save_draft(self) -> None:
        payload = self._build_payload()
        self.mail_id = self._persist(payload, "draft")
        self.draft_saved.emit(self.mail_id)
        self.accept()

    def _persist(self, payload: Dict[str, Any], status: str) -> int:
        tasks_json = json.dumps(payload["tasks"], ensure_ascii=False)
        attachments_json = json.dumps(payload["attachments"], ensure_ascii=False)
        if self.mail_id is not None:
            self.store.update_mail(
                self.mail_id,
                to_addr=payload["to_addr"],
                subject=payload["subject"],
                html=payload["html"],
                text=payload["text"],
                tasks_json=tasks_json,
                attachments_json=attachments_json,
                note=payload["note"],
                status=status,
                error="",
            )
            if status == "sent":
                self.store.set_mail_status(self.mail_id, "sent")
            return self.mail_id
        self.mail_id = self.store.add_mail(
            to_addr=payload["to_addr"],
            subject=payload["subject"],
            html=payload["html"],
            text=payload["text"],
            tasks=payload["tasks"],
            attachments=payload["attachments"],
            note=payload["note"],
            status=status,
        )
        return self.mail_id

    def _current_smtp(self) -> SmtpConfig:
        """按写信窗口里选中的发件邮箱构造 SMTP 配置。"""
        account_id = self.sender_select.currentData() if hasattr(self, "sender_select") else ""
        if account_id:
            account = accounts.get(self.store, account_id)
            if account:
                return SmtpConfig(
                    host=account.get("host", ""),
                    port=int(account.get("port", 465) or 465),
                    security=account.get("security", "ssl") or "ssl",
                    user=account.get("user", ""),
                    password=accounts.password(self.store, account["id"]),
                    sender_name=account.get("sender_name", ""),
                    sender_email=account.get("sender_email", ""),
                    account_id=account["id"],
                    account_label=account.get("label", ""),
                )
        return SmtpConfig.from_store(self.store)

    def _send(self) -> None:
        if self._sending:
            return
        payload = self._build_payload()
        problem = self._validate()
        if problem:
            self._status(problem, DANGER)
            return
        cfg = self._current_smtp()
        if not cfg.is_configured():
            self._status("尚未配置发件邮箱，请先在「设置」中填写 SMTP 信息", WARNING)
            self.settings_button.show()
            return
        self.settings_button.hide()
        self.mail_id = self._persist(payload, "draft")
        self._set_sending(True, cfg)
        self.mail_sender.send(
            self.mail_id,
            payload["to_addr"],
            payload["subject"],
            payload["text"],
            payload["html"],
            payload["attachments"],
            cfg,
        )

    def _tick_sending(self) -> None:
        self._send_dots = (self._send_dots + 1) % 4
        host = self.store.get_setting("smtp_host", "")
        self._status(f"正在通过 {host} 发送" + "·" * self._send_dots, ACCENT2)

    def _set_sending(self, sending: bool, cfg: Optional[SmtpConfig] = None) -> None:
        self._sending = sending
        for widget in (
            self.recipient, self.tasks_switch, self.results_switch, self.subject_edit,
            self.note_edit, self.draft_button, self.send_button, self.drop_zone,
        ):
            widget.setEnabled(not sending)
        if sending:
            self.close_button.setEnabled(False)
            self.send_button.setText("发送中…")
            self._send_dots = 0
            self._tick_sending()
            self._send_timer.start()
        else:
            self.close_button.setEnabled(True)
            self.send_button.setText("发送邮件")
            self._send_timer.stop()

    def _on_sent(self, mail_id: int, ok: bool, error: str) -> None:
        self._set_sending(False)
        if ok:
            self.store.set_mail_status(mail_id, "sent")
            self.sent.emit(mail_id)
            self.accept()
        else:
            self.store.set_mail_status(mail_id, "failed", error)
            self._status(error, DANGER)
            self._status_mail_failed(mail_id)
            self.failed_mail_id = mail_id

    def _status_mail_failed(self, mail_id: int) -> None:
        """发送失败后保留邮件记录；再次点击发送会更新同一条记录。"""
        self.mail_id = mail_id

    # -------------------------------------------------- 拖拽
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_zone.set_drag_active(True)

    def dragLeaveEvent(self, event):
        self.drop_zone.set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.drop_zone.set_drag_active(False)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.add_files(paths)
        event.acceptProposedAction()

    def keyPressEvent(self, event):
        if self._sending and event.key() == Qt.Key.Key_Escape:
            return
        super().keyPressEvent(event)

    def reject(self) -> None:
        if self._sending:
            return
        super().reject()
