"""定位与出行估算：当前位置、教学楼坐标、步行/骑行距离与时间。

定位策略（按可靠性排序）
------------------------
1. 手动地点：设置里选一个常用地点（宿舍、食堂、图书馆…），最可靠；
2. IP 定位：请求 ip-api.com，城市级精度（~10km），只能判断「在不在校内」；
3. Windows 系统定位：未打包的桌面程序会被拒绝（0x80070005），仅留接口不做默认。

上海校区建筑坐标是按公开地图估的近似值（误差几百米），可在设置里校准。
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple

# ------------------------------------------------------------------ 建筑坐标
# (id, 名称, 别名关键词, 纬度, 经度, 说明)
CAMPUS_PLACES: List[Dict[str, Any]] = [
    {"id": "guanghua", "name": "光华楼（东主楼）", "aliases": ["H3", "H2", "H4", "光华楼东"],
     "lat": 31.3022, "lon": 121.5030, "note": "邯郸校区中心，H3401 等教室"},
    {"id": "guanghua_w", "name": "光华楼西辅楼", "aliases": ["HGX", "光华楼西"],
     "lat": 31.3020, "lon": 121.5018, "note": "HGX302 等教室"},
    {"id": "building6", "name": "第六教学楼", "aliases": ["H6", "六教", "H6101"],
     "lat": 31.3040, "lon": 121.5043, "note": "靠近国年路"},
    {"id": "building3", "name": "第三教学楼", "aliases": ["H3教", "三教"],
     "lat": 31.2999, "lon": 121.5053, "note": ""},
    {"id": "library", "name": "文科图书馆", "aliases": ["图书", "图书馆"],
     "lat": 31.3013, "lon": 121.5022, "note": ""},
    {"id": "south_area", "name": "南区（学生公寓/球场）", "aliases": ["南区", "南苑"],
     "lat": 31.2963, "lon": 121.5004, "note": "南区排球场等"},
    {"id": "north_area", "name": "北区（学生公寓）", "aliases": ["北区", "北苑"],
     "lat": 31.3058, "lon": 121.4995, "note": ""},
    {"id": "east_area", "name": "东区", "aliases": ["东区", "东苑"],
     "lat": 31.3012, "lon": 121.5078, "note": ""},
    {"id": "canteen", "name": "本部食堂", "aliases": ["食堂", "本部食堂"],
     "lat": 31.3008, "lon": 121.5012, "note": ""},
    {"id": "gate", "name": "正门（邯郸路 220 号）", "aliases": ["正门", "邯郸路"],
     "lat": 31.3005, "lon": 121.5029, "note": ""},
]

CAMPUS_CENTER = (31.3006, 121.5030)

# 出行方式默认速度（米/分钟）与绕行系数
TRAVEL_MODES = {
    "walk": {"name": "步行", "speed": 80.0, "factor": 1.35},
    "bike": {"name": "骑行", "speed": 200.0, "factor": 1.20},
}


def place_by_id(place_id: str) -> Optional[Dict[str, Any]]:
    for place in CAMPUS_PLACES:
        if place["id"] == place_id:
            return dict(place)
    return None


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点球面距离（米）。"""
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(a))


def match_place(text: str) -> Optional[Dict[str, Any]]:
    """从教室名（如 H3401 / HGX302 / 南区排球场）猜出建筑。"""
    text = (text or "").strip()
    if not text:
        return None
    upper = text.upper()
    # 先看别名（长的优先，避免 H3 抢先匹配 HGX）
    best: Optional[Dict[str, Any]] = None
    best_len = 0
    for place in CAMPUS_PLACES:
        for alias in place["aliases"]:
            alias_upper = alias.upper()
            if not alias_upper:
                continue
            if alias_upper in upper and len(alias_upper) > best_len:
                best, best_len = place, len(alias_upper)
    return dict(best) if best else None


def parse_location_from_description(description: str) -> str:
    """从日程描述里取出「地点：xxx」。"""
    for line in (description or "").splitlines():
        line = line.strip()
        for prefix in ("地点：", "地点:", "教室：", "教室:"):
            if line.startswith(prefix):
                return line[len(prefix):].strip()
    return ""


# ------------------------------------------------------------------ IP 定位
_ip_cache: Dict[str, Any] = {"at": 0.0, "value": None}
IP_CACHE_SECONDS = 600


def ip_location(timeout: float = 4.0) -> Optional[Dict[str, Any]]:
    """用 IP 粗略定位，返回 {lat, lon, city, accuracy_m, source}。"""
    now = time.time()
    cached = _ip_cache.get("value")
    if cached and now - float(_ip_cache.get("at") or 0) < IP_CACHE_SECONDS:
        return cached
    try:
        import requests
    except Exception:
        return None
    try:
        response = requests.get(
            "http://ip-api.com/json/?fields=status,lat,lon,city,query",
            timeout=timeout,
        )
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    try:
        value = {
            "lat": float(payload["lat"]),
            "lon": float(payload["lon"]),
            "city": str(payload.get("city") or ""),
            "ip": str(payload.get("query") or ""),
            "accuracy_m": 8000.0,
            "source": "ip",
        }
    except (KeyError, TypeError, ValueError):
        return None
    _ip_cache["at"] = now
    _ip_cache["value"] = value
    return value


def on_campus(location: Optional[Dict[str, Any]], radius_m: float = 3000.0) -> bool:
    if not location:
        return False
    return haversine(location.get("lat", 0.0), location.get("lon", 0.0),
                     CAMPUS_CENTER[0], CAMPUS_CENTER[1]) <= radius_m


# ------------------------------------------------------------------ 当前位置
def nearest_place(lat: float, lon: float,
                  limit_m: float = 400.0) -> Optional[Dict[str, Any]]:
    """离这个坐标最近的校园地点（超过 limit_m 就认为不在校园里）。"""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    best = None
    best_distance = float(limit_m)
    for place in CAMPUS_PLACES:
        distance = haversine(lat, lon, place["lat"], place["lon"])
        if distance < best_distance:
            best_distance = distance
            best = dict(place)
    if best is not None:
        best["distance_m"] = best_distance
    return best


def resolve_location(store) -> Dict[str, Any]:
    """按设置解析当前位置。

    location_mode: auto（手动优先、否则 IP）/ manual（只用设置里的地点）/ ip（只用 IP）
    location_place: 手动地点 id，或 custom 配合 location_lat/location_lon
    """
    store_get = getattr(store, "get_setting", None)
    mode = (store_get("location_mode", "auto") if store_get else "auto") or "auto"
    place_id = (store_get("location_place", "south_area") if store_get else "") or ""
    custom_lat = _to_float(store_get("location_lat", "") if store_get else "")
    custom_lon = _to_float(store_get("location_lon", "") if store_get else "")

    manual: Optional[Dict[str, Any]] = None
    if place_id == "custom" and custom_lat is not None and custom_lon is not None:
        manual = {"lat": custom_lat, "lon": custom_lon, "name": "自定义位置",
                  "accuracy_m": 100.0, "source": "manual"}
    elif place_id:
        place = place_by_id(place_id)
        if place:
            manual = {"lat": place["lat"], "lon": place["lon"], "name": place["name"],
                      "accuracy_m": 150.0, "source": "manual"}

    if mode == "manual" and manual:
        return manual
    if mode != "manual":
        ip = ip_location()
        # IP 精度只有城市级；如果在校内，用「校园中心 + IP 判定」兜底
        if ip is not None:
            ip["on_campus"] = on_campus(ip)
            if ip["on_campus"]:
                # 校内时 IP 会落在校园网出口，位置基本没用：退回手动地点
                return manual or {"lat": ip["lat"], "lon": ip["lon"],
                                  "name": ip.get("city") or "校内", "accuracy_m": 8000.0,
                                  "source": "ip"}
            return ip
    if manual:
        return manual
    return {"lat": CAMPUS_CENTER[0], "lon": CAMPUS_CENTER[1], "name": "校园中心（默认）",
            "accuracy_m": 1000.0, "source": "default"}


def current_location(store) -> Dict[str, Any]:
    """业务层用这个：优先 Wi-Fi 指纹 / 系统定位（米级），再退回老策略。"""
    try:
        from . import positioning

        return positioning.resolve(store)
    except Exception:
        return resolve_location(store)


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ 出行估算
def estimate(from_loc: Dict[str, Any], to_place: Dict[str, Any],
             modes: Optional[List[str]] = None,
             speeds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """估算到目标建筑的出行时间。"""
    straight = haversine(from_loc.get("lat", 0.0), from_loc.get("lon", 0.0),
                         to_place.get("lat", 0.0), to_place.get("lon", 0.0))
    result: Dict[str, Any] = {
        "straight_m": round(straight),
        "from": from_loc.get("name", ""),
        "to": to_place.get("name", ""),
        "modes": {},
    }
    for key in (modes or ["walk", "bike"]):
        mode = TRAVEL_MODES.get(key)
        if not mode:
            continue
        speed = float((speeds or {}).get(key) or mode["speed"])
        distance = straight * mode["factor"]
        minutes = max(1, int(round(distance / max(20.0, speed))))
        result["modes"][key] = {
            "name": mode["name"],
            "distance_m": round(distance),
            "minutes": minutes,
        }
    return result


def format_estimate(est: Dict[str, Any]) -> str:
    """拼成「步行 12 分钟（950 米）· 骑行 5 分钟」。"""
    parts = []
    for key in ("walk", "bike"):
        item = (est.get("modes") or {}).get(key)
        if not item:
            continue
        parts.append(f"{item['name']} {item['minutes']} 分钟（{_format_m(item['distance_m'])}）")
    return " · ".join(parts)


def _format_m(distance: float) -> str:
    if distance >= 1000:
        return f"{distance / 1000:.1f} 公里"
    return f"{int(round(distance / 10.0) * 10)} 米"


# ------------------------------------------------------------------ 出发时间
def departure_time(start_hm: str, minutes: float, buffer_min: float = 2.0) -> Optional[str]:
    """上课前多少分钟该出发，返回 HH:MM。"""
    try:
        hour, minute = [int(part) for part in str(start_hm).split(":")[:2]]
    except (ValueError, AttributeError):
        return None
    total = hour * 60 + minute - int(round(minutes + buffer_min))
    if total < 0:
        total = 0
    return f"{total // 60:02d}:{total % 60:02d}"
