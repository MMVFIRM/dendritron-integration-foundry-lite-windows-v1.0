from __future__ import annotations

from typing import Any

from difoundry.adapters.memory import MemoryAdapter
from difoundry.models import OperationProfile
from difoundry.phase2_benchmark import benchmark_contract, benchmark_profiles, evaluation_cases, training_set
from difoundry.simulator import IntegrationSimulator
from difoundry.tissue import DendritronRoutingTissue, DendritronTissueConfig


class FailingAdapter:
    system_id = "sink"

    def execute(
        self,
        operation: OperationProfile,
        payload: dict[str, Any],
        path_parameters: dict[str, Any],
        query_parameters: dict[str, Any],
        idempotency_key: str,
        simulate: bool,
    ) -> dict[str, Any]:
        raise RuntimeError("target schema changed: segment is no longer writable")


def make_tissue():
    contract = benchmark_contract()
    tissue = DendritronRoutingTissue.from_contract(
        contract,
        DendritronTissueConfig(novelty_threshold=0.58, ownership_margin=0.025, spawn_below_similarity=0.78),
    )
    tissue.train(contract, training_set())
    return contract, tissue


def test_simulator_carries_ownership_into_actions_without_mutating_on_dry_run():
    contract, tissue = make_tissue()
    profiles = benchmark_profiles()
    before = tissue.state.version
    simulator = IntegrationSimulator(profiles, {"source": MemoryAdapter("source"), "sink": MemoryAdapter("sink")}, router=tissue)
    event = next(event for event, expected in evaluation_cases() if expected == "smb_east")
    result = simulator.process(contract, event, simulate=True)
    assert result.status == "simulated"
    assert result.plan is not None
    action = result.plan.actions[0]
    assert action.branch_id == "smb_east"
    assert action.ownership_key == tissue.state.branch("dispatch", "smb_east").ownership_key
    assert action.specialist_ids
    assert tissue.state.version == before


def test_execute_failure_is_attributed_to_exact_branch():
    contract, tissue = make_tissue()
    profiles = benchmark_profiles()
    simulator = IntegrationSimulator(profiles, {"source": MemoryAdapter("source"), "sink": FailingAdapter()}, router=tissue)
    event = next(event for event, expected in evaluation_cases() if expected == "smb_east")
    result = simulator.process(contract, event, simulate=False)
    assert result.status == "failed"
    assert tissue.state.failure_attributions
    attribution = tissue.state.failure_attributions[-1]
    assert attribution.route_id == "dispatch"
    assert attribution.branch_id == "smb_east"
    assert tissue.state.branch("dispatch", "enterprise_west").failures == 0
    assert tissue.state.branch("dispatch", "public_central").failures == 0
