"""本地模型运行时：自动拉起 llama.cpp / SGLang（命令行窗口隐藏），健康检查后可对话。

用户只需要在设置里填：运行时类型、模型文件（或模型目录）、端口、以及少量参数；
启动/停止全由程序完成，不弹命令行窗口（复用 proc.popen_hidden）。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .config import DATA_DIR
from .proc import hidden_kwargs, run_hidden

RUNTIMES = [
    {"id": "llamacpp", "name": "llama.cpp（llama-server，GGUF）"},
    {"id": "sglang", "name": "SGLang（python -m sglang.launch_server）"},
    {"id": "openai", "name": "外部 OpenAI 兼容接口（已在别处跑着）"},
]

DEFAULTS = {
    "llm_runtime": "llamacpp",
    "llm_model_path": "",          # GGUF 文件，或 SGLang 的模型目录
    "llm_port": "8080",
    "llm_ctx": "8192",
    "llm_gpu_layers": "0",
    "llm_extra_args": "",
    "llm_bin_dir": "",              # llama-server 所在目录（可留空，走 PATH）
    "llm_base_url": "http://127.0.0.1:8080/v1",
    "llm_api_key": "",
    "llm_model_name": "",
}


def settings(store) -> Dict[str, str]:
    values = {}
    for key, default in DEFAULTS.items():
        values[key] = store.get_setting(key, default) or default
    return values


def find_llama_server(bin_dir: str = "") -> str:
    """找 llama-server 可执行文件。"""
    candidates = []
    if bin_dir:
        base = Path(bin_dir)
        candidates += [base / "llama-server.exe", base / "llama-server"]
    found = shutil.which("llama-server")
    if found:
        candidates.insert(0, Path(found))
    candidates += [
        DATA_DIR / "llm" / "llama-server.exe",
        Path(r"C:\llama.cpp\build\bin\llama-server.exe"),
        Path(r"C:\Program Files\llama.cpp\llama-server.exe"),
    ]
    for candidate in candidates:
        try:
            if candidate and Path(candidate).exists():
                return str(candidate)
        except Exception:
            continue
    return ""


def find_python() -> str:
    return sys.executable or "python"


def build_command(cfg: Dict[str, str]) -> Optional[list]:
    """按运行时拼启动命令；缺参数返回 None。"""
    runtime = cfg.get("llm_runtime", "llamacpp")
    model = (cfg.get("llm_model_path") or "").strip()
    port = (cfg.get("llm_port") or "8080").strip()
    extra = (cfg.get("llm_extra_args") or "").split()
    if runtime == "llamacpp":
        binary = find_llama_server(cfg.get("llm_bin_dir", ""))
        if not binary or not model:
            return None
        command = [binary, "-m", model, "--port", port, "--host", "127.0.0.1"]
        ctx = (cfg.get("llm_ctx") or "").strip()
        if ctx:
            command += ["-c", ctx]
        gpu = (cfg.get("llm_gpu_layers") or "").strip()
        if gpu:
            command += ["-ngl", gpu]
        return command + extra
    if runtime == "sglang":
        if not model:
            return None
        command = [find_python(), "-m", "sglang.launch_server",
                   "--model-path", model, "--port", port, "--host", "127.0.0.1"]
        ctx = (cfg.get("llm_ctx") or "").strip()
        if ctx:
            command += ["--context-length", ctx]
        return command + extra
    return None


def health_url(cfg: Dict[str, str]) -> str:
    base = (cfg.get("llm_base_url") or "").strip() or "http://127.0.0.1:8080/v1"
    return base.rstrip("/") + "/models"


class LocalModelRunner(QObject):
    """管理本地模型进程：启动、健康检查、停止。"""

    status_changed = Signal(str, str)      # 文本, 语气（info/ok/error）
    ready = Signal(str)                    # base_url

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._process = None
        self._log_tail = ""
        self._log_path = DATA_DIR / "llm" / "server.log"
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._poll_health)
        self._wait_ticks = 0

    # -------------------------------------------------- 状态
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def base_url(self) -> str:
        return settings(self.store).get("llm_base_url", DEFAULTS["llm_base_url"])

    # -------------------------------------------------- 启动
    def start(self) -> bool:
        cfg = settings(self.store)
        runtime = cfg.get("llm_runtime", "llamacpp")
        if runtime == "openai":
            self.status_changed.emit("使用外部 OpenAI 兼容接口", "ok")
            self.ready.emit(self.base_url())
            return True
        if self.is_running():
            self.status_changed.emit("本地模型已经在运行", "info")
            return True
        command = build_command(cfg)
        if not command:
            if runtime == "llamacpp":
                hint = ("没找到 llama-server，或模型路径为空。"
                        "请下载 llama.cpp 的 Windows 版本，把 llama-server.exe 所在目录填到「运行时目录」")
            else:
                hint = "模型目录为空，或没安装 sglang（pip install sglang）"
            self.status_changed.emit(hint, "error")
            return False
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            log_file = open(self._log_path, "ab")
            self._process = __import__("subprocess").Popen(
                command,
                stdout=log_file,
                stderr=__import__("subprocess").STDOUT,
                stdin=__import__("subprocess").DEVNULL,
                **hidden_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001
            self.status_changed.emit(f"启动失败：{exc}", "error")
            return False
        self._wait_ticks = 0
        self._timer.start()
        self.status_changed.emit("正在启动本地模型（首次加载可能较慢）…", "info")
        return True

    # -------------------------------------------------- 健康检查
    def _poll_health(self) -> None:
        if self._process is None:
            self._timer.stop()
            return
        if self._process.poll() is not None:
            self._timer.stop()
            self._process = None
            tail = self._tail_log()
            self.status_changed.emit(
                "本地模型进程退出了。" + (f"\n{tail}" if tail else ""), "error")
            return
        if self._probe():
            self._timer.stop()
            self.status_changed.emit("本地模型已就绪", "ok")
            self.ready.emit(self.base_url())
            return
        self._wait_ticks += 1
        if self._wait_ticks % 6 == 0:
            self.status_changed.emit(
                f"等待模型加载…（{self._wait_ticks * 1.5:.0f} 秒）", "info")

    def _probe(self) -> bool:
        """探一下 /models 有没有响应（用当前设置里的地址，不是写死的端口）。"""
        try:
            import requests

            url = health_url(settings(self.store))
            response = requests.get(url, timeout=2)
            return response.status_code < 500
        except Exception:
            return False

    def _tail_log(self, lines: int = 6) -> str:
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(text.strip().splitlines()[-lines:])
        except Exception:
            return ""

    # -------------------------------------------------- 停止
    def stop(self) -> None:
        self._timer.stop()
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.terminate()
            for _ in range(20):
                if process.poll() is not None:
                    break
                time.sleep(0.1)
            if process.poll() is None:
                process.kill()
        except Exception:
            pass
        self.status_changed.emit("已停止本地模型", "info")


def settings_placeholder_base() -> str:
    """健康检查默认地址（没拿到设置时用）。"""
    return "http://127.0.0.1:8080/v1"
