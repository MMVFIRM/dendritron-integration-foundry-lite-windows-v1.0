from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .adapters.memory import MemoryAdapter
from .models import (
    ActionDefinition,
    CanonicalEvent,
    CertifierDefinition,
    IntegrationContract,
    MappingRule,
    ObjectFieldProfile,
    ObjectProfile,
    OperationProfile,
    RouteBranch,
    RouteDefinition,
    SystemProfile,
    TriggerDefinition,
)
from .nervous import (
    CoordinationInput,
    CoordinationStep,
    CoordinationWorkflow,
    DaughterCapability,
    DaughterRegistration,
    DaughterRuntime,
    GlobalPolicyRule,
    GlobalPolicySet,
    MultiSystemNervousSystem,
    NervousLedger,
    NervousTopologyStore,
)
from .tissue import DendritronRoutingTissue


class FailingAdapter(MemoryAdapter):
    def execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated target outage")


def _profile(system_id: str, operation_id: str, fields: list[str]) -> SystemProfile:
    schema = {
        "type": "object",
        "properties": {field: {"type": "string"} for field in fields},
        "required": list(fields),
        "additionalProperties": False,
    }
    return SystemProfile(
        system_id=system_id,
        name=system_id.replace("_", " ").title(),
        protocol="custom",
        objects=[
            ObjectProfile(
                object_id="record",
                name="Record",
                fields=[
                    ObjectFieldProfile(name=field, path=field, data_type="string", required=True, nullable=False)
                    for field in fields
                ],
            )
        ],
        operations=[
            OperationProfile(
                operation_id=operation_id,
                method="EXECUTE",
                path=f"capability://{operation_id}",
                object_id="record",
                operation_kind="create",
                request_schema=schema,
                required_permissions=[f"{operation_id}.execute"],
                idempotency_supported=True,
            )
        ],
    )


def _daughter(
    daughter_id: str,
    capability: str,
    source_object: str,
    event_type: str,
    target_profile: SystemProfile,
    operation_id: str,
    mappings: list[tuple[str, str]],
    *,
    adapter: MemoryAdapter | None = None,
) -> DaughterRuntime:
    contract = IntegrationContract(
        contract_id=f"contract_{daughter_id}",
        version="1.0.0",
        name=f"{daughter_id} contract",
        trigger=TriggerDefinition(system_id="nervous_fabric", object_type=source_object, event_type=event_type),
        routes=[
            RouteDefinition(
                route_id=f"route_{capability}",
                branches=[RouteBranch(branch_id=f"branch_{capability}")],
                actions=[
                    ActionDefinition(
                        action_id=operation_id,
                        target_system_id=target_profile.system_id,
                        operation_id=operation_id,
                        mappings=[MappingRule(source=source, target=target, required=True) for source, target in mappings],
                        certifiers=[CertifierDefinition(kind="permission", required=True)],
                    )
                ],
            )
        ],
        permissions={target_profile.system_id: [f"{operation_id}.execute"]},
    )
    tissue = DendritronRoutingTissue.from_contract(contract)
    registration = DaughterRegistration(
        daughter_id=daughter_id,
        name=daughter_id.replace("_", " ").title(),
        contract_id=contract.contract_id,
        capabilities=[
            DaughterCapability(
                capability_id=capability,
                route_ids=[f"route_{capability}"],
                event_types=[event_type],
                source_objects=[source_object],
            )
        ],
    )
    runtime_adapter = adapter or MemoryAdapter(target_profile.system_id)
    return DaughterRuntime(
        registration,
        contract,
        {target_profile.system_id: target_profile},
        {target_profile.system_id: runtime_adapter},
        tissue,
    )


def _state_hash(runtime: DaughterRuntime) -> str:
    payload = runtime.tissue.state.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_phase5_fixture(ledger_path: str | Path = ":memory:") -> tuple[MultiSystemNervousSystem, CoordinationWorkflow]:
    identity_profile = _profile("identity_platform", "create_identity", ["name", "email"])
    billing_profile = _profile("billing_platform", "create_billing_account", ["identity_id", "email"])
    analytics_profile = _profile("analytics_platform", "publish_customer_event", ["name", "email"])
    provisioning_profile = _profile("provisioning_platform", "provision_workspace", ["identity_id", "name"])

    identity = _daughter(
        "identity_daughter", "identity.create", "customer", "coordinate",
        identity_profile, "create_identity", [("name", "name"), ("email", "email")]
    )
    billing = _daughter(
        "billing_daughter", "billing.create", "billing_request", "coordinate",
        billing_profile, "create_billing_account", [("identity_id", "identity_id"), ("email", "email")]
    )
    analytics = _daughter(
        "analytics_daughter", "analytics.publish", "analytics_event", "coordinate",
        analytics_profile, "publish_customer_event", [("name", "name"), ("email", "email")]
    )
    analytics.registration.capabilities.append(
        DaughterCapability(
            capability_id="sensitive.export",
            route_ids=["route_analytics.publish"],
            event_types=["coordinate"],
            source_objects=["analytics_event"],
        )
    )
    provisioning = _daughter(
        "provisioning_daughter", "workspace.provision", "workspace_request", "coordinate",
        provisioning_profile, "provision_workspace", [("identity_id", "identity_id"), ("name", "name")],
        adapter=FailingAdapter(provisioning_profile.system_id),
    )

    policy = GlobalPolicySet(
        policy_id="phase5-benchmark-policy",
        default_effect="deny",
        maximum_hops=8,
        maximum_fanout=8,
        rules=[
            GlobalPolicyRule(
                rule_id="allow-owned-capabilities",
                effect="allow",
                source_daughter_id="*",
                target_daughter_id="*",
                capability_id="*",
                event_type="coordinate",
                priority=10,
            ),
            GlobalPolicyRule(
                rule_id="deny-sensitive-export",
                effect="deny",
                capability_id="sensitive.export",
                priority=100,
                reason="Sensitive export is forbidden by global policy",
            ),
        ],
    )
    nervous = MultiSystemNervousSystem(policy, NervousLedger(ledger_path))
    for daughter in (identity, billing, analytics, provisioning):
        nervous.register_daughter(daughter)

    workflow = CoordinationWorkflow(
        workflow_id="customer_onboarding",
        version="1.0.0",
        name="Cross-system customer onboarding",
        steps=[
            CoordinationStep(
                step_id="identity",
                daughter_id="identity_daughter",
                capability_id="identity.create",
                source_system="nervous_fabric",
                source_object="customer",
                event_type="coordinate",
                inputs=[
                    CoordinationInput(source="root.payload.name", target="name"),
                    CoordinationInput(source="root.payload.email", target="email"),
                ],
            ),
            CoordinationStep(
                step_id="analytics",
                daughter_id="analytics_daughter",
                capability_id="analytics.publish",
                source_system="nervous_fabric",
                source_object="analytics_event",
                event_type="coordinate",
                inputs=[
                    CoordinationInput(source="root.payload.name", target="name"),
                    CoordinationInput(source="root.payload.email", target="email"),
                ],
                required=False,
            ),
            CoordinationStep(
                step_id="billing",
                daughter_id="billing_daughter",
                capability_id="billing.create",
                source_system="nervous_fabric",
                source_object="billing_request",
                event_type="coordinate",
                depends_on=["identity"],
                inputs=[
                    CoordinationInput(
                        source="steps.identity.outputs.create_identity.record_id", target="identity_id"
                    ),
                    CoordinationInput(source="root.payload.email", target="email"),
                ],
            ),
            CoordinationStep(
                step_id="workspace",
                daughter_id="provisioning_daughter",
                capability_id="workspace.provision",
                source_system="nervous_fabric",
                source_object="workspace_request",
                event_type="coordinate",
                depends_on=["identity"],
                inputs=[
                    CoordinationInput(
                        source="steps.identity.outputs.create_identity.record_id", target="identity_id"
                    ),
                    CoordinationInput(source="root.payload.name", target="name"),
                ],
                required=False,
            ),
        ],
    )
    nervous.register_workflow(workflow)
    return nervous, workflow


def run_phase5_benchmark(output_path: str | Path | None = None) -> dict[str, Any]:
    with TemporaryDirectory() as temporary:
        nervous, workflow = build_phase5_fixture(Path(temporary) / "nervous-ledger.sqlite")
        before = {daughter_id: _state_hash(runtime) for daughter_id, runtime in nervous.daughters.items()}
        root_event = CanonicalEvent(
            event_id="evt_phase5_customer",
            source_system="external_crm",
            source_object="customer",
            event_type="created",
            source_record_id="customer-42",
            correlation_id="corr_phase5_customer",
            idempotency_key="phase5-customer-42",
            payload={"name": "Example Customer", "email": "customer@example.invalid"},
        )
        result = nervous.coordinate(workflow.workflow_id, root_event, simulate=False)
        after = {daughter_id: _state_hash(runtime) for daughter_id, runtime in nervous.daughters.items()}
        failure_counts = {
            daughter_id: sum(branch.failures for branch in runtime.tissue.state.branches)
            for daughter_id, runtime in nervous.daughters.items()
        }
        lineage = nervous.ledger.export_lineage(root_event.event_id)

        statuses = {step.step_id: step.status for step in result.steps}
        ownership_complete = all(
            bool(step.ownership_keys)
            for step in result.steps
            if step.status in {"succeeded", "failed", "simulated"}
        )
        failed_owner_changed = before["provisioning_daughter"] != after["provisioning_daughter"]
        unrelated_operational = statuses["analytics"] == "succeeded" and statuses["billing"] == "succeeded"
        distinct_contracts = len({step.local_contract_id for step in result.steps if step.local_contract_id}) == 4
        causal_links = {
            event["step_id"]: event["causation_id"] for event in lineage["events"]
        }
        causal_chain_valid = (
            causal_links["identity"] == root_event.event_id
            and causal_links["analytics"] == root_event.event_id
            and causal_links["billing"] != root_event.event_id
            and causal_links["workspace"] != root_event.event_id
        )

        try:
            nervous.coordinate(workflow.workflow_id, root_event, simulate=False)
        except ValueError:
            duplicate_prevented = True
        else:
            duplicate_prevented = False

        denied_workflow = CoordinationWorkflow(
            workflow_id="forbidden_export",
            name="Forbidden export",
            steps=[
                CoordinationStep(
                    step_id="export",
                    daughter_id="analytics_daughter",
                    capability_id="sensitive.export",
                    source_system="nervous_fabric",
                    source_object="analytics_event",
                    event_type="coordinate",
                )
            ],
        )
        nervous.register_workflow(denied_workflow)
        denied_event = root_event.model_copy(
            update={
                "event_id": "evt_phase5_denied",
                "idempotency_key": "phase5-denied",
                "correlation_id": "corr_phase5_denied",
            }
        )
        denied = nervous.coordinate(denied_workflow.workflow_id, denied_event)

        try:
            CoordinationWorkflow(
                workflow_id="cycle",
                name="Invalid cycle",
                steps=[
                    CoordinationStep(
                        step_id="a", daughter_id="identity_daughter", capability_id="identity.create",
                        source_system="nervous_fabric", source_object="customer", event_type="coordinate", depends_on=["b"]
                    ),
                    CoordinationStep(
                        step_id="b", daughter_id="billing_daughter", capability_id="billing.create",
                        source_system="nervous_fabric", source_object="billing_request", event_type="coordinate", depends_on=["a"]
                    ),
                ],
            )
        except ValueError:
            cycle_rejected = True
        else:
            cycle_rejected = False

        topology_path = Path(temporary) / "nervous-topology.json"
        NervousTopologyStore.save(topology_path, nervous.topology_bundle())
        loaded_topology = NervousTopologyStore.load(topology_path)
        topology_round_trip = loaded_topology.topology_hash == nervous.topology_bundle().topology_hash
        envelope = json.loads(topology_path.read_text(encoding="utf-8"))
        envelope["bundle"]["policy"]["maximum_hops"] = 99
        topology_path.write_text(json.dumps(envelope), encoding="utf-8")
        try:
            NervousTopologyStore.load(topology_path)
        except ValueError:
            topology_tamper_detected = True
        else:
            topology_tamper_detected = False

        checks = {
            "multi_daughter_coordination": len(result.steps) == 4 and distinct_contracts,
            "parallel_fanout": statuses["identity"] == "succeeded" and statuses["analytics"] == "succeeded",
            "dependent_handoff": statuses["billing"] == "succeeded",
            "local_failure_isolated": statuses["workspace"] == "failed" and unrelated_operational,
            "exact_local_ownership": ownership_complete,
            "failed_owner_updated_locally": failed_owner_changed,
            "failure_attribution_is_local": (
                failure_counts["provisioning_daughter"] == 1
                and all(
                    failure_counts[item] == 0
                    for item in ("identity_daughter", "billing_daughter", "analytics_daughter")
                )
            ),
            "unrelated_daughters_not_failed": all(statuses[item] == "succeeded" for item in ("identity", "billing", "analytics")),
            "causal_lineage_complete": causal_chain_valid and len(lineage["events"]) == 4,
            "global_policy_enforced": (
                denied.status == "blocked"
                and denied.steps[0].status == "blocked"
                and denied.steps[0].policy_decision is not None
                and denied.steps[0].policy_decision.rule_id == "deny-sensitive-export"
            ),
            "distributed_idempotency": duplicate_prevented,
            "workflow_cycle_rejected": cycle_rejected,
            "lineage_hash_bound": len(result.lineage_hash) == 64,
            "topology_round_trip": topology_round_trip,
            "topology_tamper_detected": topology_tamper_detected,
        }
        report = {
            "phase": 5,
            "evaluation_kind": "deterministic four-daughter coordination fixture",
            "claim_boundary": "Architectural correctness fixture only; not throughput, scale, availability, or universal coordination evidence.",
            "gate_pass": all(checks.values()),
            "checks": checks,
            "metrics": {
                "registered_daughters": len(nervous.daughters),
                "registered_workflows": len(nervous.workflows),
                "coordinated_steps": len(result.steps),
                "successful_steps": sum(step.status == "succeeded" for step in result.steps),
                "failed_steps": sum(step.status == "failed" for step in result.steps),
                "distinct_local_contracts": len({step.local_contract_id for step in result.steps if step.local_contract_id}),
                "nervous_events_recorded": len(lineage["events"]),
                "global_policy_rules": len(nervous.policy.rules),
            },
            "coordination": result.model_dump(mode="json"),
            "topology": nervous.topology(),
            "boundaries": {
                "daughter_contracts_merged": False,
                "daughter_tissues_shared": False,
                "global_policy_bypassed": False,
                "cross_daughter_gradient": False,
                "central_payload_reinterpretation": False,
            },
        }
        if output_path is not None:
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        return report
