"""Wi-Fi 扫描：用 wlanapi.dll 读周围 AP 的 BSSID 和信号强度，用来做指纹定位。

Windows 11 把「扫 Wi-Fi」也算作敏感数据：**没开定位服务时**
`WlanGetNetworkBssList` 直接返回 ERROR_ACCESS_DENIED(5)，所以要给用户明确提示。

对外接口：
    visible_networks()   -> List[{"ssid", "bssid", "signal"}]   # signal 0-100
    current_connection() -> Optional[{"ssid", "bssid", "signal"}]
    permission_message() -> str      # 给人看的解释
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Dict, List, Optional

ERROR_SUCCESS = 0
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87

LOCATION_SETTINGS_URI = "ms-settings:privacy-location"


class WifiError(RuntimeError):
    """扫描失败，message 里是能直接给用户看的说明。"""

    def __init__(self, message: str, denied: bool = False):
        super().__init__(message)
        self.denied = denied


def permission_message() -> str:
    return ("Windows 把 Wi-Fi 扫描算作位置数据：需要打开「定位服务」并允许桌面应用访问。\n"
            "设置 → 隐私和安全性 → 定位 → 打开「定位服务」和「让桌面应用访问你的位置」。\n"
            f"也可以直接打开：{LOCATION_SETTINGS_URI}")


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class _DOT11_SSID(ctypes.Structure):
    _fields_ = [("uSSIDLength", ctypes.c_ulong), ("ucSSID", ctypes.c_ubyte * 32)]


class _WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [("InterfaceGuid", _GUID),
                ("strInterfaceDescription", ctypes.c_wchar * 256),
                ("isState", ctypes.c_uint)]


class _WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [("dwNumberOfItems", ctypes.c_ulong), ("dwIndex", ctypes.c_ulong),
                ("InterfaceInfo", _WLAN_INTERFACE_INFO * 1)]


class _WLAN_BSS_ENTRY(ctypes.Structure):
    _fields_ = [
        ("dot11Ssid", _DOT11_SSID),
        ("uPhyId", ctypes.c_ulong),
        ("dot11Bssid", ctypes.c_ubyte * 6),
        ("dot11BssType", ctypes.c_uint),
        ("dot11PhyType", ctypes.c_uint),
        ("uDot11PhyIndex", ctypes.c_ulong),
        ("wlanSignalQuality", ctypes.c_ulong),
        ("ulRxRate", ctypes.c_ulong),
        ("ulTxRate", ctypes.c_ulong),
    ]


class _WLAN_BSS_LIST(ctypes.Structure):
    _fields_ = [("dwTotalSize", ctypes.c_ulong),
                ("dwNumberOfItems", ctypes.c_ulong),
                ("wlanBssEntries", _WLAN_BSS_ENTRY * 1)]


def _open_handle():
    if sys.platform != "win32":
        raise WifiError("只有 Windows 支持 Wi-Fi 扫描定位")
    try:
        wlanapi = ctypes.windll.wlanapi
    except Exception as exc:  # noqa: BLE001
        raise WifiError(f"无法加载 wlanapi：{exc}") from exc
    handle = wintypes.HANDLE()
    negotiated = wintypes.DWORD()
    rc = wlanapi.WlanOpenHandle(2, None, ctypes.byref(negotiated), ctypes.byref(handle))
    if rc == ERROR_ACCESS_DENIED:
        raise WifiError(permission_message(), denied=True)
    if rc != ERROR_SUCCESS:
        raise WifiError(f"打开 WLAN 服务失败（错误码 {rc}），检查一下 Wi-Fi 是否开启")
    return wlanapi, handle


def _interface_guid(wlanapi, handle):
    listing = ctypes.POINTER(_WLAN_INTERFACE_INFO_LIST)()
    rc = wlanapi.WlanEnumInterfaces(handle, None, ctypes.byref(listing))
    if rc != ERROR_SUCCESS:
        raise WifiError(f"读取无线网卡失败（错误码 {rc}）")
    try:
        count = listing.contents.dwNumberOfItems
        if count <= 0:
            raise WifiError("没找到无线网卡，先确认 Wi-Fi 是开着的")
        return listing.contents.InterfaceInfo[0].InterfaceGuid
    finally:
        wlanapi.WlanFreeMemory(listing)


def _mac(raw) -> str:
    return ":".join("%02x" % byte for byte in raw)


def visible_networks() -> List[Dict[str, object]]:
    """扫描周围 AP。没有定位权限会抛 WifiError(denied=True)。"""
    wlanapi, handle = _open_handle()
    try:
        guid = _interface_guid(wlanapi, handle)
        result = ctypes.POINTER(_WLAN_BSS_LIST)()
        rc = wlanapi.WlanGetNetworkBssList(
            handle, ctypes.byref(guid), None, 3, False, None, ctypes.byref(result))
        if rc == ERROR_ACCESS_DENIED:
            raise WifiError(permission_message(), denied=True)
        if rc == ERROR_INVALID_PARAMETER:
            raise WifiError("无线网卡不在工作状态（可能没连上 Wi-Fi）")
        if rc != ERROR_SUCCESS:
            raise WifiError(f"扫描 Wi-Fi 失败（错误码 {rc}）")
        networks: List[Dict[str, object]] = []
        try:
            count = result.contents.dwNumberOfItems
            for index in range(count):
                entry = result.contents.wlanBssEntries[index]
                length = entry.dot11Ssid.uSSIDLength
                raw = bytes(entry.dot11Ssid.ucSSID[:max(0, min(32, length))])
                networks.append({
                    "ssid": raw.decode("utf-8", "replace"),
                    "bssid": _mac(entry.dot11Bssid),
                    "signal": int(entry.wlanSignalQuality),
                })
        finally:
            wlanapi.WlanFreeMemory(result)
        networks.sort(key=lambda item: -int(item["signal"]))
        return networks
    finally:
        try:
            wlanapi.WlanCloseHandle(handle, None)
        except Exception:
            pass


def current_connection() -> Optional[Dict[str, object]]:
    """当前连着的那个 AP（同样需要定位权限）。"""
    try:
        networks = visible_networks()
    except WifiError:
        raise
    return networks[0] if networks else None


def scan_summary() -> Dict[str, object]:
    """给设置页用：要么返回扫描结果，要么返回错误说明，不抛异常。"""
    try:
        networks = visible_networks()
        return {"ok": True, "networks": networks, "count": len(networks)}
    except WifiError as exc:
        return {"ok": False, "error": str(exc), "denied": bool(getattr(exc, "denied", False))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "denied": False}
