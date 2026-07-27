# Foundry Lite Security Model

## Intended deployment

Foundry Lite is a single-user, local-first application. It is not an unauthenticated internet service.

## Network boundary

- Default host: `127.0.0.1`
- Default port: `8765`
- A non-loopback bind is rejected unless `DIFOUNDRY_LITE_ALLOW_LAN=true`
- Docker Compose publishes only to the host loopback interface
- OpenAPI, Swagger UI, and ReDoc are disabled

## Browser boundary

The console sets a random local session cookie. State-changing and private `/lite/*` requests must echo that value in `X-Foundry-Lite-Session`. This limits cross-site requests against a localhost application without requiring a visible login.

This is not a substitute for authentication when LAN or remote access is enabled.

## Credential storage

Credentials are encrypted using AES-256-GCM. A random local vault key is generated outside the SQLite database. The key and data directory are assigned private filesystem modes when supported.

The API exposes only credential references and connection status. It does not provide a plaintext-secret retrieval endpoint.

## External-system trust

The user explicitly chooses external system URLs. Foundry may connect to public or private/internal addresses because private systems are a core use case.

Accordingly, Lite does not claim an enterprise SSRF boundary. Do not expose the Lite interface to untrusted users. Enterprise deployment should use the production egress-proxy boundary.

## Webhooks

Each connection receives a random webhook token embedded in its path. The webhook is additionally protected by the local same-origin session in v0.1, making it primarily a local/test ingestion surface. A future authenticated remote relay is needed for public SaaS webhooks without enabling LAN exposure.

## Backups

The application data directory contains encrypted credentials, daughter artifacts, activity history, and the vault key. A backup containing both the database and the key can decrypt credentials. Store backups accordingly.
