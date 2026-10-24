"""SQLite 持久层：日程、邮件、联系人、设置。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import secure
from .config import DB_PATH


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_dt(date_str: str, time_str: str) -> datetime:
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")


def compute_remind_at(date_str: str, start_time: str, minutes: Optional[int]) -> Optional[str]:
    if minutes is None:
        return None
    dt = _to_dt(date_str, start_time) - timedelta(minutes=int(minutes))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class Store:
    """所有界面共享的数据库访问对象（单连接，主线程使用）。"""

    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=10.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            # 别让一次卡住的写把界面拖住 10 秒
            self.conn.execute("PRAGMA busy_timeout=2500")
        except Exception:
            pass
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()
        self._apply_defaults()
        self.seed_demo_data()

    # ------------------------------------------------------------------ 基础
    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL,
                start_time TEXT NOT NULL DEFAULT '09:00',
                end_time TEXT NOT NULL DEFAULT '10:00',
                remind_minutes INTEGER,
                remind_at TEXT,
                notified INTEGER NOT NULL DEFAULT 0,
                done INTEGER NOT NULL DEFAULT 0,
                color TEXT NOT NULL DEFAULT 'violet',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);

            CREATE TABLE IF NOT EXISTS mails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                to_addr TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                html TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                tasks_json TEXT NOT NULL DEFAULT '[]',
                attachments_json TEXT NOT NULL DEFAULT '[]',
                note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                sent_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mails_status ON mails(status);

            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    html TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memos_updated ON memos(updated_at);
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT '',
    read_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notices_url ON notices(url);
CREATE TABLE IF NOT EXISTS event_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT 'teal',
                created_at TEXT NOT NULL
            );

CREATE TABLE IF NOT EXISTS important_dates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                date TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT 'rose',
                kind TEXT NOT NULL DEFAULT 'custom',
                event_id INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """老库补列：events.group_id（日程所属分组）、notices.published_at（网页上的发布时间）。"""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(events)")}
        if "group_id" not in columns:
            self.conn.execute("ALTER TABLE events ADD COLUMN group_id INTEGER")
            self.conn.commit()
        notice_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(notices)")}
        if notice_columns and "published_at" not in notice_columns:
            self.conn.execute(
                "ALTER TABLE notices ADD COLUMN published_at TEXT NOT NULL DEFAULT ''")
            self.conn.commit()

    def _apply_defaults(self) -> None:
        defaults = {
            "smtp_host": "",
            "smtp_port": "",
            "smtp_security": "ssl",
            "smtp_user": "",
            "smtp_password": "",
            "sender_name": "",
            "sender_email": "",
            "default_to": "",
            "reminders_enabled": "1",
            "reminder_sound": "1",
            "timetable_enabled": "0",
            "timetable_status": "",
            "theme_accent": "#2F7A5F",
            "theme_accent2": "#D99A4E",
            "theme_bg": "linen",
            "theme_alpha": "0.78",
            "theme_glass": "1",
            "theme_blur": "24",
            "seeded": "0",
        }
        for key, value in defaults.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
            )
        self.conn.commit()

    def seed_demo_data(self) -> None:
        """仅在首次启动时写入几条示例数据，方便直接体验。"""
        if self.get_setting("seeded") == "1":
            return
        today = datetime.now().date()
        samples = [
            (today, "09:30", "10:30", "项目周会", "同步本周里程碑进度，确认交付物与风险项。", 15, "violet", 0),
            (today, "14:00", "15:30", "完成季度汇报材料", "整理数据看板与关键结论，输出汇报初稿。", 30, "teal", 0),
            (today, "18:30", "19:30", "健身训练", "力量训练 45 分钟 + 拉伸。", 15, "amber", 0),
            (today + timedelta(days=1), "10:00", "11:00", "设计评审会", "评审新版工作台交互方案，收集反馈。", 30, "sky", 0),
            (today - timedelta(days=1), "16:00", "17:30", "整理客户反馈并输出改进清单", "汇总 12 条反馈，输出优先级与负责人建议。", None, "rose", 1),
        ]
        for day, start, end, title, desc, remind, color, done in samples:
            d = day.strftime("%Y-%m-%d")
            self.add_event(
                title=title,
                description=desc,
                date=d,
                start_time=start,
                end_time=end,
                remind_minutes=remind,
                color=color,
                done=bool(done),
            )
        if not self.list_contacts():
            self.add_contact("王经理（示例）", "wang.manager@example.com")
        self.set_setting("seeded", "1")

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ 设置
    # ---------------------------------------------------------- 备忘录
    def list_memos(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM memos ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_memo(self, memo_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM memos WHERE id = ?", (int(memo_id),)
        ).fetchone()
        return dict(row) if row else None

    def add_memo(self, title: str = "", html: str = "") -> int:
        ts = now_str()
        cursor = self.conn.execute(
            "INSERT INTO memos(title, html, created_at, updated_at) VALUES (?,?,?,?)",
            (title, html, ts, ts),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_memo(self, memo_id: int, title: Optional[str] = None,
                    html: Optional[str] = None) -> None:
        fields: List[str] = ["updated_at = ?"]
        params: List[Any] = [now_str()]
        if title is not None:
            fields.append("title = ?")
            params.append(title)
        if html is not None:
            fields.append("html = ?")
            params.append(html)
        params.append(int(memo_id))
        self.conn.execute(
            "UPDATE memos SET " + ", ".join(fields) + " WHERE id = ?", params
        )
        self.conn.commit()

    def delete_memo(self, memo_id: int) -> None:
        self.conn.execute("DELETE FROM memos WHERE id = ?", (int(memo_id),))
        self.conn.commit()

    # ---------------------------------------------------------- 通知
    def add_notice(self, source: str, title: str, url: str,
                   published_at: str = "") -> bool:
        """插入一条通知；已存在（按 URL 去重）返回 False。"""
        existing = self.conn.execute(
            "SELECT id, published_at FROM notices WHERE url = ?", (url,)
        ).fetchone()
        if existing:
            # 老数据没有发布时间：这次抓到了就补上
            if published_at and not (existing["published_at"] or "").strip():
                self.conn.execute(
                    "UPDATE notices SET published_at = ? WHERE url = ?",
                    (published_at, url))
                self.conn.commit()
            return False
        self.conn.execute(
            "INSERT INTO notices(source, title, url, fetched_at, published_at)"
            " VALUES (?,?,?,?,?)",
            (source, title, url, now_str(), published_at or ""),
        )
        self.conn.commit()
        return True

    def list_notices(self, limit: int = 40) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM notices ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(row) for row in rows]

    def unread_notice_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM notices WHERE read_at IS NULL"
        ).fetchone()
        return int(row["n"]) if row else 0

    def mark_notices_read(self) -> None:
        self.conn.execute(
            "UPDATE notices SET read_at = ? WHERE read_at IS NULL", (now_str(),)
        )
        self.conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, "" if value is None else str(value)),
        )
        self.conn.commit()

    def update_settings(self, values: Dict[str, Any]) -> None:
        with self.conn:
            for key, value in values.items():
                self.conn.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, "" if value is None else str(value)),
                )

    def get_secret(self, key: str, default: str = "") -> str:
        stored = self.get_setting(key, "")
        if not stored:
            return default
        return secure.unprotect(stored)

    def set_secret(self, key: str, plain: str) -> None:
        self.set_setting(key, secure.protect(plain) if plain else "")

    # ------------------------------------------------------------------ 日程
    @staticmethod
    def _event_row(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["done"] = bool(item["done"])
        item["notified"] = bool(item["notified"])
        return item

    def add_event(
        self,
        title: str,
        description: str = "",
        date: str = "",
        start_time: str = "09:00",
        end_time: str = "10:00",
        remind_minutes: Optional[int] = None,
        color: str = "violet",
        done: bool = False,
        group_id=None,
    ) -> int:
        ts = now_str()
        remind_at = compute_remind_at(date, start_time, remind_minutes)
        cur = self.conn.execute(
            "INSERT INTO events(title, description, date, start_time, end_time, remind_minutes,"
            " remind_at, notified, done, color, group_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                title.strip(), description, date, start_time, end_time, remind_minutes,
                remind_at, 0, 1 if done else 0, color, group_id, ts, ts,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_event(self, event_id: int, **fields: Any) -> None:
        allowed = {
            "title", "description", "date", "start_time", "end_time",
            "remind_minutes", "color", "done", "group_id",
        }
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        row = self.get_event(event_id)
        if row is None:
            return
        merged = {**row, **fields}
        remind_at = compute_remind_at(
            merged["date"], merged["start_time"], merged["remind_minutes"]
        )
        # 时间被修改后重新启用提醒
        time_changed = (
            merged["date"] != row["date"]
            or merged["start_time"] != row["start_time"]
            or merged["remind_minutes"] != row["remind_minutes"]
        )
        sets = [f"{key} = ?" for key in fields]
        params: List[Any] = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        sets += ["remind_at = ?", "updated_at = ?"]
        params += [remind_at, now_str()]
        if time_changed:
            sets.append("notified = ?")
            params.append(0)
        params.append(event_id)
        self.conn.execute(f"UPDATE events SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()

    def get_event(self, event_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._event_row(row) if row else None

    def delete_event(self, event_id: int) -> None:
        self.conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        self.conn.commit()

    def auto_complete_past(self, now: Optional[datetime] = None) -> int:
        """把「已经过了结束时间」的日程自动标为已完成，返回更新条数。"""
        now = now or datetime.now()
        today = now.strftime("%Y-%m-%d")
        hhmm = now.strftime("%H:%M")
        try:
            cursor = self.conn.execute(
                "UPDATE events SET done = 1, updated_at = ? "
                "WHERE done = 0 AND ("
                "  date < ? OR (date = ? AND "
                "  COALESCE(NULLIF(end_time, ''), '23:59') <= ?))",
                (now_str(), today, today, hhmm),
            )
            self.conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            return 0

    def set_done(self, event_id: int, done: bool) -> None:
        self.conn.execute(
            "UPDATE events SET done = ?, updated_at = ? WHERE id = ?",
            (1 if done else 0, now_str(), event_id),
        )
        self.conn.commit()

    def events_between(self, start: str, end: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE date BETWEEN ? AND ? ORDER BY date, start_time, id",
            (start, end),
        ).fetchall()
        return [self._event_row(r) for r in rows]

    def events_on(self, date: str) -> List[Dict[str, Any]]:
        return self.events_between(date, date)

    def event_index(self, start: str, end: str) -> Dict[str, List[Dict[str, Any]]]:
        index: Dict[str, List[Dict[str, Any]]] = {}
        for item in self.events_between(start, end):
            index.setdefault(item["date"], []).append(item)
        return index

    def counts_between(self, start: str, end: str,
                       exclude_group_ids: Optional[List[int]] = None) -> Dict[str, int]:
        """区间内的日程数；可以排除某些分组（比如课程表导入的课）。"""
        sql = ("SELECT COUNT(*) AS total, COALESCE(SUM(done), 0) AS done"
               " FROM events WHERE date BETWEEN ? AND ?")
        params: List[Any] = [start, end]
        excluded = [int(value) for value in (exclude_group_ids or [])]
        if excluded:
            placeholders = ",".join("?" for _ in excluded)
            sql += f" AND (group_id IS NULL OR group_id NOT IN ({placeholders}))"
            params.extend(excluded)
        row = self.conn.execute(sql, params).fetchone()
        return {"total": int(row["total"]), "done": int(row["done"])}

    def task_candidates(self, days_back: int = 30, days_forward: int = 14) -> List[Dict[str, Any]]:
        today = datetime.now().date()
        start = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
        end = (today + timedelta(days=days_forward)).strftime("%Y-%m-%d")
        return self.events_between(start, end)

    def pending_reminders(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now or datetime.now()
        horizon = (now - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        rows = self.conn.execute(
            "SELECT * FROM events WHERE done = 0 AND notified = 0 AND remind_at IS NOT NULL"
            " AND remind_at <= ? AND remind_at >= ?"
            " ORDER BY remind_at",
            (now.strftime("%Y-%m-%d %H:%M:%S"), horizon),
        ).fetchall()
        return [self._event_row(r) for r in rows]

    def mark_notified(self, event_id: int) -> None:
        self.conn.execute("UPDATE events SET notified = 1 WHERE id = ?", (event_id,))
        self.conn.commit()

    def snooze(self, event_id: int, until: datetime) -> None:
        self.conn.execute(
            "UPDATE events SET notified = 0, remind_at = ? WHERE id = ?",
            (until.strftime("%Y-%m-%d %H:%M:%S"), event_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ 邮件
    @staticmethod
    def _mail_row(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        try:
            item["tasks"] = json.loads(item.get("tasks_json") or "[]")
        except Exception:
            item["tasks"] = []
        try:
            item["attachments"] = json.loads(item.get("attachments_json") or "[]")
        except Exception:
            item["attachments"] = []
        return item

    def add_mail(
        self,
        to_addr: str,
        subject: str,
        html: str,
        text: str,
        tasks: Iterable[Dict[str, Any]] = (),
        attachments: Iterable[Dict[str, Any]] = (),
        note: str = "",
        status: str = "draft",
        error: str = "",
        sent_at: Optional[str] = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO mails(to_addr, subject, html, text, tasks_json, attachments_json,"
            " note, status, error, created_at, sent_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                to_addr, subject, html, text,
                json.dumps(list(tasks), ensure_ascii=False),
                json.dumps(list(attachments), ensure_ascii=False),
                note, status, error, now_str(), sent_at,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_mail(self, mail_id: int, **fields: Any) -> None:
        allowed = {
            "to_addr", "subject", "html", "text", "tasks_json",
            "attachments_json", "note", "status", "error", "sent_at",
        }
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{key} = ?" for key in fields)
        self.conn.execute(f"UPDATE mails SET {sets} WHERE id = ?", [*fields.values(), mail_id])
        self.conn.commit()

    def set_mail_status(self, mail_id: int, status: str, error: str = "") -> None:
        sent_at = now_str() if status == "sent" else None
        self.conn.execute(
            "UPDATE mails SET status = ?, error = ?, sent_at = ? WHERE id = ?",
            (status, error, sent_at, mail_id),
        )
        self.conn.commit()

    def get_mail(self, mail_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM mails WHERE id = ?", (mail_id,)).fetchone()
        return self._mail_row(row) if row else None

    def list_mails(self, query: str = "") -> List[Dict[str, Any]]:
        sql = "SELECT * FROM mails"
        params: List[Any] = []
        if query:
            like = f"%{query}%"
            sql += " WHERE subject LIKE ? OR to_addr LIKE ? OR text LIKE ?"
            params = [like, like, like]
        sql += " ORDER BY COALESCE(sent_at, created_at) DESC, id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._mail_row(r) for r in rows]

    def mail_counts(self) -> Dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS c FROM mails GROUP BY status").fetchall()
        counts = {"draft": 0, "sent": 0, "failed": 0}
        for row in rows:
            counts[row["status"]] = int(row["c"])
        counts["total"] = sum(counts.values())
        return counts

    def delete_mail(self, mail_id: int) -> None:
        self.conn.execute("DELETE FROM mails WHERE id = ?", (mail_id,))
        self.conn.commit()

    # ------------------------------------------------------------------ 联系人
    def list_contacts(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM contacts ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def add_contact(self, name: str, email: str) -> int:
        existing = self.conn.execute(
            "SELECT id FROM contacts WHERE email = ?", (email.strip(),)
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE contacts SET name = ? WHERE id = ?", (name.strip(), existing["id"])
            )
            self.conn.commit()
            return int(existing["id"])
        cur = self.conn.execute(
            "INSERT INTO contacts(name, email, created_at) VALUES (?,?,?)",
            (name.strip(), email.strip(), now_str()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def delete_contact(self, contact_id: int) -> None:
        self.conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        self.conn.commit()


    # ------------------------------------------------------------------ 日程分组
    def list_groups(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM event_groups ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def group_colors(self) -> Dict[int, str]:
        return {int(row["id"]): str(row["color"]) for row in self.list_groups()}

    def add_group(self, name: str, color: str = "teal") -> int:
        cur = self.conn.execute(
            "INSERT INTO event_groups(name, color, created_at) VALUES (?,?,?)",
            (name.strip() or "新分组", color, now_str()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_group(self, group_id: int, name: str | None = None,
                     color: str | None = None) -> None:
        sets, params = [], []
        if name:
            sets.append("name = ?")
            params.append(name.strip())
        if color:
            sets.append("color = ?")
            params.append(color)
        if not sets:
            return
        params.append(int(group_id))
        self.conn.execute(f"UPDATE event_groups SET {', '.join(sets)} WHERE id = ?", params)
        if color:
            # 改组色时同步组内所有日程，保证「同组同色」
            self.conn.execute(
                "UPDATE events SET color = ?, updated_at = ? WHERE group_id = ?",
                (color, now_str(), int(group_id)),
            )
        self.conn.commit()

    def delete_group(self, group_id: int) -> None:
        self.conn.execute("UPDATE events SET group_id = NULL WHERE group_id = ?",
                          (int(group_id),))
        self.conn.execute("DELETE FROM event_groups WHERE id = ?", (int(group_id),))
        self.conn.commit()
