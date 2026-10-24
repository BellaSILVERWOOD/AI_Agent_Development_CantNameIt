"""检查实例属性是否覆盖了 Qt 的虚函数/方法名（可能导致原生崩溃）。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QDate, Qt, SignalInstance  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from lumina import theme  # noqa: E402
from lumina.dialogs import ComposeDialog, EventDialog  # noqa: E402
from lumina.main_window import MainWindow  # noqa: E402
from lumina.reminders import ReminderToast  # noqa: E402
from lumina.store import Store  # noqa: E402

app = QApplication(sys.argv)
theme.apply_theme(app)
store = Store(Path(tempfile.mkdtemp()) / "shadow.db")
window = MainWindow(store)
window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
window.show()
compose = ComposeDialog(store, window)
compose.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
event = EventDialog(store, window, date=QDate.currentDate())
event.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
toast = ReminderToast(store.events_on(QDate.currentDate().toString("yyyy-MM-dd"))[0])
toast.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)

roots = [window, compose, event, toast]
objects = []
for root in roots:
    objects.append(root)
    objects.extend(root.findChildren(QWidget))

seen = set()
problems = []
for obj in objects:
    cls = type(obj)
    for name, value in vars(obj).items():
        if name.startswith("__"):
            continue
        if isinstance(value, SignalInstance):
            continue
        if name not in seen and hasattr(cls, name):
            attribute = getattr(cls, name)
            if callable(attribute):
                problems.append((cls.__name__, name, type(value).__name__))
        seen.add(name)

print("checked objects:", len(objects))
for cls_name, name, kind in problems:
    print("SHADOW", cls_name, "self." + name, "->", kind)
if not problems:
    print("no dangerous attribute shadowing found")
