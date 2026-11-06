# -*- coding: utf-8 -*-
"""对话区的排版：用户气泡在右、助手正文在左、代码块是带头的圆角卡片。

参考用户给的 ChatGPT / Codex 截图：

* 用户消息是一条**靠右的圆角气泡**（浅灰底），不带「我：」这种前缀；
* 助手正文**不带气泡**，直接铺在背景上，行距宽松，标题加粗；
* 代码块是一张**圆角卡片**：上面一行是「</> 语言名」和右边的「复制」按钮，
  下面是等宽代码（自带轻量语法着色，注释灰、字符串绿、关键字紫）；
* 行内代码（``NOTE:``）给一层浅底，看起来像一个小标签；
* 思考过程 / 工具调用各自一张很淡的小卡片。

为什么不用 `QTextBrowser` 一把梭：Qt 的富文本不支持 `border-radius`，
代码块的圆角卡片和气泡必须是真的控件（`QLabel` / `Card` 的样式表才认圆角）。
所以这里用「一列控件」的实现，但对外仍然提供 `toPlainText()`，测试照旧能读文本。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QPixmap, QTextBlockFormat, QTextCharFormat,
    QTextCursor, QSyntaxHighlighter,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QSizePolicy,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import chat_math, theme
from .utils import human_size
from .widgets import Card, SoftButton

#: 用户气泡最宽占对话区的比例
MAX_BUBBLE_RATIO = 0.76
#: 气泡的左右内边距（与 QLabel 样式里的 padding 保持一致）
BUBBLE_PAD_X = 14
#: 气泡的最小宽度（再短的消息也别窄成一条）
BUBBLE_MIN_WIDTH = 38

# 代码着色（One Dark / One Light 的常用色）
SYNTAX = {
    "dark": {"keyword": "#C678DD", "string": "#98C379", "comment": "#7F848E",
             "number": "#D19A66", "func": "#61AFEF", "plain": "#ABB2BF"},
    "light": {"keyword": "#A626A4", "string": "#50A14F", "comment": "#A0A1A7",
              "number": "#986801", "func": "#4078F2", "plain": "#383A42"},
}

KEYWORDS = {
    "import", "from", "as", "def", "class", "return", "if", "elif", "else",
    "for", "while", "in", "not", "and", "or", "is", "None", "True", "False",
    "with", "try", "except", "finally", "raise", "pass", "break", "continue",
    "lambda", "yield", "global", "nonlocal", "assert", "del", "async", "await",
    "self", "int", "float", "bool", "str", "list", "dict", "tuple", "set",
    "void", "char", "const", "static", "struct", "typedef", "unsigned",
    "signed", "long", "short", "double", "sizeof", "extern", "switch", "case",
    "default", "goto", "do", "enum", "union", "volatile", "register",
}
C_KEYWORDS = {
    "__global__", "__shared__", "__device__", "__host__", "__syncthreads",
    "threadIdx", "blockIdx", "blockDim", "gridDim",
}


def colors() -> Dict[str, str]:
    mode = "dark" if theme.effective_mode() == "dark" else "light"
    return theme.PALETTES.get(mode) or theme.PALETTES["light"]


def syntax_colors() -> Dict[str, str]:
    mode = "dark" if theme.effective_mode() == "dark" else "light"
    return SYNTAX.get(mode, SYNTAX["light"])


def mono_font(point_size: float = 10.8) -> QFont:
    """等宽字体：优先 Cascadia / Consolas，中文靠系统字体回退。"""
    families = QFontDatabase.families()
    for name in ("Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas",
                 "Menlo", "DejaVu Sans Mono", "Courier New"):
        if name in families:
            font = QFont(name)
            break
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSizeF(point_size)
    return font


# ---------------------------------------------------------------- 公式渲染（后台）
#: 同时最多跑几个 xelatex（再多也没用，编译本身是瓶颈）
MATH_THREADS = 4
_MATH_POOL = QThreadPool()
_MATH_POOL.setMaxThreadCount(MATH_THREADS)


class _MathJobSignals(QObject):
    done = Signal(str, str)          # 占位符 key, PDF 路径（空串 = 失败）


class _MathJob(QRunnable):
    """后台只跑 xelatex 出 PDF（**不碰任何 Qt 绘图**），转 PNG 回主线程做。"""

    def __init__(self, key: str, latex: str, display: bool, color: str):
        super().__init__()
        self.key = key
        self.latex = latex
        self.display = display
        self.color = color
        self.signals = _MathJobSignals()

    def run(self) -> None:
        path = ""
        try:
            from . import latex_math

            path = latex_math.compile_pdf(self.latex, self.display,
                                          self.color) or ""
        except Exception:
            path = ""
        try:
            self.signals.done.emit(self.key, path)
        except Exception:
            pass


#: 流式生成时提前编译的公式（key → 公式信息）；编译好就写进缓存
_PREFETCH: Dict[str, Dict[str, Any]] = {}


def prefetch_math(parts: List[Dict[str, Any]], color: str) -> None:
    """流式生成时先把**已经出现**的公式丢给后台编译，等回答结束就已经是缓存了。

    用户反馈「渲染太慢」—— 边生成边编译，收尾时公式基本都好了。
    """
    try:
        for index, part in enumerate(parts or []):
            latex = str(part.get("latex") or "")
            display = bool(part.get("display"))
            if not latex or chat_math.cached(latex, display, color):
                continue
            key = "pre-%s-%d-%s" % (part.get("key"), index, color)
            if key in _PREFETCH:
                continue
            _PREFETCH[key] = {"latex": latex, "display": display, "color": color}
            job = _MathJob(key, latex, display, color)
            job.signals.done.connect(_on_prefetch_done)
            _MATH_POOL.start(job)
    except Exception:
        pass


def _on_prefetch_done(key: str, pdf_path: str) -> None:
    info = _PREFETCH.pop(str(key or ""), None)
    if not info or not pdf_path:
        return
    try:
        from . import latex_math

        latex_math.png_from_pdf(pdf_path, info["latex"], info["display"],
                                chat_math.DPI, info["color"])
    except Exception:
        pass


# ---------------------------------------------------------------- Markdown 正文
class MarkdownText(QTextBrowser):
    """助手正文：无边框、透明、高度跟着内容走（不自带滚动条）。"""

    def __init__(self, text: str = "", muted: bool = False, parent=None,
                 math: bool = True):
        super().__init__(parent)
        self._muted = bool(muted)
        self._math = bool(math)
        self._math_count = 0
        self._math_parts: List[Dict[str, Any]] = []
        self._masked = ""
        self._source = ""
        self._math_width = 0
        self._math_queue: List[Dict[str, Any]] = []
        self._math_pending: set = set()
        self._math_failed: set = set()
        self._math_color = ""
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setOpenExternalLinks(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        # 注意：全局样式表里的 `QTextEdit { padding: 6px 10px; border: 1px }`
        # 会按「类型选择器匹配子类」的规则套到 QTextBrowser 上，
        # 白白吃掉左右各 10px、上下各 6px —— 正文因此变窄、还会差几像素
        # 冒出一条内部滚动条。这里把 padding 显式清零。
        self.setStyleSheet("QTextBrowser { background: transparent;"
                           " border: none; padding: 0px; }")
        # 正文字号：比全局默认大一点（用户说「看上去好累」），标题 / 列表跟着它缩放
        body_font = QFont(self.font())
        body_font.setPointSizeF(11.2)
        self.setFont(body_font)
        try:
            self.document().setDefaultFont(body_font)
        except Exception:
            pass
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)
        self.document().setDocumentMargin(0)
        self.set_markdown(text)

    # -------------------------------------------------- 内容
    def set_markdown(self, text: str) -> None:
        """Markdown 排版 + 公式图片（`$…$` / `$$…$$` / 矩阵环境）。"""
        self._source = text or ""
        self._math_parts: List[Dict[str, Any]] = []
        masked_text = self._source
        if chat_math.has_math(self._source):
            masked, parts = chat_math.extract(self._source)
            if self._math:
                self._math_parts = parts
                masked_text = masked
            else:
                # 还在流式生成：公式原样显示，但让后台先把它们编译好
                prefetch_math(parts, colors()["TEXT"])
        self._masked = masked_text
        self._layout_document()

    def _layout_document(self) -> None:
        """按 `_masked` 重新排一遍（宽高变了要重排，公式图片得跟着缩放）。"""
        self.setMarkdown(self._masked or "")
        if self._math_parts:
            self._insert_math(self._math_parts)
        self._restyle()
        self._fit()

    def _insert_math(self, parts: List[Dict[str, Any]]) -> None:
        """把占位符换成公式图片（没渲染好的先显示 `$原文$`，后台并行编译）。

        以前是一个一个排队同步跑 xelatex（0.8 秒/个），一条有十几个公式的回答要等
        十几秒，用户就说「渲染太慢」。现在：已经渲染过的（缓存）立刻插进去；
        没渲染过的丢给 4 个后台线程并行编译，回来一个补一个。
        """
        try:
            color = colors()["TEXT"]
            self._math_color = color
            height = max(12, self.fontMetrics().height() - 2)
            width = max(220, self.viewport().width() or self.width() or 640)
            self._math_width = int(width)
            images, missing = chat_math.collect_cached(parts, color)
            missing = [part for part in missing
                       if str(part.get("key")) not in self._math_failed]
            self._math_count = chat_math.apply(
                self.document(), parts, images, font_height=height,
                max_width=width, dpr=self.devicePixelRatioF())
            self._math_queue = list(missing)
            for part in missing:
                key = str(part.get("key") or "")
                if not key or key in self._math_pending:
                    continue
                self._math_pending.add(key)
                job = _MathJob(key, str(part.get("latex") or ""),
                               bool(part.get("display")), color)
                job.signals.done.connect(self._on_math_compiled)
                _MATH_POOL.start(job)
        except Exception:
            self._math_count = 0
            self._math_queue = []

    def _on_math_compiled(self, key: str, pdf_path: str) -> None:
        """后台编译完了：主线程转 PNG → 重排一次（这次就命缓存了）。"""
        key = str(key or "")
        self._math_pending.discard(key)
        self._math_queue = [part for part in self._math_queue
                            if str(part.get("key")) != key]
        part = next((item for item in self._math_parts
                     if str(item.get("key")) == key), None)
        if not pdf_path or part is None:
            self._math_failed.add(key)
        else:
            try:
                from . import latex_math

                path = latex_math.png_from_pdf(
                    pdf_path, str(part.get("latex") or ""),
                    bool(part.get("display")), chat_math.DPI,
                    getattr(self, "_math_color", "") or colors()["TEXT"])
            except Exception:
                path = None
            if not path:
                self._math_failed.add(key)
        try:
            self._layout_document()
        except Exception:
            pass

    def _rebuild_math(self) -> None:
        """宽度变化后重排一次（只在这条消息里真有公式时才做）。"""
        if not self._math_parts or getattr(self, "_rebuilding", False):
            return
        self._rebuilding = True
        try:
            self._layout_document()
        except Exception:
            pass
        finally:
            self._rebuilding = False

    def chat_text(self) -> str:
        return self.toPlainText()

    # -------------------------------------------------- 排版
    def _restyle(self) -> None:
        colors_ = colors()
        doc = self.document()
        mode = "dark" if theme.effective_mode() == "dark" else "light"
        chip = "#2A2A2D" if mode == "dark" else "#EFEFF2"
        try:
            doc.setDefaultStyleSheet(
                f"a {{ color: {theme.appearance().accent}; text-decoration: none; }}"
                f"code {{ background: {chip}; }}")
        except Exception:
            pass
        base = QColor(colors_["MUTED"] if self._muted else colors_["TEXT"])
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        block = doc.begin()
        while block.isValid():
            block_format = block.blockFormat()
            block_format.setLineHeight(142, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            heading = block_format.headingLevel()
            block_format.setBottomMargin(4 if heading else 8)
            block_format.setTopMargin(10 if heading else 0)
            # 「一行里只有一个公式图片」= 行间公式：居中摆放，像论文里那样
            if self._block_is_image_only(block):
                block_format.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            fixer = QTextCursor(block)
            fixer.setBlockFormat(block_format)
            piece = block.begin()
            while not piece.atEnd():
                fragment = piece.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    if fmt.isImageFormat():
                        piece += 1              # 公式图片：别改它的颜色
                        continue
                    if fmt.fontFixedPitch():
                        # 行内代码：给一层浅底，像个小标签
                        fmt.setBackground(QColor(chip))
                        fmt.setForeground(QColor(colors_["TEXT"]))
                    else:
                        fmt.setForeground(base)
                    if heading:
                        fmt.setFontWeight(QFont.Weight.DemiBold)
                    run = QTextCursor(doc)
                    run.setPosition(fragment.position())
                    run.setPosition(fragment.position() + fragment.length(),
                                    QTextCursor.MoveMode.KeepAnchor)
                    run.setCharFormat(fmt)
                piece += 1
            block = block.next()
        cursor.endEditBlock()

    @staticmethod
    def _block_is_image_only(block) -> bool:
        """这一段里是不是只有一个图片（行间公式）？"""
        try:
            text = "".join(ch for ch in (block.text() or "")
                           if ch not in ("\ufffc", "\u3000", " ", "\t"))
            if text:
                return False
            iterator = block.begin()
            if iterator.atEnd():
                return False
            fragment = iterator.fragment()
            return bool(fragment.isValid()
                        and fragment.charFormat().isImageFormat())
        except Exception:
            return False

    def _fit(self) -> None:
        """按内容算高度。注意：setFixedHeight 会再触发 resizeEvent，
        这里用重入锁，否则内容一长就会互相递归到栈溢出。"""
        if getattr(self, "_fitting", False):
            return
        self._fitting = True
        try:
            width = max(120, self.viewport().width() or self.width())
            doc = self.document()
            doc.setTextWidth(width)
            # +8：QTextDocument 算出来的高度偶尔比实际少几像素，
            # 少 10px 就会把最后一行切掉（用户看到的「留一个不大不小的框」）
            height = int(doc.size().height()) + 8
            if height != getattr(self, "_fit_height", -1):
                self._fit_height = height
                self.setFixedHeight(height)
        finally:
            self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()
        # 宽度变化超过一截：公式图片要按新宽度重新缩一次（不然会很小/被切）
        width = self.viewport().width()
        if self._math_parts and abs(
                width - int(getattr(self, "_math_width", 0) or 0)) > 60:
            self._rebuild_math()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit()
        if self._math_parts and not getattr(self, "_math_width", 0):
            self._rebuild_math()

    def wheelEvent(self, event):
        """正文不自己滚：滚轮交给外层的对话区。"""
        event.ignore()


# ---------------------------------------------------------------- 代码块
# 说明：这里**不用** QSyntaxHighlighter。实测在 QPlainTextEdit 上
# `rehighlight()` 会以 0xC0000005 崩（PySide6 + Python 子类 + 构造期调用），
# 所以改成「设完文本再一次性涂色」——反正代码块是只读的，改了再涂一遍就行。
def _code_spans(text: str) -> List[Tuple[int, int, str]]:
    """把一行代码切成 [(起点, 长度, 用哪种颜色)]。"""
    spans: List[Tuple[int, int, str]] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "#":
            spans.append((index, length - index, "comment"))
            return spans
        if char in "\"'":
            quote = char
            end = index + 1
            while end < length:
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == quote:
                    end += 1
                    break
                end += 1
            end = min(end, length)
            spans.append((index, end - index, "string"))
            index = end
            continue
        if char.isdigit():
            end = index
            while end < length and (text[end].isdigit()
                                    or text[end] in ".xXabcdefABCDEF_"):
                end += 1
            spans.append((index, end - index, "number"))
            index = end
            continue
        if char.isalpha() or char == "_":
            end = index
            while end < length and (text[end].isalnum() or text[end] == "_"):
                end += 1
            word = text[index:end]
            if word in KEYWORDS or word in C_KEYWORDS:
                spans.append((index, end - index, "keyword"))
            elif end < length and text[end] == "(":
                spans.append((index, end - index, "func"))
            index = end
            continue
        index += 1
    return spans


def colorize(document) -> None:
    """给整篇文档涂上轻量语法色（注释灰 / 字符串绿 / 关键字紫 / 数字橙）。"""
    tones = syntax_colors()
    palette = {
        "comment": QColor(tones["comment"]),
        "string": QColor(tones["string"]),
        "number": QColor(tones["number"]),
        "keyword": QColor(tones["keyword"]),
        "func": QColor(tones["func"]),
    }
    cursor = QTextCursor(document)
    cursor.beginEditBlock()
    block = document.begin()
    while block.isValid():
        line = block.text()
        for start, count, tone in _code_spans(line):
            fmt = QTextCharFormat()
            fmt.setForeground(palette[tone])
            run = QTextCursor(document)
            run.setPosition(block.position() + start)
            run.setPosition(block.position() + start + count,
                            QTextCursor.MoveMode.KeepAnchor)
            run.setCharFormat(fmt)
        block = block.next()
    cursor.endEditBlock()


class CodeEdit(QPlainTextEdit):
    """代码正文：只读、无边框、高度自适应、能选中复制。"""

    def __init__(self, code: str = "", parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(mono_font(10.6))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        colors_ = colors()
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; border: none;"
            f" color: {colors_['TEXT']}; padding: 2px 0 6px 0;"
            f" selection-background-color: {theme.appearance().accent}; }}")
        self.set_code(code or "")

    def set_code(self, code: str) -> None:
        self.setPlainText(code or "")
        colorize(self.document())
        self._fit()

    def _fit(self) -> None:
        """按行数算高度（不换行模式，行数 × 行高就够了，比问文档要稳）。"""
        if getattr(self, "_fitting", False):
            return
        self._fitting = True
        try:
            metrics = self.fontMetrics()
            line_height = max(1, metrics.lineSpacing())
            # 行数 × 行高 + 文档边距 + 内边距（留够，别把最后一行切掉）
            margin = int(2 * self.document().documentMargin()) + 2 * self.frameWidth()
            height = self.document().blockCount() * line_height + margin + 16
            if height != getattr(self, "_fit_height", -1):
                self._fit_height = height
                self.setFixedHeight(height)
        finally:
            self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class CodeCard(Card):
    """代码块卡片：头部（</> 语言 + 复制）+ 等宽正文。"""

    def __init__(self, code: str, language: str = "", parent=None):
        colors_ = colors()
        super().__init__(parent, radius=12, fill=colors_["CARD_ALT"],
                         border=colors_["BORDER_SOFT"], padding=0, spacing=0)
        self._code = code or ""
        self._language = (language or "").strip()

        head_host = QWidget()
        head_host.setStyleSheet(
            f"QWidget {{ background: transparent;"
            f" border-bottom: 1px solid {colors_['BORDER_SOFT']}; }}")
        head = QHBoxLayout(head_host)
        head.setContentsMargins(12, 6, 8, 6)
        head.setSpacing(8)
        glyph = QLabel("</>")
        glyph.setFont(mono_font(9.8))
        glyph.setStyleSheet(f"color: {colors_['MUTED']}; border: none;")
        head.addWidget(glyph)
        name = self._pretty_language()
        label = QLabel(name)
        label.setStyleSheet(
            f"color: {colors_['TEXT_SOFT']}; font-size: 10.2pt; border: none;")
        head.addWidget(label)
        head.addStretch(1)
        self.copy_button = SoftButton("复制", "ghost", height=24, min_width=52,
                                      point_size=9.2)
        self.copy_button.setToolTip("把这整段代码复制到剪贴板")
        self.copy_button.clicked.connect(self.copy)
        head.addWidget(self.copy_button)
        self.body.addWidget(head_host)

        editor_host = QWidget()
        editor_layout = QVBoxLayout(editor_host)
        editor_layout.setContentsMargins(12, 4, 10, 6)
        editor_layout.setSpacing(0)
        self.editor = CodeEdit(self._code)
        editor_layout.addWidget(self.editor)
        self.body.addWidget(editor_host)

    # -------------------------------------------------- 语言名
    def _pretty_language(self) -> str:
        name = self._language.strip().lower()
        table = {"py": "Python", "python": "Python", "python3": "Python",
                 "c": "C", "cpp": "C++", "c++": "C++", "cu": "CUDA C",
                 "cuda": "CUDA C", "cuh": "CUDA C", "js": "JavaScript",
                 "javascript": "JavaScript", "ts": "TypeScript",
                 "bash": "Shell", "sh": "Shell", "shell": "Shell",
                 "zsh": "Shell", "powershell": "PowerShell", "ps1": "PowerShell",
                 "json": "JSON", "yaml": "YAML", "yml": "YAML", "sql": "SQL",
                 "html": "HTML", "css": "CSS", "java": "Java", "rust": "Rust",
                 "go": "Go", "latex": "LaTeX", "tex": "LaTeX", "md": "Markdown"}
        if not name:
            return "代码"
        return table.get(name, self._language.strip())

    def copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._code)
        self.copy_button.setText("已复制")
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(1200, lambda: self.copy_button.setText("复制"))
        except Exception:
            pass

    def chat_text(self) -> str:
        language = self._language or "代码"
        return "%s\n%s" % (language, self._code)


# ---------------------------------------------------------------- 小卡片
#: 文档类型标签（显示在附件小卡上）
DOC_BADGES = {
    ".pdf": "PDF", ".docx": "DOC", ".doc": "DOC", ".pptx": "PPT",
    ".ppt": "PPT", ".xlsx": "XLS", ".xls": "XLS", ".txt": "TXT",
    ".md": "MD", ".csv": "CSV", ".json": "JSON", ".py": "PY",
    ".js": "JS", ".ts": "TS", ".c": "C", ".cpp": "C++", ".h": "H",
    ".java": "JAVA", ".tex": "TEX", ".bib": "BIB", ".html": "HTML",
    ".xml": "XML", ".yaml": "YAML", ".yml": "YAML", ".zip": "ZIP",
}
#: 缩略图最大显示尺寸（逻辑像素）
THUMB_MAX_W = 132
THUMB_MAX_H = 92


class AttachmentsRow(QWidget):
    """用户消息带上的附件：靠右排一行小卡（图片给缩略图，文档给类型 + 名字）。"""

    def __init__(self, items: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        colors_ = colors()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)                      # 靠右
        self._names: List[str] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            chip = self._make_chip(item, colors_)
            if chip is not None:
                row.addWidget(chip, 0, Qt.AlignmentFlag.AlignBottom)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    # -------------------------------------------------- 单个附件
    def _make_chip(self, item: Dict[str, Any], colors_):
        from pathlib import Path

        name = str(item.get("name") or "附件")
        self._names.append(name)
        path = str(item.get("path") or "")
        kind = str(item.get("kind") or "")
        suffix = Path(name).suffix.lower()
        is_image = kind == "image" or suffix in (".png", ".jpg", ".jpeg",
                                                 ".webp", ".gif", ".bmp")
        if is_image:
            chip = self._image_chip(name, path, colors_)
            if chip is not None:
                return chip
        return self._doc_chip(name, suffix, path, colors_)

    def _image_chip(self, name: str, path: str, colors_):
        from pathlib import Path

        pixmap = QPixmap()
        try:
            if path and Path(path).exists():
                pixmap = QPixmap(path)
        except Exception:
            pixmap = QPixmap()
        if pixmap.isNull():
            return None
        if pixmap.width() > THUMB_MAX_W or pixmap.height() > THUMB_MAX_H:
            pixmap = pixmap.scaled(
                THUMB_MAX_W, THUMB_MAX_H,
                aspectMode=Qt.AspectRatioMode.KeepAspectRatio,
                mode=Qt.TransformationMode.SmoothTransformation)
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        thumb = QLabel()
        thumb.setPixmap(pixmap)
        thumb.setStyleSheet(
            f"QLabel {{ background: {colors_['CARD_ALT']};"
            f" border: 1px solid {colors_['BORDER_SOFT']};"
            f" border-radius: 10px; padding: 2px; }}")
        thumb.setToolTip(name)
        box.addWidget(thumb, 0, Qt.AlignmentFlag.AlignRight)
        caption = QLabel(self._elide(name, 132))
        caption.setStyleSheet(
            f"color: {colors_['MUTED']}; font-size: 9.2pt; background: transparent;")
        caption.setToolTip(name)
        box.addWidget(caption, 0, Qt.AlignmentFlag.AlignRight)
        holder.setToolTip("图片附件：%s" % name)
        return holder

    def _doc_chip(self, name: str, suffix: str, path: str, colors_):
        badge = DOC_BADGES.get(suffix) or (suffix.lstrip(".").upper()[:4] or "FILE")
        chip = Card(radius=10, fill=colors_["CARD_ALT"],
                    border=colors_["BORDER_SOFT"], padding=6, spacing=6)
        inside = QHBoxLayout()
        inside.setContentsMargins(0, 0, 0, 0)
        inside.setSpacing(6)
        mark = QLabel(badge)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(34, 34)
        mark.setStyleSheet(
            f"QLabel {{ background: {colors_['INPUT_BG']};"
            f" color: {colors_['TEXT_SOFT']}; border-radius: 8px;"
            f" font-size: 8.8pt; font-weight: 600; }}")
        inside.addWidget(mark, 0)
        text = QLabel(self._elide(name, 150))
        text.setStyleSheet(
            f"color: {colors_['TEXT']}; font-size: 9.8pt; background: transparent;")
        inside.addWidget(text, 0)
        chip.body.addLayout(inside)
        chip.setToolTip("文档附件：%s" % name)
        size = ""
        try:
            from pathlib import Path

            if path and Path(path).exists():
                size = human_size(Path(path).stat().st_size)
        except Exception:
            size = ""
        chip.setToolTip("文档附件：%s%s" % (name, ("（%s）" % size) if size else ""))
        return chip

    @staticmethod
    def _elide(text: str, width: int) -> str:
        from PySide6.QtGui import QFontMetrics

        metrics = QFontMetrics(QLabel().font())
        return metrics.elidedText(str(text), Qt.TextElideMode.ElideMiddle, width)

    def chat_text(self) -> str:
        """复制整段对话时也能看出带了哪些附件。"""
        return "\n".join("【附件：%s】" % name for name in self._names)


class Bubble(QWidget):
    """用户消息：靠右的圆角气泡。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        colors_ = colors()
        mode = "dark" if theme.effective_mode() == "dark" else "light"
        fill = "#2E2E31" if mode == "dark" else "#EFEFF2"
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)
        self._text = text or ""
        self.label = QLabel(self._text)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setStyleSheet(
            f"QLabel {{ background: {fill}; color: {colors_['TEXT']};"
            f" border-radius: 16px; padding: 8px 15px; font-size: 11.2pt; }}")
        row.addWidget(self.label)
        # 不要让气泡顶满整行：右边留一点，宽度另行限制
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def set_max_width(self, width: int) -> None:
        """气泡宽度就贴着文字走：放得下最长那行，但不超过可用宽度的 76%。

        以前这里有个「至少 120px」的下限，于是「hi」这种短消息也会拉出一大块
        灰底（用户反馈「气泡长度一点也不美观」）。现在只有真的内容短时才窄，
        长消息仍然在 76% 处折行。

        **单行文字不能开 wordWrap**：`QLabel` 开了自动换行之后 `sizeHint()` /
        `heightForWidth()` 会按很窄的宽度去量，一个 20 个字的气泡被算成 3 行高
        （54px），实际只画一行 —— 这就是「单行文字气泡太高」的原因。
        所以放得下就关掉换行，只有真的超宽才开。
        """
        limit = max(120, int(width * MAX_BUBBLE_RATIO))
        metrics = self.label.fontMetrics()
        widest = 0
        for line in str(self._text or "").splitlines() or [""]:
            widest = max(widest, metrics.horizontalAdvance(line))
        needed = max(BUBBLE_MIN_WIDTH, widest + BUBBLE_PAD_X * 2 + 4)
        if needed <= limit:
            self.label.setWordWrap(False)
            self.label.setFixedWidth(int(needed))
        else:
            self.label.setWordWrap(True)
            self.label.setFixedWidth(int(limit))

    def chat_text(self) -> str:
        return self._text


class MutedCard(Card):
    """思考过程 / 工具调用：一张很淡的小卡片。

    标题行可以点：**要么完全收起、要么完全展开**，不留「固定高的小框」；
    正文按内容撑开，滚轮穿透到外层对话区（不会滚自己里面的内容）。
    """

    def __init__(self, title: str, body: str, tone: str = "", parent=None):
        colors_ = colors()
        super().__init__(parent, radius=12, fill=colors_["INPUT_BG"],
                         border=colors_["BORDER_SOFT"], padding=12, spacing=4)
        self._title = title
        self._text = body
        self._collapsed = False
        accent = tone or colors_["MUTED"]
        head = QWidget()
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.head_label = QLabel(title)
        self.head_label.setStyleSheet(
            f"color: {accent}; font-size: 10.2pt; font-weight: 600;"
            f" background: transparent;")
        row.addWidget(self.head_label, 1)
        self.toggle_label = QLabel("收起")
        self.toggle_label.setStyleSheet(
            f"color: {colors_['MUTED']}; font-size: 9.6pt; background: transparent;")
        row.addWidget(self.toggle_label, 0)
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        head.mouseReleaseEvent = self._on_head_click
        self.head = head
        self.body.addWidget(head)
        # 正文放在容器里，折叠时整块隐藏（不是给它一个固定高度）
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(4)
        self.body.addWidget(self.content)

    def _on_head_click(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_collapsed(not self._collapsed)

    def set_collapsed(self, value: bool) -> None:
        self._collapsed = bool(value)
        self.content.setVisible(not self._collapsed)
        self.toggle_label.setText("展开" if self._collapsed else "收起")

    def is_collapsed(self) -> bool:
        return self._collapsed

    def chat_text(self) -> str:
        return "%s\n%s" % (self._title, self._text)


class ThinkingCard(MutedCard):
    """思考过程：标题 + 灰字正文。"""

    def __init__(self, text: str, parent=None):
        super().__init__("思考过程", text, parent=parent)
        view = MarkdownText(text, muted=True)
        self.content_layout.addWidget(view)
        self.markdown = view


class ToolCard(MutedCard):
    """一次工具调用：✓ 工具名 + 结果。"""

    def __init__(self, item: Dict[str, Any], parent=None):
        colors_ = colors()
        ok = bool(item.get("ok"))
        tone = colors_["SUCCESS"] if ok else colors_["DANGER"]
        label = str(item.get("label") or item.get("name") or "工具")
        result = str(item.get("content") or "")
        super().__init__("%s %s" % ("✓" if ok else "!", label), result, tone=tone,
                         parent=parent)
        text = QLabel(result)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setStyleSheet(
            f"color: {colors_['TEXT_SOFT']}; font-size: 10.2pt; background: transparent;")
        self.content_layout.addWidget(text)


class NoticeLabel(QLabel):
    """一行灰色说明（例如「这次没有思考内容」）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        colors_ = colors()
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setStyleSheet(
            f"color: {colors_['MUTED']}; font-size: 10.2pt; background: transparent;")

    def chat_text(self) -> str:
        return self.text()


class HintLabel(QLabel):
    """灰字提示（正在生成 / 空对话时的说明），也参与 toPlainText。"""

    def __init__(self, text: str, size: float = 10.2, padding: str = "0", parent=None):
        super().__init__(text, parent)
        colors_ = colors()
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setStyleSheet(
            f"color: {colors_['MUTED']}; font-size: {size}pt;"
            f" background: transparent; padding: {padding};")

    def chat_text(self) -> str:
        return self.text()


# ---------------------------------------------------------------- Markdown 拆分
def split_code(markdown: str) -> List[Tuple[str, str, str]]:
    """把 Markdown 拆成 [(kind, a, b)]：("text", md, "") 或 ("code", lang, code)。"""
    parts: List[Tuple[str, str, str]] = []
    lines = (markdown or "").splitlines(keepends=True)
    buffer: List[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = stripped[3:].strip()
            if buffer:
                parts.append(("text", "".join(buffer), ""))
                buffer = []
            index += 1
            body: List[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                body.append(lines[index])
                index += 1
            index += 1  # 跳过收尾的 ```
            parts.append(("code", fence, "".join(body).rstrip("\n")))
            continue
        buffer.append(line)
        index += 1
    if buffer:
        parts.append(("text", "".join(buffer), ""))
    return parts


def assistant_widgets(content: str, parent=None,
                      math: bool = True) -> List[QWidget]:
    """助手一段回答 → 一组控件（正文 + 代码卡片）。

    `math=False` 时正文里的公式先不渲染（流式生成时用，免得每 60ms 重编一次）。
    """
    widgets: List[QWidget] = []
    for kind, first, second in split_code(content):
        if kind == "code":
            widgets.append(CodeCard(second, first, parent=parent))
        else:
            text = first.strip("\n")
            if text.strip():
                widgets.append(MarkdownText(text, parent=parent, math=math))
    return widgets


# ---------------------------------------------------------------- 对话区
class ChatView(QScrollArea):
    """对话区容器：一列控件，对外保留 toPlainText()。"""

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._apply_scroll_style()
        self._body = _DropBody(self)
        self._body.files_dropped.connect(self.files_dropped.emit)
        column = QVBoxLayout(self._body)
        column.setContentsMargins(6, 6, 8, 6)
        column.setSpacing(10)
        self._column = column
        column.addStretch(1)
        self.setWidget(self._body)
        self._messages: List[QWidget] = []
        self._live: List[QWidget] = []
        self._signature: Any = None
        self._stick_bottom = True
        self._adjusting = False
        self.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.verticalScrollBar().rangeChanged.connect(self._on_range_changed)
        self._last: Optional[Tuple[List[Dict[str, Any]], Dict[str, Any],
                                   bool, str]] = None

    def _apply_scroll_style(self) -> None:
        colors_ = colors()
        self.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            f"QScrollBar:vertical {{ width: 8px; background: transparent; }}"
            f"QScrollBar::handle:vertical {{ background: {colors_['SCROLL_HANDLE']};"
            f" border-radius: 4px; min-height: 24px; }}")

    def on_theme_changed(self) -> None:
        """换主题：所有气泡 / 代码卡都得按新配色重画。"""
        self._apply_scroll_style()
        self._signature = None
        if self._last is not None:
            history, live, thinking, empty_text = self._last
            self.render(history, live, thinking=thinking, empty_text=empty_text)

    # -------------------------------------------------- 内容
    def clear_messages(self) -> None:
        for widget in self._messages + self._live:
            widget.setParent(None)
            widget.deleteLater()
        self._messages = []
        self._live = []
        self._signature = None

    def toPlainText(self) -> str:
        chunks: List[str] = []
        for widget in self._messages + self._live:
            getter = getattr(widget, "chat_text", None)
            if callable(getter):
                text = str(getter() or "").strip()
                if text:
                    chunks.append(text)
        return "\n\n".join(chunks)

    # -------------------------------------------------- 渲染
    def render(self, history: List[Dict[str, Any]],
               live: Optional[Dict[str, Any]] = None,
               thinking: bool = True, empty_text: str = "") -> None:
        """history 里的消息只在内容变化时重建；live（正在生成的那段）每次都重建。

        滚动：**只在用户本来就贴着底部时才跟到底**。用户往上翻着看的时候
        （比如生成到一半想回头看前面的推导），内容再更新也不能把他拽回底部 ——
        这就是「生成回答时无法向上滚动」的原因。
        """
        follow = self._stick_bottom
        self._last = (list(history), dict(live or {}), bool(thinking), empty_text)
        signature = [(item.get("role"), len(str(item.get("content") or "")),
                      bool(item.get("reasoning")), bool(thinking),
                      theme.revision())
                     for item in history]
        if signature != self._signature:
            self._rebuild_history(history, thinking)
            self._signature = signature
        self._rebuild_live(live or {}, thinking, empty_text)
        self._update_bubble_widths()
        if follow:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())
            self._stick_bottom = True

    # -------------------------------------------------- 滚动跟随
    #: 离底部多近算「贴着底部」（像素）
    STICK_SLACK = 24

    def _is_at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - self.STICK_SLACK

    def _on_scroll_changed(self, _value: int) -> None:
        """用户滚上去就停止跟随；滚回底部再继续跟随。"""
        if getattr(self, "_adjusting", False):
            return
        self._stick_bottom = self._is_at_bottom()

    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        """内容长高了：如果本来在跟随，就重新贴到底部（布局是分几步走的）。"""
        if getattr(self, "_adjusting", False):
            return
        bar = self.verticalScrollBar()
        if self._stick_bottom or bar.value() >= maximum - self.STICK_SLACK:
            self._adjusting = True
            try:
                bar.setValue(maximum)
            finally:
                self._adjusting = False
            self._stick_bottom = True

    def scroll_to_bottom(self) -> None:
        """手动回到底部（点「回到底部」时用），并重新开始跟随。"""
        self._adjusting = True
        try:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())
        finally:
            self._adjusting = False
        self._stick_bottom = True

    def _insert(self, widget: QWidget) -> None:
        self._column.insertWidget(self._column.count() - 1, widget)

    def _rebuild_history(self, history: List[Dict[str, Any]],
                         thinking: bool = True) -> None:
        for widget in self._messages:
            widget.setParent(None)
            widget.deleteLater()
        self._messages = []
        for item in history:
            role = item.get("role")
            content = str(item.get("content") or "")
            if role == "user":
                attachments = item.get("attachments") or []
                if isinstance(attachments, list) and attachments:
                    self._messages.append(
                        AttachmentsRow(attachments, self._body))
                self._messages.append(Bubble(content, self._body))
            elif role == "assistant":
                reasoning = str(item.get("reasoning") or "")
                if reasoning and thinking:
                    self._messages.append(ThinkingCard(reasoning, self._body))
                self._messages.extend(assistant_widgets(content, self._body))
            elif role == "tool":
                self._messages.append(ToolCard(item, self._body))
        for widget in self._messages:
            self._insert(widget)

    def _rebuild_live(self, live: Dict[str, Any], thinking: bool = True,
                      empty_text: str = "") -> None:
        for widget in self._live:
            widget.setParent(None)
            widget.deleteLater()
        self._live = []
        reasoning = str(live.get("reasoning") or "")
        answer = str(live.get("answer") or "")
        notice = str(live.get("notice") or "")
        if reasoning and thinking:
            self._live.append(ThinkingCard(reasoning, self._body))
        if answer:
            # 还在生成：公式先不渲染（会一遍遍重编 xelatex），等结束再渲染
            self._live.extend(assistant_widgets(
                answer, self._body, math=not live.get("streaming")))
        if live.get("streaming"):
            text = "正在生成…" if answer or reasoning else "正在思考…"
            self._live.append(HintLabel(text, parent=self._body))
        if notice:
            self._live.append(NoticeLabel(notice, self._body))
        if (not self._messages and not self._live and empty_text):
            self._live.append(HintLabel(empty_text, size=10.0, padding="12px 4px",
                                        parent=self._body))
        for widget in self._live:
            self._insert(widget)

    def _update_bubble_widths(self) -> None:
        width = max(200, self.viewport().width())
        for widget in self._messages + self._live:
            if isinstance(widget, Bubble):
                widget.set_max_width(width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_bubble_widths()


class _DropBody(QWidget):
    """滚动区里的那一列控件，同时接住拖进来的文件。"""

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setStyleSheet("background: transparent;")

    @staticmethod
    def _paths(event) -> List:
        from pathlib import Path

        try:
            mime = event.mimeData()
            if not mime.hasUrls():
                return []
            return [Path(url.toLocalFile()) for url in mime.urls()
                    if url.isLocalFile()]
        except Exception:
            return []

    def dragEnterEvent(self, event):
        if self._paths(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._paths(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = self._paths(event)
        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
            return
        super().dropEvent(event)
