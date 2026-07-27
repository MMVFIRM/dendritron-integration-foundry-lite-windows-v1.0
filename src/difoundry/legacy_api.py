from __future__ import annotations

import os

if os.getenv("DIFOUNDRY_ENV", "development").lower() == "production":
    raise RuntimeError("The Phase 0-5 developer API cannot run with DIFOUNDRY_ENV=production")

from fastapi import FastAPI, HTTPException, Query

from .adapters.memory import MemoryAdapter
from .composition import CompositionError, DaughterComposer
from .discovery import DiscoveryService
from .ledger import EventLedger
from .intelligence import (
    InheritedRepairAdvisor,
    InheritedSemanticMatcher,
    IntelligenceExporter,
    IntelligencePack,
    IntelligencePattern,
    IntelligenceRegistry,
)
from .models import (
    CanonicalEvent,
    CompositionRequest,
    CompositionResult,
    DiscoveryResult,
    DiscoverySource,
    IntegrationContract,
    SimulationResult,
    SystemProfile,
)
from .registry import PlatformRegistry
from .repair import (
    DriftObservation,
    RepairApprovalService,
    RepairCandidate,
    RepairDeployment,
    RepairDeploymentManager,
    RepairGenerator,
    RepairLedger,
    RepairSigner,
    RepairVerificationRequest,
    RepairVerifier,
)
from .simulator import IntegrationSimulator
from .validation import ContractValidator
from .nervous import (
    CoordinationResult,
    CoordinationWorkflow,
    DaughterRegistration,
    DaughterRuntime,
    DaughterRuntimeRequest,
    GlobalPolicyEngine,
    GlobalPolicySet,
    MultiSystemNervousSystem,
)
from .tissue import (
    DendritronRoutingTissue,
    DendritronTissueConfig,
    DendritronTissueState,
    RouterTrainingSet,
)

app = FastAPI(title="Dendritron Integration Foundry Developer API", version="0.7.2")
registry = PlatformRegistry()
ledger = EventLedger()
discovery_service = DiscoveryService()
composer = DaughterComposer()
repair_ledger = RepairLedger()
repair_generator = RepairGenerator()
repair_verifier = RepairVerifier()
repair_deployer = RepairDeploymentManager()
intelligence_registry = IntelligenceRegistry()
intelligence_exporter = IntelligenceExporter()
nervous_system = MultiSystemNervousSystem()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "phase": 6,
        "profiles": len(registry.profiles),
        "contracts": len(registry.contracts),
        "discoveries": len(registry.discoveries),
        "compositions": len(registry.compositions),
        "tissues": len(registry.tissues),
        "repairs": len(registry.repairs),
        "deployments": len(registry.deployments),
        "quarantines": len(repair_ledger.pending_quarantines()),
        "intelligence_patterns": len(intelligence_registry.patterns),
        "eligible_intelligence_patterns": len(intelligence_registry.eligible()),
        "nervous_daughters": len(nervous_system.daughters),
        "nervous_workflows": len(nervous_system.workflows),
    }


@app.get("/discovery/formats")
def discovery_formats() -> dict[str, list[str]]:
    return {"formats": discovery_service.formats()}


@app.post("/discover", response_model=DiscoveryResult)
def discover(source: DiscoverySource, register: bool = Query(default=True)) -> DiscoveryResult:
    try:
        result = discovery_service.discover(source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if register:
        registry.register_discovery(result)
    return result


@app.get("/discoveries", response_model=list[DiscoveryResult])
def list_discoveries() -> list[DiscoveryResult]:
    return list(registry.discoveries.values())


@app.post("/profiles", response_model=SystemProfile)
def register_profile(profile: SystemProfile) -> SystemProfile:
    registry.register_profile(profile)
    return profile


@app.get("/profiles", response_model=list[SystemProfile])
def list_profiles() -> list[SystemProfile]:
    return list(registry.profiles.values())


@app.post("/compose", response_model=CompositionResult)
def compose(
    request: CompositionRequest,
    register: bool = Query(default=True),
    inherit: bool = Query(default=False),
) -> CompositionResult:
    active_composer = DaughterComposer(InheritedSemanticMatcher(intelligence_registry)) if inherit else composer
    try:
        result = active_composer.compose(request, registry.profiles)
    except CompositionError as exc:
        raise HTTPException(400, str(exc)) from exc
    if register:
        registry.register_composition(result)
    return result


@app.get("/compositions", response_model=list[CompositionResult])
def list_compositions() -> list[CompositionResult]:
    return list(registry.compositions.values())

@app.post("/intelligence/patterns", response_model=IntelligencePattern)
def register_intelligence_pattern(pattern: IntelligencePattern) -> IntelligencePattern:
    try:
        return intelligence_registry.add(pattern)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/intelligence/patterns", response_model=list[IntelligencePattern])
def list_intelligence_patterns(eligible_only: bool = Query(default=False)) -> list[IntelligencePattern]:
    return intelligence_registry.eligible() if eligible_only else list(intelligence_registry.patterns.values())


@app.post("/intelligence/export/compositions/{composition_id}", response_model=list[IntelligencePattern])
def export_composition_intelligence(
    composition_id: str,
    origin_ref: str = Query(..., min_length=1),
) -> list[IntelligencePattern]:
    try:
        composition = registry.compositions[composition_id]
    except KeyError as exc:
        raise HTTPException(404, "Composition not found") from exc
    try:
        patterns = intelligence_exporter.from_composition(composition, registry.profiles, origin_ref)
        return [intelligence_registry.add(pattern) for pattern in patterns]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/intelligence/export/repairs/{repair_id}", response_model=IntelligencePattern)
def export_repair_intelligence(
    repair_id: str,
    origin_ref: str = Query(..., min_length=1),
    drift_kind: str = Query(..., min_length=1),
) -> IntelligencePattern:
    try:
        repair = registry.repairs[repair_id]
    except KeyError as exc:
        raise HTTPException(404, "Repair not found") from exc
    try:
        return intelligence_registry.add(
            intelligence_exporter.from_repair(repair, origin_ref=origin_ref, drift_kind=drift_kind)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/intelligence/pack", response_model=IntelligencePack)
def export_intelligence_pack(include_quarantined: bool = Query(default=False)) -> IntelligencePack:
    return intelligence_registry.pack(include_quarantined=include_quarantined)


@app.post("/intelligence/pack", response_model=list[IntelligencePattern])
def import_intelligence_pack(pack: IntelligencePack) -> list[IntelligencePattern]:
    try:
        return intelligence_registry.import_pack(pack)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/intelligence/repair-advice/{drift_kind}")
def inherited_repair_advice(drift_kind: str) -> list[dict[str, object]]:
    return InheritedRepairAdvisor(intelligence_registry).advise(drift_kind)



@app.post("/tissues/{contract_id}", response_model=DendritronTissueState)
def initialize_tissue(
    contract_id: str, config: DendritronTissueConfig | None = None
) -> DendritronTissueState:
    try:
        contract = registry.contracts[contract_id]
    except KeyError as exc:
        raise HTTPException(404, "Contract not found") from exc
    tissue = DendritronRoutingTissue.from_contract(contract, config)
    registry.register_tissue(tissue)
    return tissue.state


@app.get("/tissues", response_model=list[DendritronTissueState])
def list_tissues() -> list[DendritronTissueState]:
    return [tissue.state for tissue in registry.tissues.values()]


@app.get("/tissues/{tissue_id}/summary")
def tissue_summary(tissue_id: str) -> dict[str, object]:
    try:
        tissue = registry.tissues[tissue_id]
    except KeyError as exc:
        raise HTTPException(404, "Tissue not found") from exc
    return tissue.summary()


@app.post("/tissues/{tissue_id}/train")
def train_tissue(tissue_id: str, training_set: RouterTrainingSet) -> dict[str, object]:
    try:
        tissue = registry.tissues[tissue_id]
    except KeyError as exc:
        raise HTTPException(404, "Tissue not found") from exc
    try:
        contract = registry.contracts[tissue.state.contract_id]
    except KeyError as exc:
        raise HTTPException(409, "Tissue contract is not registered") from exc
    try:
        return tissue.train(contract, training_set)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/contracts", response_model=IntegrationContract)
def register_contract(contract: IntegrationContract) -> IntegrationContract:
    report = ContractValidator().validate(contract, registry.profiles)
    if report.errors:
        raise HTTPException(400, {"errors": report.errors, "warnings": report.warnings})
    registry.register_contract(contract)
    return contract


@app.get("/contracts", response_model=list[IntegrationContract])
def list_contracts() -> list[IntegrationContract]:
    return list(registry.contracts.values())


@app.post("/simulate/{contract_id}", response_model=SimulationResult)
def simulate(
    contract_id: str,
    event: CanonicalEvent,
    execute: bool = Query(default=False),
    tissue_id: str | None = Query(default=None),
) -> SimulationResult:
    try:
        contract = registry.contracts[contract_id]
    except KeyError as exc:
        raise HTTPException(404, "Contract not found") from exc
    router = None
    if tissue_id is not None:
        try:
            router = registry.tissues[tissue_id]
        except KeyError as exc:
            raise HTTPException(404, "Tissue not found") from exc
        try:
            router.validate_contract(contract)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    adapters = {system_id: MemoryAdapter(system_id) for system_id in registry.profiles}
    simulator = IntegrationSimulator(registry.profiles, adapters, ledger=ledger, router=router)
    return simulator.process(contract, event, simulate=not execute)


@app.get("/events/{event_id}")
def event_history(event_id: str) -> dict[str, object]:
    try:
        return ledger.export_event_history(event_id)
    except KeyError as exc:
        raise HTTPException(404, "Event not found") from exc


@app.post("/drifts", response_model=DriftObservation)
def register_drift(observation: DriftObservation) -> DriftObservation:
    repair_ledger.record_drift(observation)
    return observation


@app.post("/repairs/propose", response_model=RepairCandidate)
def propose_repair(observation: DriftObservation) -> RepairCandidate:
    try:
        contract = registry.contracts[observation.contract_id]
    except KeyError as exc:
        raise HTTPException(404, "Contract not found") from exc
    try:
        candidate = repair_generator.propose(observation, contract, registry.profiles)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    registry.register_repair(candidate)
    repair_ledger.record_drift(observation)
    repair_ledger.save_repair(candidate)
    return candidate


@app.get("/repairs", response_model=list[RepairCandidate])
def list_repairs() -> list[RepairCandidate]:
    return list(registry.repairs.values())


@app.post("/repairs/{repair_id}/verify", response_model=RepairCandidate)
def verify_repair(repair_id: str, request: RepairVerificationRequest) -> RepairCandidate:
    try:
        candidate = registry.repairs[repair_id]
        contract = registry.contracts[candidate.contract_id]
        tissue = registry.tissues[request.tissue_id]
    except KeyError as exc:
        raise HTTPException(404, "Repair, contract, or tissue not found") from exc
    try:
        candidate, _contract, _profiles = repair_verifier.verify(
            candidate,
            contract,
            registry.profiles,
            tissue,
            request.events,
            impacted_event_ids=set(request.impacted_event_ids),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    registry.register_repair(candidate)
    repair_ledger.save_repair(candidate)
    return candidate


@app.post("/repairs/{repair_id}/approve", response_model=RepairCandidate)
def approve_repair(
    repair_id: str, approver: str = Query(...), reason: str = Query(default="")
) -> RepairCandidate:
    try:
        candidate = registry.repairs[repair_id]
    except KeyError as exc:
        raise HTTPException(404, "Repair not found") from exc
    try:
        candidate = RepairApprovalService.approve(candidate, approver, reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    registry.register_repair(candidate)
    repair_ledger.save_repair(candidate)
    return candidate


@app.post("/repairs/{repair_id}/sign", response_model=RepairCandidate)
def sign_repair(repair_id: str, key_id: str = Query(default="platform")) -> RepairCandidate:
    signing_key = os.environ.get("DIFOUNDRY_REPAIR_SIGNING_KEY")
    if not signing_key:
        raise HTTPException(503, "DIFOUNDRY_REPAIR_SIGNING_KEY is not configured")
    try:
        candidate = registry.repairs[repair_id]
    except KeyError as exc:
        raise HTTPException(404, "Repair not found") from exc
    try:
        candidate = RepairSigner.sign(candidate, signing_key.encode("utf-8"), key_id=key_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    registry.register_repair(candidate)
    repair_ledger.save_repair(candidate)
    return candidate


@app.post("/repairs/{repair_id}/deploy", response_model=RepairDeployment)
def deploy_repair(
    repair_id: str, tissue_id: str = Query(...), output_dir: str | None = Query(default=None)
) -> RepairDeployment:
    signing_key = os.environ.get("DIFOUNDRY_REPAIR_SIGNING_KEY")
    if not signing_key:
        raise HTTPException(503, "DIFOUNDRY_REPAIR_SIGNING_KEY is not configured")
    try:
        candidate = registry.repairs[repair_id]
        contract = registry.contracts[candidate.contract_id]
        tissue = registry.tissues[tissue_id]
    except KeyError as exc:
        raise HTTPException(404, "Repair, contract, or tissue not found") from exc
    try:
        deployment, repaired_contract, repaired_profiles, repaired_tissue = repair_deployer.deploy(
            candidate, contract, registry.profiles, tissue, signing_key.encode("utf-8"), output_dir=output_dir
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    registry.register_contract(repaired_contract)
    for profile in repaired_profiles.values():
        registry.register_profile(profile)
    registry.register_tissue(repaired_tissue)
    registry.register_deployment(deployment)
    repair_ledger.record_deployment(deployment)
    repair_ledger.save_repair(candidate)
    return deployment


@app.get("/quarantines")
def list_quarantines(ownership_key: str | None = Query(default=None)) -> list[dict[str, object]]:
    rows = repair_ledger.pending_quarantines(ownership_key)
    for row in rows:
        row.pop("event_json", None)
        row.pop("result_json", None)
    return rows


@app.put("/nervous/policy", response_model=GlobalPolicySet)
def set_nervous_policy(policy: GlobalPolicySet) -> GlobalPolicySet:
    nervous_system.policy_engine = GlobalPolicyEngine(policy)
    return policy


@app.post("/nervous/daughters", response_model=DaughterRegistration)
def register_nervous_daughter(request: DaughterRuntimeRequest) -> DaughterRegistration:
    profile_map = {profile.system_id: profile for profile in request.profiles}
    for profile in request.profiles:
        registry.register_profile(profile)
    registry.register_contract(request.contract)
    tissue = DendritronRoutingTissue.from_contract(request.contract)
    adapters = {system_id: MemoryAdapter(system_id) for system_id in profile_map}
    runtime = DaughterRuntime(
        request.registration, request.contract, profile_map, adapters, tissue
    )
    try:
        nervous_system.register_daughter(runtime)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    registry.register_tissue(tissue)
    return request.registration


@app.get("/nervous/daughters", response_model=list[DaughterRegistration])
def list_nervous_daughters() -> list[DaughterRegistration]:
    return [runtime.registration for _, runtime in sorted(nervous_system.daughters.items())]


@app.post("/nervous/workflows", response_model=CoordinationWorkflow)
def register_nervous_workflow(workflow: CoordinationWorkflow) -> CoordinationWorkflow:
    try:
        nervous_system.register_workflow(workflow)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return workflow


@app.get("/nervous/workflows", response_model=list[CoordinationWorkflow])
def list_nervous_workflows() -> list[CoordinationWorkflow]:
    return [workflow for _, workflow in sorted(nervous_system.workflows.items())]


@app.post("/nervous/coordinate/{workflow_id}", response_model=CoordinationResult)
def coordinate_nervous_workflow(
    workflow_id: str,
    event: CanonicalEvent,
    execute: bool = Query(default=False),
    approvals: list[str] = Query(default=[]),
) -> CoordinationResult:
    try:
        return nervous_system.coordinate(
            workflow_id, event, approvals=set(approvals), simulate=not execute
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/nervous/topology")
def nervous_topology() -> dict[str, object]:
    return nervous_system.topology()


@app.get("/nervous/lineage/{root_event_id}")
def nervous_lineage(root_event_id: str) -> dict[str, object]:
    return nervous_system.ledger.export_lineage(root_event_id)
