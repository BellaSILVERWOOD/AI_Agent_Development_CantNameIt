"""通知页：所有通知（不含日程），可按来源多选筛选、只看未读、搜索。"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget,
)

from . import icons, notices, theme
from .widgets import Card, Pill, SoftButton, ToggleSwitch, make_scroll
from .widgets_filter import MultiSelectFilter


CATEGORY_COLORS = {
    "教务": "sky",
    "活动": "amber",
    "讲座": "violet",
    "图书馆": "teal",
    "其他": "rose",
}


class NoticeRow(QWidget):
    """一条通知。"""

    def __init__(self, notice: Dict[str, Any], source_label: str,
                 category: str, parent=None):
        super().__init__(parent)
        self.notice = notice
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        self.dot = QLabel("●")
        self.dot.setFixedWidth(12)
        unread = not notice.get("read_at")
        self.dot.setStyleSheet(
            f"color: {theme.ACCENT}; font-size: 9px;" if unread
            else f"color: {theme.FAINT}; font-size: 9px;")
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignTop)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title = QLabel(str(notice.get("title", "")))
        title.setWordWrap(True)
        font = QFont(self.font())
        font.setPointSizeF(10.0)
        font.setWeight(QFont.Weight.DemiBold if unread else QFont.Weight.Normal)
        title.setFont(font)
        title.setStyleSheet(f"color: {theme.TEXT};")
        text_box.addWidget(title)
        published = str(notice.get("published_at") or "").strip()
        fetched = str(notice.get("fetched_at", ""))[:16]
        stamp = published or fetched
        if published:
            meta = QLabel(f"{source_label} · 发布于 {stamp}")
        else:
            meta = QLabel(f"{source_label} · 抓取于 {stamp}")
        meta.setObjectName("Tiny")
        text_box.addWidget(meta)
        layout.addLayout(text_box, 1)

        color = CATEGORY_COLORS.get(category, "sky")
        pill = Pill(category or "通知", theme.event_base(color),
                    theme.soft(theme.event_base(color), 0.14), point_size=8.0)
        layout.addWidget(pill, 0, Qt.AlignmentFlag.AlignTop)

    def mouseReleaseEvent(self, event):
        url = str(self.notice.get("url", ""))
        if event.button() == Qt.MouseButton.LeftButton and url:
            QDesktopServices.openUrl(QUrl(url))
        super().mouseReleaseEvent(event)


class NoticesPage(QWidget):
    """通知列表页。"""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._rows: List[NoticeRow] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("通知")
        title.setObjectName("H1")
        self.subtitle = QLabel("教务、活动等通知都在这里（日程不在此列）")
        self.subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        self.unread_pill = Pill("未读 0", theme.ACCENT, theme.soft(theme.ACCENT, 0.14))
        header.addWidget(self.unread_pill)

        self.read_button = SoftButton("全部标为已读", "ghost", height=32,
                                      min_width=118, point_size=8.8)
        self.read_button.clicked.connect(self._mark_all_read)
        header.addWidget(self.read_button)
        self.refresh_button = SoftButton("立即抓取", "primary", height=32,
                                         min_width=104, glyph=icons.REFRESH,
                                         point_size=8.8)
        self.refresh_button.clicked.connect(self.refresh_now)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        # ---------------- 筛选行
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.source_filter = MultiSelectFilter(
            [(source["id"], source["name"]) for source in notices.SOURCES],
            placeholder="来源")
        self.source_filter.changed.connect(lambda _keys: self.reload())
        filter_row.addWidget(self.source_filter)

        self.category_filter = MultiSelectFilter(
            self._category_items(), placeholder="分类")
        self.category_filter.changed.connect(lambda _keys: self.reload())
        filter_row.addWidget(self.category_filter)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索标题关键词…")
        self.search_edit.setFixedHeight(32)
        self.search_edit.textChanged.connect(lambda _text: self.reload())
        filter_row.addWidget(self.search_edit, 1)

        only_unread_label = QLabel("只看未读")
        only_unread_label.setObjectName("Tiny")
        filter_row.addWidget(only_unread_label)
        self.unread_switch = ToggleSwitch(False)
        self.unread_switch.toggled.connect(lambda _checked: self.reload())
        filter_row.addWidget(self.unread_switch)
        root.addLayout(filter_row)

        # ---------------- 列表
        self.scroll, _body, self.list_layout = make_scroll()
        root.addWidget(self.scroll, 1)
        self.empty_label = QLabel("还没有通知，点右上角「立即抓取」试试")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.reload()

    # -------------------------------------------------- 数据
    def _category_items(self) -> List[tuple]:
        seen = []
        for source in notices.SOURCES:
            category = source.get("category", "其他")
            if (category, category) not in seen:
                seen.append((category, category))
        return seen

    def _source_label(self, source_id: str) -> str:
        for source in notices.SOURCES:
            if source["id"] == source_id:
                return source["name"]
        return source_id

    def _category_of(self, source_id: str) -> str:
        for source in notices.SOURCES:
            if source["id"] == source_id:
                return str(source.get("category", "其他"))
        return "其他"

    # -------------------------------------------------- 渲染
    def reload(self) -> None:
        from .widgets import clear_layout

        clear_layout(self.list_layout)
        self._rows = []
        rows = self.store.list_notices(300)

        sources = set(self.source_filter.selected())
        categories = set(self.category_filter.selected())
        keyword = self.search_edit.text().strip().lower()
        only_unread = self.unread_switch.isChecked()

        shown = 0
        for notice in rows:
            source_id = str(notice.get("source", ""))
            category = self._category_of(source_id)
            if sources and source_id not in sources:
                continue
            if categories and category not in categories:
                continue
            if only_unread and notice.get("read_at"):
                continue
            if keyword and keyword not in str(notice.get("title", "")).lower():
                continue
            row = NoticeRow(notice, self._source_label(source_id), category)
            self.list_layout.addWidget(row)
            self._rows.append(row)
            shown += 1
        if shown == 0:
            self.list_layout.addWidget(self.empty_label)
            self.empty_label.show()
        self.list_layout.addStretch(1)
        self._update_counts(len(rows))

    def _update_counts(self, total: int) -> None:
        unread = self.store.unread_notice_count()
        self.unread_pill.set_text(f"未读 {unread}")
        last = self.store.get_setting("notice_last_run", "") or "还没有抓取过"
        self.subtitle.setText(
            f"共 {total} 条通知 · 未读 {unread} 条 · 上次抓取 {last}（日程不在此列）")

    # -------------------------------------------------- 操作
    def _mark_all_read(self) -> None:
        self.store.mark_notices_read()
        self.reload()

    def refresh_now(self) -> None:
        self.refresh_button.setEnabled(False)
        self.subtitle.setText("正在抓取通知…")
        QTimer.singleShot(50, self._do_refresh)

    def _do_refresh(self) -> None:
        try:
            new_items = notices.sync(self.store, self)
        except Exception as exc:  # noqa: BLE001
            self.subtitle.setText(f"抓取失败：{exc}")
            self.refresh_button.setEnabled(True)
            return
        self.refresh_button.setEnabled(True)
        self.reload()
        if new_items:
            self.subtitle.setText(f"抓到 {len(new_items)} 条新通知")
