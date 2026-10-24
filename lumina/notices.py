"""教务通知监控：定时抓取通知列表，有新的就提醒。

抓取方式：用内嵌浏览器（QtWebEngine）打开列表页再读 DOM。
为什么不用 requests：教务处/教学门户的列表页是 JS 渲染 + WAF 保护的，
直接请求只会拿到「提示信息」页。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from . import fdu

# 通知来源（可以继续加）
SOURCES: List[Dict[str, str]] = [
    {"id": "jwc_gzdt", "name": "教务处 · 工作动态", "category": "教务",
     "url": "https://jwc.fudan.edu.cn/gzdt/list.htm"},
    {"id": "news_notice", "name": "新闻文化网 · 通知公告", "category": "通知",
     "url": "https://news.fudan.edu.cn/45/list.htm"},
    {"id": "news_dynamic", "name": "新闻文化网 · 一线动态", "category": "动态",
     "url": "https://news.fudan.edu.cn/40/list.htm"},
]

DEFAULT_INTERVAL_HOURS = 3.0
DEFAULT_SOURCES = ["jwc_gzdt", "news_notice", "news_dynamic"]

_JS_LIST = r"""
(function () {
  var DATE_RE = /(\d{4})[-\/\.年](\d{1,2})[-\/\.月](\d{1,2})|(\d{1,2})[-\/\.](\d{1,2})/;
  function dateNear(a) {
    // 先找列表项容器，再逐层往上找 6 层；日期可能在兄弟节点、也可能在 span.date 里
    var node = a.closest('li, tr, article, .item, .list-item, .news-item, .news_list li, p')
               || a.parentElement;
    for (var depth = 0; depth < 6 && node; depth++) {
      var text = (node.innerText || node.textContent || '').replace(/\s+/g, ' ');
      var match = text.match(DATE_RE);
      if (match) { return match[0]; }
      var stamps = node.querySelectorAll('.date, .time, .riqi, span, em, i, time');
      for (var k = 0; k < stamps.length; k++) {
        var value = (stamps[k].innerText || stamps[k].textContent || '').trim();
        if (value && value.length <= 24 && DATE_RE.test(value)) { return value; }
      }
      node = node.parentElement;
    }
    return '';
  }
  var out = [];
  var seen = {};
  var nodes = document.querySelectorAll('a');
  for (var i = 0; i < nodes.length; i++) {
    var a = nodes[i];
    var text = (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim();
    var href = a.href || '';
    if (text.length < 6) { continue; }
    if (href.indexOf('javascript') >= 0) { continue; }
    if (href.indexOf('list.htm') >= 0 || href.indexOf('main.htm') >= 0) { continue; }
    if (href.indexOf('/page.htm') < 0 && href.indexOf('/info/') < 0) { continue; }
    if (seen[href]) { continue; }
    seen[href] = true;
    out.push({title: text.slice(0, 120), url: href, date: dateNear(a)});
  }
  return JSON.stringify({url: location.href, title: document.title, items: out.slice(0, 40)});
})()
"""


# ------------------------------------------------------------------ 发布时间
_MONTHS = ("%02d" % m for m in range(1, 13))


def normalize_published(raw: str) -> str:
    """把网页上五花八门的日期写法统一成 YYYY-MM-DD；认不出来就返回空串。"""
    import re
    from datetime import date

    text = (raw or "").strip()
    if not text:
        return ""
    full = re.search(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", text)
    if full:
        year, month, day = (int(part) for part in full.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
    short = re.search(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)", text)
    if short:
        month, day = (int(part) for part in short.groups())
        today = date.today()
        for year in (today.year, today.year - 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if candidate <= today or year < today.year:
                return candidate.isoformat()
        return ""
    return ""


# ------------------------------------------------------------------ 抓取
def fetch_source(url: str, timeout_s: float = 20.0,
                 parent=None) -> List[Dict[str, str]]:
    """用内嵌浏览器打开一个列表页，返回 [{title, url}]。"""
    from PySide6.QtWidgets import QApplication
    try:
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception:
        return []

    app = QApplication.instance()
    if app is None:
        return []

    profile = QWebEngineProfile("lumina-notice", app)
    storage = fdu.browser_dir()
    try:
        from .web_panel import clean_stale_locks

        clean_stale_locks(storage)
    except Exception:
        pass
    profile.setPersistentStoragePath(str(storage))
    profile.setCachePath(str(storage / "cache"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    view = QWebEngineView(parent)
    try:
        view.setPage(QWebEnginePage(profile, view))
    except Exception:
        return []
    state = {"loaded": False, "raw": None}
    view.loadFinished.connect(lambda ok: state.update(loaded=True))
    view.setUrl(QUrl(url))

    deadline = time.time() + timeout_s
    while time.time() < deadline and not state["loaded"]:
        app.processEvents()
        time.sleep(0.02)
    # 等 JS 把列表渲染出来
    settle = time.time() + 2.5
    while time.time() < settle:
        app.processEvents()
        time.sleep(0.02)

    view.page().runJavaScript(_JS_LIST, lambda raw: state.update(raw=raw))
    deadline = time.time() + 8
    while time.time() < deadline and state["raw"] is None:
        app.processEvents()
        time.sleep(0.02)

    items: List[Dict[str, str]] = []
    raw = state.get("raw")
    if isinstance(raw, str) and raw.strip():
        import json

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        for item in data.get("items") or []:
            title = str(item.get("title") or "").strip()
            href = str(item.get("url") or "").strip()
            if not title or not href:
                continue
            if len(title) > 70:      # 首页横幅之类的长句，不是通知标题
                continue
            if title in ("首页Home", "首页 Home"):
                continue
            published = normalize_published(str(item.get("date") or ""))
            items.append({"title": title, "url": href, "published_at": published})
    try:
        view.stop()
        view.setPage(None)
        view.page().deleteLater()
        view.setParent(None)
        view.deleteLater()
    except Exception:
        pass
    try:
        profile.deleteLater()
    except Exception:
        pass
    return items


def selected_sources(store) -> List[Dict[str, str]]:
    raw = store.get_setting("notice_sources", ",".join(DEFAULT_SOURCES)) or ""
    ids = [item for item in raw.split(",") if item]
    picked = [source for source in SOURCES if source["id"] in ids]
    return picked or [SOURCES[0]]


def interval_hours(store) -> float:
    try:
        value = float(store.get_setting("notice_interval_hours",
                                       str(DEFAULT_INTERVAL_HOURS)) or 0)
    except ValueError:
        value = DEFAULT_INTERVAL_HOURS
    return max(0.25, min(48.0, value))


def sync(store, parent=None) -> List[Dict[str, Any]]:
    """抓一轮通知，返回**新**的通知列表。"""
    new_items: List[Dict[str, Any]] = []
    for source in selected_sources(store):
        try:
            items = fetch_source(source["url"], parent=parent)
        except Exception:
            continue
        for item in items:
            try:
                added = store.add_notice(
                    source["id"], item["title"], item["url"],
                    item.get("published_at", ""))
            except Exception:
                continue
            if added:
                new_items.append({"source": source["id"], "source_name": source["name"],
                                  "title": item["title"], "url": item["url"]})
    store.set_setting("notice_last_run", time.strftime("%Y-%m-%d %H:%M"))
    store.set_setting("notice_last_count", str(len(new_items)))
    return new_items


# ------------------------------------------------------------------ 定时器
class NoticeWatcher(QObject):
    """每隔一段时间检查一次，有新通知就发信号。"""

    new_notices = Signal(list)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setInterval(5 * 60 * 1000)      # 每 5 分钟看一次「到点没有」
        self._timer.timeout.connect(self._maybe_refresh)
        self._started = False

    # -------------------------------------------------- 控制
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._timer.start()
        QTimer.singleShot(12_000, self._maybe_refresh)

    def stop(self) -> None:
        self._timer.stop()

    def enabled(self) -> bool:
        return (self.store.get_setting("notice_enabled", "1") or "1") == "1"

    def _due(self) -> bool:
        last = self.store.get_setting("notice_last_run", "") or ""
        if not last:
            return True
        try:
            last_ts = time.mktime(time.strptime(last, "%Y-%m-%d %H:%M"))
        except ValueError:
            return True
        return (time.time() - last_ts) >= interval_hours(self.store) * 3600

    def _maybe_refresh(self) -> None:
        if self._busy or not self.enabled() or not self._due():
            return
        self.refresh()

    # -------------------------------------------------- 抓取
    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            items = sync(self.store, self.parent())
        except Exception:
            items = []
        finally:
            self._busy = False
        if items:
            self.new_notices.emit(items)


# ------------------------------------------------------------------ 桌面提醒
def make_notice_toast(items: List[Dict[str, Any]], parent=None):
    """新通知的桌面提醒卡片（返回窗口对象，调用方负责 show）。"""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

    from . import icons, theme
    from .widgets import ElidedLabel, IconBadge, IconButton, ShadowPanel, SoftButton

    width, height = 392, 206
    toast = QWidget(
        parent,
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.NoDropShadowWindowHint,
    )
    toast.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    toast.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    toast.setFixedSize(width, height)

    outer = QVBoxLayout(toast)
    outer.setContentsMargins(12, 12, 12, 12)
    panel = ShadowPanel(toast, radius=theme.WINDOW_RADIUS, shadow=12, glow=False, flat=True)
    outer.addWidget(panel)

    layout = QVBoxLayout(panel)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(8)

    top = QHBoxLayout()
    top.setSpacing(11)
    top.addWidget(IconBadge(icons.BELL, size=36,
                            color=theme.WARNING,
                            bg_alpha=0.16, radius=11, glyph_size=15))
    text_box = QVBoxLayout()
    text_box.setSpacing(1)
    title = ElidedLabel(f"教务通知 · {len(items)} 条新的")
    title_font = QFont(toast.font())
    title_font.setPixelSize(14)
    title_font.setWeight(QFont.Weight.DemiBold)
    title.setFont(title_font)
    text_box.addWidget(title)
    source = items[0].get("source_name", "") if items else ""
    meta = QLabel(source)
    meta.setObjectName("Tiny")
    text_box.addWidget(meta)
    top.addLayout(text_box, 1)
    close = IconButton(icons.CLOSE, size=26, glyph_size=9, tip="关闭")
    close.clicked.connect(toast.close)
    top.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
    layout.addLayout(top)

    for item in items[:2]:
        line = QLabel("· " + str(item.get("title", ""))[:42])
        line.setObjectName("Small")
        line.setWordWrap(True)
        layout.addWidget(line)
    if len(items) > 2:
        more = QLabel(f"…… 另外还有 {len(items) - 2} 条")
        more.setObjectName("Tiny")
        layout.addWidget(more)

    layout.addStretch(1)
    row = QHBoxLayout()
    row.addStretch(1)
    ok = SoftButton("知道了", "primary", height=30, point_size=8.8)
    ok.clicked.connect(toast.close)
    row.addWidget(ok)
    layout.addLayout(row)

    timer = QTimer(toast)
    timer.setSingleShot(True)
    timer.timeout.connect(toast.close)
    timer.start(30000)

    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        toast.move(area.right() - width - 18, area.bottom() - height - 18)
    return toast
