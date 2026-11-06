# -*- coding: utf-8 -*-
"""仪表板：小组件（Apple 风格）网格 —— 今天、天气、日程、通知、作业、ehall 卡片。

放在左侧导航最上方，打开应用先看到「今天要干什么」。
* 每块内容都是一张**小组件卡片**（圆角 + 彩色图标徽标 + 大号数值）；
* 4 列网格：今天 / 天气 各占 2 列，即将到来占 2 列，教务信息和最近作业各 1 格，
  ehall 的卡片各占 1 格（窄窗口自动缩，不会叠在一起）；
* 「空教室」这张卡是**可交互**的：先选教学楼、再选教室，点「查看」才去 ehall 查
  （ehall 那边本来就是这个流程：选楼 → 选教室 → 查询）；
* 所有联网内容都是后台线程 / 后台页面 + 优雅失败：拿不到就显示占位和「重试」。
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel,
                             QVBoxLayout, QWidget)

from . import dashboard, ehall_bridge, icons, theme
from .classroom_view import (
    CARD_VIEW_HEIGHT, HEADER, PAGE_ROWS, ROW_HEIGHT, ClassroomBoard, PagerBar,
    TIME_CHOICES, open_room_window, time_window, window_summary,
)
from .dashboard_widgets import (
    HeroWidget, ListWidget, SMALL_HEIGHT, WidgetCard, big_label, SectionRow,
)
from .theme import MUTED, TEXT
from .widgets import Select, SoftButton, clear_layout, make_scroll

EVENT_COLORS = {"teal": "#30B0C7", "amber": "#FF9500", "violet": "#AF52DE",
                "rose": "#FF2D55", "sky": "#007AFF"}

GRID_COLUMNS = 4
GRID_GAP = 14
ALL_ROOMS = "全部教室"


class EhallWidget(WidgetCard):
    """一张 ehall 小组件：状态在右上角，数值一行，下面两三条灰字。"""

    def __init__(self, spec: Dict[str, Any], parent=None):
        super().__init__(spec["title"], spec.get("glyph") or icons.INFO,
                         spec.get("tone") or "teal", "", SMALL_HEIGHT, parent)
        self.card_id = spec["id"]
        self._hint = str(spec.get("hint") or "")
        self.value_label = big_label("", 15.0)
        self.value_label.setWordWrap(True)
        self.add_widget(self.value_label)
        self._lines_host = QWidget()
        self._lines_layout = QVBoxLayout(self._lines_host)
        self._lines_layout.setContentsMargins(0, 0, 0, 0)
        self._lines_layout.setSpacing(1)
        self.add_widget(self._lines_host)
        # 一行小字提示（没填地址时才有；完整说明放 tooltip 里）
        self._hint_label = QLabel(self._short_hint())
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet(
            f"color: {MUTED}; font-size: 8.5pt; background: transparent;")
        self._hint_label.setToolTip(self._hint)
        self.add_widget(self._hint_label)
        footer = QHBoxLayout()
        footer.setSpacing(6)
        self.open_button = SoftButton("打开", "ghost", height=24, min_width=56,
                                      point_size=8.2)
        footer.addWidget(self.open_button)
        if spec["id"] == "empty_room":
            self.room_button = SoftButton("空教室", "ghost", height=24,
                                          min_width=64, point_size=8.2)
            footer.addWidget(self.room_button)
        footer.addStretch(1)
        host = QWidget()
        host.setLayout(footer)
        self.add_widget(host)
        # 兼容老接口（设置/测试里按这些名字取）
        self._value_label = self.value_label
        self._state_label = self.meta_label

    def _short_hint(self) -> str:
        """小组件里只放一句短的（完整说明在 tooltip）。"""
        table = {
            "card": "填一卡通页面的地址",
            "bus": "填校车时刻表页面的地址",
            "sports": "填刷锻页面的地址",
            "library_people": "填在馆人数页面的地址",
            "library_booking": "填座位 / 研讨间预约地址",
            "empty_room": "填空闲教室查询的地址",
            "ehall_todo": "登录 eHall 后同步即可",
        }
        return table.get(self.card_id, "填这个服务的页面地址")

    def set_value(self, text: str, size: float = 15.0) -> None:
        font = self.value_label.font()
        font.setPointSizeF(size)
        self.value_label.setFont(font)
        self.value_label.setText(text)


class EmptyRoomWidget(WidgetCard):
    """空教室小组件：先选教学楼、再选教室，点「查看」才去 ehall 查。

    ehall 的「空闲教室查询」就是先选楼、再选教室、点查询才出表格，所以这里照它的
    流程来：选完点「查看空教室」→ 后台页面替你点选 + 点查询 + 抓表格。
    """

    query_requested = Signal(str, str, str)     # 校区, 教学楼, 教室
    open_requested = Signal()

    def __init__(self, store, parent=None):
        super().__init__("空教室", icons.CALENDAR, "teal", "", 260, parent)
        self.store = store
        self._buildings_by_campus: Dict[str, List[str]] = {}
        self.campus_box = Select()
        self.campus_box.setFixedHeight(26)
        self.campus_box.setToolTip("先选校区")
        self.building_box = Select()
        self.building_box.setFixedHeight(26)
        self.building_box.setToolTip("再选这个校区里的教学楼")
        self.room_box = Select()
        self.room_box.setFixedHeight(26)
        self.room_box.setToolTip("最后选教室（不选就是整栋楼）")
        self.campus_box.currentIndexChanged.connect(self._on_campus_changed)
        self.building_box.currentIndexChanged.connect(self._on_building_changed)
        for box in (self.campus_box, self.building_box, self.room_box):
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            box.setMinimumContentsLength(3)
            box.setMinimumWidth(64)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.campus_box, 1)
        row.addWidget(self.building_box, 1)
        row.addWidget(self.room_box, 1)
        host = QWidget()
        host.setLayout(row)
        self.add_widget(host)
        # 选完条件按回车 = 点「查看」（用户要求：别非要自己点）
        for box, tip in ((self.campus_box, "先选校区；选完按回车直接查"),
                         (self.building_box, "再选教学楼；按回车直接查"),
                         (self.room_box, "最后选教室（不选就是整栋楼）；按回车直接查")):
            box.setToolTip(tip)
            box.installEventFilter(self)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.view_button = SoftButton("查看", "primary", height=24, min_width=56,
                                      point_size=8.4)
        self.view_button.setToolTip("按选中条件去 ehall 查，右边画出教室占用情况")
        actions.addWidget(self.view_button)
        self.zoom_button = SoftButton("放大查看", "ghost", height=24,
                                      min_width=76, point_size=8.2)
        self.zoom_button.setToolTip("到「空教室」页看完整的时间轴")
        actions.addWidget(self.zoom_button)
        self.open_button = SoftButton("在网页里查", "ghost", height=24,
                                      min_width=84, point_size=8.2)
        self.open_button.setToolTip("打开「空教室」页，用学校的网页自己选")
        actions.addWidget(self.open_button)
        actions.addStretch(1)
        action_host = QWidget()
        action_host.setLayout(actions)
        self.add_widget(action_host)

        # 时间轴：一行一个教室，红块=上课，浅色=空闲；一页 10 间，多的翻页（不截断）
        self.board = ClassroomBoard()
        self.board.set_max_height(CARD_VIEW_HEIGHT)
        self.add_widget(self.board)
        self.pager = PagerBar(PAGE_ROWS)
        self.pager.changed.connect(lambda _page: self._render_page())
        self.add_widget(self.pager)

        self.value_label = big_label("", 13.0)
        self.value_label.setWordWrap(True)
        self.value_label.setVisible(False)
        self._lines_host = QWidget()
        self._lines_layout = QVBoxLayout(self._lines_host)
        self._lines_layout.setContentsMargins(0, 0, 0, 0)
        self._lines_layout.setSpacing(1)
        self.add_widget(self._lines_host)
        self._hint_label = QLabel("先选教学楼，点「查看」")
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet(
            f"color: {MUTED}; font-size: 8.5pt; background: transparent;")
        self.add_widget(self._hint_label)
        # 兼容老接口
        self._value_label = self.value_label
        self._state_label = self.meta_label
        self.count_label = self._hint_label
        self.result_layout = self.board.timeline
        self.view_button.clicked.connect(self._emit_query)
        self.zoom_button.clicked.connect(self._open_zoom)
        self.open_button.clicked.connect(self.open_requested.emit)
        self._compat_set_rooms = True
        self._last_rooms: List[Dict[str, Any]] = []
        self._last_payload: Dict[str, Any] = {}

    def _open_zoom(self) -> None:
        """「放大查看」：弹一个占屏幕大半的窗口看完整时间轴。"""
        if not self._last_rooms:
            self.open_requested.emit()
            return
        subtitle = self._hint_label.text() if self._hint_label else ""
        try:
            open_room_window(self._last_rooms, self.building() or "空教室",
                             subtitle, self)
        except Exception:
            self.open_requested.emit()

    def _emit_query(self) -> None:
        self.query_requested.emit(self.campus(), self.building(), self.room())

    def eventFilter(self, watched, event):
        """下拉框上按回车 → 直接查（跟点「查看」一样）。"""
        try:
            from PySide6.QtCore import QEvent

            boxes = (getattr(self, "campus_box", None),
                     getattr(self, "building_box", None),
                     getattr(self, "room_box", None))
            if watched in boxes and event.type() == QEvent.Type.KeyPress \
                    and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._emit_query()
                return True
        except Exception:
            pass
        return super().eventFilter(watched, event)

    def _on_campus_changed(self, _index: int = -1) -> None:
        campus = self.campus()
        names = self._buildings_by_campus.get(campus) or []
        current = self.building_box.currentData() or ""
        self.building_box.blockSignals(True)
        self.building_box.clear()
        for name in names:
            self.building_box.addItem(str(name), str(name))
        index = self.building_box.findData(current)
        self.building_box.setCurrentIndex(max(0, index))
        self.building_box.blockSignals(False)
        self._on_building_changed()

    def _on_building_changed(self, _index: int = -1) -> None:
        # 换教学楼：教室下拉换成这栋楼记过的教室
        try:
            from . import dashboard as _dashboard

            self.set_rooms(_dashboard.empty_room_rooms(self.store,
                                                       self.building()))
        except Exception:
            pass

    # -------------------------------------------------- 选项（校区 → 教学楼 → 教室）
    def set_campuses(self, mapping: Dict[str, List[str]]) -> None:
        self._buildings_by_campus = {
            str(campus): dashboard.clean_buildings(names)
            for campus, names in (mapping or {}).items()}
        current = self.campus_box.currentData() or ""
        self.campus_box.blockSignals(True)
        self.campus_box.clear()
        for campus in dashboard.CAMPUSES:
            if self._buildings_by_campus.get(campus):
                self.campus_box.addItem(campus, campus)
        for campus in self._buildings_by_campus:
            if campus not in dashboard.CAMPUSES:
                self.campus_box.addItem(campus, campus)
        index = self.campus_box.findData(current)
        self.campus_box.setCurrentIndex(max(0, index))
        self.campus_box.blockSignals(False)
        self._on_campus_changed()

    def set_buildings(self, buildings: List[str]) -> None:
        """兼容老接口：给一堆教学楼时按楼名归到各校区。"""
        mapping: Dict[str, List[str]] = {}
        for name in dashboard.clean_buildings(buildings):
            mapping.setdefault(dashboard.guess_campus(name), []).append(name)
        self.set_campuses(mapping)

    def campus(self) -> str:
        return str(self.campus_box.currentData() or "")

    def building(self) -> str:
        return str(self.building_box.currentData() or "")

    def time_key(self) -> str:
        """不再选时段：一律整天（1–14 节）。"""
        return "all"

    def room(self) -> str:
        data = self.room_box.currentData()
        if data in ("", None, ALL_ROOMS):
            return ""
        return str(data)

    def set_rooms(self, rooms: List[str]) -> None:
        current = self.room_box.currentData() or ""
        self.room_box.blockSignals(True)
        self.room_box.clear()
        self.room_box.addItem(ALL_ROOMS, ALL_ROOMS)
        # 只放真教室名（「8:00」「教室 00:00」这种是时段，不是教室）
        for name in dashboard.clean_rooms(rooms):
            self.room_box.addItem(str(name), str(name))
        index = self.room_box.findData(current)
        self.room_box.setCurrentIndex(max(0, index))
        self.room_box.blockSignals(False)

    def set_status(self, text: str) -> None:
        self.meta_label.setText(text)
        self._hint_label.setText(text)

    def set_loading(self, loading: bool) -> None:
        self.view_button.setEnabled(not loading)
        self.view_button.setText("查询中…" if loading else "查看")

    def show_rows(self, rows: List[List[str]], value: str = "") -> None:
        """老接口（测试里还在用）：把行拼成文字，塞进小字区。"""
        clear_layout(self._lines_layout)
        self.value_label.setText(value)
        shown = rows[:4]
        for row in shown:
            cells = [str(cell) for cell in row if str(cell).strip()]
            if not cells:
                continue
            label = QLabel(" · ".join(cells[:4]))
            label.setWordWrap(True)
            label.setStyleSheet(
                f"color: {MUTED}; font-size: 8.5pt; background: transparent;")
            self._lines_layout.addWidget(label)
        self._lines_host.setVisible(bool(shown))

    def apply_result(self, payload: Dict[str, Any]) -> None:
        """把桥查回来的「教室 × 节次」画成时间轴（一页 10 间，多的翻页）。"""
        payload = payload if isinstance(payload, dict) else {}
        self.set_loading(False)
        low, high = 0, 13            # 不再选时段：一律整天
        self.board.set_window(low, high)
        rooms = list(payload.get("rooms") or [])
        if not rooms:
            rooms = self._last_rooms
        self._last_rooms = rooms
        self._last_payload = payload
        self.pager.set_total(len(rooms), 0)
        self._render_page()
        clear_layout(self._lines_layout)
        self._lines_host.setVisible(False)
        if not payload.get("ok"):
            self.set_status("没查到")
            self._hint_label.setText(str(payload.get("error") or "没查到教室安排"))
            return
        where = str(payload.get("building") or self.building() or "")
        self.set_status("刚刚更新")
        summary = window_summary(rooms, low, high, "all")
        pages = self.pager.pages() if self.pager.isVisible() else 1
        self._hint_label.setText(
            "%s：%s（红=上课，浅色=空着；%s）"
            % (where or "查询结果", summary,
               ("共 %d 页，用下面的「上一页 / 下一页」翻" % pages)
               if pages > 1 else "「放大查看」看全"))

    def _render_page(self) -> None:
        """只画当前这一页的教室（没翻页时就是全部）。"""
        rows = max(1, self.pager.page_rows())
        start = self.pager.page() * rows
        page_rooms = self._last_rooms[start:start + rows]
        # 让这一页的高度刚好放得下，别让卡片再把最后几行切掉
        need = HEADER + max(1, len(page_rooms)) * ROW_HEIGHT + 8
        self.board.setFixedHeight(min(CARD_VIEW_HEIGHT, need))
        self.board.set_rooms(page_rooms)
        # 卡片也跟着长高，别让翻页条/统计把小字压在教室行上
        try:
            self.setMinimumHeight(min(760, 150 + int(self.board.height())))
            self.updateGeometry()
        except Exception:
            pass


class DashboardPage(QWidget):
    """一屏看完今天（小组件网格）。"""

    open_ehall = Signal(str)          # 请求主窗口打开 ehall 页面（带 URL）
    open_homework = Signal()
    open_homework_url = Signal(str)   # 请求主窗口在「作业」页里打开某个 eLearning 地址
    open_classroom = Signal()
    weather_ready = Signal(dict)
    assignments_ready = Signal(dict)  # 后台同步完作业

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._cards: Dict[str, Card] = {}
        self.bridge = None
        self._assign_busy = False
        self._assign_asked = False
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 18)
        root.setSpacing(10)

        # -------------------------------------------------- 顶部
        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("仪表板")
        title.setObjectName("H1")
        self.subtitle = QLabel("今天要做什么，一眼看完")
        self.subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.ehall_status = QLabel("")
        self.ehall_status.setObjectName("Tiny")
        self.ehall_status.setStyleSheet(f"color: {MUTED};")
        header.addWidget(self.ehall_status)
        self.login_button = SoftButton("登录 eHall", "ghost", height=32,
                                       min_width=92, point_size=8.6)
        self.login_button.setToolTip(
            "用 设置 → 统一身份认证 里存好的学号密码在后台登录一次"
            "（cookie 会持久保存，之后就不用再登）")
        self.login_button.clicked.connect(self.login_ehall)
        header.addWidget(self.login_button)
        self.sync_button = SoftButton("同步 ehall", "ghost", glyph=icons.REFRESH,
                                      height=32, min_width=104, point_size=8.6)
        self.sync_button.clicked.connect(self.sync_ehall)
        header.addWidget(self.sync_button)
        self.refresh_button = SoftButton("刷新", "ghost", glyph=icons.REFRESH,
                                         height=32, min_width=76, point_size=8.6)
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        # -------------------------------------------------- 小组件区
        self.scroll, _body, self.content = make_scroll()
        self.content.setSpacing(10)
        root.addWidget(self.scroll, 1)

        self._build_today_grid()
        self._build_ehall_grid()
        self.content.addStretch(1)

        self.weather_ready.connect(self._on_weather)
        self.assignments_ready.connect(self._on_assignments)
        self.refresh()

    # -------------------------------------------------- 网格工具
    @staticmethod
    def _grid(columns: int = GRID_COLUMNS) -> tuple:
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(GRID_GAP)
        grid.setVerticalSpacing(GRID_GAP)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        return host, grid

    # -------------------------------------------------- 今天 / 天气 / 列表
    def _build_today_grid(self) -> None:
        self.content.addWidget(SectionRow("今天", "日程 · 天气 · 教务与作业"))

        host, grid = self._grid()
        self.today_card = HeroWidget("今天", icons.CALENDAR, "teal")
        # 「28」这个大数字 + 右边两行（星期三 / 2026 年 10 月 28 日 · 第 44 周）
        self.today_day = big_label("", 34.0)
        self.today_card.value_label.setVisible(False)
        self.today_card.content_layout.insertWidget(0, self.today_day)
        self.today_weekday = QLabel("")
        self.today_weekday.setStyleSheet(
            f"color: {TEXT}; font-size: 11pt; background: transparent;")
        self.today_line = QLabel("")
        self.today_line.setStyleSheet(
            f"color: {MUTED}; font-size: 9pt; background: transparent;")
        self.today_line.setWordWrap(True)
        self.today_card.sub_label.setVisible(False)
        self.today_card.hint_label.setVisible(False)
        box = QVBoxLayout()
        box.setSpacing(1)
        box.addWidget(self.today_weekday)
        box.addWidget(self.today_line)
        holder = QWidget()
        holder.setLayout(box)
        self.today_card.content_layout.insertWidget(1, holder)
        self.today_greeting = QLabel("")
        self.today_greeting.setWordWrap(True)
        self.today_greeting.setStyleSheet(
            f"color: {MUTED}; font-size: 9.5pt; background: transparent;")
        self.today_card.content_layout.insertWidget(2, self.today_greeting)
        self.today_card.footer.setVisible(False)
        # 老接口：有些地方按 today_big 取「今天日期」
        self.today_big = self.today_line
        grid.addWidget(self.today_card, 0, 0, 1, 2)

        self.weather_card = HeroWidget("天气", icons.INFO, "sky", "上海")
        # 大号温度 + 右边一列：天气描述 / 湿度风速
        self.weather_label = self.weather_card.value_label
        self.weather_label.setStyleSheet(
            f"color: {TEXT}; background: transparent;")
        self.weather_desc = self.weather_card.sub_label
        self.weather_desc.setStyleSheet(
            f"color: {TEXT}; font-size: 11pt; background: transparent;")
        self.weather_hint = self.weather_card.hint_label
        self.weather_hint.setWordWrap(True)
        self.weather_hint.setStyleSheet(
            f"color: {MUTED}; font-size: 9pt; background: transparent;")
        self.weather_card.footer.setVisible(False)
        self.retry_button = SoftButton("重试", "ghost", height=24, min_width=52,
                                       point_size=8.2)
        self.retry_button.setToolTip("重新获取一次天气")
        self.retry_button.clicked.connect(lambda: self._fetch_weather(force=True))
        # 放到头部右上角，正文里只留温度与细节
        self.weather_card.header.addWidget(self.retry_button, 0,
                                           Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.weather_card, 0, 2, 1, 2)

        self.upcoming_card = ListWidget("即将到来", icons.CLOCK, "violet",
                                        "未来 3 天", 190)
        self.upcoming_host = self.upcoming_card.rows
        self.upcoming_layout = self.upcoming_card.rows_layout
        grid.addWidget(self.upcoming_card, 1, 0, 1, 2)

        self.notices_card = ListWidget("教务信息", icons.BELL, "amber",
                                       "最近 5 条", SMALL_HEIGHT)
        self.notices_host = self.notices_card.rows
        self.notices_layout = self.notices_card.rows_layout
        grid.addWidget(self.notices_card, 1, 2, 1, 1)

        self.assignments_card = ListWidget("最近作业", icons.DOCUMENT, "rose",
                                           "来自 eLearning", SMALL_HEIGHT)
        self.assign_host = self.assignments_card.rows
        self.assign_layout = self.assignments_card.rows_layout
        grid.addWidget(self.assignments_card, 1, 3, 1, 1)

        # 「同步」放在卡片右上角：dash 的 To Do 随时会变，点一下就能重读
        self.sync_homework_button = SoftButton("同步", "ghost", height=24,
                                               min_width=52, point_size=8.2)
        self.sync_homework_button.setToolTip(
            "从 eLearning 的仪表板（elearning.fudan.edu.cn/dash）右侧 To Do "
            "读一次作业与截止时间")
        self.sync_homework_button.clicked.connect(
            lambda: self.sync_homework(force=True))
        self.assignments_card.header.addWidget(
            self.sync_homework_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.open_homework_button = SoftButton("打开作业页", "ghost", height=24,
                                               min_width=88, point_size=8.2)
        self.open_homework_button.clicked.connect(self.open_homework.emit)
        self.assignments_card.add_widget(self.open_homework_button,
                                        Qt.AlignmentFlag.AlignLeft)
        self.content.addWidget(host)

    # -------------------------------------------------- ehall 卡片
    def _build_ehall_grid(self) -> None:
        self.content.addWidget(SectionRow(
            "ehall 数据", "登录一次 eHall 就能取；地址在 设置 → 仪表板 里填"))
        host, grid = self._grid()
        self._ehall_cards: Dict[str, WidgetCard] = {}
        for position, spec in enumerate(dashboard.EHALL_CARDS):
            if spec["id"] == "empty_room":
                card = EmptyRoomWidget(self.store)
                card.open_requested.connect(self.open_classroom.emit)
                card.query_requested.connect(self._query_classroom)
                card.set_campuses(dashboard.campus_buildings_map(self.store))
                card.set_rooms(dashboard.empty_room_rooms(
                    self.store, card.building()))
                self.empty_room_card = card
                self._ehall_cards[spec["id"]] = card
                continue                 # 空教室单独占一整行（时间轴要宽）
            card = EhallWidget(spec)
            card.open_button.clicked.connect(
                lambda _=False, cid=spec["id"]: self._open_card(cid))
            self._ehall_cards[spec["id"]] = card
            grid.addWidget(card, position // GRID_COLUMNS,
                           position % GRID_COLUMNS)
        self.content.addWidget(host)

        # 空教室：整行 4 列（教室多、节次多，太窄看不清），高度上限见 CARD_VIEW_HEIGHT
        self.content.addWidget(SectionRow(
            "空教室", "选教学楼 → 查看；「放大查看」可以铺满屏幕看"))
        wide_host, wide_grid = self._grid()
        wide_grid.addWidget(self.empty_room_card, 0, 0, 1, GRID_COLUMNS)
        self.content.addWidget(wide_host)

    def _open_card(self, card_id: str) -> None:
        url = dashboard.source_url(self.store, card_id) or dashboard.EHALL_HOME
        self.open_ehall.emit(url)

    # -------------------------------------------------- 空教室（交互）
    def _query_classroom(self, campus: str, building: str = "",
                         room: str = "") -> None:
        card = getattr(self, "empty_room_card", None)
        if card is None:
            return
        if not dashboard.source_url(self.store, "empty_room"):
            card.set_status("还没填地址")
            card._hint_label.setText("去 设置 → 仪表板 填空闲教室查询页面的地址")
            self.ehall_status.setText(
                "去 设置 → 仪表板 填空闲教室查询页面的地址")
            return
        if self.bridge is None:
            card.set_status("同步不可用")
            return
        card.set_loading(True)
        card.set_status("查询中…")
        try:
            self.bridge.classroom_query(building, room, "all", campus=campus)
        except Exception as exc:  # noqa: BLE001
            card.set_loading(False)
            card.set_status("查询失败")
            card._hint_label.setText("查询失败：%s" % str(exc)[:50])

    def _on_classroom_ready(self, payload: Dict[str, Any]) -> None:
        card = getattr(self, "empty_room_card", None)
        if card is None:
            return
        payload = payload if isinstance(payload, dict) else {}
        card.set_loading(False)
        if "selects" in payload and payload.get("selects"):
            options = payload["selects"][0].get("options") or []
            if options:
                card.set_buildings(options)
                dashboard.set_empty_room_buildings(self.store, options)
                # 顺手记住「这个校区有哪些楼」，下次切校区就能对上
                campus = str(payload.get("campus") or card.campus() or "")
                if campus:
                    dashboard.set_campus_buildings(self.store, campus, options)
        card.apply_result(payload)
        if payload.get("ok"):
            # 把这一批教室名记下来，下次「教室」下拉就能选
            names = [str(item.get("name") or "").strip()
                     for item in (payload.get("rooms") or [])]
            building = str(payload.get("building") or "")
            names = [name for name in names if name]
            if building and names:
                known = dashboard.empty_room_rooms(self.store, building)
                merged = list(dict.fromkeys(names + known))
                dashboard.set_empty_room_rooms(self.store, building, merged)
                card.set_rooms(merged)
        else:
            card.setToolTip(str(payload.get("error") or ""))

    # -------------------------------------------------- 刷新
    def refresh(self) -> None:
        info = dashboard.today_info()
        events_today = self._today_events()
        weekday = info["weekday"]
        self.today_day.setText(str(int(info["date"][-2:])))
        self.today_weekday.setText("%s · 第 %d 周"
                                  % (weekday, info["week_number"]))
        self.today_line.setText(info["ymd"])
        self.today_greeting.setText("%s · 今天 %d 个日程"
                                    % (info["greeting"], len(events_today)))
        self.today_card.set_meta(info["date"])
        self._render_upcoming()
        self._render_notices()
        self._render_assignments()
        self._render_ehall()
        self._maybe_load_room_options()
        self._fetch_weather()
        self._maybe_sync_assignments()

    def _maybe_load_room_options(self) -> None:
        """配了空教室地址的话，顺手读一次「教学楼」下拉（只读一次）。"""
        if getattr(self, "_room_options_asked", False):
            return
        if self.bridge is None:
            return
        if not dashboard.source_url(self.store, "empty_room"):
            return
        self._room_options_asked = True
        try:
            self.bridge.classroom_options()
        except Exception:
            pass

    def _today_events(self) -> List[Dict[str, Any]]:
        try:
            return self.store.events_on(str(dashboard.today_info()["date"]))
        except Exception:
            return []

    def _fetch_weather(self, force: bool = False) -> None:
        self.weather_label.setText("—")
        self.weather_desc.setText("正在获取天气…")
        self.weather_hint.setText("")

        def work() -> None:
            result = dashboard.weather(self.store, force=force)
            self.weather_ready.emit(result)

        threading.Thread(target=work, name="lumina-dashboard-weather",
                         daemon=True).start()

    def _on_weather(self, result: Dict[str, Any]) -> None:
        text = str(result.get("text") or "")
        if not text:
            self.weather_label.setText("—")
            self.weather_desc.setText("天气暂时拿不到")
            self.weather_hint.setText(str(result.get("error") or ""))
            return
        # 「上海 多云 · 22°C · 湿度 68% · 风速 9 km/h」→ 大号 22°C / 多云 / 细节
        parts = [part.strip() for part in text.split("·")]
        head = parts[0] if parts else text
        desc = head.replace(dashboard.CITY, "").strip() or head
        temperature = ""
        detail: List[str] = []
        for part in parts[1:]:
            if "°C" in part and not temperature:
                temperature = part
            else:
                detail.append(part)
        self.weather_label.setText(temperature or desc)
        self.weather_desc.setText(desc if temperature else "")
        self.weather_hint.setText(" · ".join(detail))
        self.weather_card.set_meta(dashboard.CITY)

    # -------------------------------------------------- 列表渲染
    def _render_upcoming(self) -> None:
        card = self.upcoming_card
        card.clear_rows()
        items = dashboard.upcoming_events(self.store, limit=5)
        if not items:
            card.add_hint("未来 3 天没有日程")
            return
        for item in items:
            begin = str(item.get("begin") or "")
            if item.get("ongoing"):
                # 已经开始了：写「进行中」，不再显示开始时间（用户报过这条）
                meta, active = "进行中", True
            else:
                meta = "%s %s" % (str(item.get("day") or "")[-5:], begin)
                active = False
            card.add_row(str(item.get("title") or "（未命名）"), meta.strip(),
                         str(item.get("color") or "violet"), meta_active=active)

    def _render_notices(self) -> None:
        card = self.notices_card
        card.clear_rows()
        items = dashboard.recent_notices(self.store, limit=5)
        if not items:
            card.add_hint("还没有抓到通知（「通知」页开启监控后会存到这里）")
            return
        fallback = dashboard.source_url(self.store, "notice") or \
            dashboard.EHALL_HOME
        for item in items:
            when = str(item.get("published_at") or item.get("fetched_at") or "")[:10]
            url = str(item.get("url") or "").strip() or fallback
            card.add_row(str(item.get("title") or ""), when, "amber",
                         url=url, on_open=self.open_ehall.emit)

    def _render_assignments(self, result: Dict[str, Any] = None) -> None:
        card = self.assignments_card
        card.clear_rows()
        if not isinstance(result, dict):
            result = dashboard.assignments(self.store, limit=4)
        if not result.get("ok"):
            card.add_hint(str(result.get("error") or ""))
            return
        for item in (result.get("items") or [])[:4]:
            url = str(item.get("url") or "").strip()
            line = card.add_row(
                str(item.get("title") or item.get("name") or "作业"),
                str(item.get("due") or item.get("deadline") or ""), "rose",
                url=url or None,
                # 作业是 eLearning 的东西：点开要在「作业」页里打开，不是 ehall
                on_open=self.open_homework_url.emit if url else None)
            # 卡片窄，课程名和链接放 tooltip 里
            bits = [str(item.get("course") or "").strip()]
            due = str(item.get("due") or "").strip()
            if due:
                bits.append("截止 %s" % due)
            if url:
                bits.append(url)
            tip = "\n".join(bit for bit in bits if bit)
            if tip:
                line.setToolTip(tip)

    # -------------------------------------------------- 作业同步（eLearning dash）
    def _maybe_sync_assignments(self) -> None:
        """打开仪表板时，缓存没有或太旧就自己去 eLearning 读一次。

        只在一开始问一次（`_assign_asked`），而且后台线程里跑；冒烟/测试
        模式不联网，免得拖慢启动。
        """
        if self._assign_asked or os.environ.get("LUMINA_SMOKE"):
            return
        self._assign_asked = True
        age = dashboard.assignments_age(self.store)
        if age is not None and age < dashboard.ASSIGN_TTL:
            return
        self.sync_homework(force=False)

    def sync_homework(self, force: bool = True) -> None:
        """去 eLearning 的 dash（To Do）读作业，读完刷这张卡。"""
        if self._assign_busy:
            return
        self._assign_busy = True
        self.sync_homework_button.setEnabled(False)
        self.sync_homework_button.setText("同步中…")
        self.assignments_card.set_meta("同步中…")

        def work() -> None:
            result = dashboard.sync_assignments(self.store)
            self.assignments_ready.emit(result if isinstance(result, dict) else {})

        threading.Thread(target=work, name="lumina-dashboard-homework",
                         daemon=True).start()

    def _on_assignments(self, result: Dict[str, Any]) -> None:
        result = result if isinstance(result, dict) else {}
        self._assign_busy = False
        self.sync_homework_button.setEnabled(True)
        self.sync_homework_button.setText("同步")
        if result.get("ok"):
            self.assignments_card.set_meta("来自 eLearning")
            self._render_assignments(result)
            if not result.get("count"):
                # To Do 空着：缓存里还有（作业页读到的、或上次同步的）就继续显示
                cached = dashboard.assignments(self.store, limit=4)
                if cached.get("ok"):
                    self._render_assignments(cached)
                    self.assignments_card.set_meta("To Do 空着，显示上次的")
                else:
                    self.assignments_card.clear_rows()
                    self.assignments_card.add_hint(
                        str(result.get("error")
                            or "eLearning 的 To Do 里现在没有要交的作业"))
            return
        self.assignments_card.set_meta("同步失败")
        self.assignments_card.clear_rows()
        self.assignments_card.add_hint(
            str(result.get("error") or "同步失败，稍后再试"))
        # 有旧缓存就继续显示旧的，别把已有内容清空
        if dashboard.assignments(self.store, limit=1).get("ok"):
            self._render_assignments()

    # -------------------------------------------------- ehall 同步
    def attach_bridge(self, bridge) -> None:
        """主窗口把后台取数桥接进来（共用 ehall 的登录态）。"""
        self.bridge = bridge
        try:
            bridge.card_ready.connect(self._on_card_ready)
            bridge.finished.connect(self._on_sync_finished)
            bridge.status.connect(self._on_sync_status)
            bridge.classroom_ready.connect(self._on_classroom_ready)
            bridge.login_changed.connect(self._on_login_changed)
        except Exception:
            pass

    def login_ehall(self) -> None:
        """后台走一次登录（存过学号密码就自动填表；没存就提示去哪儿存）。"""
        if self.bridge is None:
            self.ehall_status.setText("同步不可用（缺浏览器组件）")
            return
        self.ehall_status.setText("正在检查登录状态…")
        try:
            self.bridge.ensure_login()
        except Exception as exc:  # noqa: BLE001
            self.ehall_status.setText("登录检查失败：%s" % str(exc)[:40])

    def _on_login_changed(self, ok: bool) -> None:
        self.login_button.setText("已登录" if ok else "登录 eHall")
        if ok:
            self.ehall_status.setText("ehall 已登录")
            try:
                from . import uis_login
            except Exception:
                return
            return
        # 没登录：告诉用户两种办法
        try:
            from . import uis_login

            has = uis_login.has_credentials(self.store)
        except Exception:
            has = False
        self.ehall_status.setText(
            "登录态过期：去 设置 → 统一身份认证 存一次学号密码，以后自动登录"
            if not has else "自动登录没成功，到「eHall」页手动登录一次")

    def sync_ehall(self) -> None:
        if self.bridge is None:
            self.ehall_status.setText("同步不可用（缺浏览器组件）")
            return
        try:
            self.sync_button.setEnabled(False)
            self.sync_button.setText("同步中…")
            if not self.bridge.busy():
                self.ehall_status.setText("正在同步…")
            self.bridge.sync()
        except Exception as exc:  # noqa: BLE001
            self.ehall_status.setText("同步失败：%s" % str(exc)[:50])
            self.sync_button.setEnabled(True)
            self.sync_button.setText("同步 ehall")

    def _on_sync_status(self, text: str) -> None:
        if text:
            self.ehall_status.setText(text)

    def _on_sync_finished(self, _results: Dict[str, Any]) -> None:
        self.sync_button.setEnabled(True)
        self.sync_button.setText("同步 ehall")
        self.ehall_status.setText("同步完成")
        self._render_ehall()

    def _on_card_ready(self, card_id: str, payload: Dict[str, Any]) -> None:
        self._render_ehall_card(card_id, payload)

    def _render_ehall(self) -> None:
        for spec in dashboard.EHALL_CARDS:
            cached = ehall_bridge.cache_get(self.store, spec["id"])
            self._render_ehall_card(spec["id"], cached or {})

    def _render_ehall_card(self, card_id: str, payload: Dict[str, Any]) -> None:
        card = self._ehall_cards.get(card_id)
        if card is None:
            return
        payload = payload if isinstance(payload, dict) else {}
        ok = bool(payload.get("ok"))
        age = ehall_bridge.cache_age_text(self.store, card_id)
        rows = payload.get("rows") or []
        if card_id == "empty_room" and isinstance(card, EmptyRoomWidget):
            # 空教室这张卡是「选了才查」的：缓存里有房间就直接画到时间轴上
            if not payload:
                card.apply_result({"ok": False, "rooms": [], "error": ""})
                card.set_status("还没查过")
                return
            card.apply_result(payload)
            return
        url = dashboard.source_url(self.store, card_id)
        card._hint_label.setVisible(not url)
        clear_layout(card._lines_layout)
        if not payload:
            card._state_label.setText("还没同步")
        elif ok:
            card._state_label.setText(age or "已同步")
        else:
            card._state_label.setText("同步了但没取到")
        if not url and not payload:
            card.set_value("还没填地址", 13.0)
            return
        value = str(payload.get("value") or "")
        if ok:
            card.set_value(value or "已同步", 15.0)
        else:
            card.set_value("没取到数据", 13.0)
        lines = [str(item) for item in (payload.get("lines") or [])]
        if not ok and payload.get("error"):
            # 失败原因当一行小字（太长就截断，完整原因在 tooltip 里）
            reason = str(payload.get("error"))
            card.setToolTip(reason)
            lines = [reason] + lines
        if card_id == "ehall_todo":
            todo = payload.get("todo") or []
            doing = payload.get("doing") or []
            done = payload.get("done") or []
            lines = (["待办：" + str(item) for item in todo]
                     + ["进行中：" + str(item) for item in doing]
                     + ["已完成：" + str(item) for item in done])[:6] or lines
        for line in lines[:4]:
            hint = QLabel(line)
            hint.setWordWrap(True)
            hint.setStyleSheet(
                f"color: {MUTED}; font-size: 8.5pt; background: transparent;")
            card._lines_layout.addWidget(hint)
        card._lines_host.setVisible(bool(lines[:4]))
