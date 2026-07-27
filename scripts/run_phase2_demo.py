from __future__ import annotations

import argparse
import json
from pathlib import Path

from difoundry.adapters.memory import MemoryAdapter
from difoundry.phase2_benchmark import (
    benchmark_contract,
    benchmark_profiles,
    evaluation_cases,
    novelty_cases,
    run_phase2_benchmark,
    training_set,
)
from difoundry.simulator import IntegrationSimulator
from difoundry.tissue import DendritronRoutingTissue, DendritronTissueConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Phase 2 Dendritron daughter-runtime demonstration")
    parser.add_argument("--output", default="build/phase2-demo")
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    contract = benchmark_contract()
    profiles = benchmark_profiles()
    tissue_path = output / "daughter.tissue.json"
    tissue = DendritronRoutingTissue.from_contract(
        contract,
        DendritronTissueConfig(
            novelty_threshold=0.58,
            ownership_margin=0.025,
            spawn_below_similarity=0.78,
        ),
        store_path=tissue_path,
    )
    training_report = tissue.train(contract, training_set())

    adapters = {system_id: MemoryAdapter(system_id) for system_id in profiles}
    simulator = IntegrationSimulator(profiles, adapters, router=tissue)
    known_event = evaluation_cases()[2][0]
    known_result = simulator.process(contract, known_event, simulate=True)
    novel_result = simulator.process(contract, novelty_cases()[0], simulate=True)
    benchmark = run_phase2_benchmark(output / "phase2-benchmark.json")

    report = {
        "training": training_report,
        "known_event": known_result.model_dump(mode="json"),
        "novel_event": novel_result.model_dump(mode="json"),
        "tissue": tissue.summary(),
        "benchmark_gate_pass": benchmark["gate_pass"],
    }
    (output / "demo-report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
