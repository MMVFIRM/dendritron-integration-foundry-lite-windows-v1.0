# Phase 2 — Dendritron-Native Daughter Runtime

## Objective

Give every generated daughter a persistent, sparse, locally adaptive routing tissue while preserving the deterministic contracts, certification gates, adapters, and replayable execution kernel from Phases 0 and 1.

Phase 2 does not make an unconstrained model responsible for production actions. The Dendritron tissue decides which owned pathway recognizes an event; the Integration Contract remains the hard authorization boundary, and deterministic software constructs and executes the action.

## Runtime Pipeline

```text
Canonical Event
      │
      ▼
Hard Contract Gate
      │
      ▼
Sparse Feature Encoder
      │
      ▼
Dendritron Ownership Tissue
  ├── Route owner
  ├── Branch owner
  └── Top-k local specialists
      │
      ├── unfamiliar / ambiguous ──► abstain + quarantine
      │
      ▼
Deterministic Mapping
      │
      ▼
Mandatory Certification
      │
      ▼
Adapter Execution
      │
      ▼
Branch-local outcome and failure attribution
```

## Core Invariants

1. **Contracts remain authoritative.** Learning cannot activate a branch whose hard conditions fail.
2. **Ownership is persistent.** Every route and branch has a stable ownership key bound to a contract ID and version.
3. **Activation is sparse.** Only the top-k specialists within the selected owner become active.
4. **Novelty fails closed.** A trained route abstains when no specialist reaches its configured similarity threshold.
5. **Ambiguity fails closed.** A route abstains when the ownership margin is too small.
6. **Updates are local.** Training changes only the explicitly named branch and its specialists.
7. **Outcomes are local.** Success or failure updates only the branch and specialists that owned the action.
8. **Simulation is non-mutating.** Dry-run traffic does not update daughter health or learning state.
9. **State is reproducible.** Tissue files are SHA-256 bound and written atomically.
10. **Phase 0/1 interfaces remain valid.** Profiles, contracts, discovery, composition, mapping, certification, adapters, and replay are retained.

## Tissue Anatomy

### Tissue State

A tissue is bound to exactly one Integration Contract version. It contains:

- Tissue identity and global version
- Configuration thresholds
- Route/branch ownership records
- Local branch versions
- Sparse specialists
- Branch and specialist health
- Failure attributions
- Source and runtime metadata

### Branch Owner

Each branch owner has:

- `route_id`
- `branch_id`
- Stable `ownership_key`
- Local specialists
- Observation, success, and failure counts
- Independent local version
- Independent enabled/disabled state
- Last failure signature

### Specialist

A specialist owns a sparse prototype of observed event structure:

- Exact path/value features
- Field-existence features
- String token features
- Type features
- Collection-size features
- Numeric sign and magnitude features

Similarity uses inspectable weighted Jaccard activation. This is deliberately not an opaque embedding dependency. The feature and activation interfaces can later be replaced by a more advanced Dendritron implementation without changing contracts or execution.

## Learning

Training examples explicitly name the branch that should own an event:

```yaml
examples:
  - event:
      source_system: source
      source_object: record
      event_type: upsert
      idempotency_key: training-001
      payload:
        segment: smb
        region: east
    route_id: dispatch
    branch_id: smb_east
    reward: 1.0
```

Positive feedback either:

- Updates the closest local specialist, or
- Grows a new specialist when similarity is below the branch spawn threshold.

Negative feedback weakens only the named branch specialist. There is no platform-wide gradient and no implicit update to neighboring owners.

## Novelty and Abstention

A route is considered trained after any owner contains specialists. From that point forward:

- Untrained sibling branches are treated as unknown, not implicitly trusted.
- Low specialist similarity produces novelty abstention.
- Insufficient separation between the best two owners produces ownership abstention.
- Hard-condition failures always produce contract-gate abstention.

The trace records:

- Every branch activation
- Hard and learned activation components
- Selected specialists
- Novelty score
- Ownership key
- Tissue version
- Sparse active/available specialist counts
- Abstention reason

## Failure Attribution

Every planned action carries its routing ownership metadata:

- Route ID
- Branch ID
- Specialist IDs
- Ownership key

On committed execution failure, the runtime creates a failure attribution containing:

- Action ID
- Exact owning route and branch
- Active specialists
- Stable normalized failure signature
- Tissue version
- Observation time

Unrelated owners are not modified.

## Persistence and Integrity

Tissue state is stored in a versioned JSON envelope:

```json
{
  "format": "difoundry-dendritron-tissue-v1",
  "state_hash": "sha256...",
  "state": {}
}
```

Writes use a temporary file, flush and `fsync`, followed by atomic replacement. Loads recompute the state hash and reject tampering.

Generated daughter bundles now include:

```text
runtime/
├── dendritron-tissue.json
└── training-examples.yaml
```

Both are included in the daughter artifact manifest.

## CLI

```bash
# Initialize a tissue from any Integration Contract
difoundry tissue-init \
  --contract integration-contract.yaml \
  --output daughter.tissue.json

# Train branch ownership
difoundry tissue-train \
  --contract integration-contract.yaml \
  --tissue daughter.tissue.json \
  --examples training-examples.yaml

# Inspect local ownership and health
difoundry tissue-inspect --tissue daughter.tissue.json

# Verify the bound state hash
difoundry tissue-verify --tissue daughter.tissue.json

# Use the tissue during simulation or execution
difoundry simulate \
  --profile source-profile.yaml \
  --profile target-profile.yaml \
  --contract integration-contract.yaml \
  --event event.yaml \
  --tissue daughter.tissue.json
```

## API

Phase 2 adds:

- `POST /tissues/{contract_id}`
- `GET /tissues`
- `GET /tissues/{tissue_id}/summary`
- `POST /tissues/{tissue_id}/train`
- `POST /simulate/{contract_id}?tissue_id=...`

The API registry holds live tissue runtime instances so training and execution share one lock-protected owner graph.

## Benchmark

The Phase 2 benchmark deliberately creates three branches with identical hard contract conditions. The declarative baseline can only choose by static priority. The Dendritron tissue must learn local event patterns, activate only top-k specialists, and abstain on unfamiliar patterns.

Packaged benchmark gates:

- Static-priority synthetic holdout accuracy: see `reports/phase2-benchmark.json`
- Dendritron synthetic holdout accuracy: see `reports/phase2-benchmark.json`
- Synthetic novelty abstention rate: see `reports/phase2-benchmark.json`

These values come from a deterministic, hand-structured combinatorial fixture. They are architecture smoke-test results, not production integration accuracy, generalization evidence, or a trained-baseline superiority claim.
- Mean active specialist fraction: `0.2500`
- Branch-scoped adaptation: pass
- Damage isolation: pass
- Persistence round trip: pass

The benchmark is synthetic and demonstrates runtime properties; it is not evidence of universal superiority over every routing model.

## Phase 2 Exit Gates

- Persistent tissue initialized from an arbitrary contract.
- Stable route and branch ownership keys.
- Sparse top-k specialist activation.
- Exact hard-gate preservation.
- Known-pattern branch ownership benchmark.
- Novel-event abstention benchmark.
- Low-margin ownership abstention.
- Branch-scoped learning.
- Partial-training safety.
- Failure attribution to exact owner.
- Damage isolation after branch disablement.
- Non-mutating simulation.
- Atomic hash-bound persistence.
- Tissue lifecycle through CLI and API.
- Tissue artifacts embedded in generated daughter bundles.
- Full Phase 0 and Phase 1 regression suite retained.

## Phase 3 Boundary

Phase 3 adds bounded self-repair:

- Drift-event classification
- Contract-delta analysis
- Repair candidate generation
- Branch-local patch construction
- Historical replay and unrelated-path regression
- Risk-tier approval rules
- Signed patch deployment and rollback
- Quarantined-event recovery

Phase 2 supplies the exact owner and failure locus that Phase 3 needs in order to repair locally rather than regenerate or retrain an entire daughter.
