"""Lumina 桌面应用入口。"""
from __future__ import annotations

import ctypes
import os
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from lumina import theme
from lumina.config import APP_TITLE, DATA_DIR, DB_PATH
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
            "Lumina.Schedule.Hub.1"
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


def main() -> int:
    smoke = "--smoke-test" in sys.argv
    if smoke:
        # 冒烟测试不去碰 Chromium 配置目录，免得和正在用的实例抢锁
        os.environ.setdefault("LUMINA_SMOKE", "1")
    _set_app_user_model_id()
    _configure_text_rendering()
    app = QApplication(sys.argv)
    app.setApplicationName("Lumina")
    app.setApplicationDisplayName(APP_TITLE)
    app.setOrganizationName("Lumina")
    app.setQuitOnLastWindowClosed(True)

    store = Store(DB_PATH)
    theme.migrate_to_apple(store)
    theme.load_bundled_fonts()
    theme.load_from_store(store)
    theme.apply_theme(app)
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
                "Lumina 已经在运行了。\n\n同时开两个会抢浏览器配置，网页可能打不开，"
                "所以这里就不再启动第二个窗口了。")
            store.close()
            return 0

    window = MainWindow(store)
    window._instance_lock = instance_lock
    if smoke:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    if smoke:
        QTimer.singleShot(1200, app.quit)
    code = app.exec()
    # QtWebEngine 收尾偶尔会卡住，数据已经落盘，直接结束进程
    hard_exit(store, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
