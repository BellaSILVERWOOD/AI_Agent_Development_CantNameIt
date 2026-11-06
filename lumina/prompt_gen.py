"""高级提示词：用外部程序（Python / C / C++ / 任意 exe）每次现场生成系统提示词。

约定很简单：

* 设置 `prompt_gen_command` 指向一个程序：
  - `.py` → 用当前 Python 解释器跑（`python -X utf8 脚本`）；
  - 其它（`.exe` / `.bat` / `.cmd`）→ 直接执行；
* 程序从 **stdin** 收到一行 JSON 上下文（时间、IP、主机名、模型、场景……），
  把要追加的提示词写到 **stdout**（纯文本，UTF-8）；
* 超时（默认 3 秒）或报错时不影响聊天：把原因写进状态栏，正文照常用基础提示词。

这样 C/C++ 也能写：编成一个 exe，读 stdin 的 JSON、往 stdout 打文本即可。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import context_info

ENABLED_KEY = "prompt_gen_enabled"
COMMAND_KEY = "prompt_gen_command"
TIMEOUT_KEY = "prompt_gen_timeout"
DEFAULT_TIMEOUT = 3.0
MAX_CHARS = 8000


class PromptGenError(Exception):
    pass


def enabled(store) -> bool:
    try:
        return (store.get_setting(ENABLED_KEY, "0") or "0") == "1"
    except Exception:
        return False


def set_enabled(store, value: bool) -> None:
    store.set_setting(ENABLED_KEY, "1" if value else "0")


def command(store) -> str:
    return (store.get_setting(COMMAND_KEY, "") or "").strip()


def set_command(store, value: str) -> None:
    store.set_setting(COMMAND_KEY, str(value or "").strip())


def timeout(store) -> float:
    try:
        return max(0.5, min(30.0, float(store.get_setting(
            TIMEOUT_KEY, str(DEFAULT_TIMEOUT)) or DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def set_timeout(store, value: float) -> None:
    store.set_setting(TIMEOUT_KEY, str(max(0.5, min(30.0, float(value)))))


def _argv(script: str) -> list:
    path = Path(script)
    suffix = path.suffix.lower()
    if suffix == ".py":
        return [sys.executable, "-X", "utf8", str(path)]
    if suffix in (".bat", ".cmd"):
        return ["cmd", "/c", str(path)]
    return [str(path)]


def build_context(store, extra: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    info = context_info.context_dict(store)
    if store is not None:
        try:
            info["model"] = str(store.get_setting("llm_model_name", "") or "")
            info["base_url"] = str(store.get_setting("llm_base_url", "") or "")
        except Exception:
            pass
    if extra:
        info.update({str(k): str(v) for k, v in extra.items()})
    return info


def generate(store, extra: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """跑一次生成程序，返回 (文本, 错误信息)。错误不影响聊天。"""
    if not enabled(store):
        return "", ""
    script = command(store)
    if not script:
        return "", "还没有指定提示词生成程序（设置 → 提示词）"
    path = Path(script)
    if not path.exists():
        return "", f"提示词生成程序不存在：{path}"
    payload = json.dumps(build_context(store, extra), ensure_ascii=False)
    try:
        completed = subprocess.run(
            _argv(str(path)),
            input=payload.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout(store),
            cwd=str(path.parent),
        )
    except subprocess.TimeoutExpired:
        return "", f"提示词生成程序超过 {timeout(store):.0f} 秒没返回，已跳过"
    except Exception as exc:  # noqa: BLE001
        return "", f"提示词生成程序跑不起来：{exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        return "", (f"提示词生成程序退出码 {completed.returncode}"
                    + (f"：{detail[:160]}" if detail else ""))
    text = (completed.stdout or b"").decode("utf-8", "replace").strip()
    if not text:
        return "", "提示词生成程序没有输出（stdout 是空的）"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n…（生成内容过长，已截断）"
    return text, ""


SAMPLE_SCRIPT = '''# -*- coding: utf-8 -*-
"""DanLume 提示词生成程序（示例）。

约定：
  从 stdin 读一行 JSON 上下文（now / date / weekday / local_ip / hostname /
  model / base_url ...），把要追加到系统提示词里的文字写到 stdout。
换成本机时间做「人设」、加位置、加课程安排都随你。

C / C++ 也能这么写：读 stdin 的 JSON，printf 出文本即可，编成 exe 后
把设置里的「命令」指向那个 exe。
"""
import json
import sys

data = json.loads(sys.stdin.read() or "{}")
lines = [
    "现在是 %s（%s）。" % (data.get("now", ""), data.get("weekday", "")),
    "我在 %s（本机 IP %s）。" % (data.get("hostname", "这台电脑"),
                              data.get("local_ip", "未知")),
    "回答时请把日期算准；涉及「今天/明天」就按上面的日期来。",
]
if data.get("model"):
    lines.append("当前模型：%s。" % data["model"])
print("\\n".join(lines))
'''


def write_sample(store) -> Path:
    """在 data 目录放一个示例脚本，方便用户照着改。"""
    from .config import DATA_DIR

    target = DATA_DIR / "prompt_gen_example.py"
    try:
        target.write_text(SAMPLE_SCRIPT, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        raise PromptGenError(f"写示例脚本失败：{exc}") from exc
    return target
