from __future__ import annotations

from typing import Any

from ..models import DiscoveryEvidence, DiscoveryResult, DiscoverySource, ObjectFieldProfile, ObjectProfile, OperationProfile, SystemProfile
from ..naming import singularize, slugify


def _unwrap_type(type_ref: dict[str, Any] | None) -> tuple[str, bool]:
    required = False
    current = type_ref or {}
    while current.get("kind") in {"NON_NULL", "LIST"}:
        if current.get("kind") == "NON_NULL":
            required = True
        current = current.get("ofType") or {}
    return str(current.get("name") or "any"), required


def _json_type(graphql_type: str) -> str:
    return {
        "String": "string",
        "ID": "string",
        "Int": "integer",
        "Float": "number",
        "Boolean": "boolean",
        "JSON": "object",
    }.get(graphql_type, "object")


class GraphQLDiscoveryProvider:
    name = "graphql"
    formats = ("graphql",)

    def can_handle(self, source: DiscoverySource) -> bool:
        if source.format == "graphql":
            return True
        if not isinstance(source.document, dict):
            return False
        return "__schema" in source.document or "__schema" in source.document.get("data", {})

    def discover(self, source: DiscoverySource) -> DiscoveryResult:
        if not isinstance(source.document, dict):
            raise ValueError("GraphQL discovery requires an introspection object")
        schema = source.document.get("data", source.document).get("__schema", {})
        if not schema:
            raise ValueError("GraphQL introspection document does not contain __schema")
        title = source.name or source.system_id or "Discovered GraphQL System"
        system_id = source.system_id or slugify(title)
        types = {item.get("name"): item for item in schema.get("types", []) if item.get("name")}
        objects: list[ObjectProfile] = []
        evidence: list[DiscoveryEvidence] = []
        for type_name, type_spec in types.items():
            if type_spec.get("kind") not in {"OBJECT", "INPUT_OBJECT"} or str(type_name).startswith("__"):
                continue
            fields_spec = type_spec.get("fields") or type_spec.get("inputFields") or []
            fields: list[ObjectFieldProfile] = []
            identifiers: list[str] = []
            for field in fields_spec:
                raw_type, required = _unwrap_type(field.get("type"))
                name = str(field.get("name"))
                fields.append(
                    ObjectFieldProfile(
                        name=name,
                        path=name,
                        data_type=_json_type(raw_type),
                        required=required,
                        nullable=not required,
                        description=str(field.get("description") or ""),
                        metadata={"graphql_type": raw_type},
                    )
                )
                if name.lower() in {"id", "uuid", "guid", "key"}:
                    identifiers.append(name)
            if fields:
                object_id = singularize(type_name)
                objects.append(
                    ObjectProfile(
                        object_id=object_id,
                        name=str(type_name),
                        description=str(type_spec.get("description") or ""),
                        fields=fields,
                        identifiers=identifiers,
                        metadata={"graphql_kind": type_spec.get("kind")},
                    )
                )
                evidence.append(
                    DiscoveryEvidence(artifact=object_id, location=f"__schema.types.{type_name}", statement=f"Discovered GraphQL object {type_name}")
                )

        operations: list[OperationProfile] = []
        query_type = schema.get("queryType") or {}
        mutation_type = schema.get("mutationType") or {}
        subscription_type = schema.get("subscriptionType") or {}
        roots = [
            (query_type.get("name"), "QUERY", "read"),
            (mutation_type.get("name"), "MUTATION", "custom"),
            (subscription_type.get("name"), "SUBSCRIPTION", "subscribe"),
        ]
        for root_name, method, default_kind in roots:
            root = types.get(root_name) if root_name else None
            if not root:
                continue
            for field in root.get("fields", []):
                operation_name = str(field.get("name"))
                return_type, _ = _unwrap_type(field.get("type"))
                args = field.get("args", [])
                properties: dict[str, Any] = {}
                required: list[str] = []
                for arg in args:
                    arg_type, arg_required = _unwrap_type(arg.get("type"))
                    properties[str(arg.get("name"))] = {"type": _json_type(arg_type), "x-graphql-type": arg_type}
                    if arg_required:
                        required.append(str(arg.get("name")))
                request_schema: dict[str, Any] = {"type": "object", "properties": properties}
                if required:
                    request_schema["required"] = required
                kind = self._operation_kind(method, operation_name, default_kind)
                operations.append(
                    OperationProfile(
                        operation_id=slugify(operation_name),
                        method=method,
                        path=operation_name,
                        description=str(field.get("description") or ""),
                        object_id=singularize(return_type),
                        operation_kind=kind,
                        request_schema=request_schema,
                        response_schema={"x-graphql-type": return_type},
                        idempotency_supported=False,
                        metadata={"graphql_return_type": return_type},
                    )
                )
                evidence.append(
                    DiscoveryEvidence(artifact=operation_name, location=f"__schema.types.{root_name}.{operation_name}", statement=f"Discovered GraphQL {method.lower()} {operation_name}")
                )

        profile = SystemProfile(
            system_id=system_id,
            name=title,
            version=str(source.metadata.get("version", "1")),
            protocol="graphql",
            base_url=source.base_url,
            operations=operations,
            objects=objects,
            metadata={"discovery_format": "graphql", **source.metadata},
        )
        warnings = [] if operations else ["No GraphQL root operations were discovered"]
        return DiscoveryResult(provider=self.name, source_id=source.source_id, profile=profile, warnings=warnings, evidence=evidence)

    @staticmethod
    def _operation_kind(method: str, name: str, default: str) -> str:
        lowered = name.lower()
        for token, kind in (
            ("create", "create"),
            ("add", "create"),
            ("update", "update"),
            ("edit", "update"),
            ("delete", "delete"),
            ("remove", "delete"),
            ("upsert", "upsert"),
            ("search", "search"),
            ("list", "list"),
            ("publish", "publish"),
        ):
            if token in lowered:
                return kind
        return "read" if method == "QUERY" else default
