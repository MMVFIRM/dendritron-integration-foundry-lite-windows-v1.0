from fastapi.testclient import TestClient

from difoundry.legacy_api import app, nervous_system
from difoundry.nervous import DaughterRuntimeRequest
from difoundry.phase5_benchmark import build_phase5_fixture
from difoundry.models import CanonicalEvent


def test_phase5_nervous_api(tmp_path):
    fixture, workflow = build_phase5_fixture(tmp_path / "fixture.sqlite")
    nervous_system.daughters.clear()
    nervous_system.workflows.clear()
    nervous_system.policy_engine.policy = fixture.policy
    client = TestClient(app)
    assert client.get("/health").json()["phase"] == 6

    for runtime in fixture.daughters.values():
        request = DaughterRuntimeRequest(
            registration=runtime.registration,
            contract=runtime.contract,
            profiles=list(runtime.profiles.values()),
        )
        response = client.post("/nervous/daughters", json=request.model_dump(mode="json"))
        assert response.status_code == 200, response.text

    response = client.post("/nervous/workflows", json=workflow.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    event = CanonicalEvent(
        event_id="evt_api_phase5",
        source_system="external",
        source_object="customer",
        event_type="created",
        correlation_id="corr_api_phase5",
        idempotency_key="idem_api_phase5",
        payload={"name": "Grace", "email": "grace@example.invalid"},
    )
    response = client.post(
        f"/nervous/coordinate/{workflow.workflow_id}",
        json=event.model_dump(mode="json"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "simulated"
    assert len(body["steps"]) == 4
    topology = client.get("/nervous/topology").json()
    assert len(topology["daughters"]) == 4
    lineage = client.get("/nervous/lineage/evt_api_phase5").json()
    assert len(lineage["events"]) == 4
