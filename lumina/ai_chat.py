"""直接对话：走 OpenAI 兼容接口（本地 llama.cpp / SGLang / DeepSeek API 都行）。

* 提示词全部来自 `data/prompts.json`（通过 `prompts` 模块读），代码里不写死；
* 会自动把「今天的日程」拼进上下文，方便问「我今天还有什么没做完」；
* 请求放在 QThread 里，界面不卡。

设置项：llm_base_url / llm_api_key / llm_model_name（都在「本地模型」卡片里填）
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QTextBrowser, QVBoxLayout, QWidget,
)

from . import prompts, theme
from .widgets import SoftButton

DEFAULT_MODEL = "gpt-3.5-turbo"


# ------------------------------------------------------------------ 客户端
def config(store) -> Dict[str, str]:
    stored = (store.get_setting("llm_base_url", "") or "").strip()
    base = _normalize_base(stored or "http://127.0.0.1:8080/v1")
    return {
        "base_url": base,
        "api_key": store.get_secret("llm_api_key", "") or "",
        "model": (store.get_setting("llm_model_name", "") or "").strip(),
    }


def _normalize_base(base: str) -> str:
    """只有「光秃秃的域名」才补 /v1；DeepSeek、火山方舟（/api/v3）这类自带版本的不要动。"""
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    parsed = urlparse(base)
    path = (parsed.path or "").strip("/")
    if not path or re.fullmatch(r"v\d+", path):
        return base if re.fullmatch(r"v\d+", path) else base + "/v1"
    return base


def api_ready(store) -> bool:
    """接口能不能用：有地址 + 有模型名（API Key 可选，本地模型不需要）。"""
    cfg = config(store)
    return bool((cfg.get("base_url") or "").strip() and (cfg.get("model") or "").strip())


def context_lines(store, limit: int = 8) -> List[str]:
    """把今天的日程压成几行，作为上下文。"""
    lines: List[str] = []
    try:
        today = date.today().strftime("%Y-%m-%d")
        events = store.events_on(today)
    except Exception:
        return lines
    for item in events[:limit]:
        stamp = f"{item.get('start_time', '')[:5]}-{item.get('end_time', '')[:5]}"
        done = "（已完成）" if item.get("done") else ""
        lines.append(f"- {stamp} {item.get('title', '')}{done}")
    return lines


def build_messages(store, scene: Optional[str], user_text: str) -> List[Dict[str, str]]:
    system_parts = [prompts.system("assistant")]
    if scene:
        extra = prompts.scene(scene)
        if extra:
            system_parts.append(extra)
    context = context_lines(store)
    if context:
        system_parts.append("我今天的日程：\n" + "\n".join(context))
    return [
        {"role": "system", "content": "\n\n".join(part for part in system_parts if part)},
        {"role": "user", "content": user_text},
    ]


def chat(store, messages: List[Dict[str, str]], timeout: float = 90.0) -> str:
    """同步请求（在子线程里调用）。"""
    import requests

    cfg = config(store)
    url = cfg["base_url"] + "/chat/completions"
    model = cfg["model"] or DEFAULT_MODEL
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    payload = {"model": model, "messages": messages, "stream": False,
               "temperature": 0.6}
    response = requests.post(url, headers=headers, data=json.dumps(payload),
                             timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"接口返回 {response.status_code}：{response.text[:200]}")
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("接口没有返回内容")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    return str(content).strip() or "（模型返回了空内容）"


# ------------------------------------------------------------------ 连通性测试
class LlmTester(QObject):
    """在后台 ping 一下所填的接口，设置页用。"""

    finished = Signal(bool, str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._busy = False

    def is_busy(self) -> bool:
        return self._busy

    def test(self) -> None:
        if self._busy:
            return
        self._busy = True

        def worker() -> None:
            started = time.time()
            try:
                reply = chat(self.store, [{"role": "user", "content": "你好"}],
                             timeout=20.0)
                used = time.time() - started
                preview = reply.strip().replace("\n", " ")[:40]
                self.finished.emit(
                    True, f"连接正常（{used:.1f} 秒）：{preview}")
            except Exception as exc:  # noqa: BLE001
                self.finished.emit(False, f"连不上：{exc}")
            finally:
                self._busy = False

        threading.Thread(target=worker, name="lumina-llm-test",
                         daemon=True).start()


# ------------------------------------------------------------------ 流式对话
def stream_chat(store, messages: List[Dict[str, str]],
                on_delta, on_reasoning=None, on_done=None, on_error=None,
                should_stop=None, timeout: float = 180.0) -> None:
    """流式请求：边收边回调（`on_delta(文本片段)`、`on_reasoning(思考片段)`）。

    * 走 OpenAI 兼容的 `stream=true`（SSE，`data: {...}` 每行一个 chunk）；
    * DeepSeek R1 / 豆包等会额外给 `reasoning_content`，这里单独回调，
      界面上就能像 Harness 一样把「思考过程」折叠显示；
    * `should_stop()` 返回 True 时停止读取（界面上的「停止」按钮）。
    """
    import requests

    cfg = config(store)
    url = cfg["base_url"] + "/chat/completions"
    model = cfg["model"] or DEFAULT_MODEL
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.6,
    }
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload),
                                 timeout=timeout, stream=True)
    except Exception as exc:  # noqa: BLE001
        if on_error:
            on_error(f"请求失败：{exc}")
        return
    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.text[:300]
        except Exception:
            pass
        if on_error:
            on_error(f"接口返回 {response.status_code}：{detail}")
        return

    finished = False
    try:
        for raw in response.iter_lines(decode_unicode=False):
            if should_stop is not None and should_stop():
                break
            if not raw:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                finished = True
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning and on_reasoning is not None:
                on_reasoning(str(reasoning))
            piece = delta.get("content")
            if piece:
                on_delta(str(piece))
    except Exception as exc:  # noqa: BLE001
        if on_error:
            on_error(f"读取中断：{exc}")
        return
    finally:
        try:
            response.close()
        except Exception:
            pass
    if on_done:
        on_done(finished)


class StreamWorker(QObject):
    """把 `stream_chat` 包成后台线程里的对象，界面用信号接收。"""

    delta = Signal(str)
    reasoning = Signal(str)
    done = Signal(bool)
    failed = Signal(str)

    def __init__(self, store, messages: List[Dict[str, str]]):
        super().__init__()
        self.store = store
        self.messages = messages
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        stream_chat(
            self.store,
            self.messages,
            on_delta=self.delta.emit,
            on_reasoning=self.reasoning.emit,
            on_done=self.done.emit,
            on_error=self.failed.emit,
            should_stop=lambda: self._stop,
        )


# ------------------------------------------------------------------ 线程
class ChatWorker(QObject):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, store, messages: List[Dict[str, str]]):
        super().__init__()
        self.store = store
        self.messages = messages

    def run(self) -> None:
        try:
            self.done.emit(chat(self.store, self.messages))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


# ------------------------------------------------------------------ 对话框
class AiChatDialog(QDialog):
    """轻量的对话窗口：左边聊天记录，下面输入框。"""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._thread: Optional[QThread] = None
        self._history: List[Dict[str, str]] = []
        self.setWindowTitle("Lumina · AI 对话")
        self.resize(680, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel("AI 对话")
        self.title.setObjectName("H2")
        head.addWidget(self.title)
        head.addStretch(1)
        self.scene_select = QWidget()
        from .widgets import Select

        self.scene_box = Select()
        self.scene_box.setFixedWidth(150)
        self.scene_box.addItem("通用助手", "system:assistant")
        for key, item in (prompts.load().get("scenes") or {}).items():
            if isinstance(item, dict) and item.get("enabled", True):
                self.scene_box.addItem(str(item.get("title") or key), f"scene:{key}")
        head.addWidget(self.scene_box)
        self.settings_hint = QLabel("")
        self.settings_hint.setObjectName("Tiny")
        head.addWidget(self.settings_hint)
        root.addLayout(head)

        self.view = QTextBrowser()
        self.view.setStyleSheet(
            f"QTextBrowser {{ background: {theme.CARD_ALT}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 10px; padding: 10px; }}")
        root.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("问点什么…（Enter 发送）")
        self.input.setFixedHeight(36)
        self.input.returnPressed.connect(self.send)
        row.addWidget(self.input, 1)
        self.send_button = SoftButton("发送", "primary", height=36, min_width=88)
        self.send_button.clicked.connect(self.send)
        row.addWidget(self.send_button)
        root.addLayout(row)

        self._refresh_hint()
        self._append("system", "提示词来自 data/prompts.json；模型地址和 Key 在「设置 → 本地模型」里填。")

    # -------------------------------------------------- 辅助
    def _refresh_hint(self) -> None:
        cfg = config(self.store)
        missing = []
        if not cfg["model"]:
            missing.append("模型名")
        hint = "已就绪" if not missing else "还缺：" + "、".join(missing)
        self.settings_hint.setText(f"{cfg['base_url']} · {hint}")

    def _append(self, role: str, text: str) -> None:
        color = theme.ACCENT if role == "user" else theme.TEXT_SOFT
        label = "我" if role == "user" else ("AI" if role == "assistant" else "说明")
        body = (text or "").replace("\n", "<br>")
        self.view.append(
            f"<p style='color:{color}; margin:6px 4px;'><b>{label}：</b>{body}</p>")
        try:
            self.view.verticalScrollBar().setValue(
                self.view.verticalScrollBar().maximum())
        except Exception:
            pass

    # -------------------------------------------------- 发送
    def send(self) -> None:
        text = self.input.text().strip()
        if not text or self._busy():
            return
        self.input.clear()
        self._append("user", text)
        scene_key = self.scene_box.currentData() or "system:assistant"
        scene = scene_key.split(":", 1)[1] if scene_key.startswith("scene:") else None
        messages = build_messages(self.store, scene, text)
        self._history.append({"role": "user", "content": text})
        full = messages[:-1] + self._history[-8:]
        self.send_button.setEnabled(False)

        worker = ChatWorker(self.store, full)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _busy(self) -> bool:
        thread = self._thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            self._thread = None
            return False

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None

    def _on_done(self, text: str) -> None:
        self.send_button.setEnabled(True)
        self._append("assistant", text)
        self._history.append({"role": "assistant", "content": text})

    def _on_failed(self, message: str) -> None:
        self.send_button.setEnabled(True)
        self._append("system", f"请求失败：{message}")
