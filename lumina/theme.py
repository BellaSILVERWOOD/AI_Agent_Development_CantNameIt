"""Apple 风格主题：浅色 / 深色配色、字体排印、全局样式表与可调外观。

设计基准
--------
* 颜色：Apple HIG 的语义色（label / secondaryLabel / separator / systemGroupedBackground
  等）与 System Colors（systemBlue、systemPurple…）的浅色与深色两套取值。
* 圆角：窗口 8px（Windows 11 系统应用同款），卡片 12~16px，控件 8~10px。
* 字体：西文用 SF 的近似替代（Segoe UI Variable Text），中文用苹方的近似替代
  （HarmonyOS Sans SC / 微软雅黑 / 思源黑体），字号按 Apple 的排印层级取 px。
"""
from __future__ import annotations

import re
import sys
from typing import Dict, List, Optional, Tuple

from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from .config import EVENT_COLORS

# ------------------------------------------------------------------ Apple 系统色
# 浅色 / 深色两套（对应 Apple HIG 的 System Colors）
SYSTEM_COLORS: Dict[str, Tuple[str, str]] = {
    "blue": ("#007AFF", "#0A84FF"),
    "indigo": ("#5856D6", "#5E5CE6"),
    "purple": ("#AF52DE", "#BF5AF2"),
    "pink": ("#FF2D55", "#FF375F"),
    "red": ("#FF3B30", "#FF453A"),
    "orange": ("#FF9500", "#FF9F0A"),
    "yellow": ("#FFCC00", "#FFD60A"),
    "green": ("#34C759", "#30D158"),
    "mint": ("#00C7BE", "#63E6E2"),
    "teal": ("#30B0C7", "#40C8E0"),
    "cyan": ("#32ADE6", "#64D2FF"),
    "brown": ("#A2845E", "#AC8E68"),
    "gray": ("#8E8E93", "#98989D"),
}


def system_color(name: str, dark: Optional[bool] = None) -> str:
    light, night = SYSTEM_COLORS.get(name, SYSTEM_COLORS["blue"])
    if dark is None:
        dark = is_dark()
    return night if dark else light


# ------------------------------------------------------------------ 语义调色板
PALETTES: Dict[str, Dict[str, str]] = {
    "light": {
        # 外壳渐变（Apple 系统灰）
        "WINDOW_TOP": "#FBFBFD",
        "WINDOW_MID": "#F5F5F7",
        "WINDOW_BOTTOM": "#EDEDF0",
        # 文字
        "TEXT": "#1D1D1F",
        "TEXT_SOFT": "#48484D",
        "MUTED": "#6E6E73",
        "FAINT": "#A1A1A6",
        # 卡片与分隔
        "CARD": "#FFFFFF",
        "CARD_ALT": "#F5F5F7",
        "CARD_HOVER": "#FAFAFC",
        "BORDER": "#D3D3DB",
        "BORDER_SOFT": "#E4E4EA",
        "INPUT_BG": "#FFFFFF",
        "INPUT_HOVER_BORDER": "#C7C7CC",
        "INPUT_DISABLED_BG": "#F2F2F5",
        "INPUT_DISABLED_TEXT": "#AEAEB2",
        # 语义状态（Apple 可读性版本）
        "DANGER": "#D70015",
        "SUCCESS": "#248A3D",
        "WARNING": "#B25000",
        # 提示与滚动
        "TOOLTIP_BG": "#F7F7F9",
        "TOOLTIP_TEXT": "#1D1D1F",
        "SCROLL_HANDLE": "#C7C7CC",
        "SCROLL_HANDLE_HOVER": "#AEAEB2",
        "SLIDER_GROOVE": "#E5E5EA",
        "SHADOW_TINT": "#000000",
        "SIDEBAR_BG": "#F1F1F4",
    },
    "dark": {
        "WINDOW_TOP": "#26262A",
        "WINDOW_MID": "#212124",
        "WINDOW_BOTTOM": "#1A1A1C",
        "TEXT": "#F5F5F7",
        "TEXT_SOFT": "#D1D1D6",
        "MUTED": "#98989D",
        "FAINT": "#6E6E73",
        "CARD": "#2C2C2E",
        "CARD_ALT": "#35353A",
        "CARD_HOVER": "#3A3A3F",
        "BORDER": "#414147",
        "BORDER_SOFT": "#38383D",
        "INPUT_BG": "#1B1B1D",
        "INPUT_HOVER_BORDER": "#48484D",
        "INPUT_DISABLED_BG": "#232326",
        "INPUT_DISABLED_TEXT": "#6E6E73",
        "DANGER": "#FF453A",
        "SUCCESS": "#30D158",
        "WARNING": "#FF9F0A",
        "TOOLTIP_BG": "#2C2C2E",
        "TOOLTIP_TEXT": "#F5F5F7",
        "SCROLL_HANDLE": "#48484D",
        "SCROLL_HANDLE_HOVER": "#636369",
        "SLIDER_GROOVE": "#3A3A3E",
        "SHADOW_TINT": "#000000",
        "SIDEBAR_BG": "#26262A",
    },
}

# Apple 排印层级（px）。中文最小可读字号约 11px，故 Tiny 不再更小。
TYPE_SCALE: Dict[str, str] = {
    "F_H1": "22px",
    "F_H2": "15px",
    "F_H3": "13px",
    "F_BODY": "13px",
    "F_INPUT": "13px",
    "F_SMALL": "11.5px",
    "F_TINY": "11px",
    "F_MENU": "13px",
}

# ------------------------------------------------------------------ 基础颜色
# 这些名字会被主题按当前模式覆盖（浅色为初始值），并同步到引用它们的模块。
WINDOW_TOP = PALETTES["light"]["WINDOW_TOP"]
WINDOW_MID = PALETTES["light"]["WINDOW_MID"]
WINDOW_BOTTOM = PALETTES["light"]["WINDOW_BOTTOM"]
TEXT = PALETTES["light"]["TEXT"]
TEXT_SOFT = PALETTES["light"]["TEXT_SOFT"]
MUTED = PALETTES["light"]["MUTED"]
FAINT = PALETTES["light"]["FAINT"]
CARD = PALETTES["light"]["CARD"]
CARD_ALT = PALETTES["light"]["CARD_ALT"]
CARD_HOVER = PALETTES["light"]["CARD_HOVER"]
BORDER = PALETTES["light"]["BORDER"]
BORDER_SOFT = PALETTES["light"]["BORDER_SOFT"]
INPUT_BG = PALETTES["light"]["INPUT_BG"]
INPUT_HOVER_BORDER = PALETTES["light"]["INPUT_HOVER_BORDER"]
INPUT_DISABLED_BG = PALETTES["light"]["INPUT_DISABLED_BG"]
INPUT_DISABLED_TEXT = PALETTES["light"]["INPUT_DISABLED_TEXT"]
DANGER = PALETTES["light"]["DANGER"]
SUCCESS = PALETTES["light"]["SUCCESS"]
WARNING = PALETTES["light"]["WARNING"]
TOOLTIP_BG = PALETTES["light"]["TOOLTIP_BG"]
TOOLTIP_TEXT = PALETTES["light"]["TOOLTIP_TEXT"]
SCROLL_HANDLE = PALETTES["light"]["SCROLL_HANDLE"]
SCROLL_HANDLE_HOVER = PALETTES["light"]["SCROLL_HANDLE_HOVER"]
SLIDER_GROOVE = PALETTES["light"]["SLIDER_GROOVE"]
SHADOW_TINT = PALETTES["light"]["SHADOW_TINT"]
SIDEBAR_BG = PALETTES["light"]["SIDEBAR_BG"]

# 强调色（会跟随用户选择）
ACCENT_DEFAULT = SYSTEM_COLORS["blue"][0]
ACCENT2_DEFAULT = SYSTEM_COLORS["cyan"][0]
ACCENT = ACCENT_DEFAULT
ACCENT2 = ACCENT2_DEFAULT
ACCENT_DEEP = "#0062CC"

APPEARANCE_TOKENS = (
    "WINDOW_TOP", "WINDOW_MID", "WINDOW_BOTTOM",
    "TEXT", "TEXT_SOFT", "MUTED", "FAINT",
    "CARD", "CARD_ALT", "CARD_HOVER", "BORDER", "BORDER_SOFT",
    "INPUT_BG", "INPUT_HOVER_BORDER", "INPUT_DISABLED_BG", "INPUT_DISABLED_TEXT",
    "DANGER", "SUCCESS", "WARNING",
    "TOOLTIP_BG", "TOOLTIP_TEXT", "SCROLL_HANDLE", "SCROLL_HANDLE_HOVER",
    "SLIDER_GROOVE", "SHADOW_TINT", "SIDEBAR_BG",
)

# ------------------------------------------------------------------ 可调外观
ACCENT_PRESETS: List[Dict[str, str]] = [
    {"id": "apple-blue", "name": "系统蓝", "accent": "#007AFF", "accent2": "#64D2FF"},
    {"id": "apple-indigo", "name": "系统靛蓝", "accent": "#5856D6", "accent2": "#BF5AF2"},
    {"id": "apple-teal", "name": "系统青", "accent": "#30B0C7", "accent2": "#64D2FF"},
    {"id": "apple-green", "name": "系统绿", "accent": "#34C759", "accent2": "#00C7BE"},
    {"id": "apple-pink", "name": "系统粉", "accent": "#FF2D55", "accent2": "#BF5AF2"},
    {"id": "apple-orange", "name": "系统橙", "accent": "#FF9500", "accent2": "#FFCC00"},
    {"id": "apple-purple", "name": "系统紫", "accent": "#AF52DE", "accent2": "#FF2D55"},
    {"id": "apple-graphite", "name": "系统石墨", "accent": "#48484A", "accent2": "#8E8E93"},
]

BG_PRESETS: List[Dict[str, str]] = [
    {
        "id": "macos", "name": "系统灰（Apple）",
        "top": "#FBFBFD", "mid": "#F5F5F7", "bottom": "#EDEDF0",
        "card": "#FFFFFF", "border": "#DCDCE2", "input": "#FFFFFF",
    },
    {
        "id": "mist", "name": "雾灰白",
        "top": "#FBFCFD", "mid": "#F3F5F7", "bottom": "#EBEEF1",
        "card": "#FFFFFF", "border": "#DDE2E7", "input": "#FFFFFF",
    },
    {
        "id": "linen", "name": "暖砂白",
        "top": "#FCFCFA", "mid": "#F6F6F2", "bottom": "#EFEFEA",
        "card": "#FFFFFF", "border": "#E2E2DB", "input": "#FFFFFF",
    },
    {
        "id": "sand", "name": "米杏色",
        "top": "#FDFBF6", "mid": "#F8F4EB", "bottom": "#F1EBE0",
        "card": "#FFFFFF", "border": "#E8E1D3", "input": "#FFFFFF",
    },
    {
        "id": "mint", "name": "薄荷白",
        "top": "#FAFDFB", "mid": "#F1F7F3", "bottom": "#E8F1EB",
        "card": "#FFFFFF", "border": "#DCE8DF", "input": "#FFFFFF",
    },
    {
        "id": "blush", "name": "微粉白",
        "top": "#FDFAFB", "mid": "#F8F1F2", "bottom": "#F1E7E9",
        "card": "#FFFFFF", "border": "#E9DEE0", "input": "#FFFFFF",
    },
]

# 日程的柔和底色与文字色：key -> (soft, hover, ink)，浅色 / 深色两套
EVENT_TINTS: Dict[str, Tuple[str, str, str]] = {
    "violet": ("#F3EDFE", "#EBE2FC", "#6C3FA6"),
    "teal": ("#E2F5F7", "#D5EFF2", "#00707F"),
    "amber": ("#FDF1DF", "#FBE8CC", "#8A5300"),
    "rose": ("#FEE9EE", "#FBDCE5", "#B1123C"),
    "sky": ("#E4F0FF", "#D6E8FF", "#0058B8"),
}

EVENT_TINTS_DARK: Dict[str, Tuple[str, str, str]] = {
    "violet": ("#352B47", "#3E3253", "#D7B8FF"),
    "teal": ("#103235", "#143B3E", "#7FE0DD"),
    "amber": ("#3A2B15", "#44331B", "#FFC563"),
    "rose": ("#3C1F2B", "#472533", "#FF8FAE"),
    "sky": ("#10283F", "#153149", "#7FBEFF"),
}

# 窗口圆角：采用 Windows 11 系统应用的 8px（卡片内部仍可用更大的圆角）
WINDOW_RADIUS = 8.0

MODES = ("light", "dark", "auto")
MODE_LABELS = {"light": "浅色", "dark": "深色", "auto": "自动（跟随系统）"}
MODE_SHORT = {"light": "浅色", "dark": "深色", "auto": "自动"}

# 窗口不透明度的下限：再低界面就看不清了（35%）
ALPHA_MIN = 0.35

DEFAULT_APPEARANCE = {
    "accent": ACCENT_DEFAULT,
    "accent2": ACCENT2_DEFAULT,
    "bg": "macos",
    "alpha": 0.98,
    "glass": True,
    "blur": 24.0,
    "mode": "auto",
    "browser_theme": "follow",
}
APPEARANCE_KEYS = ("theme_accent", "theme_accent2", "theme_bg", "theme_alpha",
                   "theme_glass", "theme_blur", "theme_mode", "theme_browser")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def is_hex_color(value: str) -> bool:
    return bool(value) and bool(_HEX_RE.match(value))


def bg_preset(preset_id: str) -> Dict[str, str]:
    for preset in BG_PRESETS:
        if preset["id"] == preset_id:
            return preset
    return BG_PRESETS[0]


def accent_preset(preset_id: str) -> Optional[Dict[str, str]]:
    for preset in ACCENT_PRESETS:
        if preset["id"] == preset_id:
            return preset
    return None


class Appearance:
    """当前外观状态（深浅色、强调色、背景预设、窗口通透度）。"""

    def __init__(self):
        self.accent = DEFAULT_APPEARANCE["accent"]
        self.accent2 = DEFAULT_APPEARANCE["accent2"]
        self.bg = DEFAULT_APPEARANCE["bg"]
        self.alpha = DEFAULT_APPEARANCE["alpha"]
        self.glass = DEFAULT_APPEARANCE["glass"]
        self.blur = DEFAULT_APPEARANCE["blur"]
        self.mode = DEFAULT_APPEARANCE["mode"]
        self.browser_theme = "follow"

    # ---------------------------------------------- 强调色
    def accent_q(self, alpha: Optional[float] = None) -> QColor:
        color = QColor(self.accent)
        if alpha is not None:
            color.setAlphaF(max(0.0, min(1.0, alpha)))
        return color

    def accent2_q(self, alpha: Optional[float] = None) -> QColor:
        color = QColor(self.accent2)
        if alpha is not None:
            color.setAlphaF(max(0.0, min(1.0, alpha)))
        return color

    def accent_deep_q(self) -> QColor:
        return QColor(self.accent).darker(118)

    def accent_light_q(self) -> QColor:
        return QColor(self.accent).lighter(116)

    def accent_soft_q(self, alpha: float = 0.12) -> QColor:
        return self.accent_q(alpha)

    def on_accent_q(self) -> QColor:
        color = QColor(self.accent)
        luminance = (
            0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
        )
        return QColor("#FFFFFF" if luminance < 0.62 else "#20261F")

    # ---------------------------------------------- 背景
    def bg_data(self) -> Dict[str, str]:
        return bg_preset(self.bg)

    def bg_colors(self) -> Tuple[str, str, str]:
        if is_dark():
            return WINDOW_TOP, WINDOW_MID, WINDOW_BOTTOM
        data = self.bg_data()
        return data["top"], data["mid"], data["bottom"]

    def card_hex(self) -> str:
        if is_dark():
            return CARD
        return self.bg_data()["card"]

    def border_hex(self) -> str:
        if is_dark():
            return BORDER
        return self.bg_data()["border"]

    def input_hex(self) -> str:
        if is_dark():
            return INPUT_BG
        return self.bg_data()["input"]

    def shell_alpha(self) -> float:
        return self.alpha

    def matched_accent_preset(self) -> Optional[Dict[str, str]]:
        for preset in ACCENT_PRESETS:
            if preset["accent"].lower() == self.accent.lower():
                return preset
        return None


_appearance = Appearance()


def appearance() -> Appearance:
    return _appearance


# ------------------------------------------------------------------ 深浅色
def _system_scheme() -> str:
    app = QGuiApplication.instance()
    if app is None:
        return "light"
    try:
        hints = app.styleHints()
        if hints is not None and hints.colorScheme().name.lower().endswith("dark"):
            return "dark"
    except Exception:
        pass
    return "light"


def mode() -> str:
    return _appearance.mode


def effective_mode() -> str:
    """把 auto 解析成 light / dark。"""
    chosen = _appearance.mode
    if chosen in ("light", "dark"):
        return chosen
    return _system_scheme()


def is_dark() -> bool:
    return effective_mode() == "dark"


def is_light() -> bool:
    return not is_dark()


def set_mode(value: Optional[str]) -> bool:
    if value is None:
        return False
    value = str(value).strip().lower()
    if value not in MODES:
        return False
    if value == _appearance.mode:
        return False
    _appearance.mode = value
    _apply_palette()
    return True


def toggle_mode() -> str:
    """在浅色 / 深色之间切换（自动模式下按当前实际显示反转）。"""
    target = "light" if is_dark() else "dark"
    _appearance.mode = target
    _apply_palette()
    return target


# ------------------------------------------------------------------ 调色板下发
def _palette_values() -> Dict[str, str]:
    data = dict(PALETTES["dark" if is_dark() else "light"])
    data["ACCENT"] = _appearance.accent
    data["ACCENT2"] = _appearance.accent2
    data["ACCENT_DEEP"] = _appearance.accent_deep_q().name()
    return data


def _apply_palette(rebind: bool = True) -> Dict[str, str]:
    """把当前主题的色值写入本模块全局量，并同步到已导入这些名字的模块。"""
    global _PREV_TOKENS
    previous = _snapshot_tokens()
    values = _palette_values()
    if any(previous.get(key, "") != values.get(key, "")
           for key in previous):
        # 记下「换之前」的颜色：控件里写死的样式表要照着它替换
        _PREV_TOKENS = previous
    for key, value in values.items():
        globals()[key] = value
    if rebind:
        _rebind_modules(values)
    return values


def _rebind_modules(values: Dict[str, str]) -> None:
    """`from .theme import TEXT` 会在别的模块里留一份副本，切换主题时要一起更新。"""
    package = __package__ or "lumina"
    for name, module in list(sys.modules.items()):
        if not name.startswith(package + ".") or module is None:
            continue
        namespace = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict):
            continue
        for key, value in values.items():
            if key in namespace:
                namespace[key] = value


def set_appearance(
    accent: Optional[str] = None,
    accent2: Optional[str] = None,
    bg: Optional[str] = None,
    alpha: Optional[float] = None,
    glass: Optional[bool] = None,
    blur: Optional[float] = None,
    mode_: Optional[str] = None,
) -> bool:
    """更新外观状态，返回是否有变化。"""
    before = (
        _appearance.accent, _appearance.accent2, _appearance.bg, _appearance.alpha,
        _appearance.glass, _appearance.blur, _appearance.mode,
    )
    if accent and is_hex_color(accent):
        _appearance.accent = accent.lower()
    if accent2 and is_hex_color(accent2):
        _appearance.accent2 = accent2.lower()
    if bg and any(preset["id"] == bg for preset in BG_PRESETS):
        _appearance.bg = bg
    if alpha is not None:
        _appearance.alpha = max(ALPHA_MIN, min(1.0, float(alpha)))
    if glass is not None:
        _appearance.glass = bool(glass)
    if blur is not None:
        _appearance.blur = max(0.0, min(60.0, float(blur)))
    if mode_ is not None:
        set_mode(mode_)
    after = (
        _appearance.accent, _appearance.accent2, _appearance.bg, _appearance.alpha,
        _appearance.glass, _appearance.blur, _appearance.mode,
    )
    if before != after:
        _apply_palette()
    return before != after


def set_alpha(value: float) -> float:
    """只改「窗口不透明度」，不动调色板/样式表（拖滑块时用这个，才不卡）。"""
    try:
        _appearance.alpha = max(ALPHA_MIN, min(1.0, float(value)))
    except (TypeError, ValueError):
        pass
    return _appearance.alpha


def load_from_store(store) -> None:
    accent = store.get_setting("theme_accent", ACCENT_DEFAULT) or ACCENT_DEFAULT
    accent2 = store.get_setting("theme_accent2", ACCENT2_DEFAULT) or ACCENT2_DEFAULT
    bg = store.get_setting("theme_bg", "macos") or "macos"
    alpha_text = store.get_setting("theme_alpha", "0.98") or "0.98"
    try:
        alpha = float(alpha_text)
    except ValueError:
        alpha = DEFAULT_APPEARANCE["alpha"]
    glass = (store.get_setting("theme_glass", "1") or "1") == "1"
    blur_text = store.get_setting("theme_blur", "24") or "24"
    try:
        blur = float(blur_text)
    except ValueError:
        blur = DEFAULT_APPEARANCE["blur"]
    mode_text = store.get_setting("theme_mode", "") or ""
    if mode_text not in MODES:
        mode_text = DEFAULT_APPEARANCE["mode"]
    _appearance.mode = mode_text
    _appearance.accent = accent if is_hex_color(accent) else ACCENT_DEFAULT
    _appearance.accent2 = accent2 if is_hex_color(accent2) else ACCENT2_DEFAULT
    if any(preset["id"] == bg for preset in BG_PRESETS):
        _appearance.bg = bg
    else:
        _appearance.bg = DEFAULT_APPEARANCE["bg"]
    _appearance.alpha = max(ALPHA_MIN, min(1.0, alpha))
    _appearance.glass = glass
    _appearance.blur = max(0.0, min(60.0, blur))
    browser = store.get_setting("theme_browser", "follow") or "follow"
    if browser in BROWSER_THEMES:
        _appearance.browser_theme = browser
    load_fonts(store)
    _apply_palette()


def save_to_store(store) -> None:
    store.update_settings({
        "theme_accent": _appearance.accent,
        "theme_accent2": _appearance.accent2,
        "theme_bg": _appearance.bg,
        "theme_alpha": f"{_appearance.alpha:.2f}",
        "theme_glass": "1" if _appearance.glass else "0",
        "theme_blur": f"{_appearance.blur:.0f}",
        "theme_mode": _appearance.mode,
        "theme_browser": _appearance.browser_theme,
    })
    save_fonts(store)


def migrate_to_apple(store) -> bool:
    """一次性把旧版默认配色迁移到 Apple 系统色（用户自定义过的颜色不动）。"""
    if (store.get_setting("theme_apple_v2", "") or "") == "1":
        return False
    updates = {"theme_apple_v2": "1"}
    accent = (store.get_setting("theme_accent", "") or "").lower()
    accent2 = (store.get_setting("theme_accent2", "") or "").lower()
    if accent in ("", "#2f7a5f", "#255f49"):
        updates["theme_accent"] = ACCENT_DEFAULT
    if accent2 in ("", "#d99a4e"):
        updates["theme_accent2"] = ACCENT2_DEFAULT
    bg = (store.get_setting("theme_bg", "") or "").lower()
    if bg in ("", "linen"):
        updates["theme_bg"] = "macos"
    if not (store.get_setting("theme_mode", "") or ""):
        updates["theme_mode"] = "auto"
    store.update_settings(updates)
    return True


def harmonize_accent(hex_color: str) -> str:
    """给自定义强调色配一个协调的辅助色。"""
    color = QColor(hex_color)
    hue = (color.hue() + 145) % 360
    if hue < 0:
        hue = 0
    helper = QColor.fromHsv(hue, 110, 205)
    return helper.name()


# ------------------------------------------------------------------ 便捷取色
def hex_alpha(color: str, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def q_color(value: str, alpha: Optional[float] = None) -> QColor:
    color = QColor(value)
    if alpha is not None:
        color.setAlphaF(max(0.0, min(1.0, alpha)))
    return color


_EVENT_SYSTEM = {
    "violet": "purple",
    "teal": "teal",
    "amber": "orange",
    "rose": "pink",
    "sky": "blue",
}


def _event_system_name(key: str) -> str:
    return _EVENT_SYSTEM.get(str(key), "purple")


def event_base(key: str) -> str:
    """日程主题色（按深浅色取 Apple System Color 的对应值）。"""
    return system_color(_event_system_name(key))


def event_color(key: str, light: bool = False) -> QColor:
    base, _label = EVENT_COLORS.get(key, EVENT_COLORS["violet"])
    if not light:
        return QColor(event_base(key))
    return QColor(base)


def event_label(key: str) -> str:
    return EVENT_COLORS.get(key, EVENT_COLORS["violet"])[1]


def event_tint(key: str) -> Tuple[QColor, QColor, QColor]:
    """返回 (柔和底色, 悬停底色, 深色文字)。"""
    table = EVENT_TINTS_DARK if is_dark() else EVENT_TINTS
    soft, hover, ink = table.get(str(key), table["violet"])
    return QColor(soft), QColor(hover), QColor(ink)


def event_ink(key: str) -> QColor:
    return event_tint(key)[2]


def mail_tone(status: str) -> str:
    """发件箱状态色（草稿灰 / 已发绿 / 失败红 / 退信橙）。"""
    if status == "sent":
        return SUCCESS
    if status == "failed":
        return DANGER
    if status == "bounced":
        return WARNING
    return MUTED


def shadow_color(level: str = "soft") -> QColor:
    """卡片阴影：浅色用极淡的黑，深色更重一些。"""
    if is_dark():
        alpha = 0.42 if level == "strong" else 0.30
    else:
        alpha = 0.16 if level == "strong" else 0.10
    return QColor(0, 0, 0, int(round(alpha * 255)))


def soft(color: str, alpha: float = 0.12) -> str:
    """把颜色按比例混进卡片底色，得到浅色 / 深色下都成立的柔和底色。"""
    return mix(QColor(CARD), QColor(color), alpha).name()


def hover_wash(alpha: float = 0.06) -> QColor:
    """中性悬停底色：浅色下压暗，深色下提亮。"""
    return mix(QColor(CARD), QColor(TEXT), alpha)


def mix(color: QColor, other: QColor, ratio: float) -> QColor:
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        int(color.red() + (other.red() - color.red()) * ratio),
        int(color.green() + (other.green() - color.green()) * ratio),
        int(color.blue() + (other.blue() - color.blue()) * ratio),
        int(color.alpha() + (other.alpha() - color.alpha()) * ratio),
    )


# ------------------------------------------------------------------ 字体
# 空字符串表示使用默认字体栈（鸿蒙黑体 HarmonyOS Sans SC）
DEFAULT_FONTS = {"zh": "", "en": ""}
FONT_KEYS = ("font_zh", "font_en")

# 默认全用鸿蒙黑体（中文与西文同一套字形，观感统一）；
# 万一机器上没装，再依次退回这些系统字体。
DEFAULT_PRIMARY = "HarmonyOS Sans SC"
DEFAULT_EN_STACK = [
    DEFAULT_PRIMARY, "Segoe UI Variable Text", "Segoe UI", "SF Pro Text",
]
DEFAULT_ZH_STACK = [
    DEFAULT_PRIMARY, "PingFang SC", "Microsoft YaHei UI", "Noto Sans SC",
]

# 设置页可直接选用的搭配（全部为本机已安装字体；assets/fonts 里的字体也会出现在下拉框）
FONT_PRESETS: List[Dict[str, str]] = [
    {"id": "harmony-default", "name": "鸿蒙黑体（默认）", "zh": "", "en": ""},
    {"id": "harmony", "name": "鸿蒙黑体 + 系统西文 Segoe", "zh": "HarmonyOS Sans SC",
     "en": "Segoe UI Variable Text"},
    {"id": "apple", "name": "系统西文 + 苹果近似", "zh": "", "en": "Segoe UI Variable Text"},
    {"id": "pingfang", "name": "苹方 + SF Pro（需自备字体）", "zh": "PingFang SC",
     "en": "SF Pro Text"},
    {"id": "noto", "name": "思源黑体 + Segoe UI", "zh": "Noto Sans SC", "en": "Segoe UI"},
    {"id": "yahei", "name": "微软雅黑 + Segoe UI", "zh": "Microsoft YaHei UI",
     "en": "Segoe UI"},
    {"id": "songti", "name": "思源宋体 + Georgia", "zh": "Noto Serif SC", "en": "Georgia"},
    {"id": "sitka", "name": "思源宋体 + Sitka", "zh": "Noto Serif SC", "en": "Sitka Text"},
    {"id": "kaiti", "name": "楷体 + Cambria", "zh": "KaiTi", "en": "Cambria"},
]

_fonts = dict(DEFAULT_FONTS)


def fonts() -> Dict[str, str]:
    return dict(_fonts)


def set_fonts(zh: Optional[str] = None, en: Optional[str] = None) -> bool:
    before = dict(_fonts)
    if zh is not None:
        _fonts["zh"] = (zh or "").strip()
    if en is not None:
        _fonts["en"] = (en or "").strip()
    return before != _fonts


def load_fonts(store) -> None:
    set_fonts(store.get_setting("font_zh", "") or "", store.get_setting("font_en", "") or "")


_bundled_families: List[str] = []


def bundled_families() -> List[str]:
    """从 assets/fonts 加载进来的字体家族名。"""
    return list(_bundled_families)


def load_bundled_fonts() -> List[str]:
    """加载 assets/fonts 下的字体文件（.ttf/.otf/.ttc）。

    把自己的字体（例如苹方、思源宋体、SF Pro…）放进这个目录就能在设置里选中，
    不需要装进系统。返回加载成功的家族名。
    """
    from PySide6.QtGui import QFontDatabase

    from .config import APP_ROOT

    folder = APP_ROOT / "assets" / "fonts"
    if not folder.is_dir():
        return []
    loaded: List[str] = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in (".ttf", ".otf", ".ttc", ".otc"):
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            if family not in loaded:
                loaded.append(family)
    _bundled_families[:] = loaded
    return loaded


def save_fonts(store) -> None:
    store.update_settings({"font_zh": _fonts["zh"], "font_en": _fonts["en"]})


def font_families() -> List[str]:
    """西文在前、中文在后：Qt 按字符回退，于是能实现「中文宋体 + 西文衬线」。"""
    families: List[str] = []
    if _fonts.get("en"):
        families.append(_fonts["en"])
    else:
        families += DEFAULT_EN_STACK
    if _fonts.get("zh"):
        families.append(_fonts["zh"])
    else:
        families += DEFAULT_ZH_STACK
    families += ["Segoe UI", "Microsoft YaHei UI", "Noto Sans SC"]
    ordered: List[str] = []
    for family in families:
        if family and family not in ordered:
            ordered.append(family)
    return ordered


def app_font() -> QFont:
    font = QFont()
    font.setFamilies(font_families())
    font.setPixelSize(13)
    # 默认用 Medium（500）：Regular 在 125% 缩放 + 灰度渲染下显得偏细
    font.setWeight(QFont.Weight.Medium)
    # 灰度抗锯齿 + 关闭字形微调（hinting）：笔画边缘连续光滑，接近 macOS 的渲染观感
    font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.NoSubpixelAntialias
    )
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


BROWSER_THEMES = ("follow", "light", "dark")
BROWSER_THEME_LABELS = {
    "follow": "跟随软件风格",
    "light": "强制浅色",
    "dark": "强制深色",
}


def browser_theme() -> str:
    value = getattr(_appearance, "browser_theme", "follow") or "follow"
    return value if value in BROWSER_THEMES else "follow"


def set_browser_theme(value: str) -> None:
    if value in BROWSER_THEMES:
        _appearance.browser_theme = value


def web_dark_mode() -> bool:
    """网页是否应该用暗色。"""
    mode = browser_theme()
    if mode == "dark":
        return True
    if mode == "light":
        return False
    return is_dark()


_REVISION = 0


def revision() -> int:
    """主题版本号，每换一次主题 +1。

    自绘控件（日历网格、卡片、图标按钮）会在构造时把颜色抄一份留着，
    换主题后必须让它们知道「缓存过期了」。paintEvent 里对一下版本号最省事，
    也不需要挨个控件去连信号。
    """
    return _REVISION


def _bump_revision() -> int:
    global _REVISION
    _REVISION += 1
    return _REVISION


def apply_theme(app: QApplication) -> None:
    _apply_palette()
    _bump_revision()
    app.setStyle("Fusion")
    app.setFont(app_font())

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(_appearance.bg_colors()[2]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(INPUT_BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(CARD))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(CARD))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(_appearance.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Link, QColor(_appearance.accent))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(TOOLTIP_BG))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TOOLTIP_TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(FAINT))
    palette.setColor(QPalette.ColorRole.Mid, QColor(BORDER))
    palette.setColor(QPalette.ColorRole.Dark, QColor(BORDER))
    palette.setColor(QPalette.ColorRole.Light, QColor(CARD_HOVER))
    app.setPalette(palette)
    # 先把样式表清空再设一遍：Qt 只在字符串变化时才重新 polish，
    # 清空一下能逼所有控件按新主题重新解析（否则有些控件还留着旧配色）
    app.setStyleSheet("")
    app.setStyleSheet(current_stylesheet())
    _rewrite_inline_styles(app)
    _refresh_app_widgets(app)
    # 主题变了，正显示着的提示气泡按旧配色画的，直接收掉
    try:
        from . import tooltip

        tooltip.hide_tip()
    except Exception:
        pass


_TOKEN_NAMES = tuple(sorted(PALETTES["light"].keys())) + ("ACCENT", "ACCENT2",
                                                          "ACCENT_DEEP")
_PREV_TOKENS: Dict[str, str] = {}


def _snapshot_tokens() -> Dict[str, str]:
    """当前这套主题令牌的色值（换主题前拍一张，用来改掉控件里写死的旧颜色）。"""
    return {name: str(globals().get(name, "") or "") for name in _TOKEN_NAMES}


def _rewrite_inline_styles(app: QApplication) -> None:
    """把控件自己 setStyleSheet 里写死的旧主题色换成新主题色。

    很多地方是 `label.setStyleSheet(f"color: {TEXT}")` 这种写法 —— 样式表是
    当时算好的字符串，换主题时不会跟着变（浅色模式下备忘编辑器整块黑、
    左下角那个日期一直看不见，都是这个原因）。这里按「旧色值 → 新色值」做一次
    替换，通用且不用挨个改控件。

    **换完必须把 `_PREV_TOKENS` 清空**：一次切主题 souvent 会连着调好几次
    `apply_theme`（set_mode → preview → 刷新），第二遍再用同一张映射表就会**链式替换**
    —— 浅色 TEXT #1D1D1F 先变成深色 TEXT #F5F5F7，第二遍又把 #F5F5F7 当成
    「浅色 WINDOW_MID」换成 #212124，结果深色下的文字还是深色（用户报的
    「切深色之后这些文字还是深色」就是这个）。清空后第二遍就是空操作。
    """
    global _PREV_TOKENS
    after = _snapshot_tokens()
    before = _PREV_TOKENS or {}
    mapping = {}
    # 文字类令牌最后写：同一个色值被多个令牌共用时（浅色 TEXT 和 TOOLTIP_TEXT
    # 都是 #1D1D1F、CARD 和 INPUT_BG 都是 #FFFFFF），文字要按文字换，
    # 底色之间仍按原来的顺序（INPUT_BG 在 CARD 之后，浅色 #FFFFFF 换成 #1B1B1D，
    # 备忘编辑器就靠这个）。
    text_names = ("TEXT", "TEXT_SOFT", "MUTED", "FAINT")
    names = [name for name in before if name not in text_names] + \
            [name for name in text_names if name in before]
    for name in names:
        old = str(before.get(name, "") or "")
        new = str(after.get(name, "") or "")
        if len(old) == 7 and old.startswith("#") and new and old.lower() != new.lower():
            mapping[old.lower()] = new
    _PREV_TOKENS = {}
    if not mapping:
        return
    # 一次扫描替换：不能一条条 replace —— 新的浅色值可能正好等于另一个令牌的
    # 旧深色值（比如深色 TEXT 和浅色 CARD_ALT 都是 #F5F5F7），顺序替换会串色。
    pattern = re.compile("|".join(re.escape(old) for old in mapping),
                         re.IGNORECASE)
    try:
        widgets = list(app.allWidgets())
    except Exception:
        return
    for widget in widgets:
        try:
            sheet = widget.styleSheet()
        except Exception:
            continue
        if not sheet or "#" not in sheet:
            continue
        if not pattern.search(sheet):
            continue
        patched = pattern.sub(lambda match: mapping[match.group(0).lower()], sheet)
        if patched != sheet:
            try:
                widget.setStyleSheet(patched)
            except Exception:
                continue


def _refresh_app_widgets(app: QApplication) -> None:
    """重刷所有控件的绘制，并让内嵌网页按新主题重新设定暗色。

    为什么需要这个：日历网格 / 日程列表 / 左下角日期这些都是**自绘**控件
    （`paintEvent` 里读颜色）；内嵌网页（eLearning / eHall）的暗色是
    `ForceDarkMode` 设置，只在创建时设过一次 —— 浅色模式下它们会一直是黑的。
    """
    widgets = []
    try:
        widgets = list(app.allWidgets())
    except Exception:
        widgets = []
    for widget in widgets:
        # 自绘控件如果实现了 on_theme_changed，就先让它把缓存的主题色换掉再重绘
        hook = getattr(widget, "on_theme_changed", None)
        if callable(hook):
            try:
                hook()
            except Exception:
                pass
        try:
            widget.update()
        except Exception:
            continue
    try:
        from . import web_panel

        for widget in widgets:
            if isinstance(widget, web_panel.EmbeddedWebPanel):
                try:
                    widget.refresh_browser_theme()
                except Exception:
                    continue
    except Exception:
        pass


def _tokens() -> Dict[str, str]:
    values = _palette_values()
    values.update(TYPE_SCALE)
    accent = QColor(values["ACCENT"])
    card = QColor(values["CARD"])
    values["ACCENT_SOFT"] = mix(card, accent, 0.12).name()
    values["ACCENT_SOFT_STRONG"] = mix(card, accent, 0.20).name()
    return values


def current_stylesheet() -> str:
    qss = STYLESHEET
    for key, value in _tokens().items():
        qss = qss.replace("@" + key + "@", value)
    return qss


STYLESHEET = """
QWidget {
    color: @TEXT@;
    outline: none;
}
QToolTip {
    background: @TOOLTIP_BG@;
    color: @TOOLTIP_TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: @F_TINY@;
}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
    background: @INPUT_BG@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
    padding: 6px 10px;
    color: @TEXT@;
    font-size: @F_INPUT@;
    selection-background-color: @ACCENT@;
    selection-color: #FFFFFF;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover {
    border-color: @INPUT_HOVER_BORDER@;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border-color: @ACCENT@;
    background: @INPUT_BG@;
}
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {
    color: @INPUT_DISABLED_TEXT@;
    background: @INPUT_DISABLED_BG@;
    border-color: @BORDER_SOFT@;
}
QLineEdit[error="true"] {
    border-color: @DANGER@;
}
QComboBox {
    padding-right: 26px;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    width: 0px;
    height: 0px;
}
QComboBox QLineEdit {
    background: transparent;
    border: none;
    padding: 0px;
    margin: 0px;
}
QComboBox QAbstractItemView {
    background: @CARD@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
    padding: 4px;
    outline: 0;
    selection-background-color: @ACCENT_SOFT@;
    selection-color: @TEXT@;
}
QComboBox QAbstractItemView::item {
    min-height: 24px;
    padding: 2px 8px;
    border-radius: 6px;
    color: @TEXT_SOFT@;
}
QComboBox QAbstractItemView::item:selected {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
}

QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollArea > QWidget > QScrollBar {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 9px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: @SCROLL_HANDLE@;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: @SCROLL_HANDLE_HOVER@;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 9px;
}
QScrollBar::handle:horizontal {
    background: @SCROLL_HANDLE@;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: @SCROLL_HANDLE_HOVER@;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}

QLabel {
    background: transparent;
}
QLabel#H1 {
    font-size: @F_H1@;
    font-weight: 600;
    color: @TEXT@;
}
QLabel#H2 {
    font-size: @F_H2@;
    font-weight: 600;
    color: @TEXT@;
}
QLabel#H3 {
    font-size: @F_H3@;
    font-weight: 600;
    color: @TEXT_SOFT@;
}
QLabel#PanelTitle {
    font-size: @F_H3@;
    font-weight: 600;
    color: @TEXT@;
}
QLabel#Muted {
    color: @MUTED@;
    font-size: @F_SMALL@;
}
QLabel#Small {
    color: @TEXT_SOFT@;
    font-size: @F_SMALL@;
}
QLabel#Tiny {
    color: @MUTED@;
    font-size: @F_TINY@;
}
QLabel#Error {
    color: @DANGER@;
    font-size: @F_SMALL@;
}
QLabel#Success {
    color: @SUCCESS@;
    font-size: @F_SMALL@;
}
QLabel#Warn {
    color: @WARNING@;
    font-size: @F_SMALL@;
}

QTextEdit#Note {
    padding: 7px 10px;
    font-size: @F_BODY@;
}
QTextBrowser {
    background: @CARD@;
    border: none;
}

/* 文档页的目录树：透明底，选中/悬停沿用主题色 */
QTreeWidget#DocsTree {
    background: transparent;
    border: none;
    color: @TEXT_SOFT@;
    font-size: @F_SMALL@;
    show-decoration-selected: 1;
}
QTreeWidget#DocsTree::item {
    padding: 4px 2px;
    border-radius: 6px;
    color: @TEXT_SOFT@;
}
QTreeWidget#DocsTree::item:hover {
    background: @CARD_HOVER@;
}
QTreeWidget#DocsTree::item:selected {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
}

QSlider::groove:horizontal {
    height: 4px;
    background: @SLIDER_GROOVE@;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: @ACCENT@;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: #FFFFFF;
    border: 2px solid @ACCENT@;
}

QMenu {
    background: @CARD@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
    padding: 5px;
}
QMenu::item {
    padding: 5px 20px 5px 12px;
    border-radius: 5px;
    color: @TEXT_SOFT@;
    font-size: @F_MENU@;
}
QMenu::item:selected {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
}
QMenu::separator {
    height: 1px;
    background: @BORDER@;
    margin: 4px 8px;
}

QSizeGrip {
    background: transparent;
}
"""


def make_app_icon(size: int = 128):
    """绘制应用图标：渐变圆角方块 + 日历字形。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import (
        QBrush, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap,
    )

    from . import icons

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, _appearance.accent_deep_q())
    gradient.setColorAt(0.55, QColor(_appearance.accent))
    gradient.setColorAt(1.0, QColor(_appearance.accent2))
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.24, size * 0.24)
    painter.fillPath(path, QBrush(gradient))
    painter.setFont(_icon_font(size * 0.46))
    painter.setPen(QColor("#FFFFFF"))
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), icons.CALENDAR)
    painter.end()
    return QIcon(pixmap)


def _icon_font(pixel_size: float):
    from . import icons

    font = QFont(icons.icon_family())
    font.setPixelSize(max(8, int(pixel_size)))
    font.setWeight(QFont.Weight.DemiBold)
    return font
