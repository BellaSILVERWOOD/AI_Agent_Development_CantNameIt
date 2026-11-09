"""DanLume（旦光）桌面应用入口。"""
from __future__ import annotations

import ctypes
import os
import sys
import traceback

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from lumina import theme
from lumina.config import APP_NAME, APP_TITLE, DATA_DIR, DB_PATH
from lumina.main_window import MainWindow, hard_exit
from lumina.store import Store


def _pid_alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _claim_single_instance():
    """同一份数据目录只允许一个实例（两个实例会抢 Chromium 配置锁，网页就打不开）。

    拿到锁就返回锁对象（要一直持有到进程结束），拿不到返回 None。
    上次被强杀会留下锁文件，这里会先确认那个进程还活着没有。
    """
    try:
        from PySide6.QtCore import QLockFile

        lock = QLockFile(str(DATA_DIR / "lumina.lock"))
        lock.setStaleLockTime(15000)
        if lock.tryLock(300):
            return lock
        info = lock.getLockInfo()
        pid = 0
        try:
            pid = int(info[0]) if info else 0
        except Exception:
            pid = 0
        if pid and not _pid_alive(pid):
            try:
                lock.removeStaleLockFile()
            except Exception:
                pass
            if lock.tryLock(300):
                return lock
    except Exception:
        return True
    return None


def _set_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "DanLume.Schedule.Hub.1"
        )
    except Exception:
        pass


def _configure_text_rendering() -> None:
    """Windows 默认用 DirectWrite + ClearType（亚像素）渲染文字，小字号下笔画会带彩边、
    看起来发虚发糙。改用 Qt 自带的 FreeType 引擎做灰度抗锯齿，笔画连续光滑，接近 macOS。
    必须在创建 QApplication 之前设置。
    """
    if sys.platform != "win32":
        return
    if os.environ.get("LUMINA_FONT_ENGINE", "freetype") != "freetype":
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "windows:fontengine=freetype")


def _tell_import_done(window) -> None:
    """上一次启动时刚把数据包换进来：说清楚「数据已恢复、密钥要重填」。

    只在那一次启动里弹一次；之后设置页的「数据迁移」卡片会一直显示最近一次的记录。
    """
    from PySide6.QtWidgets import QMessageBox

    report = {}
    try:
        from lumina import backup as backup_module

        report = backup_module.import_report(DATA_DIR) or {}
    except Exception:
        report = {}
    backup_path = report.get("backup") or ""
    lines = ["数据已从数据包恢复，导入前的旧数据也已经自动备份。", ""]
    if backup_path:
        lines.append("旧库备份：%s" % backup_path)
    if report.get("source"):
        lines.append("数据包：%s" % report.get("source"))
    lines += [
        "",
        "为了安全，密钥和登录都不进数据包，需要你重新填写：",
        "  · 邮件（SMTP）口令、收件箱（IMAP）口令",
        "  · AI 接口 Key（含多套 API 配置）",
        "  · 教务（UIS）口令",
        "  · 服务器账号：重新登录一次",
        "  · 管理员签名私钥（这台机器原来是管理员的话）",
    ]
    try:
        QMessageBox.information(window, APP_TITLE, "\n".join(lines))
    except Exception:
        pass


def main() -> int:
    argv = sys.argv[1:]
    # 「诊断入口」：不打开界面，直接把环境 / 数据目录 / 账户 / 设置 / 日志打印出来，
    # 带 --export 再打一个 zip 发给帮忙排查的人。见 lumina/diag.py。
    if "--diagnose" in argv or "--diag" in argv:
        from lumina import diag

        sys.stdout.reconfigure(encoding="utf-8", errors="replace") \
            if hasattr(sys.stdout, "reconfigure") else None
        return diag.cli(argv)
    debug = "--debug" in argv or "--debug-log" in argv
    if debug:
        # 先开日志和崩溃捕获：后面任何一步崩了都能留下 traceback
        try:
            from lumina import diag

            diag.enable_debug_logging()
        except Exception:
            traceback.print_exc()
    smoke = "--smoke-test" in argv
    if smoke:
        # 冒烟测试不去碰 Chromium 配置目录，免得和正在用的实例抢锁
        os.environ.setdefault("LUMINA_SMOKE", "1")

    # 「数据包导入」的收尾工作：上一次在设置页导入的数据包被解成 lumina.db.imported，
    # 必须在**打开 Store 之前**换进来（带着打开的手柄换不了名字）。见 lumina/backup.py。
    import_backup = None
    try:
        from lumina import backup as backup_module

        import_backup = backup_module.apply_pending_import(DATA_DIR)
    except Exception:
        if debug:
            traceback.print_exc()
        import_backup = None

    _set_app_user_model_id()
    _configure_text_rendering()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(True)
    # 滚轮不许改控件的值：设置里滚页面时，别把下拉/滑杆/勾选框滚出别的值来
    try:
        from lumina import wheel_guard

        wheel_guard.install(app)
    except Exception:
        pass

    store = Store(DB_PATH)
    # 服务器账号客户端：把数据层绑上去（令牌 / 服务器地址 / 公告游标都从这里读写）
    try:
        from lumina import account as account_module

        account_module.bind(store)
    except Exception:
        pass
    # 多套 API 配置：老的「单套」设置第一次运行会变成第 1 套，之后在面板/设置里随时切换
    try:
        from lumina import llm_profiles

        llm_profiles.migrate(store)
    except Exception:
        pass
    theme.migrate_to_apple(store)
    theme.load_bundled_fonts()
    theme.load_from_store(store)
    theme.apply_theme(app)
    # 提示气泡自己画（Qt 默认那个会继承控件颜色，深色下变「黑底深字」）
    try:
        from lumina import tooltip

        tooltip.install(app)
    except Exception:
        pass
    try:
        from lumina import settings_dirs

        settings_dirs.apply_to_modules(store)
    except Exception:
        pass
    # 启动时把代理（如果有）装到 Qt 上，内嵌网页才会跟着走
    try:
        from lumina import vpn

        vpn.apply_qt_proxy(store)
    except Exception:
        pass
    # 提示词都放在 <data>/prompts.json 里，首次运行写一份默认的出来
    try:
        from lumina import prompts

        prompts.ensure()
    except Exception:
        pass
    app.setWindowIcon(theme.make_app_icon())

    instance_lock = None
    if not smoke and "--allow-multi" not in sys.argv:
        instance_lock = _claim_single_instance()
        if instance_lock is None:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                None, APP_TITLE,
                "DanLume 已经在运行了。\n\n同时开两个会抢浏览器配置，网页可能打不开，"
                "所以这里就不再启动第二个窗口了。")
            store.close()
            return 0

    # 登录：账户存在本机（密码只存 PBKDF2 哈希），本地比对；「记住这台设备」24/48 小时
    current_user = None
    if not smoke and "--no-login" not in sys.argv:
        from lumina import users

        try:
            current_user = users.auto_login(store)
        except Exception:
            current_user = None
        if current_user is None and users.login_required(store):
            from PySide6.QtWidgets import QDialog

            from lumina.login_window import LoginWindow

            login = LoginWindow(store)
            if login.exec() != QDialog.DialogCode.Accepted or login.user is None:
                store.close()
                return 0
            current_user = login.user

    # 让各处都能知道「现在登录的是谁」（管理员不受接口授权 / 使用限制）。
    # 必须在建主窗口之前登记：设置页一构造就会用它决定要不要显示「申请授权」。
    try:
        from lumina import users as users_module

        users_module.set_current_user_provider(lambda: current_user)
    except Exception:
        pass

    window = MainWindow(store)
    # 有系统托盘时：窗口收进托盘不算「关掉了」，程序要继续在后台跑
    try:
        if window.tray_available():
            app.setQuitOnLastWindowClosed(False)
    except Exception:
        pass
    window.current_user = current_user
    window._instance_lock = instance_lock
    # 管理员第一次登录时把密钥准备好（公钥会写到程序根目录，随安装包分发）
    try:
        from lumina import adminsign, users as users_module

        if users_module.is_admin(current_user) and not adminsign.has_private_key(store):
            adminsign.generate_keypair(store)
    except Exception:
        pass
    if smoke:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    # 启动时刚换过库：提示「数据已恢复，密钥 / 登录要重填」（冒烟测试不弹窗）
    if import_backup is not None and not smoke:
        QTimer.singleShot(600, lambda: _tell_import_done(window))
    if smoke:
        QTimer.singleShot(1200, app.quit)
    code = app.exec()
    # QtWebEngine 收尾偶尔会卡住，数据已经落盘，直接结束进程
    hard_exit(store, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
