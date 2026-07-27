import json
import os
import subprocess
import sys
from pathlib import Path

import difoundry


def test_phase5_cli_benchmark(tmp_path):
    output = tmp_path / "phase5.json"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(difoundry.__file__).resolve().parents[1])
    completed = subprocess.run(
        [sys.executable, "-m", "difoundry.cli", "benchmark-phase5", "--output", str(output)],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text())
    assert report["gate_pass"]
