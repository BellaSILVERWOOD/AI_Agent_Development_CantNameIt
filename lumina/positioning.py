"""定位：Wi-Fi 指纹校准 + 系统定位 + IP 兜底，目标是「米级」而不是「城市级」。

为什么不直接用系统定位
----------------------
Windows 的 Geolocator 精度一般 20–50 m，而且桌面程序默认没有权限（定位服务关着就拒）。
要做到米级，最靠谱的是**Wi-Fi 指纹**：同一个位置周围 AP 的 BSSID 集合 + 信号强度几乎是唯一的，
校准一次以后，站到同一个地方就能认出来。

数据表（sqlite）
----------------
loc_points(id, name, lat, lon, note, source, created_at)
loc_prints(point_id, bssid, ssid, signal)

对外接口
--------
status(store)                       -> 当前定位能力/权限状态
capture(store)                      -> 现在可见的指纹 {bssid: signal}
calibrate(store, name, lat, lon)    -> 把当前指纹存成一个校准点
locate(store)                       -> 匹配出来的点（含置信度/估计误差）
resolve(store)                      -> 最终给业务用的位置（指纹 > 系统 > IP > 默认）
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import geo, location_win, wifi_scan
from .utils import open_external

MIN_MATCH = 2          # 至少命中 2 个 AP 才算认得出来
MIN_SCORE = 0.42       # 归一化相似度阈值
GOOD_MATCH = 4         # 命中这么多基本就在同一间屋子


# ------------------------------------------------------------------ 表结构
def ensure_schema(store) -> None:
    try:
        store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS loc_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'wifi',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS loc_prints (
                point_id INTEGER NOT NULL,
                bssid TEXT NOT NULL,
                ssid TEXT NOT NULL DEFAULT '',
                signal INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (point_id, bssid)
            );
            """
        )
        store.conn.commit()
    except Exception:
        pass


# ------------------------------------------------------------------ 采集
def capture(store) -> Dict[str, Any]:
    """扫一次 Wi-Fi，返回 {"ok", "prints", "error", "denied"}。"""
    ensure_schema(store)
    try:
        networks = wifi_scan.visible_networks()
    except wifi_scan.WifiError as exc:
        return {"ok": False, "prints": {}, "error": str(exc),
                "denied": bool(getattr(exc, "denied", False))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "prints": {}, "error": str(exc), "denied": False}
    prints = {str(item["bssid"]): int(item.get("signal") or 0) for item in networks
              if item.get("bssid")}
    return {"ok": True, "prints": prints, "networks": networks, "error": "", "denied": False}


# ------------------------------------------------------------------ 校准
def calibrate(store, name: str, lat: float, lon: float,
              note: str = "") -> Dict[str, Any]:
    """把当前位置的 Wi-Fi 指纹存成一个校准点。"""
    ensure_schema(store)
    name = (name or "").strip() or "未命名地点"
    captured = capture(store)
    if not captured.get("ok"):
        return {"ok": False, "error": captured.get("error") or "扫描失败",
                "denied": bool(captured.get("denied"))}
    prints = captured.get("prints") or {}
    if not prints:
        return {"ok": False, "error": "没有扫到任何 Wi-Fi，先确认 Wi-Fi 开着"}
    stamp = datetime.now().isoformat(timespec="seconds")
    cur = store.conn.execute(
        "INSERT INTO loc_points(name, lat, lon, note, source, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (name, float(lat), float(lon), note or "", "wifi", stamp))
    point_id = int(cur.lastrowid)
    for bssid, signal in prints.items():
        store.conn.execute(
            "INSERT OR REPLACE INTO loc_prints(point_id, bssid, ssid, signal)"
            " VALUES (?,?,?,?)", (point_id, bssid, "", int(signal)))
    store.conn.commit()
    return {"ok": True, "point_id": point_id, "prints": len(prints), "name": name}


def points(store) -> List[Dict[str, Any]]:
    ensure_schema(store)
    rows = store.conn.execute(
        "SELECT p.id, p.name, p.lat, p.lon, p.note, p.created_at,"
        " COUNT(f.bssid) AS aps FROM loc_points p"
        " LEFT JOIN loc_prints f ON f.point_id = p.id"
        " GROUP BY p.id ORDER BY p.id DESC").fetchall()
    return [dict(row) for row in rows]


def delete_point(store, point_id: int) -> None:
    ensure_schema(store)
    store.conn.execute("DELETE FROM loc_prints WHERE point_id = ?", (int(point_id),))
    store.conn.execute("DELETE FROM loc_points WHERE id = ?", (int(point_id),))
    store.conn.commit()


def _prints_of(store, point_id: int) -> Dict[str, int]:
    rows = store.conn.execute(
        "SELECT bssid, signal FROM loc_prints WHERE point_id = ?",
        (int(point_id),)).fetchall()
    return {str(row["bssid"]): int(row["signal"]) for row in rows}


# ------------------------------------------------------------------ 匹配
def score(now: Dict[str, int], stored: Dict[str, int]) -> Tuple[float, int]:
    """余弦式相似度：命中越多、信号越像，分数越高（0..1）。"""
    if not now or not stored:
        return 0.0, 0
    common = set(now) & set(stored)
    if not common:
        return 0.0, 0
    total = 0.0
    for bssid in common:
        diff = abs(now[bssid] - stored[bssid])
        total += max(0.0, 1.0 - diff / 60.0)
    denominator = math.sqrt(len(now) * len(stored))
    return total / denominator if denominator else 0.0, len(common)


def locate(store) -> Dict[str, Any]:
    """当前指纹匹配结果：{"ok","name","lat","lon","accuracy_m","score","matched"}。"""
    ensure_schema(store)
    captured = capture(store)
    if not captured.get("ok"):
        return {"ok": False, "error": captured.get("error") or "扫描失败",
                "denied": bool(captured.get("denied"))}
    now = captured.get("prints") or {}
    best: Optional[Dict[str, Any]] = None
    for point in points(store):
        value, matched = score(now, _prints_of(store, point["id"]))
        if matched < MIN_MATCH:
            continue
        # 只命中 2 个 AP 时要求更像一些，免得认错屋子
        threshold = MIN_SCORE if matched >= 3 else 0.60
        if value < threshold:
            continue
        if best is None or value > best["score"]:
            accuracy = 8.0 if matched >= GOOD_MATCH else (15.0 if matched >= 3 else 25.0)
            best = {"ok": True, "name": point["name"], "lat": point["lat"],
                    "lon": point["lon"], "score": round(value, 3), "matched": matched,
                    "accuracy_m": accuracy, "source": "wifi", "error": ""}
    if best is None:
        return {"ok": False, "error": "没有匹配到已校准的位置（可以先在设置里校准一次）",
                "denied": False, "seen": len(now)}
    return best


# ------------------------------------------------------------------ 综合
def status(store) -> Dict[str, Any]:
    ensure_schema(store)
    scan = wifi_scan.scan_summary()
    saved = points(store)
    win = location_win.position(timeout=18.0) if location_win.available() else {
        "ok": False, "error": "非 Windows 平台"}
    return {
        "wifi_ok": bool(scan.get("ok")),
        "wifi_error": scan.get("error") or "",
        "wifi_denied": bool(scan.get("denied")),
        "wifi_count": int(scan.get("count") or 0),
        "points": saved,
        "windows_ok": bool(win.get("ok")),
        "windows_error": win.get("error") or "",
        "windows_lat": win.get("lat"),
        "windows_lon": win.get("lon"),
        "windows_accuracy": win.get("accuracy_m"),
        "settings_uri": wifi_scan.LOCATION_SETTINGS_URI,
    }


def resolve(store) -> Dict[str, Any]:
    """给出行提醒用的当前位置：指纹 → 系统定位 → IP → 默认。"""
    mode = (store.get_setting("location_mode", "auto") or "auto").strip()
    if mode == "manual":
        base = geo.resolve_location(store)
        base["source"] = base.get("source") or "manual"
        return base

    fix = locate(store)
    if fix.get("ok"):
        return {"lat": fix["lat"], "lon": fix["lon"], "name": fix["name"],
                "accuracy_m": float(fix.get("accuracy_m") or 20.0), "source": "wifi",
                "score": fix.get("score"), "matched": fix.get("matched")}

    perm = (store.get_setting("location_windows", "1") or "1") != "0"
    if perm:
        win = location_win.position(timeout=18.0)
        if win.get("ok"):
            nearest = geo.nearest_place(win["lat"], win["lon"])
            return {"lat": win["lat"], "lon": win["lon"],
                    "name": (nearest or {}).get("name") or "系统定位",
                    "accuracy_m": float(win.get("accuracy_m") or 50.0),
                    "source": "windows"}
    base = geo.resolve_location(store)
    if fix.get("error"):
        base["hint"] = fix["error"]
    return base


def open_settings() -> None:
    """打开 Windows 的定位设置页。"""
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        open_external(wifi_scan.LOCATION_SETTINGS_URI)
    except Exception:
        pass
