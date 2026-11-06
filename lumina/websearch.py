"""不需要 API Key 的网页搜索 / 抓取（第 2、3、4 步的地基）。

为什么要自己写：应用没有搜索引擎的 API Key，而「搜一下课程视频 / 找一篇文献」
这种事不能指望模型猜链接。所以：

* `search(query)`：**DuckDuckGo 的 HTML 版**（不用 JS、不用 Key）为主，
  不行就换 **Bing** 再试一次；两家都解析不出结果就老实说「没搜到」。
* `fetch_text(url)`：把网页抓下来转成纯文本（复用 `imap_inbox.html_to_text`，
  它已经会整块删掉 `<style>/<script>`），带长度上限，避免把上下文撑爆。
* 只允许 `http/https`：`file:`、`javascript:`、`data:` 一律拒绝（防止模型
  借搜索工具去读本地文件或执行脚本）。

注意：这里只做**取数据**，不注册成 AI 工具；要不要让 agent 用，由用户在
「设置 → AI 接口 → 工具审核」里批准（用户要求每个工具都先审核）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
DDG_HTML = "https://html.duckduckgo.com/html/"
BING_SEARCH = "https://www.bing.com/search"
DEFAULT_LIMIT = 8
MAX_FETCH_CHARS = 20000
TIMEOUT = 8.0

_LINK_RE = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.S | re.I)
_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)
_CLASS_RE = re.compile(r'class\s*=\s*["\']([^"\']*)["\']', re.I)
_TAG_RE = re.compile(r"<[^>]+>")


# ------------------------------------------------------------------ 基础
def allowed_url(url: str) -> bool:
    """只放行 http / https（不给模型留 file:// 之类的口子）。"""
    text = str(url or "").strip()
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except Exception:
        return False
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def _clean(text: str) -> str:
    value = _TAG_RE.sub(" ", str(text or ""))
    value = (value.replace("&nbsp;", " ").replace("&amp;", "&")
             .replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", value).strip()


def _get(url: str, params: Optional[Dict[str, str]] = None,
         timeout: float = TIMEOUT) -> Tuple[str, str]:
    """GET 一个网页，返回 (html, error)。"""
    if not allowed_url(url):
        return "", "只支持 http/https 网址"
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return "", f"缺少 requests：{exc}"
    try:
        response = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": UA,
                                         "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    except Exception as exc:  # noqa: BLE001
        return "", f"连不上：{exc}"
    if response.status_code >= 400:
        return "", f"HTTP {response.status_code}"
    text = response.text or ""
    if not text.strip():
        return "", "返回内容为空"
    return text, ""


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo 的结果链接是 /l/?uddg=… 包一层的，这里拆开。"""
    text = str(href or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    try:
        parsed = urlparse(text)
    except Exception:
        return text
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        query = parse_qs(parsed.query)
        target = (query.get("uddg") or [""])[0]
        if target:
            return unquote(target)
    return text


def parse_ddg(html: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, str]]:
    """解析 DuckDuckGo HTML 版结果页。"""
    results: List[Dict[str, str]] = []
    seen = set()
    for match in _LINK_RE.finditer(str(html or "")):
        attrs, inner = match.group(1), match.group(2)
        classes = (_CLASS_RE.search(attrs).group(1) if _CLASS_RE.search(attrs) else "")
        if "result__a" not in classes:
            continue
        href_match = _HREF_RE.search(attrs)
        if not href_match:
            continue
        url = _unwrap_ddg(href_match.group(1))
        if not allowed_url(url) or url in seen:
            continue
        title = _clean(inner)
        if not title:
            continue
        seen.add(url)
        results.append({"title": title, "url": url, "snippet": ""})
        if len(results) >= int(limit):
            break
    # 摘要：紧跟在结果链接后面的 result__snippet
    snippets = re.findall(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a\s*>',
                          str(html or ""), re.S | re.I)
    for index, text in enumerate(snippets):
        if index < len(results):
            results[index]["snippet"] = _clean(text)[:400]
    return results


def parse_bing(html: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, str]]:
    """解析 Bing 结果页（b_algo）。"""
    results: List[Dict[str, str]] = []
    seen = set()
    blocks = re.split(r'<li[^>]*class="[^"]*b_algo[^"]*"', str(html or ""), flags=re.I)
    for block in blocks[1:]:
        match = re.search(r'<h2[^>]*>.*?<a\b([^>]*)>(.*?)</a\s*>', block, re.S | re.I)
        if not match:
            continue
        attrs, inner = match.group(1), match.group(2)
        href_match = _HREF_RE.search(attrs)
        if not href_match:
            continue
        url = href_match.group(1).strip()
        if url.startswith("//"):
            url = "https:" + url
        if not allowed_url(url) or url in seen:
            continue
        title = _clean(inner)
        if not title:
            continue
        snippet = ""
        para = re.search(r"<p[^>]*>(.*?)</p>", block, re.S | re.I)
        if para:
            snippet = _clean(para.group(1))[:400]
        seen.add(url)
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= int(limit):
            break
    return results


def search(query: str, limit: int = DEFAULT_LIMIT,
           timeout: float = TIMEOUT) -> Dict[str, Any]:
    """搜网页：先 DuckDuckGo，失败再 Bing。返回 {"ok", "engine", "results", "summary"}。"""
    text = str(query or "").strip()
    if not text:
        return {"ok": False, "engine": "", "results": [], "summary": "关键词是空的"}
    cap = max(1, min(20, int(limit or DEFAULT_LIMIT)))
    errors: List[str] = []
    html, error = _get(DDG_HTML, {"q": text, "kl": "cn-zh"},
                       timeout=timeout)
    if html:
        found = parse_ddg(html, cap)
        if found:
            return {"ok": True, "engine": "duckduckgo", "query": text,
                    "results": found,
                    "summary": f"在 DuckDuckGo 上搜到 {len(found)} 条结果"}
    else:
        errors.append(f"duckduckgo: {error}")
    html, error = _get(BING_SEARCH, {"q": text, "setlang": "zh-Hans"},
                       timeout=timeout)
    if html:
        found = parse_bing(html, cap)
        if found:
            return {"ok": True, "engine": "bing", "query": text,
                    "results": found,
                    "summary": f"在 Bing 上搜到 {len(found)} 条结果"}
    else:
        errors.append(f"bing: {error}")
    return {"ok": False, "engine": "", "query": text, "results": [],
            "summary": "没搜到结果（" + ("；".join(errors) if errors
                                      else "两个搜索引擎都没给出结果") + "）"}


def fetch_text(url: str, limit: int = MAX_FETCH_CHARS,
               timeout: float = TIMEOUT) -> Dict[str, Any]:
    """抓网页正文（转纯文本）。返回 {"ok", "title", "text", "summary"}。"""
    target = str(url or "").strip()
    if not allowed_url(target):
        return {"ok": False, "title": "", "text": "",
                "summary": "只支持 http/https 网址"}
    html, error = _get(target, None, timeout=timeout)
    if not html:
        return {"ok": False, "title": "", "text": "", "summary": f"抓不到：{error}"}
    from . import imap_inbox

    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title\s*>", html, re.S | re.I)
    if match:
        title = _clean(match.group(1))[:120]
    text = imap_inbox.html_to_text(html)
    cap = max(1000, min(MAX_FETCH_CHARS * 3, int(limit or MAX_FETCH_CHARS)))
    truncated = len(text) > cap
    if truncated:
        text = text[:cap] + "\n…（已截断）"
    return {"ok": True, "url": target, "title": title, "text": text,
            "truncated": truncated, "chars": len(text),
            "summary": (f"抓到 {title or target}：{len(text)} 字"
                        + ("（已截断）" if truncated else ""))}


def ddg_url(query: str) -> str:
    """给界面用的「在浏览器里搜」链接。"""
    return DDG_HTML + "?q=" + quote_plus(str(query or ""))
