# Phase 4 — Inherited Integration Intelligence

## Objective

Phase 4 allows one daughter’s verified structural experience to improve future daughters without transferring tenant payloads, credentials, identities, or executable code.

The lifecycle is:

```text
Verified composition, repair, or tissue
                │
                ▼
      Structural pattern exporter
                │
                ▼
        Privacy-policy scanner
                │
                ▼
 Consent scope + artifact provenance
                │
                ▼
  Content-hash consensus aggregation
                │
                ▼
      Eligibility and trust policy
                │
                ▼
 New daughter receives inherited evidence
                │
                ▼
 Existing verification and execution gates
```

## Core Invariants

1. **No payload transfer.** Event bodies, records, adapter responses, and Dendritron event prototypes are excluded.
2. **No secret transfer.** Credentials, tokens, private keys, URLs containing access material, and secret-like keys are rejected.
3. **No tenant identity transfer.** System IDs, tenant IDs, user IDs, emails, phone numbers, UUIDs, IP addresses, and local paths are not exported.
4. **No executable transfer.** Patterns contain structural descriptions, not generated code or arbitrary transform constants.
5. **Consent controls scope.** Only `sanitized_shared` provenance enters the cross-tenant eligible pool.
6. **One origin cannot establish truth.** Single-origin patterns remain quarantined.
7. **Advice and authority differ.** Basic consensus can provide advice; stronger consensus is required to resolve review automatically.
8. **Inheritance is evidence.** It cannot override hard Integration Contract conditions or any certifier.
9. **Every pattern is provenance-bound.** Artifact hashes and pseudonymous origin hashes are retained.
10. **Every pack is integrity-bound.** Pattern hashes, pack hashes, storage hashes, and optional signatures detect modification.

## Pattern Types

### Semantic Mapping

A semantic pattern includes normalized structural features:

- Source field terms and type
- Target field terms and type
- Source and target object terms
- Required/optional status
- Mapping relation
- Safe named transforms
- Confidence and independent-origin support

The pattern excludes source and target system IDs.

### Repair Strategy

A repair pattern includes:

- Drift category
- Risk tier
- Patch artifact kind
- Generalized JSON-Pointer shape
- Patch operation
- Value type, not value
- Rollback availability
- Hash-only failure signatures

It does not contain the actual patched field name, endpoint, permission, or customer-specific value.

### Routing Topology

A topology pattern includes reusable Dendritron configuration and structural counts. It excludes specialist prototypes and event-feature values.

## Privacy Enforcement

`PrivacySanitizer` recursively inspects exported payloads. The default policy rejects:

- Secret-bearing keys
- Payload or record-bearing keys
- Tenant, user, customer, email, and phone identifiers
- Email addresses
- URLs
- IP addresses
- UUIDs
- Local absolute paths
- Excessively long literals

Exporters further minimize data before inspection. Semantic descriptions are not copied, transform dictionaries are dropped, and repair values are reduced to types and generalized paths.

## Consent Scopes

Each provenance record declares one scope:

- `private`: usable only by the originating daughter or tenant
- `organization`: usable inside one organization
- `sanitized_shared`: eligible for cross-tenant aggregation

The shared registry ignores origins whose scope is not allowed by policy.

## Consensus

Patterns are deduplicated by a content hash over:

- Pattern kind
- Schema version
- Sanitized payload

The hash intentionally excludes origin metadata, timestamps, confidence aggregation, and pattern IDs. Matching structures from different origins therefore converge on one registry entry.

Default policy:

- Minimum two distinct shared origins for eligibility
- Minimum three distinct origins for automatic semantic acceptance
- Minimum confidence and pattern-fit thresholds
- Privacy report must pass

## Inherited Semantic Matching

The inherited matcher runs after the deterministic baseline. It compares eligible patterns against every source/target field pair and may boost the existing mapping evidence.

Every inherited edge records:

- Pattern hash
- Number of independent origins
- Pattern-fit score
- Baseline score

A review question is removed only when:

- The inherited score passes the requested review threshold
- The pattern passes privacy and consent policy
- The pattern has enough independent origins for automatic acceptance

The resulting mapping still passes the ordinary contract validator and request-schema certifier.

## Inherited Repair Advice

The repair advisor retrieves eligible strategies matching a drift category. It returns generalized patch shapes and rollback availability.

It does not generate or deploy a patch by itself. Phase 3 still requires:

- Exact owner attribution
- Explicit repair values
- Historical replay
- Unrelated-path regression
- Risk approval
- Signature
- Atomic deployment

## Integrity

### Pattern Hash

Binds the sanitized structural meaning of one pattern.

### Pack Hash

Binds the complete list of patterns and pack metadata.

### Storage Hash

Binds the serialized pack envelope on disk.

### Optional Signature

The reference signer uses HMAC-SHA256 over the pack hash. Production installations should use an organization KMS, HSM, or asymmetric signing service.

## CLI

```bash
difoundry benchmark-phase4 --output reports/phase4-benchmark.json

difoundry intelligence-export \
  --composition composition-result.json \
  --profile source.yaml \
  --profile target.yaml \
  --origin-ref authenticated-origin \
  --output daughter-pack.json

difoundry intelligence-verify --pack daughter-pack.json
difoundry intelligence-inspect --pack daughter-pack.json

difoundry intelligence-merge \
  --pack daughter-a.json \
  --pack daughter-b.json \
  --pack daughter-c.json \
  --minimum-origins 3 \
  --output inherited-pack.json
```

## API

Phase 4 adds:

- `POST /intelligence/patterns`
- `GET /intelligence/patterns`
- `POST /intelligence/export/compositions/{composition_id}`
- `POST /intelligence/export/repairs/{repair_id}`
- `GET /intelligence/pack`
- `POST /intelligence/pack`
- `GET /intelligence/repair-advice/{drift_kind}`
- `POST /compose?inherit=true`

## Phase 4 Exit Gates

- Verified semantic structures export without system IDs or payloads.
- Private and organization-only patterns remain outside the shared pool.
- Equivalent patterns from independent origins merge by content hash.
- Single-origin patterns remain quarantined.
- Strongly corroborated inherited evidence can reduce semantic review.
- Inherited repair strategy is advisory and cannot bypass Phase 3.
- Intelligence packs round-trip without modification.
- Storage tampering is detected.
- Optional signature verification passes and fails under the wrong key.
- Generated daughter bundles include inheritance provenance.
- Phase 0 through Phase 3 tests remain green.

## Production Boundary

The reference implementation receives an `origin_ref` from its caller and stores only its hash. A production service must derive that origin from authenticated tenant context or verify a signed origin attestation. Without that binding, an attacker could create multiple self-asserted origins and simulate consensus.

## Phase 5 Boundary

Phase 5 expands pairwise daughters into a coordinated multi-system nervous system. Multiple daughters will cooperate under global policy while retaining local ownership, isolation, state, and repair boundaries.
