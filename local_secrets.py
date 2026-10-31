"""Atomic local JSON storage; Windows secrets are bound to the current user.

DPAPI never sends data over the network. A decryption error must not be treated
as an empty configuration: doing so could overwrite the user's saved accounts.
"""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile

PREFIX = "win-dpapi:v1:"
SECRET_KEYS = frozenset({"api_token", "cf_publish_secret", "publish_secret",
                         "secret", "access_password", "password"})


class SecretStoreError(ValueError):
    pass


def _dpapi(data: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("此配置使用 Windows 账户加密，请在原 Windows 账户中打开，或重新配置密钥。")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), "Data summary local secrets", None, None, None, 1, ctypes.byref(result))
    function.restype = wintypes.BOOL
    if not function(*args):
        raise SecretStoreError("无法访问 Windows 加密配置，请使用原 Windows 账户，或重新填写密钥；原配置未覆盖。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def protect_tree(value, key=""):
    if isinstance(value, dict):
        return {k: protect_tree(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [protect_tree(v, key) for v in value]
    if key in SECRET_KEYS and isinstance(value, str) and value:
        if value.startswith(PREFIX):
            raise SecretStoreError("请先解密本机配置再保存，防止误保存无法读取的密钥。")
        if os.name == "nt":
            return PREFIX + base64.b64encode(_dpapi(value.encode("utf-8"))).decode("ascii")
    return value


def unprotect_tree(value):
    if isinstance(value, dict):
        return {k: unprotect_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unprotect_tree(v) for v in value]
    if isinstance(value, str) and value.startswith(PREFIX):
        try:
            return _dpapi(base64.b64decode(value[len(PREFIX):], validate=True), decrypt=True).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise SecretStoreError("本机加密配置无法读取，原文件已保留，请重新配置密钥。") from exc
    return value


def atomic_json_write(path: Path, value, *, encrypt=True):
    path = Path(path)
    serialized = json.dumps(protect_tree(value) if encrypt else value, ensure_ascii=False, indent=2)
    descriptor, temporary = tempfile.mkstemp(prefix=".private-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_private_json(path: Path, *, migrate=True):
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    decoded = unprotect_tree(raw)
    if migrate and os.name == "nt":
        def has_plaintext(value):
            if isinstance(value, dict):
                return any((k in SECRET_KEYS and isinstance(v, str) and v and not v.startswith(PREFIX))
                           or has_plaintext(v) for k, v in value.items())
            return isinstance(value, list) and any(has_plaintext(v) for v in value)
        if has_plaintext(raw):
            atomic_json_write(path, decoded)
    return decoded
