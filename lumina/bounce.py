"""退信识别：把 PostMaster / Delivery Status 的退信，和发件箱里那封邮件对上。

用途：发出去的邮件如果被对方服务器拒绝（比如地址不存在），
收件箱里会收到一封退信（`Undeliverable:` / `来自…的退信` / `Delivery Status`）。
这里把这些退信解析出「失败的收件地址 + 状态码」，再把发件箱里对应那封标成「已退信」。

识别退信的经验：
* 发件人常见 `postmaster@`、`MAILER-DAEMON`；
* 主题常见 `Undeliverable`、`退信`、`Delivery Status Notification`、`failure notice`；
* 正文里有 `550 5.5.0 …`、`mailbox unavailable`、`does not exist` 之类的字样。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
CODE_RE = re.compile(r"\b(5\d\d)\s+(\d\.\d{1,3}\.\d{1,3})\b[^\n\r]{0,120}")
SUBJECT_HINTS = ("undeliverable", "退信", "delivery status", "failure notice",
                 "delivery failure", "returned mail", "mail delivery failed")
REASON_HINTS = {
    "5.5.0": "对方邮箱不存在（mailbox unavailable）",
    "5.1.1": "收件人地址不存在",
    "5.1.10": "收件人地址不存在",
    "5.2.2": "对方邮箱已满",
    "5.2.1": "对方邮箱已满 / 被停用",
    "5.7.1": "对方服务器拒绝接收（可能在黑名单 / 权限不足）",
    "5.7.60": "发件地址与登录账号不一致",
    "5.4.7": "投递超时",
}
# 退信正文里除了「失败的收件人」，往往还抄着原始邮件头（含我们自己的发件地址）。
# 这几句提示词后面跟着的，才是真正没投递成功的地址。
FOCUS_HINTS = ("couldn't be delivered", "could not be delivered", "unable to deliver",
               "delivery has failed", "failed to these", "these recipients",
               "recipients or groups", "failed recipient", "following address",
               "delivery to", "failure occurred during the delivery",
               "diagnostic information", "final-recipient",
               "无法发送到", "收件人", "以下地址", "未送达", "退信原因", "被退回邮件",
               "无法投递", "投递失败")


def _dedupe(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        low = item.lower()
        if low not in seen:
            seen.add(low)
            out.append(item)
    return out


def is_bounce(sender: str, subject: str) -> bool:
    """这封是不是退信？"""
    text = f"{sender or ''} {subject or ''}".lower()
    if "postmaster" in text or "mailer-daemon" in text:
        return True
    return any(hint in text for hint in SUBJECT_HINTS)


def parse(body: str, subject: str = "") -> Dict[str, Any]:
    """从退信正文里抠出失败地址、状态码和一句人话。"""
    text = f"{subject or ''}\n{body or ''}"
    lines = text.splitlines()
    # 优先只在提示词附近找地址（提示行 + 下一行），避免把原文邮件头里的
    # 发件地址（我们自己）当成失败地址。
    focused: List[str] = []
    for index, line in enumerate(lines):
        low = line.lower()
        if not any(hint in low for hint in FOCUS_HINTS):
            continue
        window = line
        if index + 1 < len(lines):
            window += "\n" + lines[index + 1]
        focused.extend(ADDRESS_RE.findall(window))
    if focused:
        addresses = _dedupe(focused)
    else:
        addresses = _dedupe(ADDRESS_RE.findall(text))
    codes: List[str] = []
    for major, minor in CODE_RE.findall(text):
        code = f"{major} {minor}"
        if code not in codes:
            codes.append(code)
    reason = ""
    for code in codes:
        if code in REASON_HINTS:
            reason = REASON_HINTS[code]
            break
    if not reason:
        low = text.lower()
        if "mailbox unavailable" in low or "does not exist" in low:
            reason = "对方邮箱不存在"
        elif "mailbox full" in low or "over quota" in low:
            reason = "对方邮箱已满"
        elif "blocked" in low or "blacklist" in low or "spam" in low:
            reason = "被对方当成垃圾邮件 / 拒收"
    return {"addresses": addresses, "codes": codes, "reason": reason}


def match_outbox(store, addresses: List[str], subject_hint: str = "") -> List[int]:
    """在发件箱里找「发给这些地址、而且还没退信」的邮件，返回 id 列表。"""
    wanted = {item.strip().lower() for item in addresses if item}
    if not wanted:
        return []
    hits: List[int] = []
    try:
        rows = store.list_mails()
    except Exception:
        return []
    for mail in rows:
        if str(mail.get("status")) not in ("sent", "failed"):
            continue
        to_addr = str(mail.get("to_addr") or "").strip().lower()
        if not to_addr:
            continue
        target = ADDRESS_RE.findall(to_addr)
        candidates = {item.lower() for item in target} or {to_addr}
        if not (candidates & wanted):
            continue
        hits.append(int(mail["id"]))
    return hits


def mark_bounced(store, mail_id: int, reason: str, code: str = "") -> bool:
    try:
        store.set_mail_status(mail_id, "bounced",
                              error=(f"{code} {reason}".strip() or "对方退信"))
        return True
    except Exception:
        return False


def sync(store, inbox_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """扫一遍收件箱里的退信，把发件箱对应的邮件标成「已退信」。

    返回被标记的摘要：[{"mail_id":…, "to":…, "reason":…, "code":…}]
    """
    marked: List[Dict[str, Any]] = []
    for row in inbox_rows or []:
        sender = str(row.get("from") or row.get("sender") or "")
        subject = str(row.get("subject") or "")
        if not is_bounce(sender, subject):
            continue
        body = str(row.get("snippet") or row.get("body") or "")
        info = parse(body, subject)
        for mail_id in match_outbox(store, info["addresses"]):
            code = info["codes"][0] if info["codes"] else ""
            if mark_bounced(store, mail_id, info["reason"], code):
                marked.append({"mail_id": mail_id, "reason": info["reason"],
                               "code": code})
    return marked
