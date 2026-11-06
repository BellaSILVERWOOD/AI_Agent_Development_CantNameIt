"""C1–C5：联网找学习资源（用户逐个批准）。

* `web_search`            —— 通用网页搜索（DuckDuckGo 主、Bing 兜底）
* `web_fetch`             —— 抓网页正文转纯文本
* `video_search`          —— 课程视频（B 站 / 中国大学 MOOC / YouTube 优先）
* `course_resource_search`—— 讲义/课件/习题：**网页 + eLearning 课程文件**一起找
  （用户补充要求：先去 eLearning 翻翻课程文件里有没有课件）
* `resource_push`         —— 把找到的资源存进「学习资源」并推送成卡片

为了让测试不依赖外网，搜索/抓取函数是模块级可替换的（`SEARCH` / `FETCH`）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from . import websearch

SEARCH: Callable[..., Dict[str, Any]] = websearch.search
FETCH: Callable[..., Dict[str, Any]] = websearch.fetch_text

#: 主窗口注册的「在应用内打开网页」钩子（library_open 用）
_OPEN_HANDLER = None


def set_open_handler(handler) -> None:
    """主窗口调用：`handler(url) -> bool`（在应用内的浏览器里打开）。"""
    global _OPEN_HANDLER
    _OPEN_HANDLER = handler


def open_in_app(url: str) -> bool:
    handler = _OPEN_HANDLER
    if handler is None:
        return False
    try:
        return bool(handler(str(url or "")))
    except Exception:
        return False

VIDEO_SITES = [
    {"name": "哔哩哔哩", "host": "bilibili.com", "query": "site:bilibili.com"},
    {"name": "中国大学MOOC", "host": "icourse163.org", "query": "site:icourse163.org"},
    {"name": "YouTube", "host": "youtube.com", "query": "site:youtube.com"},
]
DOC_HINTS = ("讲义", "课件", "ppt", "pdf", "习题", "答案", "笔记", "教材")


# ------------------------------------------------------------------ C1 / C2
def web_search(store, query: str, limit: int = 8) -> Dict[str, Any]:
    text = str(query or "").strip()
    if not text:
        return {"ok": False, "results": [], "summary": "要给个关键词"}
    result = SEARCH(text, limit=max(1, min(20, int(limit or 8))))
    if not isinstance(result, dict):
        return {"ok": False, "results": [], "summary": "搜索没有返回结果"}
    results = result.get("results") or []
    for item in results:
        item["platform"] = platform_of(item.get("url") or "")
    return {"ok": bool(result.get("ok")), "engine": result.get("engine") or "",
            "query": text, "results": results, "count": len(results),
            "summary": result.get("summary") or f"搜到 {len(results)} 条"}


def web_fetch(store, url: str, limit: int = 12000) -> Dict[str, Any]:
    text = str(url or "").strip()
    if not text:
        return {"ok": False, "text": "", "summary": "要给个网址"}
    result = FETCH(text, limit=max(1000, min(20000, int(limit or 12000))))
    if not isinstance(result, dict):
        return {"ok": False, "text": "", "summary": "抓取没有返回结果"}
    return result


def platform_of(url: str) -> str:
    """按域名认出平台名（给界面显示小标签用）。"""
    try:
        host = urlparse(str(url or "")).netloc.lower()
    except Exception:
        return ""
    table = [
        ("bilibili.com", "哔哩哔哩"),
        ("b23.tv", "哔哩哔哩"),
        ("icourse163.org", "中国大学MOOC"),
        ("youtube.com", "YouTube"),
        ("youtu.be", "YouTube"),
        ("mooc1.chaoxing.com", "超星"),
        ("chaoxing.com", "超星"),
        ("zhihu.com", "知乎"),
        ("csdn.net", "CSDN"),
        ("github.com", "GitHub"),
        ("arxiv.org", "arXiv"),
        ("cnki.net", "知网"),
    ]
    for suffix, name in table:
        if host.endswith(suffix):
            return name
    return host[4:] if host.startswith("www.") else host


# ------------------------------------------------------------------ C3 视频
def video_search(store, query: str, limit: int = 8,
                 sites: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    text = str(query or "").strip()
    if not text:
        return {"ok": False, "results": [], "summary": "要给个关键词"}
    cap = max(1, min(20, int(limit or 8)))
    chosen = sites or VIDEO_SITES
    merged: List[Dict[str, Any]] = []
    seen = set()
    errors: List[str] = []
    per_site = max(1, cap // max(1, len(chosen)) + 1)
    for site in chosen:
        result = SEARCH(f"{text} {site['query']}", limit=per_site)
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        for item in result.get("results") or []:
            url = str(item.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append({"title": str(item.get("title") or ""),
                           "url": url,
                           "snippet": str(item.get("snippet") or ""),
                           "platform": site["name"]})
            if len(merged) >= cap:
                break
        if len(merged) >= cap:
            break
    if not merged:
        return {"ok": False, "results": [], "summary": "没找到课程视频（换个关键词试试）"}
    return {"ok": True, "query": text, "results": merged, "count": len(merged),
            "errors": errors,
            "summary": f"找到 {len(merged)} 个课程视频（"
                       + "、".join(sorted({item["platform"] for item in merged}))
                       + "）"}


# ------------------------------------------------------------------ C4 课件 / 资料
def elearning_files(store, keyword: str = "", limit: int = 12) -> List[Dict[str, Any]]:
    """在 eLearning 的课程文件里找（用户补充要求）。

    登录态不足 / 没网时返回空列表（不让整个工具失败）。
    """
    from . import ai_tools, elearning

    try:
        data = ai_tools._read_courses_and_assignments(keyword)
    except Exception:
        return []
    if not data.get("ok"):
        return []
    client = data.get("client")
    found: List[Dict[str, Any]] = []
    low = str(keyword or "").strip().lower()
    for course in (data.get("courses") or [])[:12]:
        try:
            files = elearning.list_files(client, course["id"])
        except Exception:
            continue
        for item in files or []:
            name = str(item.get("name") or "")
            url = str(item.get("url") or "")
            if not name or not url:
                continue
            if low and low not in name.lower() and low not in str(
                    course.get("name", "")).lower():
                continue
            found.append({"title": name, "url": url, "platform": "eLearning",
                          "snippet": f"{course.get('name', '')} 的课程文件",
                          "local": True})
            if len(found) >= max(1, int(limit or 12)):
                return found
    return found


def course_resource_search(store, keyword: str, limit: int = 10) -> Dict[str, Any]:
    """讲义 / 课件 / 习题：先翻 eLearning 课程文件，再上网搜。"""
    text = str(keyword or "").strip()
    if not text:
        return {"ok": False, "results": [], "summary": "要给个课程名或关键词"}
    cap = max(1, min(20, int(limit or 10)))
    results: List[Dict[str, Any]] = []
    seen = set()
    for item in elearning_files(store, text, cap):
        results.append(item)
        seen.add(item["url"])
    hint = " ".join(DOC_HINTS[:4])
    query = f"{text} 讲义 课件 习题"
    web = SEARCH(query, limit=cap)
    if isinstance(web, dict) and web.get("ok"):
        for item in web.get("results") or []:
            url = str(item.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            results.append({"title": str(item.get("title") or ""), "url": url,
                            "snippet": str(item.get("snippet") or ""),
                            "platform": platform_of(url), "local": False})
            if len(results) >= cap:
                break
    if not results:
        return {"ok": False, "results": [],
                "summary": f"没找到「{text}」的讲义/课件（eLearning 里没有，网上也没有）"}
    local_count = sum(1 for item in results if item.get("local"))
    return {"ok": True, "query": text, "results": results, "count": len(results),
            "from_elearning": local_count,
            "summary": (f"找到 {len(results)} 份资料（其中 {local_count} 份就在 "
                        f"eLearning 你的课程文件里）" + f"；提示词 {hint}"[:0])}


# ------------------------------------------------------------------ C5 推送 / 收藏
def ensure_resources(store) -> None:
    try:
        store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT '',
                snippet TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                added_at TEXT NOT NULL,
                UNIQUE(url)
            );
            """
        )
        store.conn.commit()
    except Exception:
        pass


def push_resources(store, items: List[Dict[str, Any]],
                   kind: str = "resource") -> Dict[str, Any]:
    ensure_resources(store)
    saved: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title and not url:
            continue
        try:
            store.conn.execute(
                "INSERT INTO resources(title, url, platform, kind, snippet, added_at)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(url) DO UPDATE SET title=excluded.title,"
                " snippet=excluded.snippet, platform=excluded.platform",
                (title[:300], url[:600], str(item.get("platform") or "")[:60],
                 str(kind or "resource")[:40], str(item.get("snippet") or "")[:600],
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            saved.append({"title": title, "url": url,
                          "platform": item.get("platform") or ""})
        except Exception:
            continue
    try:
        store.conn.commit()
    except Exception:
        pass
    if not saved:
        return {"ok": False, "summary": "没有可推送的资源"}
    return {"ok": True, "cards": saved, "count": len(saved),
            "summary": (f"已推送 {len(saved)} 个资源（都存进「学习资源」了，"
                        f"可以在左侧边栏里回看）")}


def list_resources(store, query: str = "", kind: str = "",
                   limit: int = 200) -> List[Dict[str, Any]]:
    ensure_resources(store)
    sql = "SELECT * FROM resources WHERE 1=1"
    params: List[Any] = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if query:
        like = f"%{query}%"
        sql += " AND (title LIKE ? OR snippet LIKE ? OR platform LIKE ?)"
        params.extend([like, like, like])
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    try:
        rows = store.conn.execute(sql, params).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def remove_resource(store, resource_id: int) -> bool:
    ensure_resources(store)
    try:
        store.conn.execute("DELETE FROM resources WHERE id=?", (int(resource_id),))
        store.conn.commit()
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ D5 打开数据库
def library_open(store, query: str = "", which: str = "") -> Dict[str, Any]:
    """在应用内的浏览器里打开图书馆 / 数据库搜索页（验证码由用户自己过）。"""
    from . import lit

    links = lit.database_links(query)
    target = None
    name = str(which or "").strip().lower()
    if name:
        for item in links:
            if name in item["id"].lower() or name in item["name"].lower():
                target = item
                break
    if target is None:
        target = links[0]
    opened = open_in_app(target["url"])
    if not opened:
        return {"ok": False, "url": target["url"],
                "summary": (f"要在应用里打开「{target['name']}」需要主界面窗口；"
                            f"也可以自己点这个链接：{target['url']}")}
    need = "（这个库要登录/验证码，页面已经给你，过了验证就能用）" if \
        target["needs_login"] else ""
    return {"ok": True, "url": target["url"], "name": target["name"],
            "links": links,
            "summary": f"已在应用里打开「{target['name']}」{need}"}
