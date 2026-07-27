from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthenticationProfile(StrictModel):
    kind: str = "none"
    secret_refs: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class ObjectFieldProfile(StrictModel):
    name: str
    path: str
    data_type: str = "any"
    required: bool = False
    nullable: bool = True
    description: str = ""
    format: str | None = None
    enum: list[Any] = Field(default_factory=list)
    relation: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObjectProfile(StrictModel):
    object_id: str
    name: str
    description: str = ""
    fields: list[ObjectFieldProfile] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_fields(self) -> "ObjectProfile":
        paths = [field.path for field in self.fields]
        if len(paths) != len(set(paths)):
            raise ValueError(f"field paths must be unique within object {self.object_id!r}")
        return self

    def field(self, path: str) -> ObjectFieldProfile:
        for field in self.fields:
            if field.path == path or field.name == path:
                return field
        raise KeyError(f"Field {path!r} is not defined for object {self.object_id!r}")


class OperationProfile(StrictModel):
    operation_id: str
    method: str
    path: str
    description: str = ""
    object_id: str | None = None
    operation_kind: Literal[
        "create", "read", "update", "delete", "upsert", "search", "list", "publish", "subscribe", "custom"
    ] = "custom"
    request_schema: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    required_permissions: list[str] = Field(default_factory=list)
    idempotency_supported: bool = False
    timeout_seconds: float = 30.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SystemProfile(StrictModel):
    system_id: str
    name: str
    version: str = "1"
    protocol: str = "rest"
    base_url: str | None = None
    authentication: AuthenticationProfile = Field(default_factory=AuthenticationProfile)
    operations: list[OperationProfile] = Field(default_factory=list)
    objects: list[ObjectProfile] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_artifacts(self) -> "SystemProfile":
        operation_ids = [operation.operation_id for operation in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operation_id values must be unique within a system profile")
        object_ids = [obj.object_id for obj in self.objects]
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("object_id values must be unique within a system profile")
        return self

    def operation(self, operation_id: str) -> OperationProfile:
        for operation in self.operations:
            if operation.operation_id == operation_id:
                return operation
        raise KeyError(f"Operation {operation_id!r} is not defined for {self.system_id!r}")

    def object(self, object_id: str) -> ObjectProfile:
        for obj in self.objects:
            if obj.object_id == object_id:
                return obj
        raise KeyError(f"Object {object_id!r} is not defined for {self.system_id!r}")


class TriggerDefinition(StrictModel):
    system_id: str
    object_type: str = "*"
    event_type: str = "*"


class ConditionOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    IN = "in"
    NOT_IN = "not_in"
    EXISTS = "exists"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"


class RouteCondition(StrictModel):
    path: str
    operator: ConditionOperator = ConditionOperator.EQ
    value: Any = None


class RouteBranch(StrictModel):
    branch_id: str
    description: str = ""
    conditions: list[RouteCondition] = Field(default_factory=list)
    priority: int = 0
    minimum_activation: float = 1.0


class MappingRule(StrictModel):
    source: str
    target: str
    required: bool = False
    default: Any = None
    transforms: list[dict[str, Any] | str] = Field(default_factory=list)


class CertifierDefinition(StrictModel):
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)
    required: bool = True


class ActionDefinition(StrictModel):
    action_id: str
    target_system_id: str
    operation_id: str
    mappings: list[MappingRule] = Field(default_factory=list)
    certifiers: list[CertifierDefinition] = Field(default_factory=list)
    path_parameters: dict[str, str] = Field(default_factory=dict)
    query_parameters: dict[str, Any] = Field(default_factory=dict)


class RouteDefinition(StrictModel):
    route_id: str
    branches: list[RouteBranch] = Field(default_factory=list)
    actions: list[ActionDefinition]
    abstain_on_tie: bool = True

    @model_validator(mode="after")
    def unique_branches(self) -> "RouteDefinition":
        branch_ids = [branch.branch_id for branch in self.branches]
        if len(branch_ids) != len(set(branch_ids)):
            raise ValueError("branch_id values must be unique within a route")
        return self


class IntegrationContract(StrictModel):
    contract_id: str
    version: str = "1"
    name: str
    trigger: TriggerDefinition
    routes: list[RouteDefinition]
    permissions: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_ids(self) -> "IntegrationContract":
        route_ids = [route.route_id for route in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("route_id values must be unique")
        action_ids = [action.action_id for route in self.routes for action in route.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action_id values must be unique within a contract")
        return self


class CanonicalEvent(StrictModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex}")
    source_system: str
    source_object: str
    event_type: str
    source_record_id: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str = "1"
    correlation_id: str = Field(default_factory=lambda: f"corr_{uuid4().hex}")
    idempotency_key: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteTrace(StrictModel):
    route_id: str
    selected_branch_id: str | None = None
    branch_activations: dict[str, float] = Field(default_factory=dict)
    abstained: bool = False
    reason: str | None = None
    router_kind: str = "declarative"
    selected_specialist_ids: list[str] = Field(default_factory=list)
    novelty_score: float = Field(default=0.0, ge=0.0, le=1.0)
    ownership_key: str | None = None
    tissue_version: int | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class CertificationResult(StrictModel):
    kind: str
    passed: bool
    required: bool = True
    details: str = ""


class PlannedAction(StrictModel):
    action_id: str
    target_system_id: str
    operation_id: str
    payload: dict[str, Any]
    path_parameters: dict[str, Any] = Field(default_factory=dict)
    query_parameters: dict[str, Any] = Field(default_factory=dict)
    certifications: list[CertificationResult] = Field(default_factory=list)
    route_id: str | None = None
    branch_id: str | None = None
    specialist_ids: list[str] = Field(default_factory=list)
    ownership_key: str | None = None

    @property
    def certified(self) -> bool:
        return all(result.passed or not result.required for result in self.certifications)


class ExecutionPlan(StrictModel):
    plan_id: str = Field(default_factory=lambda: f"plan_{uuid4().hex}")
    contract_id: str
    contract_version: str
    event_id: str
    route_traces: list[RouteTrace]
    actions: list[PlannedAction]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    plan_hash: str
    behavior_hash: str


class ActionExecution(StrictModel):
    action_id: str
    status: Literal["simulated", "succeeded", "failed", "blocked"]
    response: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class SimulationResult(StrictModel):
    event_id: str
    plan: ExecutionPlan | None = None
    status: Literal["planned", "simulated", "succeeded", "blocked", "duplicate", "abstained", "failed"]
    executions: list[ActionExecution] = Field(default_factory=list)
    message: str = ""


# Phase 1 discovery and composition artifacts.


class DiscoverySource(StrictModel):
    source_id: str = Field(default_factory=lambda: f"src_{uuid4().hex}")
    format: str = "auto"
    document: dict[str, Any] | list[Any] | str
    system_id: str | None = None
    name: str | None = None
    base_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoveryEvidence(StrictModel):
    artifact: str
    location: str
    statement: str


class DiscoveryResult(StrictModel):
    discovery_id: str = Field(default_factory=lambda: f"disc_{uuid4().hex}")
    provider: str
    source_id: str
    source_hash: str = ""
    profile: SystemProfile
    warnings: list[str] = Field(default_factory=list)
    evidence: list[DiscoveryEvidence] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SemanticNode(StrictModel):
    node_id: str
    system_id: str
    object_id: str
    field_path: str | None = None
    label: str
    kind: Literal["object", "field"]
    data_type: str = "any"
    required: bool = False
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticEdge(StrictModel):
    source_node_id: str
    target_node_id: str
    relation: Literal["exact", "likely", "derived", "ambiguous", "unsupported"]
    score: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    suggested_transforms: list[str | dict[str, Any]] = Field(default_factory=list)
    needs_review: bool = False


class SemanticQuestion(StrictModel):
    question_id: str
    prompt: str
    reason: str
    source_node_id: str | None = None
    target_node_id: str | None = None
    choices: list[str] = Field(default_factory=list)
    required: bool = True


class SemanticGraph(StrictModel):
    graph_id: str = Field(default_factory=lambda: f"graph_{uuid4().hex}")
    source_system_id: str
    source_object_id: str
    target_system_id: str
    target_object_id: str
    nodes: list[SemanticNode] = Field(default_factory=list)
    edges: list[SemanticEdge] = Field(default_factory=list)
    questions: list[SemanticQuestion] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class TargetIntent(StrictModel):
    target_system_id: str
    target_object_id: str | None = None
    operation_id: str | None = None
    action_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompositionRequest(StrictModel):
    composition_id: str = Field(default_factory=lambda: f"compose_{uuid4().hex}")
    name: str
    source_system_id: str
    source_object_id: str | None = None
    event_type: str = "*"
    targets: list[TargetIntent]
    minimum_mapping_score: float = Field(default=0.58, ge=0.0, le=1.0)
    require_review_below: float = Field(default=0.78, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DaughterManifest(StrictModel):
    daughter_id: str
    version: str = "0.1.0"
    name: str
    status: Literal["scaffolded", "verified", "shadow", "deployed", "paused"] = "scaffolded"
    source_system_id: str
    target_system_ids: list[str]
    owned_objects: dict[str, list[str]] = Field(default_factory=dict)
    contract_ids: list[str]
    semantic_graph_ids: list[str]
    required_adapters: dict[str, str] = Field(default_factory=dict)
    required_permissions: dict[str, list[str]] = Field(default_factory=dict)
    state_capabilities: list[str] = Field(default_factory=list)
    trust_policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationCase(StrictModel):
    case_id: str
    category: Literal[
        "contract", "schema", "mapping", "permission", "idempotency", "replay", "drift", "failure_isolation", "security"
    ]
    description: str
    required: bool = True
    status: Literal["scaffolded", "passed", "failed", "blocked"] = "scaffolded"
    inputs: dict[str, Any] = Field(default_factory=dict)
    assertions: list[str] = Field(default_factory=list)


class VerificationBundle(StrictModel):
    bundle_id: str = Field(default_factory=lambda: f"verify_{uuid4().hex}")
    daughter_id: str
    cases: list[VerificationCase]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompositionResult(StrictModel):
    composition_id: str
    contract: IntegrationContract
    semantic_graphs: list[SemanticGraph]
    daughter_manifest: DaughterManifest
    verification_bundle: VerificationBundle
    questions: list[SemanticQuestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ready_for_verification: bool = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
