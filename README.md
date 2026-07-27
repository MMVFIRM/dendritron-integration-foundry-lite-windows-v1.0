# Dendritron Integration Foundry Lite — Windows Desktop 1.0

Foundry Lite is a local-first Windows integration application for small organizations.

Download the installer, launch the app, connect the systems you use, enter the credentials those systems require, and describe what they should do together. Foundry discovers the live schemas, generates task-specific Dendritron daughter modules, tests the plan, and runs the connections in the background.

> The user provides system identity, authority, and business intent. Foundry provides the technical understanding and integration machinery.

## Windows user experience

```text
Run Dendritron-Foundry-Lite-1.0.0-Setup.exe
        ↓
Foundry installs for the current Windows user
        ↓
The app launches and opens the local console
        ↓
Choose a known app or enter an internal-system URL
        ↓
Enter the API key, token, or account credentials required
        ↓
Foundry discovers the schemas and capabilities
        ↓
Describe the integration in chat
        ↓
Review the safe preview and turn it on
```

The installed edition requires:

- No Python installation
- No Docker
- No command line
- No administrator privileges
- No Foundry account or login
- No schema files or manual mappings

The application remains active in the Windows notification area after the browser closes.

## Installation behavior

- Installs under `%LOCALAPPDATA%\Programs\Dendritron Foundry Lite`
- Stores application data under `%LOCALAPPDATA%\Dendritron Foundry Lite\Data`
- Binds only to `127.0.0.1`
- Optionally starts at Windows sign-in
- Creates Start Menu and optional desktop shortcuts
- Preserves data and credentials during upgrades
- Preserves user data by default during uninstall
- Launches automatically after installation

## Preconfigured systems

The connection picker includes starting configurations for:

- HubSpot
- Slack
- Stripe
- GitHub
- Microsoft 365 / Microsoft Graph
- Notion
- Airtable
- QuickBooks Online
- Salesforce
- Shopify
- Zendesk
- Custom REST, GraphQL, OData, and internal systems

These entries supply safe URL and authentication hints. Foundry still verifies the live connected system rather than treating the catalog as authoritative.

## Autonomous discovery

Foundry attempts discovery without requesting documentation uploads:

1. OpenAPI and Swagger endpoints
2. Linked machine-readable specifications
3. GraphQL introspection
4. OData metadata
5. Authenticated capability indexes
6. Representative read-only JSON responses
7. HTTP `OPTIONS` capability evidence

Formal evidence is preferred. Behavioral inference is labeled as lower-confidence evidence. Default discovery does not create, update, or delete external records.

## Desktop reliability

The Windows launcher provides:

- One application instance per Windows user
- Health-checked server startup before opening the browser
- System-tray Open, Backup, and Exit controls
- Clean stop and status commands for installers and support
- Rotating local logs
- SQLite integrity checks at startup
- Rolling local backups
- Redacted support bundles
- Graceful process shutdown

## Security

“No login” is a local trust model, not a public unauthenticated service.

- Localhost-only binding by default
- Same-origin local session protection
- AES-256-GCM encrypted connected-system credentials
- Windows DPAPI protection for the vault master key
- Credential values never returned by the API
- OpenAPI and interactive docs disabled
- Unpredictable per-connection webhook tokens
- Redacted diagnostics export
- No Windows firewall exception required

LAN mode remains an explicit advanced setting and should be placed behind an authenticated reverse proxy.

## Building the installer

PyInstaller must build Windows executables on Windows. The repository includes a complete Windows build and release pipeline:

```powershell
.\packaging\windows\build.ps1 -Version 1.0.0
```

The build performs:

1. Dependency installation with pinned constraints
2. Full automated test suite
3. PyInstaller desktop bundle creation
4. Packaged-executable health check
5. Optional Authenticode signing
6. Portable ZIP creation
7. Inno Setup one-click installer creation
8. Installer signing
9. CycloneDX SBOM generation
10. SHA-256 checksum generation

The included GitHub Actions workflow runs this pipeline on `windows-latest` and uploads the installer artifacts with build provenance attestation.

See [Windows desktop packaging](packaging/windows/README.md).

## Source development

```bash
python -m pip install -e ".[dev]"
pytest -q
DIFOUNDRY_LITE_OPEN_BROWSER=false difoundry-lite
```

## Verification

The source release includes tests for:

- All Phase 0–6.2 inherited guarantees
- Autonomous Lite discovery and daughter composition
- Real local Uvicorn startup
- Windows desktop health-check lifecycle
- DPAPI key-envelope migration behavior
- Backup and database integrity behavior
- Support-bundle redaction
- Installer safety directives
- Build signing order
- Windows CI release structure

The Windows installer itself must be compiled and smoke-tested on a Windows runner; Linux cannot produce a trustworthy PyInstaller Windows executable.

## Current boundaries

The release does not include provider-owned OAuth client credentials. Providers such as Intuit, Google, Microsoft, and Salesforce may require an OAuth application registration or administrator-issued token. The provider catalog reduces setup but cannot replace provider authorization.

The current desktop edition also does not include:

- A public webhook relay for systems that cannot reach localhost
- A native browser-traffic discovery extension
- A local desktop/database discovery agent
- A trusted production code-signing certificate in the repository
- Automatic internet-delivered application updates
- A measured commercial compatibility matrix

Updates are supported by running a newer installer over the existing installation; the stable application identity and separate data directory preserve the workspace.

## Documentation

- [Windows desktop architecture](docs/WINDOWS_DESKTOP.md)
- [Windows security model](docs/WINDOWS_SECURITY.md)
- [Windows release process](docs/WINDOWS_RELEASE.md)
- [Autonomous discovery](docs/AUTONOMOUS_DISCOVERY.md)
- [Foundry Lite boundaries](docs/FOUNDRY_LITE_BOUNDARIES.md)
