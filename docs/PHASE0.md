# Phase 0 — Integration Simulator Kernel

## Objective

Create the invariant platform substrate before autonomous discovery or daughter generation is introduced.

Phase 0 proves that arbitrary system descriptions can be loaded into the same runtime and processed through the same contract, routing, mapping, certification, execution, ledger, and replay interfaces.

## Invariants

1. No vendor-specific fields or endpoints exist in core code.
2. System behavior enters through versioned System Profiles or adapter plugins.
3. Event routing is explicit, sparse, traceable, and allowed to abstain.
4. Mappings are declarative and transformations are deterministic.
5. Confidence never replaces certification.
6. Event effects are protected by idempotency.
7. Every plan and execution is reconstructable.
8. Replays use the same platform kernel as live events.
9. Live HTTP execution is disabled unless the caller explicitly requests it.
10. The routing port can be replaced by a trained Dendritron without changing contract or execution APIs.

## Phase 0 Exit Gates

- Load multiple arbitrary System Profiles and protocols without core-code changes.
- Validate a generic Integration Contract.
- Route a canonical event to an owned branch.
- Abstain on unknown events rather than guessing.
- Produce deterministic mapped payloads and stable behavior hashes across replay.
- Block operations that fail mandatory certification or request-schema validation.
- Prevent duplicate effects using idempotency keys.
- Record event, plan, and result artifacts.
- Replay historical events through the same planner.
- Expose the kernel through CLI and HTTP API.
