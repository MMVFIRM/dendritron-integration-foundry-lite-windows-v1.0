from __future__ import annotations

import os
import sys
from pathlib import Path

APP_RUN_NAME = "Dendritron Foundry Lite"


def executable_command(background: bool = True) -> str:
    executable = Path(sys.executable).resolve()
    suffix = " --background" if background else ""
    if getattr(sys, "frozen", False):
        return f'"{executable}"{suffix}'
    return f'"{executable}" -m difoundry.lite.desktop{suffix}'


def startup_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            value, _kind = winreg.QueryValueEx(key, APP_RUN_NAME)
        return bool(value)
    except FileNotFoundError:
        return False


def set_startup_enabled(enabled: bool) -> bool:
    if sys.platform != "win32":
        raise RuntimeError("Start-at-sign-in is available in the Windows desktop edition")
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            winreg.SetValueEx(key, APP_RUN_NAME, 0, winreg.REG_SZ, executable_command(True))
        else:
            try:
                winreg.DeleteValue(key, APP_RUN_NAME)
            except FileNotFoundError:
                pass
    return startup_enabled()


def installed_mode() -> bool:
    return bool(getattr(sys, "frozen", False)) or os.getenv("DIFOUNDRY_LITE_DESKTOP") == "true"
