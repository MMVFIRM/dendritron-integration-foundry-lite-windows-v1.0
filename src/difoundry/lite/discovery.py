from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import httpx

from ..discovery import DiscoveryService
from ..discovery.schema import object_from_schema
from ..models import AuthenticationProfile, DiscoverySource, OperationProfile, SystemProfile
from ..naming import singularize, slugify

OPENAPI_PATHS = (
    "/openapi.json",
    "/swagger.json",
    "/v3/api-docs",
    "/api/openapi.json",
    "/api/swagger.json",
    "/.well-known/openapi.json",
)
GRAPHQL_PATHS = ("/graphql", "/api/graphql")
METADATA_PATHS = (
    "/$metadata",
    "/api/$metadata",
    "/metadata",
    "/api/metadata",
    "/schema",
    "/api/schema",
    "/resources",
    "/api/resources",
    "/objects",
    "/api/objects",
)
INTROSPECTION_QUERY = """query FoundryLiteIntrospection { __schema { queryType { name } mutationType { name } subscriptionType { name } types { kind name description fields(includeDeprecated:true) { name description args { name type { kind name ofType { kind name ofType { kind name } } } } type { kind name ofType { kind name ofType { kind name } } } } inputFields { name description type { kind name ofType { kind name ofType { kind name } } } } } } }"""


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in {"a", "link", "script"}:
            return
        values = dict(attrs)
        value = values.get("href") or values.get("src")
        if value:
            self.links.append(value)


@dataclass(slots=True)
class ProbeEvidence:
    method: str
    url: str
    status_code: int | None
    outcome: str
    detail: str = ""


@dataclass(slots=True)
class LiveDiscoveryResult:
    profile: SystemProfile
    method: str
    evidence: list[ProbeEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class AutonomousDiscoveryEngine:
    """Discover live schemas without asking the user to upload documentation.

    Discovery order:
      1. linked/standard OpenAPI endpoints
      2. GraphQL introspection
      3. OData metadata
      4. authenticated capability indexes
      5. representative read-only JSON plus OPTIONS
    """

    def __init__(
        self,
        client_factory: Callable[..., httpx.Client] | None = None,
        timeout: float = 12.0,
        max_probes: int = 24,
    ) -> None:
        self.client_factory = client_factory or httpx.Client
        self.timeout = timeout
        self.max_probes = max_probes
        self.providers = DiscoveryService()

    def discover(
        self,
        name: str,
        base_url: str,
        auth_kind: str = "none",
        credentials: dict[str, Any] | None = None,
        system_id: str | None = None,
    ) -> LiveDiscoveryResult:
        base_url = self._validate_url(base_url)
        headers = self._headers(auth_kind, credentials or {})
        evidence: list[ProbeEvidence] = []
        with self.client_factory(headers=headers, timeout=self.timeout, follow_redirects=True) as client:
            candidates = list(OPENAPI_PATHS)
            root = self._safe_get(client, base_url, evidence)
            if root is not None and "text/html" in root.headers.get("content-type", ""):
                parser = _LinkParser()
                parser.feed(root.text[:500000])
                for link in parser.links:
                    lowered = link.lower()
                    if any(token in lowered for token in ("openapi", "swagger", "api-doc", "schema", "graphql", "metadata")):
                        candidates.append(urlparse(urljoin(base_url, link)).path)

            for path in list(dict.fromkeys(candidates))[: self.max_probes]:
                url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
                response = self._safe_get(client, url, evidence)
                document = self._json(response)
                if isinstance(document, dict) and ("openapi" in document or "swagger" in document):
                    result = self.providers.discover(
                        DiscoverySource(
                            format="openapi",
                            document=document,
                            name=name,
                            system_id=system_id or slugify(name),
                            base_url=base_url,
                            metadata={"live_discovery": True, "discovery_url": str(response.url)},
                        )
                    )
                    return LiveDiscoveryResult(result.profile, "openapi", evidence, result.warnings)

            for path in GRAPHQL_PATHS:
                url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
                response = self._safe_post(client, url, {"query": INTROSPECTION_QUERY}, evidence)
                document = self._json(response)
                if isinstance(document, dict) and document.get("data", {}).get("__schema"):
                    result = self.providers.discover(
                        DiscoverySource(
                            format="graphql",
                            document=document,
                            name=name,
                            system_id=system_id or slugify(name),
                            base_url=url,
                            metadata={"live_discovery": True, "discovery_url": url},
                        )
                    )
                    return LiveDiscoveryResult(result.profile, "graphql-introspection", evidence, result.warnings)

            for path in METADATA_PATHS:
                url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
                response = self._safe_get(client, url, evidence)
                if response is None:
                    continue
                text = response.text.lstrip()
                if path.endswith("$metadata") and text.startswith("<"):
                    return LiveDiscoveryResult(
                        self._odata_profile(name, system_id or slugify(name), base_url, text),
                        "odata-metadata",
                        evidence,
                        [],
                    )
                profile = self._profile_from_capability_document(
                    name,
                    system_id or slugify(name),
                    base_url,
                    self._json(response),
                    client,
                    evidence,
                )
                if profile:
                    return LiveDiscoveryResult(
                        profile,
                        "capability-probe",
                        evidence,
                        ["Profile inferred from authenticated read-only capability responses"],
                    )

            profile = self._behavioral_probe(name, system_id or slugify(name), base_url, client, evidence, root)
            if profile:
                return LiveDiscoveryResult(
                    profile,
                    "behavioral-probe",
                    evidence,
                    ["No formal schema was exposed; profile inferred from read-only responses and OPTIONS metadata"],
                )

        raise ValueError(
            "Foundry connected successfully but could not discover a safe machine-readable schema or representative read-only capability. "
            "A provider plugin or local discovery agent is required."
        )

    @staticmethod
    def _validate_url(value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("A valid http(s) system URL is required")
        return value.rstrip("/")

    @staticmethod
    def _headers(kind: str, credentials: dict[str, Any]) -> dict[str, str]:
        headers = {
            "Accept": "application/json, application/xml;q=0.8, text/html;q=0.5",
            "User-Agent": "Dendritron-Foundry-Lite/0.1",
        }
        if kind in {"bearer", "oauth2"} and credentials.get("token"):
            headers["Authorization"] = f"Bearer {credentials['token']}"
        elif kind == "api_key" and credentials.get("api_key"):
            headers[str(credentials.get("header") or "X-API-Key")] = str(credentials["api_key"])
        elif kind == "basic" and credentials.get("username"):
            raw = f"{credentials['username']}:{credentials.get('password', '')}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        return headers

    def _safe_get(self, client: httpx.Client, url: str, evidence: list[ProbeEvidence]) -> httpx.Response | None:
        try:
            response = client.get(url)
            evidence.append(ProbeEvidence("GET", url, response.status_code, "accepted" if response.status_code < 400 else "rejected"))
            return response if response.status_code < 400 else None
        except Exception as exc:
            evidence.append(ProbeEvidence("GET", url, None, "error", str(exc)))
            return None

    def _safe_post(
        self,
        client: httpx.Client,
        url: str,
        payload: dict[str, Any],
        evidence: list[ProbeEvidence],
    ) -> httpx.Response | None:
        try:
            response = client.post(url, json=payload)
            evidence.append(ProbeEvidence("POST", url, response.status_code, "introspection" if response.status_code < 400 else "rejected"))
            return response if response.status_code < 400 else None
        except Exception as exc:
            evidence.append(ProbeEvidence("POST", url, None, "error", str(exc)))
            return None

    @staticmethod
    def _json(response: httpx.Response | None) -> Any:
        if response is None:
            return None
        try:
            return response.json()
        except Exception:
            return None

    def _profile_from_capability_document(
        self,
        name: str,
        system_id: str,
        base_url: str,
        document: Any,
        client: httpx.Client,
        evidence: list[ProbeEvidence],
    ) -> SystemProfile | None:
        resources: list[str] = []
        if isinstance(document, list) and all(isinstance(item, str) for item in document):
            resources = list(document)
        elif isinstance(document, dict):
            raw = document.get("resources") or document.get("objects") or document.get("entities")
            if isinstance(raw, list):
                resources = [str(item.get("name") if isinstance(item, dict) else item) for item in raw]
            elif isinstance(raw, dict):
                resources = list(raw)
        if not resources:
            return None

        objects = []
        operations = []
        for resource in resources[:32]:
            clean = slugify(resource)
            url = urljoin(base_url.rstrip("/") + "/", clean)
            sample = self._safe_get(client, url, evidence)
            schema = self._schema_from_value(self._json(sample))
            if not schema:
                continue
            object_id = singularize(clean)
            item_schema = self._item_schema(schema)
            objects.append(object_from_schema(object_id, item_schema, name=resource))
            operations.append(
                OperationProfile(
                    operation_id=f"list_{clean}",
                    method="GET",
                    path=urlparse(url).path,
                    object_id=object_id,
                    operation_kind="list",
                    response_schema=schema,
                )
            )
            allow = self._options(client, url, evidence)
            if "POST" in allow:
                operations.append(
                    OperationProfile(
                        operation_id=f"create_{object_id}",
                        method="POST",
                        path=urlparse(url).path,
                        object_id=object_id,
                        operation_kind="create",
                        request_schema=item_schema,
                    )
                )
        if not objects:
            return None
        return SystemProfile(
            system_id=system_id,
            name=name,
            protocol="rest",
            base_url=base_url,
            authentication=AuthenticationProfile(),
            objects=objects,
            operations=operations,
            metadata={"discovery_format": "capability_probe", "live_discovery": True},
        )

    def _behavioral_probe(
        self,
        name: str,
        system_id: str,
        base_url: str,
        client: httpx.Client,
        evidence: list[ProbeEvidence],
        root: httpx.Response | None,
    ) -> SystemProfile | None:
        candidates: list[tuple[str, httpx.Response | None]] = [("root", root)]
        for path in ("/api", "/api/v1", "/data", "/items"):
            candidates.append((path, self._safe_get(client, urljoin(base_url.rstrip("/") + "/", path.lstrip("/")), evidence)))
        for label, response in candidates:
            if response is None:
                continue
            schema = self._schema_from_value(self._json(response))
            if not schema:
                continue
            object_id = singularize(slugify(label if label != "root" else name))
            item_schema = self._item_schema(schema)
            obj = object_from_schema(object_id, item_schema, name=object_id)
            path = urlparse(str(response.url)).path if response is not None else "/"
            operations = [
                OperationProfile(
                    operation_id=f"read_{object_id}",
                    method="GET",
                    path=path,
                    object_id=object_id,
                    operation_kind="read",
                    response_schema=schema,
                )
            ]
            allow = self._options(client, str(response.url), evidence) if response is not None else set()
            if "POST" in allow:
                operations.append(
                    OperationProfile(
                        operation_id=f"create_{object_id}",
                        method="POST",
                        path=path,
                        object_id=object_id,
                        operation_kind="create",
                        request_schema=item_schema,
                    )
                )
            return SystemProfile(
                system_id=system_id,
                name=name,
                protocol="rest",
                base_url=base_url,
                objects=[obj],
                operations=operations,
                metadata={"discovery_format": "behavioral_probe", "live_discovery": True},
            )
        return None

    def _options(self, client: httpx.Client, url: str, evidence: list[ProbeEvidence]) -> set[str]:
        try:
            response = client.options(url)
            evidence.append(ProbeEvidence("OPTIONS", url, response.status_code, "metadata"))
            return {item.strip().upper() for item in response.headers.get("allow", "").split(",") if item.strip()}
        except Exception as exc:
            evidence.append(ProbeEvidence("OPTIONS", url, None, "error", str(exc)))
            return set()

    @classmethod
    def _schema_from_value(cls, value: Any) -> dict[str, Any] | None:
        if isinstance(value, list):
            if not value:
                return None
            item = cls._schema_from_value(value[0])
            return {"type": "array", "items": item} if item else None
        if isinstance(value, dict):
            return {
                "type": "object",
                "properties": {str(key): cls._schema_from_value(item) or {"type": "any"} for key, item in value.items()},
            }
        if isinstance(value, bool):
            return {"type": "boolean"}
        if isinstance(value, int):
            return {"type": "integer"}
        if isinstance(value, float):
            return {"type": "number"}
        if isinstance(value, str):
            return {"type": "string"}
        if value is None:
            return {"type": "any", "nullable": True}
        return None

    @staticmethod
    def _item_schema(schema: dict[str, Any]) -> dict[str, Any]:
        return schema.get("items", schema)

    @staticmethod
    def _odata_profile(name: str, system_id: str, base_url: str, text: str) -> SystemProfile:
        root = ET.fromstring(text)
        entities = []
        operations = []
        for entity in root.findall(".//{*}EntityType"):
            entity_name = entity.attrib.get("Name", "Entity")
            properties: dict[str, Any] = {}
            required: list[str] = []
            identifiers: list[str] = []
            for prop in entity.findall("{*}Property"):
                pname = prop.attrib.get("Name", "field")
                ptype = prop.attrib.get("Type", "Edm.String")
                properties[pname] = {"type": AutonomousDiscoveryEngine._odata_type(ptype)}
                if prop.attrib.get("Nullable", "true").lower() == "false":
                    required.append(pname)
            for ref in entity.findall(".//{*}PropertyRef"):
                if ref.attrib.get("Name"):
                    identifiers.append(ref.attrib["Name"])
            schema = {"type": "object", "properties": properties, "required": required}
            obj = object_from_schema(singularize(entity_name), schema, name=entity_name).model_copy(update={"identifiers": identifiers})
            entities.append(obj)
            path = f"/{entity_name}"
            operations.extend(
                [
                    OperationProfile(
                        operation_id=f"list_{slugify(entity_name)}",
                        method="GET",
                        path=path,
                        object_id=obj.object_id,
                        operation_kind="list",
                        response_schema={"type": "array", "items": schema},
                    ),
                    OperationProfile(
                        operation_id=f"create_{slugify(entity_name)}",
                        method="POST",
                        path=path,
                        object_id=obj.object_id,
                        operation_kind="create",
                        request_schema=schema,
                    ),
                ]
            )
        if not entities:
            raise ValueError("OData metadata exposed no entity types")
        return SystemProfile(
            system_id=system_id,
            name=name,
            protocol="rest",
            base_url=base_url,
            objects=entities,
            operations=operations,
            metadata={"discovery_format": "odata", "live_discovery": True},
        )

    @staticmethod
    def _odata_type(value: str) -> str:
        lower = value.lower()
        if any(token in lower for token in ("int", "decimal", "double", "single")):
            return "number"
        if "bool" in lower:
            return "boolean"
        return "string"
