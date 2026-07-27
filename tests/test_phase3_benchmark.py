from difoundry.phase3_benchmark import run_phase3_benchmark


def test_phase3_benchmark_passes(tmp_path):
    report = run_phase3_benchmark(tmp_path / "phase3.json")
    assert report["gate_pass"]
    assert report["drift_detection"]["pass"]
    assert report["failure_locality"]["pass"]
    assert report["verification"]["passed"]
    assert report["signature"]["valid"]
    assert report["recovery"]["pass"]
