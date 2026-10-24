"""系统提示词页：就在这里改 AI 的人设与场景提示词（写进 data/prompts.json）。"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget,
)

from . import icons, prompts, theme
from .widgets import Select, SoftButton


def _colors() -> Dict[str, str]:
    mode = "dark" if theme.effective_mode() == "dark" else "light"
    return theme.PALETTES.get(mode) or theme.PALETTES["light"]


class PromptPanel(QWidget):
    """左选提示词、右边编辑，保存即生效（不用重启）。"""

    saved = Signal(str)

    def __init__(self, store, compact: bool = False, parent=None):
        super().__init__(parent)
        self.store = store
        self.compact = bool(compact)
        colors = _colors()

        root = QVBoxLayout(self)
        root.setContentsMargins(8 if self.compact else 16, 8 if self.compact else 12,
                                8 if self.compact else 16, 8 if self.compact else 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.select = Select()
        self.select.setFixedWidth(186 if self.compact else 220)
        self.select.currentIndexChanged.connect(self._on_select)
        head.addWidget(self.select)
        self.hint = QLabel("")
        self.hint.setObjectName("Tiny")
        head.addWidget(self.hint, 1)
        root.addLayout(head)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("在这里写提示词…")
        self.editor.setStyleSheet(
            f"QPlainTextEdit {{ background: {colors['CARD']}; color: {colors['TEXT']};"
            f" border: 1px solid {colors['BORDER']}; border-radius: 12px; padding: 10px; }}")
        root.addWidget(self.editor, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        row.addWidget(self.status, 1)
        self.reset_button = SoftButton("恢复默认", "ghost", height=30,
                                       min_width=88, point_size=8.6)
        self.reset_button.clicked.connect(self._reset)
        row.addWidget(self.reset_button)
        self.save_button = SoftButton("保存", "primary", glyph=icons.SAVE, height=30,
                                      min_width=88, point_size=8.6)
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.save_button)
        root.addLayout(row)

        self._keys: List[tuple] = []
        self._build_options()
        self.editor.textChanged.connect(self._mark_dirty)
        self._dirty = False

    # -------------------------------------------------- 选项
    def _build_options(self) -> None:
        self.select.blockSignals(True)
        self.select.clear()
        self._keys = []
        try:
            data = prompts.load()
        except Exception:
            data = {}
        systems = data.get("system") or {}
        labels = {
            "assistant": "通用助手（所有人设的基础）",
            "weekly_report": "周报",
            "homework": "作业 / 课件摘要",
            "latex": "公式 / LaTeX",
            "wechat_todo": "微信待办识别",
        }
        for key in systems:
            title = labels.get(key, key)
            self.select.addItem(f"系统提示词 · {title}", f"system:{key}")
            self._keys.append(("system", key, title))
        for key, item in (data.get("scenes") or {}).items():
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or key)
            self.select.addItem(f"场景模板 · {title}", f"scene:{key}")
            self._keys.append(("scene", key, title))
        self.select.blockSignals(False)
        if self.select.count():
            self.select.setCurrentIndex(0)
            self._load_current()

    def _current(self) -> Optional[tuple]:
        index = self.select.currentIndex()
        if 0 <= index < len(self._keys):
            return self._keys[index]
        return None

    def _on_select(self, _index: int) -> None:
        if self._dirty and not self._confirm_discard():
            return
        self._load_current()

    def _confirm_discard(self) -> bool:
        from .widgets import ConfirmDialog

        dialog = ConfirmDialog(self.window(), "放弃修改？",
                               "当前提示词还没保存，切换会丢掉这次改动。",
                               confirm_text="放弃修改")
        return bool(dialog.exec() and dialog.confirmed)

    def _load_current(self) -> None:
        current = self._current()
        if current is None:
            return
        kind, key, title = current
        try:
            text = (prompts.system(key) if kind == "system"
                    else (prompts.scene(key) or ""))
        except Exception:
            text = ""
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)
        self._dirty = False
        self.hint.setText(f"{title} · {len(text)} 字")
        self.status.setText("改完点「保存」立即生效")

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.status.setText("有未保存的修改")

    # -------------------------------------------------- 保存 / 重置
    def save(self) -> None:
        current = self._current()
        if current is None:
            return
        kind, key, title = current
        text = self.editor.toPlainText()
        payload = {"system": {key: text}} if kind == "system" else {
            "scenes": {key: {"title": title, "prompt": text, "enabled": True}}}
        if prompts.save(payload):
            self._dirty = False
            self.status.setText(f"已保存「{title}」")
            self.saved.emit(title)
        else:
            self.status.setText("保存失败：检查 data/prompts.json 能不能写")

    def _reset(self) -> None:
        current = self._current()
        if current is None:
            return
        kind, key, title = current
        default = prompts.DEFAULTS
        if kind == "system":
            text = (default.get("system") or {}).get(key, "")
        else:
            item = (default.get("scenes") or {}).get(key) or {}
            text = str(item.get("prompt") or "")
        if not text:
            self.status.setText("这条没有内置默认值")
            return
        self.editor.setPlainText(text)
        self.status.setText("已恢复成默认内容，记得点保存")
