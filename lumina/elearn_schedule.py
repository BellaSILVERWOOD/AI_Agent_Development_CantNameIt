# -*- coding: utf-8 -*-
"""每天把 eLearning 的作业截止时间写进日程（已有的不重复加）。

用户：「https://elearning.fudan.edu.cn/dash 里面有各项作业的截止时间。
请每天自动抓取网页内容并添加日程（已有的日程无需重复添加）」

做法：
* 数据优先走**联网**的 `elearning.todo_items()`（就是 dash 右侧 To Do 那一份）；
  没登录 / 没网时退回仪表板已经缓存下来的 `dashboard.assignments()`；
* 每条作业 → 一条日程：标题「作业截止：课程 · 作业名」，时间范围是
  **截止前 30 分钟 → 截止时间**（用户要求），提醒就落在块的开始（= 截止前 30 分钟），
  颜色 rose；
* 去重靠 `elearning_event_map`（去重键 → 日程 id）+ 再按「标题 + 日期 + 开始时间」
  查一遍现有日程，所以删了日程 / 丢了映射也不会重复加；
* 旧版本写进去的块（「截止 → 截止 +30 分钟」）会被**改成新的时间范围**，不会变成两条；
* 只在「开启」且距上次同步超过 `INTERVAL` 时才跑，一天最多一次（应用开着就行）。
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

KEY_ON = "elearning_to_calendar"          # "1" = 每天自动写入（默认开）
KEY_MAP = "elearning_event_map"           # {去重键: 日程 id}
KEY_TS = "elearning_event_sync_at"        # 上次同步时间（unix 秒）
KEY_VERSION = "elearning_blocks_version"  # 日程块的格式版本（换了时间范围就升级）
BLOCKS_VERSION = "2"                      # 2 = 块是「截止前 30 分钟 → 截止时间」
INTERVAL = 20 * 3600                      # 20 小时，一天最多跑一次
TITLE_PREFIX = "作业截止："
#: 日程块是「截止前 30 分钟 → 截止时间」（用户要求：这段时间才是要紧的时候）
LEAD_MINUTES = 30
#: 提醒就落在块的开始，也就是**截止前 30 分钟**
REMIND_MINUTES = 0
DEFAULT_MINUTES = LEAD_MINUTES


def enabled(store) -> bool:
    try:
        return (str(store.get_setting(KEY_ON, "1")) != "0")
    except Exception:
        return True


def set_enabled(store, value: bool) -> None:
    try:
        store.set_setting(KEY_ON, "1" if value else "0")
    except Exception:
        pass


def last_sync(store) -> float:
    try:
        return float(store.get_setting(KEY_TS, "0") or 0)
    except Exception:
        return 0.0


def should_sync(store, force: bool = False) -> bool:
    if not enabled(store):
        return False
    if force:
        return True
    if needs_upgrade(store):
        # 块的时间范围换过（老版本写的是「截止 → +30 分钟」）：立刻改一次，
        # 不用等 20 小时的闸门
        return True
    return (time.time() - last_sync(store)) >= INTERVAL


def needs_upgrade(store) -> bool:
    """日程块还是老格式吗（升级上来之后要跑一次把时间改过来）。"""
    try:
        return str(store.get_setting(KEY_VERSION, "") or "") != BLOCKS_VERSION
    except Exception:
        return False


def load_map(store) -> Dict[str, int]:
    try:
        raw = str(store.get_setting(KEY_MAP, "") or "")
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    out: Dict[str, int] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            try:
                out[str(key)] = int(value)
            except Exception:
                continue
    return out


def save_map(store, mapping: Dict[str, int]) -> None:
    try:
        store.set_setting(KEY_MAP, json.dumps(mapping, ensure_ascii=False))
    except Exception:
        pass


def dedupe_key(item: Dict[str, Any]) -> str:
    """同一条作业的稳定身份：优先 html_url，其次「课程 + 标题 + 截止」。"""
    url = str((item or {}).get("url") or "").strip()
    if url:
        return url
    return "|".join(str((item or {}).get(part) or "").strip()
                    for part in ("course", "title", "due"))


def due_clock(item: Dict[str, Any]) -> str:
    """这条作业的截止时刻 `HH:MM`（没有就是空串）。"""
    due = str((item or {}).get("due") or "").strip()
    when = due.replace("T", " ")
    if not when:
        return ""
    clock = when[11:16] if len(when) >= 16 else "23:59"
    try:
        datetime.strptime(clock, "%H:%M")
    except ValueError:
        return ""
    return clock


def event_fields(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """一条作业 → 日程字段；没有截止时间的返回 None（不写日程）。

    **时间范围是「截止前 30 分钟 → 截止时间」**（用户要求）：这个块落在日历上，
    一眼就能看出「再过半小时要交」。截止在 00:10 这种跨夜的，块从 23:40 开始，
    结束时间比开始时间早（日历按隔夜处理）。
    """
    due = str((item or {}).get("due") or "").strip()
    if not due:
        return None
    when = due.replace("T", " ")
    day = when[:10]
    clock = when[11:16] if len(when) >= 16 else "23:59"
    try:
        datetime.strptime(day, "%Y-%m-%d")
        due_at = datetime.strptime("%s %s" % (day, clock), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    start = due_at - timedelta(minutes=LEAD_MINUTES)
    course = str((item or {}).get("course") or "").strip()
    name = str((item or {}).get("title") or (item or {}).get("name") or "作业").strip()
    title = TITLE_PREFIX + (("%s · %s" % (course, name)) if course else name)
    description = "\n".join(bit for bit in (
        "课程：%s" % course if course else "",
        "截止：%s" % when,
        "日程块：截止前 %d 分钟（%s–%s）"
        % (LEAD_MINUTES, start.strftime("%H:%M"), due_at.strftime("%H:%M")),
        "类型：%s" % str((item or {}).get("kind") or "作业"),
        "链接：%s" % str((item or {}).get("url") or "") if (item or {}).get("url") else "",
        "（由 eLearning 的 dash 自动写入）",
    ) if bit)
    return {
        "title": title[:120],
        "date": start.strftime("%Y-%m-%d"),
        "start_time": start.strftime("%H:%M"),
        "end_time": due_at.strftime("%H:%M"),
        "description": description,
        "remind_minutes": REMIND_MINUTES,
        "color": "rose",
    }


def existing(store, fields: Dict[str, Any],
             legacy_start: str = "") -> Optional[int]:
    """日程里是不是已经有这条了（标题 + 日期 + 开始时间）。

    `legacy_start` 是旧版本写进去的开始时间（以前块是「截止 → 截止 +30 分钟」），
    认出来之后会被改成新的时间范围，不会多出一条重复的。
    """
    try:
        rows = store.events_on(fields["date"])
    except Exception:
        rows = []
    for row in rows:
        if str(row.get("title") or "") != fields["title"]:
            continue
        start = str(row.get("start_time") or "")[:5]
        if start == fields["start_time"] or (legacy_start and start == legacy_start):
            return int(row.get("id") or 0) or None
    return None


def fetch_items(store) -> List[Dict[str, Any]]:
    """拿作业清单：先联网读 dash，失败就用仪表板的缓存。"""
    try:
        from . import elearning

        client = elearning.open_client(store)
        rows = elearning.todo_items(client)
        if rows:
            return rows
    except Exception:
        pass
    try:
        from . import dashboard

        result = dashboard.assignments(store, limit=50)
        return list(result.get("items") or [])
    except Exception:
        return []


def _needs_refresh(row: Dict[str, Any], fields: Dict[str, Any]) -> bool:
    """这条日程的时间范围还是旧的吗（以前是「截止 → +30 分钟」）。"""
    if not str(row.get("title") or "").startswith(TITLE_PREFIX):
        return False
    for key in ("date", "start_time", "end_time"):
        current = str(row.get(key) or "")[:5]
        wanted = str(fields.get(key) or "")
        if key == "date":
            if str(row.get(key) or "") != str(fields.get(key) or ""):
                return True
        elif current != wanted[:5]:
            return True
    return False


def sync(store, items: Optional[List[Dict[str, Any]]] = None,
         force: bool = False) -> Dict[str, Any]:
    """把作业截止写进日程。返回 {"ok","added","updated","skipped","error","total"}。"""
    if not should_sync(store, force):
        return {"ok": False, "added": 0, "updated": 0, "skipped": 0,
                "error": "自动写入没开（或今天已经同步过）", "total": 0}
    rows = items if items is not None else fetch_items(store)
    mapping = load_map(store)
    added = 0
    updated = 0
    skipped = 0
    today = date.today().strftime("%Y-%m-%d")
    for item in rows or []:
        fields = event_fields(item)
        if not fields:
            skipped += 1
            continue
        # 已经过期的截止时间就别补了（块开始和截止都比今天早）
        if str(item.get("due") or "").replace("T", " ")[:10] < today:
            skipped += 1
            continue
        key = dedupe_key(item)
        known = mapping.get(key)
        if known:
            try:
                row = store.get_event(int(known))
            except Exception:
                row = None
            if row is not None:
                if _needs_refresh(row, fields):
                    try:
                        store.update_event(
                            int(known), date=fields["date"],
                            start_time=fields["start_time"],
                            end_time=fields["end_time"],
                            remind_minutes=fields["remind_minutes"],
                            description=fields["description"])
                        updated += 1
                    except Exception:  # noqa: BLE001
                        skipped += 1
                else:
                    skipped += 1
                continue
        found = existing(store, fields, legacy_start=due_clock(item))
        if found:
            mapping[key] = int(found)
            try:
                row = store.get_event(int(found))
            except Exception:
                row = None
            if row is not None and _needs_refresh(row, fields):
                try:
                    store.update_event(
                        int(found), date=fields["date"],
                        start_time=fields["start_time"],
                        end_time=fields["end_time"],
                        remind_minutes=fields["remind_minutes"],
                        description=fields["description"])
                    updated += 1
                except Exception:  # noqa: BLE001
                    skipped += 1
            else:
                skipped += 1
            continue
        try:
            event_id = store.add_event(**fields)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "added": added, "updated": updated,
                    "skipped": skipped,
                    "error": "写日程失败：%s" % exc, "total": len(rows or [])}
        mapping[key] = int(event_id)
        added += 1
    save_map(store, mapping)
    try:
        store.set_setting(KEY_TS, str(int(time.time())))
        store.set_setting(KEY_VERSION, BLOCKS_VERSION)
    except Exception:
        pass
    return {"ok": True, "added": added, "updated": updated, "skipped": skipped,
            "error": "", "total": len(rows or [])}


def summary(result: Dict[str, Any]) -> str:
    """给状态栏一句话。"""
    result = result or {}
    if not result.get("ok"):
        return str(result.get("error") or "没同步")
    added = int(result.get("added") or 0)
    updated = int(result.get("updated") or 0)
    skipped = int(result.get("skipped") or 0)
    if updated:
        return ("已把 %d 个作业截止写进日程（%d 个改成「截止前 %d 分钟」、跳过 %d 个）"
                % (added, updated, LEAD_MINUTES, skipped))
    return "已把 %d 个作业截止写进日程（跳过 %d 个已有 / 已过期的）" % (added, skipped)
