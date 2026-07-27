from __future__ import annotations

from ..models import DiscoveryEvidence, DiscoveryResult, DiscoverySource, SystemProfile


class SystemProfileDiscoveryProvider:
    name = "system_profile"
    formats = ("system_profile",)

    def can_handle(self, source: DiscoverySource) -> bool:
        if source.format == "system_profile":
            return True
        return isinstance(source.document, dict) and {"system_id", "name", "protocol"}.issubset(source.document)

    def discover(self, source: DiscoverySource) -> DiscoveryResult:
        if not isinstance(source.document, dict):
            raise ValueError("System Profile discovery requires an object document")
        profile = SystemProfile.model_validate(source.document)
        return DiscoveryResult(
            provider=self.name,
            source_id=source.source_id,
            profile=profile,
            evidence=[DiscoveryEvidence(artifact=profile.system_id, location="$", statement="Loaded native System Profile")],
        )
