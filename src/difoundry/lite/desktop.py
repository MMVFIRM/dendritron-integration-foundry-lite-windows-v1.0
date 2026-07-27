from __future__ import annotations

import argparse
import ctypes
import logging
import os
import signal
import socket
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import FrameType
from typing import Any

import httpx
import uvicorn

from .api import create_lite_app
from .service import LiteContext
from .settings import LiteSettings

LOGGER = logging.getLogger("difoundry.lite.desktop")
APP_MUTEX_NAME = "Local\\DendritronFoundryLite-5DBE87E1-24C4-49BA-A7B9-9C5BA72DA132"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="FoundryLite", description="Dendritron Foundry Lite desktop application")
    parser.add_argument("--background", action="store_true", help="Start in the system tray without opening the browser")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser after startup")
    parser.add_argument("--stop", action="store_true", help="Stop the running desktop application")
    parser.add_argument("--status", action="store_true", help="Return success when the local application is running")
    parser.add_argument("--health-check", action="store_true", help="Start, verify, and stop a temporary desktop runtime")
    return parser


def configure_logging(settings: LiteSettings) -> Path:
    settings.ensure()
    assert settings.log_dir is not None
    path = settings.log_dir / "foundry-lite.log"
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=4, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(item, RotatingFileHandler) and getattr(item, "baseFilename", "") == str(path) for item in root.handlers):
        root.addHandler(handler)
    return path


def _message_box(title: str, message: str, *, error: bool = False) -> None:
    if sys.platform == "win32":
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)
    else:
        print(f"{title}: {message}", file=sys.stderr if error else sys.stdout)


class SingleInstance:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.handle: Any = None
        self.file: Any = None

    def acquire(self) -> bool:
        if sys.platform == "win32":
            self.handle = ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
            if not self.handle:
                raise OSError("Unable to create the Foundry Lite single-instance mutex")
            return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
        self.data_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.data_dir / "desktop.lock"
        self.file = lock_path.open("a+")
        try:
            import fcntl

            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (ImportError, BlockingIOError):
            return False

    def release(self) -> None:
        if self.handle and sys.platform == "win32":
            ctypes.windll.kernel32.ReleaseMutex(self.handle)
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None
        if self.file:
            try:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            self.file.close()
            self.file = None



def _port_file(settings: LiteSettings) -> Path:
    return settings.data_dir / "desktop-port.txt"


def apply_saved_port(settings: LiteSettings) -> None:
    if os.getenv("DIFOUNDRY_LITE_PORT"):
        return
    path = _port_file(settings)
    if path.exists():
        try:
            value = int(path.read_text(encoding="ascii").strip())
            if 1024 <= value <= 65535:
                settings.port = value
        except (OSError, ValueError):
            pass


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


def select_desktop_port(settings: LiteSettings) -> int:
    """Keep the previous port when possible, otherwise select a nearby free loopback port."""
    apply_saved_port(settings)
    if running_status(settings):
        return settings.port
    if _port_available(settings.host, settings.port):
        chosen = settings.port
    elif os.getenv("DIFOUNDRY_LITE_PORT"):
        raise RuntimeError(f"Configured port {settings.port} is already in use")
    else:
        chosen = next((port for port in range(8765, 8786) if _port_available(settings.host, port)), 0)
        if not chosen:
            raise RuntimeError("No free local port was found between 8765 and 8785")
        settings.port = chosen
    _port_file(settings).write_text(str(chosen), encoding="ascii")
    return chosen

def _base_url(settings: LiteSettings) -> str:
    host = "127.0.0.1" if settings.host in {"localhost", "::1"} else settings.host
    return f"http://{host}:{settings.port}"


def running_status(settings: LiteSettings, timeout: float = 1.0) -> bool:
    try:
        response = httpx.get(_base_url(settings) + "/lite/liveness", timeout=timeout)
        return response.status_code == 200 and response.json().get("edition") == "lite"
    except Exception:
        return False


def open_console(settings: LiteSettings) -> None:
    webbrowser.open(_base_url(settings) + "/console")


def stop_running(settings: LiteSettings) -> bool:
    try:
        with httpx.Client(base_url=_base_url(settings), timeout=3.0) as client:
            console = client.get("/console")
            if console.status_code != 200:
                return False
            token = client.cookies.get("foundry_lite_session")
            response = client.post("/lite/desktop/shutdown", headers={"X-Foundry-Lite-Session": token or ""})
            return response.status_code in {200, 202}
    except Exception:
        return False


class DesktopRuntime:
    def __init__(self, settings: LiteSettings):
        self.settings = settings
        self.context = LiteContext.build(settings)
        self.server: uvicorn.Server | None = None
        self.server_thread: threading.Thread | None = None
        self.tray_icon: Any = None
        self.stopped = threading.Event()
        self.app = create_lite_app(self.context, shutdown_callback=self.request_shutdown)

    def start(self) -> None:
        config = uvicorn.Config(
            self.app,
            host=self.settings.host,
            port=self.settings.port,
            log_level="warning",
            log_config=None,
            proxy_headers=False,
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.server.install_signal_handlers = lambda: None  # desktop owns process signals
        self.server_thread = threading.Thread(target=self.server.run, name="foundry-lite-http", daemon=True)
        self.server_thread.start()
        if not self.wait_ready(20):
            self.request_shutdown()
            raise RuntimeError(f"Foundry Lite could not start on {_base_url(self.settings)}")

    def wait_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if running_status(self.settings, timeout=0.4):
                return True
            if self.server_thread and not self.server_thread.is_alive():
                return False
            time.sleep(0.1)
        return False

    def request_shutdown(self) -> None:
        if self.stopped.is_set():
            return
        self.stopped.set()
        if self.server:
            self.server.should_exit = True
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                LOGGER.exception("Unable to stop tray icon cleanly")

    def wait_for_exit(self) -> None:
        while self.server_thread and self.server_thread.is_alive() and not self.stopped.wait(0.25):
            pass
        if self.server_thread:
            self.server_thread.join(timeout=15)

    def run_tray(self) -> None:
        try:
            import pystray
            from PIL import Image
            from importlib.resources import files

            image = Image.open(str(files("difoundry.lite").joinpath("static", "app-icon.png")))
            menu = pystray.Menu(
                pystray.MenuItem("Open Foundry Lite", lambda _icon, _item: open_console(self.settings), default=True),
                pystray.MenuItem("Create backup", lambda _icon, _item: self.context.service.create_backup("tray")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", lambda _icon, _item: self.request_shutdown()),
            )
            self.tray_icon = pystray.Icon("DendritronFoundryLite", image, "Dendritron Foundry Lite", menu)
            self.tray_icon.run()
        except ImportError as exc:
            LOGGER.error("System tray dependency is unavailable: %s", exc)
            self.wait_for_exit()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = LiteSettings.from_env()
    settings.desktop_mode = True
    settings.open_browser = False
    settings.ensure()
    apply_saved_port(settings)
    configure_logging(settings)

    if settings.host not in {"127.0.0.1", "localhost", "::1"} and not settings.allow_lan:
        _message_box("Foundry Lite", "Refusing a non-local network bind. Enable LAN mode explicitly.", error=True)
        return 2
    if args.status:
        return 0 if running_status(settings) else 1
    if args.stop:
        return 0 if stop_running(settings) else 1

    instance = SingleInstance(settings.data_dir)
    if not instance.acquire():
        if running_status(settings):
            if not args.background and not args.no_browser:
                open_console(settings)
            return 0
        _message_box(
            "Foundry Lite",
            "Another Foundry Lite process is starting. Try again in a few seconds.",
            error=True,
        )
        return 1

    select_desktop_port(settings)
    runtime: DesktopRuntime | None = None

    def shutdown_handler(_signum: int, _frame: FrameType | None) -> None:
        if runtime:
            runtime.request_shutdown()

    try:
        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)
        runtime = DesktopRuntime(settings)
        runtime.start()
        LOGGER.info("Foundry Lite desktop started at %s", _base_url(settings))
        if args.health_check:
            runtime.request_shutdown()
            runtime.wait_for_exit()
            return 0
        if not args.background and not args.no_browser:
            open_console(settings)
        runtime.run_tray()
        runtime.request_shutdown()
        runtime.wait_for_exit()
        return 0
    except Exception as exc:
        LOGGER.exception("Foundry Lite desktop failed")
        _message_box("Foundry Lite could not start", str(exc), error=True)
        if runtime:
            runtime.request_shutdown()
            runtime.wait_for_exit()
        return 1
    finally:
        instance.release()


if __name__ == "__main__":
    raise SystemExit(main())
