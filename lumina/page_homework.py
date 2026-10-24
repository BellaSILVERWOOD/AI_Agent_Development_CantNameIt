"""作业页：内嵌 eLearning 网页（不弹窗）+ 课程/作业列表 + 一键下载。

附件来源：
* 课程文件夹（/api/v1/courses/:id/files）—— 主来源
* 作业描述里出现的文件链接（.../files/<id>/download）
下载前查重名：已存在就跳过，最后弹窗告知跳过几个。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QStackedWidget, QVBoxLayout, QWidget,
)

from . import elearning, icons, theme
from .web_panel import EmbeddedWebPanel
from .widgets import Card, Pill, SoftButton, make_scroll

FILE_LINK_RE = re.compile(
    r"(?:https?://[^\s\"'<>]*)?/files/(\d+)/download[^\s\"'<>]*", re.I)


class HomeworkPage(QWidget):
    """eLearning：内嵌网页登录 + 课程作业列表 + 批量下载。"""

    data_changed = Signal()
    open_document = Signal(str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._courses: List[Dict[str, Any]] = []
        self._assignments: List[Dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("作业")
        title.setObjectName("H1")
        self.subtitle = QLabel("内嵌 eLearning 网页，登录后读取本学期课程与作业")
        self.subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        self.count_pill = Pill("未读取", theme.MUTED, theme.CARD_ALT)
        header.addWidget(self.count_pill)

        self.read_button = SoftButton("读取我的课程", "ghost", height=34,
                                      min_width=132, point_size=8.8)
        self.read_button.clicked.connect(self.read_courses)
        header.addWidget(self.read_button)

        self.download_button = SoftButton("一键下载所有作业", "primary", height=34,
                                          min_width=158, glyph=icons.DOWNLOAD,
                                          point_size=8.8)
        self.download_button.clicked.connect(self.download_all)
        header.addWidget(self.download_button)

        self.open_dir_button = SoftButton("打开下载目录", "ghost", height=34,
                                          min_width=118, glyph=icons.FOLDER,
                                          point_size=8.8)
        self.open_dir_button.clicked.connect(self.open_download_dir)
        header.addWidget(self.open_dir_button)

        self.switch_button = SoftButton("看列表", "subtle", height=34, min_width=86,
                                        point_size=8.8)
        self.switch_button.clicked.connect(self.toggle_view)
        header.addWidget(self.switch_button)
        root.addLayout(header)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索课程或作业名…")
        self.search_edit.setFixedHeight(32)
        self.search_edit.textChanged.connect(lambda _t: self.reload())
        filter_row.addWidget(self.search_edit, 1)
        root.addLayout(filter_row)

        self.stack = QStackedWidget()
        self.web = EmbeddedWebPanel(elearning.PROFILE_NAME, "elearning",
                                    elearning.BASE_URL + "/", self,
                                    store=self.store)
        self.web.loaded.connect(
            lambda ok: self.subtitle.setText(
                "已打开 eLearning 网页，登录后点「读取我的课程」" if ok else "网页加载失败"))
        self.stack.addWidget(self.web)
        # 提前把内嵌浏览器建好（原生窗口在窗口显示前创建，第一次点「作业」就不会整窗闪一下）
        # 冒烟测试时跳过：避免和正在运行的实例抢同一个 Chromium 配置目录
        import os

        if not os.environ.get("LUMINA_SMOKE"):
            try:
                self.web.ensure_view()
            except Exception:
                pass

        list_host = QWidget()
        list_layout = QVBoxLayout(list_host)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll, _body, self.list_layout = make_scroll()
        list_layout.addWidget(self.scroll)
        self.stack.addWidget(list_host)
        root.addWidget(self.stack, 1)

        self.empty = QLabel("还没有读取课程：在网页里登录后点右上角「读取我的课程」")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reload()

    # -------------------------------------------------- 视图
    def toggle_view(self) -> None:
        if self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)
            self.switch_button.setText("看网页")
        else:
            self.stack.setCurrentIndex(0)
            self.switch_button.setText("看列表")

    # -------------------------------------------------- 读取
    def _client(self):
        """读课程用的客户端。

        顺序：
          1. 磁盘 cookie（只要在应用里登录过一次就有，最稳、且是同步的）；
          2. 页面通道（页面里带着 HttpOnly cookie，走页面自己的 fetch）；
          3. 裸客户端（没 cookie 时用来给出「先去登录」的提示）。
        """
        try:
            cookies = elearning.read_cookies()
        except Exception:
            cookies = {}
        if not cookies:
            # 会话过期：如果配了统一身份认证，就用它自动换一次 eLearning 会话
            try:
                from . import uis_login

                if uis_login.has_credentials(self.store):
                    cookies = uis_login.canvas_cookies(self.store)
            except Exception:
                cookies = {}
        if cookies:
            return elearning.ElearningClient(cookies)
        view = self.web.ensure_view()
        if view is not None:
            bridge = elearning.PageBridge(view)
            if bridge.has_cookies():
                return bridge
        return elearning.ElearningClient()

    def open_login_page(self) -> None:
        """切到网页视图，方便用户登录 eLearning。"""
        try:
            self.stack.setCurrentIndex(0)
            self.switch_button.setText("看列表")
            self.web.go_home()
            self.subtitle.setText("在网页里登录 eLearning，然后点「读取我的课程」")
        except Exception:
            pass

    def read_courses(self) -> None:
        self.subtitle.setText("正在读取课程与作业…")
        client = self._client()
        if not client.has_cookies():
            self.subtitle.setText(
                "还没有 eLearning 登录信息：先点右上角「看网页」登录一次，再点「读取我的课程」")
            return
        try:
            courses = elearning.list_courses(client)
        except Exception as exc:  # noqa: BLE001
            self.subtitle.setText(str(exc))
            return
        if not courses:
            self.subtitle.setText(
                "没读到课程：请先在网页里完成登录，再点一次「读取我的课程」")
            return
        self._courses = courses
        self._assignments = []
        for course in courses:
            try:
                self._assignments.extend(
                    elearning.list_assignments(client, course["id"],
                                               str(course.get("name", ""))))
            except elearning.ElearningError:
                continue
        self.subtitle.setText(
            f"已读取 {len(self._courses)} 门课程、{len(self._assignments)} 个作业")
        self.store.set_setting("elearning_status", self.subtitle.text())
        self.reload()
        self.stack.setCurrentIndex(1)
        self.switch_button.setText("看网页")

    # -------------------------------------------------- 渲染
    def reload(self) -> None:
        from .widgets import clear_layout

        clear_layout(self.list_layout)
        keyword = self.search_edit.text().strip().lower()
        for course in self._courses:
            items = [item for item in self._assignments
                     if item.get("course_id") == course.get("id")]
            if keyword and keyword not in str(course.get("name", "")).lower() and not any(
                    keyword in str(item.get("name", "")).lower() for item in items):
                continue
            self.list_layout.addWidget(self._course_card(course, items, keyword))
        if not self._courses:
            self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)
        if self._courses:
            self.count_pill.set_text(
                f"{len(self._courses)} 门课 · {len(self._assignments)} 个作业")
        else:
            self.count_pill.set_text("未读取")

    def _course_card(self, course: Dict[str, Any], items: List[Dict[str, Any]],
                     keyword: str) -> Card:
        card = Card(radius=14, fill=theme.CARD, border=theme.BORDER, padding=14,
                    spacing=6)
        top = QHBoxLayout()
        top.setSpacing(8)
        name = QLabel(str(course.get("name", "")))
        font = QFont(self.font())
        font.setPointSizeF(10.5)
        font.setWeight(QFont.Weight.DemiBold)
        name.setFont(font)
        top.addWidget(name)
        top.addStretch(1)
        top.addWidget(Pill(f"{len(items)} 个作业", theme.MUTED, theme.CARD_ALT,
                           point_size=8.0))
        card.body.addLayout(top)

        for item in items:
            if keyword and keyword not in str(item.get("name", "")).lower() and (
                    keyword not in str(course.get("name", "")).lower()):
                continue
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(f"· {item.get('name', '')}")
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {theme.TEXT_SOFT}; font-size: 12px;")
            row.addWidget(label, 1)
            due = str(item.get("due_at") or "").replace("T", " ")[:16]
            if due:
                due_label = QLabel(due)
                due_label.setObjectName("Tiny")
                row.addWidget(due_label)
            open_button = SoftButton("打开", "subtle", height=26, min_width=56,
                                     point_size=8.2)
            open_button.clicked.connect(
                lambda _checked=False, name=str(item.get("name", "")):
                self._open_downloaded(name))
            row.addWidget(open_button)
            card.body.addLayout(row)
        return card

    # -------------------------------------------------- 文件与下载
    def download_target_dir(self) -> Path:
        from . import settings_dirs

        return settings_dirs.downloads_dir(self.store)

    def _collect_items(self, client) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        seen = set()
        # ① 课程文件
        for course in self._courses:
            for file_item in elearning.list_files(client, course["id"]):
                url = file_item.get("url") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                items.append({"name": file_item["name"], "url": url})
        # ② 作业描述里的文件链接
        for assignment in self._assignments:
            for attachment in assignment.get("attachments") or []:
                url = str(attachment.get("url") or "")
                if not url or url in seen:
                    continue
                seen.add(url)
                items.append({"name": attachment.get("name") or "附件", "url": url})
        return items

    def download_all(self) -> None:
        if not self._courses:
            self.subtitle.setText("先读取课程，再一键下载")
            return
        client = self._client()
        if not client.has_cookies():
            self.subtitle.setText("还没有 eLearning 登录信息，先在网页里登录")
            return
        self.download_button.setEnabled(False)
        self.subtitle.setText("正在收集文件清单…")
        try:
            items = self._collect_items(client)
        except Exception as exc:  # noqa: BLE001
            self.subtitle.setText(f"收集文件失败：{exc}")
            self.download_button.setEnabled(True)
            return
        if not items:
            self.subtitle.setText("没有找到可下载的文件（课程文件为空）")
            self.download_button.setEnabled(True)
            return
        target = self.download_target_dir()
        self.subtitle.setText(f"正在下载 {len(items)} 个文件到 {target} …")

        def _progress(done: int, total: int, name: str) -> None:
            if done % 5 == 0 or done == total:
                self.subtitle.setText(f"正在下载 {done}/{total}：{name[:32]}")
                from PySide6.QtWidgets import QApplication

                QApplication.processEvents()

        result: Dict[str, Any] = {"saved": [], "skipped": [], "failed": []}
        try:
            result = elearning.download_all(client, items, target,
                                            on_progress=_progress)
        except Exception as exc:  # noqa: BLE001
            self.subtitle.setText(f"下载失败：{exc}")
        self.download_button.setEnabled(True)
        self._report_result(result, target)

    def _report_result(self, result: Dict[str, Any], target: Path) -> None:
        saved = result.get("saved") or []
        skipped = result.get("skipped") or []
        failed = result.get("failed") or []
        self.subtitle.setText(
            f"下载完成：新增 {len(saved)} 个，跳过重名 {len(skipped)} 个，"
            f"失败 {len(failed)} 个 → {target}")
        if not (skipped or failed):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("下载结果")
        dialog.resize(480, 320)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        summary = QLabel(
            f"已下载 {len(saved)} 个文件。\n"
            f"其中 {len(skipped)} 个同名文件已存在，已跳过（不覆盖、不重复下载）。"
            + (f"\n另有 {len(failed)} 个下载失败。" if failed else ""))
        summary.setWordWrap(True)
        layout.addWidget(summary)
        if skipped:
            names = QLabel("跳过的文件：\n" + "\n".join(f"· {n}" for n in skipped[:12])
                           + ("\n…" if len(skipped) > 12 else ""))
            names.setWordWrap(True)
            names.setObjectName("Tiny")
            layout.addWidget(names)
        row = QHBoxLayout()
        row.addStretch(1)
        open_button = SoftButton("打开目录", "ghost", height=32, min_width=96)
        open_button.clicked.connect(self.open_download_dir)
        ok_button = SoftButton("知道了", "primary", height=32, min_width=96)
        ok_button.clicked.connect(dialog.accept)
        row.addWidget(open_button)
        row.addWidget(ok_button)
        layout.addLayout(row)
        dialog.setStyleSheet(f"QDialog {{ background: {theme.CARD}; }}"
                             f"QLabel {{ color: {theme.TEXT}; }}")
        dialog.exec()

    def _open_downloaded(self, name: str) -> None:
        """尝试打开下载目录里与该作业同名的文件。"""
        from . import settings_dirs
        from .elearning import safe_filename

        target_dir = settings_dirs.downloads_dir(self.store)
        candidate = target_dir / safe_filename(name)
        if candidate.exists():
            self.open_document.emit(str(candidate))
            return
        # 宽松匹配：文件名包含作业名
        try:
            for path in target_dir.iterdir():
                if path.is_file() and name and name in path.name:
                    self.open_document.emit(str(path))
                    return
        except Exception:
            pass
        self.subtitle.setText("下载目录里还没有这个作业的文件，先点「一键下载所有作业」")

    def open_download_dir(self) -> None:
        target = self.download_target_dir()
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def showEvent(self, event):
        super().showEvent(event)
        status = self.store.get_setting("elearning_status", "") or ""
        if status and self._courses:
            self.subtitle.setText(status)
