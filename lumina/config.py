"""应用级常量与路径。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "DanLume"
APP_NAME_LEGACY = "Lumina"          # 旧品牌名：只用于兼容旧的目录 / 指针文件
APP_TITLE = "DanLume · 日程与汇报"
APP_SUBTITLE = "本地优先 · 日程规划与工作汇报"
APP_VERSION = "1.0.0"


# 数据目录：默认放在项目下的 data/；打包成 exe 后放在 exe 同级的 data/
#
# 为什么还要一个「指针文件」：数据目录跟着程序走，程序被复制一份就多出一份空库 ——
# 于是出现「明明我是管理员，登录却说没有这个账户」。所以允许在程序根目录（或
# %LOCALAPPDATA%\Lumina）放一个 `data_dir.txt`，里面写真正要用的数据目录。
# 优先级：环境变量 LUMINA_DATA_DIR > data_dir.txt > 默认位置。
DATA_DIR_POINTER = "data_dir.txt"


def _app_root_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _pointer_files() -> list:
    """按优先级列出可能放指针文件的位置。

    * 程序根目录：源码版就是 main.py 那层；打包版是 **exe 所在目录**；
    * 上一级目录：压缩包通常解成 `DanLume\\DanLume.exe`，用户很自然会把
      `data_dir.txt` 放在外面那层；
    * `%LOCALAPPDATA%\\DanLume\\data_dir.txt`：整台机器共用一个数据目录时用；
    * 老的 `%LOCALAPPDATA%\\Lumina\\data_dir.txt` 也认，改名前的设置不能失效。
    """
    root = _app_root_dir()
    files = [root / DATA_DIR_POINTER, root.parent / DATA_DIR_POINTER]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for name in (APP_NAME, APP_NAME_LEGACY):
            files.append(Path(local) / name / DATA_DIR_POINTER)
    return files


def data_dir_pointer() -> Path:
    """当前生效的指针文件路径（不管它存不存在）。"""
    for path in _pointer_files():
        if path.exists():
            return path
    return _pointer_files()[0]


def pointed_data_dir():
    """指针文件指向的数据目录；没有 / 无效就返回 None。"""
    for path in _pointer_files():
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if not text:
            continue
        try:
            target = Path(text).expanduser()
        except Exception:
            continue
        if target.is_dir():
            return target
    return None


def set_data_dir_pointer(folder) -> Path:
    """把数据目录写到指针文件里（下次启动生效），返回写入的路径。"""
    target = Path(str(folder)).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    errors = []
    for path in _pointer_files():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(target) + "\n", encoding="utf-8")
            return path
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (path, exc))
    raise OSError("写不了指针文件：" + "；".join(errors))


def _resolve_data_dir() -> Path:
    env = os.environ.get("LUMINA_DATA_DIR")
    if env:
        base = Path(env)
    else:
        pointed = pointed_data_dir()
        if pointed is not None:
            base = pointed
        elif getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent / "data"
        else:
            base = Path(__file__).resolve().parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


DATA_DIR = _resolve_data_dir()


def data_dir_source() -> str:
    """数据目录是怎么定下来的（登录窗口 / 诊断报告里显示给人看）。"""
    if os.environ.get("LUMINA_DATA_DIR"):
        return "环境变量 LUMINA_DATA_DIR"
    if pointed_data_dir() is not None:
        return "data_dir.txt"
    return "exe 同级 data/" if getattr(sys, "frozen", False) else "程序目录下 data/"


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

# 周视图的作息表（第几节课 → 起止时间）。跟教务处一致，可以在周视图里改。
PERIOD_TIMES = [
    (1, "08:00", "08:45"),
    (2, "08:55", "09:40"),
    (3, "09:55", "10:40"),
    (4, "10:50", "11:35"),
    (5, "11:45", "12:30"),
    (6, "13:30", "14:15"),
    (7, "14:25", "15:10"),
    (8, "15:25", "16:10"),
    (9, "16:20", "17:05"),
    (10, "17:15", "18:00"),
    (11, "18:30", "19:15"),
    (12, "19:25", "20:10"),
    (13, "20:20", "21:05"),
    (14, "21:15", "22:00"),
]

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
    "bounced": "已退信",
}
MAIL_STATUS_TONES = {
    "draft": "#6F786D",
    "sent": "#2F7A5F",
    "failed": "#B33F3B",
    "bounced": "#B36B00",
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
