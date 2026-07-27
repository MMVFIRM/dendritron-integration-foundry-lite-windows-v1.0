from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import ObjectProfile, OperationProfile, SystemProfile
from ..naming import lexical_similarity, tokens


@dataclass(slots=True)
class TaskIntent:
    name: str
    event_type: str
    source_object_id: str
    targets: list[tuple[str, str, str]]
    condition_path: str | None
    condition_value: str | None
    explanation: str


class TaskInterpreter:
    ACTION_WORDS = {
        "create": "create",
        "add": "create",
        "make": "create",
        "update": "update",
        "sync": "upsert",
        "upsert": "upsert",
        "send": "publish",
        "post": "publish",
        "delete": "delete",
        "remove": "delete",
    }

    def interpret(self, goal: str, source: SystemProfile, targets: list[SystemProfile]) -> TaskIntent:
        source_obj = self._best_object(goal, source.objects)
        result: list[tuple[str, str, str]] = []
        for profile in targets:
            target_obj = self._best_object(goal, profile.objects)
            operation = self._best_operation(goal, profile.operations, target_obj.object_id)
            result.append((profile.system_id, target_obj.object_id, operation.operation_id))
        event = self._event_type(goal)
        condition_path, condition_value = self._condition(goal, source_obj, event)
        return TaskIntent(
            name=self._name(goal, source.name, [target.name for target in targets]),
            event_type=event,
            source_object_id=source_obj.object_id,
            targets=result,
            condition_path=condition_path,
            condition_value=condition_value,
            explanation=f"Watch {source.name} {source_obj.name} events ({event}) and run {len(result)} target action(s).",
        )

    def _best_object(self, goal: str, objects: list[ObjectProfile]) -> ObjectProfile:
        if not objects:
            raise ValueError("The system profile contains no discoverable business objects")
        scored = []
        for obj in objects:
            score = max(lexical_similarity(goal, obj.name), lexical_similarity(goal, obj.object_id))
            field_hits = sum(1 for field in obj.fields if tokens(field.name) & tokens(goal))
            scored.append((score + min(0.25, field_hits * 0.03), obj))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def _best_operation(self, goal: str, operations: list[OperationProfile], object_id: str) -> OperationProfile:
        candidates = [
            operation
            for operation in operations
            if operation.object_id in {None, object_id}
            and operation.operation_kind in {"create", "update", "upsert", "publish", "custom"}
        ]
        if not candidates:
            raise ValueError(f"No safe writable operation was discovered for {object_id}")
        desired = None
        lower = goal.lower()
        for word, kind in self.ACTION_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\w*\b", lower):
                desired = kind
        scored = []
        for operation in candidates:
            score = lexical_similarity(goal, operation.operation_id) + lexical_similarity(goal, operation.description) * 0.4
            if desired and operation.operation_kind == desired:
                score += 1.0
            if desired == "upsert" and operation.operation_kind in {"create", "update"}:
                score += 0.5
            scored.append((score, operation))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    @staticmethod
    def _condition(goal: str, source: ObjectProfile, event_type: str) -> tuple[str | None, str | None]:
        if event_type not in {"approved", "closed_won", "paid", "failed"}:
            return None, None
        preferred = ["stage", "status", "state", "outcome", "result"]
        paths = {field.name.lower(): field.path for field in source.fields}
        path = next((paths[name] for name in preferred if name in paths), None)
        if not path:
            return None, None
        value = {"closed_won": "closed won", "approved": "approved", "paid": "paid", "failed": "failed"}[event_type]
        return f"payload.{path}", value

    @staticmethod
    def _event_type(goal: str) -> str:
        lower = goal.lower()
        patterns = (
            ("closed won", "closed_won"),
            ("approved", "approved"),
            ("created", "created"),
            ("new ", "created"),
            ("updated", "updated"),
            ("changes", "updated"),
            ("deleted", "deleted"),
            ("paid", "paid"),
            ("fails", "failed"),
        )
        for token, value in patterns:
            if token in lower:
                return value
        return "changed"

    @staticmethod
    def _name(goal: str, source: str, targets: list[str]) -> str:
        first = re.sub(r"\s+", " ", goal.strip()).rstrip(".")
        return first[:96] if first else f"{source} to {', '.join(targets)}"
