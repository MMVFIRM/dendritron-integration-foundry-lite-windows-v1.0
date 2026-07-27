from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..models import ObjectFieldProfile, ObjectProfile
from ..naming import singularize, slugify


def resolve_local_ref(document: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict) or "$ref" not in value:
        return value
    ref = value["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return value
    current: Any = document
    for part in ref[2:].split("/"):
        current = current[part.replace("~1", "/").replace("~0", "~")]
    merged = deepcopy(current)
    merged.update({key: val for key, val in value.items() if key != "$ref"})
    return merged


def normalize_schema(document: dict[str, Any], schema: Any, seen: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    seen = seen or set()
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if ref in seen:
            return {"type": "object", "x-recursive-ref": ref}
        seen = {*seen, ref}
    schema = resolve_local_ref(document, schema)
    if "allOf" in schema:
        combined: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for part in schema.get("allOf", []):
            normalized = normalize_schema(document, part, seen)
            combined["properties"].update(normalized.get("properties", {}))
            combined["required"].extend(normalized.get("required", []))
        combined.update({key: value for key, value in schema.items() if key != "allOf"})
        combined["required"] = sorted(set(combined.get("required", [])))
        schema = combined
    result = deepcopy(schema)
    if "properties" in result:
        result["properties"] = {
            key: normalize_schema(document, value, seen) for key, value in result.get("properties", {}).items()
        }
    if "items" in result:
        result["items"] = normalize_schema(document, result["items"], seen)
    return result


def infer_type(schema: dict[str, Any]) -> str:
    value = schema.get("type")
    if isinstance(value, list):
        return "|".join(sorted(str(item) for item in value))
    if value:
        return str(value)
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    if "enum" in schema:
        return "enum"
    return "any"


def fields_from_schema(schema: dict[str, Any], prefix: str = "", max_depth: int = 4) -> list[ObjectFieldProfile]:
    fields: list[ObjectFieldProfile] = []
    if max_depth < 0:
        return fields
    required = set(schema.get("required", []))
    for name, raw in schema.get("properties", {}).items():
        child = raw if isinstance(raw, dict) else {}
        path = f"{prefix}.{name}" if prefix else name
        data_type = infer_type(child)
        fields.append(
            ObjectFieldProfile(
                name=name,
                path=path,
                data_type=data_type,
                required=name in required,
                nullable=bool(child.get("nullable", name not in required)),
                description=str(child.get("description", "")),
                format=child.get("format"),
                enum=list(child.get("enum", [])),
                relation=child.get("x-relation"),
                metadata={"read_only": bool(child.get("readOnly", False)), "write_only": bool(child.get("writeOnly", False))},
            )
        )
        if data_type == "object" and child.get("properties"):
            fields.extend(fields_from_schema(child, path, max_depth - 1))
    return fields


def object_from_schema(object_id: str, schema: dict[str, Any], name: str | None = None) -> ObjectProfile:
    fields = fields_from_schema(schema)
    identifiers = [
        field.path
        for field in fields
        if field.path.split(".")[-1].lower() in {"id", "uuid", "guid", "key"} or field.path.split(".")[-1].lower().endswith(("_id", "_key"))
        or bool(field.metadata.get("primary_key"))
    ]
    return ObjectProfile(
        object_id=singularize(object_id),
        name=name or str(schema.get("title") or object_id),
        description=str(schema.get("description", "")),
        fields=fields,
        identifiers=identifiers,
        metadata={"schema": schema},
    )


def schema_for_object(objects: list[ObjectProfile], object_id: str) -> dict[str, Any]:
    for obj in objects:
        if obj.object_id == object_id:
            properties: dict[str, Any] = {}
            required: list[str] = []
            for field in obj.fields:
                if "." in field.path:
                    continue
                spec: dict[str, Any] = {"type": field.data_type if field.data_type in {"string", "number", "integer", "boolean", "object", "array"} else "string"}
                if field.enum:
                    spec["enum"] = field.enum
                if field.format:
                    spec["format"] = field.format
                properties[field.name] = spec
                if field.required:
                    required.append(field.name)
            result: dict[str, Any] = {"type": "object", "properties": properties}
            if required:
                result["required"] = required
            return result
    return {}


def operation_id_from(method: str, path: str) -> str:
    cleaned = path.replace("{", "").replace("}", "")
    return slugify(f"{method}_{cleaned}")
