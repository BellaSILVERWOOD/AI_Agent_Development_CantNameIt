"""把本地文件读成纯文本、把图片读成「可直接发给模型的数据」，塞进对话上下文。

支持：
* 纯文本类（`TEXT_SUFFIXES`）：按 UTF-8 → GB18030 依次尝试解码；
* Word / PowerPoint：复用 `docs_reader` 的 XML 解析，再去掉 HTML 标签；
* **PDF**：用 QtPdf 逐页抽文字（不依赖第三方库），页数 / 字数都有上限；
* **图片**（png / jpg / webp / gif / bmp）：转成 `data:image/…;base64,…`，
  作为「视觉输入」发给支持多模态的模型（OpenAI 兼容的 `image_url` 写法）。
  太大就自动缩小、重新压成 JPEG；超过 `MAX_IMAGE_BYTES` 直接拒掉并说清楚；
* 老式的 .doc / .xls（OLE 复合文档）不做解析，会明确提示另存为新格式；
* 可以一次给很多个文件、也可以直接拖整个文件夹进来（`expand_paths` 会递归展开）。

限制：单文件最多 `MAX_CHARS` 个字符（超出截断并标注）；
一次最多 `MAX_FILES` 个文件、其中最多 `MAX_IMAGES` 张图、
合计最多 `MAX_TOTAL_CHARS` 字符（超出会先丢掉最早附加的）。
"""
from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

MAX_CHARS = 24000
MAX_FILES = 20
MAX_TOTAL_CHARS = 60000
MAX_FOLDER_FILES = 200        # 拖一个文件夹进来时最多扫这么多个文件
MAX_IMAGES = 6                # 一次最多带几张图
MAX_IMAGE_BYTES = 4 * 1024 * 1024     # 单张图超过 4MB 就拒掉
MAX_IMAGE_SIDE = 1600         # 太大就缩到这个边长以内再发

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".tex", ".bib", ".csv", ".tsv", ".log",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".bat", ".sql",
    ".html", ".htm", ".xml", ".css",
}
OFFICE_SUFFIXES = {".docx", ".pptx"}
PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
#: 这些名字看着就像「能吃图片」的模型（DeepSeek 的 reasoner / chat 不吃图）
VISION_HINTS = ("vl", "vision", "gpt-4o", "gpt-4.1", "gpt-5", "o3", "o4",
                "gemini", "claude", "glm-4v", "glm-4.5v", "internvl", "llava",
                "minicpm-v", "pixtral", "qwen2-vl", "qwen2.5-vl", "qwen3-vl",
                "step-1v", "ernie-4.5-vl", "doubao-vision")
#: PDF 最多读多少页 / 每页最多留多少字（扫描件一般是空的，会提示）
PDF_MAX_PAGES = 60
PDF_MAX_CHARS = 24000


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() in PDF_SUFFIXES


def supported(path: Path) -> bool:
    return path.suffix.lower() in (TEXT_SUFFIXES | OFFICE_SUFFIXES
                                   | PDF_SUFFIXES | IMAGE_SUFFIXES)


def vision_like(model: str) -> bool:
    """模型名看着像支持看图吗（只是提醒用，不做硬拦）。"""
    name = str(model or "").lower()
    if not name:
        return False
    return any(hint in name for hint in VISION_HINTS)


def file_filter() -> str:
    text_pat = " ".join("*" + suffix for suffix in sorted(TEXT_SUFFIXES))
    image_pat = " ".join("*" + suffix for suffix in sorted(IMAGE_SUFFIXES))
    return (f"支持的文件 ({text_pat} *.pdf *.docx *.pptx {image_pat});;"
            f"文本文件 ({text_pat});;PDF (*.pdf);;"
            f"Word / PPT (*.docx *.pptx);;"
            f"图片 ({image_pat});;所有文件 (*.*)")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _html_to_text(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "",
                  flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def expand_paths(paths: Iterable, recursive: bool = True) -> Tuple[List[Path], List[str]]:
    """把「文件 + 文件夹」展开成一串文件。

    返回 (文件列表, 提示信息)。文件夹会递归找支持的文件；隐藏文件跳过。
    """
    files: List[Path] = []
    notes: List[str] = []
    seen = set()
    for raw in paths:
        path = Path(str(raw))
        if not path.exists():
            notes.append(f"{path.name}：找不到这个文件")
            continue
        if path.is_dir():
            if not recursive:
                notes.append(f"{path.name}：是个文件夹")
                continue
            found = []
            try:
                for child in sorted(path.rglob("*")):
                    if len(found) >= MAX_FOLDER_FILES:
                        notes.append(f"文件夹 {path.name} 文件太多，只取了前 "
                                     f"{MAX_FOLDER_FILES} 个")
                        break
                    if not child.is_file():
                        continue
                    if any(part.startswith(".") for part in child.parts):
                        continue
                    if supported(child):
                        found.append(child)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"文件夹 {path.name} 读不出来：{exc}")
                continue
            if not found:
                notes.append(f"文件夹 {path.name} 里没有可读的文件")
            files.extend(found)
            continue
        files.append(path)
    # 去重（同一个文件拖两次只留一份）
    unique: List[Path] = []
    for path in files:
        try:
            key = str(path.resolve()).lower()
        except Exception:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique, notes


def _read_pdf_text(path: Path, max_pages: int = PDF_MAX_PAGES) -> Tuple[str, str]:
    """用 QtPdf 逐页抽文字，返回 (text, error)。

    不依赖 poppler / pypdf：PySide6 自带 QtPdf（公式预览、文档页都在用）。
    扫描件（纯图片 PDF）抽不到字，这里如实说明，不要假装成功。
    """
    try:
        from PySide6.QtPdf import QPdfDocument
    except Exception:
        return "", "这台机器上的 Qt 没有 QtPdf，读不了 PDF（可以另存为 .docx）"
    document = QPdfDocument()
    try:
        error = document.load(str(path))
        if error != QPdfDocument.Error.None_:
            return "", "PDF 打不开（可能加密或损坏）"
        pages = int(document.pageCount() or 0)
        if pages <= 0:
            return "", "这个 PDF 里没有页面"
        chunks: List[str] = []
        used = 0
        for index in range(min(pages, max_pages)):
            try:
                selection = document.getAllText(index)
                text = selection.text() if selection is not None else ""
            except Exception:
                text = ""
            text = (text or "").strip()
            if not text:
                continue
            chunks.append(f"—— 第 {index + 1} 页 ——\n{text}")
            used += len(text)
            if used >= PDF_MAX_CHARS:
                break
        if not chunks:
            return "", ("这个 PDF 里抽不到文字（大概是扫描件 / 图片版），"
                        "可以截图后当图片发，或者先 OCR 成文字")
        text = "\n\n".join(chunks)
        if pages > max_pages:
            text += f"\n…（共 {pages} 页，只读了前 {max_pages} 页）"
        return text, ""
    except Exception as exc:  # noqa: BLE001
        return "", f"读 PDF 出错了：{exc}"
    finally:
        try:
            document.close()
        except Exception:
            pass


def load_text(path: Path) -> Tuple[str, str]:
    """返回 (text, error)。error 非空时 text 为空。"""
    if not path.exists():
        return "", "文件不存在"
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "", "这是图片（会作为图片发给模型，不需要读成文字）"
    try:
        if suffix in OFFICE_SUFFIXES:
            from . import docs_reader

            html = (docs_reader.docx_to_html(path) if suffix == ".docx"
                    else docs_reader.pptx_to_html(path))
            text = _html_to_text(html)
        elif suffix in PDF_SUFFIXES:
            text, error = _read_pdf_text(path)
            if error:
                return "", error
        elif suffix in TEXT_SUFFIXES:
            text = _read_text(path)
        else:
            return "", (f"暂不支持 {suffix or '这种'} 文件"
                        f"（PDF / Word / PPT / 文本才认；老的 .doc、.xls 请另存为新格式）")
    except Exception as exc:  # noqa: BLE001
        return "", f"读取失败：{exc}"
    text = (text or "").strip()
    if not text:
        return "", "文件里没有可读的文字（可能是扫描件 / 图片）"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n…（已截断，原文件更长）"
    return text, ""


def image_data_url(path: Path) -> Tuple[str, str]:
    """把图片转成 `data:image/…;base64,…`，返回 (data_url, error)。"""
    try:
        raw = path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        return "", f"读不出来：{exc}"
    if not raw:
        return "", "文件是空的"
    if len(raw) > MAX_IMAGE_BYTES:
        shrunk, error = _shrink_image(path, raw)
        if error:
            return "", error
        raw = shrunk
    mime = (mimetypes.guess_type(path.name)[0] or "image/png")
    if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"):
        mime = "image/png"
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii")), ""


def _shrink_image(path: Path, raw: bytes) -> Tuple[bytes, str]:
    """太大就缩一下（有 Pillow 就缩，没有就直接拒掉）。"""
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        return b"", (f"图片太大（{len(raw) / 1024 / 1024:.1f}MB），"
                     f"超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB，先压缩一下再发")
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            image = image.convert("RGB")
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=85)
            return out.getvalue(), ""
    except Exception as exc:  # noqa: BLE001
        return b"", f"图片太大又压不动（{exc}），先自己压缩一下再发"


def load_many(paths: List[Path],
              budget: int = MAX_TOTAL_CHARS) -> Tuple[List[Dict[str, str]], List[str]]:
    """一次读多个文件（可以带文件夹），返回 (成功列表, 提示列表)。

    图片会作为 `kind="image"` 的条目返回（带 `data_url`），文字文件照旧。
    会按 `budget` 控总量：读着读着超了就停下并说明「还有几个没读」。
    """
    files, notes = expand_paths(paths)
    loaded: List[Dict[str, str]] = []
    errors: List[str] = list(notes)
    used = 0
    images = 0
    for index, path in enumerate(files):
        if len(loaded) >= MAX_FILES:
            errors.append(f"一次最多带 {MAX_FILES} 个文件，后面 {len(files) - index} 个没加进来")
            break
        if is_image(path):
            if images >= MAX_IMAGES:
                errors.append(f"一次最多带 {MAX_IMAGES} 张图片，"
                              f"后面 {len(files) - index} 个没加进来")
                break
            data_url, error = image_data_url(path)
            if error:
                errors.append(f"{path.name}：{error}")
                continue
            images += 1
            loaded.append({
                "name": path.name,
                "path": str(path),
                "text": "",
                "kind": "image",
                "data_url": data_url,
                "chars": "0",
            })
            continue
        text, error = load_text(path)
        if error:
            errors.append(f"{path.name}：{error}")
            continue
        if used and used + len(text) > budget:
            errors.append(f"合计字数到上限了（{budget} 字），后面 "
                          f"{len(files) - index} 个文件没加进来")
            break
        used += len(text)
        loaded.append({
            "name": path.name,
            "path": str(path),
            "text": text,
            "kind": "text",
            "chars": str(len(text)),
        })
    return loaded, errors


def envelope(text: str, budget: int = MAX_TOTAL_CHARS) -> Tuple[str, bool]:
    """按总预算再截一刀（多轮追加附件时用）。"""
    text = text or ""
    if len(text) <= budget:
        return text, False
    return text[:budget] + "\n…（附件合计超出上限，后面的内容已截断）", True


def as_context(files: List[Dict[str, str]]) -> str:
    """拼成给模型看的上下文块（图片不进这里，走 `image_parts`）。"""
    blocks = []
    for item in files:
        if str(item.get("kind") or "") == "image":
            continue
        blocks.append(
            f"【附件：{item.get('name')}】\n```\n{item.get('text')}\n```")
    if not blocks:
        return ""
    text, _cut = envelope("\n\n".join(blocks))
    return text


def image_parts(files: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """图片附件 → OpenAI 兼容的多模态 content 片段。"""
    parts: List[Dict[str, Any]] = []
    for item in files or []:
        if str(item.get("kind") or "") != "image":
            continue
        data_url = str(item.get("data_url") or "")
        if not data_url.startswith("data:"):
            continue
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts


def count_images(files: List[Dict[str, str]]) -> int:
    return sum(1 for item in files or [] if str(item.get("kind") or "") == "image")
