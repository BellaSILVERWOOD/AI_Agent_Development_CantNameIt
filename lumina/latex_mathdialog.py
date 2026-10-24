"""公式输入对话框：写 LaTeX，实时看渲染效果，插入到文档里。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout,
)

from . import icons, latex_builder, latex_math, latex_matrix, math_input, theme
from .widgets import BaseDialog, SoftButton, Select


class MathDialog(BaseDialog):
    """输入/编辑一个公式（行内或行间）。"""

    def __init__(self, parent=None, latex: str = "", display: bool = False):
        super().__init__(
            parent, width=620, height=430, title="插入公式",
            subtitle="直接敲数学就行（如 (a+b)/2、sum_(i=1)^n a_i）；也可以写 LaTeX",
            icon=icons.DOCUMENT,
        )
        self._latex = latex or ""
        self._display = display

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(QLabel("输入方式"))
        self.input_mode = Select()
        self.input_mode.setFixedWidth(206)
        self.input_mode.addItem("快速输入（不用写 LaTeX）", "quick")
        self.input_mode.addItem("LaTeX 源码", "latex")
        self.input_mode.setCurrentIndex(0 if not self._latex else 1)
        self.input_mode.currentIndexChanged.connect(self._on_input_mode_changed)
        top.addWidget(self.input_mode)
        top.addSpacing(8)
        top.addWidget(QLabel("形式"))
        self.display_select = Select()
        self.display_select.setFixedWidth(180)
        self.display_select.addItem("行内公式 $…$", False)
        self.display_select.addItem("行间公式 \\[…\\]", True)
        self.display_select.setCurrentIndex(1 if display else 0)
        self.display_select.currentIndexChanged.connect(self._on_form_changed)
        top.addWidget(self.display_select)
        top.addSpacing(8)
        self.matrix_button = SoftButton("矩阵 / 行列式…", "ghost", height=32,
                                       min_width=132, point_size=8.8)
        self.matrix_button.clicked.connect(self._insert_matrix)
        top.addWidget(self.matrix_button)
        top.addStretch(1)
        self.body.addLayout(top)

        self.quick_edit = QLineEdit()
        self.quick_edit.setPlaceholderText("例如：(a+b)/2 、x^2 、sqrt(2) 、sum_(i=1)^n a_i 、λ^2-4λ+3=0")
        self.quick_edit.textChanged.connect(self._on_quick_changed)
        self.quick_hint = QLabel(
            "支持：a/b 分数、x^2 与 a_i 上下标、sqrt() 根号、sum/int/lim、|x| 绝对值、"
            "[[1,2],[3,4]] 矩阵、alpha/λ 之类希腊字母，以及 <= >= -> oo 等符号"
        )
        self.quick_hint.setObjectName("Tiny")
        self.quick_hint.setWordWrap(True)
        self.body.addWidget(self.quick_edit)
        self.body.addWidget(self.quick_hint)

        self.source_edit = QPlainTextEdit()
        self.source_edit.setPlainText(self._latex)
        self.source_edit.setFixedHeight(96)
        font = self.source_edit.font()
        font.setFamilies(["Cascadia Code", "Consolas", "Segoe UI"])
        font.setPixelSize(13)
        self.source_edit.setFont(font)
        self.source_edit.textChanged.connect(self._schedule_preview)
        self.body.addWidget(self.source_edit)

        preview_label = QLabel("预览")
        preview_label.setObjectName("Small")
        self.body.addWidget(preview_label)

        self.preview = QLabel("在下面输入公式后这里会显示渲染结果")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(150)
        self.preview.setStyleSheet(
            f"background: {theme.CARD_ALT}; border: 1px solid {theme.BORDER};"
            " border-radius: 10px; padding: 8px;"
        )
        self.body.addWidget(self.preview, 1)

        self.hint = QLabel("")
        self.hint.setObjectName("Tiny")
        self.body.addWidget(self.hint)

        self.ok_button = SoftButton("插入", "primary", height=36, min_width=104)
        self.ok_button.clicked.connect(self._accept)
        cancel = SoftButton("取消", "ghost", height=36, min_width=92)
        cancel.clicked.connect(self.reject)
        self.add_footer([], [cancel, self.ok_button])

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._render_preview)
        self._schedule_preview()

    # -------------------------------------------------- 行为
    def latex(self) -> str:
        return self.source_edit.toPlainText().strip()

    def is_display(self) -> bool:
        return bool(self.display_select.currentData())

    def _on_form_changed(self, _index: int) -> None:
        self._schedule_preview()

    def _is_quick(self) -> bool:
        return (self.input_mode.currentData() or "quick") == "quick"

    def _on_input_mode_changed(self, _index: int) -> None:
        quick = self._is_quick()
        self.quick_edit.setVisible(quick)
        self.quick_hint.setVisible(quick)
        self.source_edit.setVisible(not quick)
        if quick:
            # 从 LaTeX 反推不现实，直接从空开始
            self.quick_edit.setFocus()
            self._on_quick_changed(self.quick_edit.text())
        else:
            self.source_edit.setFocus()

    def _on_quick_changed(self, text: str) -> None:
        if not self._is_quick():
            return
        text = (text or "").strip()
        if not text:
            self.hint.setText("")
            self.source_edit.setPlainText("")
            self._schedule_preview()
            return
        try:
            latex = math_input.to_latex(text)
        except Exception as exc:  # noqa: BLE001
            self.hint.setText(f"暂时看不懂：{exc}")
            self.hint.setStyleSheet(f"color: {theme.WARNING};")
            return
        self.hint.setText("→ " + latex)
        self.hint.setStyleSheet(f"color: {theme.MUTED};")
        self.source_edit.blockSignals(True)
        self.source_edit.setPlainText(latex)
        self.source_edit.blockSignals(False)
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        self._timer.start()

    def _render_preview(self) -> None:
        latex = self.latex()
        if not latex:
            self.preview.setText("在下面输入公式后这里会显示渲染结果")
            self.preview.setPixmap(QPixmap())
            return
        path = latex_math.render(latex, self.is_display())
        if not path:
            self.preview.setText("这个公式没能渲染出来（检查 LaTeX 语法）")
            self.preview.setPixmap(QPixmap())
            return
        pixmap = QPixmap(path)
        if pixmap.width() > 560:
            pixmap = pixmap.scaledToWidth(560, Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pixmap)

    def _insert_matrix(self) -> None:
        dialog = latex_matrix.MatrixDialog(self, math_wrap=False)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            code = dialog.generated_code().strip()
            if code.startswith("\\["):
                code = code[2:].strip()
                if code.endswith("\\]"):
                    code = code[:-2].strip()
            self.source_edit.setPlainText(code)
            self._schedule_preview()

    def showEvent(self, event):
        super().showEvent(event)
        self._on_input_mode_changed(self.input_mode.currentIndex())
        if self._is_quick():
            self.quick_edit.setFocus()

    def _accept(self) -> None:
        if not self.latex():
            self.hint.setText("公式不能为空")
            self.hint.setStyleSheet(f"color: {theme.DANGER};")
            return
        self.accept()


class SourceInsertDialog(BaseDialog):
    """把任意 LaTeX 片段插到光标处（例如自己拼好的环境）。"""

    def __init__(self, parent=None, title: str = "插入 LaTeX 片段"):
        super().__init__(parent, width=560, height=320, title=title,
                         subtitle="插入的代码会出现在文档里，导出时原样保留",
                         icon=icons.DOCUMENT)
        self.edit = QPlainTextEdit()
        font = self.edit.font()
        font.setFamilies(["Cascadia Code", "Consolas", "Segoe UI"])
        font.setPixelSize(13)
        self.edit.setFont(font)
        self.body.addWidget(self.edit, 1)
        ok = SoftButton("插入", "primary", height=36, min_width=104)
        ok.clicked.connect(self.accept)
        cancel = SoftButton("取消", "ghost", height=36, min_width=92)
        cancel.clicked.connect(self.reject)
        self.add_footer([], [cancel, ok])

    def text(self) -> str:
        return self.edit.toPlainText()


class InlineMathPopup(QDialog):
    """就地输入公式的小窗：Enter 插入行内，Shift+Enter 插入行间。"""

    def __init__(self, parent=None, display: bool = False):
        super().__init__(parent, Qt.WindowType.Popup)
        self._display = display
        self.setFixedWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("敲数学，例如 (a+b)/2 、x^2 、sqrt(2) 、λ^2-4λ+3=0")
        layout.addWidget(self.edit)

        self.hint = QLabel("Enter 插入行内公式 · Shift+Enter 插入行间公式 · Esc 取消")
        self.hint.setObjectName("Tiny")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(56)
        self.image.setStyleSheet(
            f"background: {theme.CARD_ALT}; border: 1px solid {theme.BORDER};"
            " border-radius: 8px;"
        )
        layout.addWidget(self.image)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(220)
        self._timer.timeout.connect(self._refresh)
        self.edit.textChanged.connect(lambda _t: self._timer.start())
        self._refresh()

        self.setStyleSheet(
            f"QDialog {{ background: {theme.CARD}; border: 1px solid {theme.BORDER};"
            " border-radius: 10px; }"
        )

    # -------------------------------------------------- 逻辑
    def latex(self) -> str:
        return self._convert()[0]

    def is_display(self) -> bool:
        return self._display

    def _convert(self) -> tuple:
        text = self.edit.text().strip()
        if not text:
            return "", ""
        try:
            return math_input.to_latex(text), ""
        except Exception as exc:  # noqa: BLE001
            return "", str(exc)

    def _refresh(self) -> None:
        latex, error = self._convert()
        if error:
            self.hint.setText(f"暂时看不懂：{error}")
            self.hint.setStyleSheet(f"color: {theme.WARNING};")
            self.image.setText("")
            self.image.setPixmap(QPixmap())
            return
        if not latex:
            self.image.setText("")
            self.image.setPixmap(QPixmap())
            return
        cached = latex_math.cached_png(latex, self._display)
        if cached:
            pixmap = QPixmap(cached)
            if pixmap.width() > 380:
                pixmap = pixmap.scaledToWidth(
                    380, Qt.TransformationMode.SmoothTransformation)
            self.image.setPixmap(pixmap)
            self.image.setText("")
        else:
            self.image.setPixmap(QPixmap())
            self.image.setText(latex)
            self.image.setStyleSheet(
                f"background: {theme.CARD_ALT}; border: 1px solid {theme.BORDER};"
                " border-radius: 8px; padding: 6px;"
                " font-family: 'Cascadia Code', Consolas, monospace;"
                f" color: {theme.TEXT_SOFT};"
            )
        self.hint.setText("Enter 插入行内公式 · Shift+Enter 插入行间公式 · Esc 取消")
        self.hint.setStyleSheet(f"color: {theme.MUTED};")

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._display = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if self.latex():
                self.accept()
            else:
                self.hint.setText("先敲一个公式")
                self.hint.setStyleSheet(f"color: {theme.DANGER};")
            return
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.edit.setFocus()
