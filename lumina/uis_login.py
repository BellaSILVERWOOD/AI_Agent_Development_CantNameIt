"""复旦统一身份认证（UIS）。

⚠ 2026 年复旦把 UIS 换成了新的 `id.fudan.edu.cn/ac/`（Vue 单页 + RSA 加密的
`/authn/authExecute` 接口），老的 `uis.fudan.edu.cn/authserver/login` 已经变成
一张公告页（notice.html），所以**后台直连表单登录这条路没了**。

现在的做法：学号/密码只在**我们自己的内嵌浏览器**里自动填进登录页（页面自己用
JSEncrypt 加密再提交），不由程序拼请求。好处：
* 不用逆向前端加密，不会被改版打断；
* 需要二次验证（短信/邮箱/扫码）时，用户手动过一次就行；
* 密码不会经过我们拼的 HTTP 请求。

设置项：
    uis_username          学号 / 工号（明文）
    uis_password          密码（secure 模块 DPAPI 加密，设置里只能改不能看）
    uis_autofill          "1" = 内嵌网页遇到登录页时自动填账号密码（默认开）

对外接口：
    has_credentials(store) -> bool
    credentials(store)     -> {"username","password"}
    autofill_enabled(store)-> bool
    autofill_script(store) -> str       # 要注入的 JS（填表 + 点登录）
    is_login_url(url)      -> bool      # 是不是统一身份认证的页面
    login(store)           -> Dict      # 后台直连（老接口），现在只用于兼容
    canvas_cookies(store)  -> Dict      # 旧的 SSO 换 cookie，保留兼容
    test(store)            -> Dict      # 设置页「测试」
    save(store, user, pwd) / clear_password(store)
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

UIS_LOGIN_URL = "https://uis.fudan.edu.cn/authserver/login"
UIS_NEW_HOME = "https://id.fudan.edu.cn/ac/"
CANVAS_LOGIN_URL = "https://elearning.fudan.edu.cn/login/saml"
UIS_HOME = "https://uis.fudan.edu.cn/authserver/login?service=https%3A%2F%2Felearning.fudan.edu.cn%2Flogin%2Fsaml"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

LOGIN_HOSTS = ("id.fudan.edu.cn", "uis.fudan.edu.cn")


class UisError(RuntimeError):
    """登录失败，message 直接给人看。"""


def credentials(store) -> Dict[str, str]:
    return {
        "username": (store.get_setting("uis_username", "") or "").strip(),
        "password": store.get_secret("uis_password", "") or "",
    }


def has_credentials(store) -> bool:
    data = credentials(store)
    return bool(data["username"] and data["password"])


def autofill_enabled(store) -> bool:
    return (store.get_setting("uis_autofill", "1") or "1") == "1"


def is_login_url(url: str) -> bool:
    text = (url or "").lower()
    if not text:
        return False
    if not any(host in text for host in LOGIN_HOSTS):
        return False
    # 登录页 + CAS 跳转页；已经进到业务页面的不算
    return True


# ------------------------------------------------------------------ 自动填表
AUTOFILL_JS = r"""
(function(){
  var user = __USER__, pwd = __PWD__;
  function setValue(el, value){
    if (!el) return false;
    el.focus();
    var proto = Object.getPrototypeOf(el);
    var desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) { desc.set.call(el, value); }
    else { el.value = value; }
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
    el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));
    return true;
  }
  var inputs = Array.prototype.slice.call(document.querySelectorAll('input'));
  var userInput = null, pwdInput = null;
  for (var i = 0; i < inputs.length; i++){
    var el = inputs[i];
    var type = (el.type || '').toLowerCase();
    if (type === 'password' && !pwdInput) pwdInput = el;
    if (!userInput && (type === 'text' || type === 'tel' || type === 'email' || !type)){
      userInput = el;
    }
  }
  var filledUser = setValue(userInput, user);
  var filledPwd = setValue(pwdInput, pwd);
  if (!pwdInput) return JSON.stringify({ok:false, reason:'没找到密码输入框', url:location.href});
  // 点登录：优先文案匹配，其次 submit 按钮
  var clicked = false;
  var nodes = Array.prototype.slice.call(document.querySelectorAll('button, a, div[role="button"], input[type="submit"]'));
  for (var j = 0; j < nodes.length; j++){
    var text = (nodes[j].innerText || nodes[j].value || '').replace(/\s+/g, '');
    if (/^(登录|登陆|login|提交|确定)$/i.test(text)) {
      nodes[j].click();
      clicked = true;
      break;
    }
  }
  if (!clicked) {
    var form = pwdInput.form;
    if (form) { form.submit(); clicked = true; }
  }
  return JSON.stringify({ok:true, user:filledUser, pwd:filledPwd, clicked:clicked, url:location.href});
})()
"""


def autofill_script(store) -> str:
    """生成注入用的 JS（账号密码做 JSON 转义，安全）。"""
    data = credentials(store)
    return (AUTOFILL_JS
            .replace("__USER__", json.dumps(data["username"]))
            .replace("__PWD__", json.dumps(data["password"])))


# ------------------------------------------------------------------ 后台直连（旧的）
def _proxies(store) -> Optional[Dict[str, str]]:
    try:
        from . import vpn

        return vpn.requests_proxies(store)
    except Exception:
        return None


def login(store, timeout: float = 20.0) -> Dict[str, str]:
    """后台表单登录。

    老接口已经下线（会 302 到公告页），所以这里只给出准确的错误说明，
    真正的登录交给内嵌浏览器自动填表（`autofill_script`）。
    """
    data = credentials(store)
    if not data["username"] or not data["password"]:
        raise UisError("还没有配置统一身份认证的账号密码（设置 → 统一身份认证）")
    import requests

    proxies = _proxies(store)
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    try:
        page = session.get(UIS_LOGIN_URL, timeout=timeout, proxies=proxies)
    except Exception as exc:  # noqa: BLE001
        raise UisError(f"打不开 UIS：{exc}") from exc
    html = page.text or ""
    execution = re.search(r'name="execution"\s+value="([^"]+)"', html)
    if execution:
        payload = {
            "username": data["username"],
            "password": data["password"],
            "execution": execution.group(1),
            "_eventId": "submit",
            "lt": "",
            "rmShown": "1",
        }
        try:
            response = session.post(UIS_LOGIN_URL, data=payload, timeout=timeout,
                                    proxies=proxies, allow_redirects=True)
        except Exception as exc:  # noqa: BLE001
            raise UisError(f"提交登录失败：{exc}") from exc
        cookies = session.cookies.get_dict()
        if not cookies:
            raise UisError("登录没有拿到任何 cookie")
        if "CASTGC" not in cookies and "authserver" in (response.url or ""):
            raise UisError("账号或密码不对（UIS 仍在登录页）")
        return cookies
    raise UisError(
        "复旦已经换成新的统一身份认证（id.fudan.edu.cn），后台直连登录已不可用。"
        "现在改成在网页里自动填账号密码：打开 eHall / 作业页，遇到登录页会自动填好，"
        "点一下「登录」即可；有二次验证的话手动过一次。")


def canvas_cookies(store, timeout: float = 25.0) -> Dict[str, str]:
    """走 SSO 换 eLearning 的会话 cookie（老流程，新 UIS 下会失败）。"""
    import requests

    data = credentials(store)
    if not data["username"] or not data["password"]:
        raise UisError("还没有配置统一身份认证的账号密码")
    proxies = _proxies(store)
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    try:
        page = session.get(CANVAS_LOGIN_URL, timeout=timeout, proxies=proxies)
    except Exception as exc:  # noqa: BLE001
        raise UisError(f"打不开 eLearning 登录入口：{exc}") from exc
    html = page.text or ""
    execution = re.search(r'name="execution"\s+value="([^"]+)"', html)
    if not execution:
        cookies = session.cookies.get_dict()
        if cookies and "authserver" not in (page.url or ""):
            return cookies
        raise UisError("eLearning 的登录页也换成新版统一身份认证了，"
                       "请在作业页的网页里登录一次（会自动填账号密码）")
    payload = {
        "username": data["username"],
        "password": data["password"],
        "execution": execution.group(1),
        "_eventId": "submit",
        "lt": "",
        "rmShown": "1",
    }
    try:
        response = session.post(page.url or UIS_LOGIN_URL, data=payload,
                                timeout=timeout, proxies=proxies,
                                allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        raise UisError(f"提交 SSO 登录失败：{exc}") from exc
    cookies = session.cookies.get_dict()
    keys = set(cookies)
    if not keys & {"_normandy_session", "_legacy_normandy_session", "log_session_id"}:
        raise UisError("SSO 之后没拿到 eLearning 会话（可能是密码错误或需要二次验证）")
    return cookies


def test(store, timeout: float = 20.0) -> Dict[str, Any]:
    """给设置页用：新 UIS 下只做「能不能自动填表」的检查。"""
    if not has_credentials(store):
        return {"ok": False, "error": "还没填学号或密码"}
    if not autofill_enabled(store):
        return {"ok": True,
                "error": "账号密码已保存，但「网页自动填充」是关的，打开才能自动登录"}
    return {
        "ok": True,
        "mode": "autofill",
        "message": "账号密码已保存：打开 eHall / 作业页时，遇到统一身份认证会自动填好，"
                   "点一下「登录」（有二次验证就过一次）",
    }


def save(store, username: str, password: str) -> None:
    """保存账号；密码只在非空时覆盖（设置里看不到原密码）。"""
    store.set_setting("uis_username", (username or "").strip())
    if password:
        store.set_secret("uis_password", password)


def clear_password(store) -> None:
    store.set_secret("uis_password", "")

