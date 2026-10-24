"""验证可调配色的保存、即时换肤与界面重建。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lumina import theme  # noqa: E402
from lumina.main_window import MainWindow  # noqa: E402
from lumina.store import Store  # noqa: E402

SHOTS = ROOT / "shots"
no_screen = Qt.WidgetAttribute.WA_DontShowOnScreen


def capture(widget, name):
    SHOTS.mkdir(exist_ok=True)
    app = QApplication.instance()
    for _ in range(3):
        app.processEvents()
    pixmap = widget.grab()
    pixmap.setDevicePixelRatio(1.0)
    canvas = QPixmap(pixmap.size())
    canvas.fill(QColor("#E7EAE3"))
    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    canvas.save(str(SHOTS / name))
    print("saved", name, flush=True)


def main():
    store = Store(Path(tempfile.mkdtemp()) / "theme.db")
    app = QApplication(sys.argv)
    theme.load_from_store(store)
    theme.apply_theme(app)

    window = MainWindow(store)
    window.setAttribute(no_screen, True)
    window.show()
    app.processEvents()
    capture(window, "07-theme-before.png")
    print("before:", theme.appearance().accent, theme.appearance().bg,
          theme.appearance().alpha, flush=True)

    window.navigate(2)
    app.processEvents()
    page = window.settings_page
    page._choose_accent("#3B7BD4", "#D9A15C")
    page._choose_bg("mint")
    page.alpha_slider.setValue(94)
    page.save()
    for _ in range(4):
        app.processEvents()

    print("store:", store.get_setting("theme_accent"), store.get_setting("theme_bg"),
          store.get_setting("theme_alpha"), flush=True)
    print("after:", theme.appearance().accent, theme.appearance().bg,
          theme.appearance().alpha, flush=True)

    windows = [
        item for item in app.topLevelWidgets()
        if isinstance(item, MainWindow) and item.isVisible()
    ]
    print("visible main windows:", len(windows), flush=True)
    assert len(windows) == 1, "换肤后应只有一个主窗口"
    new_window = windows[0]
    assert new_window is not window
    capture(new_window, "08-theme-after.png")
    new_window.navigate(1)
    capture(new_window, "09-theme-outbox.png")
    new_window.close()
    print("theme test ok", flush=True)


if __name__ == "__main__":
    main()
