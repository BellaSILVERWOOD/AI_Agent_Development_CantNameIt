"""参考文献：存条目、从 BibTeX / RIS / DOI 导入、导出成 EndNote 能吃的格式。

数据表 refs(id, kind, title, authors, year, venue, doi, url, note, created_at)

对外接口：
    ensure_schema(store)
    add(store, entry) / list_refs(store, keyword) / delete(store, ref_id)
    parse_bibtex(text) -> List[dict]
    parse_ris(text) -> List[dict]
    fetch_doi(doi) -> dict            # 走 Crossref，取标题/作者/期刊/年份
    to_bibtex(entries) -> str
    to_ris(entries) -> str
    export(store, path, fmt="ris")
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

FMT_RIS = "ris"
FMT_BIB = "bib"


# ------------------------------------------------------------------ 表
def ensure_schema(store) -> None:
    try:
        store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL DEFAULT 'article',
                title TEXT NOT NULL DEFAULT '',
                authors TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                venue TEXT NOT NULL DEFAULT '',
                doi TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        store.conn.commit()
    except Exception:
        pass


def add(store, entry: Dict[str, Any]) -> int:
    ensure_schema(store)
    stamp = datetime.now().isoformat(timespec="seconds")
    cur = store.conn.execute(
        "INSERT INTO refs(kind, title, authors, year, venue, doi, url, note, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            str(entry.get("kind") or "article"),
            str(entry.get("title") or "").strip(),
            str(entry.get("authors") or "").strip(),
            str(entry.get("year") or "").strip(),
            str(entry.get("venue") or "").strip(),
            str(entry.get("doi") or "").strip(),
            str(entry.get("url") or "").strip(),
            str(entry.get("note") or "").strip(),
            stamp,
        ),
    )
    store.conn.commit()
    return int(cur.lastrowid)


def add_many(store, entries: List[Dict[str, Any]]) -> int:
    count = 0
    for entry in entries:
        title = str(entry.get("title") or "").strip()
        doi = str(entry.get("doi") or "").strip().lower()
        existing = store.conn.execute(
            "SELECT id FROM refs WHERE (title != '' AND title = ?)"
            " OR (doi != '' AND lower(doi) = ?)", (title, doi)).fetchone()
        if existing:
            continue
        add(store, entry)
        count += 1
    return count


def list_refs(store, keyword: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    ensure_schema(store)
    rows = store.conn.execute("SELECT * FROM refs ORDER BY id DESC LIMIT ?",
                              (int(limit),)).fetchall()
    items = [dict(row) for row in rows]
    text = (keyword or "").strip().lower()
    if text:
        items = [item for item in items
                 if text in " ".join(str(item.get(key, "")) for key in
                                     ("title", "authors", "venue", "doi")).lower()]
    return items


def delete(store, ref_id: int) -> None:
    ensure_schema(store)
    store.conn.execute("DELETE FROM refs WHERE id = ?", (int(ref_id),))
    store.conn.commit()


# ------------------------------------------------------------------ 解析
def parse_bibtex(text: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for match in re.finditer(r"@(\w+)\s*\{([^,]*),(.*?)\n\}", text, re.S):
        kind = match.group(1).lower()
        body = match.group(3)
        fields: Dict[str, str] = {}
        for field in re.finditer(r"(\w+)\s*=\s*[{\"](.+?)[}\"]\s*,?\s*\n", body, re.S):
            fields[field.group(1).lower()] = re.sub(r"\s+", " ", field.group(2)).strip()
        if not fields:
            continue
        authors = fields.get("author", "")
        authors = authors.replace(" and ", ", ")
        entries.append({
            "kind": "article" if kind in ("article", "inproceedings", "book") else kind,
            "title": fields.get("title", ""),
            "authors": authors,
            "year": fields.get("year", ""),
            "venue": fields.get("journal") or fields.get("booktitle") or "",
            "doi": fields.get("doi", ""),
            "url": fields.get("url", ""),
        })
    return entries


def parse_ris(text: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    current: Dict[str, List[str]] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = re.match(r"^([A-Z][A-Z0-9])\s{1,2}-\s?(.*)$", line)
        if not match:
            continue
        tag, value = match.group(1), match.group(2).strip()
        current.setdefault(tag, []).append(value)
        if tag == "ER":
            entries.append({
                "kind": "article",
                "title": " ".join(current.get("TI") or current.get("T1") or []),
                "authors": ", ".join(current.get("AU") or current.get("A1") or []),
                "year": (current.get("PY") or current.get("Y1") or [""])[0][:4],
                "venue": " ".join(current.get("JO") or current.get("JF")
                                  or current.get("T2") or []),
                "doi": " ".join(current.get("DO") or []),
                "url": " ".join(current.get("UR") or []),
            })
            current = {}
    return entries


def fetch_doi(doi: str, timeout: float = 10.0) -> Dict[str, Any]:
    """用 Crossref 把 DOI 变成一条文献。"""
    import requests

    doi = (doi or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    if not doi:
        raise RuntimeError("DOI 是空的")
    response = requests.get(f"https://api.crossref.org/works/{doi}", timeout=timeout,
                            headers={"User-Agent": "Lumina/1.0 (mailto:noreply@example.com)"})
    if response.status_code >= 400:
        raise RuntimeError(f"Crossref 返回 {response.status_code}")
    message = (response.json() or {}).get("message") or {}
    authors = []
    for person in message.get("author") or []:
        family = person.get("family") or person.get("name") or ""
        given = person.get("given") or ""
        authors.append(f"{family}, {given}".strip(", "))
    issued = message.get("issued", {}).get("date-parts") or [[""]]
    year = str(issued[0][0]) if issued and issued[0] else ""
    return {
        "kind": "article",
        "title": " ".join(message.get("title") or [""]).strip(),
        "authors": ", ".join(authors),
        "year": year,
        "venue": " ".join(message.get("container-title") or [""]).strip(),
        "doi": doi,
        "url": message.get("URL") or f"https://doi.org/{doi}",
    }


# ------------------------------------------------------------------ 导出
def to_ris(entries: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for item in entries:
        lines.append("TY  - JOUR")
        if item.get("title"):
            lines.append(f"TI  - {item['title']}")
        for author in str(item.get("authors") or "").split(","):
            name = author.strip()
            if name:
                lines.append(f"AU  - {name}")
        if item.get("year"):
            lines.append(f"PY  - {item['year']}")
        if item.get("venue"):
            lines.append(f"JO  - {item['venue']}")
        if item.get("doi"):
            lines.append(f"DO  - {item['doi']}")
        if item.get("url"):
            lines.append(f"UR  - {item['url']}")
        if item.get("note"):
            lines.append(f"N1  - {item['note']}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines)


def to_bibtex(entries: List[Dict[str, Any]]) -> str:
    chunks: List[str] = []
    for index, item in enumerate(entries, start=1):
        first_author = str(item.get("authors") or "").split(",")[0].strip()
        key = re.sub(r"[^A-Za-z0-9]", "", first_author.split()[-1] if first_author else "ref")
        year = str(item.get("year") or "")
        label = f"{key or 'ref'}{year or index}"
        fields = []
        if item.get("title"):
            fields.append(f"  title = {{{item['title']}}}")
        if item.get("authors"):
            fields.append(f"  author = {{{item['authors'].replace(', ', ' and ')}}}")
        if item.get("venue"):
            fields.append(f"  journal = {{{item['venue']}}}")
        if item.get("year"):
            fields.append(f"  year = {{{item['year']}}}")
        if item.get("doi"):
            fields.append(f"  doi = {{{item['doi']}}}")
        if item.get("url"):
            fields.append(f"  url = {{{item['url']}}}")
        chunks.append("@article{" + label + ",\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(chunks)


def export(store, path: Path, fmt: str = FMT_RIS,
           entries: Optional[List[Dict[str, Any]]] = None) -> Path:
    items = entries if entries is not None else list_refs(store)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = to_ris(items) if fmt == FMT_RIS else to_bibtex(items)
    target.write_text(content, encoding="utf-8")
    return target
