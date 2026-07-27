from __future__ import annotations

import json
from pathlib import Path

from difoundry.lite.benchmark import run


if __name__ == "__main__":
    output = Path("reports/foundry-lite-benchmark.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(run(output), indent=2, sort_keys=True))
