from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters.memory import MemoryAdapter
from .ledger import EventLedger
from .models import (
    ActionDefinition,
    CanonicalEvent,
    ConditionOperator,
    IntegrationContract,
    MappingRule,
    OperationProfile,
    RouteBranch,
    RouteCondition,
    RouteDefinition,
    SystemProfile,
    TriggerDefinition,
)
from .repair import (
    Phase3Runtime,
    RepairApprovalService,
    RepairDeploymentManager,
    RepairGenerator,
    RepairLedger,
    RepairSigner,
    RepairVerifier,
)
from .simulator import IntegrationSimulator
from .tissue import DendritronRoutingTissue, DendritronTissueConfig, RouterTrainingExample, RouterTrainingSet


class SchemaV2Adapter(MemoryAdapter):
    def execute(self, operation, payload, path_parameters, query_parameters, idempotency_key, simulate):
        if self.system_id == "customer_sink" and "display_name" not in payload:
            raise ValueError("schema drift: required field 'display_name' is missing; 'customer_name' is no longer accepted")
        return super().execute(operation, payload, path_parameters, query_parameters, idempotency_key, simulate)


def phase3_contract() -> IntegrationContract:
    return IntegrationContract(
        contract_id="phase3-repair-benchmark",
        version="1.0.0",
        name="Three-path repair isolation benchmark",
        trigger=TriggerDefinition(system_id="source", object_type="record", event_type="upsert"),
        routes=[
            _route("customer", "customer_sink", "write_customer", "write_customer", "customer_name"),
            _route("invoice", "finance_sink", "write_invoice", "write_invoice", "invoice_name"),
            _route("ticket", "support_sink", "write_ticket", "write_ticket", "ticket_name"),
        ],
        permissions={
            "customer_sink": ["records.write"],
            "finance_sink": ["records.write"],
            "support_sink": ["records.write"],
        },
    )


def _route(kind: str, target: str, operation_id: str, action_id: str, target_field: str) -> RouteDefinition:
    return RouteDefinition(
        route_id=f"sync_{kind}",
        branches=[
            RouteBranch(
                branch_id=f"{kind}_owner",
                conditions=[RouteCondition(path="payload.kind", operator=ConditionOperator.EQ, value=kind)],
            )
        ],
        actions=[
            ActionDefinition(
                action_id=action_id,
                target_system_id=target,
                operation_id=operation_id,
                mappings=[MappingRule(source="name", target=target_field, required=True)],
            )
        ],
    )


def phase3_profiles() -> dict[str, SystemProfile]:
    return {
        "source": SystemProfile(system_id="source", name="Generic Source", protocol="custom"),
        "customer_sink": _sink("customer_sink", "write_customer", "customer_name"),
        "finance_sink": _sink("finance_sink", "write_invoice", "invoice_name"),
        "support_sink": _sink("support_sink", "write_ticket", "ticket_name"),
    }


def _sink(system_id: str, operation_id: str, field: str) -> SystemProfile:
    return SystemProfile(
        system_id=system_id,
        name=system_id,
        protocol="custom",
        operations=[
            OperationProfile(
                operation_id=operation_id,
                method="WRITE",
                path="records",
                operation_kind="create",
                request_schema={
                    "type": "object",
                    "properties": {field: {"type": "string"}},
                    "required": [field],
                    "additionalProperties": False,
                },
                required_permissions=["records.write"],
            )
        ],
    )


def make_event(kind: str, suffix: str) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"evt_{kind}_{suffix}",
        source_system="source",
        source_object="record",
        event_type="upsert",
        source_record_id=f"{kind}-{suffix}",
        idempotency_key=f"idem_{kind}_{suffix}",
        payload={"kind": kind, "name": f"{kind.title()} {suffix}"},
    )


def trained_tissue(contract: IntegrationContract) -> DendritronRoutingTissue:
    tissue = DendritronRoutingTissue.from_contract(
        contract,
        DendritronTissueConfig(novelty_threshold=0.25, ownership_margin=0.0, top_k_specialists=1),
    )
    tissue.train(
        contract,
        RouterTrainingSet(
            examples=[
                RouterTrainingExample(event=make_event(kind, "train"), route_id=f"sync_{kind}", branch_id=f"{kind}_owner")
                for kind in ("customer", "invoice", "ticket")
            ]
        ),
    )
    return tissue


def run_phase3_benchmark(output_path: str | Path | None = None) -> dict[str, Any]:
    contract = phase3_contract()
    profiles = phase3_profiles()
    tissue = trained_tissue(contract)
    repair_ledger = RepairLedger()
    adapters = {system_id: SchemaV2Adapter(system_id) for system_id in profiles}
    simulator = IntegrationSimulator(profiles, adapters, EventLedger(), router=tissue)
    runtime = Phase3Runtime(simulator, repair_ledger=repair_ledger, isolate_failed_branch=True)

    customer = make_event("customer", "drift")
    invoice = make_event("invoice", "regression")
    ticket = make_event("ticket", "regression")
    unrelated_before = {
        "invoice": tissue.branch_hash("sync_invoice", "invoice_owner"),
        "ticket": tissue.branch_hash("sync_ticket", "ticket_owner"),
    }
    failed = runtime.process(
        contract,
        customer,
        simulate=False,
        evidence={
            "kind": "schema",
            "repair_type": "field_renamed",
            "old_field": "customer_name",
            "new_field": "display_name",
            "observed_request_schema": {
                "type": "object",
                "properties": {"display_name": {"type": "string"}},
                "required": ["display_name"],
                "additionalProperties": False,
            },
        },
    )
    drift = failed.drifts[0]
    unrelated_after_failure = {
        "invoice": tissue.branch_hash("sync_invoice", "invoice_owner"),
        "ticket": tissue.branch_hash("sync_ticket", "ticket_owner"),
    }
    failure_locality = unrelated_before == unrelated_after_failure
    quarantine_count = len(repair_ledger.pending_quarantines(drift.ownership_key))

    generator = RepairGenerator()
    candidate = generator.propose(drift, contract, profiles)
    tissue.set_branch_enabled("sync_customer", "customer_owner", True)
    verifier = RepairVerifier()
    candidate, repaired_contract, repaired_profiles = verifier.verify(
        candidate,
        contract,
        profiles,
        tissue,
        [customer, invoice, ticket],
        impacted_event_ids={customer.event_id},
        adapter_factory=SchemaV2Adapter,
    )
    candidate = RepairApprovalService.approve(candidate, "policy:low-risk", "All Phase 3 verification gates passed")
    key = b"phase3-benchmark-signing-key"
    candidate = RepairSigner.sign(candidate, key, key_id="benchmark")
    deployer = RepairDeploymentManager()
    output = Path(output_path) if output_path else None
    deployment_dir = output.parent / "phase3-deployments" if output else None
    deployment, repaired_contract, repaired_profiles, repaired_tissue = deployer.deploy(
        candidate, contract, profiles, tissue, key, output_dir=deployment_dir
    )

    repaired_tissue.set_branch_enabled("sync_customer", "customer_owner", True)
    repaired_adapters = {system_id: SchemaV2Adapter(system_id) for system_id in repaired_profiles}
    repaired_runtime = Phase3Runtime(
        IntegrationSimulator(repaired_profiles, repaired_adapters, EventLedger(), router=repaired_tissue),
        repair_ledger=repair_ledger,
        isolate_failed_branch=True,
    )
    recovery = repaired_runtime.recover(repaired_contract, drift.ownership_key)
    recovered = len(recovery) == 1 and recovery[0].status == "succeeded"
    pending_after = len(repair_ledger.pending_quarantines(drift.ownership_key))

    invoice_result = repaired_runtime.process(repaired_contract, make_event("invoice", "postrepair"), simulate=False)
    ticket_result = repaired_runtime.process(repaired_contract, make_event("ticket", "postrepair"), simulate=False)
    unrelated_operational = invoice_result.result.status == "succeeded" and ticket_result.result.status == "succeeded"

    deployment_payload = deployment.model_dump(mode="json")
    if output and deployment.artifact_dir:
        try:
            deployment_payload["artifact_dir"] = str(Path(deployment.artifact_dir).relative_to(output.parent))
        except ValueError:
            pass
    report = {
        "benchmark": "phase3-bounded-self-repair",
        "evaluation_kind": "deterministic injected-drift architecture fixture",
        "claim_boundary": "Functional repair-gate fixture only; not a measured autonomous repair success rate across real vendor systems.",
        "drift_detection": {
            "pass": failed.quarantined and len(failed.drifts) == 1,
            "kind": drift.kind,
            "ownership_key": drift.ownership_key,
            "failure_signature": drift.failure_signature,
        },
        "failure_locality": {"pass": failure_locality},
        "quarantine": {"before_repair": quarantine_count, "after_recovery": pending_after, "pass": quarantine_count == 1 and pending_after == 0},
        "candidate": {
            "repair_id": candidate.repair_id,
            "risk": candidate.risk,
            "patch_count": len(candidate.patches),
            "candidate_hash": candidate.candidate_hash,
            "status": candidate.status,
        },
        "verification": candidate.verification.model_dump(mode="json") if candidate.verification else None,
        "signature": {"valid": RepairSigner.verify(candidate, key), "key_id": candidate.signature.key_id if candidate.signature else None},
        "deployment": deployment_payload,
        "recovery": {"pass": recovered, "results": [item.model_dump(mode="json") for item in recovery]},
        "unrelated_paths_operational": {"pass": unrelated_operational},
    }
    report["gate_pass"] = all(
        [
            report["drift_detection"]["pass"],
            report["failure_locality"]["pass"],
            report["quarantine"]["pass"],
            bool(report["verification"] and report["verification"]["passed"]),
            report["signature"]["valid"],
            report["recovery"]["pass"],
            report["unrelated_paths_operational"]["pass"],
        ]
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report
