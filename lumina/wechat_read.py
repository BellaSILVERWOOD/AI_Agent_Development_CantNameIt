"""直接读取微信：截当前聊天窗口 → Windows OCR 识别文字（不需要装任何第三方库）。

为什么要 OCR：微信 4.x（进程 `Weixin.exe`，Qt + `MMUIRenderSubWindow` 自绘）在
UI Automation 里只有两个空 Pane，读不到任何文字，所以走「截图 + 系统自带 OCR」。

流程：
    1. `find_window()`：按进程名找微信主窗口；
    2. `capture()`：PrintWindow 截窗口（窗口在后台也能截到，不会抢焦点）；
    3. 裁掉左边的会话列表、顶部标题、底部输入框，只留消息区域，再放大 2 倍；
    4. 交给 PowerShell 调 Windows.Media.Ocr（zh-Hans）识别，结果写到 UTF-8 文本；
    5. `normalize()` 去掉中文之间的空格、丢掉 UI 噪声行。

对外的接口：
    available()          -> bool    有没有 PowerShell + 中文 OCR 引擎
    grab_current_chat()  -> str     当前打开的聊天里的文字（失败抛 RuntimeError）
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

# ------------------------------------------------------------------ OCR 脚本
# 注意：脚本通过 -EncodedCommand（UTF-16LE + base64）传给 PowerShell，
# 所以这里写中文注释不会有编码问题。
OCR_SCRIPT = r"""
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $resultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($resultType)
    $task = $asTask.Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}
$engine = $null
foreach ($tag in @("zh-Hans-CN", "zh-Hans", "en-US")) {
    try {
        $lang = New-Object Windows.Globalization.Language $tag
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
    } catch { $engine = $null }
    if ($null -ne $engine) { break }
}
if ($null -eq $engine) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if ($null -eq $engine) { Write-Output "NO_ENGINE"; exit 2 }
$png = $env:LUMINA_WECHAT_PNG
$txt = $env:LUMINA_WECHAT_TXT
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($png)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = New-Object System.Collections.Generic.List[string]
foreach ($line in $result.Lines) { $lines.Add($line.Text) }
[System.IO.File]::WriteAllText($txt, ($lines -join "`n"), (New-Object System.Text.UTF8Encoding $false))
$stream.Dispose()
Write-Output ("OK " + $lines.Count)
"""

PROCESS_HINTS = ("Weixin", "WeChat")
WINDOW_HINTS = ("微信", "Weixin", "WeChat")

# 这些是界面上的固定文案，OCR 会读到但没用
NOISE = (
    "search", "hide pinned chats", "hold mouse to input by voice", "send",
    "todolist", "favorites", "file transfer", "contacts", "moments", "channels",
    "微信", "通讯录", "收藏", "小程序", "文件传输助手", "朋友圈", "视频号", "搜索",
)

_CACHE: dict = {"available": None, "ocr_engine_ok": None}


# ------------------------------------------------------------------ 基础
def _powershell() -> str:
    for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    fallback = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(fallback) if fallback.exists() else ""


def available() -> bool:
    """能不能直读（PowerShell + Windows OCR）。结论缓存，不每次都试。"""
    if _CACHE["available"] is not None:
        return bool(_CACHE["available"])
    ok = bool(_powershell())
    _CACHE["available"] = ok
    return ok


def _dpi_scale(hwnd: int) -> float:
    try:
        import ctypes

        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        if dpi:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return 1.0


# ------------------------------------------------------------------ 找窗口
def find_window() -> int:
    """微信主窗口句柄（Weixin.exe 4.x 或老版 WeChat.exe）。"""
    try:
        import win32gui
        import win32process
    except Exception:
        return 0
    found: list = []

    def collect(handle, _param):
        try:
            if not win32gui.IsWindowVisible(handle):
                return True
            left, top, right, bottom = win32gui.GetWindowRect(handle)
            if right - left < 300 or bottom - top < 300:
                return True
            title = win32gui.GetWindowText(handle) or ""
            class_name = win32gui.GetClassName(handle) or ""
            if not any(hint in title for hint in WINDOW_HINTS):
                return True
            _, pid = win32process.GetWindowThreadProcessId(handle)
            process = _process_name(pid)
            if not any(hint.lower() in process.lower() for hint in PROCESS_HINTS):
                return True
            found.append((handle, title, class_name, pid))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        return 0
    if not found:
        return 0
    # 优先 Qt 主窗口
    for handle, _title, class_name, _pid in found:
        if class_name.startswith("Qt"):
            return int(handle)
    return int(found[0][0])


def _process_name(pid: int) -> str:
    try:
        import win32process

        handle = win32process.OpenProcess(0x1000, False, pid)
        try:
            return win32process.GetModuleFileNameEx(handle, 0).split("\\")[-1]
        finally:
            win32process.CloseHandle(handle)
    except Exception:
        try:
            import ctypes
            import ctypes.wintypes

            PROCESS_QUERY_LIMITED = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
            if not handle:
                return ""
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.wintypes.DWORD(1024)
            kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            kernel32.CloseHandle(handle)
            return buf.value.split("\\")[-1]
        except Exception:
            return ""


# ------------------------------------------------------------------ 截图
def _is_foreground(hwnd: int) -> bool:
    try:
        import ctypes

        return ctypes.windll.user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _grab_window_bitmap(hwnd: int):
    """窗口在前台就直接截屏（最干净）；在后台用 PrintWindow（不抢焦点）。"""
    import ctypes

    import win32gui
    import win32ui
    from PIL import Image, ImageGrab

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise RuntimeError("微信窗口尺寸异常")

    if _is_foreground(hwnd):
        try:
            shot = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
            if shot is not None and shot.size[0] >= width - 4:
                return shot.convert("RGB")
        except Exception:
            pass

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(hwnd_dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    memory.SelectObject(bitmap)
    try:
        ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2)
        info = bitmap.GetInfo()
        return Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    finally:
        try:
            win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def capture(hwnd: int, path: Path, crop: bool = True) -> Tuple[int, int]:
    """截窗口并只留消息区域，返回裁剪后的图片尺寸。"""
    from PIL import Image

    image = _grab_window_bitmap(hwnd)
    width, height = image.size
    if crop:
        scale = _dpi_scale(hwnd)
        # 左边是会话列表（微信 4 里约 340 逻辑像素宽），顶部标题，底部输入框
        x0 = min(int(340 * scale) + 6, max(0, width - 120))
        y0 = min(int(52 * scale), max(0, height - 160))
        y1 = max(y0 + 80, height - int(62 * scale))
        image = image.crop((x0, y0, width, y1))
        # 放大 2 倍，OCR 对小字更稳
        image = image.resize((image.width * 2, image.height * 2), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return image.size


# ------------------------------------------------------------------ OCR
def _clean_error(raw: str) -> str:
    """PowerShell 的 stderr 是 CLIXML，挑出人能看懂的部分。"""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = (text.replace("_x000D_", " ").replace("_x000A_", " ")
                .replace("\ufffd", ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200]


def _run_ocr(png: Path, out: Path) -> str:
    powershell = _powershell()
    if not powershell:
        raise RuntimeError("找不到 PowerShell")
    if not png.exists():
        raise RuntimeError("截图文件不存在")
    # 路径通过环境变量传：PowerShell 会把 ‘’“” 当成引号，直接用字面量会踩坑
    env = dict(os.environ)
    env["LUMINA_WECHAT_PNG"] = str(png)
    env["LUMINA_WECHAT_TXT"] = str(out)
    encoded = base64.b64encode(OCR_SCRIPT.encode("utf-16-le")).decode("ascii")
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        finished = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, timeout=90, creationflags=flags, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError("OCR 超时")
    output = (finished.stdout or b"").decode("utf-8", "replace").strip()
    if "NO_ENGINE" in output:
        raise RuntimeError(
            "系统没有中文 OCR 引擎（设置 → 时间和语言 → 语言 → 中文 → 可选功能里的「光学字符识别」）")
    if not out.exists():
        detail = _clean_error((finished.stderr or b"").decode("utf-8", "replace"))
        raise RuntimeError("OCR 失败：" + (detail or output or "未知错误"))
    _CACHE["ocr_engine_ok"] = True
    return out.read_text(encoding="utf-8", errors="replace")


# ------------------------------------------------------------------ 清洗
_CJK = r"\u3400-\u9fff\u3000-\u303f\uff00-\uffef"
_SPACE_BETWEEN_CJK = re.compile(r"(?<=[" + _CJK + r"])\s+(?=[" + _CJK + r"])")
_TIME_ONLY = re.compile(r"^[\d:：\s年月日/\-\.]+$")
_CJK_CHAR = re.compile(r"[" + _CJK + r"]")
# 屏幕上别的窗口压过来时会 OCR 到这些（例如某个用量面板）
_JUNK_WORDS = ("缓存命中", "未缓存命中", "缓存读取", "token", "输出", "input tokens",
               "output tokens", "消耗", "余额", "thinking")


def _mostly_garbage(line: str) -> bool:
    """汉字太少、又不像一句人话的，直接丢掉（OCR 噪声）。"""
    cjk = len(_CJK_CHAR.findall(line))
    if cjk >= 2:
        return False
    letters = sum(1 for ch in line if ch.isalpha() and ord(ch) < 128)
    # 纯英文短语（比如群名）保留一点，但要够长
    return not (letters >= 4 and " " not in line.strip())


def normalize(text: str) -> str:
    """去掉中文之间被 OCR 插进去的空格，丢掉界面噪声行。"""
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = _SPACE_BETWEEN_CJK.sub("", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        lowered = line.lower()
        if lowered in NOISE:
            continue
        if any(word in lowered for word in _JUNK_WORDS):
            continue
        if _TIME_ONLY.match(line):
            continue
        if len(line) <= 1:
            continue
        if _mostly_garbage(line):
            continue
        lines.append(line)
    return "\n".join(lines)


# ------------------------------------------------------------------ 入口
def grab_current_chat(crop: bool = True, keep: bool = False) -> str:
    """截当前微信聊天窗口并识别成文字（窗口在后台也能用，不抢焦点）。"""
    if not available():
        raise RuntimeError("这台机器没有可用的 PowerShell / OCR")
    hwnd = find_window()
    if not hwnd:
        raise RuntimeError("没找到微信窗口（微信要先打开）")
    tmp_dir = Path(tempfile.gettempdir())
    stamp = int(time.time() * 1000)
    png = tmp_dir / f"lumina-wechat-{stamp}.png"
    out = tmp_dir / f"lumina-wechat-{stamp}.txt"
    try:
        capture(hwnd, png, crop=crop)
        raw = _run_ocr(png, out)
    finally:
        if not keep:
            for path in (png, out):
                try:
                    path.unlink()
                except Exception:
                    pass
    text = normalize(raw)
    if not text.strip():
        raise RuntimeError("截到图了，但没识别出文字（聊天窗口是不是空着？）")
    return text
