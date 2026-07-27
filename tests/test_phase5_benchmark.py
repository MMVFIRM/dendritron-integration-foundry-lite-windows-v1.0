from difoundry.phase5_benchmark import run_phase5_benchmark


def test_phase5_benchmark_passes(tmp_path):
    report = run_phase5_benchmark(tmp_path / "phase5.json")
    assert report["gate_pass"]
    assert report["checks"]["local_failure_isolated"]
    assert report["checks"]["global_policy_enforced"]
    assert report["metrics"]["distinct_local_contracts"] == 4
