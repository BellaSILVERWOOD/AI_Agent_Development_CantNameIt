# -*- coding: utf-8 -*-
"""给一篇文献生成「方法流程图」：问模型要几个步骤，再自己画成 PNG。

为什么要自己画：模型只会写字，直接把它的 ASCII 图贴出来又丑又对不齐。
这里让它输出**结构化步骤**（JSON），然后用 QPainter 画成竖排方框 + 箭头，
字体、配色都跟应用主题一致，还能存成图片带走。

* `generate()`：有缓存就用缓存（`data/lit_flows/<id>.png`）；
* 模型调用走 `ai_chat.chat()`（就是用户在设置里配的那套接口）；
* `render_steps()` 只负责画，纯 Qt，不联网。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

FLOW_PROMPT = (
    "请把这篇论文的**方法流程**拆成 4–8 个步骤，每个步骤一句话（不超过 18 个字），"
    "按执行顺序排列。只输出 JSON，不要解释：\n"
    '{"title": "流程标题（不超过 14 字）", "steps": ["步骤一", "步骤二"]}\n\n'
    "标题：{title}\n\n摘要：{abstract}\n"
)

MAX_STEPS = 10


def flow_dir() -> Path:
    from .config import DATA_DIR

    folder = Path(DATA_DIR) / "lit_flows"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def flow_path(paper_id: int) -> Path:
    return flow_dir() / ("flow-%d.png" % int(paper_id))


def parse_steps(text: str) -> Dict[str, Any]:
    """从模型输出里抠出 {"title", "steps"}（容忍前后有多余的话）。"""
    raw = str(text or "")
    match = re.search(r"\{.*\}", raw, re.S)
    data: Any = None
    if match:
        try:
            data = json.loads(match.group(0))
        except Exception:
            data = None
    if data is None:
        # 退一步：按行取「1. xxx」这种
        steps = [line.strip(" 0123456789.、)）") for line in raw.splitlines()
                 if line.strip() and len(line.strip()) > 2]
        return {"title": "方法流程", "steps": steps[:MAX_STEPS]}
    title = str((data or {}).get("title") or "方法流程").strip()
    steps: List[str] = []
    for item in (data or {}).get("steps") or []:
        if isinstance(item, dict):
            text_value = str(item.get("label") or item.get("text") or "").strip()
        else:
            text_value = str(item or "").strip()
        if text_value:
            steps.append(text_value)
    return {"title": title, "steps": steps[:MAX_STEPS]}


def generate(store, paper_id: int, force: bool = False,
             timeout: float = 120.0) -> Dict[str, Any]:
    """生成流程图，返回 {"ok","title","steps","path","error"}。"""
    from . import ai_chat, lit

    paper = lit.get_paper(store, int(paper_id))
    if not paper:
        return {"ok": False, "error": "找不到这篇文献", "steps": [], "path": ""}
    target = flow_path(paper_id)
    if target.exists() and not force:
        return {"ok": True, "title": "方法流程", "steps": [],
                "path": str(target), "cached": True}
    title = str(paper.get("title") or "")
    abstract = str(paper.get("abstract") or "").strip()
    if not abstract:
        return {"ok": False, "error": "这篇没有摘要，没法拆流程", "steps": [],
                "path": ""}
    prompt = (FLOW_PROMPT.replace("{title}", title)
              .replace("{abstract}", abstract[:1500]))
    try:
        text = ai_chat.chat(store, [{"role": "user", "content": prompt}],
                            timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"调用模型失败：{exc}", "steps": [],
                "path": ""}
    parsed = parse_steps(text)
    steps = parsed.get("steps") or []
    if not steps:
        return {"ok": False, "error": "模型没给出步骤，再试一次", "steps": [],
                "path": ""}
    ok = render_steps(steps, str(parsed.get("title") or "方法流程"),
                      str(target))
    return {"ok": bool(ok), "title": parsed.get("title") or "方法流程",
            "steps": steps, "path": str(target) if ok else "",
            "error": "" if ok else "画图失败"}


# ------------------------------------------------------------------ 画图
def render_steps(steps: List[str], title: str, target: str,
                 width: int = 760) -> bool:
    """把步骤画成竖排流程图（方框 + 箭头），存成 PNG。"""
    from PySide6.QtCore import QPoint, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygon

    from . import theme

    steps = [str(step) for step in steps or [] if str(step).strip()]
    if not steps:
        return False
    dark = theme.effective_mode() == "dark"
    card = QColor(theme.CARD if dark else "#FFFFFF")
    border = QColor(theme.BORDER if dark else "#D9D9DE")
    text_color = QColor(theme.TEXT)
    accent = QColor(theme.appearance().accent)

    margin = 28
    box_w = width - margin * 2
    gap = 34
    title_h = 54
    box_h = 62
    height = margin * 2 + title_h + len(steps) * (box_h + gap) - gap

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(card)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    title_font = QFont()
    title_font.setPointSizeF(15.0)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.setPen(QPen(text_color))
    painter.drawText(QRectF(margin, margin - 6, box_w, title_h),
                     int(Qt.AlignmentFlag.AlignHCenter
                         | Qt.AlignmentFlag.AlignVCenter), title)

    step_font = QFont()
    step_font.setPointSizeF(11.5)
    painter.setFont(step_font)

    y = margin + title_h
    for index, step in enumerate(steps, start=1):
        rect = QRectF(margin, y, box_w, box_h)
        # 第一颗高亮成主题色，其余用卡片色
        if index == 1:
            fill = QColor(accent)
            fill.setAlpha(38)
            pen = QPen(accent)
        else:
            fill = QColor(theme.CARD_ALT if dark else "#F6F6F8")
            pen = QPen(border)
        pen.setWidthF(1.4)
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 12, 12)
        painter.setPen(QPen(text_color))
        painter.drawText(rect.adjusted(18, 0, -18, 0),
                         int(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter),
                         "%d. %s" % (index, step))
        # 箭头（最后一颗不画）
        if index < len(steps):
            center_x = width / 2.0
            start_y = y + box_h + 6
            end_y = y + box_h + gap - 8
            painter.setBrush(accent)
            painter.setPen(QPen(accent, 2))
            painter.drawLine(int(center_x), int(start_y), int(center_x),
                             int(end_y - 8))
            head = 8
            painter.drawPolygon(QPolygon([
                QPoint(int(center_x), int(end_y)),
                QPoint(int(center_x - head), int(end_y - head - 2)),
                QPoint(int(center_x + head), int(end_y - head - 2)),
            ]))
        y += box_h + gap

    painter.end()
    try:
        return bool(image.save(str(target), "PNG"))
    except Exception:
        return False
