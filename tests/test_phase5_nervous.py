import json

import pytest

from difoundry.models import CanonicalEvent
from difoundry.nervous import (
    CoordinationStep,
    CoordinationWorkflow,
    GlobalPolicyRule,
    GlobalPolicySet,
    NervousTopologyStore,
)
from difoundry.phase5_benchmark import build_phase5_fixture


def _event(event_id="evt_test"):
    return CanonicalEvent(
        event_id=event_id,
        source_system="external",
        source_object="customer",
        event_type="created",
        correlation_id=f"corr_{event_id}",
        idempotency_key=f"idem_{event_id}",
        payload={"name": "Ada", "email": "ada@example.invalid"},
    )


def test_multi_system_lineage_and_failure_isolation(tmp_path):
    nervous, workflow = build_phase5_fixture(tmp_path / "ledger.sqlite")
    result = nervous.coordinate(workflow.workflow_id, _event(), simulate=False)
    statuses = {step.step_id: step.status for step in result.steps}
    assert result.status == "partial"
    assert statuses == {
        "analytics": "succeeded",
        "identity": "succeeded",
        "billing": "succeeded",
        "workspace": "failed",
    }
    history = nervous.ledger.export_lineage("evt_test")
    assert len(history["events"]) == 4
    assert len({step.local_contract_id for step in result.steps}) == 4
    assert all(step.ownership_keys for step in result.steps)


def test_distributed_idempotency_rejects_duplicate_root(tmp_path):
    nervous, workflow = build_phase5_fixture(tmp_path / "ledger.sqlite")
    event = _event("evt_duplicate")
    nervous.coordinate(workflow.workflow_id, event)
    with pytest.raises(ValueError, match="already been coordinated"):
        nervous.coordinate(workflow.workflow_id, event)


def test_policy_priority_denies_sensitive_export(tmp_path):
    nervous, _workflow = build_phase5_fixture(tmp_path / "ledger.sqlite")
    workflow = CoordinationWorkflow(
        workflow_id="sensitive",
        name="Sensitive",
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
    nervous.register_workflow(workflow)
    result = nervous.coordinate(workflow.workflow_id, _event("evt_sensitive"))
    assert result.status == "blocked"
    assert result.steps[0].policy_decision.rule_id == "deny-sensitive-export"


def test_workflow_cycles_are_rejected():
    with pytest.raises(ValueError, match="dependency cycle"):
        CoordinationWorkflow(
            workflow_id="cycle",
            name="Cycle",
            steps=[
                CoordinationStep(
                    step_id="a",
                    daughter_id="a",
                    capability_id="a",
                    source_system="fabric",
                    source_object="x",
                    event_type="x",
                    depends_on=["b"],
                ),
                CoordinationStep(
                    step_id="b",
                    daughter_id="b",
                    capability_id="b",
                    source_system="fabric",
                    source_object="x",
                    event_type="x",
                    depends_on=["a"],
                ),
            ],
        )


def test_topology_store_detects_tampering(tmp_path):
    nervous, _workflow = build_phase5_fixture(tmp_path / "ledger.sqlite")
    path = tmp_path / "topology.json"
    bundle = nervous.topology_bundle()
    NervousTopologyStore.save(path, bundle)
    loaded = NervousTopologyStore.load(path)
    assert loaded.topology_hash == bundle.topology_hash
    envelope = json.loads(path.read_text())
    envelope["bundle"]["policy"]["default_effect"] = "allow"
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="hash mismatch"):
        NervousTopologyStore.load(path)


def test_fanout_limit_is_enforced(tmp_path):
    nervous, _workflow = build_phase5_fixture(tmp_path / "ledger.sqlite")
    nervous.policy_engine.policy = GlobalPolicySet(
        default_effect="allow",
        maximum_fanout=1,
        rules=[GlobalPolicyRule(rule_id="all", effect="allow")],
    )
    workflow = CoordinationWorkflow(
        workflow_id="too_wide",
        name="Too wide",
        steps=[
            CoordinationStep(
                step_id="one",
                daughter_id="identity_daughter",
                capability_id="identity.create",
                source_system="nervous_fabric",
                source_object="customer",
                event_type="coordinate",
            ),
            CoordinationStep(
                step_id="two",
                daughter_id="analytics_daughter",
                capability_id="analytics.publish",
                source_system="nervous_fabric",
                source_object="analytics_event",
                event_type="coordinate",
            ),
        ],
    )
    with pytest.raises(ValueError, match="fan-out"):
        nervous.register_workflow(workflow)


def test_capability_scopes_multi_route_daughter():
    from difoundry.adapters.memory import MemoryAdapter
    from difoundry.models import (
        ActionDefinition,
        IntegrationContract,
        MappingRule,
        OperationProfile,
        RouteBranch,
        RouteDefinition,
        SystemProfile,
        TriggerDefinition,
    )
    from difoundry.nervous import DaughterCapability, DaughterRegistration, DaughterRuntime
    from difoundry.tissue import DendritronRoutingTissue

    profile = SystemProfile(
        system_id="target",
        name="Target",
        operations=[
            OperationProfile(
                operation_id="create_a",
                method="POST",
                path="/a",
                operation_kind="create",
                request_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
            ),
            OperationProfile(
                operation_id="create_b",
                method="POST",
                path="/b",
                operation_kind="create",
                request_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
            ),
        ],
    )
    contract = IntegrationContract(
        contract_id="multi",
        name="Multi",
        trigger=TriggerDefinition(system_id="fabric", object_type="thing", event_type="coordinate"),
        routes=[
            RouteDefinition(
                route_id="route_a",
                branches=[RouteBranch(branch_id="a")],
                actions=[ActionDefinition(action_id="create_a", target_system_id="target", operation_id="create_a", mappings=[MappingRule(source="value", target="value", required=True)])],
            ),
            RouteDefinition(
                route_id="route_b",
                branches=[RouteBranch(branch_id="b")],
                actions=[ActionDefinition(action_id="create_b", target_system_id="target", operation_id="create_b", mappings=[MappingRule(source="value", target="value", required=True)])],
            ),
        ],
    )
    registration = DaughterRegistration(
        daughter_id="multi_daughter",
        name="Multi Daughter",
        contract_id="multi",
        capabilities=[
            DaughterCapability(capability_id="cap_a", route_ids=["route_a"], event_types=["coordinate"], source_objects=["thing"]),
            DaughterCapability(capability_id="cap_b", route_ids=["route_b"], event_types=["coordinate"], source_objects=["thing"]),
        ],
    )
    adapter = MemoryAdapter("target")
    runtime = DaughterRuntime(
        registration,
        contract,
        {"target": profile},
        {"target": adapter},
        DendritronRoutingTissue.from_contract(contract),
    )
    event = CanonicalEvent(
        source_system="fabric",
        source_object="thing",
        event_type="coordinate",
        idempotency_key="scope-a",
        payload={"value": "x"},
    )
    result = runtime.process("cap_a", event, simulate=False)
    assert result.status == "succeeded"
    assert [item.action_id for item in result.plan.actions] == ["create_a"]
    assert [call["operation_id"] for call in adapter.calls] == ["create_a"]


def test_approval_required_rule_needs_exact_approval(tmp_path):
    nervous, _workflow = build_phase5_fixture(tmp_path / "ledger.sqlite")
    nervous.daughters["billing_daughter"].registration.capabilities.append(
        nervous.daughters["billing_daughter"].registration.capabilities[0].model_copy(
            update={"capability_id": "billing.approved"}
        )
    )
    nervous.policy_engine.policy.rules.append(
        GlobalPolicyRule(
            rule_id="approve-billing",
            effect="require_approval",
            capability_id="billing.approved",
            event_type="coordinate",
            priority=200,
        )
    )
    workflow = CoordinationWorkflow(
        workflow_id="approval_flow",
        name="Approval flow",
        steps=[
            CoordinationStep(
                step_id="billing",
                daughter_id="billing_daughter",
                capability_id="billing.approved",
                source_system="nervous_fabric",
                source_object="billing_request",
                event_type="coordinate",
                inputs=[],
            )
        ],
    )
    nervous.register_workflow(workflow)
    event = CanonicalEvent(
        event_id="evt_approval_blocked",
        source_system="external",
        source_object="billing_request",
        event_type="created",
        idempotency_key="approval-blocked",
        payload={"identity_id": "id-1", "email": "a@example.invalid"},
    )
    blocked = nervous.coordinate(workflow.workflow_id, event)
    assert blocked.status == "blocked"
    approved_event = event.model_copy(
        update={"event_id": "evt_approval_allowed", "idempotency_key": "approval-allowed"}
    )
    approved = nervous.coordinate(
        workflow.workflow_id, approved_event, approvals={"approve-billing"}
    )
    assert approved.status == "succeeded"
    assert approved.steps[0].policy_decision.allowed


def test_multi_parent_causation_is_preserved(tmp_path):
    nervous, workflow = build_phase5_fixture(tmp_path / "ledger.sqlite")
    join = CoordinationStep(
        step_id="join",
        daughter_id="analytics_daughter",
        capability_id="analytics.publish",
        source_system="nervous_fabric",
        source_object="analytics_event",
        event_type="coordinate",
        depends_on=["identity", "billing"],
        inputs=[
            # Both values remain deterministic and available after both parents.
            # The event lineage itself must retain both parent event IDs.
        ],
        required=False,
    )
    joined = workflow.model_copy(
        update={"workflow_id": "joined", "steps": [*workflow.steps, join]}
    )
    nervous.register_workflow(joined)
    result = nervous.coordinate(
        joined.workflow_id,
        CanonicalEvent(
            event_id="evt_joined",
            source_system="external",
            source_object="customer",
            event_type="created",
            idempotency_key="joined",
            payload={"name": "Ada", "email": "ada@example.invalid"},
        ),
        simulate=True,
    )
    lineage = nervous.ledger.export_lineage("evt_joined")
    join_event = next(item for item in lineage["events"] if item["step_id"] == "join")
    assert len(join_event["causation_ids"]) == 2
    assert set(join_event["source_daughter_ids"]) == {"identity_daughter", "billing_daughter"}
    assert result.steps[-1].step_id == "workspace" or any(step.step_id == "join" for step in result.steps)
