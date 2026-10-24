"""复旦 eLearning（Canvas）直连：requests + 浏览器 cookie（最稳的一条路）。

为什么这么做：
* Canvas 有标准 REST API，但需要登录 cookie；
* 登录仍在应用内嵌网页里完成（SSO），cookie 会落到 Chromium 的 Cookies 库里；
* 直接读那个 SQLite 库拿到 cookie，交给 requests 就能读写 API、下载文件，
  既快又不用折腾浏览器下载通道。
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_URL = "https://elearning.fudan.edu.cn"
HOST = "elearning.fudan.edu.cn"
COURSES_API = "/api/v1/courses?enrollment_state=active&per_page=100"
ASSIGNMENTS_API = "/api/v1/courses/{cid}/assignments?per_page=100"
FILES_API = "/api/v1/courses/{cid}/files?per_page=100&sort=created_at&order=desc"
PROFILE_NAME = "lumina-elearning"
STORAGE_SUB = "elearning"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class ElearningError(Exception):
    """带中文提示的错误。"""


# ------------------------------------------------------------------ cookie
def cookie_db_path() -> Path:
    from .config import DATA_DIR

    return Path(DATA_DIR) / "browser" / STORAGE_SUB / "Cookies"


def _dpapi_decrypt(blob: bytes) -> str:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def make_blob(data: bytes) -> "DATA_BLOB":
        buffer = ctypes.create_string_buffer(data, len(data))
        return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    data_in = make_blob(blob[3:])
    data_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
            ctypes.byref(data_in), None, None, None, None, 0, ctypes.byref(data_out)):
        return ""
    try:
        raw = ctypes.string_at(data_out.pbData, data_out.cbData)
        return raw.decode("utf-8", "ignore").replace(chr(0), "")
    finally:
        kernel32.LocalFree(data_out.pbData)


def read_cookies_from_profile(profile, wait_ms: int = 1500) -> Dict[str, str]:
    """从正在运行的 QWebEngineProfile 里读 cookie（磁盘库被浏览器锁住时用这个）。"""
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if profile is None or app is None:
        return {}
    jar: Dict[str, str] = {}
    try:
        store = profile.cookieStore()
    except Exception:
        return {}

    def _add(cookie) -> None:
        try:
            jar[bytes(cookie.name()).decode("utf-8", "ignore")] = bytes(
                cookie.value()).decode("utf-8", "ignore")
        except Exception:
            pass

    try:
        store.cookieAdded.connect(_add)
        store.loadAllCookies()
        loop = QEventLoop()
        QTimer.singleShot(wait_ms, loop.quit)
        loop.exec()
        store.cookieAdded.disconnect(_add)
    except Exception:
        pass
    return jar


def read_cookies() -> Dict[str, str]:
    """从 Chromium 的 Cookies 库里读 eLearning 的 cookie。

    浏览器（内嵌网页）运行时会把库文件锁住，所以先复制一份再读；
    复制也失败时再直接以只读方式打开。
    """
    path = cookie_db_path()
    if not path.exists():
        return {}
    rows = None
    try:
        import shutil
        import tempfile

        # Chromium 的 Cookies 库是 WAL 模式：要把 -wal / -shm 一起复制，
        # 否则只能读到旧数据（浏览器刚写入的登录态会丢）
        temp_dir = Path(tempfile.gettempdir()) / f"lumina_cookies_{os.getpid()}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp = temp_dir / "Cookies"
        shutil.copy2(path, temp)
        for suffix in ("-wal", "-shm"):
            source = Path(str(path) + suffix)
            if source.exists():
                try:
                    shutil.copy2(source, Path(str(temp) + suffix))
                except Exception:
                    pass
        conn = sqlite3.connect(str(temp))
        rows = conn.execute(
            "select host_key, name, value, encrypted_value from cookies").fetchall()
        conn.close()
        try:
            temp.unlink()
        except OSError:
            pass
    except Exception:
        rows = None
    if rows is None:
        try:
            conn = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
            rows = conn.execute(
                "select host_key, name, value, encrypted_value from cookies").fetchall()
            conn.close()
        except Exception:
            return {}
    jar: Dict[str, str] = {}
    for host_key, name, value, encrypted in rows:
        host = (host_key or "").lstrip(".")
        if host and not (host == HOST or HOST.endswith("." + host)
                         or host.endswith("fudan.edu.cn")):
            continue
        text = value
        if not text and encrypted:
            blob = bytes(encrypted)
            if blob[:3] == b"v10":
                text = _dpapi_decrypt(blob)
        if text:
            jar[str(name)] = str(text)
    return jar


# ------------------------------------------------------------------ 页面通道
class PageBridge:
    """在已经登录的内嵌网页里发请求（HttpOnly cookie 也能带上）。"""

    def __init__(self, view, app=None):
        self._view = view
        self._app = app
        if self._app is None:
            from PySide6.QtWidgets import QApplication

            self._app = QApplication.instance()

    # -------------------------------------------------- 工具
    def has_cookies(self) -> bool:
        """页面通道不依赖磁盘 cookie；有页面就算可用（是否登录由请求结果判断）。"""
        return self._view is not None

    def _pump(self, seconds: float) -> None:
        import time as _time

        end = _time.time() + seconds
        while _time.time() < end:
            self._app.processEvents()
            _time.sleep(0.01)

    def _run(self, script: str) -> Any:
        holder: Dict[str, Any] = {}
        self._view.page().runJavaScript(
            script, lambda raw, box=holder: box.setdefault("raw", raw))
        for _ in range(400):
            self._app.processEvents()
            if "raw" in holder:
                break
            self._pump(0.01)
        return holder.get("raw")

    def _poll(self, name: str, timeout: float = 120.0) -> Optional[dict]:
        import json as _json
        import time as _time

        end = _time.time() + timeout
        while _time.time() < end:
            raw = self._run("JSON.stringify(window." + name + "||null)")
            if isinstance(raw, str) and raw not in ("null", ""):
                try:
                    data = _json.loads(raw)
                except json.JSONDecodeError:
                    data = None
                if isinstance(data, dict) and data.get("done"):
                    return data
            self._pump(0.05)
        return None

    # -------------------------------------------------- JSON
    def fetch_json(self, url: str, timeout: float = 40.0) -> Any:
        start = (
            "(function(){window.__luminaFetch={done:false};"
            "fetch(" + __import__("json").dumps(url) + ",{credentials:'include',"
            "headers:{'Accept':'application/json'}})"
            ".then(function(r){window.__luminaFetch.status=r.status;return r.text();})"
            ".then(function(t){window.__luminaFetch.done=true;"
            "window.__luminaFetch.ok=true;window.__luminaFetch.data=t;})"
            ".catch(function(e){window.__luminaFetch.done=true;"
            "window.__luminaFetch.ok=false;window.__luminaFetch.err=String(e);});"
            "return 'started';})()"
        )
        self._run(start)
        data = self._poll("__luminaFetch", timeout)
        if not data:
            raise ElearningError("页面请求超时（可能没登录）")
        if not data.get("ok"):
            raise ElearningError(f"页面请求失败：{data.get('err')}")
        text = str(data.get("data") or "")
        if not text.strip():
            return []
        import json as _json

        try:
            return _json.loads(text)
        except json.JSONDecodeError as exc:
            raise ElearningError(f"返回的不是 JSON：{exc}") from exc

    # -------------------------------------------------- 下载
    def download_to(self, url: str, target: Path, chunk: int = 262144,
                    timeout: float = 300.0) -> int:
        import base64 as _b64
        import json as _json
        import time as _time

        url = download_url(url)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        start = (
            "(function(){window.__luminaDL={done:false};"
            "fetch(" + _json.dumps(url) + ",{credentials:'include'})"
            ".then(function(r){if(!r.ok){throw new Error('HTTP '+r.status);}"
            "return r.arrayBuffer();})"
            ".then(function(b){window.__luminaDL.buf=b;"
            "window.__luminaDL.size=b.byteLength;window.__luminaDL.done=true;})"
            ".catch(function(e){window.__luminaDL.done=true;"
            "window.__luminaDL.err=String(e);});"
            "return 'started';})()"
        )
        self._run(start)
        meta = self._poll("__luminaDL", timeout)
        if not meta:
            raise ElearningError("下载超时")
        if meta.get("err"):
            raise ElearningError(f"下载失败：{meta['err']}")
        size = int(meta.get("size") or 0)
        if size <= 0:
            raise ElearningError("下载内容为空")

        temp = target.with_name(target.name + ".part")
        written = 0
        with open(temp, "wb") as handle:
            offset = 0
            while offset < size:
                length = min(chunk, size - offset)
                script = (
                    "(function(){var u=new Uint8Array(window.__luminaDL.buf,"
                    + str(offset) + "," + str(length) + ");var s='';"
                    "for(var i=0;i<u.length;i++){s+=String.fromCharCode(u[i]);}"
                    "return btoa(s);})()"
                )
                piece = self._run(script)
                if not isinstance(piece, str) or not piece:
                    temp.unlink(missing_ok=True)
                    raise ElearningError("分块读取失败")
                data = _b64.b64decode(piece)
                handle.write(data)
                written += len(data)
                offset += length
        if written <= 0:
            temp.unlink(missing_ok=True)
            raise ElearningError("下载内容为空")
        temp.replace(target)
        return written


# ------------------------------------------------------------------ 客户端
class ElearningClient:
    """用 requests + 登录 cookie 访问 Canvas API。"""

    def __init__(self, cookies: Optional[Dict[str, str]] = None):
        import requests

        self._session = requests.Session()
        try:
            from . import vpn

            proxies = vpn.requests_proxies(store)
            if proxies:
                self._session.proxies.update(proxies)
        except Exception:
            pass
        self._session.headers.update({"User-Agent": UA, "Accept": "application/json"})
        jar = cookies if cookies is not None else read_cookies()
        for name, value in jar.items():
            for domain in (HOST, ".fudan.edu.cn"):
                try:
                    self._session.cookies.set(name, value, domain=domain)
                except Exception:
                    continue
        self._cookies = jar

    # -------------------------------------------------- 基础
    def has_cookies(self) -> bool:
        return bool(self._cookies)

    def get_json(self, path: str, timeout: float = 25.0) -> Any:
        url = path if path.startswith("http") else BASE_URL + path
        try:
            response = self._session.get(url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            raise ElearningError(f"网络请求失败：{exc}") from exc
        if response.status_code == 401:
            raise ElearningError("未登录：请先在「作业」页的网页里登录 eLearning")
        if response.status_code >= 400:
            raise ElearningError(f"接口返回 HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ElearningError(f"返回内容不是 JSON：{exc}") from exc

    def download_to(self, url: str, target: Path, timeout: float = 180.0) -> int:
        """下载到文件，返回字节数；失败抛 ElearningError。"""
        url = download_url(url)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".part")
        try:
            with self._session.get(url, timeout=timeout, stream=True) as response:
                if response.status_code >= 400:
                    raise ElearningError(f"HTTP {response.status_code}")
                size = 0
                with open(temp, "wb") as handle:
                    for chunk in response.iter_content(65536):
                        if chunk:
                            handle.write(chunk)
                            size += len(chunk)
                if size == 0:
                    try:
                        temp.unlink()
                    except OSError:
                        pass
                    raise ElearningError("下载内容为空")
                temp.replace(target)
                return size
        except ElearningError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ElearningError(f"下载失败：{exc}") from exc


# ------------------------------------------------------------------ 数据
def list_courses(client: ElearningClient) -> List[Dict[str, Any]]:
    data = client.get_json(COURSES_API)
    courses: List[Dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        courses.append({
            "id": item.get("id"),
            "name": item.get("name") or item.get("course_code") or "",
            "code": item.get("course_code") or "",
        })
    return courses


def list_assignments(client: ElearningClient, course_id: Any,
                     course_name: str = "") -> List[Dict[str, Any]]:
    data = client.get_json(ASSIGNMENTS_API.format(cid=course_id))
    out: List[Dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        description = item.get("description") or ""
        attachments: List[Dict[str, str]] = []
        for att in item.get("attachments") or []:
            if isinstance(att, dict) and att.get("url"):
                attachments.append({
                    "name": att.get("display_name") or att.get("filename") or "附件",
                    "url": str(att.get("url")),
                })
        attachments.extend(parse_file_links(description))
        out.append({
            "id": item.get("id"),
            "name": item.get("name") or "（未命名作业）",
            "due_at": item.get("due_at") or "",
            "html_url": item.get("html_url") or "",
            "description": description,
            "course_id": course_id,
            "course_name": course_name,
            "attachments": attachments,
        })
    return out


def list_files(client: ElearningClient, course_id: Any) -> List[Dict[str, Any]]:
    try:
        data = client.get_json(FILES_API.format(cid=course_id))
    except ElearningError:
        return []
    out: List[Dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or ""
        if not url:
            continue
        out.append({
            "name": item.get("display_name") or item.get("filename") or "文件",
            "url": str(url),
            "size": item.get("size"),
        })
    return out


# ------------------------------------------------------------------ 链接处理
_FILE_HREF_RE = re.compile(
    r'<a[^>]+href="([^"]*?/files/(\d+)/download[^"]*?)"[^>]*>(.*?)</a>',
    re.I | re.S)
_FILE_ANY_RE = re.compile(
    r"(?:https?://[^\s\"'<>]*)?/files/(\d+)/download[^\s\"'<>]*", re.I)


def _absolute(url: str) -> str:
    url = (url or "").strip().replace("&amp;", "&")
    if url.startswith("http"):
        return url
    return BASE_URL + (url if url.startswith("/") else "/" + url)


def download_url(url: str) -> str:
    """把 Canvas 文件地址改造成一定会触发下载的形式。"""
    url = (url or "").strip()
    if not url:
        return url
    if "/download" in url:
        if "download_frd" not in url:
            url += ("&" if "?" in url else "?") + "download_frd=1"
        return url
    if "/files/" in url:
        base = url.split("?")[0].rstrip("/")
        sep = "&" if "?" in url else "?"
        return base + "/download" + sep + "download_frd=1"
    return url


def parse_file_links(html: str) -> List[Dict[str, str]]:
    """从作业描述里挑出文件链接（按文件 id 去重）。"""
    text = html or ""
    by_id: Dict[str, Dict[str, str]] = {}
    for match in _FILE_HREF_RE.finditer(text):
        file_id = match.group(2)
        label = re.sub(r"<[^>]+>", "", match.group(3) or "").strip()
        url = _absolute(match.group(1))
        by_id.setdefault(file_id, {"name": label or f"附件_{file_id}", "url": url})
    for match in _FILE_ANY_RE.finditer(text):
        file_id = match.group(1)
        if file_id in by_id:
            continue
        by_id[file_id] = {"name": f"附件_{file_id}", "url": _absolute(match.group(0))}
    return list(by_id.values())


def safe_filename(name: str) -> str:
    cleaned = "".join("_" if char in '\\/:*?"<>|' else char for char in (name or ""))
    cleaned = cleaned.strip().strip(".")
    return cleaned or "未命名"


# ------------------------------------------------------------------ 批量下载
def download_all(client: ElearningClient, items: List[Dict[str, Any]],
                 target_dir: Path, on_progress=None) -> Dict[str, Any]:
    """批量下载；**重名的跳过**（不覆盖、不重复下载）。"""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {"saved": [], "skipped": [], "failed": []}
    total = len(items)
    used_names = set()
    for index, item in enumerate(items):
        name = safe_filename(str(item.get("name") or "附件"))
        if name in used_names:
            result["skipped"].append(name)
            continue
        used_names.add(name)
        path = target_dir / name
        if path.exists():
            result["skipped"].append(name)
            continue
        try:
            size = client.download_to(str(item.get("url")), path)
            result["saved"].append((name, str(path), size))
        except Exception as exc:  # noqa: BLE001
            result["failed"].append((name, str(exc)))
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        if on_progress is not None:
            try:
                on_progress(index + 1, total, name)
            except Exception:
                pass
    return result
