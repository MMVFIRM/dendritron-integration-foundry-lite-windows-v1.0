from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .jsonpath import get_path
from .models import ConditionOperator, IntegrationContract, RouteBranch, RouteDefinition, RouteTrace


@dataclass(frozen=True)
class RouteSelection:
    route: RouteDefinition
    trace: RouteTrace


class RoutingPort(Protocol):
    def select(self, contract: IntegrationContract, event_context: dict[str, Any]) -> list[RouteSelection]: ...


class DendriticOwnedRouter:
    """Exact, traceable branch ownership router.

    Each route contains one or more dendritic branches. Conditions within a branch
    are conjunctive: the branch activation is the minimum condition activation.
    A route selects its strongest branch, with explicit abstention on ties or when
    the minimum activation threshold is not reached.

    Phase 0 uses declarative branch conditions. A learned Dendritron can replace
    the scoring implementation later without changing the planner contract.
    """

    def select(self, contract: IntegrationContract, event_context: dict[str, Any]) -> list[RouteSelection]:
        selections: list[RouteSelection] = []
        for route in contract.routes:
            if not route.branches:
                trace = RouteTrace(route_id=route.route_id, selected_branch_id="default", branch_activations={"default": 1.0})
                selections.append(RouteSelection(route=route, trace=trace))
                continue

            scored: list[tuple[float, int, RouteBranch]] = []
            activations: dict[str, float] = {}
            for branch in route.branches:
                activation = self._branch_activation(branch, event_context)
                activations[branch.branch_id] = activation
                scored.append((activation, branch.priority, branch))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            best_activation, _priority, best_branch = scored[0]
            tied = [item for item in scored if item[0] == best_activation and item[1] == _priority]
            if best_activation < best_branch.minimum_activation:
                trace = RouteTrace(
                    route_id=route.route_id,
                    branch_activations=activations,
                    abstained=True,
                    reason=f"No branch reached minimum activation {best_branch.minimum_activation}",
                )
            elif route.abstain_on_tie and len(tied) > 1:
                trace = RouteTrace(
                    route_id=route.route_id,
                    branch_activations=activations,
                    abstained=True,
                    reason="Multiple branches tied for ownership",
                )
            else:
                trace = RouteTrace(
                    route_id=route.route_id,
                    selected_branch_id=best_branch.branch_id,
                    branch_activations=activations,
                )
                selections.append(RouteSelection(route=route, trace=trace))
                continue
            selections.append(RouteSelection(route=route, trace=trace))
        return selections

    def _branch_activation(self, branch: RouteBranch, context: dict[str, Any]) -> float:
        if not branch.conditions:
            return 1.0
        return min(self._condition_activation(condition.path, condition.operator, condition.value, context) for condition in branch.conditions)

    @staticmethod
    def _condition_activation(path: str, operator: ConditionOperator, expected: Any, context: dict[str, Any]) -> float:
        try:
            actual = get_path(context, path)
            exists = True
        except KeyError:
            actual = None
            exists = False
        try:
            if operator == ConditionOperator.EQ:
                matched = actual == expected
            elif operator == ConditionOperator.NE:
                matched = actual != expected
            elif operator == ConditionOperator.IN:
                matched = actual in expected
            elif operator == ConditionOperator.NOT_IN:
                matched = actual not in expected
            elif operator == ConditionOperator.EXISTS:
                matched = exists == bool(expected)
            elif operator == ConditionOperator.GT:
                matched = actual > expected
            elif operator == ConditionOperator.GTE:
                matched = actual >= expected
            elif operator == ConditionOperator.LT:
                matched = actual < expected
            elif operator == ConditionOperator.LTE:
                matched = actual <= expected
            elif operator == ConditionOperator.CONTAINS:
                matched = expected in actual
            else:
                matched = False
        except (TypeError, ValueError):
            matched = False
        return 1.0 if matched else 0.0
