# Phase 6.2 Security Model

## Production application boundary

`difoundry.api:app` is the only production web application. It mounts the authenticated `/platform/*` router, public liveness/readiness, and console assets. It does not mount research routes from Phases 0–5.

`difoundry.legacy_api:app` contains the research/developer HTTP surface and raises at import when `DIFOUNDRY_ENV=production`. It must never share a public listener, Service, or Ingress with the production app.

## Trust boundaries

1. **Browser → verified edge → control plane**: TLS, single-hop verified client identity, bearer authentication, request-size limits, token-bucket rate limits, and browser security headers.
2. **Control plane → PostgreSQL**: least-privilege database identity and encrypted transport supplied by the operator.
3. **Control plane → key custody**: versioned process keyring in the reference implementation; KMS/HSM required for regulated production.
4. **Control plane → external audit anchor**: signed immutable anchor objects; storage retention/WORM or SIEM replication remains an operator requirement.
5. **Mother → remote daughter**: signed artifacts and authenticated runner identity remain required before remote execution is trusted.
6. **Daughter → external systems**: contract-scoped credentials plus an enforced egress proxy/allowlist.

## Authentication and token lifecycle

- Argon2id hashes all passwords.
- Unknown-user login attempts perform a dummy Argon2 verification to reduce timing-based enumeration.
- Login requires tenant slug, email, and password.
- Failed attempts are persisted in the security-event table and, when a tenant/user is known, in the tenant audit ledger.
- Account lockout follows configurable consecutive-failure thresholds.
- Tokens are signed, expiring, and contain a random token identifier plus a token version.
- Every authenticated request re-reads the user and tenant from the database.
- Password changes, logout, role changes, deactivation, and explicit revocation increment token version, immediately invalidating older tokens.

This is structural timing equalization, not a formal side-channel certification. Deployment latency, proxies, and database behavior still require measurement.

## Authorization and tenancy

Roles are:

- `platform_admin`: tenant creation plus root-tenant administration
- `admin`: user, system, credential, connection, and monitoring administration inside one tenant
- `operator`: system, credential, connection, chat, and monitoring operations inside one tenant
- `viewer`: read-only tenant monitoring

All tenant resources are queried using the authenticated `tenant_id`. Cross-tenant misses return `404` where revealing existence would leak information. User email uniqueness is `(tenant_id, email)`, permitting the same human address in different tenants.

## Bootstrap

Production defaults to bootstrap disabled. The production process fails at startup when external keys, PostgreSQL, or an audit-anchor path are missing, when wildcard CORS is configured, or when bootstrap is enabled without an out-of-band `DIFOUNDRY_BOOTSTRAP_TOKEN`. The HTTP caller must also supply `X-Bootstrap-Token`.

Operational procedure:

1. Generate a high-entropy one-time token outside the application.
2. Enable bootstrap for a controlled maintenance window.
3. Create the initial platform administrator over TLS.
4. Disable bootstrap and remove the token.
5. Confirm readiness reports `bootstrap_closed=true`.

## Credential vault

AES-256-GCM authenticated encryption binds each envelope to tenant, resource type, resource ID, secret reference, and key version.

- The database primary key is `(tenant_id, secret_ref)`.
- Writes verify that one exact row was inserted or updated.
- Reads select the row’s recorded key version from the configured keyring.
- Rotation re-encrypts tenant envelopes into a selected target key version.
- Old keys must remain in the keyring until every envelope and backup retention period has migrated.
- Plaintext is never returned by an HTTP read endpoint.

The reference keyring is process memory. Use envelope keys from KMS/HSM and formal dual-control rotation in production.

## Audit integrity

Audit events use a monotonic per-tenant sequence. The sequence is part of the event hash, eliminating timestamp-order ambiguity. Verification checks both the event chain and the independently maintained database head/version, detecting tail truncation.

Production also writes one signed anchor object per sequence using atomic create. Verification compares the highest signed external sequence and head hash to the database.

Limitations:

- A normal writable filesystem is not immutable by itself.
- Put the anchor directory on retention-locked/WORM storage or export records to an external SIEM/transparency service.
- A process-held HMAC key can be abused after full process compromise; use KMS/HSM or offline signing for stronger separation.
- Database and anchor disaster recovery must be tested together.

## Reverse-proxy identity and rate limits

The application does not trust arbitrary forwarded headers. It uses `X-Forwarded-For` only when the immediate socket peer is inside `DIFOUNDRY_TRUSTED_PROXY_CIDRS`.

The Kubernetes reference uses an in-pod Nginx verifier:

- Only ingress-controller pods may reach its port.
- It converts the ingress controller’s verified `X-Real-IP` to a single-hop assertion.
- The application trusts only loopback.
- Uvicorn proxy-header rewriting is disabled.

The SQL token bucket uses PostgreSQL row locks in multi-replica deployment. SQLite is single-process only and uses a thread lock for reference tests. Fixed-window burst doubling and orphaned fixed-window rows were removed.

## SSRF and egress

Registration rejects malformed URLs and, in production, resolves hostnames and rejects loopback, private, link-local, multicast, unspecified, and reserved addresses.

That check is not a complete outbound SSRF defense. DNS can change after registration, redirects can cross trust boundaries, and connector-specific protocols may have other pivots. Production execution must pass through an egress proxy that re-resolves and enforces destination, protocol, redirect, and port allowlists at connection time.

## API discovery exposure

Production disables Swagger UI, ReDoc, and the OpenAPI document. Development retains them for local integration work.

## Metrics and health

- `/platform/liveness` and `/platform/readiness` are public and intentionally contain no tenant counts or secrets.
- `/platform/metrics` requires authentication and returns counts only for the caller’s tenant.
- Exporting aggregate infrastructure metrics to Prometheus should use a separate internal-only collector or service account.

## Remaining security work

- OIDC/SAML/MFA and organization lifecycle federation
- KMS/HSM-backed token, vault, and anchor keys
- Remote daughter identity attestation and mTLS
- Formal secrets-redaction review across all logs
- Penetration testing and threat-model review
- Dependency, SBOM, provenance, and image-signing pipelines
- External audit transparency/retention service
- Production egress proxy implementation and DNS-rebinding tests
- Jurisdiction-specific compliance controls
