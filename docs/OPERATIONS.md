# Phase 6.2 Operations Guide

## Supported operating modes

### Local evaluation

The default fileless SQLite database is supported for tests and local demonstrations. It uses a shared-memory URI and pooled connections so Uvicorn request threads see one database, while database operations are serialized to avoid shared-cache lock failures. SQLite remains a single-process development backend and is not a multi-replica production backend.

### Production

Use PostgreSQL, separate API and worker replicas, externally supplied independent keys, a verified reverse-proxy boundary, and retention-locked audit anchor storage.

## First deployment

1. Create a PostgreSQL database and least-privilege application role.
2. Generate independent 32-byte URL-safe base64 token, vault, and audit-anchor keys.
3. Configure `DIFOUNDRY_VAULT_KEYS` as a JSON version-to-key map.
4. Configure a WORM/retention-capable audit anchor directory or external export.
5. Configure the exact immediate trusted proxy CIDR; never use a universal range.
6. Start with bootstrap disabled.
7. Open a controlled first-boot window with a one-time bootstrap token.
8. Create the platform administrator.
9. Disable bootstrap, remove its token, and confirm readiness.
10. Create additional tenants through the platform-admin console or API.

## Database schema

Phase 6.2 uses schema v2. A v1 database causes startup to fail with a migration instruction.

```bash
python scripts/migrate_v1_to_v2.py "$DIFOUNDRY_DATABASE_URL"
```

Take a verified backup first. SQLite files are copied automatically by the script unless `--no-backup` is supplied. PostgreSQL migration should run during a maintenance window with application processes stopped, because audit chains are re-sequenced and re-hashed.

After migration:

- Run `/platform/readiness`.
- Sign in as the promoted platform administrator.
- Verify every tenant audit chain.
- Rotate or re-save secrets if key custody changed.
- Run a connection health check for every daughter.

## Vault rotation

1. Add the new key version to `DIFOUNDRY_VAULT_KEYS` on every API/worker replica.
2. Keep the previous key version present.
3. Restart or roll replicas to load the complete keyring.
4. Call `POST /platform/vault/rotate` per tenant with the target version.
5. Confirm all rows report the new version and resolve successfully.
6. Keep old keys for backup-retention recovery.
7. Remove an old key only after all live rows and restorable backups no longer require it.

## Audit anchors

Production creates one signed file per tenant sequence. The Kubernetes PVC must support `ReadWriteMany` and atomic create. More importantly, its storage policy must prevent deletion/rewriting for the required retention period, or records must be forwarded to an external immutable service.

Monitor:

- Missing external anchors
- Anchor signature failures
- Database head/anchor sequence mismatches
- Audit sequence gaps
- Recovery restores where DB and anchor generations differ

## API scaling

The production API holds no authoritative Phase 0–5 registry, nervous ledger, repair ledger, or intelligence registry in process memory. Production resource state is PostgreSQL-backed and visible across replicas.

The developer API still has in-memory research registries by design. It is non-production and cannot start with `DIFOUNDRY_ENV=production`.

## Worker leases

Workers claim jobs with a random lease token and renew long-running leases on a heartbeat. Completion, failure, and lease renewal require matching:

- Job ID
- Worker ID
- Lease token
- Unexpired lease

An expired or stolen lease cannot commit and no longer terminates the worker process. SIGTERM and SIGINT stop the polling loop gracefully. Configure heartbeat shorter than lease duration and give pods enough termination grace for current work to complete or lose ownership safely. PostgreSQL uses row locking/`SKIP LOCKED` for competing workers. Monitor queued age, retry count, heartbeat failures, lease loss, dead-letter count, and expired lease recovery.

## Reverse proxy

Disable Uvicorn proxy-header rewriting. Let the application validate forwarded identity against the immediate trusted hop.

The Kubernetes sidecar accepts ingress traffic, normalizes a verified client IP, and proxies over loopback. If the ingress controller does not supply a trustworthy `X-Real-IP`, configure its real-IP module before enabling public login; otherwise login rate limiting conservatively collapses to the ingress peer.

## Egress

The included network policy permits API/worker egress only to DNS, PostgreSQL, and an egress-proxy pod. The platform does not include a production egress proxy implementation. Deploy one that validates resolved IPs, ports, protocols, TLS names, and redirects on every outbound request.

## Backups and recovery

Back up together:

- PostgreSQL
- Vault key versions and custody metadata
- Audit anchor objects
- Deployment configuration
- Daughter artifacts and signed manifests

A valid database restore with missing or older audit anchors must fail audit verification. Recovery procedures must explicitly reconcile or reject mismatched generations rather than silently resetting heads.

## Observability

Authenticated tenant metrics are available at `/platform/metrics`. Public readiness exposes only boolean component status and schema version.

Production should additionally export:

- API latency/error distributions
- Login failure/lockout events
- Job queue depth and oldest age
- Worker lease expiry and dead letters
- Connection health and drift events
- Audit-anchor lag
- Vault rotation progress
- Database pool saturation

## Release verification

Run:

```bash
pytest -q
node --check src/difoundry/static/app.js
python -m build
```

Then test the installed wheel and an extracted release archive independently. The bundled benchmark is a small correctness fixture, not a load test. Run separate PostgreSQL concurrency, failover, soak, and restore tests before production approval.
