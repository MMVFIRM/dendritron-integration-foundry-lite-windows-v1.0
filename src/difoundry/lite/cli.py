from __future__ import annotations

import argparse
import json
from pathlib import Path

from .benchmark import run


def benchmark_main() -> None:
    parser=argparse.ArgumentParser(description="Run the deterministic Foundry Lite product fixture")
    parser.add_argument("--output", default="reports/foundry-lite-benchmark.json")
    args=parser.parse_args()
    report=run(Path(args.output))
    print(json.dumps(report, indent=2, sort_keys=True))
