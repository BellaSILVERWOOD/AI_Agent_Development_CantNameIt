"""「思考」（reasoning）开关：真的让接口别思考，而不是把思考过程藏起来。

有些模型的思考过程可以关掉，各家写法不一样：

| 服务 / 模型 | 关掉思考的写法 |
| --- | --- |
| 通义千问 Qwen3（dashscope） | `"enable_thinking": false` |
| 自建 vLLM / SGLang 跑的 Qwen3 | `"chat_template_kwargs": {"enable_thinking": false}`（顶层 `enable_thinking` 会被忽略） |
| Qwen3 系（模板兜底） | 最后一条用户消息结尾加 `/no_think` |
| 智谱 GLM（bigmodel） | `"thinking": {"type": "disabled"}` |
| 豆包 / 火山方舟（volces、ark） | `"thinking": {"type": "disabled"}` |
| 硅基流动 / 其他 OpenAI 兼容网关 | `"thinking": {"type": "disabled"}` 或 `"enable_thinking": false` |
| OpenAI o 系 / gpt-5 | `"reasoning_effort": "minimal"` |
| DeepSeek deepseek-reasoner | **关不掉**（R1 天生会思考），想快请换 `deepseek-chat` |

设置项 `ai_thinking`：

* `"auto"`（默认）：什么都不传，让模型自己决定；
* `"off"`：按上面这张表挑参数传过去，接口不认就自动退回（见 `ai_chat` 的重试）；
* `"on"`：反过来强制打开（Qwen3 这类默认不思考的模型用得上）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

KEY = "ai_thinking"
MODES = ("auto", "off", "on")
MODE_LABELS = {"auto": "自动（由模型决定）", "off": "关闭思考", "on": "强制思考"}
REASONER_HINT = ("当前模型自己就会思考（比如 deepseek-reasoner / R1），接口关不掉；"
                 "想要「不思考」的快回答，请把模型名换成 deepseek-chat 这类非推理模型。")


def mode(store) -> str:
    """现在的思考模式。"""
    try:
        value = (store.get_setting(KEY, "auto") or "auto").strip().lower()
    except Exception:
        return "auto"
    return value if value in MODES else "auto"


def set_mode(store, value: str) -> str:
    value = str(value or "auto").strip().lower()
    if value not in MODES:
        value = "auto"
    try:
        store.set_setting(KEY, value)
    except Exception:
        pass
    return value


def label(store) -> str:
    return MODE_LABELS.get(mode(store), MODE_LABELS["auto"])


def is_reasoner_model(model: str) -> bool:
    text = (model or "").lower()
    return any(token in text for token in
               ("reasoner", "-r1", "r1-", "qwq", "o1", "o3", "o4"))


# 自己搭的接口（vLLM / SGLang / Ollama / LM Studio）关思考的写法和云服务不一样：
# 顶层 `enable_thinking` 会被直接忽略，要放进 `chat_template_kwargs`。
SELF_HOSTED_HINTS = ("vllm", "sglang", "ollama", "lmstudio", "lm-studio",
                     "text-generation-webui", "localhost", "127.0.0.1",
                     "0.0.0.0", "::1")


def looks_self_hosted(base_url: str = "", model: str = "") -> bool:
    """是不是自己搭的接口（内网 IP / 本机 / 这几个推理框架）。"""
    text = f"{base_url or ''} {model or ''}".lower()
    if any(token in text for token in SELF_HOSTED_HINTS):
        return True
    host = re.sub(r"^https?://", "", (base_url or "").lower()).split("/")[0]
    host = host.split(":")[0]
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host))


def matches_qwen(model: str = "", base_url: str = "") -> bool:
    text = f"{model or ''} {base_url or ''}".lower()
    return "qwen" in text or "qwq" in text


# Qwen3 的 chat template 认这个软开关：哪怕服务端忽略参数，加在消息里也能关掉思考
NO_THINK = "/no_think"


def extra_payload(store, model: str = "", base_url: str = "") -> Dict[str, Any]:
    """要附加到请求体里的字段（空字典 = 不加任何东西）。"""
    current = mode(store)
    if current == "auto":
        return {}
    text = f"{base_url or ''} {model or ''}".lower()
    local = looks_self_hosted(base_url, model)

    if current == "on":
        # 强制思考：Qwen3 这类默认不思考的要显式打开；
        # 其他服务大多默认就会思考，不用加（加了反而可能 400）
        if "qwen" in text or "qwq" in text:
            if local:
                return {"enable_thinking": True,
                        "chat_template_kwargs": {"enable_thinking": True}}
            return {"enable_thinking": True}
        if "glm" in text or "bigmodel" in text or "doubao" in text or "ark" in text \
                or "volces" in text:
            return {"thinking": {"type": "enabled"}}
        return {}

    # ---- 关闭思考 ----
    if is_reasoner_model(model) or "deepseek-reasoner" in text:
        # R1 类模型关不掉，别乱塞参数（塞了也无效，还可能被拒）
        return {}
    if matches_qwen(model, base_url) or "dashscope" in text or "aliyuncs" in text:
        payload: Dict[str, Any] = {"enable_thinking": False}
        if local:
            # vLLM / SGLang：顶层字段被忽略，必须走 chat_template_kwargs
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload
    if "gpt-5" in text or re.search(r"\bo[1-4](?:-|\b)", text):
        return {"reasoning_effort": "minimal"}
    if any(token in text for token in ("glm", "bigmodel", "doubao", "volces", "ark",
                                       "siliconflow", "moonshot", "kimi")):
        return {"thinking": {"type": "disabled"}}
    if local:
        # 自己搭的接口：多半是 vLLM / SGLang，按 chat template 参数关
        return {"thinking": {"type": "disabled"},
                "chat_template_kwargs": {"enable_thinking": False}}
    # 剩下的 OpenAI 兼容服务：先试最常见的写法，接口不认会自动退回去
    return {"thinking": {"type": "disabled"}}


def decorate_messages(messages, store, model: str = "", base_url: str = ""):
    """「关闭思考」时给 Qwen3 系的最后一条用户消息加 `/no_think`。

    有些自建服务既不认 `enable_thinking` 也不认 `chat_template_kwargs`（旧版
    vLLM、各种反代），但 Qwen3 的模板本身认 `/no_think` —— 加在消息里基本都能关掉。
    别的模型不加，免得污染提示词。
    """
    if mode(store) != "off" or not matches_qwen(model, base_url):
        return messages
    if is_reasoner_model(model):
        return messages
    out = [dict(item) for item in (messages or [])]
    for item in reversed(out):
        if str(item.get("role") or "") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and NO_THINK not in content:
            item["content"] = content.rstrip() + " " + NO_THINK
        break
    return out


def note(store, model: str = "", base_url: str = "") -> str:
    """给界面的一句说明（为什么关了还在思考 / 关不掉）。"""
    current = mode(store)
    if current == "auto":
        return ""
    if current == "on":
        return "已强制打开思考（部分模型默认就会思考）"
    if is_reasoner_model(model) or "deepseek-reasoner" in (base_url or "").lower():
        return REASONER_HINT
    extra = extra_payload(store, model, base_url)
    if not extra:
        return "这个接口不用额外参数，思考由模型自己决定"
    pairs = []
    for key, value in extra.items():
        if isinstance(value, dict):
            pairs.append(f"{key}={value.get('type') or value}")
        else:
            pairs.append(f"{key}={value}")
    text = "已关闭思考（请求里带 " + "、".join(pairs)
    if matches_qwen(model, base_url) and not is_reasoner_model(model):
        text += "，消息尾部加 " + NO_THINK
    return text + "）"


def describe(store, model: str = "", base_url: str = "") -> Dict[str, Any]:
    """给设置页 / 面板显示用的一份摘要。"""
    return {
        "mode": mode(store),
        "label": label(store),
        "extra": extra_payload(store, model, base_url),
        "note": note(store, model, base_url),
    }


def parse_extra(payload: str) -> Optional[Dict[str, Any]]:
    """调试用：把请求体 JSON 里的思考相关字段挑出来。"""
    import json

    try:
        body = json.loads(payload or "{}")
    except Exception:
        return None
    keys = ("thinking", "enable_thinking", "reasoning_effort",
            "chat_template_kwargs")
    return {key: body[key] for key in keys if key in body}
