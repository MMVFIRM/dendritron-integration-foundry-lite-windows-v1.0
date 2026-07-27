from __future__ import annotations

import base64
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Callable

_DPAPI_PREFIX = "DPAPI1:"
_RAW_PREFIX = "RAW1:"
_ENTROPY = b"Dendritron Foundry Lite vault key v1"


class KeyProtectionError(RuntimeError):
    pass


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _as_blob(value: bytes) -> tuple[_DATA_BLOB, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    return _DATA_BLOB(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _windows_protect(value: bytes) -> bytes:
    if sys.platform != "win32":
        raise KeyProtectionError("Windows DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob, input_buffer = _as_blob(value)
    entropy_blob, entropy_buffer = _as_blob(_ENTROPY)
    output_blob = _DATA_BLOB()
    # CRYPTPROTECT_UI_FORBIDDEN: never show an unexpected credential dialog.
    ok = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Dendritron Foundry Lite",
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    )
    _ = input_buffer, entropy_buffer
    if not ok:
        raise KeyProtectionError(f"CryptProtectData failed with Windows error {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _windows_unprotect(value: bytes) -> bytes:
    if sys.platform != "win32":
        raise KeyProtectionError("Windows DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob, input_buffer = _as_blob(value)
    entropy_blob, entropy_buffer = _as_blob(_ENTROPY)
    output_blob = _DATA_BLOB()
    description = wintypes.LPWSTR()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        ctypes.byref(description),
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    )
    _ = input_buffer, entropy_buffer
    if not ok:
        raise KeyProtectionError(
            "The local vault key could not be unlocked for this Windows user. "
            "Restore it under the same Windows account or reconnect the systems."
        )
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if description:
            kernel32.LocalFree(description)
        kernel32.LocalFree(output_blob.pbData)


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="ascii")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_or_create_vault_key(
    path: Path,
    *,
    platform_name: str | None = None,
    protect: Callable[[bytes], bytes] | None = None,
    unprotect: Callable[[bytes], bytes] | None = None,
) -> bytes:
    """Load the 32-byte vault key, using per-user DPAPI protection on Windows.

    Legacy v0.1 files contained raw URL-safe base64. They are migrated atomically to
    DPAPI on Windows and to an explicit RAW1 envelope elsewhere.
    """

    platform_name = platform_name or sys.platform
    protect = protect or _windows_protect
    unprotect = unprotect or _windows_unprotect

    if path.exists():
        encoded = path.read_text(encoding="ascii").strip()
        try:
            if encoded.startswith(_DPAPI_PREFIX):
                key = unprotect(base64.urlsafe_b64decode(encoded[len(_DPAPI_PREFIX) :]))
            elif encoded.startswith(_RAW_PREFIX):
                key = base64.urlsafe_b64decode(encoded[len(_RAW_PREFIX) :])
            else:  # v0.1 legacy raw-base64 key
                key = base64.urlsafe_b64decode(encoded)
        except Exception as exc:
            if isinstance(exc, KeyProtectionError):
                raise
            raise KeyProtectionError("Foundry Lite vault key is unreadable or corrupted") from exc
        if len(key) != 32:
            raise KeyProtectionError("Foundry Lite vault key must be exactly 32 bytes")
        if platform_name == "win32" and not encoded.startswith(_DPAPI_PREFIX):
            _write_private(path, _DPAPI_PREFIX + base64.urlsafe_b64encode(protect(key)).decode("ascii"))
        elif platform_name != "win32" and not encoded.startswith(_RAW_PREFIX):
            _write_private(path, _RAW_PREFIX + base64.urlsafe_b64encode(key).decode("ascii"))
        return key

    key = os.urandom(32)
    if platform_name == "win32":
        content = _DPAPI_PREFIX + base64.urlsafe_b64encode(protect(key)).decode("ascii")
    else:
        content = _RAW_PREFIX + base64.urlsafe_b64encode(key).decode("ascii")
    _write_private(path, content)
    return key
