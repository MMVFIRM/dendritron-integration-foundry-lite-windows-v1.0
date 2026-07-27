# Phase 6.2 — Adversarial Production Hardening

## Purpose

Phase 6 introduced a production-oriented UI and control plane. Phase 6.2 closes the gaps found during an adversarial release review rather than treating them as documentation issues.

## Corrected production topology

```text
Public TLS ingress
        ↓
Verified edge proxy
        ↓
Production FastAPI app
  /platform/* only
        ↓
PostgreSQL shared state
        ├── tenants and users
        ├── systems and daughters
        ├── durable jobs
        ├── vault envelopes
        ├── rate buckets
        └── audit heads/events
        ↓
Signed external anchor objects
```

The Phase 0–5 developer API runs as a separate non-production application and is not mounted under the production Ingress.

## Closed findings

### Production boundary

Unauthenticated research routes were removed from `difoundry.api:app`. Production probes for `/nervous/policy`, `/discover`, repair deployment, tissue training, and legacy `/health` return `404`.

### Reachable multi-tenancy

A platform administrator can create tenants. Tenant email uniqueness is scoped locally. Login includes tenant slug. The operator console exposes tenant and user administration.

### Audit-tail integrity

Events persist a monotonic sequence. Verification compares the recomputed chain with both database head/version and an external signed anchor. Tail deletion, same-timestamp ordering, signature modification, missing anchors, and concurrent append sequences are tested.

### Vault correctness and rotation

The vault key is tenant-scoped, writes verify row count, rows retain key version, a versioned keyring decrypts legacy rows, and tenant rotation re-encrypts envelopes. Same-reference cross-tenant writes and real rotation are tested.

### Replica correctness

The production app no longer uses the research module’s process-global registries. Shared production state is SQL-backed. Jobs use lease tokens and PostgreSQL row locks. Multiple production contexts against the same database see the same systems, users, and jobs.

### Benchmark labeling

Phase 2 now reports a deterministic synthetic combinatorial holdout with an explicit claim boundary. Phase 4 labels its fixture-level review change. Phase 6 labels its single-process release-gate workflow and does not present harness runtime as throughput evidence.

### Identity lifecycle

Password change, logout, role change, deactivation, and explicit revocation invalidate prior tokens immediately. Failed attempts are stored and lockouts enforced. Unknown users receive dummy Argon2 work.

### Rate limiting and proxy identity

Fixed windows were replaced with SQL token buckets. Forwarded identity is accepted only from trusted immediate proxies. Kubernetes uses an in-pod verified proxy and no universal forwarded-IP trust.

### Packaging

The Docker image installs a built wheel. `.dockerignore` excludes databases, caches, tests, reports, local secrets, wheels, archives, and build output. Importing the production app creates no local database.

## Verification scope

The Phase 6.2 suite includes unit, integration, adversarial, concurrency, static-asset, deployment-YAML, clean-wheel, and extracted-archive checks. These are release correctness gates. They do not replace PostgreSQL load/failover testing, penetration testing, or compliance validation.

## Remaining explicit boundaries

- KMS/HSM and offline audit signing
- Retention-locked external anchor storage
- Enterprise identity federation and MFA
- Production egress proxy
- Remote daughter mTLS/attestation
- Multi-region failover
- Independent security assessment
- Real-system accuracy and drift benchmarks
