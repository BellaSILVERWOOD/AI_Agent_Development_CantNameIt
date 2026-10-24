"""调用 XeLaTeX 编译预览（后台线程，不卡界面）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, Signal

from .config import DATA_DIR
from .proc import hidden_kwargs

_BUILD_ROOT = DATA_DIR / "latex_build"


def find_engine(name: str = "xelatex") -> str:
    """找 LaTeX 引擎：先看 PATH，再试常见安装位置。"""
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        Path(r"D:\Program Files\MiKTeX\miktex\bin\x64") / f"{name}.exe",
        Path(r"C:\Program Files\MiKTeX\miktex\bin\x64") / f"{name}.exe",
        Path(r"C:\texlive\2024\bin\windows") / f"{name}.exe",
        Path(r"C:\texlive\2023\bin\windows") / f"{name}.exe",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return ""


def engine_available() -> bool:
    return bool(find_engine("xelatex"))


class LatexCompiler(QObject):
    """在后台编译一段 LaTeX，成功后给出 PDF 路径。"""

    finished = Signal(bool, str, str)   # ok, pdf_path, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

    def is_busy(self) -> bool:
        return self._busy

    def compile(self, source: str, keep_dir: bool = False) -> None:
        if self._busy:
            return
        engine = find_engine("xelatex")
        if not engine:
            self.finished.emit(False, "", "没有找到 xelatex，请先安装 MiKTeX 或 TeX Live")
            return
        self._busy = True
        _BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        work = _BUILD_ROOT / uuid.uuid4().hex[:8]
        work.mkdir(parents=True, exist_ok=True)
        tex_path = work / "preview.tex"
        tex_path.write_text(source, encoding="utf-8")

        def worker() -> None:
            log = ""
            pdf_path = ""
            ok = False
            try:
                ok, log = self._run(engine, tex_path, work)
                candidate = work / "preview.pdf"
                if ok and candidate.exists():
                    pdf_path = str(candidate)
            except Exception as exc:  # noqa: BLE001
                log = f"编译异常：{exc}"
            finally:
                self._busy = False
            if not ok:
                summary = self._summarise(log)
                self.finished.emit(False, "", summary)
            else:
                self.finished.emit(True, pdf_path, "")

        threading.Thread(target=worker, name="lumina-latex", daemon=True).start()

    @staticmethod
    def _run(engine: str, tex_path: Path, work: Path) -> tuple:
        base: List[str] = [
            engine,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            "-output-directory=" + str(work),
        ]
        # MiKTeX 自动装包会卡住，先禁用；不支持这个开关时再退回普通调用
        attempts = (base + ["-disable-installer", tex_path.name],
                    base + [tex_path.name])
        # 让 MiKTeX 不要在后台弹「安装宏包」窗
        env = dict(os.environ)
        env.setdefault("MIKTEX_ENABLEINSTALLER", "0")
        env.setdefault("MIKTEX_AUTOINSTALL", "0")
        log = ""
        for command in attempts:
            try:
                proc = subprocess.run(
                    command, cwd=str(work), capture_output=True, timeout=120,
                    text=True, encoding="utf-8", errors="replace",
                    env=env, **hidden_kwargs(),
                )
            except subprocess.TimeoutExpired:
                return False, "编译超时（>120 秒），可能是宏包自动安装卡住了"
            except Exception as exc:  # noqa: BLE001
                return False, f"无法启动编译器：{exc}"
            log = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode == 0:
                return True, log
            if "unrecognized option" in log or "unknown option" in log.lower():
                continue
            return False, log
        return False, log

    @staticmethod
    def _summarise(log: str, limit: int = 6) -> str:
        """从 LaTeX 日志里挑出有用的报错行。"""
        lines = [line.strip() for line in (log or "").splitlines()]
        picked: List[str] = []
        for line in lines:
            if not line:
                continue
            if line.startswith("!") or ": " in line and (".tex:" in line or "Error" in line):
                picked.append(line)
            elif "Undefined control sequence" in line or "Missing" in line:
                picked.append(line)
            if len(picked) >= limit:
                break
        if not picked:
            tail = [line for line in lines if line][-limit:]
            picked = tail or ["编译失败，但没有拿到日志"]
        return "\n".join(picked)
