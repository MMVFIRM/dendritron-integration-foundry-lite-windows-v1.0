# Phase 6.2 Reliability Hardening

Phase 6.2 responds to a second adversarial review of the production platform. The reviewer identified three release-blocking defects and two smaller runtime-boundary problems. This document records the exact disposition.

## 1. Account lockout could be cleared by another failed guess

**Reproduced:** yes. The threshold attempt wrote an active lock while resetting the failure counter. A later bad guess calculated from zero and wrote `locked_until = NULL`.

**Correction:** login failure state is recomputed inside a database transaction from the current row. While a lock is active, additional attempts preserve both the threshold counter and the existing expiration. PostgreSQL uses a row lock; the SQLite development backend serializes transactions.

**Threat-oriented tests:**

- Exact threshold followed by a correct password remains locked.
- Threshold plus one additional attack attempt followed by a correct password remains locked.
- Concurrent bad guesses cannot lose increments or clear the lock.

## 2. Fileless SQLite failed under real Uvicorn request threads

**Reproduced:** yes. A plain `sqlite:///:memory:` database is connection-private, while FastAPI sync endpoints run in a thread pool. Concurrent requests received fresh connections without the schema.

**Correction:** the development backend now uses a unique named shared-memory SQLite URI with a `QueuePool`. All request threads see one database. SQLite transactions and reads are serialized inside the process because shared-cache table locks still produced `SQLITE_LOCKED` failures under concurrent writes.

**Live-server test:** a real Uvicorn subprocess bootstraps once and completes 24 concurrent logins with 24 HTTP 200 responses.

**Boundary:** this is a correct single-process development backend. PostgreSQL remains mandatory for production and replicas.

## 3. Lease loss could terminate the worker

**Reproduced:** yes. `complete()` raised for an expired lease, the exception handler called `fail()` using the same dead token, and that second exception escaped the worker loop.

**Correction:**

- Lease loss has an explicit exception type.
- Execution errors and lease-ownership errors are handled separately.
- Long-running jobs renew leases on a heartbeat.
- A lost heartbeat prevents completion/failure under stale ownership.
- Lease loss is audited best-effort and does not exit the worker.
- SIGTERM/SIGINT stop the polling loop cleanly.

**Tests:** expired completion, expired failure handling, long-running heartbeat renewal, and process-level SIGTERM exit all pass.

## 4. Production API documentation was public

**Correction:** production sets `docs_url`, `redoc_url`, and `openapi_url` to `None`. `/docs`, `/redoc`, and `/openapi.json` return 404. Development keeps them enabled.

## 5. Default development bootstrap/readiness contradicted itself

**Correction:** production bootstrap remains disabled by default and requires an out-of-band token whenever enabled. Development may use tokenless bootstrap when explicitly enabled, and readiness reports that state as available rather than unsafe. The default console can create a local development platform.

## Phase 2 fixture redesign

The previous holdout was honestly labeled but structurally separable. Phase 6.2 replaces it with:

- 18 training cases
- 360 independently generated holdout cases
- Every categorical value appearing in every branch
- Strategic/east, enterprise/central, and high-value SMB combinations
- 35% coordinate corruption in holdout cases
- Explicit ambiguous cases requiring abstention
- Six out-of-distribution novelty cases
- Static-priority, best-single-field, and exact categorical-tuple lookup comparators

The tissue exceeds the exact tuple lookup fixture while maintaining selective accuracy and ambiguity abstention. This remains synthetic architecture evidence only. It does not establish real-world integration accuracy or superiority over trained statistical baselines.

## Release boundary

Phase 6.2 corrects these defects but does not replace live PostgreSQL load/failover testing, KMS/HSM validation, immutable audit infrastructure, enterprise identity integration, penetration testing, or independent production certification.
