"""收件箱面板：多账户 IMAP 邮件列表 + 正文预览。

* 列表：标题加粗，下面一行是「发件人 · 时间」（多账户时再带账户名）；
* 本地保存：收到的邮件头写进 `inbox_messages`，打开页面先显示本地缓存，
  再后台刷新；正文读过一次也存下来，下次不再联网；
* 没配账户时整块提示去设置。
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QScrollArea,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import adminsign, bounce, imap_inbox, theme
from .utils import human_size
from .widgets import (
    Card, ElidedLabel, SoftButton, make_scroll, rounded_path,
)

MAX_MESSAGES = 40
FETCH_COOLDOWN = 90.0   # 秒：切回收件箱时，这么久之内不重复连 IMAP


def _palette() -> Dict[str, str]:
    mode = "dark" if theme.effective_mode() == "dark" else "light"
    return theme.PALETTES.get(mode) or theme.PALETTES["light"]


def sender_name(raw: str) -> str:
    """把「张三 <zhangsan@example.com>」变成「张三」。"""
    text = (raw or "").strip()
    if not text:
        return "（未知发件人）"
    if "<" in text:
        head = text.split("<", 1)[0].strip().strip('"').strip()
        if head:
            return head
        return text.split("<", 1)[1].split(">", 1)[0].strip() or text
    return text


#: 附件卡片上的类型徽标（跟文件后缀对应）
_ATTACH_BADGES = {
    ".pdf": ("PDF", "#C0504D"), ".docx": ("W", "#2B579A"),
    ".doc": ("W", "#2B579A"), ".xlsx": ("X", "#217346"),
    ".xls": ("X", "#217346"), ".pptx": ("P", "#D24726"),
    ".ppt": ("P", "#D24726"), ".png": ("IMG", "#7A5AF8"),
    ".jpg": ("IMG", "#7A5AF8"), ".jpeg": ("IMG", "#7A5AF8"),
    ".gif": ("IMG", "#7A5AF8"), ".zip": ("ZIP", "#8A6D3B"),
}


class _AttachmentChip(Card):
    """附件小卡：左边类型徽标，右边文件名 + 大小；点一下保存到下载目录。"""

    def __init__(self, item: Dict[str, Any], on_save, parent=None):
        from pathlib import Path

        from .widgets import Card

        super().__init__(radius=10, fill=theme.CARD_ALT,
                         border=theme.BORDER_SOFT, padding=8, spacing=6,
                         parent=parent)
        name = str(item.get("name") or "附件")
        suffix = Path(name).suffix.lower()
        badge, color = _ATTACH_BADGES.get(suffix, ("FILE", "#6B7280"))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        mark = QLabel(badge)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(30, 30)
        mark.setStyleSheet(
            f"QLabel {{ background: {color}; color: #FFFFFF; border-radius: 6px;"
            f" font-size: {'7.4pt' if len(badge) > 1 else '11pt'};"
            " font-weight: 700; }")
        row.addWidget(mark, 0)
        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        title = ElidedLabel(name)
        title.setFixedWidth(148)
        title.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 8.8pt; background: transparent;")
        text_box.addWidget(title)
        size = human_size(item.get("size"))
        hint = QLabel((size or "") + ("　点一下保存" if size else "点一下保存"))
        hint.setStyleSheet(
            f"color: {theme.MUTED}; font-size: 8pt; background: transparent;")
        text_box.addWidget(hint)
        row.addLayout(text_box, 1)
        self.body.addLayout(row)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("保存到下载目录：%s" % name)
        self._on_save = on_save

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.MouseButton.LeftButton and callable(self._on_save):
                self._on_save()
                return
        except Exception:
            pass
        super().mouseReleaseEvent(event)


class MailImageDialog(QDialog):
    """双击邮件里的图片：在独立窗口里放大看（可另存）。"""

    def __init__(self, path: str, title: str = "图片", parent=None):
        super().__init__(parent)
        from pathlib import Path

        self.setWindowTitle(title)
        self.setStyleSheet(
            "QDialog { background: %s; } QLabel { background: transparent; }"
            % theme.CARD)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        self.path = str(path or "")
        pixmap = QPixmap(self.path) if Path(self.path).exists() else QPixmap()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if pixmap.isNull():
            self.image_label.setText("这张图片没能读出来（可能在邮件里被压掉了）")
        else:
            self.image_label.setPixmap(pixmap)
        holder_layout.addWidget(self.image_label)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.save_button = SoftButton("另存为图片", "primary", height=32,
                                      min_width=110)
        self.save_button.setEnabled(not pixmap.isNull())
        self.save_button.clicked.connect(self._save)
        row.addWidget(self.save_button)
        row.addStretch(1)
        self.close_button = SoftButton("关闭", "ghost", height=32, min_width=80)
        self.close_button.clicked.connect(self.accept)
        row.addWidget(self.close_button)
        root.addLayout(row)
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        if area is not None and not pixmap.isNull():
            width = min(max(520, pixmap.width() + 60), int(area.width() * 0.8))
            height = min(max(420, pixmap.height() + 90), int(area.height() * 0.8))
            self.resize(width, height)
        else:
            self.resize(720, 560)

    def _save(self) -> None:
        from pathlib import Path

        target, _filter = QFileDialog.getSaveFileName(
            self, "保存图片", Path(self.path).name or "image.png",
            "图片 (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        if not target:
            return
        try:
            from shutil import copyfile

            copyfile(self.path, target)
            self.close_button.setText("已保存")
        except Exception as exc:  # noqa: BLE001
            self.close_button.setText("保存失败：%s" % exc)


def sender_address(raw: str) -> str:
    text = (raw or "").strip()
    if "<" in text and ">" in text:
        return text.split("<", 1)[1].split(">", 1)[0].strip()
    return text


class InboxRow(QWidget):
    """一封邮件：加粗标题 + 发件人。"""

    clicked = Signal(int)

    def __init__(self, message: Dict[str, Any], selected: bool = False,
                 show_account: bool = False, parent=None):
        super().__init__(parent)
        self.message = message
        self._selected = bool(selected)
        self._hover = False
        self.setFixedHeight(64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        colors = _palette()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)

        subject = str(message.get("subject") or "（无主题）")
        subject_label = ElidedLabel(subject)
        font = QFont(self.font())
        font.setPointSizeF(9.6)
        font.setWeight(QFont.Weight.DemiBold)
        subject_label.setFont(font)
        subject_label.setStyleSheet(
            f"color: {colors['TEXT']}; background: transparent;")
        layout.addWidget(subject_label)

        meta = QHBoxLayout()
        meta.setSpacing(6)
        from_label = ElidedLabel(sender_name(str(message.get("from") or "")))
        from_label.setObjectName("Small")
        meta.addWidget(from_label, 3)
        date_label = QLabel(str(message.get("date") or ""))
        date_label.setObjectName("Tiny")
        meta.addWidget(date_label)
        layout.addLayout(meta)
        # 这里**不**设 tooltip：列表行已经把主题 / 发件人 / 时间都写出来了，
        # 再弹一个提示纯属多余（用户：「没有悬浮设置就别显示东西，别拿黑框充数」）。

    def set_selected(self, value: bool) -> None:
        if self._selected != bool(value):
            self._selected = bool(value)
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
            self.clicked.emit(int(self.message.get("_index") or 0))
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        colors = _palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded_path(rect, 10)
        if self._selected:
            painter.fillPath(path, theme.appearance().accent_q(0.12))
            painter.setPen(QPen(theme.appearance().accent_q(0.42), 1.0))
        else:
            painter.fillPath(path, QColor(colors["CARD_ALT"]))
            if self._hover:
                painter.fillPath(path, theme.hover_wash(0.06))
            painter.setPen(QPen(QColor(colors["BORDER"]), 1.0))
        painter.drawPath(path)


class _FetchWorker(QObject):
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, store, limit: int = 12):
        super().__init__()
        self.store = store
        self.limit = limit

    def run(self) -> None:
        try:
            # 只联网取数据，不写库（写库交给 GUI 线程，免得抢 SQLite 连接）
            self.done.emit(imap_inbox.fetch_all(self.store, self.limit))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _BodyWorker(QObject):
    """在后台取正文（含 HTML / 内联图片 / 附件），避免网慢时把界面卡住。"""

    done = Signal(dict)

    def __init__(self, store, account: Dict[str, Any], uid: str):
        super().__init__()
        self.store = store
        self.account = account
        self.uid = uid

    def run(self) -> None:
        account_id = int(self.account.get("id") or 0)
        try:
            detail = imap_inbox.fetch_body(self.store, self.account, self.uid)
            self.done.emit({
                "account_id": account_id,
                "uid": self.uid,
                "body": str(detail.get("body") or ""),
                "error": "",
                "links": list(detail.get("links") or []),
                "subject": str(detail.get("subject") or ""),
                "html": str(detail.get("html") or ""),
                "media_dir": str(detail.get("media_dir") or ""),
                "attachments": list(detail.get("attachments") or []),
            })
        except Exception as exc:  # noqa: BLE001
            self.done.emit({"account_id": account_id, "uid": self.uid,
                            "body": "", "error": str(exc), "links": [],
                            "subject": "", "html": "", "media_dir": "",
                            "attachments": []})


class InboxPanel(QWidget):
    """左边多账户邮件列表，右边正文。"""

    loaded = Signal(int)
    bounced = Signal(list)      # 退信识别结果：[{"mail_id","reason","code"}]
    request_settings = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._messages: List[Dict[str, Any]] = []
        self._rows: List[InboxRow] = []
        self._selected = -1
        self._thread: Optional[QThread] = None
        self._worker: Optional[_FetchWorker] = None
        self._body_thread: Optional[QThread] = None
        self._body_worker: Optional[_BodyWorker] = None
        self._pending_body: Optional[tuple] = None
        self._dirty_bodies: set = set()      # 缓存正文是脏的（CSS）：只重取一次
        self._loaded_once = False
        self._last_fetch = 0.0
        self._fetching = False
        self._fetch_started = 0.0
        self._fetch_wait = 0
        # 收信时每秒更新一次状态栏（让用户看到「正在连接…已等 3 秒」，而不是干等）
        self._fetch_timer = QTimer(self)
        self._fetch_timer.setInterval(1000)
        self._fetch_timer.timeout.connect(self._tick_fetch)
        self.summary = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(14)

        list_card = QWidget()
        list_card.setMinimumWidth(300)
        list_card.setMaximumWidth(430)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(4)
        self.list_scroll, _body, self.list_layout = make_scroll()
        list_layout.addWidget(self.list_scroll, 1)
        self.list_hint = QLabel("")
        self.list_hint.setObjectName("Tiny")
        self.list_hint.setWordWrap(True)
        list_layout.addWidget(self.list_hint)
        body.addWidget(list_card)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        # 双击正文里的图片 → 在独立窗口里看大图
        self.view.viewport().installEventFilter(self)
        colors = _palette()
        self.view.setStyleSheet(
            f"QTextBrowser {{ background: {colors['CARD']}; color: {colors['TEXT']};"
            f" border: 1px solid {colors['BORDER']}; border-radius: 14px; padding: 14px; }}")
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self.view, 1)
        # 附件条：有附件才显示（文件名 + 大小 + 保存）
        self.attachment_bar = QWidget()
        self.attachment_layout = QHBoxLayout(self.attachment_bar)
        self.attachment_layout.setContentsMargins(2, 0, 2, 0)
        self.attachment_layout.setSpacing(6)
        self.attachment_bar.setVisible(False)
        right_layout.addWidget(self.attachment_bar)
        body.addWidget(right, 1)
        root.addLayout(body, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        self.settings_button = SoftButton("去设置 IMAP 账户", "ghost", height=28,
                                          min_width=132, point_size=8.6)
        self.settings_button.clicked.connect(self.request_settings)
        self.settings_button.setVisible(False)
        bottom.addWidget(self.settings_button)
        root.addLayout(bottom)

        self._show_placeholder()
        self._load_cached()

    # -------------------------------------------------- 占位
    def _show_placeholder(self) -> None:
        colors = _palette()
        self.view.setHtml(
            "<div style='color:%s; font-size:13px; line-height:1.7'>"
            "<p><b>收件箱</b></p>"
            "<p>这里汇总所有 IMAP 账户的邮件，收到的邮件会保存在本地。"
            "选左边一封就能读正文；正文读过一次也会存下来。</p>"
            "<p>还没配置账户？到 设置 → 收件箱（IMAP）里填服务器和授权码。</p>"
            "</div>" % colors["MUTED"])

    # -------------------------------------------------- 列表
    def _load_cached(self) -> None:
        """先把本地存过的邮件显示出来，不用等网络。"""
        try:
            cached = imap_inbox.cached_messages(self.store, MAX_MESSAGES)
        except Exception:
            cached = []
        if cached:
            self._set_messages(cached, from_cache=True)
            self.status.setText("显示本地保存的 %d 封（点「刷新」收新的）" % len(cached))

    def _set_messages(self, messages: List[Dict[str, Any]],
                      from_cache: bool = False) -> None:
        self._messages = list(messages)
        accounts = 0
        try:
            accounts = len(imap_inbox.list_accounts(self.store))
        except Exception:
            accounts = 0
        first_id = None
        for index, message in enumerate(self._messages):
            message["_index"] = index
            if first_id is None:
                first_id = index
        for index, row in enumerate(self._rows):
            row.deleteLater()
        self._rows = []
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for index, message in enumerate(self._messages):
            row = InboxRow(message, selected=index == 0, show_account=accounts > 1)
            row.clicked.connect(self._select)
            self.list_layout.addWidget(row)
            self._rows.append(row)
        self.list_layout.addStretch(1)
        self._selected = first_id if first_id is not None else -1
        if self._selected >= 0:
            self._show_message(self._selected)
        elif not from_cache:
            self.view.setHtml(
                "<p style='color:%s'>收件箱是空的。</p>" % _palette()["MUTED"])

    def _select(self, index: int) -> None:
        if index == self._selected or index < 0 or index >= len(self._messages):
            return
        self._selected = index
        for position, row in enumerate(self._rows):
            row.set_selected(position == index)
        self._show_message(index)

    # -------------------------------------------------- 拉取
    def is_loaded(self) -> bool:
        return bool(self._loaded_once)

    def _busy(self) -> bool:
        thread = self._thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            self._thread = None
            return False

    def refresh(self, force: bool = False) -> None:
        """先显示本地已收的邮件，再去收新的。

        `force=False` 时有一个冷却时间（默认 90 秒），避免来回切标签页反复连 IMAP；
        「刷新」按钮用 `force=True` 强制收一次。
        """
        if self._busy():
            return
        # 第一步永远是把本地保存的邮件先摆出来，不等网络
        if not self._messages:
            self._load_cached()
        if not imap_inbox.list_accounts(self.store):
            self.summary = "还没配置 IMAP 账户 · 到设置里添加"
            self.settings_button.setVisible(True)
            if self._messages:
                # 有本地存过的邮件就继续显示，别把列表清空
                self.status.setText("还没有配置 IMAP 账户 · 下面是本地保存的邮件")
                self.loaded.emit(len(self._messages))
            else:
                self.status.setText("还没有配置 IMAP 账户")
                self._show_placeholder()
                self.loaded.emit(0)
            return
        self.settings_button.setVisible(False)
        if not force and (time.time() - self._last_fetch) < FETCH_COOLDOWN:
            # 刚收过：只把本地内容显示好，不再连服务器
            if not self._messages:
                self.status.setText("已显示本地保存的邮件（刚收过，暂无新的）")
            return
        if self._messages:
            self.status.setText(
                f"已显示本地保存的 {len(self._messages)} 封，正在收取新邮件…")
        else:
            self.status.setText("正在收取…")
        self._fetching = True
        self._fetch_started = time.time()
        self._fetch_wait = 0
        try:
            self._fetch_timer.start(1000)
        except Exception:
            pass
        worker = _FetchWorker(self.store, 8)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None

    def _tick_fetch(self) -> None:
        """收信进度：每秒刷一次「已等 N 秒」（学邮服务器登录要 3~5 秒，别让人以为卡死）。"""
        if not self._fetching:
            try:
                self._fetch_timer.stop()
            except Exception:
                pass
            return
        self._fetch_wait += 1
        try:
            self.status.setText(
                f"正在连学邮收信…已等 {self._fetch_wait} 秒"
                + ("（服务器有点慢，先看本地已保存的邮件）" if self._fetch_wait >= 4 else ""))
        except Exception:
            pass

    def _stop_fetch_timer(self) -> None:
        try:
            self._fetch_timer.stop()
        except Exception:
            pass
        self._fetch_wait = 0

    def _on_loaded(self, messages: List[Dict[str, Any]]) -> None:
        self._loaded_once = True
        self._fetching = False
        self._stop_fetch_timer()
        self._last_fetch = time.time()
        errors = [item["error"] for item in messages if item.get("error")]
        fresh = [item for item in messages if not item.get("error")]
        if fresh:
            # 写库放在 GUI 线程做（worker 只联网）
            try:
                imap_inbox.save_messages(self.store, fresh)
            except Exception:
                pass
        accounts = len(imap_inbox.list_accounts(self.store))
        cached = []
        try:
            # 老版本把 CSS 当正文存了下来（预览里出现 .eml-w { … }），先清掉，
            # 这样点开时会重新联网取一次、按新规则清洗
            imap_inbox.purge_dirty_bodies(self.store)
            # 老缓存里没有「链接对照表」的，也清一次（让「点击这里」变成能点的）
            imap_inbox.purge_unlinked_bodies(self.store)
            cached = imap_inbox.cached_messages(self.store, MAX_MESSAGES)
        except Exception:
            cached = []
        # 退信也可能早就收进本地库了（那会儿还没做退信识别），
        # 所以新收的 + 本地缓存一起扫一遍，重复扫描不会重复标记。
        marked: List[Dict[str, Any]] = []
        rows = list(fresh) + list(cached)
        if rows:
            try:
                marked = bounce.sync(self.store, rows)
            except Exception:
                marked = []
        self.summary = f"共 {len(cached or fresh)} 封 · {accounts} 个账户"
        if cached:
            self._set_messages(cached)
        elif fresh:
            self._set_messages(fresh)
        stamp = ""
        if cached and cached[0].get("fetched_at"):
            stamp = str(cached[0]["fetched_at"]).replace("T", " ")[:16]
        if errors:
            note = "；".join(errors)
        elif stamp:
            note = (f"已收 {len(fresh)} 封新邮件（{stamp}）· "
                    f"本地已保存 {len(cached)} 封")
        else:
            note = "已刷新"
        if marked:
            reasons = []
            for item in marked:
                text = str(item.get("reason") or "对方退信")
                if text not in reasons:
                    reasons.append(text)
            note += f" · 有 {len(marked)} 封邮件被退信：{'；'.join(reasons)}"
        self.status.setText(note)
        self.loaded.emit(len(cached or fresh))
        if marked:
            self.bounced.emit(marked)

    def _on_failed(self, message: str) -> None:
        self._fetching = False
        self._stop_fetch_timer()
        self.status.setText(f"收取失败（先看本地保存的邮件）：{message}")

    # -------------------------------------------------- 阅读
    def _body_busy(self) -> bool:
        thread = self._body_thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            self._body_thread = None
            return False

    def _show_message(self, index: int) -> None:
        if index < 0 or index >= len(self._messages):
            return
        message = self._messages[index]
        colors = _palette()
        header = (f"<p style='color:{colors['MUTED']};font-size:11px'>"
                  f"{sender_name(str(message.get('from') or ''))} · "
                  f"{message.get('date','')} · "
                  f"{message.get('account_name','')}</p>"
                  f"<h3 style='color:{colors['TEXT']}'>{message.get('subject','')}</h3>")
        account_id = int(message.get("account_id") or 0)
        uid = str(message.get("uid") or "")
        body = ""
        try:
            body = imap_inbox.cached_body(self.store, account_id, uid)
        except Exception:
            body = ""
        # 缓存里如果还是「CSS 当正文」的老数据，忽略它，重新联网取一次
        if body and imap_inbox.looks_like_raw_css(body):
            key = (account_id, uid)
            if key not in self._dirty_bodies:
                self._dirty_bodies.add(key)
                body = ""
        # 富正文（HTML + 内联图片 + 附件）优先：读过一次就缓存在本地
        rich = {"html": "", "dir": "", "attachments": []}
        try:
            rich = imap_inbox.cached_rich(account_id, uid)
        except Exception:
            rich = {"html": "", "dir": "", "attachments": []}
        if rich.get("html"):
            self._set_rich_body(header, rich["html"], rich.get("dir") or "",
                                rich.get("attachments") or [])
            return
        if body:
            try:
                links = imap_inbox.cached_links(self.store, account_id, uid)
            except Exception:
                links = []
            self._set_body_html(header, body, links)
            self._render_attachments(rich.get("attachments") or [])
            # 老缓存里只有纯文本（那时候还没做图片/附件）：后台补取一次 HTML，
            # 否则这种邮件永远是一片灰字、图片位置空着（用户报的就是这个）
            if not rich.get("tried"):
                self._start_body(account_id, uid)
            return
        snippet = str(message.get("snippet") or "").replace("<", "&lt;")
        # 注册申请的摘要是那串密文，别在「正在读取正文…」里先糊一遍
        if adminsign.looks_like_seal(snippet) or \
                "LUMINA-SEAL1" in str(message.get("snippet") or ""):
            snippet = "（加密的注册申请，点开查看）"
        self.view.setHtml(
            header + f"<p style='color:{colors['TEXT_SOFT']}'>{snippet}</p>"
                     f"<p style='color:{colors['MUTED']};font-size:11px'>"
                     f"正在读取正文…</p>")
        self._start_body(account_id, uid)

    def _start_body(self, account_id: int, uid: str) -> None:
        """后台取一封邮件的正文（HTML / 图片 / 附件）。"""
        if not uid or self._body_busy():
            return
        account = None
        for item in imap_inbox.list_accounts(self.store):
            if int(item["id"]) == account_id:
                account = item
                break
        if account is None:
            return
        self._pending_body = (account_id, uid)
        worker = _BodyWorker(self.store, account, uid)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_body)
        worker.done.connect(thread.quit)
        thread.finished.connect(self._on_body_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._body_thread = thread
        self._body_worker = worker
        thread.start()

    def _on_body_thread_finished(self) -> None:
        self._body_thread = None
        self._body_worker = None
        # 取正文的时候用户点了别的邮件：等这次回来再补上
        target = self._pending_body
        self._pending_body = None
        if target is None or self._selected < 0:
            return
        message = self._messages[self._selected] if self._selected < len(self._messages) else None
        if message is None:
            return
        want = (int(message.get("account_id") or 0), str(message.get("uid") or ""))
        if want == target:
            return          # 这封已经显示过了
        try:
            body = imap_inbox.cached_body(self.store, want[0], want[1])
        except Exception:
            body = ""
        if not body:
            self._show_message(self._selected)

    def _set_rich_body(self, header: str, html: str, media_dir: str = "",
                       attachments: Optional[List[Dict[str, Any]]] = None) -> None:
        """按邮件原本的 HTML 渲染（表格、颜色、内联图片），下面列出附件。"""
        from . import mail_html

        clean = mail_html.sanitize(html)
        if media_dir:
            try:
                self.view.setSearchPaths([str(media_dir)])
            except Exception:
                pass
        if "<body" in clean.lower():
            # 把「发件人 / 时间 / 主题」塞进邮件 body 的开头
            clean = re.sub(r"<body([^>]*)>", r"<body\1>" + header, clean,
                           count=1, flags=re.I)
        else:
            clean = header + clean
        self.view.setHtml(clean)
        self._render_attachments(attachments or [])

    def _render_attachments(self, attachments: Optional[List[Dict[str, Any]]]) -> None:
        """正文下面的附件：一张张小卡（类型图标 + 文件名 + 大小），点一下就保存。

        以前是「附件 + 一长串按钮」，文件名还是 Content-ID 那种乱码；用户要的是
        像邮箱客户端那样的卡片（见截图三）。
        """
        while self.attachment_layout.count():
            item = self.attachment_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        items = [item for item in (attachments or [])
                 if isinstance(item, dict) and (item.get("name") or item.get("path"))]
        self.attachment_bar.setVisible(bool(items))
        if not items:
            return
        for item in items:
            chip = _AttachmentChip(
                item, lambda source=str(item.get("path") or ""),
                title=str(item.get("name") or "附件"):
                self._save_attachment(source, title))
            self.attachment_layout.addWidget(chip)
        self.attachment_layout.addStretch(1)

    # -------------------------------------------------- 双击看大图
    def eventFilter(self, watched, event):
        """正文里双击图片 → 弹独立窗口（滚动看、还能另存）。"""
        try:
            from PySide6.QtCore import QEvent

            if watched is self.view.viewport() \
                    and event.type() == QEvent.Type.MouseButtonDblClick:
                path = self.image_path_at(event.position().toPoint())
                if path:
                    self.open_image_window(path)
                    return True
        except Exception:
            pass
        return super().eventFilter(watched, event)

    def image_path_at(self, pos) -> str:
        """这个位置上是图片吗？是的话返回本地文件路径。"""
        from pathlib import Path

        try:
            cursor = self.view.cursorForPosition(pos)
            fmt = cursor.charFormat()
            if not fmt.isImageFormat():
                return ""
            name = str(fmt.toImageFormat().name() or "")
        except Exception:
            return ""
        if not name:
            return ""
        candidates = []
        try:
            for folder in self.view.searchPaths() or []:
                candidates.append(Path(folder) / name)
        except Exception:
            pass
        candidates.append(Path(name))
        for path in candidates:
            try:
                if path.exists():
                    return str(path)
            except Exception:
                continue
        return ""

    def open_image_window(self, path: str) -> None:
        dialog = MailImageDialog(path, "邮件图片", self)
        dialog.exec()

    def _save_attachment(self, source: str, name: str) -> None:
        if not source:
            self.status.setText("这个附件没缓存下来，重新打开这封邮件试试")
            return
        try:
            saved = imap_inbox.save_attachment(source, name)
        except Exception as exc:  # noqa: BLE001
            self.status.setText("保存失败：%s" % str(exc)[:60])
            return
        self.status.setText("已保存：%s" % saved)

    def _set_body_html(self, header: str, body: str,
                       links: Optional[List[Dict[str, str]]] = None) -> None:
        colors = _palette()
        # 注册申请邮件：正文是一大坨 LUMINA-SEAL1.… 密文，别直接糊给用户
        seal = self._seal_preview(body)
        if seal is not None:
            self.view.setHtml(header + seal)
            return
        # 正文是纯文本；用 body_html 把「点击这里」这类链接文字和裸露网址变成可点的 <a>
        html_body = imap_inbox.body_html(body, links)
        self.view.setHtml(
            header + f"<p style='color:{colors['TEXT_SOFT']}'>{html_body}</p>")

    def _seal_preview(self, body: str) -> Optional[str]:
        """把「加密的注册申请」翻成人话；不是注册申请就返回 None。

        * 管理员自己这台机器能解开 → 列出申请账户 / 邮箱 / 设备 / 时间，
          并告诉管理员去哪儿批准；
        * 解开不了（客户端版 / 别人的公钥）→ 说明只有管理员能解开，别显示密文。
        """
        if not body or not adminsign.looks_like_seal(body):
            return None
        colors = _palette()
        try:
            from . import registration

            info = registration.seal_preview(self.store, body)
        except Exception:
            info = {"is_seal": True, "ok": False}
        title = ("这是一封 DanLume 注册申请（已在这台机器上解开）："
                 if info.get("ok") else
                 "这是一封 DanLume 注册申请：")
        rows = ""
        if info.get("ok"):
            for label, value in (("申请账户", info.get("username")),
                                 ("绑定邮箱", info.get("email")),
                                 ("设备指纹", info.get("device")),
                                 ("申请时间", info.get("time"))):
                if not value:
                    continue
                style = (f"color:{colors['TEXT_SOFT']};font-size:12px;"
                         f"padding:2px 0")
                rows += (f"<tr><td style='color:{colors['MUTED']};font-size:12px;"
                         f"padding:2px 12px 2px 0'>{label}</td>"
                         f"<td style='{style}'>{value}</td></tr>")
            hint = ("在「账户与登录 → 用户审核」里点「检查新申请」，"
                    "就能批准或拒绝这条申请。")
        else:
            hint = ("申请内容已加密，只有管理员机器的私钥能解开。"
                    "请在「账户与登录 → 用户审核」里点「检查新申请」处理，"
                    "这里不需要（也不会）显示密文。")
        return (
            f"<p style='color:{colors['TEXT']};font-size:13px'>{title}</p>"
            + (f"<table cellspacing='0' cellpadding='0'>{rows}</table>" if rows else "")
            + f"<p style='color:{colors['TEXT_SOFT']};font-size:12px'>{hint}</p>")

    def _on_body(self, result: Dict[str, Any]) -> None:
        result = result if isinstance(result, dict) else {}
        account_id = int(result.get("account_id") or 0)
        uid = str(result.get("uid") or "")
        body = str(result.get("body") or "")
        error = str(result.get("error") or "")
        links = list(result.get("links") or [])
        subject = str(result.get("subject") or "")
        html = str(result.get("html") or "")
        media_dir = str(result.get("media_dir") or "")
        attachments = list(result.get("attachments") or [])
        if error:
            # 正文已经显示出来了、只是顺手补取 HTML 失败（比如断网）：别红字吓人
            shown = ""
            try:
                shown = imap_inbox.cached_body(self.store, account_id, uid)
            except Exception:
                shown = ""
            if not shown:
                self.status.setText(f"正文读取失败：{error}")
            return
        if not body and not html:
            self.status.setText("这封邮件没有正文内容")
            return
        if self._selected < 0 or self._selected >= len(self._messages):
            return
        message = self._messages[self._selected]
        if int(message.get("account_id") or 0) != account_id \
                or str(message.get("uid") or "") != uid:
            return                      # 用户已经换了一封，别把正文贴错
        # 再核对一次主题：万一服务器给的正文不是这一封（以前用序号当 uid 时
        # 就会串），宁可不显示，也不要「选中的邮件和正文不匹配」
        if subject and str(message.get("subject") or "") \
                and subject.strip() != str(message.get("subject") or "").strip():
            self.status.setText("这封邮件的正文对不上（可能刚有新邮件到达），"
                                "再点一次这封邮件试试")
            return
        try:
            imap_inbox.save_body(self.store, account_id, uid, body)
            # 存一份链接对照表：下次直接显示可点的链接，不用再联网
            imap_inbox.save_links(self.store, account_id, uid, list(links or []))
        except Exception:
            pass
        colors = _palette()
        header = (f"<p style='color:{colors['MUTED']};font-size:11px'>"
                  f"{sender_name(str(message.get('from') or ''))} · "
                  f"{message.get('date','')} · "
                  f"{message.get('account_name','')}</p>"
                  f"<h3 style='color:{colors['TEXT']}'>{message.get('subject','')}</h3>")
        # 有 HTML 正文就按邮件原本的排版渲染（图片 / 表格 / 颜色都在）
        if html:
            self._set_rich_body(header, html, media_dir, attachments)
            return
        self._set_body_html(header, body, links)
        self._render_attachments(attachments)
        # 这封就是纯文本：记一笔，下次打开别再联网补取 HTML
        try:
            imap_inbox.mark_no_html(account_id, uid)
        except Exception:
            pass
