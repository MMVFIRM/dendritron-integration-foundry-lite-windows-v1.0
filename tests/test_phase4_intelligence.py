import json
from pathlib import Path

import pytest

from difoundry.artifacts import DaughterBundleWriter, verify_artifact_manifest
from difoundry.composition import DaughterComposer
from difoundry.intelligence import (
    InheritancePolicy,
    InheritedRepairAdvisor,
    InheritedSemanticMatcher,
    IntelligenceExporter,
    IntelligencePackSigner,
    IntelligencePattern,
    IntelligenceRegistry,
    IntelligenceStore,
    PatternProvenance,
    PrivacyReport,
    PrivacySanitizer,
)
from difoundry.phase4_benchmark import _compose, _repair_candidate


def _pattern(origin: str) -> IntelligencePattern:
    return IntelligencePattern(
        kind="semantic_mapping",
        payload={
            "source": {"field_terms": ["client", "display", "name"], "object_terms": ["client"], "data_type": "string", "required": True},
            "target": {"field_terms": ["display", "name"], "object_terms": ["account"], "data_type": "string", "required": True},
            "relation": "likely",
            "transforms": [],
        },
        confidence=0.80,
        provenance=[PatternProvenance(origin_hash=origin)],
        privacy_report=PrivacyReport(passed=True),
    )


def test_privacy_policy_rejects_tenant_data():
    with pytest.raises(ValueError, match="privacy policy"):
        PrivacySanitizer().require_safe({"tenant_id": "tenant-123", "email": "person@example.com"})


def test_multi_origin_consensus_and_single_origin_quarantine():
    registry = IntelligenceRegistry(policy=InheritancePolicy(minimum_distinct_origins=2))
    first = registry.add(_pattern("origin-a"))
    assert registry.eligible() == []
    second = registry.add(_pattern("origin-b"))
    assert first.pattern_id == second.pattern_id
    assert second.support_count == 2
    assert registry.eligible()[0].pattern_id == first.pattern_id



def test_organization_scope_does_not_enter_shared_pool():
    pattern = _pattern("organization-origin")
    pattern.provenance[0].consent_scope = "organization"
    pattern.origin_hashes = ["organization-origin"]
    registry = IntelligenceRegistry(policy=InheritancePolicy(minimum_distinct_origins=1))
    registry.add(pattern)
    assert registry.eligible() == []
    assert registry.quarantined()[0].pattern_id == pattern.pattern_id


def test_inherited_matcher_reduces_required_review():
    policy = InheritancePolicy(
        minimum_distinct_origins=2,
        minimum_confidence=0.60,
        minimum_pattern_fit=0.68,
        maximum_mapping_boost=0.40,
    )
    registry = IntelligenceRegistry(policy=policy)
    exporter = IntelligenceExporter()
    for index in (1, 2, 3):
        composition, profiles = _compose(f"history_{index}", "client_display_name", DaughterComposer(), 0.65)
        for pattern in exporter.from_composition(composition, profiles, f"origin-{index}"):
            registry.add(pattern)

    baseline, _ = _compose("new_system", "client_caption", DaughterComposer(), 0.70)
    matcher = InheritedSemanticMatcher(registry, policy)
    inherited, _ = _compose("new_system", "client_caption", DaughterComposer(matcher), 0.70)

    assert len([q for q in baseline.questions if q.required]) == 1
    assert len([q for q in inherited.questions if q.required]) == 0
    assert matcher.last_report.inherited_mapping_count == 1
    assert inherited.ready_for_verification


def test_pack_round_trip_signature_and_tamper_detection(tmp_path: Path):
    registry = IntelligenceRegistry(policy=InheritancePolicy(minimum_distinct_origins=1))
    registry.add(_pattern("origin-a"))
    pack = IntelligencePackSigner.sign(registry.pack(include_quarantined=True), b"test-key", key_id="test")
    assert IntelligencePackSigner.verify(pack, b"test-key")
    assert not IntelligencePackSigner.verify(pack, b"wrong-key")
    path = IntelligenceStore.save(tmp_path / "pack.json", pack)
    loaded = IntelligenceStore.load(path)
    assert loaded.pack_hash == pack.pack_hash

    payload = json.loads(path.read_text())
    payload["pack"]["patterns"][0]["payload"]["relation"] = "exact"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="storage hash mismatch"):
        IntelligenceStore.load(path)


def test_repair_strategy_requires_consensus():
    registry = IntelligenceRegistry(policy=InheritancePolicy(minimum_distinct_origins=2, minimum_confidence=0.7))
    exporter = IntelligenceExporter()
    candidate = _repair_candidate()
    registry.add(exporter.from_repair(candidate, "repair-a", "schema"))
    assert InheritedRepairAdvisor(registry).advise("schema") == []
    registry.add(exporter.from_repair(candidate, "repair-b", "schema"))
    advice = InheritedRepairAdvisor(registry).advise("schema")
    assert advice and advice[0]["rollback_available"]


def test_daughter_bundle_contains_inheritance_workspace(tmp_path: Path):
    composition, profiles = _compose("bundle", "client_display_name", DaughterComposer(), 0.65)
    root = DaughterBundleWriter().write(tmp_path / "daughter", composition, profiles)
    assert (root / "inheritance" / "inheritance-report.json").exists()
    assert (root / "inheritance" / "README.md").exists()
    assert verify_artifact_manifest(root)["valid"]
