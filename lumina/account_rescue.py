# -*- coding: utf-8 -*-
"""本机管理员账户「找回」：数据都在、只是账户没了 / 密码忘了时的紧急出口。

为什么要有这条路：账户存在**每份数据目录自己的库**里。程序被复制一份、数据目录跟着
换掉之后，就会出现「明明我是管理员，登录窗口却说没有这个账户」。

放行条件只有一个：**本机数据目录里有管理员私钥**（`admin_key.pem` 或库里加密保存的
那份）。有私钥的人本来就是管理员 —— 客户端版安装包里没有私钥，所以这条出口对普通
同学不可用，也就不会被绕过审批流程。

它只做两件事，都不碰别人的数据：
* 本机没有任何管理员 → 用给定的用户名建一个（角色 admin）；
* 已经有同名管理员 → 只改密码，其余不动。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

from . import adminsign, users

MIN_PASSWORD = getattr(users, "MIN_PASSWORD", 8)


def admin_key_available(store) -> bool:
    """本机有没有管理员私钥（文件或库里的加密副本）。"""
    try:
        if adminsign.private_pem_file().exists():
            return True
    except Exception:
        pass
    try:
        return bool(adminsign.has_private_key(store))
    except Exception:
        return False


def local_admins(store) -> List[Dict[str, Any]]:
    try:
        return [item for item in users.list_users(store)
                if str(item.get("role") or "") == "admin"]
    except Exception:
        return []


def suggested_username(store) -> str:
    """默认填哪个名字：有管理员就用第一个，没有就留空让人自己填。"""
    admins = local_admins(store)
    if admins:
        return str(admins[0].get("username") or "")
    return ""


def describe(store) -> str:
    admins = local_admins(store)
    names = "、".join(str(item.get("username")) for item in admins) or "（没有）"
    return "本机管理员：%s" % names


def _log(store, text: str) -> None:
    """留一条痕迹：谁在什么时候找回过账户。"""
    try:
        from .config import DATA_DIR

        path = Path(DATA_DIR) / "rescue.log"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (stamp, text))
    except Exception:
        pass


def rescue(store, username: str, password: str) -> Dict[str, Any]:
    """建管理员 / 重置管理员密码。失败抛 users.AccountError（原因给人看）。"""
    name = str(username or "").strip()
    if not name:
        raise users.AccountError("先填一个用户名（可以和原来一样）")
    if len(str(password or "")) < MIN_PASSWORD:
        raise users.AccountError("密码至少 %d 位" % MIN_PASSWORD)
    if not admin_key_available(store):
        raise users.AccountError(
            "本机没有管理员私钥，不能这样找回账户；请让管理员按注册流程批准。")

    existing = users.find_user(store, name)
    if existing is not None:
        if str(existing.get("role") or "") != "admin":
            raise users.AccountError(
                f"「{name}」是普通账户，不能当管理员；换一个名字，"
                f"或者先用它登录后请管理员提权。")
        users.set_password(store, int(existing["id"]), password)
        # 找回时顺手把「停用 / 到期」这两个拦路的状态清掉
        store.conn.execute(
            "UPDATE users SET status = 'active', expires_at = '' WHERE id = ?",
            (int(existing["id"]),))
        store.conn.commit()
        _log(store, "重置管理员密码：%s（#%s）" % (name, existing["id"]))
        return {"action": "reset", "username": name, "id": int(existing["id"])}

    user_id = users.create_user(store, name, password, email="", role="admin")
    _log(store, "重建管理员账户：%s（#%s）" % (name, user_id))
    return {"action": "created", "username": name, "id": int(user_id)}


def hint(store) -> str:
    """给登录窗口的一句话说明（什么时候该点这个按钮）。"""
    if not admin_key_available(store):
        return ""
    admins = local_admins(store)
    if admins:
        return ("这台电脑的管理员密钥还在。如果密码忘了，可以点「找回管理员账户」"
                "直接重设密码（日程、邮件、备忘都不会动）。")
    return ("这个数据目录里**没有管理员账户**，但管理员密钥还在 —— 点"
            "「找回管理员账户」用密钥重建一个，原来的数据都还在。")
