"""给 AI 用的「应用内工具」（OpenAI function calling / tools 协议）。

把应用自己已经会做的事暴露给 AI 面板里的 API 对话：

* `schedule_list` / `schedule_add` / `schedule_update` / `schedule_delete`：日程的查、加、改、删；
* `elearning_courses`：读 eLearning（Canvas）的课程、作业与截止时间；
* `elearning_download`：把课程文件 / 作业附件下载到本机（下载目录跟设置里一致）；
* `timetable_fetch`：用教务登录态抓课表并写进日历（连同「课程表」分组）。

约定：
* 读日程、写日程这些**数据库操作只在界面线程做**（跨线程写 sqlite 会把界面拖死）；
* 抓课表 / 读 eLearning / 下载文件是网络 + 磁盘活，放到后台线程里跑，
  调用方用 `pump` 回调保持界面刷新（不给 `pump` 就一直等到结束）；
* 每个工具都返回 `{"ok": bool, "summary": str, ...}`，`summary` 是给人看的一句话，
  其余字段会以 JSON 形式回给模型。
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

TOOL_ENABLED_KEY = "ai_tools_enabled"
COLORS = ("violet", "teal", "amber", "rose", "sky")

# 界面上显示的中文名（日志 / 卡片标题用）
LABELS = {
    "schedule_list": "查日程",
    "schedule_add": "加日程",
    "schedule_update": "改日程",
    "schedule_delete": "删日程",
    "elearning_courses": "读 eLearning",
    "elearning_download": "下载课程文件",
    "timetable_fetch": "抓课表",
    "fs_list": "看本地目录",
    "fs_read": "读本地文件",
    "fs_search": "搜本地文件",
    "fs_write": "写本地文件",
    "fs_discard": "丢弃文件（改名）",
    "time_now": "看时间",
    "sys_info": "看电脑状况",
    "app_state": "看应用状态",
    "memo_search": "搜备忘",
    "usage_stats": "看用量",
    "tool_help": "查可用工具",
    "window_list": "看窗口标题",
    "memo_append": "记到备忘",
    "countdown_add": "加倒计时",
    "flashcards_create": "生成复习卡片",
    "pomodoro_start": "开始专注",
    "web_search": "上网搜索",
    "web_fetch": "读网页",
    "video_search": "找课程视频",
    "course_resource_search": "找课件资料",
    "resource_push": "推送资源",
    "arxiv_search": "搜 arXiv",
    "crossref_search": "搜 Crossref",
    "semantic_scholar_search": "搜 Semantic Scholar",
    "doi_lookup": "按 DOI 查",
    "literature_search": "搜文献",
    "pdf_download": "下载文献 PDF",
    "citation_format": "生成引用",
    "endnote_export": "导出给 EndNote",
    "library_open": "打开数据库",
    "mail_send": "发邮件",
    "setting_get": "读设置",
    "setting_set": "改设置",
    "zip_extract": "解压压缩包",
}

# 这些工具会睡觉（网络 / 磁盘），放到后台线程里跑
SLOW_TOOLS = {"elearning_courses", "elearning_download", "timetable_fetch",
              "fs_search"}


# ------------------------------------------------------------------ 工具声明
def _fn(name: str, description: str, properties: Dict[str, Any],
        required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required or []),
            },
        },
    }


_DATE_PROP = {"type": "string", "description": "日期，格式 YYYY-MM-DD（例如 2026-10-24）"}
_TIME_PROP = {"type": "string", "description": "时间，24 小时制 HH:MM（例如 09:30）"}

_SCHEMAS: List[Dict[str, Any]] = [
    _fn(
        "schedule_list",
        "查看某个时间段内的日程（默认从今天起 7 天）。回答「我明天有什么安排」这类问题前先调用它。",
        {
            "date_from": dict(_DATE_PROP, description="开始日期 YYYY-MM-DD，默认今天"),
            "date_to": dict(_DATE_PROP, description="结束日期 YYYY-MM-DD（含），默认开始日期 +7 天"),
            "keyword": {"type": "string", "description": "只看标题/备注里包含这个词的日程，可留空"},
        },
    ),
    _fn(
        "schedule_add",
        "在日历里新建一条日程。用户说「明天下午三点开组会」这种就调用它。",
        {
            "title": {"type": "string", "description": "日程标题"},
            "date": dict(_DATE_PROP, description="日期 YYYY-MM-DD，可说「今天 / 明天」时换算好再填"),
            "start_time": dict(_TIME_PROP, description="开始时间，默认 09:00"),
            "end_time": dict(_TIME_PROP, description="结束时间，默认开始时间 +1 小时"),
            "description": {"type": "string", "description": "备注，可留空"},
            "color": {"type": "string", "enum": list(COLORS),
                      "description": "颜色：violet/teal/amber/rose/sky，默认 violet"},
            "remind_minutes": {"type": "integer",
                              "description": "提前多少分钟提醒；0=开始时，不填=不提醒"},
        },
        ["title", "date"],
    ),
    _fn(
        "schedule_update",
        "修改一条已有日程（只传要改的字段）。先用 schedule_list 拿到 id。",
        {
            "event_id": {"type": "integer", "description": "日程 id（schedule_list 返回的 id）"},
            "title": {"type": "string"},
            "date": _DATE_PROP,
            "start_time": _TIME_PROP,
            "end_time": _TIME_PROP,
            "description": {"type": "string"},
            "color": {"type": "string", "enum": list(COLORS)},
            "remind_minutes": {"type": "integer"},
            "done": {"type": "boolean", "description": "是否标记为已完成"},
        },
        ["event_id"],
    ),
    _fn(
        "schedule_delete",
        "删除一条日程（用户明确要求删掉时才用）。",
        {"event_id": {"type": "integer", "description": "日程 id"}},
        ["event_id"],
    ),
    _fn(
        "elearning_courses",
        "读取 eLearning（Canvas）里的课程、作业和截止时间。需要之前在「作业」页登录过一次。",
        {"keyword": {"type": "string", "description": "只看课程名或作业名里包含这个词的，可留空"}},
    ),
    _fn(
        "elearning_download",
        "把 eLearning 的课程文件与作业附件下载到本机下载目录（重名会跳过）。",
        {
            "keyword": {"type": "string",
                        "description": "只下载课程名包含这个词的文件，可留空=全部"},
            "limit": {"type": "integer", "description": "最多下载几个文件，默认 40"},
        },
    ),
    _fn(
        "timetable_fetch",
        "用教务系统的登录状态抓取本学期课表并写进日历（重复的课会跳过）。需要之前在设置里登录过教务。",
        {
            "semester": {"type": "string",
                         "description": "学期编号，一般留空，程序会自己找"},
            "term_start": dict(_DATE_PROP, description="学期第一周周一，留空则按教务给的日期算"),
        },
    ),
]

# 本地资源工具（按权限档位动态出现：关闭时根本不告诉模型有这些工具）
_LOCAL_SCHEMAS: List[Dict[str, Any]] = [
    _fn(
        "fs_list",
        "列出本机某个目录里的文件（只能在你允许的目录里）。判断「这个文件夹里有什么」时用。",
        {
            "path": {"type": "string",
                     "description": "目录，相对路径按工作区解析；留空=工作区根目录"},
            "limit": {"type": "integer", "description": "最多列多少项，默认 400"},
        },
    ),
    _fn(
        "fs_read",
        "读取本机上的文本文件内容（只能读你允许的目录）。回答与文件内容有关的问题前先读它。",
        {
            "path": {"type": "string", "description": "文件路径（必填）"},
            "limit": {"type": "integer", "description": "最多读多少字节，默认按设置"},
        },
        ["path"],
    ),
    _fn(
        "fs_search",
        "在你允许的目录里，按**文件内容**搜索关键词，返回命中的文件与上下文片段。",
        {
            "keyword": {"type": "string", "description": "要找的关键词"},
            "path": {"type": "string", "description": "从哪个目录开始找，留空=工作区"},
            "limit": {"type": "integer", "description": "最多返回几条，默认 50"},
        },
        ["keyword"],
    ),
    _fn(
        "fs_write",
        "把文本写入本机文件（只有「读写」档才可用）。用户明确要求保存/新建文件时才调用。",
        {
            "path": {"type": "string", "description": "文件路径（必填）"},
            "content": {"type": "string", "description": "要写入的文本"},
            "append": {"type": "boolean", "description": "true=追加到末尾，默认覆盖"},
        },
        ["path", "content"],
    ),
    _fn(
        "fs_discard",
        "把文件「丢弃」：不会真的删除，而是把文件名改成 `[discarded] 原名`，"
        "同时告诉用户去哪个目录把它彻底删掉。用户说「删掉这个文件」时用它。",
        {
            "path": {"type": "string", "description": "要丢弃的文件路径（必填）"},
        },
        ["path"],
    ),
]

#: 只读信息类（A1–A6，用户逐个批准）：跟本地资源开关无关，一直可用（除非单独关掉）
_INFO_SCHEMAS: List[Dict[str, Any]] = [
    _fn(
        "time_now",
        "取当前精确日期、时间、星期和时区。要算「还有几天 / 现在几点」时用（比系统提示词里的日期更准）。",
        {},
    ),
    _fn(
        "sys_info",
        "读这台电脑的概况：CPU 核数、内存占用、磁盘剩余、电池、系统版本、本机 IP。"
        "用户问「电脑卡不卡 / 空间够不够 / 还有多少电」时用。",
        {},
    ),
    _fn(
        "app_state",
        "读应用内部状态：今天的日程数与完成情况、未读通知数、邮件数、备忘数、当前登录用户。"
        "回答「我今天有哪些安排 / 有没有新通知」前先看它。",
        {},
    ),
    _fn(
        "memo_search",
        "在本地备忘/笔记里按关键词搜索（只读）。用户问「我之前记过什么」时用。",
        {
            "keyword": {"type": "string", "description": "关键词"},
            "limit": {"type": "integer", "description": "最多几条，默认 10"},
        },
        ["keyword"],
    ),
    _fn(
        "usage_stats",
        "读本机的 API 用量统计：今天用了多久、调用多少次、平均首字延迟与出字速度。",
        {},
    ),
    _fn(
        "tool_help",
        "列出你现在可用的工具（以及哪些被用户关掉了）。不确定能不能做某件事时先查一下。",
        {},
    ),
    _fn(
        "window_list",
        "列出当前可见的窗口标题（隐私敏感，用户自己打开才可用）。回答「我现在在写什么」时可能用到。",
        {"limit": {"type": "integer", "description": "最多列几个，默认 15"}},
    ),
    _fn(
        "memo_append",
        "往备忘里追加一段文字（有同名备忘就追加，没有就新建）。用户说「记一下 / 记到备忘里」时用。",
        {
            "title": {"type": "string", "description": "备忘标题，留空则叫「AI 速记」"},
            "text": {"type": "string", "description": "要记的内容（必填）"},
        },
        ["text"],
    ),
    _fn(
        "countdown_add",
        "新建一条重要日倒计时（考试、截止日）。日期用 YYYY-MM-DD。",
        {
            "title": {"type": "string", "description": "事件名（必填）"},
            "day": {"type": "string", "description": "日期 YYYY-MM-DD（必填）"},
            "note": {"type": "string", "description": "备注，可留空"},
        },
        ["title", "day"],
    ),
    _fn(
        "flashcards_create",
        "把知识点做成复习卡片（间隔重复）。用户说「帮我出几道题考自己」时用。",
        {
            "cards": {
                "type": "array",
                "description": "卡片数组，每项 {front: 问题, back: 答案}",
                "items": {"type": "object", "properties": {
                    "front": {"type": "string"},
                    "back": {"type": "string"},
                }},
            },
            "source": {"type": "string", "description": "来源说明（课程名/文献名），可留空"},
        },
        ["cards"],
    ),
    _fn(
        "pomodoro_start",
        "启动应用里的专注计时（默认 25 分钟）。用户说「开始专注 / 番茄钟」时用。",
        {
            "minutes": {"type": "integer", "description": "分钟数，默认 25，最多 180"},
            "label": {"type": "string", "description": "这次专注做什么，可留空"},
        },
    ),
    _fn(
        "web_search",
        "上网搜索（普通网页）。回答「怎么学 / 有没有好的讲解 / 求推荐资料」这类问题时，"
        "先搜一下再把链接给用户，别凭空编网址。",
        {
            "query": {"type": "string", "description": "搜索关键词"},
            "limit": {"type": "integer", "description": "最多几条，默认 8"},
        },
        ["query"],
    ),
    _fn(
        "web_fetch",
        "抓取一个网页的正文并转成纯文本（有长度上限）。要读某个链接的具体内容时用。",
        {
            "url": {"type": "string", "description": "http/https 网址（必填）"},
            "limit": {"type": "integer", "description": "最多多少字，默认 12000"},
        },
        ["url"],
    ),
    _fn(
        "video_search",
        "找课程视频：优先哔哩哔哩 / 中国大学MOOC / YouTube，返回标题与链接。",
        {
            "query": {"type": "string", "description": "课程或知识点（必填）"},
            "limit": {"type": "integer", "description": "最多几条，默认 8"},
        },
        ["query"],
    ),
    _fn(
        "course_resource_search",
        "找某门课的讲义 / 课件 / 习题：**先翻 eLearning 里你的课程文件**，再上网搜。",
        {
            "keyword": {"type": "string", "description": "课程名或关键词（必填）"},
            "limit": {"type": "integer", "description": "最多几条，默认 10"},
        },
        ["keyword"],
    ),
    _fn(
        "resource_push",
        "把找到的学习资源推送成卡片并收藏进「学习资源」（用户可以在左侧边栏回看）。",
        {
            "items": {
                "type": "array",
                "description": "资源数组，每项 {title, url, platform, snippet}",
                "items": {"type": "object", "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "platform": {"type": "string"},
                    "snippet": {"type": "string"},
                }},
            },
            "kind": {"type": "string", "description": "video / doc / paper / other"},
        },
        ["items"],
    ),
    _fn(
        "arxiv_search",
        "在 arXiv 上搜论文（标题/作者/年份/摘要/PDF 链接）。结果会进「我的文献库」侧栏，"
        "可以多选后下载或引用。",
        {
            "query": {"type": "string", "description": "关键词（必填）"},
            "limit": {"type": "integer", "description": "最多几篇，默认 8"},
            "project": {"type": "string", "description": "归到哪个项目，可留空"},
        },
        ["query"],
    ),
    _fn(
        "crossref_search",
        "用 Crossref 搜论文（DOI/期刊/出版社，覆盖 IEEE、Elsevier 等）。",
        {
            "query": {"type": "string", "description": "关键词（必填）"},
            "limit": {"type": "integer", "description": "最多几篇，默认 8"},
            "project": {"type": "string", "description": "归到哪个项目，可留空"},
        },
        ["query"],
    ),
    _fn(
        "semantic_scholar_search",
        "用 Semantic Scholar 搜论文（带引用数与摘要，适合挑高影响力文献）。",
        {
            "query": {"type": "string", "description": "关键词（必填）"},
            "limit": {"type": "integer", "description": "最多几篇，默认 8"},
            "project": {"type": "string", "description": "归到哪个项目，可留空"},
        },
        ["query"],
    ),
    _fn(
        "doi_lookup",
        "按 DOI 精确查一篇文献的元数据，并生成 BibTeX。",
        {"doi": {"type": "string", "description": "DOI（必填）"},
         "project": {"type": "string", "description": "归到哪个项目，可留空"}},
        ["doi"],
    ),
    _fn(
        "literature_search",
        "一次搜三家（arXiv + Crossref + Semantic Scholar）并按 DOI/标题合并去重。"
        "用户问「有没有相关文献」时优先用它。",
        {
            "query": {"type": "string", "description": "关键词（必填）"},
            "limit": {"type": "integer", "description": "最多几篇，默认 12"},
            "project": {"type": "string", "description": "归到哪个项目，可留空"},
        },
        ["query"],
    ),
    _fn(
        "pdf_download",
        "把**用户已经勾选**的文献 PDF 下载进「我的文献库」。会自动弹窗让你确认，"
        "而且只能写进文献库目录。",
        {
            "ids": {"type": "array", "items": {"type": "integer"},
                    "description": "文献 id 数组（来自检索结果）"},
        },
        ["ids"],
    ),
    _fn(
        "citation_format",
        "给选中的文献生成引用文本：gbt7714 / apa / bibtex / ris。",
        {
            "ids": {"type": "array", "items": {"type": "integer"},
                    "description": "文献 id 数组"},
            "style": {"type": "string",
                      "enum": ["gbt7714", "apa", "bibtex", "ris", "enw"],
                      "description": "引用格式，默认 gbt7714"},
        },
        ["ids"],
    ),
    _fn(
        "endnote_export",
        "把选中的文献导出成 EndNote 能导入的文件（.enw/.ris）。没装 EndNote 时"
        "会先问用户要不要经 ehall 安装。",
        {
            "ids": {"type": "array", "items": {"type": "integer"},
                    "description": "文献 id 数组"},
            "style": {"type": "string", "enum": ["enw", "ris"],
                      "description": "enw（EndNote 原生）或 ris，默认 enw"},
        },
        ["ids"],
    ),
    _fn(
        "library_open",
        "在应用内浏览器里打开图书馆 / IEEE / ScienceDirect / 知网等数据库搜索页。"
        "需要登录或人机验证时，页面直接交给用户处理。",
        {
            "query": {"type": "string", "description": "搜索关键词，可留空"},
            "which": {"type": "string",
                      "description": "library / arxiv / ieee / sciencedirect / scholar / cnki"},
        },
    ),
    _fn(
        "mail_send",
        "代用户发一封邮件。**会先弹出可编辑的窗口让用户确认收件人/主题/正文**，"
        "用户点发送才真的发。用户明确让你发信时才调用。",
        {
            "to": {"type": "string", "description": "收件人邮箱（必填）"},
            "subject": {"type": "string", "description": "主题"},
            "text": {"type": "string", "description": "正文（纯文本）"},
        },
        ["to"],
    ),
    _fn(
        "setting_get",
        "读应用里那些允许修改的设置（白名单内的键）。",
        {"key": {"type": "string", "description": "设置键名，留空=列出全部可改项"}},
    ),
    _fn(
        "setting_set",
        "改一个应用设置（只允许安全白名单里的键，且**每次都要用户确认**）。",
        {
            "key": {"type": "string", "description": "设置键名（必填）"},
            "value": {"type": "string", "description": "新值"},
        },
        ["key", "value"],
    ),
    _fn(
        "zip_extract",
        "把工作区里的 zip 压缩包解压到工作区（防路径穿越；每次都要用户确认）。",
        {
            "archive": {"type": "string", "description": "压缩包路径（必填）"},
            "dest": {"type": "string", "description": "解压到哪个目录，留空=同名目录"},
        },
        ["archive"],
    ),
]


def schemas(store=None) -> List[Dict[str, Any]]:
    """给 /chat/completions 的 tools 字段。

    给了 `store` 就按设置过滤本地文件工具（关闭时不出现；只读时没有 fs_write）。
    """
    items = list(_SCHEMAS)
    if store is None:
        return items + list(_INFO_SCHEMAS)
    try:
        from . import ai_local

        current = ai_local.mode(store)
    except Exception:
        current = "read"
    items = [item for item in items
             if tool_enabled(store, item["function"]["name"])]
    items = items + [schema for schema in _INFO_SCHEMAS
                     if tool_enabled(store, schema["function"]["name"])]
    if current == "off":
        return items
    for schema in _LOCAL_SCHEMAS:
        name = schema["function"]["name"]
        if name == "fs_write" and current != "write":
            continue
        if not tool_enabled(store, name):
            continue
        items.append(schema)
    return items


def names(store=None) -> List[str]:
    return [item["function"]["name"] for item in schemas(store)]


def describe(name: str, args: Optional[Dict[str, Any]] = None) -> str:
    """把一次调用变成一句中文，显示在对话里。"""
    label = LABELS.get(name, name)
    args = args or {}
    bits: List[str] = []
    for key, value in args.items():
        if value in (None, "", []):
            continue
        text = str(value)
        if len(text) > 28:
            text = text[:28] + "…"
        bits.append(f"{key}={text}")
    return f"{label}（{'，'.join(bits)}）" if bits else label


# ------------------------------------------------------------------ 小工具
def _today() -> date:
    return date.today()


def _to_date(text: Any, default: date) -> date:
    raw = str(text or "").strip()
    if not raw:
        return default
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%m-%d", "%m/%d"):
        try:
            parsed = datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
        if pattern in ("%m-%d", "%m/%d"):
            parsed = parsed.replace(year=default.year)
        return parsed
    raise ValueError(f"看不懂日期「{raw}」，请用 YYYY-MM-DD")


def _to_clock(text: Any, default: str = "") -> str:
    raw = str(text or "").strip().replace("：", ":")
    if not raw:
        return default
    for pattern in ("%H:%M", "%H:%M:%S", "%H", "%H点%M分", "%H点"):
        try:
            return datetime.strptime(raw, pattern).strftime("%H:%M")
        except ValueError:
            continue
    raise ValueError(f"看不懂时间「{raw}」，请用 HH:MM")


def _shift_clock(clock: str, minutes: int) -> str:
    try:
        base = datetime.strptime(clock, "%H:%M") + timedelta(minutes=int(minutes))
    except ValueError:
        return clock
    return base.strftime("%H:%M")


def _fmt_event(item: Dict[str, Any]) -> str:
    clock = f"{item.get('start_time', '')}-{item.get('end_time', '')}"
    marks = " [已完成]" if item.get("done") else ""
    title = item.get("title") or "（无标题）"
    return f"#{item.get('id')} {item.get('date', '')} {clock} {title}{marks}"


def _short(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(item.get("id") or 0),
        "title": item.get("title") or "",
        "date": item.get("date") or "",
        "start_time": item.get("start_time") or "",
        "end_time": item.get("end_time") or "",
        "done": bool(item.get("done")),
        "color": item.get("color") or "",
        "description": (item.get("description") or "")[:200],
    }


# ------------------------------------------------------------------ 日程
def schedule_list(store, args: Dict[str, Any]) -> Dict[str, Any]:
    start = _to_date(args.get("date_from"), _today())
    end = _to_date(args.get("date_to"), start + timedelta(days=7))
    if end < start:
        start, end = end, start
    keyword = str(args.get("keyword") or "").strip().lower()
    rows = store.events_between(start.isoformat(), end.isoformat())
    if keyword:
        rows = [item for item in rows
                if keyword in str(item.get("title", "")).lower()
                or keyword in str(item.get("description", "")).lower()]
    lines = [_fmt_event(item) for item in rows[:80]]
    if not rows:
        summary = f"{start} 到 {end} 没有日程"
    else:
        summary = (f"{start} 到 {end} 共 {len(rows)} 条日程"
                   + ("（只列前 80 条）" if len(rows) > 80 else ""))
    return {"ok": True, "summary": summary, "count": len(rows),
            "events": [_short(item) for item in rows[:80]],
            "text": "\n".join(lines)}


def schedule_add(store, args: Dict[str, Any]) -> Dict[str, Any]:
    title = str(args.get("title") or "").strip()
    if not title:
        return {"ok": False, "summary": "新建日程失败：标题是空的"}
    when = _to_date(args.get("date"), _today())
    start_clock = _to_clock(args.get("start_time"), "09:00")
    end_clock = _to_clock(args.get("end_time"), _shift_clock(start_clock, 60))
    color = str(args.get("color") or "violet").strip()
    if color not in COLORS:
        color = "violet"
    remind = args.get("remind_minutes")
    try:
        remind = None if remind in (None, "") else int(remind)
    except (TypeError, ValueError):
        remind = None
    event_id = store.add_event(
        title=title,
        description=str(args.get("description") or "").strip(),
        date=when.isoformat(),
        start_time=start_clock,
        end_time=end_clock,
        remind_minutes=remind,
        color=color,
    )
    return {"ok": True, "id": event_id,
            "summary": f"已加入日程：{when} {start_clock}-{end_clock} {title}（#{event_id}）"}


def schedule_update(store, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        event_id = int(args.get("event_id") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "summary": "修改失败：event_id 不是数字"}
    current = store.get_event(event_id)
    if current is None:
        return {"ok": False, "summary": f"修改失败：没有 id={event_id} 的日程"}
    fields: Dict[str, Any] = {}
    if args.get("title"):
        fields["title"] = str(args["title"]).strip()
    if args.get("description") is not None:
        fields["description"] = str(args.get("description") or "")
    if args.get("date"):
        fields["date"] = _to_date(args["date"], _today()).isoformat()
    if args.get("start_time"):
        fields["start_time"] = _to_clock(args["start_time"])
    if args.get("end_time"):
        fields["end_time"] = _to_clock(args["end_time"])
    if args.get("color") in COLORS:
        fields["color"] = args["color"]
    if args.get("remind_minutes") not in (None, ""):
        try:
            fields["remind_minutes"] = int(args["remind_minutes"])
        except (TypeError, ValueError):
            pass
    if isinstance(args.get("done"), bool):
        fields["done"] = bool(args["done"])
    if not fields:
        return {"ok": False, "summary": "修改失败：没有给要改的字段"}
    store.update_event(event_id, **fields)
    merged = store.get_event(event_id) or current
    return {"ok": True, "id": event_id,
            "summary": "已修改：" + _fmt_event(merged)}


def schedule_delete(store, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        event_id = int(args.get("event_id") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "summary": "删除失败：event_id 不是数字"}
    current = store.get_event(event_id)
    if current is None:
        return {"ok": False, "summary": f"删除失败：没有 id={event_id} 的日程"}
    store.delete_event(event_id)
    return {"ok": True, "id": event_id,
            "summary": "已删除：" + _fmt_event(current)}


# ------------------------------------------------------------------ eLearning
def _elearning_client():
    """磁盘 cookie 建客户端（和「作业」页同一个来源）。"""
    from . import elearning

    cookies = {}
    try:
        cookies = elearning.read_cookies()
    except Exception:
        cookies = {}
    return elearning.ElearningClient(cookies)


def _read_courses_and_assignments(keyword: str = "") -> Dict[str, Any]:
    from . import elearning

    client = _elearning_client()
    if not client.has_cookies():
        return {"ok": False,
                "summary": ("还没有 eLearning 登录信息：先在「作业」页点右上角「看网页」"
                            "登录一次，之后我就能直接读课程了。")}
    courses = elearning.list_courses(client)
    if not courses:
        return {"ok": False, "summary": "没读到课程（登录可能过期了，重新登录一次 eLearning）"}
    assignments: List[Dict[str, Any]] = []
    for course in courses:
        try:
            assignments.extend(
                elearning.list_assignments(client, course["id"],
                                           str(course.get("name", ""))))
        except elearning.ElearningError:
            continue
    low = keyword.strip().lower()
    if low:
        courses = [item for item in courses
                   if low in str(item.get("name", "")).lower()
                   or any(low in str(a.get("name", "")).lower()
                          for a in assignments if a.get("course_id") == item.get("id"))]
        ids = {item.get("id") for item in courses}
        assignments = [item for item in assignments if item.get("course_id") in ids]
    return {"ok": True, "client": client, "courses": courses,
            "assignments": assignments}


def elearning_courses(store, args: Dict[str, Any]) -> Dict[str, Any]:
    keyword = str(args.get("keyword") or "")
    data = _read_courses_and_assignments(keyword)
    if not data.get("ok"):
        return data
    courses = data["courses"]
    assignments = data["assignments"]
    lines: List[str] = []
    for course in courses[:20]:
        lines.append(f"· {course.get('name', '')}")
        items = [a for a in assignments if a.get("course_id") == course.get("id")]
        for item in items[:6]:
            due = str(item.get("due_at") or "").replace("T", " ")[:16]
            lines.append(f"    - {item.get('name', '')}" + (f"（截止 {due}）" if due else ""))
        if len(items) > 6:
            lines.append(f"    - …还有 {len(items) - 6} 个作业")
    return {"ok": True,
            "summary": f"读到 {len(courses)} 门课程、{len(assignments)} 个作业",
            "count_courses": len(courses), "count_assignments": len(assignments),
            "text": "\n".join(lines)}


def elearning_download(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import elearning, settings_dirs

    keyword = str(args.get("keyword") or "").strip()
    try:
        limit = int(args.get("limit") or 40)
    except (TypeError, ValueError):
        limit = 40
    limit = max(1, min(200, limit))
    data = _read_courses_and_assignments(keyword)
    if not data.get("ok"):
        return data
    client = data["client"]
    courses = data["courses"]
    assignments = data["assignments"]
    course_names = {str(course.get("id")): str(course.get("name") or "")
                    for course in courses}

    items: List[Dict[str, Any]] = []
    seen = set()
    for course in courses:
        try:
            for file_item in elearning.list_files(client, course["id"]):
                url = file_item.get("url") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                items.append({"name": file_item["name"], "url": url,
                              "course": course.get("id"),
                              "course_name": course.get("name")})
        except Exception:
            continue
    for assignment in assignments:
        for attachment in assignment.get("attachments") or []:
            url = str(attachment.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            course_id = str(assignment.get("course_id"))
            items.append({"name": attachment.get("name") or "附件", "url": url,
                          "course": assignment.get("course_id"),
                          "course_name": course_names.get(course_id, ""),
                          "assignment": assignment.get("id")})
        if len(items) >= limit:
            break
    items = items[:limit]
    if not items:
        return {"ok": True, "summary": "没有找到可下载的文件（课程文件是空的或登录过期了）"}
    target = settings_dirs.downloads_dir(store)
    # 按课程分文件夹下载：下载目录/<课程名>/<文件名>
    result = elearning.download_by_course(client, items, target)
    saved = result.get("saved") or []
    skipped = result.get("skipped") or []
    failed = result.get("failed") or []
    return {"ok": True,
            "summary": (f"下载完成：新增 {len(saved)} 个、重名跳过 {len(skipped)} 个、"
                        f"失败 {len(failed)} 个 → {target}（按课程分了 "
                        f"{len(result.get('dirs') or [])} 个文件夹）"),
            "saved": len(saved), "skipped": len(skipped), "failed": len(failed),
            "target": str(target), "dirs": result.get("dirs") or [],
            "files": [str(n) for n in saved[:20]]}


# ------------------------------------------------------------------ 课表
def timetable_fetch(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import fdu, timetable

    semester = str(args.get("semester") or "").strip()
    term_start = str(args.get("term_start") or "").strip()
    cookies = {}
    try:
        cookies = fdu.read_cookies_from_disk()
    except Exception:
        cookies = {}
    if not cookies:
        return {"ok": False,
                "summary": ("还没登录教务：先在「设置 → 课程表」点「登录教务抓取课表」"
                            "登录一次，之后我就能自动抓课表了。")}
    try:
        _semester, payload = fdu.fetch_print_data(semester, cookies)
        activities = fdu.parse_print_data(payload)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"抓课表失败：{exc}"}
    if not activities:
        return {"ok": False, "summary": "教务返回的课表是空的（可能学期还没开始）"}
    if term_start:
        try:
            monday = fdu.first_monday_from(datetime.strptime(term_start, "%Y-%m-%d").date())
        except ValueError:
            monday = None
        if monday is None:
            return {"ok": False, "summary": f"看不懂学期第一天「{term_start}」，用 YYYY-MM-DD"}
    else:
        today = _today()
        monday = today - timedelta(days=today.weekday())
    rows = fdu.to_rows(activities, monday)
    if not rows:
        return {"ok": False, "summary": "解析出来 0 节课，可能学期日期不对"}
    group_id = fdu.ensure_course_group(store)
    added, skipped = fdu.import_rows(store, rows, group_id=group_id)
    store.set_setting("timetable_status", f"已导入 {added} 节课")
    return {"ok": True, "added": added, "skipped": skipped,
            "summary": f"课表已更新：新增 {added} 节、跳过重复 {skipped} 节"}


# ------------------------------------------------------------------ 本地资源
def fs_list(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import ai_local

    try:
        limit = int(args.get("limit") or 0) or 400
    except (TypeError, ValueError):
        limit = 400
    return ai_local.list_dir(store, str(args.get("path") or ""), limit=limit)


def fs_read(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import ai_local

    limit = args.get("limit")
    try:
        limit = int(limit) if limit not in (None, "") else None
    except (TypeError, ValueError):
        limit = None
    return ai_local.read_file(store, str(args.get("path") or ""), limit=limit)


def fs_search(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import ai_local

    try:
        limit = int(args.get("limit") or 0) or 50
    except (TypeError, ValueError):
        limit = 50
    return ai_local.search(store, str(args.get("keyword") or ""),
                           str(args.get("path") or ""), limit=limit)


def fs_write(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import ai_local

    return ai_local.write_file(store, str(args.get("path") or ""),
                               str(args.get("content") or ""),
                               append=bool(args.get("append")))


def fs_discard(store, args: Dict[str, Any]) -> Dict[str, Any]:
    """「删除文件」= 改名成 `[discarded] 原名`（用户明确要求的做法）。"""
    from . import ai_local

    return ai_local.discard(store, str(args.get("path") or ""))


# ------------------------------------------------------------------ 只读信息（A1–A6）
def time_now(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_info

    return agent_info.time_now()


def sys_info(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_info

    return agent_info.sys_info()


def app_state(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_info

    return agent_info.app_state(store)


def memo_search(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_info

    try:
        limit = int(args.get("limit") or 0) or 10
    except (TypeError, ValueError):
        limit = 10
    return agent_info.memo_search(store, str(args.get("keyword") or ""), limit=limit)


def usage_stats(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_info

    return agent_info.usage_stats(store)


def tool_help(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_info

    return agent_info.tool_help(store)


def window_list(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_info

    try:
        limit = int(args.get("limit") or 0) or 15
    except (TypeError, ValueError):
        limit = 15
    return agent_info.window_list(limit)


def memo_append(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_write

    return agent_write.memo_append(store, str(args.get("title") or ""),
                                   str(args.get("text") or ""))


def countdown_add(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_write

    return agent_write.countdown_add(store, str(args.get("title") or ""),
                                      str(args.get("day") or ""),
                                      str(args.get("note") or ""))


def flashcards_create(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_write

    cards = args.get("cards")
    if not isinstance(cards, list):
        cards = []
    return agent_write.flashcards_create(store, cards,
                                         str(args.get("source") or ""))


def pomodoro_start(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_write

    try:
        minutes = int(args.get("minutes") or 25)
    except (TypeError, ValueError):
        minutes = 25
    return agent_write.pomodoro_start(minutes, str(args.get("label") or ""))


def web_search(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_web

    try:
        limit = int(args.get("limit") or 0) or 8
    except (TypeError, ValueError):
        limit = 8
    return agent_web.web_search(store, str(args.get("query") or ""), limit)


def web_fetch(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_web

    try:
        limit = int(args.get("limit") or 0) or 12000
    except (TypeError, ValueError):
        limit = 12000
    return agent_web.web_fetch(store, str(args.get("url") or ""), limit)


def video_search(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_web

    try:
        limit = int(args.get("limit") or 0) or 8
    except (TypeError, ValueError):
        limit = 8
    return agent_web.video_search(store, str(args.get("query") or ""), limit)


def course_resource_search(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_web

    try:
        limit = int(args.get("limit") or 0) or 10
    except (TypeError, ValueError):
        limit = 10
    return agent_web.course_resource_search(store, str(args.get("keyword") or ""),
                                            limit)


def resource_push(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_web

    items = args.get("items")
    if not isinstance(items, list):
        items = []
    return agent_web.push_resources(store, items, str(args.get("kind") or "resource"))


# ------------------------------------------------------------------ D 组：文献
def _ids(args: Dict[str, Any]) -> List[int]:
    raw = args.get("ids")
    out: List[int] = []
    if isinstance(raw, list):
        for item in raw:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
    return out


def arxiv_search(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import literature

    try:
        limit = int(args.get("limit") or 0) or 8
    except (TypeError, ValueError):
        limit = 8
    return literature.search_arxiv(store, str(args.get("query") or ""), limit,
                                   str(args.get("project") or ""))


def crossref_search(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import literature

    try:
        limit = int(args.get("limit") or 0) or 8
    except (TypeError, ValueError):
        limit = 8
    return literature.search_crossref(store, str(args.get("query") or ""), limit,
                                      str(args.get("project") or ""))


def semantic_scholar_search(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import literature

    try:
        limit = int(args.get("limit") or 0) or 8
    except (TypeError, ValueError):
        limit = 8
    return literature.search_semantic(store, str(args.get("query") or ""), limit,
                                      str(args.get("project") or ""))


def doi_lookup(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import literature

    return literature.doi_lookup(store, str(args.get("doi") or ""),
                                 str(args.get("project") or ""))


def literature_search(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import literature

    try:
        limit = int(args.get("limit") or 0) or 12
    except (TypeError, ValueError):
        limit = 12
    return literature.search(store, str(args.get("query") or ""), limit,
                             str(args.get("project") or ""))


def pdf_download(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import literature

    return literature.download(store, _ids(args))


def citation_format(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import literature

    style = str(args.get("style") or "gbt7714")
    return literature.citations(store, _ids(args), style)


def endnote_export(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import literature

    style = str(args.get("style") or "enw")
    return literature.export(store, _ids(args), style)


def library_open(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_web

    return agent_web.library_open(store, str(args.get("query") or ""),
                                  str(args.get("which") or ""))


# ------------------------------------------------------------------ E 组
def mail_send(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_mail

    return agent_mail.mail_send(store, str(args.get("to") or ""),
                                str(args.get("subject") or ""),
                                str(args.get("text") or ""))


def setting_get(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_settings

    return agent_settings.setting_get(store, str(args.get("key") or ""))


def setting_set(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_settings

    return agent_settings.setting_set(store, str(args.get("key") or ""),
                                      args.get("value"))


def zip_extract(store, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import agent_zip

    return agent_zip.extract(store, str(args.get("archive") or ""),
                             str(args.get("dest") or ""))


# ------------------------------------------------------------------ 执行入口
_IMPL: Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]] = {
    "schedule_list": schedule_list,
    "schedule_add": schedule_add,
    "schedule_update": schedule_update,
    "schedule_delete": schedule_delete,
    "elearning_courses": elearning_courses,
    "elearning_download": elearning_download,
    "timetable_fetch": timetable_fetch,
    "fs_list": fs_list,
    "fs_read": fs_read,
    "fs_search": fs_search,
    "fs_write": fs_write,
    "fs_discard": fs_discard,
    "time_now": time_now,
    "sys_info": sys_info,
    "app_state": app_state,
    "memo_search": memo_search,
    "usage_stats": usage_stats,
    "tool_help": tool_help,
    "window_list": window_list,
    "memo_append": memo_append,
    "countdown_add": countdown_add,
    "flashcards_create": flashcards_create,
    "pomodoro_start": pomodoro_start,
    "web_search": web_search,
    "web_fetch": web_fetch,
    "video_search": video_search,
    "course_resource_search": course_resource_search,
    "resource_push": resource_push,
    "arxiv_search": arxiv_search,
    "crossref_search": crossref_search,
    "semantic_scholar_search": semantic_scholar_search,
    "doi_lookup": doi_lookup,
    "literature_search": literature_search,
    "pdf_download": pdf_download,
    "citation_format": citation_format,
    "endnote_export": endnote_export,
    "library_open": library_open,
    "mail_send": mail_send,
    "setting_get": setting_get,
    "setting_set": setting_set,
    "zip_extract": zip_extract,
}

# 本地工具 → 需要的权限档位
_LOCAL_NEEDS = {"fs_list": "read", "fs_read": "read", "fs_search": "read",
                "fs_write": "write"}

# ------------------------------------------------------------------ 工具清单（审核用）
# 每个工具都要让用户看得见、能单独关：设置 → AI 接口 → 「工具审核」。
RISK_LABELS = {"low": "低风险", "mid": "中风险", "high": "高风险"}
RISK_ORDER = {"low": 0, "mid": 1, "high": 2}

#: name, 中文名, 一句话作用, 风险档, 默认是否开启
_SPECS: List[Dict[str, Any]] = [
    {"name": "schedule_list", "label": "查日程", "risk": "low", "default": True,
     "desc": "查看某段时间的日程（只读应用内的日程表）"},
    {"name": "schedule_add", "label": "加日程", "risk": "low", "default": True,
     "desc": "新建一条日程"},
    {"name": "schedule_update", "label": "改日程", "risk": "low", "default": True,
     "desc": "修改已有日程"},
    {"name": "schedule_delete", "label": "删日程", "risk": "mid", "default": True,
     "desc": "删除日程（删了就没了）"},
    {"name": "elearning_courses", "label": "读 eLearning", "risk": "low",
     "default": True, "desc": "读课程与作业截止时间（需要已登录）"},
    {"name": "elearning_download", "label": "下载课程文件", "risk": "mid",
     "default": True, "desc": "把课程文件/作业附件下载到下载目录"},
    {"name": "timetable_fetch", "label": "抓课表", "risk": "mid", "default": True,
     "desc": "用教务登录态抓课表并写进日历"},
    {"name": "fs_list", "label": "看本地目录", "risk": "low", "default": True,
     "desc": "列出允许目录里的文件（只读）"},
    {"name": "fs_read", "label": "读本地文件", "risk": "low", "default": True,
     "desc": "读取允许目录里的文本文件（只读）"},
    {"name": "fs_search", "label": "搜本地文件", "risk": "low", "default": True,
     "desc": "在允许目录里按内容搜关键词（只读）"},
    {"name": "fs_write", "label": "写本地文件", "risk": "mid", "default": False,
     "desc": "新建/覆盖/追加文本文件（需要「读写」档）"},
    {"name": "fs_discard", "label": "丢弃文件（改名）", "risk": "mid",
     "default": False,
     "desc": "「删除」时不真删：改名成 [discarded] 原名，并告诉你去哪个目录彻底删"},
    {"name": "time_now", "label": "看时间", "risk": "low", "default": True,
     "desc": "返回精确日期/时间/星期/时区（只读）"},
    {"name": "sys_info", "label": "看电脑状况", "risk": "low", "default": True,
     "desc": "CPU/内存/磁盘/电池/系统版本/本机 IP（只读）"},
    {"name": "app_state", "label": "看应用状态", "risk": "low", "default": True,
     "desc": "今天日程、未读通知、邮件数、备忘数（只读）"},
    {"name": "memo_search", "label": "搜备忘", "risk": "low", "default": True,
     "desc": "在本地备忘里搜关键词（只读）"},
    {"name": "usage_stats", "label": "看用量", "risk": "low", "default": True,
     "desc": "本机 API 调用次数 / token / 时长（只读）"},
    {"name": "tool_help", "label": "查可用工具", "risk": "low", "default": True,
     "desc": "列出现在能用哪些工具（只读）"},
    {"name": "window_list", "label": "看窗口标题", "risk": "mid", "default": False,
     "desc": "列出当前可见窗口标题（暴露你开着什么，默认关）"},
    {"name": "memo_append", "label": "记到备忘", "risk": "low", "default": True,
     "desc": "往备忘里追加一段（没有同名就新建），只写应用内数据"},
    {"name": "countdown_add", "label": "加倒计时", "risk": "low", "default": True,
     "desc": "新建重要日倒计时（考试/截止日）"},
    {"name": "flashcards_create", "label": "生成复习卡片", "risk": "mid",
     "default": False,
     "desc": "把知识点做成间隔重复卡片（默认关）"},
    {"name": "pomodoro_start", "label": "开始专注", "risk": "low", "default": True,
     "desc": "启动应用里的专注计时器"},
    {"name": "web_search", "label": "上网搜索", "risk": "low", "default": True,
     "desc": "通用网页搜索（DDG 主、Bing 兜底）"},
    {"name": "web_fetch", "label": "读网页", "risk": "low", "default": True,
     "desc": "抓网页正文转纯文本（限长）"},
    {"name": "video_search", "label": "找课程视频", "risk": "low", "default": True,
     "desc": "找 B 站 / MOOC / YouTube 上的课程视频"},
    {"name": "course_resource_search", "label": "找课件资料", "risk": "low",
     "default": True,
     "desc": "找讲义/课件/习题：先翻 eLearning 课程文件，再上网搜"},
    {"name": "resource_push", "label": "推送资源", "risk": "low", "default": True,
     "desc": "把资源存进「学习资源」并推送成卡片"},
    {"name": "arxiv_search", "label": "搜 arXiv", "risk": "low", "default": True,
     "desc": "arXiv 检索（不用 Key）"},
    {"name": "crossref_search", "label": "搜 Crossref", "risk": "low",
     "default": True, "desc": "Crossref 检索（DOI/期刊/出版社）"},
    {"name": "semantic_scholar_search", "label": "搜 Semantic Scholar",
     "risk": "low", "default": True, "desc": "带引用数与摘要的检索"},
    {"name": "doi_lookup", "label": "按 DOI 查", "risk": "low", "default": True,
     "desc": "DOI 精确查元数据 + BibTeX"},
    {"name": "literature_search", "label": "搜文献", "risk": "low", "default": True,
     "desc": "三家一起搜并按 DOI/标题合并去重"},
    {"name": "pdf_download", "label": "下载文献 PDF", "risk": "mid",
     "default": True, "desc": "下载用户勾选的文献到「我的文献库」（每次都要你确认）"},
    {"name": "citation_format", "label": "生成引用", "risk": "low",
     "default": True, "desc": "GB/T 7714 / APA / BibTeX / RIS"},
    {"name": "endnote_export", "label": "导出给 EndNote", "risk": "low",
     "default": True, "desc": "导出 .enw/.ris；没装 EndNote 会先问你是否经 ehall 装"},
    {"name": "library_open", "label": "打开数据库", "risk": "low", "default": True,
     "desc": "打开图书馆/IEEE/ScienceDirect 搜索页（验证交给你）"},
    {"name": "mail_send", "label": "发邮件", "risk": "high", "default": False,
     "desc": "代发邮件：先弹可编辑窗口让你确认/修改，点发送才发"},
    {"name": "setting_get", "label": "读设置", "risk": "low", "default": True,
     "desc": "读白名单里允许修改的设置"},
    {"name": "setting_set", "label": "改设置", "risk": "high", "default": False,
     "desc": "改白名单设置（每次都要你确认；密码/账号一律不给改）"},
    {"name": "zip_extract", "label": "解压压缩包", "risk": "high", "default": False,
     "desc": "解压到工作区（防路径穿越，每次都要你确认）"},
]

# 高风险工具（目前还没实现，先留位置）：每次执行都必须用户确认
HIGH_RISK_NAMES = {"mail_send", "setting_set", "powershell_run", "screen_capture",
                   "zip_extract", "delete_file"}
# 每次都要确认的（用户要求：下载文件由他自己决定，别自动下）
_ALWAYS_CONFIRM = {"pdf_download", "endnote_export"}


def _spec(name: str) -> Optional[Dict[str, Any]]:
    for item in _SPECS:
        if item["name"] == name:
            return item
    return None


def _enabled_key(name: str) -> str:
    return "tool_on_" + str(name)


def default_on(name: str) -> bool:
    spec = _spec(name)
    if spec is None:
        return True
    if name in HIGH_RISK_NAMES:
        return False
    return bool(spec.get("default", True))


def risk_of(name: str) -> str:
    spec = _spec(name)
    if name in HIGH_RISK_NAMES:
        return "high"
    return str((spec or {}).get("risk") or "low")


def tool_enabled(store, name: str) -> bool:
    """这个工具开关是不是开着（没设过就用默认值）。"""
    try:
        value = store.get_setting(_enabled_key(name), "")
    except Exception:
        value = ""
    text = str(value or "").strip()
    if text in ("0", "1"):
        return text == "1"
    return default_on(name)


def set_tool_enabled(store, name: str, on: bool) -> bool:
    try:
        store.set_setting(_enabled_key(name), "1" if on else "0")
    except Exception:
        pass
    return bool(on)


def catalog(store=None) -> List[Dict[str, Any]]:
    """工具清单（设置页「工具审核」用）：名字、作用、风险、是否开启。"""
    items: List[Dict[str, Any]] = []
    for spec in _SPECS:
        name = spec["name"]
        enabled = tool_enabled(store, name) if store is not None else default_on(name)
        items.append({
            "name": name,
            "label": spec.get("label") or LABELS.get(name, name),
            "desc": spec.get("desc") or "",
            "risk": risk_of(name),
            "risk_label": RISK_LABELS.get(risk_of(name), "低风险"),
            "default": default_on(name),
            "enabled": bool(enabled),
        })
    return items


# ------------------------------------------------------------------ 用户确认
_CONFIRM_HANDLER: Optional[Callable[[Dict[str, Any]], str]] = None
_SESSION_OK: set = set()          # 本次会话已允许的 (工具, 目标)
_SESSION_AUTO = False             # 「自动执行」：本会话不再逐条确认（发信/下载/导出除外）
# 就算开了「自动执行」，这几个也一律要问：删文件、跑命令、截屏
_AUTO_ALWAYS_ASK = {"delete_file", "powershell_run", "screen_capture"}


def session_auto() -> bool:
    """「自动执行」开关的当前状态（界面上那个「需要确认 / 自动执行」）。"""
    return bool(_SESSION_AUTO)


def set_session_auto(on: bool) -> bool:
    global _SESSION_AUTO
    _SESSION_AUTO = bool(on)
    if _SESSION_AUTO:
        _SESSION_OK.clear()       # 自动执行时不需要记住逐条允许
    return _SESSION_AUTO


def set_confirm_handler(handler: Optional[Callable[[Dict[str, Any]], str]]) -> None:
    """装一个「问用户要不要允许」的回调（API 面板会装上）。

    回调收到 {"tool", "label", "target", "reason", "risk"}，
    返回 "allow"（本次）/ "session"（本次会话都允许）/ "deny"（拒绝）。
    """
    global _CONFIRM_HANDLER
    _CONFIRM_HANDLER = handler


def needs_confirm(store, name: str, args: Optional[Dict[str, Any]] = None
                  ) -> tuple:
    """这次调用要不要先问用户。返回 (要不要, 原因, 目标路径)。"""
    args = args or {}
    if name == "mail_send":
        # 发信自己会弹「可编辑 + 确认」的窗口（用户要求能改），不再叠加一层确认
        return False, "", str(args.get("to") or "")
    if (_SESSION_AUTO and name not in _ALWAYS_CONFIRM
            and name not in _AUTO_ALWAYS_ASK):
        # 用户切到「自动执行」：普通操作不再一条条问；
        # 下载/导出、删文件、跑命令、截屏这些仍然会问
        return False, "", str(args.get("path") or args.get("to") or "")
    if name in HIGH_RISK_NAMES:
        return True, "这是高风险操作", str(args.get("path") or args.get("to") or "")
    if name in _ALWAYS_CONFIRM:
        target = ""
        if isinstance(args.get("ids"), list):
            target = "文献 " + "、".join(str(item) for item in args["ids"][:10])
        elif args.get("path"):
            target = str(args["path"])
        return True, "下载 / 导出这类操作要先让你确认", target
    if name not in _LOCAL_NEEDS:
        return False, "", ""
    try:
        from . import ai_local

        if ai_local.mode(store) == "off":
            return False, "", ""
        target, error = ai_local.resolve(store, str(args.get("path") or ""),
                                         need_write=(name in ("fs_write",
                                                              "fs_discard")))
        if error or target is None:
            return False, "", ""            # 会直接报错，不用打扰用户
        if ai_local.is_direct(store, target):
            return False, "", str(target)
        return (True,
                "这个路径不在「本次工作直接访问」的目录里，需要你确认",
                str(target))
    except Exception:
        return False, "", ""


def _ask(store, name: str, target: str, reason: str) -> str:
    key = (name, target)
    if key in _SESSION_OK:
        return "allow"
    info = {
        "tool": name,
        "label": LABELS.get(name, name),
        "target": target,
        "reason": reason,
        "risk": risk_of(name),
    }
    handler = _CONFIRM_HANDLER
    if handler is None:
        return "deny"
    try:
        choice = str(handler(info) or "deny")
    except Exception:
        return "deny"
    if choice not in ("allow", "session", "deny"):
        choice = "deny"
    if choice == "session":
        _SESSION_OK.add(key)
    return choice


def clear_session_approvals() -> None:
    _SESSION_OK.clear()


def _run_with_pump(func, store, args, pump=None, timeout: float = 900.0) -> Dict[str, Any]:
    """慢工具放后台线程跑，界面继续刷新（不然下载时整个窗口会卡住）。"""
    box: Dict[str, Any] = {}

    def work() -> None:
        try:
            box["result"] = func(store, args)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=work, name="lumina-ai-tool", daemon=True)
    thread.start()
    deadline = time.time() + timeout
    while thread.is_alive() and time.time() < deadline:
        if pump is not None:
            try:
                pump()
            except Exception:
                pass
        time.sleep(0.03)
    if thread.is_alive():
        return {"ok": False, "summary": "这个操作太久了，已经放弃（可以稍后再试）"}
    if "error" in box:
        return {"ok": False, "summary": f"执行失败：{box['error']}"}
    return box.get("result") or {"ok": False, "summary": "工具没有返回结果"}


def execute(store, name: str, args: Optional[Dict[str, Any]] = None,
            pump: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    """执行一次工具调用。`pump` 给一个「让界面刷新」的回调（慢工具用）。"""
    func = _IMPL.get(str(name))
    if func is None:
        return {"ok": False, "summary": f"没有这个工具：{name}"}
    if not enabled(store):
        return {"ok": False, "summary": "应用内工具已在设置里关掉了"}
    if not tool_enabled(store, str(name)):
        return {"ok": False,
                "summary": f"「{LABELS.get(str(name), name)}」这个工具被你关掉了"
                           "（设置 → AI 接口 → 工具审核）"}
    # 本地文件工具：再挡一道（就算模型乱编工具名，也得过权限这关）
    need = _LOCAL_NEEDS.get(str(name))
    if need:
        from . import ai_local

        current = ai_local.mode(store)
        if current == "off":
            return {"ok": False,
                    "summary": "本地资源访问已关闭（设置 → AI 接口 → 本地资源）"}
        if need == "write" and current != "write":
            return {"ok": False,
                    "summary": "本地资源是「只读」档，不能写文件（要写请在设置里改成「读写」）"}
    # 工作区之外的路径 / 高风险工具：先问用户
    data = args if isinstance(args, dict) else {}
    ask, reason, target = needs_confirm(store, str(name), data)
    if ask:
        choice = _ask(store, str(name), target, reason)
        if choice == "deny":
            return {"ok": False,
                    "summary": f"用户没有允许这次操作（{reason}）"}
    data = args if isinstance(args, dict) else {}
    if name in SLOW_TOOLS:
        result = _run_with_pump(func, store, data, pump)
    else:
        try:
            result = func(store, data)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "summary": f"执行失败：{exc}"}
    if not isinstance(result, dict):
        result = {"ok": False, "summary": "工具返回了奇怪的结果"}
    result.setdefault("ok", False)
    result.setdefault("summary", "（没有说明）")
    return result


def enabled(store) -> bool:
    try:
        return (store.get_setting(TOOL_ENABLED_KEY, "1") or "1") != "0"
    except Exception:
        return True


def result_for_model(result: Dict[str, Any]) -> str:
    """给模型看的工具结果（压缩成 JSON，别撑爆上下文）。"""
    import json

    payload = {key: value for key, value in result.items()
               if key not in ("text",) or len(str(value)) < 2000}
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except Exception:
        text = str(result)
    return text[:4000]


def system_block(store=None) -> str:
    """拼进系统提示词，告诉模型「你可以直接动这个应用」。"""
    if store is not None and not enabled(store):
        return ""
    local_block = ""
    if store is not None:
        try:
            from . import ai_local

            local_block = ai_local.system_block(store)
        except Exception:
            local_block = ""
    base = (
        "你可以调用这个应用自带的工具来真的做事，而不只是回答：\n"
        "- 日程：schedule_list（查）、schedule_add（加）、schedule_update（改）、"
        "schedule_delete（删）。日期用 YYYY-MM-DD，时间用 HH:MM；"
        "用户说「明天下午三点」就先换算出具体日期时间再调用。\n"
        "- eLearning：elearning_courses 读课程与作业截止时间；elearning_download 把课程文件/作业附件"
        "下载到本机下载目录。\n"
        "- 课表：timetable_fetch 用教务登录态抓取本学期课表并写进日历。\n"
        "规则：\n"
        "1. 需要「真实数据」时先调工具再回答，不要凭空猜课表、作业或日程；\n"
        "2. 只有用户明确要求改动时才调用写操作（加/改/删日程、下载、抓课表）；\n"
        "3. 工具报错（比如没登录）就把原因原原本本告诉用户，并说清下一步该做什么；\n"
        "4. 工具返回的 id 要记住，后续改/删同一个日程时直接用。")
    if local_block:
        base += "\n\n" + local_block
    return base
