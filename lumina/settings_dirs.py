"""可配置目录：作业下载、LaTeX 编译缓存等都可以在设置里改。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import DATA_DIR

DEFAULT_DOWNLOADS = "elearning"
DEFAULT_LATEX_BUILD = "latex_build"
DEFAULT_LATEX_MATH = "latex_math"


def _resolve(value: str, default_rel: str) -> Path:
    text = (value or "").strip()
    if text:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = DATA_DIR / path
        return path
    return DATA_DIR / default_rel


def downloads_dir(store) -> Path:
    """作业 / 附件下载目录。"""
    value = store.get_setting("dir_downloads", "") if store is not None else ""
    return _resolve(str(value), DEFAULT_DOWNLOADS)


def latex_build_dir(store) -> Path:
    """LaTeX 编译临时目录（PDF、日志）。"""
    value = store.get_setting("dir_latex_build", "") if store is not None else ""
    return _resolve(str(value), DEFAULT_LATEX_BUILD)


def latex_math_dir(store) -> Path:
    """公式渲染缓存目录。"""
    value = store.get_setting("dir_latex_math", "") if store is not None else ""
    return _resolve(str(value), DEFAULT_LATEX_MATH)


def data_dir() -> Path:
    return DATA_DIR


def apply_to_modules(store) -> None:
    """把设置里的目录同步到各模块（启动时与保存设置后调用）。"""
    try:
        from . import latex_compile, latex_math

        build = latex_build_dir(store)
        build.mkdir(parents=True, exist_ok=True)
        latex_compile._BUILD_ROOT = build                 # noqa: SLF001
        math_dir = latex_math_dir(store)
        math_dir.mkdir(parents=True, exist_ok=True)
        latex_math.CACHE_DIR = math_dir                   # noqa: SLF001
        latex_math.BUILD_DIR = build / "math"             # noqa: SLF001
    except Exception:
        pass


def default_values(store) -> dict:
    return {
        "dir_downloads": store.get_setting("dir_downloads", "") or "",
        "dir_latex_build": store.get_setting("dir_latex_build", "") or "",
        "dir_latex_math": store.get_setting("dir_latex_math", "") or "",
    }
