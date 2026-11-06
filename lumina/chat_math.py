# -*- coding: utf-8 -*-
"""把对话里的 LaTeX 公式找出来、渲染成图片，插进 Markdown 正文里。

用户拿到的回答经常长这样（截图里的样子）：

    **(1) 拟合直线 $f(x)=ax+b$**
    $$\\begin{pmatrix}0&1\\\\1&1\\end{pmatrix}\\begin{pmatrix}a\\\\b\\end{pmatrix}=…$$

Qt 的 `setMarkdown()` 认粗体 / 标题 / 列表 / 表格，但**不认数学公式**，
`$…$`、`$$…$$`、`\\begin{pmatrix}` 会原样留在屏幕上。这里做三件事：

1. `extract()`：把这些公式抠出来，先换成一对「私用区」占位符
   （`\\ue0000\\ue001`，Markdown 不会解释它们）；
2. 交给 `latex_math.render()` 用 xelatex 渲染成 PNG（按代码哈希缓存，
   同一个公式只编译一次；颜色跟主题走）；
3. `apply()`：在已经排好版的 QTextDocument 里找到占位符，换成图片。
   渲染失败的公式就**退回原文**（`$…$`），宁可难看也不要吞内容。

注意性能：第一次渲染一个公式约 0.9 秒（之后走缓存是几毫秒），
所以流式生成过程中（`math=False`）先不渲染，等这段回答结束再渲染。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

#: 占位符两侧的字符：Unicode 私用区，Markdown / 富文本都不会特殊对待
MASK_OPEN = "\ue000"
MASK_CLOSE = "\ue001"

#: 一条消息里最多渲染多少个公式（防止模型发一整页公式把界面拖死）
MAX_FORMULAS = 60
#: 渲染用的分辨率：480 DPI 再按字号缩，高分屏（125% / 150%）下也不糊
DPI = 480
#: 行间公式最宽占正文宽度的比例
DISPLAY_MAX_RATIO = 0.94
#: 行间公式最高像素（再高就缩，别让一个矩阵占掉半屏）
DISPLAY_MAX_HEIGHT = 240
#: `latex_math` 里的文档字号（12pt）：用来把 360 DPI 的图片缩回正文字号
DOC_POINT_SIZE = 12.0

DISPLAY_DOLLAR = re.compile(r"\$\$(.+?)\$\$", re.S)
DISPLAY_BRACKET = re.compile(r"\\\[(.+?)\\\]", re.S)
INLINE_PAREN = re.compile(r"\\\((.+?)\\\)", re.S)
INLINE_DOLLAR = re.compile(r"(?<![\\$])\$(?!\$)([^$\n]{1,400}?)\$(?!\$)")
ENV_BLOCK = re.compile(
    r"(\\begin\{(?:pmatrix|bmatrix|vmatrix|matrix|pmatrix\*|cases|aligned|"
    r"array|equation\*?|gather\*?|align\*?)\}.+?\\end\{(?:pmatrix|bmatrix|"
    r"vmatrix|matrix|pmatrix\*|cases|aligned|array|equation\*?|gather\*?|"
    r"align\*?)\})", re.S)

_MATH_HINT = re.compile(r"[\\^_{}=<>]")
_SIMPLE_VAR = re.compile(r"[A-Za-z][A-Za-z0-9]{0,3}")
_OP = re.compile(r"[+\-*/]")


def _looks_like_math(core: str) -> bool:
    """`$…$` 里的内容像不像公式（别把「$5 and $10」当公式）。"""
    text = (core or "").strip()
    if not text or len(text) > 400:
        return False
    if _MATH_HINT.search(text):
        return True
    if _SIMPLE_VAR.fullmatch(text):
        return True
    # 「x + y」「a,b」这种：得有运算符、且不能是一句英文
    if _OP.search(text) and not re.search(r"\b(?:and|or|the|to|of|is)\b", text,
                                          re.I):
        return bool(re.fullmatch(r"[A-Za-z0-9\s\+\-\*/\\^_{}\(\),\.\[\]]+", text))
    return False


def extract(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """把公式抠出来，返回 (掩码后的文本, 公式列表)。

    每个公式是 `{"key": 占位符, "latex": 源码, "display": 是否行间}`。
    """
    parts: List[Dict[str, Any]] = []
    if not text or ("$" not in text and "\\(" not in text and "\\[" not in text
                    and "\\begin{" not in text):
        return text or "", parts

    def mask(raw: str, display: bool, whole: str = "") -> str:
        """把公式换成占位符；超上限或内容是空的就原样留着（不丢美元符号）。"""
        if len(parts) >= MAX_FORMULAS:
            return whole or raw
        latex = raw.strip()
        if not latex:
            return whole or raw
        key = "%s%d%s" % (MASK_OPEN, len(parts), MASK_CLOSE)
        parts.append({"key": key, "latex": latex, "display": bool(display)})
        return key

    out = text
    # ① 行间公式先处理（它的 $ 更多，先抠出来免得被行内规则拆开）
    out = DISPLAY_DOLLAR.sub(
        lambda m: mask(m.group(1), True, m.group(0)), out)
    out = DISPLAY_BRACKET.sub(
        lambda m: mask(m.group(1), True, m.group(0)), out)
    # ② 没写 $ 的矩阵 / 对齐环境
    out = ENV_BLOCK.sub(lambda m: mask(m.group(1), True, m.group(0)), out)
    # ③ 行内公式 \(…\)
    out = INLINE_PAREN.sub(
        lambda m: mask(m.group(1), False, m.group(0)), out)
    # ④ 行内公式 $…$（内容得像个公式）
    out = INLINE_DOLLAR.sub(
        lambda m: mask(m.group(1), False, m.group(0))
        if _looks_like_math(m.group(1)) else m.group(0), out)
    return out, parts


def render_one(latex: str, display: bool, color: str = "") -> Optional[str]:
    """渲染一个公式，返回 PNG 路径；渲染不了返回 None。"""
    try:
        from . import latex_math

        return latex_math.render(latex, display=display, dpi=DPI, color=color)
    except Exception:
        return None


def cached(latex: str, display: bool, color: str = "") -> Optional[str]:
    """已经渲染过的话直接拿缓存（不编译），没渲染过返回 None。"""
    try:
        from . import latex_math

        return latex_math.cached_png(latex, display=display, dpi=DPI, color=color)
    except Exception:
        return None


def collect_cached(parts: List[Dict[str, Any]],
                   color: str = "") -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """把已经有缓存的公式挑出来，返回 ({占位符: 图片}, 还没渲染的公式列表)。

    第一次出现的公式要跑一遍 xelatex（约 0.9 秒），全部同步做会把界面卡住，
    所以调用方先插缓存好的，剩下的一颗一颗后台补（见 `MarkdownText`）。
    """
    images: Dict[str, str] = {}
    missing: List[Dict[str, Any]] = []
    for part in parts or []:
        path = cached(str(part.get("latex") or ""), bool(part.get("display")),
                      color)
        if path:
            images[str(part.get("key"))] = path
        else:
            missing.append(part)
    return images, missing


def apply(document, parts: List[Dict[str, Any]], images: Dict[str, str],
          font_height: int = 18, max_width: int = 760,
          dpr: float = 1.0) -> int:
    """把文档里的占位符换成图片，返回真的插进去多少张。

    `dpr` 是屏幕的 devicePixelRatio（125% 缩放就是 1.25）：图片的**像素**按
    逻辑尺寸×dpr 准备，格式里写逻辑尺寸，让 Qt 1:1 落到设备像素上 —— 不然高分屏
    会把图片拉大，看起来就糊。
    """
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtGui import (QImage, QTextCharFormat, QTextCursor,
                               QTextDocument, QTextImageFormat)

    fallback = QTextCharFormat()
    try:
        fallback.setFontFamilies(["Consolas", "Cascadia Mono"])
    except Exception:
        pass

    ratio = max(1.0, float(dpr or 1.0))
    inserted = 0
    for part in parts or []:
        key = str(part.get("key") or "")
        if not key:
            continue
        found = document.find(key)
        if found.isNull():
            continue
        cursor = QTextCursor(found)
        path = images.get(key)
        image = QImage(path) if path else QImage()
        if image.isNull():
            cursor.insertText("$%s$" % str(part.get("latex") or "").strip(),
                              fallback)
            continue
        display = bool(part.get("display"))
        logical_w, logical_h = _target_size(image, display, font_height,
                                            max_width)
        # 真正要画的设备像素 = 逻辑尺寸 × dpr，按这个把图缩到位（1:1 映射，不糊）
        px_w = max(1, int(round(logical_w * ratio)))
        px_h = max(1, int(round(logical_h * ratio)))
        if (px_w, px_h) != (image.width(), image.height()):
            image = image.scaled(
                px_w, px_h,
                aspectMode=Qt.AspectRatioMode.IgnoreAspectRatio,
                mode=Qt.TransformationMode.SmoothTransformation)
        name = "math-%d" % inserted
        fmt = QTextImageFormat()
        fmt.setName(name)
        try:
            # 让图片和正文一起对齐（图片是「字」，不让它顶到行顶）
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)
        except Exception:
            pass
        fmt.setWidth(logical_w)
        fmt.setHeight(logical_h)
        document.addResource(QTextDocument.ImageResource, QUrl(name), image)
        cursor.insertImage(fmt)
        inserted += 1
    return inserted


def _target_size(image, display: bool, font_height: int,
                 max_width: int) -> Tuple[int, int]:
    """算出公式该显示成多大。

    渲染时文档是 12pt、按 `DPI`（360）出图，所以图片里的字比正文大得多；
    这里按 `正文字号 / (12pt × DPI/72)` 缩回去，公式的字就和正文一样大。
    行间公式再按可用宽度 / 高度收一道，免得一个矩阵占掉半屏。
    """
    width = max(1, image.width())
    height = max(1, image.height())
    scale = (max(9, int(font_height)) * 72.0) / (DOC_POINT_SIZE * float(DPI))
    if display:
        limit_w = max(120, int(max_width * DISPLAY_MAX_RATIO))
        if width * scale > limit_w:
            scale = limit_w / width
        if height * scale > DISPLAY_MAX_HEIGHT:
            scale = DISPLAY_MAX_HEIGHT / height
    else:
        if width * scale > max_width:
            scale = max_width / width
    return max(1, int(width * scale)), max(1, int(height * scale))


def has_math(text: str) -> bool:
    """这段文字里有没有公式（便宜的先判断，省得白跑一遍正则）。"""
    if not text:
        return False
    return ("$" in text) or ("\\(" in text) or ("\\[" in text) or \
        ("\\begin{" in text)
