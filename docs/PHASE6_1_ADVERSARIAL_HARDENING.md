# Phase 6.1 Adversarial Hardening

Phase 6.1 is a corrective release produced from an adversarial review of Phase 6. The review was correct that the original production boundary was not closed. The fixes below are release requirements, not optional recommendations.

## 1. Closed production API boundary

`difoundry.api:app` now mounts only the authenticated production control plane, console assets, login/bootstrap, and health probes. Phase 0–5 developer routes live in `difoundry.legacy_api:app`, which refuses to start when `DIFOUNDRY_ENV=production`.

Adversarial tests verify that production does not expose `/nervous/policy`, `/discover`, repair deployment, tissue training, or the legacy health endpoint. A generic route inspection test fails if any non-public production route lacks an authentication dependency.

## 2. Reachable multi-tenancy

A platform administrator can create tenants and provision each tenant's first administrator. Tenant administrators can create, update, deactivate, and delete users inside their tenant. Login includes a workspace slug. Email uniqueness is scoped to `(tenant_id, email)`, so the same person may belong to multiple tenants.

## 3. Audit truncation and ordering resistance

Audit events now persist a monotonic per-tenant sequence. Verification checks the complete event chain, the database head, event count, and an external signed anchor. Deleting the newest events fails verification. Equal timestamps do not affect ordering. Failed login attempts are audited.

The included signed directory anchor is replica-order-independent, but the backing storage must enforce retention or WORM semantics, or forward anchors to an external immutable system. A database administrator with control of both the database and mutable anchor storage is outside the reference implementation's protection boundary.

## 4. Tenant-safe vault and real key rotation

Secret identity is the composite `(tenant_id, secret_ref)`. Writes check exact affected-row counts and cannot silently disappear because another tenant uses the same reference. The vault supports a versioned AES-256-GCM keyring and decrypts each row with its recorded key version. Rotation re-encrypts existing tenant envelopes under the new active version.

Production key custody remains an external KMS/HSM or secret-manager responsibility.

## 5. Replica-safe production state

The production control plane stores tenants, users, systems, connections, jobs, audit state, rate-limit state, and secret envelopes in SQL. Process-global Phase 0–5 registries exist only in the developer application. Durable jobs use unforgeable lease tokens; stale, expired, or stolen leases cannot complete work.

The production image is built from a wheel in a multi-stage Dockerfile. The build context excludes databases, caches, reports, tests, local secrets, and build products.

## 6. Benchmark claim correction

All benchmark reports now include `evaluation_kind` and `claim_boundary`. Phase 2 uses a deterministic synthetic holdout rather than reporting recall over its training examples. These fixtures validate architectural behavior; they are not throughput, universal accuracy, penetration, availability, or multi-process benchmarks.

## Additional corrections

- Password change, logout, role change, deactivation, and explicit revocation invalidate tokens immediately through a persisted token version.
- Unknown-user login performs dummy Argon2 verification; failed attempts are persisted, audited, and subject to lockout.
- Fixed-window rate limits were replaced with a concurrency-tested SQL token bucket.
- Forwarded client identity is accepted only from configured immediate-proxy CIDRs.
- Bootstrap is closed by default in production and requires an out-of-band token when enabled.
- Metrics require authentication and are tenant-scoped.
- Dead idempotency schema and the unused CORS header claim were removed.
- SSRF protection and the required production egress-proxy boundary are explicitly documented.
- A v1-to-v2 database migration preserves data, rekeys tenant-local identities, sequences and rehashes audit events, and removes dead tables.
- Production startup fails closed for SQLite, fallback keys, missing audit-anchor storage, wildcard CORS, or unsafe bootstrap configuration.

## Verification scope

The release passes 100 tests from the source tree, installed wheel, and extracted archive. The suite includes concurrent audit append, rate-limit consumption, job-lease theft/expiry, audit-tail truncation, token revocation, same-email multi-tenancy, cross-tenant secret collisions, key rotation, migration, and production route-boundary tests.

CLI statement coverage is measured at 56% for the in-process primary-path suite. Uncovered command branches remain explicitly unclaimed.
