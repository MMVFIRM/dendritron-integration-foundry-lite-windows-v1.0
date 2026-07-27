from __future__ import annotations

import re
from typing import Any

from ..models import DiscoveryEvidence, DiscoveryResult, DiscoverySource, ObjectFieldProfile, ObjectProfile, OperationProfile, SystemProfile
from ..naming import singularize, slugify
from .schema import schema_for_object

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[\w.\"`\[\]]+)\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)


def _split_columns(body: str) -> list[str]:
    result: list[str] = []
    current: list[str] = []
    depth = 0
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            result.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        result.append("".join(current).strip())
    return [item for item in result if item]


def _sql_type(value: str) -> str:
    lowered = value.lower()
    if any(token in lowered for token in ("int", "serial")):
        return "integer"
    if any(token in lowered for token in ("numeric", "decimal", "real", "float", "double", "money")):
        return "number"
    if any(token in lowered for token in ("bool",)):
        return "boolean"
    if any(token in lowered for token in ("json",)):
        return "object"
    if any(token in lowered for token in ("array", "[]")):
        return "array"
    return "string"


class SQLDiscoveryProvider:
    name = "sql"
    formats = ("sql",)

    def can_handle(self, source: DiscoverySource) -> bool:
        if source.format == "sql":
            return True
        return isinstance(source.document, str) and bool(re.search(r"\bCREATE\s+TABLE\b", source.document, re.IGNORECASE))

    def discover(self, source: DiscoverySource) -> DiscoveryResult:
        if not isinstance(source.document, str):
            raise ValueError("SQL discovery requires a DDL string")
        title = source.name or source.system_id or "Discovered SQL System"
        system_id = source.system_id or slugify(title)
        objects: list[ObjectProfile] = []
        evidence: list[DiscoveryEvidence] = []
        warnings: list[str] = []
        for match in _CREATE_TABLE_RE.finditer(source.document):
            raw_name = match.group("name").strip('"`[]')
            table_name = raw_name.split(".")[-1]
            fields: list[ObjectFieldProfile] = []
            identifiers: list[str] = []
            table_primary_keys: set[str] = set()
            chunks = _split_columns(match.group("body"))
            for chunk in chunks:
                upper = chunk.upper()
                if upper.startswith("PRIMARY KEY"):
                    keys = re.search(r"\((.*?)\)", chunk)
                    if keys:
                        table_primary_keys.update(part.strip().strip('"`[]') for part in keys.group(1).split(","))
                    continue
                if upper.startswith(("CONSTRAINT", "FOREIGN KEY", "UNIQUE", "CHECK")):
                    continue
                column = re.match(r"(?P<name>[\w\"`\[\]]+)\s+(?P<type>[A-Za-z0-9_]+(?:\s*\([^)]*\))?(?:\[\])?)(?P<constraints>.*)", chunk, re.DOTALL)
                if not column:
                    warnings.append(f"Could not parse SQL column definition: {chunk[:80]}")
                    continue
                name = column.group("name").strip('"`[]')
                type_name = column.group("type")
                constraints = column.group("constraints")
                primary = "PRIMARY KEY" in constraints.upper()
                required = "NOT NULL" in constraints.upper() or primary
                relation = None
                reference = re.search(r"REFERENCES\s+([\w.\"`\[\]]+)", constraints, re.IGNORECASE)
                if reference:
                    relation = reference.group(1).strip('"`[]')
                field = ObjectFieldProfile(
                    name=name,
                    path=name,
                    data_type=_sql_type(type_name),
                    required=required,
                    nullable=not required,
                    relation=relation,
                    metadata={"sql_type": type_name, "primary_key": primary},
                )
                fields.append(field)
                if primary:
                    identifiers.append(name)
            identifiers.extend(key for key in table_primary_keys if key not in identifiers)
            for index, field in enumerate(fields):
                if field.name in table_primary_keys and not field.metadata.get("primary_key"):
                    fields[index] = field.model_copy(update={"required": True, "nullable": False, "metadata": {**field.metadata, "primary_key": True}})
            object_id = singularize(table_name)
            objects.append(
                ObjectProfile(
                    object_id=object_id,
                    name=table_name,
                    fields=fields,
                    identifiers=identifiers,
                    metadata={"table": raw_name},
                )
            )
            evidence.append(DiscoveryEvidence(artifact=object_id, location=f"CREATE TABLE {raw_name}", statement=f"Discovered SQL table {raw_name}"))

        operations: list[OperationProfile] = []
        for obj in objects:
            schema = schema_for_object(objects, obj.object_id)
            table = str(obj.metadata.get("table", obj.name))
            operations.extend(
                [
                    OperationProfile(operation_id=f"insert_{obj.object_id}", method="INSERT", path=table, object_id=obj.object_id, operation_kind="create", request_schema=schema, required_permissions=[f"{table}.insert"], idempotency_supported=False),
                    OperationProfile(operation_id=f"select_{obj.object_id}", method="SELECT", path=table, object_id=obj.object_id, operation_kind="read", response_schema=schema, required_permissions=[f"{table}.select"]),
                    OperationProfile(operation_id=f"update_{obj.object_id}", method="UPDATE", path=table, object_id=obj.object_id, operation_kind="update", request_schema=schema, required_permissions=[f"{table}.update"]),
                    OperationProfile(operation_id=f"delete_{obj.object_id}", method="DELETE", path=table, object_id=obj.object_id, operation_kind="delete", required_permissions=[f"{table}.delete"]),
                ]
            )
        if not objects:
            warnings.append("No CREATE TABLE statements were discovered")
        profile = SystemProfile(
            system_id=system_id,
            name=title,
            version=str(source.metadata.get("version", "1")),
            protocol="sql",
            base_url=source.base_url,
            operations=operations,
            objects=objects,
            metadata={"discovery_format": "sql", "dialect": source.metadata.get("dialect", "generic"), **source.metadata},
        )
        return DiscoveryResult(provider=self.name, source_id=source.source_id, profile=profile, warnings=warnings, evidence=evidence)
