# Foundry Lite v0.1 Current Boundaries

The release proves a functional local product path. It does not prove universal compatibility.

## Implemented

- No-account local workspace
- Autonomous OpenAPI/Swagger discovery
- GraphQL introspection
- OData metadata discovery
- Capability-index discovery
- Read-only behavioral schema inference
- Encrypted local credential storage
- Local OAuth authorization and token exchange with no MMV service
- PKCE for Google Sheets, Microsoft 365, and Salesforce public desktop clients
- Google Sheets, Microsoft 365 calendar, and Salesforce contact profiles
- OAuth token refresh and revocation
- Chat-selected task composition
- Plain-language business-question resolution
- Task-specific daughter bundles
- Trigger-condition ownership
- Safe simulation preview
- Local webhook ingestion
- Polling with baseline and change fingerprints
- Embedded background execution
- Pause, enable, monitor, delete, and export

## Not yet implemented

- Provider profiles beyond the four listed local OAuth connectors
- Zero-configuration OAuth for providers that require confidential clients
- Public remote webhook relay
- Browser or desktop interface observation
- Local database discovery agent
- Filesystem and message-broker discovery in the Lite UI
- Automatic bounded repair UX
- Additional commercial SaaS capability profiles
- Application auto-update
- Multi-user access
- Remote access authentication
- Production support matrix across commercial SaaS products

## Fixture claim

The bundled benchmark uses two mock systems whose OpenAPI documents and behavior are controlled by the test. It confirms that the assembled product path works. It is not a discovery success-rate, usability, latency, throughput, or reliability measurement.
