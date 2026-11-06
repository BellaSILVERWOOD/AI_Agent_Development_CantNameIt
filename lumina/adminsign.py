"""管理员密钥：注册申请「加密」+ 批准回执「签名」。

为什么要公私钥，而不是对称密码：

* 注册申请里带着用户名、邮箱、设备指纹，会**经过邮件服务器**，不能用明文；
  用「管理员公钥」加密（RSA-2048 + OAEP 包裹一把随机的 AES-256 密钥，
  正文用 AES-GCM 加密），只有拿着私钥的管理员能解开；申请人自己也不需要
  提前跟管理员共享任何密码；
* 批准回执要能离线验真（申请人那台机器连不到管理员的数据库），所以用
  「管理员私钥」签名（RSA-2048 + PSS-SHA256），把公钥随分发包带出去，
  申请人本机用公钥验签即可，改一个字节都验不过；
* 私钥只留在这台机器上，并且用 Windows DPAPI 再包一层（`secure.protect`），
  拷走 `.pem` 文件在别的 Windows 账户下也解不开。
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import secure

SEAL_PREFIX = "LUMINA-SEAL1."
RECEIPT_PREFIX = "LUMINA-RECEIPT1."
KEY_BITS = 2048
SEAL_KEY = "admin_private_key"      # DPAPI 加密后存在 settings 里


class SignError(Exception):
    pass


# ------------------------------------------------------------------ 路径
def _data_dir() -> Path:
    from .config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _bundle_dir() -> Optional[Path]:
    """单文件 exe（--onefile）解包出来的临时目录：`sys._MEIPASS`。"""
    import sys

    base = getattr(sys, "_MEIPASS", "")
    return Path(base) if base else None


def resource_candidates(name: str) -> list:
    """随包文件可能在哪：程序根目录 → 单文件解包目录 → data 目录。"""
    from .config import APP_ROOT, DATA_DIR

    found = [APP_ROOT / name]
    bundle = _bundle_dir()
    if bundle is not None:
        found.append(bundle / name)
    found.append(DATA_DIR / name)
    return found


def private_pem_file() -> Path:
    return _data_dir() / "admin_key.pem"


def public_pem_file() -> Path:
    return _data_dir() / "admin_pub.pem"


def bundled_public_pem_file() -> Path:
    """随程序走的公钥（给别人用的那份，只有公钥，没有风险）。"""
    for path in resource_candidates("admin_pub.pem"):
        if path.exists():
            return path
    return resource_candidates("admin_pub.pem")[0]


# ------------------------------------------------------------------ 生成 / 读取
def generate_keypair(store) -> str:
    """生成密钥对：私钥用 DPAPI 包起来存 settings，公钥写成文件。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    store.set_secret(SEAL_KEY, private_pem)
    try:
        private_pem_file().write_text(private_pem, encoding="ascii")
    except Exception:
        pass
    save_public(public_pem)
    return public_pem


def save_public(public_pem: str) -> None:
    try:
        public_pem_file().write_text(public_pem, encoding="ascii")
    except Exception:
        pass
    if _isolated_data_dir():
        return          # 测试 / 探针：只写临时目录，不动程序目录里那份随包公钥
    try:
        bundled_public_pem_file().write_text(public_pem, encoding="ascii")
    except Exception:
        pass


def _isolated_data_dir() -> bool:
    """这次运行用的是不是「临时数据目录」（测试 / 探针）。

    `LUMINA_DATA_DIR` 被显式指到别处时（测试、探针都这么干），就不要去改
    程序目录里那份**随包发的公钥** —— 否则跑一遍测试就把仓库里的
    `admin_pub.pem` 换成了测试用的新密钥，真实客户端会用错公钥加密，
    管理员的私钥再也解不开（这个坑真踩过：`_lumina/admin_pub.pem` 被测试改掉）。
    """
    import os
    from .config import DATA_DIR

    env = str(os.environ.get("LUMINA_DATA_DIR") or "").strip()
    if not env:
        return False
    try:
        return Path(env).resolve() == Path(DATA_DIR).resolve()
    except Exception:
        return False


def has_private_key(store) -> bool:
    try:
        return bool(store.get_secret(SEAL_KEY, ""))
    except Exception:
        return False


def private_key(store):
    pem = store.get_secret(SEAL_KEY, "")
    if not pem:
        path = private_pem_file()
        try:
            if path.exists():
                pem = path.read_text(encoding="ascii").strip()
                store.set_secret(SEAL_KEY, pem)
        except Exception:
            pem = ""
    if not pem:
        raise SignError("这台机器上还没有管理员密钥（先到「账户审核」里生成）")
    try:
        return serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    except Exception as exc:  # noqa: BLE001
        raise SignError(f"管理员密钥读不出来：{exc}") from exc


def public_pem(store) -> str:
    if has_private_key(store):
        try:
            key = private_key(store)
            return key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("ascii")
        except Exception:
            pass
    for path in (public_pem_file(), bundled_public_pem_file()):
        try:
            if path.exists():
                return path.read_text(encoding="ascii").strip()
        except Exception:
            continue
    return ""


def public_fingerprint(store) -> str:
    """公钥指纹（SHA-256 前 16 位，用于人眼核对）。"""
    pem = public_pem(store)
    if not pem:
        return ""
    digest = hashes.Hash(hashes.SHA256())
    digest.update(pem.encode("ascii"))
    return digest.finalize().hex()[:16]


# ------------------------------------------------------------------ 加密（申请人 → 管理员）
def seal(payload: Dict[str, Any], public_pem_text: str) -> str:
    """用管理员公钥加密一份 JSON，返回可放进邮件正文的一行文本。"""
    try:
        pub = serialization.load_pem_public_key(public_pem_text.encode("ascii"))
    except Exception as exc:  # noqa: BLE001
        raise SignError(f"公钥读不出来：{exc}") from exc
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    aes_key = os.urandom(32)
    nonce = os.urandom(12)
    cipher = AESGCM(aes_key).encrypt(nonce, raw, None)
    wrapped = pub.encrypt(
        aes_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()),
                     algorithm=hashes.SHA1(), label=None),
    )
    box = {"n": base64.b64encode(nonce).decode("ascii"),
           "c": base64.b64encode(cipher).decode("ascii"),
           "k": base64.b64encode(wrapped).decode("ascii")}
    return SEAL_PREFIX + base64.urlsafe_b64encode(
        json.dumps(box).encode("utf-8")).decode("ascii")


def unseal(store, blob: str) -> Dict[str, Any]:
    text = str(blob or "").strip()
    if not text.startswith(SEAL_PREFIX):
        raise SignError("这不是注册申请（缺少 LUMINA-SEAL1 标记）")
    try:
        box = json.loads(base64.urlsafe_b64decode(
            text[len(SEAL_PREFIX):].encode("ascii")).decode("utf-8"))
        key = private_key(store)
        aes_key = key.decrypt(
            base64.b64decode(box["k"]),
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()),
                         algorithm=hashes.SHA1(), label=None),
        )
        raw = AESGCM(aes_key).decrypt(base64.b64decode(box["n"]),
                                      base64.b64decode(box["c"]), None)
        return json.loads(raw.decode("utf-8"))
    except SignError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SignError(f"解不开这份申请：{exc}（可能不是用你这台机器的公钥加密的）") from exc


def looks_like_seal(text: str) -> bool:
    return SEAL_PREFIX in str(text or "")


CLIENT_MODE_KEY = "install_mode"      # "client" = 客户端版（必须走注册审批）
CLIENT_MARKER = "client_only.flag"    # 打包时放一个空文件，效果同上


def install_is_client(store) -> bool:
    """这份安装包是不是「客户端版」：只有公钥、必须走注册审批。

    * 打包给别人时，在程序根目录放一个空文件 `client_only.flag`（或者把设置
      `install_mode` 设成 `client`）——这样对方既不能自己新建本地账户，
      也不会看到「账户审核」；
    * 管理员自己这份没有这个标记，照常建账户 / 审批。
    """
    try:
        if (store.get_setting(CLIENT_MODE_KEY, "") or "") == "client":
            return True
    except Exception:
        pass
    try:
        for path in resource_candidates(CLIENT_MARKER):
            if path.exists():
                return True
    except Exception:
        pass
    return False


_SEAL_BODY_RE = re.compile(r"[A-Za-z0-9\-_=]+")


def extract_seal(text: str) -> str:
    """从邮件正文里把那一行密文抠出来。

    要宽容一点：邮件客户端经常把超长的 base64 **折成多行**（甚至刚好折在
    `LUMINA-` 和 `SEAL1.` 中间），所以这里先把整体空白压掉再找前缀，
    然后只取 base64 合法字符，最后拼回一整行。
    """
    raw = str(text or "")
    if not raw:
        return ""
    best = ""
    # ① 原样按行找
    for line in raw.replace("\r", "").split("\n"):
        line = line.strip()
        if line.startswith(SEAL_PREFIX):
            best = line
            break
    # ② 折行了：压掉所有空白再找前缀，往后取合法的 base64 字符。
    #    注意要跟 ① 比长度 —— 折行的第一行也「以 LUMINA-SEAL1. 开头」，
    #    但只有压平后的那一段才是完整的密文。
    flat = re.sub(r"\s+", "", raw)
    index = flat.find(SEAL_PREFIX)
    if index >= 0:
        rest = flat[index + len(SEAL_PREFIX):]
        match = _SEAL_BODY_RE.match(rest)
        if match:
            candidate = SEAL_PREFIX + match.group(0)
            if len(candidate) > len(best):
                best = candidate
    return best


# ------------------------------------------------------------------ 签名（管理员 → 申请人）
def sign_receipt(store, payload: Dict[str, Any]) -> str:
    """给批准回执签名，返回 `LUMINA-RECEIPT1.<正文>.<签名>`。"""
    key = private_key(store)
    body = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).decode("ascii")
    signature = key.sign(
        body.encode("ascii"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return RECEIPT_PREFIX + body + "." + base64.urlsafe_b64encode(
        signature).decode("ascii")


def verify_receipt(token: str, public_pem_text: str) -> Dict[str, Any]:
    """用管理员公钥验证回执；验不过就抛 SignError。"""
    text = str(token or "").strip()
    if not text.startswith(RECEIPT_PREFIX):
        raise SignError("这不是批准回执（缺少 LUMINA-RECEIPT1 标记）")
    rest = text[len(RECEIPT_PREFIX):]
    if "." not in rest:
        raise SignError("回执格式不对（少了签名）")
    body, signature = rest.rsplit(".", 1)
    try:
        pub = serialization.load_pem_public_key(public_pem_text.encode("ascii"))
    except Exception as exc:  # noqa: BLE001
        raise SignError(f"公钥读不出来：{exc}") from exc
    try:
        pub.verify(
            base64.urlsafe_b64decode(signature.encode("ascii")),
            body.encode("ascii"),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
    except Exception as exc:  # noqa: BLE001
        raise SignError("回执签名不对（可能被改过，或者不是这位管理员签发的）") from exc
    try:
        return json.loads(base64.urlsafe_b64decode(body.encode("ascii")).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SignError(f"回执内容读不出来：{exc}") from exc


def extract_receipt(text: str) -> str:
    """从邮件正文里抠出回执（同样容忍折行）。"""
    raw = str(text or "")
    if not raw:
        return ""
    best = ""
    for line in raw.replace("\r", "").split("\n"):
        line = line.strip()
        if line.startswith(RECEIPT_PREFIX):
            best = line
            break
    flat = re.sub(r"\s+", "", raw)
    index = flat.find(RECEIPT_PREFIX)
    if index < 0:
        return best
    rest = flat[index + len(RECEIPT_PREFIX):]
    # 回执是 `<body>.<signature>`，两段都是 urlsafe base64
    match = re.match(r"[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+", rest)
    candidate = ""
    if match:
        candidate = RECEIPT_PREFIX + match.group(0)
    else:
        match = _SEAL_BODY_RE.match(rest)
        if match:
            candidate = RECEIPT_PREFIX + match.group(0)
    return candidate if len(candidate) > len(best) else best
