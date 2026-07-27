from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def default_data_dir(platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    if platform_name == "win32":
        root = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return root / "Dendritron Foundry Lite" / "Data"
    return Path("~/.difoundry-lite").expanduser()


@dataclass(slots=True)
class LiteSettings:
    data_dir: Path
    database_path: Path
    key_path: Path
    host: str = "127.0.0.1"
    port: int = 8765
    allow_lan: bool = False
    request_timeout_seconds: float = 12.0
    max_probe_endpoints: int = 24
    open_browser: bool = True
    backup_dir: Path | None = None
    log_dir: Path | None = None
    backup_retention: int = 7
    desktop_mode: bool = False

    @classmethod
    def from_env(cls) -> "LiteSettings":
        data_dir = Path(os.getenv("DIFOUNDRY_LITE_DATA_DIR", str(default_data_dir()))).expanduser().resolve()
        return cls(
            data_dir=data_dir,
            database_path=Path(os.getenv("DIFOUNDRY_LITE_DATABASE", str(data_dir / "foundry-lite.sqlite3"))).expanduser().resolve(),
            key_path=Path(os.getenv("DIFOUNDRY_LITE_KEY_PATH", str(data_dir / "local-vault.key"))).expanduser().resolve(),
            host=os.getenv("DIFOUNDRY_LITE_HOST", "127.0.0.1"),
            port=int(os.getenv("DIFOUNDRY_LITE_PORT", "8765")),
            allow_lan=os.getenv("DIFOUNDRY_LITE_ALLOW_LAN", "false").lower() in {"1", "true", "yes"},
            request_timeout_seconds=float(os.getenv("DIFOUNDRY_LITE_DISCOVERY_TIMEOUT", "12")),
            max_probe_endpoints=int(os.getenv("DIFOUNDRY_LITE_MAX_PROBES", "24")),
            open_browser=os.getenv("DIFOUNDRY_LITE_OPEN_BROWSER", "true").lower() in {"1", "true", "yes"},
            backup_dir=Path(os.getenv("DIFOUNDRY_LITE_BACKUP_DIR", str(data_dir / "Backups"))).expanduser().resolve(),
            log_dir=Path(os.getenv("DIFOUNDRY_LITE_LOG_DIR", str(data_dir / "Logs"))).expanduser().resolve(),
            backup_retention=max(1, int(os.getenv("DIFOUNDRY_LITE_BACKUP_RETENTION", "7"))),
            desktop_mode=os.getenv("DIFOUNDRY_LITE_DESKTOP", "false").lower() in {"1", "true", "yes"},
        )

    def ensure(self) -> None:
        self.backup_dir = self.backup_dir or (self.data_dir / "Backups")
        self.log_dir = self.log_dir or (self.data_dir / "Logs")
        for directory in (self.data_dir, self.backup_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)
            try:
                directory.chmod(0o700)
            except OSError:
                pass
