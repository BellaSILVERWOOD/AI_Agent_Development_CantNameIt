"""E1：让 agent 代发邮件 —— 但**每次都必须由用户确认并修改**。

用户要求（E1）：「A，但是每次发件必须经由用户确认和修改」。
所以这个工具不会直接发信：
1. 先把收件人/主题/正文填好，弹一个可编辑的窗口给用户；
2. 用户改完点「发送」才真的发；点取消就什么都不做；
3. 发信本身走 `sending.send_mail`（和界面上「新邮件」同一套 SMTP 配置）。

界面通过 `set_compose_handler()` 注册「弹窗编辑」的回调；没有窗口时一律拒绝。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

_COMPOSE_HANDLER: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None


def set_compose_handler(handler) -> None:
    """主窗口注册：`handler(payload) -> dict|None`（None = 用户取消）。"""
    global _COMPOSE_HANDLER
    _COMPOSE_HANDLER = handler


def compose(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    handler = _COMPOSE_HANDLER
    if handler is None:
        return None
    try:
        edited = handler(dict(payload or {}))
    except Exception:
        return None
    if not isinstance(edited, dict):
        return None
    return edited


def mail_send(store, to: str = "", subject: str = "", text: str = "",
              confirm: bool = True) -> Dict[str, Any]:
    """先让用户确认+修改，再发信。"""
    from . import mailer, sending  # noqa: F401  （确保发送模块已加载）

    recipient = str(to or "").strip()
    title = str(subject or "").strip()
    body = str(text or "")
    if not recipient:
        return {"ok": False, "summary": "要发给谁？收件人地址是空的"}
    if not confirm:
        return {"ok": False, "summary": "发信必须由用户确认，不能跳过"}

    payload = {"to": recipient, "subject": title, "text": body}
    edited = compose(payload)
    if edited is None:
        return {"ok": False, "cancelled": True,
                "summary": "用户取消了这次发送（没有发出去）"}
    recipient = str(edited.get("to") or "").strip()
    title = str(edited.get("subject") or "").strip()
    body = str(edited.get("text") or "")
    if not recipient:
        return {"ok": False, "cancelled": True,
                "summary": "用户把收件人清空了，已取消"}
    if "@" not in recipient:
        return {"ok": False, "summary": f"收件人地址看起来不对：{recipient}"}

    try:
        result = _send_now(store, recipient, title, body)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"发送失败：{exc}"}
    if not result.get("ok"):
        return result
    return {"ok": True, "to": recipient, "subject": title,
            "mail_id": result.get("mail_id") or "",
            "summary": f"已发送给 {recipient}（主题：{title or '（无主题）'}）"}


def _send_now(store, to_addr: str, subject: str, text: str) -> Dict[str, Any]:
    """真的走 SMTP 发出去，并在发件箱留一条记录。"""
    from datetime import datetime

    from .mailer import SmtpConfig, build_message, send_message

    try:
        cfg = SmtpConfig.from_store(store)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"读不到 SMTP 配置：{exc}"}
    if not cfg.is_configured():
        return {"ok": False,
                "summary": "还没配置发件邮箱（设置 → 发件邮箱），发不了信"}
    subject = subject or "（无主题）"
    html = ("<p>" + str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "<br>") + "</p>")
    try:
        message = build_message(to_addr, subject, text or "", html, [], cfg)
        send_message(message, cfg)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"发送失败：{exc}"}
    mail_id = 0
    try:
        mail_id = int(store.add_mail(
            to_addr, subject, html, text or "", status="sent",
            sent_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    except Exception:
        mail_id = 0
    return {"ok": True, "mail_id": mail_id}
