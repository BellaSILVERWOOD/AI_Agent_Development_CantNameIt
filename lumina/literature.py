"""文献流程（D1–D8）：检索 → 存库 → 侧栏多选 → 下载 / 引用 / 导出。

用户的要求：
* 提问时后台搜文献，结果**列成可多选列表**（侧栏），能全选/全不选；
* 每条可展开摘要；确认后**一键下载**或**一键引用**（EndNote）；
* 下载的 PDF 进「我的文献库」，按「项目/年份」归类，能检索；
* 本机没装 EndNote 就先问要不要经 ehall 装。

这个模块是「界面 + agent」共用的服务层：不掺 Qt，方便测试。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import endnote, lit

#: 检索完的结果要「自动出现在边栏」：主窗口注册一个回调，服务层搜到就推过去
_RESULTS_HANDLER = None


def set_results_handler(handler) -> None:
    """主窗口注册：`handler(papers)`（把结果丢给右侧「文献」面板）。"""
    global _RESULTS_HANDLER
    _RESULTS_HANDLER = handler


def _publish(papers: list) -> None:
    handler = _RESULTS_HANDLER
    if handler is None or not papers:
        return
    try:
        handler(list(papers))
    except Exception:
        pass


# ------------------------------------------------------------------ 检索
def search(store, query: str, limit: Optional[int] = None, project: str = "",
           with_semantic: bool = True, page: int = 1,
           page_size: int = lit.PAGE_SIZE) -> Dict[str, Any]:
    """搜文献并写进库里（侧栏列表就是库里的结果）。

    分页规则（用户要求）：一页 10 条；一次搜索最多 50 条；如果查询里写了具体数字
    （「20 篇」「前 15 条」「top 30」），按那个数字来，但不超过 50。
    """
    text = str(query or "").strip()
    page_size = max(1, min(lit.MAX_LIMIT, int(page_size or lit.PAGE_SIZE)))
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    wanted = lit.parse_requested_limit(text, page_size) if limit is None \
        else max(page_size, min(lit.MAX_LIMIT, int(limit)))
    # 要看第 N 页就得多取一些（仍然受 50 条上限约束）
    fetch = min(lit.MAX_LIMIT, max(wanted, page * page_size))
    result = lit.search_all(text, limit=fetch, with_semantic=with_semantic)
    saved: List[int] = []
    for item in result.get("results") or []:
        row = lit.add_paper(store, item, project=project)
        if row.get("id"):
            saved.append(int(row["id"]))
    rows = [lit.get_paper(store, paper_id) for paper_id in saved]
    rows = [row for row in rows if row]
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    if page > pages:
        page = pages
    window = rows[(page - 1) * page_size: page * page_size]
    _publish(window)
    if not rows:
        summary = result.get("summary") or "没搜到文献"
    elif pages > 1:
        summary = (f"找到 {total} 篇（第 {page}/{pages} 页，每页 {page_size} 条，"
                   f"单次最多 {lit.MAX_LIMIT} 条）")
    else:
        summary = f"找到 {total} 篇相关文献（侧栏里可以逐条勾选）"
    return {"ok": bool(result.get("ok")) and bool(rows),
            "query": text, "ids": saved, "count": len(window),
            "errors": result.get("errors") or [],
            "results": window, "all_results": rows,
            "page": page, "pages": pages, "page_size": page_size,
            "total": total, "limit": wanted,
            "summary": summary}


def search_arxiv(store, query: str, limit: int = 8, project: str = "") -> Dict[str, Any]:
    xml, error = lit._get_text(lit.ARXIV_API, {
        "search_query": f"all:{query}", "start": "0", "max_results": str(limit)})
    if not xml:
        return {"ok": False, "summary": f"arXiv 连不上：{error}", "results": []}
    items = lit.parse_arxiv(xml, limit)
    saved = [lit.add_paper(store, item, project=project) for item in items]
    saved = [row for row in saved if row.get("id")]
    _publish(saved)
    return {"ok": bool(saved), "results": saved, "count": len(saved),
            "summary": f"arXiv 上找到 {len(saved)} 篇"}


def search_crossref(store, query: str, limit: int = 8,
                    project: str = "") -> Dict[str, Any]:
    payload, error = lit._get_json(lit.CROSSREF_API,
                                   {"query": query, "rows": str(limit)})
    if payload is None:
        return {"ok": False, "summary": f"Crossref 连不上：{error}", "results": []}
    items = lit.parse_crossref(payload, limit)
    saved = [lit.add_paper(store, item, project=project) for item in items]
    saved = [row for row in saved if row.get("id")]
    _publish(saved)
    return {"ok": bool(saved), "results": saved, "count": len(saved),
            "summary": f"Crossref 上找到 {len(saved)} 篇"}


def search_semantic(store, query: str, limit: int = 8,
                    project: str = "") -> Dict[str, Any]:
    payload, error = lit._get_json(lit.S2_API, {
        "query": query, "limit": str(limit),
        "fields": "title,abstract,year,venue,authors,externalIds,openAccessPdf,paperId",
    })
    if payload is None:
        return {"ok": False,
                "summary": f"Semantic Scholar 连不上：{error}", "results": []}
    items = lit.parse_semantic(payload, limit)
    saved = [lit.add_paper(store, item, project=project) for item in items]
    saved = [row for row in saved if row.get("id")]
    _publish(saved)
    return {"ok": bool(saved), "results": saved, "count": len(saved),
            "summary": f"Semantic Scholar 上找到 {len(saved)} 篇"}


def doi_lookup(store, doi: str, project: str = "") -> Dict[str, Any]:
    text = str(doi or "").strip()
    if not text:
        return {"ok": False, "summary": "要给个 DOI"}
    if text.lower().startswith("https://doi.org/"):
        text = text.split("doi.org/", 1)[-1]
    payload, error = lit._get_json(lit.CROSSREF_API + "/" + text)
    if payload is None:
        return {"ok": False, "summary": f"查不到这个 DOI：{error}"}
    items = lit.parse_crossref({"message": {"items": [payload.get("message") or {}]}})
    if not items:
        return {"ok": False, "summary": "这个 DOI 没有元数据"}
    row = lit.add_paper(store, items[0], project=project)
    return {"ok": bool(row.get("id")), "paper": row, "count": 1,
            "bibtex": lit.cite_bibtex(items[0]),
            "summary": f"查到了：{items[0].get('title', '')}"}


# ------------------------------------------------------------------ 列表 / 下载
def results(store, query: str = "", project: str = "", limit: int = 200
            ) -> List[Dict[str, Any]]:
    return lit.list_papers(store, query=query, project=project, limit=limit)


def papers_by_ids(store, ids: List[int]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for paper_id in ids or []:
        try:
            row = lit.get_paper(store, int(paper_id))
        except (TypeError, ValueError):
            continue
        if row:
            items.append(row)
    return items


def download(store, ids: List[int], timeout: float = 30.0) -> Dict[str, Any]:
    """一键下载（用户勾选之后才调；不会自己偷偷下）。"""
    items = papers_by_ids(store, ids)
    if not items:
        return {"ok": False, "summary": "没有选中文献"}
    done: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for item in items:
        got = lit.download_pdf(store, int(item["id"]), timeout=timeout)
        if got.get("ok"):
            done.append({"id": item["id"], "title": item.get("title", ""),
                         "path": got.get("path", "")})
        else:
            failed.append({"id": item["id"], "title": item.get("title", ""),
                           "reason": got.get("summary", "")})
    summary = f"下载完成 {len(done)} 篇"
    if failed:
        summary += f"，{len(failed)} 篇没成功（多半要机构登录，可以点链接自己下）"
    return {"ok": bool(done), "downloaded": done, "failed": failed,
            "summary": summary}


# ------------------------------------------------------------------ 引用 / 导出
def citations(store, ids: List[int], style: str = "gbt7714") -> Dict[str, Any]:
    items = papers_by_ids(store, ids)
    if not items:
        return {"ok": False, "text": "", "summary": "没有选中文献"}
    text = lit.citations(items, style)
    return {"ok": True, "text": text, "count": len(items), "style": style,
            "summary": f"已生成 {len(items)} 条引用（{style}）"}


def export(store, ids: List[int], style: str = "enw") -> Dict[str, Any]:
    items = papers_by_ids(store, ids)
    return endnote.export(store, items, style)


def endnote_status() -> Dict[str, Any]:
    return endnote.detect()


def endnote_install_hint() -> Dict[str, str]:
    return endnote.install_hint()


def open_endnote(path: str) -> bool:
    return endnote.open_with_endnote(path)


# ------------------------------------------------------------------ 数据库链接
def library_links(query: str = "") -> List[Dict[str, Any]]:
    return lit.database_links(query)
