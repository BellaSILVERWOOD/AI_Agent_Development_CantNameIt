"""E2：让 agent 改应用设置 —— 只开放**安全白名单**，并且每次都要用户确认。

用户要求（E2）：「A，并且每次必须经过用户确认」。

白名单里只放「不影响安全、随时能改回来」的设置：提醒间隔、用量上报间隔、
是否在提示词里带日期/IP、默认收件人、主题模式等。
**绝不**放：密码、密钥、邮箱账号、安装模式、管理员相关、文件路径类。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: key -> (说明, 类型, 可选值/范围)
WHITELIST: Dict[str, Dict[str, Any]] = {
    "report_interval_minutes": {
        "desc": "使用上报间隔（分钟，1–1440）",
        "type": "int", "min": 1, "max": 1440},
    "daily_limit_hours": {
        "desc": "每天使用时长上限（小时，1–24）",
        "type": "int", "min": 1, "max": 24},
    "prompt_prepend_context": {
        "desc": "系统提示词最前面是否带日期与本机 IP（1/0）",
        "type": "bool"},
    "prompt_public_ip": {
        "desc": "提示词里是否带公网 IP（1/0）",
        "type": "bool"},
    "ai_thinking": {
        "desc": "思考模式：auto / off / on",
        "type": "enum", "values": ["auto", "off", "on"]},
    "local_access": {
        "desc": "本地资源权限：off / read / write",
        "type": "enum", "values": ["off", "read", "write"]},
    "default_recipient": {
        "desc": "汇报邮件默认收件人（邮箱地址）",
        "type": "email"},
    "theme_mode": {
        "desc": "主题：light / dark / auto",
        "type": "enum", "values": ["light", "dark", "auto"]},
    "focus_minutes": {
        "desc": "专注时长（分钟，1–180）",
        "type": "int", "min": 1, "max": 180},
}

BLOCKED_PREFIXES = ("admin", "smtp", "imap", "llm", "report_url", "package",
                    "install", "client", "password", "secret", "token")


def describe() -> str:
    lines = ["可以改这些设置（每次都会先问你）："]
    for key, spec in WHITELIST.items():
        lines.append(f"- {key}：{spec['desc']}")
    return "\n".join(lines)


def _validate(key: str, value: Any) -> tuple:
    spec = WHITELIST.get(key)
    if spec is None:
        return False, f"「{key}」不在可改的白名单里（为了安全，密码/账号/密钥一律不给改）"
    kind = spec["type"]
    text = str(value)
    if kind == "int":
        try:
            number = int(float(text))
        except (TypeError, ValueError):
            return False, f"「{key}」要填整数"
        if not (spec["min"] <= number <= spec["max"]):
            return False, (f"「{key}」要在 {spec['min']}–{spec['max']} 之间")
        return True, str(number)
    if kind == "bool":
        low = text.strip().lower()
        if low in ("1", "true", "yes", "on", "是", "开"):
            return True, "1"
        if low in ("0", "false", "no", "off", "否", "关"):
            return True, "0"
        return False, f"「{key}」要填 1 或 0"
    if kind == "enum":
        low = text.strip().lower()
        if low not in [str(item).lower() for item in spec["values"]]:
            return False, f"「{key}」只能是 {' / '.join(spec['values'])}"
        return True, low
    if kind == "email":
        value = text.strip()
        if "@" not in value or " " in value:
            return False, "要填一个邮箱地址"
        return True, value
    return False, "这个设置不支持修改"


def setting_get(store, key: str = "") -> Dict[str, Any]:
    name = str(key or "").strip()
    if not name:
        items = {item: store.get_setting(item, "") for item in WHITELIST}
        return {"ok": True, "settings": items,
                "summary": "当前可改的设置：" + "、".join(
                    f"{item}={value}" for item, value in items.items())}
    try:
        value = store.get_setting(name, "")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"读不到设置：{exc}"}
    return {"ok": True, "key": name, "value": value,
            "summary": f"{name} = {value}"}


def setting_set(store, key: str = "", value: Any = "") -> Dict[str, Any]:
    name = str(key or "").strip()
    if not name:
        return {"ok": False, "summary": "要改哪个设置？（给个 key）"}
    if any(name.startswith(prefix) for prefix in BLOCKED_PREFIXES):
        return {"ok": False,
                "summary": f"「{name}」涉及敏感配置，不允许 AI 修改"}
    ok, result = _validate(name, value)
    if not ok:
        return {"ok": False, "summary": result}
    try:
        before = store.get_setting(name, "")
        store.set_setting(name, result)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"改不了：{exc}"}
    return {"ok": True, "key": name, "before": before, "value": result,
            "summary": f"已把 {name} 从 {before!r} 改成 {result!r}"}
