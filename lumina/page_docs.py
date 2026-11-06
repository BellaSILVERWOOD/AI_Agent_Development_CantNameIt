"""文档页：在应用内打开 PDF / DOCX / PPTX / XLSX / 文本。

左侧是文件列表（下载目录 + 最近打开），右侧是查看器：
PDF 用 QtPdf，其它格式解析成 HTML 用 QTextBrowser 显示。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QPointF, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QStackedWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from . import docs_reader, icons, theme
from .utils import open_external
from .widgets import Card, IconButton, Pill, SoftButton, make_scroll

RECENT_KEY = "docs_recent"


class DocsPage(QWidget):
    """文档查看页。"""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._current: Optional[Path] = None
        self._pdf_doc = None
        self._shortcuts: List[QShortcut] = []

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
        list_title = QLabel("最近打开 / 下载目录")
        list_title.setObjectName("Small")
        list_card.body.addWidget(list_title)
        self.dir_label = QLabel("")
        self.dir_label.setObjectName("Tiny")
        list_card.body.addWidget(self.dir_label)
        self.scroll, _body, self.list_layout = make_scroll()
        list_card.body.addWidget(self.scroll, 1)
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

    # -------------------------------------------------- 列表
    def _recent(self) -> List[str]:
        raw = self.store.get_setting(RECENT_KEY, "") or ""
        return [item for item in raw.split("|") if item]

    def _push_recent(self, path: Path) -> None:
        items = [str(path)] + [item for item in self._recent() if item != str(path)]
        self.store.set_setting(RECENT_KEY, "|".join(items[:12]))
        self._refresh_list()

    def _refresh_list(self) -> None:
        from .widgets import clear_layout

        clear_layout(self.list_layout)
        entries: List[Path] = [Path(item) for item in self._recent() if Path(item).exists()]
        # 下载目录（eLearning 作业文件夹）里的文档也列出来，含课程子文件夹
        from . import settings_dirs

        downloads = settings_dirs.downloads_dir(self.store)
        try:
            if hasattr(self, "dir_label"):
                self.dir_label.setText(f"{downloads.name or downloads}")
                self.dir_label.setToolTip(str(downloads))
        except Exception:
            pass
        if downloads.exists():
            found: List[Path] = []
            try:
                for path in downloads.rglob("*"):
                    if not path.is_file():
                        continue
                    if path.suffix.lower() not in docs_reader.SUPPORTED:
                        continue
                    try:
                        depth = len(path.relative_to(downloads).parts)
                    except Exception:
                        depth = 9
                    if depth > 4:
                        continue
                    found.append(path)
            except Exception:
                found = []
            found.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            for path in found[:18]:
                if path not in entries:
                    entries.append(path)
        if not entries:
            empty = QLabel("还没有文档：点右上角「打开文件」，或在作业页下载课件")
            empty.setObjectName("Tiny")
            empty.setWordWrap(True)
            self.list_layout.addWidget(empty)
        for path in entries[:24]:
            self.list_layout.addWidget(self._row(path))
        self.list_layout.addStretch(1)

    def _row(self, path: Path) -> QWidget:
        row = QWidget()
        row.setFixedHeight(38)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)
        label = QLabel(path.stem[:28])
        label.setStyleSheet(f"color: {theme.TEXT_SOFT}; font-size: 12px;")
        label.setToolTip(str(path))
        layout.addWidget(label, 1)
        suffix = QLabel(path.suffix.lower().lstrip(".").upper())
        suffix.setObjectName("Tiny")
        layout.addWidget(suffix)
        row.mouseReleaseEvent = lambda event, p=path: self.open_path(p)
        return row

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
        """进出全屏：收起自己的标题行 / 翻页行 / 左侧列表，把阅读区放到最大。"""
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
