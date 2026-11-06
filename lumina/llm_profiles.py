# -*- coding: utf-8 -*-
"""多套 API 配置：同一个程序里存好几组「接口地址 + 模型名 + Key」，随时切换。

为什么要它：以前设置里只有一组 `llm_base_url / llm_model_name / llm_api_key`，
想从「学长的内网 Qwen」换到「DeepSeek 官方」就得把手填的值全改一遍，改完还回不去。

设计（尽量不惊动老代码）：

* 真身存在新表 `llm_profiles`，每行一套配置（name / base_url / model / preset），
  API Key 用 `store.set_secret("llm_profile_key_<id>")` 加密保存（DPAPI）；
* **当前选中的那套**会被「投影」回老的三个设置项（`llm_base_url` / `llm_model_name`
  / 密钥 `llm_api_key`）。于是 `ai_chat.config(store)`、`agent.py`、`prompt_gen.py`、
  `llm_local.py` 全都不用改，读到的一直是当前这套；
* 第一次启动时如果库里已经有老的单套配置，`migrate()` 会把它变成第 1 套配置。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

TABLE = "llm_profiles"
ACTIVE_KEY = "llm_profile_active"
SECRET_PREFIX = "llm_profile_key_"
MAX_PROFILES = 20


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure(store) -> None:
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS llm_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            base_url TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            preset TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    store.conn.commit()


def _guess_name(base_url: str, model: str) -> str:
    text = str(model or "").strip()
    if text:
        return text
    base = str(base_url or "").strip()
    if base:
        try:
            from urllib.parse import urlparse

            host = urlparse(base).hostname or base
            return host
        except Exception:
            return base[:24]
    return "配置 1"


def list_profiles(store) -> List[Dict[str, Any]]:
    ensure(store)
    rows = store.conn.execute(
        "SELECT * FROM llm_profiles ORDER BY id").fetchall()
    items = []
    for row in rows:
        items.append(_decorate(store, dict(row)))
    return items


def get_profile(store, profile_id) -> Optional[Dict[str, Any]]:
    ensure(store)
    try:
        row = store.conn.execute("SELECT * FROM llm_profiles WHERE id = ?",
                                 (int(profile_id),)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    item = _decorate(store, dict(row))
    return item


def _decorate(store, item: Dict[str, Any]) -> Dict[str, Any]:
    """补上 `has_key` / `label`：`get_profile()` 以前不带这两个字段，
    设置页 `profile.get("has_key")` 永远是 None → 明明存了 Key 也显示「这套没填 Key」。"""
    item["has_key"] = bool(secret(store, int(item.get("id") or 0)))
    item["label"] = item.get("name") or _guess_name(item.get("base_url") or "",
                                                    item.get("model") or "")
    return item


def secret_key(profile_id) -> str:
    return "%s%s" % (SECRET_PREFIX, int(profile_id))


def secret(store, profile_id) -> str:
    try:
        value = store.get_secret(secret_key(profile_id), "") or ""
    except Exception:
        return ""
    # 早期版本把老 key 存在 llm_api_key 里，迁移时由 project()/sync 负责搬运
    return str(value)


def active_id(store) -> Optional[int]:
    ensure(store)
    try:
        value = int(float(store.get_setting(ACTIVE_KEY, "") or 0))
    except Exception:
        value = 0
    if value:
        row = store.conn.execute("SELECT id FROM llm_profiles WHERE id = ?",
                                 (value,)).fetchone()
        if row:
            return int(row["id"])
    row = store.conn.execute("SELECT id FROM llm_profiles ORDER BY id LIMIT 1").fetchone()
    return int(row["id"]) if row else None


def active(store) -> Optional[Dict[str, Any]]:
    pid = active_id(store)
    return get_profile(store, pid) if pid else None


def _write_legacy(store, profile: Dict[str, Any]) -> None:
    """把某套配置写回老的三个设置项，让所有老代码都跟着切换。"""
    base = str(profile.get("base_url") or "").strip()
    model = str(profile.get("model") or "").strip()
    if base:
        store.set_setting("llm_base_url", base)
    if model:
        store.set_setting("llm_model_name", model)
    if profile.get("preset"):
        store.set_setting("llm_preset", str(profile["preset"]))
    key = secret(store, profile["id"])
    if key:
        store.set_secret("llm_api_key", key)
    else:
        # 这套没 Key：把老字段清掉，别让上一套的 Key 串过来
        try:
            store.set_secret("llm_api_key", "")
        except Exception:
            pass


def project(store) -> Optional[Dict[str, Any]]:
    """把「当前配置」投影到老设置项上（切换 / 编辑 / 启动时都调它）。"""
    profile = active(store)
    if profile is not None:
        _write_legacy(store, profile)
    return profile


def set_active(store, profile_id) -> Optional[Dict[str, Any]]:
    profile = get_profile(store, profile_id)
    if profile is None:
        return None
    store.set_setting(ACTIVE_KEY, str(int(profile["id"])))
    _write_legacy(store, profile)
    return profile


def _next_name(store) -> str:
    existing = {item["name"] for item in list_profiles(store)}
    for index in range(1, MAX_PROFILES + 2):
        name = "配置 %d" % index
        if name not in existing:
            return name
    return "配置 %d" % len(existing) + 1


def create_profile(store, name: str = "", base_url: str = "", model: str = "",
                   api_key: Optional[str] = None, preset: str = "") -> int:
    ensure(store)
    if len(list_profiles(store)) >= MAX_PROFILES:
        raise ValueError("最多只能存 %d 套配置" % MAX_PROFILES)
    stamp = _now()
    label = str(name or "").strip() or _next_name(store)
    cursor = store.conn.execute(
        "INSERT INTO llm_profiles(name, base_url, model, preset, note, created_at,"
        " updated_at) VALUES (?,?,?,?,?,?,?)",
        (label, str(base_url or "").strip(), str(model or "").strip(),
         str(preset or ""), "", stamp, stamp))
    store.conn.commit()
    pid = int(cursor.lastrowid)
    if api_key is not None:
        try:
            store.set_secret(secret_key(pid), str(api_key))
        except Exception:
            pass
    return pid


def save_profile(store, profile_id, *, name: Optional[str] = None,
                 base_url: Optional[str] = None, model: Optional[str] = None,
                 api_key: Optional[str] = None, preset: Optional[str] = None) -> None:
    """改一套配置；api_key 传 None 表示不动，传 "" 表示清空。"""
    ensure(store)
    profile = get_profile(store, profile_id)
    if profile is None:
        return
    fields = {}
    if name is not None:
        fields["name"] = str(name).strip() or profile.get("name") or ""
    if base_url is not None:
        fields["base_url"] = str(base_url).strip()
    if model is not None:
        fields["model"] = str(model).strip()
    if preset is not None:
        fields["preset"] = str(preset)
    if fields:
        fields["updated_at"] = _now()
        keys = ", ".join("%s = ?" % key for key in fields)
        store.conn.execute("UPDATE llm_profiles SET %s WHERE id = ?" % keys,
                           (*fields.values(), int(profile_id)))
        store.conn.commit()
    if api_key is not None:
        try:
            store.set_secret(secret_key(profile_id), str(api_key))
        except Exception:
            pass
    if active_id(store) == int(profile_id):
        project(store)


def delete_profile(store, profile_id) -> bool:
    """删掉一套配置；最后一套不能删（不然接口就彻底没配置了）。"""
    ensure(store)
    profiles = list_profiles(store)
    if len(profiles) <= 1:
        return False
    target = None
    for item in profiles:
        if int(item["id"]) == int(profile_id):
            target = item
            break
    if target is None:
        return False
    store.conn.execute("DELETE FROM llm_profiles WHERE id = ?", (int(profile_id),))
    store.conn.commit()
    try:
        store.set_secret(secret_key(profile_id), "")
    except Exception:
        pass
    if active_id(store) == int(profile_id):
        remaining = list_profiles(store)
        if remaining:
            set_active(store, remaining[0]["id"])
    return True


def migrate(store) -> Optional[int]:
    """第一次用：把老的单套配置变成第 1 套配置，并设为当前。"""
    ensure(store)
    if list_profiles(store):
        project(store)
        return active_id(store)
    try:
        base = (store.get_setting("llm_base_url", "") or "").strip()
        model = (store.get_setting("llm_model_name", "") or "").strip()
        key = store.get_secret("llm_api_key", "") or ""
        preset = (store.get_setting("llm_preset", "") or "").strip()
    except Exception:
        return None
    if not base and not model:
        # 一条配置都没有：给一份空白配置，用户才好上手
        pid = create_profile(store, "本地模型",
                             "http://127.0.0.1:8080/v1", "", "", preset)
    else:
        pid = create_profile(store, _guess_name(base, model), base, model, key, preset)
    set_active(store, pid)
    return pid


def sync_from_legacy(store, name: str = "") -> Optional[int]:
    """设置页保存后调用：把界面上的三个值写回当前这套配置。"""
    ensure(store)
    pid = active_id(store)
    if pid is None:
        pid = migrate(store)
    if pid is None:
        return None
    try:
        base = (store.get_setting("llm_base_url", "") or "").strip()
        model = (store.get_setting("llm_model_name", "") or "").strip()
        key = store.get_secret("llm_api_key", "") or ""
    except Exception:
        return pid
    fields = {"base_url": base, "model": model}
    if name:
        fields["name"] = name
    save_profile(store, pid, **fields)
    # 只有真的填了才写 Key（老字段偶尔会是空的，别把这一套已有的 Key 冲掉）
    if key:
        save_profile(store, pid, api_key=key)
    return pid


def describe(store, profile: Optional[Dict[str, Any]] = None) -> str:
    profile = profile or active(store)
    if not profile:
        return "还没配置接口"
    base = str(profile.get("base_url") or "").strip()
    model = str(profile.get("model") or "").strip()
    has_key = profile.get("has_key")
    if has_key is None:
        has_key = bool(secret(store, profile.get("id")))
    keyed = "有 Key" if has_key else "没填 Key"
    label = profile.get("label") or profile.get("name") or "配置"
    return "%s · %s · %s · %s" % (label, model or "（未填模型名）",
                                  base or "（未填地址）", keyed)


def summary(store) -> str:
    return describe(store)
