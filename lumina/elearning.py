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
#: eLearning 的「仪表板」页（右侧 To Do 就是「最近要交的作业」）
DASH_URL = BASE_URL + "/dash"
#: dash 右侧 To Do 用的接口（和页面里那份数据是同一个）
TODO_API = "/api/v1/users/self/todo?per_page=50"
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


_PROFILE_PROVIDER = None
_LIVE_JAR: Dict[str, str] = {}
_HOOKED_STORES: set = set()


def install_cookie_hook(profile) -> Dict[str, str]:
    """持续监听浏览器的 cookie 变化，攒一份「最新登录态」。

    为什么要持续监听：`loadAllCookies()` 只在特定时机把已有 cookie 发出来，
    用户**刚在网页里登录**时新写入的那些，老老实实监听 `cookieAdded` 才拿得到。
    """
    from PySide6.QtWidgets import QApplication

    if profile is None or QApplication.instance() is None:
        return _LIVE_JAR
    try:
        store = profile.cookieStore()
    except Exception:
        return _LIVE_JAR
    key = id(store)
    if key in _HOOKED_STORES:
        return _LIVE_JAR

    def _add(cookie) -> None:
        try:
            name = bytes(cookie.name()).decode("utf-8", "ignore")
            value = bytes(cookie.value()).decode("utf-8", "ignore")
            if name:
                _LIVE_JAR[name] = value
        except Exception:
            pass

    try:
        store.cookieAdded.connect(_add)
        _HOOKED_STORES.add(key)
    except Exception:
        pass
    return _LIVE_JAR


def live_jar() -> Dict[str, str]:
    return dict(_LIVE_JAR)


def set_profile_provider(provider) -> None:
    """登记「拿到当前内嵌浏览器 profile」的办法（主窗口启动时调用）。

    为什么需要它：内嵌网页登录之后，Chromium 会把 Cookies 库**独占锁住**，
    磁盘上那份连 `immutable=1` 都打不开（实测：`unable to open database file`）。
    所以登录态只能从正在运行的 `QWebEngineProfile.cookieStore()` 里读。
    """
    global _PROFILE_PROVIDER
    _PROFILE_PROVIDER = provider
    try:
        install_cookie_hook(current_profile())
    except Exception:
        pass


_PROFILE_PROVIDER = None


def current_profile():
    provider = _PROFILE_PROVIDER
    if provider is None:
        return None
    try:
        return provider()
    except Exception:
        return None


def read_cookies(retries: int = 3, wait_ms: int = 700) -> Dict[str, str]:
    """读 eLearning 的 cookie（会重试几次）。

    顺序：
      1. **正在运行的浏览器**（`cookieStore()`）——登录后最可靠，磁盘库被锁时也能读；
      2. 复制一份磁盘库再读（浏览器没跑的时候用）；
      3. 直接只读打开。

    为什么要重试：统一身份认证 → eLearning 要跳好几次，登录刚完成的那一刻
    cookie 可能还没全部落到 profile 里；以前只读一次，就会「明明登录了却说没登录」。
    """
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        jar = _read_cookies_once(wait_ms=wait_ms)
        if jar:
            return jar
        if attempt + 1 < attempts:
            try:
                import time as _time

                _time.sleep(0.35)
            except Exception:
                pass
    return {}


def has_cookies() -> bool:
    """有没有 eLearning 登录态（给界面判断用）。"""
    return bool(read_cookies())


def _read_cookies_once(wait_ms: int = 700) -> Dict[str, str]:
    profile = current_profile()
    jar: Dict[str, str] = {}
    if profile is not None:
        # ① 持续监听攒下来的（刚登录时最准，也不用碰磁盘）
        try:
            install_cookie_hook(profile)
            jar.update(live_jar())
        except Exception:
            pass
        if jar:
            return jar
        # ② 监听里还没有：让 Qt 把存着的 cookie 全吐一遍
        try:
            jar.update(read_cookies_from_profile(profile))
        except Exception:
            pass
        if jar:
            return jar
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
                except _json.JSONDecodeError:
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
        except _json.JSONDecodeError as exc:
            raise ElearningError(f"返回的不是 JSON：{exc}") from exc

    def get_json(self, path: str, timeout: float = 40.0) -> Any:
        """和 `ElearningClient.get_json` 一样的接口（内部走页面 fetch）。

        `list_courses` / `todo_items` 这些数据函数只认 `get_json`，
        所以页面通道必须补这一个同名方法，否则「用页面里的登录态取数」会直接
        AttributeError（这条兜底路径以前其实一直是坏的）。
        """
        url = path if path.startswith("http") else BASE_URL + path
        return self.fetch_json(url, timeout=timeout)

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


def open_client(store=None) -> "ElearningClient":
    """不借助内嵌网页也能用的客户端：磁盘 cookie → 统一身份认证换一次。

    仪表板的「最近作业」要在后台线程里取数，拿不到页面对象，只能走这条路。
    """
    try:
        cookies = read_cookies()
    except Exception:
        cookies = {}
    if not cookies and store is not None:
        try:
            from . import uis_login

            if uis_login.has_credentials(store):
                cookies = uis_login.canvas_cookies(store)
        except Exception:
            cookies = {}
    return ElearningClient(cookies)


#: To Do 里各种 type 的中文叫法
TODO_KINDS = {
    "submitting": "要交",
    "grading": "待评分",
    "not_graded": "待评分",
    "peer_review": "互评",
}


def _todo_kind(value: str) -> str:
    text = str(value or "").strip()
    return TODO_KINDS.get(text, "作业")


def _local_due(value: str) -> str:
    """UTC 的 due_at → 本地「YYYY-MM-DD HH:MM」（工具函数在 utils 里）。"""
    try:
        from .utils import local_datetime

        return local_datetime(value)
    except Exception:
        return str(value or "").replace("T", " ")[:16]


def todo_rows(data: Any) -> List[Dict[str, Any]]:
    """把 `/users/self/todo` 的返回整理成「最近作业」要的行。

    dash 页面右侧 To Do 显示的就是这份数据，每条的形如::

        {"type": "submitting",
         "assignment": {"name": ..., "due_at": ..., "html_url": ...},
         "course_id": 123, "context_name": "高等数学", "html_url": ...}

    有的是 `quiz`（小测）而不是 `assignment`，两种都认；同一条重复出现只留一条。
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        body = item.get("assignment")
        if not isinstance(body, dict):
            body = item.get("quiz")
        if not isinstance(body, dict):
            body = {}
        title = str(body.get("name") or body.get("title")
                    or item.get("name") or "").strip()
        if not title:
            continue
        course = str(item.get("context_name") or item.get("course_name")
                     or "").strip()
        due = str(body.get("due_at") or item.get("due_at") or "").strip()
        key = (title, course)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": title,
            "course": course,
            "due": _local_due(due),
            "due_at": due,
            "url": str(item.get("html_url") or body.get("html_url") or ""),
            "kind": _todo_kind(str(item.get("type") or "")),
        })
    out.sort(key=lambda row: (row.get("due") or "9999-99-99", row.get("title") or ""))
    return out


def todo_items(client, timeout: float = 20.0) -> List[Dict[str, Any]]:
    """读 eLearning dash 的 To Do（最近要交什么、哪天交）。"""
    return todo_rows(client.get_json(TODO_API, timeout=timeout))


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
    # 非法字符统一换成 `-`，与 Rust 版 course_dir_name 的规则保持一致，
    # 这样同一门课在两个版本里下载下来的目录名完全相同。
    cleaned = "".join("-" if char in '\\/:*?"<>|' else char for char in (name or ""))
    cleaned = cleaned.strip().strip(".")
    return cleaned or "未命名"


# ------------------------------------------------------------------ 批量下载
def download_all(client: ElearningClient, items: List[Dict[str, Any]],
                 target_dir: Path, course: Optional[str] = None,
                 on_progress=None) -> Dict[str, Any]:
    """批量下载；**重名的跳过**（不覆盖、不重复下载）。

    `course` 传课程名时存到 ``target_dir/<课程名>/<文件名>``：下载目录按
    来源课程分二级文件夹，一学期的课件不再摊成一整层。不传时行为和以前
    一样（平铺），老调用点不用改。
    """
    target_dir = Path(target_dir)
    if course:
        target_dir = target_dir / safe_filename(str(course))
    target_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {"saved": [], "skipped": [], "failed": [],
                              "dir": str(target_dir)}
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


def download_by_course(client: ElearningClient, items: List[Dict[str, Any]],
                       target_dir: Path, on_progress=None) -> Dict[str, Any]:
    """一次下载多门课的文件：按课程分组，各自落到自己的课程文件夹。

    为什么需要：「一键下载」收的是所有课的清单，直接平铺又回到全堆一层；
    这里按 item 里的 `course_name`（没有就用课程 id）分组后逐组调用
    `download_all`。返回值结构和 `download_all` 一样，另加 `dirs` 列出
    本次用到的子目录。
    """
    root = Path(target_dir)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        course = str(item.get("course_name") or item.get("course")
                     or item.get("course_id") or "").strip()
        groups.setdefault(course, []).append(item)

    merged: Dict[str, Any] = {"saved": [], "skipped": [], "failed": [],
                              "dir": str(root), "dirs": []}
    total = sum(len(group) for group in groups.values())
    done = 0
    for course, group in groups.items():
        def _progress(index: int, _count: int, name: str, base: int = done) -> None:
            if on_progress is not None:
                on_progress(base + index, total, name)

        result = download_all(client, group, root, course=course or None,
                              on_progress=_progress if on_progress else None)
        done += len(group)
        for key in ("saved", "skipped", "failed"):
            merged[key].extend(result.get(key) or [])
        used = result.get("dir") or ""
        if used and used not in merged["dirs"]:
            merged["dirs"].append(used)
    return merged


# ---------------------------------------------------------------- 本地索引
#: 「这个作业/这门课的文件，本地叫什么」——下载完记一笔，打开时就不用猜
FILE_INDEX_KEY = "elearning_file_index"
#: 索引最多留多少条（190 多个文件也就一两百条，留余量）
FILE_INDEX_MAX = 2000
#: 「打开」时最多列几个候选文件让人挑
MAX_CANDIDATES = 12

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_number(text: str) -> Optional[int]:
    """把「八」「十二」「二十一」这种中文数字读成阿拉伯数字。"""
    value = str(text or "").strip()
    if not value or any(ch not in _CN_DIGITS and ch != "十" for ch in value):
        return None
    if "十" not in value:
        return _CN_DIGITS.get(value) if len(value) == 1 else None
    left, _, right = value.partition("十")
    if left and len(left) > 1:
        return None
    if right and len(right) > 1:
        return None
    tens = _CN_DIGITS.get(left, 1) if left else 1
    ones = _CN_DIGITS.get(right, 0) if right else 0
    return tens * 10 + ones


def number_tokens(text: str) -> List[str]:
    """抽出文字里的「编号」：阿拉伯数字整段 + 中文数字，用来对齐「第 8 周」和 `HW8.2`。"""
    value = str(text or "")
    tokens = re.findall(r"\d+", value)
    for run in re.findall(r"[零一二两三四五六七八九十]{1,3}", value):
        number = _cn_number(run)
        if number is not None:
            tokens.append(str(number))
    out: List[str] = []
    for token in tokens:
        if token and token not in out:
            out.append(token)
    return out


def load_file_index(store) -> List[Dict[str, str]]:
    """读「课程/作业 → 本地文件名」的索引（没有就是空表）。"""
    if store is None:
        return []
    try:
        import json

        raw = store.get_setting(FILE_INDEX_KEY, "") or ""
        data = json.loads(raw) if raw.strip() else []
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [dict(entry) for entry in data if isinstance(entry, dict)]


def save_file_index(store, entries: List[Dict[str, str]]) -> None:
    if store is None:
        return
    import json

    trimmed = [dict(entry) for entry in entries][-FILE_INDEX_MAX:]
    try:
        store.set_setting(FILE_INDEX_KEY, json.dumps(trimmed, ensure_ascii=False))
    except Exception:
        pass


def _download_rel(root: Path, name: str, course: str = "") -> str:
    """文件下载后相对下载目录的路径（「课程/文件名」；没有课程时就是文件名）。

    索引里现在记这个相对路径，否则按课程分了文件夹之后只记文件名会对不上。
    """
    base = Path(root)
    folder = safe_filename(str(course)) if course else ""
    for prefix in ([folder] if folder else []) + [""]:
        path = base / prefix / name if prefix else base / name
        if path.is_file():
            return f"{prefix}/{name}" if prefix else name
    return ""


def remember_files(store, items: List[Dict[str, Any]],
                   target_dir: Path) -> int:
    """把「已经下载到本地的文件」记进索引：课程 / 作业 → 相对路径。

    `items` 里的每一项可以带 ``course`` / ``course_name`` / ``assignment``
    （page_homework 收集清单时会带上）；文件已经存在（跳过重名）的也照记，
    这样以前下过的一批文件也能对上号。返回新增条数。
    """
    root = Path(target_dir)
    entries = load_file_index(store)
    seen = {(str(entry.get("course") or ""), str(entry.get("assignment") or ""),
             str(entry.get("name") or "")) for entry in entries}
    added = 0
    for item in items or []:
        name = safe_filename(str(item.get("name") or ""))
        if not name:
            continue
        rel = _download_rel(root, name,
                            str(item.get("course_name") or item.get("course")
                                or item.get("course_id") or ""))
        if not rel:
            continue
        key = (str(item.get("course") or ""), str(item.get("assignment") or ""), rel)
        if key in seen:
            continue
        entries.append({"course": key[0], "assignment": key[1], "name": rel,
                        "course_name": str(item.get("course_name") or "")})
        seen.add(key)
        added += 1
    if added:
        save_file_index(store, entries)
    return added


def find_local_file(root: Path, names: List[str],
                    recursive: bool = True) -> Optional[Path]:
    """在下载目录里按文件名找文件：先精确，再忽略大小写，最后「包含」。"""
    base = Path(root)
    if not base.is_dir():
        return None
    wanted = []
    for value in names or []:
        text = str(value or "").strip()
        if not text:
            continue
        for candidate in (text, safe_filename(text)):
            if candidate and candidate not in wanted:
                wanted.append(candidate)
    if not wanted:
        return None
    # ① 精确（目录里直接就有）
    for text in wanted:
        path = base / text
        if path.is_file():
            return path
    # ② 忽略大小写 / 顺带看一层子目录
    files: List[Path] = []
    try:
        for path in sorted(base.iterdir()):
            if path.is_file():
                files.append(path)
    except OSError:
        return None
    lowered = {path.name.lower(): path for path in files}
    for text in wanted:
        hit = lowered.get(text.lower())
        if hit is not None:
            return hit
    # ③ 文件名里包含作业名（或反过来），或去掉扩展名后相同
    for text in wanted:
        key = text.lower()
        stem_key = Path(key).stem
        if len(key) < 2:
            continue
        for path in files:
            low = path.name.lower()
            stem = path.stem.lower()
            if low == key or stem == key or stem == stem_key:
                return path
            if len(stem_key) >= 3 and (stem_key in stem or stem in stem_key):
                return path
    # ④ 子目录里再找一遍（有人把文件按课程分了文件夹）
    if recursive:
        try:
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                low = path.name.lower()
                for text in wanted:
                    key = text.lower()
                    if low == key or (len(key) >= 3 and key in low):
                        return path
        except OSError:
            return None
    return None


def _indexed_path(root: Path, name: str) -> Optional[Path]:
    """索引里的名字 → 实际文件；现在可能是「课程/文件.pdf」这种相对路径。"""
    direct = Path(root) / str(name or "")
    if direct.is_file():
        return direct
    return find_local_file(Path(root), [str(name or "")])


def files_for(store, root: Path, course: str = "",
              assignment: str = "") -> List[Path]:
    """索引里这个作业（或这门课）对应的本地文件，按存在的挑出来。"""
    base = Path(root)
    names: List[str] = []
    for entry in load_file_index(store):
        entry_course = str(entry.get("course") or "")
        entry_assignment = str(entry.get("assignment") or "")
        if assignment and entry_assignment == str(assignment) and (
                not course or entry_course == str(course)):
            names.append(str(entry.get("name") or ""))
        elif not assignment and course and entry_course == str(course):
            names.append(str(entry.get("name") or ""))
    found: List[Path] = []
    for name in names:
        hit = _indexed_path(base, name)
        if hit is not None and hit not in found:
            found.append(hit)
    return found


def local_file_candidates(store, root: Path, item: Dict[str, Any]) -> List[Path]:
    """给一条作业列出「它在本地的文件」，靠得住排前面、猜出来的排后面。

    顺序：索引里记过的（这个作业）→ 附件名 / 作业名对得上 → 同课程里编号对得上
    （「第八周作业」→ `HW8.*`）。真找不到就返回空表，别硬塞一个错文件。
    """
    base = Path(root)
    found: List[Path] = []

    def add(path: Optional[Path]) -> None:
        if path is not None and Path(path).is_file() and Path(path) not in found:
            found.append(Path(path))

    course = str(item.get("course") or item.get("course_id") or "")
    assignment = str(item.get("assignment") or item.get("id") or "")
    name = str(item.get("name") or "")
    if assignment:
        for path in files_for(store, base, course, assignment):
            add(path)
    attachments = [str(one.get("name") or "") for one in
                   (item.get("attachments") or []) if isinstance(one, dict)]
    for value in attachments + [name]:
        if value:
            add(find_local_file(base, [value]))
    tokens = set(number_tokens(name))
    if found or not tokens:
        # 名字已经对上了（或名字里压根没有编号）就别再按编号扩散，
        # 否则「HW8.2.pdf」会把同周的 HW8.1 也拉进来
        return found
    if course:
        for entry in load_file_index(store):
            if str(entry.get("course") or "") != course:
                continue
            file_name = str(entry.get("name") or "")
            if tokens & set(number_tokens(file_name)):
                add(_indexed_path(base, file_name))
    # ④ 索引里还没有（升级前就下过的一批文件）：直接扫下载目录，按编号对一对
    if not found:
        try:
            listing = sorted(path for path in base.rglob("*")
                             if path.is_file()
                             and len(path.relative_to(base).parts) <= 3)
        except OSError:
            listing = []
        for path in listing:
            if len(found) >= MAX_CANDIDATES:
                break
            if tokens & set(number_tokens(path.stem)):
                add(path)
    return found


def local_file_for(store, root: Path, item: Dict[str, Any]) -> Optional[Path]:
    """只有一个候选文件时才返回它（多个候选让人自己挑，别猜）。"""
    found = local_file_candidates(store, root, item)
    return found[0] if len(found) == 1 else None
