"""使用 Windows DPAPI 加密本地保存的邮箱密码。

DPAPI（CryptProtectData）把密文绑定到当前 Windows 用户，
即使数据库文件被复制到别的电脑也无法解密。非 Windows 或调用失败时
退化为 base64 混淆，并在字符串前缀中标记，保证程序仍可运行。
"""
from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

_DPAPI_PREFIX = "dpapi:"
_B64_PREFIX = "b64:"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _bind_functions():
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL

    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL

    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _to_blob(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _protect_dpapi(data: bytes) -> bytes:
    crypt32, kernel32 = _bind_functions()
    input_blob, _input_buf = _to_blob(data)
    output_blob = _DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    ok = crypt32.CryptProtectData(
        ctypes.byref(input_blob), None, None, None, None, flags,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def _unprotect_dpapi(data: bytes) -> bytes:
    crypt32, kernel32 = _bind_functions()
    input_blob, _input_buf = _to_blob(data)
    output_blob = _DataBlob()
    description = wintypes.LPWSTR()
    flags = 0x1
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), ctypes.byref(description), None, None, None,
        flags, ctypes.byref(output_blob),
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))


def protect(text: str) -> str:
    """加密一个字符串，返回可直接存入数据库的文本。"""
    if not text:
        return ""
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        try:
            return _DPAPI_PREFIX + base64.b64encode(_protect_dpapi(raw)).decode("ascii")
        except Exception:
            pass
    return _B64_PREFIX + base64.b64encode(raw).decode("ascii")


def unprotect(value: str) -> str:
    """解密 protect() 的返回值；无法解密时返回空字符串。"""
    if not value:
        return ""
    try:
        if value.startswith(_DPAPI_PREFIX):
            blob = base64.b64decode(value[len(_DPAPI_PREFIX):])
            return _unprotect_dpapi(blob).decode("utf-8")
        if value.startswith(_B64_PREFIX):
            blob = base64.b64decode(value[len(_B64_PREFIX):])
            return blob.decode("utf-8")
        return value  # 兼容旧的明文数据
    except Exception:
        return ""


def is_protected(value: str) -> bool:
    return bool(value) and value.startswith((_DPAPI_PREFIX, _B64_PREFIX))
