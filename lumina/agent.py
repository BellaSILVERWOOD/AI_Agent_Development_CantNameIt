"""Agent：从微信消息里识别「需要加入日程」的待办，交用户确认后写入日历。

两种识别方式：
1. 配了 API Key（设置 → 本地模型里填接口地址/模型名/API Key）→ 用大模型抽取，准确；
2. 没配 → 用内置规则兜底（识别「明天/周五/10月25日 + 上午9点/14:30 + 地点」这类写法）。

对外接口：
    extract(store, text)       -> List[dict]   # 候选日程（含 confidence/raw）
    add_to_calendar(store, item)-> int         # 真正写进 events
    read_clipboard()           -> str          # 从剪贴板拿微信里刚复制的内容
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from . import prompts

DEFAULT_PROMPT = (
    "你是日程助手。请从下面的聊天记录里找出「需要用户去做、且有明确时间」的事项，"
    "输出 JSON 数组，每项字段：title（简短标题）、date（YYYY-MM-DD）、"
    "start_time（HH:MM）、end_time（HH:MM，未知就留空）、location（地点，未知留空）、"
    "note（原话摘录）。没有就输出 []。只输出 JSON，不要解释。"
)

WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
KEYWORDS = ("开会", "会议", "答辩", "讲座", "上课", "交表", "提交", "截止", "报名",
            "面试", "讨论", "组会", "报告", "考试", "交作业", "签到", "集合", "活动")


# ------------------------------------------------------------------ 入口
def read_clipboard() -> str:
    from .wechat import clipboard_text

    return clipboard_text()


def api_ready(store) -> bool:
    base = (store.get_setting("llm_base_url", "") or "").strip()
    model = (store.get_setting("llm_model_name", "") or "").strip()
    return bool(base and model)


_api_ready = api_ready


def extract(store, text: str) -> Dict[str, Any]:
    """返回 {"ok", "items", "source", "error"}。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "items": [], "source": "none", "error": "没有内容"}
    if _api_ready(store):
        try:
            items = _extract_with_model(store, text)
            return {"ok": True, "items": items, "source": "model", "error": ""}
        except Exception as exc:  # noqa: BLE001
            items = _extract_with_rules(text)
            return {"ok": True, "items": items, "source": "rules",
                    "error": f"模型不可用（{exc}），已用规则识别"}
    return {"ok": True, "items": _extract_with_rules(text), "source": "rules",
            "error": ""}


# ------------------------------------------------------------------ 模型
def _extract_with_model(store, text: str) -> List[Dict[str, Any]]:
    from .ai_chat import chat

    system = (prompts.system("wechat_todo") or DEFAULT_PROMPT)
    system = system.replace("{today}", date.today().isoformat())
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": text[:4000]},
    ]
    reply = chat(store, messages, timeout=90.0)
    payload = _json_block(reply)
    items: List[Dict[str, Any]] = []
    for entry in payload if isinstance(payload, list) else []:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        day = str(entry.get("date") or "").strip()
        if not title or not _looks_like_date(day):
            continue
        items.append({
            "title": title[:60],
            "date": day,
            "start_time": _norm_time(entry.get("start_time")) or "09:00",
            "end_time": _norm_time(entry.get("end_time")) or "",
            "location": str(entry.get("location") or "").strip()[:40],
            "note": str(entry.get("note") or "").strip()[:200],
            "confidence": "model",
        })
    return items


def _json_block(text: str) -> Any:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return []
    return []


# ------------------------------------------------------------------ 规则
_DATE_FULL = re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
_DATE_SHORT = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?(?!\d)")
_DATE_CN = re.compile(r"([一二两三四五六七八九十]{1,3})\s*月\s*([一二两三四五六七八九十]{1,3})\s*[日号]")
_WEEKDAY = re.compile(r"(下下|下|这|本)?周\s*([一二三四五六日天])")
_REL = re.compile(r"(今天|今晚|今早|明天|明晚|明早|后天|大后天)")
_REL_OFFSET = {"今天": 0, "今晚": 0, "今早": 0, "明天": 1, "明晚": 1,
               "明早": 1, "后天": 2, "大后天": 3}
_TIME = re.compile(
    r"(上午|下午|晚上|今晚|明晚|中午|早上|今早|明早|凌晨)?\s*"
    r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*"
    r"(?:[:：]|点|时)\s*"
    r"(半|\d{1,2}|[零一二三四五六七八九十]{1,2})?"
)
_LOCATION = re.compile(r"(?:地点|地址|在|于)\s*[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9\-]{2,20}?(?:楼|室|教室|馆|厅|中心|校区|号|栋)[\u4e00-\u9fa5A-Za-z0-9\-]{0,10})")

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_number(text: str) -> Optional[int]:
    """把「三」「十」「十一」「二十五」这类中文数字转成整数。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if not all(ch in _CN_DIGITS or ch == "十" for ch in text):
        return None
    if "十" not in text:
        value = 0
        for ch in text:
            value = value * 10 + _CN_DIGITS[ch]
        return value
    head, _, tail = text.partition("十")
    tens = _CN_DIGITS.get(head, 1) if head else 1
    ones = _CN_DIGITS.get(tail, 0) if tail else 0
    return tens * 10 + ones


def _looks_like_date(value: str) -> bool:
    return bool(re.match(r"20\d{2}-\d{2}-\d{2}$", value or ""))


def _norm_time(value: Any) -> str:
    text = str(value or "").strip()
    match = re.match(r"^(\d{1,2})[:：](\d{1,2})", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return "%02d:%02d" % (hour, minute)
    return ""


def _resolve_date(line: str, today: Optional[date] = None) -> Optional[str]:
    today = today or date.today()
    full = _DATE_FULL.search(line)
    if full:
        try:
            return date(int(full.group(1)), int(full.group(2)),
                        int(full.group(3))).isoformat()
        except ValueError:
            return None
    short = _DATE_SHORT.search(line)
    if short:
        try:
            month, day = int(short.group(1)), int(short.group(2))
            candidate = date(today.year, month, day)
            if candidate < today - timedelta(days=1):
                candidate = date(today.year + 1, month, day)
            return candidate.isoformat()
        except ValueError:
            pass
    cn = _DATE_CN.search(line)
    if cn:
        month, day = cn_number(cn.group(1)), cn_number(cn.group(2))
        if month and day:
            try:
                candidate = date(today.year, month, day)
                if candidate < today - timedelta(days=1):
                    candidate = date(today.year + 1, month, day)
                return candidate.isoformat()
            except ValueError:
                pass
    rel = _REL.search(line)
    if rel:
        return (today + timedelta(days=_REL_OFFSET[rel.group(1)])).isoformat()
    week = _WEEKDAY.search(line)
    if week:
        prefix, name = week.group(1) or "", week.group(2)
        target = WEEKDAYS.get(name)
        if target is not None:
            delta = (target - today.weekday()) % 7
            if prefix in ("下", "下下"):
                delta += 7 if prefix == "下" else 14
            elif delta == 0 and prefix in ("这", "本"):
                delta = 0
            return (today + timedelta(days=delta)).isoformat()
    return None


def _resolve_time(line: str) -> tuple:
    match = _TIME.search(line)
    if not match:
        return "09:00", ""
    period, hour_text, minute_text = match.groups()
    hour = cn_number(hour_text)
    if hour is None:
        return "09:00", ""
    if (minute_text or "").strip() == "半":
        minute = 30
    else:
        minute = cn_number(minute_text) or 0
    if period in ("下午", "晚上", "今晚", "明晚") and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if period == "凌晨" and hour == 12:
        hour = 0
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        start = "%02d:%02d" % (hour, minute)
        end_hour = hour + 1
        end = "%02d:%02d" % (end_hour % 24, minute)
        return start, end
    return "09:00", ""


def _clean_title(line: str, location: str = "") -> str:
    """把日期/时间/地点从原话里抠掉，剩下当标题。"""
    text = line
    for pattern in (_DATE_FULL, _DATE_SHORT, _DATE_CN, _WEEKDAY, _REL, _TIME):
        text = pattern.sub(" ", text)
    if location:
        text = text.replace(location, " ")
    text = re.sub(r"(地点|地址)", " ", text)
    text = re.sub(r"(?<=\s)在|在(?=\s)|(?<=\s)于|于(?=\s)", " ", text)
    text = re.sub(r"(?<![\dA-Za-z])\d{2,4}(?![\dA-Za-z])", " ", text)
    text = re.sub(r"[\s，,、。；;！!？?]+", " ", text).strip()
    text = re.sub(r"^(要|记得|请|记得要)\s*", "", text).strip()
    if len(text) < 2:
        text = re.sub(r"\s+", " ", line).strip()
    return text[:28].strip(" -—·") or "待办"


def _extract_with_rules(text: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for raw in re.split(r"[\n。；;]+", text):
        line = raw.strip()
        if len(line) < 4:
            continue
        day = _resolve_date(line)
        if not day:
            continue
        if not any(word in line for word in KEYWORDS) and not _TIME.search(line):
            continue
        start, end = _resolve_time(line)
        guessed = _TIME.search(line) is None
        location_match = _LOCATION.search(line)
        location = (location_match.group(1) if location_match else "")[:40]
        title = _clean_title(line, location)
        items.append({
            "title": title,
            "date": day,
            "start_time": start,
            "end_time": end,
            "location": location,
            "note": line[:200],
            "time_guessed": guessed,
            "confidence": "rules",
        })
    return items


# ------------------------------------------------------------------ 写入
def add_to_calendar(store, item: Dict[str, Any], group_id=None) -> int:
    """把确认过的候选写进日程表。"""
    title = (item.get("title") or "").strip() or "未命名日程"
    day = item.get("date") or date.today().isoformat()
    start = _norm_time(item.get("start_time")) or "09:00"
    end = _norm_time(item.get("end_time"))
    if not end:
        hour = min(23, int(start.split(":")[0]) + 1)
        end = "%02d:%s" % (hour, start.split(":")[1])
    location = (item.get("location") or "").strip()
    note = (item.get("note") or "").strip()
    description = location and f"地点：{location}" or note
    if location and note and note != location:
        description = f"地点：{location}\n{note}"
    return store.add_event(
        title=title, description=description, date=day,
        start_time=start, end_time=end, remind_minutes=10,
        color="sky", group_id=group_id,
    )
