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


def cache_key(latex: str, display: bool = False) -> str:
    raw = ("d:" if display else "i:") + (latex or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cached_png(latex: str, display: bool = False) -> Optional[str]:
    path = CACHE_DIR / f"{cache_key(latex, display)}.png"
    return str(path) if path.exists() else None


def _math_document(latex: str, display: bool) -> str:
    # standalone + preview 对「行间公式环境」支持不好，统一用行内数学 + \displaystyle
    body = ("$\\displaystyle " + latex + "$") if display else ("$" + latex + "$")
    return (
        "\\documentclass[12pt,border=2pt]{standalone}\n"
        "\\usepackage{amsmath,amssymb,bm}\n"
        "\\usepackage{xcolor}\n"
        "\\begin{document}\n"
        + body + "\n"
        "\\end{document}\n"
    )


def render(latex: str, display: bool = False, dpi: int = 220) -> Optional[str]:
    """渲染公式为 PNG，返回路径；失败返回 None。"""
    latex = (latex or "").strip()
    if not latex:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / f"{cache_key(latex, display)}.png"
    if target.exists():
        return str(target)

    engine = find_engine("xelatex")
    if not engine:
        return None
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    work = BUILD_DIR / uuid.uuid4().hex[:8]
    work.mkdir(parents=True, exist_ok=True)
    tex = work / "math.tex"
    tex.write_text(_math_document(latex, display), encoding="utf-8")
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
    if not pdf.exists():
        return None

    image = _pdf_to_image(str(pdf), dpi)
    if image is None:
        return None
    if image.save(str(target), "PNG"):
        return str(target)
    return None


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
