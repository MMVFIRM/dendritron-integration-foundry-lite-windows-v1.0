from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from .models import CompositionResult, SystemProfile
from .tissue import DendritronRoutingTissue, RouterTrainingSet, TissueStore
from .repair import RepairPolicy
from .nervous import DaughterCapability, DaughterRegistration


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


class DaughterBundleWriter:
    def write(
        self,
        output_dir: str | Path,
        composition: CompositionResult,
        profiles: dict[str, SystemProfile],
    ) -> Path:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        (root / "profiles").mkdir(exist_ok=True)
        (root / "semantic").mkdir(exist_ok=True)
        (root / "verification").mkdir(exist_ok=True)
        (root / "runtime").mkdir(exist_ok=True)
        (root / "repair").mkdir(exist_ok=True)
        (root / "inheritance").mkdir(exist_ok=True)
        (root / "nervous").mkdir(exist_ok=True)

        referenced = {composition.daughter_manifest.source_system_id, *composition.daughter_manifest.target_system_ids}
        for system_id in sorted(referenced):
            profile = profiles[system_id]
            self._yaml(root / "profiles" / f"{system_id}.yaml", profile)
        self._yaml(root / "integration-contract.yaml", composition.contract)
        self._yaml(root / "daughter-manifest.yaml", composition.daughter_manifest)
        self._yaml(root / "verification" / "verification-bundle.yaml", composition.verification_bundle)
        for graph in composition.semantic_graphs:
            self._yaml(root / "semantic" / f"{graph.graph_id}.yaml", graph)
        tissue = DendritronRoutingTissue.from_contract(composition.contract)
        TissueStore.save(root / "runtime" / "dendritron-tissue.json", tissue.state)
        self._yaml(root / "runtime" / "training-examples.yaml", RouterTrainingSet(examples=[]))
        self._yaml(root / "repair" / "repair-policy.yaml", RepairPolicy())
        inheritance_summary = {
            "applied_pattern_hashes": sorted({
                pattern_hash
                for graph in composition.semantic_graphs
                for pattern_hash in graph.metadata.get("applied_pattern_hashes", [])
            }),
            "policy": {
                "tenant_payloads_allowed": False,
                "tenant_identifiers_allowed": False,
                "executable_code_allowed": False,
                "minimum_independent_origins": 2,
                "certification_bypass_allowed": False,
            },
        }
        self._json(root / "inheritance" / "inheritance-report.json", inheritance_summary)
        (root / "inheritance" / "README.md").write_text(
            "# Inherited Integration Intelligence\n\nOnly sanitized, hash-bound structural patterns may enter this daughter. Payloads, credentials, tenant identifiers, proprietary descriptions, and executable code are forbidden. Inherited evidence never bypasses schema, permission, policy, replay, or deployment gates.\n",
            encoding="utf-8",
        )
        (root / "repair" / "README.md").write_text(
            "# Bounded Repair Workspace\n\nRepair candidates, verification reports, approvals, signatures, deployments, and rollback artifacts belong here. Generated repairs cannot bypass contract validation, replay gates, approval policy, or signature verification.\n",
            encoding="utf-8",
        )
        registration = DaughterRegistration(
            daughter_id=composition.daughter_manifest.daughter_id,
            name=composition.daughter_manifest.name,
            contract_id=composition.contract.contract_id,
            capabilities=[
                DaughterCapability(
                    capability_id=route.route_id,
                    route_ids=[route.route_id],
                    event_types=[composition.contract.trigger.event_type],
                    source_objects=[composition.contract.trigger.object_type],
                    description=f"Owns route {route.route_id}",
                )
                for route in composition.contract.routes
            ],
            metadata={
                "contract_version": composition.contract.version,
                "global_policy_authority": False,
                "local_execution_authority": True,
            },
        )
        self._yaml(root / "nervous" / "daughter-registration.yaml", registration)
        self._yaml(
            root / "nervous" / "coordination-boundary.yaml",
            {
                "global_policy_may_select_daughter": True,
                "global_policy_may_override_local_contract": False,
                "global_policy_may_mutate_local_tissue": False,
                "daughter_may_mutate_other_daughters": False,
                "causal_lineage_required": True,
                "cross_daughter_gradient_allowed": False,
            },
        )
        (root / "nervous" / "README.md").write_text(
            "# Multi-System Nervous System Boundary\n\nThis daughter advertises owned capabilities to the Mother control plane. Global coordination may dispatch an event only through a registered capability and approved policy. The daughter retains its own contract, Dendritron tissue, adapter state, failure domain, and repair boundary.\n",
            encoding="utf-8",
        )
        self._json(
            root / "composition-report.json",
            {
                "composition_id": composition.composition_id,
                "ready_for_verification": composition.ready_for_verification,
                "warnings": composition.warnings,
                "questions": [_plain(question) for question in composition.questions],
                "generated_at": composition.generated_at.isoformat(),
            },
        )
        (root / "README.md").write_text(self._readme(composition), encoding="utf-8")
        self._write_artifact_manifest(root, composition)
        return root

    @staticmethod
    def _yaml(path: Path, value: Any) -> None:
        path.write_text(yaml.safe_dump(_plain(value), sort_keys=False, allow_unicode=True), encoding="utf-8")

    @staticmethod
    def _json(path: Path, value: Any) -> None:
        path.write_text(json.dumps(_plain(value), indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _write_artifact_manifest(root: Path, composition: CompositionResult) -> None:
        files: dict[str, dict[str, Any]] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == "artifact-manifest.json":
                continue
            data = path.read_bytes()
            files[str(path.relative_to(root))] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        manifest = {
            "daughter_id": composition.daughter_manifest.daughter_id,
            "composition_id": composition.composition_id,
            "generated_at": composition.generated_at.isoformat(),
            "files": files,
        }
        (root / "artifact-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _readme(composition: CompositionResult) -> str:
        status = "READY FOR VERIFICATION" if composition.ready_for_verification else "REVIEW REQUIRED"
        questions = "\n".join(f"- {question.prompt} ({question.reason})" for question in composition.questions) or "- None"
        return f"""# {composition.daughter_manifest.name}\n\nGenerated by Dendritron Integration Foundry Phase 5.\n\n## Status\n\n**{status}**\n\n## Included artifacts\n\n- Versioned System Profiles\n- Integration Contract scaffold\n- Semantic mapping graphs\n- Daughter Manifest\n- Verification Bundle\n- Composition report\n- SHA-256 artifact manifest\n- Bounded self-repair policy and workspace
- Privacy-bound inherited intelligence workspace\n- Multi-system nervous-system capability registration and trust boundary\n\n## Unresolved questions\n\n{questions}\n\nThis bundle is a scaffold. The included tissue owns the generated route graph but starts untrained. Production execution remains blocked until mandatory semantic questions, tissue training, verification gates, and repair-governance requirements are resolved.\n"""


def verify_artifact_manifest(bundle_dir: str | Path) -> dict[str, Any]:
    root = Path(bundle_dir)
    manifest_path = root / "artifact-manifest.json"
    if not manifest_path.exists():
        return {"valid": False, "errors": ["artifact-manifest.json is missing"], "files": {}}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    statuses: dict[str, dict[str, Any]] = {}
    expected_files = manifest.get("files", {})
    for relative, expected in expected_files.items():
        path = root / relative
        if not path.exists():
            errors.append(f"Missing artifact: {relative}")
            statuses[relative] = {"valid": False, "reason": "missing"}
            continue
        data = path.read_bytes()
        actual_hash = hashlib.sha256(data).hexdigest()
        actual_size = len(data)
        valid = actual_hash == expected.get("sha256") and actual_size == expected.get("bytes")
        statuses[relative] = {
            "valid": valid,
            "sha256": actual_hash,
            "bytes": actual_size,
        }
        if not valid:
            errors.append(f"Artifact hash or size mismatch: {relative}")
    actual_files = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.name != "artifact-manifest.json"
    }
    unexpected = sorted(actual_files - set(expected_files))
    for relative in unexpected:
        errors.append(f"Unexpected artifact not present in manifest: {relative}")
        statuses[relative] = {"valid": False, "reason": "unexpected"}
    return {
        "valid": not errors,
        "daughter_id": manifest.get("daughter_id"),
        "composition_id": manifest.get("composition_id"),
        "errors": errors,
        "files": statuses,
    }
