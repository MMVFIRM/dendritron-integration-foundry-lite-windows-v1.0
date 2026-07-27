# Foundry Lite Windows desktop architecture

## Product boundary

The Windows desktop edition is a local application, not a remotely hosted multi-tenant control plane. One Windows user owns one local workspace. The app starts an embedded Mother and daughter runner on loopback and presents the UI through the user's default browser.

```text
Windows shortcut or sign-in startup
              ↓
Single-instance desktop supervisor
              ↓
Health-checked local FastAPI process
              ↓
Browser console on 127.0.0.1
              ↓
Embedded discovery, composition, and daughter runner
              ↓
Connected external systems
```

## Desktop supervisor

`difoundry.lite.desktop` owns:

- The named Windows mutex
- Uvicorn lifecycle
- Browser launch timing
- Notification-area icon
- Stop/status/health-check commands
- Process signals
- Rotating log initialization
- Fatal-startup user messages

A second launch opens the existing console rather than creating a second database writer or binding a second server.

## Data directories

```text
%LOCALAPPDATA%\Dendritron Foundry Lite\Data\
├── foundry-lite.sqlite3
├── local-vault.key
├── Backups\
├── Logs\
├── Exports\
└── daughters\
```

The application install directory is separate from the data directory. This allows installer upgrades to replace the application without touching systems, daughters, credentials, activity, or backups.

## Backup and recovery

At startup, Foundry:

1. Opens the SQLite database.
2. Applies the idempotent schema.
3. Runs `PRAGMA integrity_check`.
4. Creates a daily backup when one is due.
5. Retains the configured number of rolling backups.

Users can create an immediate backup from Settings or the tray icon. Backups contain encrypted secret envelopes but not the DPAPI-protected master key; restoration must occur for the same Windows user or include the matching key file.

## Installer behavior

The Inno Setup installer:

- Installs per-user without UAC by default.
- Gracefully stops an existing process before replacement.
- Uses Restart Manager as a fallback.
- Preserves the data directory.
- Optionally installs a startup registry entry.
- Creates Start Menu shortcuts.
- Launches the application after installation.
- Removes the startup entry on uninstall.

## Release architecture

The Windows release pipeline produces:

- One-directory PyInstaller runtime
- One-click Inno Setup executable
- Portable ZIP
- CycloneDX SBOM
- SHA-256 checksums
- Optional Authenticode signatures
- GitHub build-provenance attestation

The one-directory runtime was selected over PyInstaller one-file mode for faster startup, clearer antivirus scanning, and more predictable upgrades.
