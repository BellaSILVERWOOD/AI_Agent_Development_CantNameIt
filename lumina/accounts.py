"""发件邮箱账户：支持配置多个 SMTP 账户并随时切换。

- 账户列表以 JSON 存在 settings.smtp_accounts
- 当前使用的账户 id 存在 settings.smtp_active
- 每个账户的密码单独加密保存（settings 表里的 secret key: ``smtp_password::<id>``）
- 兼容旧版本：若还没有账户列表而旧版单账户字段有值，会自动迁移成一个账户
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

ACCOUNTS_KEY = "smtp_accounts"
ACTIVE_KEY = "smtp_active"
LEGACY_SECRET = "smtp_password"

SECRET_PREFIX = "smtp_password::"


def secret_key(account_id: str) -> str:
    return f"{SECRET_PREFIX}{account_id}"


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def blank_account(name: str = "") -> Dict[str, Any]:
    """新建账户的初始结构。"""
    return {
        "id": new_id(),
        "label": name or "新账户",
        "host": "",
        "port": 465,
        "security": "ssl",
        "user": "",
        "sender_name": "",
        "sender_email": "",
    }


def _normalize(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把任意来源的字典整理成账户结构；缺关键字段时返回 None。"""
    if not isinstance(raw, dict):
        return None
    try:
        port = int(raw.get("port") or 465)
    except (TypeError, ValueError):
        port = 465
    security = str(raw.get("security") or "ssl")
    if security not in ("ssl", "starttls", "none"):
        security = "ssl"
    sender = str(raw.get("sender_email") or "").strip()
    host = str(raw.get("host") or "").strip()
    if not host and not sender:
        return None
    account = {
        "id": str(raw.get("id") or new_id()),
        "label": str(raw.get("label") or sender or host or "账户"),
        "host": host,
        "port": max(1, min(65535, port)),
        "security": security,
        "user": str(raw.get("user") or "").strip(),
        "sender_name": str(raw.get("sender_name") or "").strip(),
        "sender_email": sender,
    }
    return account


def load(store) -> List[Dict[str, Any]]:
    """读取账户列表（含旧数据迁移）。"""
    raw = store.get_setting(ACCOUNTS_KEY, "") or ""
    accounts: List[Dict[str, Any]] = []
    if raw.strip():
        try:
            data = json.loads(raw)
        except Exception:
            data = []
        if isinstance(data, list):
            for item in data:
                account = _normalize(item)
                if account:
                    accounts.append(account)
    if accounts:
        return accounts

    # ---- 旧版单账户迁移 ----
    host = (store.get_setting("smtp_host", "") or "").strip()
    sender = (store.get_setting("sender_email", "") or "").strip()
    if host or sender:
        legacy = _normalize(
            {
                "id": "legacy",
                "label": sender or host or "默认账户",
                "host": host,
                "port": store.get_setting("smtp_port", "465") or 465,
                "security": store.get_setting("smtp_security", "ssl") or "ssl",
                "user": store.get_setting("smtp_user", "") or "",
                "sender_name": store.get_setting("sender_name", "") or "",
                "sender_email": sender,
            }
        )
        if legacy:
            password = store.get_secret(LEGACY_SECRET, "")
            if password:
                store.set_secret(secret_key(legacy["id"]), password)
            accounts = [legacy]
            save(store, accounts, legacy["id"])
    return accounts


def save(store, accounts: List[Dict[str, Any]], active_id: str = "") -> None:
    """保存账户列表与当前账户。"""
    cleaned: List[Dict[str, Any]] = []
    for item in accounts:
        account = _normalize(item)
        if account:
            cleaned.append(account)
    store.set_setting(ACCOUNTS_KEY, json.dumps(cleaned, ensure_ascii=False))
    if cleaned:
        ids = [item["id"] for item in cleaned]
        store.set_setting(ACTIVE_KEY, active_id if active_id in ids else ids[0])
    else:
        store.set_setting(ACTIVE_KEY, "")


def active_id(store) -> str:
    accounts = load(store)
    if not accounts:
        return ""
    current = store.get_setting(ACTIVE_KEY, "") or ""
    if any(item["id"] == current for item in accounts):
        return current
    return accounts[0]["id"]


def active(store) -> Optional[Dict[str, Any]]:
    """当前使用的账户；没有账户时返回 None。"""
    accounts = load(store)
    if not accounts:
        return None
    wanted = active_id(store)
    for item in accounts:
        if item["id"] == wanted:
            return item
    return accounts[0]


def get(store, account_id: str) -> Optional[Dict[str, Any]]:
    for item in load(store):
        if item["id"] == account_id:
            return item
    return None


def password(store, account_id: str) -> str:
    if not account_id:
        return ""
    value = store.get_secret(secret_key(account_id), "")
    if value:
        return value
    if account_id == "legacy":
        return store.get_secret(LEGACY_SECRET, "")
    return ""


def set_password(store, account_id: str, plain: str) -> None:
    if account_id:
        store.set_secret(secret_key(account_id), plain)


def stamp(account: Dict[str, Any]) -> str:
    """生成一个可读的时间戳，用于默认账户名。"""
    return time.strftime("%m-%d %H:%M", time.localtime())
