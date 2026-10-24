"""文档读取：PDF 交给 QtPdf，DOCX / PPTX / XLSX 直接解析 OOXML（不依赖第三方库）。

* DOCX：解压 word/document.xml，按段落/加粗/斜体/字号/图片还原成 HTML
* PPTX：解压 ppt/slides/slideN.xml，按幻灯片输出标题/正文/图片
* XLSX：用 openpyxl 转成表格
* PDF ：交给 QtPdf（page_docs 里用 QPdfView 显示）
"""
from __future__ import annotations

import base64
import mimetypes
import re
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
M_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"

SUPPORTED = (".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md")

# 内嵌媒体上限：课件里常塞着几十兆的音频/视频，全部转成 base64 会让页面卡死
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_MEDIA_TOTAL = 14 * 1024 * 1024


class DocError(Exception):
    """带中文提示的读取错误。"""


# ------------------------------------------------------------------ 工具
def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    try:
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        return ""
    return f"data:{mime};base64,{payload}"


def _img_tag(zip_file: zipfile.ZipFile, member: str, width: int = 420,
             budget: Optional[List[int]] = None,
             seen: Optional[set] = None) -> str:
    """把包内的一张图片转成 data URI；非图片、超大图、超额都直接跳过。"""
    mime = mimetypes.guess_type(member)[0] or ""
    if not mime.startswith("image/"):
        return ""          # 音频 / 视频 / 字体一律不内嵌
    if seen is not None:
        if member in seen:
            return ""      # 同一张图在多页复用，只放一次
        seen.add(member)
    try:
        data = zip_file.read(member)
    except Exception:
        return ""
    if not data or len(data) > MAX_IMAGE_BYTES:
        return ""
    if budget is not None:
        if budget[0] <= 0:
            return ""
        budget[0] -= len(data)
    payload = base64.b64encode(data).decode("ascii")
    return (f"<p><img src='data:{mime};base64,{payload}' width='{width}'></p>")


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _looks_like_document(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED:
        return suffix
    return None


# ------------------------------------------------------------------ DOCX
def docx_to_html(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "word/document.xml" not in names:
                raise DocError("这个 .docx 里没有正文（可能是旧版 .doc 改了后缀）")
            xml = archive.read("word/document.xml")
            root = ET.fromstring(xml)

            # 先记下图片位置
            images = [name for name in names if name.startswith("word/media/")]
            image_index = {"i": 0}
            budget = [MAX_MEDIA_TOTAL]
            seen: set = set()

            def _format_run(text: str, props) -> str:
                bold = italic = underline = False
                size = None
                if props is not None:
                    bold = props.find(f"{W_NS}b") is not None
                    italic = props.find(f"{W_NS}i") is not None
                    underline = props.find(f"{W_NS}u") is not None
                    size_node = props.find(f"{W_NS}sz")
                    if size_node is not None:
                        try:
                            size = int(size_node.get(f"{W_NS}val")) / 2.0
                        except (TypeError, ValueError):
                            size = None
                escaped = (text.replace("&", "&amp;").replace("<", "&lt;")
                           .replace(">", "&gt;"))
                if bold:
                    escaped = f"<b>{escaped}</b>"
                if italic:
                    escaped = f"<i>{escaped}</i>"
                if underline:
                    escaped = f"<u>{escaped}</u>"
                if size and size >= 16:
                    escaped = (f"<span style='font-size:{size:.0f}pt'>{escaped}</span>")
                return escaped

            def _paragraph_html(paragraph) -> str:
                """按文档顺序输出：普通文字保留格式，Word 公式（OMML）转成斜体文字。"""
                pieces: List[str] = []
                props = None
                in_math = False
                for node in paragraph.iter():
                    tag = node.tag
                    if tag == f"{W_NS}r":
                        props = node.find(f"{W_NS}rPr")
                        in_math = False
                    elif tag == f"{M_NS}oMath":
                        in_math = True
                        props = None
                    elif tag == f"{M_NS}t":
                        text = (node.text or "")
                        if text.strip() or text:
                            pieces.append("<i>" + _escape(text) + "</i>")
                    elif tag == f"{A_NS}blip":
                        index = image_index["i"]
                        if index < len(images):
                            image_index["i"] += 1
                            pieces.append(_img_tag(archive, images[index], 420,
                                                   budget, seen))
                    elif tag == f"{W_NS}t" and not in_math:
                        text = node.text or ""
                        if text:
                            pieces.append(_format_run(text, props))
                return "".join(pieces)

            parts: List[str] = []
            for paragraph in root.iter(f"{W_NS}p"):
                html = _paragraph_html(paragraph).strip()
                if html:
                    parts.append(f"<p>{html}</p>")
                # 表格里的段落也会被遍历到，简单起见按段落输出
            if not parts:
                raise DocError("没有解析到正文内容")
            return "".join(parts)
    except DocError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocError(f"读取 .docx 失败：{exc}") from exc


# ------------------------------------------------------------------ PPTX
def pptx_to_html(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            slides = sorted(
                [name for name in names
                 if re.match(r"ppt/slides/slide\d+\.xml$", name)],
                key=lambda name: int(re.search(r"slide(\d+)", name).group(1)),
            )
            if not slides:
                raise DocError("这个 .pptx 里没有幻灯片")
            media = [name for name in names if name.startswith("ppt/media/")]
            budget = [MAX_MEDIA_TOTAL]
            seen: set = set()
            parts: List[str] = []
            for index, slide in enumerate(slides, start=1):
                root = ET.fromstring(archive.read(slide))
                texts: List[str] = []
                for paragraph in root.iter(f"{A_NS}p"):
                    line = "".join(node.text or "" for node in paragraph.iter(f"{A_NS}t"))
                    line = line.strip()
                    if line:
                        texts.append(line)
                body = "".join(f"<p>{t}</p>" for t in texts[:20]) or "<p>（本页无文字）</p>"
                # 本页图片：ppt/slides/_rels/slideN.xml.rels 里引用 media
                images_html = ""
                rels_name = f"ppt/slides/_rels/{Path(slide).name}.rels"
                if rels_name in names:
                    try:
                        rels = ET.fromstring(archive.read(rels_name))
                        for rel in rels:
                            target = rel.get("Target") or ""
                            if "media/" in target:
                                member = "ppt/" + target.replace("../", "").lstrip("/")
                                if member in names:
                                    images_html += _img_tag(archive, member, 380,
                                                            budget, seen)
                    except Exception:
                        pass
                parts.append(
                    f"<h3>第 {index} 页</h3>{body}{images_html}<hr>")
            return "".join(parts)
    except DocError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocError(f"读取 .pptx 失败：{exc}") from exc


# ------------------------------------------------------------------ XLSX
def xlsx_to_html(path: Path) -> str:
    try:
        import openpyxl
    except Exception as exc:  # noqa: BLE001
        raise DocError(f"缺少 openpyxl，无法读取 Excel：{exc}") from exc
    try:
        book = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise DocError(f"打开 Excel 失败：{exc}") from exc
    parts: List[str] = []
    for sheet in book.worksheets:
        parts.append(f"<h3>{sheet.title}</h3>")
        parts.append("<table border='1' cellspacing='0' cellpadding='4'>")
        for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
            if row_index > 200:
                break
            cells = "".join(
                f"<td>{'' if value is None else str(value)}</td>" for value in row)
            parts.append(f"<tr>{cells}</tr>")
        parts.append("</table>")
    book.close()
    return "".join(parts) or "<p>（空表格）</p>"


# ------------------------------------------------------------------ 文本
def text_to_html(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        raise DocError(f"读取文本失败：{exc}") from exc
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "<pre style='white-space:pre-wrap'>" + escaped + "</pre>"


# ------------------------------------------------------------------ 统一入口
def render(path: Path) -> Tuple[str, str]:
    """返回 (类型, 内容)：
    类型 "pdf" → 内容为空（交给 QPdfView）
    类型 "html" → 内容是 HTML
    """
    path = Path(path)
    if not path.exists():
        raise DocError(f"文件不存在：{path}")
    suffix = _looks_like_document(path)
    if suffix is None:
        raise DocError(f"暂不支持这种格式：{path.suffix}")
    if suffix == ".pdf":
        return "pdf", ""
    if suffix == ".docx":
        return "html", docx_to_html(path)
    if suffix == ".pptx":
        return "html", pptx_to_html(path)
    if suffix == ".xlsx":
        return "html", xlsx_to_html(path)
    return "html", text_to_html(path)
