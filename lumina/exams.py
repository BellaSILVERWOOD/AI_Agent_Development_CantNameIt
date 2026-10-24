"""从教务系统读考试安排（期中/期末），并加进日历 + 重要日倒计时。

怎么读：教务（fdjwgl）是统一身份认证 + SPA，没法用请求直接拿，所以沿用课程表那套：
`exam_dialog.py` 里开一个内嵌浏览器（复用 `data/browser/fdu` 的登录态），
用户在窗口里登录并打开「我的考试」，点「读取考试」，用 JS 把页面上的表格行和正文捞出来，
再交给这里的解析器。

解析尽量宽容：
* 一行里能同时找到「日期」和「时间」就算一条；
* 标题取那一行里最像课程名的单元格；
* 地点取带「楼/室/教室/校区/馆/厅」的单元格；
* 表格解析不出来时，退回「按行扫正文」（`parse_text`）。

对外接口：
    EXAM_URLS
    PAGE_JS                在页面里收集数据的 JS
    parse_exams(raw)       raw = {"rows": [...], "text": "..."} -> [item]
    parse_rows(rows)       -> [item]
    parse_text(text)       -> [item]
    add_exams(store, items, as_important=True) -> (added, skipped)
    ensure_group(store)    「考试」分组 id
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

EXAM_URLS = [
    "https://fdjwgl.fudan.edu.cn/student/for-std/exam-arrange",
    "https://fdjwgl.fudan.edu.cn/student/for-std/exam-arrange/index",
    "https://fdjwgl.fudan.edu.cn/student/for-std/exam",
]

GROUP_NAME = "考试"

# 在页面里：把表格行、卡片行、正文都收进 window.__luminaExams
PAGE_JS = r"""
(function(){
  function cellText(cell){ return (cell.innerText||cell.textContent||'').replace(/\s+/g,' ').trim(); }
  var rows = [];
  document.querySelectorAll('table tr').forEach(function(tr){
    var cells = [];
    tr.querySelectorAll('th,td').forEach(function(td){ cells.push(cellText(td)); });
    if (cells.length >= 2) rows.push(cells);
  });
  document.querySelectorAll('.ant-table-row, .el-table__row, [role="row"]').forEach(function(r){
    var cells = [];
    r.querySelectorAll('[role="cell"], .ant-table-cell, td').forEach(function(td){ cells.push(cellText(td)); });
    if (cells.length >= 2) rows.push(cells);
  });
  var text = (document.body.innerText || '').replace(/\u00a0/g,' ');
  var title = document.title || '';
  window.__luminaExams = {done:true, rows:rows, text:text, title:title,
                          url:location.href, at:Date.now()};
  return 'ok';
})()
"""

# ------------------------------------------------------------------ 解析工具
_DATE_PATTERNS = (
    re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?"),
    re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?"),
    re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})(?!\d)"),
)
_TIME_RANGE = re.compile(
    r"(\d{1,2}:\d{2})\s*(?:-|~|—|–|至|到)\s*(\d{1,2}:\d{2})")
_TIME_ONE = re.compile(r"(?<!\d)(\d{1,2}:\d{2})(?!\d)")
_LOCATION_HINT = ("楼", "室", "教室", "校区", "馆", "厅", "中心", "号楼", "座")
_SEAT = re.compile(r"(?:座位|座号|考号)\s*[:：]?\s*([A-Za-z0-9\-]+)")


def _today() -> date:
    return date.today()


def find_date(text: str) -> Optional[str]:
    raw = text or ""
    match = _DATE_PATTERNS[0].search(raw)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)),
                        int(match.group(3))).isoformat()
        except ValueError:
            return None
    match = _DATE_PATTERNS[1].search(raw)
    if match:
        try:
            return date(_today().year, int(match.group(1)),
                        int(match.group(2))).isoformat()
        except ValueError:
            return None
    match = _DATE_PATTERNS[2].search(raw)
    if match:
        try:
            moment = date(_today().year, int(match.group(1)), int(match.group(2)))
            if (moment - _today()).days < -120:      # 明显是去年的，推到下一年
                moment = moment.replace(year=_today().year + 1)
            return moment.isoformat()
        except ValueError:
            return None
    return None


def find_time(text: str) -> Tuple[str, str]:
    raw = text or ""
    match = _TIME_RANGE.search(raw)
    if match:
        return _norm_time(match.group(1)), _norm_time(match.group(2))
    match = _TIME_ONE.search(raw)
    if match:
        start = _norm_time(match.group(1))
        hour, minute = start.split(":")
        end = "%02d:%s" % (min(23, int(hour) + 1), minute)
        return start, end
    return "09:00", ""


def _norm_time(value: str) -> str:
    match = re.match(r"^(\d{1,2})[:：](\d{1,2})", (value or "").strip())
    if not match:
        return ""
    hour, minute = int(match.group(1)), int(match.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return "%02d:%02d" % (hour, minute)
    return ""


def _looks_like_location(text: str) -> bool:
    raw = (text or "").strip()
    return bool(raw) and len(raw) <= 30 and any(hint in raw for hint in _LOCATION_HINT)


def _looks_like_title(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or len(raw) > 40:
        return False
    if find_date(raw):
        return False
    if _TIME_ONE.search(raw):
        return False
    if re.fullmatch(r"[\d\s:：\-/.]+", raw):
        return False
    return any("\u4e00" <= ch <= "\u9fff" for ch in raw)


def _clean_title(text: str) -> str:
    raw = (text or "").strip()
    raw = _DATE_PATTERNS[0].sub("", raw)
    raw = _DATE_PATTERNS[1].sub("", raw)
    raw = _TIME_RANGE.sub("", raw)
    raw = _TIME_ONE.sub("", raw)
    raw = re.sub(r"[\s|｜,，、;；:：]+", " ", raw).strip(" -—·")
    return raw[:40]


def _seat(text: str) -> str:
    match = _SEAT.search(text or "")
    return match.group(1) if match else ""


# ------------------------------------------------------------------ 表格解析
def parse_rows(rows: List[List[str]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen = set()
    for row in rows or []:
        cells = [str(cell or "").strip() for cell in row]
        joined = " ".join(cells)
        day = find_date(joined)
        if not day:
            continue
        start, end = find_time(joined)
        location = ""
        for cell in cells:
            if _looks_like_location(cell) and not find_date(cell):
                location = cell
                break
        title = ""
        for cell in cells:
            if _looks_like_title(cell) and cell != location:
                title = _clean_title(cell)
                break
        if not title:
            title = _clean_title(joined) or "考试"
        seat = _seat(joined)
        key = (title, day, start)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "title": title,
            "date": day,
            "start_time": start,
            "end_time": end,
            "location": location,
            "seat": seat,
            "note": joined[:160],
            "confidence": "table",
        })
    return items


# ------------------------------------------------------------------ 正文兜底
def parse_text(text: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if len(line) < 4 or len(line) > 160:
            continue
        day = find_date(line)
        if not day or not _TIME_ONE.search(line):
            continue
        start, end = find_time(line)
        location = ""
        for token in re.split(r"[\s,，、|｜;；]+", line):
            if _looks_like_location(token):
                location = token
                break
        title = _clean_title(line) or "考试"
        key = (title, day, start)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "title": title[:40],
            "date": day,
            "start_time": start,
            "end_time": end,
            "location": location,
            "seat": _seat(line),
            "note": line[:160],
            "confidence": "text",
        })
    return items


def parse_exams(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """先按表格解析；表格没结果就扫正文。"""
    rows = raw.get("rows") if isinstance(raw, dict) else None
    text = raw.get("text") if isinstance(raw, dict) else ""
    items = parse_rows(rows or [])
    if items:
        return items
    return parse_text(text or "")


# ------------------------------------------------------------------ 写进日历
def ensure_group(store) -> int:
    from . import important

    return important.ensure_exam_group(store)


def _existing_keys(store) -> set:
    keys = set()
    try:
        rows = store.conn.execute(
            "SELECT title, date, start_time FROM events").fetchall()
    except Exception:
        return keys
    for row in rows:
        keys.add((str(row["title"]), str(row["date"]), str(row["start_time"])))
    return keys


def add_exams(store, items: List[Dict[str, Any]],
              as_important: bool = True) -> Tuple[int, int]:
    """写入日历（分组「考试」）；as_important 时同时建重要日。返回 (新增, 跳过)。"""
    from . import important

    group_id = ensure_group(store)
    existing = _existing_keys(store)
    added = 0
    skipped = 0
    for item in items:
        title = (item.get("title") or "考试").strip()
        day = item.get("date") or ""
        start = _norm_time(item.get("start_time") or "") or "09:00"
        end = _norm_time(item.get("end_time") or "")
        if not end:
            end = "%02d:%s" % (min(23, int(start.split(":")[0]) + 2),
                               start.split(":")[1])
        if not day:
            skipped += 1
            continue
        if (title, day, start) in existing:
            skipped += 1
            continue
        description = str(item.get("note") or "")
        location = str(item.get("location") or "")
        seat = str(item.get("seat") or "")
        pieces = []
        if location:
            pieces.append(f"地点：{location}")
        if seat:
            pieces.append(f"座位：{seat}")
        if description:
            pieces.append(description)
        event_id = store.add_event(
            title=title, description="\n".join(pieces), date=day,
            start_time=start, end_time=end, remind_minutes=60,
            color="rose", group_id=group_id,
        )
        existing.add((title, day, start))
        added += 1
        if as_important:
            try:
                important.add_from_event(
                    store,
                    {"id": event_id, "title": title, "date": day, "color": "rose"},
                    note=location or description[:60],
                )
            except Exception:
                pass
    return added, skipped
