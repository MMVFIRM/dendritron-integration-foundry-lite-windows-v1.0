from __future__ import annotations

from ..models import DiscoveryEvidence, DiscoveryResult, DiscoverySource, SystemProfile
from ..naming import singularize, slugify
from .schema import normalize_schema, object_from_schema


class JSONSchemaDiscoveryProvider:
    name = "json_schema"
    formats = ("json_schema",)

    def can_handle(self, source: DiscoverySource) -> bool:
        if source.format == "json_schema":
            return True
        return isinstance(source.document, dict) and (
            "$schema" in source.document or "properties" in source.document or source.document.get("type") == "object"
        )

    def discover(self, source: DiscoverySource) -> DiscoveryResult:
        if not isinstance(source.document, dict):
            raise ValueError("JSON Schema discovery requires an object document")
        document = normalize_schema(source.document, source.document)
        title = source.name or str(document.get("title") or source.system_id or "Discovered Schema System")
        system_id = source.system_id or slugify(title)
        object_id = singularize(str(source.metadata.get("object_id") or document.get("title") or "record"))
        profile = SystemProfile(
            system_id=system_id,
            name=title,
            version=str(source.metadata.get("version", "1")),
            protocol="custom",
            base_url=source.base_url,
            objects=[object_from_schema(object_id, document, name=str(document.get("title") or object_id))],
            metadata={"discovery_format": "json_schema", "schema_only": True, **source.metadata},
        )
        return DiscoveryResult(
            provider=self.name,
            source_id=source.source_id,
            profile=profile,
            warnings=["JSON Schema describes data shape but not executable operations; an adapter or operation manifest is still required"],
            evidence=[DiscoveryEvidence(artifact=object_id, location="$", statement=f"Discovered object schema {object_id}")],
        )
