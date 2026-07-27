from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Literal
from uuid import uuid4

import yaml
from pydantic import Field, model_validator

from .adapters.memory import MemoryAdapter
from .ledger import EventLedger
from .models import (
    CanonicalEvent,
    IntegrationContract,
    MappingRule,
    SimulationResult,
    StrictModel,
    SystemProfile,
)
from .simulator import IntegrationSimulator
from .tissue import DendritronRoutingTissue, TissueStore
from .validation import ContractValidator


DriftKind = Literal[
    "schema", "endpoint", "permission", "authentication", "behavior", "semantic", "volume", "latency", "unknown"
]
RiskLevel = Literal["low", "medium", "high", "critical"]
RepairStatus = Literal[
    "proposed", "verification_failed", "verified", "approval_required", "approved", "signed", "deployed", "rejected"
]
PatchOp = Literal["add", "replace", "remove", "test"]


class DriftObservation(StrictModel):
    drift_id: str = Field(default_factory=lambda: f"drift_{uuid4().hex}")
    kind: DriftKind = "unknown"
    event_id: str
    contract_id: str
    contract_version: str
    action_id: str | None = None
    target_system_id: str | None = None
    operation_id: str | None = None
    route_id: str | None = None
    branch_id: str | None = None
    ownership_key: str | None = None
    specialist_ids: list[str] = Field(default_factory=list)
    failure_signature: str
    error: str = ""
    expected: dict[str, Any] = Field(default_factory=dict)
    observed: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ArtifactPatch(StrictModel):
    artifact: str
    operation: PatchOp
    path: str
    value: Any = None
    reason: str = ""

    @model_validator(mode="after")
    def valid_pointer(self) -> "ArtifactPatch":
        if not self.path.startswith("/") and self.path != "":
            raise ValueError("patch path must be a JSON Pointer")
        return self


class RepairApproval(StrictModel):
    status: Literal["pending", "approved", "rejected"] = "pending"
    approver: str | None = None
    reason: str = ""
    approved_at: datetime | None = None


class RepairSignature(StrictModel):
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    key_id: str
    signed_payload_hash: str
    digest: str
    signed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RepairVerificationCase(StrictModel):
    name: str
    passed: bool
    details: str = ""


class RepairVerificationReport(StrictModel):
    report_id: str = Field(default_factory=lambda: f"repair_verify_{uuid4().hex}")
    repair_id: str
    passed: bool
    impacted_events: int = 0
    unrelated_events: int = 0
    cases: list[RepairVerificationCase] = Field(default_factory=list)
    before_owner_hash: str | None = None
    after_owner_hash: str | None = None
    unrelated_branch_hashes_unchanged: bool = True
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RepairCandidate(StrictModel):
    repair_id: str = Field(default_factory=lambda: f"repair_{uuid4().hex}")
    drift_id: str
    contract_id: str
    base_contract_version: str
    proposed_contract_version: str
    route_id: str | None = None
    branch_id: str | None = None
    ownership_key: str | None = None
    risk: RiskLevel = "medium"
    summary: str
    patches: list[ArtifactPatch]
    affected_artifacts: list[str] = Field(default_factory=list)
    expected_scope: list[str] = Field(default_factory=list)
    rollback_patches: list[ArtifactPatch] = Field(default_factory=list)
    status: RepairStatus = "proposed"
    approval: RepairApproval = Field(default_factory=RepairApproval)
    verification: RepairVerificationReport | None = None
    signature: RepairSignature | None = None
    candidate_hash: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bind_hash(self) -> "RepairCandidate":
        if not self.affected_artifacts:
            self.affected_artifacts = sorted({patch.artifact for patch in self.patches})
        actual = repair_candidate_hash(self)
        if self.candidate_hash and self.candidate_hash != actual:
            raise ValueError("repair candidate hash mismatch")
        self.candidate_hash = actual
        return self


class RepairPolicy(StrictModel):
    automatic_risk_levels: list[RiskLevel] = Field(default_factory=lambda: ["low"])
    require_human_for_permissions: bool = True
    require_human_for_endpoints: bool = True
    require_signature: bool = True
    allow_contract_paths: list[str] = Field(
        default_factory=lambda: ["/routes/", "/permissions/", "/metadata/"]
    )
    allow_profile_paths: list[str] = Field(
        default_factory=lambda: ["/operations/", "/objects/", "/metadata/", "/version"]
    )
    forbidden_fragments: list[str] = Field(
        default_factory=lambda: ["secret", "password", "token", "private_key"]
    )


class RepairDeployment(StrictModel):
    deployment_id: str = Field(default_factory=lambda: f"deploy_{uuid4().hex}")
    repair_id: str
    contract_id: str
    previous_version: str
    deployed_version: str
    contract_hash: str
    profile_hashes: dict[str, str] = Field(default_factory=dict)
    tissue_hash: str
    deployed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    artifact_dir: str | None = None




class RepairVerificationRequest(StrictModel):
    tissue_id: str
    events: list[CanonicalEvent]
    impacted_event_ids: list[str] = Field(default_factory=list)

class Phase3RuntimeResult(StrictModel):
    result: SimulationResult
    drifts: list[DriftObservation] = Field(default_factory=list)
    quarantined: bool = False


def canonical_hash(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def repair_candidate_hash(candidate: RepairCandidate | dict[str, Any]) -> str:
    data = candidate.model_dump(mode="json") if hasattr(candidate, "model_dump") else copy.deepcopy(candidate)
    for key in ("candidate_hash", "signature", "verification", "approval", "status"):
        data.pop(key, None)
    return canonical_hash(data)


def repair_signing_hash(candidate: RepairCandidate) -> str:
    payload = {
        "candidate_hash": candidate.candidate_hash,
        "verification": candidate.verification.model_dump(mode="json") if candidate.verification else None,
        "approval": candidate.approval.model_dump(mode="json"),
    }
    return canonical_hash(payload)


def failure_signature(error: str) -> str:
    normalized = " ".join(error.lower().split())[:512]
    return "failure:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


class DriftDetector:
    """Turns execution or certification failures into exact owner-bound observations."""

    def detect(
        self,
        result: SimulationResult,
        event: CanonicalEvent,
        contract: IntegrationContract,
        profiles: dict[str, SystemProfile],
        evidence: dict[str, Any] | None = None,
    ) -> list[DriftObservation]:
        if result.plan is None:
            if result.status != "failed":
                return []
            return [
                DriftObservation(
                    event_id=event.event_id,
                    contract_id=contract.contract_id,
                    contract_version=contract.version,
                    failure_signature=failure_signature(result.message),
                    error=result.message,
                    evidence=evidence or {},
                )
            ]
        execution_by_action = {execution.action_id: execution for execution in result.executions}
        observations: list[DriftObservation] = []
        for action in result.plan.actions:
            execution = execution_by_action.get(action.action_id)
            certification_failures = [item.details for item in action.certifications if item.required and not item.passed]
            error = execution.error if execution and execution.status == "failed" else "; ".join(certification_failures)
            if not error:
                continue
            operation = profiles[action.target_system_id].operation(action.operation_id)
            kind = self._classify(error, evidence or {})
            observations.append(
                DriftObservation(
                    kind=kind,
                    event_id=event.event_id,
                    contract_id=contract.contract_id,
                    contract_version=contract.version,
                    action_id=action.action_id,
                    target_system_id=action.target_system_id,
                    operation_id=action.operation_id,
                    route_id=action.route_id,
                    branch_id=action.branch_id,
                    ownership_key=action.ownership_key,
                    specialist_ids=action.specialist_ids,
                    failure_signature=failure_signature(error),
                    error=error,
                    expected={"request_schema": operation.request_schema, "permissions": operation.required_permissions},
                    observed=(evidence or {}).get("observed", {}),
                    evidence=evidence or {},
                )
            )
        return observations

    @staticmethod
    def _classify(error: str, evidence: dict[str, Any]) -> DriftKind:
        explicit = evidence.get("kind")
        if explicit in {"schema", "endpoint", "permission", "authentication", "behavior", "semantic", "volume", "latency"}:
            return explicit
        lowered = error.lower()
        if any(word in lowered for word in ("schema", "required", "field", "property", "type")):
            return "schema"
        if any(word in lowered for word in ("permission", "scope", "forbidden", "403")):
            return "permission"
        if any(word in lowered for word in ("authentication", "unauthorized", "401", "credential")):
            return "authentication"
        if any(word in lowered for word in ("endpoint", "404", "method not allowed")):
            return "endpoint"
        if any(word in lowered for word in ("timeout", "latency")):
            return "latency"
        return "behavior"


class RepairLedger:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS drifts (
                    drift_id TEXT PRIMARY KEY, drift_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS repairs (
                    repair_id TEXT PRIMARY KEY, repair_json TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS quarantines (
                    quarantine_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, drift_id TEXT NOT NULL,
                    ownership_key TEXT, event_json TEXT NOT NULL, status TEXT NOT NULL,
                    result_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, recovered_at TEXT
                );
                CREATE TABLE IF NOT EXISTS deployments (
                    deployment_id TEXT PRIMARY KEY, deployment_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self.connection.commit()

    def record_drift(self, observation: DriftObservation) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO drifts(drift_id, drift_json) VALUES (?, ?)",
                (observation.drift_id, observation.model_dump_json()),
            )
            self.connection.commit()

    def save_repair(self, candidate: RepairCandidate) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO repairs(repair_id, repair_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (candidate.repair_id, candidate.model_dump_json()),
            )
            self.connection.commit()

    def quarantine(self, event: CanonicalEvent, drift: DriftObservation, result: SimulationResult) -> str:
        quarantine_id = f"quarantine_{uuid4().hex}"
        with self._lock:
            self.connection.execute(
                "INSERT INTO quarantines(quarantine_id, event_id, drift_id, ownership_key, event_json, status, result_json) "
                "VALUES (?, ?, ?, ?, ?, 'quarantined', ?)",
                (
                    quarantine_id,
                    event.event_id,
                    drift.drift_id,
                    drift.ownership_key,
                    event.model_dump_json(),
                    result.model_dump_json(),
                ),
            )
            self.connection.commit()
        return quarantine_id

    def pending_quarantines(self, ownership_key: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM quarantines WHERE status = 'quarantined'"
        params: tuple[Any, ...] = ()
        if ownership_key is not None:
            query += " AND ownership_key = ?"
            params = (ownership_key,)
        with self._lock:
            return [dict(row) for row in self.connection.execute(query, params).fetchall()]

    def mark_recovered(self, quarantine_id: str, result: SimulationResult) -> None:
        with self._lock:
            self.connection.execute(
                "UPDATE quarantines SET status='recovered', result_json=?, recovered_at=CURRENT_TIMESTAMP WHERE quarantine_id=?",
                (result.model_dump_json(), quarantine_id),
            )
            self.connection.commit()

    def record_deployment(self, deployment: RepairDeployment) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO deployments(deployment_id, deployment_json) VALUES (?, ?)",
                (deployment.deployment_id, deployment.model_dump_json()),
            )
            self.connection.commit()


class RepairStore:
    format_name = "difoundry-repair-candidate-v1"

    @classmethod
    def save(cls, path: str | Path, candidate: RepairCandidate) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = candidate.model_dump(mode="json")
        envelope = {"format": cls.format_name, "payload_hash": canonical_hash(payload), "candidate": payload}
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, target)
        return target

    @classmethod
    def load(cls, path: str | Path) -> RepairCandidate:
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        if envelope.get("format") != cls.format_name:
            raise ValueError("unsupported repair candidate format")
        if envelope.get("payload_hash") != canonical_hash(envelope.get("candidate")):
            raise ValueError("repair candidate envelope hash mismatch")
        return RepairCandidate.model_validate(envelope["candidate"])


class ArtifactPatchApplier:
    def apply(
        self,
        candidate: RepairCandidate,
        contract: IntegrationContract,
        profiles: dict[str, SystemProfile],
    ) -> tuple[IntegrationContract, dict[str, SystemProfile]]:
        contract_data = contract.model_dump(mode="json")
        profile_data = {key: value.model_dump(mode="json") for key, value in profiles.items()}
        for patch in candidate.patches:
            if patch.artifact == "contract":
                self._apply_pointer(contract_data, patch)
            elif patch.artifact.startswith("profile:"):
                system_id = patch.artifact.split(":", 1)[1]
                if system_id not in profile_data:
                    raise ValueError(f"unknown profile artifact {system_id!r}")
                self._apply_pointer(profile_data[system_id], patch)
            else:
                raise ValueError(f"unsupported repair artifact {patch.artifact!r}")
        contract_data["version"] = candidate.proposed_contract_version
        repaired_contract = IntegrationContract.model_validate(contract_data)
        repaired_profiles = {key: SystemProfile.model_validate(value) for key, value in profile_data.items()}
        report = ContractValidator().validate(repaired_contract, repaired_profiles)
        if not report.valid:
            raise ValueError("repaired artifacts fail contract validation: " + "; ".join(report.errors))
        return repaired_contract, repaired_profiles

    @classmethod
    def _apply_pointer(cls, document: Any, patch: ArtifactPatch) -> None:
        parts = [cls._unescape(part) for part in patch.path.split("/")[1:]] if patch.path else []
        if not parts:
            raise ValueError("root replacement is not allowed")
        parent = document
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        leaf = parts[-1]
        if patch.operation == "test":
            current = parent[int(leaf)] if isinstance(parent, list) else parent.get(leaf)
            if current != patch.value:
                raise ValueError(f"patch test failed at {patch.path}")
            return
        if isinstance(parent, list):
            if patch.operation == "add":
                if leaf == "-":
                    parent.append(copy.deepcopy(patch.value))
                else:
                    parent.insert(int(leaf), copy.deepcopy(patch.value))
            elif patch.operation == "replace":
                parent[int(leaf)] = copy.deepcopy(patch.value)
            elif patch.operation == "remove":
                parent.pop(int(leaf))
            return
        if patch.operation in {"add", "replace"}:
            parent[leaf] = copy.deepcopy(patch.value)
        elif patch.operation == "remove":
            parent.pop(leaf)

    @staticmethod
    def _unescape(value: str) -> str:
        return value.replace("~1", "/").replace("~0", "~")


class RepairPolicyEngine:
    def __init__(self, policy: RepairPolicy | None = None) -> None:
        self.policy = policy or RepairPolicy()

    def assess(self, patches: list[ArtifactPatch], drift_kind: DriftKind) -> RiskLevel:
        serialized = json.dumps([patch.model_dump(mode="json") for patch in patches]).lower()
        if any(fragment in serialized for fragment in self.policy.forbidden_fragments):
            return "critical"
        if any(patch.operation == "remove" for patch in patches):
            return "high"
        if any(str(patch.value).lower() in {"delete", "drop", "truncate"} for patch in patches):
            return "critical"
        if drift_kind in {"permission", "authentication"} or "required_permissions" in serialized or "/permissions" in serialized:
            return "high"
        if drift_kind == "endpoint" or any(
            patch.path == "/base_url" or patch.path.endswith("/path") for patch in patches
        ):
            return "high"
        if "/branches/" in serialized or "transforms" in serialized:
            return "medium"
        return "low"

    def validate_scope(self, candidate: RepairCandidate) -> None:
        for patch in candidate.patches:
            if patch.artifact == "contract":
                if not any(patch.path.startswith(prefix) for prefix in self.policy.allow_contract_paths):
                    raise ValueError(f"contract patch path is outside repair policy: {patch.path}")
            elif patch.artifact.startswith("profile:"):
                if not any(patch.path.startswith(prefix) for prefix in self.policy.allow_profile_paths):
                    raise ValueError(f"profile patch path is outside repair policy: {patch.path}")
            else:
                raise ValueError(f"artifact is outside repair policy: {patch.artifact}")

    def approval_required(self, candidate: RepairCandidate) -> bool:
        return candidate.risk not in self.policy.automatic_risk_levels


class RepairGenerator:
    """Deterministic repair generator for structured drift evidence.

    Model-assisted generators can emit the same explicit ArtifactPatch format,
    but they cannot bypass policy, replay, approval, or signature gates.
    """

    def __init__(self, policy_engine: RepairPolicyEngine | None = None) -> None:
        self.policy_engine = policy_engine or RepairPolicyEngine()

    def propose(
        self,
        drift: DriftObservation,
        contract: IntegrationContract,
        profiles: dict[str, SystemProfile],
    ) -> RepairCandidate:
        if drift.action_id is None or drift.target_system_id is None or drift.operation_id is None:
            raise ValueError("repair generation requires an attributed target action")
        action_location = self._action_location(contract, drift.action_id)
        patches: list[ArtifactPatch] = []
        summary = ""
        evidence = drift.evidence
        repair_type = evidence.get("repair_type")
        profile = profiles[drift.target_system_id]
        operation_index = next(i for i, item in enumerate(profile.operations) if item.operation_id == drift.operation_id)

        if repair_type == "field_renamed":
            old_field = str(evidence["old_field"])
            new_field = str(evidence["new_field"])
            mapping_index = self._mapping_index(contract, action_location, old_field)
            patches.append(
                ArtifactPatch(
                    artifact="contract",
                    operation="replace",
                    path=f"{action_location}/mappings/{mapping_index}/target",
                    value=new_field,
                    reason=f"Target field changed from {old_field} to {new_field}",
                )
            )
            if "observed_request_schema" in evidence:
                patches.append(
                    ArtifactPatch(
                        artifact=f"profile:{drift.target_system_id}",
                        operation="replace",
                        path=f"/operations/{operation_index}/request_schema",
                        value=evidence["observed_request_schema"],
                        reason="Bind the local System Profile to the observed request contract",
                    )
                )
            summary = f"Rename mapped target field {old_field!r} to {new_field!r} for {drift.action_id}"
        elif repair_type == "required_field_added":
            target_field = str(evidence["target_field"])
            rule = MappingRule(
                source=str(evidence.get("source_path", target_field)),
                target=target_field,
                required=True,
                default=evidence.get("default"),
                transforms=list(evidence.get("transforms", [])),
            )
            patches.append(
                ArtifactPatch(
                    artifact="contract",
                    operation="add",
                    path=f"{action_location}/mappings/-",
                    value=rule.model_dump(mode="json"),
                    reason=f"Supply newly required field {target_field}",
                )
            )
            if "observed_request_schema" in evidence:
                patches.append(
                    ArtifactPatch(
                        artifact=f"profile:{drift.target_system_id}",
                        operation="replace",
                        path=f"/operations/{operation_index}/request_schema",
                        value=evidence["observed_request_schema"],
                        reason="Update the local request schema",
                    )
                )
            summary = f"Add mapping for newly required field {target_field!r}"
        elif repair_type == "permission_changed":
            required_permissions = list(evidence["required_permissions"])
            patches.extend(
                [
                    ArtifactPatch(
                        artifact=f"profile:{drift.target_system_id}",
                        operation="replace",
                        path=f"/operations/{operation_index}/required_permissions",
                        value=required_permissions,
                        reason="Update observed permission contract",
                    ),
                    ArtifactPatch(
                        artifact="contract",
                        operation="replace" if drift.target_system_id in contract.permissions else "add",
                        path=f"/permissions/{self._escape(drift.target_system_id)}",
                        value=required_permissions,
                        reason="Permission expansion requires explicit approval",
                    ),
                ]
            )
            summary = f"Update permission requirements for {drift.action_id}"
        elif repair_type == "endpoint_changed":
            patches.append(
                ArtifactPatch(
                    artifact=f"profile:{drift.target_system_id}",
                    operation="replace",
                    path=f"/operations/{operation_index}/path",
                    value=str(evidence["new_path"]),
                    reason="Update observed endpoint path",
                )
            )
            summary = f"Update endpoint path for {drift.operation_id}"
        elif repair_type == "explicit_patches":
            patches = [ArtifactPatch.model_validate(item) for item in evidence.get("patches", [])]
            summary = str(evidence.get("summary", "Apply explicit bounded repair"))
        else:
            raise ValueError("unsupported or missing structured repair_type evidence")

        risk = self.policy_engine.assess(patches, drift.kind)
        candidate = RepairCandidate(
            drift_id=drift.drift_id,
            contract_id=contract.contract_id,
            base_contract_version=contract.version,
            proposed_contract_version=self._next_version(contract.version),
            route_id=drift.route_id,
            branch_id=drift.branch_id,
            ownership_key=drift.ownership_key,
            risk=risk,
            summary=summary,
            patches=patches,
            rollback_patches=self._rollback_patches(patches, contract, profiles),
            expected_scope=[item for item in [drift.ownership_key, drift.action_id] if item],
            metadata={"failure_signature": drift.failure_signature, "repair_type": repair_type},
        )
        self.policy_engine.validate_scope(candidate)
        candidate.status = "approval_required" if self.policy_engine.approval_required(candidate) else "proposed"
        candidate.candidate_hash = repair_candidate_hash(candidate)
        return candidate

    @classmethod
    def _rollback_patches(
        cls, patches: list[ArtifactPatch], contract: IntegrationContract, profiles: dict[str, SystemProfile]
    ) -> list[ArtifactPatch]:
        documents: dict[str, Any] = {"contract": contract.model_dump(mode="json")}
        documents.update({f"profile:{key}": value.model_dump(mode="json") for key, value in profiles.items()})
        rollback: list[ArtifactPatch] = []
        for patch in patches:
            document = documents[patch.artifact]
            existed, previous = cls._read_pointer(document, patch.path)
            if patch.operation == "add":
                inverse_path = cls._resolved_add_path(document, patch.path)
                inverse = ArtifactPatch(artifact=patch.artifact, operation="remove", path=inverse_path, reason="Rollback added value")
            elif patch.operation == "remove":
                if not existed:
                    raise ValueError(f"cannot remove missing value at {patch.path}")
                inverse = ArtifactPatch(artifact=patch.artifact, operation="add", path=patch.path, value=previous, reason="Restore removed value")
            elif patch.operation == "replace":
                if not existed:
                    raise ValueError(f"cannot replace missing value at {patch.path}")
                inverse = ArtifactPatch(artifact=patch.artifact, operation="replace", path=patch.path, value=previous, reason="Restore previous value")
            else:
                continue
            rollback.insert(0, inverse)
            ArtifactPatchApplier._apply_pointer(document, patch)
        return rollback

    @staticmethod
    def _resolved_add_path(document: Any, path: str) -> str:
        if not path.endswith("/-"):
            return path
        parent_path = path[:-2]
        existed, parent = RepairGenerator._read_pointer(document, parent_path)
        if not existed or not isinstance(parent, list):
            raise ValueError(f"append path does not target a list: {path}")
        return f"{parent_path}/{len(parent)}"

    @staticmethod
    def _read_pointer(document: Any, path: str) -> tuple[bool, Any]:
        parts = [ArtifactPatchApplier._unescape(part) for part in path.split("/")[1:]] if path else []
        current = document
        try:
            for part in parts:
                if isinstance(current, list):
                    if part == "-":
                        return False, None
                    current = current[int(part)]
                else:
                    current = current[part]
            return True, copy.deepcopy(current)
        except (KeyError, IndexError, ValueError, TypeError):
            return False, None

    @staticmethod
    def _action_location(contract: IntegrationContract, action_id: str) -> str:
        for route_index, route in enumerate(contract.routes):
            for action_index, action in enumerate(route.actions):
                if action.action_id == action_id:
                    return f"/routes/{route_index}/actions/{action_index}"
        raise KeyError(action_id)

    @staticmethod
    def _mapping_index(contract: IntegrationContract, action_location: str, target: str) -> int:
        route_index = int(action_location.split("/")[2])
        action_index = int(action_location.split("/")[4])
        action = contract.routes[route_index].actions[action_index]
        for index, mapping in enumerate(action.mappings):
            if mapping.target == target:
                return index
        raise ValueError(f"no mapping targets {target!r}")

    @staticmethod
    def _next_version(version: str) -> str:
        parts = version.split(".")
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return version + ".repair1"
        if len(numbers) == 1:
            return str(numbers[0] + 1)
        numbers[-1] += 1
        return ".".join(str(number) for number in numbers)

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("~", "~0").replace("/", "~1")


class RepairVerifier:
    def __init__(self, applier: ArtifactPatchApplier | None = None) -> None:
        self.applier = applier or ArtifactPatchApplier()

    def verify(
        self,
        candidate: RepairCandidate,
        contract: IntegrationContract,
        profiles: dict[str, SystemProfile],
        tissue: DendritronRoutingTissue,
        events: list[CanonicalEvent],
        impacted_event_ids: set[str] | None = None,
        adapter_factory: Callable[[str], Any] | None = None,
    ) -> tuple[RepairCandidate, IntegrationContract, dict[str, SystemProfile]]:
        repaired_contract, repaired_profiles = self.applier.apply(candidate, contract, profiles)
        tissue.validate_contract(contract)
        impacted_event_ids = impacted_event_ids or set()
        cases: list[RepairVerificationCase] = []
        before_branch_hashes = {
            (branch.route_id, branch.branch_id): tissue.branch_hash(branch.route_id, branch.branch_id)
            for branch in tissue.state.branches
        }
        owner_key = (candidate.route_id, candidate.branch_id)
        impacted_count = 0
        unrelated_count = 0
        impacted_ok = True
        unrelated_ok = True
        for event in events:
            is_impacted = event.event_id in impacted_event_ids
            original_plan = self._plan(contract, profiles, tissue, event)
            repaired_plan = self._plan(repaired_contract, repaired_profiles, tissue, event, allow_contract_rebind=True)
            if is_impacted:
                impacted_count += 1
                if not repaired_plan.actions or any(not action.certified for action in repaired_plan.actions):
                    impacted_ok = False
                if adapter_factory:
                    adapters = {system_id: adapter_factory(system_id) for system_id in repaired_profiles}
                    runtime_tissue = self._rebound_tissue(tissue, repaired_contract)
                    execution = IntegrationSimulator(
                        repaired_profiles, adapters, EventLedger(), router=runtime_tissue
                    ).process(repaired_contract, self._fresh_event(event, "verify"), simulate=False)
                    if execution.status != "succeeded":
                        impacted_ok = False
            else:
                unrelated_count += 1
                if stable_plan_fingerprint(original_plan) != stable_plan_fingerprint(repaired_plan):
                    unrelated_ok = False
        cases.append(
            RepairVerificationCase(
                name="impacted_historical_replay",
                passed=impacted_ok and impacted_count > 0,
                details=f"{impacted_count} impacted events replayed",
            )
        )
        cases.append(
            RepairVerificationCase(
                name="unrelated_path_regression",
                passed=unrelated_ok,
                details=f"{unrelated_count} unrelated events preserved their plan fingerprints",
            )
        )
        after_branch_hashes = {
            (branch.route_id, branch.branch_id): tissue.branch_hash(branch.route_id, branch.branch_id)
            for branch in tissue.state.branches
        }
        branch_hash_ok = all(
            before_hash == after_branch_hashes[key]
            for key, before_hash in before_branch_hashes.items()
            if key != owner_key
        )
        cases.append(
            RepairVerificationCase(
                name="unrelated_tissue_isolation",
                passed=branch_hash_ok,
                details="No unrelated branch state changed during repair verification",
            )
        )
        contract_report = ContractValidator().validate(repaired_contract, repaired_profiles)
        cases.append(
            RepairVerificationCase(
                name="repaired_contract_validation",
                passed=contract_report.valid,
                details="; ".join(contract_report.errors) or "Repaired contract is structurally valid",
            )
        )
        passed = all(case.passed for case in cases)
        report = RepairVerificationReport(
            repair_id=candidate.repair_id,
            passed=passed,
            impacted_events=impacted_count,
            unrelated_events=unrelated_count,
            cases=cases,
            before_owner_hash=before_branch_hashes.get(owner_key),
            after_owner_hash=after_branch_hashes.get(owner_key),
            unrelated_branch_hashes_unchanged=branch_hash_ok,
        )
        candidate.verification = report
        candidate.status = "verified" if passed else "verification_failed"
        candidate.candidate_hash = repair_candidate_hash(candidate)
        return candidate, repaired_contract, repaired_profiles

    @staticmethod
    def _plan(
        contract: IntegrationContract,
        profiles: dict[str, SystemProfile],
        tissue: DendritronRoutingTissue,
        event: CanonicalEvent,
        allow_contract_rebind: bool = False,
    ) -> Any:
        active_tissue = RepairVerifier._rebound_tissue(tissue, contract) if allow_contract_rebind else tissue
        simulator = IntegrationSimulator(
            profiles,
            {system_id: MemoryAdapter(system_id) for system_id in profiles},
            EventLedger(),
            router=active_tissue,
        )
        result = simulator.process(contract, RepairVerifier._fresh_event(event, "plan"), simulate=True)
        if result.plan is None:
            raise ValueError(f"event {event.event_id} could not be planned: {result.message}")
        return result.plan

    @staticmethod
    def _rebound_tissue(tissue: DendritronRoutingTissue, contract: IntegrationContract) -> DendritronRoutingTissue:
        state = tissue.state.model_copy(deep=True, update={"contract_version": contract.version})
        return DendritronRoutingTissue(state)

    @staticmethod
    def _fresh_event(event: CanonicalEvent, suffix: str) -> CanonicalEvent:
        return event.model_copy(
            update={
                "event_id": f"{event.event_id}:{suffix}:{uuid4().hex[:8]}",
                "idempotency_key": f"{event.idempotency_key}:{suffix}:{uuid4().hex[:8]}",
            }
        )


class RepairSigner:
    @staticmethod
    def sign(candidate: RepairCandidate, key: bytes, key_id: str = "local") -> RepairCandidate:
        if candidate.approval.status != "approved":
            raise ValueError("repair must be approved before signing")
        if candidate.verification is None or not candidate.verification.passed:
            raise ValueError("repair must pass verification before signing")
        candidate.candidate_hash = repair_candidate_hash(candidate)
        signed_payload_hash = repair_signing_hash(candidate)
        digest = hmac.new(key, signed_payload_hash.encode("utf-8"), hashlib.sha256).hexdigest()
        candidate.signature = RepairSignature(key_id=key_id, signed_payload_hash=signed_payload_hash, digest=digest)
        candidate.status = "signed"
        return candidate

    @staticmethod
    def verify(candidate: RepairCandidate, key: bytes) -> bool:
        if candidate.signature is None:
            return False
        actual_hash = repair_candidate_hash(candidate)
        if actual_hash != candidate.candidate_hash:
            return False
        signed_payload_hash = repair_signing_hash(candidate)
        if signed_payload_hash != candidate.signature.signed_payload_hash:
            return False
        expected = hmac.new(key, signed_payload_hash.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, candidate.signature.digest)


class RepairApprovalService:
    @staticmethod
    def approve(candidate: RepairCandidate, approver: str, reason: str = "") -> RepairCandidate:
        if candidate.verification is None or not candidate.verification.passed:
            raise ValueError("repair cannot be approved before successful verification")
        candidate.approval = RepairApproval(
            status="approved", approver=approver, reason=reason, approved_at=datetime.now(timezone.utc)
        )
        candidate.status = "approved"
        candidate.signature = None
        candidate.candidate_hash = repair_candidate_hash(candidate)
        return candidate

    @staticmethod
    def reject(candidate: RepairCandidate, approver: str, reason: str) -> RepairCandidate:
        candidate.approval = RepairApproval(status="rejected", approver=approver, reason=reason)
        candidate.status = "rejected"
        candidate.signature = None
        candidate.candidate_hash = repair_candidate_hash(candidate)
        return candidate


class RepairDeploymentManager:
    def __init__(self, applier: ArtifactPatchApplier | None = None) -> None:
        self.applier = applier or ArtifactPatchApplier()

    def deploy(
        self,
        candidate: RepairCandidate,
        contract: IntegrationContract,
        profiles: dict[str, SystemProfile],
        tissue: DendritronRoutingTissue,
        signing_key: bytes,
        output_dir: str | Path | None = None,
    ) -> tuple[RepairDeployment, IntegrationContract, dict[str, SystemProfile], DendritronRoutingTissue]:
        if not RepairSigner.verify(candidate, signing_key):
            raise ValueError("repair signature verification failed")
        if candidate.status != "signed":
            raise ValueError("repair is not in signed state")
        if candidate.verification is None or not candidate.verification.passed:
            raise ValueError("repair verification gate is not satisfied")
        repaired_contract, repaired_profiles = self.applier.apply(candidate, contract, profiles)
        repaired_state = tissue.state.model_copy(deep=True)
        repaired_state.contract_version = repaired_contract.version
        repaired_state.version += 1
        repaired_state.updated_at = datetime.now(timezone.utc)
        repaired_state.metadata = {
            **repaired_state.metadata,
            "last_repair_id": candidate.repair_id,
            "last_repair_hash": candidate.candidate_hash,
        }
        if candidate.route_id and candidate.branch_id:
            owner = repaired_state.branch(candidate.route_id, candidate.branch_id)
            owner.disabled = False
            owner.local_version += 1
            owner.updated_at = datetime.now(timezone.utc)
        repaired_tissue = DendritronRoutingTissue(repaired_state)
        repaired_tissue.validate_contract(repaired_contract)
        deployment = RepairDeployment(
            repair_id=candidate.repair_id,
            contract_id=contract.contract_id,
            previous_version=contract.version,
            deployed_version=repaired_contract.version,
            contract_hash=canonical_hash(repaired_contract),
            profile_hashes={key: canonical_hash(value) for key, value in repaired_profiles.items()},
            tissue_hash=canonical_hash(repaired_tissue.state),
        )
        if output_dir is not None:
            final = Path(output_dir) / deployment.deployment_id
            stage = final.with_name(final.name + ".tmp")
            stage.mkdir(parents=True, exist_ok=False)
            (stage / "profiles").mkdir()
            (stage / "integration-contract.yaml").write_text(
                yaml.safe_dump(repaired_contract.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
            )
            for system_id, profile in repaired_profiles.items():
                (stage / "profiles" / f"{system_id}.yaml").write_text(
                    yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
                )
            TissueStore.save(stage / "dendritron-tissue.json", repaired_tissue.state)
            (stage / "repair-candidate.json").write_text(candidate.model_dump_json(indent=2), encoding="utf-8")
            (stage / "deployment.json").write_text(deployment.model_dump_json(indent=2), encoding="utf-8")
            manifest = {
                str(path.relative_to(stage)): canonical_hash(path.read_text(encoding="utf-8"))
                for path in stage.rglob("*")
                if path.is_file()
            }
            (stage / "deployment-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, final)
            deployment.artifact_dir = str(final)
        candidate.status = "deployed"
        return deployment, repaired_contract, repaired_profiles, repaired_tissue


class Phase3Runtime:
    def __init__(
        self,
        simulator: IntegrationSimulator,
        repair_ledger: RepairLedger | None = None,
        detector: DriftDetector | None = None,
        isolate_failed_branch: bool = True,
    ) -> None:
        self.simulator = simulator
        self.repair_ledger = repair_ledger or RepairLedger()
        self.detector = detector or DriftDetector()
        self.isolate_failed_branch = isolate_failed_branch

    def process(
        self,
        contract: IntegrationContract,
        event: CanonicalEvent,
        simulate: bool = True,
        evidence: dict[str, Any] | None = None,
    ) -> Phase3RuntimeResult:
        result = self.simulator.process(contract, event, simulate=simulate)
        drifts = self.detector.detect(result, event, contract, self.simulator.profiles, evidence=evidence)
        for drift in drifts:
            self.repair_ledger.record_drift(drift)
            if not simulate:
                self.repair_ledger.quarantine(event, drift, result)
                if self.isolate_failed_branch and drift.route_id and drift.branch_id:
                    setter = getattr(self.simulator.router, "set_branch_enabled", None)
                    if callable(setter):
                        setter(drift.route_id, drift.branch_id, enabled=False)
        return Phase3RuntimeResult(result=result, drifts=drifts, quarantined=bool(drifts) and not simulate)

    def recover(
        self,
        contract: IntegrationContract,
        ownership_key: str | None = None,
    ) -> list[SimulationResult]:
        recovered: list[SimulationResult] = []
        for row in self.repair_ledger.pending_quarantines(ownership_key):
            event = CanonicalEvent.model_validate_json(row["event_json"])
            replay = event.model_copy(
                update={
                    "event_id": f"{event.event_id}:recovery:{uuid4().hex[:8]}",
                    "idempotency_key": f"{event.idempotency_key}:recovery:{uuid4().hex[:8]}",
                    "metadata": {**event.metadata, "recovery_of": event.event_id},
                }
            )
            result = self.simulator.process(contract, replay, simulate=False)
            if result.status == "succeeded":
                self.repair_ledger.mark_recovered(row["quarantine_id"], result)
            recovered.append(result)
        return recovered


def stable_plan_fingerprint(plan: Any) -> str:
    content = {
        "traces": [
            {
                "route_id": trace.route_id,
                "selected_branch_id": trace.selected_branch_id,
                "abstained": trace.abstained,
                "ownership_key": trace.ownership_key,
            }
            for trace in plan.route_traces
        ],
        "actions": [
            {
                "action_id": action.action_id,
                "target_system_id": action.target_system_id,
                "operation_id": action.operation_id,
                "payload": action.payload,
                "path_parameters": action.path_parameters,
                "query_parameters": action.query_parameters,
                "route_id": action.route_id,
                "branch_id": action.branch_id,
                "ownership_key": action.ownership_key,
                "certifications": [
                    {"kind": cert.kind, "passed": cert.passed, "required": cert.required}
                    for cert in action.certifications
                ],
            }
            for action in plan.actions
        ],
    }
    return canonical_hash(content)
