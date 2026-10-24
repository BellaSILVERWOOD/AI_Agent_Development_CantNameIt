"""应用级常量与路径。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Lumina"
APP_TITLE = "Lumina · 日程与汇报"
APP_SUBTITLE = "本地优先 · 日程规划与工作汇报"
APP_VERSION = "1.0.0"


# 数据目录：默认放在项目下的 data/；打包成 exe 后放在 exe 同级的 data/
def _resolve_data_dir() -> Path:
    env = os.environ.get("LUMINA_DATA_DIR")
    if env:
        base = Path(env)
    elif getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent / "data"
    else:
        base = Path(__file__).resolve().parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


DATA_DIR = _resolve_data_dir()


# 应用根目录（打包后是 exe 所在目录）：assets/ 就放在这里
def _resolve_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = _resolve_app_root()
DB_PATH = DATA_DIR / "lumina.db"

# 日程的主题色
# 采用 Apple System Colors（浅色取值）；深色模式由 theme.event_base() 换成对应深色值
EVENT_COLORS = {
    "violet": ("#AF52DE", "系统紫"),
    "teal": ("#30B0C7", "系统青"),
    "amber": ("#FF9500", "系统橙"),
    "rose": ("#FF2D55", "系统粉"),
    "sky": ("#007AFF", "系统蓝"),
}
COLOR_ORDER = ["violet", "teal", "amber", "rose", "sky"]

# 提醒提前量（分钟，None = 不提醒）
REMIND_OPTIONS = [
    (None, "不提醒"),
    (0, "日程开始时"),
    (5, "提前 5 分钟"),
    (15, "提前 15 分钟"),
    (30, "提前 30 分钟"),
    (60, "提前 1 小时"),
    (1440, "提前 1 天"),
]
REMIND_LABELS = {minutes: label for minutes, label in REMIND_OPTIONS}

WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
WEEKDAY_SHORT = ["一", "二", "三", "四", "五", "六", "日"]

SMTP_SECURITY = [
    ("ssl", "SSL / TLS（常用 465）"),
    ("starttls", "STARTTLS（常用 587）"),
    ("none", "不加密（内网 / 本地测试）"),
]
SMTP_SECURITY_LABELS = {key: label for key, label in SMTP_SECURITY}

MAIL_STATUS_LABELS = {
    "draft": "草稿",
    "sent": "已发送",
    "failed": "发送失败",
}
MAIL_STATUS_TONES = {
    "draft": "#6F786D",
    "sent": "#2F7A5F",
    "failed": "#B33F3B",
}

MAX_FILE_MB = 20
MAX_TOTAL_MB = 25

ATTACHMENT_FILTER = (
    "支持的文件 (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg *.pdf "
    "*.doc *.docx *.xls *.xlsx *.ppt *.pptx *.txt *.csv *.md *.zip *.rar *.7z);;"
    "图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg);;"
    "文档 (*.pdf *.doc *.docx *.xls *.xlsx *.ppt *.pptx *.txt *.csv *.md);;"
    "所有文件 (*.*)"
)
