from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .composition import DaughterComposer
from .intelligence import (
    InheritancePolicy,
    InheritedRepairAdvisor,
    InheritedSemanticMatcher,
    IntelligenceExporter,
    IntelligencePattern,
    IntelligenceRegistry,
    IntelligenceStore,
    PatternProvenance,
    PrivacyReport,
)
from .models import (
    AuthenticationProfile,
    CompositionRequest,
    ObjectFieldProfile,
    ObjectProfile,
    OperationProfile,
    SystemProfile,
    TargetIntent,
)
from .repair import ArtifactPatch, RepairCandidate


def _profiles(prefix: str, source_field: str) -> dict[str, SystemProfile]:
    source_id = f"{prefix}_source"
    target_id = f"{prefix}_target"
    source = SystemProfile(
        system_id=source_id,
        name=f"{prefix} source",
        protocol="custom-event",
        authentication=AuthenticationProfile(),
        objects=[
            ObjectProfile(
                object_id="client",
                name="Client",
                fields=[
                    ObjectFieldProfile(
                        name=source_field,
                        path=source_field,
                        data_type="string",
                        required=True,
                        nullable=False,
                    )
                ],
            )
        ],
        operations=[],
    )
    target = SystemProfile(
        system_id=target_id,
        name=f"{prefix} target",
        protocol="rest",
        authentication=AuthenticationProfile(),
        objects=[
            ObjectProfile(
                object_id="account",
                name="Account",
                fields=[
                    ObjectFieldProfile(
                        name="display_name",
                        path="display_name",
                        data_type="string",
                        required=True,
                        nullable=False,
                    )
                ],
            )
        ],
        operations=[
            OperationProfile(
                operation_id="create_account",
                method="POST",
                path="/accounts",
                object_id="account",
                operation_kind="create",
                request_schema={
                    "type": "object",
                    "properties": {"display_name": {"type": "string"}},
                    "required": ["display_name"],
                },
                required_permissions=["accounts.write"],
            )
        ],
    )
    return {source_id: source, target_id: target}


def _compose(prefix: str, source_field: str, composer: DaughterComposer, review_below: float) -> tuple[Any, dict[str, SystemProfile]]:
    profiles = _profiles(prefix, source_field)
    result = composer.compose(
        CompositionRequest(
            name=f"{prefix} integration",
            source_system_id=f"{prefix}_source",
            source_object_id="client",
            event_type="created",
            targets=[TargetIntent(target_system_id=f"{prefix}_target", target_object_id="account", operation_id="create_account")],
            minimum_mapping_score=0.30,
            require_review_below=review_below,
        ),
        profiles,
    )
    return result, profiles


def _repair_candidate() -> RepairCandidate:
    return RepairCandidate(
        drift_id="drift_sanitized",
        contract_id="generic_contract",
        base_contract_version="1.0.0",
        proposed_contract_version="1.0.1",
        risk="medium",
        summary="Update a renamed target field",
        patches=[
            ArtifactPatch(
                artifact="contract",
                operation="replace",
                path="/routes/0/actions/0/mappings/0/target",
                value="display_name",
                reason="Observed field rename",
            )
        ],
        rollback_patches=[
            ArtifactPatch(
                artifact="contract",
                operation="replace",
                path="/routes/0/actions/0/mappings/0/target",
                value="name",
                reason="Restore prior mapping",
            )
        ],
    )


def run_phase4_benchmark(output_path: str | Path | None = None) -> dict[str, Any]:
    policy = InheritancePolicy(
        minimum_distinct_origins=2,
        minimum_confidence=0.60,
        minimum_pattern_fit=0.68,
        maximum_mapping_boost=0.40,
    )
    registry = IntelligenceRegistry(policy=policy)
    exporter = IntelligenceExporter()

    # Two independently verified daughters contribute the same structural mapping.
    for index in (1, 2, 3):
        composition, profiles = _compose(
            f"historical_{index}", "client_display_name", DaughterComposer(), review_below=0.65
        )
        patterns = exporter.from_composition(
            composition, profiles, origin_ref=f"tenant-private-origin-{index}"
        )
        for pattern in patterns:
            registry.add(pattern)

    eligible_semantic = registry.eligible("semantic_mapping")

    # A single-origin poisoned pattern remains quarantined.
    poison = IntelligencePattern(
        kind="semantic_mapping",
        payload={
            "source": {"field_terms": ["amount"], "object_terms": ["invoice"], "data_type": "number", "required": True},
            "target": {"field_terms": ["admin"], "object_terms": ["account"], "data_type": "string", "required": True},
            "relation": "likely",
            "transforms": [],
        },
        confidence=0.99,
        provenance=[PatternProvenance(origin_hash="single_untrusted_origin")],
        privacy_report=PrivacyReport(passed=True),
    )
    registry.add(poison)

    baseline, new_profiles = _compose("new_vendor", "client_caption", DaughterComposer(), review_below=0.70)
    inherited_matcher = InheritedSemanticMatcher(registry, policy)
    inherited, _ = _compose(
        "new_vendor", "client_caption", DaughterComposer(matcher=inherited_matcher), review_below=0.70
    )
    inheritance_report = inherited_matcher.last_report

    # Two verified repairs contribute a reusable repair strategy.
    repair_candidate = _repair_candidate()
    for index in (1, 2):
        registry.add(
            exporter.from_repair(
                repair_candidate,
                origin_ref=f"repair-origin-{index}",
                drift_kind="schema",
                failure_signatures=["failure:structural-hash-only"],
            )
        )
    repair_advice = InheritedRepairAdvisor(registry).advise("schema")

    pack = registry.pack(metadata={"benchmark": "phase4"})
    pack_text = json.dumps(pack.model_dump(mode="json"), sort_keys=True)
    forbidden_literals = [
        "tenant-private-origin-1",
        "tenant-private-origin-2",
        "tenant-private-origin-3",
        "historical_1_source",
        "historical_2_target",
        "new_vendor_source",
    ]
    privacy_clean = not any(value in pack_text for value in forbidden_literals)

    with TemporaryDirectory() as temp:
        path = Path(temp) / "intelligence-pack.json"
        IntelligenceStore.save(path, pack)
        round_trip = IntelligenceStore.load(path)
        round_trip_valid = round_trip.pack_hash == pack.pack_hash
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["pack"]["metadata"]["tampered"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            IntelligenceStore.load(path)
        except ValueError:
            tamper_detected = True
        else:
            tamper_detected = False

    baseline_required = len([question for question in baseline.questions if question.required])
    inherited_required = len([question for question in inherited.questions if question.required])
    poison_eligible = any(item.pattern_id == poison.pattern_id for item in registry.eligible())
    semantic_support = max((item.support_count for item in eligible_semantic), default=0)

    checks = {
        "multi_origin_consensus": bool(eligible_semantic) and semantic_support >= 3,
        "single_origin_poison_quarantined": not poison_eligible,
        "baseline_requires_review": baseline_required > 0,
        "inheritance_reduces_review": inherited_required < baseline_required,
        "inherited_mapping_applied": inheritance_report.inherited_mapping_count > 0,
        "inherited_composition_ready": inherited.ready_for_verification,
        "privacy_clean": privacy_clean,
        "repair_strategy_inherited": bool(repair_advice) and repair_advice[0]["support_count"] >= 2,
        "pack_round_trip": round_trip_valid,
        "pack_tamper_detected": tamper_detected,
    }
    report = {
        "phase": 4,
        "evaluation_kind": "deterministic architectural inheritance fixture",
        "claim_boundary": "The 1-to-0 review-question change is a fixture-level gate, not a measured real-world automation rate.",
        "gate_pass": all(checks.values()),
        "checks": checks,
        "metrics": {
            "registered_patterns": len(registry.patterns),
            "eligible_patterns": len(registry.eligible()),
            "quarantined_patterns": len(registry.quarantined()),
            "semantic_pattern_support": semantic_support,
            "baseline_required_questions": baseline_required,
            "inherited_required_questions": inherited_required,
            "inherited_mapping_count": inheritance_report.inherited_mapping_count,
            "repair_advice_count": len(repair_advice),
        },
        "inheritance_report": inheritance_report.model_dump(mode="json"),
        "pack_hash": pack.pack_hash,
        "boundaries": {
            "payloads_exported": False,
            "tenant_identifiers_exported": False,
            "executable_code_exported": False,
            "contract_certification_bypassed": False,
        },
    }
    if output_path is not None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report
