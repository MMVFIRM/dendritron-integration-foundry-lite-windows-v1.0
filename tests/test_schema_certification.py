from pathlib import Path

from difoundry.adapters.memory import MemoryAdapter
from difoundry.io import load_model
from difoundry.ledger import EventLedger
from difoundry.models import CanonicalEvent, IntegrationContract, SystemProfile
from difoundry.simulator import IntegrationSimulator

ROOT = Path(__file__).parents[1]


def test_operation_request_schema_is_automatically_certified():
    profiles = {
        profile.system_id: profile
        for profile in [
            load_model(ROOT / "examples/source_system.yaml", SystemProfile),
            load_model(ROOT / "examples/target_system.yaml", SystemProfile),
            load_model(ROOT / "examples/analytics_system.yaml", SystemProfile),
        ]
    }
    contract = load_model(ROOT / "examples/contract.yaml", IntegrationContract)
    event = load_model(ROOT / "examples/event.json", CanonicalEvent)
    event.payload["id"] = 1001  # schemas require a string after mapping
    event = event.model_copy(update={"event_id": "evt_bad_schema", "idempotency_key": "bad-schema"})
    simulator = IntegrationSimulator(profiles, {key: MemoryAdapter(key) for key in profiles}, EventLedger())
    result = simulator.process(contract, event)
    assert result.status == "blocked"
    assert all(execution.status == "blocked" for execution in result.executions)
    schema_results = [
        certification
        for action in result.plan.actions
        for certification in action.certifications
        if certification.kind == "request_schema"
    ]
    assert schema_results
    assert all(not result.passed for result in schema_results)
