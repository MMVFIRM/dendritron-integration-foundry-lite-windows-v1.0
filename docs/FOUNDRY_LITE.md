# Foundry Lite v0.1 Architecture

## Purpose

Foundry Lite is the small-organization edition of Dendritron Integration Foundry. It removes enterprise administration while preserving the daughter-system guarantees.

## Components

```text
Browser UI
   ↓
Local FastAPI application
   ├── autonomous discovery
   ├── chat intent interpreter
   ├── daughter composer
   ├── preview simulator
   ├── encrypted local vault
   ├── SQLite state and activity
   └── embedded event runner
          ↓
Task-specific Dendritron daughters
          ↓
Connected external systems
```

## One workspace

The database initializes one workspace named `My Foundry`. There are no users or login records. The local operating-system account and application data directory form the administrative boundary.

## System lifecycle

1. User provides a name, base URL, authorization type, and credentials.
2. Credentials are encrypted immediately.
3. Autonomous discovery probes safe schema and capability surfaces.
4. A live System Profile is persisted with evidence and warnings.
5. The UI exposes plain-language object and operation summaries.

## Connection lifecycle

1. User selects a source and one or more targets.
2. User describes the outcome in chat.
3. The Task Interpreter selects source object, trigger, conditions, target objects, and operations.
4. The composer builds semantic graphs and an Integration Contract.
5. Missing technical mappings are inferred where defensible.
6. Missing business values become explicit questions.
7. The simulator generates a safe request preview.
8. The daughter bundle is written and hash-bound.
9. The user enables the connection only after required questions are resolved.
10. Polling or webhooks feed the embedded runner.

## Exactness boundary

```text
Discovered event
      ↓
Explicit trigger condition
      ↓
Dendritron route ownership
      ↓
Deterministic mapping
      ↓
Required-field and permission certification
      ↓
Adapter execution
```

Unresolved business constants are represented by an explicit internal placeholder. They satisfy the structural need for a reviewable scaffold but fail required-field certification until the user supplies a value. This behavior exists only in the Lite composer; the enterprise composer remains fail-closed at composition time.

## Triggering

Foundry Lite supports two trigger paths:

- **Webhook:** every connection receives a high-entropy local webhook path.
- **Polling:** when discovery finds a list, search, or read operation, the runner baselines the first result set and later emits changes using record fingerprints.

Polling currently occurs every 30 seconds while the app process is running.

## Export

Every connection can be exported as a ZIP containing the complete daughter artifact. Export does not include plaintext system credentials or the local vault key.
