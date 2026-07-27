from difoundry.certification import CertificationEngine
from difoundry.discovery import DiscoveryService
from difoundry.models import (
    CertificationResult,
    CertifierDefinition,
    DiscoveryResult,
    DiscoverySource,
    OperationProfile,
    SystemProfile,
)


class ProprietaryDiscoveryProvider:
    name = "proprietary_rfc"
    formats = ("proprietary_rfc",)

    def can_handle(self, source: DiscoverySource) -> bool:
        return source.format == "proprietary_rfc"

    def discover(self, source: DiscoverySource) -> DiscoveryResult:
        return DiscoveryResult(
            provider=self.name,
            source_id=source.source_id,
            profile=SystemProfile(
                system_id=source.system_id or "proprietary",
                name=source.name or "Proprietary System",
                protocol="sap_rfc",
            ),
        )


def test_custom_discovery_format_and_protocol_require_no_core_model_change():
    service = DiscoveryService()
    service.register(ProprietaryDiscoveryProvider(), first=True)
    result = service.discover(
        DiscoverySource(format="proprietary_rfc", document="opaque descriptor", system_id="sap_core")
    )
    assert result.profile.protocol == "sap_rfc"
    assert result.provider == "proprietary_rfc"
    assert "proprietary_rfc" in service.formats()


def test_custom_certifier_can_be_registered_without_changing_contract_models():
    engine = CertificationEngine()
    engine.register(
        "tenant_boundary",
        lambda payload, config, operation, permissions: (
            payload.get("tenant_id") == config.get("tenant_id"),
            "tenant matched" if payload.get("tenant_id") == config.get("tenant_id") else "tenant mismatch",
        ),
    )
    results = engine.certify(
        {"tenant_id": "tenant_1"},
        [CertifierDefinition(kind="tenant_boundary", config={"tenant_id": "tenant_1"})],
        OperationProfile(operation_id="write", method="CUSTOM", path="write"),
        [],
    )
    custom = next(result for result in results if result.kind == "tenant_boundary")
    assert custom.passed is True
