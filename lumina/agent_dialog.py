"""Agent 对话窗口：最简洁的形态 —— 一个聊天框 + 一条待确认的日程清单。

做的事：
1. 从微信（剪贴板复制 / UI 自动化）拿到聊天内容；
2. 找出「需要用户去做、且有时间」的事项（配了 API Key 用模型，没配用规则）；
3. 列出候选日程，勾选确认后才写进日历 —— 绝不自动加。

依赖：agent.py（识别与写入）、wechat.py（取消息）、ai_chat.py（模型调用）
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import agent, theme, wechat
from .widgets import SoftButton

WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
UIA_KEY = "agent_read_wechat"


def tokens() -> Dict[str, str]:
    """按当前明暗模式取颜色，和主界面一致（不要在对话框里写死浅色值）。"""
    mode = "dark" if theme.effective_mode() == "dark" else "light"
    values = theme.PALETTES.get(mode) or theme.PALETTES["light"]
    return {
        "card": values["CARD"],
        "alt": values["CARD_ALT"],
        "text": values["TEXT"],
        "soft": values["TEXT_SOFT"],
        "border": values["BORDER"],
        "accent": theme.ACCENT,
    }


class ExtractWorker(QObject):
    """在后台线程里识别，避免界面卡住（模型请求可能几秒）。"""

    done = Signal(dict)

    def __init__(self, store, text: str):
        super().__init__()
        self.store = store
        self.text = text

    def run(self) -> None:
        try:
            self.done.emit(agent.extract(self.store, self.text))
        except Exception as exc:  # noqa: BLE001
            self.done.emit({"ok": False, "items": [], "source": "none",
                            "error": str(exc)})


class AgentDialog(QDialog):
    """简洁的 Agent 对话窗口。"""

    changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._thread: Optional[QThread] = None
        self._worker: Optional[ExtractWorker] = None
        self._items: List[Dict[str, Any]] = []
        self._boxes: List[QCheckBox] = []

        self.setWindowTitle("DanLume · Agent")
        self.resize(700, 640)
        colors = tokens()
        self._colors = colors
        self.setStyleSheet(f"QDialog {{ background: {colors['card']}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # -------------------------------------------------- 顶部
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("Agent")
        title.setObjectName("H2")
        head.addWidget(title)
        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        head.addWidget(self.status)
        head.addStretch(1)
        self.wechat_button = SoftButton("读微信", "ghost", height=30, min_width=76,
                                        point_size=8.6)
        self.wechat_button.setToolTip("先在本机微信里选中消息并复制，再点这里")
        self.wechat_button.clicked.connect(self.read_wechat)
        head.addWidget(self.wechat_button)
        self.send_wechat_button = SoftButton("发微信", "ghost", height=30,
                                             min_width=76, point_size=8.6)
        self.send_wechat_button.setToolTip("让 agent 帮你给指定的人发一条消息（要确认）")
        self.send_wechat_button.clicked.connect(self.open_send_dialog)
        head.addWidget(self.send_wechat_button)
        self.clear_button = SoftButton("清空", "ghost", height=30, min_width=56,
                                       point_size=8.6)
        self.clear_button.clicked.connect(self.clear_all)
        head.addWidget(self.clear_button)
        root.addLayout(head)

        # 直读开关：截当前聊天窗口 + 系统 OCR，不用手动复制
        self.uia_box = QCheckBox("允许直接读取微信（截取聊天窗口识别文字）")
        self.uia_box.setChecked(
            (self.store.get_setting(UIA_KEY, "0") or "0") == "1")
        self.uia_box.setStyleSheet(f"QCheckBox {{ color: {colors['soft']}; }}")
        if wechat.direct_available():
            self.uia_box.toggled.connect(self._on_uia_toggled)
            self.uia_box.setToolTip(
                "用系统自带 OCR 识别微信当前聊天窗口里的文字（窗口在后台也能截）。"
                "识别可能有错字，重要内容请核对。")
        else:
            self.uia_box.setChecked(False)
            self.uia_box.setEnabled(False)
            self.uia_box.setText("直接读取微信不可用（需要 PowerShell + 系统中文 OCR）")
        root.addWidget(self.uia_box)

        # -------------------------------------------------- 盯邮箱
        watch_row = QHBoxLayout()
        watch_row.setSpacing(6)
        from . import mail_watch

        self.watch_box = QCheckBox("盯邮箱")
        self.watch_box.setStyleSheet(f"QCheckBox {{ color: {colors['soft']}; }}")
        self.watch_box.setChecked(mail_watch.enabled(self.store))
        self.watch_box.setToolTip(
            "只看这些发件人的新邮件：识别里面的待办，弹窗让你确认后再加日程")
        self.watch_box.toggled.connect(self._on_watch_toggled)
        watch_row.addWidget(self.watch_box)
        self.watch_senders = QLineEdit(
            self.store.get_setting(mail_watch.SENDERS_KEY, ""))
        self.watch_senders.setPlaceholderText("发件人关键词，逗号分隔（如 教务处,李老师）")
        self.watch_senders.setFixedHeight(28)
        self.watch_senders.editingFinished.connect(self._on_watch_senders_changed)
        watch_row.addWidget(self.watch_senders, 1)
        self.watch_hint = QLabel("")
        self.watch_hint.setObjectName("Tiny")
        watch_row.addWidget(self.watch_hint)
        root.addLayout(watch_row)
        self._refresh_watch_hint()

        # -------------------------------------------------- 聊天记录
        self.view = QTextBrowser()
        self.view.setStyleSheet(
            f"QTextBrowser {{ background: {colors['alt']}; color: {colors['text']};"
            f" border: 1px solid {colors['border']}; border-radius: 10px; padding: 10px; }}")
        root.addWidget(self.view, 1)

        # -------------------------------------------------- 候选日程
        self.candidate_card = QFrame()
        self.candidate_card.setObjectName("AgentCandidates")
        self.candidate_card.setVisible(False)
        self.candidate_card.setStyleSheet(
            f"QFrame#AgentCandidates {{ background: {colors['card']};"
            f" border: 1px solid {colors['border']}; border-radius: 10px; }}")
        card_layout = QVBoxLayout(self.candidate_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)
        self.candidate_title = QLabel("")
        self.candidate_title.setObjectName("Tiny")
        self.candidate_title.setStyleSheet(f"color: {colors['soft']};")
        card_layout.addWidget(self.candidate_title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet(
            f"QScrollArea, QScrollArea > QWidget > QWidget {{"
            f" background: transparent; border: 0px; }}")
        self.list_host = QWidget()
        self.list_host.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(2)
        self.scroll.setWidget(self.list_host)
        card_layout.addWidget(self.scroll)
        self.candidate_card.setMaximumHeight(210)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.ignore_button = SoftButton("都不要", "ghost", height=30, min_width=68,
                                        point_size=8.6)
        self.ignore_button.clicked.connect(self.dismiss_candidates)
        buttons.addWidget(self.ignore_button)
        self.confirm_button = SoftButton("加入日程", "primary", height=30,
                                         min_width=88, point_size=8.6)
        self.confirm_button.clicked.connect(self.confirm_candidates)
        buttons.addWidget(self.confirm_button)
        card_layout.addLayout(buttons)
        root.addWidget(self.candidate_card)

        # -------------------------------------------------- 输入
        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("粘微信消息，或直接说「明天下午三点组会」…（Enter 发送）")
        self.input.setFixedHeight(36)
        self.input.returnPressed.connect(self.send)
        row.addWidget(self.input, 1)
        self.send_button = SoftButton("发送", "primary", height=36, min_width=88)
        self.send_button.clicked.connect(self.send)
        row.addWidget(self.send_button)
        root.addLayout(row)

        self._refresh_status()
        self._append("system",
                     "把微信里的聊天记录复制过来（或点「读微信」），我会挑出需要你去做的事，"
                     "勾选确认后才加进日程。开了上面的「允许直接读取微信」就不用复制，"
                     "直接点「读微信」截当前聊天窗口识别。")

    # -------------------------------------------------- 状态
    def _refresh_status(self) -> None:
        parts = []
        parts.append("微信在运行" if wechat.running() else "没检测到微信窗口")
        if wechat.direct_available():
            parts.append("直读已开" if self.uia_box.isChecked() else "直读关（用剪贴板）")
        else:
            parts.append("用剪贴板导入")
        parts.append("模型识别" if agent.api_ready(self.store)
                     else "规则识别（可在设置里填 API Key 提高准确度）")
        self.status.setText(" · ".join(parts))

    def _append(self, role: str, text: str) -> None:
        colors = getattr(self, "_colors", {}) or {}
        color = colors.get("accent" if role == "user" else "soft", theme.TEXT_SOFT)
        label = "我" if role == "user" else ("Agent" if role == "assistant" else "说明")
        body = (text or "").replace("\n", "<br>")
        self.view.append(
            f"<p style='color:{color}; margin:6px 4px;'><b>{label}：</b>{body}</p>")
        try:
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    # -------------------------------------------------- 盯邮箱
    def _refresh_watch_hint(self) -> None:
        try:
            from . import imap_inbox, mail_watch

            if not imap_inbox.list_accounts(self.store):
                self.watch_hint.setText("（还没配置 IMAP 账户）")
                return
            if not mail_watch.enabled(self.store):
                self.watch_hint.setText("（关）")
                return
            minutes = mail_watch.interval_minutes(self.store)
            if not mail_watch.senders(self.store):
                self.watch_hint.setText("（填个发件人）")
            else:
                self.watch_hint.setText(f"每 {minutes} 分钟看一次")
        except Exception:
            self.watch_hint.setText("")

    def _on_watch_toggled(self, on: bool) -> None:
        from . import mail_watch

        mail_watch.set_enabled(self.store, on)
        self._refresh_watch_hint()
        self._restart_watcher()

    def _on_watch_senders_changed(self) -> None:
        from . import mail_watch

        mail_watch.set_senders(self.store, self.watch_senders.text())
        self._refresh_watch_hint()
        self._restart_watcher()

    def _restart_watcher(self) -> None:
        window = self.window()
        watcher = getattr(window, "mail_watch", None)
        if watcher is not None:
            try:
                watcher.restart()
            except Exception:
                pass

    # -------------------------------------------------- 外部喂数据（盯邮箱用）
    def present_items(self, items: List[Dict[str, Any]], note: str = "") -> None:
        """把别处识别出来的候选日程直接摆到确认列表里。"""
        items = [dict(item) for item in (items or [])]
        if not items:
            self._append("system", "这次没识别到需要加日程的事")
            return
        if note:
            self._append("system", note)
        self._append("assistant",
                     f"识别到 {len(items)} 条可能需要加进日程的事，你确认一下：")
        self._show_candidates(items)

    # -------------------------------------------------- 发微信
    def open_send_dialog(self) -> None:
        from .wechat_send_dialog import WechatSendDialog

        contact = ""
        text = self.input.text().strip()
        dialog = WechatSendDialog(self.store, self, contact=contact, text=text)
        dialog.exec()

    # -------------------------------------------------- 取消息
    def _uia_allowed(self) -> bool:
        return (wechat.direct_available()
                and self.uia_box.isChecked()
                and self.uia_box.isEnabled())

    def _on_uia_toggled(self, on: bool) -> None:
        try:
            self.store.set_setting(UIA_KEY, "1" if on else "0")
        except Exception:
            pass
        self._refresh_status()

    def read_wechat(self) -> None:
        if self._busy():
            return
        text = ""
        direct = self._uia_allowed()
        if direct:
            try:
                text = wechat.grab_current_chat()
            except Exception as exc:  # noqa: BLE001
                self._append("system", f"直接读微信没成功（{exc}），改用剪贴板")
                text = ""
        if not text.strip():
            text = wechat.clipboard_text()
        if not text.strip():
            if direct:
                self._append("system",
                             "直接读没拿到内容，剪贴板也是空的。微信里要先打开一个聊天，"
                             "或者选好消息右键「复制」再试。")
            else:
                self._append("system",
                             "剪贴板里没有内容。先在微信里选中要看的消息，右键「复制」，"
                             "再点一次「读微信」；或者把消息直接粘到输入框。")
            return
        how = "直接读取的当前聊天" if direct else "剪贴板"
        blocks = wechat.split_blocks(text)
        self._append("system", f"从{how}拿到 {len(blocks)} 条消息，正在识别…")
        self.analyze(text)

    # -------------------------------------------------- 识别
    def send(self) -> None:
        text = self.input.text().strip()
        if not text or self._busy():
            return
        self.input.clear()
        self._append("user", text)
        self.analyze(text)

    def analyze(self, text: str) -> None:
        self._set_busy(True)
        worker = ExtractWorker(self.store, text)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_done)
        worker.done.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thread_finished(self) -> None:
        # QThread 会被 deleteLater 回收，这里立刻丢掉引用，避免访问已销毁的 C++ 对象
        self._thread = None
        self._worker = None

    def _on_done(self, result: dict) -> None:
        self._set_busy(False)
        source = result.get("source") or "rules"
        items = result.get("items") or []
        error = (result.get("error") or "").strip()
        if error:
            self._append("system", error)
        if not items:
            self._append("assistant",
                         "这条里没找到需要加日程的事（要同时有时间，比如「明天下午三点」「10月25日 开会」）。")
            self.candidate_card.setVisible(False)
            return
        how = "模型" if source == "model" else "规则"
        self._append("assistant", f"用{how}识别到 {len(items)} 条可能需要加进日程的事，你确认一下：")
        self._show_candidates(items)

    def _busy(self) -> bool:
        thread = self._thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            self._thread = None
            return False

    def _set_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        self.wechat_button.setEnabled(not busy)
        self.send_button.setText("识别中…" if busy else "发送")

    # -------------------------------------------------- 候选清单
    def _format_item(self, item: Dict[str, Any]) -> str:
        day = str(item.get("date") or "")
        weekday = ""
        try:
            weekday = WEEKDAY_NAMES[datetime.strptime(day, "%Y-%m-%d").weekday()]
        except Exception:
            weekday = ""
        start = item.get("start_time") or ""
        end = item.get("end_time") or ""
        span = start if not end else f"{start}-{end}"
        location = (item.get("location") or "").strip()
        head = f"{day[5:]} {weekday} {span} · {item.get('title') or '未命名'}"
        if location:
            head += f" @ {location}"
        if item.get("time_guessed"):
            head += "（没提到时间，按 9:00）"
        return head

    def _show_candidates(self, items: List[Dict[str, Any]]) -> None:
        while self.list_layout.count():
            child = self.list_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        self._items = list(items)
        self._boxes = []
        colors = self._colors
        row_height = 30
        for item in self._items:
            box = QCheckBox(self._format_item(item))
            box.setChecked(True)
            box.setStyleSheet(
                f"QCheckBox {{ color: {colors['text']}; background: transparent;"
                f" spacing: 8px; padding: 4px 2px; }}")
            note = (item.get("note") or "").strip()
            if note:
                box.setToolTip(note)
            self.list_layout.addWidget(box)
            self._boxes.append(box)
        self.list_layout.addStretch(1)
        row_total = min(len(self._items), 6) * row_height + 6
        self.scroll.setFixedHeight(row_total)
        self.candidate_title.setText(
            f"识别到 {len(self._items)} 条，勾选后点「加入日程」：")
        self.candidate_card.setVisible(True)

    def confirm_candidates(self) -> None:
        picked = [item for item, box in zip(self._items, self._boxes)
                  if box.isChecked()]
        if not picked:
            self._append("system", "一条都没勾，就没加。")
            return
        added, failed = 0, 0
        for item in picked:
            try:
                agent.add_to_calendar(self.store, item)
                added += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self._append("system", f"「{item.get('title')}」没加上：{exc}")
        if added:
            self.changed.emit()
        lines = [f"已加入 {added} 条" if added else "没加进去"]
        for item in picked[:6]:
            lines.append("· " + self._format_item(item))
        self._append("assistant", "\n".join(lines))
        self.dismiss_candidates()

    def dismiss_candidates(self) -> None:
        self._items = []
        self._boxes = []
        self.candidate_card.setVisible(False)

    def clear_all(self) -> None:
        if self._busy():
            return
        self.view.clear()
        self.dismiss_candidates()
        self._append("system", "已清空。把微信消息粘进来，或点「读微信」。")
