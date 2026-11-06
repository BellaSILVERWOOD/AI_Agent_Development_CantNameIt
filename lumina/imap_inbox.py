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
import json
import mimetypes
import re
from datetime import datetime, timedelta
from pathlib import Path
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

from . import bounce

SECURITY = [("ssl", "SSL/TLS（993）"), ("starttls", "STARTTLS（143）")]
BOUNCE_TEXT_LIMIT = 4000     # 退信正文留多少字（要装得下状态码和原始邮件头）


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
                links TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL,
                UNIQUE(account_id, uid)
            );
            CREATE INDEX IF NOT EXISTS idx_inbox_date ON inbox_messages(date);
            """
        )
        # 老库补列：邮件里的链接（正文转成纯文本后，靠这份对照表让预览里的
        # 「点击这里」仍然可点）
        try:
            columns = {row[1] for row in
                       store.conn.execute("PRAGMA table_info(inbox_messages)")}
            if "links" not in columns:
                store.conn.execute(
                    "ALTER TABLE inbox_messages ADD COLUMN links TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        store.conn.commit()
    except Exception:
        pass
    _migrate_uid_cache(store)


#: 缓存键语义的版本：1（早期）存的其实是 IMAP **序号**，2 起才是真正的 UID
UID_CACHE_KEY = "imap_uid_cache"

UID_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _migrate_uid_cache(store) -> None:
    """把「按序号存」的老邮件缓存整个丢掉（只做一次）。

    老版本把 IMAP **序号**当成 uid 写进 `inbox_messages`（列表与正文都用它），
    而序号会随新邮件整体后移 —— 于是「点开 A，正文却是 B」，甚至新邮件的真 UID
    刚好等于某条老记录的序号时还会串。换到真正的 UID 之后，老记录既不能对应
    服务器上的邮件、也可能和新 UID 撞号，所以直接清空：邮件头本来每次刷新都会
    重新取，离线时少看几封也比看错强。
    """
    try:
        if (store.get_setting(UID_CACHE_KEY, "") or "") == "2":
            return
        store.conn.execute("DELETE FROM inbox_messages")
        store.conn.commit()
        store.set_setting(UID_CACHE_KEY, "2")
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


_ALIASES = {
    "gb2312": "gb18030", "gbk": "gb18030", "cp936": "gb18030", "x-gbk": "gb18030",
    "utf8": "utf-8", "utf-8-sig": "utf-8", "unicode-1-1-utf-8": "utf-8",
    "us-ascii": "ascii", "ascii": "ascii",
    "ks_c_5601-1987": "cp949", "euc-kr": "cp949",
    "big5": "big5", "big-5": "big5", "cp950": "big5",
    "iso-8859-1": "latin-1", "windows-1252": "cp1252",
}


def decode_bytes(raw: bytes, declared: Optional[str] = None) -> str:
    """把邮件正文的字节解成文字。

    中文邮件常见坑：声明是 `gb2312`、或者干脆没声明（`us-ascii`）却装的是 GBK 字节，
    以前直接按声明解，结果一堆 `���`。这里把候选编码都试一遍，
    按「替换字符最少、汉字/正常字符最多」挑一个最像的。
    """
    if not raw:
        return ""
    candidates = []
    if declared:
        key = str(declared).strip().lower().strip('"\'')
        candidates.append(_ALIASES.get(key, key))
    for name in ("utf-8", "gb18030", "big5", "cp949", "utf-16", "latin-1"):
        if name not in candidates:
            candidates.append(name)

    def score(text: str) -> tuple:
        bad = text.count("\ufffd")
        # 满是替换符 / 一堆控制字符的，判为更差
        weird = sum(1 for ch in text
                    if ch in "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f")
        return (bad, weird)

    best_text = ""
    best_score = None
    for name in candidates:
        try:
            text = raw.decode(name, "replace")
        except (LookupError, UnicodeDecodeError):
            continue
        current = score(text)
        if best_score is None or current < best_score:
            best_text, best_score = text, current
        if current == (0, 0):
            break
    text = best_text
    # 「UTF-8 被当成 latin-1」这种双重错码，再扳回来一次
    if "\u00c3" in text or "\u00e4\u00b8" in text:
        try:
            fixed = text.encode("latin-1", "ignore").decode("utf-8", "replace")
            if score(fixed) < score(text):
                text = fixed
        except Exception:
            pass
    return text


def part_text(part) -> str:
    """取一个 MIME 部分的文本（自动处理编码/声明不符）。"""
    payload = part.get_payload(decode=True) or b""
    return decode_bytes(payload, part.get_content_charset())


def _snippet(msg) -> str:
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return _clean(part_text(part))
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    return _clean(html_to_text(part_text(part)))
            return ""
        text = part_text(msg)
        if msg.get_content_type() == "text/html":
            text = html_to_text(text)
        return _clean(text)
    except Exception:
        return ""


CSS_HINT_RE = re.compile(
    r"@media|@font-face|!important|font-family\s*:|font-size\s*:|margin-[a-z]+\s*:|"
    r"padding-[a-z]+\s*:|border-radius\s*:|display\s*:", re.I)
CSS_RULE_RE = re.compile(r"\{[^{}]{0,800}:[^{}]*\}")


def looks_like_raw_css(text: str) -> bool:
    """这段文字是不是「把 CSS 当正文」存下来的脏数据？

    老版本存正文时只删了 `<style>` **标签**、留下了里面的样式源码，于是邮件
    预览里出现 `.eml-w { font-size: 16px !important; }`、`@media (max-width: 760px)`
    这类东西。这种正文要丢掉重新联网取（或者重新清洗）。
    """
    sample = str(text or "")[:4000]
    if not sample:
        return False
    if "<style" in sample.lower() or "</style" in sample.lower():
        return True
    hints = len(CSS_HINT_RE.findall(sample))
    return hints >= 2 and bool(CSS_RULE_RE.search(sample))


def purge_dirty_bodies(store) -> int:
    """把本地缓存里「存成 CSS」的正文清掉（返回清了几封）。

    清掉之后正文长度算 0，界面会重新联网取一次、顺便按新规则清洗，
    用户就不用自己去数据库里改了。
    """
    ensure_schema(store)
    cleaned = 0
    try:
        rows = store.conn.execute(
            "SELECT id, body FROM inbox_messages WHERE COALESCE(body, '') <> ''"
        ).fetchall()
    except Exception:
        return 0
    for row in rows:
        try:
            row_id, body = int(row[0]), str(row[1] or "")
        except Exception:
            continue
        if not looks_like_raw_css(body):
            continue
        try:
            store.conn.execute("UPDATE inbox_messages SET body = '' WHERE id = ?",
                               (row_id,))
            cleaned += 1
        except Exception:
            continue
    if cleaned:
        try:
            store.conn.commit()
        except Exception:
            pass
    return cleaned


def html_to_text(html: str) -> str:
    """HTML → 可读文本。

    要点：先整块删掉 `<style>` / `<script>`（它们的**内容**是 CSS/JS，不是正文；
    以前只删标签，结果邮件里把 `@media { … }` 当成正文显示出来），
    再把块级标签换成换行、去掉剩下的标签、还原 HTML 实体。
    """
    import html as _html

    text = str(html or "")
    # ① 整块删掉样式 / 脚本 / 注释
    text = re.sub(r"<(style|script)\b[^>]*>.*?</\1\s*>", " ", text,
                  flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    # ② 块级标签变换行，<br> 也是换行
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|h[1-6]|blockquote|table|section|article)\s*>",
                  "\n", text, flags=re.I)
    # ③ 剩下的标签去掉
    text = re.sub(r"<[^>]+>", " ", text)
    # ④ 实体还原
    try:
        text = _html.unescape(text)
    except Exception:
        text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">"))
    # ⑤ 收拾空白：行内空格合并、最多留一个空行
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_to_text(text) if "<" in (text or "") else
                  (text or "")).strip()[:280]


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


def _fetch_many(server, uids: List[bytes], clause: str) -> Dict[int, bytes]:
    """一次 UID FETCH 拿多条（`{UID: 原始字节}`），省掉「每封一次往返」的慢。

    **必须按 UID 取，不能按序号取**：以前这里用的是 `server.fetch()`（序号），
    而列表里存的 `uid` 其实也是序号 —— 新邮件一到，序号整体后移，
    本地缓存的正文就会挂到**别的邮件**上（用户报的「邮件内容显示得不对」就是
    点开一封大会邀请，正文却是另一封注册申请的密文）。

    UID FETCH 的返回里每段都带 `UID 1234`，用它归位；顺便从 `* 5 FETCH` 的
    序号里拿不到稳定身份，所以只认 UID。
    """
    out: Dict[int, bytes] = {}
    if not uids:
        return out
    try:
        status, payload = server.uid("FETCH", b",".join(uids), clause)
    except Exception:
        return out
    current: Optional[int] = None
    for part in payload or []:
        if isinstance(part, tuple) and len(part) >= 2:
            head = part[0] or b""
            found = re.search(rb"UID\s+(\d+)", head)
            if found:
                current = int(found.group(1))
            if current is not None:
                out[current] = out.get(current, b"") + (part[1] or b"")
        elif isinstance(part, bytes) and current is not None:
            out[current] = out.get(current, b"") + part
    return out


def fetch_account(store, account: Dict[str, Any], limit: int = 10,
                  timeout: float = 20.0, headers_only: bool = True
                  ) -> List[Dict[str, Any]]:
    """取这个账户最近 limit 封邮件的摘要。

    `headers_only=True`（默认）只取邮件头 + 一小段正文，**不下载整封邮件**：
    以前每封都 `(RFC822)` 拉全量，8 封就要 8 秒，界面看起来像「刷新不出来」。
    列表只需要发件人/主题/时间，正文等用户点开时再单独取（那边已有后台线程）。
    退信（PostMaster）例外：它的关键信息在正文里，才走整封。
    """
    secret = password(store, int(account["id"]))
    if not secret:
        raise RuntimeError("这个账户还没有保存密码")
    server = _connect(account, secret, timeout=timeout)
    try:
        server.select("INBOX", readonly=True)
        status, data = server.uid("SEARCH", None, "ALL")
        ids = (data[0] or b"").split() if data else []
        picked = ids[-limit:][::-1]
        messages: List[Dict[str, Any]] = []
        if headers_only:
            blobs = _fetch_many(
                server, picked,
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] BODY.PEEK[TEXT]<0.600>)")
        else:
            blobs = {}
        for uid in picked:
            try:
                number = int(uid)
            except (TypeError, ValueError):
                number = -1
            raw = blobs.get(number, b"")
            partial = b""
            if not raw:
                # 批量取失败（服务器不认）就退回一封一封取（仍然按 UID）
                try:
                    status, payload = server.uid("FETCH", uid, "(RFC822)")
                except Exception:
                    continue
                for part in payload or []:
                    if isinstance(part, tuple) and len(part) > 1:
                        raw = part[1] or b""
                        break
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            sender = _decode(msg.get("From"))
            subject = _decode(msg.get("Subject")) or "（无主题）"
            item = {
                "account_id": int(account["id"]),
                "account_name": account.get("name") or account.get("user") or "",
                "uid": uid.decode("ascii", "ignore"),
                "subject": subject,
                "from": sender,
                "date": _when(msg),
                "snippet": "",
            }
            # 退信（PostMaster / Undeliverable）：失败地址和「550 5.5.0 …」
            # 往往在正文末尾，280 字的摘要根本装不下；这类邮件留全量正文，
            # 好让 bounce.sync 能把发件箱里那封标成「已退信」。
            if bounce.is_bounce(sender, subject):
                detail = ""
                try:
                    full = server.uid("FETCH", uid, "(RFC822)")
                    raw_full = None
                    for part in full or []:
                        if isinstance(part, tuple) and len(part) > 1:
                            raw_full = part[1]
                            break
                    if raw_full:
                        whole = email.message_from_bytes(raw_full)
                        detail = bounce_text(whole) or message_text(whole)
                except Exception:
                    detail = ""
                if not detail and partial:
                    detail = partial.decode("utf-8", "replace")
                if detail:
                    item["snippet"] = detail[:BOUNCE_TEXT_LIMIT]
                    item["full_text"] = detail[:BOUNCE_TEXT_LIMIT]
                    item["is_bounce"] = True
            elif partial:
                item["snippet"] = _clean(partial.decode("utf-8", "replace"))[:280]
            messages.append(item)
        return messages
    finally:
        try:
            server.logout()
        except Exception:
            pass


def bounce_text(msg) -> str:
    """退信邮件专用：把**所有**文字部分合起来。

    退信是 `multipart/report`：QQ/复旦那封把「退信原因 + 550」放在 **HTML** 里，
    纯文本部分只是被退回去的原信；Outlook 那封诊断信息又在纯文本的中间。
    所以这里不做「纯文本优先」，全部收进来（`parse` 只抠地址和状态码，多收无害）。
    """
    chunks: List[str] = []
    try:
        parts = list(msg.walk())
    except Exception:
        parts = [msg]
    for part in parts:
        ctype = part.get_content_type()
        try:
            if ctype == "text/plain":
                chunks.append(part_text(part))
            elif ctype == "text/html":
                chunks.append(html_to_text(part_text(part)))
            elif ctype in ("message/delivery-status", "text/rfc822-headers"):
                raw = part.get_payload(decode=True)
                if raw:
                    chunks.append(raw.decode("utf-8", "replace"))
        except Exception:
            continue
    unique: List[str] = []
    for chunk in chunks:
        text = (chunk or "").strip()
        if text and text not in unique:
            unique.append(text)
    return "\n\n".join(unique)


def message_text(msg) -> str:
    """整封邮件的正文纯文本：纯文本优先，其次 HTML 去标签。

    只有 HTML 的通知邮件很多，转换时要把 `<style>` / `<script>` 整块删掉，
    别把 CSS 当正文显示（`html_to_text` 内部已经这么做了）。
    """
    body = ""
    try:
        if msg.is_multipart():
            found_plain = False
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part_text(part)
                    found_plain = True
                    break
            if not found_plain:
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        body = html_to_text(part_text(part))
                        break
        else:
            body = part_text(msg)
            if msg.get_content_type() == "text/html":
                body = html_to_text(body)
    except Exception:
        body = ""
    return (body or "").strip()


def message_links(msg) -> List[Dict[str, str]]:
    """把整封邮件里所有 HTML 部分的链接抠出来。"""
    links: List[Dict[str, str]] = []
    try:
        parts = list(msg.walk())
    except Exception:
        parts = [msg]
    for part in parts:
        try:
            if part.get_content_type() != "text/html":
                continue
        except Exception:
            continue
        try:
            links.extend(html_links(part_text(part)))
        except Exception:
            continue
    deduped: List[Dict[str, str]] = []
    seen = set()
    for item in links:
        key = (str(item.get("text") or "").lower(), str(item.get("href") or "").lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:40]


def _list_mailboxes(server) -> List[str]:
    """列出服务器上的文件夹名（拿不到就只返回 INBOX）。"""
    names: List[str] = ["INBOX"]
    try:
        status, data = server.list()
    except Exception:
        return names
    for raw in data or []:
        try:
            text = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
        except Exception:
            continue
        # 形如：(\HasNoChildren) "/" "Junk"
        parts = text.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        name = parts[1].strip().strip('"')
        if name and name not in names:
            names.append(name)
    return names


#: 垃圾邮件文件夹的常见名字（各邮箱叫法不同，只能按关键字猜）
_SPAM_HINTS = ("junk", "spam", "bulk", "垃圾", "广告", "advert")


def spam_mailboxes(server) -> List[str]:
    """找出「垃圾邮件 / 广告」类文件夹：不扫这些，被误判的申请就永远看不见。"""
    out: List[str] = []
    for name in _list_mailboxes(server):
        low = name.lower()
        if name.upper() == "INBOX":
            continue
        if any(hint in low for hint in _SPAM_HINTS):
            out.append(name)
    return out


_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def imap_since(days: int = 30) -> str:
    """IMAP 的 SINCE 日期（`01-Nov-2026`），跟本机语言环境无关。"""
    day = datetime.now() - timedelta(days=max(1, int(days)))
    return "%02d-%s-%04d" % (day.day, _MONTH_NAMES[day.month - 1], day.year)


def fetch_subject_messages(store, account: Dict[str, Any],
                           prefix: str = "[LUMINA-",
                           limit: int = 200, timeout: float = 20.0,
                           since: Optional[str] = None,
                           include_spam: bool = True,
                           prefixes: Optional[List[str]] = None
                           ) -> List[Dict[str, Any]]:
    """只抓「主题带某个前缀」的邮件（一次连接搞定头部 + 正文）。

    为什么单独写一个：管理员点「检查新申请」时，以前是把最近 20 封**整封**拉下来、
    再一封封重新登录去取正文 —— 又慢又卡（界面直接冻住）。这里改成：
    ① 让服务器按 SUBJECT 过滤（`SEARCH SUBJECT`），通常只有 0~2 封；
    ② 同一条连接里顺手把正文也取出来；
    ③ 取不到就跳过，不让整个操作失败。

    后来补的三处（都是「申请明明发了却刷不出来」的真实原因）：
    * `since`：只问「某天之后」的邮件，不再被收件箱里的历史邮件挤出窗口；
    * `limit` 默认 200（原来是 20，周一发的件很容易被后来的邮件顶掉）；
    * `include_spam`：顺带扫垃圾邮件 / 广告文件夹 —— 自建发件被判定垃圾太常见了。
    * `prefixes`：能一次找多个主题前缀。**这条最关键**：程序改名成 DanLume 之后，
      新客户端发的是 `[DANLUME-REG]`，只认 `[LUMINA-` 会把新申请整封漏掉。
    """
    secret = password(store, int(account["id"]))
    if not secret:
        raise RuntimeError("这个账户还没有保存密码")
    needles = [str(one).replace('"', "") for one in (prefixes or [prefix]) if one]
    server = _connect(account, secret, timeout=timeout)
    messages: List[Dict[str, Any]] = []
    seen_uids = set()
    try:
        boxes = ["INBOX"]
        if include_spam:
            for name in spam_mailboxes(server):
                if name.upper() != "INBOX" and name not in boxes:
                    boxes.append(name)
        for box in boxes:
            try:
                server.select(box, readonly=True)
            except Exception:
                continue
            picked: List[bytes] = []
            for needle in needles:
                try:
                    if since:
                        status, data = server.uid(
                            "SEARCH", None, "SUBJECT", f'"{needle}"', "SINCE", since)
                    else:
                        status, data = server.uid("SEARCH", None, "SUBJECT", f'"{needle}"')
                except Exception:
                    # 服务器不认组合条件（或不认 SUBJECT 搜索）：退回「某天之后全部」
                    try:
                        if since:
                            status, data = server.uid("SEARCH", None, "SINCE", since)
                        else:
                            status, data = server.uid("SEARCH", None, "ALL")
                    except Exception:
                        data = None
                ids = (data[0] or b"").split() if data else []
                for uid in ids:
                    if uid not in seen_uids:
                        seen_uids.add(uid)
                        picked.append(uid)
            picked = picked[-max(1, int(limit)):][::-1]
            for uid in picked:
                try:
                    status, payload = server.uid("FETCH", uid, "(RFC822)")
                except Exception:
                    continue
                raw = None
                for part in payload or []:
                    if isinstance(part, tuple) and len(part) > 1:
                        raw = part[1]
                        break
                if not raw:
                    continue
                try:
                    msg = email.message_from_bytes(raw)
                except Exception:
                    continue
                subject = _decode(msg.get("Subject")) or ""
                if needles and not any(needle in subject for needle in needles):
                    continue         # 服务器不认 SUBJECT 搜索时，本地再过滤一次
                messages.append({
                    "account_id": int(account["id"]),
                    "account_name": account.get("name") or account.get("user") or "",
                    "uid": uid.decode("ascii", "ignore"),
                    "folder": box,
                    "subject": subject,
                    "from": _decode(msg.get("From")),
                    "date": _when(msg),
                    "snippet": _snippet(msg),
                    "body": message_text(msg),
                })
    finally:
        try:
            server.logout()
        except Exception:
            pass
    return messages


def fetch_body(store, account: Dict[str, Any], uid: str) -> Dict[str, Any]:
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
        # 按 UID 取（和列表里存的 uid 是同一套身份）；按序号取会在新邮件到达后错位
        status, payload = server.uid("FETCH", uid.encode(), "(RFC822)")
        raw = None
        for part in payload or []:
            if isinstance(part, tuple) and len(part) > 1:
                raw = part[1]
                break
        if not raw:
            return {"body": "", "error": "没取到正文"}
        msg = email.message_from_bytes(raw)
        # _snippet 只截 280 字，这里取完整正文
        text = message_text(msg) or _snippet(msg)
        save_body(store, int(account["id"]), str(uid), text)   # 正文也存本地
        rich: Dict[str, Any] = {"html": "", "attachments": [], "images": 0,
                                "dir": ""}
        try:
            rich = extract_media(msg, int(account["id"]), str(uid))
            if rich.get("html"):
                save_rich_cache(int(account["id"]), str(uid), rich["html"])
        except Exception:
            rich = {"html": "", "attachments": [], "images": 0, "dir": ""}
        return {"body": text,
                "links": message_links(msg),
                "html": str(rich.get("html") or ""),
                "media_dir": str(rich.get("dir") or ""),
                "attachments": list(rich.get("attachments") or []),
                "images": int(rich.get("images") or 0),
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
        # 退信邮件：正文一起存下来，点开就能看到「550 5.5.0 …」，
        # 下次也不再联网取一遍
        if item.get("full_text"):
            try:
                save_body(store, int(item.get("account_id") or 0),
                          str(item.get("uid") or ""), str(item["full_text"]))
            except Exception:
                pass
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


# ------------------------------------------------------------------ 链接
# 正文在入库时被转成了纯文本（为了不漏 CSS、也不让富文本把界面搞乱），
# 于是 `<a href="…">点击这里</a>` 只剩「点击这里」，点不动了。
# 这里单独把「链接文字 → 地址」的对照表存一份，预览时再把它们变回可点的链接。
ANCHOR_RE = re.compile(r"<a\b[^>]*?(?:href|HREF)\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a\s*>",
                       re.S)
BARE_URL_RE = re.compile(r"(?:https?|ftp)://[^\s<>\"'）)】\]，。；、]+")
TAG_RE = re.compile(r"<[^>]+>")


def html_links(html: str) -> List[Dict[str, str]]:
    """从 HTML 里抠出 [(链接文字, 地址)]，去重、去掉空壳。"""
    found: List[Dict[str, str]] = []
    seen = set()
    for href, label in ANCHOR_RE.findall(str(html or "")):
        href = (href or "").strip()
        text = re.sub(r"\s+", " ", TAG_RE.sub("", label or "")).strip()
        text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").strip())
        if not href or href.lower().startswith("javascript:"):
            continue
        key = (text.lower(), href.lower())
        if key in seen:
            continue
        seen.add(key)
        found.append({"text": text, "href": href})
    return found[:40]


def save_links(store, account_id: int, uid: str, links: List[Dict[str, str]]) -> None:
    ensure_schema(store)
    try:
        store.conn.execute(
            "UPDATE inbox_messages SET links=? WHERE account_id=? AND uid=?",
            (json.dumps(list(links or []), ensure_ascii=False),
             int(account_id), str(uid)))
        store.conn.commit()
    except Exception:
        pass


def cached_links(store, account_id: int, uid: str) -> List[Dict[str, str]]:
    ensure_schema(store)
    try:
        row = store.conn.execute(
            "SELECT links FROM inbox_messages WHERE account_id=? AND uid=?",
            (int(account_id), str(uid))).fetchone()
    except Exception:
        return []
    if not row:
        return []
    try:
        data = json.loads(str(row[0] or "[]"))
    except Exception:
        return []
    return [item for item in data if isinstance(item, dict) and item.get("href")]


def purge_unlinked_bodies(store) -> int:
    """清掉「有正文、但没存过链接表」的老缓存（打开时会重新取一次）。

    这样老邮件里的「点击这里」也能变成可点的链接，而不是一堆死文字。
    """
    ensure_schema(store)
    cleaned = 0
    try:
        rows = store.conn.execute(
            "SELECT id FROM inbox_messages WHERE COALESCE(body, '') <> ''"
            " AND COALESCE(links, '') = ''").fetchall()
    except Exception:
        return 0
    for row in rows:
        try:
            store.conn.execute("UPDATE inbox_messages SET body = '' WHERE id = ?",
                               (int(row[0]),))
            cleaned += 1
        except Exception:
            continue
    if cleaned:
        try:
            store.conn.commit()
        except Exception:
            pass
    return cleaned


def body_html(text: str, links: Optional[List[Dict[str, str]]] = None) -> str:
    """把纯文本正文变成「能点链接」的 HTML（已转义，可直接塞进 QTextBrowser）。

    顺序很关键：先把「链接文字」换成占位符 → 转义 → 再把裸露的网址变链接 →
    最后把占位符换成 `<a>`（这样不会把 href 里的网址又套一层）。
    """
    import html as _html

    source = str(text or "")
    placeholders: Dict[str, Dict[str, str]] = {}
    for index, item in enumerate(links or []):
        label = str(item.get("text") or "").strip()
        href = str(item.get("href") or "").strip()
        if not href or not label or len(label) < 2 or label == href:
            continue
        if label not in source:
            continue
        token = "\x00L%d\x00" % index
        source = source.replace(label, token, 1)
        placeholders[token] = {"text": label, "href": href}

    escaped = _html.escape(source, quote=False)
    # 裸露的网址（去掉句末标点那种常见的粘连）
    escaped = BARE_URL_RE.sub(
        lambda m: '<a href="%s">%s</a>' % (m.group(0), m.group(0)), escaped)
    for token, item in placeholders.items():
        anchor = '<a href="%s">%s</a>' % (
            _html.escape(item["href"], quote=True), _html.escape(item["text"]))
        escaped = escaped.replace(token, anchor)
    return escaped.replace("\n", "<br>")


# ------------------------------------------------------------------ 富邮件：HTML / 图片 / 附件
#: 邮件的 HTML 正文、内联图片、附件都落在这个目录（按账户 / UID 分文件夹）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MEDIA_ROOT = "mail_media"
#: 单封邮件最多缓存这么多媒体字节，超出的只列名字不保存
MEDIA_LIMIT = 40 * 1024 * 1024


def _safe_segment(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(text or "0"))[:80] or "0"


def mail_media_dir(account_id: int, uid: str) -> Path:
    from .config import DATA_DIR

    return Path(DATA_DIR) / MEDIA_ROOT / _safe_segment(account_id) \
        / _safe_segment(uid)


def message_html(msg) -> str:
    """整封邮件里最像样的那个 HTML 正文（没有就返回空串）。"""
    best = ""
    try:
        parts = list(msg.walk())
    except Exception:
        parts = [msg]
    for part in parts:
        try:
            if part.get_content_type() != "text/html":
                continue
            text = part_text(part).strip()
        except Exception:
            continue
        if len(text) > len(best):
            best = text
    return best


def _safe_filename(name: str) -> str:
    """文件名里不能有的字符换成下划线，太长的截断。"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "")).strip(" .")
    return text[:80] or "file"


#: 图片文件头（有的邮件把 PNG 写成 application/octet-stream，得靠内容认）
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


def _sniff_image(data: bytes) -> str:
    """看文件头猜图片后缀（猜不出返回空串）。"""
    head = bytes(data or b"")[:16]
    if not head:
        return ""
    for magic, suffix in _IMAGE_MAGIC:
        if head.startswith(magic):
            return suffix
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return ""


def _looks_gibberish(name: str) -> bool:
    """这个文件名是不是「Content-ID 那种乱码」（AF80F@621A0B33.88D4E26A…）。"""
    text = str(name or "").strip()
    if not text:
        return True
    if "@" in text:
        return True
    if re.fullmatch(r"[0-9A-Fa-f_.\-]{12,}", text):
        return True
    if len(re.findall(r"[0-9A-Fa-f]{8,}", text)) >= 1 and len(text) > 16:
        return True
    return False


def _part_filename(part) -> str:
    name = ""
    try:
        name = part.get_filename() or ""
    except Exception:
        name = ""
    if not name:
        try:
            name = part.get_param("name") or ""
        except Exception:
            name = ""
    if name:
        return _safe_filename(_decode(name))
    cid = str(part.get("Content-ID") or "").strip("<> ")
    if cid:
        return _safe_segment(cid)
    return ""


_IMG_SRC_RE = re.compile(
    r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])(https?://[^"\']+)\2', re.I)


def _localize_remote_images(html: str, folder: Path,
                            attachments: List[Dict[str, Any]]) -> str:
    """把 HTML 里的远程图片下载到本地，`src` 换成本地文件名。

    公众号 / 企业微信这类邮件正文里的图片全是 `https://…` 外链，QTextBrowser
    不会去联网取，所以以前那里是一片空白（用户：「这里哪里有照片？？？」）。
    这里用 requests 取一次、存进 media 目录，渲染时就能显示。
    """
    urls = list(dict.fromkeys(match.group(3) for match in _IMG_SRC_RE.finditer(html)))
    if not urls:
        return html
    try:
        import requests
    except Exception:
        return html
    budget = 30 * 1024 * 1024
    total = sum(int(item.get("size") or 0) for item in attachments)
    mapping: Dict[str, str] = {}
    for index, url in enumerate(urls[:40]):
        if total >= budget:
            break
        try:
            response = requests.get(url, timeout=8, headers={
                "User-Agent": UA, "Referer": url,
            })
            if response.status_code >= 400:
                continue
            data = response.content or b""
        except Exception:
            continue
        if len(data) < 128:            # 追踪像素 / 空图，别存
            continue
        ctype = str(response.headers.get("Content-Type") or "").lower()
        suffix = mimetypes.guess_extension(ctype.split(";")[0].strip()) or ".png"
        target = folder / ("img_remote_%02d%s" % (index, suffix))
        try:
            target.write_bytes(data)
        except Exception:
            continue
        total += len(data)
        mapping[url] = target.name
    if not mapping:
        return html

    def _sub(match):
        url = match.group(3)
        name = mapping.get(url)
        if not name:
            return match.group(0)
        return "%s%s%s%s" % (match.group(1), match.group(2), name, match.group(2))

    return _IMG_SRC_RE.sub(_sub, html)


def extract_media(msg, account_id: int, uid: str) -> Dict[str, Any]:
    """把内联图片和附件存到本地，并把 HTML 里的 `cid:` 换成真实文件。

    返回 ``{"html", "attachments": [{"name","size","path","mime"}],
    "images": n, "dir": 目录}``。
    """
    folder = mail_media_dir(account_id, uid)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    attachments: List[Dict[str, Any]] = []
    cid_map: Dict[str, str] = {}
    total = 0
    images = 0
    html = message_html(msg)
    try:
        parts = list(msg.walk())
    except Exception:
        parts = [msg]
    for index, part in enumerate(parts):
        try:
            ctype = part.get_content_type()
        except Exception:
            continue
        if ctype in ("text/plain", "text/html") or part.is_multipart():
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:
            data = b""
        if not data:
            continue
        disposition = str(part.get("Content-Disposition") or "").lower()
        cid = str(part.get("Content-ID") or "").strip("<> ")
        name = _part_filename(part)
        inline = bool(cid) or ("inline" in disposition and "attachment" not in disposition)
        # 有的邮件把图片写成 application/octet-stream（文件名还是 Content-ID），
        # 以前会当成「附件」存下来，于是正文里的 <img src="cid:…"> 一直显示不出来。
        sniffed = _sniff_image(data)
        is_image = ctype.startswith("image/") or bool(sniffed)
        if is_image:
            suffix = sniffed or mimetypes.guess_extension(ctype) or ".png"
            if suffix == ".jpe":
                suffix = ".jpg"
            base = name if name and not _looks_gibberish(name) else "图片%d" % (
                images + 1)
            base = _safe_segment(base)
            if not base.lower().endswith(suffix.lower()):
                base += suffix
            target = folder / ("img_%02d_%s" % (index, base))
            try:
                if len(data) + total <= MEDIA_LIMIT:
                    target.write_bytes(data)
                    total += len(data)
                    images += 1
                    if cid:
                        cid_map[cid] = target.name
                    if name:
                        cid_map.setdefault(name, target.name)
            except Exception:
                pass
            continue
        if not name and not inline:
            continue
        # 附件名像乱码（Content-ID）时给个人话名字
        ext = Path(name).suffix if name else ""
        if not ext:
            ext = mimetypes.guess_extension(str(ctype).split(";")[0].strip()) or ""
        label = name if name and not _looks_gibberish(name) \
            else "附件%d%s" % (len(attachments) + 1, ext)
        target = folder / ("att_%02d_%s" % (index, _safe_segment(label)))
        saved = False
        try:
            if len(data) + total <= MEDIA_LIMIT:
                target.write_bytes(data)
                total += len(data)
                saved = True
        except Exception:
            saved = False
        if cid:
            # 正文里可能用 <img src="cid:…"> 引用这个附件：也映射过去
            cid_map.setdefault(cid, target.name)
        attachments.append({
            "name": label,
            "size": len(data),
            "mime": ctype,
            "path": str(target) if saved else "",
        })
    if not str(html or "").strip():
        try:
            (folder / ".nohtml").write_text("1", encoding="utf-8")
        except Exception:
            pass
    try:
        meta = [{"name": item.get("name"), "size": item.get("size"),
                 "mime": item.get("mime"),
                 "file": Path(item["path"]).name if item.get("path") else ""}
                for item in attachments]
        (folder / "attachments.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    if html:
        html = _localize_remote_images(html, folder, attachments)
    if html and cid_map:
        for cid, filename in cid_map.items():
            html = html.replace('cid:%s' % cid, filename)
            html = html.replace('cid:%s' % cid.replace(" ", "%20"), filename)
    return {"html": html, "attachments": attachments, "images": images,
            "dir": str(folder)}


def save_rich_cache(account_id: int, uid: str, html: str) -> str:
    """把（已经换成本地图片的）HTML 存一份，下次打开不用重新联网。"""
    if not str(html or "").strip():
        return ""
    folder = mail_media_dir(account_id, uid)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "body.html"
        target.write_text(str(html), encoding="utf-8")
        return str(target)
    except Exception:
        return ""


def mark_no_html(account_id: int, uid: str) -> None:
    """记下「这封没有 HTML 正文」：以后打开它就别再联网补取了。"""
    try:
        folder = mail_media_dir(account_id, uid)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".nohtml").write_text("1", encoding="utf-8")
    except Exception:
        pass


def cached_rich(account_id: int, uid: str) -> Dict[str, Any]:
    """上次缓存的富正文：``{"html":..., "dir":..., "attachments":[...]}``。"""
    folder = mail_media_dir(account_id, uid)
    html = ""
    try:
        path = folder / "body.html"
        if path.exists():
            html = path.read_text(encoding="utf-8")
            if "cid:" in (html or "").lower():
                # 老缓存里 `cid:` 没换成本地文件 —— 于是正文图片一直是空的。
                # 当成「还没缓存」，让界面重取一次（新代码会把它换成真实图片）。
                html = ""
    except Exception:
        html = ""
    attachments: List[Dict[str, Any]] = []
    # 优先读 extract_media 写下的清单（原始文件名是中文，不能靠磁盘名反推）
    try:
        meta_path = folder / "attachments.json"
        if meta_path.exists():
            for item in json.loads(meta_path.read_text(encoding="utf-8")):
                filename = str((item or {}).get("file") or "")
                if not filename:
                    continue
                attachments.append({
                    "name": str(item.get("name") or filename),
                    "size": int(item.get("size") or 0),
                    "mime": str(item.get("mime") or ""),
                    "path": str(folder / filename),
                })
    except Exception:
        attachments = []
    if not attachments:
        try:
            for item in sorted(folder.iterdir()):
                if item.name in ("body.html", "attachments.json") \
                        or not item.is_file():
                    continue
                if not item.name.startswith("att_"):
                    continue                  # img_ 是正文里的内联图片，不算附件
                attachments.append({
                    "name": item.name.split("_", 2)[-1],
                    "size": item.stat().st_size,
                    "mime": "",
                    "path": str(item),
                })
        except Exception:
            attachments = []
    tried = bool(html) or (folder / ".nohtml").exists()
    return {"html": html, "dir": str(folder), "attachments": attachments,
            "tried": tried}


def save_attachment(source: str, name: str = "", target_dir=None) -> str:
    """把附件复制到下载目录（用原始文件名），返回新路径。"""
    import shutil

    src = Path(str(source or ""))
    if not src.exists():
        raise RuntimeError("附件文件不在了")
    if target_dir is None:
        from . import settings_dirs

        target_dir = settings_dirs.downloads_dir(None)
    filename = _safe_filename(name or src.name.split("_", 2)[-1])
    target = Path(target_dir) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        stem, suffix = target.stem, target.suffix
        index = 2
        while target.exists():
            target = target.with_name("%s (%d)%s" % (stem, index, suffix))
            index += 1
    shutil.copy2(src, target)
    return str(target)