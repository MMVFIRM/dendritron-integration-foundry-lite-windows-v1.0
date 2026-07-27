from __future__ import annotations

import json
from pathlib import Path

import pytest

from difoundry.models import CanonicalEvent, PlannedAction
from difoundry.phase2_benchmark import (
    benchmark_contract,
    ambiguous_cases,
    evaluation_cases,
    novelty_cases,
    training_set,
)
from difoundry.tissue import (
    DendritronRoutingTissue,
    DendritronTissueConfig,
    RouterTrainingExample,
    TissueIntegrityError,
    TissueStore,
    event_context,
)


def trained_tissue() -> tuple[object, DendritronRoutingTissue]:
    contract = benchmark_contract()
    tissue = DendritronRoutingTissue.from_contract(
        contract,
        DendritronTissueConfig(novelty_threshold=0.35, ownership_margin=0.015, spawn_below_similarity=0.70, max_specialists_per_branch=16),
    )
    tissue.train(contract, training_set())
    return contract, tissue


def test_learned_tissue_selects_overlapping_owners_and_abstains_when_needed():
    contract, tissue = trained_tissue()
    correct = 0
    non_abstained = 0
    non_abstained_correct = 0
    for event, expected in evaluation_cases():
        trace = tissue.select(contract, event_context(event))[0].trace
        correct += int(trace.selected_branch_id == expected)
        if not trace.abstained:
            non_abstained += 1
            non_abstained_correct += int(trace.selected_branch_id == expected)
            assert trace.router_kind == "dendritron_tissue"
            assert 1 <= len(trace.selected_specialist_ids) <= tissue.state.config.top_k_specialists
            sparse = trace.diagnostics["sparse_activation"]
            assert sparse["active"] < sparse["available"]
    assert correct / len(evaluation_cases()) >= 0.85
    assert non_abstained_correct / non_abstained >= 0.95

    for event in ambiguous_cases():
        trace = tissue.select(contract, event_context(event))[0].trace
        assert trace.abstained
        assert "Ownership margin" in (trace.reason or "")

    for event in novelty_cases():
        trace = tissue.select(contract, event_context(event))[0].trace
        assert trace.abstained
        assert trace.selected_branch_id is None
        assert any(marker in (trace.reason or "") for marker in ("Novel event", "Ownership margin"))


def test_hard_contract_gate_cannot_be_overridden_by_learning():
    contract, tissue = trained_tissue()
    event = evaluation_cases()[0][0].model_copy(update={"event_type": "deleted"})
    context = event_context(event)
    # Direct router call isolates hard gating from trigger validation in the planner.
    trace = tissue.select(contract, context)[0].trace
    assert trace.abstained
    assert trace.reason == "No branch satisfied its hard contract gate"


def test_adaptation_is_branch_scoped():
    contract, tissue = trained_tissue()
    before = {branch.branch_id: tissue.branch_hash(branch.route_id, branch.branch_id) for branch in tissue.state.branches}
    event = CanonicalEvent(
        source_system="source",
        source_object="record",
        event_type="upsert",
        idempotency_key="local-only",
        payload={
            "segment": "microbusiness",
            "region": "east",
            "channel": "selfserve",
            "service": "starter",
            "amount": 90,
            "active": True,
        },
    )
    tissue.learn(contract, RouterTrainingExample(event=event, route_id="dispatch", branch_id="smb_east"))
    after = {branch.branch_id: tissue.branch_hash(branch.route_id, branch.branch_id) for branch in tissue.state.branches}
    assert [key for key in before if before[key] != after[key]] == ["smb_east"]


def test_failure_attribution_updates_only_owner():
    _contract, tissue = trained_tissue()
    before = {branch.branch_id: tissue.branch_hash(branch.route_id, branch.branch_id) for branch in tissue.state.branches}
    action = PlannedAction(
        action_id="write_record",
        target_system_id="sink",
        operation_id="write",
        payload={"segment": "smb"},
        route_id="dispatch",
        branch_id="smb_east",
        specialist_ids=[tissue.state.branch("dispatch", "smb_east").specialists[0].specialist_id],
        ownership_key=tissue.state.branch("dispatch", "smb_east").ownership_key,
    )
    attribution = tissue.record_outcome(action, success=False, error="HTTP 409 duplicate external identifier")
    after = {branch.branch_id: tissue.branch_hash(branch.route_id, branch.branch_id) for branch in tissue.state.branches}
    assert attribution is not None
    assert attribution.branch_id == "smb_east"
    assert attribution.failure_signature.startswith("failure:")
    assert [key for key in before if before[key] != after[key]] == ["smb_east"]


def test_tissue_persistence_is_hash_bound(tmp_path: Path):
    _contract, tissue = trained_tissue()
    path = tmp_path / "daughter.tissue.json"
    TissueStore.save(path, tissue.state)
    loaded = TissueStore.load(path)
    assert loaded.model_dump(mode="json") == tissue.state.model_dump(mode="json")

    envelope = json.loads(path.read_text())
    envelope["state"]["version"] += 1
    path.write_text(json.dumps(envelope))
    with pytest.raises(TissueIntegrityError):
        TissueStore.load(path)


def test_disabling_one_branch_does_not_damage_other_owners():
    contract, tissue = trained_tissue()
    alpha_event = next(example.event for example in training_set().examples if example.branch_id == "enterprise_west")
    assert tissue.select(contract, event_context(alpha_event))[0].trace.selected_branch_id == "enterprise_west"
    tissue.set_branch_enabled("dispatch", "public_central", False)
    assert tissue.select(contract, event_context(alpha_event))[0].trace.selected_branch_id == "enterprise_west"


def test_partially_trained_route_does_not_trust_untrained_branches():
    contract = benchmark_contract()
    tissue = DendritronRoutingTissue.from_contract(
        contract,
        DendritronTissueConfig(novelty_threshold=0.4, ownership_margin=0.01, spawn_below_similarity=0.78),
    )
    alpha_example = training_set().examples[0]
    tissue.learn(contract, alpha_example)
    trace = tissue.select(contract, event_context(alpha_example.event))[0].trace
    assert trace.selected_branch_id == "enterprise_west"
    assert trace.diagnostics["branches"]["smb_east"]["learned_activation"] == 0.0
    assert trace.diagnostics["branches"]["public_central"]["learned_activation"] == 0.0


def test_low_ownership_margin_abstains_even_when_static_priority_differs():
    contract = benchmark_contract()
    tissue = DendritronRoutingTissue.from_contract(
        contract,
        DendritronTissueConfig(
            novelty_threshold=0.1,
            ownership_margin=0.05,
            spawn_below_similarity=0.78,
        ),
    )
    event = training_set().examples[0].event
    tissue.learn(contract, RouterTrainingExample(event=event, route_id="dispatch", branch_id="enterprise_west"))
    tissue.learn(contract, RouterTrainingExample(event=event, route_id="dispatch", branch_id="smb_east"))
    trace = tissue.select(contract, event_context(event))[0].trace
    assert trace.abstained
    assert "Ownership margin" in (trace.reason or "")


def test_negative_feedback_is_local_and_reduces_specialist_similarity():
    contract, tissue = trained_tissue()
    event = next(example.event for example in training_set().examples if example.branch_id == "enterprise_west")
    before_trace = tissue.select(contract, event_context(event))[0].trace
    before_score = before_trace.diagnostics["branches"]["enterprise_west"]["learned_activation"]
    other_before = tissue.branch_hash("dispatch", "smb_east")
    tissue.learn(
        contract,
        RouterTrainingExample(
            event=event,
            route_id="dispatch",
            branch_id="enterprise_west",
            reward=-1.0,
        ),
    )
    after_trace = tissue.select(contract, event_context(event))[0].trace
    after_score = after_trace.diagnostics["branches"]["enterprise_west"]["learned_activation"]
    assert after_score < before_score
    assert tissue.branch_hash("dispatch", "smb_east") == other_before
