"""数据包（.dlpk）导出 / 导入：换电脑、重装系统、Python 版与 Rust 版之间搬数据。

统一格式（Rust 版 DanLume-v2 与本 Python 版**完全一致**）::

    manifest.json      # 元信息：格式、版本、各表行数、被剥离的密钥 key
    lumina.db          # SQLite 一致性快照
    README.txt         # 一页中文说明

几条硬规矩：

* 导出用 ``sqlite3.Connection.backup()`` 生成一致性快照（WAL 里已经提交、还没落盘的
  内容也算进去），不会拷出一个「写了一半」的库；
* 导出前把 ``settings`` 表里所有**值以 ``dpapi:`` 开头**的行删掉，key 记进
  ``manifest.stripped_keys``（``secrets_stripped`` = 这次是否真删掉了什么东西，
  和 Rust 版 backup.rs 的写法保持一致）。DPAPI 密文绑定当前 Windows 用户，
  换台电脑也解不开，留着只会让用户以为还能用；
* 导入**绝不覆盖正在用的库**：先自动备份当前库，再把包里的库解成
  ``<data>/lumina.db.imported``，在当前库里写 ``pending_import=1`` 标记，然后等下一次
  启动时由 :func:`apply_pending_import` 在**打开 Store 之前**完成替换。中途断电最多
  丢掉这一次导入，正在用的库还在。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import APP_VERSION

# ---------------------------------------------------------------- 包格式常量
FORMAT = "danlume-backup"
FORMAT_VERSION = 1
SCHEMA_VERSION = 1            # 数据库结构版本：现在只有 1
APP_TAG = "python"            # 导出方是哪个版本（Rust 版写 "rust"）
PACKAGE_SUFFIX = ".dlpk"

MANIFEST_NAME = "manifest.json"
DB_NAME = "lumina.db"
README_NAME = "README.txt"
# 旧版（本 Python 版）把 AI 记忆存在 data/memory.md 纯文本里；Rust 版存在 memories 表。
# 包里带上一份 memory.md，换版本时记忆就不会「看不到」。
MEMORY_NAME = "memory.md"
MEMORY_PENDING_NAME = "memory.md.imported"     # 解出来、等重启换上的记忆文件

# ---------------------------------------------------------------- 本机路径 / 标记
LIVE_DB_NAME = "lumina.db"
PENDING_DB_NAME = "lumina.db.imported"        # 解包出来、等重启换进去的库
BACKUP_DIR_NAME = "backups"
REPORT_NAME = "import-report.json"            # 最近一次换库的记录（设置页显示用）
PENDING_KEY = "pending_import"                # "1" = 有导入等着重启生效
PENDING_FROM_KEY = "pending_import_from"      # 包路径

DPAPI_PREFIX = "dpapi:"
B64_PREFIX = "b64:"            # 非 Windows 的退化混淆（见 secure.py），不算 DPAPI


# ====================================================================== 小工具
def _now_iso() -> str:
    """UTC 时间，形如 2026-11-09T13:40:00Z（manifest 里用）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    """本地时间，形如 20261109-134000（文件名里用）。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _fail(message: str, **extra: Any) -> Dict[str, Any]:
    """统一的失败返回值：ok=False + 一句能直接给用户看的中文。"""
    result: Dict[str, Any] = {"ok": False, "error": message}
    result.update(extra)
    return result


def _write_text(path: Path, text: str) -> None:
    """按 UTF-8 + LF 写文本（不用 Path.write_text：3.9 还没有 newline 参数）。"""
    with open(str(path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _remove_quietly(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _write_bytes(path: Path, data: bytes) -> None:
    with open(str(path), "wb") as handle:
        handle.write(data)


def memory_entries() -> List[str]:
    """读记忆文件里的「- 」条目（读不到 / 没装记忆模块时返回空表）。"""
    try:
        from . import memory
        return memory.entries()
    except Exception:                                # noqa: BLE001
        return []


def memory_export_text(entries: List[str]) -> str:
    """把记忆条目拼成 memory.md（与 memory.py 的 _HEADER 一致，Rust 版认这个格式）。"""
    header = ("# DanLume 记忆\n\n"
              "> 这里的内容会随每次 AI 对话带给模型（有长度上限）。\n"
              "> 想让 AI 做什么/记住什么，直接跟它说「记住：……」即可；也可以手动编辑这个文件。\n")
    lines = ["- %s\n" % str(item).strip() for item in entries if str(item).strip()]
    return header + "".join(lines)


def _sqlite_backup(source: sqlite3.Connection, dest_path: Path) -> None:
    """把 source 的一致性快照写到 dest_path（目标库用默认回滚日志，不带 -wal）。"""
    _remove_quietly(dest_path)
    target = sqlite3.connect(str(dest_path))
    try:
        source.backup(target)
    finally:
        target.close()


def integrity_ok(conn: sqlite3.Connection) -> bool:
    """``PRAGMA integrity_check`` 必须回 ok。"""
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError:
        return False
    return bool(row) and str(row[0]).strip().lower() == "ok"


def table_names(conn: sqlite3.Connection) -> List[str]:
    """库里所有用户表（排除 sqlite_ 内部表），按名字排序。"""
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    return [str(row[0]) for row in rows]


def count_tables(conn: sqlite3.Connection) -> Dict[str, int]:
    """各表行数，例如 ``{"events": 12, "settings": 30}``。"""
    counts: Dict[str, int] = {}
    for name in table_names(conn):
        try:
            row = conn.execute(
                'SELECT COUNT(*) FROM "%s"' % name.replace('"', '""')
            ).fetchone()
        except sqlite3.DatabaseError:
            continue
        counts[name] = int(row[0]) if row else 0
    return counts


def stripped_secret_keys(conn: sqlite3.Connection) -> List[str]:
    """``settings`` 里值以 ``dpapi:`` 开头的 key（只看，不删）。"""
    try:
        rows = conn.execute(
            "SELECT key FROM settings WHERE value LIKE ?", (DPAPI_PREFIX + "%",)
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    return sorted({str(row[0]) for row in rows})


def strip_secret_rows(conn: sqlite3.Connection) -> List[str]:
    """删掉 ``settings`` 里所有 ``dpapi:`` 开头的行，返回被删掉的 key。"""
    keys = stripped_secret_keys(conn)
    if keys:
        try:
            conn.execute("DELETE FROM settings WHERE value LIKE ?", (DPAPI_PREFIX + "%",))
            conn.commit()
        except sqlite3.DatabaseError:
            return []
    return keys


def _b64_secret_count(conn: sqlite3.Connection) -> int:
    """非 Windows 上 secure.protect() 会退化成 ``b64:``，这些是「能解开的口令」。"""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM settings WHERE value LIKE ?", (B64_PREFIX + "%",)
        ).fetchone()
    except sqlite3.DatabaseError:
        return 0
    return int(row[0]) if row else 0


# ====================================================================== 导出
def export_package(store, out_path: Path) -> Dict[str, Any]:
    """导出一个 .dlpk 数据包。

    :param store: ``lumina.store.Store``（只用它的 ``conn`` / ``path``）
    :param out_path: 目标文件；后缀不是 ``.dlpk`` 会自动补上
    :return: ``{ok, path, size, tables, stripped_keys, warnings}``；
             失败时 ``{ok: False, error}``（不抛异常，界面直接显示 error）
    """
    warnings: List[str] = []
    target = Path(str(out_path)).expanduser()
    if target.suffix.lower() != PACKAGE_SUFFIX:
        target = target.with_name(target.name + PACKAGE_SUFFIX)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fail("写不了这个位置：%s" % exc)

    workdir = Path(tempfile.mkdtemp(prefix="danlume-export-"))
    try:
        # 1) 一致性快照
        snapshot = workdir / DB_NAME
        try:
            _sqlite_backup(store.conn, snapshot)
        except Exception as exc:                     # noqa: BLE001
            return _fail("生成数据库快照失败：%s" % exc)

        # 2) 在快照上删密钥、数行数（绝不动正在用的库）
        stripped: List[str] = []
        tables: Dict[str, int] = {}
        b64_left = 0
        try:
            conn = sqlite3.connect(str(snapshot))
            try:
                if not integrity_ok(conn):
                    return _fail(
                        "数据库快照没通过完整性检查（PRAGMA integrity_check），"
                        "先别导出：可能需要先修复数据库")
                stripped = strip_secret_rows(conn)
                tables = count_tables(conn)
                b64_left = _b64_secret_count(conn)
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            return _fail("读数据库快照失败：%s" % exc)

        if not tables:
            warnings.append("数据库里一张表都没有，导出的是个空包")
        elif "events" not in tables:
            warnings.append("快照里没有 events 表，这个包可能来自别的版本")
        if b64_left:
            warnings.append(
                "有 %d 项口令是 b64: 混淆（不是 Windows DPAPI），会随包一起导出，"
                "请注意别把这个包发给别人" % b64_left)
        if not stripped:
            warnings.append("本机没有保存 DPAPI 加密的密钥，stripped_keys 是空的")

        # 3) manifest + README（+ 记忆文件）
        remembered = memory_entries()
        manifest: Dict[str, Any] = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app": APP_TAG,
            "app_version": APP_VERSION,
            "exported_at": _now_iso(),
            "schema_version": SCHEMA_VERSION,
            "tables": tables,
            "secrets_stripped": bool(stripped),
            "stripped_keys": stripped,
            "memory_included": bool(remembered),
            "memory_entries": len(remembered),
        }
        try:
            _write_text(workdir / MANIFEST_NAME,
                        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            _write_text(workdir / README_NAME, _readme_text(manifest, target.name))
            if remembered:
                _write_text(workdir / MEMORY_NAME, memory_export_text(remembered))
        except OSError as exc:
            return _fail("写临时文件失败：%s" % exc)

        # 4) 打 zip（压缩级别 6）
        members = [MANIFEST_NAME, DB_NAME, README_NAME]
        if remembered:
            members.append(MEMORY_NAME)
        try:
            with zipfile.ZipFile(str(target), "w", zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as archive:
                for name in members:
                    archive.write(str(workdir / name), arcname=name)
        except (OSError, zipfile.BadZipFile) as exc:
            return _fail("写 .dlpk 文件失败：%s" % exc)

        try:
            size = int(target.stat().st_size)
        except OSError:
            size = 0
        return {
            "ok": True,
            "path": str(target.resolve()),
            "size": size,
            "tables": tables,
            "stripped_keys": stripped,
            "warnings": warnings,
            "memory_entries": len(remembered),
            "app_version": APP_VERSION,
            "exported_at": manifest["exported_at"],
        }
    finally:
        shutil.rmtree(str(workdir), ignore_errors=True)


def _readme_text(manifest: Dict[str, Any], file_name: str) -> str:
    """包里那份一页中文说明。"""
    tables = manifest.get("tables") or {}
    stripped = manifest.get("stripped_keys") or []
    table_lines = "\n".join("     %-24s %d 行" % (name, count)
                            for name, count in tables.items()) or "     （空）"
    key_lines = "\n".join("     - %s" % key for key in stripped) or "     （没有需要剥离的密钥）"
    memory_count = int(manifest.get("memory_entries") or 0)
    memory_line = ("%d 条，导入时一起还原" % memory_count) if memory_count else "本次没有记忆条目"
    return """DanLume 数据包（.dlpk）说明
==============================

这是 DanLume 导出的数据包，文件名叫 {file_name}。
它本质上是一个 zip，里面有：

   manifest.json   元信息：格式、版本、各表行数、被剥离的密钥
   lumina.db       SQLite 数据库快照（一致性快照，不含 -wal / -shm）
   memory.md       AI 记忆（纯文本，{memory_line}）
   README.txt      本说明（就是你现在看的这份）

--------------------------------------------------------------------------
一、这个包是从哪儿来的
--------------------------------------------------------------------------
   导出时间（UTC）：{exported_at}
   导出程序：DanLume {app_version}（{app} 版）
   包格式版本：{format_version}
   数据库结构版本：{schema_version}

   各表行数：
{table_lines}

--------------------------------------------------------------------------
二、密钥不随包走（重要）
--------------------------------------------------------------------------
导出时已经删掉了 settings 表里所有用 Windows DPAPI 加密的密钥 —— DPAPI 密文
是绑在「这台电脑的这个 Windows 用户」上的，换台电脑本来就解不开，留着也没用。

本次删掉的密钥（{key_count} 项）：
{key_lines}

所以导入之后，下面这些都需要你**重新填写**：

   * 邮件（SMTP）发信口令；开启了 OAuth 的话要重新授权
   * 收件箱（IMAP）账户口令
   * AI 接口的 API Key（含多套 API 配置里的 Key）
   * 教务（UIS）口令
   * 服务器账号：需要重新登录（令牌被视作密钥，已清除）
   * 网络代理口令（如果填过）
   * 管理员签名私钥（如果这台机器原来是管理员：需要重新导入密钥）

日程、备忘、汇报邮件草稿、联系人、分组、重要日、提示词、外观设置等**都在**，
不需要重填。

--------------------------------------------------------------------------
三、怎么导入
--------------------------------------------------------------------------
Python 版：设置 → 其他 → 数据迁移 → 「从数据包导入…」
Rust 版（DanLume-v2）：设置里同一张「数据迁移」卡片，按钮一样。

导入时会发生什么：

   1. 先检查 zip 结构、manifest、数据库完整性；
   2. 自动把**当前**数据库备份到 data/backups/pre-import-时间.db；
   3. 把包里的库解成 data/lumina.db.imported，并写一个「等重启生效」的标记；
   4. 重启 DanLume，启动时把 lumina.db.imported 换成正式的 lumina.db。

也就是说：导入不会当场覆盖正在用的库；只要你没重启，原来的数据一直还在。
真出了问题，把 data/backups/pre-import-*.db 改回 data/lumina.db 就能回去。

--------------------------------------------------------------------------
四、注意
--------------------------------------------------------------------------
   * 只接受 format=danlume-backup、format_version=1、schema_version=1 的包；
     版本不一致会被拒绝（并给出中文提示），不会动你的数据。
   * 两个版本（Python / Rust）用的是同一种 SQLite 库，可以直接互相导入；
     但界面上的设置项名称不同，个别只在一端存在的设置可能显示不出来。
   * 不要在程序运行的时候手工替换 data/lumina.db（WAL 会打架）；
     要换就用上面的「导入」流程。
   * 这个包里有你的日程和邮件内容，请像对待私人文件一样保管。
""".format(
        file_name=file_name,
        exported_at=manifest.get("exported_at", ""),
        app_version=manifest.get("app_version", ""),
        app=manifest.get("app", ""),
        format_version=manifest.get("format_version", ""),
        schema_version=manifest.get("schema_version", ""),
        table_lines=table_lines,
        key_count=len(stripped),
        key_lines=key_lines,
        memory_line=memory_line,
    )


# ====================================================================== 导入
def _check_manifest(manifest: Dict[str, Any]) -> Optional[str]:
    """校验 manifest 的三个关键字段，通过返回 None，否则返回中文错误。"""
    fmt = str(manifest.get("format") or "")
    if fmt != FORMAT:
        return "这不是 DanLume 数据包（manifest 里 format=%s，应该是 %s）" % (
            fmt or "空", FORMAT)
    try:
        format_version = int(manifest.get("format_version"))
    except (TypeError, ValueError):
        format_version = -1
    if format_version != FORMAT_VERSION:
        return "数据包格式版本 %s 不受支持，本机只认 %d" % (
            manifest.get("format_version"), FORMAT_VERSION)
    try:
        schema_version = int(manifest.get("schema_version"))
    except (TypeError, ValueError):
        schema_version = -1
    if schema_version != SCHEMA_VERSION:
        return ("数据库结构版本不一致（数据包是 %s，本机是 %d）。"
                "请把两台电脑上的 DanLume 升到同一个版本再迁移。" % (
                    manifest.get("schema_version"), SCHEMA_VERSION))
    return None


def import_package(store, pkg_path: Path) -> Dict[str, Any]:
    """导入一个 .dlpk：校验 → 备份当前库 → 解成 ``lumina.db.imported`` + 写标记。

    :param store: ``lumina.store.Store``（当前正在用的库）
    :param pkg_path: .dlpk 文件路径
    :return: 成功 ``{ok: True, needs_restart: True, backup, tables, stripped_keys,
             warnings}``；失败 ``{ok: False, error}``，并且**现有库一个字节都不动**
    """
    pkg = Path(str(pkg_path)).expanduser()
    data_dir = Path(str(store.path)).resolve().parent
    warnings: List[str] = []
    if not pkg.is_file():
        return _fail("找不到这个数据包：%s" % pkg)

    try:
        archive = zipfile.ZipFile(str(pkg), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        return _fail("这不是能打开的 .dlpk 数据包（zip 读取失败：%s）" % exc)

    with archive:
        # 1) zip 结构
        names = set(archive.namelist())
        missing = [name for name in (MANIFEST_NAME, DB_NAME) if name not in names]
        if missing:
            return _fail("数据包结构不对：缺少 %s（.dlpk 里应该有 manifest.json 和 "
                         "lumina.db）" % "、".join(missing))
        # 2) manifest
        try:
            raw = archive.read(MANIFEST_NAME)
            manifest = json.loads(raw.decode("utf-8"))
        except (KeyError, ValueError, OSError, zipfile.BadZipFile) as exc:
            return _fail("manifest.json 读不出来：%s" % exc)
        if not isinstance(manifest, dict):
            return _fail("manifest.json 格式不对（不是一个 JSON 对象）")
        problem = _check_manifest(manifest)
        if problem:
            return _fail(problem)

        # 3) 解到临时文件（校验通过才改名成 .imported）
        backups_dir = data_dir / BACKUP_DIR_NAME
        try:
            backups_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _fail("建不了备份目录 %s：%s" % (backups_dir, exc))

        # 3.5) 记忆文件（可选）：Rust 版把 memories 表导成 memory.md 一起放进包里。
        #      先只读进内存，等库确实就位了再落盘（不留半成品文件）。
        memory_bytes = b""
        memory_entries_count = 0
        if MEMORY_NAME in names:
            try:
                memory_bytes = archive.read(MEMORY_NAME)
            except (KeyError, OSError, zipfile.BadZipFile):
                memory_bytes = b""
            if memory_bytes:
                memory_entries_count = sum(
                    1 for line in memory_bytes.decode("utf-8", "ignore").splitlines()
                    if line.strip().startswith(("- ", "* ")))

        incoming = data_dir / (PENDING_DB_NAME + ".part")
        _remove_quietly(incoming)
        try:
            with archive.open(DB_NAME) as source, open(str(incoming), "wb") as dest:
                shutil.copyfileobj(source, dest, 1024 * 256)
        except (KeyError, OSError, zipfile.BadZipFile) as exc:
            _remove_quietly(incoming)
            return _fail("把数据库从包里解出来失败：%s" % exc)
        try:
            incoming_size = int(incoming.stat().st_size)
        except OSError:
            incoming_size = 0
        if incoming_size < 512:
            _remove_quietly(incoming)
            return _fail("包里的 lumina.db 只有 %d 字节，不像是真的数据库" % incoming_size)

        # 4) 完整性检查
        try:
            conn = sqlite3.connect(str(incoming))
            try:
                if not integrity_ok(conn):
                    _remove_quietly(incoming)
                    return _fail("包里的数据库没通过完整性检查（PRAGMA integrity_check），"
                                 "你现在的数据没有被改动")
                tables = count_tables(conn)
                package_keys = stripped_secret_keys(conn)
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            _remove_quietly(incoming)
            return _fail("包里的数据库打不开：%s" % exc)
        if not tables:
            _remove_quietly(incoming)
            return _fail("包里的数据库一张表都没有，你现在的数据没有被改动")
        if "events" not in tables:
            warnings.append("包里的库没有 events 表：可能是别的版本导出的，"
                            "导入后日历里可能是空的")
        if package_keys:
            warnings.append("包里的库还有 %d 项 DPAPI 密钥（导出方没剥离干净），"
                            "换台电脑多半解不开" % len(package_keys))

        # 5) 备份当前库（备份不成功就不许继续）
        backup_path = backups_dir / ("pre-import-%s.db" % _stamp())
        try:
            _sqlite_backup(store.conn, backup_path)
        except Exception as exc:                     # noqa: BLE001
            _remove_quietly(incoming)
            return _fail("备份当前数据库失败（%s）。为了不丢数据，导入已取消" % exc)
        try:
            backup_size = int(backup_path.stat().st_size)
        except OSError:
            backup_size = 0
        if backup_size < 512:
            _remove_quietly(incoming)
            return _fail("备份文件没写成功（%s）。为了不丢数据，导入已取消" % backup_path)
        backup_ok = False
        try:
            check = sqlite3.connect(str(backup_path))
            try:
                backup_ok = integrity_ok(check)
            finally:
                check.close()
        except sqlite3.DatabaseError:
            backup_ok = False
        if not backup_ok:
            _remove_quietly(incoming)
            return _fail("备份文件没通过完整性检查（%s）。为了不丢数据，导入已取消"
                         % backup_path)

        # 6) 就位 + 写「等重启」标记
        imported = data_dir / PENDING_DB_NAME
        try:
            os.replace(str(incoming), str(imported))
        except OSError as exc:
            _remove_quietly(incoming)
            return _fail("把包里的库放到 %s 失败：%s" % (imported, exc))

        # 6.5) 记忆文件也放成「等重启」的样子：重启时和库一起换上
        if memory_bytes:
            try:
                _write_bytes(data_dir / MEMORY_PENDING_NAME, memory_bytes)
            except OSError:
                memory_entries_count = 0
        try:
            store.set_setting(PENDING_KEY, "1")
            store.set_setting(PENDING_FROM_KEY, str(pkg.resolve()))
        except Exception as exc:                     # noqa: BLE001
            _remove_quietly(imported)
            return _fail("数据库里写不了导入标记：%s" % exc)

        keys = manifest.get("stripped_keys") or []
        if not isinstance(keys, list):
            keys = []
        return {
            "ok": True,
            "needs_restart": True,
            "backup": str(backup_path.resolve()),
            "tables": tables,
            "stripped_keys": [str(key) for key in keys],
            "warnings": warnings,
            "memory_entries": memory_entries_count,
            "package": str(pkg.resolve()),
            "pending_path": str(imported.resolve()),
            "app": str(manifest.get("app") or ""),
            "app_version": str(manifest.get("app_version") or ""),
            "exported_at": str(manifest.get("exported_at") or ""),
        }


# ====================================================================== 启动时换库
def apply_pending_import(data_dir: Path) -> Optional[str]:
    """把 ``lumina.db.imported`` 换成正式的 ``lumina.db``。

    **必须在打开 Store 之前调用**（main.py 启动早期）：把正在用的库删掉再换名，
    带着打开的手柄是做不成的。

    :param data_dir: 数据目录（就是 config.DATA_DIR）
    :return: 被替换掉的旧库的备份路径（提示用）；什么都没做时返回 None
    """
    directory = Path(str(data_dir))
    imported = directory / PENDING_DB_NAME
    if not imported.exists():
        return None

    # 1) 先验一下包里的库：坏了就别换，留给人排查
    ok = False
    tables: Dict[str, int] = {}
    try:
        conn = sqlite3.connect(str(imported))
        try:
            ok = integrity_ok(conn)
            tables = count_tables(conn) if ok else {}
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        ok = False
    if not ok or not tables:
        broken = imported.with_name("lumina.db.imported.broken-%s" % _stamp())
        try:
            os.replace(str(imported), str(broken))
            broken_note = str(broken)
        except OSError:
            broken_note = str(imported)
        _write_report(directory, {
            "ok": False,
            "applied_at": _now_iso(),
            "error": "数据包里的库没通过完整性检查，导入已放弃（原数据未动）",
            "broken_file": broken_note,
        })
        return None

    live = directory / LIVE_DB_NAME
    backups_dir = directory / BACKUP_DIR_NAME
    try:
        backups_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        backups_dir = directory          # 实在建不了就放数据目录下

    # 2) 旧库连带 -wal / -shm 一起挪进 backups（拆散会丢已提交的内容）
    source_package = ""
    if live.exists():
        try:
            read = sqlite3.connect(str(live))
            try:
                row = read.execute(
                    "SELECT value FROM settings WHERE key = ?", (PENDING_FROM_KEY,)
                ).fetchone()
                source_package = str(row[0]) if row else ""
            finally:
                read.close()
        except sqlite3.DatabaseError:
            source_package = ""
    replaced = backups_dir / ("pre-apply-%s.db" % _stamp())
    moved = False
    if live.exists():
        for suffix in ("", "-wal", "-shm", "-journal"):
            part = live.with_name(live.name + suffix)
            if not part.exists():
                continue
            try:
                os.replace(str(part), str(replaced.with_name(replaced.name + suffix)))
                moved = True
            except OSError:
                pass
        if moved is False:
            replaced = None
    else:
        replaced = None

    # 3) 换库
    try:
        os.replace(str(imported), str(live))
    except OSError as exc:
        # 换不动就把旧库放回去，当作没发生
        if replaced is not None and replaced.exists():
            try:
                os.replace(str(replaced), str(live))
            except OSError:
                pass
        _write_report(directory, {
            "ok": False,
            "applied_at": _now_iso(),
            "error": "替换数据库失败：%s" % exc,
        })
        return None

    # 4) 清掉可能残留的 -wal / -shm / -journal（新库自己会重建）
    for suffix in ("-wal", "-shm", "-journal"):
        _remove_quietly(live.with_name(live.name + suffix))

    # 5) 标记用完就删：包里的库可能是另一个版本导出的（Rust 版会把标记也写进导入库，
    #    换库后由这里清掉），留着会让界面一直显示「等待重启」。
    #    顺手补一个 seeded=1：Store 第一次打开没有这个标记的库时会写入 5 条示例日程，
    #    导入回来的数据里不该突然多出演示数据（Rust 版的库里根本没有这个标记）。
    try:
        fresh = sqlite3.connect(str(live))
        try:
            fresh.execute("DELETE FROM settings WHERE key IN (?, ?)",
                          (PENDING_KEY, PENDING_FROM_KEY))
            fresh.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES ('seeded', '1')")
            fresh.commit()
            fresh.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            fresh.close()
    except sqlite3.DatabaseError:
        pass

    # 5.5) 记忆文件：包里的 memory.md 换上（旧的那份留成 memory.md.bak，不直接丢）
    memory_applied = False
    memory_pending = directory / MEMORY_PENDING_NAME
    if memory_pending.exists():
        try:
            live_memory = directory / MEMORY_NAME
            if live_memory.exists():
                try:
                    os.replace(str(live_memory),
                               str(live_memory.with_name(MEMORY_NAME + ".bak")))
                except OSError:
                    _remove_quietly(live_memory)
            os.replace(str(memory_pending), str(live_memory))
            memory_applied = True
        except OSError:
            memory_applied = False

    pre_import = _newest_pre_import_backup(backups_dir)
    old_backup = pre_import or (str(replaced) if replaced is not None else None)
    _write_report(directory, {
        "ok": True,
        "applied_at": _now_iso(),
        "backup": old_backup,
        "tables": tables,
        "source": source_package,
        "memory_applied": memory_applied,
    })
    return old_backup


def _newest_pre_import_backup(backups_dir: Path) -> Optional[str]:
    """backups/ 里最新的 pre-import-*.db（导入时做的自动备份）。"""
    try:
        found = sorted(backups_dir.glob("pre-import-*.db"),
                       key=lambda path: path.stat().st_mtime)
    except OSError:
        return None
    return str(found[-1].resolve()) if found else None


def _write_report(data_dir: Path, payload: Dict[str, Any]) -> None:
    """把这次换库的结果写进 <data>/import-report.json（设置页显示用）。"""
    path = Path(str(data_dir)) / REPORT_NAME
    try:
        _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        pass


def import_report(data_dir: Path) -> Optional[Dict[str, Any]]:
    """读最近一次换库的记录；没有 / 坏了返回 None。"""
    path = Path(str(data_dir)) / REPORT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def pending_import_path(data_dir: Path) -> Optional[Path]:
    """现在有没有等着重启生效的导入（有就返回 .imported 路径）。"""
    path = Path(str(data_dir)) / PENDING_DB_NAME
    return path if path.exists() else None


def format_size(size: int) -> str:
    """字节 → 给人看的大小（导出成功提示里用）。"""
    try:
        size = int(size)
    except (TypeError, ValueError):
        return "0 B"
    if size >= 1024 * 1024:
        return "%.2f MB" % (size / (1024 * 1024))
    if size >= 1024:
        return "%.0f KB" % (size / 1024)
    return "%d B" % size
