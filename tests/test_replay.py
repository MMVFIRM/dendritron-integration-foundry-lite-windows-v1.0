from pathlib import Path

from difoundry.adapters.memory import MemoryAdapter
from difoundry.io import load_model
from difoundry.ledger import EventLedger
from difoundry.models import CanonicalEvent, IntegrationContract, SystemProfile
from difoundry.simulator import IntegrationSimulator

ROOT = Path(__file__).parents[1]


def test_replay_has_same_plan_hash_for_same_payload():
    source = load_model(ROOT / "examples/source_system.yaml", SystemProfile)
    target = load_model(ROOT / "examples/target_system.yaml", SystemProfile)
    analytics = load_model(ROOT / "examples/analytics_system.yaml", SystemProfile)
    contract = load_model(ROOT / "examples/contract.yaml", IntegrationContract)
    event = load_model(ROOT / "examples/event.json", CanonicalEvent)
    profiles = {source.system_id: source, target.system_id: target, analytics.system_id: analytics}
    ledger = EventLedger()
    simulator = IntegrationSimulator(profiles, {key: MemoryAdapter(key) for key in profiles}, ledger)
    original = simulator.process(contract, event)
    replay = simulator.replay(event.event_id, contract)
    assert original.plan is not None
    assert replay.plan is not None
    # Event identifiers differ, so total hashes differ; planned action behavior remains identical.
    assert [action.payload for action in original.plan.actions] == [action.payload for action in replay.plan.actions]
    assert original.plan.route_traces == replay.plan.route_traces
    assert original.plan.behavior_hash == replay.plan.behavior_hash
