"""右侧栏的「文献」面板：搜文献 → 多选 → 一键下载 / 一键引用。

用户要求（第 4 步）：
* 提问时后台检索文献，结果列在边栏里；
* 列表可多选，支持全选 / 全不选；
* 每条能展开看摘要；
* 一键下载（下载进「我的文献库」）、一键引用（GB/T 7714 复制 / EndNote 导出）；
* 本机没装 EndNote 就先问要不要经 ehall 装。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QTextBrowser, QVBoxLayout, QWidget,
)

from . import icons, literature, theme
from .widgets import ElidedLabel, IconButton, SoftButton, make_scroll

MAX_ROWS = 40


class _PaperRow(QWidget):
    """一条文献：勾选 + 标题 + 作者/年份 + 可展开摘要 + 中文概述。"""

    toggled = Signal(int, bool)
    summarize_requested = Signal(int)

    def __init__(self, paper: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.paper = paper
        self._expanded = False
        paper_id = int(paper.get("id") or 0)
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 3, 2, 5)
        root.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.check = QCheckBox()
        self.check.setChecked(False)
        self.check.stateChanged.connect(
            lambda _state: self.toggled.emit(paper_id, self.check.isChecked()))
        head.addWidget(self.check)
        title = str(paper.get("title") or "（无标题）")
        self.title_label = ElidedLabel(title)
        self.title_label.setObjectName("Small")
        self.title_label.setToolTip(title)
        head.addWidget(self.title_label, 1)
        self.expand_button = IconButton(icons.CHEVRON_DOWN, size=22, glyph_size=9,
                                        tip="展开 / 收起摘要")
        self.expand_button.clicked.connect(self.toggle_expand)
        head.addWidget(self.expand_button)
        root.addLayout(head)

        authors = str(paper.get("authors") or "").strip()
        year = str(paper.get("year") or "").strip()
        venue = str(paper.get("venue") or "").strip()
        bits = [bit for bit in (authors[:48], year, venue[:26]) if bit]
        self.meta = QLabel(" · ".join(bits) or "（没有元数据）")
        self.meta.setObjectName("Tiny")
        self.meta.setStyleSheet(f"color: {theme.MUTED};")
        self.meta.setWordWrap(True)
        root.addWidget(self.meta)

        self.abstract_view = QLabel(str(paper.get("abstract") or "（这篇没有摘要）"))
        self.abstract_view.setObjectName("Tiny")
        self.abstract_view.setWordWrap(True)
        self.abstract_view.setStyleSheet(f"color: {theme.TEXT_SOFT};")
        self.abstract_view.setVisible(False)
        root.addWidget(self.abstract_view)

        # 中文概述：有缓存就直接显示，没有就给一个「生成」按钮（按需花 token）
        self.summary_box = QWidget()
        summary_layout = QVBoxLayout(self.summary_box)
        summary_layout.setContentsMargins(0, 2, 0, 0)
        summary_layout.setSpacing(2)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("Tiny")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(f"color: {theme.TEXT_SOFT};")
        summary_layout.addWidget(self.summary_label)
        self.summary_button = SoftButton("生成中文概述", "ghost", height=24,
                                         min_width=104, point_size=8.2)
        self.summary_button.setToolTip(
            "用设置里配置的模型，把这篇文献用中文说清楚「做了什么、有什么特色」"
            "（生成后会存进库里，下次不用再花 token）")
        self.summary_button.clicked.connect(
            lambda: self.summarize_requested.emit(paper_id))
        summary_layout.addWidget(self.summary_button)
        self.summary_box.setVisible(False)
        root.addWidget(self.summary_box)
        self.setObjectName("LitRow")
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        text = str(self.paper.get("summary_zh") or "").strip()
        self.summary_label.setText(("中文概述：" + text) if text else "")
        self.summary_label.setVisible(bool(text))
        self.summary_button.setText("重新生成" if text else "生成中文概述")

    def set_summary(self, text: str) -> None:
        self.paper["summary_zh"] = str(text or "")
        self._refresh_summary()
        self.summary_box.setVisible(self._expanded)

    def toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self.abstract_view.setVisible(self._expanded)
        self.summary_box.setVisible(self._expanded)
        self.expand_button.setToolTip("收起摘要" if self._expanded else "展开摘要")


class _SearchWorker(QWidget):
    """占位类：真正的工作线程用 Qt 的 QThread 之外的方式太麻烦，这里用简单线程 + 信号。"""


class LiteraturePanel(QWidget):
    """搜 + 选 + 下载/引用。"""

    status_changed = Signal(str)
    papers_changed = Signal(list)
    summary_ready = Signal(int, str, str)

    def __init__(self, store, parent=None, compact: bool = True):
        super().__init__(parent)
        self.store = store
        self._papers: List[Dict[str, Any]] = []
        self._rows: List[_PaperRow] = []
        self._selected: Dict[int, bool] = {}
        self._busy = False
        self._compact = compact
        # 分页状态：一页 10 条，单次最多 50 条
        self._all: List[Dict[str, Any]] = []
        self._page = 1
        self._pages = 1
        self._page_size = 10
        self._total = 0
        self._summary_text = ""
        self.summary_ready.connect(self._on_summary_ready)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("搜文献：关键词 / 标题 / 作者")
        self.query_edit.setFixedHeight(30)
        self.query_edit.returnPressed.connect(self.search)
        head.addWidget(self.query_edit, 1)
        self.search_button = SoftButton("搜索", "primary", height=30, min_width=68,
                                        point_size=8.6)
        self.search_button.clicked.connect(self.search)
        head.addWidget(self.search_button)
        root.addLayout(head)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.select_all_button = SoftButton("全选", "ghost", height=26, min_width=52,
                                            point_size=8.2)
        self.select_all_button.clicked.connect(lambda: self.set_all(True))
        tools.addWidget(self.select_all_button)
        self.select_none_button = SoftButton("全不选", "ghost", height=26, min_width=60,
                                             point_size=8.2)
        self.select_none_button.clicked.connect(lambda: self.set_all(False))
        tools.addWidget(self.select_none_button)
        self.count_label = QLabel("已选 0 篇")
        self.count_label.setObjectName("Tiny")
        tools.addWidget(self.count_label)
        tools.addStretch(1)
        root.addLayout(tools)

        self.scroll, _body, self.list_layout = make_scroll()
        root.addWidget(self.scroll, 1)

        # 分页：一页 10 条，单次搜索最多 50 条（查询里写了数字按数字来）
        pager = QHBoxLayout()
        pager.setSpacing(6)
        self.prev_button = SoftButton("上一页", "ghost", height=24, min_width=64,
                                      point_size=8.2)
        self.prev_button.clicked.connect(lambda: self.show_page(self._page - 1))
        pager.addWidget(self.prev_button)
        self.page_label = QLabel("")
        self.page_label.setObjectName("Tiny")
        pager.addWidget(self.page_label)
        self.next_button = SoftButton("下一页", "ghost", height=24, min_width=64,
                                      point_size=8.2)
        self.next_button.clicked.connect(lambda: self.show_page(self._page + 1))
        pager.addWidget(self.next_button)
        pager.addStretch(1)
        root.addLayout(pager)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.download_button = SoftButton("一键下载", "primary", height=28,
                                          min_width=88, point_size=8.4)
        self.download_button.setToolTip("把勾选的文献 PDF 下载进「我的文献库」")
        self.download_button.clicked.connect(self.download_selected)
        actions.addWidget(self.download_button)
        self.cite_button = SoftButton("一键引用", "ghost", height=28, min_width=88,
                                      point_size=8.4)
        self.cite_button.setToolTip("生成 GB/T 7714 引用并复制到剪贴板")
        self.cite_button.clicked.connect(self.cite_selected)
        actions.addWidget(self.cite_button)
        self.endnote_button = SoftButton("导出 EndNote", "ghost", height=28,
                                         min_width=104, point_size=8.4)
        self.endnote_button.setToolTip("导出 .enw 给 EndNote（没装会先问你要不要经 ehall 装）")
        self.endnote_button.clicked.connect(self.export_selected)
        actions.addWidget(self.endnote_button)
        root.addLayout(actions)

        self.status = QLabel("搜一下试试：比如「linear algebra eigenvalue」")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

    # -------------------------------------------------- 数据
    def papers(self) -> List[Dict[str, Any]]:
        return list(self._papers)

    def set_papers(self, papers: List[Dict[str, Any]]) -> None:
        # 刷新列表时**保留已经勾上的**（下载完刷新、翻页都不该把选择弄丢）
        keep = {paper_id for paper_id, chosen in self._selected.items() if chosen}
        self._papers = [item for item in (papers or []) if item.get("id")][:MAX_ROWS]
        # 注意：翻页时当前页只显示一部分，不能按「本页 id」过滤，否则别的页的勾选会丢
        self._selected = {paper_id: True for paper_id in keep}
        self._render_rows()
        self.papers_changed.emit(self._papers)

    def selected_ids(self) -> List[int]:
        return [paper_id for paper_id, chosen in self._selected.items() if chosen]

    def set_all(self, chosen: bool) -> None:
        for row in self._rows:
            row.check.setChecked(bool(chosen))
        self._refresh_count()

    def _refresh_count(self) -> None:
        count = len(self.selected_ids())
        self.count_label.setText(f"已选 {count} 篇")
        for button in (self.download_button, self.cite_button, self.endnote_button):
            button.setEnabled(count > 0)

    def _render_rows(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self._rows = []
        if not self._papers:
            empty = QLabel("还没有结果。输入关键词点「搜索」。")
            empty.setObjectName("Tiny")
            empty.setWordWrap(True)
            self.list_layout.addWidget(empty)
            self._refresh_count()
            return
        for paper in self._papers:
            row = _PaperRow(paper)
            row.toggled.connect(self._on_row_toggled)
            row.summarize_requested.connect(self.summarize)
            if self._selected.get(int(paper.get("id") or 0)):
                row.check.setChecked(True)
            self._rows.append(row)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)
        self._refresh_count()
        self._refresh_pager()

    def _on_row_toggled(self, paper_id: int, chosen: bool) -> None:
        self._selected[int(paper_id)] = bool(chosen)
        self._refresh_count()

    # -------------------------------------------------- 搜索 / 分页
    def search_for(self, text: str) -> None:
        """外部（「文献库」页的「联网检索」按钮）把关键词丢进来直接搜。"""
        self.query_edit.setText(str(text or "").strip())
        self.search()

    def search(self) -> None:
        if self._busy:
            return
        query = self.query_edit.text().strip()
        if not query:
            self.status.setText("先输入关键词")
            return
        self._busy = True
        self.search_button.setEnabled(False)
        self.status.setText("正在搜文献…（arXiv / Crossref / Semantic Scholar）")

        def work() -> None:
            try:
                result = literature.search(self.store, query, page=1)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "results": [],
                          "summary": f"搜索失败：{exc}"}
            self._search_done(result)

        threading.Thread(target=work, name="lumina-lit-search", daemon=True).start()

    def _search_done(self, result: Dict[str, Any]) -> None:
        self._busy = False
        try:
            self.search_button.setEnabled(True)
        except Exception:
            pass
        self._all = list(result.get("all_results") or result.get("results") or [])
        self._page = int(result.get("page") or 1)
        self._pages = int(result.get("pages") or 1)
        self._page_size = int(result.get("page_size") or 10)
        self._total = int(result.get("total") or len(self._all))
        self._summary_text = str(result.get("summary") or "搜索完成")
        self._show_page()
        self.status.setText(self._summary_text)
        self.status_changed.emit(self.status.text())

    def show_page(self, page: int) -> None:
        """翻页：只换列表内容，勾选状态按 id 保留。"""
        if not self._all:
            return
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1
        page = max(1, min(self._pages or 1, page))
        self._page = page
        start = (page - 1) * self._page_size
        window = self._all[start:start + self._page_size]
        self.set_papers(window)
        self._refresh_pager()
        self.status.setText("第 %d/%d 页 · 共 %d 篇（每页 %d 条）"
                            % (self._page, self._pages, self._total, self._page_size))
        self.status_changed.emit(self.status.text())

    def _refresh_pager(self) -> None:
        try:
            self.page_label.setText("第 %d / %d 页 · 共 %d 篇"
                                    % (self._page, self._pages, self._total))
            self.prev_button.setEnabled(self._page > 1)
            self.next_button.setEnabled(self._page < self._pages)
            self.page_label.setVisible(bool(self._all))
        except Exception:
            pass

    def pages(self) -> int:
        return getattr(self, "_pages", 1)

    def page(self) -> int:
        return getattr(self, "_page", 1)

    def summarize(self, paper_id: int) -> None:
        """按需生成中文概述（后台线程，完成后回填到那一行）。"""
        self.status.setText("正在让模型写中文概述…（会存进库里，下次不用再花 token）")
        self.status_changed.emit(self.status.text())

        def work() -> None:
            from . import lit

            try:
                result = lit.summarize_zh(self.store, int(paper_id))
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "text": "", "summary": f"生成失败：{exc}"}
            self.summary_ready.emit(int(paper_id), str(result.get("text") or ""),
                                    str(result.get("summary") or ""))

        threading.Thread(target=work, name="lumina-lit-summary", daemon=True).start()

    def _on_summary_ready(self, paper_id: int, text: str, note: str) -> None:
        for row in self._rows:
            if int(row.paper.get("id") or 0) == int(paper_id):
                if text:
                    row.set_summary(text)
                    if not row._expanded:
                        row.toggle_expand()
                break
        for paper in self._all:
            if int(paper.get("id") or 0) == int(paper_id) and text:
                paper["summary_zh"] = text
        self.status.setText(note or ("中文概述已生成" if text else "没生成出来"))
        self.status_changed.emit(self.status.text())

    # -------------------------------------------------- 下载 / 引用
    def download_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            self.status.setText("先勾选要下载的文献")
            return
        self.status.setText(f"正在下载 {len(ids)} 篇…")
        result = literature.download(self.store, ids)
        self.status.setText(str(result.get("summary") or "下载完成"))
        self.status_changed.emit(self.status.text())
        if not result.get("ok"):
            return
        # 下载完刷新一下列表（file_path 变了）
        self.set_papers(literature.papers_by_ids(
            self.store, [int(item["id"]) for item in self._papers]))

    def cite_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            self.status.setText("先勾选要引用的文献")
            return
        result = literature.citations(self.store, ids, "gbt7714")
        if not result.get("ok"):
            self.status.setText(str(result.get("summary")))
            return
        text = str(result.get("text") or "")
        try:
            from PySide6.QtWidgets import QApplication

            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
            self.status.setText(f"{result.get('summary')}，已复制到剪贴板")
        except Exception:
            self.status.setText(str(result.get("summary")))
        self.status_changed.emit(self.status.text())
        return text

    def export_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            self.status.setText("先勾选要导出的文献")
            return
        result = literature.export(self.store, ids, "enw")
        if not result.get("ok"):
            self.status.setText(str(result.get("summary") or "导出失败"))
            return
        info = result.get("endnote") or {}
        if info.get("found"):
            opened = literature.open_endnote(result["path"])
            self.status.setText(str(result.get("summary"))
                                + ("；已用 EndNote 打开" if opened else ""))
        else:
            hint = literature.endnote_install_hint()
            self.status.setText(f"{result.get('summary')}。{hint['message']} {hint['ask']}")
            self.ask_endnote_install.emit(hint)
        self.status_changed.emit(self.status.text())

    #: 没装 EndNote 时抛给主窗口（由它弹窗问要不要打开 ehall）
    from PySide6.QtCore import Signal as _Signal

    ask_endnote_install = _Signal(dict)
