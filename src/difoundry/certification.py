from __future__ import annotations

from typing import Any, Callable

from jsonschema import Draft202012Validator

from .jsonpath import get_path
from .models import CertificationResult, CertifierDefinition, OperationProfile

Certifier = Callable[[dict[str, Any], dict[str, Any], OperationProfile, list[str]], tuple[bool, str]]


class CertificationEngine:
    def __init__(self) -> None:
        self._handlers: dict[str, Certifier] = {}
        self.register("required_fields", self._certify_required_fields)
        self.register("allowed_values", self._certify_allowed_values)
        self.register("permission", self._certify_permission)
        self.register("not_empty", self._certify_not_empty)
        self.register("expression", self._certify_expression)

    def register(self, kind: str, certifier: Certifier) -> None:
        if not kind:
            raise ValueError("Certifier kind cannot be empty")
        self._handlers[kind] = certifier

    def certify(
        self,
        payload: dict[str, Any],
        definitions: list[CertifierDefinition],
        operation: OperationProfile,
        granted_permissions: list[str],
    ) -> list[CertificationResult]:
        results: list[CertificationResult] = []
        if operation.request_schema:
            errors = sorted(Draft202012Validator(operation.request_schema).iter_errors(payload), key=lambda error: list(error.path))
            details = "request payload satisfies operation schema" if not errors else "; ".join(
                f"{'.'.join(str(part) for part in error.path) or '$'}: {error.message}" for error in errors
            )
            results.append(CertificationResult(kind="request_schema", passed=not errors, required=True, details=details))
        for definition in definitions:
            handler = self._handlers.get(definition.kind)
            if handler is None:
                results.append(
                    CertificationResult(
                        kind=definition.kind,
                        passed=False,
                        required=definition.required,
                        details=f"No certifier is registered for kind {definition.kind!r}",
                    )
                )
                continue
            passed, details = handler(payload, definition.config, operation, granted_permissions)
            results.append(CertificationResult(kind=definition.kind, passed=passed, required=definition.required, details=details))
        if not any(definition.kind == "permission" for definition in definitions):
            passed, details = self._certify_permission(payload, {}, operation, granted_permissions)
            results.append(CertificationResult(kind="permission", passed=passed, required=True, details=details))
        return results

    @staticmethod
    def _certify_required_fields(payload: dict[str, Any], config: dict[str, Any], _operation: OperationProfile, _permissions: list[str]) -> tuple[bool, str]:
        fields = list(config.get("fields", []))
        missing = []
        for field in fields:
            try:
                value = get_path(payload, field)
                if value is None:
                    missing.append(field)
            except KeyError:
                missing.append(field)
        return (not missing, "all required fields present" if not missing else f"missing fields: {missing}")

    @staticmethod
    def _certify_allowed_values(payload: dict[str, Any], config: dict[str, Any], _operation: OperationProfile, _permissions: list[str]) -> tuple[bool, str]:
        path = str(config.get("path", ""))
        allowed = list(config.get("values", []))
        try:
            value = get_path(payload, path)
        except KeyError:
            return False, f"path {path!r} not found"
        return (value in allowed, f"value {value!r} {'is' if value in allowed else 'is not'} allowed")

    @staticmethod
    def _certify_permission(_payload: dict[str, Any], _config: dict[str, Any], operation: OperationProfile, permissions: list[str]) -> tuple[bool, str]:
        missing = sorted(set(operation.required_permissions) - set(permissions))
        return (not missing, "permission scope satisfied" if not missing else f"missing permissions: {missing}")

    @staticmethod
    def _certify_not_empty(payload: dict[str, Any], config: dict[str, Any], _operation: OperationProfile, _permissions: list[str]) -> tuple[bool, str]:
        path = str(config.get("path", ""))
        try:
            value = get_path(payload, path)
        except KeyError:
            return False, f"path {path!r} not found"
        passed = value not in (None, "", [], {})
        return passed, f"path {path!r} {'is populated' if passed else 'is empty'}"

    @staticmethod
    def _certify_expression(payload: dict[str, Any], config: dict[str, Any], _operation: OperationProfile, _permissions: list[str]) -> tuple[bool, str]:
        # Deliberately constrained expression certifier. No eval.
        left_path = str(config.get("left", ""))
        operator = str(config.get("operator", "eq"))
        right = config.get("right")
        try:
            left = get_path(payload, left_path)
        except KeyError:
            return False, f"left path {left_path!r} not found"
        comparisons = {
            "eq": left == right,
            "ne": left != right,
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }
        passed = bool(comparisons.get(operator, False))
        return passed, f"expression {left_path} {operator} {right!r} evaluated to {passed}"
