"""邮箱页：一个页面里放「收件箱 / 发件箱」，用顶部两个按钮切换。

* 收件箱：`InboxPanel`（多 IMAP 账户合并列表 + 正文预览，点刷新才联网）；
* 发件箱：`OutboxPage(embedded=True)`（汇报邮件列表 + 预览 + 写新邮件）；
* 顶部只放一套：标签按钮、搜索（只作用于发件箱）、刷新、新邮件。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget,
)

from . import icons
from .inbox_panel import InboxPanel
from .page_outbox import OutboxPage
from .widgets import ElidedLabel, SearchBox, SoftButton

TAB_INBOX = 0
TAB_OUTBOX = 1


class MailboxPage(QWidget):
    compose_requested = Signal(object)
    request_settings = Signal()
    data_changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._tab = TAB_INBOX
        self._shown_once = False

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 18, 26, 20)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("邮箱")
        title.setObjectName("H1")
        self.subtitle = ElidedLabel("收件箱与发件箱在同一个页面里，点上面的按钮切换")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setMinimumWidth(180)
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addSpacing(14)

        self.inbox_tab = SoftButton("收件箱", "primary", glyph=icons.INBOX,
                                    height=36, min_width=104, point_size=8.8)
        self.inbox_tab.setToolTip("看别人发来的邮件（IMAP 多账户合并）")
        self.inbox_tab.clicked.connect(lambda: self.set_tab(TAB_INBOX))
        header.addWidget(self.inbox_tab)

        self.outbox_tab = SoftButton("发件箱", "ghost", glyph=icons.SEND,
                                     height=36, min_width=104, point_size=8.8)
        self.outbox_tab.setToolTip("看自己发过的汇报邮件、草稿和失败记录")
        self.outbox_tab.clicked.connect(lambda: self.set_tab(TAB_OUTBOX))
        header.addWidget(self.outbox_tab)

        header.addStretch(1)

        self.search = SearchBox("搜索主题或收件人")
        self.search.setFixedWidth(196)
        self.search.textChanged.connect(self._on_search)
        self.search.setVisible(False)
        header.addWidget(self.search)

        self.refresh_button = SoftButton("刷新", "ghost", height=36,
                                         glyph=icons.REFRESH, min_width=92,
                                         point_size=8.8)
        self.refresh_button.clicked.connect(self.refresh_current)
        header.addWidget(self.refresh_button)

        self.new_mail_button = SoftButton("新邮件", "primary", glyph=icons.MAIL,
                                          height=36, min_width=118)
        self.new_mail_button.clicked.connect(
            lambda: self.compose_requested.emit(None))
        header.addWidget(self.new_mail_button)
        root.addLayout(header)

        self.stack = QStackedWidget()
        self.inbox = InboxPanel(store, self)
        self.inbox.loaded.connect(self._on_inbox_loaded)
        self.inbox.request_settings.connect(self.request_settings)
        self.stack.addWidget(self.inbox)

        self.outbox = OutboxPage(store, embedded=True)
        self.outbox.compose_requested.connect(self.compose_requested)
        self.outbox.request_settings.connect(self.request_settings)
        self.outbox.data_changed.connect(self._on_outbox_changed)
        self.stack.addWidget(self.outbox)
        root.addWidget(self.stack, 1)

        self.set_tab(TAB_INBOX, refresh=False)
        self._update_tab_counts()

    # -------------------------------------------------- 首次显示
    def showEvent(self, event):
        super().showEvent(event)
        # 第一次进「邮箱」就把收件箱收一遍（之后靠刷新按钮，避免反复联网）
        if not self._shown_once:
            self._shown_once = True
            if self._tab == TAB_INBOX:
                self.inbox.refresh()

    # -------------------------------------------------- 标签
    def current_tab(self) -> int:
        return self._tab

    def set_tab(self, index: int, refresh: bool = True) -> None:
        index = TAB_OUTBOX if int(index) == TAB_OUTBOX else TAB_INBOX
        self._tab = index
        self.stack.setCurrentIndex(index)
        inbox = index == TAB_INBOX
        self.inbox_tab.setKind("primary" if inbox else "ghost")
        self.outbox_tab.setKind("ghost" if inbox else "primary")
        self.search.setVisible(not inbox)
        self.refresh_button.setToolTip(
            "重新收一次收件箱（IMAP）" if inbox else "重新读取发件箱记录")
        self.new_mail_button.setVisible(not inbox)
        if inbox:
            self.subtitle.setText(
                self.inbox.summary or "收件箱与发件箱在同一个页面里，点上面的按钮切换")
            if refresh:
                self.inbox.refresh()
        else:
            self.outbox.refresh()
            self.subtitle.setText(self.outbox.subtitle.text())
        self._update_tab_counts()

    def show_inbox(self) -> None:
        self.set_tab(TAB_INBOX)

    def show_outbox(self) -> None:
        self.set_tab(TAB_OUTBOX)

    # -------------------------------------------------- 刷新
    def refresh(self) -> None:
        """主窗口定时刷新用：发件箱总是刷（本地库），收件箱先显示本地再收新（有冷却）。"""
        self.outbox.refresh()
        if self._tab == TAB_OUTBOX:
            self.subtitle.setText(self.outbox.subtitle.text())
        else:
            # 这里不带 force：先用本地缓存，超过冷却时间才真的连 IMAP
            self.inbox.refresh()
            if self.inbox.summary:
                self.subtitle.setText(self.inbox.summary)

    def refresh_current(self) -> None:
        if self._tab == TAB_INBOX:
            self.inbox.refresh(force=True)
        else:
            self.outbox.refresh()

    def _on_search(self, text: str) -> None:
        self.outbox.set_query(text)

    def _on_inbox_loaded(self, count: int) -> None:
        if self._tab == TAB_INBOX:
            self.subtitle.setText(self.inbox.summary or f"共 {count} 封")
        self.inbox_tab.setText(f"收件箱 {count}" if count else "收件箱")
        self._update_tab_counts()

    def _on_outbox_changed(self) -> None:
        self.data_changed.emit()

    def _update_tab_counts(self) -> None:
        drafts = 0
        try:
            drafts = int(self.store.mail_counts().get("draft", 0) or 0)
        except Exception:
            drafts = 0
        self.outbox_tab.setText(f"发件箱 {drafts}" if drafts else "发件箱")

    # -------------------------------------------------- 兼容旧入口
    def select_mail(self, mail_id: int) -> None:
        """发信 / 存草稿后跳到发件箱并定位到那封邮件。"""
        self.set_tab(TAB_OUTBOX, refresh=False)
        self.outbox.select_mail(int(mail_id))
        self.subtitle.setText(self.outbox.subtitle.text())

    @property
    def mailbox_subtitle(self) -> str:
        return self.subtitle.text()
