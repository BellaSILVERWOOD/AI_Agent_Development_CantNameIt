"""收件箱面板：多账户 IMAP 邮件列表 + 正文预览。

* 列表：标题加粗，下面一行是「发件人 · 时间」（多账户时再带账户名）；
* 本地保存：收到的邮件头写进 `inbox_messages`，打开页面先显示本地缓存，
  再后台刷新；正文读过一次也存下来，下次不再联网；
* 没配账户时整块提示去设置。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout, QWidget,
)

from . import imap_inbox, theme
from .widgets import (
    ElidedLabel, SoftButton, make_scroll, rounded_path,
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
        self.setToolTip(
            f"{subject}\n发件人：{str(message.get('from') or '')}"
            + (f"\n账户：{message.get('account_name')}" if show_account else ""))

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
    """在后台取正文，避免网慢时把界面卡住。"""

    done = Signal(int, str, str, str)     # account_id, uid, body, error

    def __init__(self, store, account: Dict[str, Any], uid: str):
        super().__init__()
        self.store = store
        self.account = account
        self.uid = uid

    def run(self) -> None:
        account_id = int(self.account.get("id") or 0)
        try:
            detail = imap_inbox.fetch_body(self.store, self.account, self.uid)
            self.done.emit(account_id, self.uid, str(detail.get("body") or ""), "")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(account_id, self.uid, "", str(exc))


class InboxPanel(QWidget):
    """左边多账户邮件列表，右边正文。"""

    loaded = Signal(int)
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
        self._loaded_once = False
        self._last_fetch = 0.0
        self._fetching = False
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
        colors = _palette()
        self.view.setStyleSheet(
            f"QTextBrowser {{ background: {colors['CARD']}; color: {colors['TEXT']};"
            f" border: 1px solid {colors['BORDER']}; border-radius: 14px; padding: 14px; }}")
        body.addWidget(self.view, 1)
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
        worker = _FetchWorker(self.store, 12)
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

    def _on_loaded(self, messages: List[Dict[str, Any]]) -> None:
        self._loaded_once = True
        self._fetching = False
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
            cached = imap_inbox.cached_messages(self.store, MAX_MESSAGES)
        except Exception:
            cached = []
        if cached:
            self._set_messages(cached)
        elif fresh:
            self._set_messages(fresh)
        self.summary = f"共 {len(cached or fresh)} 封 · {accounts} 个账户"
        stamp = ""
        if cached and cached[0].get("fetched_at"):
            stamp = str(cached[0]["fetched_at"]).replace("T", " ")[:16]
        if errors:
            self.status.setText("；".join(errors))
        elif stamp:
            self.status.setText(
                f"已收 {len(fresh)} 封新邮件（{stamp}）· 本地已保存 {len(cached)} 封")
        else:
            self.status.setText("已刷新")
        self.loaded.emit(len(cached or fresh))

    def _on_failed(self, message: str) -> None:
        self._fetching = False
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
        if body:
            self._set_body_html(header, body)
            return
        snippet = str(message.get("snippet") or "").replace("<", "&lt;")
        self.view.setHtml(
            header + f"<p style='color:{colors['TEXT_SOFT']}'>{snippet}</p>"
                     f"<p style='color:{colors['MUTED']};font-size:11px'>"
                     f"正在读取正文…</p>")
        account = None
        for item in imap_inbox.list_accounts(self.store):
            if int(item["id"]) == account_id:
                account = item
                break
        if account is None or not uid:
            return
        if self._body_busy():
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

    def _set_body_html(self, header: str, body: str) -> None:
        colors = _palette()
        html_body = str(body or "").replace("<", "&lt;").replace("\n", "<br>")
        self.view.setHtml(
            header + f"<p style='color:{colors['TEXT_SOFT']}'>{html_body}</p>")

    def _on_body(self, account_id: int, uid: str, body: str, error: str) -> None:
        if error:
            self.status.setText(f"正文读取失败：{error}")
            return
        if not body:
            self.status.setText("这封邮件没有正文内容")
            return
        try:
            imap_inbox.save_body(self.store, account_id, uid, body)
        except Exception:
            pass
        if self._selected < 0 or self._selected >= len(self._messages):
            return
        message = self._messages[self._selected]
        if int(message.get("account_id") or 0) != account_id \
                or str(message.get("uid") or "") != uid:
            return                      # 用户已经换了一封，别把正文贴错
        colors = _palette()
        header = (f"<p style='color:{colors['MUTED']};font-size:11px'>"
                  f"{sender_name(str(message.get('from') or ''))} · "
                  f"{message.get('date','')} · "
                  f"{message.get('account_name','')}</p>"
                  f"<h3 style='color:{colors['TEXT']}'>{message.get('subject','')}</h3>")
        self._set_body_html(header, body)
