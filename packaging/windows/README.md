# Windows desktop packaging

The Windows edition is a per-user installation. It bundles Python and all application dependencies, installs without administrator privileges, binds only to `127.0.0.1`, and runs from the Windows notification area.

## Build on Windows

Install Python 3.11 and Inno Setup 6, then run:

```powershell
.\packaging\windows\build.ps1 -Version 1.1.0
```

Outputs:

- `Dendritron-Foundry-Lite-1.1.0-Setup.exe`
- Portable ZIP
- CycloneDX SBOM
- SHA-256 checksums

PyInstaller is not a cross-compiler, so the executable and installer must be built on Windows. The included GitHub Actions workflow uses a Windows runner and publishes the outputs as workflow artifacts.

## Signing

Set these repository secrets to Authenticode-sign the executable and installer:

- `WINDOWS_CERTIFICATE_BASE64`
- `WINDOWS_CERTIFICATE_PASSWORD`

Unsigned builds are suitable for internal testing. Public distribution should use a trusted code-signing certificate.

## Installed behavior

- Install location: `%LOCALAPPDATA%\Programs\Dendritron Foundry Lite`
- Data location: `%LOCALAPPDATA%\Dendritron Foundry Lite\Data`
- No Python installation required
- Optional start at Windows sign-in
- Data and credentials are preserved during upgrades and uninstall
- Vault master key is protected with Windows DPAPI for the current user
