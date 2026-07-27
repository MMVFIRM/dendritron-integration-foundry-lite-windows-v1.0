from __future__ import annotations

from typing import Any

from .jsonpath import get_path, set_path
from .models import MappingRule
from .transforms import TransformRegistry


class MappingError(ValueError):
    pass


class MappingEngine:
    def __init__(self, transforms: TransformRegistry | None = None) -> None:
        self.transforms = transforms or TransformRegistry()

    def map_payload(self, source: dict[str, Any], rules: list[MappingRule], context: dict[str, Any] | None = None) -> dict[str, Any]:
        output: dict[str, Any] = {}
        mapping_context = {"source": source, **(context or {})}
        for rule in rules:
            try:
                value = get_path(source, rule.source)
            except KeyError:
                value = rule.default
                if rule.required and value is None:
                    raise MappingError(f"Required source path {rule.source!r} is missing")
            for transform in rule.transforms:
                value = self.transforms.apply(value, transform, mapping_context)
            if rule.required and value is None:
                raise MappingError(f"Required mapping {rule.source!r} produced None")
            set_path(output, rule.target, value)
        return output
