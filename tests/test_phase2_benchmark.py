from difoundry.phase2_benchmark import run_phase2_benchmark


def test_phase2_benchmark_gates(tmp_path):
    report = run_phase2_benchmark(tmp_path / "benchmark.json")
    assert report["gate_pass"]
    assert report["evaluation_kind"] == "deterministic synthetic overlapping holdout"
    assert report["training_case_count"] == 18
    assert report["holdout_case_count"] == 360
    assert report["overlap_design"]["all_categorical_values_appear_in_every_branch"] is True
    assert report["static_priority_holdout_accuracy"] == 1 / 3
    assert report["best_single_field_accuracy"] <= 0.40
    assert report["dendritron_holdout_accuracy"] >= 0.85
    assert report["dendritron_holdout_accuracy"] > report["categorical_tuple_lookup_accuracy"]
    assert report["dendritron_selective_accuracy"] >= 0.95
    assert report["ambiguous_case_abstention_rate"] >= 0.80
    assert report["novelty_abstention_rate"] >= 0.80
    assert report["mean_active_specialist_fraction"] < 0.5
    assert report["branch_scoped_adaptation"]["pass"]
    assert report["damage_isolation"]["pass"]
