from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import IntegrationContract, SystemProfile


@dataclass
class ValidationReport:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ContractValidator:
    """Cross-artifact validation that cannot be performed by one Pydantic model alone."""

    def validate(self, contract: IntegrationContract, profiles: dict[str, SystemProfile]) -> ValidationReport:
        errors: list[str] = []
        warnings: list[str] = []
        source_profile = profiles.get(contract.trigger.system_id)
        if source_profile is None:
            errors.append(f"Trigger system profile is missing: {contract.trigger.system_id}")
        elif contract.trigger.object_type != "*" and source_profile.objects:
            if not any(obj.object_id == contract.trigger.object_type for obj in source_profile.objects):
                errors.append(
                    f"Trigger object {contract.trigger.object_type!r} is not defined on {contract.trigger.system_id!r}"
                )

        source_fields: set[str] | None = None
        if source_profile and source_profile.objects and contract.trigger.object_type != "*":
            try:
                source_fields = {field.path for field in source_profile.object(contract.trigger.object_type).fields}
            except KeyError:
                source_fields = set()

        for route in contract.routes:
            if not route.actions:
                errors.append(f"Route {route.route_id!r} has no actions")
            if not route.branches:
                warnings.append(f"Route {route.route_id!r} has no branches and will always own matching events")
            for action in route.actions:
                profile = profiles.get(action.target_system_id)
                if profile is None:
                    errors.append(f"Target system profile is missing: {action.target_system_id}")
                    continue
                try:
                    operation = profile.operation(action.operation_id)
                except KeyError:
                    errors.append(
                        f"Action {action.action_id!r} references missing operation "
                        f"{action.operation_id!r} on {action.target_system_id!r}"
                    )
                    continue
                granted = set(contract.permissions.get(action.target_system_id, []))
                missing = set(operation.required_permissions) - granted
                if missing:
                    warnings.append(
                        f"Action {action.action_id!r} will be blocked unless permissions are granted: {sorted(missing)}"
                    )
                for mapping in action.mappings:
                    synthetic_sources = {"__foundry_constant__", "__foundry_unresolved_business_value__"}
                    if source_fields is not None and mapping.source not in source_fields and mapping.source not in synthetic_sources:
                        errors.append(
                            f"Action {action.action_id!r} maps unknown source field {mapping.source!r} "
                            f"from {contract.trigger.system_id!r}/{contract.trigger.object_type!r}"
                        )
                    if mapping.source == "__foundry_constant__" and mapping.default is None:
                        errors.append(
                            f"Action {action.action_id!r} uses a constant mapping for {mapping.target!r} without a value"
                        )
                    if operation.request_schema and not self._schema_path_exists(operation.request_schema, mapping.target):
                        errors.append(
                            f"Action {action.action_id!r} maps unknown target field {mapping.target!r} "
                            f"for operation {operation.operation_id!r}"
                        )
                required = self._required_paths(operation.request_schema)
                mapped = {mapping.target for mapping in action.mappings}
                missing_required = required - mapped
                if missing_required:
                    errors.append(
                        f"Action {action.action_id!r} does not map required operation fields: {sorted(missing_required)}"
                    )
        return ValidationReport(valid=not errors, errors=errors, warnings=warnings)

    @classmethod
    def _schema_path_exists(cls, schema: dict[str, Any], path: str) -> bool:
        current = schema
        parts = path.split(".")
        for index, part in enumerate(parts):
            properties = current.get("properties", {}) if isinstance(current, dict) else {}
            if part not in properties:
                additional = current.get("additionalProperties", True) if isinstance(current, dict) else False
                return bool(additional)
            current = properties[part]
            if index < len(parts) - 1 and not isinstance(current, dict):
                return False
        return True

    @classmethod
    def _required_paths(cls, schema: dict[str, Any], prefix: str = "") -> set[str]:
        if not isinstance(schema, dict):
            return set()
        required = set(schema.get("required", []))
        result: set[str] = set()
        properties = schema.get("properties", {})
        for name in required:
            path = f"{prefix}.{name}" if prefix else name
            result.add(path)
            child = properties.get(name)
            if isinstance(child, dict):
                result.update(cls._required_paths(child, path))
        return result
