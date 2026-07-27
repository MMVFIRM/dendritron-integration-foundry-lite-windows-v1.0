# Windows release process

## Required environment

- Windows 10 version 1809 or later
- Python 3.11
- Inno Setup 6
- Optional trusted Authenticode PFX certificate

## Build

```powershell
.\packaging\windows\build.ps1 -Version 1.0.0
```

The script creates an isolated virtual environment, applies pinned dependency constraints, runs the tests, builds the PyInstaller directory, executes the packaged application's health check, optionally signs it, builds the portable ZIP and installer, optionally signs the installer, and emits an SBOM plus checksums.

## Code-signing secrets

GitHub Actions expects:

- `WINDOWS_CERTIFICATE_BASE64`
- `WINDOWS_CERTIFICATE_PASSWORD`

When these are absent, the workflow can build an unsigned internal-test artifact. An unsigned installer should not be represented as a public production release.

## Release gates

A distributable installer must pass:

1. Full Python suite on Windows.
2. Packaged executable `--health-check`.
3. Fresh-install launch test.
4. Upgrade over a populated prior workspace.
5. Start-at-sign-in test.
6. Stop/uninstall test while the tray app is running.
7. Signature verification when signing is enabled.
8. Antivirus and SmartScreen review.
9. SBOM and checksum presence.
10. Manual connection/discovery smoke test against at least one controlled API.

## Upgrade model

The stable Inno Setup AppId upgrades the existing per-user installation. The installer stops the current application and replaces only files under the install directory. User data remains under Local AppData and is not removed.
