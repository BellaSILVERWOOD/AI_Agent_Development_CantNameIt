"""API 服务页：内置的流式对话窗口（不依赖内嵌网页）。

对标 DeepSeek Harness Desktop 的基本体验：
* 边生成边显示（SSE 流式），不是等整段回来才出现；
* 「思考过程」单独一块，可以折叠/展开（DeepSeek-R1、豆包等模型会返回 reasoning）；
* 随时可以「停止生成」，已经生成的部分保留；
* 回答按 Markdown 渲染（标题、列表、代码块、粗体）；
* 顶部能选「通用助手 / 场景提示词」，和提示词配置里的一致；
* 可以上传本地文件（按钮或直接把文件拖进对话区）当上下文，回答下方显示 token 用量；
* 说「记住……」会写进 data/memory.md（`memory` 模块），下次对话自动带上。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QVBoxLayout, QWidget,
)

from . import ai_chat, prompts, theme
from .chat_view import ChatView


from .widgets import (Card, ClickableLabel, HScrollRow, round_menu, Select,
                      SoftButton)

MAX_CONTEXT_TURNS = 8
MAX_TOOL_ROUNDS = 6        # 旧上限（保留名字兼容旧引用；实际已不再用它限制轮数）
#: 只做「病态死循环」的极端保护，正常对话碰不到（用户要求：每轮对话的工具调用不设上限）
SAFETY_MAX_TOOL_ROUNDS = 1000
#: 附件路径存哪儿：附件一直带着，重开应用也要能读回来
ATTACH_KEY = "ai_attach_paths"


def _colors() -> Dict[str, str]:
    mode = "dark" if theme.effective_mode() == "dark" else "light"
    return theme.PALETTES.get(mode) or theme.PALETTES["light"]


class ApiChatPanel(QWidget):
    """一个极简、但能流式显示的对话面板。"""
    agent_requested = Signal()
    settings_requested = Signal()
    ai_metrics = Signal(dict)      # TTFT / tok/s（给主窗口做使用统计）

    def __init__(self, store, compact: bool = False, parent=None):
        super().__init__(parent)
        self.store = store
        self.compact = bool(compact)
        self._history: List[Dict[str, str]] = []
        # 历史会话：当前这段在数据库里的 id（0 = 还没存过）
        self._session_id = 0
        self._session_title = ""
        self._answer = ""
        self._reasoning = ""
        self._saw_reasoning = False
        self._notice = ""
        self._thread: Optional[QThread] = None
        self._worker: Optional[ai_chat.StreamWorker] = None
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(60)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)

        colors = _colors()
        root = QVBoxLayout(self)
        root.setContentsMargins(8 if compact else 16, 8 if compact else 12,
                                8 if compact else 16, 8 if compact else 12)
        root.setSpacing(8)

        # -------------------------------------------------- 顶部
        # 分栏变窄时这一排放不下 → 横向滚动，而不是叠在一起
        head = HScrollRow(height=34, spacing=6)
        self.scene_box = Select()
        self.scene_box.setFixedWidth(132 if compact else 168)
        self.scene_box.setMinimumHeight(34)
        self.scene_box.setStyleSheet("QComboBox { font-size: 10.6pt; }")
        self.scene_box.addItem("通用助手", "system:assistant")
        try:
            for key, item in (prompts.load().get("scenes") or {}).items():
                if isinstance(item, dict) and item.get("enabled", True):
                    self.scene_box.addItem(str(item.get("title") or key),
                                           f"scene:{key}")
        except Exception:
            pass
        head.addWidget(self.scene_box)

        # 下面这些控件**不挂在顶栏上**，只作为「⋯」菜单里的项存在（用户要求顶栏
        # 只留「通用助手 + ⋯」）。保留这些按钮对象是为了让状态文字 / 老调用照常工作。
        from . import ai_thinking

        self.think_mode = ai_thinking.mode(store)
        self.think_button = SoftButton("思考：自动", "ghost", height=28,
                                       min_width=88, point_size=9.4, parent=self)
        self.think_button.clicked.connect(self._toggle_think_mode)
        self.think_button.setVisible(False)

        # 应用内工具（日程 / eLearning / 课表）：开着模型才能真的动手
        from . import ai_tools

        self._tools_on = ai_tools.enabled(store)
        self.tools_button = SoftButton("工具", "ghost", height=28,
                                       min_width=60, point_size=9.4, parent=self)
        self.tools_button.clicked.connect(self._toggle_tools)
        self.tools_button.setVisible(False)

        self.new_button = SoftButton("新对话", "ghost", height=28,
                                     min_width=74, point_size=9.4, parent=self)
        self.new_button.clicked.connect(self.new_chat)
        self.new_button.setVisible(False)

        # 顶栏只留这一个「⋯」：思考模式 / 工具 / 思考过程 / 新对话 / 清空 / 历史 / 微信
        self.more_button = SoftButton("⋯", "ghost", height=28, min_width=34,
                                      point_size=12.4)
        self.more_button.setToolTip("更多：思考模式 / 工具 / 新对话 / 历史 / 清空 / 微信")
        self.more_menu = round_menu(QMenu(self.more_button))
        self.think_action = self.more_menu.addAction("思考：自动")
        self.think_action.setToolTip("点一下在「自动 / 关闭 / 强制思考」之间切换")
        self.think_action.triggered.connect(lambda _=False: self._toggle_think_mode())
        self.tools_action = self.more_menu.addAction("让 AI 动手（工具）")
        self.tools_action.setCheckable(True)
        self.tools_action.blockSignals(True)
        self.tools_action.setChecked(bool(self._tools_on))
        self.tools_action.blockSignals(False)
        self.tools_action.setToolTip(
            "打开后 AI 能直接加/改/删日程、读 eLearning、下载课程文件、抓课表")
        self.tools_action.toggled.connect(self._set_tools)
        self.thinking_action = self.more_menu.addAction("显示思考过程")
        self.thinking_action.setCheckable(True)
        self.thinking_action.blockSignals(True)
        self.thinking_action.setChecked(True)
        self.thinking_action.blockSignals(False)
        self.thinking_action.toggled.connect(self._set_thinking_visible)
        self.more_menu.addSeparator()
        self.new_action = self.more_menu.addAction("新对话")
        self.new_action.setToolTip("开一段新对话（当前这段会自动存进历史）")
        self.new_action.triggered.connect(lambda _=False: self.new_chat())
        self.clear_action = self.more_menu.addAction("清空当前对话")
        self.clear_action.setToolTip("清空当前对话（历史会话里还留着）")
        self.clear_action.triggered.connect(lambda _=False: self.clear())
        self.history_action = self.more_menu.addAction("历史会话")
        self.history_action.setToolTip("继续 / 重命名 / 删除以前的对话")
        self.history_action.triggered.connect(lambda _=False: self.open_history())
        self.agent_action = self.more_menu.addAction("读取微信消息")
        self.agent_action.setToolTip("读微信消息，识别要加日程的事")
        self.agent_action.triggered.connect(
            lambda _=False: self.agent_requested.emit())
        try:
            self.more_menu.setToolTipsVisible(True)
        except Exception:
            pass
        self.more_button.setMenu(self.more_menu)
        self._sync_think_button()
        head.addWidget(self.more_button)
        head.addStretch(1)
        # 多套 API 配置：下拉放在下面的撰写卡片里（跟模型选择器同位置），
        # 这里只把控件建出来，不往顶栏加。
        from . import llm_profiles

        try:
            llm_profiles.migrate(store)
        except Exception:
            pass
        self.profile_box = Select()
        self.profile_box.setMinimumWidth(40)
        self.profile_box.setFixedHeight(26)
        self.profile_box.setToolTip(
            "切换 API 配置（接口地址 + 模型 + Key）。多套配置在 设置 → AI 接口 里管理")
        self._reload_profiles()
        self.profile_box.currentIndexChanged.connect(self._on_profile_changed)
        self.model_label = QLabel("")
        self.model_label.setObjectName("Tiny")
        self.model_label.setStyleSheet(
            "font-size: 10.2pt; color: %s;" % colors.get("MUTED", theme.MUTED))
        head.addWidget(self.model_label)
        root.addWidget(head)

        # -------------------------------------------------- 一整块「对话 + 输入」
        # 参考 Codex：对话区和输入框在**同一个背景块**里，中间没有硬分隔；
        # 输入框只是贴在这块底部的浅色圆角条，而不是另起一张卡片。
        self.surface = Card(radius=18, fill=colors["CARD"], border=colors["BORDER"],
                            padding=0, spacing=0)
        surface_layout = QVBoxLayout()
        surface_layout.setContentsMargins(10, 10, 10, 8)
        surface_layout.setSpacing(6)
        self.surface.body.addLayout(surface_layout)
        root.addWidget(self.surface, 1)

        self.view = ChatView()
        self.view.files_dropped.connect(self.attach_paths)
        # 用户往上翻（不跟随）时才显示「回到底部」
        self.view.verticalScrollBar().valueChanged.connect(
            lambda _=0: self._sync_jump_button())
        self.view.verticalScrollBar().rangeChanged.connect(
            lambda *_args: self._sync_jump_button())
        surface_layout.addWidget(self.view, 1)
        # 整个面板都能接住拖进来的文件（含文件夹 / 多选）
        self.setAcceptDrops(True)

        # -------------------------------------------------- 输入（贴底的撰写条）
        # 形状参考现代对话产品：一条浅色圆角栏，上面一行「附件小标签」，
        # 中间是无边框输入行，底下一排操作（加附件 / 是否先问 / 模型 / 发送）。
        self.composer = Card(radius=14, fill=colors["CARD_ALT"],
                             border=colors["CARD_ALT"], padding=10, spacing=6)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.attach_label = QLabel("")
        self.attach_label.setObjectName("Tiny")
        self.attach_label.setStyleSheet(
            "font-size: 9.8pt; color: %s;" % colors.get("TEXT_SOFT", theme.TEXT_SOFT))
        self.attach_label.setWordWrap(True)
        chips.addWidget(self.attach_label, 1)
        chips.addStretch(1)
        # 「回到底部」：生成中往上翻看前面的内容时出现，点一下继续跟随
        self.jump_button = SoftButton("回到底部", "ghost", height=24,
                                      min_width=82, point_size=9.0)
        self.jump_button.setToolTip("回到最新一条（重新跟随生成）")
        self.jump_button.setVisible(False)
        self.jump_button.clicked.connect(self._jump_to_bottom)
        chips.addWidget(self.jump_button)
        self.usage_label = ClickableLabel("")
        self.usage_label.setObjectName("Tiny")
        self.usage_label.setStyleSheet(
            "font-size: 9.8pt; color: %s;" % colors.get("MUTED", theme.MUTED))
        self.usage_label.setToolTip("点一下看精确到个位的 token 用量")
        self.usage_label.clicked.connect(self.show_usage_detail)
        chips.addWidget(self.usage_label)
        self.detach_button = SoftButton("清除附件", "ghost", height=24,
                                        min_width=80, point_size=9.0)
        self.detach_button.clicked.connect(self.clear_attachments)
        self.detach_button.setVisible(False)
        chips.addWidget(self.detach_button)
        self.composer.body.addLayout(chips)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("有什么要做？")
        self.input.setFixedHeight(42)
        self.input.setStyleSheet(
            "QLineEdit { background: transparent; border: none; padding: 2px 4px;"
            " font-size: 15px; }")
        self.input.returnPressed.connect(self.send)
        # 输入框自己会把拖进来的文件变成一行路径文字，这里拦下来当附件
        self.input.setAcceptDrops(True)
        self.input.installEventFilter(self)
        # 右键菜单里的「粘贴」也走同一条路（图片 / 文件 → 附件）
        self.input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.input.customContextMenuRequested.connect(self._input_menu)
        input_row.addWidget(self.input, 1)
        self.composer.body.addLayout(input_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        # 左下角：实心圆形「+」加附件
        from .widgets import RoundIconButton, RoundSendButton

        self.attach_button = RoundIconButton(
            "+", size=30,
            tip="上传文件当上下文（PDF / Word / PPT / 文本 / 图片）\n"
                "可一次多选；也可以把文件或整个文件夹拖进来，"
                "或者直接 Ctrl+V 粘贴截图 / 文件")
        self.attach_button.clicked.connect(self.attach_files)
        actions.addWidget(self.attach_button)

        # 「需要确认 / 自动执行」：无边框文字 + 下拉菜单（像复选框那样选一个）
        from . import ai_tools as _ai_tools

        self._auto_exec = False
        try:
            self._auto_exec = bool(_ai_tools.session_auto())
        except Exception:
            self._auto_exec = False
        self.auto_button = QPushButton("需要确认", self)
        self.auto_button.setFlat(True)
        self.auto_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_button.setStyleSheet(
            "QPushButton { border: none; background: transparent; padding: 2px 6px;"
            " font-size: 13.6px; color: %s; }"
            "QPushButton:hover { color: %s; }"
            % (colors["MUTED"], colors["TEXT"]))
        self.auto_button.setToolTip(
            "「需要确认」：动手前会先弹窗问你（默认）\n"
            "「自动执行」：本次会话里普通操作不再逐条确认；"
            "删文件、跑命令、截屏、发信、下载、导出仍然会问")
        self._approval_menu = round_menu(QMenu(self.auto_button))
        self._ask_action = self._approval_menu.addAction("需要确认")
        self._ask_action.setCheckable(True)
        self._ask_action.triggered.connect(lambda _=False: self._set_auto_exec(False))
        self._auto_action = self._approval_menu.addAction("自动执行")
        self._auto_action.setCheckable(True)
        self._auto_action.triggered.connect(lambda _=False: self._set_auto_exec(True))
        self.auto_button.setMenu(self._approval_menu)
        self._sync_auto_button()
        actions.addWidget(self.auto_button)
        actions.addStretch(1)
        # 模型名称：无边框，靠右（跟截图里的模型选择器一个位置）
        self.profile_box.setStyleSheet(
            "QComboBox { background: transparent; border: none; padding: 0 22px 0 6px;"
            " color: %s; font-size: 12.6px; }"
            "QComboBox::drop-down { border: none; width: 0; }"
            "QComboBox QAbstractItemView { border: 1px solid %s; }"
            % (colors["MUTED"], colors["BORDER"]))
        self.profile_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        actions.addWidget(self.profile_box)
        # 右下角：实心圆形发送（↑ / ■）
        self.send_button = RoundSendButton(
            32, tip="发送（Enter）\n生成中再点一次是「停止」")
        self.send_button.clicked.connect(self.send)
        actions.addWidget(self.send_button)
        self.composer.body.addLayout(actions)
        # 输入条贴在背景块底部（不再单独 root.addWidget，否则又变成两块了）
        surface_layout.addWidget(self.composer)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setStyleSheet(
            "font-size: 10.2pt; color: %s;" % colors.get("MUTED", theme.MUTED))
        self.status.setWordWrap(True)
        self.status.setContentsMargins(4, 0, 4, 0)
        surface_layout.addWidget(self.status)

        self._thinking_visible = True
        self._attachments: List[Dict[str, str]] = []
        self._usage_total = {"prompt": 0, "completion": 0, "cache_hit": 0,
                            "cache_miss": 0}
        self._usage_last = {"prompt": 0, "completion": 0, "cache_hit": 0,
                            "cache_miss": 0}
        self._last_metrics: Dict[str, Any] = {}
        # 工具调用（一轮提问里可能要调好几次工具、来回好几轮）
        self._pending_calls: List[Dict[str, str]] = []
        self._turn_messages: List[Dict[str, Any]] = []
        self._turn_round = 0
        self._stopped = False
        self.refresh_model_label()
        self._refresh_attach_label()
        self._refresh_usage_label()
        # 工具要动「工作区外面」的东西、或者高风险操作时，弹窗问用户
        ai_tools.set_confirm_handler(self._confirm_tool)
        self._render()
        # 上次挂着的附件：稍等一下再读（别拖慢启动），文件不在了就安静跳过
        QTimer.singleShot(200, self._load_attachments)

    # -------------------------------------------------- 状态
    def _reload_profiles(self) -> None:
        """把库里的 API 配置填进下拉（保持当前选中）。"""
        from . import llm_profiles

        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        try:
            items = llm_profiles.list_profiles(self.store)
        except Exception:
            items = []
        active = None
        try:
            active = llm_profiles.active_id(self.store)
        except Exception:
            active = None
        for item in items:
            label = str(item.get("label") or item.get("name") or "配置")
            self.profile_box.addItem(label, int(item["id"]))
        if active is not None:
            index = self.profile_box.findData(int(active))
            if index >= 0:
                self.profile_box.setCurrentIndex(index)
        self.profile_box.blockSignals(False)
        self.profile_box.setEnabled(bool(items))

    def _on_profile_changed(self, _index: int) -> None:
        from . import llm_profiles

        profile_id = self.profile_box.currentData()
        if profile_id is None:
            return
        profile = None
        try:
            profile = llm_profiles.set_active(self.store, int(profile_id))
        except Exception as exc:  # noqa: BLE001
            self.status.setText("切换配置失败：%s" % exc)
            return
        self.refresh_model_label()
        if profile is not None:
            self.status.setText("已切换到「%s」：%s"
                                % (profile.get("label") or profile.get("name"),
                                   llm_profiles.describe(self.store, profile)))
            self._sync_think_button()

    def refresh_model_label(self) -> None:
        from . import llm_profiles

        try:
            # 设置页可能刚加/改了配置：顺手把下拉刷新一下（保持当前选中）
            self._reload_profiles()
        except Exception:
            pass
        cfg = ai_chat.config(self.store)
        base = cfg.get("base_url") or "（未配置接口地址）"
        model = cfg.get("model") or "（未填模型名）"
        keyed = "有 Key" if cfg.get("api_key") else "没填 Key"
        # 一行只说清楚「哪个模型 + 有没有 Key」；接口地址放进悬停提示，
        # 免得这一行又长又跟别处重复（用户：「两个地方重复了」）
        self.model_label.setText(f"{model} · {keyed}")
        self.model_label.setToolTip(f"接口地址：{base}")
        try:
            profile = llm_profiles.active(self.store)
        except Exception:
            profile = None
        if profile is not None:
            self.model_label.setToolTip(
                f"接口地址：{base}\n" + llm_profiles.describe(self.store, profile))

    def _sync_auto_button(self) -> None:
        """「需要确认 / 自动执行」按钮的样子跟着状态走（无边框，文字即状态）。"""
        try:
            self.auto_button.setText("自动执行" if self._auto_exec else "需要确认")
            self._ask_action.setChecked(not self._auto_exec)
            self._auto_action.setChecked(bool(self._auto_exec))
        except Exception:
            pass

    def _set_auto_exec(self, on: bool) -> None:
        """设置成「需要确认 / 自动执行」并说明清楚（菜单里选哪个都走这里）。"""
        from . import ai_tools

        self._auto_exec = bool(on)
        try:
            ai_tools.set_session_auto(self._auto_exec)
        except Exception:
            pass
        self._sync_auto_button()
        self.status.setText("已设为「自动执行」：本会话里普通操作不再逐条确认"
                            "（删文件、跑命令、截屏、发信、下载、导出仍会问你）"
                            if self._auto_exec
                            else "已设为「需要确认」：动手前会先弹窗问你")

    def _toggle_auto_exec(self) -> None:
        """兼容旧调用：在两种模式之间切换。"""
        self._set_auto_exec(not self._auto_exec)

    def _toggle_thinking(self) -> None:
        """兼容旧调用：切一下「显示思考过程」。"""
        self._set_thinking_visible(not self._thinking_visible)

    def _set_thinking_visible(self, value: bool) -> None:
        self._thinking_visible = bool(value)
        try:
            self.thinking_action.setChecked(self._thinking_visible)
        except Exception:
            pass
        if hasattr(self, "view"):
            self._render()

    # -------------------------------------------------- 工具确认（工作区外 / 高风险）
    def _confirm_tool(self, info: Dict[str, Any]) -> str:
        """问用户：AI 要动工作区外的文件（或做高风险操作），允许吗？

        返回 "allow"（就这一次）/ "session"（本次会话都允许）/ "deny"。
        """
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QLabel, QVBoxLayout

        from . import icons
        from .widgets import BaseDialog, SoftButton

        answer = {"value": "deny"}
        tool = str(info.get("tool") or "")
        label = str(info.get("label") or tool)
        target = str(info.get("target") or "")
        reason = str(info.get("reason") or "")
        risk = str(info.get("risk") or "low")
        try:
            dialog = BaseDialog(self, width=520, height=250, title="需要你确认",
                                subtitle="AI 想动这台机器上的东西",
                                icon=icons.WARNING,
                                icon_color=QColor(theme.WARNING).name())
            body = QVBoxLayout()
            body.setSpacing(8)
            title = QLabel(f"工具：{label}（{tool}）"
                           f"{'  · 高风险' if risk == 'high' else ''}")
            title.setObjectName("Small")
            body.addWidget(title)
            if target:
                path_label = QLabel(f"目标：{target}")
                path_label.setWordWrap(True)
                path_label.setObjectName("Muted")
                body.addWidget(path_label)
            why = QLabel(reason or "这次操作需要你点头")
            why.setWordWrap(True)
            why.setObjectName("Tiny")
            body.addWidget(why)
            hint = QLabel("「本次会话都允许」只对这一个目标记住，关掉软件就忘掉。")
            hint.setObjectName("Tiny")
            hint.setWordWrap(True)
            body.addWidget(hint)
            dialog.body.addLayout(body)

            deny = SoftButton("拒绝", "ghost", height=34, min_width=84)
            once = SoftButton("允许这一次", "ghost", height=34, min_width=112)
            always = SoftButton("本次会话都允许", "primary", height=34, min_width=136)

            def choose(value: str) -> None:
                answer["value"] = value
                dialog.accept()

            deny.clicked.connect(lambda: choose("deny"))
            once.clicked.connect(lambda: choose("allow"))
            always.clicked.connect(lambda: choose("session"))
            dialog.add_footer([], [deny, once, always])
            dialog.exec()
        except Exception:
            return "deny"
        return answer["value"]

    # -------------------------------------------------- 思考模式（真关掉）
    def _think_context(self) -> tuple:
        cfg = ai_chat.config(self.store)
        return str(cfg.get("model") or ""), str(cfg.get("base_url") or "")

    def _sync_think_button(self) -> None:
        from . import ai_thinking

        model, base = self._think_context()
        off = self.think_mode == "off"
        label = ("思考：关闭" if off else
                 ("思考：强制" if self.think_mode == "on" else "思考：自动"))
        self.think_button.setText(label)
        self.think_button.setKind("primary" if off else "ghost")
        tip = ai_thinking.note(self.store, model, base)
        self.think_button.setToolTip(
            ("点一下切换：自动 → 关闭 → 强制 → 自动。"
             "「关闭」是请求里就不让模型思考，不只是把思考过程藏起来。\n" + tip)
            if tip else
            "点一下切换：自动 → 关闭 → 强制 → 自动。\n"
            "「关闭」是请求里就不让模型思考（按服务自动选对参数）。")
        try:
            self.think_action.setText(label)      # 「⋯」菜单里那一项跟按钮同步
        except Exception:
            pass

    def _toggle_think_mode(self) -> None:
        from . import ai_thinking

        order = ("auto", "off", "on")
        index = order.index(self.think_mode) if self.think_mode in order else 0
        self.think_mode = ai_thinking.set_mode(self.store, order[(index + 1) % 3])
        self._sync_think_button()
        model, base = self._think_context()
        note = ai_thinking.note(self.store, model, base)
        if self.think_mode == "off":
            message = note or "已关闭思考：下次请求就不让模型思考了"
        elif self.think_mode == "on":
            message = note or "已强制打开思考"
        else:
            message = "思考交回模型自己决定"
        self.status.setText(message)
        try:
            self._render()
        except Exception:
            pass

    def _toggle_tools(self) -> None:
        self._set_tools(not self._tools_on)

    def _set_tools(self, value: bool) -> None:
        """打开 / 关闭应用内工具（菜单项和隐藏按钮都走这里）。"""
        self._tools_on = bool(value)
        try:
            self.tools_button.setKind("primary" if self._tools_on else "ghost")
        except Exception:
            pass
        try:
            self.tools_action.blockSignals(True)
            self.tools_action.setChecked(self._tools_on)
            self.tools_action.blockSignals(False)
        except Exception:
            pass
        try:
            from . import ai_tools

            self.store.set_setting(ai_tools.TOOL_ENABLED_KEY,
                                   "1" if self._tools_on else "0")
        except Exception:
            pass
        self.status.setText(
            "已开启应用内工具：AI 可以直接加日程、读 eLearning、抓课表"
            if self._tools_on else "已关闭应用内工具：AI 只会回答，不会动你的数据")

    # -------------------------------------------------- 拖拽（整个面板 + 输入框）
    @staticmethod
    def _dropped_paths(event) -> List[Path]:
        try:
            mime = event.mimeData()
            if mime is None or not mime.hasUrls():
                return []
            return [Path(url.toLocalFile()) for url in mime.urls()
                    if url.isLocalFile()]
        except Exception:
            return []

    def dragEnterEvent(self, event):
        if self._dropped_paths(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._dropped_paths(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = self._dropped_paths(event)
        if paths:
            event.acceptProposedAction()
            self.attach_paths(paths)
            return
        super().dropEvent(event)

    def eventFilter(self, watched, event):
        """输入框上的拖放 / 粘贴：都当成附件，不要变成一行路径文字。"""
        try:
            from PySide6.QtCore import QEvent
            from PySide6.QtGui import QKeySequence

            if watched is self.input:
                if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                    if self._dropped_paths(event):
                        event.acceptProposedAction()
                        return True
                elif event.type() == QEvent.Type.Drop:
                    paths = self._dropped_paths(event)
                    if paths:
                        event.acceptProposedAction()
                        self.attach_paths(paths)
                        return True
                elif event.type() == QEvent.Type.KeyPress:
                    # Ctrl+V：剪贴板里是图片 / 文件就收成附件，是文字就照常粘贴
                    if event.matches(QKeySequence.StandardKey.Paste):
                        if self.paste_clipboard():
                            return True
        except Exception:
            pass
        return super().eventFilter(watched, event)

    # -------------------------------------------------- 粘贴（图片 / 文件）
    def paste_clipboard(self) -> bool:
        """把剪贴板里的图片 / 文件当附件收下；纯文字返回 False 走默认粘贴。

        用户：「为什么不能直接粘贴图片或文档」—— 以前只有「＋」和拖放，
        Ctrl+V 贴一张截图只会往输入框里塞一个空格。
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        data = clipboard.mimeData() if clipboard is not None else None
        if data is None:
            return False
        # ① 从资源管理器「复制」的文件 / 文件夹（剪贴板里是 URL）
        if data.hasUrls():
            paths = []
            for url in data.urls():
                if isinstance(url, QUrl) and url.isLocalFile():
                    paths.append(url.toLocalFile())
            if paths:
                self.attach_paths(paths)
                return True
        # ② 截图 / 图片（画图、浏览器「复制图片」、Win+Shift+S 截图）
        if data.hasImage():
            image = clipboard.image()
            if image is not None and not image.isNull():
                path = self.save_clipboard_image(image)
                if path:
                    self.attach_paths([path])
                    return True
        return False

    def save_clipboard_image(self, image) -> str:
        """把剪贴板里的图片存成 png（放进数据目录的 paste 文件夹），返回路径。"""
        from datetime import datetime

        from . import config

        try:
            folder = Path(config.DATA_DIR) / "paste"
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = folder / (f"粘贴的图片-{stamp}.png")
            index = 2
            while target.exists():
                target = folder / (f"粘贴的图片-{stamp}-{index}.png")
                index += 1
            if not image.save(str(target), "PNG"):
                return ""
            return str(target)
        except Exception:
            return ""

    def paste_or_text(self) -> None:
        """右键菜单的「粘贴」：剪贴板里是图片 / 文件就当附件，否则照常贴文字。"""
        if not self.paste_clipboard():
            self.input.paste()

    def _input_menu(self, pos) -> None:
        """输入框右键菜单：粘贴（图片 / 文件也行）/ 复制 / 剪切 / 全选。"""
        menu = round_menu(QMenu(self.input))
        paste = menu.addAction("粘贴（图片 / 文件也行）")
        paste.triggered.connect(lambda _=False: self.paste_or_text())
        menu.addSeparator()
        copy = menu.addAction("复制")
        copy.setEnabled(bool(self.input.selectedText()))
        copy.triggered.connect(lambda _=False: self.input.copy())
        cut = menu.addAction("剪切")
        cut.setEnabled(bool(self.input.selectedText()))
        cut.triggered.connect(lambda _=False: self.input.cut())
        select_all = menu.addAction("全选")
        select_all.setEnabled(bool(self.input.text()))
        select_all.triggered.connect(lambda _=False: self.input.selectAll())
        menu.exec(self.input.mapToGlobal(pos))

    # -------------------------------------------------- 附件 / 用量
    def attach_files(self) -> None:
        """一次可以选多个文件（Ctrl / Shift 多选，也可以选整个文件夹里的文件）。"""
        from PySide6.QtWidgets import QFileDialog

        from . import ai_files

        paths, _filter = QFileDialog.getOpenFileNames(
            self, "选择要一起发给模型的文件（可多选）", "",
            ai_files.file_filter())
        if paths:
            self.attach_paths(paths)

    def attach_paths(self, paths, quiet: bool = False) -> None:
        """读取一批文件（也可以是文件夹）并挂到以后的每次提问上。

        用户：「上传一个附件应该是默认每次不用再传」—— 所以附件是**一直带着**的，
        直到点「清除附件」；这里顺手把路径存进设置，重开应用也还在。
        """
        from . import ai_files

        if isinstance(paths, (str, Path)):
            paths = [paths]
        loaded, notes = ai_files.load_many([Path(str(item)) for item in paths])
        if loaded:
            known = {str(item.get("path") or "").lower() for item in self._attachments}
            fresh = [item for item in loaded
                     if str(item.get("path") or "").lower() not in known]
            duplicate = len(loaded) - len(fresh)
            self._attachments.extend(fresh)
            # 超出「文件数 / 总字数」就从最早的开始丢
            while len(self._attachments) > ai_files.MAX_FILES:
                self._attachments.pop(0)
            total = sum(len(item.get("text") or "") for item in self._attachments)
            while total > ai_files.MAX_TOTAL_CHARS and len(self._attachments) > 1:
                dropped = self._attachments.pop(0)
                total -= len(dropped.get("text") or "")
                notes.append(f"合计字数超上限，先移除了 {dropped.get('name')}")
        else:
            fresh = []
            duplicate = 0
        self._refresh_attach_label()
        parts: List[str] = []
        if fresh:
            count = len(fresh)
            chars = sum(len(item.get("text") or "") for item in fresh)
            parts.append(f"已附加 {count} 个文件（共 {chars} 字）")
        if duplicate:
            parts.append(f"跳过 {duplicate} 个重复的")
        if notes:
            parts.append("；".join(notes[:3]) + ("…" if len(notes) > 3 else ""))
        if parts and not quiet:
            self.status.setText("，".join(parts))
        self._save_attachments()

    def _jump_to_bottom(self) -> None:
        """回到最新一条，并重新跟随生成。"""
        try:
            self.view.scroll_to_bottom()
        except Exception:
            pass
        self._sync_jump_button()

    def _sync_jump_button(self) -> None:
        """往上翻着看的时候才显示「回到底部」。"""
        try:
            bar = self.view.verticalScrollBar()
            show = (bar.maximum() > 0) and (not self.view._stick_bottom)
            self.jump_button.setVisible(bool(show))
        except Exception:
            pass

    def clear_attachments(self) -> None:
        self._attachments = []
        self._refresh_attach_label()
        self._save_attachments()
        self.status.setText("已清除附件")

    # -------------------------------------------------- 附件的持久化
    def _save_attachments(self) -> None:
        """把附件的**路径**记进设置：下次打开应用还挂着，不用重新传。"""
        try:
            paths = [str(item.get("path") or "") for item in self._attachments
                     if str(item.get("path") or "")]
            self.store.set_setting(ATTACH_KEY,
                                   json.dumps(paths, ensure_ascii=False))
        except Exception:
            pass

    def _load_attachments(self) -> None:
        """启动时把上次挂着的附件读回来（文件已经不在了就安静地跳过）。"""
        try:
            raw = self.store.get_setting(ATTACH_KEY, "") or ""
            paths = json.loads(raw) if raw else []
        except Exception:
            paths = []
        existing = []
        for path in paths if isinstance(paths, list) else []:
            try:
                if path and Path(str(path)).exists():
                    existing.append(str(path))
            except Exception:
                continue
        if not existing:
            return
        self.attach_paths(existing, quiet=True)
        self.status.setText("上次附带的 %d 个文件已经自动带上" % len(self._attachments))

    def _attachment_meta(self) -> List[Dict[str, str]]:
        """这次提问带着哪些附件（写进消息里，气泡上方要显示出来）。"""
        meta: List[Dict[str, str]] = []
        for item in self._attachments or []:
            meta.append({
                "name": str(item.get("name") or ""),
                "kind": str(item.get("kind") or "text"),
                "path": str(item.get("path") or ""),
                "mime": str(item.get("mime") or ""),
            })
        return meta

    def _refresh_attach_label(self) -> None:
        has_files = bool(self._attachments)
        try:
            self.detach_button.setVisible(has_files)
        except Exception:
            pass
        if not has_files:
            self.attach_label.setText("")
            self.attach_label.setVisible(False)
            return
        from . import ai_files

        total = sum(len(item.get("text") or "") for item in self._attachments)
        pdfs = sum(1 for item in self._attachments
                   if str(item.get("name") or "").lower().endswith(".pdf"))
        pictures = ai_files.count_images(self._attachments)
        names = "、".join(item["name"] for item in self._attachments[:3])
        if len(self._attachments) > 3:
            names += f" 等 {len(self._attachments)} 个"
        extra = ""
        if pdfs:
            extra += f"（{pdfs} 个 PDF 已抽文字"
            extra += f"，{pictures} 张图片按视觉输入" if pictures else ""
            extra += "）"
        elif pictures:
            extra += f"（{pictures} 张图片按视觉输入）"
        # 只占一行：附件多了也不能把输入框挤没了（完整清单放悬停提示里）
        self.attach_label.setText(
            f"已附加 {len(self._attachments)} 个：{names}{extra}　"
            f"以后每次都自动带上，点「清除附件」去掉")
        detail = "\n".join(
            "%s（%s 字）" % (item.get("name"),
                            len(item.get("text") or "") if
                            item.get("kind") != "image" else "图片")
            for item in self._attachments)
        tip = (f"文字共 {total} 字 / 上限 {ai_files.MAX_TOTAL_CHARS} 字；"
               f"每次提问都会自动带上\n{detail}")
        try:
            model = ai_chat.config(self.store).get("model") or ""
        except Exception:
            model = ""
        if pictures and model and not ai_files.vision_like(model):
            tip += f"\n注意：「{model}」多半不收图片，可能报错"
        self.attach_label.setToolTip(tip)
        self.attach_label.setVisible(True)

    def _refresh_usage_label(self) -> None:
        from .utils import format_tokens_m

        last = self._usage_last
        total = self._usage_total
        speed = self._metrics_text()
        has_any = any(total.get(key) for key in ("prompt", "completion", "cache_hit"))
        if not has_any and not speed:
            self.usage_label.setText("token：—")
            self.usage_label.setToolTip(
                "token 数按 M（百万）显示；点一下能看到精确到个位的数字\n"
                "有些接口不返回用量；DeepSeek / OpenAI 会返回 prompt/completion tokens，"
                "缓存命中（prompt cache hit）也会一起显示")
            return
        text = (f"token：本次 输入 {format_tokens_m(last.get('prompt'))}"
                f" / 输出 {format_tokens_m(last.get('completion'))}"
                f"　累计 输入 {format_tokens_m(total.get('prompt'))}"
                f" / 输出 {format_tokens_m(total.get('completion'))}")
        # 缓存命中：接口报了才显示（DeepSeek / OpenAI 会报）
        if total.get("cache_hit") or last.get("cache_hit"):
            text += (f"　缓存命中 {format_tokens_m(total.get('cache_hit'))}"
                     f"（本次 {format_tokens_m(last.get('cache_hit'))}）")
        if speed:
            text = f"{speed}　{text}"
        self.usage_label.setText(text)
        self.usage_label.setToolTip(
            "M = 百万 token（例如 1.23M = 123 万 token）；点一下看精确数字\n"
            "「首字」= 从发请求到出现第一个字用了多久（TTFT）\n"
            "「字/秒」= 生成速度（tok/s，1 个 token 大约 1–2 个汉字；"
            "前面带 ≈ 表示接口没报用量、按字数估的）\n"
            f"本次：输入 {last.get('prompt', 0)} / 输出 {last.get('completion', 0)}"
            f" / 缓存命中 {last.get('cache_hit', 0)}\n"
            f"累计：输入 {total.get('prompt', 0)} / 输出 {total.get('completion', 0)}"
            f" / 缓存命中 {total.get('cache_hit', 0)}")

    def usage_detail_lines(self) -> List[str]:
        """精确到个位的用量明细（点 token 那行时弹出来）。"""
        from .utils import format_tokens_m

        last = self._usage_last
        total = self._usage_total
        prompt = int(last.get("prompt") or 0)
        hit = int(last.get("cache_hit") or 0)
        miss = int(last.get("cache_miss") or 0)
        rate = (hit / prompt * 100.0) if prompt else 0.0
        lines = [
            "本次请求",
            f"　输入 {prompt} tokens（{format_tokens_m(prompt)}）",
            f"　输出 {int(last.get('completion') or 0)} tokens"
            f"（{format_tokens_m(last.get('completion'))}）",
        ]
        if hit or miss:
            lines.append(f"　缓存命中 {hit} tokens（{format_tokens_m(hit)}）"
                         f" / 未命中 {miss}"
                         + (f"，命中率 {rate:.1f}%" if prompt else ""))
        else:
            lines.append("　缓存命中：这个接口没有返回缓存信息（DeepSeek / OpenAI 会返回）")
        lines += [
            "",
            "累计（本次会话）",
            f"　输入 {int(total.get('prompt') or 0)} tokens"
            f"（{format_tokens_m(total.get('prompt'))}）",
            f"　输出 {int(total.get('completion') or 0)} tokens"
            f"（{format_tokens_m(total.get('completion'))}）",
            f"　缓存命中 {int(total.get('cache_hit') or 0)} tokens"
            f"（{format_tokens_m(total.get('cache_hit'))}）",
        ]
        total_prompt = int(total.get("prompt") or 0)
        total_hit = int(total.get("cache_hit") or 0)
        if total_prompt and total_hit:
            lines.append(f"　累计命中率 {total_hit / total_prompt * 100.0:.1f}%")
        metrics = self._last_metrics or {}
        if metrics.get("ttft") is not None:
            lines += ["", f"首字延迟 {float(metrics['ttft']):.2f}s"
                           f"，出字速度 {float(metrics.get('tokens_per_second') or 0):.1f} tok/s"]
        if metrics.get("estimated_tokens"):
            lines.append("（接口没给 usage，输出是按字数估算的 ≈）")
        return lines

    def show_usage_detail(self) -> None:
        """点 token 那行：弹一个精确用量的小窗。"""
        from PySide6.QtWidgets import QMessageBox

        dialog = QMessageBox(self)
        dialog.setWindowTitle("token 用量")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText("token 数按 M（百万）显示，这里是精确到个位的数字")
        dialog.setDetailedText("\n".join(self.usage_detail_lines()))
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        try:
            dialog.exec()
        except Exception:
            pass

    def _metrics_text(self) -> str:
        """「首字 / 出字速度」那一行（没有数据就返回空串）。

        以前写的是 `TTFT 7.11s · 607.6 tok/s`，用户看不懂（「这一行是什么东西？」），
        这里改成中文，完整解释放在悬停提示里。
        """
        metrics = self._last_metrics or {}
        parts: List[str] = []
        ttft = metrics.get("ttft")
        if ttft is not None:
            parts.append(f"首字 {float(ttft):.2f}s")
        speed = metrics.get("tokens_per_second") or 0
        if speed:
            mark = "≈" if metrics.get("estimated_tokens") else ""
            parts.append(f"{mark}{float(speed):.0f} 字/秒")
        return " · ".join(parts)

    def _on_metrics(self, metrics) -> None:
        try:
            self._last_metrics = dict(metrics or {})
        except Exception:
            self._last_metrics = {}
        self._refresh_usage_label()
        try:
            self.ai_metrics.emit(self._last_metrics)
        except Exception:
            pass

    def _on_usage(self, usage) -> None:
        try:
            parsed = ai_chat.parse_usage(dict(usage or {}))
        except Exception:
            return
        self._usage_last = {"prompt": parsed["prompt"],
                            "completion": parsed["completion"],
                            "cache_hit": parsed["cache_hit"],
                            "cache_miss": parsed["cache_miss"]}
        self._usage_total["prompt"] = self._usage_total.get("prompt", 0) + parsed["prompt"]
        self._usage_total["completion"] = (self._usage_total.get("completion", 0)
                                           + parsed["completion"])
        self._usage_total["cache_hit"] = (self._usage_total.get("cache_hit", 0)
                                          + parsed["cache_hit"])
        self._usage_total["cache_miss"] = (self._usage_total.get("cache_miss", 0)
                                           + parsed["cache_miss"])
        self._refresh_usage_label()

    def _reset_usage_last(self) -> None:
        self._usage_last = {"prompt": 0, "completion": 0, "cache_hit": 0,
                            "cache_miss": 0}
        self._last_metrics = {}
        self._refresh_usage_label()

    # -------------------------------------------------- 渲染
    def _render(self) -> None:
        """重画对话区：用户气泡靠右、助手正文铺开、代码块是卡片。"""
        try:
            self._render_timer.stop()
        except Exception:
            pass
        live = {"answer": self._answer, "reasoning": self._reasoning,
                "notice": self._notice, "streaming": self._streaming}
        empty = ("API 对话\n直接用配置好的接口地址 / 模型名 / API Key 对话，回答是流式的。"
                 "接口在 设置 → AI 接口 里填。")
        try:
            self.view.render(list(self._history), live,
                            thinking=bool(self._thinking_visible),
                            empty_text=empty)
        except Exception as exc:  # noqa: BLE001
            # 排版出问题也别让对话没了：退回纯文本显示
            self.view.render([{"role": "assistant", "content": str(exc)}], None)

    # -------------------------------------------------- 发送
    @property
    def _streaming(self) -> bool:
        thread = self._thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            self._thread = None
            return False

    def send(self) -> None:
        text = self.input.text().strip()
        if self._streaming:
            self.stop()
            return
        if not text and not self._attachments:
            return
        if not text:
            # 只拖了图/附件、没打字：也给一句人话，不然模型不知道要干嘛
            from . import ai_files

            text = ("看看这张图片" if ai_files.count_images(self._attachments)
                    else "看看这些附件")
        if not ai_chat.api_ready(self.store):
            self.status.setText(
                "还没配置接口：到 设置 → AI 接口 选服务、填模型名和 API Key")
            return
        self.input.clear()
        # 附件只对这一次生效：文字拼在用户消息前面，图片作为视觉输入一起发
        payload_text = text
        image_items: List[Dict[str, Any]] = []
        if self._attachments:
            from . import ai_files

            context = ai_files.as_context(self._attachments)
            payload_text = f"{text}\n\n{context}" if context else text
            for item in ai_files.image_parts(self._attachments):
                image_items.append({"url": item["image_url"]["url"]})
        self._sent_images = len(image_items)
        # 把附件记进这条用户消息里：气泡上方要显示「上传了什么」（图片小图 / 文档类型）
        meta = self._attachment_meta()
        entry: Dict[str, Any] = {"role": "user", "content": text}
        if meta:
            entry["attachments"] = meta
        self._history.append(entry)
        self._answer = ""
        self._reasoning = ""
        self._saw_reasoning = False
        self._notice = ""
        self._pending_calls = []
        self._stopped = False
        self._reset_usage_last()
        self.status.setText("正在生成…（可以点「停止」）")
        self.send_button.setText("停止")
        self._render()
        self._render_timer.start(60)

        messages = ai_chat.build_messages(
            self.store, self._scene_name(), payload_text,
            tools=self._tools_on, images=image_items)
        # 带上最近几轮上下文（只放对话，工具消息留在本轮里）
        recent = [{"role": m["role"], "content": m.get("content", "")}
                  for m in self._history[:-1]
                  if m.get("role") in ("user", "assistant")][-MAX_CONTEXT_TURNS * 2:]
        if recent:
            messages = messages[:1] + recent + messages[-1:]
        self._turn_messages = messages
        self._turn_round = 0
        self._start_round()

    # -------------------------------------------------- 一轮（可能带工具调用）
    def _start_round(self) -> None:
        from . import ai_tools

        tools = None
        if self._tools_on and ai_tools.enabled(self.store):
            try:
                tools = ai_tools.schemas(self.store)
            except Exception:
                tools = None
        worker = ai_chat.StreamWorker(self.store, self._turn_messages, tools=tools)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.delta.connect(self._on_delta)
        worker.reasoning.connect(self._on_reasoning)
        worker.usage.connect(self._on_usage)
        worker.metrics.connect(self._on_metrics)
        worker.tool_calls.connect(self._on_tool_calls)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda t=thread: self._on_thread_finished(t))
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_tool_calls(self, calls) -> None:
        self._pending_calls = list(calls or [])

    def _run_tool_round(self) -> None:
        """模型要求调工具：执行 → 把结果喂回去 → 再来一轮。"""
        from PySide6.QtWidgets import QApplication

        from . import ai_tools

        calls = self._pending_calls
        self._pending_calls = []
        partial = self._answer.strip()
        self._answer = ""
        self._reasoning = ""
        self._turn_round += 1
        if self._stopped:
            # 用户在等工具结果的时候点了「停止」：不再继续下一轮
            if partial:
                self._history.append({"role": "assistant", "content": partial})
            self._finish_turn()
            self.status.setText("已停止")
            return
        # 用户要求：**不再限制工具调用轮数**（原来超过 MAX_TOOL_ROUNDS=6 就停）。
        # 循环自然结束的条件是「模型这一轮不再返回 tool_calls」；用户随时可以点「停止」中断。
        # 这里只保留一个纯保护性的极端值（防止模型病态地无限自调用），正常对话永远不会触及。
        if self._turn_round > SAFETY_MAX_TOOL_ROUNDS:
            self._history.append({"role": "assistant",
                                  "content": partial or "（工具调用轮数异常，先停在这里）"})
            self.status.setText(f"工具调用超过 {SAFETY_MAX_TOOL_ROUNDS} 轮（异常保护），先停了")
            self._finish_turn()
            return

        # ① assistant 的这条消息（带 tool_calls）要原样回给模型
        packed = []
        for index, call in enumerate(calls):
            call_id = call.get("id") or f"call_{self._turn_round}_{index}"
            packed.append({
                "id": call_id,
                "type": "function",
                "function": {"name": call.get("name") or "",
                             "arguments": call.get("arguments") or "{}"},
            })
        self._turn_messages.append(
            {"role": "assistant", "content": partial, "tool_calls": packed})
        if partial:
            self._history.append({"role": "assistant", "content": partial})

        # ② 逐个执行（慢工具会一边跑一边刷界面）
        for index, call in enumerate(calls):
            name = call.get("name") or ""
            args = self._parse_args(call.get("arguments"))
            self.status.setText(f"正在{ai_tools.describe(name, args)}…")
            QApplication.processEvents()
            try:
                result = ai_tools.execute(self.store, name, args,
                                          pump=QApplication.processEvents)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "summary": f"执行失败：{exc}"}
            self._history.append({
                "role": "tool",
                "name": name,
                "label": ai_tools.describe(name, args),
                "ok": bool(result.get("ok")),
                "content": str(result.get("summary") or ""),
            })
            self._turn_messages.append({
                "role": "tool",
                "tool_call_id": packed[index]["id"],
                "content": ai_tools.result_for_model(result),
            })
        self._render()
        self.send_button.setText("停止")
        self.status.setText("工具执行完了，正在整理回答…")
        self._start_round()

    @staticmethod
    def _parse_args(raw) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def stop(self) -> None:
        self._stopped = True
        worker = self._worker
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
        self.status.setText("已停止生成（保留已生成的内容）")

    def _scene_name(self) -> Optional[str]:
        data = self.scene_box.currentData() or "system:assistant"
        if str(data).startswith("scene:"):
            return str(data).split(":", 1)[1]
        return None

    def _on_delta(self, piece: str) -> None:
        self._answer += piece
        if not self._render_timer.isActive():
            self._render_timer.start(60)

    def _on_reasoning(self, piece: str) -> None:
        self._saw_reasoning = True
        self._reasoning += piece
        if self.think_mode == "off" and not self._notice:
            # 明明关了还在思考：说清楚是接口没理会，而不是我们没关
            self._notice = ("这个接口没理会「关闭思考」：请求里已经带了 "
                            "chat_template_kwargs / enable_thinking 和 /no_think，"
                            "但它还是返回了思考内容。要么在服务端关掉思考模板，"
                            "要么把模型换成非推理模型（如 deepseek-chat）。")
        if not self._render_timer.isActive():
            self._render_timer.start(60)

    def _no_reasoning_notice(self) -> str:
        """模型没返回思考过程时给一句说明（不然会以为坏了）。"""
        cfg = ai_chat.config(self.store)
        model = (cfg.get("model") or "").strip() or "当前模型"
        if not self._thinking_visible:
            return ""
        return (f"这次没有思考内容：{model} 不返回「思考过程」。"
                f"DeepSeek 的 deepseek-chat 属于这一类，想要思考过程请在"
                f" 设置 → AI 接口 选「DeepSeek R1（会思考）」（模型名 deepseek-reasoner）。")

    def _on_done(self, finished: bool) -> None:
        if self._pending_calls:
            # 模型要调工具：执行完再来一轮，这一轮不算结束
            self._run_tool_round()
            return
        self._finalize(finished)

    def _finish_turn(self) -> None:
        self.send_button.setText("发送")
        self._render()

    def _finalize(self, finished: bool) -> None:
        self.send_button.setText("发送")
        answer = self._answer.strip() or "（模型返回了空内容）"
        reasoning = self._reasoning.strip()
        # 「记住……」：模型会在回答里带 [[记住]] 行，摘出来写进 memory.md
        remembered: List[str] = []
        try:
            from . import memory

            answer, remembered = memory.extract(answer)
            for item in remembered:
                memory.append(item)
        except Exception:
            remembered = []
        self._history.append({"role": "assistant", "content": answer,
                              "reasoning": reasoning})
        self._answer = ""
        self._reasoning = ""
        status = "完成" if finished else "已停止"
        if remembered:
            status += f"　已写入记忆 {len(remembered)} 条（设置里的 memory.md）"
        self.status.setText(status)
        if not reasoning and finished:
            self._notice = self._no_reasoning_notice()
        self._render()
        # 存进历史会话（第一句用户消息自动当标题）
        self._save_session()
        # 工具改过日程 / 课表：让日历、右侧栏跟着刷新
        if any(item.get("role") == "tool" for item in self._history[-12:]):
            try:
                window = self.window()
                refresh = getattr(window, "refresh_all", None)
                if callable(refresh):
                    refresh()
            except Exception:
                pass

    # -------------------------------------------------- 历史会话
    def _save_session(self) -> int:
        """把当前这段对话写进历史（没有就新建）。"""
        from . import chat_store

        if not any(item.get("role") == "user" for item in self._history):
            return self._session_id
        try:
            cfg = ai_chat.config(self.store)
            self._session_id = chat_store.save(
                self.store, self._session_id, self._history,
                title=self._session_title, model=cfg.get("model", ""),
                scene=self._scene_name() or "")
            if not self._session_title:
                loaded = chat_store.load(self.store, self._session_id) or {}
                self._session_title = str(loaded.get("title") or "")
        except Exception:
            pass
        return self._session_id

    def new_chat(self) -> None:
        """开一段新对话：当前这段已经在历史里了，这里只清空面板。"""
        if self._streaming:
            self.stop()
        self._save_session()
        self._session_id = 0
        self._session_title = ""
        self._history = []
        self._answer = ""
        self._reasoning = ""
        self._saw_reasoning = False
        self._notice = ""
        self._pending_calls = []
        self._turn_messages = []
        self._turn_round = 0
        self._stopped = False
        self._render()
        self.status.setText("已开始新对话（上一段已存进「历史」）")

    def open_history(self) -> None:
        """历史会话窗口：继续 / 重命名 / 删除。"""
        from .chat_history_dialog import ChatHistoryDialog

        self._save_session()
        dialog = ChatHistoryDialog(self.store, self.window(),
                                   current_session=self._session_id)
        try:
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            if dialog.selected_id:
                self.load_session(int(dialog.selected_id))
        finally:
            try:
                dialog.deleteLater()
            except Exception:
                pass

    def load_session(self, session_id: int) -> None:
        """把一段历史会话装回面板，接着聊。"""
        from . import chat_store

        data = chat_store.load(self.store, int(session_id))
        if not data:
            self.status.setText("这段会话已经不存在了")
            return
        if self._streaming:
            self.stop()
        items = [item for item in (data.get("items") or [])
                 if isinstance(item, dict)]
        self._history = items
        self._session_id = int(session_id)
        self._session_title = str(data.get("title") or "")
        self._answer = ""
        self._reasoning = ""
        self._saw_reasoning = False
        self._notice = ""
        self._pending_calls = []
        self._turn_messages = []
        self._turn_round = 0
        self._stopped = False
        self._reset_usage_last()
        self._render()
        self.status.setText(
            f"已载入「{self._session_title}」（{len(items)} 条记录），可以接着问")

    def _on_failed(self, message: str) -> None:
        self.send_button.setText("发送")
        hint = ""
        if getattr(self, "_sent_images", 0):
            # 带了图片却失败：多半是这个模型不认 image_url，说清楚怎么办
            hint = (f"（这次带了 {self._sent_images} 张图片：如果这个模型不收图片，"
                    f"换 qwen3-vl / gpt-4o 这类多模态模型，或者把图里的文字"
                    f"复制成文字再问）")
        self._sent_images = 0
        self.status.setText(f"请求失败：{message}{hint}")
        if self._answer.strip():
            self._history.append({"role": "assistant",
                                  "content": self._answer.strip(),
                                  "reasoning": self._reasoning.strip()})
        self._answer = ""
        self._reasoning = ""
        try:
            self._save_session()
        except Exception:
            pass
        self._render()

    def _on_thread_finished(self, thread=None) -> None:
        # 多轮工具调用时，上一轮的线程结束不能把新一轮的状态清掉
        if thread is not None and self._thread is not thread:
            return
        self._thread = None
        self._worker = None
        self.send_button.setText("发送")
        # 线程真正结束后再画一次：否则「正在生成…」会残留在已完成的回答下面
        self._render()

    def clear(self) -> None:
        """清空面板：先把这段存进「历史」，再开一段新的。"""
        if self._streaming:
            self.stop()
        self._save_session()
        self._session_id = 0
        self._session_title = ""
        self._history = []
        self._answer = ""
        self._reasoning = ""
        self._saw_reasoning = False
        self._notice = ""
        self._pending_calls = []
        self._turn_messages = []
        self._turn_round = 0
        self._stopped = False
        self.status.setText("已清空（刚才那段还在「历史」里）")
        self.refresh_model_label()
        self._render()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_model_label()
        self._render()
