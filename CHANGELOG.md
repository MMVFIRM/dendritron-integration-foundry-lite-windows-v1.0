# Changelog

## 1.0.0 — Windows desktop distribution

- Added per-user one-click Windows installer definition.
- Added PyInstaller desktop bundle and Windows GitHub Actions release pipeline.
- Added single-instance tray supervisor with health-checked startup and clean shutdown.
- Added Windows DPAPI protection and legacy vault-key migration.
- Added startup backups, SQLite integrity verification, and redacted support bundles.
- Added start-at-sign-in controls and Settings/recovery UI.
- Added preconfigured common-system connection hints.
- Added code-signing, SBOM, checksum, and provenance build boundaries.
- Added Windows desktop and installer adversarial tests.

## 0.8.0 — Foundry Lite v0.1

- Added a separate no-account, local-first Foundry Lite application.
- Added autonomous OpenAPI, Swagger, GraphQL, OData, capability-index, and read-only behavioral discovery.
- Added encrypted local credential storage and a localhost same-origin session boundary.
- Added a four-area operator UI for Create, Systems, Connections, and Activity.
- Added chat-driven task interpretation and task-specific Dendritron daughter composition.
- Added plain-language resolution of missing business constants without schema editing.
- Added safe preview, trigger conditions, polling baselines, change fingerprints, webhooks, and embedded execution.
- Added daughter export for Enterprise migration.
- Added Docker Lite packaging and explicit product/security/current-boundary documentation.
- Added 21 Lite-specific tests while retaining all Phase 0–6.2 regressions (132 total).

## 0.7.2 — Phase 6.2 reliability hardening

### Fixed

- Made account lockout monotonic through the complete lock window. Threshold-plus-one and concurrent attack attempts can no longer clear `locked_until` or reset the counter.
- Replaced connection-private `sqlite:///:memory:` behavior with a uniquely named shared-memory SQLite database, pooled request-thread connections, and serialized SQLite transactions for the single-process development backend.
- Added a live Uvicorn regression test that performs 24 concurrent authenticated logins against the default no-file database.
- Added worker lease heartbeats, explicit lease-loss handling, and safe completion/failure behavior when ownership expires.
- Added SIGTERM/SIGINT shutdown handling and a process-level graceful-termination test.
- Disabled OpenAPI, Swagger UI, and ReDoc in production.
- Restored a usable tokenless bootstrap only in development; production bootstrap remains closed by default and token-protected when enabled.
- Rebuilt the Phase 2 fixture with overlapping feature families, corrupted-coordinate holdout cases, ambiguous abstention cases, and lookup/single-field comparators.

### Deployment

- Worker manifests now specify lease and heartbeat intervals and a 120-second termination grace period.
- Corrected the example audit-anchor path to the directory-based immutable-record store.

### Verification

- Added threat-oriented threshold-plus-one and concurrent-lockout tests.
- Added live-server request-thread concurrency coverage.
- Added expired-lease, heartbeat, and worker-process SIGTERM tests.
- Added production documentation-exposure and development-bootstrap tests.

## 0.7.1 — Phase 6.1 adversarial hardening

### Release blockers closed

- Split the production and developer HTTP applications; production no longer mounts Phase 0–5 control routes.
- Added platform-admin tenant creation, tenant-scoped email uniqueness, and tenant-slug login.
- Added monotonic audit sequences, database-head comparison, tail-truncation detection, and signed external anchor objects.
- Rebuilt the vault around `(tenant_id, secret_ref)`, exact write checks, versioned keyrings, and executable tenant rotation.
- Removed process-global research registries from the production app; production resource state is SQL-backed across replicas.
- Replaced misleading benchmark field names with explicit synthetic/release-gate claim boundaries.

### Security and lifecycle

- Added password changes, user deactivation/deletion, role updates, logout, token-version revocation, and platform/tenant administration.
- Added dummy Argon2 work for unknown users, failed-login security events, lockout, and authenticated tenant-scoped metrics.
- Replaced fixed-window rate limits with SQL token buckets and added stale-bucket sweeping.
- Added trusted-immediate-proxy parsing and removed universal forwarded-header trust from deployment manifests.
- Closed production bootstrap by default and required an out-of-band bootstrap token.
- Added explicit SSRF/egress boundary documentation.

### Correctness and packaging

- Added unforgeable job lease tokens and stale/stolen lease rejection.
- Added schema v1→v2 migration tooling for SQLite and PostgreSQL.
- Added wheel-based multi-stage container builds and `.dockerignore`; local databases, caches, tests, reports, secrets, and build artifacts no longer ship.
- Added a verified Kubernetes edge sidecar, PostgreSQL-backed replicas, and signed-anchor storage boundary.
- Added administration UI for tenants, users, and session revocation.

### Verification

- 100 automated source-tree tests before final packaging.
- Adversarial tests for legacy-route exposure, bootstrap land-grab, tenant reachability, audit truncation, vault collision/rotation, immediate token revocation, login lockout, unknown-user hashing, stale leases, shared database state, import-time files, anchor ordering, rate-limit concurrency, proxy trust, and metrics authentication.
- Benchmark outputs explicitly state that they are synthetic architecture fixtures, not throughput or external accuracy evidence.


## 0.7.0 — Phase 6

### Added

- Responsive production operator console
- Chat-driven integration planning and connection creation
- Multi-tenant SQLAlchemy control-plane repository
- SQLite local backend and PostgreSQL production target
- Argon2id password authentication
- Signed expiring bearer tokens
- Admin, operator, and viewer roles
- AES-256-GCM tenant/resource-bound credential envelopes
- Durable SQL job queue with worker leases, retries, and dead letters
- System and daughter connection monitoring
- Per-tenant tamper-evident audit chain
- SQL-backed rate limiting
- Request-size and security-header middleware
- SSRF-resistant system URL registration boundary
- Liveness, readiness, and Prometheus metric endpoints
- Non-root hardened Docker image
- Production Docker Compose stack
- Kubernetes API and worker deployments, HPAs, PDB, TLS ingress, and network policies
- Phase 6 benchmark and production security test suite

### Verification

- 80 automated tests across Phases 0–6
- 15/15 Phase 6 production benchmark gates
- Chat-to-daughter end-to-end composition
- Cross-tenant isolation
- Credential ciphertext inspection
- Worker lease exclusivity
- Audit tamper detection
- UI asset and security-header validation


## 0.6.0 — Phase 5

### Added

- Multi-system nervous-system control plane
- Explicit daughter capability registration
- Capability-to-local-route binding for multi-route daughters
- Cross-daughter workflow DAGs with dependency validation
- Root, correlation, causation, workflow, and step identifiers
- Fail-closed global policy with allow, deny, and approval-required effects
- Policy priority and exact transition matching
- Registered-capability enforcement
- Global fan-out and hop limits
- Distributed root-event/workflow idempotency
- Global nervous event and coordination ledger
- Exact local contract, behavior-hash, and Dendritron-owner lineage
- Independent daughter failure domains and dependent-step skipping
- Hash-bound nervous topology bundles and storage envelopes
- Daughter nervous-system registration artifacts
- Phase 5 CLI, API, benchmark, and demonstration

### Strengthened

- Global dispatch cannot activate routes outside the selected capability
- Multi-route daughters must explicitly bind capabilities to route IDs
- A daughter failure cannot update another daughter’s failure counters
- Missing dependency outputs fail closed rather than being fabricated
- Workflow cycles and excessive fan-out are rejected before registration
- Global coordination cannot bypass local schema, permission, or contract certification

### Verification

- 67 automated source-tree tests before release packaging
- Complete Phase 0 through Phase 4 regression suite retained
- Four-daughter coordination: pass
- Parallel independent fan-out: pass
- Dependent cross-daughter handoff: pass
- Local failure isolation: pass
- Exact local ownership lineage: pass
- Global policy denial: pass
- Distributed idempotency: pass
- Workflow cycle rejection: pass
- Capability-scoped route dispatch: pass
- Topology round trip and tamper detection: pass
- API lifecycle: pass
- CLI benchmark: pass

## 0.5.0 — Phase 4

### Added

- Privacy-preserving inherited integration intelligence
- Sanitized semantic-mapping, repair-strategy, and Dendritron-topology pattern exporters
- Payload, secret, identifier, URL, email, IP, UUID, path, and long-literal privacy inspection
- Consent scopes for private, organization, and sanitized shared intelligence
- Content-hash deduplication across independent pattern origins
- Multi-origin eligibility and stronger automatic semantic-acceptance thresholds
- Single-origin quarantine for poisoning resistance
- Hash-bound intelligence packs and storage envelopes
- HMAC-SHA256 reference pack signing and verification
- Inherited semantic matcher with explicit evidence and provenance
- Inherited repair advisor using verified bounded-repair shapes
- Daughter inheritance workspace and artifact-manifest binding
- Phase 4 CLI, API, benchmark, and demonstration

### Strengthened

- Inherited evidence remains subordinate to contract, schema, permission, replay, approval, and deployment gates
- Private and organization-scoped patterns cannot enter the shared eligible pool
- Arbitrary transform dictionaries and constants are not exported
- Automatic semantic acceptance requires more independent origins than advisory eligibility
- Production origin-attestation limitation is explicit

### Verification

- 58 automated tests
- Complete Phase 0 through Phase 3 regression suite retained
- Multi-origin consensus: pass
- Single-origin poisoning quarantine: pass
- Privacy and consent enforcement: pass
- Inherited semantic review reduction: pass
- Inherited repair advice: pass
- Pack round trip: pass
- Storage tamper detection: pass
- Signature verification: pass
- Daughter artifact binding: pass
- API lifecycle: pass
- CLI benchmark: pass

## 0.4.0 — Phase 3

### Added

- Owner-bound drift observations across schema, endpoint, permission, authentication, behavior, semantic, volume, and latency categories
- Stable failure signatures and committed-event quarantine ledger
- Automatic failed-branch isolation with dry-run protection
- Explicit JSON-Pointer repair candidates over Integration Contracts and System Profiles
- Deterministic repair generators for renamed fields, required fields, permissions, endpoints, and externally generated bounded patches
- Executable rollback patch generation
- Candidate and envelope SHA-256 integrity binding
- Impacted historical replay and optional sandbox adapter execution
- Unrelated-path stable plan fingerprints
- Unrelated Dendritron branch-hash regression gates
- Risk policy with permission, endpoint, removal, destructive-operation, and secret escalation
- Approval and rejection records
- HMAC-SHA256 reference signer binding patch identity, verification evidence, and approval evidence
- Atomic deployment bundles with repaired contract, profiles, tissue, candidate, deployment record, and manifest
- Automatic repaired-owner re-enablement and tissue provenance
- Owner-scoped quarantined-event recovery
- Phase 3 CLI, API, demonstration, benchmark, and daughter repair workspace

### Verification

- 48 automated tests
- Full Phase 0, Phase 1, and Phase 2 regression suite retained
- Drift detection: pass
- Exact owner attribution: pass
- Failure locality: pass
- Quarantine and recovery: pass
- Impacted replay: pass
- Unrelated plan regression: pass
- Unrelated tissue isolation: pass
- Risk escalation: pass
- Signature tamper detection: pass
- Atomic deployment: pass

## 0.3.0 — Phase 2

### Added

- Persistent Dendritron ownership tissue bound to Integration Contracts
- Stable route and branch ownership keys
- Sparse, inspectable event feature encoder
- Branch-local specialists and top-k activation
- Positive and negative branch-scoped adaptation
- Novelty threshold and ownership-margin abstention
- Partial-training safety for unknown sibling branches
- Branch and specialist health counters
- Exact action ownership metadata in execution plans
- Local failure attribution with normalized failure signatures
- Branch enable/disable controls and damage-isolation tests
- SHA-256-bound tissue envelope
- Atomic tissue file writes
- Tissue CLI commands: initialize, train, inspect, verify
- Tissue API lifecycle and tissue-backed simulation
- Initialized tissue and training scaffold in generated daughter bundles
- Phase 2 benchmark and complete demo

### Strengthened

- Simulation does not mutate tissue health
- SQLite event ledger is thread-safe for API execution
- Live API tissue registry preserves lock-protected runtime instances
- Generated manifests declare Dendritron runtime state capabilities
- Phase 0 and Phase 1 behavior remains backward compatible

### Verification

- 33 automated tests
- Static baseline accuracy: 0.3333
- Dendritron benchmark accuracy: 1.0000
- Novelty abstention rate: 1.0000
- Mean active specialist fraction: 0.2500
- Branch-scoped adaptation: pass
- Damage isolation: pass
- Persistence integrity: pass

## 0.2.0 — Phase 1

- Plugin-based discovery across OpenAPI, AsyncAPI, GraphQL, SQL, JSON Schema, and native profiles
- Semantic graph generation with evidence and explicit uncertainty
- Multi-target daughter composition
- Operation-input-aware mappings
- Daughter manifests, verification bundles, and hash-bound artifact bundles

## 0.1.0 — Phase 0

- System-agnostic integration simulator kernel
- Declarative System Profiles and Integration Contracts
- Exact branch ownership router
- Deterministic mapping and transforms
- Certification, idempotency, ledger, and replay
