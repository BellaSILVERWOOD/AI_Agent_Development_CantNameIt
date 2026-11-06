# -*- coding: utf-8 -*-
"""流程图预览对话框：看大图、复制步骤、另存为图片、重新生成。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import BaseDialog, SoftButton


class FlowDialog(BaseDialog):
    """把生成的流程图显示出来（图片可滚动，按钮可保存 / 复制步骤）。"""

    def __init__(self, store, result: Dict[str, Any], parent=None):
        super().__init__(parent, width=760, height=680, title="方法流程图")
        self.store = store
        self.result = dict(result or {})
        body = QVBoxLayout()
        body.setSpacing(8)

        title = QLabel(str(self.result.get("title") or "方法流程图"))
        title.setObjectName("H3")
        body.addWidget(title)
        steps = [str(step) for step in (self.result.get("steps") or [])]
        if steps:
            hint = QLabel("共 %d 步：%s" % (len(steps), " → ".join(steps[:4])
                                          + ("…" if len(steps) > 4 else "")))
            hint.setObjectName("Tiny")
            hint.setWordWrap(True)
            body.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignHCenter
                                     | Qt.AlignmentFlag.AlignTop)
        self.image_label.setStyleSheet(
            f"QLabel {{ background: {theme.CARD_ALT}; border-radius: 10px; }}")
        path = str(self.result.get("path") or "")
        pixmap = QPixmap(path) if path and Path(path).exists() else QPixmap()
        if pixmap.isNull():
            self.image_label.setText("（没有图片：%s）"
                                     % (self.result.get("error") or "未知原因"))
        else:
            self.image_label.setPixmap(pixmap)
        holder_layout.addWidget(self.image_label)
        scroll.setWidget(holder)
        body.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.save_button = SoftButton("另存为图片", "primary", height=34,
                                      min_width=112)
        self.save_button.setEnabled(not pixmap.isNull())
        self.save_button.clicked.connect(self._save_image)
        buttons.addWidget(self.save_button)
        self.copy_button = SoftButton("复制步骤", "ghost", height=34, min_width=96)
        self.copy_button.setEnabled(bool(steps))
        self.copy_button.clicked.connect(self._copy_steps)
        buttons.addWidget(self.copy_button)
        self.regenerate_button = SoftButton("重新生成", "ghost", height=34,
                                            min_width=100)
        self.regenerate_button.setToolTip("忽略缓存，让模型再拆一次")
        buttons.addWidget(self.regenerate_button)
        buttons.addStretch(1)
        self.close_button = SoftButton("关闭", "ghost", height=34, min_width=80)
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        body.addLayout(buttons)

        self.body.addLayout(body)

    # -------------------------------------------------- 动作
    def _save_image(self) -> None:
        path = str(self.result.get("path") or "")
        if not path or not Path(path).exists():
            return
        target, _filter = QFileDialog.getSaveFileName(
            self, "保存流程图", Path(path).name, "PNG 图片 (*.png)")
        if not target:
            return
        try:
            from shutil import copyfile

            copyfile(path, target)
            self.close_button.setText("已保存")
        except Exception as exc:  # noqa: BLE001
            self.close_button.setText("保存失败：%s" % exc)

    def _copy_steps(self) -> None:
        steps = [str(step) for step in (self.result.get("steps") or [])]
        if not steps:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n".join("%d. %s" % (index, step)
                                        for index, step in enumerate(steps, 1)))
        self.copy_button.setText("已复制")
