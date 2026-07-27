# Phase 5 — Multi-System Nervous System

Phase 5 expands the Foundry from independently operating daughter integrations into a coordinated system of daughters governed by a Mother control plane.

## Architectural boundary

The Mother system owns:

- Cross-daughter causal routing
- Global policy
- Workflow dependency graphs
- Capability discovery
- Correlation and causation identifiers
- Distributed idempotency
- Global lineage
- Fan-out and hop limits

Each daughter retains exclusive ownership of:

- Its Integration Contract
- Its Dendritron tissue
- Its route and branch owners
- Its adapters and credentials
- Its state and idempotency ledger
- Its execution outcomes
- Its drift and repair boundary

The Mother can select a registered capability. It cannot directly activate an undeclared daughter route, rewrite a local contract, mutate a local tissue, or bypass local certification.

## Capability-scoped dispatch

A daughter advertises explicit capabilities. Every capability is bound to one or more local route identifiers.

```text
Global workflow step
        ↓
Global policy decision
        ↓
Registered daughter capability
        ↓
Capability-bound local route set
        ↓
Dendritron local ownership
        ↓
Deterministic mapping and certification
        ↓
Adapter execution
```

This prevents a cross-system instruction from activating unrelated routes in a multi-route daughter.

## Causal event fabric

Every cross-daughter message contains:

- Root event identifier
- Correlation identifier
- Causation identifier
- Workflow and step identifiers
- Source and target daughters
- Capability identifier
- Hop count
- Payload
- Policy metadata

The nervous ledger records every message and the final coordination result. The lineage hash binds stable execution facts, local contract versions, Dendritron ownership keys, behavior hashes, policy rules, statuses, and errors.

## Global policy

The policy engine is fail-closed by default. Rules can:

- Allow a transition
- Deny a transition
- Require explicit approval

Rules may target source daughter, target daughter, capability, event type, and exact metadata. Higher-priority rules win. Capability registration remains mandatory even when a broad allow rule exists.

## Failure isolation

A daughter failure changes only the executing daughter’s local tissue health and failure attribution. Independent branches continue. Dependent steps are skipped rather than receiving fabricated outputs.

The benchmark proves that a provisioning daughter can fail while identity, billing, and analytics daughters continue successfully.

## Workflow safety

Phase 5 rejects:

- Dependency cycles
- Unknown daughters
- Undeclared capabilities
- Duplicate root-event/workflow execution
- Excessive root or branch fan-out
- Excessive hop count
- Missing required coordination inputs
- Globally denied transitions

## Hash-bound topology

The complete global topology can be exported as a hash-bound bundle containing:

- Global policy
- Daughter registrations
- Capability-to-route bindings
- Coordination workflows

The storage envelope and internal topology hash detect modification.

## Reference benchmark

The Phase 5 benchmark coordinates four independent daughters:

```text
External customer event
        ├── Identity daughter
        │      ├── Billing daughter
        │      └── Provisioning daughter (injected failure)
        └── Analytics daughter
```

The gate verifies:

- Four distinct local contracts
- Independent fan-out
- Dependency handoff
- Exact Dendritron owner lineage
- Local failure attribution
- Continued unrelated execution
- Global policy denial
- Distributed idempotency
- Cycle rejection
- Topology round trip
- Topology tamper detection

## Current boundary

This reference runtime coordinates workflow steps deterministically. It does not yet provide a distributed consensus protocol across multiple Mother replicas, transactional compensation across daughter boundaries, or production identity attestation for daughter registrations. Those are production-hardening layers rather than reasons to merge daughter ownership into the control plane.
