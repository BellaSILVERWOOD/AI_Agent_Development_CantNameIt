"""历史会话窗口：继续 / 重命名 / 删除。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from . import chat_store, icons
from .widgets import BaseDialog, ConfirmDialog, SoftButton


class ChatHistoryDialog(BaseDialog):
    """选一段会话继续，或者改名 / 删除。

    关闭前如果点了「继续对话」，`selected_id` 就是那段会话的 id，
    调用方据此把内容装回面板。
    """

    def __init__(self, store, parent=None, current_session: Optional[int] = None):
        super().__init__(
            parent, width=560, height=460,
            title="历史会话",
            subtitle="这里保存着 API 对话的历史；可以继续、改名或删除",
            icon=icons.DOCUMENT,
        )
        self.store = store
        self.current_session = int(current_session or 0)
        self.selected_id: Optional[int] = None
        self._sessions: List[Dict[str, Any]] = []

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setStyleSheet("font-size: 12px;")
        self.list.itemDoubleClicked.connect(lambda _item: self._resume())
        layout.addWidget(self.list)
        holder = QWidget()
        holder.setLayout(layout)
        self.body.addWidget(holder, 1)

        self.hint = QLabel("")
        self.hint.setObjectName("Tiny")
        self.hint.setWordWrap(True)
        self.body.addWidget(self.hint)

        self.resume_button = SoftButton("继续对话", "primary", height=34,
                                        min_width=104, point_size=8.8)
        self.resume_button.clicked.connect(self._resume)
        self.rename_button = SoftButton("重命名", "ghost", height=34,
                                        min_width=88, point_size=8.8)
        self.rename_button.clicked.connect(self._rename)
        self.delete_button = SoftButton("删除", "danger", height=34,
                                        min_width=80, point_size=8.8)
        self.delete_button.clicked.connect(self._delete)
        close_button = SoftButton("关闭", "ghost", height=34, min_width=80,
                                  point_size=8.8)
        close_button.clicked.connect(self.reject)
        self.add_footer([self.delete_button],
                        [self.rename_button, self.resume_button, close_button])
        self.reload()

    # -------------------------------------------------- 列表
    def reload(self, keep: Optional[int] = None) -> None:
        keep_id = keep or self.selected_id or self.current_session or 0
        self._sessions = chat_store.list_sessions(self.store)
        self.list.clear()
        for item in self._sessions:
            mark = "　（当前）" if int(item["id"]) == self.current_session else ""
            row = QListWidgetItem(
                f"{item['title']}{mark}\n"
                f"    {item['updated_at']} · {item['count']} 轮 · "
                f"{item.get('model') or '（未知模型）'}")
            row.setData(Qt.ItemDataRole.UserRole, int(item["id"]))
            self.list.addItem(row)
            if int(item["id"]) == keep_id:
                self.list.setCurrentRow(self.list.count() - 1)
        if self._sessions:
            self.hint.setText(
                f"共 {len(self._sessions)} 段对话；双击或点「继续对话」接着聊。"
                "删除只删本机聊天记录，不影响日程 / 邮件。")
        else:
            self.hint.setText("还没有历史会话：在 AI 面板里聊过一次就会自动保存。")

    def selected(self) -> int:
        item = self.list.currentItem()
        if item is None:
            return 0
        return int(item.data(Qt.ItemDataRole.UserRole) or 0)

    # -------------------------------------------------- 操作
    def _resume(self) -> None:
        session_id = self.selected()
        if not session_id:
            self.hint.setText("先选一段对话")
            return
        self.selected_id = session_id
        self.accept()

    def _rename(self) -> None:
        session_id = self.selected()
        if not session_id:
            self.hint.setText("先选一段对话")
            return
        old = next((item["title"] for item in self._sessions
                    if int(item["id"]) == session_id), "")
        text, ok = QInputDialog.getText(self, "重命名会话", "新标题：", text=old)
        if not ok:
            return
        if chat_store.rename(self.store, session_id, text):
            self.selected_id = session_id
            self.reload(keep=session_id)
            self.hint.setText("已改名")
        else:
            self.hint.setText("标题不能为空")

    def _delete(self) -> None:
        session_id = self.selected()
        if not session_id:
            self.hint.setText("先选一段对话")
            return
        title = next((item["title"] for item in self._sessions
                      if int(item["id"]) == session_id), "")
        dialog = ConfirmDialog(
            self,
            title="删除这段会话",
            message=f"要删除「{title}」吗？\n\n只删本机的聊天记录，不影响日程 / 邮件。",
            confirm_text="删除",
            danger=True,
        )
        if dialog.exec() != ConfirmDialog.DialogCode.Accepted:
            return
        chat_store.delete(self.store, session_id)
        if self.selected_id == session_id:
            self.selected_id = None
        if self.current_session == session_id:
            self.current_session = 0
        self.reload()
        self.hint.setText("已删除")
