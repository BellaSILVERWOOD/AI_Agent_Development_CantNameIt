"""导入考试安排：内嵌浏览器读教务页面 → 确认（可改）→ 写日历 + 重要日。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import exams, icons, theme
from .widgets import SoftButton, clear_layout


def _colors() -> Dict[str, str]:
    mode = "dark" if theme.effective_mode() == "dark" else "light"
    return theme.PALETTES.get(mode) or theme.PALETTES["light"]


class ExamRowWidget(QWidget):
    """一行可编辑的考试：勾选 / 名称 / 日期 / 时间 / 地点。"""

    def __init__(self, item: Dict[str, Any], parent=None):
        super().__init__(parent)
        colors = _colors()
        self.setStyleSheet(
            f"QWidget {{ background: {colors['CARD_ALT']}; border-radius: 8px; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self.box = QCheckBox()
        self.box.setChecked(True)
        layout.addWidget(self.box)

        self.title_edit = self._field(str(item.get("title") or ""), "课程/考试", 3)
        layout.addWidget(self.title_edit, 3)

        self.date_edit = self._field(str(item.get("date") or ""), "YYYY-MM-DD", 0)
        self.date_edit.setFixedWidth(96)
        layout.addWidget(self.date_edit)

        self.start_edit = self._field(str(item.get("start_time") or ""), "HH:MM", 0)
        self.start_edit.setFixedWidth(62)
        layout.addWidget(self.start_edit)

        self.end_edit = self._field(str(item.get("end_time") or ""), "结束", 0)
        self.end_edit.setFixedWidth(62)
        layout.addWidget(self.end_edit)

        self.location_edit = self._field(str(item.get("location") or ""), "地点", 0)
        self.location_edit.setFixedWidth(150)
        layout.addWidget(self.location_edit)

        self.item = dict(item)

    def _field(self, text: str, placeholder: str, _weight: int) -> QLineEdit:
        edit = QLineEdit(text)
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(30)
        return edit

    def payload(self) -> Dict[str, Any]:
        merged = dict(self.item)
        merged.update({
            "title": self.title_edit.text().strip() or "考试",
            "date": self.date_edit.text().strip(),
            "start_time": self.start_edit.text().strip(),
            "end_time": self.end_edit.text().strip(),
            "location": self.location_edit.text().strip(),
        })
        return merged


class ExamImportDialog(QDialog):
    """两步：① 浏览器登录并读取；② 勾选确认后加入日历。"""

    imported = Signal(int)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._rows: List[ExamRowWidget] = []
        colors = _colors()
        self.setStyleSheet(f"QDialog {{ background: {colors['CARD']}; }}")
        self.setWindowTitle("DanLume · 导入考试安排")
        self.resize(1120, 820)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        self.title_label = QLabel("导入考试安排")
        self.title_label.setObjectName("H2")
        head.addWidget(self.title_label)
        self.hint = QLabel("① 在下面的窗口登录教务（清华统一身份认证）② 打开「我的考试」③ 点「读取考试」")
        self.hint.setObjectName("Tiny")
        self.hint.setWordWrap(True)
        head.addWidget(self.hint, 1)
        root.addLayout(head)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # ---------------- 第一页：浏览器
        browser_page = QWidget()
        browser_layout = QVBoxLayout(browser_page)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.setSpacing(8)
        self.view = self._make_view()
        if self.view is not None:
            browser_layout.addWidget(self.view, 1)
        else:
            missing = QLabel("这个环境缺少 QtWebEngine，没法打开登录窗口。")
            missing.setObjectName("Muted")
            browser_layout.addWidget(missing, 1)
        self.status = QLabel("等待登录…")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        browser_layout.addWidget(self.status)
        self.stack.addWidget(browser_page)

        # ---------------- 第二页：确认
        confirm_page = QWidget()
        confirm_layout = QVBoxLayout(confirm_page)
        confirm_layout.setContentsMargins(0, 0, 0, 0)
        confirm_layout.setSpacing(8)
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        confirm_layout.addWidget(self.summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: 0px; }")
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        scroll.setWidget(self.list_host)
        confirm_layout.addWidget(scroll, 1)

        self.important_box = QCheckBox("同时设为重要日（日历页显示倒计时）")
        self.important_box.setChecked(True)
        confirm_layout.addWidget(self.important_box)

        self.stack.addWidget(confirm_page)

        # ---------------- 底部按钮
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.back_button = SoftButton("返回浏览器", "ghost", height=34, min_width=110)
        self.back_button.clicked.connect(self._back_to_browser)
        self.back_button.setVisible(False)
        bottom.addWidget(self.back_button)
        bottom.addStretch(1)
        self.grab_button = SoftButton("读取考试", "primary", glyph=icons.DOWNLOAD,
                                      height=34, min_width=112)
        self.grab_button.clicked.connect(self.grab)
        bottom.addWidget(self.grab_button)
        self.add_button = SoftButton("加入日程", "primary", height=34, min_width=104)
        self.add_button.clicked.connect(self.confirm)
        self.add_button.setVisible(False)
        bottom.addWidget(self.add_button)
        close_button = SoftButton("关闭", "ghost", height=34, min_width=80)
        close_button.clicked.connect(self.reject)
        bottom.addWidget(close_button)
        root.addLayout(bottom)

    # -------------------------------------------------- 浏览器
    def _make_view(self):
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except Exception:
            return None
        from . import fdu

        profile = QWebEngineProfile("lumina-fdu", self)
        try:
            profile.setPersistentStoragePath(str(fdu.browser_dir()))
            profile.setCachePath(str(fdu.browser_dir() / "cache"))
            profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        except Exception:
            pass
        view = QWebEngineView(self)
        page = QWebEnginePage(profile, view)
        view.setPage(page)
        view.setUrl(QUrl(exams.EXAM_URLS[0]))
        self._page = page
        return view

    # -------------------------------------------------- 读取
    def grab(self) -> None:
        if getattr(self, "view", None) is None:
            return
        self.status.setText("正在读取页面…")
        self.grab_button.setEnabled(False)

        def _on_js(_result=None):
            self.view.page().runJavaScript(
                "JSON.stringify(window.__luminaExams||null)", self._on_payload)

        try:
            self.view.page().runJavaScript(exams.PAGE_JS, _on_js)
        except Exception as exc:  # noqa: BLE001
            self._fail(f"读取失败：{exc}")

    def _on_payload(self, raw) -> None:
        try:
            data: Optional[Dict[str, Any]] = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            data = None
        if not isinstance(data, dict):
            self._fail("页面里没拿到数据：先在浏览器里打开「我的考试」再点读取")
            return
        items = exams.parse_exams(data)
        if not items:
            text_len = len(str(data.get("text") or ""))
            self._fail(
                f"页面上没找到「日期 + 时间」的考试信息（正文 {text_len} 字）。"
                "确认已经在「我的考试」页面，往下滚动加载完再试。")
            return
        self.show_items(items, source=str(data.get("url") or ""))

    def _fail(self, message: str) -> None:
        self.status.setText(message)
        self.grab_button.setEnabled(True)

    def _back_to_browser(self) -> None:
        self.stack.setCurrentIndex(0)
        self.title_label.setText("导入考试安排")
        self.hint.setText(
            "① 在下面的窗口登录教务（统一身份认证）② 打开「我的考试」③ 点「读取考试」")
        self.grab_button.setVisible(True)
        self.grab_button.setText("读取考试")
        self.add_button.setVisible(False)
        self.back_button.setVisible(False)

    # -------------------------------------------------- 确认页
    def show_items(self, items: List[Dict[str, Any]], source: str = "") -> None:
        clear_layout(self.list_layout)
        self._rows = []
        for item in items:
            row = ExamRowWidget(item)
            self.list_layout.addWidget(row)
            self._rows.append(row)
        self.list_layout.addStretch(1)
        self.summary.setText(
            f"从教务读到 {len(items)} 场考试"
            + (f"（{source}）" if source else "")
            + "。可以改名称/时间/地点，勾掉的不会加。")
        self.stack.setCurrentIndex(1)
        self.title_label.setText("确认考试")
        self.hint.setText("确认下面这些考试：名称/日期/时间/地点都能改，勾掉的不加。")
        self.grab_button.setVisible(False)
        self.add_button.setVisible(True)
        self.back_button.setVisible(True)

    def confirm(self) -> None:
        payload = [row.payload() for row in self._rows if row.box.isChecked()]
        if not payload:
            self.summary.setText("一条都没勾。")
            return
        added, skipped = exams.add_exams(
            self.store, payload, as_important=self.important_box.isChecked())
        self.imported.emit(added)
        message = f"已加入 {added} 场考试"
        if skipped:
            message += f"，跳过 {skipped} 条（重复或没日期）"
        if self.important_box.isChecked() and added:
            message += "；已加进重要日倒计时"
        self.summary.setText(message)
        self.add_button.setVisible(False)
        self.back_button.setVisible(False)
        self.grab_button.setVisible(True)
        self.grab_button.setText("再读一次")
