"""备忘录页：左侧列表 + 右侧富文本编辑器（加粗/斜体/字号/颜色/待办清单）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QKeySequence, QShortcut, QTextBlockFormat, QTextCharFormat,
    QTextCursor, QTextListFormat,
)
from PySide6.QtWidgets import (
    QColorDialog, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)

from . import icons, theme
from .widgets import Card, HScrollRow, IconButton, Select, SoftButton, make_scroll

# 正文 / 标题的默认字号（用户规格：标题 16 加粗，正文 10 不加粗）
BODY_SIZE = 10.0
TITLE_SIZE = 16.0

STYLE_ITEMS = ["正文", "标题", "副标题", "一级小标题", "二级小标题", "自定义"]
STYLE_SIZES = {
    "正文": (BODY_SIZE, QFont.Weight.Normal),
    "标题": (TITLE_SIZE, QFont.Weight.DemiBold),
    "副标题": (12.0, QFont.Weight.DemiBold),
    "一级小标题": (11.0, QFont.Weight.DemiBold),
    "二级小标题": (10.5, QFont.Weight.DemiBold),
}

CHECK_OPEN = "○"
CHECK_DONE = "●"
FONT_SIZES = [10, 11, 12, 13, 14, 16, 18, 20, 24]


class MemoListItem(QWidget):
    """左侧列表里的一条备忘。"""

    clicked = Signal(int)

    def __init__(self, memo: Dict[str, Any], active: bool = False, parent=None):
        super().__init__(parent)
        self.memo = memo
        self._active = active
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        title = str(memo.get("title") or "").strip() or "无标题备忘"
        self.title_label = QLabel(title)
        font = QFont(self.font())
        font.setPointSizeF(10.0)
        font.setWeight(QFont.Weight.DemiBold if active else QFont.Weight.Normal)
        self.title_label.setFont(font)
        self.title_label.setStyleSheet(f"color: {theme.TEXT};")
        layout.addWidget(self.title_label)

        updated = str(memo.get("updated_at", ""))[:16]
        snippet = _plain_text(str(memo.get("html", ""))).strip().replace("\n", " ")
        meta = QLabel(f"{updated} · {snippet[:40] or '（空）'}")
        meta.setObjectName("Tiny")
        layout.addWidget(meta)

    def set_active(self, active: bool) -> None:
        if self._active == active:
            return
        self._active = active
        font = self.title_label.font()
        font.setWeight(QFont.Weight.DemiBold if active else QFont.Weight.Normal)
        self.title_label.setFont(font)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(int(self.memo.get("id", 0)))
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QPen
        from .widgets import rounded_path
        from PySide6.QtCore import QRectF

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 内缩 1.5px：圆角描边完整落在控件内，不会被父容器裁掉上边缘
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = rounded_path(rect, 9)
        if self._active:
            fill = QColor(theme.ACCENT)
            fill.setAlphaF(0.12)
            painter.fillPath(path, fill)
            painter.setPen(QPen(QColor(theme.ACCENT), 1.0))
            painter.drawPath(path)
        else:
            painter.fillPath(path, QColor(theme.CARD))
            painter.setPen(QPen(QColor(theme.BORDER), 1.0))
            painter.drawPath(path)
        painter.end()


def _plain_text(html: str) -> str:
    from PySide6.QtGui import QTextDocument

    document = QTextDocument()
    document.setHtml(html or "")
    return document.toPlainText()


class MemosPage(QWidget):
    """备忘录页面。"""

    data_changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._current_id: Optional[int] = None
        self._loading = False
        self._items: List[MemoListItem] = []

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(700)
        self._save_timer.timeout.connect(self._save_current)

        # 粘贴多行文本时，整篇会继承标题格式；停手后再纠正回来
        self._format_timer = QTimer(self)
        self._format_timer.setSingleShot(True)
        self._format_timer.setInterval(280)
        self._format_timer.timeout.connect(self._fix_uniform_title_format)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("备忘录")
        title.setObjectName("H1")
        self.subtitle = QLabel("随手记：支持加粗、斜体、字号、颜色和待办清单，自动保存")
        self.subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.new_button = SoftButton("新建备忘", "primary", height=34, min_width=118,
                                     glyph=icons.ADD, point_size=8.8)
        self.new_button.clicked.connect(self.new_memo)
        header.addWidget(self.new_button)
        self.delete_button = SoftButton("删除", "ghost", height=34, min_width=84,
                                        glyph=icons.DELETE, point_size=8.8)
        self.delete_button.clicked.connect(self.delete_current)
        header.addWidget(self.delete_button)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)

        # ---------------- 左侧列表
        list_card = Card(radius=14, fill=theme.CARD, border=theme.BORDER, padding=10,
                         spacing=6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索备忘…")
        self.search_edit.setFixedHeight(32)
        self.search_edit.textChanged.connect(lambda _t: self.reload())
        list_card.body.addWidget(self.search_edit)
        self.scroll, _body, self.list_layout = make_scroll()
        self.list_layout.setContentsMargins(0, 0, 2, 0)
        list_card.body.addWidget(self.scroll, 1)
        # 左列给个下限，否则 splitter 会把它压成一条缝（搜索框/标题都被截）
        list_card.setMinimumWidth(250)
        list_card.setMaximumWidth(420)
        splitter.addWidget(list_card)

        # ---------------- 右侧编辑器
        editor_card = Card(radius=14, fill=theme.CARD, border=theme.BORDER, padding=10,
                           spacing=8)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("名称（留空就用正文第一行）")
        self.title_edit.setFixedHeight(34)
        title_font = QFont(self.title_edit.font())
        title_font.setPointSizeF(11.0)
        title_font.setWeight(QFont.Weight.DemiBold)
        self.title_edit.setFont(title_font)
        self.title_edit.textChanged.connect(self._on_title_changed)
        editor_card.body.addWidget(self.title_edit)
        editor_card.body.addWidget(self._build_toolbar())

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(True)
        self.editor.setStyleSheet(
            f"QTextEdit {{ background: {theme.INPUT_BG}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 10px; padding: 12px; }}"
        )
        body_font = QFont()
        body_font.setFamilies(theme.font_families())
        body_font.setPointSizeF(11.0)
        self.editor.setFont(body_font)
        self.editor.document().setDocumentMargin(10)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.mousePressEvent = self._editor_mouse_press  # 点方框切换待办
        self.editor.keyPressEvent = self._editor_key_press      # 标题行回车接正文
        try:
            self.editor.cursorPositionChanged.connect(self._sync_toolbar_state)
            self.editor.currentCharFormatChanged.connect(
                lambda _fmt: self._sync_toolbar_state())
        except Exception:
            pass
        editor_card.body.addWidget(self.editor, 1)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        editor_card.body.addWidget(self.status)
        splitter.addWidget(editor_card)
        splitter.setSizes([300, 760])
        root.addWidget(splitter, 1)

        self.reload()
        self._select_first()

    # -------------------------------------------------- 工具栏
    def _tool(self, text: str, tip: str, handler, width: int = 42) -> SoftButton:
        button = SoftButton(text, "ghost", height=30, min_width=width, point_size=8.8)
        button.setToolTip(tip)
        button.clicked.connect(handler)
        # 记住按钮，光标移动时同步「按下」状态（不然看不出有没有生效）
        if not hasattr(self, "_tools"):
            self._tools = {}
        self._tools[text.replace(" ", "")] = button
        return button

    def _build_toolbar(self) -> QWidget:
        # 分栏变窄时工具条放不下 → 横向滚动，而不是叠在一起
        bar = HScrollRow(height=36, spacing=6)
        row = bar

        self.style_select = Select()
        self.style_select.setFixedWidth(112)
        for name in STYLE_ITEMS:
            self.style_select.addItem(name)
        self.style_select.activated.connect(self._on_style)
        row.addWidget(self.style_select)

        row.addWidget(self._tool("B", "加粗（Ctrl+B）", self._toggle_bold, 38))
        row.addWidget(self._tool("I", "斜体（Ctrl+I）", self._toggle_italic, 38))
        row.addWidget(self._tool("U", "下划线（Ctrl+U）", self._toggle_underline, 38))
        self.size_select = Select()
        self.size_select.setFixedWidth(88)
        for size in FONT_SIZES:
            self.size_select.addItem(f"{size} pt")
        self.size_select.setCurrentIndex(FONT_SIZES.index(11))
        self.size_select.activated.connect(self._on_size)
        row.addWidget(self.size_select)
        row.addWidget(self._tool("A", "文字颜色", self._pick_color, 38))
        row.addWidget(self._tool("▍", "高亮颜色", self._pick_highlight, 38))
        row.addSpacing(6)
        row.addWidget(self._tool("• 列表", "无序列表", lambda: self._toggle_list(False), 62))
        row.addWidget(self._tool("1. 列表", "有序列表", lambda: self._toggle_list(True), 66))
        row.addWidget(self._tool(f"{CHECK_OPEN} 待办", "插入待办项（点方框可勾选）",
                                 self._insert_todo, 70))
        row.addWidget(self._tool("清除格式", "清除文字格式", self._clear_format, 74))
        row.addStretch(1)
        return bar

    # -------------------------------------------------- 列表
    def reload(self) -> None:
        from .widgets import clear_layout

        clear_layout(self.list_layout)
        self._items = []
        keyword = self.search_edit.text().strip().lower()
        memos = self.store.list_memos()
        for memo in memos:
            text = (str(memo.get("title", "")) + " " + _plain_text(str(memo.get("html", "")))).lower()
            if keyword and keyword not in text:
                continue
            item = MemoListItem(memo, active=(int(memo["id"]) == self._current_id))
            item.clicked.connect(self.select_memo)
            self.list_layout.addWidget(item)
            self._items.append(item)
        if not self._items:
            empty = QLabel("还没有备忘，点右上角「新建备忘」")
            empty.setObjectName("Tiny")
            empty.setWordWrap(True)
            self.list_layout.addWidget(empty)
        self.list_layout.addStretch(1)

    def _select_first(self) -> None:
        memos = self.store.list_memos()
        if memos:
            self.select_memo(int(memos[0]["id"]))
        else:
            self.new_memo()

    def select_memo(self, memo_id: int) -> None:
        memo = self.store.get_memo(int(memo_id))
        if memo is None:
            return
        self._save_current()
        self._current_id = int(memo_id)
        self._loading = True
        self.editor.setHtml(str(memo.get("html", "")))
        self._apply_default_styles()
        # 名称：库里存的和第一行一样就当作「跟随第一行」，输入框留空
        stored = str(memo.get("title", "")).strip()
        first = self._first_line()
        custom = stored if stored and stored != first else ""
        self.title_edit.setText(custom)
        self._loading = False
        for item in self._items:
            item.set_active(int(item.memo.get("id", 0)) == self._current_id)
        self.status.setText("")
        self._sync_list_name()

    def new_memo(self) -> None:
        memo_id = self.store.add_memo("新备忘", "")
        self.reload()
        self.select_memo(memo_id)
        self.title_edit.clear()
        self._apply_default_styles()
        self._focus_title_line()
        self.editor.setFocus()

    def delete_current(self) -> None:
        if self._current_id is None:
            return
        self.store.delete_memo(self._current_id)
        self._current_id = None
        self.reload()
        self._select_first()

    # -------------------------------------------------- 保存
    def _on_title_changed(self, _text: str) -> None:
        if self._loading:
            return
        self._sync_list_name()
        self._save_timer.start()

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._sync_list_name()
        self._save_timer.start()
        self._format_timer.start()

    def _fix_uniform_title_format(self) -> None:
        """整篇都被标题格式「污染」时（多行粘贴最常见），把第一行之后的恢复成正文。

        只在「第一行之后的段落全是标题格式」时才动手，避免覆盖用户手动改过的某一行。
        """
        if self._loading or self._current_id is None:
            return
        from PySide6.QtGui import QTextFormat

        document = self.editor.document()
        blocks = []
        block = document.firstBlock()
        while block.isValid():
            blocks.append(block)
            block = block.next()
        if len(blocks) < 3:          # 标题 + 至少两行正文才判断
            return

        def is_title_format(target) -> bool:
            fragment = target.begin()
            if fragment.atEnd():
                return False
            fmt = fragment.fragment().charFormat()
            return (fmt.hasProperty(QTextFormat.Property.FontPointSize)
                    and fmt.fontPointSize() >= TITLE_SIZE - 0.6
                    and fmt.fontWeight() >= QFont.Weight.DemiBold)

        rest = blocks[1:]
        if not all(is_title_format(item) for item in rest):
            return
        self._loading = True
        try:
            for target_block in rest:
                self._format_block(target_block, self._body_format())
        finally:
            self._loading = False
        self._save_timer.start()
        self.status.setText("已把标题之后的内容恢复成正文格式")

    def _sync_list_name(self) -> None:
        """列表里的名字：没填自定义名称时，跟着正文第一行走。"""
        title = self._resolved_title()
        for item in self._items:
            if int(item.memo.get("id", 0)) == self._current_id:
                item.title_label.setText(title)
                item.memo["title"] = title

    def _save_current(self) -> None:
        if self._current_id is None:
            return
        title = self._resolved_title()
        self.store.update_memo(self._current_id,
                               title=title,
                               html=self.editor.toHtml())
        import time as _time

        self.status.setText("已保存 · " + _time.strftime("%H:%M:%S"))
        for item in self._items:
            if int(item.memo.get("id", 0)) == self._current_id:
                item.memo["title"] = title
                item.memo["html"] = self.editor.toHtml()
                item.title_label.setText(title)

    # -------------------------------------------------- 文字格式
    def _target_name(self) -> str:
        """状态提示用：选中了几个字 / 当前段落。"""
        cursor = self.editor.textCursor()
        count = len(cursor.selectedText()) if cursor.hasSelection() else 0
        return f"所选 {count} 个字" if count else "当前段落"

    def _merge(self, fmt: QTextCharFormat, label: str = "") -> None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            # 没选中就作用在当前段落上，否则「点了一下但看不出来」
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)
        self.editor.setFocus()
        if label:
            self.status.setText(f"已把「{label}」应用到{self._target_name()}")
        self._sync_toolbar_state()

    def _toggle_bold(self) -> None:
        current = self.editor.textCursor().charFormat()
        bold = current.fontWeight() < 600
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        self._merge(fmt, "加粗" if bold else "取消加粗")

    def _toggle_italic(self) -> None:
        italic = not self.editor.textCursor().charFormat().fontItalic()
        fmt = QTextCharFormat()
        fmt.setFontItalic(italic)
        self._merge(fmt, "斜体" if italic else "取消斜体")

    def _toggle_underline(self) -> None:
        underline = not self.editor.textCursor().charFormat().fontUnderline()
        fmt = QTextCharFormat()
        fmt.setFontUnderline(underline)
        self._merge(fmt, "下划线" if underline else "取消下划线")

    def _on_style(self, _index: int = -1) -> None:
        """一键套用字号/字重：作用于选中段落（或光标所在段落）。"""
        name = STYLE_ITEMS[self.style_select.currentIndex()]
        if name == "自定义":
            self.size_select.setFocus()
            return
        size, weight = STYLE_SIZES[name]
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        fmt.setFontWeight(weight)
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.mergeCharFormat(fmt)
        else:
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.mergeCharFormat(fmt)
            cursor.clearSelection()
            self.editor.setTextCursor(cursor)
        self.editor.mergeCurrentCharFormat(fmt)
        self.editor.setFocus()
        self.status.setText(f"已套用「{name}」（{int(size)} pt）到{self._target_name()}")
        self._sync_toolbar_state()

    def _sync_toolbar_state(self) -> None:
        """让工具栏跟着光标走：B/I/U 是否按下、字号、样式下拉。"""
        if getattr(self, "_loading", False) or not hasattr(self, "_tools"):
            return
        try:
            fmt = self.editor.textCursor().charFormat()
            for key, active in (("B", fmt.fontWeight() >= 600),
                                ("I", bool(fmt.fontItalic())),
                                ("U", bool(fmt.fontUnderline()))):
                button = self._tools.get(key)
                if button is not None:
                    button.setKind("subtle" if active else "ghost")
            size = fmt.fontPointSize()
            if size > 0 and getattr(self, "size_select", None) is not None:
                index = min(range(len(FONT_SIZES)),
                            key=lambda i: abs(FONT_SIZES[i] - size))
                self.size_select.blockSignals(True)
                self.size_select.setCurrentIndex(index)
                self.size_select.blockSignals(False)
            if getattr(self, "style_select", None) is not None:
                bold = fmt.fontWeight() >= 600
                match = "自定义"
                for name, (value, _weight) in STYLE_SIZES.items():
                    if abs(size - value) < 0.6 and (name == "正文") != bold:
                        match = name
                        break
                self.style_select.blockSignals(True)
                self.style_select.setCurrentIndex(STYLE_ITEMS.index(match))
                self.style_select.blockSignals(False)
        except Exception:
            pass

    def _on_size(self, _index: int = -1) -> None:
        try:
            size = float(FONT_SIZES[self.size_select.currentIndex()])
        except (ValueError, IndexError):
            return
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        self._merge(fmt, f"字号 {int(size)} pt")

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(theme.TEXT), self, "选择文字颜色")
        if not color.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        self._merge(fmt, "文字颜色")

    def _pick_highlight(self) -> None:
        color = QColorDialog.getColor(QColor(theme.soft(theme.ACCENT, 0.25)), self,
                                      "选择高亮颜色")
        if not color.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setBackground(color)
        self._merge(fmt, "高亮")

    def _toggle_list(self, ordered: bool) -> None:
        cursor = self.editor.textCursor()
        style = (QTextListFormat.Style.ListDecimal if ordered
                 else QTextListFormat.Style.ListDisc)
        current = cursor.currentList()
        if current is not None and current.format().style() == style:
            block_format = QTextBlockFormat()
            block_format.setIndent(0)
            block_format.setObjectIndex(-1)
            cursor.setBlockFormat(block_format)
            self.status.setText("已取消列表")
        else:
            list_format = QTextListFormat()
            list_format.setStyle(style)
            list_format.setIndent(1)
            cursor.createList(list_format)
            self.status.setText("已改成列表")
        self.editor.setFocus()
        self._sync_toolbar_state()

    def _insert_todo(self) -> None:
        """给光标行加待办圆圈；有选区时给每一行都加上。"""
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            cursor.insertText(CHECK_OPEN + " ")
            self.editor.setTextCursor(cursor)
            self.editor.setFocus()
            return

        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        document = self.editor.document()
        block = document.findBlock(start)
        while block.isValid() and block.position() <= end:
            block_cursor = QTextCursor(block)
            text = block.text()
            if not text.lstrip().startswith((CHECK_OPEN, CHECK_DONE)):
                block_cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                block_cursor.insertText(CHECK_OPEN + " ")
                end += len(CHECK_OPEN) + 1
            block = block.next()
        self.editor.setFocus()

    def _clear_format(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Normal)
        fmt.setFontItalic(False)
        fmt.setFontUnderline(False)
        fmt.setForeground(QColor(theme.TEXT))
        fmt.setBackground(Qt.BrushStyle.NoBrush)
        fmt.setFontPointSize(BODY_SIZE)
        self._merge(fmt, "清除格式（回到正文）")

    # -------------------------------------------------- 待办勾选
    def _editor_mouse_press(self, event):
        """点正文里的 ☐/☑ 就切换它。"""
        if event.button() == Qt.MouseButton.LeftButton:
            cursor = self.editor.cursorForPosition(event.position().toPoint())
            document = self.editor.document()
            for position in (cursor.position(), cursor.position() - 1):
                if position < 0:
                    continue
                probe = QTextCursor(document)
                probe.setPosition(position)
                probe.movePosition(QTextCursor.MoveOperation.Right,
                                   QTextCursor.MoveMode.KeepAnchor, 1)
                char = probe.selectedText()
                if char in (CHECK_OPEN, CHECK_DONE):
                    new_char = CHECK_DONE if char == CHECK_OPEN else CHECK_OPEN
                    probe.insertText(new_char)
                    self.editor.setTextCursor(probe)
                    self._save_timer.start()
                    return
        QTextEdit.mousePressEvent(self.editor, event)

    # -------------------------------------------------- 快捷键
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Bold):
            self._toggle_bold()
            return
        if event.matches(QKeySequence.StandardKey.Italic):
            self._toggle_italic()
            return
        if event.matches(QKeySequence.StandardKey.Underline):
            self._toggle_underline()
            return
        super().keyPressEvent(event)

    # -------------------------------------------------- 标题 / 正文默认格式
    @staticmethod
    def _title_format() -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setFontPointSize(TITLE_SIZE)
        fmt.setFontWeight(QFont.Weight.DemiBold)
        return fmt

    @staticmethod
    def _body_format() -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setFontPointSize(BODY_SIZE)
        fmt.setFontWeight(QFont.Weight.Normal)
        return fmt

    def _first_line(self) -> str:
        block = self.editor.document().firstBlock()
        while block.isValid():
            text = block.text().strip()
            if text:
                return text
            block = block.next()
        return ""

    def _resolved_title(self) -> str:
        """列表里显示的名字：用户填了就用它，否则用第一行。"""
        custom = self.title_edit.text().strip()
        if custom:
            return custom
        return self._first_line() or "无标题备忘"

    def _apply_default_styles(self) -> None:
        """第一行 = 标题（加粗、比正文大 3 号），第二行起 = 正文（不加粗、小字号）。

        * 已经是「标题样子」的首行不动；用户手动改过字号/加粗的行也不动；
        * 其余（新建备忘、旧版本存下来的纯文本）按默认格式补齐。
        """
        from PySide6.QtGui import QTextFormat

        document = self.editor.document()
        block = document.firstBlock()
        first = True
        while block.isValid():
            fmt = QTextCharFormat()
            fragment = block.begin()
            if not fragment.atEnd():
                fmt = fragment.fragment().charFormat()
            has_size = fmt.hasProperty(QTextFormat.Property.FontPointSize)
            size = fmt.fontPointSize() if has_size else 0.0
            bold = fmt.fontWeight() >= QFont.Weight.DemiBold
            if first:
                already_title = has_size and size >= TITLE_SIZE - 0.6 and bold
                if not already_title:
                    self._format_block(block, self._title_format())
            else:
                untouched_body = (not has_size or abs(size - BODY_SIZE) < 0.2) and not bold
                if untouched_body:
                    self._format_block(block, self._body_format())
            first = False
            block = block.next()
        # 光标放到末尾，接着写默认是正文
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)
        self.editor.setCurrentCharFormat(self._body_format())

    def _format_block(self, block, fmt: QTextCharFormat) -> None:
        cursor = QTextCursor(block)
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        cursor.mergeCharFormat(fmt)

    def _focus_title_line(self) -> None:
        """新建备忘后把光标放在第一行（标题），输入直接是标题格式。"""
        cursor = QTextCursor(self.editor.document())
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.editor.setTextCursor(cursor)
        self.editor.setCurrentCharFormat(self._title_format())

    def _after_enter(self) -> None:
        """标题行按回车之后：把新的一段设成正文格式。"""
        cursor = self.editor.textCursor()
        block = cursor.block()
        if block.blockNumber() != 1:
            return
        block_cursor = QTextCursor(block)
        block_cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        block_cursor.mergeCharFormat(self._body_format())
        self.editor.setCurrentCharFormat(self._body_format())

    def _editor_key_press(self, event) -> None:
        """在标题行按回车时，接着写的那一行默认是正文。"""
        from PySide6.QtCore import Qt as _Qt

        # 粘贴：按「当前是哪一行」套格式，避免粘贴进来的多行继承标题的大字号
        try:
            if event.matches(QKeySequence.StandardKey.Paste):
                self._paste_as_plain()
                return
        except Exception:
            pass
        is_return = event.key() in (_Qt.Key.Key_Return, _Qt.Key.Key_Enter)
        in_title = self.editor.textCursor().block().blockNumber() == 0
        QTextEdit.keyPressEvent(self.editor, event)
        if is_return and in_title:
            self._after_enter()

    def _paste_as_plain(self) -> None:
        """粘贴纯文本：标题行里贴的算标题，其余算正文。"""
        from PySide6.QtWidgets import QApplication

        text = QApplication.clipboard().text()
        if not text:
            return
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
        in_title = cursor.block().blockNumber() == 0
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for index, line in enumerate(lines):
            if index:
                cursor.insertBlock()
            fmt = self._title_format() if (in_title and index == 0) else self._body_format()
            cursor.insertText(line, fmt)
        self.editor.setTextCursor(cursor)
        self.editor.setCurrentCharFormat(self._body_format())
        self.editor.setFocus()

    def hideEvent(self, event):
        self._save_current()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._current_id is not None:
            memo = self.store.get_memo(self._current_id)
            if memo is None:
                self._current_id = None
                self.reload()
