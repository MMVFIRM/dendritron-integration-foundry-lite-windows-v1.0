# Provider and Extension SDK

The platform is designed so that adding a system family does not require edits to discovery, semantic composition, runtime planning, or artifact models.

## 1. Discovery Provider

A provider converts an external description into a `DiscoveryResult` containing a `SystemProfile`.

```python
from difoundry.models import DiscoveryResult, DiscoverySource, SystemProfile


class ProprietaryRFCProvider:
    name = "proprietary_rfc"
    formats = ("proprietary_rfc",)

    def can_handle(self, source: DiscoverySource) -> bool:
        return source.format == "proprietary_rfc"

    def discover(self, source: DiscoverySource) -> DiscoveryResult:
        profile = SystemProfile(
            system_id=source.system_id or "proprietary_system",
            name=source.name or "Proprietary System",
            protocol="sap_rfc",
            objects=[],
            operations=[],
        )
        return DiscoveryResult(
            provider=self.name,
            source_id=source.source_id,
            profile=profile,
        )
```

Register it before discovery:

```python
from difoundry.discovery import DiscoveryService

service = DiscoveryService()
service.register(ProprietaryRFCProvider(), first=True)
```

The service adds the canonical source hash and provenance metadata automatically.

## 2. Adapter

An adapter executes an `OperationProfile` for one protocol or product family.

```python
class RFCAdapter:
    system_id: str

    def execute(
        self,
        operation,
        payload,
        path_parameters,
        query_parameters,
        idempotency_key,
        simulate,
    ):
        if simulate:
            return {"simulated": True, "operation": operation.operation_id, "payload": payload}
        # Invoke the external system here.
```

The planner and simulator only depend on the adapter protocol.

## 3. Transform

Register deterministic transforms on `TransformRegistry`:

```python
from difoundry.transforms import TransformRegistry

registry = TransformRegistry()
registry.register("normalize_customer_code", lambda value, config, context: str(value).strip().upper())
```

Contracts refer to the transform by name.

## 4. Certifier

Register bounded policy or business-rule certifiers:

```python
from difoundry.certification import CertificationEngine

engine = CertificationEngine()
engine.register(
    "tenant_boundary",
    lambda payload, config, operation, permissions: (
        payload.get("tenant_id") == config["tenant_id"],
        "tenant boundary evaluated",
    ),
)
```

An unknown required certifier fails closed rather than being ignored.

## 5. Semantic Matcher

`DaughterComposer` accepts any matcher that returns the standard `SemanticGraph` artifact.

```python
from difoundry.composition import DaughterComposer

composer = DaughterComposer(matcher=my_dendritron_matcher)
```

A future Dendritron matcher can therefore replace the deterministic baseline without changing daughter manifests, contracts, verification bundles, or runtime execution.

## 6. Exactness Requirements

A production provider should preserve:

- Source locations for discovered claims
- Schema versions
- Authentication and permission requirements
- Operation input and output separation
- Explicit unsupported capabilities
- Stable object and operation identifiers
- No credentials inside profiles
- No silent guessing when the source is incomplete
