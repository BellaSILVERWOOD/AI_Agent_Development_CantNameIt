"""Windows 系统定位（Geolocator）。

用的是 PowerShell 5.1 的 WinRT 互操作（PowerShell 7 不支持），整套在隐藏窗口里跑。
Windows 11 上默认「定位服务」是关的，关着的时候会抛 UnauthorizedAccessException，
这里统一翻译成人话。
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

from . import proc
from .wifi_scan import LOCATION_SETTINGS_URI

_SCRIPT = r"""
$ErrorActionPreference = "Stop"
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
  [void][Windows.Devices.Geolocation.Geolocator, Windows.Devices.Geolocation, ContentType=WindowsRuntime]
  $geo = New-Object Windows.Devices.Geolocation.Geolocator
  $geo.DesiredAccuracyInMeters = 20
  $op = $geo.GetGeopositionAsync()
  $ext = [System.WindowsRuntimeSystemExtensions]
  $asTask = ($ext.GetMethods() | Where-Object {
      $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
      $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  $task = $asTask.MakeGenericMethod([Windows.Devices.Geolocation.Geoposition]).Invoke($null, @($op))
  try { $task.Wait(8000) | Out-Null } catch {
    $e = $_.Exception
    while ($e.InnerException) { $e = $e.InnerException }
    "@@ERR@@" + $e.GetType().FullName + "|" + $e.Message
    exit 0
  }
  $p = $task.Result
  "@@OK@@" + $p.Coordinate.Point.Position.Latitude + "|" +
            $p.Coordinate.Point.Position.Longitude + "|" +
            $p.Coordinate.Accuracy + "|" + $p.Coordinate.PositionSource
} catch {
  "@@ERR@@" + $_.Exception.GetType().FullName + "|" + $_.Exception.Message
}
"""


def available() -> bool:
    return sys.platform == "win32"


def position(timeout: float = 20.0) -> Dict[str, Any]:
    """返回 {"ok":bool, "lat","lon","accuracy_m","source","error","denied"}。"""
    if not available():
        return {"ok": False, "error": "只有 Windows 支持系统定位", "denied": False}
    try:
        completed = proc.run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"调用系统定位失败：{exc}", "denied": False}
    text = (getattr(completed, "stdout", "") or "").strip()
    for line in text.splitlines():
        if line.startswith("@@OK@@"):
            parts = line[6:].split("|")
            try:
                return {"ok": True, "lat": float(parts[0]), "lon": float(parts[1]),
                        "accuracy_m": float(parts[2]),
                        "source": parts[3] if len(parts) > 3 else "windows",
                        "error": "", "denied": False}
            except Exception:
                return {"ok": False, "error": "系统定位返回的数据看不懂", "denied": False}
        if line.startswith("@@ERR@@"):
            detail = line[7:]
            kind = detail.split("|", 1)[0]
            message = detail.split("|", 1)[1] if "|" in detail else detail
            denied = "UnauthorizedAccess" in kind or "拒绝访问" in message
            if denied:
                return {"ok": False, "denied": True,
                        "error": ("Windows 定位服务是关的，所以拿不到坐标。\n"
                                  "设置 → 隐私和安全性 → 定位 → 打开「定位服务」")
                        + f"\n可以点下面的按钮直接打开：{LOCATION_SETTINGS_URI}"}
            return {"ok": False, "error": f"{message}", "denied": False}
    return {"ok": False, "error": "系统定位没有返回结果（可能超时）", "denied": False}


def as_json() -> str:
    return json.dumps(position(), ensure_ascii=False)
