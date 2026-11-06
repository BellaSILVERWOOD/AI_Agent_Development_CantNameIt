"""B6–B9：写应用内数据的工具（用户逐个批准）。

* `memo_append`       —— 往备忘里追加（没有同标题就新建）
* `countdown_add`     —— 新建重要日倒计时（复用 `important.add`）
* `flashcards_create` —— 复习卡片（间隔重复），存 `flashcards` 表
* `pomodoro_start`    —— 启动专注计时：**界面上没法从后台线程直接开**，
  所以这里给主窗口留一个「UI 钩子」，由主窗口注册一个真正的启动函数。

这些工具只改应用自己的数据（不碰文件、不联网），都能撤回或自己再改回来。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

#: 主窗口注册的 UI 钩子（pomodoro 需要真的动界面）
_POMODORO_HANDLER = None


def set_pomodoro_handler(handler) -> None:
    """主窗口调用：`handler(minutes) -> bool`。"""
    global _POMODORO_HANDLER
    _POMODORO_HANDLER = handler


# ------------------------------------------------------------------ B6 备忘
def memo_append(store, title: str = "", text: str = "") -> Dict[str, Any]:
    body = str(text or "").strip()
    if not body:
        return {"ok": False, "summary": "要写的内容是空的"}
    name = str(title or "").strip() or "AI 速记"
    escaped = (body.replace("&", "&amp;").replace("<", "&lt;")
               .replace(">", "&gt;").replace("\n", "</p><p>"))
    snippet = f"<p>{escaped}</p>"
    try:
        memos = store.list_memos()
    except Exception:
        memos = []
    target = None
    for item in memos:
        if str(item.get("title") or "").strip() == name:
            target = item
            break
    try:
        if target is None:
            memo_id = int(store.add_memo(title=name, html=snippet))
            return {"ok": True, "memo_id": memo_id, "created": True,
                    "summary": f"已在备忘里新建「{name}」（{len(body)} 字）"}
        old = str(target.get("html") or "")
        store.update_memo(int(target["id"]), html=old + snippet)
        return {"ok": True, "memo_id": int(target["id"]), "created": False,
                "summary": f"已追加到备忘「{name}」（+{len(body)} 字）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"写备忘失败：{exc}"}


# ------------------------------------------------------------------ B7 倒计时
def countdown_add(store, title: str = "", day: str = "", note: str = "") -> Dict[str, Any]:
    from . import important

    name = str(title or "").strip()
    if not name:
        return {"ok": False, "summary": "要告诉我是哪件事（标题）"}
    parsed = important.parse_day(str(day or "").strip()) if day else None
    if not parsed:
        raw = str(day or "").strip()
        if raw:
            return {"ok": False, "summary": f"看不懂日期「{raw}」，请用 YYYY-MM-DD"}
        return {"ok": False, "summary": "还要给个日期（YYYY-MM-DD）"}
    try:
        item_id = important.add(store, name, parsed, note=str(note or ""))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"建倒计时失败：{exc}"}
    left = important.days_between(parsed)
    friendly = important.label(left)
    return {"ok": True, "id": int(item_id), "date": parsed,
            "days_left": left,
            "summary": f"已记录「{name}」：{parsed}（{friendly}）"}


# ------------------------------------------------------------------ B8 复习卡片
def ensure_schema(store) -> None:
    try:
        store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS flashcards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                front TEXT NOT NULL,
                back TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                seen INTEGER NOT NULL DEFAULT 0,
                box INTEGER NOT NULL DEFAULT 1,
                next_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        store.conn.commit()
    except Exception:
        pass


def flashcards_create(store, cards: Optional[List[Dict[str, Any]]] = None,
                      source: str = "") -> Dict[str, Any]:
    ensure_schema(store)
    items = [item for item in (cards or []) if isinstance(item, dict)]
    made: List[int] = []
    for item in items[:20]:
        front = str(item.get("front") or item.get("question") or "").strip()
        back = str(item.get("back") or item.get("answer") or "").strip()
        if not front:
            continue
        try:
            store.conn.execute(
                "INSERT INTO flashcards(front, back, source, created_at, next_at)"
                " VALUES (?,?,?,?,?)",
                (front[:500], back[:2000], str(source or "")[:120],
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 date.today().isoformat()))
            made.append(int(store.conn.execute(
                "SELECT last_insert_rowid()").fetchone()[0]))
        except Exception:
            continue
    try:
        store.conn.commit()
    except Exception:
        pass
    if not made:
        return {"ok": False, "summary": "没有可用的卡片（每张至少要有正面问题）"}
    return {"ok": True, "ids": made, "count": len(made),
            "summary": f"已生成 {len(made)} 张复习卡片（可在「我的复习」里看）"}


def list_flashcards(store, limit: int = 100) -> List[Dict[str, Any]]:
    ensure_schema(store)
    try:
        rows = store.conn.execute(
            "SELECT * FROM flashcards ORDER BY id DESC LIMIT ?",
            (int(limit),)).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def review_flashcard(store, card_id: int, remembered: bool = True) -> bool:
    """复习一次：记得就进下一盒（间隔更长），忘了就回到第 1 盒。"""
    ensure_schema(store)
    row = None
    try:
        row = store.conn.execute("SELECT * FROM flashcards WHERE id=?",
                                 (int(card_id),)).fetchone()
    except Exception:
        return False
    if not row:
        return False
    box = int(row["box"] or 1)
    box = min(5, box + 1) if remembered else 1
    gaps = {1: 1, 2: 2, 3: 4, 4: 7, 5: 15}
    next_at = (date.today() + timedelta(days=gaps.get(box, 1))).isoformat()
    try:
        store.conn.execute(
            "UPDATE flashcards SET box=?, seen=seen+1, next_at=? WHERE id=?",
            (box, next_at, int(card_id)))
        store.conn.commit()
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ B9 专注计时
def pomodoro_start(minutes: int = 25, label: str = "") -> Dict[str, Any]:
    try:
        value = int(float(minutes or 25))
    except (TypeError, ValueError):
        value = 25
    value = max(1, min(180, value))
    handler = _POMODORO_HANDLER
    if handler is None:
        return {"ok": False,
                "summary": "现在没有可用的专注计时窗口（要在主界面里用）"}
    try:
        started = bool(handler(value))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"启动专注计时失败：{exc}"}
    if not started:
        return {"ok": False, "summary": "专注计时没启动成功"}
    text = f"开始 {value} 分钟专注" + (f"：{label}" if label else "")
    return {"ok": True, "minutes": value, "summary": text + "（点浮窗可以暂停）"}
