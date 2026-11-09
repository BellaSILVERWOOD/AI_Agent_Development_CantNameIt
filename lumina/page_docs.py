"""文档页：在应用内打开 PDF / DOCX / PPTX / XLSX / 文本。

左侧是目录树（「最近打开」+ 下载目录，文件夹可展开 / 收起），右侧是查看器：
PDF 用 QtPdf，其它格式解析成 HTML 用 QTextBrowser 显示。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QPointF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QStackedWidget, QTextBrowser, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import docs_reader, icons, theme
from .utils import open_external
from .widgets import Card, IconButton, Pill, SoftButton, icon_font

RECENT_KEY = "docs_recent"
#: 树节点的数据：路径 / 节点类型（group 分组、folder 文件夹、file 文件、hint 提示）
PATH_ROLE = int(Qt.ItemDataRole.UserRole)
KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1
#: 「最近打开」最多列几条
RECENT_MAX = 20


def _glyph_icon(glyph: str, color: str) -> QIcon:
    """把图标字形画成 QIcon：树节点只收 QIcon，QSS 管不到节点图标的颜色。"""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setFont(icon_font(22))
    painter.setPen(QColor(color))
    painter.drawText(pixmap.rect(), int(Qt.AlignmentFlag.AlignCenter), glyph)
    painter.end()
    return QIcon(pixmap)


class DocsPage(QWidget):
    """文档查看页。"""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._current: Optional[Path] = None
        self._pdf_doc = None
        self._shortcuts: List[QShortcut] = []
        self._loaded: set = set()      # 子节点已经填好的文件夹（懒加载去重）
        self._expanded: set = set()    # 用户正展开着的文件夹（刷新列表后还原）
        self._built = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("文档")
        title.setObjectName("H1")
        self.subtitle = QLabel("PDF / Word / PPT / Excel 都能在应用里直接看")
        self.subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        self.kind_pill = Pill("未打开", theme.MUTED, theme.CARD_ALT)
        header.addWidget(self.kind_pill)

        self.open_button = SoftButton("打开文件", "primary", height=34, min_width=112,
                                      glyph=icons.FOLDER, point_size=8.8)
        self.open_button.clicked.connect(self.open_file)
        header.addWidget(self.open_button)

        self.downloads_button = SoftButton("打开下载目录", "ghost", height=34,
                                           min_width=126, point_size=8.8)
        self.downloads_button.clicked.connect(self.open_downloads_dir)
        header.addWidget(self.downloads_button)

        self.external_button = SoftButton("用系统程序打开", "ghost", height=34,
                                          min_width=128, point_size=8.8)
        self.external_button.clicked.connect(self.open_external)
        header.addWidget(self.external_button)

        self.fullscreen_button = SoftButton("全屏", "ghost", height=34, min_width=84,
                                            point_size=8.8)
        self.fullscreen_button.setToolTip("全屏看文档（按 Esc 退出）")
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        header.addWidget(self.fullscreen_button)
        self.header_row = QWidget()
        self.header_row.setLayout(header)
        root.addWidget(self.header_row)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.prev_button = IconButton(icons.CHEVRON_LEFT, size=28, glyph_size=11,
                                      tip="上一页（PgUp）")
        self.prev_button.clicked.connect(lambda: self._flip_page(-1))
        nav.addWidget(self.prev_button)
        self.next_button = IconButton(icons.CHEVRON_RIGHT, size=28, glyph_size=11,
                                      tip="下一页（PgDn）")
        self.next_button.clicked.connect(lambda: self._flip_page(1))
        nav.addWidget(self.next_button)
        self.page_label = QLabel("—")
        self.page_label.setObjectName("Tiny")
        nav.addWidget(self.page_label)
        nav.addStretch(1)
        self.nav_row = QWidget()
        self.nav_row.setLayout(nav)
        root.addWidget(self.nav_row)

        body = QHBoxLayout()
        body.setSpacing(10)

        list_card = Card(radius=14, fill=theme.CARD, border=theme.BORDER, padding=10,
                         spacing=6)
        self.list_card = list_card
        list_title = QLabel("目录")
        list_title.setObjectName("Small")
        list_card.body.addWidget(list_title)
        self.dir_label = QLabel("")
        self.dir_label.setObjectName("Tiny")
        list_card.body.addWidget(self.dir_label)

        # 全部展开 / 全部收起：树一深就得有一键到位的地方（风格同页面其它小按钮）
        tools_row = QHBoxLayout()
        tools_row.setSpacing(6)
        self.expand_button = SoftButton("全部展开", "subtle", height=26, min_width=86,
                                        point_size=8.2)
        self.expand_button.setToolTip("把目录树里的文件夹全部展开")
        self.expand_button.clicked.connect(self.expand_all)
        tools_row.addWidget(self.expand_button)
        self.collapse_button = SoftButton("全部收起", "subtle", height=26, min_width=86,
                                          point_size=8.2)
        self.collapse_button.setToolTip("把目录树里的文件夹全部收起")
        self.collapse_button.clicked.connect(self.collapse_all)
        tools_row.addWidget(self.collapse_button)
        tools_row.addStretch(1)
        list_card.body.addLayout(tools_row)

        self.tree = QTreeWidget()
        self.tree.setObjectName("DocsTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemExpanded.connect(self._on_expanded)
        self.tree.itemCollapsed.connect(self._on_collapsed)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        list_card.body.addWidget(self.tree, 1)
        list_card.setFixedWidth(268)
        body.addWidget(list_card)

        viewer_card = Card(radius=14, fill=theme.CARD, border=theme.BORDER, padding=8,
                           spacing=6)
        self.viewer_card = viewer_card
        self.stack = QStackedWidget()
        self.html_view = QTextBrowser()
        self.html_view.setOpenExternalLinks(True)
        self.html_view.setStyleSheet(
            f"QTextBrowser {{ background: {theme.CARD}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 10px; padding: 12px; }}"
        )
        self.stack.addWidget(self.html_view)
        self.pdf_view = self._make_pdf_view()
        self.stack.addWidget(self.pdf_view)
        viewer_card.body.addWidget(self.stack, 1)
        body.addWidget(viewer_card, 1)
        root.addLayout(body, 1)

        self._install_shortcuts()
        self._refresh_list()

    # -------------------------------------------------- PDF 视图
    def _make_pdf_view(self) -> QWidget:
        try:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView

            self._pdf_doc = QPdfDocument(self)
            view = QPdfView(self)
            view.setDocument(self._pdf_doc)
            # 连续滚动：滚轮上下就能从上一页翻到下一页
            view.setPageMode(QPdfView.PageMode.MultiPage)
            view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            view.setStyleSheet(
                f"QPdfView {{ background: {theme.CARD_ALT};"
                f" border: 1px solid {theme.BORDER}; border-radius: 10px; }}")
            try:
                view.pageNavigator().currentPageChanged.connect(self._sync_page_label)
            except Exception:
                pass
            return view
        except Exception:  # noqa: BLE001
            label = QLabel("当前环境不支持 PDF 预览")
            label.setObjectName("Muted")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return label

    # -------------------------------------------------- 目录树
    def _recent(self) -> List[str]:
        raw = self.store.get_setting(RECENT_KEY, "") or ""
        return [item for item in raw.split("|") if item]

    def _push_recent(self, path: Path) -> None:
        items = [str(path)] + [item for item in self._recent() if item != str(path)]
        self.store.set_setting(RECENT_KEY, "|".join(items[:12]))
        self._refresh_list()

    def _refresh_list(self) -> None:
        """重建整棵目录树：「最近打开」+ 下载目录（子文件夹展开时才加载）。"""
        from . import settings_dirs

        expanded = set(self._expanded)      # 先拍快照：clear() 时可能触发收起信号
        self.tree.clear()
        self._loaded.clear()
        downloads = settings_dirs.downloads_dir(self.store)
        try:
            self.dir_label.setText(f"{downloads.name or downloads}")
            self.dir_label.setToolTip(str(downloads))
        except Exception:
            pass
        self.tree.addTopLevelItem(self._recent_item())
        self.tree.addTopLevelItem(self._downloads_item(downloads))
        root_item = self.tree.topLevelItem(1)
        recent_item = self.tree.topLevelItem(0)
        if recent_item is not None:
            recent_item.setExpanded(True)
        if not self._built:
            # 第一次进来把下载目录展开，和以前「直接看到文件列表」保持一致
            self._built = True
            expanded.add(str(downloads))
            self._expanded.add(str(downloads))
        if root_item is not None and str(downloads) in expanded:
            root_item.setExpanded(True)
        # 还原用户上次展开的文件夹：按层级从浅到深，父节点展开后子节点才存在
        for value in sorted(expanded, key=lambda text: len(Path(text).parts)):
            if value == str(downloads):
                continue
            item = self._find_item(value)
            if item is not None:
                item.setExpanded(True)

    def _downloads_item(self, downloads: Path) -> QTreeWidgetItem:
        """下载目录的根节点：内容懒加载，没有目录就先给一句提示。"""
        item = self._folder_item(downloads)
        item.setText(0, downloads.name or str(downloads))
        if not downloads.is_dir():
            item.takeChildren()
            item.addChild(self._hint_item(
                "还没有文档：点右上角「打开文件」，或在作业页下载课件"))
        return item

    def _recent_item(self) -> QTreeWidgetItem:
        """「最近打开」是虚拟分组节点，不占磁盘目录。"""
        item = QTreeWidgetItem(["最近打开"])
        item.setIcon(0, _glyph_icon(icons.CLOCK, theme.ACCENT2))
        item.setData(0, KIND_ROLE, "group")
        entries = [Path(value) for value in self._recent() if Path(value).exists()]
        for path in entries[:RECENT_MAX]:
            item.addChild(self._file_item(path))
        if not entries:
            item.addChild(self._hint_item("还没有打开过文档"))
        return item

    def _folder_item(self, path: Path) -> QTreeWidgetItem:
        item = QTreeWidgetItem([path.name or str(path)])
        item.setIcon(0, _glyph_icon(icons.FOLDER, theme.ACCENT))
        item.setData(0, KIND_ROLE, "folder")
        item.setData(0, PATH_ROLE, str(path))
        item.setToolTip(0, str(path))
        # 占位子节点：让文件夹先显示成「可展开」，真正的内容等展开时再列
        item.addChild(self._hint_item("展开查看…"))
        return item

    def _file_item(self, path: Path) -> QTreeWidgetItem:
        item = QTreeWidgetItem([path.name])
        item.setIcon(0, _glyph_icon(icons.DOCUMENT, theme.TEXT_SOFT))
        item.setData(0, KIND_ROLE, "file")
        item.setData(0, PATH_ROLE, str(path))
        item.setToolTip(0, str(path))
        return item

    @staticmethod
    def _hint_item(text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setData(0, KIND_ROLE, "hint")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setForeground(0, QColor(theme.MUTED))
        return item

    @staticmethod
    def _item_path(item: Optional[QTreeWidgetItem]) -> Optional[Path]:
        raw = item.data(0, PATH_ROLE) if item is not None else None
        return Path(raw) if raw else None

    def _fill_folder(self, item: QTreeWidgetItem, folder: Path) -> None:
        """把一个文件夹的直接子项填进节点：文件夹排在文件前面，深层继续懒加载。"""
        item.takeChildren()
        try:
            entries = sorted(folder.iterdir(),
                             key=lambda one: (not one.is_dir(), one.name.lower()))
        except OSError:
            entries = []
        added = 0
        for path in entries:
            try:
                if path.name.startswith("."):
                    continue
                if path.is_dir():
                    child = self._folder_item(path)
                elif path.suffix.lower() in docs_reader.SUPPORTED:
                    child = self._file_item(path)
                else:
                    continue
            except OSError:
                continue
            item.addChild(child)
            added += 1
        if not added:
            item.addChild(self._hint_item("（空文件夹）"))

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        path = self._item_path(item)
        if path is None or item.data(0, KIND_ROLE) != "folder":
            return
        key = str(path)
        self._expanded.add(key)
        if key in self._loaded:
            return
        self._loaded.add(key)
        self._fill_folder(item, path)

    def _on_collapsed(self, item: QTreeWidgetItem) -> None:
        path = self._item_path(item)
        if path is not None:
            self._expanded.discard(str(path))

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        """双击文件用现有 open_path 打开；双击文件夹就是展开 / 收起。"""
        kind = item.data(0, KIND_ROLE)
        if kind == "folder":
            item.setExpanded(not item.isExpanded())
            return
        if kind != "file":
            return
        path = self._item_path(item)
        if path is None:
            return
        # 打开文档会刷新整棵树（可能把当前节点删掉），所以等这轮信号走完再开
        QTimer.singleShot(0, lambda target=path: self.open_path(target))

    def _find_item(self, path: str) -> Optional[QTreeWidgetItem]:
        stack = [self.tree.topLevelItem(index)
                 for index in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            if str(item.data(0, PATH_ROLE) or "") == path:
                return item
            stack.extend(item.child(index) for index in range(item.childCount()))
        return None

    def expand_all(self) -> None:
        """全部展开：子目录是展开时懒加载的，所以要自己一层层往下走。"""
        for index in range(self.tree.topLevelItemCount()):
            self._expand_item(self.tree.topLevelItem(index))

    def _expand_item(self, item: Optional[QTreeWidgetItem]) -> None:
        if item is None:
            return
        if item.data(0, KIND_ROLE) == "folder" and not item.isExpanded():
            item.setExpanded(True)      # 展开时才把子节点填进来
        for index in range(item.childCount()):
            self._expand_item(item.child(index))

    def collapse_all(self) -> None:
        """全部收起：只留「最近打开」和下载目录两个顶层分组。"""
        self.tree.collapseAll()
        self._expanded.clear()

    # -------------------------------------------------- 打开
    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开文档", str(self.current_dir()),
            "文档 (*.pdf *.docx *.pptx *.xlsx *.txt *.md);;所有文件 (*.*)")
        if path:
            self.open_path(Path(path))

    def current_dir(self) -> Path:
        from . import settings_dirs

        return settings_dirs.downloads_dir(self.store)

    def open_downloads_dir(self) -> None:
        target = self.current_dir()
        target.mkdir(parents=True, exist_ok=True)
        open_external(str(target), local=True)

    def open_external(self) -> None:
        if self._current is not None:
            open_external(str(self._current), local=True)

    def open_path(self, path: Path) -> None:
        path = Path(path)
        try:
            kind, payload = docs_reader.render(path)
        except docs_reader.DocError as exc:
            self.subtitle.setText(str(exc))
            return
        self._current = path
        self._push_recent(path)
        if kind == "pdf":
            if self._pdf_doc is None:
                self.subtitle.setText("当前环境不支持 PDF 预览")
                return
            try:
                self._pdf_doc.close()
                error = self._pdf_doc.load(str(path))
                if error != self._pdf_doc.Error.None_:
                    self.subtitle.setText(f"PDF 打开失败：{error}")
                    return
                self.pdf_view.setDocument(self._pdf_doc)
                self._show_view(1)
                self._sync_page_label(0)
                self.kind_pill.set_text("PDF")
            except Exception as exc:  # noqa: BLE001
                self.subtitle.setText(f"PDF 打开失败：{exc}")
                return
        else:
            self.html_view.setHtml(self._wrap_html(payload))
            self._show_view(0)
            self.kind_pill.set_text(path.suffix.lower().lstrip(".").upper())
            self.page_label.setText("—")
        self.subtitle.setText(f"{path.name} · {self._human_size(path)}")
        self.store.set_setting("last_doc", str(path))
        self._set_pdf_mode(kind == "pdf")

    # -------------------------------------------------- 全屏
    def toggle_fullscreen(self) -> None:
        on = not getattr(self, "_fullscreen", False)
        self.sync_fullscreen_state(on)
        window = self.window()
        setter = getattr(window, "set_panel_fullscreen", None)
        if callable(setter):
            try:
                setter(on, self.viewer_card)
            except Exception:
                pass

    def sync_fullscreen_state(self, on: bool) -> None:
        """进出全屏：收起自己的标题行 / 翻页行 / 左侧目录树，把阅读区放到最大。"""
        self._fullscreen = bool(on)
        try:
            self.fullscreen_button.setText("退出全屏" if on else "全屏")
        except Exception:
            pass
        for widget in (getattr(self, "header_row", None),
                       getattr(self, "nav_row", None),
                       getattr(self, "list_card", None)):
            if widget is not None:
                try:
                    widget.setVisible(not on)
                except Exception:
                    pass
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not getattr(self, "_fullscreen", False):
            return
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QPen(QColor(theme.MUTED)))
        painter.drawText(
            self.rect().adjusted(0, 0, -18, -12),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
            "Esc 退出全屏 · 滚轮 / PgUp / PgDn 翻页",
        )

    def _set_pdf_mode(self, on: bool) -> None:
        """只有看 PDF 时才启用翻页快捷键，免得抢走文本视图的按键。"""
        for shortcut in self._shortcuts:
            try:
                shortcut.setEnabled(bool(on))
            except Exception:
                pass
        for button in (self.prev_button, self.next_button):
            try:
                button.setEnabled(bool(on))
            except Exception:
                pass

    def _show_view(self, index: int) -> None:
        """切换查看器，并强制让新视图拿到正确大小（否则 PDF 会缩在角落）。"""
        self.stack.setCurrentIndex(index)
        try:
            layout = self.stack.layout()
            if layout is not None:
                layout.activate()
        except Exception:
            pass
        try:
            widget = self.stack.currentWidget()
            if widget is not None:
                widget.updateGeometry()
                widget.update()
        except Exception:
            pass

    def _wrap_html(self, body: str) -> str:
        family = "、".join(theme.font_families()[:2])
        return (
            f"<html><body style=\"font-family:'{family}'; font-size:13px;"
            f" color:{theme.TEXT}; line-height:1.6\">"
            f"{body}</body></html>"
        )

    @staticmethod
    def _human_size(path: Path) -> str:
        try:
            size = path.stat().st_size
        except OSError:
            return ""
        if size > 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        if size > 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size} B"

    # -------------------------------------------------- 翻页
    def _flip_page(self, delta: int) -> None:
        if self._pdf_doc is None or self.stack.currentIndex() != 1:
            return
        try:
            total = self._pdf_doc.pageCount()
            navigator = self.pdf_view.pageNavigator()
            current = navigator.currentPage()
        except Exception:
            return
        if total <= 0:
            return
        target = max(0, min(total - 1, current + delta))
        if target == current:
            return
        # Qt 6.10 里叫 jump()，老版本叫 jumpTo()，两个都试一下
        jump = getattr(navigator, "jump", None) or getattr(navigator, "jumpTo", None)
        try:
            if jump is not None:
                jump(target, QPointF(0.0, 0.0))
        except Exception:
            pass
        try:
            if navigator.currentPage() != target:
                navigator.update(target, QPointF(0.0, 0.0), 1.0)
        except Exception:
            pass
        self._sync_page_label(target)

    def _sync_page_label(self, page: int = -1) -> None:
        if self._pdf_doc is None:
            return
        try:
            total = self._pdf_doc.pageCount()
            index = page if page >= 0 else self.pdf_view.pageNavigator().currentPage()
        except Exception:
            total, index = 0, 0
        self.page_label.setText(f"{index + 1}/{total}" if total else "—")

    # -------------------------------------------------- 快捷键
    def _install_shortcuts(self) -> None:
        for sequence, delta in (
            ("PgDown", 1), ("PgUp", -1), ("Space", 1),
        ):
            try:
                shortcut = QShortcut(QKeySequence(sequence), self)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(lambda d=delta: self._flip_page(d))
                shortcut.setEnabled(False)
                self._shortcuts.append(shortcut)
            except Exception:
                pass

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_list()
