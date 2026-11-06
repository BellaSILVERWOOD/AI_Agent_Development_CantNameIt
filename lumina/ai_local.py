"""本地资源访问（沙盒）：让 AI 能读写**你允许的目录**里的文件。

三条硬规矩（都在这儿实现，工具层再挡一道）：

1. **只在白名单目录里**：默认只有应用自己的 `data/agent_workspace`；
   你可以在设置里把别的目录加进来（比如某个项目文件夹）。
   任何 `../..`、绝对路径跑到白名单外、指向白名单外的符号链接，一律拒绝。
2. **权限档位**：`off`（默认，完全不给）/ `read`（只能看）/ `write`（可以改）。
   写操作还要过 `write` 这一档。
3. **敏感文件不碰**：`.ssh`、`*.pem`、`*.key`、`.env`、`id_rsa`、`credentials*`、
   `node_modules`/`.git` 这类目录直接跳过（就算你把家目录加进白名单也一样）。

另外每次操作都写一行审计日志（`data/agent_fs.log`），出了问题能查是哪一次干的。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MODE_KEY = "local_access"            # off / read / write
ROOTS_KEY = "local_roots"            # 多行文本，一行一个目录（这些目录里的操作要确认）
DIRECT_KEY = "local_direct_roots"    # JSON 列表：本次工作「直接访问」的目录（不确认）
MAX_BYTES_KEY = "local_max_bytes"    # 单文件读取上限
DEFAULT_MODE = "off"
DEFAULT_MAX_BYTES = 256 * 1024
MAX_READ_BYTES = 2 * 1024 * 1024     # 就算设置写大了也不超过这个
WORKSPACE_NAME = "agent_workspace"
LOG_NAME = "agent_fs.log"
MAX_LIST = 400
MAX_SEARCH_FILES = 1200

MODES = ("off", "read", "write")
MODE_LABELS = {"off": "关闭（AI 不能碰本地文件）",
               "read": "只读（只能查看）",
               "write": "读写（可以新建/修改）"}
SKIP_DIRS = {".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
             ".idea", ".vscode", ".mypy_cache", ".pytest_cache", "site-packages"}
SENSITIVE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc",
                   ".env", "credentials", "credentials.json", ".git-credentials"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".kdbx"}
SENSITIVE_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".config"}


# ------------------------------------------------------------------ 设置
def mode(store) -> str:
    try:
        value = (store.get_setting(MODE_KEY, DEFAULT_MODE) or DEFAULT_MODE).strip().lower()
    except Exception:
        return DEFAULT_MODE
    return value if value in MODES else DEFAULT_MODE


def set_mode(store, value: str) -> str:
    value = str(value or DEFAULT_MODE).strip().lower()
    if value not in MODES:
        value = DEFAULT_MODE
    try:
        store.set_setting(MODE_KEY, value)
    except Exception:
        pass
    return value


def max_bytes(store) -> int:
    try:
        value = int(float(store.get_setting(MAX_BYTES_KEY, str(DEFAULT_MAX_BYTES))
                          or DEFAULT_MAX_BYTES))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_BYTES
    return max(4096, min(MAX_READ_BYTES, value))


def _data_dir() -> Path:
    from .config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def workspace(store=None, create: bool = True) -> Path:
    """默认工作区：`data/agent_workspace`。"""
    path = _data_dir() / WORKSPACE_NAME
    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return path


def raw_roots(store) -> List[str]:
    """设置里写的那几行（保持用户输入原文，便于界面回显）。"""
    try:
        text = store.get_setting(ROOTS_KEY, "") or ""
    except Exception:
        text = ""
    items: List[str] = []
    for line in str(text).replace(";", "\n").splitlines():
        line = line.strip()
        if line and line not in items:
            items.append(line)
    return items


def set_roots(store, roots: List[str]) -> List[str]:
    cleaned: List[str] = []
    for item in roots:
        text = str(item or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    try:
        store.set_setting(ROOTS_KEY, "\n".join(cleaned))
    except Exception:
        pass
    return cleaned


def add_root(store, path) -> List[str]:
    text = str(path or "").strip()
    roots = raw_roots(store)
    if text and text not in roots:
        roots.append(text)
    return set_roots(store, roots)


def remove_root(store, path) -> List[str]:
    text = str(path or "").strip()
    roots = [item for item in raw_roots(store) if item != text]
    return set_roots(store, roots)


def roots(store, create: bool = True) -> List[Path]:
    """实际生效的目录（白名单）。默认一定含内置工作区。"""
    found: List[Path] = [workspace(store, create=create)]
    for item in raw_roots(store):
        try:
            path = Path(item).expanduser()
            if path.exists() and path.is_dir():
                found.append(path)
        except Exception:
            continue
    unique: List[Path] = []
    for path in found:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved not in unique:
            unique.append(resolved)
    return unique


# ------------------------------------------------------------------ 直接访问 vs 需要确认
def raw_direct_roots(store) -> List[str]:
    """标记为「本次工作直接访问」的目录（不用每次确认）。"""
    try:
        data = json.loads(store.get_setting(DIRECT_KEY, "[]") or "[]")
    except Exception:
        data = []
    if not isinstance(data, list):
        data = []
    return [str(item) for item in data if str(item or "").strip()]


def set_direct_roots(store, paths: List[str]) -> List[str]:
    cleaned: List[str] = []
    for item in paths:
        text = str(item or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    try:
        store.set_setting(DIRECT_KEY, json.dumps(cleaned, ensure_ascii=False))
    except Exception:
        pass
    return cleaned


def direct_roots(store) -> List[Path]:
    """可以直接访问（不用每次确认）的目录：内置工作区 + 用户标记过的。"""
    found: List[Path] = [workspace(store)]
    for item in raw_direct_roots(store):
        try:
            path = Path(item).expanduser()
            if path.exists() and path.is_dir():
                found.append(path)
        except Exception:
            continue
    unique: List[Path] = []
    for path in found:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved not in unique:
            unique.append(resolved)
    return unique


def is_direct(store, path: Path) -> bool:
    """这个路径是不是在「直接访问」的目录里（不在就要问用户）。"""
    try:
        resolved = Path(path).resolve()
    except Exception:
        return False
    return any(_inside(resolved, root) for root in direct_roots(store))


def set_direct(store, path, direct: bool = True) -> List[str]:
    """把某个目录标成「直接访问」或改回「每次确认」。"""
    try:
        resolved = str(Path(str(path)).expanduser().resolve())
    except Exception:
        resolved = str(path)
    items = raw_direct_roots(store)
    if direct:
        if resolved not in items:
            items.append(resolved)
    else:
        items = [item for item in items if item != resolved]
    return set_direct_roots(store, items)


# ------------------------------------------------------------------ 判定
def is_sensitive(path: Path) -> bool:
    """敏感文件 / 目录：就算在白名单里也不碰。"""
    name = path.name.lower()
    if name in SENSITIVE_NAMES or name.startswith("credentials"):
        return True
    if path.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    parts = {part.lower() for part in path.parts}
    return bool(parts & SENSITIVE_DIRS)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve(store, raw: str = "", need_write: bool = False) -> Tuple[Optional[Path], str]:
    """把用户/模型给的路子解成白名单里的真实路径。返回 (路径, 错误)。"""
    current = mode(store)
    if current == "off":
        return None, "本地资源访问是关闭的（设置 → AI 接口 → 本地资源）"
    if need_write and current != "write":
        return None, ("本地资源现在是「只读」档，不能写文件"
                      "（设置 → AI 接口 → 本地资源 改成「读写」）")
    text = str(raw or "").strip().strip('"')
    base = workspace(store)
    if not text:
        candidate = base
    else:
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
    try:
        resolved = candidate.resolve()
    except Exception as exc:  # noqa: BLE001
        return None, f"路径看不懂：{exc}"
    allowed = roots(store)
    if not any(_inside(resolved, root) for root in allowed):
        return None, ("这个路径不在允许的目录里。允许的是："
                      + "、".join(str(root) for root in allowed)
                      + "（可以在设置里添加目录）")
    if is_sensitive(resolved):
        return None, "这个文件/目录属于敏感内容，不允许访问"
    parts = {part.lower() for part in resolved.parts}
    if parts & SENSITIVE_DIRS:
        return None, "这个目录属于敏感内容，不允许访问"
    return resolved, ""


# ------------------------------------------------------------------ 操作
def _audit(store, action: str, path, ok: bool, note: str = "") -> None:
    line = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "path": str(path),
        "ok": bool(ok),
        "note": str(note or "")[:200],
    }
    try:
        with open(_data_dir() / LOG_NAME, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


def audit_lines(store, limit: int = 80) -> List[Dict[str, Any]]:
    """读回最近的操作日志（设置页「查看操作记录」用）。"""
    path = _data_dir() / LOG_NAME
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines[-int(limit):]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _deny(store, action: str, raw, reason: str) -> Dict[str, Any]:
    """被拒绝的访问也要留痕（安全审计要能看出「有人试图越界」）。"""
    _audit(store, action, raw, False, reason)
    return {"ok": False, "summary": reason}


def list_dir(store, path: str = "", limit: int = MAX_LIST) -> Dict[str, Any]:
    target, error = resolve(store, path)
    if error:
        return _deny(store, "list", path, error)
    if not target.exists():
        return {"ok": False, "summary": f"没有这个目录：{target}"}
    if not target.is_dir():
        return {"ok": False, "summary": f"不是目录：{target.name}"}
    items: List[Dict[str, Any]] = []
    try:
        children = sorted(target.iterdir(), key=lambda item: (not item.is_dir(),
                                                             item.name.lower()))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"列不出来：{exc}"}
    for child in children:
        if len(items) >= int(limit):
            break
        if child.is_dir() and child.name.lower() in SKIP_DIRS:
            continue
        if is_sensitive(child):
            continue
        try:
            stat = child.stat()
        except Exception:
            continue
        items.append({
            "name": child.name,
            "is_dir": child.is_dir(),
            "size": 0 if child.is_dir() else int(stat.st_size),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    _audit(store, "list", target, True, f"{len(items)} 项")
    dirs = sum(1 for item in items if item["is_dir"])
    return {
        "ok": True,
        "path": str(target),
        "items": items,
        "count": len(items),
        "summary": f"{target}：{dirs} 个文件夹 / {len(items) - dirs} 个文件",
    }


def read_file(store, path: str = "", limit: Optional[int] = None) -> Dict[str, Any]:
    target, error = resolve(store, path)
    if error:
        return _deny(store, "read", path, error)
    if not target.exists():
        return {"ok": False, "summary": f"没有这个文件：{target}"}
    if target.is_dir():
        return {"ok": False, "summary": "这是个目录，用 fs_list 看里面有什么"}
    cap = int(limit or max_bytes(store))
    cap = max(1024, min(MAX_READ_BYTES, cap))
    try:
        raw = target.read_bytes()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"读不出来：{exc}"}
    truncated = len(raw) > cap
    raw = raw[:cap]
    text = ""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        text = raw.decode("utf-8", "replace")
    if "\x00" in text[:2000]:
        _audit(store, "read", target, False, "二进制文件")
        return {"ok": False, "summary": "这看起来是二进制文件（不是文本），读不了"}
    _audit(store, "read", target, True, f"{len(text)} 字")
    summary = f"读了 {target.name}（{len(text)} 字"
    summary += "，已截断）" if truncated else "）"
    return {"ok": True, "path": str(target), "text": text,
            "truncated": truncated, "summary": summary}


def write_file(store, path: str = "", content: str = "",
               append: bool = False) -> Dict[str, Any]:
    target, error = resolve(store, path, need_write=True)
    if error:
        return _deny(store, "write", path, error)
    if not target.name:
        return {"ok": False, "summary": "要写文件得给个文件名"}
    if target.is_dir():
        return {"ok": False, "summary": "这个路径是个目录"}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"建不了上级目录：{exc}"}
    existed = target.exists()
    try:
        with open(target, "a" if append else "w", encoding="utf-8", newline="\n") as handle:
            handle.write(str(content or ""))
    except Exception as exc:  # noqa: BLE001
        _audit(store, "write", target, False, str(exc))
        return {"ok": False, "summary": f"写不进去：{exc}"}
    action = "追加" if append else ("覆盖" if existed else "新建")
    _audit(store, "write", target, True, f"{action} {len(str(content or ''))} 字")
    return {"ok": True, "path": str(target), "created": not existed,
            "summary": f"已{action} {target.name}（{len(str(content or ''))} 字）"}


DISCARD_PREFIX = "[discarded] "


def discard(store, path: str = "") -> Dict[str, Any]:
    """「删除」文件的方式：**改名**成 `[discarded] 原名`，不真的删掉。

    用户的要求：删除文件时把文件名从 `name.ext` 改成 `[discarded] name.ext`。
    这样还能从文件管理器里找回来，也不怕 AI 误删。重名就加 (2)、(3)。
    """
    target, error = resolve(store, path, need_write=True)
    if error:
        return _deny(store, "discard", path, error)
    if not target.exists():
        return {"ok": False, "summary": f"没有这个文件：{target}"}
    if target.name.startswith(DISCARD_PREFIX):
        return {"ok": True, "path": str(target),
                "summary": f"{target.name} 已经是「已丢弃」状态，不用再改"}
    if target.is_dir():
        new_name = DISCARD_PREFIX + target.name
    else:
        new_name = DISCARD_PREFIX + target.name
    candidate = target.with_name(new_name)
    index = 2
    while candidate.exists():
        if target.is_dir():
            candidate = target.with_name(f"{DISCARD_PREFIX}{target.name} ({index})")
        else:
            candidate = target.with_name(
                f"{DISCARD_PREFIX}{target.stem} ({index}){target.suffix}")
        index += 1
        if index > 200:
            break
    try:
        target.rename(candidate)
    except Exception as exc:  # noqa: BLE001
        _audit(store, "discard", target, False, str(exc))
        return {"ok": False, "summary": f"改不了名（可能正被别的程序占用）：{exc}"}
    _audit(store, "discard", target, True, f"→ {candidate.name}")
    folder = str(candidate.parent)
    hint = (f"没有真删：已改名为「{candidate.name}」。"
            f"要彻底删除的话，去 {folder} 里把它删掉（文件管理器里右键删除即可）。")
    return {"ok": True, "from": str(target), "path": str(candidate),
            "discarded_path": str(candidate), "folder": folder,
            "delete_hint": f"彻底删除请到：{folder}",
            "summary": hint}


def search(store, keyword: str = "", path: str = "",
           limit: int = 50) -> Dict[str, Any]:
    """在白名单目录里找「文件内容包含关键词」的文件。"""
    needle = str(keyword or "").strip()
    if not needle:
        return {"ok": False, "summary": "要给个关键词"}
    start, error = resolve(store, path)
    if error:
        return _deny(store, "search", path, error)
    bases = [start] if start.is_file() else [start]
    hits: List[Dict[str, Any]] = []
    scanned = 0
    cap = int(limit or 50)
    for base in bases:
        for file in _walk_files(base, MAX_SEARCH_FILES):
            if len(hits) >= cap:
                break
            scanned += 1
            try:
                if file.stat().st_size > max_bytes(store):
                    continue
                raw = file.read_bytes()
            except Exception:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = raw.decode("gb18030")
                except UnicodeDecodeError:
                    continue
            if "\x00" in text[:2000]:
                continue
            index = text.lower().find(needle.lower())
            if index < 0:
                continue
            start_at = max(0, index - 40)
            snippet = text[start_at:index + len(needle) + 60].replace("\n", " ")
            hits.append({"path": str(file), "snippet": snippet})
    _audit(store, "search", start, True, f"{needle} → {len(hits)} 个")
    return {"ok": True, "keyword": needle, "matches": hits, "count": len(hits),
            "scanned": scanned,
            "summary": (f"在 {start} 里找到 {len(hits)} 个包含「{needle}」的文件"
                        f"（扫了 {scanned} 个）")}


def _walk_files(base: Path, max_files: int):
    count = 0
    try:
        if base.is_file():
            yield base
            return
        for root, dirs, files in os.walk(base):
            dirs[:] = [name for name in dirs if name.lower() not in SKIP_DIRS]
            for name in files:
                if count >= max_files:
                    return
                path = Path(root) / name
                if is_sensitive(path):
                    continue
                count += 1
                yield path
    except Exception:
        return


def summary(store) -> Dict[str, Any]:
    """给界面/系统提示词用的摘要。"""
    current = mode(store)
    allowed = roots(store)
    direct = direct_roots(store)
    return {
        "mode": current,
        "label": MODE_LABELS.get(current, MODE_LABELS["off"]),
        "roots": [str(path) for path in allowed],
        "direct_roots": [str(path) for path in direct],
        "max_bytes": max_bytes(store),
        "enabled": current != "off",
        "can_write": current == "write",
    }


def system_block(store) -> str:
    """拼进系统提示词：告诉模型本地文件能力与边界。"""
    info = summary(store)
    if not info["enabled"]:
        return ""
    lines = [
        "你还可以直接读写这台机器上的文件（沙盒限制，越界会被拒绝）：",
        "- fs_list(path=\"\")：列目录；path 相对路径按工作区解析。",
        "- fs_read(path=\"…\")：读文本文件（自动截断）。",
        "- fs_search(keyword=\"…\", path=\"\")：按内容搜文件。",
    ]
    if info["can_write"]:
        lines.append("- fs_write(path=\"…\", content=\"…\", append=false)：写文本文件。")
    else:
        lines.append("- 现在是「只读」档，只能看不能改；用户要写文件时请让他去设置里改成「读写」。")
    lines.append("允许的目录：" + "；".join(info["roots"]))
    direct = info.get("direct_roots") or []
    if direct:
        lines.append("其中「直接访问」（不用每次问用户）：" + "；".join(direct))
    lines.append("规则：只碰这些目录；**不在「直接访问」列表里的目录，每次操作前都会先问用户**，"
                 "被拒绝就停下并说明；用户没要求时不要改文件；写之前先说清楚要写哪个文件。")
    return "\n".join(lines)
