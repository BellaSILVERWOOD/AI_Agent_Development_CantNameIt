"""Windows 窗口磨砂（毛玻璃）效果。

通过未公开的 ``SetWindowCompositionAttribute`` 给窗口加上 Acrylic 模糊，
使窗口“半透明”看到的是**模糊后的桌面**，而不是清晰的桌面内容。

- Windows 10 1803 及以上： acrylic（真正的磨砂）
- 更老的系统：退回普通的 blur
- 非 Windows 或调用失败： 静默失败，界面照常显示（保持原来的半透明效果）
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_ACCENT_DISABLED = 0
_ACCENT_ENABLE_BLURBEHIND = 3
_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
_WCA_ACCENT_POLICY = 19


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class _WinCompAttrData(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


_user32 = None


def _load() -> bool:
    global _user32
    if _user32 is not None:
        return True
    if sys.platform != "win32":
        return False
    try:
        _user32 = ctypes.windll.user32
        _user32.SetWindowCompositionAttribute.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(_WinCompAttrData),
        ]
        _user32.SetWindowCompositionAttribute.restype = wintypes.BOOL
        return True
    except Exception:
        _user32 = None
        return False


def available() -> bool:
    """当前系统是否支持（能调用到接口）。"""
    return _load()


def _set_accent(hwnd: int, state: int, gradient: int) -> bool:
    if not _load() or not hwnd:
        return False
    policy = _AccentPolicy(state, 0, gradient, 0)
    data = _WinCompAttrData(
        _WCA_ACCENT_POLICY,
        ctypes.cast(ctypes.byref(policy), ctypes.c_void_p),
        ctypes.sizeof(policy),
    )
    try:
        return bool(_user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:
        return False


def _gradient_color(hex_rgb: str, alpha: int) -> int:
    """把 #RRGGBB + alpha 打包成 0xAABBGGRR。"""
    value = (hex_rgb or "#FFFFFF").lstrip("#")
    try:
        red = int(value[0:2], 16)
        green = int(value[2:4], 16)
        blue = int(value[4:6], 16)
    except Exception:
        red = green = blue = 255
    alpha = max(0, min(255, int(alpha)))
    return (alpha << 24) | (blue << 16) | (green << 8) | red


def apply_glass(hwnd: int, tint_hex: str = "#F6F8F2", tint_alpha: int = 150) -> bool:
    """给窗口加上磨砂效果。``tint_hex`` 是磨砂的底色，``tint_alpha`` 是底色不透明度。"""
    gradient = _gradient_color(tint_hex, tint_alpha)
    if _set_accent(hwnd, _ACCENT_ENABLE_ACRYLICBLURBEHIND, gradient):
        return True
    return _set_accent(hwnd, _ACCENT_ENABLE_BLURBEHIND, gradient)


def clear_glass(hwnd: int) -> bool:
    """关闭磨砂效果。"""
    return _set_accent(hwnd, _ACCENT_DISABLED, 0)


_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DONOTROUND = 1
_DWMWCP_ROUND = 2
_DWMWA_NCRENDERING_POLICY = 2
_DWMNCRP_DISABLED = 1
_DWMNCRP_ENABLED = 2
_WS_EX_LAYERED = 0x00080000
_GWL_EXSTYLE = -20


class _Margins(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def set_round_corners(hwnd: int, prefer_round: bool = True) -> bool:
    """请求系统给窗口做圆角（Windows 11 才有；其它系统静默失败）。

    不透明窗口也能用：圆角由 DWM 裁，鼠标命中判定跟着圆角走，
    所以不会像 setMask 那样把点击判定弄坏。
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        dwm = ctypes.windll.dwmapi
        value = ctypes.c_int(_DWMWCP_ROUND if prefer_round else _DWMWCP_DONOTROUND)
        result = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_int(_DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return result == 0
    except Exception:
        return False


def enable_system_shadow(hwnd: int, enable: bool = True) -> bool:
    """给无边框窗口要一圈系统投影（DWM 画的，不算在自己窗口里）。

    自己画阴影在「不透明窗口」里会在四周留一圈深色带，所以交给 DWM：
    `DwmExtendFrameIntoClientArea(-1)` + 打开非客户区渲染。
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        dwm = ctypes.windll.dwmapi
        margins = _Margins(-1, -1, -1, -1) if enable else _Margins(0, 0, 0, 0)
        ok = dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))
        policy = ctypes.c_int(_DWMNCRP_ENABLED if enable else _DWMNCRP_DISABLED)
        dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_int(_DWMWA_NCRENDERING_POLICY),
            ctypes.byref(policy),
            ctypes.sizeof(policy),
        )
        return ok == 0
    except Exception:
        return False


def clear_layered(hwnd: int) -> bool:
    """把 WS_EX_LAYERED 去掉（不透明窗口不该是分层窗口）。

    什么时候会被加上：启动时的淡入动画用 `setWindowOpacity()`，Qt 会给窗口加
    分层样式，动画结束回到 1.0 之后**不一定**会去掉 —— 这时候窗口就是「分层窗口」，
    而分层窗口 + Chromium 原生子窗口会出现点击穿透。所以动画结束后主动清一次。
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        getter = getattr(user32, "GetWindowLongPtrW", None)
        setter = getattr(user32, "SetWindowLongPtrW", None)
        if getter is None or setter is None:      # 32 位系统
            getter = user32.GetWindowLongW
            setter = user32.SetWindowLongW
        # LONG_PTR：64 位上必须按指针宽度传，不然样式值会被截断、清不掉
        long_ptr = ctypes.c_ssize_t
        getter.argtypes = [wintypes.HWND, ctypes.c_int]
        getter.restype = long_ptr
        setter.argtypes = [wintypes.HWND, ctypes.c_int, long_ptr]
        setter.restype = long_ptr
        style = int(getter(wintypes.HWND(hwnd), _GWL_EXSTYLE))
        if not style & _WS_EX_LAYERED:
            return False
        setter(wintypes.HWND(hwnd), _GWL_EXSTYLE, long_ptr(style & ~_WS_EX_LAYERED))
        user32.SetWindowPos(
            wintypes.HWND(hwnd), None, 0, 0, 0, 0,
            0x0002 | 0x0001 | 0x0004 | 0x0020,   # NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED
        )
        return not is_layered(hwnd)
    except Exception:
        return False


def is_layered(hwnd: int) -> bool:
    """窗口是不是「分层窗口」（WS_EX_LAYERED）。

    分层窗口 + Chromium 原生子窗口 = 点击穿透，这是之前那个 bug 的根因，
    测试里直接查这个标志确认没踩坑。
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        getter = getattr(ctypes.windll.user32, "GetWindowLongPtrW", None)
        if getter is None:
            getter = ctypes.windll.user32.GetWindowLongW
        style = getter(wintypes.HWND(hwnd), _GWL_EXSTYLE)
        return bool(int(style) & _WS_EX_LAYERED)
    except Exception:
        return False


def rounded_region(width: int, height: int, radius: float, inset: int = 1):
    """生成圆角矩形区域（Qt 的 QRegion），用于把窗口裁成圆角。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QBitmap, QPainter, QPainterPath, QPixmap, QRegion

    width = max(1, int(width))
    height = max(1, int(height))
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(Qt.GlobalColor.black)
    rect = QRectF(inset, inset, width - inset * 2, height - inset * 2)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.drawPath(path)
    painter.end()
    return QRegion(pixmap.mask())


# ---------------------------------------------------------------- 抓屏做磨砂
_WDA_EXCLUDEFROMCAPTURE = 0x00000011


def exclude_from_capture(hwnd: int, enable: bool = True) -> bool:
    """把窗口排除在截屏之外（Win10 2004+），这样抓到的背景里没有自己。"""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        affinity = _WDA_EXCLUDEFROMCAPTURE if enable else 0
        return bool(user32_SetWindowDisplayAffinity(wintypes.HWND(hwnd), affinity))
    except Exception:
        return False


def user32_SetWindowDisplayAffinity(hwnd, affinity: int) -> bool:
    return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity))


def _box_blur_axis(array, radius: int, axis: int):
    """沿指定轴做一次盒式模糊（前缀和实现，O(n)）。"""
    import numpy as np

    if radius <= 0:
        return array
    pad = [(radius, radius) if i == axis else (0, 0) for i in range(array.ndim)]
    padded = np.pad(array, pad, mode="edge")
    cumulative = np.cumsum(padded, axis=axis, dtype=np.float32)
    zero_shape = list(cumulative.shape)
    zero_shape[axis] = 1
    cumulative = np.concatenate(
        [np.zeros(zero_shape, dtype=np.float32), cumulative], axis=axis
    )
    length = array.shape[axis]
    window = 2 * radius + 1
    upper = np.take(cumulative, np.arange(window, window + length), axis=axis)
    lower = np.take(cumulative, np.arange(0, length), axis=axis)
    return (upper - lower) / window


def _numpy_gaussian(pixmap, radius: float):
    """可分离高斯模糊（三次盒式卷积近似）。

    先把图像降到 1/4 分辨率再模糊（高斯本身丢高频，视觉无差别、速度快很多），
    最后用平滑插值放大回去，因此不会出现块状台阶。
    """
    import numpy as np
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QImage, QPixmap

    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    if width < 8 or height < 8:
        return pixmap

    factor = 4
    small_w = max(2, width // factor)
    small_h = max(2, height // factor)
    scaled = image.scaled(
        small_w, small_h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_RGBA8888)

    raw = np.frombuffer(scaled.constBits(), dtype=np.uint8, count=scaled.sizeInBytes())
    raw = raw.reshape(small_h, scaled.bytesPerLine())[:, : small_w * 4]
    pixels = raw.reshape(small_h, small_w, 4).astype(np.float32)

    box_radius = max(1, int(radius / factor / 1.6))
    blurred = pixels
    for _ in range(3):
        blurred = _box_blur_axis(blurred, box_radius, 1)
        blurred = _box_blur_axis(blurred, box_radius, 0)

    out = QImage(small_w, small_h, QImage.Format.Format_RGBA8888)
    out_bytes = np.clip(blurred, 0, 255).astype(np.uint8)
    target = np.frombuffer(out.bits(), dtype=np.uint8, count=out.sizeInBytes())
    target = target.reshape(small_h, out.bytesPerLine())
    target[:, : small_w * 4] = out_bytes.reshape(small_h, small_w * 4)

    return QPixmap.fromImage(out).scaled(
        QSize(width, height),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _qt_blur(pixmap, radius: float):
    """numpy 不可用时的退路：Qt 自带的 C++ 模糊效果。"""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import (
        QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene,
    )

    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(pixmap)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(float(radius))
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)

    result = QPixmap(pixmap.size())
    result.fill(0)
    painter = QPainter(result)
    scene.render(painter, QRectF(result.rect()), QRectF(pixmap.rect()))
    painter.end()
    return result


def blur_pixmap(pixmap, radius: float = 26.0, passes: int = 3):
    """磨砂用的模糊：numpy 可分离高斯 > Qt 内置模糊 > 缩放近似。"""
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QPixmap

    if pixmap is None or pixmap.isNull():
        return pixmap
    try:
        import numpy  # noqa: F401
        return _numpy_gaussian(pixmap, radius)
    except Exception:
        pass
    try:
        return _qt_blur(pixmap, radius)
    except Exception:
        pass

    result = QPixmap(pixmap)
    factor = max(2.0, float(radius) / 2.0)
    for _ in range(max(1, passes)):
        width = max(1, int(result.width() / factor))
        height = max(1, int(result.height() / factor))
        result = result.scaled(QSize(width, height), Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        result = result.scaled(QSize(int(width * factor), int(height * factor)),
                               Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
    return result
