from pathlib import Path

from difoundry.adapters.memory import MemoryAdapter
from difoundry.io import load_model
from difoundry.ledger import EventLedger
from difoundry.models import CanonicalEvent, IntegrationContract, SystemProfile
from difoundry.simulator import IntegrationSimulator

ROOT = Path(__file__).parents[1]


def test_unknown_event_abstains_instead_of_guessing():
    source = load_model(ROOT / "examples/source_system.yaml", SystemProfile)
    target = load_model(ROOT / "examples/target_system.yaml", SystemProfile)
    analytics = load_model(ROOT / "examples/analytics_system.yaml", SystemProfile)
    contract = load_model(ROOT / "examples/contract.yaml", IntegrationContract)
    event = load_model(ROOT / "examples/event.json", CanonicalEvent)
    event.payload["customer_type"] = "government"
    event = event.model_copy(update={"event_id": "evt_unknown_route", "idempotency_key": "unknown-route"})
    profiles = {source.system_id: source, target.system_id: target, analytics.system_id: analytics}
    simulator = IntegrationSimulator(profiles, {key: MemoryAdapter(key) for key in profiles}, EventLedger())
    result = simulator.process(contract, event)
    assert result.status == "abstained"
    assert result.plan is not None
    assert result.plan.route_traces[0].abstained is True
