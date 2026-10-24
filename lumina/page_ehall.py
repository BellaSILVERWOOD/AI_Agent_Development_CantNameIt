"""eHall（复旦网上办事大厅）：内嵌网页，登录状态会保留。"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .web_panel import EmbeddedWebPanel
from .widgets import SoftButton

EHALL_URL = "https://ehall.fudan.edu.cn/"
HOME_KEYS = ("ehall_home",)


class EhallPage(QWidget):
    """把 eHall 直接嵌进来，不弹独立窗口。"""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("eHall")
        title.setObjectName("H1")
        self.subtitle = QLabel("复旦网上办事大厅 · 登录状态会保留，过期可在网页里重新登录")
        self.subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        self.home_button = SoftButton("回到首页", "ghost", height=32, min_width=96,
                                      point_size=8.8)
        self.home_button.clicked.connect(self.go_home)
        header.addWidget(self.home_button)
        self.reload_button = SoftButton("刷新", "ghost", height=32, min_width=76,
                                        point_size=8.8)
        self.reload_button.clicked.connect(self.reload)
        header.addWidget(self.reload_button)
        root.addLayout(header)

        url = (store.get_setting("ehall_url", "") or "").strip() or EHALL_URL
        self.web = EmbeddedWebPanel("lumina-ehall", "ehall", url, self,
                                    store=store)
        self.web.loaded.connect(self._on_loaded)
        root.addWidget(self.web, 1)

        import os

        if not os.environ.get("LUMINA_SMOKE"):
            try:
                self.web.ensure_view()
            except Exception:
                pass

    def _on_loaded(self, ok: bool) -> None:
        if ok:
            self.subtitle.setText("eHall 已就绪 · 登录一次之后不用重复登录")
        else:
            self.subtitle.setText("eHall 加载失败，检查网络或 VPN")

    def go_home(self) -> None:
        url = (self.store.get_setting("ehall_url", "") or "").strip() or EHALL_URL
        try:
            self.web.load(url)
        except Exception:
            pass

    def reload(self) -> None:
        try:
            self.web.reload()
        except Exception:
            pass

    def sync_fullscreen_state(self, on: bool) -> None:
        try:
            self.web.sync_fullscreen_state(on)
        except Exception:
            pass
