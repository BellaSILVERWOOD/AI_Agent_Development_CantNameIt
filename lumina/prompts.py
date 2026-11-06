"""提示词配置：所有 AI 提示词都存在一个可编辑的配置文件里，不写死在代码中。

文件位置：`<data>/prompts.json`（跟着数据目录走，可在设置里换盘；升级软件不会覆盖）。
结构：
    {
      "version": 1,
      "updated_at": "2026-10-23T21:00:00",
      "system": {
        "assistant": "……",        // 通用系统提示词
        "weekly_report": "……",    // 周报场景
        "homework": "……",         // 作业/课件摘要场景
        "latex": "……"             // 公式/LaTeX 场景
      },
      "scenes": {                  // 场景模板（可增删）
        "名称": {"title": "显示名", "prompt": "……", "enabled": true}
      }
    }

用法：
    prompts.ensure()                 启动时确保文件存在
    prompts.get("system", "assistant")
    prompts.scene("weekly_report")
    prompts.save({"system": {...}})  写回（会合并，不会丢用户的其它字段）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .config import DATA_DIR

FILE_NAME = "prompts.json"
VERSION = 1
# 助手人格：品牌叫 DanLume，AI 自己叫 Lume（中文名「卢梅」）
PERSONA = "你是 Lume（卢梅），DanLume 里的写作与学习助手。"
# 老版本的默认人格：迁移时替换掉；用户自己改过的人设不动
# （这里必须保留旧的写法，否则老库里的 prompts.json 认不出来）
LEGACY_PERSONAS = (
    "你是 Lumina 里的写作与学习助手。",
    "你是 DanLume 里的写作与学习助手。",
)

DEFAULTS: Dict[str, Any] = {
    "version": VERSION,
    "note": "这里是 DanLume 的提示词配置，可以自己改；改完重启软件生效。",
    "system": {
        "assistant": (
            PERSONA + "回答用简体中文，简洁直接，"
            "先给结论再给要点；不确定的地方要说明不确定，不要编造。"
        ),
        "weekly_report": (
            "把下面这一周的日程与完成情况整理成周报：分「本周完成」「遇到的问题」"
            "「下周计划」三段，每段用短句，不要客套话，总字数控制在 400 字以内。"
        ),
        "homework": (
            "阅读这份课件/作业，输出：1) 三句话摘要；2) 需要动手完成的任务清单；"
            "3) 截止时间（如果提到了）；4) 需要用到的知识点。"
        ),
        "latex": (
            "把这段自然语言里的数学内容转成 LaTeX 公式，只用数学模式内容，"
            "不要加解释文字；多个公式用换行分隔。"
        ),
        "wechat_todo": (
            "你是日程助手。从用户给的聊天记录里找出「用户自己需要去做、且有明确时间」的事项，"
            "忽略纯通知、广告、别人的安排、没有时间的闲聊。"
            "只输出 JSON 数组，不要任何解释文字，格式：\n"
            "[{\"title\":\"简短标题\",\"date\":\"YYYY-MM-DD\",\"start_time\":\"HH:MM\","
            "\"end_time\":\"HH:MM或空\",\"location\":\"地点或空\",\"note\":\"原话摘录\"}]\n"
            "今天的日期是 {today}。没有合适事项就输出 []。"
        ),
    },
    "scenes": {
        "weekly_report": {
            "title": "生成周报",
            "prompt": "根据我的日程数据写一份周报。（场景模板，可自行修改）",
            "enabled": True,
        },
        "homework_summary": {
            "title": "作业摘要",
            "prompt": "总结这份作业要求，列出要做的事和截止时间。",
            "enabled": True,
        },
    },
}


def path() -> Path:
    return Path(DATA_DIR) / FILE_NAME


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load() -> Dict[str, Any]:
    """读配置；文件不存在或坏了就返回默认值。"""
    target = path()
    if not target.exists():
        return json.loads(json.dumps(DEFAULTS))
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return json.loads(json.dumps(DEFAULTS))
    if not isinstance(data, dict):
        return json.loads(json.dumps(DEFAULTS))
    return _deep_merge(DEFAULTS, data)


def ensure() -> Path:
    """确保配置文件存在（首次运行时写一份默认的出来），并把旧品牌/旧人格迁移掉。"""
    target = path()
    if not target.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return target
    migrate_brand(target)
    return target


def migrate_brand(target: Optional[Path] = None) -> bool:
    """把老的「Lumina」品牌与老默认人格换成 DanLume / Lume（卢梅）。

    只改**字符串里出现的品牌词和那几句老默认人格**，用户自己写的其它内容原样保留。
    返回是否改过（改过就写回文件）。
    """
    target = Path(target) if target is not None else path()
    if not target.exists():
        return False
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    changed = [False]

    def fix(value: str, persona: bool) -> str:
        """persona=True 说明这是提示词正文（旧品牌名要换成人格名 Lume），
        否则是说明文字（旧品牌名换成产品名 DanLume）。"""
        text = str(value)
        for legacy in LEGACY_PERSONAS:
            if text.startswith(legacy):
                text = PERSONA + text[len(legacy):].lstrip()
                changed[0] = True
        # 以前用户自己写的「你是复旦大学学生学习助手 Lumina 光华。……」
        # 按要求统一成「你是 Lume（卢梅），复旦大学学生的学习助手。」
        if persona and text.startswith("你是复旦大学学生学习助手"):
            head, sep, tail = text.partition("。")
            if sep:
                text = "你是 Lume（卢梅），复旦大学学生的学习助手" + sep + tail
                changed[0] = True
        if "Lumina" in text:
            text = text.replace("Lumina 光华", "Lume 卢梅")
            text = text.replace("Lumina光华", "Lume卢梅")
            text = text.replace("Lumina", "Lume" if persona else "DanLume")
            changed[0] = True
        return text

    def walk(node, persona: bool = True):
        if isinstance(node, dict):
            return {key: walk(value, persona and key != "note")
                    for key, value in node.items()}
        if isinstance(node, list):
            return [walk(value, persona) for value in node]
        if isinstance(node, str):
            return fix(node, persona)
        return node

    updated = walk(data)
    if not changed[0]:
        return False
    updated["version"] = VERSION
    try:
        from datetime import datetime

        updated["updated_at"] = datetime.now().isoformat(timespec="seconds")
    except Exception:
        pass
    try:
        target.write_text(json.dumps(updated, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        return True
    except Exception:
        return False


def save(values: Dict[str, Any]) -> bool:
    """把改动写回文件（和现有内容合并，保留用户自己加的键）。"""
    current = load()
    merged = _deep_merge(current, values or {})
    merged["version"] = VERSION
    try:
        from datetime import datetime

        merged["updated_at"] = datetime.now().isoformat(timespec="seconds")
    except Exception:
        pass
    try:
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def get(section: str, name: str, default: str = "") -> str:
    data = load()
    bucket = data.get(section) or {}
    if isinstance(bucket, dict):
        value = bucket.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return default


def system(name: str = "assistant") -> str:
    return get("system", name, DEFAULTS["system"].get(name, ""))


def scenes() -> Dict[str, Dict[str, Any]]:
    data = load()
    bucket = data.get("scenes")
    if isinstance(bucket, dict):
        return {key: value for key, value in bucket.items()
                if isinstance(value, dict) and value.get("enabled", True)}
    return {}


def scene(name: str) -> Optional[str]:
    item = scenes().get(name)
    if isinstance(item, dict):
        prompt = item.get("prompt")
        if isinstance(prompt, str):
            return prompt
    return None
