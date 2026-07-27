from __future__ import annotations

import argparse
import json

from difoundry.phase5_benchmark import run_phase5_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Dendritron Integration Foundry Phase 5 gates")
    parser.add_argument("--output", default="reports/phase5-benchmark.json")
    args = parser.parse_args()
    report = run_phase5_benchmark(args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["gate_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
