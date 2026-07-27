# Phase 1 — Discovery and Composition Layer

## Objective

Turn unfamiliar external system descriptions into inspectable, versioned artifacts that the Phase 0 runtime can execute without vendor-specific changes.

Phase 1 is the first daughter-construction layer. It does not assume that a connector already exists.

## Pipeline

```text
Specification / Schema / Manifest
                ↓
        Discovery Provider
                ↓
          System Profile
                ↓
       Semantic Graph Builder
                ↓
        Daughter Composer
                ↓
 Contract + Manifest + Verification Bundle
                ↓
          Artifact Bundle
                ↓
        Phase 0 Runtime
```

## Invariants

1. Discovery providers are plugins, not conditionals embedded throughout the platform.
2. Every discovered profile is bound to its source document by SHA-256.
3. System objects and operations are separate concepts.
4. Target mappings are made against the writable operation input schema.
5. Semantic matches include scores and evidence.
6. Ambiguity produces questions or refusal, never silent invention.
7. One daughter may fan out to multiple systems and protocols.
8. Generated contracts must pass cross-artifact validation.
9. Daughter bundles contain reproducible profiles, graphs, contracts, verification cases, and hashes.
10. Phase 0 execution and replay interfaces remain unchanged.

## Built-In Discovery Providers

| Provider | Input | Output protocol | Executable operation discovery |
|---|---|---:|---:|
| OpenAPI | YAML/JSON | REST | Yes |
| AsyncAPI | YAML/JSON | Queue | Yes |
| GraphQL | Introspection JSON | GraphQL | Yes |
| SQL | DDL text | SQL | Yes, generic CRUD catalog |
| JSON Schema | YAML/JSON | Custom | No; schema-only warning |
| System Profile | YAML/JSON | Any supported protocol | As declared |

## Semantic Baseline

The deterministic matcher uses:

- Normalized field names
- Context-stripped field names
- A small explicit synonym catalog
- Type compatibility
- Identifier compatibility
- Description overlap
- Required-field agreement

The matcher is intentionally replaceable. A Dendritron semantic matcher or model-assisted evidence collector can produce the same `SemanticGraph` artifact later.

## Composition Outputs

### Integration Contract

Defines trigger, routes, actions, mappings, permissions, and certifiers in the Phase 0 DSL.

### Semantic Graphs

One graph per source-object/target-operation relationship, including nodes, edges, scores, evidence, transforms, and questions.

### Daughter Manifest

Declares daughter identity, owned systems and objects, adapter requirements, permissions, state capabilities, trust policy, source versions, and source hashes.

### Verification Bundle

Scaffolds required gates for:

- Contract references
- Mapping completeness
- Request schemas
- Permissions
- Idempotency
- Deterministic replay
- Drift
- Failure isolation

### Artifact Manifest

Contains SHA-256 and byte size for every generated file.

## Phase 1 Exit Gates

- Discover a REST system from OpenAPI.
- Discover an event system from AsyncAPI.
- Discover a GraphQL system from introspection data.
- Discover SQL objects and writable operations from DDL.
- Load JSON Schema and native System Profile sources.
- Bind every discovery to a stable source hash.
- Produce semantic mappings with evidence and explicit uncertainty.
- Map against target operation request schemas.
- Compose one source into multiple target protocols.
- Generate a contract accepted by the Phase 0 validator.
- Execute the generated contract in the Phase 0 simulator.
- Emit Daughter Manifest and Verification Bundle artifacts.
- Emit a hash-bound daughter bundle.
- Preserve all Phase 0 tests.
- Expose discovery and composition through CLI and API.

## Phase 1 Test Result

The packaged repository contains twenty automated tests covering Phase 0 compatibility, discovery providers, semantic composition, multi-target runtime execution, artifact generation, and API flow.

## Phase 2 Boundary

Phase 2 replaces or augments the declarative routing and semantic-selection baselines with a Dendritron-native daughter runtime:

- Persistent route ownership
- Sparse learned specialist activation
- Novelty detection
- Local failure attribution
- Branch-specific updates
- Damage isolation benchmarks

The Phase 1 artifacts remain the contracts and evidence surfaces consumed by that runtime.
