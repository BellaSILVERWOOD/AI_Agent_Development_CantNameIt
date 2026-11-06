# -*- coding: utf-8 -*-
"""图书馆数据库窗口：在应用里打开复旦图书馆的数据库导航，并按机构方式登录。

为什么做成一个窗口而不是新页面：数据库这种站点必须用**真实的浏览器内核**（登录态、
Cookie、重定向都在浏览器里），我们已经有 `EmbeddedWebPanel`，直接复用；
主界面导航（侧栏、PAGE_TO_NAV）不用动，风险最小。

机构访问的三种情况：
* **校内网**：数据库直接认校园 IP，打开就能用（默认「校内直连」）；
* **校外**：先用「统一身份认证」登录（id.fudan.edu.cn），再经 WebVPN / libproxy 访问。
  各家前缀不一样，所以给一个「校外访问前缀」输入框（保存后自动套在数据库地址前面）；
* 不开代理：也能打开数据库首页，只是点全文时会要求登录/付费。
"""
from __future__ import annotations

from typing import List

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from . import lit
from .web_panel import EmbeddedWebPanel
from .widgets import BaseDialog, SoftButton

START_URL = lit.DBNav_URL


class LibraryBrowserDialog(BaseDialog):
    """「图书馆数据库」窗口。"""

    def __init__(self, store, parent=None):
        super().__init__(parent, title="图书馆数据库",
                         subtitle="用机构账号访问学校买的数据库（校内直连 / 校外代理）",
                         width=1080, height=760)
        self.store = store
        root = QVBoxLayout()
        self.body.addLayout(root)
        root.setSpacing(8)
        # -------------------------------------------------- 顶部操作
        header = QHBoxLayout()
        header.setSpacing(8)
        self.home_button = SoftButton("数据库导航", "primary", height=32,
                                      min_width=104, point_size=8.8)
        self.home_button.setToolTip("复旦图书馆的数据库导航（按字母 / 学科浏览全部数据库）")
        self.home_button.clicked.connect(self.go_home)
        header.addWidget(self.home_button)
        self.uis_button = SoftButton("统一身份认证", "ghost", height=32,
                                     min_width=112, point_size=8.8)
        self.uis_button.setToolTip(
            "打开 id.fudan.edu.cn 用学号登录；登录后图书馆/数据库那边也会认这个身份")
        self.uis_button.clicked.connect(self.open_uis)
        header.addWidget(self.uis_button)
        self.webvpn_button = SoftButton("WebVPN", "ghost", height=32,
                                        min_width=88, point_size=8.8)
        self.webvpn_button.setToolTip("校外先用 WebVPN 登录，再用下面的「校外访问前缀」访问数据库")
        self.webvpn_button.clicked.connect(self.open_webvpn)
        header.addWidget(self.webvpn_button)
        self.reload_button = SoftButton("刷新", "ghost", height=32, min_width=76,
                                        point_size=8.8)
        self.reload_button.clicked.connect(self.reload)
        header.addWidget(self.reload_button)
        header.addStretch(1)
        root.addLayout(header)

        # -------------------------------------------------- 访问方式
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self.access_button = SoftButton("校内直连", "ghost", height=30,
                                        min_width=96, point_size=8.4)
        self.access_button.setToolTip(
            "校内直连：数据库按校园网 IP 认身份\n"
            "校外代理：把数据库地址套上「校外访问前缀」（WebVPN / libproxy）")
        self.access_button.clicked.connect(self._toggle_access)
        mode_row.addWidget(self.access_button)
        self.prefix_edit = QLineEdit(lit.proxy_prefix(store))
        self.prefix_edit.setPlaceholderText(
            "校外访问前缀，例如 https://libproxy.fudan.edu.cn:8080/LibProxy/?url=")
        self.prefix_edit.setFixedHeight(30)
        self.prefix_edit.editingFinished.connect(self._save_prefix)
        mode_row.addWidget(self.prefix_edit, 1)
        self.save_prefix_button = SoftButton("保存前缀", "ghost", height=30,
                                            min_width=92, point_size=8.4)
        self.save_prefix_button.clicked.connect(self._save_prefix)
        mode_row.addWidget(self.save_prefix_button)
        root.addLayout(mode_row)

        # -------------------------------------------------- 常用数据库
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        chips_label = QLabel("常用：")
        chips_label.setObjectName("Tiny")
        chips_row.addWidget(chips_label)
        self.chips: List[SoftButton] = []
        for item in lit.database_links():
            if item["id"] == "dbnav":
                continue
            chip = SoftButton(item["name"], "ghost", height=28, min_width=76,
                              point_size=8.2)
            chip.setToolTip(item["url"])
            chip.clicked.connect(
                lambda _=False, url=item["url"], name=item["name"]:
                self.open_url(url, name))
            chips_row.addWidget(chip)
            self.chips.append(chip)
        chips_row.addStretch(1)
        root.addLayout(chips_row)

        # -------------------------------------------------- 浏览器
        self.web = EmbeddedWebPanel("lumina-library", "library",
                                    START_URL, self, store=store, autofill=False)
        self.web.loaded.connect(self._on_loaded)
        root.addWidget(self.web, 1)

        self.status = QLabel("提示：数据库的全文/下载由学校购买的权限决定；校外请先登录再套代理前缀。")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self._sync_access_button()
        import os

        if not os.environ.get("LUMINA_SMOKE"):
            try:
                self.web.ensure_view()
            except Exception:
                pass

    # -------------------------------------------------- 状态
    def _sync_access_button(self) -> None:
        mode = lit.access_mode(self.store)
        self.access_button.setText("校外代理" if mode == "prefix" else "校内直连")
        self.access_button.setToolTip(
            "当前：%s\n点一下切换「校内直连 / 校外代理」"
            % ("校外代理（套前缀）" if mode == "prefix" else "校内直连（校园网）"))

    def access_mode(self) -> str:
        return lit.access_mode(self.store)

    def _toggle_access(self) -> None:
        mode = lit.access_mode(self.store)
        lit.set_access_mode(self.store, "campus" if mode == "prefix" else "prefix")
        self._sync_access_button()
        if self.access_mode() == "prefix" and not lit.proxy_prefix(self.store):
            self.status.setText("已切到校外代理：请在右边填「校外访问前缀」并保存。")
        else:
            self.status.setText("已切到：%s" % self.access_button.text())

    def _save_prefix(self) -> None:
        lit.set_proxy_prefix(self.store, self.prefix_edit.text())
        self.status.setText("前缀已保存：%s" % (lit.proxy_prefix(self.store) or "（空）"))

    # -------------------------------------------------- 打开
    def open_url(self, url: str, name: str = "") -> None:
        target = lit.access_url(self.store, url)
        try:
            self.web.load(target)
        except Exception as exc:  # noqa: BLE001
            self.status.setText("打不开 %s：%s" % (name or url, exc))
            return
        self.status.setText("正在打开：%s%s"
                            % (name or target, "" if target == url else "（已套校外前缀）"))

    def go_home(self) -> None:
        self.open_url(lit.DBNav_URL, "数据库导航")

    def open_uis(self) -> None:
        self.open_url(lit.UIS_LOGIN_URL, "统一身份认证")

    def open_webvpn(self) -> None:
        self.open_url(lit.WEBVPN_URL, "WebVPN")

    def reload(self) -> None:
        try:
            self.web.reload()
        except Exception:
            pass

    def _on_loaded(self, ok: bool) -> None:
        if not ok:
            self.status.setText("加载失败：检查网络 / 是否需要先连校园网或 WebVPN")
