# -*- coding: utf-8 -*-
"""仪表板的数据层：今天、天气、即将到来的日程、最近通知、ehall 卡片清单。

设计原则：**能本地算的绝不联网，能联网的必须能优雅失败**。
每个取数函数都返回带 ``ok`` / ``error`` 的字典，界面据此显示「暂无 / 需要登录 / 连不上」，
而不是弹异常。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .utils import iso, local_datetime

WEATHER_KEY = "dashboard_weather"
WEATHER_TS_KEY = "dashboard_weather_at"
WEATHER_TTL = 600            # 天气缓存 10 分钟
WEATHER_URL = ("https://api.open-meteo.com/v1/forecast"
               "?latitude=31.23&longitude=121.47"
               "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
               "weather_code,wind_speed_10m&timezone=Asia%2FShanghai")
CITY = "上海"

# WMO 天气代码 → 中文
WEATHER_CODES = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}


# ------------------------------------------------------------------ 今天
def today_info() -> Dict[str, Any]:
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return {
        "date": now.strftime("%Y-%m-%d"),
        "ymd": now.strftime("%Y 年 %m 月 %d 日"),
        "weekday": weekdays[now.weekday()],
        "week_number": now.isocalendar()[1],
        "time": now.strftime("%H:%M"),
        "greeting": _greeting(now.hour),
    }


def _greeting(hour: int) -> str:
    if hour < 6:
        return "早点休息"
    if hour < 11:
        return "早上好"
    if hour < 14:
        return "中午好"
    if hour < 18:
        return "下午好"
    return "晚上好"


# ------------------------------------------------------------------ 天气
def weather(store, force: bool = False, timeout: float = 8.0) -> Dict[str, Any]:
    """上海当前天气（open-meteo，不需要 key）。缓存 10 分钟。"""
    cached = ""
    try:
        cached = str(store.get_setting(WEATHER_KEY, "") or "")
        stamp = float(store.get_setting(WEATHER_TS_KEY, "0") or 0)
    except Exception:
        stamp = 0.0
    if cached and not force and time.time() - stamp < WEATHER_TTL:
        return {"ok": True, "text": cached, "cached": True, "error": ""}
    try:
        import requests

        response = requests.get(WEATHER_URL, timeout=timeout,
                                headers={"User-Agent": "DanLume/1.0"})
        if response.status_code >= 400:
            return {"ok": False, "text": cached, "cached": bool(cached),
                    "error": "天气接口返回 HTTP %d" % response.status_code}
        data = (response.json() or {}).get("current") or {}
        temp = data.get("temperature_2m")
        feels = data.get("apparent_temperature")
        humidity = data.get("relative_humidity_2m")
        wind = data.get("wind_speed_10m")
        code = int(data.get("weather_code") or 0)
        desc = WEATHER_CODES.get(code, "天气代码 %d" % code)
        bits = [f"{CITY} {desc}"]
        if temp is not None:
            bits.append("%.0f°C" % float(temp))
        if feels is not None and temp is not None and abs(float(feels) - float(temp)) >= 2:
            bits.append("体感 %.0f°C" % float(feels))
        if humidity is not None:
            bits.append("湿度 %.0f%%" % float(humidity))
        if wind is not None:
            bits.append("风速 %.0f km/h" % float(wind))
        text = " · ".join(bits)
        try:
            store.set_setting(WEATHER_KEY, text)
            store.set_setting(WEATHER_TS_KEY, str(time.time()))
        except Exception:
            pass
        return {"ok": True, "text": text, "cached": False, "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": cached, "cached": bool(cached),
                "error": "天气取不到（%s）" % str(exc)[:60]}


# ------------------------------------------------------------------ 即将到来
def upcoming_events(store, limit: int = 6, horizon_days: int = 3
                    ) -> List[Dict[str, Any]]:
    """从现在起 horizon_days 天内还没结束的日程。

    `ongoing` 为真表示**已经开始、还没结束**（界面上写「进行中」，不显示开始时间）。
    """
    now = datetime.now()
    start = date.today().strftime("%Y-%m-%d")
    end = (date.today() + timedelta(days=max(1, horizon_days))).strftime("%Y-%m-%d")
    items: List[Dict[str, Any]] = []
    try:
        index = store.event_index(start, end)
    except Exception:
        return items
    for day_str in sorted(index.keys()):
        for item in index.get(day_str) or []:
            try:
                day = datetime.strptime(day_str, "%Y-%m-%d").date()
            except Exception:
                continue
            begin = str(item.get("start_time") or "")[:5]
            finish = str(item.get("end_time") or "")[:5]
            try:
                starts = datetime.strptime(
                    "%s %s" % (day_str, begin or "23:59"), "%Y-%m-%d %H:%M")
            except Exception:
                starts = datetime.combine(day, datetime.min.time())
            try:
                ends = datetime.strptime(
                    "%s %s" % (day_str, finish), "%Y-%m-%d %H:%M") if finish else starts
            except Exception:
                ends = starts
            if ends < starts:
                # 跨夜（23:40 → 00:10 这种）
                ends = ends + timedelta(days=1)
            if ends < now:
                continue
            items.append({**item, "day": day_str, "starts": starts.isoformat(),
                          "begin": begin, "finish": finish,
                          "ongoing": starts <= now < ends,
                          "minutes_until": int((starts - now).total_seconds() // 60)})
    items.sort(key=lambda row: row.get("starts") or "")
    return items[:max(1, limit)]


# ------------------------------------------------------------------ 通知 / 作业
def recent_notices(store, limit: int = 5) -> List[Dict[str, Any]]:
    """最近抓到的教务通知（本地库，不联网）。"""
    rows: List[Dict[str, Any]] = []
    try:
        cursor = store.conn.execute(
            "SELECT title, url, source, fetched_at, published_at FROM notices"
            " ORDER BY id DESC LIMIT ?", (int(limit),))
        rows = [dict(row) for row in cursor.fetchall()]
    except Exception:
        rows = []
    return rows


#: 最近作业的缓存键（作业页与仪表板共用一份，谁先读到都行）
ASSIGN_KEY = "homework_assignments"
ASSIGN_TS_KEY = "homework_assignments_at"
#: 缓存多久之后算「旧了」（30 分钟）
ASSIGN_TTL = 1800
ASSIGN_EMPTY_HINT = ("点右上角「同步」读一次作业：来自 eLearning 的 To Do"
                     "（elearning.fudan.edu.cn/dash）")


def assignments(store, limit: int = 5) -> Dict[str, Any]:
    """最近要交的作业：优先用缓存（作业页 / 仪表板自己同步都写这一份）。

    缓存是 `homework_assignments`（JSON 数组），时间戳是 `homework_assignments_at`。
    没有缓存时给出「去同步」的人话，不联网。
    """
    items: List[Dict[str, Any]] = []
    stamp = 0.0
    try:
        raw = str(store.get_setting(ASSIGN_KEY, "") or "")
        stamp = float(store.get_setting(ASSIGN_TS_KEY, "0") or 0)
    except Exception:
        raw = ""
    if raw:
        import json

        try:
            for item in json.loads(raw):
                if isinstance(item, dict):
                    items.append(item)
        except Exception:
            items = []
    items.sort(key=lambda row: str(row.get("due") or "9999"))
    return {"ok": bool(items), "items": items[:limit], "at": stamp,
            "error": "" if items else ASSIGN_EMPTY_HINT}


def assignments_age(store) -> Optional[float]:
    """缓存是多少秒之前写的；没有缓存返回 None。"""
    try:
        stamp = float(store.get_setting(ASSIGN_TS_KEY, "0") or 0)
    except Exception:
        stamp = 0.0
    if stamp <= 0:
        return None
    return max(0.0, time.time() - stamp)


def save_assignments(store, rows: List[Dict[str, Any]]) -> None:
    """写「最近作业」缓存（作业页与仪表板同步共用）。"""
    import json

    clean: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("name") or "").strip()
        if not title:
            continue
        clean.append({
            "title": title,
            "course": str(row.get("course") or row.get("course_name") or ""),
            "due": local_datetime(str(row.get("due") or row.get("due_at") or "")),
            "url": str(row.get("url") or ""),
        })
    try:
        store.set_setting(ASSIGN_KEY, json.dumps(clean, ensure_ascii=False))
        store.set_setting(ASSIGN_TS_KEY, str(time.time()))
    except Exception:
        pass


def sync_assignments(store, limit: int = 20) -> Dict[str, Any]:
    """去 eLearning 的 dash（To Do）读一次作业，写进缓存。

    在后台线程里跑：只读网络 + 写设置，不碰界面。返回
    ``{"ok": bool, "count": int, "items": [...], "error": str}``，
    失败一律给人话（没登录 / 没网 / 接口变了）。
    """
    try:
        from . import elearning
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "count": 0, "items": [],
                "error": "缺少网络组件：%s" % str(exc)[:60]}
    try:
        client = elearning.open_client(store)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "count": 0, "items": [],
                "error": "读 eLearning 登录态失败：%s" % str(exc)[:60]}
    if not getattr(client, "has_cookies", lambda: False)():
        return {"ok": False, "count": 0, "items": [],
                "error": "还没登录 eLearning：到「作业」页的网页里登录一次，"
                         "登录态会保存，之后这里自动同步"}
    try:
        rows = elearning.todo_items(client)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "count": 0, "items": [],
                "error": "读 eLearning 的 To Do 失败：%s" % str(exc)[:80]}
    rows = rows[:max(1, int(limit))]
    if rows:
        save_assignments(store, rows)
    else:
        # To Do 空着时**别覆盖**已有缓存：作业页读过的整份作业清单可能比 To Do 多
        # （To Do 只列「要交/待评分」），清掉会让用户看着像「作业都没了」。
        pass
    return {"ok": True, "count": len(rows), "items": rows,
            "error": "" if rows else
            "eLearning 的 To Do 里现在没有要交的作业（过期就不显示了）",
            "kept": not rows and assignments(store, limit=1).get("ok")}


#: 作业页的整份清单（课程 + 每个作业的附件）：重启后先显示上次读到的，
#: 否则「我下载过的那些作业」一关就全看不见了（用户报过这个问题）。
HOMEWORK_CACHE_KEY = "homework_cache"


def save_homework_cache(store, courses: List[Dict[str, Any]],
                        assignments: List[Dict[str, Any]]) -> None:
    """把作业页读到的课程与作业整份存下来（含附件列表）。"""
    import json

    try:
        payload = {
            "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "courses": courses or [],
            "assignments": assignments or [],
        }
        store.set_setting(HOMEWORK_CACHE_KEY,
                          json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass
    # 仪表板的「最近作业」只要**还没过期**的（否则一堆上学期的作业排在前面）
    today = datetime.now().strftime("%Y-%m-%d")
    upcoming = []
    for item in assignments or []:
        if not isinstance(item, dict):
            continue
        when = local_datetime(str(item.get("due") or item.get("due_at") or ""))
        if when and when[:10] < today:
            continue
        upcoming.append(item)
    save_assignments(store, upcoming)


def load_homework_cache(store) -> Dict[str, Any]:
    """读回作业页上次的清单：``{"at": str, "courses": [...], "assignments": [...]}``。"""
    import json

    try:
        raw = str(store.get_setting(HOMEWORK_CACHE_KEY, "") or "")
    except Exception:
        raw = ""
    if not raw:
        return {"at": "", "courses": [], "assignments": []}
    try:
        data = json.loads(raw)
    except Exception:
        return {"at": "", "courses": [], "assignments": []}
    if not isinstance(data, dict):
        return {"at": "", "courses": [], "assignments": []}
    courses = [item for item in (data.get("courses") or [])
               if isinstance(item, dict)]
    assignments = [item for item in (data.get("assignments") or [])
                   if isinstance(item, dict)]
    return {"at": str(data.get("at") or ""), "courses": courses,
            "assignments": assignments}


# ------------------------------------------------------------------ ehall 卡片
# 每张卡：需要 ehall 登录；数据接口地址可以让用户自己填（各人学校/版本不同），
# 填了就按 JSON 取，没填就只显示入口按钮。
EHALL_HOME = "https://ehall.fudan.edu.cn/"

EHALL_CARDS = [
    {"id": "card", "title": "校园卡余额", "desc": "一卡通余额与最近消费",
     "key": "ehall_json_card", "url": "",
     "hint": "ehall → 搜索「一卡通 / 校园卡」，进入后把地址复制到这里"},
    {"id": "bus", "title": "校车安排", "desc": "只显示你设置的两个方向的班次",
     "key": "ehall_json_bus", "url": "",
     "hint": "ehall → 搜索「校车 / 班车」，进入时刻表后把地址复制到这里"},
    {"id": "sports", "title": "体育刷锻", "desc": "本学期刷锻次数与达标情况",
     "key": "ehall_json_sports", "url": "",
     "hint": "ehall → 搜索「刷锻 / 课外锻炼」，进入后把地址复制到这里"},
    {"id": "library_people", "title": "图书馆人数", "desc": "各馆当前在馆人数",
     "key": "ehall_json_library_people", "url": "",
     "hint": "图书馆在馆人数页面（或 ehall 里的「图书馆」服务）地址"},
    {"id": "ehall_todo", "title": "我的 ehall 通知", "desc": "待办和进行中的全部，已完成只要最近 3 条",
     "key": "ehall_json_todo", "url": EHALL_HOME,
     "hint": "默认取 ehall 首页的「我的待办」，登录一次就会有内容"},
    {"id": "empty_room", "title": "空教室", "desc": "选教学楼和教室，查它们几点到几点空着",
     "key": "ehall_json_empty_room", "url": "",
     "hint": "「空闲教室查询」页面的地址（也可以在「空教室」页里填）"},
]


def card_spec(card_id: str) -> Dict[str, Any]:
    for spec in EHALL_CARDS:
        if spec["id"] == card_id:
            return dict(spec)
    return {}


def source_url(store, card_id: str) -> str:
    try:
        url = store.get_setting("ehall_json_%s" % card_id, "") or ""
    except Exception:
        url = ""
    return str(url).strip() or str(card_spec(card_id).get("url") or "").strip()


# ------------------------------------------------------------------ 空教室：教学楼 / 教室
#: 默认教学楼（复旦邯郸校区教学楼的常见叫法；用户可以在设置里改）
DEFAULT_BUILDINGS = ["二教", "三教", "四教", "五教", "六教", "光华西", "光华东",
                     "新闻", "实验楼", "邯郸", "江湾", "枫林", "张江"]
BUILDINGS_KEY = "ehall_room_buildings"
ROOMS_KEY = "ehall_room_rooms_%s"          # + 教学楼名
#: 过滤规则版本（老版本把「请选择 / 8:00 / 教室 00:00」这类东西当楼名/教室名存下来了）
ROOM_FILTER_KEY = "ehall_room_filter"
ROOM_FILTER_VERSION = "2"

#: 页面上的表单词/说明词，不是楼名也不是教室名
#: 校区名（不能当成教学楼）
CAMPUS_NAMES = ("邯郸", "江湾", "张江", "枫林")
ROOM_CHROME = ("请选择", "全部校区", "全部教学楼", "全部教室", "查询", "教学楼",
               "教室", "校区", "在线教学", "调课", "借用", "关键词", "空格",
               "分隔", "重置", "打印", "导出", "上一页", "下一页", "返回", "楼",
               "×", "X", "-", "—")
#: 教室名：H2101 / JB100 / F1101 / 光华西301 这种（字母或中文 + 2~4 位数字）
ROOM_NAME_RE = re.compile(r"^(?:[A-Za-z]{1,3}|[\u4e00-\u9fa5]{1,4})\s?-?\d{2,4}[A-Za-z]?$")
#: 楼名：二教 / 光华西 / 实验楼 / 新闻 / 江湾 / 邯郸，或 JA / JB / F1 / H2 / HGD / F报
BUILDING_NAME_RE = re.compile(
    r"^(?:[\u4e00-\u9fa5]{2,4}|[A-Za-z]{1,3}\d?|[A-Za-z][\u4e00-\u9fa5]{1,2})$")
_TIME_TEXT_RE = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}")


def _looks_chrome(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    if len(value) > 12:                 # 楼名/教室名不可能这么长
        return True
    if _TIME_TEXT_RE.search(value):     # 8:00 / 12:15 是时段，不是教室
        return True
    if "<<" in value or ">>" in value or "|" in value or "<" in value:
        return True
    if len(value.split()) > 2:          # 「查询 45/24481 楼 16 …」这种整行文字
        return True
    return any(word in value for word in ROOM_CHROME)


def is_room_name(text: str) -> bool:
    """「H2101」「JB100」「光华西301」是真教室；「8:00」「教室 00:00」不是。"""
    value = str(text or "").strip()
    if _looks_chrome(value):
        return False
    return bool(ROOM_NAME_RE.match(value))


def is_building_name(text: str) -> bool:
    """「二教」「光华西」「江湾」「JA」「JB」「F1」是真教学楼。"""
    value = str(text or "").strip()
    if _looks_chrome(value):
        return False
    return bool(BUILDING_NAME_RE.match(value))


def clean_buildings(values) -> List[str]:
    """过滤并用掉重复项的楼名列表（保持原顺序）。校区名不算教学楼。"""
    out: List[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if text in CAMPUS_NAMES:
            continue
        if not is_building_name(text):
            continue
        if text in out:
            continue
        out.append(text)
    return out


def clean_rooms(values) -> List[str]:
    out: List[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if not is_room_name(text):
            continue
        if text in out:
            continue
        out.append(text)
    return out


def _split_list(raw: str) -> List[str]:
    return [part.strip() for part in str(raw or "").replace("，", ",").split(",")
            if part.strip()]


def _migrate_room_settings(store) -> None:
    """把老版本存下来的脏楼名/脏教室名清掉（只做一次）。

    以前教学楼下拉直接把页面上的选项塞进来，于是 `请选择 / 全部校区 / 在线教学`
    都成了「教学楼」；教室名更离谱 —— 抓到的是按时间排的老表，于是
    `教室 00:00 / 8:00 / 8:55` 全被当成教室存进了 `ehall_room_rooms_二教`。
    """
    try:
        if (store.get_setting(ROOM_FILTER_KEY, "") or "") == ROOM_FILTER_VERSION:
            return
        raw = str(store.get_setting(BUILDINGS_KEY, "") or "")
        cleaned = clean_buildings(_split_list(raw))
        store.set_setting(BUILDINGS_KEY,
                          ",".join(cleaned or DEFAULT_BUILDINGS))
        for building in _split_list(raw):
            key = ROOMS_KEY % building
            old = str(store.get_setting(key, "") or "")
            if not old:
                continue
            try:
                items = json.loads(old)
            except Exception:
                items = []
            rooms = clean_rooms(items if isinstance(items, list) else [])
            if is_building_name(building) and rooms:
                store.set_setting(key, json.dumps(rooms, ensure_ascii=False))
            else:
                store.set_setting(key, "")
        store.set_setting(ROOM_FILTER_KEY, ROOM_FILTER_VERSION)
    except Exception:
        pass


def empty_room_buildings(store) -> List[str]:
    """可选的「教学楼」列表（设置里能改；脏数据在这里就被滤掉）。"""
    _migrate_room_settings(store)
    raw = ""
    try:
        raw = str(store.get_setting(BUILDINGS_KEY, "") or "")
    except Exception:
        raw = ""
    picked = clean_buildings(_split_list(raw))
    return picked or list(DEFAULT_BUILDINGS)


def set_empty_room_buildings(store, buildings: List[str]) -> None:
    cleaned = clean_buildings(buildings)
    try:
        store.set_setting(BUILDINGS_KEY, ",".join(cleaned))
    except Exception:
        pass


def empty_room_rooms(store, building: str) -> List[str]:
    """上一次从 ehall 抓到的、这个教学楼里的教室名（只留真教室名）。"""
    if not is_building_name(building):
        return []
    try:
        raw = str(store.get_setting(ROOMS_KEY % str(building or ""), "") or "")
    except Exception:
        return []
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return clean_rooms(data)


def set_empty_room_rooms(store, building: str, rooms: List[str]) -> None:
    cleaned = clean_rooms(rooms)
    try:
        store.set_setting(ROOMS_KEY % str(building or ""),
                          json.dumps(cleaned, ensure_ascii=False))
    except Exception:
        pass


# ------------------------------------------------------------------ 校区 → 教学楼 → 教室
#: 复旦的四个校区（空教室查询页的第一个下拉就是它）
CAMPUSES = list(CAMPUS_NAMES)
CAMPUS_KEY = "ehall_campus_buildings"
#: 楼名里能看出校区的关键字
_CAMPUS_HINTS = (
    ("江湾", ("江湾",)), ("张江", ("张江",)), ("枫林", ("枫林",)),
)


def guess_campus(building: str) -> str:
    """这个名字属于哪个校区：看楼名里有没有「江湾 / 张江 / 枫林」，其余默认邯郸。"""
    name = str(building or "")
    for campus, hints in _CAMPUS_HINTS:
        if any(hint in name for hint in hints):
            return campus
    return "邯郸"


def campus_buildings_map(store) -> Dict[str, List[str]]:
    """校区 → 教学楼列表（先读设置里存的，没存就按楼名猜）。"""
    raw = ""
    try:
        raw = str(store.get_setting(CAMPUS_KEY, "") or "")
    except Exception:
        raw = ""
    stored: Dict[str, List[str]] = {}
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for campus, names in data.items():
                    stored[str(campus)] = clean_buildings(names)
        except Exception:
            stored = {}
    mapping: Dict[str, List[str]] = {campus: [] for campus in CAMPUSES}
    for building in empty_room_buildings(store):
        campus = guess_campus(building)
        mapping.setdefault(campus, []).append(building)
    for campus, names in stored.items():
        if names:
            mapping[campus] = names
    for campus in mapping:
        mapping[campus] = list(dict.fromkeys(mapping[campus]))
    return mapping


def buildings_for_campus(store, campus: str) -> List[str]:
    mapping = campus_buildings_map(store)
    if campus and campus in mapping and mapping[campus]:
        return mapping[campus]
    return empty_room_buildings(store)


def set_campus_buildings(store, campus: str, buildings: List[str]) -> None:
    """记住「这个校区有哪些教学楼」（抓到页面选项后写进来）。"""
    mapping = campus_buildings_map(store)
    mapping[str(campus or "")] = clean_buildings(buildings)
    try:
        store.set_setting(CAMPUS_KEY, json.dumps(mapping, ensure_ascii=False))
    except Exception:
        pass



def ehall_cards(store) -> List[Dict[str, Any]]:
    """给界面用的卡片状态：有没有配接口地址。"""
    cards = []
    for spec in EHALL_CARDS:
        try:
            endpoint = str(store.get_setting(spec["key"], "") or "").strip()
        except Exception:
            endpoint = ""
        cards.append({**spec, "endpoint": endpoint, "configured": bool(endpoint)})
    return cards


def bus_directions(store) -> List[str]:
    """默认显示的两个校车方向。"""
    try:
        raw = str(store.get_setting("dashboard_bus_directions", "") or "")
    except Exception:
        raw = ""
    picked = [part.strip() for part in raw.split(",") if part.strip()]
    return picked[:2]


def set_bus_directions(store, directions: List[str]) -> None:
    cleaned = [str(item).strip() for item in directions if str(item).strip()][:2]
    try:
        store.set_setting("dashboard_bus_directions", ",".join(cleaned))
    except Exception:
        pass
