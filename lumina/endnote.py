"""EndNote 集成：检测安装、导出 .enw/.ris、必要时引导经 ehall 安装。

用户要求（D8）：一键引用走 EndNote；**本地没装就先问要不要经 ehall 资源安装**。

这里的做法：
* `detect()`：查注册表 + 常见安装目录，找到 `EndNote.exe` 就算装了；
* `export(store, items, style)`：把文献写成 `.enw`（EndNote 原生）或 `.ris`，
  放到文献库的 `exports/` 目录，返回文件路径；
* `open_with_endnote(path)`：装了就直接用 EndNote 打开导入；没装返回 False，
  由界面弹窗问用户「要不要经 ehall 装」；
* `install_hint()`：给出经 ehall 安装的步骤文案（连同 ehall 链接）。

全部只在本机操作，不联网、不改注册表。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

EXE_NAMES = ("EndNote.exe", "EndNote20.exe", "EndNote21.exe", "EndNote X9.exe")
EHALL_URL = "https://ehall.fudan.edu.cn/"
SOFTWARE_CENTER_URL = "https://ehall.fudan.edu.cn/"


def _candidate_dirs() -> List[Path]:
    roots: List[Path] = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(env)
        if value:
            roots.append(Path(value))
    for drive in ("C:", "D:", "E:"):
        roots.append(Path(drive + "\\Program Files"))
        roots.append(Path(drive + "\\Program Files (x86)"))
    dirs: List[Path] = []
    for root in roots:
        for name in ("EndNote", "EndNote 20", "EndNote 21", "EndNote X9",
                     "Clarivate\\EndNote", "Clarivate\\EndNote 21",
                     "ResearchSoft\\EndNote"):
            path = root / name
            if path.exists():
                dirs.append(path)
    return dirs


def _registry_path() -> str:
    try:
        import winreg
    except Exception:
        return ""
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Clarivate\EndNote"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Clarivate\EndNote"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Clarivate\EndNote"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ISI ResearchSoft\EndNote"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\ISI ResearchSoft\EndNote"),
    ]
    for hive, sub in keys:
        try:
            with winreg.OpenKey(hive, sub) as key:
                for index in range(0, 8):
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    text = str(value or "")
                    if text.lower().endswith(".exe") and "endnote" in text.lower():
                        if Path(text).exists():
                            return text
                    if name.lower() in ("installdir", "installdirectory",
                                        "install_path", "path") and text:
                        for exe in EXE_NAMES:
                            candidate = Path(text) / exe
                            if candidate.exists():
                                return str(candidate)
        except OSError:
            continue
    return ""


def detect() -> Dict[str, Any]:
    """本机有没有 EndNote？"""
    found = _registry_path()
    if not found:
        for folder in _candidate_dirs():
            for exe in EXE_NAMES:
                candidate = folder / exe
                if candidate.exists():
                    found = str(candidate)
                    break
            if found:
                break
            try:
                for child in folder.iterdir():
                    if child.name.lower().endswith(".exe") and "endnote" in \
                            child.name.lower():
                        found = str(child)
                        break
            except Exception:
                continue
            if found:
                break
    return {"found": bool(found), "path": found,
            "summary": (f"本机装了 EndNote：{found}" if found
                        else "本机没找到 EndNote")}


def export(store, items: List[Dict[str, Any]], style: str = "enw") -> Dict[str, Any]:
    """把文献导出成 EndNote 能直接导入的文件（.enw / .ris）。"""
    from . import lit

    if not items:
        return {"ok": False, "summary": "没有选中任何文献"}
    text = lit.citations(items, style if style in ("enw", "ris") else "enw")
    suffix = ".ris" if style == "ris" else ".enw"
    folder = lit.library_root() / "exports"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"建不了导出目录：{exc}"}
    from datetime import datetime

    name = "refs-" + datetime.now().strftime("%Y%m%d-%H%M%S") + suffix
    target = folder / name
    try:
        target.write_text(text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"写不了导出文件：{exc}"}
    info = detect()
    return {"ok": True, "path": str(target), "style": style,
            "count": len(items), "endnote": info,
            "summary": (f"已导出 {len(items)} 条到 {target}"
                        + ("；本机有 EndNote，可以直接打开导入"
                           if info["found"] else
                           "；本机没装 EndNote，可以双击导入 Zotero/Mendeley，"
                           "或者经 ehall 装一个"))}


def open_with_endnote(path: str) -> bool:
    """用 EndNote 打开导出文件（装了才成功）。"""
    info = detect()
    if not info["found"]:
        return False
    try:
        subprocess.Popen([info["path"], str(path)])
        return True
    except Exception:
        try:
            os.startfile(str(path))       # noqa: S606 - 用户主动点的
            return True
        except Exception:
            return False


def install_hint() -> Dict[str, str]:
    """没装 EndNote 时给用户的话（并问要不要经 ehall 装）。"""
    return {
        "title": "本机没有 EndNote",
        "message": ("参考文献要靠 EndNote 一键导入的话，得先装一个。"
                    "复旦的正版软件在 ehall 的「正版软件」/「软件资源」里，"
                    "用统一身份认证登录后下载安装即可。"),
        "ehall": EHALL_URL,
        "software": SOFTWARE_CENTER_URL,
        "ask": "要不要现在带你到 ehall 的软件页面？装上之后我可以直接把 .enw 打开导入。",
    }
