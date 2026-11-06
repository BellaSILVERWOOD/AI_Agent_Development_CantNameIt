"""本地记忆文件：用户说「记住这件事」时，把内容追加到 `<data>/memory.md`。

* 记忆文件是纯文本 Markdown，可以直接手改；
* AI 每次对话会把记忆作为系统提示词的一部分带进去（有长度上限）；
* 模型被要求「要记住的内容」单独用一行 `[[记住]] 内容` 输出，
  程序把这一行摘出来写进记忆文件，并从回答里删掉，不让它显示成正文。

对外接口：
    path()              -> Path
    load()              -> str
    entries()           -> List[str]
    append(text)        -> bool
    clear()             -> None
    remove_entry(text)  -> bool
    context_block(limit) -> str
    extract(回答文本)     -> (去掉标记的回答, [要记住的内容])
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from .config import DATA_DIR

FILE_NAME = "memory.md"
MAX_CHARS = 4000          # 带进提示词的上限
MARK = "[[记住]]"

_HEADER = """# DanLume 记忆

> 这里的内容会随每次 AI 对话带给模型（有长度上限）。
> 想让 AI 做什么/记住什么，直接跟它说「记住：……」即可；也可以手动编辑这个文件。
"""


def path() -> Path:
    return Path(DATA_DIR) / FILE_NAME


def _ensure() -> None:
    target = path()
    if target.exists():
        migrate_brand(target)
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_HEADER, encoding="utf-8")
    except Exception:
        pass


def migrate_brand(target=None) -> bool:
    """旧版本的记忆文件开头是「# Lumina 记忆」，改名后只把这一行换掉。"""
    target = Path(target) if target is not None else path()
    if not target.exists():
        return False
    try:
        text = target.read_text(encoding="utf-8")
    except Exception:
        return False
    if not text.startswith("# Lumina 记忆"):
        return False
    try:
        target.write_text(_HEADER + text[len("# Lumina 记忆\n"):].lstrip("\n"),
                          encoding="utf-8")
        return True
    except Exception:
        return False


def load() -> str:
    _ensure()
    try:
        return path().read_text(encoding="utf-8")
    except Exception:
        return ""


def entries() -> List[str]:
    """只取「- 」开头的记忆条目。"""
    items = []
    for line in load().splitlines():
        text = line.strip()
        if text.startswith(("- ", "* ")):
            items.append(text[2:].strip())
    return [item for item in items if item]


def append(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    _ensure()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        with path().open("a", encoding="utf-8") as handle:
            handle.write(f"- {text}（{stamp}）\n")
        return True
    except Exception:
        return False


def clear() -> None:
    try:
        path().write_text(_HEADER, encoding="utf-8")
    except Exception:
        pass


def remove_entry(text: str) -> bool:
    target = (text or "").strip()
    if not target:
        return False
    _ensure()
    try:
        lines = load().splitlines()
    except Exception:
        return False
    kept = [line for line in lines if not line.strip().lstrip("-* ").startswith(target)]
    if len(kept) == len(lines):
        return False
    try:
        path().write_text("\n".join(kept) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def context_block(limit: int = MAX_CHARS) -> str:
    """给系统提示词用的记忆片段（超长截断）。"""
    items = entries()
    if not items:
        return ""
    body = "\n".join(f"- {item}" for item in items)
    if len(body) > limit:
        body = body[:limit] + "\n…（记忆太长已截断）"
    return ("下面是我让你记住的长期记忆，回答时请参考：\n" + body +
            "\n\n如果我这次说「记住……」，请在回答最后单独一行输出：\n"
            f"{MARK} 要记住的内容\n（这一行只会被程序收进记忆，不会显示给我看。）")


def extract(answer: str) -> Tuple[str, List[str]]:
    """把回答里的 `[[记住]] …` 摘出来。"""
    remembered: List[str] = []
    kept_lines: List[str] = []
    pattern = re.compile(r"^\s*(?:[-*]\s*)?\[\[记住\]\]\s*(.+?)\s*$")
    for line in (answer or "").splitlines():
        match = pattern.match(line)
        if match:
            remembered.append(match.group(1).strip())
            continue
        kept_lines.append(line)
    cleaned = "\n".join(kept_lines).strip()
    return cleaned, remembered
