from difoundry.phase4_benchmark import run_phase4_benchmark


def test_phase4_benchmark_passes(tmp_path):
    report = run_phase4_benchmark(tmp_path / "phase4.json")
    assert report["gate_pass"]
    assert report["checks"]["single_origin_poison_quarantined"]
    assert report["metrics"]["inherited_required_questions"] == 0
