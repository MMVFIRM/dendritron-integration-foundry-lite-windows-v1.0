from pathlib import Path

from difoundry.adapters.memory import MemoryAdapter
from difoundry.io import load_model
from difoundry.ledger import EventLedger
from difoundry.models import CanonicalEvent, IntegrationContract, SystemProfile
from difoundry.simulator import IntegrationSimulator

ROOT = Path(__file__).parents[1]


def test_duplicate_event_is_not_reexecuted():
    source = load_model(ROOT / "examples/source_system.yaml", SystemProfile)
    target = load_model(ROOT / "examples/target_system.yaml", SystemProfile)
    analytics = load_model(ROOT / "examples/analytics_system.yaml", SystemProfile)
    contract = load_model(ROOT / "examples/contract.yaml", IntegrationContract)
    event = load_model(ROOT / "examples/event.json", CanonicalEvent)
    profiles = {source.system_id: source, target.system_id: target, analytics.system_id: analytics}
    adapter = MemoryAdapter(target.system_id)
    simulator = IntegrationSimulator(
        profiles,
        {
            source.system_id: MemoryAdapter(source.system_id),
            target.system_id: adapter,
            analytics.system_id: MemoryAdapter(analytics.system_id),
        },
        EventLedger(),
    )
    first = simulator.process(contract, event)
    duplicate = simulator.process(contract, event.model_copy(update={"event_id": "evt_duplicate"}))
    assert first.status == "simulated"
    assert duplicate.status == "duplicate"
    assert len(adapter.calls) == 1
