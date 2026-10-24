"""整理 README 使用的截图。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "shots"
OUT = ROOT / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

MAPPING = [
    ("01-calendar.png", "calendar.png", 1200),
    ("02b-outbox-report.png", "outbox.png", 1200),
    ("03-settings.png", "settings.png", 1200),
    ("04-compose.png", "compose.png", 1200),
    ("05-event.png", "event.png", 815),
    ("06-reminder.png", "reminder.png", None),
    ("08-theme-after.png", "theme-blue.png", 1200),
    ("10-date-popup.png", "date-popup.png", None),
]

for source, target, width in MAPPING:
    path = SHOTS / source
    if not path.exists():
        print("skip", source)
        continue
    image = Image.open(path).convert("RGB")
    if width and image.width > width:
        height = round(image.height * width / image.width)
        image = image.resize((width, height), Image.LANCZOS)
    image.save(OUT / target, optimize=True)
    print("saved", OUT / target, image.size)
