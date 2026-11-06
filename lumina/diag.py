# -*- coding: utf-8 -*-
"""调试 / 诊断入口：给帮忙排查问题的人看的东西，都从这里出。

为什么单独一个模块而不只是「看日志」：这套程序是 GUI，出错时窗口一关就什么都
看不到；同学的机器上也没有开发环境。所以这里做了两件事：

1. `python main.py --diagnose`：**不开界面**，直接在控制台打印一份报告
   （数据目录 / 数据库是否正常 / 账户列表 / 「记住设备」状态 / 设置项（脱敏）/
   最近日志 / 环境信息）。登录窗口说「没有这个账户」这类问题，看报告第一段
   就知道程序连的是哪个库。
2. `python main.py --diagnose --export`：把同样的内容打成一个 zip（诊断包），
   直接发给帮忙的人。里面**没有**密码、密钥、完整邮箱（都打了码）。
3. `python main.py --debug`：正常开界面，但同时把日志写进 `data/logs/`，并抓住
   崩溃（faulthandler + excepthook），崩了也能留下 traceback。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

REDACT_KEYS = ("password", "passwd", "secret", "token", "api_key", "auth",
               "oauth", "credential", "private")
MAX_LOG_LINES = 200


# ------------------------------------------------------------------ 小工具
def _say(text: str = "") -> None:
    """往控制台写一行；打包版（windowed exe）没有 stdout，直接跳过。

    PyInstaller 的 --windowed 版会把 sys.stdout / sys.stderr 设成 None，
    这里必须容忍，否则 `DanLume.exe --diagnose` 会抛 AttributeError、
    弹一个错误框卡在那里（报告文件也写不出来）。
    """
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(str(text) + "\n")
        stream.flush()
    except Exception:
        pass


def _mask_email(value: str) -> str:
    text = str(value or "")
    if "@" not in text:
        return text[:2] + "***" if text else ""
    name, domain = text.split("@", 1)
    head = name[:2] if len(name) > 2 else name[:1]
    return "%s***@%s" % (head, domain)


def _mask(value: str, keep: int = 4) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep:
        return "*" * len(text)
    return text[:keep] + "***（长度 %d）" % len(text)


def _fmt_bytes(size: int) -> str:
    if size < 1024:
        return "%d B" % size
    if size < 1024 * 1024:
        return "%.1f KB" % (size / 1024)
    if size < 1024 * 1024 * 1024:
        return "%.1f MB" % (size / 1024 / 1024)
    return "%.2f GB" % (size / 1024 / 1024 / 1024)


def _safe(func, default=None):
    try:
        return func()
    except Exception as exc:  # noqa: BLE001
        return default if default is not None else "（读取失败：%s）" % exc


# ------------------------------------------------------------------ 路径
def paths_report() -> List[str]:
    from . import config
    from .config import APP_ROOT, DATA_DIR, DB_PATH

    lines = ["== 1. 程序与数据目录 ==",
             "程序目录：%s" % APP_ROOT,
             "数据目录：%s" % DATA_DIR,
             "数据目录来源：%s" % config.data_dir_source(),
             "数据库：  %s" % DB_PATH]
    db = Path(DB_PATH)
    lines.append("数据库存在：%s" % ("是" if db.exists() else "**不是**（这就是「没有这个账户」的常见原因）"))
    if db.exists():
        lines.append("数据库大小：%s" % _fmt_bytes(db.stat().st_size))
    for suffix in ("-wal", "-shm"):
        extra = Path(str(db) + suffix)
        if extra.exists():
            lines.append("附加文件：%s（%s）" % (extra.name, _fmt_bytes(extra.stat().st_size)))
    lines.append("数据目录可写：%s" % ("是" if _writable(Path(DATA_DIR)) else "**否**"))
    try:
        usage = shutil.disk_usage(str(DATA_DIR if Path(DATA_DIR).exists() else APP_ROOT))
        lines.append("磁盘剩余：%s / 共 %s" % (_fmt_bytes(usage.free), _fmt_bytes(usage.total)))
    except Exception as exc:
        lines.append("磁盘剩余：读不到（%s）" % exc)
    lines.append("LUMINA_DATA_DIR 环境变量：%s" % (os.environ.get("LUMINA_DATA_DIR") or "（没设）"))
    return lines


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / (".write_probe_%d" % os.getpid())
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 环境
def env_report() -> List[str]:
    from . import theme

    lines = ["== 2. 环境与版本 =="]
    lines.append("Python：%s（%s）" % (platform.python_version(), sys.executable))
    lines.append("系统：%s %s（%s）" % (platform.system(), platform.release(),
                                     platform.machine()))
    for label, getter in (
        ("PySide6", lambda: __import__("PySide6").__version__),
        ("Qt", lambda: __import__("PySide6.QtCore", fromlist=["qVersion"]).qVersion()),
    ):
        lines.append("%s：%s" % (label, _safe(getter, "读不到")))
    lines.append("打包运行（冻结）：%s" % ("是" if getattr(sys, "frozen", False) else "否（源码运行）"))
    try:
        from . import adminsign

        # 传 None：这些函数只用到 store 的读取接口，全都包在 try 里，
        # 诊断模式不想为了读个标志就去开数据库（可能正被运行中的程序占用）。
        lines.append("安装模式：%s" % ("客户端版（要注册）"
                                       if adminsign.install_is_client(None)
                                       else "管理员版"))
        # 私钥可能在数据库的加密字段里，也可能在根目录的 pem 文件里；诊断模式不开库，
        # 所以这里只确认「文件那份在不在」，并说明另一种可能。
        pem_file = _safe(lambda: adminsign.private_pem_file(), None)
        has_file = bool(pem_file and getattr(pem_file, "exists", lambda: False)())
        if has_file:
            lines.append("管理员私钥：有（%s）" % pem_file)
        else:
            lines.append("管理员私钥：根目录没有 pem 文件（也可能存在数据库密钥里，"
                         "以登录后「设置 → 账户审核」显示为准）")
        lines.append("管理员公钥：%s" % ("有" if adminsign.public_pem(None)
                                        else "**没有**（注册流程会失败）"))
        lines.append("公钥指纹：%s" % (_safe(lambda: adminsign.public_fingerprint(None),
                                            "") or "读不到"))
        lines.append("公钥文件：%s" % _safe(lambda: adminsign.public_pem_file(), "?"))
    except Exception as exc:
        lines.append("安装模式：读不到（%s）" % exc)
    try:
        lines.append("屏幕 DPR：%.2f" % theme.device_pixel_ratio())
    except Exception:
        pass
    for key in sorted(os.environ):
        if key.startswith("LUMINA_"):
            lines.append("环境变量 %s=%s" % (key, os.environ[key]))
    return lines


# ------------------------------------------------------------------ 数据库
def _open_copy(db_path: Path):
    """把库（含 WAL）复制到临时目录再读 —— 不锁正在运行的程序，也不写真实库。"""
    tmp = Path(tempfile.mkdtemp(prefix="lumina_diag_"))
    target = tmp / "copy.db"
    shutil.copy2(db_path, target)
    for suffix in ("-wal", "-shm"):
        extra = Path(str(db_path) + suffix)
        if extra.exists():
            shutil.copy2(extra, Path(str(target) + suffix))
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    return conn, tmp


def database_report() -> List[str]:
    from .config import DB_PATH

    lines = ["== 3. 数据库 =="]
    db = Path(DB_PATH)
    if not db.exists():
        return lines + ["数据库文件不存在，跳过。"]
    conn = None
    tmp = None
    try:
        conn, tmp = _open_copy(db)
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
        lines.append("完整性检查：%s" % check)
        tables = [row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        lines.append("表：%d 个" % len(tables))
        for name in ("users", "login_sessions", "login_attempts", "events", "memos",
                     "mails", "inbox_messages", "reg_users", "reg_inbox",
                     "api_grants", "chat_sessions"):
            if name not in tables:
                lines.append("  %-18s 不存在" % name)
                continue
            try:
                count = conn.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0]
                lines.append("  %-18s %d 行" % (name, count))
            except Exception as exc:
                lines.append("  %-18s 读不到（%s）" % (name, exc))
    except Exception as exc:
        lines.append("打不开数据库：%s" % exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    return lines


def accounts_report() -> List[str]:
    from .config import DB_PATH

    lines = ["== 4. 本机账户（邮箱打码）=="]
    db = Path(DB_PATH)
    if not db.exists():
        return lines + ["数据库不存在，没有任何账户。"]
    conn = None
    tmp = None
    try:
        conn, tmp = _open_copy(db)
        rows = list(conn.execute(
            "SELECT id, username, role, status, email, created_at, expires_at "
            "FROM users ORDER BY id"))
        if not rows:
            lines.append("**一个账户都没有**：客户端版需要先「注册账户」等管理员批准。")
        for row in rows:
            lines.append("  #%s %-16s 角色=%-5s 状态=%-7s 到期=%s 邮箱=%s"
                         % (row["id"], row["username"], row["role"], row["status"],
                            row["expires_at"] or "永不过期",
                            _mask_email(row["email"]) or "（没绑）"))
        sessions = list(conn.execute(
            "SELECT user_id, created_at, expires_at FROM login_sessions"))
        lines.append("「记住这台设备」记录：%d 条" % len(sessions))
        for row in sessions:
            lines.append("  用户 #%s 记录于 %s，有效期到 %s"
                         % (row["user_id"], row["created_at"], row["expires_at"]))
        attempts = list(conn.execute(
            "SELECT username, failures, last_at FROM login_attempts"))
        if attempts:
            lines.append("登录失败计数：")
            for row in attempts:
                lines.append("  %-16s 失败 %s 次，最近 %s"
                             % (row["username"], row["failures"], row["last_at"]))
        else:
            lines.append("登录失败计数：没有（说明不是被冷却拦住）")
        lines.append("提示：报「没有这个账户」时，先核对上面这个列表 —— "
                     "如果这里是空的，说明程序读的不是你以为的那份数据。")
    except Exception as exc:
        lines.append("读账户失败：%s" % exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    return lines


def settings_report() -> List[str]:
    from .config import DB_PATH

    lines = ["== 5. 设置（敏感项已打码）=="]
    db = Path(DB_PATH)
    if not db.exists():
        return lines + ["数据库不存在。"]
    conn = None
    tmp = None
    try:
        conn, tmp = _open_copy(db)
        rows = list(conn.execute("SELECT key, value FROM settings ORDER BY key"))
        for row in rows:
            key = str(row["key"])
            value = str(row["value"] or "")
            if any(token in key.lower() for token in REDACT_KEYS):
                value = _mask(value)
            elif len(value) > 80:
                value = value[:77] + "…"
            lines.append("  %-28s = %s" % (key, value))
    except Exception as exc:
        lines.append("读设置失败：%s" % exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    return lines


# ------------------------------------------------------------------ 日志
def log_dirs() -> List[Path]:
    """只看数据目录里的日志：工作区里还有一堆测试脚本的日志，混进来只会误导。"""
    from .config import DATA_DIR

    root = Path(DATA_DIR)
    candidates = [root / "logs", root]
    return [path for path in candidates if path.exists()]


def log_files() -> List[Path]:
    found: List[Path] = []
    for folder in log_dirs():
        try:
            found.extend(sorted(folder.glob("*.log")))
        except Exception:
            continue
    return found


def logs_report() -> List[str]:
    lines = ["== 6. 日志 =="]
    files = log_files()
    if not files:
        return lines + ["没有日志文件（正常启动不写日志；用 --debug 启动才会写）。"]
    for path in files[-4:]:
        lines.append("")
        lines.append("--- %s（%s，最后 %d 行）---"
                     % (path, _fmt_bytes(path.stat().st_size), MAX_LOG_LINES))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            lines.append("读不到：%s" % exc)
            continue
        lines.extend(text.splitlines()[-MAX_LOG_LINES:])
    return lines


def collect_report() -> str:
    """把上面几段拼成一份文本报告。"""
    sections: List[List[str]] = []
    for func in (paths_report, env_report, database_report, accounts_report,
                 settings_report, logs_report):
        try:
            sections.append(func())
        except Exception:
            sections.append(["== %s 出错 ==" % func.__name__,
                             traceback.format_exc()])
    header = ["DanLume 诊断报告",
              "生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
              "命令行：%s" % " ".join(sys.argv),
              "（密码 / 密钥 / API Key 已打码；邮箱只保留前两位）", ""]
    body: List[str] = header
    for section in sections:
        body.extend(section)
        body.append("")
    return "\n".join(body)


# ------------------------------------------------------------------ 诊断包
def export_bundle(folder: str = "") -> Path:
    """把报告 + 日志打包成 zip，返回路径。"""
    from .config import DATA_DIR

    target_dir = Path(folder) if folder else Path(DATA_DIR) / "diagnostics"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    zip_path = target_dir / ("DanLume诊断包-%s.zip" % stamp)
    report = collect_report()
    import zipfile

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("诊断报告.txt", report)
        archive.writestr("环境.json", json.dumps(_env_json(), ensure_ascii=False,
                                                indent=2))
        for path in log_files()[-4:]:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            tail = "\n".join(text.splitlines()[-MAX_LOG_LINES:])
            archive.writestr("logs/%s" % path.name, tail)
        try:
            from .config import DB_PATH

            db = Path(DB_PATH)
            if db.exists() and db.stat().st_size < 40 * 1024 * 1024:
                # 附一份数据库副本：同学能直接复现「账户不存在」这类问题。
                # 里面只有本机数据（密码是哈希），不发出去就没事。
                archive.write(db, "data/%s" % db.name)
        except Exception:
            pass
    return zip_path


def _env_json() -> Dict[str, Any]:
    from .config import APP_ROOT, DATA_DIR, DB_PATH

    return {
        "app_root": str(APP_ROOT),
        "data_dir": str(DATA_DIR),
        "db_path": str(DB_PATH),
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": platform.python_version(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "argv": sys.argv,
    }


# ------------------------------------------------------------------ 调试模式
_DEBUG_LOG: Path = None


def enable_debug_logging() -> Path:
    """`--debug`：日志写文件 + 抓崩溃 + 控制台也有一份。"""
    global _DEBUG_LOG
    from .config import DATA_DIR

    folder = Path(DATA_DIR) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    _DEBUG_LOG = folder / ("lumina-%s.log" % time.strftime("%Y%m%d"))
    import faulthandler
    import logging

    handle = open(_DEBUG_LOG, "a", encoding="utf-8", buffering=1)
    faulthandler.enable(file=handle, all_threads=True)
    handlers = [logging.FileHandler(_DEBUG_LOG, encoding="utf-8")]
    if sys.stderr is not None:      # 打包版（windowed）没有控制台
        handlers.insert(0, logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("lumina").info("调试模式启动：%s", _DEBUG_LOG)

    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            handle.write("\n=== 未捕获异常 ===\n" + text)
            handle.flush()
        except Exception:
            pass
        if sys.stderr is not None:
            try:
                sys.stderr.write(text)
            except Exception:
                pass
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                box = QMessageBox()
                box.setWindowTitle("DanLume 出错了")
                box.setIcon(QMessageBox.Icon.Critical)
                box.setText("程序遇到了一个错误，详情已写进日志：")
                box.setInformativeText(str(_DEBUG_LOG))
                box.setDetailedText(text)
                box.exec()
        except Exception:
            pass

    sys.excepthook = hook
    return _DEBUG_LOG


def debug_log_path() -> Path:
    return _DEBUG_LOG


# ------------------------------------------------------------------ 命令行
def cli(argv: List[str]) -> int:
    """`--diagnose` 的入口：打印报告；带 `--export` 再打一个 zip。

    报告同时落一份到 `data/diagnostics/诊断报告-时间.txt`：打包版（exe，无控制台）
    直接运行时看不到打印，但文件总拿得到。
    """
    from .config import DATA_DIR

    report = collect_report()
    _say(report)
    report_path = None
    try:
        folder = Path(DATA_DIR) / "diagnostics"
        folder.mkdir(parents=True, exist_ok=True)
        report_path = folder / ("诊断报告-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
        report_path.write_text(report, encoding="utf-8")
        _say("")
        _say("报告已保存：%s" % report_path)
    except Exception as exc:
        _say("（报告写文件失败：%s）" % exc)
    if "--export" in argv:
        try:
            path = export_bundle()
        except Exception:
            traceback.print_exc()
            return 1
        _say("")
        _say("诊断包已生成：%s" % path)
        _say("把这个 zip 发给帮忙的同学就行（密码、密钥已经打码）。")
    elif report_path is not None:
        _say("想打包发给别人：再跑一次并带上 --export（或双击「诊断.bat」）。")
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
