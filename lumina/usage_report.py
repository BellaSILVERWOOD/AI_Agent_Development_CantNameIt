"""使用情况上报（不走邮件）+ 违规分级处理。

* **上报**：客户端每隔几分钟、以及启动/退出时，往管理员的地址 POST 一份 JSON
  （`http://10.137.44.215:18765/lumina-usage`）——使用时长、API 调用次数、
  首字延迟与出字速度、账户/设备指纹。局域网内直连，不需要邮件，也不需要公网；
* **接收**：管理员这份程序里跑一个小的 HTTP 监听（默认 18765 端口），
  把上报记进 `usage_reports`，顺手判违规：
  1. 用户名不在注册表（想绕过注册）；
  2. 上报里带 `api_without_grant`（没授权就调管理员的模型接口）；
  3. 同一设备短时间里换了多个用户名；
  4. 当天使用时长超过上限（默认 12 小时）。
* **分级**：第 1 次 → 提醒；第 2–3 次 → 警告；第 4 次起 → 封号（注册表停用 +
  通知对方本机锁定）。客户端收到 `ban` 会立刻停用本地账户并要求重新审批。
* 说明：这些都是「诚实客户端」的约束——对方改代码当然能不上报，所以真正的
  硬约束仍然是「签名授权 + 有效期」。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

from . import users

URL_KEY = "report_url"
DEFAULT_URL = "http://10.137.44.215:18765/lumina-usage"
PORT_KEY = "report_listen_port"
DEFAULT_PORT = 18765
INTERVAL_KEY = "report_interval_minutes"
DEFAULT_INTERVAL = 10
DAILY_LIMIT_KEY = "daily_limit_hours"
DEFAULT_DAILY_LIMIT = 12
BAN_AFTER = 4
ENABLED_KEY = "report_enabled"
SIG_KEY = "report_last_sig"          # 上次成功上报的「有没有变化」指纹
FAIL_KEY = "report_fail_count"       # 连续失败次数（用来退避，别一直重试）
CLEANUP_KEY = "report_cleanup_at"    # 上次清理上报记录的时间
KEEP_DAYS = 90                       # 管理员这边最多留 90 天
KEEP_ROWS_PER_USER = 2000            # 每个用户最多留 2000 条


# ------------------------------------------------------------------ 表
def _ensure(store) -> None:
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS usage_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE,
            device TEXT NOT NULL DEFAULT '',
            received_at TEXT NOT NULL,
            used_seconds INTEGER NOT NULL DEFAULT 0,
            requests INTEGER NOT NULL DEFAULT 0,
            api_calls INTEGER NOT NULL DEFAULT 0,
            ttft REAL NOT NULL DEFAULT 0,
            tok_s REAL NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT ''
        )"""
    )
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS usage_flags (
            username TEXT PRIMARY KEY COLLATE NOCASE,
            count INTEGER NOT NULL DEFAULT 0,
            last_reason TEXT NOT NULL DEFAULT '',
            last_at TEXT NOT NULL DEFAULT '',
            banned INTEGER NOT NULL DEFAULT 0
        )"""
    )
    store.conn.commit()


def _stamp(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ 设置
def enabled(store) -> bool:
    try:
        return (store.get_setting(ENABLED_KEY, "1") or "1") != "0"
    except Exception:
        return True


def set_enabled(store, value: bool) -> None:
    store.set_setting(ENABLED_KEY, "1" if value else "0")


def report_url(store) -> str:
    return (store.get_setting(URL_KEY, DEFAULT_URL) or DEFAULT_URL).strip()


def set_report_url(store, url: str) -> None:
    store.set_setting(URL_KEY, str(url or "").strip())


def listen_port(store) -> int:
    try:
        return max(1024, min(65535, int(float(store.get_setting(
            PORT_KEY, str(DEFAULT_PORT)) or DEFAULT_PORT))))
    except (TypeError, ValueError):
        return DEFAULT_PORT


def interval_minutes(store) -> int:
    try:
        return max(1, int(float(store.get_setting(
            INTERVAL_KEY, str(DEFAULT_INTERVAL)) or DEFAULT_INTERVAL)))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL


def daily_limit_hours(store) -> int:
    try:
        return max(1, int(float(store.get_setting(
            DAILY_LIMIT_KEY, str(DEFAULT_DAILY_LIMIT)) or DEFAULT_DAILY_LIMIT)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_LIMIT


# ------------------------------------------------------------------ 客户端计数
def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def mark_activity(store, seconds: float = 0.0, requests: int = 0,
                  api_calls: int = 0, ttft: Optional[float] = None,
                  tok_s: Optional[float] = None, api_without_grant: bool = False) -> None:
    """本地累计（当天），上报时一起发走。"""
    day = _today()
    if store.get_setting("usage_day", "") != day:
        store.update_settings({
            "usage_day": day,
            "usage_seconds": "0",
            "usage_requests": "0",
            "usage_api_calls": "0",
            "usage_ttft_sum": "0",
            "usage_ttft_n": "0",
            "usage_toks_sum": "0",
            "usage_toks_n": "0",
            "usage_bypass": "0",
        })
    def _num(key: str, default: float = 0.0) -> float:
        try:
            return float(store.get_setting(key, str(default)) or default)
        except (TypeError, ValueError):
            return default
    updates = {
        "usage_seconds": str(_num("usage_seconds") + max(0.0, seconds)),
        "usage_requests": str(int(_num("usage_requests")) + int(requests)),
        "usage_api_calls": str(int(_num("usage_api_calls")) + int(api_calls)),
    }
    if ttft is not None:
        updates["usage_ttft_sum"] = str(_num("usage_ttft_sum") + float(ttft))
        updates["usage_ttft_n"] = str(int(_num("usage_ttft_n")) + 1)
    if tok_s:
        updates["usage_toks_sum"] = str(_num("usage_toks_sum") + float(tok_s))
        updates["usage_toks_n"] = str(int(_num("usage_toks_n")) + 1)
    if api_without_grant:
        updates["usage_bypass"] = "1"
    store.update_settings(updates)


def snapshot(store, username: str = "") -> Dict[str, Any]:
    _ensure(store)
    def _num(key: str, default: float = 0.0) -> float:
        try:
            return float(store.get_setting(key, str(default)) or default)
        except (TypeError, ValueError):
            return default
    ttft_n = int(_num("usage_ttft_n"))
    tok_n = int(_num("usage_toks_n"))
    name = username
    if not name:
        try:
            current = users.current_user(store)
            if current:
                name = str(current.get("username") or "")
            if not name:
                listing = users.list_users(store)
                name = str(listing[0]["username"]) if listing else ""
        except Exception:
            name = ""
    return {
        "v": 1,
        "username": name,
        "device": users.device_hash(),
        "used_seconds": int(_num("usage_seconds")),
        "requests": int(_num("usage_requests")),
        "api_calls": int(_num("usage_api_calls")),
        "ttft": (_num("usage_ttft_sum") / ttft_n) if ttft_n else 0.0,
        "tok_s": (_num("usage_toks_sum") / tok_n) if tok_n else 0.0,
        "api_without_grant": (store.get_setting("usage_bypass", "0") or "0") == "1",
        "day": store.get_setting("usage_day", "") or _today(),
        "ts": _stamp(),
    }


def _signature(payload: Dict[str, Any]) -> str:
    """这份上报「有没有新东西」的指纹：数字都没变就说明没新使用。"""
    return "|".join(str(payload.get(key)) for key in
                    ("day", "used_seconds", "requests", "api_calls",
                     "api_without_grant"))


def _int_setting(store, key: str, default: int = 0) -> int:
    try:
        return int(float(store.get_setting(key, str(default)) or default))
    except (TypeError, ValueError):
        return default


def send(store, username: str = "", timeout: float = 4.0,
         force: bool = False) -> Dict[str, Any]:
    """POST 一份上报；返回管理员的处理结果（action / count / message）。

    省流量 / 少打搅的两条规则：
    * 数字一点没变（这段时间没新使用）就不发，除非 `force=True`（心跳）；
    * 连续失败 2 次之后先别再试（局域网里对方没开程序时，避免每 10 分钟都白连一次）。
    """
    if not enabled(store):
        return {"ok": False, "action": "off", "message": "上报已关闭"}
    payload = snapshot(store, username)
    signature = _signature(payload)
    fails = _int_setting(store, FAIL_KEY, 0)
    if not force:
        if signature and signature == (store.get_setting(SIG_KEY, "") or ""):
            return {"ok": True, "action": "skip",
                    "message": "这段时间没有新的使用记录"}
        if fails >= 2:
            return {"ok": False, "action": "backoff",
                    "message": "上次没连上管理员，先缓一缓"}
    try:
        import requests

        response = requests.post(report_url(store), json=payload, timeout=timeout)
        if response.status_code >= 400:
            store.set_setting(FAIL_KEY, str(fails + 1))
            return {"ok": False, "action": "error",
                    "message": f"上报被拒绝：HTTP {response.status_code}"}
        try:
            data = response.json()
        except Exception:
            data = {}
        store.update_settings({
            "report_last_at": _stamp(),
            "report_last_result": str(data.get("action") or "ok"),
            SIG_KEY: signature,
            FAIL_KEY: "0",
        })
        return {"ok": True, **{key: data.get(key) for key in
                              ("action", "count", "message")}}
    except Exception as exc:  # noqa: BLE001
        store.update_settings({FAIL_KEY: str(fails + 1),
                               "report_last_error": str(exc)})
        return {"ok": False, "action": "error",
                "message": f"连不上管理员的接收程序（{exc}）"}


# ------------------------------------------------------------------ 客户端处理结果
def apply_action(store, result: Dict[str, Any]) -> str:
    """把管理员的处理结果落到本机：提醒 / 警告 / 封号。返回要提示的文案。"""
    action = str((result or {}).get("action") or "")
    count = int((result or {}).get("count") or 0)
    message = str((result or {}).get("message") or "")
    if action == "remind":
        return f"提醒（第 {count} 次）：{message or '使用记录异常，请遵守使用约定'}"
    if action == "warn":
        return f"警告（第 {count} 次，累计 4 次会封号）：{message or '使用记录异常'}"
    if action == "ban":
        _lock_out(store, message)
        return f"账户已被管理员封禁：{message or '请联系管理员'}"
    return ""


def _lock_out(store, reason: str) -> None:
    """封号：本机停用账户 + 清掉「记住设备」。"""
    try:
        for item in users.list_users(store):
            if str(item.get("role")) == "admin":
                continue
            users.set_status(store, int(item["id"]), "disabled")
    except Exception:
        pass
    try:
        users.forget_device(store)
    except Exception:
        pass
    store.set_setting("ban_reason", str(reason or ""))
    store.set_setting("ban_at", _stamp())


# ------------------------------------------------------------------ 服务端（管理员）
def _flag(store, username: str, reason: str) -> Dict[str, Any]:
    _ensure(store)
    row = store.conn.execute(
        "SELECT * FROM usage_flags WHERE username = ? COLLATE NOCASE",
        (username,)).fetchone()
    count = int(row["count"] or 0) + 1 if row else 1
    banned = 1 if count >= BAN_AFTER else 0
    store.conn.execute(
        "INSERT INTO usage_flags(username, count, last_reason, last_at, banned)"
        " VALUES (?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET"
        " count = ?, last_reason = ?, last_at = ?, banned = ?",
        (username, count, reason, _stamp(), banned,
         count, reason, _stamp(), banned))
    store.conn.commit()
    if banned:
        try:
            from . import registration

            registration.set_registry_status(store, username, "disabled")
        except Exception:
            pass
        return {"action": "ban", "count": count,
                "message": f"已封禁：{reason}"}
    if count >= 2:
        return {"action": "warn", "count": count, "message": reason}
    return {"action": "remind", "count": count, "message": reason}


def _maybe_cleanup(store) -> None:
    """清理上报记录：只留最近 90 天、每人最多 2000 条（6 小时最多跑一次）。

    每 10 分钟一条、几个同学一起用，一年下来表会很大；不清理的话
    `usage_reports` 会一直长，备份和查询都会变慢。
    """
    now = datetime.now()
    last = store.get_setting(CLEANUP_KEY, "") or ""
    if last:
        try:
            if (now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")) < timedelta(hours=6):
                return
        except Exception:
            pass
    try:
        store.conn.execute("DELETE FROM usage_reports WHERE received_at < ?",
                           (_stamp(now - timedelta(days=KEEP_DAYS)),))
        store.conn.execute(
            "DELETE FROM usage_reports WHERE id IN ("
            " SELECT id FROM (SELECT id, ROW_NUMBER() OVER ("
            "   PARTITION BY username ORDER BY id DESC) AS rn"
            "  FROM usage_reports) WHERE rn > ?)",
            (KEEP_ROWS_PER_USER,))
        store.conn.commit()
        store.set_setting(CLEANUP_KEY, _stamp(now))
    except Exception:
        pass


def handle_report(store, payload: Dict[str, Any]) -> Dict[str, Any]:
    """管理员侧处理一份上报：记录 + 判违规 + 返回处置动作。"""
    _ensure(store)
    username = str(payload.get("username") or "").strip()
    device = str(payload.get("device") or "")
    used = int(payload.get("used_seconds") or 0)
    api_calls = int(payload.get("api_calls") or 0)
    if not username:
        return {"action": "ok", "count": 0, "message": ""}
    note = ""
    try:
        from . import registration

        known = registration.has_registry_entry(store, username)
    except Exception:
        known = True
    admin = False
    try:
        admin = bool(users.is_admin(users.find_user(store, username)))
    except Exception:
        admin = False
    if admin:
        note = "管理员本人"
    elif not known:
        note = "未注册的账户"
    elif payload.get("api_without_grant"):
        note = "没有授权就调用模型接口"
    elif used > daily_limit_hours(store) * 3600:
        note = f"当天使用超过 {daily_limit_hours(store)} 小时"
    else:
        # 同一设备换用户名
        rows = store.conn.execute(
            "SELECT DISTINCT username FROM usage_reports"
            " WHERE device = ? AND received_at > ?",
            (device, _stamp(datetime.now() - timedelta(hours=12)))).fetchall()
        names = {str(row[0]).lower() for row in rows}
        if names and username.lower() not in names and len(names) >= 1:
            note = "同一台设备换了账号"
    store.conn.execute(
        "INSERT INTO usage_reports(username, device, received_at, used_seconds,"
        " requests, api_calls, ttft, tok_s, note) VALUES (?,?,?,?,?,?,?,?,?)",
        (username, device, _stamp(), used, int(payload.get("requests") or 0),
         api_calls, float(payload.get("ttft") or 0),
         float(payload.get("tok_s") or 0), note))
    store.conn.commit()
    _maybe_cleanup(store)
    if note and not admin:
        return _flag(store, username, note)
    return {"action": "ok", "count": 0, "message": ""}


class UsageServer:
    """管理员本机的小监听：接收上报（局域网直连，不走邮件）。"""

    def __init__(self, store):
        self.store = store
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port = listen_port(store)
        self.error = ""

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> bool:
        if self._httpd is not None:
            return True
        store = self.store

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    self.send_response(400)
                    self.end_headers()
                    return
                if "/lumina-register" in str(self.path or ""):
                    # 客户端把「加密后的注册申请」直接 POST 过来（不走邮件）
                    try:
                        from . import registration

                        result = registration.store_incoming(store, payload or {})
                        # 老用户换新设备：能自动放行就当场批准，把验证码和回执回给客户端
                        if result.get("ok") and not result.get("repeat"):
                            nonce = str((payload or {}).get("nonce") or "")
                            approved = registration.auto_approve_inbox(store, nonce)
                            if approved:
                                result.update({
                                    "approved": True,
                                    "code": approved.get("code", ""),
                                    "receipt": approved.get("receipt", ""),
                                    "expires_at": approved.get("expires_at", ""),
                                    "detail": ("老用户新设备，已自动通过（第 %s 台设备）"
                                               % approved.get("devices", "?")),
                                })
                    except Exception as exc:  # noqa: BLE001
                        result = {"ok": False, "error": str(exc)}
                else:
                    try:
                        result = handle_report(store, payload or {})
                    except Exception as exc:  # noqa: BLE001
                        result = {"action": "error", "count": 0, "message": str(exc)}
                body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                body = b'{"ok": true, "service": "lumina-report"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            self._httpd = HTTPServer(("0.0.0.0", self.port), Handler)
        except Exception as exc:  # noqa: BLE001
            self.error = f"端口 {self.port} 起不来：{exc}"
            self._httpd = None
            return False
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="lumina-report", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass


def recent_reports(store, limit: int = 20) -> List[Dict[str, Any]]:
    _ensure(store)
    rows = store.conn.execute(
        "SELECT * FROM usage_reports ORDER BY id DESC LIMIT ?", (int(limit),)
    ).fetchall()
    return [dict(row) for row in rows]


def violations(store) -> List[Dict[str, Any]]:
    _ensure(store)
    rows = store.conn.execute(
        "SELECT * FROM usage_flags ORDER BY count DESC").fetchall()
    return [dict(row) for row in rows]
