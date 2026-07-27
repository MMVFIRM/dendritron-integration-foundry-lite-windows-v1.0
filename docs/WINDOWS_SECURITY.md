# Foundry Lite Windows security model

## Trust boundary

Foundry Lite assumes that the signed-in Windows user is authorized to operate the local workspace. It does not assume that arbitrary web pages, other network devices, or other Windows accounts are trusted.

## Network boundary

- The application binds to `127.0.0.1` by default.
- Non-loopback binding is rejected unless LAN mode is explicitly enabled.
- The installer creates no firewall rule.
- The API uses a same-origin session token set by the local console.
- OpenAPI, Swagger UI, and ReDoc are disabled.

## Credential boundary

Connected-system credentials are encrypted with AES-256-GCM. The random 256-bit vault key is protected on Windows with DPAPI using `CRYPTPROTECT_UI_FORBIDDEN` and application-specific optional entropy.

Consequences:

- Copying the SQLite database alone does not reveal credentials.
- Copying the database and protected key file to another Windows account normally does not unlock credentials.
- A process executing as the same Windows user can potentially use DPAPI and access local application data; that is inside the Lite trust boundary.

Legacy raw-key files from Foundry Lite v0.1 are migrated atomically to the DPAPI envelope on first Windows launch.

## Installer trust

Public distribution should Authenticode-sign both:

1. `FoundryLite.exe`
2. The Inno Setup installer

The executable is signed before it is embedded in the portable ZIP and installer. The installer is signed after compilation. SHA-256 checksums, an SBOM, and a GitHub provenance attestation are generated separately.

The repository intentionally does not contain a signing certificate or provider OAuth client secret.

## Support bundles

The support export removes keys containing credential, token, webhook, request, response, and payload material. It includes local version, integrity state, redacted systems, redacted connections, recent activity, and a bounded log tail.

Support bundles remain potentially sensitive operational records and should be reviewed before external transmission.

## Remaining risks

- Malware running as the same Windows user can access the local trust boundary.
- Local HTTP does not provide TLS; loopback restriction and same-origin protection are therefore mandatory.
- Provider access tokens retain the permissions granted by their provider.
- No public webhook relay is included.
- Code signing must be supplied by the distributor.
