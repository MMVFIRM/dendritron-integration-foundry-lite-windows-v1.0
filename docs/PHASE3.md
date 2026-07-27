# Phase 3 — Bounded Self-Repair

## Objective

Phase 3 turns the exact owner and failure locus produced by Phase 2 into a controlled repair lifecycle. The daughter may detect and localize drift, but no model or repair generator may mutate production artifacts directly.

The lifecycle is:

```text
Committed execution failure or certification failure
                    │
                    ▼
           Owner-bound drift observation
                    │
                    ▼
              Event quarantine
                    │
                    ▼
       Explicit artifact patch candidate
                    │
                    ▼
 Historical replay + unrelated-path regression
                    │
                    ▼
             Risk-based approval
                    │
                    ▼
       Verification-and-approval-bound signature
                    │
                    ▼
             Atomic deployment
                    │
                    ▼
        Quarantined-event recovery replay
```

## Core Invariants

1. **A repair must have an exact owner.** Repair generation requires an attributed action, route, branch, and ownership key.
2. **Repairs are explicit artifacts.** A candidate contains JSON-Pointer patch operations over named contracts and System Profiles.
3. **Generated code does not deploy itself.** Generation, verification, approval, signing, and deployment are separate gates.
4. **Dry runs are non-operational.** Simulations may report drift but cannot quarantine traffic or disable a branch.
5. **Committed failures are isolated.** Only the affected branch may be disabled or have failure health updated.
6. **Historical behavior is replayed.** Impacted events must pass against repaired artifacts.
7. **Unrelated behavior is preserved.** Unrelated plan fingerprints and branch hashes must remain unchanged.
8. **Risk controls autonomy.** Permission, authentication, endpoint, destructive, and secret-related patches are elevated.
9. **Approval evidence is signed.** The signature binds the patch identity, verification report, and approval record.
10. **Every generated repair has rollback operations.** Replacements restore prior values; additions are removed; removals are restored.
11. **Deployment is atomic.** Repaired artifacts are written to a staging directory and atomically promoted.
12. **Recovery is owner-scoped.** Only quarantined events belonging to the repaired owner are replayed.

## Drift Observations

A drift observation records:

- Drift category
- Event and contract version
- Exact action, route, branch, and ownership key
- Active specialist IDs
- Stable failure signature
- Expected local contract
- Observed external evidence
- Structured repair evidence when available

Supported categories are schema, endpoint, permission, authentication, behavior, semantic, volume, latency, and unknown.

## Repair Candidate

A candidate contains:

- Base and proposed contract versions
- Exact repair owner
- Risk level
- Forward patches
- Rollback patches
- Expected scope
- Candidate hash
- Verification report
- Approval record
- Signature
- Lifecycle state

The deterministic generator currently supports:

- Target field rename
- Newly required target field
- Permission contract change
- Endpoint path change
- Explicit bounded patch sets produced by an external generator

Model-assisted repair generators may emit the same format, but they receive no privileged path around policy or verification.

## Verification Gates

The verifier creates repaired artifacts in memory and runs:

- Cross-artifact contract validation
- Impacted historical replay
- Optional adapter execution against a sandbox implementation
- Unrelated event plan-fingerprint comparison
- Unrelated Dendritron branch-hash comparison

The verifier excludes contract and tissue version metadata from unrelated behavior fingerprints, while retaining route, action, payload, certification, and ownership semantics.

## Risk Policy

Default behavior:

- Low-risk mapping and schema repairs may qualify for policy approval after verification.
- Route-condition and transform changes are medium risk.
- Permission, authentication, endpoint, and removal operations are high risk.
- Secret-bearing or destructive-operation changes are critical.

The packaged implementation still requires an explicit approval record before signing. A policy service may act as the approver for verified low-risk candidates.

## Signature

Phase 3 uses HMAC-SHA256 as the portable reference signer. Production installations should replace or wrap it with a customer KMS, HSM, or signing service.

The signed payload binds:

- Candidate hash
- Complete verification report
- Complete approval record

Changing any of those invalidates the signature.

## Deployment and Recovery

Deployment:

- Verifies the signature
- Reapplies patches from the approved base artifacts
- Revalidates the contract
- Rebinds the tissue to the new contract version
- Re-enables the repaired owner
- Records repair provenance in tissue metadata
- Writes a deployment bundle atomically

Recovery replays pending quarantined events with new event and idempotency identifiers. Successful events are marked recovered; failures remain quarantined.

## CLI

```bash
# Run all Phase 3 gates
difoundry benchmark-phase3 --output reports/phase3-benchmark.json

# Propose from a structured drift observation
difoundry repair-propose \
  --contract integration-contract.yaml \
  --profile source.yaml \
  --profile target.yaml \
  --drift drift-observation.yaml \
  --output repair.json

# Verify using impacted and unrelated historical events
difoundry repair-verify \
  --contract integration-contract.yaml \
  --profile source.yaml \
  --profile target.yaml \
  --tissue dendritron-tissue.json \
  --candidate repair.json \
  --event impacted-event.yaml \
  --event unrelated-event.yaml \
  --impacted-event-id evt_impacted

# Approve, sign, and deploy
difoundry repair-approve --candidate repair.json --approver "policy:low-risk"
difoundry repair-sign --candidate repair.json --key-file signing.key --key-id local-kms
difoundry repair-deploy \
  --contract integration-contract.yaml \
  --profile source.yaml \
  --profile target.yaml \
  --tissue dendritron-tissue.json \
  --candidate repair.json \
  --key-file signing.key \
  --output deployments
```

## API

Phase 3 adds:

- `POST /drifts`
- `POST /repairs/propose`
- `GET /repairs`
- `POST /repairs/{repair_id}/verify`
- `POST /repairs/{repair_id}/approve`
- `POST /repairs/{repair_id}/sign`
- `POST /repairs/{repair_id}/deploy`
- `GET /quarantines`

API signing and deployment read the signing secret from `DIFOUNDRY_REPAIR_SIGNING_KEY`. Production deployments should inject this through a secret manager rather than a shell environment.

## Phase 3 Exit Gates

- Drift classified from committed execution or certification failure.
- Exact action and Dendritron owner retained.
- Failed event quarantined.
- Failed branch isolated without altering unrelated owners.
- Explicit repair candidate and executable rollback generated.
- Candidate and storage envelope hash-bound.
- Impacted event replay passes.
- Unrelated plan fingerprints remain unchanged.
- Unrelated tissue hashes remain unchanged.
- High-risk permission repair requires approval.
- Signature invalidates after patch, verification, or approval tampering.
- Unsigned repair cannot deploy.
- Deployment atomically emits repaired artifacts and manifest.
- Repaired tissue is rebound and the owner restored.
- Quarantined event succeeds after recovery replay.
- Phase 0, Phase 1, and Phase 2 tests remain green.

## Phase 4 Boundary

Phase 4 introduces inherited integration intelligence: sanitized repair patterns, connector behaviors, semantic mappings, and failure signatures may be distilled into reusable knowledge without transferring tenant payloads, secrets, or proprietary business rules.
