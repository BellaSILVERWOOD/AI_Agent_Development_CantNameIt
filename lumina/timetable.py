"""课程表导入：解析 .ics 日历文件，把课程批量加入日程。

为什么用 .ics 而不是直接读 DanXi（旦夕）的数据？
    旦夕是 Flutter 客户端，课表由服务器下发，本机不放数据库；
    它自己支持把课表导出成日历文件（服务器端 /output_timetable/ 接口），
    所以本程序提供「导入 .ics」这条通用、稳定的通路：
    旦夕（或教务系统）导出 → 在本页选择文件 → 批量写入日程。

支持：VEVENT 的 SUMMARY / LOCATION / DESCRIPTION / DTSTART / DTEND，
以及 RRULE 中常见的 FREQ=WEEKLY + COUNT / UNTIL（隔周上课也能处理 BYDAY 之外的简单情形）。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_MAX_OCCURRENCES = 80


# ---------------------------------------------------------------- DanXi 探测
_DANXI_PATTERN = re.compile(r"danxi", re.IGNORECASE)


def detect_danxi(roots: Optional[Iterable[str]] = None) -> Optional[Path]:
    """在本机常见位置寻找 DanXi 安装目录（找不到返回 None）。"""
    candidates: List[Path] = []
    for root in roots or ("D:\\Setup & Installation Files", "C:\\Program Files",
                          "C:\\Program Files (x86)", "D:\\", "E:\\"):
        base = Path(root)
        if not base.exists():
            continue
        try:
            for child in base.iterdir():
                if child.is_dir() and _DANXI_PATTERN.search(child.name):
                    candidates.append(child)
        except (OSError, PermissionError):
            continue
    for item in candidates:
        if (item / "dan_xi.exe").exists() or (item / "data").exists():
            return item
    return candidates[0] if candidates else None


# ---------------------------------------------------------------- ics 解析
def _unfold(text: str) -> List[str]:
    """处理 ics 的折行（续行以空格或制表符开头）。"""
    lines: List[str] = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw.rstrip("\r\n"))
    return lines


def _unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n")
            .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _parse_datetime(value: str) -> Optional[datetime]:
    """解析 DTSTART/DTEND 的值，兼容 20260901T080000 / 20260901 / ...Z / TZID 前缀。"""
    if not value:
        return None
    text = value.strip()
    if ":" in text and text.split(":", 1)[0].upper().startswith("TZID"):
        text = text.split(":", 1)[1]
    text = text.rstrip("Z")
    for pattern in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _parse_rrule(value: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for chunk in (value or "").split(";"):
        if "=" in chunk:
            key, item = chunk.split("=", 1)
            result[key.strip().upper()] = item.strip().upper()
    return result


def _event_dates(start: datetime, rrule: Dict[str, str]) -> List[date]:
    """按 RRULE 展开成若干日期；不支持的规则退化为只取第一周。"""
    freq = rrule.get("FREQ", "")
    if freq != "WEEKLY":
        return [start.date()]

    interval = 1
    try:
        interval = max(1, int(rrule.get("INTERVAL", "1")))
    except ValueError:
        interval = 1

    count = None
    if "COUNT" in rrule:
        try:
            count = max(1, int(rrule["COUNT"]))
        except ValueError:
            count = None

    until: Optional[datetime] = None
    if "UNTIL" in rrule:
        until = _parse_datetime(rrule["UNTIL"])

    dates: List[date] = []
    cursor = start
    while len(dates) < min(count or _MAX_OCCURRENCES, _MAX_OCCURRENCES):
        if until is not None and cursor > until:
            break
        dates.append(cursor.date())
        cursor = cursor + timedelta(days=7 * interval)
        if count is None and until is None:
            break  # 没有终止条件时只导入第一周，避免无限展开
    return dates


def parse_ics(text: str) -> List[Dict[str, Any]]:
    """解析 ics 文本，返回事件列表；每项含 title/date/start/end/location/description。"""
    if not text:
        return []
    events: List[Dict[str, Any]] = []
    current: Optional[Dict[str, str]] = None
    in_event = False

    for line in _unfold(text):
        upper = line.upper()
        if upper.startswith("BEGIN:VEVENT"):
            current = {}
            in_event = True
            continue
        if upper.startswith("END:VEVENT"):
            if current is not None:
                event = _build_event(current)
                if event:
                    events.append(event)
            current = None
            in_event = False
            continue
        if not in_event or current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        head = key.split(";", 1)[0].upper()
        current.setdefault(head, value)
        if head in ("DTSTART", "DTEND", "RRULE"):
            current[head + "_RAW"] = value
            current[head + "_PARAMS"] = key
        if head == "SUMMARY":
            current["SUMMARY"] = value
        if head == "LOCATION":
            current["LOCATION"] = value
        if head == "DESCRIPTION":
            current["DESCRIPTION"] = value

    events.sort(key=lambda item: (item["dates"][0], item["start"]) if item["dates"]
                else (date.max, item["start"]))
    return events


def _build_event(fields: Dict[str, str]) -> Optional[Dict[str, Any]]:
    raw_start = fields.get("DTSTART_RAW") or fields.get("DTSTART", "")
    start = _parse_datetime(raw_start)
    if start is None:
        return None
    raw_end = fields.get("DTEND_RAW") or fields.get("DTEND", "")
    end = _parse_datetime(raw_end) if raw_end else None
    if end is None or end <= start:
        end = start + timedelta(minutes=45)

    rrule = _parse_rrule(fields.get("RRULE_RAW") or fields.get("RRULE", ""))
    title = _unescape(fields.get("SUMMARY", "")).strip() or "课程"
    location = _unescape(fields.get("LOCATION", "")).strip()
    description = _unescape(fields.get("DESCRIPTION", "")).strip()

    return {
        "title": title,
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "location": location,
        "description": description,
        "dates": _event_dates(start, rrule),
        "rrule": rrule.get("FREQ", ""),
    }


def read_ics(path: Path | str) -> List[Dict[str, Any]]:
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
        try:
            return parse_ics(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return parse_ics(data.decode("utf-8", errors="ignore"))


def flatten(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把「每周重复」的事件展开成逐条记录（便于预览与写入）。"""
    rows: List[Dict[str, Any]] = []
    for event in events:
        for day in event.get("dates") or []:
            rows.append({
                "title": event["title"],
                "date": day.strftime("%Y-%m-%d"),
                "start": event["start"],
                "end": event["end"],
                "location": event.get("location", ""),
                "description": event.get("description", ""),
            })
    rows.sort(key=lambda item: (item["date"], item["start"]))
    return rows


def group_id(store) -> Optional[int]:
    """课程表所在分组的 id（导入时记在 settings，老数据按名字兜底找）。"""
    try:
        raw = store.get_setting("timetable_group_id", "") or ""
        if raw.strip():
            return int(float(raw))
    except Exception:
        pass
    try:
        for group in store.list_groups():
            name = str(group.get("name", ""))
            if "课程表" in name or "课表" in name:
                gid = int(group["id"])
                store.set_setting("timetable_group_id", str(gid))
                return gid
    except Exception:
        pass
    return None


def import_rows(store, rows: List[Dict[str, Any]], color: str = "teal",
                remind_minutes: Optional[int] = 10,
                group_id: Optional[int] = None) -> Tuple[int, int]:
    """把展开后的课程写入日程，返回 (新增条数, 跳过条数)。

    已存在同一时刻、同一标题的日程会被跳过，重复导入不会产生副本。
    """
    existing = {
        (item["date"], item["start_time"], item["title"])
        for item in store.events_between("1970-01-01", "2999-12-31")
    }
    added = 0
    skipped = 0
    for row in rows:
        key = (row["date"], row["start"], row["title"])
        if key in existing:
            skipped += 1
            continue
        description = row.get("description", "")
        if row.get("location"):
            description = (description + "\n" if description else "") + f"地点：{row['location']}"
        store.add_event(
            title=row["title"],
            description=description,
            date=row["date"],
            start_time=row["start"],
            end_time=row["end"],
            remind_minutes=remind_minutes,
            color=color,
            group_id=group_id,
        )
        existing.add(key)
        added += 1
    if group_id:
        try:
            store.set_setting("timetable_group_id", str(int(group_id)))
        except Exception:
            pass
    return added, skipped
