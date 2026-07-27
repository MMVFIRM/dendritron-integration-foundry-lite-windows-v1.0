from __future__ import annotations

from dataclasses import dataclass, field

from .models import CompositionResult, DiscoveryResult, IntegrationContract, SystemProfile
from .tissue import DendritronRoutingTissue
from .repair import RepairCandidate, RepairDeployment


@dataclass
class PlatformRegistry:
    profiles: dict[str, SystemProfile] = field(default_factory=dict)
    contracts: dict[str, IntegrationContract] = field(default_factory=dict)
    discoveries: dict[str, DiscoveryResult] = field(default_factory=dict)
    compositions: dict[str, CompositionResult] = field(default_factory=dict)
    tissues: dict[str, DendritronRoutingTissue] = field(default_factory=dict)
    repairs: dict[str, RepairCandidate] = field(default_factory=dict)
    deployments: dict[str, RepairDeployment] = field(default_factory=dict)

    def register_profile(self, profile: SystemProfile) -> None:
        self.profiles[profile.system_id] = profile

    def register_contract(self, contract: IntegrationContract) -> None:
        self.contracts[contract.contract_id] = contract

    def register_discovery(self, result: DiscoveryResult) -> None:
        self.discoveries[result.discovery_id] = result
        self.register_profile(result.profile)

    def register_composition(self, result: CompositionResult) -> None:
        self.compositions[result.composition_id] = result
        self.register_contract(result.contract)

    def register_tissue(self, tissue: DendritronRoutingTissue) -> None:
        self.tissues[tissue.state.tissue_id] = tissue

    def register_repair(self, repair: RepairCandidate) -> None:
        self.repairs[repair.repair_id] = repair

    def register_deployment(self, deployment: RepairDeployment) -> None:
        self.deployments[deployment.deployment_id] = deployment
