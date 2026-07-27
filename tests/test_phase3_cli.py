import json
import os
import subprocess
import sys
from pathlib import Path

import difoundry


def test_phase3_benchmark_cli(tmp_path):
    output = tmp_path / "benchmark.json"
    package_parent = str(Path(difoundry.__file__).resolve().parents[1])
    environment = {**os.environ, "PYTHONPATH": package_parent}
    completed = subprocess.run(
        [sys.executable, "-m", "difoundry.cli", "benchmark-phase3", "--output", str(output)],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text())
    assert report["gate_pass"]
