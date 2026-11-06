# -*- coding: utf-8 -*-
"""邮件 HTML 的安全清洗：去掉脚本 / iframe / 事件属性，保留排版与图片。

为什么要自己洗：邮件里的 HTML 是别人发来的，直接塞进 QTextBrowser 会把
`<script>` 之类一起带进来（虽然 QTextBrowser 不执行脚本，但 `<style>` 里的
CSS 文本会被当成正文显示出来 —— 这就是以前「正文里出现 @media …」的原因）。
"""
from __future__ import annotations

import re

#: 整块删掉的标签（连同内容）
_BLOCK_TAGS = ("script", "style", "iframe", "object", "embed", "applet",
               "form", "button", "input", "select", "textarea", "link",
               "meta", "base", "title")
#: 只删标签、保留内容的
_SELF_TAGS = ("html", "head", "body")
_EVENT_ATTR = re.compile(
    r"\son[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_JS_URL = re.compile(
    r"((?:href|src|action)\s*=\s*)(?:\"|')?\s*(?:javascript|vbscript)\s*:[^\"'\s>]*",
    re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)

#: `style="…"` / `style='…'`（邮件模板两种引号都有）
_STYLE_ATTR = re.compile(r"\bstyle\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.I)
_TABLE_OPEN = re.compile(r"<table\b([^>]*)>", re.I)
_CELL_OPEN = re.compile(r"<(td|th)\b([^>]*)>", re.I)
_WIDTH_ATTR = re.compile(r"\bwidth\s*=", re.I)
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_SRC_ATTR = re.compile(r"\bsrc\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.I)
#: 本地图片的地址前缀（`imap_inbox` 把能下的图都换成了这样的文件名）
_LOCAL_PREFIX = ("", "file:")
#: 拿不到的图片：QtWebEngine 之外，QTextBrowser 不会去网上取图
_REMOTE_PREFIX = ("http:", "https:", "//", "data:", "cid:")


def _drop_remote_images(text: str) -> str:
    """删掉拿不到的远程图片，免得正文里留下一个破图占位。

    `imap_inbox` 会把能下载的图片换成本地文件名，剩下还是 http/https/data 的
    就是下不下来的（外链挂了 / 需要登录）——QTextBrowser 自己不会联网取图，
    留着只会在正文中间显示一个空白小方块。
    """
    def sub(match: "re.Match") -> str:
        tag = match.group(0)
        src = _SRC_ATTR.search(tag)
        if src is None:
            return tag
        value = (src.group(2) if src.group(2) is not None
                 else (src.group(3) or "")).strip().lower()
        if not value:
            return ""
        if value.startswith(_REMOTE_PREFIX):
            return ""
        return tag

    return _IMG_TAG.sub(sub, text)



def _style_text(match: "re.Match") -> str:
    value = match.group(2)
    return value if value is not None else (match.group(3) or "")


def _drop_decl(style: str, name: str, percent_only: bool = False) -> str:
    """从 style 里删掉某条声明（``percent_only`` 只删百分比值）。"""
    pattern = re.compile(r"(?i)(?:^|;)\s*%s\s*:\s*([^;\"']*)" % re.escape(name))

    def repl(match: "re.Match") -> str:
        if percent_only and "%" not in match.group(1):
            return match.group(0)
        return ""

    out = pattern.sub(repl, style)
    out = re.sub(r";\s*;", ";", out)
    return out.strip(" ;\t\r\n")


def _fix_tables(text: str) -> str:
    """把邮件模板的表格宽度翻译成 Qt 认的 `width` 属性。

    Qt 的富文本引擎对 CSS 的表格宽度支持得很差：`<td style="max-width:600px;
    width:100%">` 套 `<table style="width:100%">` 这种「响应式邮件模板」会被算成
    几十像素宽，正文于是**一个汉字一行**竖着排（用户截图里就是「各 / 位 / 同 /
    学」）。实测把百分比搬进 `width="100%"` 属性后，同样的 HTML 从 173 行回到 6 行。
    """
    def table_sub(match: "re.Match") -> str:
        attrs = match.group(1)
        width_value = ""
        style_match = _STYLE_ATTR.search(attrs)
        if style_match:
            raw = _style_text(style_match)
            percent = re.search(r"(?i)\bwidth\s*:\s*(\d+(?:\.\d+)?)\s*%", raw)
            pixels = re.search(r"(?i)\bwidth\s*:\s*(\d+(?:\.\d+)?)\s*px", raw)
            if percent:
                width_value = "%s%%" % percent.group(1)
            elif pixels:
                width_value = pixels.group(1)
            cleaned = _drop_decl(_drop_decl(_drop_decl(raw, "width"),
                                            "min-width"), "max-width")
            attrs = (attrs[:style_match.start()]
                     + (' style="%s"' % cleaned if cleaned else "")
                     + attrs[style_match.end():])
        if not _WIDTH_ATTR.search(attrs):
            attrs = ' width="%s"' % (width_value or "100%") + attrs
        return "<table%s>" % attrs

    def cell_sub(match: "re.Match") -> str:
        tag, attrs = match.group(1), match.group(2)
        style_match = _STYLE_ATTR.search(attrs)
        if style_match:
            raw = _style_text(style_match)
            cleaned = _drop_decl(_drop_decl(raw, "width", percent_only=True),
                                 "max-width")
            attrs = (attrs[:style_match.start()]
                     + (' style="%s"' % cleaned if cleaned else "")
                     + attrs[style_match.end():])
        return "<%s%s>" % (tag, attrs)

    text = _TABLE_OPEN.sub(table_sub, text)
    return _CELL_OPEN.sub(cell_sub, text)


def sanitize(html: str) -> str:
    """返回可以被 QTextBrowser 安全渲染的 HTML。"""
    text = str(html or "")
    if not text.strip():
        return ""
    text = _COMMENT.sub(" ", text)
    for tag in _BLOCK_TAGS:
        text = re.sub(r"<%s\b[^>]*>.*?</%s\s*>" % (tag, tag), " ", text,
                      flags=re.S | re.I)
        text = re.sub(r"<%s\b[^>]*/?>" % tag, " ", text, flags=re.I)
    for tag in _SELF_TAGS:
        text = re.sub(r"</?%s\b[^>]*>" % tag, " ", text, flags=re.I)
    text = _EVENT_ATTR.sub(" ", text)
    text = _JS_URL.sub(lambda m: m.group(1) + '"#"', text)
    text = re.sub(r"<!DOCTYPE[^>]*>", " ", text, flags=re.I)
    text = _fix_tables(text)
    text = _drop_remote_images(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def looks_like_html(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return False
    if "<html" in value or "<body" in value or "<table" in value:
        return True
    return value.count("<") > 8 and "</" in value
