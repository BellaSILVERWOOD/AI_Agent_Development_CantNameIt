"""把 LaTeX 公式渲染成图片，用于「文档视图」里内嵌显示。

做法：拼一个最小 standalone 文档 → xelatex 出 PDF → 用 QtPdf 把第一页渲染成 PNG。
结果按代码哈希缓存在 data/latex_math/，同样的公式只编译一次。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from .config import DATA_DIR
from .latex_compile import find_engine
from .proc import hidden_kwargs

CACHE_DIR = DATA_DIR / "latex_math"
BUILD_DIR = DATA_DIR / "latex_build" / "math"


def cache_key(latex: str, display: bool = False, dpi: int = 220,
              color: str = "") -> str:
    raw = "%s%s|%d|%s" % ("d:" if display else "i:", latex or "", int(dpi),
                          (color or "").lower())
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cached_png(latex: str, display: bool = False, dpi: int = 220,
               color: str = "") -> Optional[str]:
    path = CACHE_DIR / f"{cache_key(latex, display, dpi, color)}.png"
    return str(path) if path.exists() else None


def _math_document(latex: str, display: bool, color: str = "") -> str:
    # standalone + preview 对「行间公式环境」支持不好，统一用行内数学 + \displaystyle
    body = ("$\\displaystyle " + latex + "$") if display else ("$" + latex + "$")
    if color:
        body = "\\color[HTML]{%s}%s" % (color.lstrip("#").upper(), body)
    return (
        "\\documentclass[12pt,border=2pt]{standalone}\n"
        "\\usepackage{amsmath,amssymb,bm}\n"
        "\\usepackage{xcolor}\n"
        "\\begin{document}\n"
        + body + "\n"
        "\\end{document}\n"
    )


def compile_pdf(latex: str, display: bool = False,
                color: str = "") -> Optional[str]:
    """只跑 xelatex 出 PDF（**不碰 Qt**），给后台线程用。

    返回 PDF 路径；失败返回 None。PDF 放在 `latex_build/math/<随机名>/` 下。
    """
    latex = (latex or "").strip()
    if not latex:
        return None
    engine = find_engine("xelatex")
    if not engine:
        return None
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    work = BUILD_DIR / uuid.uuid4().hex[:8]
    work.mkdir(parents=True, exist_ok=True)
    tex = work / "math.tex"
    tex.write_text(_math_document(latex, display, color), encoding="utf-8")
    commands = [
        [engine, "-interaction=nonstopmode", "-halt-on-error",
         "-disable-installer", "-output-directory=" + str(work), tex.name],
        [engine, "-interaction=nonstopmode", "-halt-on-error",
         "-output-directory=" + str(work), tex.name],
    ]
    pdf = work / "math.pdf"
    for command in commands:
        try:
            env = dict(os.environ)
            env.setdefault("MIKTEX_ENABLEINSTALLER", "0")
            env.setdefault("MIKTEX_AUTOINSTALL", "0")
            subprocess.run(command, cwd=str(work), capture_output=True,
                           timeout=60, text=True, encoding="utf-8", errors="replace",
                           env=env, **hidden_kwargs())
        except Exception:
            continue
        if pdf.exists():
            break
    return str(pdf) if pdf.exists() else None


def png_from_pdf(pdf_path: str, latex: str, display: bool = False,
                 dpi: int = 220, color: str = "") -> Optional[str]:
    """把 PDF 第一页转成 PNG 并写进缓存（**必须在有 QGuiApplication 的线程**里跑）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / f"{cache_key(latex, display, dpi, color)}.png"
    if target.exists():
        return str(target)
    image = _pdf_to_image(str(pdf_path), dpi)
    if image is None:
        return None
    return str(target) if image.save(str(target), "PNG") else None


def render(latex: str, display: bool = False, dpi: int = 220,
           color: str = "") -> Optional[str]:
    """渲染公式为 PNG，返回路径；失败返回 None。

    `dpi` 越大越清晰（默认 220；插入文档时用 360 再按字号缩小，看着才不糊）；
    `color` 是公式文字颜色（深色主题下要传亮色，否则黑字糊在黑底上）。
    """
    latex = (latex or "").strip()
    if not latex:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / f"{cache_key(latex, display, dpi, color)}.png"
    if target.exists():
        return str(target)
    pdf = compile_pdf(latex, display, color)
    if not pdf:
        return None
    return png_from_pdf(pdf, latex, display, dpi, color)


def _pdf_to_image(pdf_path: str, dpi: int):
    """用 QtPdf 把第一页渲染成 QImage（不依赖外部 poppler）。"""
    try:
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage
        from PySide6.QtPdf import QPdfDocument
    except Exception:
        return None
    document = QPdfDocument()
    error = document.load(pdf_path)
    try:
        from PySide6.QtPdf import QPdfDocument as _Doc

        if error != _Doc.Error.None_:
            document.close()
            return None
        page_size = document.pagePointSize(0)
        scale = dpi / 72.0
        size = QSize(max(1, int(page_size.width() * scale)),
                     max(1, int(page_size.height() * scale)))
        image = document.render(0, size)
        document.close()
        if image is None or image.isNull():
            return None
        return image
    except Exception:
        try:
            document.close()
        except Exception:
            pass
        return None
