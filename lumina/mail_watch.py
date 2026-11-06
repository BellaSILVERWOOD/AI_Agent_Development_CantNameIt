"""Agent 盯邮箱：只看指定发件人的新邮件，识别里面的待办，等用户确认再加日程。

设置项：
    mail_watch_enabled   "1" / "0"
    mail_watch_senders   发件人关键词（逗号分隔，匹配「名字」或「地址」）
    mail_watch_minutes   多久看一次（分钟，默认 10）
    mail_watch_seen      已经处理过的邮件（account_id:uid 列表，JSON）

流程（每 `minutes` 分钟一次）：
    1. 用 IMAP 拉每个账户最近几封（不下载正文以外的附件）；
    2. 只看发件人关键词命中的、且没处理过的；
    3. 把这几封的摘要 + 正文丢给 `agent.extract()`（模型优先，没配 Key 用规则）；
    4. 识别出候选日程 → 弹「把这几件事加进日程？」的确认窗口（复用 Agent 对话框）。

对外接口：
    enabled(store) / senders(store) / interval_minutes(store)
    set_enabled(store, on) / set_senders(store, text) / set_interval(store, minutes)
    fetch_matching(store, limit_per_account)  -> (mails, errors)
    check(store)                              -> (mails, items, errors)
    MailWatcher(store)  # 后台定时检查，发现待办发 found(mails, items)
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

from . import agent, imap_inbox

ENABLED_KEY = "mail_watch_enabled"
SENDERS_KEY = "mail_watch_senders"
MINUTES_KEY = "mail_watch_minutes"
SEEN_KEY = "mail_watch_seen"
SEEN_LIMIT = 300
DEFAULT_MINUTES = 10


# ------------------------------------------------------------------ 设置
def enabled(store) -> bool:
    return (store.get_setting(ENABLED_KEY, "0") or "0") == "1"


def set_enabled(store, on: bool) -> None:
    store.set_setting(ENABLED_KEY, "1" if on else "0")


def senders(store) -> List[str]:
    raw = store.get_setting(SENDERS_KEY, "") or ""
    return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]


def set_senders(store, text: str) -> None:
    store.set_setting(SENDERS_KEY, (text or "").strip())


def interval_minutes(store) -> int:
    try:
        value = int(float(store.get_setting(MINUTES_KEY, str(DEFAULT_MINUTES))
                          or DEFAULT_MINUTES))
    except (TypeError, ValueError):
        value = DEFAULT_MINUTES
    return max(2, min(180, value))


def set_interval(store, minutes: int) -> None:
    try:
        value = max(2, min(180, int(minutes)))
    except (TypeError, ValueError):
        value = DEFAULT_MINUTES
    store.set_setting(MINUTES_KEY, str(value))


def _seen(store) -> List[str]:
    try:
        data = json.loads(store.get_setting(SEEN_KEY, "[]") or "[]")
    except Exception:
        return []
    return [str(item) for item in data][-SEEN_LIMIT:] if isinstance(data, list) else []


def _mark_seen(store, keys: List[str]) -> None:
    merged = _seen(store) + [str(item) for item in keys]
    # 去重并保留最近的
    unique: List[str] = []
    for item in merged:
        if item not in unique:
            unique.append(item)
    store.set_setting(SEEN_KEY, json.dumps(unique[-SEEN_LIMIT:], ensure_ascii=False))


def reset_seen(store) -> None:
    store.set_setting(SEEN_KEY, "[]")


# ------------------------------------------------------------------ 抓取
def _matches(mail: Dict[str, Any], keywords: List[str]) -> bool:
    if not keywords:
        return False
    blob = (str(mail.get("from") or "") + " " + str(mail.get("account_name") or "")).lower()
    return any(word.lower() in blob for word in keywords)


def fetch_matching(store, limit_per_account: int = 5) -> Tuple[List[Dict[str, Any]],
                                                               List[str]]:
    """只看命中的新邮件（不含已经处理过的）。"""
    keywords = senders(store)
    if not keywords:
        return [], ["还没设置发件人关键词"]
    seen = set(_seen(store))
    matched: List[Dict[str, Any]] = []
    errors: List[str] = []
    for account in imap_inbox.list_accounts(store):
        try:
            mails = imap_inbox.fetch_account(store, account, limit_per_account)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{account.get('name') or account.get('host')}：{exc}")
            continue
        for mail in mails:
            key = f"{mail.get('account_id')}:{mail.get('uid')}"
            if key in seen:
                continue
            if not _matches(mail, keywords):
                continue
            mail["_key"] = key
            matched.append(mail)
    matched.sort(key=lambda item: item.get("date") or "", reverse=True)
    return matched, errors


def check(store) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """抓一次 + 识别一次，返回 (邮件, 候选日程, 错误)。"""
    mails, errors = fetch_matching(store)
    if not mails:
        return [], [], errors
    text_parts = []
    for mail in mails[:5]:
        text_parts.append(
            f"{mail.get('subject', '')}\n{mail.get('snippet', '')}")
    text = "\n\n".join(part for part in text_parts if part.strip())
    if not text.strip():
        return mails, [], errors
    result = agent.extract(store, text)
    items = result.get("items") or []
    if result.get("error"):
        errors.append(result["error"])
    return mails, items, errors


def consume(store, mails: List[Dict[str, Any]]) -> None:
    """用户已经看过这批邮件了，记下来别再提示。"""
    _mark_seen(store, [str(mail.get("_key") or "") for mail in mails])


# ------------------------------------------------------------------ 定时器
class MailWatcher(QObject):
    """后台定时检查；发现命中且有候选日程时发 `found(mails, items)`。"""

    found = Signal(list, list)
    status = Signal(str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self.check_now)
        self._started = False

    def start(self, first_delay_ms: int = 45000) -> None:
        if self._started:
            self.restart()
            return
        self._started = True
        self.restart()
        if first_delay_ms:
            QTimer.singleShot(first_delay_ms, self.check_now)

    def stop(self) -> None:
        self._timer.stop()
        self._started = False

    def restart(self) -> None:
        minutes = interval_minutes(self.store)
        self._timer.start(max(2, minutes) * 60 * 1000)
        if not self._started:
            self._started = True
        self.status.emit(f"每 {minutes} 分钟看一次邮箱")

    def check_now(self) -> None:
        if self._busy or not enabled(self.store):
            return
        if not senders(self.store):
            return
        if not imap_inbox.list_accounts(self.store):
            return
        self._busy = True
        self.status.emit("正在看邮箱…")

        def worker() -> None:
            try:
                mails, items, errors = check(self.store)
            except Exception as exc:  # noqa: BLE001
                mails, items, errors = [], [], [str(exc)]
            self._busy = False
            if errors:
                self.status.emit(errors[0][:60])
            if items:
                self.found.emit(mails, items)
            else:
                self.status.emit("邮箱里没有需要加日程的事")

        threading.Thread(target=worker, name="lumina-mail-watch",
                         daemon=True).start()
