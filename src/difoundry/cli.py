from __future__ import annotations

import argparse
from pathlib import Path

from .adapters.memory import MemoryAdapter
from .artifacts import DaughterBundleWriter, verify_artifact_manifest
from .composition import DaughterComposer
from .discovery import DiscoveryService
from .io import dump_json, dump_yaml, load_data, load_model
from .ledger import EventLedger
from .intelligence import (
    InheritancePolicy,
    InheritedSemanticMatcher,
    IntelligenceExporter,
    IntelligenceRegistry,
    IntelligenceStore,
)
from .models import (
    CanonicalEvent,
    CompositionRequest,
    CompositionResult,
    DiscoverySource,
    IntegrationContract,
    SystemProfile,
    TargetIntent,
)
from .phase2_benchmark import run_phase2_benchmark
from .phase3_benchmark import run_phase3_benchmark
from .phase4_benchmark import run_phase4_benchmark
from .phase5_benchmark import run_phase5_benchmark
from .phase6_benchmark import run_phase6_benchmark
from .repair import (
    DriftObservation,
    RepairApprovalService,
    RepairDeploymentManager,
    RepairGenerator,
    RepairSigner,
    RepairStore,
    RepairVerifier,
)
from .simulator import IntegrationSimulator
from .tissue import (
    DendritronRoutingTissue,
    DendritronTissueConfig,
    RouterTrainingSet,
    TissueStore,
    TissueIntegrityError,
)
from .validation import ContractValidator
from .nervous import NervousTopologyStore


def _load_profiles(profile_paths: list[str]) -> dict[str, SystemProfile]:
    profiles = [load_model(path, SystemProfile) for path in profile_paths]
    return {profile.system_id: profile for profile in profiles}


def _build_simulator(
    profile_paths: list[str], ledger_path: str, tissue_path: str | None = None
) -> tuple[IntegrationSimulator, dict[str, SystemProfile]]:
    profile_map = _load_profiles(profile_paths)
    adapters = {profile.system_id: MemoryAdapter(profile.system_id) for profile in profile_map.values()}
    router = DendritronRoutingTissue.load(tissue_path) if tissue_path else None
    return IntegrationSimulator(profile_map, adapters, EventLedger(ledger_path), router=router), profile_map


def _parse_target(value: str) -> TargetIntent:
    parts = value.split(":", 2)
    return TargetIntent(
        target_system_id=parts[0],
        target_object_id=parts[1] if len(parts) > 1 and parts[1] else None,
        operation_id=parts[2] if len(parts) > 2 and parts[2] else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="difoundry", description="Dendritron Integration Foundry 0.7.2 engineering CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("providers", help="List installed discovery providers")

    verify_bundle = subparsers.add_parser("verify-bundle", help="Verify a generated daughter artifact manifest")
    verify_bundle.add_argument("--bundle", required=True)

    discover = subparsers.add_parser("discover", help="Discover a System Profile from an external specification")
    discover.add_argument("--input", required=True)
    discover.add_argument("--format", default="auto", help="Discovery format; provider plugins may add values")
    discover.add_argument("--system-id")
    discover.add_argument("--name")
    discover.add_argument("--base-url")
    discover.add_argument("--output", help="Write the discovered profile as YAML")

    compose = subparsers.add_parser("compose", help="Compose a daughter scaffold from discovered System Profiles")
    compose.add_argument("--profile", action="append", required=True)
    compose.add_argument("--name", required=True)
    compose.add_argument("--source-system", required=True)
    compose.add_argument("--source-object")
    compose.add_argument("--event-type", default="*")
    compose.add_argument("--target", action="append", required=True, help="system[:object[:operation]]; repeat for fan-out")
    compose.add_argument("--minimum-score", type=float, default=0.58)
    compose.add_argument("--review-below", type=float, default=0.78)
    compose.add_argument("--output", required=True, help="Output daughter bundle directory")
    compose.add_argument("--intelligence-pack", action="append", default=[], help="Hash-bound inherited intelligence pack; repeat to merge")
    compose.add_argument("--minimum-origins", type=int, default=2)

    validate = subparsers.add_parser("validate", help="Validate profiles and a contract")
    validate.add_argument("--profile", action="append", required=True)
    validate.add_argument("--contract", required=True)

    tissue_init = subparsers.add_parser("tissue-init", help="Create a persistent Dendritron ownership tissue")
    tissue_init.add_argument("--contract", required=True)
    tissue_init.add_argument("--output", required=True)
    tissue_init.add_argument("--top-k", type=int, default=2)
    tissue_init.add_argument("--novelty-threshold", type=float, default=0.43)
    tissue_init.add_argument("--ownership-margin", type=float, default=0.04)
    tissue_init.add_argument("--spawn-below", type=float, default=0.72)
    tissue_init.add_argument("--learning-rate", type=float, default=0.25)
    tissue_init.add_argument("--max-specialists", type=int, default=8)

    tissue_train = subparsers.add_parser("tissue-train", help="Train only named daughter branches")
    tissue_train.add_argument("--contract", required=True)
    tissue_train.add_argument("--tissue", required=True)
    tissue_train.add_argument("--examples", required=True)
    tissue_train.add_argument("--output")

    tissue_inspect = subparsers.add_parser("tissue-inspect", help="Inspect persistent ownership and local health")
    tissue_inspect.add_argument("--tissue", required=True)

    tissue_verify = subparsers.add_parser("tissue-verify", help="Verify a tissue file's bound state hash")
    tissue_verify.add_argument("--tissue", required=True)

    benchmark = subparsers.add_parser("benchmark-phase2", help="Run Phase 2 routing, novelty, and isolation gates")
    benchmark.add_argument("--output", default="reports/phase2-benchmark.json")

    benchmark3 = subparsers.add_parser("benchmark-phase3", help="Run Phase 3 drift, repair, deployment, and recovery gates")
    benchmark3.add_argument("--output", default="reports/phase3-benchmark.json")

    benchmark4 = subparsers.add_parser("benchmark-phase4", help="Run Phase 4 inheritance, privacy, consensus, and transfer gates")
    benchmark4.add_argument("--output", default="reports/phase4-benchmark.json")

    benchmark5 = subparsers.add_parser("benchmark-phase5", help="Run Phase 5 multi-system coordination, policy, lineage, and isolation gates")
    benchmark5.add_argument("--output", default="reports/phase5-benchmark.json")

    benchmark6 = subparsers.add_parser("benchmark-phase6", help="Run Phase 6 production security, UI, queue, and monitoring gates")
    benchmark6.add_argument("--output", default="reports/phase6-benchmark.json")

    nervous_verify = subparsers.add_parser("nervous-verify", help="Verify a hash-bound multi-system nervous topology bundle")
    nervous_verify.add_argument("--topology", required=True)

    nervous_inspect = subparsers.add_parser("nervous-inspect", help="Inspect a verified multi-system nervous topology bundle")
    nervous_inspect.add_argument("--topology", required=True)

    intelligence_verify = subparsers.add_parser("intelligence-verify", help="Verify a hash-bound inherited intelligence pack")
    intelligence_verify.add_argument("--pack", required=True)

    intelligence_inspect = subparsers.add_parser("intelligence-inspect", help="Inspect inherited intelligence eligibility and support")
    intelligence_inspect.add_argument("--pack", required=True)
    intelligence_inspect.add_argument("--minimum-origins", type=int, default=2)

    intelligence_merge = subparsers.add_parser("intelligence-merge", help="Merge and consensus-filter inherited intelligence packs")
    intelligence_merge.add_argument("--pack", action="append", required=True)
    intelligence_merge.add_argument("--output", required=True)
    intelligence_merge.add_argument("--minimum-origins", type=int, default=2)
    intelligence_merge.add_argument("--include-quarantined", action="store_true")

    intelligence_export = subparsers.add_parser("intelligence-export", help="Export sanitized patterns from a verified composition result")
    intelligence_export.add_argument("--composition", required=True)
    intelligence_export.add_argument("--profile", action="append", required=True)
    intelligence_export.add_argument("--origin-ref", required=True)
    intelligence_export.add_argument("--output", required=True)

    repair_propose = subparsers.add_parser("repair-propose", help="Generate a bounded repair from an attributed drift observation")
    repair_propose.add_argument("--contract", required=True)
    repair_propose.add_argument("--profile", action="append", required=True)
    repair_propose.add_argument("--drift", required=True)
    repair_propose.add_argument("--output", required=True)

    repair_verify = subparsers.add_parser("repair-verify", help="Replay impacted events and regress unrelated paths")
    repair_verify.add_argument("--contract", required=True)
    repair_verify.add_argument("--profile", action="append", required=True)
    repair_verify.add_argument("--tissue", required=True)
    repair_verify.add_argument("--candidate", required=True)
    repair_verify.add_argument("--event", action="append", required=True)
    repair_verify.add_argument("--impacted-event-id", action="append", default=[])
    repair_verify.add_argument("--output")

    repair_approve = subparsers.add_parser("repair-approve", help="Approve a successfully verified repair")
    repair_approve.add_argument("--candidate", required=True)
    repair_approve.add_argument("--approver", required=True)
    repair_approve.add_argument("--reason", default="")
    repair_approve.add_argument("--output")

    repair_sign = subparsers.add_parser("repair-sign", help="Sign an approved repair using an external key file")
    repair_sign.add_argument("--candidate", required=True)
    repair_sign.add_argument("--key-file", required=True)
    repair_sign.add_argument("--key-id", default="local")
    repair_sign.add_argument("--output")

    repair_deploy = subparsers.add_parser("repair-deploy", help="Atomically deploy a signed bounded repair")
    repair_deploy.add_argument("--contract", required=True)
    repair_deploy.add_argument("--profile", action="append", required=True)
    repair_deploy.add_argument("--tissue", required=True)
    repair_deploy.add_argument("--candidate", required=True)
    repair_deploy.add_argument("--key-file", required=True)
    repair_deploy.add_argument("--output", required=True)

    simulate = subparsers.add_parser("simulate", help="Simulate an event against a contract")
    simulate.add_argument("--profile", action="append", required=True)
    simulate.add_argument("--contract", required=True)
    simulate.add_argument("--event", required=True)
    simulate.add_argument("--ledger", default="phase2-ledger.sqlite3")
    simulate.add_argument("--tissue", help="Use a persisted Dendritron routing tissue")
    simulate.add_argument("--execute", action="store_true", help="Commit to selected adapters instead of dry-run")

    replay = subparsers.add_parser("replay", help="Replay a previously recorded event")
    replay.add_argument("--profile", action="append", required=True)
    replay.add_argument("--contract", required=True)
    replay.add_argument("--event-id", required=True)
    replay.add_argument("--ledger", default="phase2-ledger.sqlite3")
    replay.add_argument("--tissue", help="Use a persisted Dendritron routing tissue")

    args = parser.parse_args()

    if args.command == "providers":
        print(dump_json({"formats": DiscoveryService().formats()}))
        return

    if args.command == "verify-bundle":
        report = verify_artifact_manifest(args.bundle)
        print(dump_json(report))
        if not report["valid"]:
            raise SystemExit(1)
        return

    if args.command == "discover":
        source = DiscoverySource(
            format=args.format,
            document=load_data(args.input),
            system_id=args.system_id,
            name=args.name,
            base_url=args.base_url,
            metadata={"input_path": str(Path(args.input))},
        )
        result = DiscoveryService().discover(source)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(dump_yaml(result.profile), encoding="utf-8")
        print(dump_json(result))
        return

    if args.command == "compose":
        profile_map = _load_profiles(args.profile)
        request = CompositionRequest(
            name=args.name,
            source_system_id=args.source_system,
            source_object_id=args.source_object,
            event_type=args.event_type,
            targets=[_parse_target(value) for value in args.target],
            minimum_mapping_score=args.minimum_score,
            require_review_below=args.review_below,
        )
        active_composer = DaughterComposer()
        if args.intelligence_pack:
            intelligence_registry = IntelligenceRegistry(
                policy=InheritancePolicy(minimum_distinct_origins=args.minimum_origins)
            )
            for pack_path in args.intelligence_pack:
                intelligence_registry.import_pack(IntelligenceStore.load(pack_path))
            active_composer = DaughterComposer(InheritedSemanticMatcher(intelligence_registry))
        result = active_composer.compose(request, profile_map)
        bundle = DaughterBundleWriter().write(args.output, result, profile_map)
        print(
            dump_json(
                {
                    "bundle": str(bundle),
                    "daughter_id": result.daughter_manifest.daughter_id,
                    "contract_id": result.contract.contract_id,
                    "ready_for_verification": result.ready_for_verification,
                    "questions": [question.model_dump(mode="json") for question in result.questions],
                    "warnings": result.warnings,
                }
            )
        )
        return

    if args.command == "validate":
        contract = load_model(args.contract, IntegrationContract)
        profile_map = _load_profiles(args.profile)
        systems = {contract.trigger.system_id} | {
            action.target_system_id for route in contract.routes for action in route.actions
        }
        report = ContractValidator().validate(contract, profile_map)
        print(
            dump_json(
                {
                    "valid": report.valid,
                    "contract_id": contract.contract_id,
                    "systems": sorted(systems),
                    "errors": report.errors,
                    "warnings": report.warnings,
                }
            )
        )
        if not report.valid:
            raise SystemExit(1)
        return

    if args.command == "tissue-init":
        contract = load_model(args.contract, IntegrationContract)
        config = DendritronTissueConfig(
            top_k_specialists=args.top_k,
            novelty_threshold=args.novelty_threshold,
            ownership_margin=args.ownership_margin,
            spawn_below_similarity=args.spawn_below,
            learning_rate=args.learning_rate,
            max_specialists_per_branch=args.max_specialists,
        )
        tissue = DendritronRoutingTissue.from_contract(contract, config, store_path=args.output)
        print(dump_json(tissue.summary()))
        return

    if args.command == "tissue-train":
        contract = load_model(args.contract, IntegrationContract)
        tissue = DendritronRoutingTissue.load(args.tissue)
        if args.output:
            tissue.store_path = Path(args.output)
        training_set = load_model(args.examples, RouterTrainingSet)
        report = tissue.train(contract, training_set)
        tissue.save(args.output or args.tissue)
        print(dump_json(report))
        return

    if args.command == "tissue-inspect":
        print(dump_json(DendritronRoutingTissue.load(args.tissue).summary()))
        return

    if args.command == "tissue-verify":
        try:
            state = TissueStore.load(args.tissue)
        except (TissueIntegrityError, OSError, ValueError) as exc:
            print(dump_json({"valid": False, "error": str(exc)}))
            raise SystemExit(1) from exc
        print(dump_json({"valid": True, "tissue_id": state.tissue_id, "version": state.version}))
        return

    if args.command == "benchmark-phase2":
        report = run_phase2_benchmark(args.output)
        print(dump_json(report))
        if not report["gate_pass"]:
            raise SystemExit(1)
        return

    if args.command == "benchmark-phase3":
        report = run_phase3_benchmark(args.output)
        print(dump_json(report))
        if not report["gate_pass"]:
            raise SystemExit(1)
        return

    if args.command == "benchmark-phase4":
        report = run_phase4_benchmark(args.output)
        print(dump_json(report))
        if not report["gate_pass"]:
            raise SystemExit(1)
        return

    if args.command == "benchmark-phase5":
        report = run_phase5_benchmark(args.output)
        print(dump_json(report))
        if not report["gate_pass"]:
            raise SystemExit(1)
        return

    if args.command == "benchmark-phase6":
        report = run_phase6_benchmark(args.output)
        print(dump_json(report))
        if not report["gate_pass"]:
            raise SystemExit(1)
        return

    if args.command == "nervous-verify":
        try:
            bundle = NervousTopologyStore.load(args.topology)
        except (ValueError, OSError) as exc:
            print(dump_json({"valid": False, "error": str(exc)}))
            raise SystemExit(1) from exc
        print(dump_json({
            "valid": True,
            "topology_hash": bundle.topology_hash,
            "daughters": len(bundle.daughters),
            "workflows": len(bundle.workflows),
            "policy_id": bundle.policy.policy_id,
        }))
        return

    if args.command == "nervous-inspect":
        bundle = NervousTopologyStore.load(args.topology)
        print(dump_json(bundle))
        return

    if args.command == "intelligence-verify":
        try:
            pack = IntelligenceStore.load(args.pack)
        except (ValueError, OSError) as exc:
            print(dump_json({"valid": False, "error": str(exc)}))
            raise SystemExit(1) from exc
        print(dump_json({"valid": True, "pack_id": pack.pack_id, "pack_hash": pack.pack_hash, "patterns": len(pack.patterns)}))
        return

    if args.command == "intelligence-inspect":
        pack = IntelligenceStore.load(args.pack)
        intelligence_registry = IntelligenceRegistry(
            policy=InheritancePolicy(minimum_distinct_origins=args.minimum_origins)
        )
        intelligence_registry.import_pack(pack)
        print(dump_json({
            "pack_id": pack.pack_id,
            "pack_hash": pack.pack_hash,
            "registered": len(intelligence_registry.patterns),
            "eligible": [item.model_dump(mode="json") for item in intelligence_registry.eligible()],
            "quarantined": [item.model_dump(mode="json") for item in intelligence_registry.quarantined()],
        }))
        return

    if args.command == "intelligence-merge":
        intelligence_registry = IntelligenceRegistry(
            policy=InheritancePolicy(minimum_distinct_origins=args.minimum_origins)
        )
        for pack_path in args.pack:
            intelligence_registry.import_pack(IntelligenceStore.load(pack_path))
        merged = intelligence_registry.pack(include_quarantined=args.include_quarantined, metadata={"merged_from": len(args.pack)})
        IntelligenceStore.save(args.output, merged)
        print(dump_json({
            "output": args.output,
            "pack_hash": merged.pack_hash,
            "patterns": len(merged.patterns),
            "eligible": len(intelligence_registry.eligible()),
            "quarantined": len(intelligence_registry.quarantined()),
        }))
        return

    if args.command == "intelligence-export":
        composition = load_model(args.composition, CompositionResult)
        profiles = _load_profiles(args.profile)
        intelligence_registry = IntelligenceRegistry(
            policy=InheritancePolicy(minimum_distinct_origins=1)
        )
        for pattern in IntelligenceExporter().from_composition(composition, profiles, args.origin_ref):
            intelligence_registry.add(pattern)
        pack = intelligence_registry.pack(include_quarantined=True, metadata={"composition_id": composition.composition_id})
        IntelligenceStore.save(args.output, pack)
        print(dump_json({"output": args.output, "pack_hash": pack.pack_hash, "patterns": len(pack.patterns)}))
        return

    if args.command == "repair-propose":
        contract = load_model(args.contract, IntegrationContract)
        profiles = _load_profiles(args.profile)
        drift = load_model(args.drift, DriftObservation)
        candidate = RepairGenerator().propose(drift, contract, profiles)
        RepairStore.save(args.output, candidate)
        print(dump_json(candidate))
        return

    if args.command == "repair-verify":
        contract = load_model(args.contract, IntegrationContract)
        profiles = _load_profiles(args.profile)
        tissue = DendritronRoutingTissue.load(args.tissue)
        candidate = RepairStore.load(args.candidate)
        events = [load_model(path, CanonicalEvent) for path in args.event]
        candidate, _repaired_contract, _repaired_profiles = RepairVerifier().verify(
            candidate, contract, profiles, tissue, events, impacted_event_ids=set(args.impacted_event_id)
        )
        destination = args.output or args.candidate
        RepairStore.save(destination, candidate)
        print(dump_json(candidate.verification))
        if candidate.verification is None or not candidate.verification.passed:
            raise SystemExit(1)
        return

    if args.command == "repair-approve":
        candidate = RepairStore.load(args.candidate)
        candidate = RepairApprovalService.approve(candidate, args.approver, args.reason)
        destination = args.output or args.candidate
        RepairStore.save(destination, candidate)
        print(dump_json(candidate))
        return

    if args.command == "repair-sign":
        candidate = RepairStore.load(args.candidate)
        key = Path(args.key_file).read_bytes()
        candidate = RepairSigner.sign(candidate, key, key_id=args.key_id)
        destination = args.output or args.candidate
        RepairStore.save(destination, candidate)
        print(dump_json({"repair_id": candidate.repair_id, "status": candidate.status, "signature": candidate.signature}))
        return

    if args.command == "repair-deploy":
        contract = load_model(args.contract, IntegrationContract)
        profiles = _load_profiles(args.profile)
        tissue = DendritronRoutingTissue.load(args.tissue)
        candidate = RepairStore.load(args.candidate)
        key = Path(args.key_file).read_bytes()
        deployment, _contract, _profiles, _tissue = RepairDeploymentManager().deploy(
            candidate, contract, profiles, tissue, key, output_dir=args.output
        )
        print(dump_json(deployment))
        return

    simulator, _profiles = _build_simulator(args.profile, args.ledger, getattr(args, "tissue", None))
    contract = load_model(args.contract, IntegrationContract)
    if args.command == "simulate":
        event = load_model(args.event, CanonicalEvent)
        result = simulator.process(contract, event, simulate=not args.execute)
    else:
        result = simulator.replay(args.event_id, contract, simulate=True)
    print(dump_json(result))


if __name__ == "__main__":
    main()
