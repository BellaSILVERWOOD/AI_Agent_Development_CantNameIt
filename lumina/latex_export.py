"""把「文档视图」（QTextDocument 富文本）转换成 LaTeX 源码。

支持范围（都是工具栏能产生的格式）：
* 段落样式：正文 / 标题 1~3 / 行间公式 / 列表 / 引用
* 段落对齐：左 / 中 / 右
* 字符格式：粗体、斜体、下划线、字号、颜色、上标 / 下标、等宽
* 列表：有序 / 无序，支持缩进层级
* 表格：转成 tabular（带 booktabs 三线表）
* 公式：以「图片对象」形式内嵌，图片名里编码了 LaTeX 源码
  - 行内公式 → $...$
  - 行间公式 → \[...\]
"""
from __future__ import annotations

import base64
from typing import List, Optional

from PySide6.QtGui import QTextBlock, QTextCharFormat, QTextDocument, QTextListFormat


# ------------------------------------------------------------------ 特殊字符
_TEX_ESCAPE = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_text(text: str) -> str:
    """转义正文里的 LaTeX 特殊字符（公式对象不经过这里）。"""
    return "".join(_TEX_ESCAPE.get(char, char) for char in (text or ""))


# ------------------------------------------------------------------ 字号映射
BASE_POINT_SIZE = 11.0      # 文档正文字号；这个字号不输出命令


def size_command(points: float) -> Optional[str]:
    if points <= 0:
        return None
    if abs(points - BASE_POINT_SIZE) < 1.4:
        return None             # 正文字号：什么都不用写
    if points < 8.5:
        return "tiny"
    if points < 9.5:
        return "scriptsize"
    if points < 10.5:
        return "footnotesize"
    if points < 11.5:
        return "small"
    if points < 12.5:
        return "normalsize"
    if points < 14.5:
        return "large"
    if points < 17.5:
        return "Large"
    if points < 20.5:
        return "LARGE"
    if points < 25.0:
        return "huge"
    return "Huge"


# ------------------------------------------------------------------ 公式对象
MATH_PREFIX = "math:"

# 源码模式下，公式以普通文本存在，靠这个自定义属性记住它的 LaTeX
from PySide6.QtGui import QTextFormat

MATH_TEXT_PROPERTY = int(QTextFormat.Property.UserProperty) + 1


def math_text_value(fmt: QTextCharFormat):
    """读取「源码模式公式文本」里保存的 (latex, display)。"""
    try:
        value = fmt.property(MATH_TEXT_PROPERTY)
    except Exception:
        return None
    if not isinstance(value, str) or "|" not in value:
        return None
    prefix, _, latex = value.partition("|")
    if prefix not in ("i", "d"):
        return None
    return latex, prefix == "d"


def math_payload(latex: str, display: bool = False) -> str:
    """把公式编码进图片名（base64，避免引号/冒号问题）。"""
    raw = base64.urlsafe_b64encode((latex or "").encode("utf-8")).decode("ascii")
    return f"{MATH_PREFIX}{'d' if display else 'i'}{raw}"


def decode_math(name: str) -> Optional[tuple]:
    """从图片名还原 (latex, display)。"""
    if not name or not name.startswith(MATH_PREFIX):
        return None
    body = name[len(MATH_PREFIX):]
    if len(body) < 2:
        return None
    display = body[0] == "d"
    payload = body[1:]
    padding = "=" * (-len(payload) % 4)
    try:
        latex = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
    except Exception:
        return None
    return latex, display


# ------------------------------------------------------------------ 段落样式
HEADING_LEVELS = {2: 1, 3: 2, 4: 3}      # QTextBlock 的 headingLevel -> LaTeX 层级


def _alignment_command(alignment) -> str:
    from PySide6.QtCore import Qt

    if alignment & Qt.AlignmentFlag.AlignHCenter:
        return "center"
    if alignment & Qt.AlignmentFlag.AlignRight:
        return "flushright"
    return ""


def _char_wrappers(fmt: QTextCharFormat, drop_size: bool = False,
                   drop_bold: bool = False) -> List[str]:
    """返回需要从内到外包裹命令列表（不用闭包，最后统一收口）。"""
    from PySide6.QtGui import QFont

    prefixes: List[str] = []
    weight = fmt.fontWeight()
    if fmt.fontItalic():
        prefixes.append(r"\textit{")
    if fmt.fontUnderline():
        prefixes.append(r"\underline{")
    if fmt.fontFixedPitch() or fmt.fontFamilies() and "Cascadia Code" in fmt.fontFamilies():
        prefixes.append(r"\texttt{")
    if weight >= 600 and not drop_bold:
        prefixes.append(r"\textbf{")
    code = size_command(fmt.fontPointSize()) if fmt.hasProperty(
        QTextCharFormat.Property.FontPointSize) else None
    if code and code != "normalsize" and not drop_size:
        prefixes.append("{\\" + code + " ")
    color = fmt.foreground().color()
    if color.isValid() and not _is_default_color(color):
        prefixes.append(r"\textcolor[HTML]{" + color.name()[1:].upper() + "}{")
    return prefixes


def _is_default_color(color) -> bool:
    hex_value = color.name().lower()
    return hex_value in ("#000000", "#1d1d1f", "#000000")


def _close_wrappers(prefixes: List[str]) -> str:
    closes = []
    for prefix in reversed(prefixes):
        closes.append("}" if prefix.startswith(("{\\", )) else "}")
    return "".join(closes)


# ------------------------------------------------------------------ 主转换
def block_to_latex(block: QTextBlock, list_stack: Optional[dict] = None) -> str:
    """把单个段落转成 LaTeX（不含列表环境包裹）。"""
    level = block.blockFormat().headingLevel()
    is_heading = level in HEADING_LEVELS
    text = _fragments_to_latex(block, heading=is_heading)
    if not text.strip():
        return ""
    level = block.blockFormat().headingLevel()
    align = block.blockFormat().alignment()
    heading = HEADING_LEVELS.get(level)
    if heading == 1:
        return "\\section*{" + text + "}"
    if heading == 2:
        return "\\subsection*{" + text + "}"
    if heading == 3:
        return "\\subsubsection*{" + text + "}"

    env = _alignment_command(align)
    if env:
        return "\\begin{" + env + "}\n" + text + "\n\\end{" + env + "}"
    return text


def _fragments_to_latex(block: QTextBlock, heading: bool = False) -> str:
    pieces: List[str] = []
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid():
            pieces.append(_fragment_to_latex(fragment, drop_size=heading,
                                             drop_bold=heading))
        iterator += 1
    return "".join(pieces)


def _fragment_to_latex(fragment, drop_size: bool = False,
                       drop_bold: bool = False) -> str:
    fmt = fragment.charFormat()
    math_text = math_text_value(fmt)
    if math_text is not None:
        latex, display = math_text
        if display:
            return "\n\\[\n  " + latex + "\n\\]\n"
        return "$" + latex + "$"
    if fmt.isImageFormat():
        payload = decode_math(fmt.toImageFormat().name())
        if payload is not None:
            latex, display = payload
            if display:
                return "\n\\[\n  " + latex + "\n\\]\n"
            return "$" + latex + "$"
        # 普通图片：用文件名
        name = fmt.toImageFormat().name()
        if name:
            return "\\includegraphics[width=0.8\\textwidth]{" + name + "}"
        return ""
    text = fragment.text()
    if not text:
        return ""
    escaped = escape_text(text)
    from PySide6.QtGui import QTextCharFormat as _F

    vertical = fmt.verticalAlignment()
    if vertical == _F.VerticalAlignment.AlignSuperScript:
        return "\\textsuperscript{" + escaped + "}"
    if vertical == _F.VerticalAlignment.AlignSubScript:
        return "\\textsubscript{" + escaped + "}"
    prefixes = _char_wrappers(fmt, drop_size=drop_size, drop_bold=drop_bold)
    if not prefixes:
        return escaped
    return "".join(prefixes) + escaped + _close_wrappers(prefixes)


def _list_environment(fmt: QTextListFormat) -> str:
    style = fmt.style()
    if style in (QTextListFormat.Style.ListDecimal,
                 QTextListFormat.Style.ListLowerAlpha,
                 QTextListFormat.Style.ListUpperAlpha,
                 QTextListFormat.Style.ListLowerRoman,
                 QTextListFormat.Style.ListUpperRoman):
        return "enumerate"
    return "itemize"


def document_to_latex(document: QTextDocument) -> str:
    """整篇文档 → LaTeX 正文（不含导言区）。"""
    from PySide6.QtGui import QTextTable

    lines: List[str] = []
    current_list = None

    def close_list() -> None:
        nonlocal current_list
        if current_list is not None:
            lines.append("\\end{" + _list_environment(current_list.format()) + "}")
            current_list = None

    root = document.rootFrame()
    iterator = root.begin()
    while not iterator.atEnd():
        child = iterator.currentFrame()
        if child is not None and child is not root:
            # 表格：整表输出，然后跳过表格内部的所有段落
            try:
                parent = child.parentFrame()
            except Exception:
                parent = root
            if parent is root:
                close_list()
                if isinstance(child, QTextTable):
                    lines.append(table_to_latex(child))
                    lines.append("")
                iterator = child.end()
                continue

        block = iterator.currentBlock()
        text_list = block.textList()
        if current_list is not None and text_list is not current_list:
            close_list()
        if text_list is not None and text_list is not current_list:
            current_list = text_list
            lines.append("\\begin{" + _list_environment(text_list.format()) + "}")

        if text_list is not None:
            item = _fragments_to_latex(block).strip()
            lines.append("  \\item " + item if item else "  \\item ")
        else:
            paragraph = block_to_latex(block)
            lines.append(paragraph if paragraph else "")

        iterator += 1

    close_list()

    # 相邻的同类型列表合并
    merged: List[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        merged.append(line)
        if index + 2 < len(lines):
            nxt, after = lines[index + 1], lines[index + 2]
            for env in ("itemize", "enumerate"):
                if (line.strip() == "\\end{" + env + "}"
                        and nxt.strip() == "\\begin{" + env + "}"):
                    merged.pop()
                    index += 2
                    break
        index += 1
    lines = merged

    result: List[str] = []
    blank = 0
    for line in lines:
        if not line.strip():
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        result.append(line)
    return "\n".join(result).strip()


# ------------------------------------------------------------------ 表格
def table_to_latex(table) -> str:
    """QTextTable → booktabs 三线表。"""
    rows = table.rows()
    cols = table.columns()
    if rows <= 0 or cols <= 0:
        return ""
    lines = ["\\begin{tabular}{" + "l" * cols + "}", "\\toprule"]
    for row in range(rows):
        cells = []
        for col in range(cols):
            cell = table.cellAt(row, col)
            text = cell.firstCursorPosition().block().text() if cell.isValid() else ""
            cells.append(escape_text(text))
        lines.append(" & ".join(cells) + " \\\\")
        if row == 0:
            lines.append("\\midrule")
    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    return "\n".join(lines)
