from pathlib import Path

from fastapi.testclient import TestClient

from difoundry.legacy_api import app, registry, repair_ledger
from difoundry.phase3_benchmark import make_event, phase3_contract, phase3_profiles, trained_tissue
from difoundry.repair import DriftObservation


def reset_registry():
    registry.profiles.clear()
    registry.contracts.clear()
    registry.discoveries.clear()
    registry.compositions.clear()
    registry.tissues.clear()
    registry.repairs.clear()
    registry.deployments.clear()
    repair_ledger.connection.executescript("DELETE FROM drifts; DELETE FROM repairs; DELETE FROM quarantines; DELETE FROM deployments;")
    repair_ledger.connection.commit()


def test_phase3_repair_api_lifecycle(monkeypatch, tmp_path: Path):
    reset_registry()
    contract = phase3_contract()
    profiles = phase3_profiles()
    tissue = trained_tissue(contract)
    registry.register_contract(contract)
    for profile in profiles.values():
        registry.register_profile(profile)
    registry.register_tissue(tissue)
    client = TestClient(app)
    assert client.get("/health").json()["phase"] == 6

    event = make_event("customer", "api")
    drift = DriftObservation(
        kind="schema",
        event_id=event.event_id,
        contract_id=contract.contract_id,
        contract_version=contract.version,
        action_id="write_customer",
        target_system_id="customer_sink",
        operation_id="write_customer",
        route_id="sync_customer",
        branch_id="customer_owner",
        ownership_key=f"{contract.contract_id}:sync_customer:customer_owner",
        failure_signature="failure:api",
        error="customer_name was renamed",
        evidence={
            "kind": "schema",
            "repair_type": "field_renamed",
            "old_field": "customer_name",
            "new_field": "display_name",
            "observed_request_schema": {
                "type": "object",
                "properties": {"display_name": {"type": "string"}},
                "required": ["display_name"],
                "additionalProperties": False,
            },
        },
    )
    proposed = client.post("/repairs/propose", json=drift.model_dump(mode="json"))
    assert proposed.status_code == 200
    candidate = proposed.json()
    repair_id = candidate["repair_id"]

    verification = client.post(
        f"/repairs/{repair_id}/verify",
        json={
            "tissue_id": tissue.state.tissue_id,
            "events": [
                event.model_dump(mode="json"),
                make_event("invoice", "api-control").model_dump(mode="json"),
            ],
            "impacted_event_ids": [event.event_id],
        },
    )
    assert verification.status_code == 200
    assert verification.json()["verification"]["passed"]

    approved = client.post(f"/repairs/{repair_id}/approve", params={"approver": "api-test"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    monkeypatch.setenv("DIFOUNDRY_REPAIR_SIGNING_KEY", "api-signing-key")
    signed = client.post(f"/repairs/{repair_id}/sign", params={"key_id": "api"})
    assert signed.status_code == 200
    assert signed.json()["status"] == "signed"

    deployed = client.post(
        f"/repairs/{repair_id}/deploy",
        params={"tissue_id": tissue.state.tissue_id, "output_dir": str(tmp_path)},
    )
    assert deployed.status_code == 200
    assert deployed.json()["deployed_version"] == "1.0.1"
    assert len(registry.deployments) == 1
