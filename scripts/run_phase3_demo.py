from __future__ import annotations

import argparse
import json
from pathlib import Path

from difoundry.phase3_benchmark import run_phase3_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the complete Phase 3 bounded self-repair demonstration")
    parser.add_argument("--output", default="build/phase3-demo/phase3-benchmark.json")
    args = parser.parse_args()
    report = run_phase3_benchmark(args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["gate_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
