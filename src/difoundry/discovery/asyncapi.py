from __future__ import annotations

from typing import Any

from ..models import AuthenticationProfile, DiscoveryEvidence, DiscoveryResult, DiscoverySource, ObjectProfile, OperationProfile, SystemProfile
from ..naming import singularize, slugify
from .schema import normalize_schema, object_from_schema


class AsyncAPIDiscoveryProvider:
    name = "asyncapi"
    formats = ("asyncapi",)

    def can_handle(self, source: DiscoverySource) -> bool:
        if source.format == "asyncapi":
            return True
        return isinstance(source.document, dict) and "asyncapi" in source.document

    def discover(self, source: DiscoverySource) -> DiscoveryResult:
        if not isinstance(source.document, dict):
            raise ValueError("AsyncAPI discovery requires an object document")
        document = source.document
        info = document.get("info", {})
        title = source.name or str(info.get("title") or source.system_id or "Discovered Event System")
        system_id = source.system_id or slugify(title)
        objects = self._objects(document)
        operations: list[OperationProfile] = []
        evidence: list[DiscoveryEvidence] = []
        warnings: list[str] = []
        for channel_name, channel in document.get("channels", {}).items():
            if not isinstance(channel, dict):
                continue
            for action_name, kind in (("publish", "publish"), ("subscribe", "subscribe")):
                action = channel.get(action_name)
                if not isinstance(action, dict):
                    continue
                message = action.get("message", {})
                if "$ref" in message:
                    message = normalize_schema(document, message)
                payload = normalize_schema(document, message.get("payload", {}) if isinstance(message, dict) else {})
                message_name = str(message.get("name") or action.get("operationId") or channel_name)
                object_id = singularize(message_name)
                operation_id = slugify(str(action.get("operationId") or f"{action_name}_{channel_name}"))
                permissions = [str(item) for item in action.get("security", []) if isinstance(item, str)]
                operations.append(
                    OperationProfile(
                        operation_id=operation_id,
                        method=action_name.upper(),
                        path=str(channel_name),
                        description=str(action.get("description") or action.get("summary") or ""),
                        object_id=object_id,
                        operation_kind=kind,
                        request_schema=payload if action_name == "publish" else {},
                        response_schema=payload if action_name == "subscribe" else {},
                        required_permissions=permissions,
                        idempotency_supported=bool(action.get("x-idempotency-supported", False)),
                        metadata={"bindings": channel.get("bindings", {}), "message": message_name},
                    )
                )
                if payload and not any(obj.object_id == object_id for obj in objects):
                    objects.append(object_from_schema(object_id, payload, name=message_name))
                evidence.append(DiscoveryEvidence(artifact=operation_id, location=f"channels.{channel_name}.{action_name}", statement=f"Discovered {action_name} channel {channel_name}"))
        if not operations:
            warnings.append("No publish or subscribe channel operations were discovered")
        profile = SystemProfile(
            system_id=system_id,
            name=title,
            version=str(info.get("version") or "1"),
            protocol="queue",
            base_url=source.base_url,
            authentication=self._authentication(document),
            operations=operations,
            objects=objects,
            metadata={"discovery_format": "asyncapi", "specification_version": str(document.get("asyncapi")), **source.metadata},
        )
        return DiscoveryResult(provider=self.name, source_id=source.source_id, profile=profile, warnings=warnings, evidence=evidence)

    @staticmethod
    def _objects(document: dict[str, Any]) -> list[ObjectProfile]:
        schemas = document.get("components", {}).get("schemas", {})
        return [object_from_schema(singularize(name), normalize_schema(document, schema), name=name) for name, schema in schemas.items()]

    @staticmethod
    def _authentication(document: dict[str, Any]) -> AuthenticationProfile:
        schemes = document.get("components", {}).get("securitySchemes", {})
        for name, scheme in schemes.items():
            kind = str(scheme.get("type", "")).lower()
            if kind in {"apikey", "httpapikey"}:
                return AuthenticationProfile(kind="api_key", secret_refs={"api_key": f"{slugify(name)}_api_key"}, config={"scheme": name})
            if kind in {"oauth2", "openidconnect"}:
                return AuthenticationProfile(kind="oauth2", secret_refs={"token": f"{slugify(name)}_token"}, config={"scheme": name})
            if kind in {"x509", "mutualtls"}:
                return AuthenticationProfile(kind="mtls", secret_refs={"certificate": "certificate", "private_key": "private_key"}, config={"scheme": name})
        return AuthenticationProfile()
