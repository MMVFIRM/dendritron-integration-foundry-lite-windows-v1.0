from __future__ import annotations

import argparse

from difoundry.phase6_benchmark import run_phase6_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 6 production-platform gates")
    parser.add_argument("--output", default="reports/phase6-benchmark.json")
    args = parser.parse_args()
    report = run_phase6_benchmark(args.output)
    print(f"Phase 6 gate pass: {report['gate_pass']}")
    for name, passed in report["gates"].items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    raise SystemExit(0 if report["gate_pass"] else 1)


if __name__ == "__main__":
    main()
