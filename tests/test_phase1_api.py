from pathlib import Path

from fastapi.testclient import TestClient

from difoundry.legacy_api import app, registry
from difoundry.io import load_data

ROOT = Path(__file__).parents[1]


def test_api_discovery_and_composition_flow():
    registry.profiles.clear()
    registry.discoveries.clear()
    registry.compositions.clear()
    registry.contracts.clear()
    registry.tissues.clear()
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["phase"] == 6

    for filename, system_id in (("crm-openapi.yaml", "atlas_crm"), ("erp.sql", "atlas_erp")):
        document = load_data(ROOT / "examples/discovery" / filename)
        response = client.post(
            "/discover",
            json={"format": "auto", "document": document, "system_id": system_id, "metadata": {}},
        )
        assert response.status_code == 200, response.text

    response = client.post(
        "/compose",
        json={
            "name": "API daughter",
            "source_system_id": "atlas_crm",
            "source_object_id": "customer",
            "event_type": "updated",
            "targets": [
                {"target_system_id": "atlas_erp", "target_object_id": "account", "operation_id": "insert_account"}
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["daughter_manifest"]["status"] == "scaffolded"
