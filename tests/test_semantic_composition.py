from pathlib import Path

from difoundry.adapters.memory import MemoryAdapter
from difoundry.composition import DaughterComposer
from difoundry.discovery import DiscoveryService
from difoundry.io import load_data
from difoundry.ledger import EventLedger
from difoundry.models import CanonicalEvent, CompositionRequest, DiscoverySource, TargetIntent
from difoundry.simulator import IntegrationSimulator

ROOT = Path(__file__).parents[1]


def _profiles():
    service = DiscoveryService()
    inputs = [
        ("crm-openapi.yaml", "atlas_crm"),
        ("erp.sql", "atlas_erp"),
        ("analytics-asyncapi.yaml", "analytics_bus"),
    ]
    results = [
        service.discover(
            DiscoverySource(
                format="auto",
                document=load_data(ROOT / "examples/discovery" / filename),
                system_id=system_id,
            )
        )
        for filename, system_id in inputs
    ]
    return {result.profile.system_id: result.profile for result in results}


def test_multi_target_composition_is_system_agnostic_and_ready():
    profiles = _profiles()
    result = DaughterComposer().compose(
        CompositionRequest(
            name="Customer distribution daughter",
            source_system_id="atlas_crm",
            source_object_id="customer",
            event_type="updated",
            targets=[
                TargetIntent(target_system_id="atlas_erp", target_object_id="account", operation_id="insert_account"),
                TargetIntent(
                    target_system_id="analytics_bus",
                    target_object_id="customer_snapshot",
                    operation_id="publish_customer_snapshot",
                ),
            ],
        ),
        profiles,
    )
    assert result.ready_for_verification is True
    assert result.questions == []
    assert len(result.semantic_graphs) == 2
    assert len(result.contract.routes[0].actions) == 2
    mappings = {
        action.target_system_id: {(rule.source, rule.target) for rule in action.mappings}
        for action in result.contract.routes[0].actions
    }
    assert ("id", "external_id") in mappings["atlas_erp"]
    assert ("company_name", "legal_name") in mappings["atlas_erp"]
    assert ("id", "customer_key") in mappings["analytics_bus"]


def test_generated_contract_runs_through_phase0_kernel():
    profiles = _profiles()
    result = DaughterComposer().compose(
        CompositionRequest(
            name="Customer distribution daughter",
            source_system_id="atlas_crm",
            source_object_id="customer",
            event_type="updated",
            targets=[
                TargetIntent(target_system_id="atlas_erp", target_object_id="account", operation_id="insert_account"),
                TargetIntent(target_system_id="analytics_bus", target_object_id="customer_snapshot", operation_id="publish_customer_snapshot"),
            ],
        ),
        profiles,
    )
    event = CanonicalEvent(
        event_id="evt_phase1_e2e",
        source_system="atlas_crm",
        source_object="customer",
        event_type="updated",
        source_record_id="cust_1",
        idempotency_key="atlas_crm:customer:cust_1:v1",
        payload={
            "id": "cust_1",
            "company_name": "Atlas Fabrication LLC",
            "status": "ACTIVE",
            "primary_email": "ops@atlas.example",
            "updated_at": "2026-07-26T18:00:00-07:00",
        },
    )
    adapters = {system_id: MemoryAdapter(system_id) for system_id in profiles}
    simulation = IntegrationSimulator(profiles, adapters, EventLedger()).process(result.contract, event)
    assert simulation.status == "simulated"
    assert len(simulation.plan.actions) == 2
    assert simulation.plan.actions[0].payload["external_id"] == "cust_1"
    assert simulation.plan.actions[1].payload["customer_key"] == "cust_1"


def test_composition_targets_operation_input_schema_for_graphql_mutation():
    from difoundry.models import ObjectFieldProfile, ObjectProfile, SystemProfile

    service = DiscoveryService()
    graphql = service.discover(
        DiscoverySource(
            format="auto",
            document=load_data(ROOT / "examples/discovery/support-graphql.json"),
            system_id="support_graph",
        )
    ).profile
    source = SystemProfile(
        system_id="issue_source",
        name="Issue Source",
        protocol="custom",
        objects=[
            ObjectProfile(
                object_id="issue",
                name="Issue",
                identifiers=["external_id"],
                fields=[
                    ObjectFieldProfile(name="external_id", path="external_id", data_type="string", required=True, nullable=False),
                    ObjectFieldProfile(name="title", path="title", data_type="string", required=True, nullable=False),
                    ObjectFieldProfile(name="status", path="status", data_type="string"),
                ],
            )
        ],
    )
    profiles = {source.system_id: source, graphql.system_id: graphql}
    result = DaughterComposer().compose(
        CompositionRequest(
            name="Issue to support ticket",
            source_system_id="issue_source",
            source_object_id="issue",
            targets=[TargetIntent(target_system_id="support_graph", target_object_id="ticket", operation_id="create_ticket")],
        ),
        profiles,
    )
    action = result.contract.routes[0].actions[0]
    assert {(rule.source, rule.target) for rule in action.mappings} == {
        ("external_id", "external_id"),
        ("title", "title"),
    }
    assert result.ready_for_verification is True
