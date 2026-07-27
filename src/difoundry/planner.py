from __future__ import annotations

import hashlib
import json
from typing import Any

from .certification import CertificationEngine
from .mapping import MappingEngine
from .models import CanonicalEvent, ExecutionPlan, IntegrationContract, PlannedAction, SystemProfile
from .routing import DendriticOwnedRouter, RoutingPort


class PlanningError(ValueError):
    pass


class IntegrationPlanner:
    def __init__(
        self,
        profiles: dict[str, SystemProfile],
        router: RoutingPort | None = None,
        mapper: MappingEngine | None = None,
        certifier: CertificationEngine | None = None,
    ) -> None:
        self.profiles = profiles
        self.router = router or DendriticOwnedRouter()
        self.mapper = mapper or MappingEngine()
        self.certifier = certifier or CertificationEngine()

    def plan(self, contract: IntegrationContract, event: CanonicalEvent) -> ExecutionPlan:
        self._validate_trigger(contract, event)
        context = {
            "event": event.model_dump(mode="python"),
            "payload": event.payload,
            "metadata": event.metadata,
        }
        selections = self.router.select(contract, context)
        actions: list[PlannedAction] = []
        traces = [selection.trace for selection in selections]
        for selection in selections:
            if selection.trace.abstained:
                continue
            for action in selection.route.actions:
                try:
                    target_profile = self.profiles[action.target_system_id]
                except KeyError as exc:
                    raise PlanningError(f"Missing target system profile: {action.target_system_id}") from exc
                operation = target_profile.operation(action.operation_id)
                mapped = self.mapper.map_payload(
                    event.payload,
                    action.mappings,
                    context={"event_id": event.event_id, "source_system": event.source_system},
                )
                permissions = contract.permissions.get(action.target_system_id, [])
                certifications = self.certifier.certify(mapped, action.certifiers, operation, permissions)
                path_parameters = {
                    key: self._resolve_parameter(value, event, mapped) for key, value in action.path_parameters.items()
                }
                actions.append(
                    PlannedAction(
                        action_id=action.action_id,
                        target_system_id=action.target_system_id,
                        operation_id=action.operation_id,
                        payload=mapped,
                        path_parameters=path_parameters,
                        query_parameters=action.query_parameters,
                        certifications=certifications,
                        route_id=selection.route.route_id,
                        branch_id=selection.trace.selected_branch_id,
                        specialist_ids=selection.trace.selected_specialist_ids,
                        ownership_key=selection.trace.ownership_key,
                    )
                )
        plan_hash = self._hash(contract, event, traces, actions)
        behavior_hash = self._behavior_hash(contract, traces, actions)
        return ExecutionPlan(
            contract_id=contract.contract_id,
            contract_version=contract.version,
            event_id=event.event_id,
            route_traces=traces,
            actions=actions,
            plan_hash=plan_hash,
            behavior_hash=behavior_hash,
        )

    @staticmethod
    def _validate_trigger(contract: IntegrationContract, event: CanonicalEvent) -> None:
        trigger = contract.trigger
        matches = (
            trigger.system_id == event.source_system
            and trigger.object_type in {"*", event.source_object}
            and trigger.event_type in {"*", event.event_type}
        )
        if not matches:
            raise PlanningError("Event does not satisfy contract trigger")

    @staticmethod
    def _resolve_parameter(specification: str, event: CanonicalEvent, mapped: dict[str, Any]) -> Any:
        from .jsonpath import get_path

        if specification.startswith("event."):
            return get_path(event.model_dump(mode="python"), specification.removeprefix("event."))
        if specification.startswith("mapped."):
            return get_path(mapped, specification.removeprefix("mapped."))
        return specification

    @staticmethod
    def _behavior_hash(contract: IntegrationContract, traces: list[Any], actions: list[PlannedAction]) -> str:
        content = {
            "contract_id": contract.contract_id,
            "contract_version": contract.version,
            "traces": [trace.model_dump(mode="json") for trace in traces],
            "actions": [action.model_dump(mode="json") for action in actions],
        }
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _hash(contract: IntegrationContract, event: CanonicalEvent, traces: list[Any], actions: list[PlannedAction]) -> str:
        content = {
            "contract_id": contract.contract_id,
            "contract_version": contract.version,
            "event": event.model_dump(mode="json"),
            "traces": [trace.model_dump(mode="json") for trace in traces],
            "actions": [action.model_dump(mode="json") for action in actions],
        }
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
