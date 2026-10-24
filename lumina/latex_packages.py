"""宏包选择对话框。"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout,
)

from . import latex_builder, theme
from .popup import RoundedPopup
from .widgets import SoftButton


class PackagesDialog(RoundedPopup):
    """勾选要加载的宏包（程序自动生成导言区）。"""

    def __init__(self, parent, selected: List[str]):
        super().__init__(parent, padding=16, radius=12)
        self._boxes = {}
        layout = self.content
        layout.setSpacing(10)

        title = QLabel("选择宏包")
        title.setObjectName("H3")
        layout.addWidget(title)
        hint = QLabel("勾选后会自动写进导言区，顺序由程序安排（hyperref 放最后）")
        hint.setObjectName("Tiny")
        layout.addWidget(hint)

        grid = QGridLayout()
        grid.setSpacing(6)
        for index, item in enumerate(latex_builder.PACKAGES):
            box = QCheckBox(str(item["name"]))
            box.setChecked(str(item["id"]) in selected)
            self._boxes[str(item["id"])] = box
            grid.addWidget(box, index // 2, index % 2)
        layout.addLayout(grid)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = SoftButton("取消", "ghost", height=34, min_width=88)
        cancel.clicked.connect(self.reject)
        ok = SoftButton("确定", "primary", height=34, min_width=96)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)

    def selected_ids(self) -> List[str]:
        return [key for key, box in self._boxes.items() if box.isChecked()]
