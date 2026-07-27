from pathlib import Path

from difoundry.adapters.memory import MemoryAdapter
from difoundry.io import load_model
from difoundry.ledger import EventLedger
from difoundry.models import CanonicalEvent, IntegrationContract, SystemProfile
from difoundry.simulator import IntegrationSimulator

ROOT = Path(__file__).parents[1]


def build_simulator():
    source = load_model(ROOT / "examples/source_system.yaml", SystemProfile)
    target = load_model(ROOT / "examples/target_system.yaml", SystemProfile)
    analytics = load_model(ROOT / "examples/analytics_system.yaml", SystemProfile)
    profiles = {source.system_id: source, target.system_id: target, analytics.system_id: analytics}
    adapters = {system_id: MemoryAdapter(system_id) for system_id in profiles}
    return IntegrationSimulator(profiles, adapters, EventLedger()), adapters


def test_generic_contract_simulates():
    simulator, adapters = build_simulator()
    contract = load_model(ROOT / "examples/contract.yaml", IntegrationContract)
    event = load_model(ROOT / "examples/event.json", CanonicalEvent)
    result = simulator.process(contract, event)
    assert result.status == "simulated"
    assert result.plan is not None
    assert result.plan.route_traces[0].selected_branch_id == "business_customer"
    assert result.plan.actions[0].payload["legal_name"] == "Atlas Fabrication LLC"
    assert result.plan.actions[0].payload["contacts"]["primary"]["email"] == "ops@atlas.example"
    assert len(adapters["target_erp"].calls) == 1
    assert len(adapters["analytics_lake"].calls) == 1
    assert len(result.plan.actions) == 2


def test_certification_blocks_missing_permission():
    simulator, _ = build_simulator()
    contract = load_model(ROOT / "examples/contract.yaml", IntegrationContract)
    contract.permissions["target_erp"] = []
    contract.permissions["analytics_lake"] = []
    event = load_model(ROOT / "examples/event.json", CanonicalEvent)
    event = event.model_copy(update={"event_id": "evt_missing_permission", "idempotency_key": "missing-permission"})
    result = simulator.process(contract, event)
    assert result.status == "blocked"
    assert result.executions[0].status == "blocked"
