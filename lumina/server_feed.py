"""服务器公告的实时通道：优先 WebSocket，连不上就退回 20 秒轮询。

两条路都只做一件事：把 `GET /announcements?since=<last_id>`（或 WS 推来的
`{"type":"announcement"}`）里的公告落到本地 `server_announcements` 表，
再把**新**的那几条用信号交给主窗口（顶部横幅 + 「通知」页分区）。

* WebSocket：`PySide6.QtWebSockets.QWebSocket`（PySide6 自带，不引新依赖）；
* 轮询：`QTimer` 每 20 秒拉一次 `GET /announcements?since=<last_id>`；
* 断线重连：1s → 2s → 4s … 封顶 60s；WS 连续失败几次之后**同时**开一路轮询
  先把公告拿到，WS 继续按退避重试，连上后自动停掉轮询回到实时通道；
* 服务器上还没有 /announcements（404）时：停掉，别一直刷屏，界面显示
  「服务器还没开通公告」；
* 退出登录 / 关闭程序：`stop()`，连接立刻断掉。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from . import account

try:                                    # PySide6 一般自带 QtWebSockets
    from PySide6.QtWebSockets import QWebSocket
    HAS_WEBSOCKETS = True
except Exception:                       # pragma: no cover - 只有精简版 Qt 才会缺
    QWebSocket = None
    HAS_WEBSOCKETS = False

POLL_INTERVAL_MS = 20_000        # 轮询间隔：20 秒
BACKOFF_START_MS = 1_000         # 重连退避起点
BACKOFF_MAX_MS = 60_000          # 重连退避上限
WS_FAILURES_BEFORE_POLL = 2      # WS 连续失败几次就不再试，改轮询
CONNECT_TIMEOUT_MS = 8_000       # WS 握手多久没成就算失败（否则会一直挂在连接中）
FIRST_POLL_DELAY_MS = 1_200      # 启动后先等界面出来再拉一次
LEVELS = ("info", "warning", "critical")


# ------------------------------------------------------------------ 本地存储
def last_announcement_id(store) -> int:
    """本地记到哪一条了（用作 `?since=`，也是去重的依据）。"""
    try:
        return max(0, int(float(store.get_setting(account.SETTING_LAST_ANNOUNCEMENT, "0") or 0)))
    except (TypeError, ValueError):
        return 0


def set_last_announcement_id(store, value: int) -> int:
    try:
        cursor = max(0, int(value))
    except (TypeError, ValueError):
        cursor = 0
    try:
        if cursor > last_announcement_id(store):
            store.set_setting(account.SETTING_LAST_ANNOUNCEMENT, str(cursor))
    except Exception:
        pass
    return cursor


def save_announcements(store, items) -> List[Dict[str, Any]]:
    """把公告写进本地库，返回**新**的那些（列表顺序保持服务器给的顺序）。"""
    added: List[Dict[str, Any]] = []
    newest = last_announcement_id(store)
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            ann_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if ann_id <= 0:
            continue
        try:
            is_new = store.add_server_announcement(item)
        except Exception:
            is_new = False
        newest = max(newest, ann_id)
        if is_new:
            added.append(item)
    set_last_announcement_id(store, newest)
    return added


# ------------------------------------------------------------------ 通道
class ServerAnnouncementWatcher(QObject):
    """服务器公告的接收器（WebSocket 优先，轮询兜底）。"""

    new_announcements = Signal(list)     # 本轮新公告（list[dict]）
    state_changed = Signal(str)          # "ws" / "poll" / "offline" / "unsupported" / "off"

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._ws: Optional[Any] = None
        self._running = False
        self._mode = "off"
        self._backoff_ms = BACKOFF_START_MS
        self._ws_failures = 0
        self._unsupported = False
        self._polling = False
        self._last_error = ""

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.poll_once)

        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._connect_ws)

        # 握手看门狗：连不上（被墙 / 端口黑）时不会一直挂在「连接中」
        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.setInterval(CONNECT_TIMEOUT_MS)
        self._connect_timer.timeout.connect(self._on_ws_connect_timeout)

    # -------------------------------------------------- 状态 / 控制
    def mode(self) -> str:
        """当前通道：ws / poll / offline / unsupported / off。"""
        return self._mode

    def transport(self) -> str:
        """给界面看的通道名（含「为什么」）。"""
        if self._mode == "ws":
            return "WebSocket 实时"
        if self._mode == "connecting":
            return "正在连接 WebSocket…"
        if self._mode == "poll":
            return "轮询（20 秒）"
        if self._mode == "unsupported":
            return "服务器未开通公告接口"
        if self._mode == "offline":
            return "离线"
        return "未连接"

    def is_running(self) -> bool:
        return self._running

    def last_error(self) -> str:
        return self._last_error

    def _set_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        try:
            self.state_changed.emit(mode)
        except Exception:
            pass

    def start(self) -> None:
        """登录状态下才真正连；没登录就什么都不做（mode = off）。"""
        if self._running:
            return
        session = account.session(self.store)
        if not session.get("logged_in"):
            self._set_mode("off")
            return
        self._running = True
        self._backoff_ms = BACKOFF_START_MS
        self._ws_failures = 0
        self._unsupported = False
        self._polling = False
        self._last_error = ""
        if QWebSocket is None:
            # 没有 QtWebSockets：直接走轮询
            self._start_polling()
            QTimer.singleShot(FIRST_POLL_DELAY_MS, self.poll_once)
            return
        self._connect_ws()

    def stop(self) -> None:
        """退出登录 / 关程序：把连接、定时器全停掉。"""
        self._running = False
        self._polling = False
        for timer in (getattr(self, "_retry_timer", None),
                      getattr(self, "_poll_timer", None),
                      getattr(self, "_connect_timer", None)):
            try:
                timer.stop()
            except Exception:
                pass
        self._close_ws()
        self._set_mode("off")

    def restart(self) -> None:
        self.stop()
        self.start()

    # -------------------------------------------------- WebSocket
    def _close_ws(self) -> None:
        try:
            self._connect_timer.stop()
        except Exception:
            pass
        ws, self._ws = self._ws, None
        if ws is None:
            return
        for name in ("disconnected", "textMessageReceived", "connected"):
            try:
                getattr(ws, name).disconnect()
            except Exception:
                pass
        try:
            ws.close()
        except Exception:
            pass
        try:
            ws.deleteLater()
        except Exception:
            pass

    def _connect_ws(self) -> None:
        if not self._running or QWebSocket is None:
            return
        session = account.session(self.store)
        if not session.get("logged_in"):
            self._running = False
            self._set_mode("off")
            return
        self._close_ws()
        try:
            ws = QWebSocket()
        except Exception as exc:            # pragma: no cover
            self._last_error = "建不了 WebSocket：%s" % exc
            self._fallback_to_poll()
            return
        self._ws = ws
        try:
            ws.connected.connect(self._on_ws_connected)
            ws.disconnected.connect(self._on_ws_disconnected)
            ws.textMessageReceived.connect(self._on_ws_text)
        except Exception:
            pass
        for signal_name in ("errorOccurred", "error"):
            handler = getattr(ws, signal_name, None)
            if handler is None:
                continue
            try:
                handler.connect(self._on_ws_error)
                break
            except Exception:
                continue
        try:
            self._apply_ssl(ws)
        except Exception:
            pass
        try:
            ws.open(QUrl(account.stream_url(self.store)))
        except Exception as exc:            # pragma: no cover
            self._last_error = "WebSocket 打不开：%s" % exc
            self._fallback_to_poll()
            return
        if self._mode in ("off", "offline", ""):
            self._set_mode("connecting")
        try:
            self._connect_timer.start()
        except Exception:
            pass

    def _apply_ssl(self, ws) -> None:
        """只设置证书验证名，不提供任何「跳过校验」的降级。

        ws 走的是标准 TLS 校验（QSslConfiguration 默认 AutoVerifyPeer）：
        证书链或主机名任何一项不过，就当作 WS 不可用，转轮询通道（轮询走
        account.py，那边同样是「链校验 + 自己比对 SAN」，都不接受坏证书）。
        """
        try:
            from PySide6.QtNetwork import QSslConfiguration, QSslSocket
        except Exception:
            return
        try:
            config = QSslConfiguration.defaultConfiguration()
        except Exception:
            return
        try:
            config.setPeerVerifyMode(QSslSocket.PeerVerifyMode.AutoVerifyPeer)
        except Exception:
            pass
        try:
            ws.setSslConfiguration(config)
        except Exception:
            pass

    def _on_ws_connected(self) -> None:
        self._backoff_ms = BACKOFF_START_MS
        self._ws_failures = 0
        try:
            self._connect_timer.stop()
        except Exception:
            pass
        # WS 回来了：轮询停掉，回到实时通道
        if self._polling:
            self._polling = False
            try:
                self._poll_timer.stop()
            except Exception:
                pass
        self._set_mode("ws")

    def _on_ws_disconnected(self) -> None:
        if not self._running or self._unsupported:
            return
        self._schedule_reconnect()

    def _on_ws_error(self, *_args) -> None:
        self._handle_ws_failure("WebSocket 连接出错")

    def _on_ws_connect_timeout(self) -> None:
        if not self._running:
            return
        self._handle_ws_failure("WebSocket 握手超时（%d 秒）" % (CONNECT_TIMEOUT_MS // 1000))

    def _handle_ws_failure(self, message: str) -> None:
        """WS 失败统一处理：退避重连，并在连续失败后启用轮询兜底。

        注意这里**不再**尝试「不校验证书再连一次」：那样等于让公告通道
        接受任意证书。证书有问题时就直接用轮询（轮询完全校验证书）。
        """
        if not self._running or self._unsupported:
            return
        self._last_error = str(message or "WebSocket 连接失败")
        self._close_ws()
        self._ws_failures += 1
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """重连：1s → 2s → 4s … 封顶 60s。

        WS 连续失败几次之后**同时**开一路 20 秒轮询（先用轮询拿数据），
        WS 还在后台按退避继续重试；重连成功后自动停掉轮询、回到实时通道。
        """
        if not self._running:
            return
        if self._ws_failures >= WS_FAILURES_BEFORE_POLL and not self._polling:
            self._start_polling()
            QTimer.singleShot(200, self.poll_once)
        try:
            self._retry_timer.start(max(BACKOFF_START_MS, int(self._backoff_ms)))
        except Exception:
            pass
        self._backoff_ms = min(BACKOFF_MAX_MS, int(self._backoff_ms) * 2)
        if not self._polling:
            self._set_mode("offline")

    def _fallback_to_poll(self) -> None:
        """彻底没有 WebSocket 可用（建不出来）：只走 20 秒轮询。"""
        if self._polling:
            return
        self._polling = True
        self._close_ws()
        if not self._running:
            return
        self._start_polling()
        QTimer.singleShot(200, self.poll_once)

    def _on_ws_text(self, message: str) -> None:
        try:
            payload = json.loads(str(message or ""))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        kind = str(payload.get("type") or "")
        if kind == "hello":
            self._set_mode("ws")
            # 连上后补一次：断线期间漏掉的公告
            QTimer.singleShot(250, self.poll_once)
            return
        if kind == "ping":
            return
        if kind == "announcement":
            item = payload.get("data")
            if isinstance(item, dict):
                added = save_announcements(self.store, [item])
                if added:
                    try:
                        self.new_announcements.emit(added)
                    except Exception:
                        pass

    # -------------------------------------------------- 轮询
    def _start_polling(self) -> None:
        if not self._running:
            return
        try:
            self._poll_timer.start()
        except Exception:
            pass
        self._polling = True
        if self._mode != "ws":
            self._set_mode("poll")

    def poll_once(self) -> None:
        """立刻拉一次；返回结果 dict（方便脚本 / 测试直接调）。"""
        if not self._running:
            return None
        result = account.announcements(last_announcement_id(self.store), self.store)
        if not result.get("ok"):
            code = str(result.get("code") or "")
            self._last_error = str(result.get("error") or "")
            if code == "not_found" or int(result.get("status") or 0) == 404:
                # 服务器这个版本还没有公告接口：停掉轮询，别每 20 秒报一次错
                self._unsupported = True
                try:
                    self._poll_timer.stop()
                except Exception:
                    pass
                self._set_mode("unsupported")
            elif result.get("offline") or code in ("offline", "tls_error"):
                self._set_mode("offline")
            elif code in ("no_session", "unauthorized"):
                self.stop()
            return result
        items = result.get("items") or []
        added = save_announcements(self.store, items)
        if added:
            try:
                self.new_announcements.emit(added)
            except Exception:
                pass
        if self._mode not in ("ws",):
            self._set_mode("poll")
        return result

    def fetch_once(self) -> Optional[Dict[str, Any]]:
        """不管有没有在跑都拉一次（登录完成后 / 手动刷新用）。"""
        result = account.announcements(last_announcement_id(self.store), self.store)
        if result.get("ok"):
            added = save_announcements(self.store, result.get("items") or [])
            if added:
                try:
                    self.new_announcements.emit(added)
                except Exception:
                    pass
        return result
