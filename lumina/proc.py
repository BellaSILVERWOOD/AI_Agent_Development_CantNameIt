"""启动外部进程的统一入口：Windows 上不弹控制台窗口。

GUI 程序（pythonw / 无控制台）启动控制台子进程时，Windows 默认会给它新建一个
黑框。加上 CREATE_NO_WINDOW + STARTF_USESHOWWINDOW/SW_HIDE 就不会再跳出来。
"""
from __future__ import annotations

import subprocess
import sys
from typing import List, Optional


def hidden_kwargs() -> dict:
    """返回让子进程不显示窗口的 subprocess 参数。"""
    kwargs: dict = {}
    if sys.platform == "win32":
        flags = 0
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = flags
        try:
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0          # SW_HIDE
            kwargs["startupinfo"] = startup
        except Exception:
            pass
    return kwargs


def run_hidden(command: List[str], cwd: Optional[str] = None, timeout: float = 60.0,
               capture: bool = True) -> subprocess.CompletedProcess:
    """跑一条命令，窗口不显示，输出被抓走。"""
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        **hidden_kwargs(),
    )


def popen_hidden(command: List[str], cwd: Optional[str] = None) -> subprocess.Popen:
    """启动一个后台进程，窗口不显示。"""
    return subprocess.Popen(command, cwd=cwd, **hidden_kwargs())
