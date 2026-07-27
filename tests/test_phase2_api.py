from fastapi.testclient import TestClient

from difoundry.legacy_api import app, registry
from difoundry.phase2_benchmark import benchmark_contract, benchmark_profiles, training_set


def test_api_tissue_lifecycle_and_simulation():
    registry.profiles.clear()
    registry.contracts.clear()
    registry.discoveries.clear()
    registry.compositions.clear()
    registry.tissues.clear()
    client = TestClient(app)

    for profile in benchmark_profiles().values():
        response = client.post("/profiles", json=profile.model_dump(mode="json"))
        assert response.status_code == 200, response.text
    contract = benchmark_contract()
    response = client.post("/contracts", json=contract.model_dump(mode="json"))
    assert response.status_code == 200, response.text

    response = client.post(
        f"/tissues/{contract.contract_id}",
        json={"novelty_threshold": 0.58, "ownership_margin": 0.025, "spawn_below_similarity": 0.78},
    )
    assert response.status_code == 200, response.text
    tissue_id = response.json()["tissue_id"]

    response = client.post(
        f"/tissues/{tissue_id}/train",
        json=training_set().model_dump(mode="json"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["examples"] == 18

    response = client.get(f"/tissues/{tissue_id}/summary")
    assert response.status_code == 200
    assert response.json()["version"] == 18

    event = next(example.event for example in training_set().examples if example.branch_id == "smb_east").model_copy(
        update={"event_id": "evt_api_phase2", "idempotency_key": "idem_api_phase2"}
    )
    response = client.post(
        f"/simulate/{contract.contract_id}?tissue_id={tissue_id}",
        json=event.model_dump(mode="json"),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "simulated"
    assert payload["plan"]["route_traces"][0]["selected_branch_id"] == "smb_east"
    assert payload["plan"]["route_traces"][0]["router_kind"] == "dendritron_tissue"
