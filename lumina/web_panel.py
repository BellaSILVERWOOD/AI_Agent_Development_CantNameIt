"""内嵌网页面板：把网站直接放在应用页面里（不弹独立窗口）。"""
from __future__ import annotations

import math
import sys
import weakref
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QPoint, QPointF, QRect, QRectF, Qt, QTimer, QUrl, Signal,
)
from PySide6.QtWidgets import QStackedWidget, QTabBar
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from . import icons, theme
from .widgets import IconButton, SoftButton

# Chromium 退出不干净时会留下这些锁文件；下次启动会让网页视图初始化失败甚至卡住。
_STALE_LOCK_NAMES = ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile")

# 是否启用 Edge 式多标签页（主窗口建页面前按设置打开；设置里改了就当场生效）
_TABS_ENABLED = False
# 已经建出来的网页面板：设置里切开关时要逐个套上去（用弱引用，别把面板留住）
_LIVE_PANELS: "weakref.WeakSet" = weakref.WeakSet()


def set_tabs_enabled(value: bool) -> None:
    """开关多标签页：已经建出来的网页面板**立刻**跟着变（不用重启）。"""
    global _TABS_ENABLED
    _TABS_ENABLED = bool(value)
    for panel in list(_LIVE_PANELS):
        try:
            panel.apply_tabs(_TABS_ENABLED)
        except Exception:
            continue


def tabs_enabled() -> bool:
    return _TABS_ENABLED


_locks_cleaned = False


def clean_stale_locks(root: Path) -> int:
    """清掉上次强杀留下的 Chromium 锁文件（每个进程只做一次）。"""
    global _locks_cleaned
    if _locks_cleaned:
        return 0
    _locks_cleaned = True
    removed = 0
    root = Path(root)
    for folder in (root, root / "Default"):
        for name in _STALE_LOCK_NAMES:
            target = folder / name
            try:
                if target.is_symlink() or target.exists():
                    target.unlink()
                    removed += 1
            except Exception:
                pass
    return removed


class _TabCloseButton(QWidget):
    """标签页上的关闭按钮：只有一个 75% 灰的 ×（不要 Windows 那颗白×红底）。

    自绘的好处：跟着主题走（浅色/深色都对），悬停时加深一点点、加一层很淡的圆角底，
    也不会像系统默认那样在鼠标移上去时突然变成红色方块。
    """

    clicked = Signal()

    def __init__(self, parent=None, host=None):
        super().__init__(parent)
        self._host = host
        self._hover = False
        self.setFixedSize(19, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("关闭这个标签页")

    # -------------------------------------------------- 交互
    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -------------------------------------------------- 绘制
    def paintEvent(self, event):
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect()
            if self._hover:
                wash = QColor(theme.TEXT)
                wash.setAlphaF(0.10)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(wash)
                painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 4, 4)
            color = QColor(theme.TEXT if self._hover else theme.MUTED)
            color.setAlphaF(1.0 if self._hover else 0.75)     # 75% 灰
            pen = QPen(color)
            pen.setWidthF(1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            margin = 5.0
            painter.drawLine(QPointF(margin, margin + 1.0),
                             QPointF(rect.width() - margin - 1.0,
                                     rect.height() - margin - 1.0))
            painter.drawLine(QPointF(rect.width() - margin - 1.0, margin + 1.0),
                             QPointF(margin, rect.height() - margin - 1.0))
        finally:
            painter.end()


class EmbeddedWebPanel(QWidget):
    """地址栏 + 内嵌浏览器；每个站点一个持久化 profile。"""

    loaded = Signal(bool)
    autofilled = Signal(bool)

    def __init__(self, profile_name: str, storage_sub: str, home_url: str,
                 parent=None, store=None, autofill: bool = True):
        super().__init__(parent)
        self._profile_name = profile_name
        self._storage_sub = storage_sub
        self._home_url = home_url
        self._store = store
        self._autofill = bool(autofill) and store is not None
        self._view = None
        self._pending_url: Optional[str] = home_url
        self._started = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._layout = layout        # 多标签页栏要往上插，得留着这个布局

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.back_button = IconButton(icons.CHEVRON_LEFT, size=28, glyph_size=11,
                                      tip="后退")
        self.back_button.clicked.connect(lambda: self._call("back"))
        bar.addWidget(self.back_button)
        self.reload_button = IconButton(icons.REFRESH, size=28, glyph_size=11,
                                        tip="刷新")
        self.reload_button.clicked.connect(self.reload)
        bar.addWidget(self.reload_button)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        self.address.setFixedHeight(30)
        self.address.setStyleSheet(
            f"QLineEdit {{ background: {theme.CARD_ALT}; color: {theme.MUTED};"
            f" border: 1px solid {theme.BORDER}; border-radius: 8px;"
            " padding: 2px 8px; font-size: 11px; }")
        bar.addWidget(self.address, 1)
        self.home_button = SoftButton("主页", "ghost", height=30, min_width=64,
                                      point_size=8.6)
        self.home_button.clicked.connect(self.go_home)
        bar.addWidget(self.home_button)
        self.fullscreen_button = SoftButton("全屏", "ghost", height=30, min_width=64,
                                            point_size=8.6)
        self.fullscreen_button.setToolTip("在应用内全屏显示这个网页")
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
        bar.addWidget(self.fullscreen_button)
        layout.addLayout(bar)

        # 懒加载：先放占位，第一次显示时再创建浏览器
        self.placeholder = QLabel("网页还没加载，点一下这里或上面的「主页」")
        self.placeholder.setObjectName("Muted")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setMinimumHeight(120)
        layout.addWidget(self.placeholder, 1)
        self.stack = QVBoxLayout()
        self.stack.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.stack, 1) if False else None

        self._fullscreen = False
        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        layout.addWidget(self.status)

        # 首帧遮罩：网页第一帧还没画出来时先盖住（用卡片色），加载完再淡出，
        # 这样看不到 Chromium 的白底闪一下
        self._cover = QWidget(self)
        self._cover.setStyleSheet(f"background: {theme.CARD};")
        self._cover.hide()
        self._revealed = False

        # ---- Edge 式多标签页（设置里可以关掉） ----
        self._views = []
        self._tabs_enabled = bool(_TABS_ENABLED)
        self._tab_bar = None
        self._tab_host = None
        self._view_stack = QStackedWidget()
        self._view_stack.hide()
        layout.addWidget(self._view_stack, 1)
        if self._tabs_enabled:
            self._build_tab_bar(layout)
        _LIVE_PANELS.add(self)

    # -------------------------------------------------- 多标签页开关（可以随时切）
    def apply_tabs(self, enabled: bool) -> None:
        """把多标签页开/关套到这个面板上（不重建网页视图，会话不丢）。"""
        enabled = bool(enabled)
        if enabled == self._tabs_enabled:
            return
        self._tabs_enabled = enabled
        if enabled:
            if self._tab_bar is None:
                self._build_tab_bar(getattr(self, "_layout", self.layout()))
            for view in self._views:
                title = ""
                try:
                    title = str(view.title() or "")[:18]
                except Exception:
                    title = ""
                try:
                    index = self._tab_bar.addTab(title or "新标签页")
                    self._attach_close_button(index)
                except Exception:
                    pass
            if self._views and self._tab_bar is not None:
                try:
                    self._tab_bar.setCurrentIndex(len(self._views) - 1)
                except Exception:
                    pass
            return
        # 关掉：拆掉标签栏，只留当前那个网页
        host = getattr(self, "_tab_host", None)
        current = self._view
        if host is not None:
            try:
                host.setParent(None)
                host.deleteLater()
            except Exception:
                pass
        self._tab_bar = None
        self._tab_host = None
        for view in list(self._views):
            if view is current:
                continue
            try:
                self._view_stack.removeWidget(view)
                view.stop()
                view.setParent(None)
                view.deleteLater()
            except Exception:
                pass
            try:
                self._views.remove(view)
            except ValueError:
                pass
        if current is not None and current not in self._views:
            self._views.append(current)
        if current is not None:
            try:
                self._view_stack.setCurrentWidget(current)
                self._view_stack.show()
            except Exception:
                pass


    # -------------------------------------------------- 多标签页
    def _build_tab_bar(self, layout) -> None:
        bar_host = QWidget()
        row = QHBoxLayout(bar_host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._tab_bar = QTabBar()
        self._tab_bar.setExpanding(False)
        self._tab_bar.setMovable(True)
        self._tab_bar.setDrawBase(False)
        # 关闭按钮自己画（Windows 自带的那颗是白×红底，太扎眼）：
        # 只要一个 75% 灰的 ×，悬停时略微加深 + 淡淡的圆角底
        self._tab_bar.setTabsClosable(False)
        self._tab_bar.setUsesScrollButtons(True)
        self._tab_bar.currentChanged.connect(self._on_tab_changed)
        self._tab_bar.setStyleSheet(
            f"QTabBar::tab {{ background: {theme.CARD_ALT}; color: {theme.MUTED};"
            f" border: 1px solid {theme.BORDER}; border-bottom: none;"
            " padding: 4px 9px 4px 13px; margin-right: 3px;"
            " border-top-left-radius: 8px; border-top-right-radius: 8px; }"
            f"QTabBar::tab:selected {{ background: {theme.CARD}; color: {theme.TEXT}; }}")
        row.addWidget(self._tab_bar, 1)
        self.new_tab_button = IconButton(icons.ADD, size=26, glyph_size=11,
                                         tip="新建标签页")
        self.new_tab_button.clicked.connect(lambda: self.new_tab())
        row.addWidget(self.new_tab_button)
        layout.insertWidget(0, bar_host)
        self._tab_host = bar_host

    def _attach_close_button(self, index: int) -> None:
        """给某个标签装自绘的灰色 ×。"""
        if self._tab_bar is None:
            return
        button = _TabCloseButton(self, self._tab_bar)
        button.clicked.connect(lambda btn=button: self._close_by_button(btn))
        try:
            self._tab_bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, button)
        except Exception:
            pass

    def _close_by_button(self, button) -> None:
        """点自绘的 ×：先按控件找到它属于哪个标签（标签可以拖动排序）。"""
        bar = self._tab_bar
        if bar is None:
            return
        for index in range(bar.count()):
            try:
                if bar.tabButton(index, QTabBar.ButtonPosition.RightSide) is button:
                    self._close_tab(index)
                    return
            except Exception:
                continue

    def new_tab(self, url: str = "") -> None:
        """开一个新标签页（默认打开这个面板的首页）。"""
        if not self._tabs_enabled:
            self.load(url or self._home_url)
            return
        target = url or self._home_url
        view = self._add_view(target)
        try:
            self._started = True
            self._pending_url = None
            view.setUrl(QUrl(target))
        except Exception:
            pass

    def _add_view(self, title: str = "") -> object:
        view = self._make_view()
        if view is None:
            return None
        self._views.append(view)
        try:
            self._view_stack.addWidget(view)
            self._view_stack.show()
            if self.placeholder is not None:
                self.placeholder.hide()
        except Exception:
            pass
        if self._tab_bar is not None:
            index = self._tab_bar.addTab(title or "新标签页")
            self._attach_close_button(index)
            try:
                self._tab_bar.setCurrentIndex(index)
            except Exception:
                pass
        else:
            try:
                self._view_stack.setCurrentWidget(view)
            except Exception:
                pass
        return view

    def _on_tab_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._views):
            return
        view = self._views[index]
        self._view = view
        try:
            self._view_stack.setCurrentWidget(view)
        except Exception:
            pass
        try:
            self._apply_browser_theme(view)
            self.address.setText(view.url().toString()[:120])
        except Exception:
            pass

    def _close_tab(self, index: int) -> None:
        if index < 0 or index >= len(self._views):
            return
        view = self._views.pop(index)
        if self._tab_bar is not None:
            try:
                self._tab_bar.removeTab(index)
            except Exception:
                pass
        try:
            self._view_stack.removeWidget(view)
            view.stop()
            view.setParent(None)
            view.deleteLater()
        except Exception:
            pass
        if not self._views:
            # 一个都不剩：留一个空白新标签，行为跟 Edge 一样
            self._add_view("新标签页")
        else:
            self._view = self._views[min(index, len(self._views) - 1)]

    # -------------------------------------------------- 内部
    def ensure_view(self):
        """创建（或返回已有的）浏览器视图。"""
        if self._view is not None:
            return self._view
        target = self._pending_url or self._home_url
        view = self._add_view("新标签页")
        if view is None:
            return None
        try:
            self._view_stack.show()
        except Exception:
            pass
        return view

    def _make_view(self) -> QWidget:
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except Exception as exc:  # noqa: BLE001
            label = QLabel(f"当前环境缺少 QtWebEngine，无法内嵌网页：{exc}")
            label.setObjectName("Muted")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._view = None
            return label

        from . import fdu

        storage = fdu.browser_dir(self._storage_sub)
        try:
            clean_stale_locks(storage)
        except Exception:
            pass
        profile = QWebEngineProfile(self._profile_name, self)
        profile.setPersistentStoragePath(str(storage))
        profile.setCachePath(str(storage / "cache"))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        view = QWebEngineView(self)
        # 提前把原生窗口建出来：这样「第一次显示网页时窗口重建」发生在启动阶段，
        # 而不是用户第一次点开网页的那一下（那一闪就是这么来的）
        try:
            view.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
            view.winId()
        except Exception:
            pass
        view.setStyleSheet(f"QWebEngineView {{ background: {theme.CARD}; }}")
        page = self._make_page(profile, view)
        view.setPage(page)
        try:
            # 先铺上主题底色，避免第一帧出现白/黑闪
            from PySide6.QtGui import QColor

            page.setBackgroundColor(QColor(theme.CARD))
        except Exception:
            pass
        self._apply_browser_theme(view)
        view.loadFinished.connect(self._on_load_finished)
        try:
            page.titleChanged.connect(self._sync_tab_title)
        except Exception:
            pass
        view.urlChanged.connect(self._on_url_changed)
        # 先不导航：等第一次显示出来再加载，避免创建原生窗口时整窗闪一下
        self._view = view
        self._profile = profile
        # 建好后马上套圆角（稍后再算一次，等布局把大小定下来）
        self._apply_view_mask()
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._apply_view_mask)
            QTimer.singleShot(120, self._apply_view_mask)
        except Exception:
            pass
        return view

    def _make_page(self, profile, view):
        """建页面对象，并接管「新窗口」请求。

        eHall 里的「进入服务」是 `target="_blank"`（或 `window.open`），
        QWebEnginePage 默认把这种请求丢掉 —— 点了什么都不发生，看起来像「没反应」。
        这里挂 `newWindowRequested`：开了多标签页就开新标签，没开就在当前网页里打开。
        """
        from PySide6.QtWebEngineCore import QWebEnginePage

        page = QWebEnginePage(profile, view)
        try:
            page.newWindowRequested.connect(self._on_new_window_requested)
        except Exception:
            pass
        return page

    def _on_new_window_requested(self, request) -> None:
        """网页想开新窗口（target=_blank / window.open）：按标签页设置处理。"""
        try:
            url = request.requestedUrl().toString()
        except Exception:
            url = ""
        if not url:
            return
        if self._tabs_enabled:
            self.new_tab(url)
        else:
            self.load(url)

    @staticmethod
    def _apply_browser_theme(view) -> None:
        """按「浏览器风格」设置打开/关闭网页暗色。"""
        from PySide6.QtWebEngineCore import QWebEngineSettings

        from . import theme

        try:
            settings = view.settings()
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.ForceDarkMode, theme.web_dark_mode())
        except Exception:
            pass

    def refresh_browser_theme(self) -> None:
        if self._view is not None:
            self._apply_browser_theme(self._view)

    def _sync_tab_title(self, title: str) -> None:
        if self._tab_bar is None:
            return
        try:
            index = self._tab_bar.currentIndex()
            if index >= 0 and title:
                self._tab_bar.setTabText(index, title[:18])
        except Exception:
            pass

    def _on_load_finished(self, ok: bool) -> None:
        self.status.setText("加载完成" if ok else "加载失败")
        self.loaded.emit(bool(ok))
        self._reveal()
        self._try_uis_autofill()

    def _uis_can_autofill(self) -> bool:
        view = self._view
        if view is None or not self._autofill:
            return False
        try:
            from . import uis_login

            if not uis_login.is_login_url(view.url().toString()):
                return False
            if not uis_login.has_credentials(self._store):
                return False
            return bool(uis_login.autofill_enabled(self._store))
        except Exception:
            return False

    def _try_uis_autofill(self) -> None:
        """遇到统一身份认证的登录页：把保存的学号密码自动填进去。

        新版 UIS 是前端框架渲染的，`loadFinished` 之后还会重渲染一次，第一下填进去
        的值经常被抹掉（于是用户看到的是空输入框 + 「用户名不能为空」）。
        所以这里**多试几次**（0 / 0.6 / 1.5 / 3 秒），填成功就停；
        每次填完都校验值真的在框里，校验通过才点「登录」。
        """
        if not self._uis_can_autofill():
            return
        self._autofill_seq = getattr(self, "_autofill_seq", 0) + 1
        seq = self._autofill_seq
        self._autofill_done = False
        for delay in (0, 600, 1500, 3000):
            QTimer.singleShot(delay, lambda d=delay: self._uis_autofill_once(seq, d))

    def _uis_autofill_once(self, seq: int, delay: int) -> None:
        if seq != getattr(self, "_autofill_seq", 0):
            return                      # 页面已经翻篇了，别再往新页面上填
        if getattr(self, "_autofill_done", False):
            return
        if not self._uis_can_autofill():
            return
        view = self._view
        if view is None:
            return
        try:
            from . import uis_login

            script = uis_login.autofill_script(self._store, click=True)
        except Exception:
            return

        def on_done(result) -> None:
            import json as _json

            if seq != getattr(self, "_autofill_seq", 0):
                return
            payload = {}
            if isinstance(result, str):
                try:
                    payload = _json.loads(result)
                except Exception:
                    payload = {}
            elif isinstance(result, dict):
                payload = result
            if payload.get("ok"):
                self._autofill_done = True
                self.status.setText(
                    "已自动填好统一身份认证并点了「登录」"
                    "（有二次验证就手动过一次）" if payload.get("clicked")
                    else "已自动填好统一身份认证：确认无误后点「登录」")
                self.autofilled.emit(True)
                if payload.get("clicked"):
                    # 点是点了，但页面可能没认（SPA 常见）：过几秒回头看一眼
                    QTimer.singleShot(3000, lambda: self._verify_autofill(seq))
                return
            reason = str(payload.get("reason") or "没找到登录框")
            if delay >= 3000:
                self.status.setText(f"自动填充没成功：{reason}")
                self.autofilled.emit(False)
            else:
                self.status.setText(f"正在重试自动填充…（{reason}）")

        try:
            view.page().runJavaScript(script, on_done)
        except Exception:
            pass

    def _verify_autofill(self, seq: int) -> None:
        """点完「登录」三秒后看一眼：还在登录页就老实说「没生效，手动点一下」。"""
        if seq != getattr(self, "_autofill_seq", 0):
            return
        view = self._view
        if view is None:
            return
        try:
            from . import uis_login

            if uis_login.is_login_url(view.url().toString()):
                self.status.setText(
                    "已填好账号密码，但「登录」好像没生效：请手动点一下登录按钮")
                self.autofilled.emit(False)
        except Exception:
            pass

    # -------------------------------------------------- 首帧遮罩
    def _place_cover(self) -> None:
        view = self._view
        if view is None or self._cover is None:
            return
        try:
            self._cover.setGeometry(view.geometry())
        except Exception:
            pass

    def _cover_visible(self) -> None:
        if self._cover is None or self._revealed:
            return
        self._place_cover()
        self._cover.show()
        self._cover.raise_()
        # 网络太慢也不能一直盖着，8 秒后无论如何都露出来
        try:
            from PySide6.QtCore import QTimer

            if getattr(self, "_cover_timer", None) is None:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.setInterval(8000)
                timer.timeout.connect(self._reveal)
                self._cover_timer = timer
            self._cover_timer.start()
        except Exception:
            pass

    def _reveal(self) -> None:
        """网页有内容了：把遮罩淡出。"""
        if self._revealed or self._cover is None:
            return
        self._revealed = True
        if not self._cover.isVisible():
            self._cover.hide()
            return
        try:
            from PySide6.QtCore import QPropertyAnimation
            from PySide6.QtWidgets import QGraphicsOpacityEffect

            effect = QGraphicsOpacityEffect(self._cover)
            self._cover.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self._cover)
            anim.setDuration(180)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.finished.connect(self._cover.hide)
            anim.start()
            self._reveal_anim = anim
        except Exception:
            self._cover.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_cover()
        self._apply_view_mask()
        self._sync_overlay()

    def _sync_overlay(self) -> None:
        """以前这里会维护四个圆角小浮窗；它们会吃点击、收起侧栏时还会崩，已经移除。

        现在网页就是方角（干净、可点），等有不吃点击的方案再补圆角。
        """
        return

    def _apply_view_mask(self) -> None:
        """保证网页视图**不被遮罩裁切**。

        以前用 setMask 做圆角，但 Windows 高 DPI 下遮罩按设备像素解释，
        会把视口裁小、露出窗口后面的桌面（右侧栏一片透明就是这个原因）。
        现在圆角交给四个 15×15 的角浮窗负责，这里只负责清掉任何残留遮罩。
        """
        view = self._view
        if view is None:
            return
        try:
            view.clearMask()
        except Exception:
            pass

    def _on_url_changed(self, url: QUrl) -> None:
        text = url.toString()
        self.address.setText(text[:120])
        try:
            history = self._view.history() if self._view is not None else None
            self.back_button.setEnabled(bool(history.canGoBack()) if history else False)
        except Exception:
            self.back_button.setEnabled(False)
        # 会话过期时 eHall 会用前端路由跳到登录页（不一定触发 loadFinished），
        # 这里补一刀，免得站着不动就登不进去
        try:
            from . import uis_login

            if uis_login.is_login_url(text) and text != getattr(self, "_last_url", ""):
                self._last_url = text
                self._try_uis_autofill()
        except Exception:
            pass

    # -------------------------------------------------- 对外
    def view(self):
        return self._view

    def profile(self):
        return getattr(self, "_profile", None)

    # -------------------------------------------------- 生命周期
    def _set_lifecycle(self, frozen: bool) -> None:
        """页面不可见时冻结渲染，避免隐藏的网页继续空转吃 CPU。"""
        view = self._view
        if view is None:
            return
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage

            state = (QWebEnginePage.LifecycleState.Frozen if frozen
                     else QWebEnginePage.LifecycleState.Active)
        except Exception:
            return
        try:
            page = view.page()
            if page is not None:
                page.setLifecycleState(state)
        except Exception:
            pass

    def shutdown(self) -> None:
        """退出前把浏览器彻底拆掉——否则 QtWebEngine 关闭时会死锁，窗口关不掉。"""
        for extra in list(getattr(self, "_views", [])):
            try:
                extra.stop()
                extra.setParent(None)
                extra.deleteLater()
            except Exception:
                pass
        try:
            self._views = []
        except Exception:
            pass
        view = self._view
        profile = getattr(self, "_profile", None)
        self._view = None
        self._profile = None
        if view is None:
            if profile is not None:
                try:
                    profile.deleteLater()
                except Exception:
                    pass
            return
        try:
            view.stop()
        except Exception:
            pass
        page = None
        try:
            page = view.page()
            view.setPage(None)
        except Exception:
            page = None
        try:
            view.setParent(None)
        except Exception:
            pass
        try:
            view.deleteLater()
        except Exception:
            pass
        if page is not None:
            try:
                page.deleteLater()
            except Exception:
                pass
        if profile is not None:
            try:
                profile.deleteLater()
            except Exception:
                pass

    def go_home(self) -> None:
        self.ensure_view()
        self._started = True
        self._pending_url = None
        if self._view is not None:
            self._view.setUrl(QUrl(self._home_url))

    def reload(self) -> None:
        self.ensure_view()
        if not self.isVisible():
            # 页面被隐藏时刷新，回来时经常只剩一片空白（原生子窗口没重绘），
            # 所以先记下来，等 showEvent 里再刷
            self._pending_reload = True
            return
        self._pending_reload = False
        if self._view is not None:
            self._view.reload()
            self._nudge_view()

    def load(self, url: str) -> None:
        self.ensure_view()
        self._started = True
        self._pending_url = None
        if self._view is not None:
            self._view.setUrl(QUrl(url))

    def _nudge_view(self) -> None:
        """让原生子窗口重新贴一次位置，逼 Windows 重画（否则偶尔一片空白）。"""
        view = self._view
        if view is None:
            return
        try:
            from PySide6.QtCore import QTimer

            view.show()
            view.raise_()
            geometry = view.geometry()
            view.setGeometry(geometry.adjusted(0, 0, 1, 0))
            view.update()
            QTimer.singleShot(0, lambda: view.setGeometry(geometry))
            QTimer.singleShot(80, view.update)
        except Exception:
            pass

    def _call(self, name: str) -> None:
        if self._view is None:
            return
        try:
            history = self._view.history()
            if name == "back":
                history.back()
                return
            if name == "forward":
                history.forward()
                return
            getattr(self._view, name)()
        except Exception:
            try:
                getattr(self._view, name)()
            except Exception:
                pass

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_compact(self, compact: bool = True) -> None:
        """放进窄侧栏时把地址栏那一行收起来，省空间（全屏按钮交给外层）。"""
        for widget in (self.back_button, self.reload_button, self.address,
                       self.home_button, self.fullscreen_button, self.status):
            try:
                widget.setVisible(not compact)
            except Exception:
                pass
        self._compact = bool(compact)

    def showEvent(self, event):
        super().showEvent(event)
        if self._view is None:
            self.ensure_view()
        self._set_lifecycle(False)
        self._cover_visible()
        self._start_pending_load()
        self._nudge_view()
        if getattr(self, "_pending_reload", False):
            self._pending_reload = False
            try:
                from PySide6.QtCore import QTimer

                QTimer.singleShot(150, self.reload)
            except Exception:
                self.reload()

    def _start_pending_load(self) -> None:
        if self._started or self._view is None:
            return
        url = self._pending_url
        if not url:
            return
        self._started = True
        self._pending_url = None
        try:
            self.status.setText("正在打开…")
            self._view.setUrl(QUrl(url))
        except Exception:
            pass

    def hideEvent(self, event):
        # 先停掉加载再冻结：直接 hide 一个正在跑的 WebEngine 视图在 Windows 上会崩
        try:
            if self._view is not None:
                self._view.stop()
        except Exception:
            pass
        super().hideEvent(event)
        self._set_lifecycle(True)

    # -------------------------------------------------- 全屏（应用内）
    def sync_fullscreen_state(self, on: bool) -> None:
        self._fullscreen = bool(on)
        try:
            self.fullscreen_button.setText("还原" if on else "全屏")
        except Exception:
            pass

    def _toggle_fullscreen(self) -> None:
        window = self.window()
        setter = getattr(window, "set_panel_fullscreen", None)
        if callable(setter):
            setter(not self._fullscreen, self)
            self._fullscreen = not self._fullscreen
            self.fullscreen_button.setText("还原" if self._fullscreen else "全屏")
            return
        # 兜底：只在本页内隐藏地址栏
        self._fullscreen = not self._fullscreen
        self.fullscreen_button.setText("还原" if self._fullscreen else "全屏")
        for child in (self.address, self.back_button, self.reload_button,
                      self.home_button, self.status):
            child.setVisible(not self._fullscreen)
