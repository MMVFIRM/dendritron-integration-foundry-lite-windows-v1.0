"""Minimal example of adding a proprietary discovery format."""

from difoundry.discovery import DiscoveryService
from difoundry.models import DiscoveryResult, DiscoverySource, SystemProfile


class ProprietaryProvider:
    name = "proprietary_descriptor"
    formats = ("proprietary_descriptor",)

    def can_handle(self, source: DiscoverySource) -> bool:
        return source.format == "proprietary_descriptor"

    def discover(self, source: DiscoverySource) -> DiscoveryResult:
        return DiscoveryResult(
            provider=self.name,
            source_id=source.source_id,
            profile=SystemProfile(
                system_id=source.system_id or "proprietary_system",
                name=source.name or "Proprietary System",
                protocol="vendor_protocol",
                metadata={"descriptor": str(source.document)},
            ),
        )


service = DiscoveryService()
service.register(ProprietaryProvider(), first=True)
result = service.discover(
    DiscoverySource(
        format="proprietary_descriptor",
        document="opaque but provider-readable descriptor",
        system_id="example_proprietary",
    )
)
print(result.profile.model_dump_json(indent=2))
