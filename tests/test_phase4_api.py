from fastapi.testclient import TestClient

from difoundry.legacy_api import app, intelligence_registry
from difoundry.intelligence import IntelligencePattern, PatternProvenance, PrivacyReport


def _pattern(origin: str) -> IntelligencePattern:
    return IntelligencePattern(
        kind="repair_strategy",
        payload={
            "drift_kind": "schema",
            "risk_level": "medium",
            "patch_shapes": [{"artifact": "contract", "operation": "replace", "path": "/routes/*/actions/*", "value_type": "str"}],
            "rollback_available": True,
            "failure_signatures": [],
        },
        confidence=0.9,
        provenance=[PatternProvenance(origin_hash=origin)],
        privacy_report=PrivacyReport(passed=True),
    )


def test_phase4_intelligence_api_consensus():
    intelligence_registry.patterns.clear()
    intelligence_registry._by_hash.clear()
    client = TestClient(app)
    assert client.get("/health").json()["phase"] == 6
    assert client.post("/intelligence/patterns", json=_pattern("origin-a").model_dump(mode="json")).status_code == 200
    assert client.get("/intelligence/patterns", params={"eligible_only": True}).json() == []
    response = client.post("/intelligence/patterns", json=_pattern("origin-b").model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["support_count"] == 2
    eligible = client.get("/intelligence/patterns", params={"eligible_only": True}).json()
    assert len(eligible) == 1
    advice = client.get("/intelligence/repair-advice/schema").json()
    assert advice and advice[0]["support_count"] == 2
