"""文档编辑器：所见即所得写文档，程序自动生成 LaTeX 源码。

两种视图（随时切换）：
* 文档视图：像 Word 一样编辑（标题、粗斜体、字号、颜色、对齐、列表、表格、公式…），
  工具栏按钮直接改变文字格式；公式以渲染好的图片显示，双击可重新编辑。
* 源码视图：显示并可编辑由文档生成的 LaTeX，也可以自己改，然后编译预览。
右侧始终是 XeLaTeX 编译出来的 PDF 预览。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QImage, QTextBlockFormat, QTextCharFormat, QTextCursor,
    QTextImageFormat, QTextListFormat,
)
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from . import (
    icons, latex_builder, latex_compile, latex_export, latex_math, latex_matrix,
    latex_mathdialog, theme,
)
from .popup import RoundedPopup
from .widgets import (
    Card, HScrollRow, IconButton, Pill, Select, SoftButton, ToggleSwitch,
)

MONO_STACK = ["Cascadia Code", "Consolas", "JetBrains Mono", "Segoe UI"]
BODY_SIZES = [10.5, 11, 12, 13, 14, 15, 16, 18, 20, 24]
STYLE_ITEMS = ["正文", "标题 1", "标题 2", "标题 3", "引用"]
HEADING_SIZES = {1: 18.0, 2: 15.5, 3: 13.5}


def _mono_font(pixel: int = 13) -> QFont:
    font = QFont()
    font.setFamilies(MONO_STACK)
    font.setPixelSize(pixel)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.NoSubpixelAntialias
    )
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


class DocEditor(QTextEdit):
    """文档视图编辑控件：双击公式图片可重新编辑。"""

    math_activated = Signal(str, bool, int)   # latex, display, 字符位置
    math_requested = Signal(bool)             # 就地新建公式（display）

    def _math_at(self, point) -> Optional[tuple]:
        cursor = self.cursorForPosition(point)
        position = cursor.position()
        document = self.document()
        for candidate in (position, position - 1, position + 1):
            if candidate < 0:
                continue
            probe = QTextCursor(document)
            probe.setPosition(candidate)
            probe.movePosition(QTextCursor.MoveOperation.Right,
                               QTextCursor.MoveMode.KeepAnchor, 1)
            fmt = probe.charFormat()
            if fmt.isImageFormat():
                payload = latex_export.decode_math(fmt.toImageFormat().name())
                if payload is not None:
                    return payload[0], payload[1], candidate
        return None

    def mouseDoubleClickEvent(self, event):
        found = self._math_at(event.position().toPoint())
        if found is not None:
            self.math_activated.emit(found[0], found[1], found[2])
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        from PySide6.QtGui import QFont, QTextBlockFormat, QTextCharFormat

        # Ctrl+E（或 Ctrl+Shift+E）就地插公式
        if event.key() == Qt.Key.Key_E and (
                event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.math_requested.emit(
                bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            return
        # 直接敲 $ 也进入公式输入
        if event.text() == "$":
            self.math_requested.emit(False)
            return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            super().keyPressEvent(event)
            # 新段落回到正文样式，避免标题样式一路继承下去
            block_format = QTextBlockFormat()
            block_format.setHeadingLevel(0)
            block_format.setLeftMargin(0)
            block_format.setIndent(0)
            block_format.setTopMargin(6)
            block_format.setBottomMargin(10)
            block_format.setLineHeight(
                150.0, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            cursor = self.textCursor()
            cursor.setBlockFormat(block_format)
            char_format = QTextCharFormat()
            char_format.setFontPointSize(11.0)
            char_format.setFontWeight(QFont.Weight.Normal)
            char_format.setFontItalic(False)
            cursor.setCharFormat(char_format)
            self.setTextCursor(cursor)
            return
        super().keyPressEvent(event)


class GreekPicker(RoundedPopup):
    """希腊字母选择板（弹出后点一个就插入为行内公式）。"""

    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, padding=12, radius=10)
        from PySide6.QtWidgets import QGridLayout

        grid = QGridLayout()
        grid.setSpacing(4)
        self.content.addLayout(grid)
        for index, item in enumerate(latex_builder.GREEK):
            button = SoftButton(item["symbol"], "ghost", height=32, min_width=40,
                                point_size=12.0)
            button.setToolTip("\\" + item["id"])
            button.clicked.connect(
                lambda _checked=False, name=item["id"]: self._pick("\\" + name))
            grid.addWidget(button, index // 9, index % 9)

    def _pick(self, command: str) -> None:
        self.picked.emit(command)
        self.close()


class LatexPage(QWidget):
    #: 公式渲染分辨率：插进文档时再按字号缩小，这样放大看也不糊
    #: 公式渲染分辨率：越高越清晰（屏幕缩放 125% 也不会糊）
    MATH_DPI = 600
    """文档 → LaTeX 编辑页面。"""

    data_changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._packages = self._load_packages()
        self._extra_preamble = self.store.get_setting("latex_preamble", "") or ""
        self._last_dir = self.store.get_setting("latex_dir", "") or ""
        self._auto_compile = (self.store.get_setting("latex_auto", "1") or "1") == "1"
        self._compiling = False
        self._mode = "doc"
        self._first_show = True
        self._math_display = (self.store.get_setting("latex_math_display", "image")
                              or "image")

        self._compiler = latex_compile.LatexCompiler(self)
        self._compiler.finished.connect(self._on_compiled)

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(900)
        self._autosave.timeout.connect(self._save_document)

        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(2600)      # 打字停下 2.6 秒才编译一次
        self._auto_timer.timeout.connect(self._auto_compile_now)

        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(700)
        self._sync_timer.timeout.connect(self._sync_source_quietly)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 14)
        root.setSpacing(10)

        # ---------------- 标题行
        # 分栏变窄时这一排放不下 → 横向滚动，而不是叠在一起
        header = HScrollRow(height=48, spacing=8)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("文档编辑器")
        title.setObjectName("H1")
        subtitle = QLabel("像 Word 一样写，LaTeX 自动生成；需要时切到源码视图手改")
        subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        ready = latex_compile.engine_available()
        self.engine_pill = Pill(
            "XeLaTeX 就绪" if ready else "未找到 XeLaTeX",
            theme.SUCCESS if ready else theme.WARNING,
            theme.soft(theme.SUCCESS if ready else theme.WARNING, 0.14),
        )
        header.addWidget(self.engine_pill)

        self.mode_doc_button = SoftButton("文档", "primary", height=32, min_width=68,
                                          point_size=8.8)
        self.mode_doc_button.clicked.connect(lambda: self.set_mode("doc"))
        self.mode_src_button = SoftButton("源码", "ghost", height=32, min_width=68,
                                          point_size=8.8)
        self.mode_src_button.clicked.connect(lambda: self.set_mode("source"))
        header.addWidget(self.mode_doc_button)
        header.addWidget(self.mode_src_button)
        header.addSpacing(6)

        self.packages_button = SoftButton("宏包", "ghost", height=32, min_width=80,
                                          glyph=icons.FILTER, point_size=8.6)
        self.packages_button.clicked.connect(self._pick_packages)
        header.addWidget(self.packages_button)
        self.open_button = SoftButton("打开", "ghost", height=32, min_width=80,
                                      glyph=icons.FOLDER, point_size=8.6)
        self.open_button.clicked.connect(self.open_file)
        header.addWidget(self.open_button)
        self.save_button = SoftButton("导出 .tex", "ghost", height=32, min_width=96,
                                      glyph=icons.SAVE, point_size=8.6)
        self.save_button.clicked.connect(self.save_file)
        header.addWidget(self.save_button)
        self.compile_button = SoftButton("编译预览", "primary", height=32, min_width=108,
                                         glyph=icons.EYE, point_size=8.6)
        self.compile_button.clicked.connect(self.compile_now)
        header.addWidget(self.compile_button)
        root.addWidget(header)

        self.toolbar = self._build_toolbar()
        root.addWidget(self.toolbar)

        # ---------------- 编辑区 + 预览
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        self.splitter = splitter

        editor_card = Card(radius=14, fill=theme.CARD, border=theme.BORDER, padding=10,
                           spacing=6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.view_label = QLabel("文档视图")
        self.view_label.setObjectName("Small")
        head.addWidget(self.view_label)
        head.addStretch(1)
        self.copy_button = IconButton(icons.DOCUMENT, size=26, glyph_size=12,
                                      tip="复制整份 LaTeX（含导言区）")
        self.copy_button.clicked.connect(self._copy_latex)
        head.addWidget(self.copy_button)
        editor_card.body.addLayout(head)

        self.stack = QStackedWidget()
        self.doc_editor = DocEditor()
        self.doc_editor.setFont(self._body_font())
        self.doc_editor.setStyleSheet(
            f"QTextEdit {{ background: {theme.CARD}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 10px;"
            f" padding: 18px 22px; }}"
        )
        # 像一页纸：四周留出页边距（Word 那种感觉），正文行距/段距在 _style_document 里
        self.doc_editor.document().setDocumentMargin(46)
        self.doc_editor.textChanged.connect(self._on_document_changed)
        self.doc_editor.math_activated.connect(self._edit_math_at)
        self.doc_editor.math_requested.connect(self.open_inline_math)
        self.stack.addWidget(self.doc_editor)

        self.source_editor = QPlainTextEdit()
        self.source_editor.setFont(_mono_font(13))
        self.source_editor.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.INPUT_BG}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 10px; padding: 10px; }}"
        )
        self.source_editor.textChanged.connect(self._on_source_changed)
        self.stack.addWidget(self.source_editor)
        editor_card.body.addWidget(self.stack, 1)
        splitter.addWidget(editor_card)

        preview_card = Card(radius=14, fill=theme.CARD, border=theme.BORDER, padding=10,
                            spacing=6)
        preview_head = QHBoxLayout()
        preview_head.setSpacing(8)
        preview_label = QLabel("预览")
        preview_label.setObjectName("Small")
        preview_head.addWidget(preview_label)
        preview_head.addStretch(1)
        self.page_label = QLabel("—")
        self.page_label.setObjectName("Tiny")
        preview_head.addWidget(self.page_label)
        self.prev_button = IconButton(icons.CHEVRON_LEFT, size=26, glyph_size=11, tip="上一页")
        self.prev_button.clicked.connect(lambda: self._flip_page(-1))
        self.next_button = IconButton(icons.CHEVRON_RIGHT, size=26, glyph_size=11, tip="下一页")
        self.next_button.clicked.connect(lambda: self._flip_page(1))
        preview_head.addWidget(self.prev_button)
        preview_head.addWidget(self.next_button)
        preview_card.body.addLayout(preview_head)
        self.pdf_view = self._make_pdf_view()
        # 预览区：没内容时别留一块灰 —— 给一句说明 / 编译中的提示
        self.preview_stack = QStackedWidget()
        self.preview_stack.addWidget(self.pdf_view)
        self.preview_hint = QLabel("点右上角「编译预览」生成 PDF")
        self.preview_hint.setObjectName("Muted")
        self.preview_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_hint.setWordWrap(True)
        self.preview_hint.setContentsMargins(18, 18, 18, 18)
        self.preview_hint.setStyleSheet(
            f"QLabel {{ background: {theme.CARD_ALT}; color: {theme.MUTED};"
            f" border: 1px dashed {theme.BORDER}; border-radius: 10px; }}")
        self.preview_stack.addWidget(self.preview_hint)
        self.preview_stack.setCurrentWidget(self.preview_hint)
        preview_card.body.addWidget(self.preview_stack, 1)
        splitter.addWidget(preview_card)
        splitter.setSizes([620, 560])
        root.addWidget(splitter, 1)

        # ---------------- 状态行
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Tiny")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        self.sync_button = SoftButton("由文档刷新源码", "ghost", height=28,
                                      min_width=136, point_size=8.4)
        self.sync_button.clicked.connect(self._refresh_source_from_document)
        self.sync_button.hide()
        status_row.addWidget(self.sync_button)
        auto_label = QLabel("自动编译")
        auto_label.setObjectName("Tiny")
        status_row.addWidget(auto_label)
        self.auto_switch = ToggleSwitch(self._auto_compile)
        self.auto_switch.toggled.connect(self._on_auto_toggled)
        status_row.addWidget(self.auto_switch)
        root.addLayout(status_row)

        self._load_document()
        self.math_mode_button.setText(
            "公式：源码" if self._math_display == "source" else "公式：图")
        self.set_mode("doc")

    # -------------------------------------------------- 基础
    def _body_font(self) -> QFont:
        font = QFont()
        font.setFamilies(theme.font_families())
        font.setPointSizeF(11.0)
        font.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.NoSubpixelAntialias
        )
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        return font

    def _make_pdf_view(self) -> QWidget:
        try:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView

            self.pdf_doc = QPdfDocument(self)
            view = QPdfView(self)
            view.setDocument(self.pdf_doc)
            view.setPageMode(QPdfView.PageMode.SinglePage)
            view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            view.setStyleSheet(
                f"QPdfView {{ background: {theme.CARD_ALT}; border: 1px solid {theme.BORDER};"
                " border-radius: 10px; }"
            )
            try:
                view.pageNavigator().currentPageChanged.connect(self._sync_page_label)
            except Exception:
                pass
            return view
        except Exception:  # noqa: BLE001
            self.pdf_doc = None
            label = QLabel("当前环境不支持 PDF 预览（缺少 QtPdf）")
            label.setObjectName("Muted")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return label

    def _tool(self, text: str, tip: str, handler, width: int = 46) -> SoftButton:
        button = SoftButton(text, "ghost", height=30, min_width=width, point_size=8.8)
        button.setToolTip(tip)
        button.clicked.connect(handler)
        return button

    def _build_toolbar(self) -> QWidget:
        card = Card(radius=14, fill=theme.CARD, border=theme.BORDER, padding=10, spacing=8)
        # 分栏变窄时这两排放不下 → 各自横向滚动，而不是叠在一起
        row1 = HScrollRow(height=36, spacing=6)
        self.style_select = Select()
        self.style_select.setFixedWidth(110)
        for name in STYLE_ITEMS:
            self.style_select.addItem(name)
        self.style_select.activated.connect(self._on_style)
        row1.addWidget(self.style_select)

        self.size_select = Select()
        self.size_select.setFixedWidth(88)
        for size in BODY_SIZES:
            self.size_select.addItem(f"{size:g} pt")
        self.size_select.setCurrentIndex(BODY_SIZES.index(11))
        self.size_select.activated.connect(self._on_size)
        row1.addWidget(self.size_select)

        row1.addWidget(self._tool("B", "加粗", self._toggle_bold, 40))
        row1.addWidget(self._tool("I", "斜体", self._toggle_italic, 40))
        row1.addWidget(self._tool("U", "下划线", self._toggle_underline, 40))
        row1.addWidget(self._tool("A", "字体颜色", self._pick_color, 40))
        row1.addSpacing(6)
        row1.addWidget(self._tool("左", "左对齐",
                                  lambda: self._align(Qt.AlignmentFlag.AlignLeft), 40))
        row1.addWidget(self._tool("中", "居中",
                                  lambda: self._align(Qt.AlignmentFlag.AlignHCenter), 40))
        row1.addWidget(self._tool("右", "右对齐",
                                  lambda: self._align(Qt.AlignmentFlag.AlignRight), 40))
        row1.addSpacing(6)
        row1.addWidget(self._tool("• 列表", "无序列表", lambda: self._toggle_list(False), 62))
        row1.addWidget(self._tool("1. 列表", "有序列表", lambda: self._toggle_list(True), 66))
        row1.addStretch(1)
        card.body.addWidget(row1)

        row2 = HScrollRow(height=36, spacing=6)
        row2.addWidget(self._tool("行内公式", "插入行内公式（$…$）",
                                  lambda: self.open_math(False), 78))
        row2.addWidget(self._tool("行间公式", "插入行间公式（\\[…\\]）",
                                  lambda: self.open_math(True), 78))
        row2.addWidget(self._tool("矩阵", "矩阵 / 行列式", self._open_matrix, 58))
        row2.addWidget(self._tool("αβ", "希腊字母", self._open_greek, 48))
        row2.addWidget(self._tool("表格", "插入表格", self._insert_table, 58))
        row2.addWidget(self._tool("图片", "插入图片", self._insert_image, 58))
        row2.addWidget(self._tool("源码片段", "插入任意 LaTeX 源码", self._insert_raw, 84))
        row2.addSpacing(8)
        self.math_mode_button = self._tool("公式：图", "切换公式显示方式（渲染图 / 源码）",
                                           self.toggle_math_display, 96)
        row2.addWidget(self.math_mode_button)
        row2.addStretch(1)
        card.body.addWidget(row2)
        return card

    # -------------------------------------------------- 视图切换
    def set_mode(self, mode: str) -> None:
        mode = "source" if mode == "source" else "doc"
        self._mode = mode
        doc_mode = mode == "doc"
        self.stack.setCurrentIndex(0 if doc_mode else 1)
        self.view_label.setText("文档视图" if doc_mode else "LaTeX 源码（由文档生成）")
        self.toolbar.setVisible(doc_mode)
        self.sync_button.setVisible(not doc_mode)
        self.mode_doc_button.setKind("primary" if doc_mode else "ghost")
        self.mode_src_button.setKind("ghost" if doc_mode else "primary")
        if not doc_mode:
            self._refresh_source_from_document(quiet=True)
        else:
            self.doc_editor.setFocus()

    def _sync_source_quietly(self) -> None:
        if self._mode == "doc":
            body = latex_export.document_to_latex(self.doc_editor.document())
            if body != self.source_editor.toPlainText():
                self.source_editor.blockSignals(True)
                self.source_editor.setPlainText(body)
                self.source_editor.blockSignals(False)

    def _refresh_source_from_document(self, quiet: bool = False) -> None:
        body = latex_export.document_to_latex(self.doc_editor.document())
        self.source_editor.blockSignals(True)
        self.source_editor.setPlainText(body)
        self.source_editor.blockSignals(False)
        self.store.set_setting("latex_generated", body)
        if not quiet:
            self._set_status(f"已由文档生成 {len(body.splitlines())} 行 LaTeX", "ok")
        if self._mode == "source" and self._auto_compile:
            self._auto_timer.start(400)

    def _on_document_changed(self) -> None:
        self._autosave.start()
        self._sync_timer.start()
        if self._auto_compile and self._mode == "doc":
            self._auto_timer.start()

    def _on_source_changed(self) -> None:
        self._autosave.start()
        if self._auto_compile:
            self._auto_timer.start()

    # -------------------------------------------------- 富文本
    def _cursor(self) -> QTextCursor:
        return self.doc_editor.textCursor()

    def _merge(self, fmt: QTextCharFormat) -> None:
        cursor = self._cursor()
        cursor.mergeCharFormat(fmt)
        self.doc_editor.mergeCurrentCharFormat(fmt)
        self.doc_editor.setFocus()

    def _toggle_bold(self) -> None:
        current = self._cursor().charFormat()
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Normal if current.fontWeight() >= 600
                          else QFont.Weight.DemiBold)
        self._merge(fmt)

    def _toggle_italic(self) -> None:
        current = self._cursor().charFormat()
        fmt = QTextCharFormat()
        fmt.setFontItalic(not current.fontItalic())
        self._merge(fmt)

    def _toggle_underline(self) -> None:
        current = self._cursor().charFormat()
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not current.fontUnderline())
        self._merge(fmt)

    def _on_size(self, _index: int = -1) -> None:
        try:
            size = float(BODY_SIZES[self.size_select.currentIndex()])
        except (ValueError, IndexError):
            return
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        self._merge(fmt)

    def _on_style(self, _index: int = -1) -> None:
        level = self.style_select.currentIndex()
        cursor = self._cursor()
        block_format = cursor.blockFormat()
        fmt = QTextCharFormat()
        if level in HEADING_SIZES:
            block_format.setHeadingLevel(level)
            block_format.setLeftMargin(0)
            fmt.setFontPointSize(HEADING_SIZES[level])
            fmt.setFontWeight(QFont.Weight.DemiBold)
        elif level == 4:
            block_format.setHeadingLevel(0)
            block_format.setLeftMargin(24)
            fmt.setFontPointSize(11.0)
            fmt.setFontWeight(QFont.Weight.Normal)
            fmt.setFontItalic(True)
        else:
            block_format.setHeadingLevel(0)
            block_format.setLeftMargin(0)
            fmt.setFontPointSize(11.0)
            fmt.setFontWeight(QFont.Weight.Normal)
        cursor.setBlockFormat(block_format)
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        cursor.mergeCharFormat(fmt)
        cursor.clearSelection()
        self.doc_editor.setTextCursor(cursor)
        self.doc_editor.setFocus()

    def _align(self, flag) -> None:
        self.doc_editor.setAlignment(flag)
        self.doc_editor.setFocus()

    def _toggle_list(self, ordered: bool) -> None:
        cursor = self._cursor()
        style = (QTextListFormat.Style.ListDecimal if ordered
                 else QTextListFormat.Style.ListDisc)
        current = cursor.currentList()
        if current is not None and current.format().style() == style:
            # 取消列表
            block_format = QTextBlockFormat()
            block_format.setIndent(0)
            cursor.setBlockFormat(block_format)
            block_format.setObjectIndex(-1)
            cursor.setBlockFormat(block_format)
        else:
            list_format = QTextListFormat()
            list_format.setStyle(style)
            list_format.setIndent(1)
            cursor.createList(list_format)
        self.doc_editor.setFocus()

    def _pick_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        color = QColorDialog.getColor(QColor(theme.ACCENT), self, "选择文字颜色")
        if not color.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        self._merge(fmt)

    # -------------------------------------------------- 插入
    def _screen_image(self, image: QImage, width: float,
                      height: float) -> QImage:
        """把公式图缩成「逻辑尺寸 × 屏幕缩放比」的设备像素（不糊的关键）。

        QTextDocument 画图片时用的缩放很粗糙（还可能用最近邻），所以**我们自己**
        先按目标尺寸做一次平滑缩放；再把格式的宽高写成逻辑尺寸，Qt 就 1:1 画到
        设备像素上。以前是直接塞 360 DPI 的原图让 Qt 去缩，高分屏上看着发虚。
        """
        try:
            dpr = float(self.doc_editor.devicePixelRatioF() or 1.0)
        except Exception:
            dpr = 1.0
        target_w = max(1, int(round(float(width) * dpr)))
        target_h = max(1, int(round(float(height) * dpr)))
        scaled = image.scaled(
            target_w, target_h,
            aspectMode=Qt.AspectRatioMode.IgnoreAspectRatio,
            mode=Qt.TransformationMode.SmoothTransformation)
        # 透明 PNG 叠在编辑区里会发灰，先合到不透明底色上
        opaque = QImage(scaled.size(), QImage.Format.Format_RGB32)
        opaque.fill(QColor(theme.CARD))
        from PySide6.QtGui import QPainter

        painter = QPainter(opaque)
        painter.drawImage(0, 0, scaled)
        painter.end()
        return opaque

    def _register_math_resource(self, latex: str, display: bool,
                                width: float = 0.0,
                                height: float = 0.0) -> Optional[str]:
        """把渲染好的公式注册为文档图片资源，返回资源名。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QTextDocument

        path = latex_math.render(latex, display, dpi=self.MATH_DPI,
                                 color=str(theme.TEXT))
        if not path:
            return None
        image = QImage(path)
        if image.isNull():
            return None
        if width <= 0 or height <= 0:
            width, height = self._math_display_size(image, display)
        prepared = self._screen_image(image, width, height)
        name = latex_export.math_payload(latex, display)
        self.doc_editor.document().addResource(
            QTextDocument.ResourceType.ImageResource, QUrl(name), prepared)
        return name

    def _math_display_size(self, image: QImage, display: bool) -> tuple:
        """公式在文档里显示多大：按**正文字号**换算，而不是把渲染像素直接铺上去。

        渲染是 360 DPI、排版基准 12pt；屏幕 96 DPI，所以「12pt 公式」的原尺寸
        就是 像素 × 96/DPI。再按当前字号（默认 11pt）缩放，行内略小、行间略大。
        以前直接乘 0.72，结果一张 1000px 宽的公式被画成 720px，又糊又大。
        """
        try:
            point_size = float(self._cursor().charFormat().fontPointSize() or 0)
        except Exception:
            point_size = 0.0
        if point_size <= 0:
            point_size = float(self.doc_editor.font().pointSizeF() or 11.0)
        body_px = point_size * 96.0 / 72.0
        natural_w = image.width() * 96.0 / float(self.MATH_DPI)
        natural_h = image.height() * 96.0 / float(self.MATH_DPI)
        factor = body_px / (12.0 * 96.0 / 72.0)
        # 行间公式只比正文大一点点（以前 ×1.25 显得特别大，跟正文不在一个节奏上）
        factor *= 1.08 if display else 1.0
        return natural_w * factor, natural_h * factor

    def _style_document(self) -> None:
        """把整篇排成「一页纸」：正文行距 1.5、段间距、页边距（Word 的感觉）。"""
        document = self.doc_editor.document()
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        block = document.begin()
        changed = False
        while block.isValid():
            fmt = block.blockFormat()
            # 只补默认值，别覆盖用户自己设的对齐 / 缩进 / 标题
            if fmt.headingLevel() == 0:
                if fmt.topMargin() < 1:
                    fmt.setTopMargin(6)
                if fmt.bottomMargin() < 1:
                    fmt.setBottomMargin(10)
            if fmt.lineHeight() <= 0:
                fmt.setLineHeight(
                    150.0, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            probe = QTextCursor(block)
            probe.setBlockFormat(fmt)
            changed = True
            block = block.next()
        cursor.endEditBlock()
        if changed:
            self.doc_editor.update()

    def _insert_math_object(self, latex: str, display: bool) -> bool:
        if self._math_display == "source":
            return self._insert_math_text(latex, display)
        path = latex_math.render(latex, display, dpi=self.MATH_DPI,
                                 color=str(theme.TEXT))
        if not path:
            self._set_status("公式渲染失败，请检查 LaTeX 语法", "error")
            return False
        image = QImage(path)
        if image.isNull():
            return False
        width, height = self._math_display_size(image, display)
        limit = max(200.0, float(self.doc_editor.viewport().width()) - 60.0)
        if width > limit:
            rate = limit / width
            width, height = limit, height * rate
        name = self._register_math_resource(latex, display, width, height)
        if not name:
            return False
        fmt = QTextImageFormat()
        fmt.setName(name)
        fmt.setWidth(width)
        fmt.setHeight(height)
        try:
            # 行内公式跟文字**居中在同一行**（以前贴着行底，看着「浮」在文字上面）
            fmt.setVerticalAlignment(
                QTextCharFormat.VerticalAlignment.AlignMiddle)
        except Exception:
            pass
        cursor = self._cursor()
        if display:
            cursor.insertBlock()
            block_format = cursor.blockFormat()
            # 行间公式跟 PDF 里一样居中
            block_format.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            cursor.setBlockFormat(block_format)
            cursor.insertImage(fmt)
            cursor.insertBlock()
            plain = QTextBlockFormat()
            plain.setAlignment(Qt.AlignmentFlag.AlignLeft)
            cursor.setBlockFormat(plain)
        else:
            cursor.insertImage(fmt)
        self._style_document()
        self.doc_editor.setFocus()
        return True

    def _insert_math_text(self, latex: str, display: bool) -> bool:
        """源码模式：公式以文本形式插入（保留下标属性，导出仍还原成公式）。"""
        fmt = QTextCharFormat()
        font = QFont()
        font.setFamilies(MONO_STACK)
        font.setPointSizeF(10.5)
        fmt.setFont(font)
        fmt.setForeground(QColor(theme.ACCENT))
        fmt.setBackground(QColor(theme.soft(theme.ACCENT, 0.10)))
        fmt.setProperty(latex_export.MATH_TEXT_PROPERTY,
                        ("d|" if display else "i|") + latex)
        cursor = self._cursor()
        if display:
            cursor.insertBlock()
            cursor.insertText(latex, fmt)
            cursor.insertBlock()
        else:
            cursor.insertText(latex, fmt)
        self.doc_editor.setFocus()
        return True

    def toggle_math_display(self) -> None:
        """在「渲染图」和「源码文本」之间整体切换，已有公式一起转换。"""
        target = "source" if self._math_display == "image" else "image"
        converted = self._convert_math_objects(target)
        self._math_display = target
        self.store.set_setting("latex_math_display", target)
        self.math_mode_button.setText("公式：源码" if target == "source" else "公式：图")
        self._set_status(f"公式显示已切到{'源码' if target == 'source' else '渲染图'}"
                        f"（转换 {converted} 个）", "ok")
        self._on_document_changed()

    def _convert_math_objects(self, target: str) -> int:
        """把文档里的公式对象统一换成图片或文本。"""
        from PySide6.QtGui import QTextDocument

        document = self.doc_editor.document()
        items = []      # (position, length, latex, display)
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    start = fragment.position()
                    length = fragment.length()
                    fmt = fragment.charFormat()
                    payload = None
                    if fmt.isImageFormat():
                        payload = latex_export.decode_math(fmt.toImageFormat().name())
                    else:
                        payload = latex_export.math_text_value(fmt)
                    if payload is not None:
                        items.append((start, length, payload[0], payload[1]))
                iterator += 1
            block = block.next()

        converted = 0
        cursor = QTextCursor(document)
        for start, length, latex, display in reversed(items):
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
            if target == "source":
                fmt = QTextCharFormat()
                font = QFont()
                font.setFamilies(MONO_STACK)
                font.setPointSizeF(10.5)
                fmt.setFont(font)
                fmt.setForeground(QColor(theme.ACCENT))
                fmt.setBackground(QColor(theme.soft(theme.ACCENT, 0.10)))
                fmt.setProperty(latex_export.MATH_TEXT_PROPERTY,
                                ("d|" if display else "i|") + latex)
                cursor.insertText(latex, fmt)
            else:
                path = latex_math.render(latex, display)
                name = self._register_math_resource(latex, display) if path else None
                if not name or path is None:
                    continue
                image = QImage(path)
                width = image.width() * 0.72
                height = image.height() * 0.72
                if display:
                    width, height = width * 1.25, height * 1.25
                limit = 520.0
                if width > limit:
                    rate = limit / width
                    width, height = limit, height * rate
                image_format = QTextImageFormat()
                image_format.setName(name)
                image_format.setWidth(width)
                image_format.setHeight(height)
                cursor.insertImage(image_format)
            converted += 1
        return converted

    def open_inline_math(self, display: bool = False) -> None:
        """在光标处弹出小输入框，Enter 插入行内、Shift+Enter 插入行间。"""
        popup = latex_mathdialog.InlineMathPopup(self, display)
        rect = self.doc_editor.cursorRect()
        point = self.doc_editor.viewport().mapToGlobal(rect.bottomLeft())
        popup.move(point.x(), point.y() + 4)
        if popup.exec() == QDialog.DialogCode.Accepted:
            self._insert_math_object(popup.latex(), popup.is_display())
        self.doc_editor.setFocus()

    def open_math(self, display: bool) -> None:
        dialog = latex_mathdialog.MathDialog(self, "", display)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._insert_math_object(dialog.latex(), dialog.is_display())

    def _open_matrix(self) -> None:
        dialog = latex_matrix.MatrixDialog(self, math_wrap=False)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            code = dialog.generated_code().strip()
            self._insert_math_object(code, "\n" in code)

    def _open_greek(self) -> None:
        picker = GreekPicker(self)
        cursor_rect = self.doc_editor.cursorRect()
        picker.move(self.doc_editor.mapToGlobal(cursor_rect.bottomLeft()))
        picker.picked.connect(self._insert_greek)
        picker.exec()

    def _insert_greek(self, command: str) -> None:
        self._insert_math_object(command, False)

    def _insert_table(self) -> None:
        rows, cols = 3, 3
        cursor = self._cursor()
        table = cursor.insertTable(rows, cols)
        headers = ["列 1", "列 2", "列 3"]
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.DemiBold)
        for column in range(cols):
            try:
                cell = table.cellAt(0, column)
                cell_cursor = cell.firstCursorPosition()
                cell_cursor.insertText(headers[column], fmt)
            except Exception:
                continue
        self.doc_editor.setFocus()

    def _insert_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", self._last_dir or "",
            "图片 (*.png *.jpg *.jpeg *.pdf *.eps)")
        if not path:
            return
        fmt = QTextImageFormat()
        fmt.setName(path)
        fmt.setWidth(300)
        fmt.setHeight(190)
        self._cursor().insertImage(fmt)
        self.doc_editor.setFocus()

    def _insert_raw(self) -> None:
        dialog = latex_mathdialog.SourceInsertDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        text = dialog.text().strip()
        if not text:
            return
        self.set_mode("source")
        cursor = self.source_editor.textCursor()
        cursor.insertText("\n" + text + "\n")
        self.source_editor.setTextCursor(cursor)

    # -------------------------------------------------- 公式编辑
    def _edit_math_at(self, latex: str, display: bool, position: int) -> None:
        dialog = latex_mathdialog.MathDialog(self, latex, display)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        cursor = QTextCursor(self.doc_editor.document())
        cursor.setPosition(position)
        cursor.movePosition(QTextCursor.MoveOperation.Right,
                            QTextCursor.MoveMode.KeepAnchor, 1)
        cursor.removeSelectedText()
        self.doc_editor.setTextCursor(cursor)
        self._insert_math_object(dialog.latex(), dialog.is_display())

    # -------------------------------------------------- 编译
    def _current_source(self) -> str:
        if self._mode == "source":
            body = self.source_editor.toPlainText()
        else:
            body = latex_export.document_to_latex(self.doc_editor.document())
        if "\\documentclass" in body:
            return body
        return latex_builder.build_preview_document(body, self._packages,
                                                    self._extra_preamble)

    def _auto_compile_now(self) -> None:
        """自动编译：页面不可见就跳过，避免后台一直调 xelatex。"""
        if not self.isVisible() or self._compiling:
            return
        self.compile_now()

    def _show_preview_hint(self, text: str) -> None:
        """预览区没有 PDF 时显示一句说明（而不是一块灰）。"""
        try:
            self.preview_hint.setText(text)
            self.preview_stack.setCurrentWidget(self.preview_hint)
        except Exception:
            pass

    def compile_now(self) -> None:
        if self._compiling:
            return
        if not latex_compile.engine_available():
            self._set_status("没有找到 xelatex，请先安装 MiKTeX 或 TeX Live", "error")
            self._show_preview_hint(
                "没找到 xelatex。\n装好 MiKTeX 或 TeX Live 后重启应用即可预览。")
            return
        self._compiling = True
        self._set_status("正在编译…")
        self._show_preview_hint("正在编译…\n第一次编译要装字体和宏包，可能要十几秒。")
        self._compiler.compile(self._current_source())

    def _on_compiled(self, ok: bool, pdf_path: str, message: str) -> None:
        self._compiling = False
        if not ok:
            self._set_status(message or "编译失败", "error")
            self._show_preview_hint(
                "编译失败：\n" + (message or "看看源码里有没有写错的命令"))
            return
        if self.pdf_doc is None:
            self._set_status("编译成功，但当前环境无法显示 PDF", "warn")
            self._show_preview_hint("编译成功了，但当前环境缺少 QtPdf，没法在这里显示。")
            return
        try:
            self.pdf_doc.close()
            error = self.pdf_doc.load(pdf_path)
            if error != self.pdf_doc.Error.None_:
                self._set_status(f"PDF 打开失败：{error}", "error")
                self._show_preview_hint(f"PDF 打不开：{error}")
                return
            self.pdf_view.setDocument(self.pdf_doc)
            self.preview_stack.setCurrentWidget(self.pdf_view)
            self._sync_page_label(0)
            self._set_status(f"编译成功 · {Path(pdf_path).name}", "ok")
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"预览出错：{exc}", "error")
            self._show_preview_hint(f"预览出错：{exc}")

    def _sync_page_label(self, page: int = 0) -> None:
        if self.pdf_doc is None:
            return
        try:
            total = self.pdf_doc.pageCount()
            current = self.pdf_view.pageNavigator().currentPage() + 1
        except Exception:
            total, current = 0, 0
        self.page_label.setText(f"{current}/{total}" if total else "—")

    def _flip_page(self, delta: int) -> None:
        if self.pdf_doc is None:
            return
        try:
            navigator = self.pdf_view.pageNavigator()
            target = max(0, min(self.pdf_doc.pageCount() - 1,
                                navigator.currentPage() + delta))
            # Qt 6.10 里叫 jump()，老版本叫 jumpTo()，两个都试一下
            jump = getattr(navigator, "jump", None) or getattr(navigator, "jumpTo", None)
            if jump is not None:
                jump(target, QPointF(0.0, 0.0))
        except Exception:
            pass

    def _on_auto_toggled(self, checked: bool) -> None:
        self._auto_compile = bool(checked)
        self.store.set_setting("latex_auto", "1" if checked else "0")
        if checked:
            self._auto_timer.start(400)

    # -------------------------------------------------- 宏包 / 文件
    def _load_packages(self) -> list:
        saved = self.store.get_setting("latex_packages", "") or ""
        if not saved:
            return list(latex_builder.DEFAULT_PACKAGES)
        return [item for item in saved.split(",") if item]

    def _pick_packages(self) -> None:
        from .latex_packages import PackagesDialog

        dialog = PackagesDialog(self, self._packages)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._packages = dialog.selected_ids()
        self.store.set_setting("latex_packages", ",".join(self._packages))
        self._set_status("宏包已更新：" + "、".join(self._packages), "ok")
        self.compile_now()

    def _copy_latex(self) -> None:
        from PySide6.QtWidgets import QApplication

        body = latex_export.document_to_latex(self.doc_editor.document())
        QApplication.clipboard().setText(
            latex_builder.build_preview_document(body, self._packages,
                                                self._extra_preamble))
        self._set_status("已复制整份 LaTeX（含导言区）", "ok")

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开文件", self._last_dir or "",
            "LaTeX 或 HTML (*.tex *.txt *.html *.htm);;所有文件 (*.*)")
        if not path:
            return
        target = Path(path)
        try:
            text = target.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"打开失败：{exc}", "error")
            return
        self._last_dir = str(target.parent)
        self.store.update_settings({"latex_dir": self._last_dir})
        if target.suffix.lower() in (".tex", ".txt"):
            self.source_editor.blockSignals(True)
            self.source_editor.setPlainText(text)
            self.source_editor.blockSignals(False)
            self.set_mode("source")
        else:
            self.doc_editor.setHtml(text)
            self._style_document()
        self._set_status(f"已打开 {target.name}", "ok")
        if self._auto_compile:
            self._auto_timer.start(400)

    def save_file(self) -> None:
        body = latex_export.document_to_latex(self.doc_editor.document())
        document = latex_builder.build_preview_document(body, self._packages,
                                                       self._extra_preamble)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 LaTeX", self._last_dir or "document.tex", "LaTeX (*.tex)")
        if not path:
            return
        if not path.lower().endswith(".tex"):
            path += ".tex"
        try:
            Path(path).write_text(document, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"保存失败：{exc}", "error")
            return
        self._last_dir = str(Path(path).parent)
        self.store.update_settings({"latex_dir": self._last_dir})
        self._set_status(f"已保存到 {path}", "ok")

    # -------------------------------------------------- 存取
    def _load_document(self) -> None:
        html = self.store.get_setting("latex_doc_html", "") or ""
        if html.strip():
            self.doc_editor.blockSignals(True)
            self.doc_editor.setHtml(html)
            self.doc_editor.blockSignals(False)
            self._reregister_math_resources()
            self._style_document()
        else:
            self._load_sample()
            self._style_document()
        self.source_editor.blockSignals(True)
        self.source_editor.setPlainText(
            self.store.get_setting("latex_source", "") or "")
        self.source_editor.blockSignals(False)
        if not self.source_editor.toPlainText().strip():
            self._refresh_source_from_document(quiet=True)

    def _reregister_math_resources(self) -> None:
        """HTML 里只存了公式代码，图片资源需要重新挂上。"""
        document = self.doc_editor.document()
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    if fmt.isImageFormat():
                        image_fmt = fmt.toImageFormat()
                        payload = latex_export.decode_math(image_fmt.name())
                        if payload is not None:
                            self._register_math_resource(
                                payload[0], payload[1],
                                image_fmt.width(), image_fmt.height())
                iterator += 1
            block = block.next()

    def _load_sample(self) -> None:
        """首次使用时的示例：标题 + 正文 + 行间公式 + 有序列表。"""
        cursor = self.doc_editor.textCursor()
        title_format = QTextCharFormat()
        title_format.setFontPointSize(HEADING_SIZES[2])
        title_format.setFontWeight(QFont.Weight.DemiBold)
        cursor.insertText("特征值与特征向量", title_format)
        block_format = cursor.blockFormat()
        block_format.setHeadingLevel(2)
        cursor.setBlockFormat(block_format)
        cursor.insertBlock()
        # 新段落回到正文样式（否则整篇都会被当成标题）
        plain_block = QTextBlockFormat()
        plain_block.setHeadingLevel(0)
        plain_block.setLeftMargin(0)
        plain_block.setIndent(0)
        cursor.setBlockFormat(plain_block)

        body_format = QTextCharFormat()
        body_format.setFontPointSize(11.0)
        cursor.insertText(
            "设 A 为 n 阶方阵。若存在数 λ 与非零向量 x，使 Ax = λx，则称 λ 为 A 的"
            "特征值，x 为对应的特征向量。", body_format)
        cursor.insertBlock()
        cursor.insertText("特征多项式由下面的行列式给出：", body_format)
        self.doc_editor.setTextCursor(cursor)
        self._insert_math_object(r"\det(A-\lambda I)=0", True)

        cursor = self.doc_editor.textCursor()
        cursor.insertText("求解步骤：", body_format)
        cursor.insertBlock()
        list_format = QTextListFormat()
        list_format.setStyle(QTextListFormat.Style.ListDecimal)
        list_format.setIndent(1)
        cursor.createList(list_format)
        cursor.insertText("展开特征多项式得到特征方程", body_format)
        cursor.insertBlock()
        cursor.insertText("解方程得到特征值，再代回求特征向量", body_format)
        self.doc_editor.setTextCursor(cursor)

    def _save_document(self) -> None:
        self.store.update_settings({
            "latex_doc_html": self.doc_editor.toHtml(),
            "latex_source": self.source_editor.toPlainText(),
        })

    # -------------------------------------------------- 状态
    def _set_status(self, text: str, tone: str = "info") -> None:
        color = {"ok": theme.SUCCESS, "error": theme.DANGER,
                 "warn": theme.WARNING}.get(tone, theme.MUTED)
        self.status_label.setText((text or "").splitlines()[0])
        self.status_label.setToolTip(text or "")
        self.status_label.setStyleSheet(f"color: {color};")

    def showEvent(self, event):
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            if self._auto_compile:
                self._auto_timer.start(500)
