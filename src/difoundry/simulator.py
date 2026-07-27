from __future__ import annotations

from typing import Any

from .adapters.base import Adapter
from .ledger import EventLedger
from .models import ActionExecution, CanonicalEvent, IntegrationContract, SimulationResult, SystemProfile
from .planner import IntegrationPlanner
from .routing import RoutingPort


class IntegrationSimulator:
    def __init__(
        self,
        profiles: dict[str, SystemProfile],
        adapters: dict[str, Adapter],
        ledger: EventLedger | None = None,
        router: RoutingPort | None = None,
    ) -> None:
        self.profiles = profiles
        self.adapters = adapters
        self.ledger = ledger or EventLedger()
        self.router = router
        self.planner = IntegrationPlanner(profiles, router=router)

    def process(self, contract: IntegrationContract, event: CanonicalEvent, simulate: bool = True) -> SimulationResult:
        if self.ledger.has_idempotency_key(event.idempotency_key):
            return SimulationResult(event_id=event.event_id, status="duplicate", message="Idempotency key already processed")

        self.ledger.record_event(event)
        try:
            plan = self.planner.plan(contract, event)
            self.ledger.record_plan(plan)
            if not plan.actions:
                status = "abstained" if any(trace.abstained for trace in plan.route_traces) else "blocked"
                result = SimulationResult(event_id=event.event_id, plan=plan, status=status, message="No executable action was selected")
                self.ledger.record_result(result)
                return result

            executions: list[ActionExecution] = []
            blocked = False
            failed = False
            for action in plan.actions:
                if not action.certified:
                    blocked = True
                    executions.append(ActionExecution(action_id=action.action_id, status="blocked", error="Mandatory certification failed"))
                    continue
                try:
                    profile = self.profiles[action.target_system_id]
                    operation = profile.operation(action.operation_id)
                    adapter = self.adapters[action.target_system_id]
                    response = adapter.execute(
                        operation,
                        action.payload,
                        action.path_parameters,
                        action.query_parameters,
                        idempotency_key=f"{event.idempotency_key}:{action.action_id}",
                        simulate=simulate,
                    )
                    executions.append(
                        ActionExecution(
                            action_id=action.action_id,
                            status="simulated" if simulate else "succeeded",
                            response=response,
                        )
                    )
                    if not simulate:
                        self._record_router_outcome(action, success=True)
                except Exception as exc:  # boundary intentionally records adapter failures
                    failed = True
                    executions.append(ActionExecution(action_id=action.action_id, status="failed", error=str(exc)))
                    if not simulate:
                        self._record_router_outcome(action, success=False, error=str(exc))

            if failed:
                status = "failed"
            elif blocked:
                status = "blocked"
            else:
                status = "simulated" if simulate else "succeeded"
            result = SimulationResult(event_id=event.event_id, plan=plan, status=status, executions=executions)
        except Exception as exc:
            result = SimulationResult(event_id=event.event_id, status="failed", message=str(exc))
        self.ledger.record_result(result)
        return result

    def _record_router_outcome(self, action: object, success: bool, error: str | None = None) -> None:
        recorder = getattr(self.router, "record_outcome", None)
        if callable(recorder):
            recorder(action, success=success, error=error)

    def replay(self, event_id: str, contract: IntegrationContract, simulate: bool = True) -> SimulationResult:
        original = self.ledger.get_event(event_id)
        replay_event = original.model_copy(
            update={
                "event_id": f"{original.event_id}_replay",
                "idempotency_key": f"{original.idempotency_key}:replay",
                "metadata": {**original.metadata, "replay_of": original.event_id},
            }
        )
        return self.process(contract, replay_event, simulate=simulate)
