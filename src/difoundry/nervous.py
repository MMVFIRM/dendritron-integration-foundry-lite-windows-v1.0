from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .adapters.base import Adapter
from .jsonpath import get_path, set_path
from .models import CanonicalEvent, IntegrationContract, SimulationResult, StrictModel, SystemProfile
from .simulator import IntegrationSimulator
from .tissue import DendritronRoutingTissue


StepStatus = Literal["pending", "succeeded", "failed", "blocked", "skipped", "simulated", "compensated"]
CoordinationStatus = Literal["succeeded", "failed", "blocked", "partial", "simulated"]
PolicyEffect = Literal["allow", "deny", "require_approval"]


class DaughterCapability(StrictModel):
    capability_id: str
    route_ids: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=lambda: ["*"])
    source_objects: list[str] = Field(default_factory=lambda: ["*"])
    description: str = ""


class DaughterRegistration(StrictModel):
    daughter_id: str
    name: str
    contract_id: str
    capabilities: list[DaughterCapability]
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_capabilities(self) -> "DaughterRegistration":
        identifiers = [item.capability_id for item in self.capabilities]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("capability_id values must be unique within a daughter")
        return self


class GlobalPolicyRule(StrictModel):
    rule_id: str
    effect: PolicyEffect
    source_daughter_id: str = "*"
    target_daughter_id: str = "*"
    capability_id: str = "*"
    event_type: str = "*"
    metadata_equals: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    priority: int = 0


class GlobalPolicySet(StrictModel):
    policy_id: str = "default"
    version: str = "1"
    default_effect: PolicyEffect = "deny"
    rules: list[GlobalPolicyRule] = Field(default_factory=list)
    maximum_hops: int = Field(default=16, ge=1, le=256)
    maximum_fanout: int = Field(default=32, ge=1, le=1024)
    require_registered_capability: bool = True


class PolicyDecision(StrictModel):
    allowed: bool
    approval_required: bool = False
    rule_id: str | None = None
    reason: str = ""


class CoordinationInput(StrictModel):
    source: str
    target: str
    required: bool = True
    default: Any = None


class CoordinationStep(StrictModel):
    step_id: str
    daughter_id: str
    capability_id: str
    source_system: str
    source_object: str
    event_type: str
    depends_on: list[str] = Field(default_factory=list)
    inputs: list[CoordinationInput] = Field(default_factory=list)
    required: bool = True
    execute: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoordinationWorkflow(StrictModel):
    workflow_id: str
    version: str = "1"
    name: str
    steps: list[CoordinationStep]
    compensate_on_required_failure: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self) -> "CoordinationWorkflow":
        identifiers = [step.step_id for step in self.steps]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("step_id values must be unique")
        known = set(identifiers)
        for step in self.steps:
            unknown = set(step.depends_on) - known
            if unknown:
                raise ValueError(f"Step {step.step_id!r} depends on unknown steps: {sorted(unknown)}")
            if step.step_id in step.depends_on:
                raise ValueError("A step cannot depend on itself")
        self._topological_order()
        return self

    def _topological_order(self) -> list[str]:
        remaining = {step.step_id: set(step.depends_on) for step in self.steps}
        ordered: list[str] = []
        while remaining:
            ready = sorted(key for key, dependencies in remaining.items() if not dependencies)
            if not ready:
                raise ValueError("Coordination workflow contains a dependency cycle")
            ordered.extend(ready)
            for key in ready:
                remaining.pop(key)
            for dependencies in remaining.values():
                dependencies.difference_update(ready)
        return ordered

    def topological_steps(self) -> list[CoordinationStep]:
        by_id = {step.step_id: step for step in self.steps}
        return [by_id[item] for item in self._topological_order()]


class NervousEvent(StrictModel):
    nervous_event_id: str = Field(default_factory=lambda: f"nev_{uuid4().hex}")
    root_event_id: str
    correlation_id: str
    causation_id: str | None = None
    causation_ids: list[str] = Field(default_factory=list)
    workflow_id: str
    step_id: str
    source_daughter_id: str = "external"
    source_daughter_ids: list[str] = Field(default_factory=list)
    target_daughter_id: str
    capability_id: str
    hop_count: int = 0
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StepExecution(StrictModel):
    step_id: str
    daughter_id: str
    capability_id: str
    status: StepStatus
    nervous_event_id: str | None = None
    local_event_id: str | None = None
    local_contract_id: str | None = None
    local_contract_version: str | None = None
    ownership_keys: list[str] = Field(default_factory=list)
    behavior_hash: str | None = None
    response: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    policy_decision: PolicyDecision | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


class CoordinationResult(StrictModel):
    coordination_id: str = Field(default_factory=lambda: f"coord_{uuid4().hex}")
    workflow_id: str
    workflow_version: str
    root_event_id: str
    correlation_id: str
    status: CoordinationStatus
    steps: list[StepExecution]
    lineage_hash: str
    started_at: datetime
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NervousTopologyBundle(StrictModel):
    format: str = "difoundry-nervous-topology-v1"
    policy: GlobalPolicySet
    daughters: list[DaughterRegistration]
    workflows: list[CoordinationWorkflow]
    topology_hash: str = ""

    @model_validator(mode="after")
    def bind_hash(self) -> "NervousTopologyBundle":
        actual = nervous_topology_hash(self)
        if self.topology_hash and self.topology_hash != actual:
            raise ValueError("nervous topology hash mismatch")
        self.topology_hash = actual
        return self


class DaughterRuntimeRequest(StrictModel):
    registration: DaughterRegistration
    contract: IntegrationContract
    profiles: list[SystemProfile]


def nervous_topology_hash(bundle: NervousTopologyBundle | dict[str, Any]) -> str:
    data = bundle.model_dump(mode="json") if hasattr(bundle, "model_dump") else deepcopy(bundle)
    data.pop("topology_hash", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class NervousTopologyStore:
    format_name = "difoundry-nervous-topology-envelope-v1"

    @classmethod
    def save(cls, path: str | Path, bundle: NervousTopologyBundle) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = bundle.model_dump(mode="json")
        envelope = {
            "format": cls.format_name,
            "bundle_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "bundle": payload,
        }
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(target)
        return target

    @classmethod
    def load(cls, path: str | Path) -> NervousTopologyBundle:
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        if envelope.get("format") != cls.format_name:
            raise ValueError("Unsupported nervous topology format")
        payload = envelope.get("bundle")
        expected = envelope.get("bundle_hash")
        actual = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if expected != actual:
            raise ValueError("Nervous topology envelope hash mismatch")
        return NervousTopologyBundle.model_validate(payload)


class CapabilityScopedRouter:
    def __init__(self, tissue: DendritronRoutingTissue, route_ids: set[str]) -> None:
        self.tissue = tissue
        self.route_ids = route_ids

    def select(self, contract: IntegrationContract, event_context: dict[str, Any]) -> list[Any]:
        return [
            selection
            for selection in self.tissue.select(contract, event_context)
            if selection.route.route_id in self.route_ids
        ]

    def record_outcome(self, action: object, success: bool, error: str | None = None) -> Any:
        return self.tissue.record_outcome(action, success=success, error=error)


class DaughterRuntime:
    def __init__(
        self,
        registration: DaughterRegistration,
        contract: IntegrationContract,
        profiles: dict[str, SystemProfile],
        adapters: dict[str, Adapter],
        tissue: DendritronRoutingTissue,
    ) -> None:
        if registration.contract_id != contract.contract_id:
            raise ValueError("Daughter registration contract_id does not match runtime contract")
        tissue.validate_contract(contract)
        target_systems = {
            action.target_system_id
            for route in contract.routes
            for action in route.actions
        }
        missing_profiles = target_systems - set(profiles)
        if missing_profiles:
            raise ValueError(f"Daughter runtime is missing target profiles: {sorted(missing_profiles)}")
        self.registration = registration
        self.contract = contract
        self.profiles = profiles
        self.adapters = adapters
        self.tissue = tissue
        self.simulator = IntegrationSimulator(profiles, adapters, router=tissue)
        known_routes = {route.route_id for route in contract.routes}
        for capability in registration.capabilities:
            if not capability.route_ids:
                if len(known_routes) == 1:
                    capability.route_ids = sorted(known_routes)
                else:
                    raise ValueError(
                        f"Capability {capability.capability_id!r} must declare route_ids for a multi-route daughter"
                    )
            unknown = set(capability.route_ids) - known_routes
            if unknown:
                raise ValueError(
                    f"Capability {capability.capability_id!r} references unknown routes: {sorted(unknown)}"
                )

    def capability(self, capability_id: str) -> DaughterCapability:
        for capability in self.registration.capabilities:
            if capability.capability_id == capability_id:
                return capability
        raise KeyError(f"Unknown daughter capability {capability_id!r}")

    def process(self, capability_id: str, event: CanonicalEvent, simulate: bool) -> SimulationResult:
        capability = self.capability(capability_id)
        router = CapabilityScopedRouter(self.tissue, set(capability.route_ids))
        simulator = IntegrationSimulator(
            self.profiles, self.adapters, ledger=self.simulator.ledger, router=router
        )
        return simulator.process(self.contract, event, simulate=simulate)

    def supports(self, capability_id: str, event_type: str, source_object: str) -> bool:
        if not self.registration.enabled:
            return False
        for capability in self.registration.capabilities:
            if capability.capability_id != capability_id:
                continue
            event_ok = "*" in capability.event_types or event_type in capability.event_types
            object_ok = "*" in capability.source_objects or source_object in capability.source_objects
            return event_ok and object_ok
        return False


class GlobalPolicyEngine:
    def __init__(self, policy: GlobalPolicySet | None = None) -> None:
        self.policy = policy or GlobalPolicySet()

    def evaluate(self, event: NervousEvent, daughter: DaughterRuntime) -> PolicyDecision:
        if event.hop_count > self.policy.maximum_hops:
            return PolicyDecision(allowed=False, reason="Global maximum hop count exceeded")
        if self.policy.require_registered_capability and not daughter.supports(
            event.capability_id, event.metadata.get("event_type", "*"), event.metadata.get("source_object", "*")
        ):
            return PolicyDecision(allowed=False, reason="Target daughter does not own the requested capability")
        matching = [rule for rule in self.policy.rules if self._matches(rule, event)]
        matching.sort(key=lambda item: item.priority, reverse=True)
        if matching:
            rule = matching[0]
            return PolicyDecision(
                allowed=rule.effect == "allow",
                approval_required=rule.effect == "require_approval",
                rule_id=rule.rule_id,
                reason=rule.reason or f"Matched global policy rule {rule.rule_id}",
            )
        return PolicyDecision(
            allowed=self.policy.default_effect == "allow",
            approval_required=self.policy.default_effect == "require_approval",
            reason=f"Global policy default effect: {self.policy.default_effect}",
        )

    @staticmethod
    def _matches(rule: GlobalPolicyRule, event: NervousEvent) -> bool:
        sources = event.source_daughter_ids or [event.source_daughter_id]
        if rule.source_daughter_id != "*" and rule.source_daughter_id not in sources:
            return False
        fields = (
            (rule.target_daughter_id, event.target_daughter_id),
            (rule.capability_id, event.capability_id),
            (rule.event_type, event.metadata.get("event_type", "*")),
        )
        if any(expected not in {"*", actual} for expected, actual in fields):
            return False
        return all(event.metadata.get(key) == value for key, value in rule.metadata_equals.items())


class NervousLedger:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS nervous_events (
                    nervous_event_id TEXT PRIMARY KEY,
                    root_event_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coordinations (
                    coordination_id TEXT PRIMARY KEY,
                    root_event_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_coordination_root_workflow
                    ON coordinations(root_event_id, workflow_id);
                """
            )

    def seen(self, root_event_id: str, workflow_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM coordinations WHERE root_event_id = ? AND workflow_id = ?", (root_event_id, workflow_id)
        ).fetchone()
        return row is not None

    def record_event(self, event: NervousEvent) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT INTO nervous_events VALUES (?, ?, ?, ?, ?)",
                (
                    event.nervous_event_id,
                    event.root_event_id,
                    event.workflow_id,
                    event.step_id,
                    json.dumps(event.model_dump(mode="json"), sort_keys=True),
                ),
            )

    def record_result(self, result: CoordinationResult) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT INTO coordinations VALUES (?, ?, ?, ?, ?)",
                (
                    result.coordination_id,
                    result.root_event_id,
                    result.workflow_id,
                    result.status,
                    json.dumps(result.model_dump(mode="json"), sort_keys=True),
                ),
            )

    def export_lineage(self, root_event_id: str) -> dict[str, Any]:
        events = self.connection.execute(
            "SELECT payload_json FROM nervous_events WHERE root_event_id = ? ORDER BY rowid", (root_event_id,)
        ).fetchall()
        coordinations = self.connection.execute(
            "SELECT result_json FROM coordinations WHERE root_event_id = ? ORDER BY rowid", (root_event_id,)
        ).fetchall()
        return {
            "root_event_id": root_event_id,
            "events": [json.loads(row["payload_json"]) for row in events],
            "coordinations": [json.loads(row["result_json"]) for row in coordinations],
        }


class MultiSystemNervousSystem:
    """Coordinates daughters without merging their execution or learning state."""

    def __init__(self, policy: GlobalPolicySet | None = None, ledger: NervousLedger | None = None) -> None:
        self.daughters: dict[str, DaughterRuntime] = {}
        self.workflows: dict[str, CoordinationWorkflow] = {}
        self.policy_engine = GlobalPolicyEngine(policy)
        self.ledger = ledger or NervousLedger()
        self._lock = RLock()

    @property
    def policy(self) -> GlobalPolicySet:
        return self.policy_engine.policy

    def register_daughter(self, daughter: DaughterRuntime) -> None:
        with self._lock:
            if daughter.registration.daughter_id in self.daughters:
                raise ValueError("Daughter is already registered")
            self.daughters[daughter.registration.daughter_id] = daughter

    def register_workflow(self, workflow: CoordinationWorkflow) -> None:
        with self._lock:
            if len(workflow.steps) > self.policy.maximum_fanout * self.policy.maximum_hops:
                raise ValueError("Workflow exceeds global policy complexity limit")
            outgoing: dict[str, int] = {step.step_id: 0 for step in workflow.steps}
            roots = 0
            for step in workflow.steps:
                if not step.depends_on:
                    roots += 1
                for dependency in step.depends_on:
                    outgoing[dependency] += 1
            if roots > self.policy.maximum_fanout or any(value > self.policy.maximum_fanout for value in outgoing.values()):
                raise ValueError("Workflow exceeds global policy fan-out limit")
            for step in workflow.steps:
                if step.daughter_id not in self.daughters:
                    raise ValueError(f"Workflow references unknown daughter {step.daughter_id!r}")
            self.workflows[workflow.workflow_id] = workflow

    def coordinate(
        self,
        workflow_id: str,
        root_event: CanonicalEvent,
        *,
        approvals: set[str] | None = None,
        simulate: bool = False,
    ) -> CoordinationResult:
        with self._lock:
            if workflow_id not in self.workflows:
                raise KeyError(f"Unknown workflow {workflow_id!r}")
            if self.ledger.seen(root_event.event_id, workflow_id):
                raise ValueError("This root event has already been coordinated for the workflow")
            workflow = self.workflows[workflow_id]
            started = datetime.now(timezone.utc)
            approvals = approvals or set()
            executions: dict[str, StepExecution] = {}
            successful_steps: list[CoordinationStep] = []

            root_context: dict[str, Any] = {
                "root": root_event.model_dump(mode="python"),
                "steps": {},
            }
            for step in workflow.topological_steps():
                dependency_results = [executions[item] for item in step.depends_on]
                if any(item.status not in {"succeeded", "simulated"} for item in dependency_results):
                    executions[step.step_id] = StepExecution(
                        step_id=step.step_id,
                        daughter_id=step.daughter_id,
                        capability_id=step.capability_id,
                        status="skipped",
                        error="A dependency did not complete successfully",
                        completed_at=datetime.now(timezone.utc),
                    )
                    root_context["steps"][step.step_id] = executions[step.step_id].model_dump(mode="python")
                    continue

                try:
                    payload = self._map_step_payload(step, root_context)
                except (KeyError, ValueError) as exc:
                    executions[step.step_id] = StepExecution(
                        step_id=step.step_id,
                        daughter_id=step.daughter_id,
                        capability_id=step.capability_id,
                        status="blocked",
                        error=str(exc),
                        completed_at=datetime.now(timezone.utc),
                    )
                    root_context["steps"][step.step_id] = executions[step.step_id].model_dump(mode="python")
                    continue
                source_daughters = (
                    [executions[dependency].daughter_id for dependency in step.depends_on]
                    if step.depends_on
                    else ["external"]
                )
                causation_ids = (
                    [
                        executions[dependency].nervous_event_id
                        for dependency in step.depends_on
                        if executions[dependency].nervous_event_id is not None
                    ]
                    if step.depends_on
                    else [root_event.event_id]
                )
                source_daughter = source_daughters[-1]
                causation_id = causation_ids[-1]
                event = NervousEvent(
                    root_event_id=root_event.event_id,
                    correlation_id=root_event.correlation_id,
                    causation_id=causation_id,
                    causation_ids=causation_ids,
                    workflow_id=workflow.workflow_id,
                    step_id=step.step_id,
                    source_daughter_id=source_daughter,
                    source_daughter_ids=source_daughters,
                    target_daughter_id=step.daughter_id,
                    capability_id=step.capability_id,
                    hop_count=self._hop_count(step, executions),
                    payload=payload,
                    metadata={
                        **step.metadata,
                        "event_type": step.event_type,
                        "source_object": step.source_object,
                        "workflow_version": workflow.version,
                    },
                )
                daughter = self.daughters[step.daughter_id]
                decision = self.policy_engine.evaluate(event, daughter)
                if decision.approval_required:
                    approval_key = decision.rule_id or self.policy.policy_id
                    if approval_key in approvals:
                        decision.allowed = True
                        decision.reason = f"Global policy approval {approval_key!r} supplied"
                    else:
                        decision.allowed = False
                        decision.reason = f"Global policy approval {approval_key!r} is required"
                if not decision.allowed:
                    executions[step.step_id] = StepExecution(
                        step_id=step.step_id,
                        daughter_id=step.daughter_id,
                        capability_id=step.capability_id,
                        status="blocked",
                        nervous_event_id=event.nervous_event_id,
                        policy_decision=decision,
                        error=decision.reason,
                        completed_at=datetime.now(timezone.utc),
                    )
                    self.ledger.record_event(event)
                    root_context["steps"][step.step_id] = executions[step.step_id].model_dump(mode="python")
                    continue

                self.ledger.record_event(event)
                local_event = CanonicalEvent(
                    event_id=f"{root_event.event_id}:{workflow.workflow_id}:{step.step_id}",
                    source_system=step.source_system,
                    source_object=step.source_object,
                    event_type=step.event_type,
                    source_record_id=root_event.source_record_id,
                    correlation_id=root_event.correlation_id,
                    idempotency_key=f"{root_event.idempotency_key}:{workflow.workflow_id}:{step.step_id}",
                    payload=payload,
                    metadata={
                        **event.metadata,
                        "nervous_event_id": event.nervous_event_id,
                        "root_event_id": root_event.event_id,
                        "causation_id": event.causation_id,
                        "causation_ids": event.causation_ids,
                        "source_daughter_ids": event.source_daughter_ids,
                    },
                )
                step_started_at = datetime.now(timezone.utc)
                result = daughter.process(step.capability_id, local_event, simulate=simulate or not step.execute)
                execution = self._step_execution(
                    step, event, daughter, local_event, result, decision, step_started_at
                )
                executions[step.step_id] = execution
                root_context["steps"][step.step_id] = {
                    **execution.model_dump(mode="python"),
                    "result": result.model_dump(mode="python"),
                    "outputs": self._result_outputs(result),
                }
                if execution.status in {"succeeded", "simulated"}:
                    successful_steps.append(step)

            ordered = [executions[step.step_id] for step in workflow.topological_steps()]
            required_failures = [
                item for item in ordered
                if next(step for step in workflow.steps if step.step_id == item.step_id).required
                and item.status not in {"succeeded", "simulated"}
            ]
            optional_failures = [item for item in ordered if item.status in {"failed", "blocked", "skipped"}]
            if required_failures:
                status: CoordinationStatus = "failed" if any(item.status == "failed" for item in required_failures) else "blocked"
            elif optional_failures:
                status = "partial"
            else:
                status = "simulated" if simulate else "succeeded"

            lineage_hash = self._lineage_hash(workflow, root_event, ordered)
            result = CoordinationResult(
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.version,
                root_event_id=root_event.event_id,
                correlation_id=root_event.correlation_id,
                status=status,
                steps=ordered,
                lineage_hash=lineage_hash,
                started_at=started,
            )
            self.ledger.record_result(result)
            return result

    @staticmethod
    def _map_step_payload(step: CoordinationStep, context: dict[str, Any]) -> dict[str, Any]:
        if not step.inputs:
            root_payload = get_path(context, "root.payload", {})
            return deepcopy(root_payload) if isinstance(root_payload, dict) else {"value": root_payload}
        payload: dict[str, Any] = {}
        for item in step.inputs:
            try:
                value = get_path(context, item.source)
            except KeyError:
                value = item.default
                if item.required and value is None:
                    raise ValueError(f"Required coordination input {item.source!r} is missing")
            set_path(payload, item.target, deepcopy(value))
        return payload

    @staticmethod
    def _hop_count(step: CoordinationStep, executions: dict[str, StepExecution]) -> int:
        if not step.depends_on:
            return 1
        return 1 + max(
            int((executions[dependency].response or {}).get("hop_count", 1)) for dependency in step.depends_on
        )

    @staticmethod
    def _result_outputs(result: SimulationResult) -> dict[str, Any]:
        return {
            execution.action_id: execution.response
            for execution in result.executions
            if execution.status in {"succeeded", "simulated"}
        }

    @staticmethod
    def _step_execution(
        step: CoordinationStep,
        event: NervousEvent,
        daughter: DaughterRuntime,
        local_event: CanonicalEvent,
        result: SimulationResult,
        decision: PolicyDecision,
        started_at: datetime,
    ) -> StepExecution:
        status_map: dict[str, StepStatus] = {
            "succeeded": "succeeded",
            "simulated": "simulated",
            "failed": "failed",
            "blocked": "blocked",
            "abstained": "blocked",
            "duplicate": "blocked",
            "planned": "simulated",
        }
        ownership = []
        behavior_hash = None
        if result.plan:
            ownership = sorted({action.ownership_key for action in result.plan.actions if action.ownership_key})
            behavior_hash = result.plan.behavior_hash
        response = {
            "hop_count": event.hop_count,
            "local_status": result.status,
            "action_outputs": MultiSystemNervousSystem._result_outputs(result),
        }
        error = result.message or "; ".join(item.error or "" for item in result.executions if item.error) or None
        return StepExecution(
            step_id=step.step_id,
            daughter_id=step.daughter_id,
            capability_id=step.capability_id,
            status=status_map[result.status],
            nervous_event_id=event.nervous_event_id,
            local_event_id=local_event.event_id,
            local_contract_id=daughter.contract.contract_id,
            local_contract_version=daughter.contract.version,
            ownership_keys=ownership,
            behavior_hash=behavior_hash,
            response=response,
            error=error,
            policy_decision=decision,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _lineage_hash(workflow: CoordinationWorkflow, root: CanonicalEvent, steps: list[StepExecution]) -> str:
        payload = {
            "workflow_id": workflow.workflow_id,
            "workflow_version": workflow.version,
            "root_event_id": root.event_id,
            "correlation_id": root.correlation_id,
            "steps": [
                {
                    "step_id": step.step_id,
                    "daughter_id": step.daughter_id,
                    "capability_id": step.capability_id,
                    "status": step.status,
                    "local_contract_id": step.local_contract_id,
                    "local_contract_version": step.local_contract_version,
                    "ownership_keys": step.ownership_keys,
                    "behavior_hash": step.behavior_hash,
                    "policy_rule_id": step.policy_decision.rule_id if step.policy_decision else None,
                    "error": step.error,
                }
                for step in steps
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def topology_bundle(self) -> NervousTopologyBundle:
        registrations: list[DaughterRegistration] = []
        for _, runtime in sorted(self.daughters.items()):
            contract_payload = runtime.contract.model_dump(mode="json")
            tissue_payload = runtime.tissue.state.model_dump(mode="json")
            metadata = {
                **runtime.registration.metadata,
                "contract_version": runtime.contract.version,
                "contract_hash": hashlib.sha256(
                    json.dumps(contract_payload, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "tissue_id": runtime.tissue.state.tissue_id,
                "tissue_version": runtime.tissue.state.version,
                "tissue_hash": hashlib.sha256(
                    json.dumps(tissue_payload, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            }
            registrations.append(runtime.registration.model_copy(update={"metadata": metadata}))
        return NervousTopologyBundle(
            policy=self.policy,
            daughters=registrations,
            workflows=[workflow for _, workflow in sorted(self.workflows.items())],
        )

    def topology(self) -> dict[str, Any]:
        bundle = self.topology_bundle()
        return bundle.model_dump(mode="json")
