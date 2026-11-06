# -*- coding: utf-8 -*-
"""ehall 取数桥：借 ehall 网页那套登录态，在后台开一个「看不见的页面」去取数据。

为什么要这么绕：ehall 各服务（校园卡、校车、体育刷锻、图书馆人数、空教室…）都是需要
统一身份认证的页面，接口地址还会随学校版本变化，硬编码没有意义。所以这里做三件事：

1. **地址可配**：每张卡片的数据来源地址存在设置里（``ehall_json_<id>``），用户在
   ehall 里打开对应服务，把地址栏复制过来填一次就行；
2. **同一份登录态**：后台页面挂在 ehall 网页面板同一个 ``QWebEngineProfile`` 上，
   cookie 是共享的 —— ehall 里登录过，这里就不用再登录；
3. **掉了登录态就自动补**：如果跳到了「统一身份认证」登录页，且设置里存过学号密码
   （``设置 → 统一身份认证``），就自动填表 + 点登录，等跳回来再继续取数。
   cookie 本身是持久化的（``ForcePersistentCookies``），所以一次登录能管很久。

另外给「空教室」用：ehall 那边是先选**教学楼**、再选**教室**、点查询才出表格，
所以这里也提供「读选项 / 选教学楼 / 选教室 / 点查询 / 抓表格」的分步流程。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .config import PERIOD_TIMES

CACHE_KEY = "ehall_cache_%s"      # + card id → JSON
CACHE_AT_KEY = "ehall_cache_at_%s"
URL_KEY = "ehall_json_%s"

SETTLE_MS = 2200                  # 页面加载完再等一会儿，让 SPA 把数据渲染出来
TIMEOUT_MS = 25000                # 单个页面最多等这么久
LOGIN_MS = 3200                   # 自动登录后等跳转的时间
AFTER_CLICK_MS = 2600             # 点了「查询」之后等表格刷新的时间

#: 这几张卡片是「交互式」的（要在组件里选条件才查），批量同步时跳过
INTERACTIVE_CARDS = {"empty_room"}

# ---------------------------------------------------------------- 取数脚本
# 通用：把页面正文抓回来（相对稳，不依赖具体 DOM）
GENERIC_JS = """
(function () {
  var t = document.body ? (document.body.innerText || '') : '';
  var tables = [];
  try {
    document.querySelectorAll('table').forEach(function (tb) {
      var rows = [];
      tb.querySelectorAll('tr').forEach(function (tr) {
        var cells = [];
        tr.querySelectorAll('th,td').forEach(function (td) {
          cells.push((td.innerText || '').replace(/\\s+/g, ' ').trim());
        });
        if (cells.length) { rows.push(cells); }
      });
      if (rows.length) { tables.push(rows); }
    });
  } catch (e) {}
  return JSON.stringify({
    u: location.href,
    h: document.title || '',
    t: t.slice(0, 14000),
    tables: tables.slice(0, 8)
  });
})()
"""

# 空教室这类课表页：表格是关键，正文其次
TABLE_JS = GENERIC_JS

# 判断当前页是不是统一身份认证的登录页（有密码框就算）
LOGIN_CHECK_JS = r"""
(function () {
  var pwd = false;
  try {
    if (document.querySelector('input[type="password"]')) { pwd = true; }
    var frames = document.querySelectorAll('iframe, frame');
    for (var i = 0; i < frames.length && !pwd; i++) {
      try {
        if (frames[i].contentDocument &&
            frames[i].contentDocument.querySelector('input[type="password"]')) {
          pwd = true;
        }
      } catch (e) {}
    }
  } catch (e) {}
  return JSON.stringify({login: pwd, url: location.href});
})()
"""

# 读出页面上所有 <select> 的选项（空教室页的「校区 / 教学楼 / 教室」都是下拉）
OPTIONS_JS = r"""
(function () {
  var selects = [];
  try {
    document.querySelectorAll('select').forEach(function (sel) {
      var options = [];
      try {
        for (var i = 0; i < sel.options.length; i++) {
          options.push((sel.options[i].text || '').trim());
        }
      } catch (e) {}
      selects.push({name: (sel.name || sel.id || ''), options: options});
    });
  } catch (e) {}
  var text = document.body ? (document.body.innerText || '').slice(0, 6000) : '';
  return JSON.stringify({u: location.href, selects: selects, t: text});
})()
"""

# 在一个下拉里按文字选中某一项（同时兼容原生 select 和可点击的 div/li）
PICK_JS = r"""
(function () {
  var want = __WANT__;
  function fire(el) {
    try { el.dispatchEvent(new Event('input', {bubbles: true})); } catch (e) {}
    try { el.dispatchEvent(new Event('change', {bubbles: true})); } catch (e) {}
    try { el.dispatchEvent(new Event('blur', {bubbles: true})); } catch (e) {}
  }
  var found = null;
  var selects = document.querySelectorAll('select');
  for (var i = 0; i < selects.length && !found; i++) {
    var sel = selects[i];
    for (var j = 0; j < sel.options.length; j++) {
      var text = (sel.options[j].text || '').trim();
      var value = (sel.options[j].value || '').trim();
      if (text === want || value === want || (want && text.indexOf(want) >= 0)) {
        sel.selectedIndex = j;
        fire(sel);
        found = {select: i, text: text};
        break;
      }
    }
  }
  if (!found) {
    var nodes = document.querySelectorAll('li, a, div[role="option"], span');
    for (var k = 0; k < nodes.length; k++) {
      var label = (nodes[k].innerText || '').replace(/\s+/g, '');
      if (label === want && nodes[k].click) {
        try { nodes[k].click(); found = {select: -1, text: want}; break; } catch (e) {}
      }
    }
  }
  return JSON.stringify(found ? {ok: true, found: found}
                             : {ok: false, reason: '页面上没有「' + want + '」这个选项'});
})()
"""

CLICK_QUERY_JS = r"""
(function () {
  var nodes = document.querySelectorAll(
    'button, a, input[type="submit"], div[role="button"], span');
  for (var i = 0; i < nodes.length; i++) {
    var label = (nodes[i].innerText || nodes[i].value || '').replace(/\s+/g, '');
    if (label === '查询' || label === '搜索' || label === '确定'
        || label === '查看' || label === '查询教室') {
      try { nodes[i].click(); return JSON.stringify({ok: true, label: label}); }
      catch (e) {}
    }
  }
  return JSON.stringify({ok: false, reason: '没找到「查询」按钮'});
})()
"""

# 空教室的结果页：优先按「教室 × 节次」的表格结构抓（行=教室，列=节次/时间），
# 表单那张长文字表会被排除；实在没有表就退回短行文本。
CLASSROOM_JS = r"""
(function () {
  function tidy(text) {
    return (text || '').replace(/\s+/g, ' ').trim();
  }
  function readTable(tb) {
    var rows = [];
    // 只读**本表自己的**行和格：按选择器取 tr 会把嵌套小表（表头里那个
    // 「8:00 - 8:45」）的行也算进来，结果一行教室变成两行、格子里塞的是
    // 教室号自己 —— 用户看到的「H2102A 重复一遍、块里写的还是 H2102A」就是这个。
    try {
      for (var i = 0; i < tb.rows.length; i++) {
        var tr = tb.rows[i];
        var cells = [];
        for (var j = 0; j < tr.cells.length; j++) {
          var td = tr.cells[j];
          var label = tidy(td.innerText).slice(0, 120);
          var title = td.getAttribute('title') || td.getAttribute('data-text') || '';
          cells.push((label || tidy(title)).slice(0, 120));
        }
        if (cells.length) { rows.push(cells); }
      }
    } catch (e) {}
    return rows;
  }
  var candidates = [];
  try {
    document.querySelectorAll('table').forEach(function (tb) {
      var rows = readTable(tb);
      if (rows.length < 2) { return; }
      var width = Math.max.apply(null, rows.map(function (r) { return r.length; }));
      var shortCells = 0, totalCells = 0;
      rows.forEach(function (row) {
        row.forEach(function (cell) {
          totalCells += 1;
          if (cell.length <= 12) { shortCells += 1; }
        });
      });
      var ratio = totalCells ? shortCells / totalCells : 0;
      // 「像教室表格」的行：格子多、第一格是教室号（JB100 / H3101 / 3101）
      var roomRows = rows.filter(function (row) {
        return row.length >= 6 && /^[A-Za-z]{0,3}\s*-?\s*\d{2,4}/.test(row[0] || '');
      }).length;
      var score = ratio * 10 + Math.min(width, 20) + Math.min(rows.length, 40) / 10
                  + roomRows * 6;
      var id = (tb.id || '');
      if (/statusTable_0/i.test(id)) { score += 40; }      // 学校页面：正文表格
      if (/statusTable_1/i.test(id)) { score -= 60; }      // 同一页的吸顶表头，别选它
      candidates.push({rows: rows, width: width, ratio: ratio, roomRows: roomRows,
                       id: id, score: score});
    });
  } catch (e) {}
  candidates.sort(function (a, b) { return b.score - a.score; });
  var best = candidates.length ? candidates[0] : {rows: [], width: 0};
  var rows = best.rows;
  var headers = [];
  if (rows.length && rows[0].length >= 3) {
    var head = rows[0];
    var timeWords = /节|时间|上午|下午|晚上|周一|周二|周三|周四|周五|周末|:|\d/;
    var hits = head.filter(function (cell) { return timeWords.test(cell); }).length;
    if (hits >= Math.max(2, head.length - 1)) {
      headers = head;
      rows = rows.slice(1);
    }
  }
  // 正文兜底：短行
  var lines = [];
  try {
    var text = document.body ? (document.body.innerText || '') : '';
    lines = text.split('\n').map(tidy)
                .filter(function (s) { return s && s.length <= 40; });
  } catch (e) {}
  return JSON.stringify({
    u: location.href,
    headers: headers,
    rows: rows.slice(0, 80),
    lines: lines.slice(0, 200),
    tables: candidates.length
  });
})()
"""

#: 这些是页面上的「框架词」，不是教室数据，清洗时要丢掉
CHROME_WORDS = ("请选择", "全部校区", "全部教学楼", "全部教室", "关键词", "查询",
                "教学楼", "教室", "节次", "校区", "空闲教室", "在线教学", "借用",
                "多个关键词", "空格分隔", "楼", "请先", "重置", "打印", "导出",
                "上一页", "下一页", "返回")


def pick_js(value: str) -> str:
    """生成「按文字选某个下拉项」的脚本（中文原样写进去，方便排查）。"""
    return PICK_JS.replace("__WANT__", json.dumps(str(value or ""),
                                                  ensure_ascii=False))


# ---------------------------------------------------------------- 解析用正则
_MONEY = re.compile(r"(?:余额|可用余额|balance)[^0-9\-]{0,12}(-?[0-9][0-9,]*(?:\.[0-9]+)?)",
                    re.IGNORECASE)
_PEOPLE = re.compile(r"(?:在馆|人数|当前在馆)[^0-9]{0,8}([0-9]{1,6})")
_RATIO = re.compile(r"([0-9]{1,6})\s*/\s*([0-9]{1,6})")
_TIMES = re.compile(r"([0-9]{1,2})\s*[:：]\s*([0-9]{2})")
_COUNT_CI = re.compile(r"([0-9]{1,4})\s*次")
_LEADNUM = re.compile(r"^\s*[0-9]+[\.、,)）]\s*")


def _lines(text: str, limit: int = 400) -> List[str]:
    out: List[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or len(line) > 120:
            continue
        if line in out:
            continue
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _money(text: str) -> Optional[str]:
    found = _MONEY.search(text)
    if not found:
        return None
    value = found.group(1).replace(",", "")
    try:
        return "%s 元" % ("%.2f" % float(value))
    except Exception:
        return found.group(1)


def _people(text: str) -> Optional[str]:
    found = _PEOPLE.search(text)
    if found:
        return "%s 人" % found.group(1)
    found = _RATIO.search(text)
    if found:
        return "%s / %s 人" % (found.group(1), found.group(2))
    return None


def _hit_lines(lines: List[str], words: List[str], need: int = 6) -> List[str]:
    picked = [line for line in lines if any(word in line for word in words)]
    return picked[:need]


def parse_card(card_id: str, raw: str, store=None) -> Dict[str, Any]:
    """把一次抓取的原始文本解析成卡片要显示的内容。永远返回 dict，不抛异常。"""
    payload: Dict[str, Any] = {"ok": False, "value": "", "lines": [], "rows": [],
                               "url": "", "text": "", "error": "", "ts": time.time()}
    data: Dict[str, Any] = {}
    text = ""
    try:
        parsed = json.loads(raw) if str(raw or "").strip().startswith("{") else None
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        data = parsed
        text = str(data.get("t") or "")
        payload["url"] = str(data.get("u") or "")
        payload["text"] = text[:4000]
        payload["rows"] = _flatten_tables(data.get("tables") or [])
    else:
        text = str(raw or "")
        payload["text"] = text[:4000]
    lines = _lines(text)
    payload["raw_lines"] = lines[:120]

    try:
        if card_id == "card":
            value = _money(text)
            if value:
                payload.update(ok=True, value="余额 %s" % value,
                               lines=_hit_lines(lines, ["消费", "交易", "余额"], 4))
            else:
                payload["error"] = "页面里没找到余额（可能没登录，或这个地址不是一卡通页面）"
        elif card_id == "bus":
            picked = _bus_lines(lines, store)
            if picked:
                payload.update(ok=True, lines=picked)
                payload["value"] = " / ".join(picked[:2])
            else:
                payload["error"] = "页面里没找到班次时间（在设置里填校车页面的地址试试）"
        elif card_id == "sports":
            counts = _COUNT_CI.findall(text)
            if counts:
                value = "%s 次" % counts[0]
                payload.update(ok=True, value=value,
                               lines=_hit_lines(lines, ["刷锻", "锻炼", "达标"], 4))
            else:
                payload["error"] = "页面里没找到刷锻次数"
        elif card_id == "library_people":
            value = _people(text)
            if value:
                payload.update(ok=True, value=value,
                               lines=_hit_lines(lines, ["在馆", "人数", "座位"], 5))
            else:
                payload["error"] = "页面里没找到在馆人数"
        elif card_id == "ehall_todo":
            payload.setdefault("todo", [])
            payload.setdefault("doing", [])
            payload.setdefault("done", [])
            block = _todo_block(lines)
            if block or _rows_look_like_todo(payload["rows"]):
                todo, doing, done = _split_todo(block, payload["rows"])
                payload["todo"] = todo
                payload["doing"] = doing
                payload["done"] = done
                total = len(todo) + len(doing)
                if total or done:
                    payload.update(ok=True,
                                   value="%d 条待处理" % total if total else "没有待处理")
                    payload["lines"] = (todo + doing + done)[:10]
                else:
                    payload["error"] = "「我的待办」里现在是空的"
            else:
                payload["error"] = _todo_error_hint(text)
        elif card_id == "empty_room":
            rows = payload["rows"]
            if rows:
                payload.update(ok=True, value="%d 行教室数据" % len(rows),
                               rows=rows[:60])
            else:
                payload["error"] = "页面里没找到教室表格（填「空闲教室查询」页面的地址试试）"
        else:
            payload.update(ok=bool(lines), value=lines[0] if lines else "",
                           lines=lines[:8])
    except Exception as exc:  # noqa: BLE001
        payload["error"] = "解析失败：%s" % str(exc)[:80]
    return payload


def _flatten_tables(tables: Any) -> List[List[str]]:
    rows: List[List[str]] = []
    try:
        for table in tables or []:
            for row in table or []:
                cells = [str(cell).strip() for cell in (row or [])]
                if cells and any(cells):
                    rows.append(cells)
    except Exception:
        return []
    return rows[:200]


def _bus_lines(lines: List[str], store=None) -> List[str]:
    """校车：设了方向就只显示这两趟，把方向名和旁边那行时间拼成一条。"""
    directions: List[str] = []
    try:
        from . import dashboard

        directions = dashboard.bus_directions(store) if store is not None else []
    except Exception:
        directions = []
    picked: List[str] = []
    if directions:
        for name in directions:
            for index, line in enumerate(lines):
                if name not in line:
                    continue
                if _TIMES.search(line):
                    picked.append(line)
                else:
                    after = lines[index + 1] if index + 1 < len(lines) else ""
                    picked.append("%s · %s" % (line, after) if after and
                                  _TIMES.search(after) else line)
                break
        return picked[:2]
    for line in lines:
        if _TIMES.search(line) and len(line) <= 60:
            picked.append(line)
        if len(picked) >= 6:
            break
    return picked


DONE_WORDS = ("已完成", "已办", "已处理", "已结束", "已通过")
DOING_WORDS = ("进行中", "办理中", "处理中", "待审核")

TODO_HEADS = ("我的待办", "待办事项", "待办任务", "我的任务")
TODO_ENDS = ("我的已办", "我的申请", "我的知会", "常用服务", "全部待办",
             "查看更多", "查看全部", "任务中心")


def _looks_like_item(line: str) -> bool:
    if len(line) < 4 or len(line) > 60:
        return False
    if _TIMES.fullmatch(line.replace(" ", "")):
        return False
    if re.fullmatch(r"[\d\s\-/年月日:\.]+", line):
        return False
    return True


def _todo_block(lines: List[str]) -> List[str]:
    """只取「我的待办」那一段的正文。

    ehall 首页整页都是服务名，直接拿正文当待办会把「数据中心」「IT服务」也算进去，
    所以必须先找到「我的待办」这个标题，再取到下一个分区标题为止。
    """
    start = None
    for index, line in enumerate(lines):
        if any(head in line for head in TODO_HEADS):
            start = index
            break
    if start is None:
        return []
    block: List[str] = []
    for line in lines[start + 1:]:
        if any(word in line for word in TODO_ENDS):
            break
        if any(head in line for head in TODO_HEADS):
            continue
        block.append(line)
        if len(block) >= 40:
            break
    return block


def _rows_look_like_todo(rows: List[List[str]]) -> bool:
    """表格里带「待办 / 进行中 / 已完成」这种状态词的，才当待办表。"""
    for row in rows or []:
        joined = " ".join(str(cell) for cell in (row or []))
        if any(word in joined for word in
               ("待办", "进行中", "已完成", "已办", "待审核", "处理中")):
            return True
    return False


def _todo_error_hint(text: str) -> str:
    """没找到待办时说清楚是「没登录」还是「地址不对」。"""
    head = str(text or "")[:400]
    if "统一身份认证" in head or "登录" in head:
        return "还没登录 ehall：先去「eHall」页登录一次，再回来同步"
    return "这个页面里没有「我的待办」区块，检查一下地址是不是 ehall 首页"


def _split_todo(lines: List[str], rows: List[List[str]]):
    """把「我的待办」切成 待办 / 进行中 / 已完成 三堆（启发式）。"""
    todo: List[str] = []
    doing: List[str] = []
    done: List[str] = []
    skip_words = ("我的待办", "我的已办", "我的申请", "我的知会", "任务中心",
                  "查看更多", "暂无", "服务类别", "首页", "办事大厅")
    entries: List[tuple] = []
    for row in rows or []:
        cells = [str(cell).strip() for cell in (row or [])]
        if not cells:
            continue
        title = next((cell for cell in cells if _looks_like_item(cell)), "")
        if not title:
            continue
        entries.append((title, " ".join(cells)))
    if not entries:
        entries = [(line, line) for line in lines if _looks_like_item(line)]
    for title, whole in entries:
        if any(word in title for word in skip_words):
            continue
        if any(word in whole for word in DONE_WORDS):
            done.append(title)
        elif any(word in whole for word in DOING_WORDS):
            doing.append(title)
        else:
            todo.append(title)
    # 待办最多 8 条，已完成只要最近 3 条
    return todo[:8], doing[:8], done[-3:]


def is_chrome_text(text: str) -> bool:
    """页面上的「框架词」不是教室数据。"""
    line = str(text or "").strip()
    if not line:
        return True
    if any(word in line for word in CHROME_WORDS):
        return True
    # 一长串楼名/选项（空格或顿号分隔）当成表单内容
    if line.count(" ") >= 6 and len(line) > 30:
        return True
    return False


def clean_room_rows(rows: Any) -> List[List[str]]:
    """表格行 → 去框架词、只留短单元格的行。

    页面把整块文字塞进一个单元格的情况很常见（用户截图里那团乱码就是），
    所以：**先按原文判断**，长于 24 字的单元格直接丢掉（教室号/节次/状态都不可能那么长），
    再去掉框架词，最后整行拼起来超过 60 字的也不要。
    """
    cleaned: List[List[str]] = []
    for row in rows or []:
        keep: List[str] = []
        for cell in (row or []):
            text = str(cell or "").strip()
            if not text or len(text) > 24:
                continue
            if is_chrome_text(text):
                continue
            keep.append(text)
        if not keep:
            continue
        if len("".join(keep)) > 60:
            continue
        cleaned.append(keep[:6])
    seen = set()
    unique: List[List[str]] = []
    for row in cleaned:
        key = "|".join(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def clean_room_lines(lines: Any) -> List[List[str]]:
    """正文兜底：短行按「教室/节次/状态」拆开。"""
    out: List[List[str]] = []
    for raw in lines or []:
        line = str(raw).strip()
        if is_chrome_text(line):
            continue
        if len(line) > 40:
            continue
        parts = [part.strip() for part in re.split(r"[|｜\t]|\s{2,}", line)
                 if part.strip()]
        out.append((parts or [line])[:6])
        if len(out) >= 40:
            break
    return out


FREE_WORDS = ("空闲", "空", "可用", "无课", "无", "-", "—", "/", "空教室")


def _cell_occupied(text: str) -> bool:
    value = str(text or "").strip()
    if not value or value in FREE_WORDS:
        return False
    if is_chrome_text(value):
        return False
    return True


# 学校页面一个格子的原文长这样（多门课会挤在同一格里）：
#   「本 理学 MATH10031.17 [线性代数A](司然){130人}」
#   「调 本 理学 MATH10009.01 [高等代数Ⅰ](谢启鸿){69人} 本 理学 …{72人}」
_COURSE_NAME_RE = re.compile(r"\[([^\]]{1,40})\]\s*[（(]([^）)]{0,30})[）)]")
_COURSE_CODE_RE = re.compile(r"[A-Z]{2,6}\s*\d{3,}[.\-]\d{1,3}")
_STUDENTS_RE = re.compile(r"\{\s*(\d{1,4})\s*人\s*\}")
_LEVEL_WORDS = ("本", "研", "博", "留", "进", "继", "调", "停")


def parse_course_cell(text: str) -> Dict[str, Any]:
    """把「本 理学 MATH10031.17 [线性代数A](司然){130人}」拆成课程 / 教师 / 代码。

    一个格子里可能同时排两门课（合班/换教室），所以课程名和老师都用「、」连起来。
    """
    raw = " ".join(str(text or "").split())
    out: Dict[str, Any] = {"raw": raw, "label": raw, "course": "", "teacher": "",
                           "code": "", "students": "", "level": "", "kind": ""}
    if not raw:
        return out
    names: List[str] = []
    teachers: List[str] = []
    for match in _COURSE_NAME_RE.finditer(raw):
        name = match.group(1).strip()
        teacher = match.group(2).strip()
        if name:
            names.append(name)
        if teacher and teacher not in teachers:
            teachers.append(teacher)
    codes = [code.replace(" ", "") for code in _COURSE_CODE_RE.findall(raw)]
    students = _STUDENTS_RE.findall(raw)
    out["course"] = "、".join(dict.fromkeys(names))
    out["teacher"] = "、".join(teachers)
    out["code"] = "、".join(dict.fromkeys(codes))
    out["students"] = "、".join(dict.fromkeys(
        "%s 人" % count for count in students))
    tokens = [token for token in raw.split() if token]
    level = ""
    while tokens and tokens[0] in _LEVEL_WORDS:
        word = tokens.pop(0)
        if word in ("本", "研", "博", "留", "进", "继") and not level:
            level = word
    out["level"] = level
    if tokens and not _COURSE_CODE_RE.match(tokens[0]) and tokens[0] not in _LEVEL_WORDS:
        out["kind"] = tokens[0]
    if out["course"]:
        out["label"] = out["course"]          # 时间轴上写课程名，不写课程代码
    elif codes:
        out["label"] = codes[0]
    return out


_TIME_RANGE_RE = re.compile(r"^\s*(\d{1,2})\s*[:：]\s*(\d{2})")
_ANY_TIME_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")
_PERIOD_RE = re.compile(r"^\s*(?:第\s*)?(\d{1,2})\s*(?:大)?\s*节\s*$")
_BARE_NUM_RE = re.compile(r"^\s*(\d{1,2})\s*$")
_CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
               "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13,
               "十四": 14}


def looks_like_time(text: str) -> bool:
    """「08:00-08:45」「1」「第 2 大节」「上午」「一 8:00 - 8:45」这种：时间轴的标签。"""
    value = str(text or "").strip()
    if not value:
        return False
    if _TIME_RANGE_RE.match(value) or _ANY_TIME_RE.search(value):
        return True
    if _PERIOD_RE.match(value) or _BARE_NUM_RE.match(value):
        return True
    return any(word in value for word in ("上午", "下午", "晚上", "节次", "时间"))


def looks_like_room(text: str) -> bool:
    """「H3101」「JB100」「光华西301」这种：教室名（学校页面上是 JB100 这种）。"""
    value = str(text or "").strip()
    if not value or len(value) > 12:
        return False
    if value in ("教室", "教室号", "房间", "座位") or is_chrome_text(value):
        return False
    if _ANY_TIME_RE.search(value):
        return False
    if re.search(r"\d{2,5}", value):
        return True
    return bool(re.search(r"[A-Za-z]{1,3}\d", value))


def is_header_cell(text: str) -> bool:
    """表头第一格（「教室」「教室号」「时间」…）。"""
    value = str(text or "").strip()
    return bool(value) and ("教室" in value or "时间" in value or "节次" in value) \
        and not _ANY_TIME_RE.search(value)


def _minutes(text: str) -> int:
    match = _TIME_RANGE_RE.match(str(text or ""))
    if not match:
        match = _ANY_TIME_RE.search(str(text or ""))
    if not match:
        return -1
    return int(match.group(1)) * 60 + int(match.group(2))


def slot_index(label: str, count: int = 14) -> int:
    """把表头/行首的标签换成节次下标（0 起）。认不出来返回 -1。"""
    text = str(label or "").strip()
    if not text:
        return -1
    match = _PERIOD_RE.match(text) or _BARE_NUM_RE.match(text)
    if match:
        return max(0, min(count - 1, int(match.group(1)) - 1))
    numeral = text.strip(" 　节大第")
    if numeral in _CN_NUMBERS:
        return max(0, min(count - 1, _CN_NUMBERS[numeral] - 1))
    start = _minutes(text)
    if start >= 0:
        for index, (_number, begin, finish) in enumerate(PERIOD_TIMES):
            low, high = _minutes(begin), _minutes(finish)
            if low <= start < high:
                return index
        best, distance = -1, 24 * 60
        for index, (_number, begin, _finish) in enumerate(PERIOD_TIMES):
            gap = abs(_minutes(begin) - start)
            if gap < distance:
                best, distance = index, gap
        return best
    return -1


def build_classroom_rooms(table: List[List[str]],
                          headers: List[str]) -> Dict[str, Any]:
    """把抓到的表格整理成「一行一间教室」的格子（自动认表格方向）。

    学校那张表的两种排法都要认：

    * **行 = 教室**：``[["H3101", "高数", "", ...], ...]``（表头是节次）；
    * **行 = 时间、列 = 教室**：表头 ``["时间", "H3101", "H3102", ...]``，
      每行 ``["08:00-08:45", "高数", "", ...]`` —— 用户看到「每行怎么还是时间」就是这个。

    输出 ``{"rooms": [...], "periods": [...], "transposed": bool}``，
    每间教室的 ``cells`` 都按 14 个节次对齐（占用格写课程名）。
    """
    table = [list(row or []) for row in (table or []) if row]
    headers = [str(item or "").strip() for item in (headers or [])]
    count = len(PERIOD_TIMES)
    if not table:
        return {"rooms": [], "periods": [], "transposed": False}

    # 表头行：第一格是「教室」「教室号」这种（学校页面第一格其实是「教室」+ 一个时钟）
    if table and is_header_cell(table[0][0] if table[0] else ""):
        if not headers:
            headers = [str(cell or "").strip() for cell in table[0]]
        table = table[1:]
    if not table:
        return {"rooms": [], "periods": [], "transposed": False}

    header_cells = headers[1:] if len(headers) > 1 else []
    column_head = header_cells or (table[0][1:] if table else [])
    first_col = [str(row[0] or "").strip() for row in table]

    # ① 按「行 = 教室」试一遍
    plain_periods = column_head if any(
        looks_like_time(cell) for cell in column_head) else []
    plain: List[Dict[str, Any]] = []
    for row in table:
        name = str(row[0] or "").strip()
        if not name or len(name) > 20 or is_chrome_text(name):
            continue
        if is_header_cell(name) or looks_like_time(name):
            continue                    # 这一行是表头/时间，不是教室
        values = row[1:]
        # 第二列可能是「座位数」（学校页面就是 JB100 | 258 | 节次…）
        if len(values) >= 3 and re.fullmatch(r"\d{1,5}", str(values[0] or "").strip()):
            values = values[1:]
        cells = []
        for offset, value in enumerate(values):
            text = str(value or "").strip()
            slot = slot_index(plain_periods[offset]) \
                if offset < len(plain_periods) else offset
            if text and text.strip() == name:
                text = ""       # 格子里写的是这间教室自己的名字 → 不是课
            detail = parse_course_cell(text) if _cell_occupied(text) else {}
            cells.append({
                "label": str(detail.get("label") or ""),
                "occupied": bool(detail),
                "slot": slot,
                "course": str(detail.get("course") or ""),
                "teacher": str(detail.get("teacher") or ""),
                "code": str(detail.get("code") or ""),
                "students": str(detail.get("students") or ""),
                "level": str(detail.get("level") or ""),
                "kind": str(detail.get("kind") or ""),
                "raw": str(detail.get("raw") or ""),
            })
        while len(cells) < count:            # 统一补到 14 节，下游好对齐
            cells.append({"label": "", "occupied": False})
        plain.append({"name": name, "cells": cells})

    # ② 按「列 = 教室」试一遍（表头是教室、行首是时间）
    column: List[Dict[str, Any]] = []
    for offset, name in enumerate(column_head):
        if not looks_like_room(name):
            continue
        cells: List[Dict[str, Any]] = [{} for _ in range(count)]
        for row in table:
            if offset + 1 >= len(row):
                continue
            value = str(row[offset + 1] or "").strip()
            slot = slot_index(row[0])
            if slot < 0 and str(row[0] or "").strip() in first_col:
                slot = first_col.index(str(row[0] or "").strip())
            if slot < 0 or not _cell_occupied(value):
                continue
            detail = parse_course_cell(value)
            cells[slot] = {
                "label": str(detail.get("label") or ""),
                "occupied": True,
                "course": str(detail.get("course") or ""),
                "teacher": str(detail.get("teacher") or ""),
                "code": str(detail.get("code") or ""),
                "students": str(detail.get("students") or ""),
                "level": str(detail.get("level") or ""),
                "kind": str(detail.get("kind") or ""),
                "raw": str(detail.get("raw") or ""),
            }
        column.append({"name": name, "cells": cells})

    def _score(rooms: List[Dict[str, Any]]) -> float:
        if not rooms:
            return 0.0
        good = sum(1 for room in rooms if looks_like_room(room["name"]))
        return good / float(len(rooms))

    def _dedupe(rooms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """同一个教室名只留一条：留「占用格更多」的那条（可能是分两行写的）。"""
        merged: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for room in rooms:
            name = str(room.get("name") or "")
            if not name:
                continue
            busy = sum(1 for cell in room.get("cells") or []
                       if cell.get("occupied"))
            if name not in merged:
                merged[name] = dict(room)
                merged[name]["_busy"] = busy
                order.append(name)
                continue
            keep = merged[name]
            if busy > int(keep.get("_busy") or 0):
                merged[name] = dict(room)
                merged[name]["_busy"] = busy
        out = []
        for name in order:
            room = merged[name]
            room.pop("_busy", None)
            out.append(room)
        return out

    if column and _score(column) > _score(plain):
        periods = [cell or "第 %d 节" % (index + 1)
                   for index, cell in enumerate(first_col)]
        return {"rooms": _dedupe(column), "periods": periods,
                "transposed": True}
    if plain:
        return {"rooms": _dedupe(plain), "periods": plain_periods,
                "transposed": False}
    if column:
        periods = [cell or "第 %d 节" % (index + 1)
                   for index, cell in enumerate(first_col)]
        return {"rooms": _dedupe(column), "periods": periods,
                "transposed": True}
    return {"rooms": [], "periods": [], "transposed": False}


def _looks_like_room(name: str) -> bool:
    """这个名字像不像教室（H2101 / JB100 / 光华西301），别把「二教」「F1」当教室。"""
    try:
        from . import dashboard
    except Exception:
        return bool(name)
    text = str(name or "").strip()
    if not text:
        return False
    if dashboard.is_room_name(text):
        return True
    try:
        if dashboard._looks_chrome(text):
            return False
        if dashboard.is_building_name(text):
            return False          # 二教 / 三教 / 江湾 / JA / F1 这些是楼
        # 兜底：带数字的短名字（A-101 / 101 / 测试用的 H32 01）
        return bool(len(text) <= 12 and re.search(r"\d", text))
    except Exception:
        return bool(text)


def parse_classroom_grid(raw: str, building: str = "",
                         room: str = "") -> Dict[str, Any]:
    """把空教室页面抓到的表解析成「教室 × 节次」的格子，给时间轴用。

    期望的输入（`CLASSROOM_JS`）：
    ``{"u": ..., "headers": [...], "rows": [[教室, 第1节, 第2节, ...], ...]}``
    以及兜底的 ``lines``（短行文本）。
    输出：``{"ok", "rooms": [{"name", "cells": [{label, occupied}]}], ...}``
    """
    payload: Dict[str, Any] = {"ok": False, "rooms": [], "headers": [],
                               "rows": [], "lines": [], "text": "", "url": "",
                               "building": building, "room": room,
                               "error": "", "ts": time.time()}
    data: Dict[str, Any] = {}
    try:
        parsed = json.loads(raw) if str(raw or "").strip().startswith("{") else None
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        data = parsed
    payload["url"] = str(data.get("u") or "")
    headers = [str(item).strip() for item in (data.get("headers") or [])]
    payload["headers"] = headers
    table: List[List[str]] = []
    for row in data.get("rows") or []:
        cells = [str(cell or "").strip() for cell in (row or [])]
        if cells and any(cells):
            table.append(cells)
    payload["rows"] = table[:60]
    payload["lines"] = [str(item) for item in (data.get("lines") or [])][:120]

    built = build_classroom_rooms(table, headers)
    rooms = list(built.get("rooms") or [])
    payload["transposed"] = bool(built.get("transposed"))
    if built.get("periods"):
        payload["periods"] = list(built.get("periods") or [])
    # 只留「像教室」的行：以前抓糊了会把教学楼下拉（二教 / 三教 / 江湾…）当成教室画出来
    if rooms:
        looks = [item for item in rooms if _looks_like_room(item.get("name"))]
        rooms = looks        # 一个都不像教室就当成没抓到（走下面的兜底 / 报错）
    if rooms:
        payload["ok"] = True
        if room:
            filters = [item for item in rooms if room in item["name"]]
            if filters:
                rooms = filters
        payload["rooms"] = rooms[:60]
        payload["value"] = "%d 间教室" % len(payload["rooms"])
        return payload

    # 兜底：只有短行文本（例如「H3101 1-2 空闲」），按行拆成单格
    fallback: List[Dict[str, Any]] = []
    for line in payload["lines"]:
        if is_chrome_text(line) or len(line) > 40:
            continue
        parts = [part for part in re.split(r"[\s|｜\t]+", line) if part.strip()]
        if not parts:
            continue
        name = parts[0]
        if len(name) > 12 or not _looks_like_room(name):
            continue                    # 「二教」「江湾」「登录」这种不是教室
        label = " ".join(parts[1:])
        fallback.append({"name": name,
                         "cells": [{"label": label,
                                    "occupied": _cell_occupied(label)}]})
    if fallback:
        payload["ok"] = True
        payload["rooms"] = fallback[:40]
        payload["value"] = "%d 间教室" % len(payload["rooms"])
        return payload
    if payload["lines"]:
        payload["error"] = ("抓到的不是教室表（看着像教学楼 / 下拉列表）："
                            "先在「空教室」页选好教学楼和教室，再点「抓取当前页面」")
        return payload
    payload["error"] = ("页面上没找到教室表：请在「空教室」页里选好教学楼和教室，"
                        "再点「抓取当前页面」")
    return payload

    """把空教室页面抓到的内容清成「教室 / 节次 / 状态」的短行。"""
    payload: Dict[str, Any] = {"ok": False, "value": "", "lines": [], "rows": [],
                               "text": "", "error": "", "url": "",
                               "ts": time.time()}
    data: Dict[str, Any] = {}
    try:
        parsed = json.loads(raw) if str(raw or "").strip().startswith("{") else None
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        data = parsed
    payload["url"] = str(data.get("u") or "")
    rows = clean_room_rows(data.get("rows") or [])
    if not rows:
        rows = clean_room_lines(data.get("lines") or [])
    payload["rows"] = rows[:40]
    if rows:
        payload.update(ok=True, value="%d 行空闲安排" % len(payload["rows"]))
    else:
        payload["error"] = ("页面上没找到教室表：请在「空教室」页里选好教学楼和教室，"
                            "再点「抓取当前页面」")
    return payload


# ---------------------------------------------------------------- 缓存
def cache_get(store, card_id: str) -> Optional[Dict[str, Any]]:
    try:
        raw = str(store.get_setting(CACHE_KEY % card_id, "") or "")
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def cache_put(store, card_id: str, payload: Dict[str, Any]) -> None:
    try:
        store.set_setting(CACHE_KEY % card_id, json.dumps(payload, ensure_ascii=False))
        store.set_setting(CACHE_AT_KEY % card_id, str(payload.get("ts") or time.time()))
    except Exception:
        pass


def cache_age_text(store, card_id: str) -> str:
    data = cache_get(store, card_id)
    if not data:
        return ""
    try:
        stamp = float(data.get("ts") or 0)
    except Exception:
        return ""
    if not stamp:
        return ""
    delta = max(0, int(time.time() - stamp))
    if delta < 90:
        return "刚刚更新"
    if delta < 3600:
        return "%d 分钟前" % (delta // 60)
    if delta < 86400:
        return "%d 小时前" % (delta // 3600)
    return "%d 天前" % (delta // 86400)


# ---------------------------------------------------------------- 桥本体
class EhallBridge(QObject):
    """用 ehall 的登录态在后台取数据（只在 GUI 线程里用）。"""

    card_ready = Signal(str, dict)     # 卡片 id, payload
    finished = Signal(dict)            # {卡片 id: payload}
    status = Signal(str)
    login_changed = Signal(bool)       # 现在有没有登录态
    classroom_ready = Signal(dict)     # 空教室：{ok, building, room, rows, ...}

    def __init__(self, store, profile_provider=None, parent=None):
        super().__init__(parent)
        self.store = store
        self._profile_provider = profile_provider
        self._page = None
        self._queue: List[Dict[str, Any]] = []
        self._job: Optional[Dict[str, Any]] = None
        self._results: Dict[str, Dict[str, Any]] = {}
        self._steps: List[Dict[str, Any]] = []
        self._step_values: List[Any] = []
        self._waiting_load = False
        self._logged_in = False
        self._login_tried = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_timeout)
        self._connected = False

    # -------------------------------------------------- 状态
    def profile(self):
        if not callable(self._profile_provider):
            return None
        try:
            return self._profile_provider()
        except Exception:
            return None

    def available(self) -> bool:
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: F401
        except Exception:
            return False
        return self.profile() is not None

    def busy(self) -> bool:
        return bool(self._job)

    def logged_in(self) -> bool:
        return bool(self._logged_in)

    # -------------------------------------------------- 公开：同步卡片
    def sync(self, card_ids: Optional[List[str]] = None) -> None:
        from . import dashboard

        ids = [str(item) for item in (card_ids or [spec["id"]
                                                   for spec in dashboard.EHALL_CARDS])]
        # 「空教室」要在组件里先选教学楼和教室，批量同步时跳过它
        ids = [item for item in ids if item not in INTERACTIVE_CARDS]
        if not ids:
            self.finished.emit({})
            return
        if not self.available():
            payload = {"ok": False, "value": "", "lines": [], "rows": [],
                       "error": "要先打开一次 ehall 网页（登录后这个后台取数才有登录态）",
                       "ts": time.time()}
            for card_id in ids:
                self._results[card_id] = payload
                self.card_ready.emit(card_id, payload)
            self.finished.emit(dict(self._results))
            self._results = {}
            return
        for card_id in ids:
            self._queue.append({"kind": "card", "card_id": card_id})
        self.status.emit("正在同步 ehall 数据…")
        if not self._job:
            QTimer.singleShot(0, self._start_next)

    # -------------------------------------------------- 公开：掉登录态就补登录
    def ensure_login(self) -> None:
        """在后台走一次登录（有存学号密码时自动填表 + 点登录）。"""
        if not self.available():
            self.login_changed.emit(False)
            return
        self._queue.append({"kind": "login"})
        if not self._job:
            QTimer.singleShot(0, self._start_next)

    # -------------------------------------------------- 公开：空教室
    def classroom_options(self) -> None:
        """读一次「空闲教室查询」页上的下拉选项（校区 / 教学楼 / 教室）。"""
        url = self._source_url("empty_room")
        if not self.available() or not url:
            self.classroom_ready.emit({"ok": False, "rows": [], "selects": [],
                                       "error": "先在设置里填空闲教室页面的地址"})
            return
        self._queue.append({"kind": "room_options"})
        if not self._job:
            QTimer.singleShot(0, self._start_next)

    def classroom_query(self, building: str, room: str = "",
                        time_key: str = "all", campus: str = "") -> None:
        """按「校区 → 教学楼（→ 教室）」去查空闲情况。

        ehall 那边就是先选校区、再选楼、再选教室、点查询才出表格；这里把这串动作
        交给后台页面做，回来的是一张「教室 × 节次」的表，界面画成时间轴。
        """
        url = self._source_url("empty_room")
        if not self.available() or not url:
            self.classroom_ready.emit({"ok": False, "rooms": [], "rows": [],
                                       "building": building, "room": room,
                                       "campus": str(campus or ""),
                                       "time_key": time_key,
                                       "error": "先在设置里填空闲教室页面的地址"})
            return
        self._queue.append({"kind": "room_query", "building": str(building or ""),
                            "room": str(room or ""),
                            "campus": str(campus or ""),
                            "time_key": str(time_key or "all")})
        if not self._job:
            QTimer.singleShot(0, self._start_next)

    # -------------------------------------------------- 内部：拿地址 / 名字
    def _title(self, card_id: str) -> str:
        from . import dashboard

        return str(dashboard.card_spec(card_id).get("title") or card_id)

    def _source_url(self, card_id: str) -> str:
        from . import dashboard

        return dashboard.source_url(self.store, card_id)

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from PySide6.QtWebEngineCore import QWebEnginePage

        profile = self.profile()
        if profile is None:
            return None
        page = QWebEnginePage(profile, self)
        self._page = page
        return page

    # -------------------------------------------------- 任务调度
    def _start_next(self) -> None:
        if self._job is not None:
            return
        if not self._queue:
            results = dict(self._results)
            self._results = {}
            self.status.emit("")
            if results:
                self.finished.emit(results)
            return
        self._job = self._queue.pop(0)
        self._steps = []
        self._step_values = []
        self._login_tried = 0
        kind = self._job.get("kind")
        if kind == "login":
            self._plan_login()
        elif kind == "room_options":
            self._plan_options()
        elif kind == "room_query":
            self._plan_query()
        else:
            self._plan_card()
        self._advance()

    def _page_ready(self) -> bool:
        page = self._ensure_page()
        if page is None:
            return False
        if not self._connected:
            try:
                page.loadFinished.connect(self._on_load_finished)
                self._connected = True
            except Exception:
                pass
        return True

    # ---- 计划：普通卡片
    def _plan_card(self) -> None:
        card_id = str(self._job.get("card_id") or "")
        url = self._source_url(card_id)
        if not url:
            self._fail_card(card_id, "还没填数据来源地址")
            return
        self.status.emit("正在读取 %s …" % self._title(card_id))
        self._steps = [{"kind": "load", "url": url},
                       {"kind": "login"},
                       {"kind": "js", "script": TABLE_JS}]

    # ---- 计划：只做登录
    def _plan_login(self) -> None:
        self.status.emit("正在检查 ehall 登录状态…")
        self._steps = [{"kind": "load", "url": self._source_url("ehall_todo")},
                       {"kind": "login", "force": True}]

    # ---- 计划：空教室 —— 读选项
    def _plan_options(self) -> None:
        self.status.emit("正在读取教学楼列表…")
        self._steps = [{"kind": "load", "url": self._source_url("empty_room")},
                       {"kind": "login"},
                       {"kind": "js", "script": OPTIONS_JS}]

    # ---- 计划：空教室 —— 选校区 → 选楼 → 选教室 → 查询 → 抓表格
    def _plan_query(self) -> None:
        building = str(self._job.get("building") or "")
        room = str(self._job.get("room") or "")
        campus = str(self._job.get("campus") or "")
        steps: List[Dict[str, Any]] = [
            {"kind": "load", "url": self._source_url("empty_room")},
            {"kind": "login"},
        ]
        if campus:
            self.status.emit("正在选择校区「%s」…" % campus)
            steps.append({"kind": "js", "script": pick_js(campus)})
            steps.append({"kind": "wait", "ms": 700})
        if building:
            self.status.emit("正在选择教学楼「%s」…" % building)
            steps.append({"kind": "js", "script": pick_js(building)})
            steps.append({"kind": "wait", "ms": 900})
        if room:
            self.status.emit("正在选择教室「%s」…" % room)
            steps.append({"kind": "js", "script": pick_js(room)})
            steps.append({"kind": "wait", "ms": 600})
        steps.append({"kind": "js", "script": CLICK_QUERY_JS})
        steps.append({"kind": "wait", "ms": AFTER_CLICK_MS})
        steps.append({"kind": "js", "script": CLASSROOM_JS})
        self._steps = steps

    # -------------------------------------------------- 执行
    def _advance(self) -> None:
        if self._job is None:
            return
        while self._steps:
            step = self._steps.pop(0)
            kind = step.get("kind")
            if kind == "load":
                if not self._page_ready():
                    self._fail_job("拿不到 ehall 的浏览器实例")
                    return
                self._waiting_load = True
                self._watchdog.start(TIMEOUT_MS)
                try:
                    from PySide6.QtCore import QUrl

                    self._page.setUrl(QUrl(str(step.get("url") or "")))
                except Exception as exc:  # noqa: BLE001
                    self._waiting_load = False
                    self._watchdog.stop()
                    self._fail_job("打不开这个地址（%s）" % str(exc)[:60])
                return
            if kind == "wait":
                self._timer.start(int(step.get("ms") or 500))
                return
            if kind == "login":
                self._check_login(step)
                return
            if kind == "login_check":
                self._verify_login()
                return
            if kind == "js":
                if not self._page_ready():
                    self._fail_job("拿不到 ehall 的浏览器实例")
                    return
                self._run_js(str(step.get("script") or ""))
                return
        self._finish_job()

    def _run_js(self, script: str) -> None:
        if self._page is None:
            self._fail_job("拿不到 ehall 的浏览器实例")
            return
        try:
            self._page.runJavaScript(script, self._on_js)
        except Exception as exc:  # noqa: BLE001
            self._fail_job("执行页面脚本失败（%s）" % str(exc)[:60])

    def _on_js(self, value: Any) -> None:
        self._step_values.append(value)
        if self._steps:
            QTimer.singleShot(120, self._advance)
            return
        self._finish_job()

    def _on_load_finished(self, ok: bool) -> None:
        if not self._waiting_load or self._job is None:
            return
        self._waiting_load = False
        self._watchdog.stop()
        if not ok:
            self._fail_job("页面没加载成功（检查网络 / VPN，或在 eHall 里重新登录）")
            return
        # 等 SPA 把数据渲染出来再进入下一步
        self._timer.start(SETTLE_MS)

    def _on_timeout(self) -> None:
        if self._page is None or self._job is None:
            self._fail_job("取数超时")
            return
        if self._waiting_load:
            self._waiting_load = False
            # 页面可能加载完了但很慢：继续往下走
            QTimer.singleShot(0, self._advance)
            return
        self._fail_job("取数超时")

    # -------------------------------------------------- 登录
    def _check_login(self, step: Dict[str, Any]) -> None:
        """看看当前页是不是登录页：是的话用存下来的学号密码自动登录。"""
        force = bool(step.get("force"))

        def after_check(value: Any) -> None:
            need_login = False
            if isinstance(value, str) and value.strip():
                try:
                    need_login = bool(json.loads(value).get("login"))
                except Exception:
                    need_login = False
            if not need_login:
                self._logged_in = True
                if force:
                    self.status.emit("ehall 已经登录着，直接取数就行")
                    try:
                        self.login_changed.emit(True)
                    except Exception:
                        pass
                QTimer.singleShot(0, self._advance)
                return
            self._logged_in = False
            if self._login_tried >= 1:
                try:
                    self.login_changed.emit(False)
                except Exception:
                    pass
                self._fail_job(
                    "需要先登录统一身份认证：到「eHall」页登录一次；"
                    "或在 设置 → 统一身份认证 里存好学号密码，之后会自动登录")
                return
            self._login_tried += 1
            script = self._autofill_script()
            if not script:
                try:
                    self.login_changed.emit(False)
                except Exception:
                    pass
                self._fail_job(
                    "登录态过期了，而且没有存统一身份认证的学号密码："
                    "到 设置 → 统一身份认证 里存一次（加密保存），以后就会自动登录")
                return
            self.status.emit("登录态过期，正在用保存的账号自动登录…")
            self._run_login_script(script)

        if self._page is None:
            self._fail_job("拿不到 ehall 的浏览器实例")
            return
        try:
            self._page.runJavaScript(LOGIN_CHECK_JS, after_check)
        except Exception:
            QTimer.singleShot(0, self._advance)

    def _autofill_script(self) -> str:
        try:
            from . import uis_login

            if not uis_login.has_credentials(self.store):
                return ""
            if not uis_login.autofill_enabled(self.store):
                return ""
            return uis_login.autofill_script(self.store, click=True)
        except Exception:
            return ""

    def _verify_login(self) -> None:
        """自动登录之后再看一眼：还停在登录页就说明学号密码不对。"""
        if self._page is None:
            self._fail_job("拿不到 ehall 的浏览器实例")
            return

        def after(value: Any) -> None:
            still = False
            if isinstance(value, str) and value.strip():
                try:
                    still = bool(json.loads(value).get("login"))
                except Exception:
                    still = False
            if still:
                try:
                    self.login_changed.emit(False)
                except Exception:
                    pass
                self._fail_job(
                    "自动登录后还停在统一身份认证页：学号/密码可能不对，"
                    "或者需要二次验证（去「eHall」页手动过一次）")
                return
            self._logged_in = True
            try:
                self.login_changed.emit(True)
            except Exception:
                pass
            QTimer.singleShot(0, self._advance)

        try:
            self._page.runJavaScript(LOGIN_CHECK_JS, after)
        except Exception:
            QTimer.singleShot(0, self._advance)

    def _run_login_script(self, script: str) -> None:
        if self._page is None:
            self._fail_job("拿不到 ehall 的浏览器实例")
            return

        def after(done: Any) -> None:
            info = {}
            if isinstance(done, str) and done.strip():
                try:
                    info = json.loads(done)
                except Exception:
                    info = {}
            if not info.get("ok"):
                self._login_tried = 2       # 别再无限试
                self._fail_job("自动登录没成功：%s" % str(
                    info.get("reason") or "登录框没填进去"))
                return
            # 填好了也点了登录：等跳转回来，再确认一次是不是真的进去了
            self.status.emit("已自动提交统一身份认证，正在等 ehall 回来…")
            self._steps.insert(0, {"kind": "login_check"})
            self._timer.start(LOGIN_MS)

        try:
            self._page.runJavaScript(script, after)
        except Exception:
            self._fail_job("自动登录脚本没跑起来")

    # -------------------------------------------------- 收尾
    def _finish_job(self) -> None:
        job = self._job or {}
        self._job = None
        self._steps = []
        values = list(self._step_values)
        kind = job.get("kind")
        if kind == "card":
            card_id = str(job.get("card_id") or "")
            raw = str(values[-1] or "") if values else ""
            payload = parse_card(card_id, raw)
            payload["ts"] = time.time()
            cache_put(self.store, card_id, payload)
            self._results[card_id] = payload
            try:
                self.card_ready.emit(card_id, payload)
            except Exception:
                pass
        elif kind == "login":
            try:
                self.login_changed.emit(True)
            except Exception:
                pass
            self.status.emit("ehall 登录状态正常")
        elif kind == "room_options":
            payload = self._parse_options(values)
            self._emit_classroom(payload)
        elif kind == "room_query":
            payload = self._parse_room_rows(values, str(job.get("building") or ""),
                                            str(job.get("room") or ""))
            self._emit_classroom(payload)
        QTimer.singleShot(160, self._start_next)

    def _fail_job(self, message: str) -> None:
        job = self._job or {}
        self._job = None
        self._steps = []
        kind = job.get("kind")
        self.status.emit(message)
        if kind == "card":
            card_id = str(job.get("card_id") or "")
            payload = parse_card(card_id, "")
            payload["error"] = message
            payload["ts"] = time.time()
            cache_put(self.store, card_id, payload)
            self._results[card_id] = payload
            try:
                self.card_ready.emit(card_id, payload)
            except Exception:
                pass
        elif kind == "login":
            try:
                self.login_changed.emit(False)
            except Exception:
                pass
        elif kind in ("room_options", "room_query"):
            self._emit_classroom({"ok": False, "rows": [], "selects": [],
                                  "error": message})
        QTimer.singleShot(160, self._start_next)

    def _fail_card(self, card_id: str, message: str) -> None:
        payload = parse_card(card_id, "")
        payload["error"] = message
        payload["ts"] = time.time()
        cache_put(self.store, card_id, payload)
        self._results[card_id] = payload
        try:
            self.card_ready.emit(card_id, payload)
        except Exception:
            pass
        QTimer.singleShot(0, self._start_next)

    def _emit_classroom(self, payload: Dict[str, Any]) -> None:
        try:
            self.classroom_ready.emit(payload)
        except Exception:
            pass

    # -------------------------------------------------- 空教室解析
    def _parse_options(self, values: List[Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"ok": False, "selects": [], "rows": [],
                                   "error": "", "url": "", "text": ""}
        raw = ""
        for value in values:
            if isinstance(value, str) and value.strip().startswith("{"):
                raw = value
        if not raw:
            payload["error"] = "没读到页面内容"
            return payload
        try:
            data = json.loads(raw)
        except Exception:
            payload["error"] = "页面内容不是预期的格式"
            return payload
        payload["url"] = str(data.get("u") or "")
        payload["text"] = str(data.get("t") or "")[:4000]
        selects = data.get("selects") or []
        try:
            from . import dashboard

            clean_buildings = dashboard.clean_buildings
            clean_rooms = dashboard.clean_rooms
        except Exception:                      # noqa: BLE001
            clean_buildings = lambda values: list(values or [])   # noqa: E731
            clean_rooms = lambda values: list(values or [])       # noqa: E731
        cleaned: List[Dict[str, Any]] = []
        buildings: List[str] = []
        for item in selects:
            if not isinstance(item, dict):
                continue
            options = [str(option).strip() for option in (item.get("options") or [])
                       if str(option).strip()]
            if not options:
                continue
            # 页面上的下拉里既有「请选择 / 全部校区」这种表单词，也有楼名；
            # 只留「像楼名」的（没有楼名时才退回「像教室名」的）
            picked = clean_buildings(options)
            if picked:
                if not buildings:
                    buildings = picked
                cleaned.append({"name": str(item.get("name") or ""),
                                "options": picked})
                continue
            rooms = clean_rooms(options)
            if rooms:
                cleaned.append({"name": str(item.get("name") or ""),
                                "options": rooms})
        payload["selects"] = cleaned
        if cleaned:
            payload["ok"] = True
            # 「教学楼」下拉：把页面上认出来的楼名合并进已知列表（只留真楼名）
            if buildings:
                try:
                    from . import dashboard

                    known = dashboard.empty_room_buildings(self.store)
                    merged = list(dict.fromkeys(buildings + known))
                    dashboard.set_empty_room_buildings(self.store, merged)
                except Exception:
                    pass
        else:
            payload["error"] = ("页面上没找到教学楼下拉框（可能要先登录，"
                                "或者这个地址不是空闲教室查询页）")
        return payload

    def _parse_room_rows(self, values: List[Any], building: str,
                         room: str) -> Dict[str, Any]:
        """把空教室页面的结果清成「教室 / 节次 / 状态」这样的短行。

        页面上一半是表单（「请选择 全部校区 邯郸 二教…」这种），直接铺出来就是
        用户看到的那团乱码；所以这里做三件事：丢掉框架词、只留短内容、
        同一行里拼起来太长的整行不要。
        """
        picks: List[Dict[str, Any]] = []
        raw = ""
        for value in values:
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not text.startswith("{"):
                continue
            try:
                data = json.loads(text)
            except Exception:
                continue
            if "selects" in data or "rows" in data or "tables" in data:
                raw = text
            elif "ok" in data:
                picks.append(data)
        payload: Dict[str, Any] = {"ok": False, "value": "", "lines": [],
                                   "rows": [], "text": "", "error": "",
                                   "building": building, "room": room,
                                   "picks": picks, "ts": time.time()}
        reasons = [str(item.get("reason")) for item in picks
                   if not item.get("ok") and item.get("reason")]
        if not raw:
            payload["error"] = ("；".join(dict.fromkeys(reasons))[:120]
                                if reasons else "没读到表格（换个教学楼再试）")
            return payload
        try:
            data = json.loads(raw)
        except Exception:
            payload["error"] = "页面内容不是预期的格式"
            return payload
        payload["url"] = str(data.get("u") or "")
        grid = parse_classroom_grid(raw, building, room)
        payload.update({key: grid.get(key) for key in
                        ("ok", "rooms", "headers", "rows", "lines", "value",
                         "error", "url")})
        payload["time_key"] = str(self._job.get("time_key") or "all") \
            if self._job else "all"
        if self._job is not None:
            payload["campus"] = str(self._job.get("campus") or "")
        if payload.get("ok"):
            cache_put(self.store, "empty_room", payload)
        if not payload.get("ok") and reasons:
            payload["error"] = "；".join(dict.fromkeys(reasons))[:120]
        return payload

    # -------------------------------------------------- 收尾
    def shutdown(self) -> None:
        """退出前把后台页面收掉（它和 ehall 网页面板共用一个 profile）。"""
        self._queue = []
        self._job = None
        self._steps = []
        self._waiting_load = False
        for timer in (self._timer, self._watchdog):
            try:
                timer.stop()
            except Exception:
                pass
        page = self._page
        self._page = None
        self._connected = False
        if page is None:
            return
        try:
            page.setParent(None)
            page.deleteLater()
        except Exception:
            pass
