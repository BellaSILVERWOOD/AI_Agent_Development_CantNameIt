"""API 对话的历史会话：保存 / 重命名 / 删除 / 继续。

一个会话 = 一段 `_history`（用户、AI、工具调用卡片都在里面），整体存成 JSON。
这样面板那边不用改数据结构，工具卡片、思考过程也能原样恢复。

表：
* `chat_sessions(id, title, created_at, updated_at, model, scene, payload)`
  `payload` 是 JSON 列表，元素和面板里 `_history` 的一项一一对应。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

MAX_TITLE = 40
KEEP_SESSIONS = 200          # 只保留最近这么多段，避免库无限长


def _ensure(store) -> None:
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            scene TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL DEFAULT '[]'
        )"""
    )
    store.conn.commit()


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_title(items: List[Dict[str, Any]]) -> str:
    """用第一句用户的话当标题（太长就截断）。"""
    for item in items or []:
        if str(item.get("role")) == "user":
            text = " ".join(str(item.get("content") or "").split())
            if text:
                return text[:MAX_TITLE] + ("…" if len(text) > MAX_TITLE else "")
    return "新对话"


def create(store, items: Optional[List[Dict[str, Any]]] = None,
           title: str = "", model: str = "", scene: str = "") -> int:
    _ensure(store)
    items = items or []
    stamp = _stamp()
    cursor = store.conn.execute(
        "INSERT INTO chat_sessions(title, created_at, updated_at, model, scene,"
        " payload) VALUES (?,?,?,?,?,?)",
        (str(title or make_title(items))[:MAX_TITLE], stamp, stamp, str(model or ""),
         str(scene or ""), json.dumps(items, ensure_ascii=False)))
    store.conn.commit()
    session_id = int(cursor.lastrowid)
    _trim(store)
    return session_id


def _trim(store) -> None:
    try:
        rows = store.conn.execute(
            "SELECT id FROM chat_sessions ORDER BY updated_at DESC, id DESC"
            " LIMIT -1 OFFSET ?", (KEEP_SESSIONS,)).fetchall()
        for row in rows:
            store.conn.execute("DELETE FROM chat_sessions WHERE id = ?",
                               (int(row[0]),))
        if rows:
            store.conn.commit()
    except Exception:
        pass


def save(store, session_id: int, items: List[Dict[str, Any]],
         title: str = "", model: str = "", scene: str = "") -> int:
    """整段覆盖保存；会话不存在时新建一条。"""
    _ensure(store)
    if not session_id:
        return create(store, items, title, model, scene)
    row = store.conn.execute("SELECT id FROM chat_sessions WHERE id = ?",
                             (int(session_id),)).fetchone()
    if row is None:
        return create(store, items, title, model, scene)
    new_title = str(title or "").strip()[:MAX_TITLE] or make_title(items)
    store.conn.execute(
        "UPDATE chat_sessions SET payload = ?, updated_at = ?, model = ?, scene = ?,"
        " title = ? WHERE id = ?",
        (json.dumps(items, ensure_ascii=False), _stamp(), str(model or ""),
         str(scene or ""), new_title, int(session_id)))
    store.conn.commit()
    return int(session_id)


def load(store, session_id: int) -> Optional[Dict[str, Any]]:
    _ensure(store)
    row = store.conn.execute("SELECT * FROM chat_sessions WHERE id = ?",
                             (int(session_id),)).fetchone()
    if row is None:
        return None
    data = dict(row)
    try:
        data["items"] = json.loads(data.get("payload") or "[]")
    except Exception:
        data["items"] = []
    return data


def list_sessions(store, limit: int = 60) -> List[Dict[str, Any]]:
    _ensure(store)
    rows = store.conn.execute(
        "SELECT id, title, created_at, updated_at, model, scene, payload"
        " FROM chat_sessions ORDER BY updated_at DESC, id DESC LIMIT ?",
        (int(limit),)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            items = json.loads(item.get("payload") or "[]")
        except Exception:
            items = []
        item["count"] = sum(1 for one in items if one.get("role") == "user")
        item["preview"] = make_title(items)
        item.pop("payload", None)
        out.append(item)
    return out


def rename(store, session_id: int, title: str) -> bool:
    _ensure(store)
    text = str(title or "").strip()[:MAX_TITLE]
    if not text:
        return False
    cursor = store.conn.execute(
        "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
        (text, _stamp(), int(session_id)))
    store.conn.commit()
    return cursor.rowcount > 0


def delete(store, session_id: int) -> bool:
    _ensure(store)
    cursor = store.conn.execute("DELETE FROM chat_sessions WHERE id = ?",
                                (int(session_id),))
    store.conn.commit()
    return cursor.rowcount > 0


def delete_all(store) -> int:
    _ensure(store)
    cursor = store.conn.execute("DELETE FROM chat_sessions")
    store.conn.commit()
    return int(cursor.rowcount or 0)
