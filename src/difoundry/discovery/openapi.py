from __future__ import annotations

from typing import Any

from ..models import (
    AuthenticationProfile,
    DiscoveryEvidence,
    DiscoveryResult,
    DiscoverySource,
    ObjectProfile,
    OperationProfile,
    SystemProfile,
)
from ..naming import singularize, slugify
from .schema import normalize_schema, object_from_schema, operation_id_from

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


class OpenAPIDiscoveryProvider:
    name = "openapi"
    formats = ("openapi",)

    def can_handle(self, source: DiscoverySource) -> bool:
        if source.format == "openapi":
            return True
        return isinstance(source.document, dict) and ("openapi" in source.document or "swagger" in source.document)

    def discover(self, source: DiscoverySource) -> DiscoveryResult:
        if not isinstance(source.document, dict):
            raise ValueError("OpenAPI discovery requires an object document")
        document = source.document
        info = document.get("info", {})
        title = source.name or str(info.get("title") or source.system_id or "Discovered REST System")
        system_id = source.system_id or slugify(title)
        version = str(info.get("version") or "1")
        servers = document.get("servers", [])
        base_url = source.base_url or (servers[0].get("url") if servers else None)
        if not base_url and document.get("host"):
            scheme = (document.get("schemes") or ["https"])[0]
            base_path = str(document.get("basePath") or "")
            base_url = f"{scheme}://{document['host']}{base_path}"
        authentication = self._authentication(document)
        objects = self._objects(document)
        operations: list[OperationProfile] = []
        evidence: list[DiscoveryEvidence] = []
        warnings: list[str] = []

        for path, path_item in document.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            path_parameters = path_item.get("parameters", [])
            for method, operation in path_item.items():
                if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                    continue
                operation_id = str(operation.get("operationId") or operation_id_from(method, path))
                request_schema = self._request_schema(document, operation)
                response_schema = self._response_schema(document, operation)
                permissions = self._permissions(operation, document)
                object_id = self._infer_object_id(operation, path, request_schema, response_schema)
                operations.append(
                    OperationProfile(
                        operation_id=slugify(operation_id),
                        method=method.upper(),
                        path=path,
                        description=str(operation.get("description") or operation.get("summary") or ""),
                        object_id=object_id,
                        operation_kind=self._operation_kind(method, operation_id),
                        request_schema=request_schema,
                        response_schema=response_schema,
                        required_permissions=permissions,
                        idempotency_supported=bool(operation.get("x-idempotency-supported", method.lower() in {"put", "delete"})),
                        metadata={
                            "tags": operation.get("tags", []),
                            "parameters": [*path_parameters, *operation.get("parameters", [])],
                            "deprecated": bool(operation.get("deprecated", False)),
                        },
                    )
                )
                evidence.append(
                    DiscoveryEvidence(
                        artifact=operation_id,
                        location=f"paths.{path}.{method}",
                        statement=f"Discovered {method.upper()} operation {operation_id}",
                    )
                )

        if not operations:
            warnings.append("No operations were discovered from the OpenAPI paths document")
        if not objects:
            warnings.append("No component schemas were available; object inference is limited to operation payloads")
            objects = self._objects_from_operations(operations)

        profile = SystemProfile(
            system_id=system_id,
            name=title,
            version=version,
            protocol="rest",
            base_url=base_url,
            authentication=authentication,
            operations=operations,
            objects=objects,
            metadata={
                "discovery_format": "openapi",
                "specification_version": str(document.get("openapi") or document.get("swagger") or "unknown"),
                **source.metadata,
            },
        )
        return DiscoveryResult(provider=self.name, source_id=source.source_id, profile=profile, warnings=warnings, evidence=evidence)

    @staticmethod
    def _authentication(document: dict[str, Any]) -> AuthenticationProfile:
        schemes = document.get("components", {}).get("securitySchemes", {})
        if not schemes and "securityDefinitions" in document:
            schemes = document.get("securityDefinitions", {})
        for name, scheme in schemes.items():
            kind = str(scheme.get("type", "")).lower()
            if kind == "oauth2":
                return AuthenticationProfile(kind="oauth2", secret_refs={"token": f"{slugify(name)}_token"}, config={"scheme": name, "flows": scheme.get("flows", {})})
            if kind == "apikey":
                return AuthenticationProfile(
                    kind="api_key",
                    secret_refs={"api_key": f"{slugify(name)}_api_key"},
                    config={"scheme": name, "header": scheme.get("name"), "location": scheme.get("in")},
                )
            if kind == "http":
                scheme_name = str(scheme.get("scheme", "")).lower()
                if scheme_name == "basic":
                    return AuthenticationProfile(kind="basic", secret_refs={"username": "username", "password": "password"}, config={"scheme": name})
                if scheme_name in {"bearer", "oauth"}:
                    return AuthenticationProfile(kind="bearer", secret_refs={"token": f"{slugify(name)}_token"}, config={"scheme": name})
            if kind == "mutualtls":
                return AuthenticationProfile(kind="mtls", secret_refs={"certificate": "certificate", "private_key": "private_key"}, config={"scheme": name})
        return AuthenticationProfile()

    @staticmethod
    def _permissions(operation: dict[str, Any], document: dict[str, Any]) -> list[str]:
        security = operation.get("security", document.get("security", []))
        permissions: list[str] = []
        for requirement in security or []:
            if not isinstance(requirement, dict):
                continue
            for scheme, scopes in requirement.items():
                if scopes:
                    permissions.extend(str(scope) for scope in scopes)
                else:
                    permissions.append(f"{scheme}.access")
        return sorted(set(permissions))

    @staticmethod
    def _request_schema(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
        request_body = operation.get("requestBody", {})
        if "$ref" in request_body:
            request_body = normalize_schema(document, request_body)
        content = request_body.get("content", {}) if isinstance(request_body, dict) else {}
        media = content.get("application/json") or content.get("application/*+json") or next(iter(content.values()), {})
        schema = media.get("schema", {}) if isinstance(media, dict) else {}
        parameters = operation.get("parameters", [])
        if not schema:
            for parameter in parameters:
                if parameter.get("in") == "body":
                    schema = parameter.get("schema", {})
                    break
        if not schema and parameters:
            properties: dict[str, Any] = {}
            required: list[str] = []
            for parameter in parameters:
                if parameter.get("in") not in {"query", "path", "header", "formData"}:
                    continue
                parameter_schema = parameter.get("schema") or {key: parameter[key] for key in ("type", "format", "enum", "items") if key in parameter}
                properties[str(parameter.get("name"))] = normalize_schema(document, parameter_schema)
                if parameter.get("required"):
                    required.append(str(parameter.get("name")))
            if properties:
                schema = {"type": "object", "properties": properties, "required": required}
        return normalize_schema(document, schema)

    @staticmethod
    def _response_schema(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
        responses = operation.get("responses", {})
        preferred = None
        for code in ("200", "201", "202", "default"):
            if code in responses:
                preferred = responses[code]
                break
        if preferred is None:
            for code, candidate in responses.items():
                if str(code).startswith("2"):
                    preferred = candidate
                    break
        if not isinstance(preferred, dict):
            return {}
        content = preferred.get("content", {})
        media = content.get("application/json") or content.get("application/*+json") or next(iter(content.values()), {})
        schema = media.get("schema", {}) if isinstance(media, dict) else {}
        if not schema:
            schema = preferred.get("schema", {})
        return normalize_schema(document, schema)

    @staticmethod
    def _objects(document: dict[str, Any]) -> list[ObjectProfile]:
        schemas = document.get("components", {}).get("schemas", {}) or document.get("definitions", {})
        result: list[ObjectProfile] = []
        for name, schema in schemas.items():
            normalized = normalize_schema(document, schema)
            if normalized.get("type") == "object" or normalized.get("properties"):
                result.append(object_from_schema(singularize(name), normalized, name=name))
        return result

    @staticmethod
    def _objects_from_operations(operations: list[OperationProfile]) -> list[ObjectProfile]:
        result: dict[str, ObjectProfile] = {}
        for operation in operations:
            if not operation.object_id:
                continue
            schema = operation.request_schema or operation.response_schema
            if not schema:
                continue
            result.setdefault(operation.object_id, object_from_schema(operation.object_id, schema))
        return list(result.values())

    @staticmethod
    def _infer_object_id(operation: dict[str, Any], path: str, request_schema: dict[str, Any], response_schema: dict[str, Any]) -> str:
        tags = operation.get("tags") or []
        if tags:
            return singularize(str(tags[0]))
        for schema in (request_schema, response_schema):
            title = schema.get("title") if isinstance(schema, dict) else None
            if title:
                return singularize(str(title))
        segments = [segment for segment in path.split("/") if segment and not segment.startswith("{")]
        return singularize(segments[-1] if segments else "resource")

    @staticmethod
    def _operation_kind(method: str, operation_id: str) -> str:
        lowered = operation_id.lower()
        if "upsert" in lowered:
            return "upsert"
        if "search" in lowered or "find" in lowered:
            return "search"
        if "list" in lowered:
            return "list"
        return {"post": "create", "get": "read", "put": "update", "patch": "update", "delete": "delete"}.get(method.lower(), "custom")
