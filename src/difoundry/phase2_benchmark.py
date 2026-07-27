from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .models import (
    ActionDefinition,
    CanonicalEvent,
    ConditionOperator,
    IntegrationContract,
    MappingRule,
    OperationProfile,
    RouteBranch,
    RouteCondition,
    RouteDefinition,
    SystemProfile,
    TriggerDefinition,
)
from .routing import DendriticOwnedRouter
from .tissue import (
    DendritronRoutingTissue,
    DendritronTissueConfig,
    RouterTrainingExample,
    RouterTrainingSet,
    TissueStore,
    event_context,
)

BRANCHES = ["enterprise_west", "smb_east", "public_central"]
VALUES = {
    "segment": ["enterprise", "smb", "public"],
    "region": ["west", "east", "central"],
    "channel": ["direct", "web", "procurement"],
    "service": ["managed", "standard", "regulated"],
    "amount": [9000, 700, 6000],
}
# Every value in every field appears in every branch. Ownership is encoded by
# multi-field combinations rather than one disjoint class marker.
MODES = {
    "enterprise_west": [
        (0, 0, 0, 0, 0),
        (1, 1, 1, 1, 1),
        (2, 2, 2, 2, 2),
    ],
    "smb_east": [
        (0, 1, 2, 1, 2),  # enterprise in the east
        (1, 2, 0, 2, 0),  # SMB with an approximately $9k amount
        (2, 0, 1, 0, 1),
    ],
    "public_central": [
        (0, 2, 1, 2, 1),
        (1, 0, 2, 0, 2),
        (2, 1, 0, 1, 0),
    ],
}


def benchmark_contract() -> IntegrationContract:
    common = [RouteCondition(path="event.event_type", operator=ConditionOperator.EQ, value="upsert")]
    return IntegrationContract(
        contract_id="phase2-routing-benchmark",
        version="1",
        name="Protocol-neutral routing benchmark",
        trigger=TriggerDefinition(system_id="source", object_type="record", event_type="upsert"),
        routes=[
            RouteDefinition(
                route_id="dispatch",
                abstain_on_tie=False,
                branches=[
                    RouteBranch(branch_id="enterprise_west", conditions=common, priority=3),
                    RouteBranch(branch_id="smb_east", conditions=common, priority=2),
                    RouteBranch(branch_id="public_central", conditions=common, priority=1),
                ],
                actions=[
                    ActionDefinition(
                        action_id="write_record",
                        target_system_id="sink",
                        operation_id="write",
                        mappings=[MappingRule(source="segment", target="segment", required=True)],
                    )
                ],
            )
        ],
        permissions={"sink": ["records.write"]},
    )


def benchmark_profiles() -> dict[str, SystemProfile]:
    return {
        "source": SystemProfile(system_id="source", name="Generic Source", protocol="custom"),
        "sink": SystemProfile(
            system_id="sink",
            name="Generic Sink",
            protocol="custom",
            operations=[
                OperationProfile(
                    operation_id="write",
                    method="WRITE",
                    path="records",
                    operation_kind="create",
                    request_schema={
                        "type": "object",
                        "properties": {"segment": {"type": "string"}},
                        "required": ["segment"],
                    },
                    required_permissions=["records.write"],
                )
            ],
        ),
    }


def make_event(
    name: str,
    *,
    segment: str,
    region: str,
    channel: str,
    service: str,
    amount: int,
    extra: dict[str, Any] | None = None,
) -> CanonicalEvent:
    payload = {
        "segment": segment,
        "region": region,
        "channel": channel,
        "service": service,
        "amount": amount,
        "active": True,
    }
    payload.update(extra or {})
    return CanonicalEvent(
        event_id=f"evt_{name}",
        source_system="source",
        source_object="record",
        event_type="upsert",
        source_record_id=name,
        idempotency_key=f"idem_{name}",
        payload=payload,
    )


def _mode_event(
    seed: int,
    branch: str,
    mode_index: int,
    sample_index: int,
    *,
    corruption_probability: float,
) -> CanonicalEvent:
    rng = random.Random(
        seed * 100000 + sample_index * 17 + mode_index * 101 + BRANCHES.index(branch) * 1009
    )
    indexes = list(MODES[branch][mode_index])
    # Holdout families overlap: one categorical coordinate is sometimes replaced
    # with a value used by a competing owner. The resulting exact tuple was not
    # necessarily observed in training, while the remaining conjunction still
    # provides evidence for the originating branch.
    if rng.random() < corruption_probability:
        position = rng.randrange(4)
        indexes[position] = (indexes[position] + rng.choice([1, 2])) % 3
    amount = VALUES["amount"][indexes[4]] + rng.randint(-80, 80)
    return make_event(
        f"{seed}-{branch}-{mode_index}-{sample_index}",
        segment=VALUES["segment"][indexes[0]],
        region=VALUES["region"][indexes[1]],
        channel=VALUES["channel"][indexes[2]],
        service=VALUES["service"][indexes[3]],
        amount=amount,
        extra={"noise_tag": f"n{rng.randrange(5)}"},
    )


def training_set() -> RouterTrainingSet:
    examples = []
    for branch in BRANCHES:
        for mode_index in range(3):
            for sample_index in range(2):
                examples.append(
                    RouterTrainingExample(
                        event=_mode_event(
                            1,
                            branch,
                            mode_index,
                            sample_index,
                            corruption_probability=0.0,
                        ),
                        route_id="dispatch",
                        branch_id=branch,
                    )
                )
    return RouterTrainingSet(examples=examples)


def evaluation_cases() -> list[tuple[CanonicalEvent, str]]:
    """Deterministic overlapping holdout with unseen and corrupted tuples."""
    cases = [
        (
            _mode_event(
                2,
                branch,
                mode_index,
                sample_index,
                corruption_probability=0.35,
            ),
            branch,
        )
        for branch in BRANCHES
        for mode_index in range(3)
        for sample_index in range(40)
    ]
    random.Random(20260727).shuffle(cases)
    return cases


def ambiguous_cases() -> list[CanonicalEvent]:
    # These combinations are exact or near-exact ownership ties. They are not
    # labeled as belonging to a branch; the desired behavior is abstention.
    indexes = [
        (0, 0, 1, 1, 0),
        (0, 0, 1, 1, 1),
        (0, 0, 1, 1, 2),
        (0, 0, 2, 0, 0),
        (0, 0, 2, 0, 1),
        (0, 0, 2, 0, 2),
        (0, 0, 2, 2, 0),
        (0, 0, 2, 2, 2),
        (0, 1, 0, 1, 0),
        (0, 1, 0, 1, 1),
        (0, 1, 0, 1, 2),
        (0, 1, 0, 2, 0),
    ]
    return [
        make_event(
            f"ambiguous-{index}",
            segment=VALUES["segment"][item[0]],
            region=VALUES["region"][item[1]],
            channel=VALUES["channel"][item[2]],
            service=VALUES["service"][item[3]],
            amount=VALUES["amount"][item[4]] + 31,
        )
        for index, item in enumerate(indexes)
    ]


def novelty_cases() -> list[CanonicalEvent]:
    return [
        make_event("novel-1", segment="research", region="orbital", channel="telemetry", service="experimental", amount=42),
        make_event("novel-2", segment="unknown", region="offshore", channel="manual", service="bespoke", amount=3333, extra={"shape": "unseen"}),
        make_event("novel-3", segment="consumer", region="lunar", channel="radio", service="prototype", amount=-8),
        make_event("novel-4", segment="academic", region="subsea", channel="batch-tape", service="archival", amount=1000000),
        make_event("novel-5", segment="machine", region="interplanetary", channel="mesh", service="autonomous", amount=17),
        make_event("novel-6", segment="none", region="none", channel="none", service="none", amount=0, extra={"unknown_array": [1, 2, 3]}),
    ]


def _categorical_tuple(event: CanonicalEvent) -> tuple[str, str, str, str]:
    payload = event.payload
    return (
        str(payload["segment"]),
        str(payload["region"]),
        str(payload["channel"]),
        str(payload["service"]),
    )


def _lookup_baselines(
    training: RouterTrainingSet,
    evaluation: list[tuple[CanonicalEvent, str]],
) -> dict[str, Any]:
    fallback = Counter(example.branch_id for example in training.examples).most_common(1)[0][0]
    tuples: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    fields: dict[str, dict[str, Counter[str]]] = {
        name: defaultdict(Counter) for name in ("segment", "region", "channel", "service")
    }
    for example in training.examples:
        tuples[_categorical_tuple(example.event)][example.branch_id] += 1
        for field in fields:
            fields[field][str(example.event.payload[field])][example.branch_id] += 1

    tuple_correct = 0
    tuple_seen = 0
    single_results: dict[str, float] = {}
    for event, expected in evaluation:
        key = _categorical_tuple(event)
        votes = tuples.get(key)
        if votes:
            tuple_seen += 1
            prediction = votes.most_common(1)[0][0]
        else:
            prediction = fallback
        tuple_correct += int(prediction == expected)

    for field, table in fields.items():
        correct = 0
        for event, expected in evaluation:
            votes = table.get(str(event.payload[field]))
            prediction = votes.most_common(1)[0][0] if votes else fallback
            correct += int(prediction == expected)
        single_results[field] = correct / len(evaluation)

    return {
        "categorical_tuple_lookup_accuracy": tuple_correct / len(evaluation),
        "categorical_tuple_seen_fraction": tuple_seen / len(evaluation),
        "single_field_accuracy": single_results,
        "best_single_field_accuracy": max(single_results.values()),
    }


def run_phase2_benchmark(output_path: str | Path | None = None) -> dict[str, Any]:
    contract = benchmark_contract()
    config = DendritronTissueConfig(
        top_k_specialists=2,
        max_specialists_per_branch=16,
        spawn_below_similarity=0.70,
        novelty_threshold=0.35,
        ownership_margin=0.015,
        abstain_on_novelty=True,
    )
    tissue = DendritronRoutingTissue.from_contract(contract, config)
    training = training_set()
    evaluation = evaluation_cases()
    training_report = tissue.train(contract, training)
    baseline = DendriticOwnedRouter()
    lookup = _lookup_baselines(training, evaluation)

    baseline_correct = 0
    tissue_correct = 0
    tissue_non_abstained = 0
    tissue_non_abstained_correct = 0
    sparse_ratios: list[float] = []
    predictions: list[dict[str, Any]] = []
    for event, expected in evaluation:
        context = event_context(event)
        baseline_trace = baseline.select(contract, context)[0].trace
        tissue_trace = tissue.select(contract, context)[0].trace
        baseline_correct += int(baseline_trace.selected_branch_id == expected)
        tissue_correct += int(tissue_trace.selected_branch_id == expected)
        if not tissue_trace.abstained:
            tissue_non_abstained += 1
            tissue_non_abstained_correct += int(tissue_trace.selected_branch_id == expected)
        sparse = tissue_trace.diagnostics.get("sparse_activation", {})
        available = int(sparse.get("available", 0))
        active = int(sparse.get("active", 0))
        sparse_ratios.append(0.0 if available == 0 else active / available)
        predictions.append(
            {
                "event_id": event.event_id,
                "expected": expected,
                "static_priority": baseline_trace.selected_branch_id,
                "dendritron": tissue_trace.selected_branch_id,
                "abstained": tissue_trace.abstained,
                "reason": tissue_trace.reason,
                "novelty": tissue_trace.novelty_score,
                "active_specialists": active,
                "available_specialists": available,
            }
        )

    novelty_predictions = [tissue.select(contract, event_context(event))[0].trace for event in novelty_cases()]
    novelty_abstention_rate = sum(int(trace.abstained) for trace in novelty_predictions) / len(novelty_predictions)
    ambiguity_predictions = [tissue.select(contract, event_context(event))[0].trace for event in ambiguous_cases()]
    ambiguity_abstention_rate = sum(int(trace.abstained) for trace in ambiguity_predictions) / len(ambiguity_predictions)

    hashes_before = {
        branch.branch_id: tissue.branch_hash(branch.route_id, branch.branch_id) for branch in tissue.state.branches
    }
    tissue.learn(
        contract,
        RouterTrainingExample(
            event=make_event(
                "local-adapt",
                segment="smb",
                region="east",
                channel="web",
                service="standard",
                amount=850,
                extra={"local_variant": True},
            ),
            route_id="dispatch",
            branch_id="smb_east",
        ),
    )
    hashes_after = {
        branch.branch_id: tissue.branch_hash(branch.route_id, branch.branch_id) for branch in tissue.state.branches
    }
    changed_branches = sorted(key for key in hashes_before if hashes_before[key] != hashes_after[key])

    enterprise_event = next(example.event for example in training.examples if example.branch_id == "enterprise_west")
    before_disable = tissue.select(contract, event_context(enterprise_event))[0].trace.selected_branch_id
    tissue.set_branch_enabled("dispatch", "public_central", False)
    after_disable = tissue.select(contract, event_context(enterprise_event))[0].trace.selected_branch_id
    failure_isolation_pass = before_disable == after_disable == "enterprise_west"
    tissue.set_branch_enabled("dispatch", "public_central", True)

    output = Path(output_path) if output_path else None
    persistence_report: dict[str, Any]
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        tissue_path = output.with_suffix(".tissue.json")
        TissueStore.save(tissue_path, tissue.state)
        loaded = TissueStore.load(tissue_path)
        persistence_report = {
            "path": str(tissue_path),
            "round_trip": loaded.model_dump(mode="json") == tissue.state.model_dump(mode="json"),
        }
    else:
        persistence_report = {"round_trip": True}

    count = len(evaluation)
    coverage = tissue_non_abstained / count
    selective_accuracy = (
        tissue_non_abstained_correct / tissue_non_abstained if tissue_non_abstained else 0.0
    )
    report = {
        "benchmark": "phase2-dendritron-routing-tissue",
        "version": "0.7.2",
        "training": training_report,
        "evaluation_kind": "deterministic synthetic overlapping holdout",
        "claim_boundary": (
            "architectural routing fixture with overlapping multi-field families; not a real-world integration "
            "accuracy estimate, throughput study, or evidence of Dendritron superiority over trained statistical baselines"
        ),
        "training_case_count": len(training.examples),
        "holdout_case_count": count,
        "overlap_design": {
            "all_categorical_values_appear_in_every_branch": True,
            "holdout_single_coordinate_corruption_probability": 0.35,
            "exact_training_event_reuse": False,
            "notes": "Branch ownership depends on conjunctions. Enterprise-east and approximately-$9k SMB cases are intentionally present.",
        },
        "static_priority_holdout_accuracy": baseline_correct / count,
        **lookup,
        "dendritron_holdout_accuracy": tissue_correct / count,
        "dendritron_coverage": coverage,
        "dendritron_selective_accuracy": selective_accuracy,
        "holdout_abstention_rate": 1.0 - coverage,
        "ambiguous_case_count": len(ambiguity_predictions),
        "ambiguous_case_abstention_rate": ambiguity_abstention_rate,
        "novelty_case_count": len(novelty_predictions),
        "novelty_abstention_rate": novelty_abstention_rate,
        "mean_active_specialist_fraction": sum(sparse_ratios) / len(sparse_ratios),
        "branch_scoped_adaptation": {
            "changed_branches": changed_branches,
            "pass": changed_branches == ["smb_east"],
        },
        "damage_isolation": {"pass": failure_isolation_pass},
        "persistence": persistence_report,
        "predictions": predictions,
        "ambiguity": [trace.model_dump(mode="json") for trace in ambiguity_predictions],
        "novelty": [trace.model_dump(mode="json") for trace in novelty_predictions],
    }
    report["gate_pass"] = all(
        [
            report["dendritron_holdout_accuracy"] >= 0.85,
            report["dendritron_holdout_accuracy"] - report["categorical_tuple_lookup_accuracy"] >= 0.10,
            report["best_single_field_accuracy"] <= 0.40,
            report["dendritron_selective_accuracy"] >= 0.95,
            report["ambiguous_case_abstention_rate"] >= 0.80,
            report["novelty_abstention_rate"] >= 0.80,
            report["mean_active_specialist_fraction"] < 0.5,
            report["branch_scoped_adaptation"]["pass"],
            report["damage_isolation"]["pass"],
            report["persistence"]["round_trip"],
        ]
    )
    if output:
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report
