"""「我的文献库」页面（第 5 步）：目录式归类 + 检索 + 项目引用。

布局：
* 顶部：标题 / 副标题、搜索框、刷新；
* 左边一列：项目（全部 / 未分类 / 各个项目）+ 每个项目的文献数；
* 右边上面：文献列表（点一下看详情）；
* 右边下面：详情（标题/作者/期刊/年/DOI、摘要、GB/T 7714 引用）+ 按钮
  （打开 PDF、打开所在文件夹、复制引用、导出 EndNote）。

一键引用 / 导出 EndNote 都走 `literature` 服务层，和侧栏面板是同一套逻辑。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout, QWidget,
)

from . import icons, literature, theme
from .utils import open_external
from .widgets import ElidedLabel, EmptyState, SoftButton, make_scroll


class _PaperLine(QWidget):
    """文献列表里的一行：勾选框 + 标题 / 元数据 / 星级 / 中文概述摘录。"""

    clicked = Signal(int)
    checked_changed = Signal(int, bool)
    stars_changed = Signal(int, int)

    def __init__(self, paper: Dict[str, Any], selected: bool = False,
                 checked: bool = False, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox

        self.paper = paper
        self._selected = selected
        self._paper_id = int(paper.get("id") or 0)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 10, 6)
        outer.setSpacing(8)
        self.check = QCheckBox()
        self.check.setChecked(bool(checked))
        self.check.setToolTip("勾选后可以批量操作（复制引用 / 导出 / 打星 / 生成概述）")
        self.check.toggled.connect(
            lambda on: self.checked_changed.emit(self._paper_id, bool(on)))
        outer.addWidget(self.check, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        outer.addLayout(column, 1)

        title = str(paper.get("title") or "（无标题）")
        self.title_label = ElidedLabel(title)
        self.title_label.setObjectName("Small")
        column.addWidget(self.title_label)
        bits = [str(paper.get("authors") or "")[:40], str(paper.get("year") or ""),
                str(paper.get("venue") or "")[:24]]
        meta = " · ".join(bit for bit in bits if bit) or "（没有元数据）"
        if paper.get("file_path"):
            meta += "  ·  已下载"
        self.meta = QLabel(meta)
        self.meta.setObjectName("Tiny")
        self.meta.setStyleSheet(f"color: {theme.MUTED};")
        column.addWidget(self.meta)
        # 中文概述：列表里就能看到（不用点开），一眼判断要不要读
        summary = str(paper.get("summary_zh") or "").strip()
        self.summary = QLabel(self._snippet(summary))
        self.summary.setObjectName("Tiny")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(
            f"color: {theme.TEXT_SOFT if summary else theme.FAINT};"
            " font-size: 8.6pt;")
        self.summary.setToolTip(summary or "还没生成中文概述（会自动生成）")
        column.addWidget(self.summary)
        self.stars = _StarRow(int(paper.get("stars") or 0))
        self.stars.changed.connect(
            lambda value: self.stars_changed.emit(self._paper_id, value))
        column.addWidget(self.stars)
        if paper.get("project"):
            tag = QLabel(str(paper["project"]))
            tag.setObjectName("Tiny")
            tag.setStyleSheet(f"color: {theme.ACCENT};")
            column.addWidget(tag)
        if selected:
            self.setStyleSheet(
                f"_PaperLine {{ background: {theme.CARD_ALT}; border-radius: 8px; }}")

    @staticmethod
    def _snippet(text: str, limit: int = 110) -> str:
        text = " ".join(str(text or "").split())
        if not text:
            return "（中文概述还没生成，稍等或点「生成中文概述」）"
        return text if len(text) <= limit else text[:limit] + "…"

    def set_summary_text(self, text: str) -> None:
        self.paper["summary_zh"] = str(text or "")
        self.summary.setText(self._snippet(text))
        self.summary.setStyleSheet(
            f"color: {theme.TEXT_SOFT if text else theme.FAINT};"
            " font-size: 8.6pt;")
        self.summary.setToolTip(str(text or ""))

    def set_checked(self, value: bool) -> None:
        self.check.blockSignals(True)
        self.check.setChecked(bool(value))
        self.check.blockSignals(False)

    def mousePressEvent(self, event):
        self.clicked.emit(self._paper_id)
        super().mousePressEvent(event)


class _StarRow(QWidget):
    """五星：点第 N 颗就是 N 星；再点同一颗清成 0 星。"""

    changed = Signal(int)

    def __init__(self, stars: int = 0, parent=None):
        super().__init__(parent)
        self._stars = max(0, min(5, int(stars or 0)))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.labels: List[QLabel] = []
        for index in range(1, 6):
            label = QLabel()
            label.setCursor(Qt.CursorShape.PointingHandCursor)
            label.setToolTip(f"{index} 星")
            label.mousePressEvent = (
                lambda event, value=index: self._pick(value))
            row.addWidget(label)
            self.labels.append(label)
        row.addStretch(1)
        self._refresh()

    def stars(self) -> int:
        return self._stars

    def _pick(self, value: int) -> None:
        self.set_stars(0 if value == self._stars else value)
        self.changed.emit(self._stars)

    def set_stars(self, value: int) -> None:
        self._stars = max(0, min(5, int(value or 0)))
        self._refresh()

    def _refresh(self) -> None:
        color = theme.WARNING
        muted = theme.FAINT
        for index, label in enumerate(self.labels, start=1):
            filled = index <= self._stars
            label.setText("★" if filled else "☆")
            label.setStyleSheet(
                f"color: {color if filled else muted}; font-size: 12pt;"
                " background: transparent;")


class LiteraturePage(QWidget):
    """我的文献库。"""

    ask_endnote_install = Signal(dict)
    summary_finished = Signal(int, str, str)
    flow_ready = Signal(dict)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._papers: List[Dict[str, Any]] = []
        self._project = ""
        self._selected_id: Optional[int] = None
        self._lines: List[_PaperLine] = []
        # 多选 / 星级筛选 / 自动概述
        self._checked: set = set()
        self._stars_filter = 0            # 0 = 全部；1–5 = 至少 N 星；-1 = 未评星
        self._summary_queue: List[int] = []
        self._summary_running = False
        self._auto_summary = True
        try:
            self._auto_summary = (str(self.store.get_setting(
                "lit_auto_summary", "1")) != "0")
        except Exception:
            self._auto_summary = True

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 18, 26, 20)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("文献库")
        title.setObjectName("H1")
        self.subtitle = ElidedLabel("下载过的文献都按「项目 / 年份」分好文件夹放在这里")
        self.subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.search_edit = QWidget()  # 占位，稍后替换成真实输入框
        from .widgets import SearchBox

        # 这个框只过滤**本机已下载**的文献；真正的联网检索在右侧「文献」标签里。
        # 以前两处都叫「搜索」，很容易以为在这里搜就能搜到全网文献。
        self.search_edit = SearchBox("过滤本机文献：标题 / 作者 / 期刊")
        self.search_edit.setFixedWidth(240)
        self.search_edit.setToolTip("只在已下载的文献里过滤；要联网检索请点右边的「联网检索」")
        self.search_edit.textChanged.connect(lambda _text: self.reload())
        self.search_edit.edit.returnPressed.connect(self._search_online)
        header.addWidget(self.search_edit)
        self.online_button = SoftButton("联网检索", "primary", height=34,
                                        min_width=96, point_size=8.8)
        self.online_button.setToolTip(
            "用 arXiv / Crossref / Semantic Scholar 搜文献（在右侧「文献」标签里出结果，可以勾选下载）")
        self.online_button.clicked.connect(self._search_online)
        header.addWidget(self.online_button)
        self.refresh_button = SoftButton("刷新", "ghost", height=34,
                                         glyph=icons.REFRESH, min_width=88,
                                         point_size=8.8)
        self.refresh_button.clicked.connect(self.reload)
        header.addWidget(self.refresh_button)
        self.folder_button = SoftButton("打开文献库目录", "ghost", height=34,
                                        min_width=132, point_size=8.8)
        self.folder_button.clicked.connect(self._open_library_folder)
        header.addWidget(self.folder_button)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)

        # 左：项目列
        left = QWidget()
        left.setFixedWidth(180)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        projects_title = QLabel("项目")
        projects_title.setObjectName("Tiny")
        left_layout.addWidget(projects_title)
        self.project_scroll, _pb, self.project_layout = make_scroll()
        left_layout.addWidget(self.project_scroll, 1)
        body.addWidget(left)

        # 右：列表 + 详情
        right = QVBoxLayout()
        right.setSpacing(8)
        # 列表上方：多选 / 全选 / 星级筛选 / 自动概述开关
        from PySide6.QtWidgets import QCheckBox

        from .widgets import Select

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.select_all_button = SoftButton("全选", "ghost", height=26,
                                            min_width=56, point_size=8.2)
        self.select_all_button.setToolTip("把当前列表里的文献全部勾上")
        self.select_all_button.clicked.connect(lambda: self._check_all(True))
        tools.addWidget(self.select_all_button)
        self.select_none_button = SoftButton("全不选", "ghost", height=26,
                                             min_width=64, point_size=8.2)
        self.select_none_button.clicked.connect(lambda: self._check_all(False))
        tools.addWidget(self.select_none_button)
        self.checked_label = QLabel("已选 0 篇")
        self.checked_label.setObjectName("Tiny")
        tools.addWidget(self.checked_label)
        tools.addSpacing(6)
        self.stars_select = Select()
        self.stars_select.setFixedWidth(112)
        self.stars_select.setToolTip("按星级筛选：只看打了 N 星以上的文献")
        for label, value in (("全部星级", 0), ("★★★★★", 5), ("★★★★ 以上", 4),
                             ("★★★ 以上", 3), ("★★ 以上", 2),
                             ("未评星", -1)):
            self.stars_select.addItem(label, value)
        self.stars_select.currentIndexChanged.connect(self._on_stars_filter)
        tools.addWidget(self.stars_select)
        self.auto_summary_box = QCheckBox("自动生成中文概述")
        self.auto_summary_box.setChecked(bool(self._auto_summary))
        self.auto_summary_box.setToolTip(
            "打开后，列表里还没有中文概述的文献会排队自动生成（用设置里配置的模型）")
        self.auto_summary_box.toggled.connect(self._on_auto_summary)
        tools.addWidget(self.auto_summary_box)
        tools.addStretch(1)
        right.addLayout(tools)
        self.list_scroll, _lb, self.list_layout = make_scroll()
        right.addWidget(self.list_scroll, 3)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.detail.setMinimumHeight(180)
        self.detail.setStyleSheet(
            f"QTextBrowser {{ background: {theme.CARD_ALT}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER_SOFT}; border-radius: 10px;"
            " padding: 8px 10px; }")
        right.addWidget(self.detail, 2)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.pdf_button = SoftButton("打开 PDF", "primary", height=30,
                                     min_width=92, point_size=8.6)
        self.pdf_button.clicked.connect(self._open_pdf)
        actions.addWidget(self.pdf_button)
        self.cite_button = SoftButton("复制 GB/T 引用", "ghost", height=30,
                                      min_width=118, point_size=8.6)
        self.cite_button.clicked.connect(self._copy_citation)
        actions.addWidget(self.cite_button)
        self.bibtex_button = SoftButton("复制 BibTeX", "ghost", height=30,
                                        min_width=104, point_size=8.6)
        self.bibtex_button.clicked.connect(self._copy_bibtex)
        actions.addWidget(self.bibtex_button)
        self.endnote_button = SoftButton("导出 EndNote", "ghost", height=30,
                                         min_width=110, point_size=8.6)
        self.endnote_button.clicked.connect(self._export_endnote)
        actions.addWidget(self.endnote_button)
        self.project_cite_button = SoftButton("复制本项目全部引用", "ghost", height=30,
                                              min_width=148, point_size=8.6)
        self.project_cite_button.clicked.connect(self._copy_project_citations)
        actions.addWidget(self.project_cite_button)
        # 中文概述：用配置好的模型写一段，存进库里
        self.summary_button = SoftButton("生成中文概述", "ghost", height=30,
                                         min_width=112, point_size=8.6)
        self.summary_button.setToolTip(
            "用设置里配置的模型，用中文说清楚这篇文献做了什么、有什么特色（结果会缓存）")
        self.summary_button.clicked.connect(self._summarize)
        actions.addWidget(self.summary_button)
        # 流程图：让模型把论文方法拆成步骤，自己画成竖排流程图
        self.flow_button = SoftButton("生成流程图", "ghost", height=30,
                                      min_width=104, point_size=8.6)
        self.flow_button.setToolTip(
            "用配置好的模型把这篇论文的方法拆成步骤，画成流程图（可保存成图片）")
        self.flow_button.clicked.connect(self._make_flow)
        actions.addWidget(self.flow_button)
        # 图书馆数据库：在里面用机构账号登录各数据库
        self.library_button = SoftButton("图书馆数据库", "ghost", height=30,
                                         min_width=110, point_size=8.6)
        self.library_button.setToolTip(
            "打开复旦图书馆的数据库导航，按机构方式登录（校内直连 / WebVPN / libproxy）")
        self.library_button.clicked.connect(self._open_library)
        actions.addWidget(self.library_button)
        actions.addStretch(1)
        right.addLayout(actions)
        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        self.reload()
        try:
            self.summary_finished.connect(self._on_summary_finished)
            self.flow_ready.connect(self._on_flow_ready)
        except Exception:
            pass

    # -------------------------------------------------- 数据
    def set_online_search_handler(self, handler) -> None:
        """主窗口接上：把关键词送到右侧「文献」标签去联网检索。"""
        self._online_search = handler

    def _search_online(self) -> None:
        handler = getattr(self, "_online_search", None)
        query = ""
        try:
            query = self.search_edit.text().strip()
        except Exception:
            query = ""
        if callable(handler):
            handler(query)
            return
        # 没接上（单独用这个页面时）：至少告诉人去哪儿搜
        self.subtitle.setText(
            "联网检索在右侧「文献」标签里：输入关键词 → 搜索 → 勾选 → 一键下载")

    def reload(self) -> None:
        query = ""
        try:
            query = self.search_edit.text().strip()
        except Exception:
            query = ""
        self._papers = literature.results(self.store, query=query,
                                          project=self._project, limit=300)
        self._papers = self._apply_stars_filter(self._papers)
        self._render_projects()
        self._render_papers()
        total = len(literature.results(self.store, limit=1000))
        downloaded = len([item for item in literature.results(self.store, limit=1000)
                          if item.get("file_path")])
        self.subtitle.setText(
            f"共 {total} 篇 · 已下载 {downloaded} 篇 · 目录：{self._library_path()}")
        if self._selected_id is None and self._papers:
            self._select(int(self._papers[0]["id"]))
        elif self._selected_id is not None:
            self._select(self._selected_id)
        # 列表出来之后，把还没有中文概述的排队（自动生成，不用点）
        self._queue_missing_summaries()

    # -------------------------------------------------- 星级 / 多选
    def _apply_stars_filter(self, papers: List[Dict[str, Any]]):
        value = int(self._stars_filter or 0)
        if value == 0:
            return list(papers)
        out = []
        for item in papers:
            stars = int(item.get("stars") or 0)
            if value < 0:
                if stars <= 0:
                    out.append(item)
            elif stars >= value:
                out.append(item)
        return out

    def _on_stars_filter(self, _index: int = -1) -> None:
        try:
            value = int(self.stars_select.currentData() or 0)
        except Exception:
            value = 0
        self._stars_filter = value
        self.reload()

    def _on_auto_summary(self, checked: bool) -> None:
        self._auto_summary = bool(checked)
        try:
            self.store.set_setting("lit_auto_summary", "1" if checked else "0")
        except Exception:
            pass
        if checked:
            self._queue_missing_summaries()
        else:
            self._summary_queue = []

    def _check_all(self, value: bool) -> None:
        """全选 / 全不选（只影响当前列表里看得见的）。"""
        for line in self._lines:
            line.set_checked(bool(value))
            paper_id = int(line.paper.get("id") or 0)
            if value:
                self._checked.add(paper_id)
            else:
                self._checked.discard(paper_id)
        self._refresh_checked_label()

    def _on_line_checked(self, paper_id: int, value: bool) -> None:
        if value:
            self._checked.add(int(paper_id))
        else:
            self._checked.discard(int(paper_id))
        self._refresh_checked_label()

    def _on_line_stars(self, paper_id: int, stars: int) -> None:
        from . import lit

        saved = lit.set_stars(self.store, int(paper_id), int(stars))
        for item in self._papers:
            if int(item.get("id") or 0) == int(paper_id):
                item["stars"] = saved
                break
        self.status.setText(f"已打 {saved} 星" if saved else "已取消星级")

    def _refresh_checked_label(self) -> None:
        try:
            self.checked_label.setText("已选 %d 篇" % len(self._checked))
        except Exception:
            pass

    def selected_ids(self) -> List[int]:
        """当前勾选的文献；一个都没勾就用「详情里那一篇」。"""
        ids = sorted(self._checked)
        if ids:
            return ids
        if self._selected_id:
            return [int(self._selected_id)]
        return []

    # -------------------------------------------------- 自动生成中文概述
    def _queue_missing_summaries(self) -> None:
        """把当前列表里「还没有概述」的排队（默认自动跑，不用点按钮）。"""
        if not self._auto_summary:
            return
        try:
            from . import ai_chat

            if not ai_chat.api_ready(self.store):
                return
        except Exception:
            return
        queued = set(self._summary_queue)
        for item in self._papers:
            paper_id = int(item.get("id") or 0)
            if not paper_id or paper_id in queued:
                continue
            if str(item.get("summary_zh") or "").strip():
                continue
            if not (str(item.get("abstract") or "").strip()
                    or str(item.get("url") or "").strip()):
                continue
            self._summary_queue.append(paper_id)
            queued.add(paper_id)
            if len(self._summary_queue) >= 20:
                break
        self._pump_summary_queue()

    def _pump_summary_queue(self) -> None:
        """一次只跑一篇（别一口气把 token 烧光），跑完自动接下一篇。"""
        if self._summary_running or not self._summary_queue:
            return
        paper_id = int(self._summary_queue.pop(0))
        self._summary_running = True
        self.status.setText(f"正在自动生成中文概述…（还剩 "
                            f"{len(self._summary_queue) + 1} 篇）")

        import threading

        def work() -> None:
            from . import lit

            try:
                result = lit.summarize_zh(self.store, paper_id)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "text": "", "summary": f"生成失败：{exc}"}
            self.summary_finished.emit(paper_id, str(result.get("text") or ""),
                                       str(result.get("summary") or ""))

        threading.Thread(target=work, name="lumina-lit-auto-summary",
                         daemon=True).start()

    def _library_path(self) -> str:
        from . import lit

        try:
            return str(lit.library_root())
        except Exception:
            return "（未知）"

    def _render_projects(self) -> None:
        from .widgets import clear_layout

        clear_layout(self.project_layout)
        projects = literature.results(self.store, limit=1000)
        buckets: Dict[str, int] = {"": len(projects)}
        for item in projects:
            name = str(item.get("project") or "")
            if name:
                buckets[name] = buckets.get(name, 0) + 1
        for name, count in [("全部", buckets[""])] + sorted(
                [(key, value) for key, value in buckets.items() if key]):
            button = SoftButton(f"{name}（{count}）",
                                "primary" if (name == "全部" and not self._project)
                                or name == self._project else "ghost",
                                height=28, point_size=8.4)
            button.clicked.connect(
                lambda _checked=False, target=name: self._choose_project(target))
            self.project_layout.addWidget(button)
        self.project_layout.addStretch(1)

    def _choose_project(self, name: str) -> None:
        self._project = "" if name == "全部" else name
        self._selected_id = None
        self.reload()

    def _render_papers(self) -> None:
        from .widgets import clear_layout

        clear_layout(self.list_layout)
        self._lines = []
        if not self._papers:
            self.list_layout.addWidget(EmptyState(
                icons.DOCUMENT, "还没有文献",
                "在右侧「文献」里搜一搜，勾选后点「一键下载」就会出现在这里"))
            self._refresh_checked_label()
            return
        for paper in self._papers:
            paper_id = int(paper.get("id") or 0)
            line = _PaperLine(paper, selected=paper_id == self._selected_id,
                              checked=paper_id in self._checked)
            line.clicked.connect(self._select)
            line.checked_changed.connect(self._on_line_checked)
            line.stars_changed.connect(self._on_line_stars)
            self._lines.append(line)
            self.list_layout.addWidget(line)
        self.list_layout.addStretch(1)
        self._refresh_checked_label()

    # -------------------------------------------------- 详情
    def _select(self, paper_id: int) -> None:
        self._selected_id = int(paper_id)
        # 详情一律从库里现取：列表里那份可能是旧的（比如刚生成完中文概述），
        # 用旧 dict 渲染会导致「明明生成了，详情里还写着还没生成」。
        rows = literature.papers_by_ids(self.store, [int(paper_id)])
        item = rows[0] if rows else None
        if item is None:
            item = next((row for row in self._papers
                         if int(row.get("id") or 0) == int(paper_id)), None)
        if not item:
            self.detail.setHtml("<p>找不到这篇文献</p>")
            return
        # 把最新数据写回列表，避免再点一次又变回旧内容
        for index, row in enumerate(self._papers):
            if int(row.get("id") or 0) == int(paper_id):
                self._papers[index] = dict(item)
                break
        from . import lit

        citation = lit.citation(item, "gbt7714")
        colors = theme.PALETTES.get("dark" if theme.effective_mode() == "dark"
                                    else "light", theme.PALETTES["light"])
        parts = [
            f"<h3 style='color:{colors['TEXT']}'>{item.get('title', '')}</h3>",
            f"<p style='color:{colors['MUTED']};font-size:11px'>"
            f"{item.get('authors', '')} · {item.get('year', '')} · "
            f"{item.get('venue', '')}</p>",
        ]
        if item.get("doi"):
            parts.append(f"<p style='color:{colors['TEXT_SOFT']};font-size:11px'>"
                         f"DOI：{item['doi']}</p>")
        if item.get("project"):
            parts.append(f"<p style='color:{colors['TEXT_SOFT']};font-size:11px'>"
                         f"项目：{item['project']}</p>")
        if item.get("file_path"):
            parts.append(f"<p style='color:{colors['TEXT_SOFT']};font-size:11px'>"
                         f"文件：{item['file_path']}</p>")
        abstract = str(item.get("abstract") or "").strip()
        parts.append(f"<p style='color:{colors['TEXT_SOFT']}'>"
                     f"{abstract or '（这篇没有摘要）'}</p>")
        # 中文概述：有缓存就显示；没有就提示点下面的按钮
        summary = str(item.get("summary_zh") or "").strip()
        accent = theme.appearance().accent
        parts.append(f"<p style='color:{accent};font-size:11px'>中文概述</p>")
        parts.append(
            f"<p style='color:{colors['TEXT_SOFT']}'>"
            f"{summary or '（还没生成；点下面「生成中文概述」，让模型用中文说清楚它做了什么、有什么特色）'}</p>")
        parts.append(f"<p style='color:{colors['MUTED']};font-size:11px'>"
                     f"GB/T 7714：{citation}</p>")
        self.detail.setHtml("".join(parts))
        self._render_papers()
        self.pdf_button.setEnabled(bool(item.get("file_path")))
        self.status.setText("")

    def _current(self) -> Dict[str, Any]:
        if self._selected_id is None:
            return {}
        rows = literature.papers_by_ids(self.store, [int(self._selected_id)])
        return rows[0] if rows else {}

    def _summarize(self) -> None:
        """生成中文概述：勾了几篇就排队几篇；一篇都没勾就用当前这篇。"""
        ids = self.selected_ids()
        if not ids:
            self.status.setText("先选一篇文献")
            return
        self.summary_button.setEnabled(False)
        self.summary_button.setText("生成中…")
        for paper_id in ids:
            if int(paper_id) not in self._summary_queue:
                self._summary_queue.append(int(paper_id))
        self.status.setText(f"正在让模型写中文概述…（{len(ids)} 篇，会存进库里）")
        self._pump_summary_queue()

    def _on_summary_finished(self, paper_id: int, text: str, note: str) -> None:
        self._summary_running = False
        try:
            self.summary_button.setEnabled(True)
            self.summary_button.setText("生成中文概述")
        except Exception:
            pass
        if text:
            for item in self._papers:
                if int(item.get("id") or 0) == int(paper_id):
                    item["summary_zh"] = str(text)
                    break
            for line in self._lines:
                if int(line.paper.get("id") or 0) == int(paper_id):
                    line.set_summary_text(text)
                    break
            if int(paper_id) == int(self._selected_id or 0):
                self._select(int(paper_id))
            # 状态栏放在 _select 之后：_select 会把 status 清空
            self.status.setText(note or "中文概述已生成（已存进库里）")
        else:
            self.status.setText(note or "没生成出来")
        # 还有排队的就继续（自动概述就是这么一篇篇跑完的）
        self._pump_summary_queue()

    # -------------------------------------------------- 流程图
    def _make_flow(self, force: bool = False) -> None:
        """让 Agent 给（勾选的）文献生成方法流程图；一篇就是当前这篇。"""
        ids = self.selected_ids()
        if not ids:
            self.status.setText("先选一篇文献")
            return
        paper_id = int(ids[0])
        self.flow_button.setEnabled(False)
        self.flow_button.setText("生成中…")
        self.status.setText("正在让模型拆解方法流程…（一次先做一篇）")

        import threading

        def work() -> None:
            from . import lit_flow

            try:
                result = lit_flow.generate(self.store, paper_id, force=force)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": f"生成失败：{exc}", "steps": [],
                          "path": "", "title": ""}
            result["paper_id"] = paper_id
            self.flow_ready.emit(result)

        threading.Thread(target=work, name="lumina-lit-flow",
                         daemon=True).start()

    def _on_flow_ready(self, result: Dict[str, Any]) -> None:
        try:
            self.flow_button.setEnabled(True)
            self.flow_button.setText("生成流程图")
        except Exception:
            pass
        result = dict(result or {})
        if not result.get("ok"):
            self.status.setText(str(result.get("error") or "流程图没生成出来"))
            return
        self.status.setText("流程图已生成：" + str(result.get("path") or ""))
        from .lit_flow_dialog import FlowDialog

        dialog = FlowDialog(self.store, result, self)
        dialog.exec()

    def _open_library(self) -> None:
        from .library_page import LibraryBrowserDialog

        dialog = LibraryBrowserDialog(self.store, self)
        dialog.exec()

    def _open_pdf(self) -> None:
        """打开 PDF：勾了多篇就依次打开有 PDF 的那些。"""
        ids = self.selected_ids()
        items = literature.papers_by_ids(self.store, ids) if ids else []
        opened = 0
        for item in items:
            path = str(item.get("file_path") or "")
            if path and Path(path).exists():
                open_external(path, local=True)
                opened += 1
        if opened:
            self.status.setText(f"已打开 {opened} 个 PDF")
            return
        self.status.setText("这些还没有下载 PDF（在右侧「文献」里勾选后下载）")

    def _open_library_folder(self) -> None:
        from . import lit

        try:
            open_external(str(lit.library_root()), local=True)
            self.status.setText(f"已打开：{lit.library_root()}")
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"打不开目录：{exc}")

    def _copy_citation(self) -> str:
        items = literature.papers_by_ids(self.store, self.selected_ids())
        if not items:
            self.status.setText("先点一篇文献")
            return ""
        from . import lit

        text = "\n".join(lit.citation(item, "gbt7714") for item in items)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.status.setText(
            f"{len(items)} 篇的 GB/T 7714 引用已复制到剪贴板"
            if len(items) > 1 else "GB/T 7714 引用已复制到剪贴板")
        return text

    def _copy_bibtex(self) -> str:
        items = literature.papers_by_ids(self.store, self.selected_ids())
        if not items:
            self.status.setText("先点一篇文献")
            return ""
        from . import lit

        text = "\n\n".join(lit.citation(item, "bibtex") for item in items)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.status.setText(
            f"{len(items)} 篇的 BibTeX 已复制到剪贴板"
            if len(items) > 1 else "BibTeX 已复制到剪贴板")
        return text

    def _copy_project_citations(self) -> str:
        items = literature.results(self.store, project=self._project, limit=500)
        if not items:
            self.status.setText("这个项目下还没有文献")
            return ""
        from . import lit

        text = lit.citations(items, "gbt7714")
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.status.setText(f"本项目 {len(items)} 篇的引用已复制到剪贴板")
        return text

    def _export_endnote(self) -> None:
        ids = self.selected_ids()
        if not ids:
            self.status.setText("先点一篇文献")
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
