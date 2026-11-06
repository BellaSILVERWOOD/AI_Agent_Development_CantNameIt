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
# 新版 UIS（id.fudan.edu.cn）是前端框架渲染的单页应用，踩过的坑：
# * 页面在 `load` 之后还会重渲染一次，早早填进去的值会被框架抹掉；
# * 只设 `el.value` 不行，框架的响应式数据不会更新（于是提交时提示「用户名不能为空」）；
# * 有的入口把登录框放在 iframe 里。
# 所以这里：① 连 iframe 一起找；② 用原生 setter + input/change/keyup 事件让框架认；
# ③ 填完**校验真的写进去了**；④ 只有校验通过才点「登录」；⑤ 交给 Python 侧多重试几次。
AUTOFILL_JS = r"""
(function(){
  var user = __USER__, pwd = __PWD__, wantClick = __CLICK__;
  function docs(){
    var out = [document];
    try {
      var frames = document.querySelectorAll('iframe, frame');
      for (var i = 0; i < frames.length; i++){
        try {
          var d = frames[i].contentDocument;
          if (d) out.push(d);
        } catch (e) {}
      }
    } catch (e) {}
    return out;
  }
  function nativeSet(el, value){
    try {
      var desc = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value');
      if (desc && desc.set) { desc.set.call(el, value); }
      else { el.value = value; }
    } catch (e) { try { el.value = value; } catch (e2) { return false; } }
    try {
      el.dispatchEvent(new InputEvent('input', {bubbles: true, data: value,
                                                inputType: 'insertText'}));
    } catch (e) {
      el.dispatchEvent(new Event('input', {bubbles: true}));
    }
    el.dispatchEvent(new Event('change', {bubbles: true}));
    el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'a'}));
    try { el.dispatchEvent(new Event('blur', {bubbles: true})); } catch (e) {}
    return true;
  }
  function visible(el){
    try {
      var r = el.getBoundingClientRect();
      if (r.width < 16 || r.height < 8) { return false; }
      var s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
    } catch (e) { return true; }
  }
  function editable(el){
    if (!el || el.readOnly || el.disabled) { return false; }
    var type = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file']
        .indexOf(type) >= 0) { return false; }
    return visible(el);
  }
  function blob(el){
    var parts = [el.getAttribute('name'), el.id, el.getAttribute('placeholder'),
                 el.getAttribute('aria-label'), el.getAttribute('autocomplete'),
                 el.getAttribute('title'),
                 (el.form && (el.form.id || el.form.className)) || ''];
    return parts.join(' ').toLowerCase();
  }
  // 给「账号框」打分：像账号的加分，像语言切换 / 搜索的狠狠减分
  function score(el){
    var b = blob(el), s = 0;
    if (/user|account|login|学号|工号|账号|用户|uname/.test(b)) { s += 6; }
    if (el.getAttribute('autocomplete') === 'username') { s += 6; }
    if (/lang|language|locale|语言/i.test(b)) { s -= 12; }
    if (/search|搜索|query|filter/i.test(b)) { s -= 8; }
    if ((el.type || '').toLowerCase() === 'search') { s -= 4; }
    return s;
  }
  function before(a, b){
    try {
      return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    } catch (e) { return true; }
  }
  function norm(text){
    return String(text || '').replace(/\s+/g, '').toLowerCase();
  }
  function shown(el){
    try {
      var s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
    } catch (e) { return true; }
  }
  function looksLogin(node){
    var t = norm(node.innerText || node.value || node.textContent);
    if (!t || t.length > 12) { return false; }
    return /^(登录|登陆|login|signin|submit|提交|确定|下一步|next)$/.test(t);
  }
  function pressEnter(el){
    var opts = {bubbles: true, cancelable: true, key: 'Enter', code: 'Enter',
                keyCode: 13, which: 13};
    try { el.dispatchEvent(new KeyboardEvent('keydown', opts)); } catch (e) {}
    try { el.dispatchEvent(new KeyboardEvent('keypress', opts)); } catch (e) {}
    try { el.dispatchEvent(new KeyboardEvent('keyup', opts)); } catch (e) {}
  }
  function clickNodes(nodes, exact){
    for (var i = 0; i < nodes.length; i++){
      var node = nodes[i];
      try {
        if (!node || node.disabled) { continue; }
        if (!shown(node)) { continue; }
        var text = norm(node.innerText || node.value || node.textContent);
        if (exact) {
          if (text !== '登录' && text !== '登陆' && text !== 'login'
              && text !== 'signin') { continue; }
        } else if (!looksLogin(node)) { continue; }
        node.click();
        return true;
      } catch (e) {}
    }
    return false;
  }
  function clickLogin(found){
    // ① 真按钮 / 链接（最短路径，也不会点到包着按钮的容器）
    var real = 'button, a, input[type="submit"], input[type="button"], '
      + 'input[type="image"], div[role="button"], [role="button"]';
    var nodes = [];
    try {
      nodes = Array.prototype.slice.call(found.doc.querySelectorAll(real));
    } catch (e) { nodes = []; }
    if (clickNodes(nodes, false)) { return true; }
    // ② 按 class 猜（login / submit / btn）
    var byClass = '';
    try {
      byClass = '[class*="login"], [class*="Login"], [class*="submit"], '
        + '[class*="btn"], [id*="login"], [id*="Login"], [id*="submit"]';
      nodes = Array.prototype.slice.call(found.doc.querySelectorAll(byClass));
    } catch (e) { nodes = []; }
    if (clickNodes(nodes, false)) { return true; }
    // ③ 整棵树上找「文字就是登录」的元素（span 套在 div 里的常见写法）
    var all = [];
    try { all = Array.prototype.slice.call(found.doc.querySelectorAll('*')); }
    catch (e) { all = []; }
    if (clickNodes(all, true)) { return true; }
    // 再退一步：在密码框上按回车（很多登录表单只监听回车）
    try { found.p.focus(); } catch (e) {}
    pressEnter(found.p);
    pressEnter(found.u);
    // 最后：直接提交表单（老式页面）
    try {
      var form = found.p.form;
      if (form && form.requestSubmit) { form.requestSubmit(); return true; }
      if (form) { form.submit(); return true; }
    } catch (e) {}
    return false;
  }
  function findFields(doc){
    var all = [];
    try { all = Array.prototype.slice.call(doc.querySelectorAll('input')); }
    catch (e) { return null; }
    var passwords = all.filter(function(el){
      return (el.type || '').toLowerCase() === 'password' && editable(el);
    });
    if (!passwords.length) {
      // 有的页面密码框写成 type=text（或者 contenteditable），只能看名字
      passwords = all.filter(function(el){
        return editable(el) && /pwd|password|密码/.test(blob(el));
      });
    }
    for (var k = 0; k < passwords.length; k++) {
      var p = passwords[k];
      var scope = p.form;
      if (!scope) {
        var node = p.parentElement;
        for (var d = 0; d < 6 && node; d++) {
          try {
            if (node.querySelectorAll &&
                node.querySelectorAll('input').length > 1) { scope = node; break; }
          } catch (e) {}
          node = node.parentElement;
        }
      }
      if (!scope) { scope = doc; }
      var pool = all;
      try {
        var scoped = Array.prototype.slice.call(scope.querySelectorAll('input'));
        if (scoped.length > 1) { pool = scoped; }
      } catch (e) {}
      var cands = [];
      for (var m = 0; m < pool.length; m++) {
        var el2 = pool[m];
        if (el2 === p || !editable(el2)) { continue; }
        var type2 = (el2.type || '').toLowerCase();
        if (type2 && ['text', 'tel', 'email', 'number', 'search']
            .indexOf(type2) < 0) { continue; }
        if (!before(el2, p)) { continue; }        // 只要密码框前面的
        cands.push(el2);
      }
      if (!cands.length) { continue; }
      var best = null, bestScore = -1e9;
      for (var n = 0; n < cands.length; n++) {
        var sc = score(cands[n]);
        if (sc > bestScore) { bestScore = sc; best = cands[n]; }
      }
      if (bestScore <= 0) {
        // 都看不出是账号框：取「紧挨着密码框、在它前面」的那个
        best = cands[cands.length - 1];
      }
      if (best) { return {doc: doc, u: best, p: p, score: bestScore}; }
    }
    return null;
  }
  var found = null;
  var all = docs();
  for (var d = 0; d < all.length && !found; d++){
    found = findFields(all[d]);
  }
  if (!found) { return JSON.stringify({ok: false, reason: '没找到账号密码输入框', url: location.href}); }
  try { found.u.focus(); } catch (e) {}
  nativeSet(found.u, user);
  try { found.p.focus(); } catch (e) {}
  nativeSet(found.p, pwd);
  var userOk = (found.u.value === user);
  var pwdOk = (found.p.value === pwd);
  if (!userOk || !pwdOk){
    return JSON.stringify({ok: false, reason: '登录框没接受输入（页面可能刚刷新，正在重试）',
                           userOk: userOk, pwdOk: pwdOk, url: location.href});
  }
  var clicked = false;
  if (wantClick){
    clicked = clickLogin(found);
  }
  return JSON.stringify({ok: true, clicked: clicked, url: location.href});
})()
"""


def autofill_script(store, click: bool = True) -> str:
    """生成注入用的 JS（账号密码做 JSON 转义，安全）。"""
    data = credentials(store)
    return (AUTOFILL_JS
            .replace("__USER__", json.dumps(data["username"]))
            .replace("__PWD__", json.dumps(data["password"]))
            .replace("__CLICK__", "true" if click else "false"))


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

