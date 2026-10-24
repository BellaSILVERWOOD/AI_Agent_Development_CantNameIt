"""复旦教务（fdjwgl）课表直连：内嵌浏览器登录 → 抓取课表 JSON → 写入日程。

为什么不直接用账号密码 POST 登录？
    复旦统一身份认证（id.fudan.edu.cn）的登录过程要取 JS 公钥、把口令加密后
    再提交，并且会随学校改版而变化。这里改成「内嵌浏览器里你自己登录」，
    登录态由浏览器持有，再用同源 fetch 读取课表接口，稳定且不需要保存你的密码。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

COURSE_TABLE_URL = "https://fdjwgl.fudan.edu.cn/student/for-std/course-table"
PRINT_DATA_TPL = (
    "https://fdjwgl.fudan.edu.cn/student/for-std/course-table/semester/{sem}/print-data"
)

# 复旦常见的作息时间（第 n 节 -> 起止），可在设置里改
DEFAULT_UNIT_TIMES = {
    1: ("08:00", "08:45"),
    2: ("08:55", "09:40"),
    3: ("09:55", "10:40"),
    4: ("10:50", "11:35"),
    5: ("11:45", "12:30"),
    6: ("13:30", "14:15"),
    7: ("14:25", "15:10"),
    8: ("15:25", "16:10"),
    9: ("16:20", "17:05"),
    10: ("17:15", "18:00"),
    11: ("18:30", "19:15"),
    12: ("19:25", "20:10"),
    13: ("20:20", "21:05"),
    14: ("21:15", "22:00"),
}


class FduError(Exception):
    """带中文提示的抓取错误。"""


# ------------------------------------------------------------------ 学期
def parse_semesters(html: str) -> List[Dict[str, str]]:
    """从选课/课表页面里解析可选学期（value=学期 id）。"""
    found: Dict[str, str] = {}
    # <option value="123" ...>2026-2027 第一学期</option>
    for match in re.finditer(
        r'<option[^>]*value="(\d+)"[^>]*>([^<]{2,60})</option>', html or ""
    ):
        value, text = match.group(1), match.group(2).strip()
        if value and text:
            found.setdefault(value, text)
    return [{"id": key, "name": name} for key, name in found.items()]


def parse_semester_from_url(url: str) -> str:
    match = re.search(r"semester/(\d+)", url or "")
    return match.group(1) if match else ""


# ------------------------------------------------------------------ 课表 JSON
_ACTIVITY_HINTS = ("weekday", "startUnit", "endUnit", "weekIndexes")


def _walk(node: Any, out: List[Dict[str, Any]]) -> None:
    if isinstance(node, dict):
        if any(key in node for key in _ACTIVITY_HINTS) and not isinstance(
            node.get("weekIndexes"), (dict, str)
        ):
            out.append(node)
        for value in node.values():
            _walk(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk(item, out)


def _first(node: Dict[str, Any], keys: List[str], default: Any = "") -> Any:
    for key in keys:
        if key in node and node[key] not in (None, ""):
            return node[key]
    return default


def _as_int_list(value: Any) -> List[int]:
    if isinstance(value, list):
        result = []
        for item in value:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                continue
        return result
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        numbers = re.findall(r"\d+", value)
        return [int(item) for item in numbers]
    return []


def parse_print_data(payload: Any) -> List[Dict[str, Any]]:
    """把 print-data 的 JSON（str 或已解析对象）整理成课程片段列表。"""
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise FduError(f"课表数据不是合法 JSON：{exc}") from exc
    else:
        data = payload

    nodes: List[Dict[str, Any]] = []
    _walk(data, nodes)

    activities: List[Dict[str, Any]] = []
    seen = set()
    for node in nodes:
        weekday = _first(node, ["weekday", "dayOfWeek", "weekDay", "weekdayIndex"], None)
        try:
            weekday = int(weekday)
        except (TypeError, ValueError):
            continue
        start_unit = _first(node, ["startUnit", "startUnitIndex", "start"], None)
        end_unit = _first(node, ["endUnit", "endUnitIndex", "end"], None)
        try:
            start_unit = int(start_unit or 0)
            end_unit = int(end_unit or start_unit)
        except (TypeError, ValueError):
            continue
        weeks = _as_int_list(_first(node, ["weekIndexes", "weekIndices", "weeks"], []))
        title = str(_first(node, ["courseName", "name", "lessonName"], "")).strip()
        if not title:
            continue
        room = str(_first(node, ["room", "place", "classroom", "lessonPlace"], "")).strip()
        teachers = _first(node, ["teachers", "teacherName", "teacher"], "")
        if isinstance(teachers, list):
            teachers = "、".join(str(item) for item in teachers)
        key = (title, weekday, start_unit, end_unit, tuple(sorted(weeks)))
        if key in seen:
            continue
        seen.add(key)
        activities.append({
            "title": title,
            "weekday": weekday,
            "start_unit": start_unit,
            "end_unit": end_unit,
            "weeks": weeks or list(range(1, 17)),
            "room": room,
            "teachers": str(teachers or ""),
        })

    if not activities:
        raise FduError(
            "没有在返回的数据里找到课程（可能还没登录成功，或教务改版了）。"
            "可以先用「导入 .ics」作为备用方案。"
        )
    return activities


# ------------------------------------------------------------------ 展开成日程
def _unit_range(start_unit: int, end_unit: int,
                unit_times: Optional[Dict[int, tuple]] = None) -> tuple:
    times = unit_times or DEFAULT_UNIT_TIMES
    start = times.get(start_unit)
    end = times.get(end_unit) or times.get(start_unit)
    if not start or not end:
        return "08:00", "09:40"
    return start[0], end[1]


def parse_unit_times(payload: Any) -> Dict[int, tuple]:
    """从课表 JSON 的 timeTableLayout 里读出每节课的起止时间。

    教务返回的是 800 / 845 这种 HHMM 整数，这里转成 ("08:00", "08:45")。
    """
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    else:
        data = payload
    times: Dict[int, tuple] = {}

    def _hhmm(value: Any) -> str:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return ""
        if number <= 0:
            return ""
        hour, minute = divmod(number, 100)
        return f"{hour:02d}:{minute:02d}"

    def _walk_units(node: Any) -> None:
        if isinstance(node, dict):
            units = node.get("courseUnitList")
            if isinstance(units, list):
                for unit in units:
                    if not isinstance(unit, dict):
                        continue
                    try:
                        index = int(unit.get("indexNo") or 0)
                    except (TypeError, ValueError):
                        continue
                    start = _hhmm(unit.get("startTime"))
                    end = _hhmm(unit.get("endTime"))
                    if index and start and end:
                        times.setdefault(index, (start, end))
            for value in node.values():
                _walk_units(value)
        elif isinstance(node, list):
            for item in node:
                _walk_units(item)

    _walk_units(data)
    return times


def first_monday_from(first_day: date) -> date:
    """把教务的「学年起始日期」换算成第 1 周周一。

    复旦课表按周日→周六排，页面上的日期是第 1 周的周日，所以要往后找到最近的周一。
    """
    return first_day + timedelta(days=(7 - first_day.weekday()) % 7)


def to_rows(activities: List[Dict[str, Any]], first_monday: date,
            unit_times: Optional[Dict[int, tuple]] = None) -> List[Dict[str, Any]]:
    """把课程片段展开成逐条日程（第 1 周的周一作为起点）。"""
    rows: List[Dict[str, Any]] = []
    for activity in activities:
        start, end = _unit_range(activity["start_unit"], activity["end_unit"], unit_times)
        for week in activity["weeks"]:
            if week < 1:
                continue
            day = first_monday + timedelta(days=(week - 1) * 7 + (activity["weekday"] - 1))
            description = []
            if activity.get("teachers"):
                description.append(f"教师：{activity['teachers']}")
            if activity.get("room"):
                description.append(f"地点：{activity['room']}")
            rows.append({
                "title": activity["title"],
                "date": day.strftime("%Y-%m-%d"),
                "start": start,
                "end": end,
                "description": "\n".join(description),
            })
    rows.sort(key=lambda item: (item["date"], item["start"], item["title"]))
    return rows


def import_rows(store, rows: List[Dict[str, Any]], color: str = "sky",
                remind_minutes: Optional[int] = 10,
                group_id: Optional[int] = None) -> tuple:
    """写入日程（与 ics 导入共用去重逻辑）。"""
    from . import timetable

    return timetable.import_rows(store, rows, color=color,
                                 remind_minutes=remind_minutes, group_id=group_id)


COURSE_GROUP_NAME = "课程表"


def ensure_course_group(store, color: str = "sky") -> Optional[int]:
    """找到（或新建）「课程表」分组，返回它的 id。"""
    try:
        for group in store.list_groups():
            if str(group.get("name", "")).strip() == COURSE_GROUP_NAME:
                return int(group["id"])
        return int(store.add_group(COURSE_GROUP_NAME, color=color))
    except Exception:
        return None


# ------------------------------------------------------------------ 内嵌浏览器
# 抓取分两步：① 内嵌浏览器里你自己登录（登录态持久化在 data/browser）；
# ② 把浏览器的 cookie 交给 requests，直接 GET print-data 接口。
# 早先版本用 runJavaScript 里的 async fetch，但 Qt 的 runJavaScript 不会等待 Promise，
# 返回的是空字符串，于是永远「抓取失败」——现在改走 Python 侧请求，稳得多。
HOST = "fdjwgl.fudan.edu.cn"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def browser_dir(sub: str = "fdu"):
    """浏览器存储目录；每个站点用独立子目录，避免 Chromium 锁冲突。"""
    from .config import DATA_DIR

    path = DATA_DIR / "browser" / sub
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_cookies(view, wait_ms: int = 1200) -> dict:
    """读取这个浏览器窗口当前持有的 cookie。

    loadAllCookies() 是异步的：Chromium 的网络进程把 cookie 一条条回吐给
    QWebEngineCookieStore。早先版本用「循环 processEvents」等待，没有事件时
    一次循环只有几微秒，等于没等 → 永远读到 0 个 cookie。这里改用 QEventLoop
    真等一段时间。
    """
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    jar: dict = {}
    store = view.page().profile().cookieStore()

    def _add(cookie) -> None:
        try:
            jar[bytes(cookie.name()).decode("utf-8", "ignore")] = bytes(
                cookie.value()).decode("utf-8", "ignore")
        except Exception:
            pass

    store.cookieAdded.connect(_add)
    store.loadAllCookies()
    loop = QEventLoop()
    QTimer.singleShot(wait_ms, loop.quit)
    loop.exec()
    try:
        store.cookieAdded.disconnect(_add)
    except (RuntimeError, TypeError):
        pass
    if not jar:
        # Qt 的 cookieStore 偶发读不到（异步没回吐完），直接读数据库兜底
        jar = read_cookies_from_disk(HOST)
        if not jar:
            all_cookies = read_cookies_from_disk("")
            print(f"[fdu] 数据库里共有 {len(all_cookies)} 条 cookie，"
                  f"名称={sorted(all_cookies)[:12]}", flush=True)
    return jar


def _dpapi_decrypt(blob: bytes) -> str:
    """Chromium 旧式 cookie 加密（v10）：DPAPI 解出后用 NUL 去掉填充。"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def make_blob(data: bytes) -> DATA_BLOB:
        buffer = ctypes.create_string_buffer(data, len(data))
        return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    data_in = make_blob(blob[3:])
    data_out = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(data_in), None, None, None, None, 0, ctypes.byref(data_out)
    )
    if not ok:
        return ""
    try:
        raw = ctypes.string_at(data_out.pbData, data_out.cbData)
        return raw.decode("utf-8", "ignore").replace(chr(0), "")
    finally:
        kernel32.LocalFree(data_out.pbData)


def read_cookies_from_disk(host: str = "") -> dict:
    """直接从 Chromium 的 Cookies 数据库读（Qt 的 cookieStore 有时读不到）。

    QtWebEngine 写进去的值多数是明文，少数是老式 v10（DPAPI 加密）。
    """
    import sqlite3

    path = browser_dir() / "Cookies"
    if not path.exists():
        return {}
    jar: dict = {}
    try:
        conn = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
        rows = conn.execute(
            "select host_key, name, value, encrypted_value from cookies"
        ).fetchall()
        conn.close()
    except Exception:
        return {}
    for host_key, name, value, encrypted in rows:
        # 域 cookie 存成 .fudan.edu.cn，按完整主机名过滤会漏掉
        key = (host_key or "").lstrip(".")
        if host and key and not (host == key or host.endswith("." + key)):
            continue
        text = value
        if not text and encrypted:
            blob = bytes(encrypted)
            if blob[:3] == b"v10":
                text = _dpapi_decrypt(blob)
        if text:
            jar[str(name)] = str(text)
    return jar


_PAGE_INFO_JS = r"""
(function () {
  var selects = [];
  var nodes = document.querySelectorAll('select');
  for (var i = 0; i < nodes.length; i++) {
    var s = nodes[i];
    var text = '';
    if (s.selectedIndex >= 0 && s.options[s.selectedIndex]) {
      text = s.options[s.selectedIndex].text || '';
    }
    if (!text) { text = (s.innerText || '').replace(/\s+/g, ' ').trim(); }
    selects.push({value: String(s.value || ''), text: text});
  }
  var body = (document.body && document.body.innerText) || '';
  var term = (body.match(/学年起始日期[:：]?\s*(\d{4}-\d{1,2}-\d{1,2})/) || [])[1] || '';
  return JSON.stringify({selects: selects, termStart: term});
})()
"""


def parse_page_info(raw: Any) -> Dict[str, Any]:
    """解析 _PAGE_INFO_JS 的结果：{semester, semester_label, term_start}。"""
    info: Dict[str, Any] = {"semester": "", "semester_label": "", "term_start": ""}
    try:
        data = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        return info
    if not isinstance(data, dict):
        return info
    info["term_start"] = str(data.get("termStart") or "")
    for item in data.get("selects") or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "")
        text = str(item.get("text") or "")
        if not value.isdigit():
            continue
        if re.search(r"\d{4}\s*-\s*\d{4}", text) or "学期" in text:
            info["semester"] = value
            info["semester_label"] = text
            break
    return info


def semester_from_urls(urls) -> str:
    """从浏览器发过的请求里找学期号（SPA 不改地址栏，但请求里一定有）。"""
    for url in reversed(list(urls or [])):
        match = re.search(r"semester/(\d+)", str(url))
        if match:
            return match.group(1)
    return ""


def fetch_print_data(semester_hint: str, cookies: dict, seen_urls=None) -> tuple:
    """用浏览器里的 cookie 请求课表；返回 (semester, print-data 的 JSON 文本)。"""
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": _UA, "Referer": COURSE_TABLE_URL})
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=HOST, path="/")

    semester = semester_hint or semester_from_urls(seen_urls)
    if not semester:
        resp = session.get(COURSE_TABLE_URL, timeout=25)
        options = re.findall(
            r'<option[^>]*value="(\d+)"[^>]*>([^<]{1,60})</option>', resp.text
        )
        if options:
            semester = options[0][0]
    if not semester:
        tail = list(seen_urls or [])[-8:]
        raise FduError(
            "没有拿到学期编号：请先在窗口里点开「我的课表」页面，再点抓取课表。"
            + ("（已记录请求：" + " | ".join(str(item) for item in tail) + "）"
               if tail else "")
        )

    resp = session.get(PRINT_DATA_TPL.format(sem=semester), timeout=25)
    if resp.status_code != 200:
        raise FduError(f"课表接口返回 HTTP {resp.status_code}（登录可能已过期）")
    text = resp.text.strip()
    if not text or text[:1] not in "[{":
        raise FduError("课表接口返回的不是 JSON，可能需要重新登录（被重定向到登录页）")
    return semester, text


def _parse_semester_from_url(url: str) -> str:
    return parse_semester_from_url(url)


_TERM_START_JS = (
    "(function(){"
    "var body=(document.body&&document.body.innerText)||'';"
    "var m=body.match(/学年起始日期[:：]?\s*(\\d{4}-\\d{1,2}-\\d{1,2})/);"
    "return m?m[1]:'';"
    "})()"
)


def _js_fetch(url: str) -> str:
    """生成一段同步 JS：发起 fetch 并把结果放进 window.__luminaFdu。"""
    return (
        "(function(){"
        "window.__luminaFdu={done:false};"
        "fetch(" + json.dumps(url) + ",{credentials:'include'})"
        ".then(function(r){window.__luminaFdu.status=r.status;return r.text();})"
        ".then(function(t){window.__luminaFdu.done=true;window.__luminaFdu.ok=true;"
        "window.__luminaFdu.data=t;})"
        ".catch(function(e){window.__luminaFdu.done=true;window.__luminaFdu.ok=false;"
        "window.__luminaFdu.err=String(e);});"
        "return 'started';})()"
    )


def make_browser_dialog(parent=None, on_data: Optional[Callable[[str, str], None]] = None,
                        on_status: Optional[Callable[[str, str], None]] = None):
    """内嵌浏览器登录 + 抓取课表；成功后回调 (semester, json)。

    抓取流程：
      1. 监听页面请求，嗅探出真实学期号（SPA 不改地址栏，但请求里有 semester/527）；
      2. 让页面自己 fetch 同源的 print-data（带登录态），结果存到 window 全局；
      3. 主线程每 250ms 用 runJavaScript 读一次全局变量，拿到 JSON 就收工；
      4. 若页面 fetch 失败，再用 requests + 浏览器 cookie 兜底试一次。
    """
    import json as _json

    from PySide6.QtCore import QObject, QTimer, QUrl, Signal
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

    try:
        from PySide6.QtWebEngineCore import (
            QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor,
        )
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception as exc:  # noqa: BLE001
        raise FduError(f"当前环境缺少 QtWebEngine，无法打开登录窗口：{exc}") from exc

    from . import theme
    from .widgets import SoftButton

    dialog = QDialog(parent)
    dialog.setWindowTitle("登录复旦教务并抓取课表")
    dialog.resize(1080, 800)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(8)

    tip = QLabel(
        "① 在下面用统一身份认证登录；② 打开「我的课表」；③ 点右下角「抓取课表」。"
        "登录态保存在程序自己的 data/browser 目录里，程序不会保存你的密码。"
    )
    tip.setWordWrap(True)
    tip.setObjectName("Small")
    layout.addWidget(tip)

    class _RequestSpy(QWebEngineUrlRequestInterceptor):
        """记录页面发过的请求，用来嗅探真实学期号。"""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.urls: List[str] = []

        def interceptRequest(self, info) -> None:  # noqa: N802
            try:
                url = info.requestUrl().toString()
            except Exception:
                return
            self.urls.append(url)
            if len(self.urls) > 800:
                del self.urls[:400]

    profile = QWebEngineProfile("lumina-fdu", dialog)
    profile.setPersistentStoragePath(str(browser_dir()))
    profile.setCachePath(str(browser_dir() / "cache"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    spy = _RequestSpy(dialog)
    profile.setUrlRequestInterceptor(spy)

    view = QWebEngineView(dialog)
    page = QWebEnginePage(profile, view)
    view.setPage(page)
    view.setUrl(QUrl(COURSE_TABLE_URL))
    dialog.finished.connect(lambda _code=0: page.deleteLater())
    layout.addWidget(view, 1)

    status = QLabel("等待登录…")
    status.setObjectName("Tiny")
    layout.addWidget(status)

    class _Bridge(QObject):
        done = Signal(bool, str, str)   # ok, semester, payload_or_error

    bridge = _Bridge(dialog)
    state = {"semester": "", "term_start": "", "tries": 0, "settled": False}

    def _emit_status(text: str, tone: str = "info") -> None:
        status.setText(text)
        if on_status is not None:
            try:
                on_status(text, tone)
            except Exception:
                pass

    # ------------------------------------------------ 轮询页面里的结果
    poll = QTimer(dialog)
    poll.setInterval(250)

    def _stop_poll() -> None:
        if poll.isActive():
            poll.stop()

    def _settle(ok: bool, payload: str) -> None:
        if state["settled"]:
            return
        state["settled"] = True
        _stop_poll()
        grab_button.setEnabled(True)
        bridge.done.emit(ok, state["semester"], payload)

    def _on_polled(raw) -> None:
        state["tries"] += 1
        try:
            data = _json.loads(raw) if isinstance(raw, str) else None
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict) and data.get("done"):
            text = str(data.get("data") or "")
            if data.get("ok") and text[:1] in "[{":
                print(f"[fdu] 页面 fetch 成功，状态={data.get('status')} "
                      f"长度={len(text)}", flush=True)
                _settle(True, text)
                return
            print(f"[fdu] 页面 fetch 未拿到 JSON：状态={data.get('status')} "
                  f"错误={data.get('err')} 前 120 字={text[:120]!r}", flush=True)
            _fallback_by_requests()
            return
        if state["tries"] > 80:      # 20 秒还没结果
            print("[fdu] 页面 fetch 超时", flush=True)
            _fallback_by_requests()

    poll.timeout.connect(lambda: view.page().runJavaScript(
        "JSON.stringify(window.__luminaFdu||null)", _on_polled))

    # ------------------------------------------------ 兜底：requests + cookie
    def _fallback_by_requests() -> None:
        if state["settled"]:
            return
        cookies = read_cookies(view, wait_ms=800)
        print(f"[fdu] 兜底 requests：cookie={len(cookies)} "
              f"semester={state['semester']!r}", flush=True)
        if not cookies or not state["semester"]:
            _settle(False, "抓取失败：页面请求没有返回课表，也没拿到登录凭据。"
                           "请确认已经进入「我的课表」页面后再点抓取。")
            return

        def worker() -> None:
            try:
                sem, text = fetch_print_data(state["semester"], cookies, spy.urls)
                _settle(True, text)
            except FduError as exc:
                _settle(False, str(exc))
            except Exception as exc:  # noqa: BLE001
                _settle(False, f"抓取失败：{exc}")

        import threading

        threading.Thread(target=worker, name="lumina-fdu-fetch", daemon=True).start()

    # ------------------------------------------------ 点击抓取
    def _grab() -> None:
        state["settled"] = False
        state["tries"] = 0
        semester = semester_from_urls(spy.urls)
        state["semester"] = semester
        print(f"[fdu] 点击抓取：semester={semester!r} 已记录请求={len(spy.urls)}", flush=True)
        if not semester:
            _emit_status("还没检测到课表请求：请先在窗口里点开「我的课表」，再点抓取", "error")
            return
        _emit_status("正在读取课表…")
        grab_button.setEnabled(False)

        def _on_term(raw) -> None:
            text = str(raw or "").strip()
            if text:
                state["term_start"] = text
                print(f"[fdu] 学年起始日期={text}", flush=True)

        # 同步表达式，回调一定会回来；失败也不影响主流程
        view.page().runJavaScript(_TERM_START_JS, _on_term)
        view.page().runJavaScript(_js_fetch(PRINT_DATA_TPL.format(sem=semester)),
                                  lambda _r=None: None)
        poll.start()

    def _on_done(ok: bool, semester: str, payload: str) -> None:
        if not ok:
            _emit_status(payload, "error")
            return
        _emit_status(f"已读取学期 {semester} 的课表", "ok")
        if on_data is not None:
            try:
                on_data(semester, payload, state.get("term_start", ""))
            except TypeError:
                on_data(semester, payload)
        dialog.accept()

    bridge.done.connect(_on_done)

    row = QHBoxLayout()
    row.addStretch(1)
    grab_button = SoftButton("抓取课表", "primary", height=34, min_width=110)
    grab_button.clicked.connect(_grab)
    close_button = SoftButton("关闭", "ghost", height=34, min_width=84)
    close_button.clicked.connect(dialog.reject)
    row.addWidget(grab_button)
    row.addWidget(close_button)
    layout.addLayout(row)

    dialog.setStyleSheet(
        f"QDialog {{ background: {theme.appearance().card_hex()}; }}"
        f"QLabel {{ color: {theme.appearance().accent_deep_q().name()}; }}"
    )
    return dialog
