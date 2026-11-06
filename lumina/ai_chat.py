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


def build_messages(store, scene: Optional[str], user_text: str,
                   tools: bool = True,
                   images: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    system_parts = []
    # ① 最前面：当前时间 + 本机 IP（每次发请求都现算，日期不会过期）
    try:
        from . import context_info

        if (store.get_setting("prompt_prepend_context", "1") or "1") == "1":
            with_public = (store.get_setting("prompt_public_ip", "0") or "0") == "1"
            block = context_info.context_block(store, with_public_ip=with_public)
            if block:
                system_parts.append(block)
    except Exception:
        pass
    # ② 基础人设（data/prompts.json）
    system_parts.append(prompts.system("assistant"))
    if scene:
        extra = prompts.scene(scene)
        if extra:
            system_parts.append(extra)
    # ③ 高级：外部程序现场生成的提示词（Python / C / C++ / exe 都行）
    try:
        from . import prompt_gen

        generated, error = prompt_gen.generate(
            store, {"scene": scene or "assistant"})
        if generated:
            system_parts.append(generated)
        if error:
            try:
                store.set_setting("prompt_gen_last_error", str(error))
            except Exception:
                pass
        elif generated:
            try:
                store.set_setting("prompt_gen_last_error", "")
            except Exception:
                pass
    except Exception:
        pass
    # 告诉模型「你可以真的动这个应用」（日程 / eLearning / 课表）
    if tools:
        try:
            from . import ai_tools

            block = ai_tools.system_block(store)
            if block:
                system_parts.append(block)
        except Exception:
            pass
    context = context_lines(store)
    if context:
        system_parts.append("我今天的日程：\n" + "\n".join(context))
    # 长期记忆（data/memory.md）：用户说「记住……」时写在这里
    try:
        from . import memory

        block = memory.context_block()
        if block:
            system_parts.append(block)
    except Exception:
        pass
    # 图片附件：按 OpenAI 兼容的多模态写法放在用户消息里（只有支持视觉的模型认）
    user_content: Any = user_text
    if images:
        parts: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
        for item in images:
            url = str((item or {}).get("url") or "")
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
        if len(parts) > 1:
            user_content = parts
    return [
        {"role": "system", "content": "\n\n".join(part for part in system_parts if part)},
        {"role": "user", "content": user_content},
    ]


def chat(store, messages: List[Dict[str, str]], timeout: float = 90.0) -> str:
    """同步请求（在子线程里调用）。"""
    import requests

    cfg = config(store)
    url = cfg["base_url"] + "/chat/completions"
    model = cfg["model"] or DEFAULT_MODEL
    # 管理员的模型接口同样要授权
    from . import api_grant

    api_grant.require(store, cfg["base_url"])
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    payload = {"model": model, "messages": messages, "stream": False,
               "temperature": 0.6}
    # 思考开关（关闭思考 / 强制思考）也要对同步请求生效
    try:
        from . import ai_thinking

        payload.update(ai_thinking.extra_payload(store, model, cfg["base_url"]))
        # Qwen3 这类：消息尾部加 /no_think（服务端忽略参数时这是最后一道保险）
        payload["messages"] = ai_thinking.decorate_messages(
            messages, store, model, cfg["base_url"])
    except Exception:
        pass
    response = requests.post(url, headers=headers, data=json.dumps(payload),
                             timeout=timeout)
    if response.status_code in (400, 404, 422) and len(payload) > 4:
        # 接口不认思考参数：去掉再试一次
        for key in ("thinking", "enable_thinking", "reasoning_effort",
                    "chat_template_kwargs"):
            payload.pop(key, None)
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
def parse_usage(usage: Dict[str, Any]) -> Dict[str, Any]:
    """把各家接口的 usage 统一成 {prompt, completion, cache_hit, cache_miss}。

    各家「缓存命中」的字段名不一样：
    * DeepSeek：`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`
    * OpenAI：`prompt_tokens_details.cached_tokens`
    * Anthropic 兼容网关：`cache_read_input_tokens` / `cache_creation_input_tokens`
    * 有些代理只在顶层给 `cached_tokens`
    """
    data = usage or {}

    def number(*keys) -> int:
        for key in keys:
            value = data
            for part in key.split("."):
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    value = None
                    break
            if value is None:
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0

    prompt = number("prompt_tokens", "input_tokens")
    completion = number("completion_tokens", "output_tokens")
    cache_hit = number("prompt_cache_hit_tokens",
                       "prompt_tokens_details.cached_tokens",
                       "cache_read_input_tokens", "cached_tokens")
    cache_miss = number("prompt_cache_miss_tokens")
    if not cache_miss and cache_hit and prompt >= cache_hit:
        cache_miss = prompt - cache_hit
    return {"prompt": prompt, "completion": completion,
            "cache_hit": cache_hit, "cache_miss": cache_miss,
            "cache_reported": bool(cache_hit or cache_miss)}


def tokens_per_second(completion_tokens: int, gen_seconds: float) -> float:
    """出字速度（token/s）。太短的窗口算不出稳定值，就报 0。"""
    try:
        completion = int(completion_tokens or 0)
        seconds = float(gen_seconds or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if completion <= 0 or seconds <= 0.001:
        return 0.0
    return min(2000.0, completion / seconds)


def stream_chat(store, messages: List[Dict[str, Any]],
                on_delta, on_reasoning=None, on_done=None, on_error=None,
                on_usage=None, should_stop=None, timeout: float = 180.0,
                tools: Optional[List[Dict[str, Any]]] = None,
                on_tool_calls=None, on_metrics=None) -> None:
    """流式请求：边收边回调（`on_delta(文本片段)`、`on_reasoning(思考片段)`）。

    * 走 OpenAI 兼容的 `stream=true`（SSE，`data: {...}` 每行一个 chunk）；
    * DeepSeek R1 / 豆包等会额外给 `reasoning_content`，这里单独回调，
      界面上就能像 Harness 一样把「思考过程」折叠显示；
    * 会顺便要 `stream_options.include_usage`，接口支持的话最后一块带 token 用量
      （不支持就自动重试一次不带这个字段）；
    * `should_stop()` 返回 True 时停止读取（界面上的「停止」按钮）；
    * 给了 `tools` 就带上工具声明；模型要求调工具时，把整理好的调用列表交给
      `on_tool_calls([{id, name, arguments}, ...])`（流式分片会在这里拼回完整参数）；
    * `on_metrics({"ttft": 秒, "tokens_per_second": …, "completion_tokens": …,
      "elapsed": 秒})`：首字延迟（TTFT）与出字速度；接口不给 usage 时按字数估算。
    """
    import requests

    cfg = config(store)
    url = cfg["base_url"] + "/chat/completions"
    model = cfg["model"] or DEFAULT_MODEL
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    started = time.time()
    first_token_at: Optional[float] = None
    streamed_chars = 0

    # 用的是管理员自己的模型接口？那必须有还没过期的授权（每次最多 5 小时）
    try:
        from . import api_grant

        api_grant.require(store, cfg["base_url"])
    except api_grant.GrantError as exc:
        if on_error:
            on_error(str(exc))
        return
    except Exception:
        pass

    def _payload(with_usage: bool, with_thinking: bool = True) -> str:
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": 0.6,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if with_usage:
            body["stream_options"] = {"include_usage": True}
        if with_thinking:
            # 设置里选了「关闭思考 / 强制思考」：按服务和模型挑参数（见 ai_thinking）
            try:
                from . import ai_thinking

                body.update(ai_thinking.extra_payload(store, model, cfg["base_url"]))
            except Exception:
                pass
        # 消息装饰和参数无关：接口不认参数时，`/no_think` 是最后一道保险
        try:
            from . import ai_thinking

            body["messages"] = ai_thinking.decorate_messages(
                messages, store, model, cfg["base_url"])
        except Exception:
            pass
        return json.dumps(body, ensure_ascii=False)

    def _post(with_usage: bool, with_thinking: bool = True):
        return requests.post(url, headers=headers,
                             data=_payload(with_usage, with_thinking).encode("utf-8"),
                             timeout=timeout, stream=True)

    thinking_on = True
    try:
        response = _post(True)
        if response.status_code in (400, 404, 422):
            # 有的服务不认 stream_options，退回去再来一次
            try:
                response.close()
            except Exception:
                pass
            response = _post(False)
        if response.status_code in (400, 404, 422) and thinking_on:
            # 也有可能是不认「关闭思考」那几个字段：去掉再试一次
            try:
                response.close()
            except Exception:
                pass
            thinking_on = False
            response = _post(False, with_thinking=False)
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
            hint = ""
            if not thinking_on:
                hint = ("（这个接口不接受「关闭思考」的参数，已经自动退回默认请求；"
                        "想真的不思考，请把模型换成非推理模型）")
            on_error(f"接口返回 {response.status_code}：{detail}{hint}")
        return

    finished = False
    tool_calls: Dict[int, Dict[str, Any]] = {}
    usage_last: Dict[str, Any] = {}
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
            usage = chunk.get("usage")
            if usage:
                usage_last = usage
                if on_usage is not None:
                    on_usage(usage)
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning and on_reasoning is not None:
                on_reasoning(str(reasoning))
            # 工具调用：参数是分片流式给过来的，按 index 拼回去
            for call in delta.get("tool_calls") or []:
                try:
                    index = int(call.get("index") or 0)
                except (TypeError, ValueError):
                    index = 0
                slot = tool_calls.setdefault(
                    index, {"id": "", "name": "", "arguments": ""})
                if call.get("id"):
                    slot["id"] = str(call["id"])
                function = call.get("function") or {}
                if function.get("name"):
                    slot["name"] = str(function["name"])
                if function.get("arguments"):
                    slot["arguments"] += str(function["arguments"])
            piece = delta.get("content")
            if piece:
                if first_token_at is None:
                    first_token_at = time.time()
                streamed_chars += len(str(piece))
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
    calls = [tool_calls[key] for key in sorted(tool_calls) if tool_calls[key].get("name")]
    if on_metrics is not None:
        try:
            end = time.time()
            ttft = (first_token_at - started) if first_token_at else None
            completion = int((usage_last or {}).get("completion_tokens") or 0)
            if not completion and streamed_chars:
                # 接口没给 usage：按「约 4 个字一个 token」粗估
                completion = max(1, int(round(streamed_chars / 4.0)))
            gen_seconds = (end - first_token_at) if first_token_at else (end - started)
            speed = tokens_per_second(completion, gen_seconds)
            on_metrics({
                "ttft": ttft,
                "elapsed": end - started,
                "completion_tokens": completion,
                "tokens_per_second": speed,
                "estimated_tokens": not bool((usage_last or {}).get("completion_tokens")),
                "model": model,
                "url": url,
            })
        except Exception:
            pass
    if calls and on_tool_calls is not None:
        on_tool_calls(calls)
    if on_done:
        on_done(finished and not calls)


class StreamWorker(QObject):
    """把 `stream_chat` 包成后台线程里的对象，界面用信号接收。"""

    delta = Signal(str)
    reasoning = Signal(str)
    usage = Signal(dict)
    metrics = Signal(dict)         # TTFT / tokens per second
    tool_calls = Signal(list)      # [{id, name, arguments(JSON 文本)}, ...]
    done = Signal(bool)
    failed = Signal(str)

    def __init__(self, store, messages: List[Dict[str, Any]],
                 tools: Optional[List[Dict[str, Any]]] = None):
        super().__init__()
        self.store = store
        self.messages = messages
        self.tools = tools
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        stream_chat(
            self.store,
            self.messages,
            on_delta=self.delta.emit,
            on_reasoning=self.reasoning.emit,
            on_usage=self.usage.emit,
            on_metrics=self.metrics.emit,
            on_tool_calls=self.tool_calls.emit,
            on_done=self.done.emit,
            on_error=self.failed.emit,
            should_stop=lambda: self._stop,
            tools=self.tools,
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
        self.setWindowTitle("DanLume · AI 对话")
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
