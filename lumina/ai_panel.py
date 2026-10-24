"""AI 面板：三个标签页 —— 系统提示词 / WebUI 对话 / API 服务。

* 系统提示词：就地编辑 data/prompts.json 里的提示词，保存即生效；
* WebUI 对话：内嵌 DeepSeek / 豆包 / Kimi / 自定义站点（用网页，登录态保留）；
* API 服务：内置的流式对话窗口（不加载网页），走 OpenAI 兼容接口。

设置项：ai_service、ai_custom_sites、ai_local_sites（都是 JSON 列表）
"""
from __future__ import annotations

import json
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QStackedWidget, QVBoxLayout, QWidget,
)

from . import theme
from .web_panel import EmbeddedWebPanel
from .widgets import Select, SoftButton

SERVICE_KEY = "ai_service"
CUSTOM_KEY = "ai_custom_sites"
LOCAL_KEY = "ai_local_sites"
ADD_CUSTOM = "__add_custom__"
ADD_LOCAL = "__add_local__"

TAB_PROMPT = 0
TAB_WEBUI = 1
TAB_API = 2

SERVICES: List[Tuple[str, str, str]] = [
    ("deepseek", "DeepSeek", "https://chat.deepseek.com/"),
    ("doubao", "豆包", "https://www.doubao.com/chat/"),
    ("kimi", "Kimi", "https://kimi.moonshot.cn/"),
]


def _load_sites(store, key: str) -> List[Dict[str, str]]:
    raw = store.get_setting(key, "") or ""
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    sites: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        sites.append({
            "id": str(item.get("id") or url),
            "name": str(item.get("name") or urlparse(url).netloc or url),
            "url": url,
        })
    return sites


def _save_sites(store, key: str, sites: List[Dict[str, str]]) -> None:
    store.set_setting(key, json.dumps(sites, ensure_ascii=False))


def _name_from_url(url: str) -> str:
    host = urlparse(url).netloc or url
    return host.replace("www.", "")


class AiPage(QWidget):
    """内嵌的 AI 面板（右侧 dock 里用 compact 模式）。"""

    def __init__(self, store, parent=None, compact: bool = False):
        super().__init__(parent)
        self.store = store
        self._compact = bool(compact)
        self._custom = _load_sites(store, CUSTOM_KEY)
        self._local = _load_sites(store, LOCAL_KEY)
        self._service = (store.get_setting(SERVICE_KEY, "deepseek")
                         or "deepseek").strip()
        self._tab = TAB_PROMPT

        root = QVBoxLayout(self)
        if self._compact:
            root.setContentsMargins(6, 6, 6, 6)
        else:
            root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(8 if self._compact else 10)

        # -------------------------------------------------- 顶部：三个标签
        header = QHBoxLayout()
        header.setSpacing(6)
        self.prompt_tab = SoftButton("系统提示词", "primary", height=28,
                                     min_width=92, point_size=8.4)
        self.prompt_tab.setToolTip("调整 AI 的人设与场景提示词")
        self.prompt_tab.clicked.connect(lambda: self.set_tab(TAB_PROMPT))
        header.addWidget(self.prompt_tab)
        self.webui_tab = SoftButton("WebUI 对话", "ghost", height=28,
                                    min_width=92, point_size=8.4)
        self.webui_tab.setToolTip("内嵌 DeepSeek / 豆包 / Kimi 网页")
        self.webui_tab.clicked.connect(lambda: self.set_tab(TAB_WEBUI))
        header.addWidget(self.webui_tab)
        self.api_tab = SoftButton("API 服务", "ghost", height=28,
                                  min_width=84, point_size=8.4)
        self.api_tab.setToolTip("用接口 Key 的内置流式对话窗口")
        self.api_tab.clicked.connect(lambda: self.set_tab(TAB_API))
        header.addWidget(self.api_tab)
        header.addStretch(1)

        self.service_select = Select()
        self.service_select.setFixedWidth(132 if self._compact else 152)
        self.service_select.currentIndexChanged.connect(self._on_service_changed)
        self.service_select.setVisible(False)
        header.addWidget(self.service_select)

        self.fullscreen_button = SoftButton("全屏", "ghost", height=28,
                                            min_width=60, point_size=8.4)
        self.fullscreen_button.setToolTip("全屏看 AI（Esc 退出）")
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        if self._compact:
            header.addWidget(self.fullscreen_button)
        root.addLayout(header)

        self.note = QLabel("")
        self.note.setObjectName("Tiny")
        root.addWidget(self.note)

        # -------------------------------------------------- 三个页面
        self.stack = QStackedWidget()
        from .ai_api_panel import ApiChatPanel
        from .ai_prompt_panel import PromptPanel

        self.prompt_panel = PromptPanel(store, compact=compact, parent=self)
        self.prompt_panel.saved.connect(
            lambda title: self._set_note(f"提示词「{title}」已保存"))
        self.stack.addWidget(self.prompt_panel)

        url = self._resolve_url(self._service)
        self.web = EmbeddedWebPanel("lumina-ai", "ai", url, self,
                                    store=store, autofill=False)
        self.web.loaded.connect(self._on_loaded)
        self.stack.addWidget(self.web)
        self.api_panel = ApiChatPanel(store, compact=compact, parent=self)
        self.api_panel.agent_requested.connect(self.open_agent)
        self.api_panel.settings_requested.connect(self._open_llm_settings)
        self.stack.addWidget(self.api_panel)
        root.addWidget(self.stack, 1)

        # 提前建好内嵌浏览器（原生窗口在窗口显示前创建），第一次切过去就不会整窗闪一下
        import os

        if not os.environ.get("LUMINA_SMOKE"):
            try:
                self.web.ensure_view()
            except Exception:
                pass
        if self._compact:
            try:
                self.web.set_compact(True)
            except Exception:
                pass

        self._rebuild_options()
        self.set_tab(TAB_PROMPT, refresh=False)

    # -------------------------------------------------- 标签
    def current_tab(self) -> int:
        return self._tab

    def set_tab(self, index: int, refresh: bool = True) -> None:
        index = index if index in (TAB_PROMPT, TAB_WEBUI, TAB_API) else TAB_PROMPT
        self._tab = index
        self.stack.setCurrentIndex(index)
        for button, own in ((self.prompt_tab, TAB_PROMPT),
                            (self.webui_tab, TAB_WEBUI),
                            (self.api_tab, TAB_API)):
            button.setKind("primary" if own == index else "ghost")
        # WebUI 才显示服务下拉
        self.service_select.setVisible(index == TAB_WEBUI)
        if index == TAB_PROMPT:
            self._set_note("改完点「保存」，下次对话就用新的提示词")
        elif index == TAB_WEBUI:
            self._set_note("网页登录一次就会记住；切走再回来不用重新登录")
            if refresh:
                try:
                    self.web.reload()
                except Exception:
                    pass
        else:
            cfg = self._model_hint()
            self._set_note(cfg)
            if refresh:
                try:
                    self.api_panel.refresh_model_label()
                except Exception:
                    pass

    def _model_hint(self) -> str:
        from . import ai_chat

        cfg = ai_chat.config(self.store)
        if not ai_chat.api_ready(self.store):
            return "还没配置接口：设置 → AI 接口 里选服务、填模型名和 API Key"
        return f"接口：{cfg.get('model') or '（未填模型）'} · {cfg.get('base_url')}"

    def _set_note(self, text: str) -> None:
        self.note.setText(text)

    def open_api_chat(self) -> None:
        self.set_tab(TAB_API)

    def open_prompt_editor(self) -> None:
        self.set_tab(TAB_PROMPT)

    # -------------------------------------------------- Agent / 设置
    def open_agent(self) -> None:
        from .agent_dialog import AgentDialog

        dialog = AgentDialog(self.store, self.window())
        dialog.changed.connect(self._refresh_after_agent)
        dialog.exec()

    def _refresh_after_agent(self) -> None:
        window = self.window()
        refresh = getattr(window, "refresh_all", None)
        if callable(refresh):
            try:
                refresh()
                return
            except Exception:
                pass
        for attr in ("dock", "right_dock"):
            target = getattr(window, attr, None)
            method = getattr(target, "refresh", None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass

    def _open_llm_settings(self) -> None:
        window = self.window()
        handler = getattr(window, "open_llm_settings", None)
        if callable(handler):
            try:
                handler()
                return
            except Exception:
                pass

    # -------------------------------------------------- 全屏
    def toggle_fullscreen(self) -> None:
        window = self.window()
        setter = getattr(window, "set_panel_fullscreen", None)
        if not callable(setter):
            return
        state = not getattr(self, "_fullscreen", False)
        self.sync_fullscreen_state(state)
        setter(state, self)
        self._fullscreen = state

    def sync_fullscreen_state(self, on: bool) -> None:
        self._fullscreen = bool(on)
        try:
            self.fullscreen_button.setText("退出全屏" if on else "全屏")
        except Exception:
            pass
        try:
            self.web.sync_fullscreen_state(on)
        except Exception:
            pass

    # -------------------------------------------------- 网页服务
    def _all_sites(self) -> List[Tuple[str, str, str]]:
        sites = list(SERVICES)
        for item in self._custom:
            sites.append((item["id"], item["name"], item["url"]))
        for item in self._local:
            sites.append((item["id"], item["name"], item["url"]))
        return sites

    def _resolve_url(self, service_id: str) -> str:
        for site_id, _name, url in self._all_sites():
            if site_id == service_id:
                return url
        return SERVICES[0][2]

    def _resolve_name(self, service_id: str) -> str:
        for site_id, name, _url in self._all_sites():
            if site_id == service_id:
                return name
        return "AI"

    def _rebuild_options(self) -> None:
        self.service_select.blockSignals(True)
        self.service_select.clear()
        for site_id, name, _url in SERVICES:
            self.service_select.addItem(name, site_id)
        for item in self._custom:
            self.service_select.addItem(item["name"], item["id"])
        for item in self._local:
            self.service_select.addItem(f"🖥 {item['name']}", item["id"])
        self.service_select.addItem("＋ 添加自定义网址…", ADD_CUSTOM)
        self.service_select.addItem("＋ 添加本地模型…", ADD_LOCAL)
        index = self.service_select.findData(self._service)
        self.service_select.setCurrentIndex(max(0, index))
        self.service_select.blockSignals(False)

    def _on_service_changed(self, _index: int) -> None:
        service_id = self.service_select.currentData() or "deepseek"
        if service_id == ADD_CUSTOM:
            self._prompt_custom()
            self._rebuild_options()
            return
        if service_id == ADD_LOCAL:
            self._prompt_local()
            self._rebuild_options()
            return
        if service_id == self._service:
            return
        self._service = service_id
        self.store.set_setting(SERVICE_KEY, service_id)
        name = self._resolve_name(service_id)
        self._set_note(f"正在打开 {name}…")
        self.web.load(self._resolve_url(service_id))

    def _prompt_custom(self) -> None:
        url, ok = QInputDialog.getText(
            self, "添加自定义网址",
            "粘贴要内嵌的 AI 网址（例如公司内网 / 自建的 ChatGPT 页面）：")
        url = (url or "").strip()
        if not ok or not url.startswith("http"):
            self._restore_selection()
            return
        site = {"id": f"custom-{len(self._custom) + 1}", "name": _name_from_url(url),
                "url": url}
        self._custom.append(site)
        _save_sites(self.store, CUSTOM_KEY, self._custom)
        self._service = site["id"]
        self.store.set_setting(SERVICE_KEY, site["id"])
        self.web.load(site["url"])
        self._set_note(f"已添加 {site['name']}")

    def _prompt_local(self) -> None:
        url, ok = QInputDialog.getText(
            self, "添加本地模型",
            "本地模型服务的 Web UI 地址（llama.cpp / SGLang / Ollama 都可以）：\n"
            "例如 http://127.0.0.1:8080",
            text="http://127.0.0.1:8080")
        url = (url or "").strip()
        if not ok or not url.startswith("http"):
            self._restore_selection()
            return
        site = {"id": f"local-{len(self._local) + 1}",
                "name": _name_from_url(url), "url": url}
        self._local.append(site)
        _save_sites(self.store, LOCAL_KEY, self._local)
        self._service = site["id"]
        self.store.set_setting(SERVICE_KEY, site["id"])
        self.web.load(site["url"])
        self._set_note(f"已添加本地模型 {site['name']} · 记得先在设置里启动模型服务")

    def _restore_selection(self) -> None:
        self.service_select.blockSignals(True)
        index = self.service_select.findData(self._service)
        self.service_select.setCurrentIndex(max(0, index))
        self.service_select.blockSignals(False)

    def _on_loaded(self, ok: bool) -> None:
        name = self._resolve_name(self._service)
        if ok:
            self._set_note(f"{name} 已就绪 · 登录状态会保留")
        else:
            self._set_note(f"{name} 加载失败，检查一下网络 / VPN / 本地服务")
