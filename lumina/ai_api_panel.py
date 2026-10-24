"""API 服务页：内置的流式对话窗口（不依赖内嵌网页）。

对标 DeepSeek Harness Desktop 的基本体验：
* 边生成边显示（SSE 流式），不是等整段回来才出现；
* 「思考过程」单独一块，可以折叠/展开（DeepSeek-R1、豆包等模型会返回 reasoning）；
* 随时可以「停止生成」，已经生成的部分保留；
* 回答按 Markdown 渲染（标题、列表、代码块、粗体）；
* 顶部能选「通用助手 / 场景提示词」，和提示词配置里的一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QTextBrowser, QVBoxLayout, QWidget,
)

from . import ai_chat, prompts, theme
from .widgets import Select, SoftButton

MAX_CONTEXT_TURNS = 8


def _colors() -> Dict[str, str]:
    mode = "dark" if theme.effective_mode() == "dark" else "light"
    return theme.PALETTES.get(mode) or theme.PALETTES["light"]


def markdown_to_html(text: str) -> str:
    """把 Markdown 转成 HTML（用 Qt 自带的解析器，不引第三方库）。"""
    try:
        document = QTextDocument()
        document.setMarkdown(text or "")
        return document.toHtml()
    except Exception:
        escaped = (text or "").replace("<", "&lt;").replace("\n", "<br>")
        return f"<div>{escaped}</div>"


class ApiChatPanel(QWidget):
    """一个极简、但能流式显示的对话面板。"""

    agent_requested = Signal()
    settings_requested = Signal()

    def __init__(self, store, compact: bool = False, parent=None):
        super().__init__(parent)
        self.store = store
        self.compact = bool(compact)
        self._history: List[Dict[str, str]] = []
        self._answer = ""
        self._reasoning = ""
        self._thread: Optional[QThread] = None
        self._worker: Optional[ai_chat.StreamWorker] = None
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(60)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)

        colors = _colors()
        root = QVBoxLayout(self)
        root.setContentsMargins(8 if compact else 16, 8 if compact else 12,
                                8 if compact else 16, 8 if compact else 12)
        root.setSpacing(8)

        # -------------------------------------------------- 顶部
        head = QHBoxLayout()
        head.setSpacing(6)
        self.scene_box = Select()
        self.scene_box.setFixedWidth(120 if compact else 150)
        self.scene_box.addItem("通用助手", "system:assistant")
        try:
            for key, item in (prompts.load().get("scenes") or {}).items():
                if isinstance(item, dict) and item.get("enabled", True):
                    self.scene_box.addItem(str(item.get("title") or key),
                                           f"scene:{key}")
        except Exception:
            pass
        head.addWidget(self.scene_box)

        self.thinking_button = SoftButton("思考过程", "ghost", height=28,
                                          min_width=78, point_size=8.4)
        self.thinking_button.setToolTip("显示/隐藏模型的思考过程")
        self.thinking_button.clicked.connect(self._toggle_thinking)
        head.addWidget(self.thinking_button)
        head.addStretch(1)
        self.model_label = QLabel("")
        self.model_label.setObjectName("Tiny")
        head.addWidget(self.model_label)
        self.agent_button = SoftButton("Agent", "ghost", height=28,
                                       min_width=64, point_size=8.4)
        self.agent_button.setToolTip("读微信消息，识别要加日程的事")
        self.agent_button.clicked.connect(self.agent_requested.emit)
        head.addWidget(self.agent_button)
        self.clear_button = SoftButton("清空", "ghost", height=28,
                                       min_width=56, point_size=8.4)
        self.clear_button.clicked.connect(self.clear)
        head.addWidget(self.clear_button)
        root.addLayout(head)

        # -------------------------------------------------- 对话区
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setStyleSheet(
            f"QTextBrowser {{ background: {colors['CARD']}; color: {colors['TEXT']};"
            f" border: 1px solid {colors['BORDER']}; border-radius: 12px; padding: 12px; }}")
        root.addWidget(self.view, 1)

        # -------------------------------------------------- 输入
        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("问点什么…（Enter 发送）")
        self.input.setFixedHeight(34)
        self.input.returnPressed.connect(self.send)
        row.addWidget(self.input, 1)
        self.send_button = SoftButton("发送", "primary", height=34,
                                      min_width=76, point_size=8.8)
        self.send_button.clicked.connect(self.send)
        row.addWidget(self.send_button)
        root.addLayout(row)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self._thinking_visible = True
        self.refresh_model_label()
        self._render()

    # -------------------------------------------------- 状态
    def refresh_model_label(self) -> None:
        cfg = ai_chat.config(self.store)
        base = cfg.get("base_url") or "（未配置接口地址）"
        model = cfg.get("model") or "（未填模型名）"
        keyed = "有 Key" if cfg.get("api_key") else "没填 Key"
        self.model_label.setText(f"{model} · {base} · {keyed}")

    def _toggle_thinking(self) -> None:
        self._thinking_visible = not self._thinking_visible
        self.thinking_button.setKind(
            "primary" if self._thinking_visible else "ghost")
        self._render()

    # -------------------------------------------------- 渲染
    @staticmethod
    def _reasoning_block(text: str, colors: Dict[str, str]) -> str:
        body = (text or "").replace("<", "&lt;").replace("\n", "<br>")
        return (
            f"<div style='margin:8px 2px; padding:8px 10px;"
            f" border-left:3px solid {colors['BORDER']};"
            f" color:{colors['MUTED']}; font-size:12px'>"
            f"<b>思考过程</b><br>{body}</div>")

    def _render(self) -> None:
        colors = _colors()
        blocks: List[str] = []
        for item in self._history:
            role = item.get("role")
            content = str(item.get("content") or "")
            if role == "user":
                body = (content.replace("<", "&lt;").replace("\n", "<br>"))
                blocks.append(
                    f"<p style='color:{colors['TEXT']}; margin:10px 2px 4px 2px'>"
                    f"<b>我：</b>{body}</p>")
            elif role == "assistant":
                reasoning = str(item.get("reasoning") or "")
                if reasoning and self._thinking_visible:
                    blocks.append(self._reasoning_block(reasoning, colors))
                blocks.append(
                    f"<p style='color:{colors['TEXT']}; margin:10px 2px 0 2px'>"
                    f"<b>AI：</b></p>"
                    f"<div style='color:{colors['TEXT']}'>{markdown_to_html(content)}</div>")
        if self._reasoning and self._thinking_visible:
            blocks.append(self._reasoning_block(self._reasoning, colors))
        if self._answer:
            blocks.append(
                f"<p style='color:{colors['TEXT']}; margin:8px 2px 0 2px'><b>AI：</b></p>"
                f"<div style='color:{colors['TEXT']}'>{markdown_to_html(self._answer)}</div>")
        if not blocks:
            blocks.append(
                f"<div style='color:{colors['MUTED']}'>"
                f"<p><b>API 对话</b></p>"
                f"<p>直接用配置好的接口地址 / 模型名 / API Key 对话，回答是流式的。"
                f"接口在 设置 → AI 接口 里填。</p></div>")
        if self._streaming:
            blocks.append(
                f"<p style='color:{colors['MUTED']}; font-size:11px'>正在生成…</p>")
        self.view.setHtml("".join(blocks))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # -------------------------------------------------- 发送
    @property
    def _streaming(self) -> bool:
        thread = self._thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            self._thread = None
            return False

    def send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        if self._streaming:
            self.stop()
            return
        if not ai_chat.api_ready(self.store):
            self.status.setText(
                "还没配置接口：到 设置 → AI 接口 选服务、填模型名和 API Key")
            return
        self.input.clear()
        self._history.append({"role": "user", "content": text})
        self._answer = ""
        self._reasoning = ""
        self.status.setText("正在生成…（可以点「停止」）")
        self.send_button.setText("停止")
        self._render()
        self._render_timer.start(60)

        messages = ai_chat.build_messages(
            self.store, self._scene_name(), text)
        # 带上最近几轮上下文
        recent = [m for m in self._history[:-1]][-MAX_CONTEXT_TURNS * 2:]
        if recent:
            messages = messages[:1] + recent + messages[-1:]

        worker = ai_chat.StreamWorker(self.store, messages)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.delta.connect(self._on_delta)
        worker.reasoning.connect(self._on_reasoning)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def stop(self) -> None:
        worker = self._worker
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
        self.status.setText("已停止生成（保留已生成的内容）")

    def _scene_name(self) -> Optional[str]:
        data = self.scene_box.currentData() or "system:assistant"
        if str(data).startswith("scene:"):
            return str(data).split(":", 1)[1]
        return None

    def _on_delta(self, piece: str) -> None:
        self._answer += piece
        if not self._render_timer.isActive():
            self._render_timer.start(60)

    def _on_reasoning(self, piece: str) -> None:
        self._reasoning += piece
        if not self._render_timer.isActive():
            self._render_timer.start(60)

    def _on_done(self, finished: bool) -> None:
        self.send_button.setText("发送")
        answer = self._answer.strip() or "（模型返回了空内容）"
        reasoning = self._reasoning.strip()
        self._history.append({"role": "assistant", "content": answer,
                              "reasoning": reasoning})
        self._answer = ""
        self._reasoning = ""
        self.status.setText("完成" if finished else "已停止")
        self._render()

    def _on_failed(self, message: str) -> None:
        self.send_button.setText("发送")
        self.status.setText(f"请求失败：{message}")
        if self._answer.strip():
            self._history.append({"role": "assistant",
                                  "content": self._answer.strip(),
                                  "reasoning": self._reasoning.strip()})
        self._answer = ""
        self._reasoning = ""
        self._render()

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self.send_button.setText("发送")
        # 线程真正结束后再画一次：否则「正在生成…」会残留在已完成的回答下面
        self._render()

    def clear(self) -> None:
        if self._streaming:
            self.stop()
        self._history = []
        self._answer = ""
        self._reasoning = ""
        self.status.setText("已清空")
        self.refresh_model_label()
        self._render()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_model_label()
        self._render()
