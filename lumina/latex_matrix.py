"""图形化矩阵 / 行列式对话框：像表格一样填数，方向键换格子。

设计要点
--------
* 网格用 QLineEdit 铺在 QGridLayout 里，模仿表格：↑/↓ 直接上下换格，
  ←/→ 在光标到达行首/行尾时换格（否则正常移动光标），Tab/Shift+Tab 前后换格，
  Enter 换到下一行同一列（最后一格再按 Enter 就自动加一行）。
* 改行数/列数时保留已填内容。
* 右侧实时显示生成的 LaTeX，底部可选环境（圆括号/方括号/行列式竖线…）。
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout,
    QWidget,
)

from . import icons, latex_builder, theme
from .widgets import BaseDialog, SoftButton, Select


class _CellEdit(QLineEdit):
    """单元格输入框：把方向键/回车翻译成「换格」。"""

    move = Signal(int, int)   # dx, dy

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.move.emit(0, -1 if key == Qt.Key.Key_Up else 1)
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            text = self.text()
            pos = self.cursorPosition()
            if key == Qt.Key.Key_Left and pos == 0:
                self.move.emit(-1, 0)
                return
            if key == Qt.Key.Key_Right and pos == len(text):
                self.move.emit(1, 0)
                return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.move.emit(0, 1)
            return
        if key == Qt.Key.Key_Tab:
            self.move.emit(1, 0)
            return
        if key == Qt.Key.Key_Backtab:
            self.move.emit(-1, 0)
            return
        super().keyPressEvent(event)


class MatrixGrid(QWidget):
    """可编辑的矩阵格子（带括号样式的外框）。"""

    changed = Signal()

    def __init__(self, rows: int = 2, cols: int = 2, parent=None):
        super().__init__(parent)
        self._rows = max(1, rows)
        self._cols = max(1, cols)
        self._cells: List[List[_CellEdit]] = []
        self._env = "pmatrix"

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(34, 10, 34, 10)
        self._grid.setSpacing(6)
        self._build()

    # -------------------------------------------------- 构建
    def _build(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cells = []
        font = QFont()
        font.setFamilies(["Cascadia Code", "Consolas", "Segoe UI"])
        font.setPixelSize(13)
        for row in range(self._rows):
            line: List[_CellEdit] = []
            for col in range(self._cols):
                edit = _CellEdit(self)
                edit.setFont(font)
                edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
                edit.setMinimumWidth(76)
                edit.setProperty("matrixCell", True)
                edit.setStyleSheet(
                    "QLineEdit[matrixCell=\"true\"] {"
                    f"  background: {theme.INPUT_BG};"
                    f"  border: 1px solid {theme.BORDER};"
                    "  border-radius: 6px; padding: 4px 6px;"
                    "}"
                    "QLineEdit[matrixCell=\"true\"]:focus {"
                    f"  border: 1px solid {theme.ACCENT};"
                    "}"
                )
                edit.move.connect(self._rel_move)
                edit.textChanged.connect(self.changed)
                edit.installEventFilter(self)
                self._grid.addWidget(edit, row, col)
                line.append(edit)
            self._cells.append(line)
        self.changed.emit()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # 粘贴 TSV / 逗号分隔内容时，自动铺到格子里
        if event.type() == QEvent.Type.KeyPress and isinstance(obj, _CellEdit):
            if event.matches(Qt.Key.Key_V) and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                text = self.window().clipboard().text() if hasattr(self.window(), "clipboard") else ""
                from PySide6.QtWidgets import QApplication

                text = QApplication.clipboard().text()
                if text and ("\t" in text or "\n" in text or "," in text):
                    self._fill_from_text(text, start=obj)
                    return True
        return super().eventFilter(obj, event)

    # -------------------------------------------------- 行列
    def set_size(self, rows: int, cols: int) -> None:
        rows = max(1, min(12, int(rows)))
        cols = max(1, min(12, int(cols)))
        if rows == self._rows and cols == self._cols:
            return
        old = self.values()
        self._rows, self._cols = rows, cols
        self._build()
        for row in range(min(rows, len(old))):
            for col in range(min(cols, len(old[row]))):
                self._cells[row][col].setText(old[row][col])

    def add_row(self) -> None:
        self.set_size(self._rows + 1, self._cols)

    def rows(self) -> int:
        return self._rows

    def cols(self) -> int:
        return self._cols

    # -------------------------------------------------- 取值
    def values(self) -> List[List[str]]:
        return [[edit.text() for edit in line] for line in self._cells]

    def set_values(self, values: List[List[str]]) -> None:
        if not values:
            return
        self.set_size(len(values), max(len(row) for row in values))
        for row, line in enumerate(values):
            for col, value in enumerate(line):
                if row < self._rows and col < self._cols:
                    self._cells[row][col].setText(str(value))

    # -------------------------------------------------- 焦点移动
    def _rel_move(self, dx: int, dy: int) -> None:
        current = None
        for row, line in enumerate(self._cells):
            for col, edit in enumerate(line):
                if edit.hasFocus():
                    current = (row, col)
                    break
            if current:
                break
        if current is None:
            current = (0, 0)
        row, col = current
        row += dy
        col += dx
        if row >= self._rows:          # 到底了：自动加一行
            if dx == 0:
                self.add_row()
            row = self._rows - 1
        if row < 0:
            row = 0
        if col >= self._cols:
            col = 0
            row = min(self._rows - 1, row + 1)
        if col < 0:
            col = self._cols - 1
            row = max(0, row - 1)
        edit = self._cells[row][col]
        edit.setFocus()
        edit.selectAll()

    def focus_first(self) -> None:
        if self._cells and self._cells[0]:
            self._cells[0][0].setFocus()
            self._cells[0][0].selectAll()

    # -------------------------------------------------- 外部样式（括号）
    def set_env(self, env: str) -> None:
        self._env = env
        self.update()

    def _fill_from_text(self, text: str, start: Optional[_CellEdit] = None) -> None:
        lines = [line for line in text.replace("\r", "").split("\n") if line.strip() != ""]
        parsed: List[List[str]] = []
        for line in lines:
            if "\t" in line:
                parsed.append([part.strip() for part in line.split("\t")])
            elif "," in line:
                parsed.append([part.strip() for part in line.split(",")])
            else:
                parsed.append([part.strip() for part in line.split()])
        if not parsed:
            return
        rows = max(len(parsed), self._rows)
        cols = max(max(len(row) for row in parsed), self._cols)
        self.set_size(rows, cols)
        for row, line in enumerate(parsed):
            for col, value in enumerate(line):
                self._cells[row][col].setText(value)
        self.changed.emit()

    def paintEvent(self, event):
        """按格子区域画一对括号（用字体字形，观感和真正排出来的矩阵一致）。"""
        from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter

        glyph = {"pmatrix": ("(", ")"), "bmatrix": ("[", "]"),
                 "Bmatrix": ("{", "}"), "vmatrix": ("|", "|"),
                 "Vmatrix": ("‖", "‖")}.get(self._env)
        if not glyph:
            return
        body = None
        for line in self._cells:
            for edit in line:
                rect = QRectF(edit.geometry())
                body = rect if body is None else body.united(rect)
        if body is None or body.height() < 8 or body.width() < 8:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        pixel = max(16, int(body.height() * 1.16))
        font = QFont()
        font.setFamilies(["Cambria Math", "Times New Roman", "Segoe UI"])
        font.setPixelSize(pixel)
        font.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias
            | QFont.StyleStrategy.NoSubpixelAntialias
        )
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_SOFT))

        metrics = QFontMetrics(font)
        width = max(metrics.horizontalAdvance(glyph[0]) + 8, 14)
        top = body.center().y() - pixel / 2.0
        left_rect = QRectF(body.left() - width - 2, top, width, pixel)
        right_rect = QRectF(body.right() + 2, top, width, pixel)
        painter.drawText(left_rect, int(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter), glyph[0])
        painter.drawText(right_rect, int(Qt.AlignmentFlag.AlignLeft
                                         | Qt.AlignmentFlag.AlignVCenter), glyph[1])
        painter.end()


class MatrixDialog(BaseDialog):
    """插入矩阵 / 行列式的对话框。"""

    def __init__(self, parent=None, env: str = "pmatrix", math_wrap: bool = True):
        super().__init__(
            parent, width=620, height=520, title="插入矩阵 / 行列式",
            subtitle="像填表格一样输入：方向键换格子，Enter 下一行，Tab 下一格，粘贴 Excel 内容可整块填入",
            icon=icons.DOCUMENT,
        )
        self._math_wrap = math_wrap
        self._generated = ""

        # 顶部：行列数 + 环境
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(QLabel("行"))
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 12)
        self.rows_spin.setValue(2)
        self.rows_spin.setFixedWidth(64)
        top.addWidget(self.rows_spin)
        top.addWidget(QLabel("列"))
        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(1, 12)
        self.cols_spin.setValue(2)
        self.cols_spin.setFixedWidth(64)
        top.addWidget(self.cols_spin)
        top.addSpacing(8)
        self.env_select = Select()
        self.env_select.setMinimumWidth(190)
        for item in latex_builder.MATRIX_ENVS:
            self.env_select.addItem(item["name"], item["id"])
        index = self.env_select.findData(env)
        self.env_select.setCurrentIndex(max(0, index))
        top.addWidget(self.env_select)
        top.addSpacing(8)
        self.align_select = Select()
        self.align_select.setFixedWidth(120)
        for item in latex_builder.MATRIX_ALIGNS:
            self.align_select.addItem(item["name"], item["id"])
        self.align_select.setToolTip("array 环境的列对齐，其它环境忽略")
        top.addWidget(self.align_select)
        top.addStretch(1)
        self.body.addLayout(top)

        # 网格
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: {theme.CARD_ALT}; border: 1px solid {theme.BORDER};"
            " border-radius: 12px; }"
        )
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(6, 6, 6, 6)
        self.grid = MatrixGrid(2, 2)
        self.grid.setStyleSheet("background: transparent;")
        frame_layout.addWidget(self.grid)
        self.body.addWidget(frame, 1)

        # 预览
        preview_label = QLabel("生成的 LaTeX")
        preview_label.setObjectName("Small")
        self.body.addWidget(preview_label)
        self.preview = QLabel("")
        self.preview.setObjectName("Tiny")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview.setStyleSheet(
            f"color: {theme.TEXT_SOFT}; background: {theme.CARD_ALT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 8px; padding: 8px;"
            " font-family: 'Cascadia Code', Consolas, monospace;"
        )
        self.preview.setMinimumHeight(92)
        self.body.addWidget(self.preview)

        self.insert_button = SoftButton("插入", "primary", height=36, min_width=104)
        self.insert_button.clicked.connect(self._accept)
        self.cancel_button = SoftButton("取消", "ghost", height=36, min_width=92)
        self.cancel_button.clicked.connect(self.reject)
        self.add_footer([], [self.cancel_button, self.insert_button])

        self.rows_spin.valueChanged.connect(self._on_size_changed)
        self.cols_spin.valueChanged.connect(self._on_size_changed)
        self.env_select.currentIndexChanged.connect(self._refresh)
        self.align_select.currentIndexChanged.connect(self._refresh)
        self.grid.changed.connect(self._refresh)

        self._refresh()

    # -------------------------------------------------- 事件
    def _on_size_changed(self, _value: int) -> None:
        self.grid.set_size(self.rows_spin.value(), self.cols_spin.value())
        self._refresh()

    def _refresh(self) -> None:
        env = self.env_select.currentData() or "pmatrix"
        align = self.align_select.currentData() or "c"
        self.grid.set_env(env)
        if env == "vmatrix":
            code = latex_builder.determinant_code(
                self.grid.values(), math_wrap=self._math_wrap
            )
        else:
            code = latex_builder.matrix_code(
                self.grid.values(), env=env, align=align, math_wrap=self._math_wrap
            )
        self._generated = code
        self.preview.setText(code)

    def _accept(self) -> None:
        self._refresh()
        self.accept()

    def generated_code(self) -> str:
        return self._generated

    def showEvent(self, event):
        super().showEvent(event)
        self.grid.focus_first()
