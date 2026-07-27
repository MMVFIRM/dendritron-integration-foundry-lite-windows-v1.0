import json
import os
import subprocess
import sys
from pathlib import Path

import difoundry


def test_phase4_benchmark_cli(tmp_path):
    output = tmp_path / "phase4.json"
    env = {**os.environ, "PYTHONPATH": str(Path(difoundry.__file__).resolve().parents[1])}
    result = subprocess.run(
        [sys.executable, "-m", "difoundry.cli", "benchmark-phase4", "--output", str(output)],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["gate_pass"]
