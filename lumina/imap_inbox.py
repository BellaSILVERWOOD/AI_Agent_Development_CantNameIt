"""收件箱：用 IMAP 收取多个账户的邮件。

数据表：
    imap_accounts(id, name, host, port, security, user, created_at)
密码走 secure（DPAPI）加密，存在 settings 的 imap_password_<id>，界面上只可改不可看。

对外接口：
    list_accounts(store) / add_account / update_account / delete_account / set_password
    test_account(account, password)   -> Dict
    fetch_account(store, account, limit) -> List[Dict]     # 只取头部+摘要
    fetch_body(store, account, uid)   -> Dict              # 取正文
    fetch_all(store, limit_per_account) -> List[Dict]
"""
from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

SECURITY = [("ssl", "SSL/TLS（993）"), ("starttls", "STARTTLS（143）")]


# ------------------------------------------------------------------ 账户
def ensure_schema(store) -> None:
    try:
        store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS imap_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 993,
                security TEXT NOT NULL DEFAULT 'ssl',
                user TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inbox_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                account_name TEXT NOT NULL DEFAULT '',
                uid TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                sender TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL DEFAULT '',
                snippet TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL,
                UNIQUE(account_id, uid)
            );
            CREATE INDEX IF NOT EXISTS idx_inbox_date ON inbox_messages(date);
            """
        )
        store.conn.commit()
    except Exception:
        pass


def list_accounts(store) -> List[Dict[str, Any]]:
    ensure_schema(store)
    rows = store.conn.execute(
        "SELECT * FROM imap_accounts ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def add_account(store, name: str, host: str, port: int = 993,
                security: str = "ssl", user: str = "",
                password: str = "") -> int:
    ensure_schema(store)
    cur = store.conn.execute(
        "INSERT INTO imap_accounts(name, host, port, security, user, created_at)"
        " VALUES (?,?,?,?,?,?)",
        ((name or host).strip(), host.strip(), int(port or 993),
         security or "ssl", (user or "").strip(),
         datetime.now().isoformat(timespec="seconds")))
    store.conn.commit()
    account_id = int(cur.lastrowid)
    if password:
        store.set_secret(f"imap_password_{account_id}", password)
    return account_id


def update_account(store, account_id: int, **fields: Any) -> None:
    allowed = {"name", "host", "port", "security", "user"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    columns = ", ".join(f"{key} = ?" for key in values)
    store.conn.execute(f"UPDATE imap_accounts SET {columns} WHERE id = ?",
                       list(values.values()) + [int(account_id)])
    store.conn.commit()


def delete_account(store, account_id: int) -> None:
    ensure_schema(store)
    store.conn.execute("DELETE FROM imap_accounts WHERE id = ?", (int(account_id),))
    store.conn.commit()
    store.set_secret(f"imap_password_{account_id}", "")


def password(store, account_id: int) -> str:
    return store.get_secret(f"imap_password_{account_id}", "") or ""


def set_password(store, account_id: int, value: str) -> None:
    if value:
        store.set_secret(f"imap_password_{account_id}", value)


# ------------------------------------------------------------------ IMAP
def _connect(account: Dict[str, Any], secret: str,
             timeout: float = 15.0) -> imaplib.IMAP4:
    host = str(account.get("host") or "").strip()
    port = int(account.get("port") or 993)
    security = str(account.get("security") or "ssl")
    if not host:
        raise RuntimeError("服务器地址是空的")
    if security == "starttls":
        server = imaplib.IMAP4(host, port, timeout=timeout)
        server.starttls()
    else:
        server = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    server.login(str(account.get("user") or ""), secret)
    return server


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _snippet(msg) -> str:
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    return _clean(payload.decode(part.get_content_charset() or "utf-8",
                                                 "replace"))
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True) or b""
                    html = payload.decode(part.get_content_charset() or "utf-8",
                                          "replace")
                    return _clean(re.sub(r"<[^>]+>", " ", html))
            return ""
        payload = msg.get_payload(decode=True) or b""
        text = payload.decode(msg.get_content_charset() or "utf-8", "replace")
        if msg.get_content_type() == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
        return _clean(text)
    except Exception:
        return ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:280]


def _when(msg) -> str:
    try:
        value = parsedate_to_datetime(msg.get("Date"))
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def test_account(account: Dict[str, Any], secret: str) -> Dict[str, Any]:
    try:
        server = _connect(account, secret)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    try:
        status, data = server.select("INBOX", readonly=True)
        total = 0
        try:
            total = int((data or [b"0"])[0] or 0)
        except Exception:
            total = 0
        return {"ok": True, "total": total}
    finally:
        try:
            server.logout()
        except Exception:
            pass


def fetch_account(store, account: Dict[str, Any], limit: int = 10,
                  timeout: float = 20.0) -> List[Dict[str, Any]]:
    """取这个账户最近 limit 封邮件的摘要。"""
    secret = password(store, int(account["id"]))
    if not secret:
        raise RuntimeError("这个账户还没有保存密码")
    server = _connect(account, secret, timeout=timeout)
    try:
        server.select("INBOX", readonly=True)
        status, data = server.search(None, "ALL")
        ids = (data[0] or b"").split() if data else []
        picked = ids[-limit:][::-1]
        messages: List[Dict[str, Any]] = []
        for uid in picked:
            status, payload = server.fetch(uid, "(RFC822)")
            raw = None
            for part in payload or []:
                if isinstance(part, tuple) and len(part) > 1:
                    raw = part[1]
                    break
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            messages.append({
                "account_id": int(account["id"]),
                "account_name": account.get("name") or account.get("user") or "",
                "uid": uid.decode("ascii", "ignore"),
                "subject": _decode(msg.get("Subject")) or "（无主题）",
                "from": _decode(msg.get("From")),
                "date": _when(msg),
                "snippet": _snippet(msg),
            })
        return messages
    finally:
        try:
            server.logout()
        except Exception:
            pass


def fetch_body(store, account: Dict[str, Any], uid: str) -> Dict[str, str]:
    """按需取正文（纯文本优先，其次 HTML 去标签）。

    注意：这是**联网**操作，调用方要放在后台线程里，别在 GUI 线程直接调，
    否则网慢的时候整个界面会像卡死。
    """
    secret = password(store, int(account["id"]))
    if not secret:
        raise RuntimeError("这个账户还没有保存密码")
    server = _connect(account, secret)
    try:
        server.select("INBOX", readonly=True)
        status, payload = server.fetch(uid.encode(), "(RFC822)")
        raw = None
        for part in payload or []:
            if isinstance(part, tuple) and len(part) > 1:
                raw = part[1]
                break
        if not raw:
            return {"body": "", "error": "没取到正文"}
        msg = email.message_from_bytes(raw)
        body = _snippet(msg)
        # _snippet 只截 280 字，这里再取一次完整正文
        try:
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        data = part.get_payload(decode=True) or b""
                        body = data.decode(part.get_content_charset() or "utf-8",
                                           "replace")
                        break
            else:
                data = msg.get_payload(decode=True) or b""
                body = data.decode(msg.get_content_charset() or "utf-8", "replace")
                if msg.get_content_type() == "text/html":
                    body = re.sub(r"<[^>]+>", " ", body)
        except Exception:
            pass
        text = (body or "").strip()
        save_body(store, int(account["id"]), str(uid), text)   # 正文也存本地
        return {"body": text,
                "subject": _decode(msg.get("Subject")),
                "from": _decode(msg.get("From")),
                "date": _when(msg)}
    finally:
        try:
            server.logout()
        except Exception:
            pass


def fetch_all(store, limit_per_account: int = 8,
              save: bool = False) -> List[Dict[str, Any]]:
    """所有账户的最新邮件，按时间倒序合并。

    `save=False` 时只返回数据、不写库：写库要放在 GUI 线程做，
    不然和界面抢 SQLite 连接会卡住（`database is locked` 会等很久）。
    """
    merged: List[Dict[str, Any]] = []
    errors: List[str] = []
    for account in list_accounts(store):
        try:
            merged.extend(fetch_account(store, account, limit_per_account))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{account.get('name') or account.get('host')}：{exc}")
    merged.sort(key=lambda item: item.get("date") or "", reverse=True)
    if save and merged:
        save_messages(store, merged)
    if errors:
        merged.insert(0, {"error": "；".join(errors)})
    return merged


# ------------------------------------------------------------------ 本地缓存
def save_messages(store, messages: List[Dict[str, Any]]) -> int:
    """把收到的邮件头写进本地库（同账户同 uid 覆盖，正文保留）。"""
    ensure_schema(store)
    stamp = datetime.now().isoformat(timespec="seconds")
    saved = 0
    for item in messages or []:
        if item.get("error") or not item.get("uid"):
            continue
        try:
            store.conn.execute(
                "INSERT INTO inbox_messages(account_id, account_name, uid, subject,"
                " sender, date, snippet, fetched_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(account_id, uid) DO UPDATE SET"
                " subject=excluded.subject, sender=excluded.sender,"
                " date=excluded.date, snippet=excluded.snippet,"
                " account_name=excluded.account_name, fetched_at=excluded.fetched_at",
                (int(item.get("account_id") or 0),
                 str(item.get("account_name") or ""),
                 str(item.get("uid") or ""),
                 str(item.get("subject") or ""),
                 str(item.get("from") or ""),
                 str(item.get("date") or ""),
                 str(item.get("snippet") or ""),
                 stamp),
            )
            saved += 1
        except Exception:
            continue
    try:
        store.conn.commit()
    except Exception:
        pass
    return saved


def cached_messages(store, limit: int = 40) -> List[Dict[str, Any]]:
    """本地保存的邮件（按时间倒序），没有账户也没关系。"""
    ensure_schema(store)
    rows = store.conn.execute(
        "SELECT * FROM inbox_messages ORDER BY date DESC, id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["from"] = item.pop("sender", "")
        item["has_body"] = bool(str(item.get("body") or "").strip())
        items.append(item)
    return items


def cached_body(store, account_id: int, uid: str) -> str:
    ensure_schema(store)
    row = store.conn.execute(
        "SELECT body FROM inbox_messages WHERE account_id=? AND uid=?",
        (int(account_id), str(uid)),
    ).fetchone()
    return str(row["body"] or "") if row else ""


def save_body(store, account_id: int, uid: str, body: str) -> None:
    ensure_schema(store)
    try:
        store.conn.execute(
            "UPDATE inbox_messages SET body=? WHERE account_id=? AND uid=?",
            (str(body or ""), int(account_id), str(uid)))
        store.conn.commit()
    except Exception:
        pass
