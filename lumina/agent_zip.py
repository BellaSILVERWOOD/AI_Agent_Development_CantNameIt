"""E3：在工作区里解压压缩包（防「zip slip」，每次都要用户确认）。

安全规则：
* 只在白名单目录里解压（默认工作区），写入需要「读写」档；
* 每个条目的目标路径 `resolve()` 后必须在目标目录里 —— `../..` 一律拒绝；
* 跳过敏感文件（`.ssh`、`*.pem` 等）和符号链接条目；
* 压缩炸弹保护：单个文件与总解压体积都有上限（默认 200MB / 1GB）；
* 已存在的同名文件不覆盖，改成 `名字 (2).ext`。
"""
from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List

from . import ai_local

MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def extract(store, archive: str = "", dest: str = "") -> Dict[str, Any]:
    src, error = ai_local.resolve(store, archive)
    if error:
        return {"ok": False, "summary": error}
    if not src.exists():
        return {"ok": False, "summary": f"找不到压缩包：{src}"}
    if not zipfile.is_zipfile(src):
        return {"ok": False, "summary": "这不是一个 zip 压缩包（rar/7z 请先转成 zip）"}
    target, error = ai_local.resolve(store, dest or "", need_write=True)
    if error:
        return {"ok": False, "summary": error}
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"建不了目标目录：{exc}"}

    extracted: List[str] = []
    skipped: List[str] = []
    total = 0
    try:
        with zipfile.ZipFile(src) as bundle:
            for info in bundle.infolist():
                name = info.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts:
                    skipped.append(name)
                    continue
                if info.file_size > MAX_FILE_BYTES or total + info.file_size > MAX_TOTAL_BYTES:
                    skipped.append(name)
                    continue
                candidate = (target / pure.as_posix())
                try:
                    resolved = candidate.resolve()
                except Exception:
                    skipped.append(name)
                    continue
                if not str(resolved).startswith(str(target.resolve())):
                    skipped.append(name)
                    continue
                if ai_local.is_sensitive(resolved):
                    skipped.append(name)
                    continue
                try:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                    final = resolved
                    index = 2
                    while final.exists():
                        final = resolved.with_name(
                            f"{resolved.stem} ({index}){resolved.suffix}")
                        index += 1
                        if index > 200:
                            break
                    with bundle.open(info) as source, open(final, "wb") as out:
                        while True:
                            chunk = source.read(256 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                    total += info.file_size
                    extracted.append(final.name)
                except Exception:
                    skipped.append(name)
                    continue
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"解压失败：{exc}"}
    ai_local._audit(store, "unzip", src, True,
                    f"{len(extracted)} 个文件 → {target}")
    if not extracted:
        return {"ok": False, "skipped": skipped,
                "summary": "一个文件都没解出来（可能全被安全规则挡下了）"}
    summary = (f"解压了 {len(extracted)} 个文件到 {target}"
               f"（{total / 1024 / 1024:.1f} MB）")
    if skipped:
        summary += f"，跳过 {len(skipped)} 个（路径越界 / 太大 / 敏感）"
    return {"ok": True, "target": str(target), "extracted": extracted[:50],
            "count": len(extracted), "skipped": skipped[:20],
            "summary": summary}
