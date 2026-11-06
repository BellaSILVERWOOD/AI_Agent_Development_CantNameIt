"""文献库：检索（arXiv / Crossref / Semantic Scholar）、下载、引用格式、目录归类。

第 3–6 步的地基，全部是「纯数据 + 网络」的活，不依赖界：

* `search_all(query)`：三家公开接口一起搜，按 DOI / 标题去重合并；
  arXiv 不用 Key、Crossref 不用 Key、Semantic Scholar 也不用 Key（有免费额度）；
* `download_pdf(store, paper_id)`：把 PDF 存进文献库目录（按项目/年份分文件夹）；
* 引用格式：GB/T 7714、APA、BibTeX、RIS（EndNote 也能直接吃 RIS）、EndNote(.enw)；
* IEEE / ScienceDirect / 图书馆这些**要登录或人机验证**的库，只生成搜索链接，
  由界面把页面交给用户（`database_links`），不在这里偷偷爬。

表结构：
    papers(id, source, source_id, doi, title, authors, year, venue, abstract,
           url, pdf_url, project, folder, file_path, added_at, note)
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse

from . import websearch

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
LIBRARY_HOME = "https://library.fudan.edu.cn/"
UA = websearch.UA
TIMEOUT = 12.0
MAX_RESULTS = 25

DB_LINKS = [
    {"id": "library", "name": "复旦图书馆", "url": LIBRARY_HOME,
     "needs_login": True},
    {"id": "arxiv", "name": "arXiv", "url": "https://arxiv.org/search/?query={q}&searchtype=all",
     "needs_login": False},
    {"id": "ieee", "name": "IEEE Xplore",
     "url": "https://ieeexplore.ieee.org/search/searchresult.jsp?queryText={q}",
     "needs_login": True},
    {"id": "sciencedirect", "name": "ScienceDirect",
     "url": "https://www.sciencedirect.com/search?qs={q}", "needs_login": True},
    {"id": "scholar", "name": "Google 学术",
     "url": "https://scholar.google.com/scholar?q={q}", "needs_login": False},
    {"id": "cnki", "name": "中国知网",
     "url": "https://kns.cnki.net/kns8s/defaultresult/index?kw={q}",
     "needs_login": True},
]


# ------------------------------------------------------------------ 存储
def ensure_schema(store) -> None:
    try:
        store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                doi TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                authors TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                venue TEXT NOT NULL DEFAULT '',
                abstract TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                pdf_url TEXT NOT NULL DEFAULT '',
                project TEXT NOT NULL DEFAULT '',
                folder TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                added_at TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                UNIQUE(source, source_id)
            );
            """
        )
        store.conn.commit()
    except Exception:
        pass
    # 新版多一列「中文概述」：老的库升级时补上。
    # 注意这段要独立 try：老库可能缺别的列，别让建索引的失败挡住补列。
    try:
        columns = [row[1] for row in
                   store.conn.execute("PRAGMA table_info(papers)").fetchall()]
        if columns and "summary_zh" not in columns:
            store.conn.execute(
                "ALTER TABLE papers ADD COLUMN summary_zh TEXT NOT NULL DEFAULT ''")
            store.conn.commit()
        # 星级（0–5）：用来分组和筛选，「一眼挑出想看的」
        if columns and "stars" not in columns:
            store.conn.execute(
                "ALTER TABLE papers ADD COLUMN stars INTEGER NOT NULL DEFAULT 0")
            store.conn.commit()
    except Exception:
        pass
    try:
        store.conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
            CREATE INDEX IF NOT EXISTS idx_papers_project ON papers(project);
            """
        )
        store.conn.commit()
    except Exception:
        pass


# ------------------------------------------------------------------ 分页 / 条数
PAGE_SIZE = 10          # 一页 10 条
MAX_LIMIT = 50          # 一次搜索最多 50 条（除非用户在问题里写了具体数字）


def parse_requested_limit(query: str, default: int = PAGE_SIZE,
                          maximum: int = MAX_LIMIT) -> int:
    """从查询里认出用户要几条：「20 篇」「前 15 条」「top 30」「limit 25」。

    认不出来就用默认（一页 10 条）；认出超过上限的按上限算。
    """
    text = str(query or "")
    lowered = text.lower()
    candidates = []
    for pattern in (r"(\d+)\s*(?:篇|条|个结果|个文献|个|results?|papers?|records?)",
                    r"(?:前|取|要|top|limit\s*[=:：]?)\s*(\d+)",
                    r"\btop\s*(\d+)\b"):
        for match in re.finditer(pattern, lowered):
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if 0 < value:
                candidates.append(value)
    if not candidates:
        return max(1, min(maximum, int(default)))
    # 取最小的那个：写「前 20 篇里的 10 条」这种话时，别一次拉 20 条
    return max(1, min(maximum, min(candidates)))


def save_summary(store, paper_id: int, text: str) -> bool:
    """把中文概述缓存进库里（下次不用再花 token）。"""
    try:
        store.conn.execute("UPDATE papers SET summary_zh = ? WHERE id = ?",
                           (str(text or ""), int(paper_id)))
        store.conn.commit()
        return True
    except Exception:
        return False


def set_stars(store, paper_id: int, stars: int) -> int:
    """给一篇文献打星（0–5），返回存进去的值。"""
    value = max(0, min(5, int(stars or 0)))
    try:
        store.conn.execute("UPDATE papers SET stars = ? WHERE id = ?",
                           (value, int(paper_id)))
        store.conn.commit()
    except Exception:
        return 0
    return value


def set_stars_many(store, paper_ids, stars: int) -> int:
    """批量打星，返回影响条数。"""
    value = max(0, min(5, int(stars or 0)))
    count = 0
    for paper_id in paper_ids or []:
        try:
            store.conn.execute("UPDATE papers SET stars = ? WHERE id = ?",
                               (value, int(paper_id)))
            count += 1
        except Exception:
            continue
    try:
        store.conn.commit()
    except Exception:
        pass
    return count


SUMMARY_PROMPT = (
    "请用中文（120 字以内）说明这篇文献：它做了什么、用了什么方法、主要结论是什么、"
    "有什么值得注意的特色或局限。只输出这段说明，不要客套话、不要分点、不要复述英文摘要。\n\n"
    "标题：{title}\n\n摘要：{abstract}\n"
)


def summarize_zh(store, paper_id: int, timeout: float = 90.0,
                 force: bool = False) -> Dict[str, Any]:
    """用配置好的模型给这篇文献写一段中文概述（有缓存就直接用）。

    `force=True` 表示「重新生成」：忽略缓存，重新问一次模型。
    """
    item = get_paper(store, paper_id)
    if not item:
        return {"ok": False, "summary": "没有这篇文献", "text": ""}
    cached = str(item.get("summary_zh") or "").strip()
    if cached and not force:
        return {"ok": True, "text": cached, "cached": True, "summary": "已用缓存"}
    title = str(item.get("title") or "").strip()
    abstract = str(item.get("abstract") or "").strip()
    if not title and not abstract:
        return {"ok": False, "text": "", "summary": "这篇没有标题也没有摘要，没法概述"}
    try:
        from . import ai_chat, prompts
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": "", "summary": f"模块加载失败：{exc}"}
    if not ai_chat.api_ready(store):
        return {"ok": False, "text": "",
                "summary": "还没配置模型接口（设置 → AI 接口），没法写中文概述"}
    prompt = SUMMARY_PROMPT.format(title=title or "（无标题）",
                                   abstract=(abstract or "（没有摘要，请只根据标题推断）")[:4000])
    messages = [{"role": "system", "content": prompts.system("assistant")},
                {"role": "user", "content": prompt}]
    try:
        reply = ai_chat.chat(store, messages, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": "", "summary": f"生成失败：{exc}"}
    text = _tidy(str(reply or ""))
    if not text:
        return {"ok": False, "text": "", "summary": "模型返回了空内容"}
    text = text[:600]
    save_summary(store, paper_id, text)
    return {"ok": True, "text": text, "cached": False,
            "summary": "已生成中文概述（会存进库里）"}



def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def library_root() -> Path:
    from .config import DATA_DIR

    path = DATA_DIR / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize(name: str, limit: int = 60) -> str:
    """把标题/项目名变成安全的文件夹名。"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(name or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return (text[:limit] or "未命名").strip(" .")


def folder_for(project: str = "", year: str = "") -> Path:
    """文献库目录：`library/<项目或未分类>/<年份或未知年份>`。"""
    base = library_root()
    project_dir = base / sanitize(project or "未分类")
    year_dir = project_dir / sanitize(year or "未知年份", 12)
    year_dir.mkdir(parents=True, exist_ok=True)
    return year_dir


def _row_to_item(row) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


def add_paper(store, item: Dict[str, Any], project: str = "") -> Dict[str, Any]:
    """写一条文献（同来源同 id 就更新）。返回落库后的记录。"""
    ensure_schema(store)
    source = str(item.get("source") or "manual")
    source_id = str(item.get("source_id") or item.get("doi") or item.get("url")
                    or item.get("title") or "")
    if not source_id:
        return {}
    fields = {
        "source": source,
        "source_id": source_id[:300],
        "doi": str(item.get("doi") or "")[:200],
        "title": str(item.get("title") or "")[:500],
        "authors": str(item.get("authors") or "")[:500],
        "year": str(item.get("year") or "")[:16],
        "venue": str(item.get("venue") or "")[:300],
        "abstract": str(item.get("abstract") or "")[:4000],
        "url": str(item.get("url") or "")[:600],
        "pdf_url": str(item.get("pdf_url") or "")[:600],
        "project": str(item.get("project") or project or "")[:120],
        "note": str(item.get("note") or "")[:1000],
        "added_at": _now(),
    }
    try:
        store.conn.execute(
            "INSERT INTO papers(source, source_id, doi, title, authors, year, venue,"
            " abstract, url, pdf_url, project, note, added_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(source, source_id) DO UPDATE SET"
            " doi=excluded.doi, title=excluded.title, authors=excluded.authors,"
            " year=excluded.year, venue=excluded.venue, abstract=excluded.abstract,"
            " url=excluded.url, pdf_url=excluded.pdf_url,"
            " project=COALESCE(NULLIF(excluded.project,''), papers.project),"
            " note=COALESCE(NULLIF(excluded.note,''), papers.note)",
            (fields["source"], fields["source_id"], fields["doi"], fields["title"],
             fields["authors"], fields["year"], fields["venue"], fields["abstract"],
             fields["url"], fields["pdf_url"], fields["project"], fields["note"],
             fields["added_at"]))
        store.conn.commit()
    except Exception:
        return {}
    row = store.conn.execute(
        "SELECT * FROM papers WHERE source=? AND source_id=?",
        (fields["source"], fields["source_id"])).fetchone()
    return _row_to_item(row)


def get_paper(store, paper_id: int) -> Dict[str, Any]:
    ensure_schema(store)
    row = store.conn.execute("SELECT * FROM papers WHERE id=?",
                             (int(paper_id),)).fetchone()
    return _row_to_item(row)


def list_papers(store, query: str = "", project: str = "",
                limit: int = 300) -> List[Dict[str, Any]]:
    ensure_schema(store)
    sql = "SELECT * FROM papers WHERE 1=1"
    params: List[Any] = []
    if project:
        sql += " AND project = ?"
        params.append(str(project))
    if query:
        like = f"%{query}%"
        sql += (" AND (title LIKE ? OR authors LIKE ? OR venue LIKE ?"
                " OR abstract LIKE ? OR doi LIKE ?)")
        params.extend([like] * 5)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    try:
        rows = store.conn.execute(sql, params).fetchall()
    except Exception:
        return []
    return [_row_to_item(row) for row in rows]


def projects(store) -> List[str]:
    ensure_schema(store)
    try:
        rows = store.conn.execute(
            "SELECT DISTINCT project FROM papers WHERE project <> ''"
            " ORDER BY project").fetchall()
    except Exception:
        return []
    return [str(row[0]) for row in rows]


def set_project(store, paper_id: int, project: str) -> bool:
    ensure_schema(store)
    try:
        store.conn.execute("UPDATE papers SET project=? WHERE id=?",
                           (str(project or "")[:120], int(paper_id)))
        store.conn.commit()
        return True
    except Exception:
        return False


def remove_paper(store, paper_id: int, discard_file: bool = False) -> Dict[str, Any]:
    """从库里移除一条（文件默认不动；要动就走「改名成 [discarded]」）。"""
    ensure_schema(store)
    item = get_paper(store, paper_id)
    if not item:
        return {"ok": False, "summary": "没有这篇文献"}
    note = ""
    file_path = str(item.get("file_path") or "")
    if discard_file and file_path:
        target = Path(file_path)
        if target.exists():
            try:
                target.rename(target.with_name("[discarded] " + target.name))
                note = f"；PDF 已改名为 [discarded] {target.name}"
            except Exception:
                note = "；PDF 改名失败（可能被占用）"
    try:
        store.conn.execute("DELETE FROM papers WHERE id=?", (int(paper_id),))
        store.conn.commit()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"移除失败：{exc}"}
    return {"ok": True, "summary": f"已从文献库移除《{item.get('title','')}》{note}"}


def save_note(store, paper_id: int, note: str) -> bool:
    ensure_schema(store)
    try:
        store.conn.execute("UPDATE papers SET note=? WHERE id=?",
                           (str(note or "")[:1000], int(paper_id)))
        store.conn.commit()
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 检索
def _get_json(url: str, params: Optional[Dict[str, Any]] = None,
              timeout: float = TIMEOUT) -> Tuple[Any, str]:
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return None, f"缺少 requests：{exc}"
    try:
        response = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": UA})
    except Exception as exc:  # noqa: BLE001
        return None, f"连不上：{exc}"
    if response.status_code >= 400:
        return None, f"HTTP {response.status_code}"
    try:
        return response.json(), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"返回的不是 JSON：{exc}"


def _get_text(url: str, params: Optional[Dict[str, Any]] = None,
              timeout: float = TIMEOUT) -> Tuple[str, str]:
    return websearch._get(url, params, timeout=timeout)


def parse_arxiv(xml: str, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    """解析 arXiv 的 Atom 返回。"""
    items: List[Dict[str, Any]] = []
    for entry in re.findall(r"<entry>(.*?)</entry>", str(xml or ""), re.S):
        title = re.search(r"<title>(.*?)</title>", entry, re.S)
        summary = re.search(r"<summary>(.*?)</summary>", entry, re.S)
        published = re.search(r"<published>(.*?)</published>", entry, re.S)
        ident = re.search(r"<id>(.*?)</id>", entry, re.S)
        authors = re.findall(r"<author>\s*<name>(.*?)</name>", entry, re.S)
        pdf = re.search(r'<link[^>]*title="pdf"[^>]*href="([^"]+)"', entry)
        doi = re.search(r"<arxiv:doi[^>]*>(.*?)</arxiv:doi>", entry, re.S)
        url = (ident.group(1).strip() if ident else "")
        year = ""
        if published:
            match = re.match(r"(\d{4})", published.group(1).strip())
            year = match.group(1) if match else ""
        items.append({
            "source": "arxiv",
            "source_id": url.rsplit("/", 1)[-1] if url else (title.group(1) if title else ""),
            "doi": (doi.group(1).strip() if doi else ""),
            "title": _tidy(title.group(1) if title else ""),
            "authors": ", ".join(_tidy(name) for name in authors[:8]),
            "year": year,
            "venue": "arXiv",
            "abstract": _tidy(summary.group(1) if summary else "")[:3000],
            "url": url,
            "pdf_url": pdf.group(1) if pdf else "",
        })
        if len(items) >= int(limit):
            break
    return items


def _tidy(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return (value.replace("&amp;", "&").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&quot;", '"'))


def parse_crossref(payload: Any, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    message = (payload or {}).get("message") or {}
    for entry in (message.get("items") or [])[:int(limit)]:
        titles = entry.get("title") or []
        authors = []
        for person in entry.get("author") or []:
            name = " ".join(part for part in (person.get("given"),
                                              person.get("family")) if part)
            if name:
                authors.append(name)
        year = ""
        issued = (entry.get("issued") or {}).get("date-parts") or [[None]]
        if issued and issued[0] and issued[0][0]:
            year = str(issued[0][0])
        items.append({
            "source": "crossref",
            "source_id": str(entry.get("DOI") or entry.get("URL") or ""),
            "doi": str(entry.get("DOI") or ""),
            "title": _tidy(titles[0] if titles else ""),
            "authors": ", ".join(authors[:8]),
            "year": year,
            "venue": _tidy((entry.get("container-title") or [""])[0]),
            "abstract": _tidy(entry.get("abstract") or "")[:3000],
            "url": str(entry.get("URL") or ""),
            "pdf_url": "",
        })
    return [item for item in items if item["title"]]


def parse_semantic(payload: Any, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for entry in ((payload or {}).get("data") or [])[:int(limit)]:
        externals = entry.get("externalIds") or {}
        pdf = (entry.get("openAccessPdf") or {}).get("url") or ""
        items.append({
            "source": "semantic",
            "source_id": str(entry.get("paperId") or externals.get("DOI") or ""),
            "doi": str(externals.get("DOI") or ""),
            "title": _tidy(entry.get("title") or ""),
            "authors": ", ".join(str(author.get("name") or "")
                                 for author in (entry.get("authors") or [])[:8]),
            "year": str(entry.get("year") or ""),
            "venue": _tidy(entry.get("venue") or ""),
            "abstract": _tidy(entry.get("abstract") or "")[:3000],
            "url": ("https://doi.org/" + str(externals.get("DOI"))
                    if externals.get("DOI") else
                    ("https://www.semanticscholar.org/paper/" + str(entry.get("paperId") or ""))),
            "pdf_url": str(pdf),
        })
    return [item for item in items if item["title"]]


def _merge(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 DOI **或** 标题去重（同一篇在 arXiv 和出版方都出现时合成一条）。

    DOI 和标题两个索引都要建：arXiv 常常没 DOI，只靠标题也能对上。
    合并时优先保留「更像正式出版信息」的字段（arXiv 的 venue 会被刊物名替换）。
    """
    merged: List[Dict[str, Any]] = []
    by_key: Dict[str, int] = {}

    def title_key(text: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "",
                      str(text or "").lower())[:80]

    for item in items:
        if not item.get("title"):
            continue
        doi = str(item.get("doi") or "").strip().lower()
        tkey = title_key(item.get("title", ""))
        index = None
        if doi and doi in by_key:
            index = by_key[doi]
        elif tkey and tkey in by_key:
            index = by_key[tkey]
        if index is not None:
            target = merged[index]
            for field in ("doi", "abstract", "pdf_url", "venue", "authors", "year",
                          "url"):
                if not target.get(field) and item.get(field):
                    target[field] = item[field]
            # arXiv 的「期刊名」只是占位，遇到真刊物名就换掉
            if str(target.get("venue") or "").strip().lower() in ("", "arxiv"):
                venue = str(item.get("venue") or "").strip()
                if venue and venue.lower() != "arxiv":
                    target["venue"] = venue
            if doi:
                by_key.setdefault(doi, index)
            if tkey:
                by_key.setdefault(tkey, index)
            continue
        position = len(merged)
        merged.append(dict(item))
        if doi:
            by_key.setdefault(doi, position)
        if tkey:
            by_key.setdefault(tkey, position)
    return merged


def search_all(query: str, limit: int = PAGE_SIZE, timeout: float = TIMEOUT,
               with_semantic: bool = True) -> Dict[str, Any]:
    """arXiv + Crossref（+ Semantic Scholar）一起搜，按 DOI/标题合并。

    默认一页 10 条；`limit` 最多 50（要更多先在查询里写清楚，比如「30 篇」）。
    """
    text = str(query or "").strip()
    if not text:
        return {"ok": False, "results": [], "errors": ["关键词是空的"]}
    cap = max(1, min(MAX_LIMIT, int(limit or PAGE_SIZE)))
    errors: List[str] = []
    found: List[Dict[str, Any]] = []

    xml, error = _get_text(ARXIV_API, {"search_query": f"all:{text}",
                                       "start": "0", "max_results": str(cap)},
                           timeout=timeout)
    if xml:
        try:
            found.extend(parse_arxiv(xml, cap))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"arXiv 解析失败：{exc}")
    else:
        errors.append(f"arXiv：{error}")

    payload, error = _get_json(CROSSREF_API, {"query": text, "rows": str(cap)},
                               timeout=timeout)
    if payload is not None:
        try:
            found.extend(parse_crossref(payload, cap))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Crossref 解析失败：{exc}")
    else:
        errors.append(f"Crossref：{error}")

    if with_semantic:
        payload, error = _get_json(S2_API, {
            "query": text, "limit": str(cap),
            "fields": "title,abstract,year,venue,authors,externalIds,openAccessPdf,paperId",
        }, timeout=timeout)
        if payload is not None:
            try:
                found.extend(parse_semantic(payload, cap))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Semantic Scholar 解析失败：{exc}")
        else:
            errors.append(f"Semantic Scholar：{error}")

    merged = _merge(found)[:cap]
    return {"ok": bool(merged), "query": text, "results": merged,
            "errors": errors,
            "summary": (f"找到 {len(merged)} 篇相关文献"
                        + (f"（{len(errors)} 个来源没连上）" if errors else ""))
            if merged else ("没搜到文献" + (f"：{'；'.join(errors)}" if errors else ""))}


def database_links(query: str = "") -> List[Dict[str, Any]]:
    """需要登录/验证的数据库：给界面用它自己的内嵌浏览器打开。"""
    text = quote_plus(str(query or "").strip())
    out: List[Dict[str, Any]] = []
    for item in DB_LINKS:
        url = item["url"].replace("{q}", text) if text else item["url"].split("?")[0]
        out.append({"id": item["id"], "name": item["name"], "url": url,
                    "needs_login": bool(item["needs_login"])})
    # 复旦图书馆的「数据库导航」（按字母/学科浏览全部数据库）
    out.insert(0, {"id": "dbnav", "name": "数据库导航",
                   "url": "https://libdbnav.fudan.edu.cn/", "needs_login": True})
    return out


# ------------------------------------------------------------------ 数据库的机构访问
# 校内直连：数据库认校园网 IP；校外要经 WebVPN / libproxy，各家前缀不一样，
# 所以做成「用户填一个前缀模板」+ 可选的内置提示，不写死到代码里。
ACCESS_MODE_KEY = "library_access"
PROXY_PREFIX_KEY = "library_proxy_prefix"
UIS_LOGIN_URL = "https://id.fudan.edu.cn/"
WEBVPN_URL = "https://webvpn.fudan.edu.cn/"
DBNav_URL = "https://libdbnav.fudan.edu.cn/"


def access_mode(store) -> str:
    """校内直连（campus）还是走代理前缀（prefix）。"""
    try:
        value = str(store.get_setting(ACCESS_MODE_KEY, "campus") or "campus").strip()
    except Exception:
        return "campus"
    return value if value in ("campus", "prefix") else "campus"


def set_access_mode(store, mode: str) -> str:
    value = "prefix" if str(mode) == "prefix" else "campus"
    try:
        store.set_setting(ACCESS_MODE_KEY, value)
    except Exception:
        pass
    return value


def proxy_prefix(store) -> str:
    """校外访问前缀，例如 `https://libproxy.fudan.edu.cn:8080/LibProxy/?url=`。"""
    try:
        return str(store.get_setting(PROXY_PREFIX_KEY, "") or "").strip()
    except Exception:
        return ""


def set_proxy_prefix(store, text: str) -> None:
    try:
        store.set_setting(PROXY_PREFIX_KEY, str(text or "").strip())
    except Exception:
        pass


def access_url(store, url: str) -> str:
    """按当前访问方式把数据库地址改写好（校外时套上代理前缀）。"""
    target = str(url or "").strip()
    if not target:
        return target
    if access_mode(store) != "prefix":
        return target
    prefix = proxy_prefix(store)
    if not prefix:
        return target
    if not prefix.endswith(("=", "/", "?url=")):
        prefix = prefix + "=" if prefix.endswith("url") else prefix
    from urllib.parse import quote

    return prefix + quote(target, safe="")


# ------------------------------------------------------------------ 下载
def download_pdf(store, paper_id: int, timeout: float = 30.0) -> Dict[str, Any]:
    """把这篇文献的 PDF 存进文献库（按项目/年份分目录）。"""
    item = get_paper(store, paper_id)
    if not item:
        return {"ok": False, "summary": "没有这篇文献"}
    if item.get("file_path") and Path(str(item["file_path"])).exists():
        return {"ok": True, "path": str(item["file_path"]),
                "summary": f"已经下载过了：{item['file_path']}"}
    url = str(item.get("pdf_url") or "")
    if not url and str(item.get("doi") or ""):
        url = "https://doi.org/" + str(item["doi"])
    if not url or not websearch.allowed_url(url):
        return {"ok": False,
                "summary": "这篇没有可下载的 PDF 地址（可能需要机构登录）"}
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"缺少 requests：{exc}"}
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": UA},
                                allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"下载失败：{exc}"}
    if response.status_code >= 400:
        return {"ok": False,
                "summary": f"下载失败：HTTP {response.status_code}（可能要机构登录）"}
    content = response.content or b""
    if not content.startswith(b"%PDF"):
        return {"ok": False,
                "summary": "这个地址返回的不是 PDF（多半是需要登录/验证的页面）"}
    folder = folder_for(str(item.get("project") or ""), str(item.get("year") or ""))
    name = sanitize(str(item.get("title") or "paper"), 70) + ".pdf"
    target = folder / name
    index = 2
    while target.exists():
        target = folder / (sanitize(str(item.get("title") or "paper"), 60)
                           + f" ({index}).pdf")
        index += 1
    try:
        target.write_bytes(content)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"存不下来：{exc}"}
    try:
        store.conn.execute(
            "UPDATE papers SET file_path=?, folder=? WHERE id=?",
            (str(target), str(folder), int(paper_id)))
        store.conn.commit()
    except Exception:
        pass
    return {"ok": True, "path": str(target), "folder": str(folder),
            "bytes": len(content),
            "summary": f"已下载到 {target}（{len(content) / 1024:.0f} KB）"}


# ------------------------------------------------------------------ 引用
def _authors_list(item: Dict[str, Any]) -> List[str]:
    raw = str(item.get("authors") or "")
    parts = re.split(r"[;,、]\s*|\s+and\s+", raw)
    return [part.strip() for part in parts if part.strip()]


def cite_gbt7714(item: Dict[str, Any]) -> str:
    authors = _authors_list(item)[:3]
    author_text = ", ".join(authors) + (" 等" if len(_authors_list(item)) > 3 else "")
    year = item.get("year") or ""
    venue = item.get("venue") or ""
    kind = "[J]" if venue and "arxiv" not in str(venue).lower() else "[EB/OL]"
    text = f"{author_text}. {item.get('title','')}{kind}. {venue}"
    if year:
        text += f", {year}"
    if item.get("doi"):
        text += f". DOI: {item['doi']}"
    elif item.get("url"):
        text += f". {item['url']}"
    return text.strip() + "."


def cite_apa(item: Dict[str, Any]) -> str:
    authors = _authors_list(item)
    if authors:
        first = authors[0]
        author_text = first + (", et al." if len(authors) > 2 else
                              ((" & " + authors[1]) if len(authors) == 2 else ""))
    else:
        author_text = "Unknown"
    return (f"{author_text} ({item.get('year') or 'n.d.'}). "
            f"{item.get('title','')}. {item.get('venue','')}. "
            f"{item.get('url') or ('https://doi.org/' + str(item.get('doi'))) if (item.get('url') or item.get('doi')) else ''}").strip()


def cite_bibtex(item: Dict[str, Any], key: Optional[str] = None) -> str:
    authors = " and ".join(_authors_list(item))
    first_author = (_authors_list(item) or ["anon"])[0].split()[-1].lower()
    ident = key or f"{first_author}{item.get('year') or ''}"
    ident = re.sub(r"[^a-zA-Z0-9_:\-]", "", ident) or "ref"
    fields = [
        ("title", item.get("title", "")),
        ("author", authors),
        ("year", item.get("year", "")),
        ("journal", item.get("venue", "")),
        ("doi", item.get("doi", "")),
        ("url", item.get("url", "")),
    ]
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields if value)
    return f"@article{{{ident},\n{body}\n}}"


def cite_ris(items: List[Dict[str, Any]]) -> str:
    """RIS：EndNote / Zotero / Mendeley 都能直接导入。"""
    lines: List[str] = []
    for item in items:
        venue = str(item.get("venue") or "").lower()
        kind = "JOUR" if venue and "arxiv" not in venue else "ELEC"
        lines.append(f"TY  - {kind}")
        lines.append(f"TI  - {item.get('title','')}")
        for author in _authors_list(item):
            lines.append(f"AU  - {author}")
        if item.get("year"):
            lines.append(f"PY  - {item['year']}")
        if item.get("venue"):
            lines.append(f"JO  - {item['venue']}")
        if item.get("abstract"):
            lines.append("AB  - " + re.sub(r"\s+", " ", str(item["abstract"]))[:1200])
        if item.get("doi"):
            lines.append(f"DO  - {item['doi']}")
        if item.get("url"):
            lines.append(f"UR  - {item['url']}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def cite_enw(items: List[Dict[str, Any]]) -> str:
    """EndNote 的 .enw（%0/%T/%A…）格式。"""
    lines: List[str] = []
    for item in items:
        lines.append("%0 Journal Article")
        lines.append(f"%T {item.get('title','')}")
        for author in _authors_list(item):
            lines.append(f"%A {author}")
        if item.get("year"):
            lines.append(f"%D {item['year']}")
        if item.get("venue"):
            lines.append(f"%J {item['venue']}")
        if item.get("abstract"):
            lines.append("%X " + re.sub(r"\s+", " ", str(item["abstract"]))[:1200])
        if item.get("doi"):
            lines.append(f"%R {item['doi']}")
        if item.get("url"):
            lines.append(f"%U {item['url']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


CITATION_STYLES = ("gbt7714", "apa", "bibtex", "ris", "enw")


def citation(item: Dict[str, Any], style: str = "gbt7714") -> str:
    style = str(style or "gbt7714").lower()
    if style == "apa":
        return cite_apa(item)
    if style == "bibtex":
        return cite_bibtex(item)
    if style == "ris":
        return cite_ris([item])
    if style == "enw":
        return cite_enw([item])
    return cite_gbt7714(item)


def citations(items: List[Dict[str, Any]], style: str = "gbt7714") -> str:
    if not items:
        return ""
    style = str(style or "gbt7714").lower()
    if style == "ris":
        return cite_ris(items)
    if style == "enw":
        return cite_enw(items)
    if style == "bibtex":
        return "\n\n".join(cite_bibtex(item, f"{index + 1}")
                           for index, item in enumerate(items))
    return "\n\n".join(citation(item, style) for item in items)
