"""重要日（倒计时）：期中考试、答辩、截止日这类「还差几天」的日子。

和日程分开存（表 `important_dates`）：日程是「那天几点做什么」，重要日是「离那天还有几天」，
所以它可以不挂在某个具体时段上，只在日历页顶部显示一条倒计时。

对外接口：
    add(store, title, date, note="", color="rose", kind="custom", event_id=None) -> int
    delete(store, item_id)
    update(store, item_id, **fields)
    list_all(store)                     -> [{id,title,date,note,color,kind,event_id,days,left}]
    upcoming(store, within_days=None, limit=None) -> 同上（只留今天及以后）
    days_between(target, today=None)    -> int
    label(days)                         -> "今天" / "明天" / "12 天后" / "已过 3 天"
    parse_day(text)                     -> "YYYY-MM-DD" 或 None（支持 今天/明天/3/5/11月5日）
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

COLOR_CHOICES = ("rose", "violet", "sky", "teal", "amber")


# ------------------------------------------------------------------ 工具
def _today(today: Optional[date] = None) -> date:
    return today or date.today()


def _parse(value: str) -> Optional[date]:
    text = (value or "").strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%m-%d", "%m/%d"):
        try:
            if pattern in ("%m-%d", "%m/%d"):
                moment = datetime.strptime(text, pattern).date()
                return moment.replace(year=_today().year)
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def parse_day(text: str, today: Optional[date] = None) -> Optional[str]:
    """把用户输入的日子解析成 YYYY-MM-DD；认不出来返回 None。"""
    base = _today(today)
    raw = (text or "").strip()
    if not raw:
        return None
    words = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3}
    if raw in words:
        return (base + timedelta(days=words[raw])).isoformat()
    match = re.fullmatch(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", raw)
    if match:
        try:
            return date(base.year, int(match.group(1)), int(match.group(2))).isoformat()
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", raw)
    if match:
        try:
            return date(base.year, int(match.group(1)), int(match.group(2))).isoformat()
        except ValueError:
            return None
    match = re.fullmatch(r"\+?(\d{1,3})\s*(?:天|days?)?", raw)
    if match and raw not in ("",):
        return (base + timedelta(days=int(match.group(1)))).isoformat()
    parsed = _parse(raw)
    return parsed.isoformat() if parsed else None


def days_between(target: str, today: Optional[date] = None) -> int:
    parsed = _parse(target)
    if parsed is None:
        return 0
    return (parsed - _today(today)).days


def label(days: int) -> str:
    if days == 0:
        return "今天"
    if days == 1:
        return "明天"
    if days == 2:
        return "后天"
    if days > 0:
        return f"{days} 天后"
    return f"已过 {abs(days)} 天"


def friendly_day(value: str) -> str:
    parsed = _parse(value)
    return parsed.strftime("%m-%d") if parsed else str(value)


# ------------------------------------------------------------------ 数据
def _row_to_item(row: Any, today: Optional[date] = None) -> Dict[str, Any]:
    item = dict(row)
    left = days_between(str(item.get("date") or ""), today)
    item["days"] = left
    item["left"] = label(left)
    return item


def add(store, title: str, day: str, note: str = "", color: str = "rose",
        kind: str = "custom", event_id: Optional[int] = None) -> int:
    title = (title or "").strip() or "重要日"
    parsed = parse_day(day) or (day or "").strip()
    cursor = store.conn.execute(
        "INSERT INTO important_dates(title, date, note, color, kind, event_id,"
        " created_at) VALUES (?,?,?,?,?,?,?)",
        (title, parsed, note or "", color if color in COLOR_CHOICES else "rose",
         kind, event_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    store.conn.commit()
    return int(cursor.lastrowid)


def update(store, item_id: int, **fields: Any) -> None:
    allowed = {"title", "date", "note", "color", "kind", "event_id"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if "date" in values:
        values["date"] = parse_day(str(values["date"])) or str(values["date"])
    if not values:
        return
    assignments = ", ".join(f"{key}=?" for key in values)
    store.conn.execute(
        f"UPDATE important_dates SET {assignments} WHERE id=?",
        (*values.values(), int(item_id)),
    )
    store.conn.commit()


def delete(store, item_id: int) -> None:
    store.conn.execute("DELETE FROM important_dates WHERE id=?", (int(item_id),))
    store.conn.commit()


def list_all(store, today: Optional[date] = None) -> List[Dict[str, Any]]:
    rows = store.conn.execute(
        "SELECT * FROM important_dates ORDER BY date ASC, id ASC").fetchall()
    return [_row_to_item(row, today) for row in rows]


def upcoming(store, within_days: Optional[int] = 90,
             limit: Optional[int] = None,
             today: Optional[date] = None) -> List[Dict[str, Any]]:
    base = _today(today)
    items = [item for item in list_all(store, base) if item["days"] >= 0]
    if within_days is not None:
        items = [item for item in items if item["days"] <= int(within_days)]
    return items[:limit] if limit else items


def next_item(store, today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    items = upcoming(store, within_days=None, limit=1, today=today)
    return items[0] if items else None


def add_from_event(store, event: Dict[str, Any], note: str = "") -> int:
    """把一条日程（比如考试）加成重要日，带上颜色。"""
    return add(store, str(event.get("title") or "重要日"),
               str(event.get("date") or ""), note=note,
               color=str(event.get("color") or "rose"), kind="exam",
               event_id=int(event["id"]) if event.get("id") else None)


def ensure_exam_group(store) -> int:
    """「考试」分组：导入考试时用，和课程表分组一样按名字复用。"""
    rows = store.conn.execute(
        "SELECT id, name FROM event_groups ORDER BY id").fetchall()
    for row in rows:
        if str(row["name"]).strip() == "考试":
            return int(row["id"])
    cursor = store.conn.execute(
        "INSERT INTO event_groups(name, color, created_at) VALUES (?,?,?)",
        ("考试", "rose", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    store.conn.commit()
    return int(cursor.lastrowid)
