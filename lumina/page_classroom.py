# -*- coding: utf-8 -*-
"""空教室：左边学校网页（自己查），右边是用时间轴画出来的结果。

时间轴的样子参考日历周视图：一行一个教室，横轴是节次；**占用是红色块（带课程名）**，
空闲是绿的（或空着）。选教学楼就看整栋楼，选教室就看那一间，选时间就只看那一段。
"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from . import dashboard, ehall_bridge, icons, theme
from .classroom_view import ClassroomPanel
from .theme import BORDER, CARD, MUTED
from .web_panel import EmbeddedWebPanel
from .widgets import Card, SoftButton

URL_KEY = "ehall_empty_room_url"
#: 是否显示左边的学校网页（默认不显示：抓取在后台照样跑）
WEB_KEY = "classroom_show_web"


class ClassroomPage(QWidget):
    """左边看学校网页，右边看时间轴。"""

    status_text = Signal(str)
    query_requested = Signal(str, str, str)      # 校区, 教学楼, 教室

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._rows: List[List[str]] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("空教室")
        title.setObjectName("H1")
        self.subtitle = QLabel(
            "选教学楼 →（可选）教室 / 时间 → 点「查看」；右边会画出每间教室的占用情况")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.scrape_button = SoftButton("抓取当前页面", "primary", glyph=icons.REFRESH,
                                        height=32, min_width=124, point_size=8.8)
        self.scrape_button.setToolTip("把左边网页里现在这张表抓到右边的时间轴")
        self.scrape_button.clicked.connect(self.scrape)
        header.addWidget(self.scrape_button)
        self.home_button = SoftButton("设为首页", "ghost", height=32, min_width=92,
                                      point_size=8.8)
        self.home_button.setToolTip("把当前网页地址记下来，下次打开直接进这个查询页")
        self.home_button.clicked.connect(self.save_home)
        header.addWidget(self.home_button)
        self.reload_button = SoftButton("刷新", "ghost", height=32, min_width=76,
                                        point_size=8.8)
        self.reload_button.clicked.connect(self.reload)
        header.addWidget(self.reload_button)
        # 用户：「这里不需要把你打开的 ehall 给我看」—— 学校网页默认收起来，
        # 抓取在后台照样跑；只有要手动登录 / 换查询页时才点开
        self.web_toggle = SoftButton("显示网页", "ghost", height=32, min_width=96,
                                     point_size=8.8)
        self.web_toggle.setToolTip(
            "平时不用看学校的网页（抓取在后台照样进行）；\n"
            "需要手动登录 ehall 或换一个查询页时，再把它打开")
        self.web_toggle.clicked.connect(self.toggle_web)
        header.addWidget(self.web_toggle)
        root.addLayout(header)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_edit = QLineEdit(self._home_url())
        self.url_edit.setPlaceholderText("空闲教室查询页面的地址")
        self.url_edit.setFixedHeight(32)
        self.url_edit.returnPressed.connect(self.open_url)
        url_row.addWidget(self.url_edit, 1)
        self.open_button = SoftButton("打开", "ghost", height=32, min_width=68,
                                      point_size=8.8)
        self.open_button.clicked.connect(self.open_url)
        url_row.addWidget(self.open_button)
        root.addLayout(url_row)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.web = EmbeddedWebPanel("lumina-classroom", "classroom",
                                    self._home_url(), self, store=store)
        self.web.loaded.connect(self._on_loaded)
        body.addWidget(self.web, 1)
        self._body = body

        right = Card(radius=18, fill=CARD, border=BORDER, padding=14, spacing=8)
        right.setMinimumWidth(380)
        self.panel = ClassroomPanel(store)
        self.panel.set_campuses(dashboard.campus_buildings_map(store))
        self.panel.set_rooms(dashboard.empty_room_rooms(
            store, self.panel.building()))
        self.panel.query_requested.connect(self.query_requested.emit)
        right.body.addWidget(self.panel, 1)
        body.addWidget(right, 1)
        root.addLayout(body, 1)

        # 兼容老接口（测试 / 外部脚本按这些名字取）
        self.push_button = self.panel.view_button
        self.count_label = self.panel.status
        self.hint_label = self.panel.status
        self.result_layout = self.panel.board.timeline

        import os

        if not os.environ.get("LUMINA_SMOKE"):
            try:
                self.web.ensure_view()
            except Exception:
                pass
        # 默认把学校网页收起来：只留结果时间轴（抓取仍走后台那个页面）
        self._web_visible = False
        self._set_web_visible(self.store.get_setting(WEB_KEY, "0") == "1")

    # -------------------------------------------------- 网页显示 / 隐藏
    def toggle_web(self) -> None:
        self._set_web_visible(not getattr(self, "_web_visible", False))

    def _set_web_visible(self, show: bool) -> None:
        """显示 / 收起左边的学校网页（收起来时抓取照样进行）。"""
        self._web_visible = bool(show)
        try:
            self.web.setVisible(bool(show))
        except Exception:
            pass
        try:
            self.web_toggle.setText("隐藏网页" if show else "显示网页")
        except Exception:
            pass
        if show:
            # 要给人看的时候得有尺寸（被隐藏过的 QWebEngineView 可能没布局）
            try:
                self.web.resize(1000, 760)
                self.web.ensure_view()
            except Exception:
                pass
        try:
            self.store.set_setting(WEB_KEY, "1" if show else "0")
        except Exception:
            pass

    # -------------------------------------------------- 地址
    def _home_url(self) -> str:
        try:
            url = str(self.store.get_setting(URL_KEY, "") or "").strip()
        except Exception:
            url = ""
        return url or dashboard.EHALL_HOME

    def open_url(self) -> None:
        url = (self.url_edit.text() or "").strip() or self._home_url()
        if not url.startswith("http"):
            url = "https://" + url
        self.url_edit.setText(url)
        try:
            self.web.load(url)
            self.subtitle.setText("正在打开 %s" % url)
        except Exception:
            self.subtitle.setText("这个地址打不开")

    def save_home(self) -> None:
        url = (self.web.address.text() or "").strip() or self.url_edit.text().strip()
        if not url:
            return
        try:
            self.store.set_setting(URL_KEY, url)
            # 顺便把仪表板的空教室卡片指到这个地址
            self.store.set_setting("ehall_json_empty_room", url)
        except Exception:
            pass
        self.url_edit.setText(url)
        self.subtitle.setText("已记住这个地址，下次打开直接进")

    def reload(self) -> None:
        try:
            self.web.reload()
        except Exception:
            pass

    def _on_loaded(self, ok: bool) -> None:
        if ok:
            self.subtitle.setText("网页已就绪 · 选好查询条件后点「查看」，或直接抓当前页面")
        else:
            self.subtitle.setText("网页没加载成功，检查网络 / VPN，或登录 ehall 后重试")

    # -------------------------------------------------- 抓当前页面
    def scrape(self) -> None:
        view = self.web.view()
        page = None
        try:
            page = view.page() if view is not None else None
        except Exception:
            page = None
        if page is None:
            self._set_status("网页还没打开，先点「打开」")
            return
        self._set_status("正在读页面…")
        try:
            page.runJavaScript(ehall_bridge.CLASSROOM_JS, self._on_scraped)
        except Exception as exc:  # noqa: BLE001
            self._set_status("抓取失败：%s" % str(exc)[:60])

    def _on_scraped(self, value) -> None:
        payload = ehall_bridge.parse_classroom_grid(
            str(value or ""), self.panel.building(), self.panel.room())
        self.panel.apply_result(payload)
        if payload.get("ok"):
            self._set_status(str(payload.get("value") or "抓到结果"))
        else:
            self._set_status(str(payload.get("error") or "页面上没找到教室表"))

    # -------------------------------------------------- 结果
    def apply_result(self, payload: Dict[str, Any]) -> None:
        """桥查回来的结果（主窗口转过来）。"""
        self.panel.apply_result(payload)
        rooms = list(payload.get("rooms") or []) if isinstance(payload, dict) else []
        self._rows = [[str(item.get("name") or "")]
                      + [str(cell.get("label") or "") for cell in
                         (item.get("cells") or [])]
                      for item in rooms]
        if payload.get("ok"):
            self._set_status(str(payload.get("value") or "已更新"))
        else:
            self._set_status(str(payload.get("error") or "没查到"))

    def push_to_dashboard(self) -> None:
        """把现在时间轴上的结果送到仪表板的「空教室」卡片。"""
        rooms = list(self.panel.board.timeline.rooms())
        if not rooms:
            self._set_status("还没有可送的结果，先查一次")
            return
        import time as _time

        payload = {"ok": True, "rooms": rooms, "rows": [], "lines": [],
                   "building": self.panel.building(), "room": self.panel.room(),
                   "value": "%d 间教室" % len(rooms),
                   "error": "", "url": "", "ts": _time.time()}
        ehall_bridge.cache_put(self.store, "empty_room", payload)
        self._set_status("已送到仪表板的「空教室」卡片")

    def _set_status(self, text: str) -> None:
        self.subtitle.setText(text)
        try:
            self.status_text.emit(text)
        except Exception:
            pass
