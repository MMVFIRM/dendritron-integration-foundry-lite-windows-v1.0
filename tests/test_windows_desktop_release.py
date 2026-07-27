from __future__ import annotations

import json
import os
import socket
import zipfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from difoundry.lite.api import create_lite_app
from difoundry.lite.desktop import build_parser, main as desktop_main
from difoundry.lite.key_protection import load_or_create_vault_key
from difoundry.lite.service import LiteContext
from difoundry.lite.settings import LiteSettings, default_data_dir


def make_context(tmp_path: Path) -> LiteContext:
    settings = LiteSettings(
        data_dir=tmp_path,
        database_path=tmp_path / "lite.sqlite3",
        key_path=tmp_path / "vault.key",
        backup_dir=tmp_path / "Backups",
        log_dir=tmp_path / "Logs",
        open_browser=False,
    )
    return LiteContext.build(settings)


def session_client(context: LiteContext) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(create_lite_app(context))
    client.get("/console")
    token = client.cookies.get("foundry_lite_session")
    assert token
    return client, {"X-Foundry-Lite-Session": token}


def test_windows_default_data_dir_uses_local_app_data(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_data_dir("win32") == tmp_path / "Dendritron Foundry Lite" / "Data"


def test_dpapi_envelope_creation_and_legacy_migration(tmp_path: Path):
    path = tmp_path / "vault.key"
    protected: dict[bytes, bytes] = {}

    def protect(value: bytes) -> bytes:
        output = b"protected:" + value
        protected[output] = value
        return output

    def unprotect(value: bytes) -> bytes:
        return protected[value]

    first = load_or_create_vault_key(path, platform_name="win32", protect=protect, unprotect=unprotect)
    assert len(first) == 32
    assert path.read_text().startswith("DPAPI1:")
    assert load_or_create_vault_key(path, platform_name="win32", protect=protect, unprotect=unprotect) == first

    legacy = tmp_path / "legacy.key"
    import base64
    legacy.write_text(base64.urlsafe_b64encode(b"x" * 32).decode())
    assert load_or_create_vault_key(legacy, platform_name="win32", protect=protect, unprotect=unprotect) == b"x" * 32
    assert legacy.read_text().startswith("DPAPI1:")


def test_database_integrity_and_manual_backup(tmp_path: Path):
    context = make_context(tmp_path)
    assert context.database.integrity_check()["valid"] is True
    backup = context.service.create_backup("test")
    assert backup.exists()
    assert backup.parent == tmp_path / "Backups"


def test_support_bundle_is_redacted(tmp_path: Path):
    context = make_context(tmp_path)
    context.service.vault.put({"api_key": "top-secret"}, "known-secret")
    context.service._activity(None, "run", "failed", "failure", {"payload": {"email": "private@example.com"}, "authorization": "Bearer abc"})
    bundle = context.service.create_support_bundle()
    with zipfile.ZipFile(bundle) as archive:
        payload = archive.read("support.json").decode()
    assert "top-secret" not in payload
    assert "Bearer abc" not in payload
    assert "private@example.com" not in payload
    assert "[REDACTED]" in payload


def test_desktop_api_backup_status_and_support(tmp_path: Path):
    context = make_context(tmp_path)
    client, headers = session_client(context)
    status = client.get("/lite/desktop", headers=headers)
    assert status.status_code == 200
    assert status.json()["database_integrity"]["valid"] is True
    backup = client.post("/lite/desktop/backup", headers=headers)
    assert backup.status_code == 200
    support = client.get("/lite/desktop/support", headers=headers)
    assert support.status_code == 200
    assert support.headers["content-type"].startswith("application/zip")


def test_desktop_parser_exposes_operational_commands():
    parser = build_parser()
    assert parser.parse_args(["--background"]).background is True
    assert parser.parse_args(["--health-check"]).health_check is True
    assert parser.parse_args(["--stop"]).stop is True


def test_desktop_health_check_runs_real_local_server(monkeypatch, tmp_path: Path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("DIFOUNDRY_LITE_DATA_DIR", str(tmp_path / "Data"))
    monkeypatch.setenv("DIFOUNDRY_LITE_PORT", str(port))
    monkeypatch.setenv("DIFOUNDRY_LITE_OPEN_BROWSER", "false")
    assert desktop_main(["--health-check"]) == 0


def test_common_system_catalog_is_preconfigured(tmp_path: Path):
    context = make_context(tmp_path)
    names = {item["name"] for item in context.catalog.search("")}
    assert {"HubSpot", "Slack", "Stripe", "GitHub", "Microsoft 365 / Graph", "QuickBooks Online"} <= names


def test_windows_packaging_files_encode_safe_one_click_defaults():
    root = Path(__file__).resolve().parents[1]
    installer = (root / "packaging/windows/foundry-lite.iss").read_text()
    assert "PrivilegesRequired=lowest" in installer
    assert "{localappdata}\\Programs\\Dendritron Foundry Lite" in installer
    assert "Start Foundry Lite when I sign in to Windows" in installer
    assert "--background" in installer
    assert "SetupMutex=" in installer
    assert "CloseApplications=force" in installer
    assert "InitializeUninstall" in installer
    assert "skipifsilent" in installer
    assert "DIFOUNDRY_LITE_ALLOW_LAN" not in installer

    spec = (root / "packaging/windows/foundry-lite.spec").read_text()
    compile(spec, "foundry-lite.spec", "exec")
    assert 'console=False' in spec
    assert 'name="FoundryLite"' in spec
    assert '"rfc3987_syntax"' in spec

    desktop = (root / "src/difoundry/lite/desktop.py").read_text()
    assert "log_config=None" in desktop

    workflow = (root / ".github/workflows/windows-installer.yml").read_text()
    assert "runs-on: windows-latest" in workflow
    assert "actions/attest-build-provenance@v2" in workflow
    assert "WINDOWS_CERTIFICATE_BASE64" in workflow


def test_build_script_signs_executable_before_installer_compilation():
    root = Path(__file__).resolve().parents[1]
    script = (root / "packaging/windows/build.ps1").read_text()
    sign_position = script.index('Sign-File ".\\dist\\FoundryLite\\FoundryLite.exe"')
    compile_position = script.index("foundry-lite.iss")
    assert sign_position < compile_position
    assert "--health-check" in script
    assert "foundry-lite-sbom.json" in script
    assert "SHA256SUMS.txt" in script


def test_ui_includes_first_run_and_recovery_surfaces(tmp_path: Path):
    context = make_context(tmp_path)
    client = TestClient(create_lite_app(context))
    html = client.get("/console").text
    assert "Welcome to Foundry Lite" in html
    assert "Start Foundry Lite when I sign in to Windows" in html
    assert "Create backup now" in html
    assert "Download support bundle" in html
    assert "system-catalog" in html
