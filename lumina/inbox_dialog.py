"""收件箱窗口：把 `InboxPanel` 装进一个独立对话框（旧入口保留）。"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout

from .inbox_panel import InboxPanel
from .widgets import SoftButton


class InboxDialog(QDialog):
    """独立窗口版的收件箱；页面里用的是 `InboxPanel`。"""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Lumina · 收件箱")
        self.resize(920, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self.panel = InboxPanel(store, self)
        root.addWidget(self.panel, 1)

        head = QHBoxLayout()
        head.addStretch(1)
        self.refresh_button = SoftButton("刷新", "primary", height=32, min_width=88,
                                         point_size=8.8)
        self.refresh_button.clicked.connect(lambda: self.panel.refresh(force=True))
        head.addWidget(self.refresh_button)
        root.addLayout(head)

        self.panel.request_settings.connect(self.close)
        self.panel.loaded.connect(
            lambda count: self.refresh_button.setEnabled(True))

    def refresh(self) -> None:
        self.panel.refresh(force=True)
