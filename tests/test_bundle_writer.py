from pathlib import Path

from difoundry.artifacts import DaughterBundleWriter
from difoundry.composition import DaughterComposer
from difoundry.discovery import DiscoveryService
from difoundry.io import load_data, load_model
from difoundry.models import CompositionRequest, DiscoverySource, IntegrationContract, TargetIntent
from difoundry.tissue import DendritronRoutingTissue, TissueStore

ROOT = Path(__file__).parents[1]


def test_bundle_writer_emits_reproducible_artifact_tree(tmp_path):
    service = DiscoveryService()
    source = service.discover(
        DiscoverySource(format="auto", document=load_data(ROOT / "examples/discovery/crm-openapi.yaml"), system_id="atlas_crm")
    ).profile
    target = service.discover(
        DiscoverySource(format="auto", document=load_data(ROOT / "examples/discovery/erp.sql"), system_id="atlas_erp")
    ).profile
    profiles = {source.system_id: source, target.system_id: target}
    composition = DaughterComposer().compose(
        CompositionRequest(
            name="CRM to ERP",
            source_system_id="atlas_crm",
            source_object_id="customer",
            targets=[TargetIntent(target_system_id="atlas_erp", target_object_id="account", operation_id="insert_account")],
        ),
        profiles,
    )
    root = DaughterBundleWriter().write(tmp_path / "bundle", composition, profiles)
    assert (root / "integration-contract.yaml").exists()
    assert (root / "daughter-manifest.yaml").exists()
    assert (root / "verification/verification-bundle.yaml").exists()
    assert len(list((root / "semantic").glob("*.yaml"))) == 1
    assert len(list((root / "profiles").glob("*.yaml"))) == 2
    assert (root / "runtime/dendritron-tissue.json").exists()
    assert (root / "runtime/training-examples.yaml").exists()
    assert (root / "repair/repair-policy.yaml").exists()
    assert (root / "repair/README.md").exists()
    assert (root / "nervous/daughter-registration.yaml").exists()
    assert (root / "nervous/coordination-boundary.yaml").exists()
    assert (root / "nervous/README.md").exists()
    tissue_state = TissueStore.load(root / "runtime/dendritron-tissue.json")
    generated_contract = load_model(root / "integration-contract.yaml", IntegrationContract)
    DendritronRoutingTissue(tissue_state).validate_contract(generated_contract)
    assert (root / "artifact-manifest.json").exists()
    manifest = (root / "artifact-manifest.json").read_text()
    assert "integration-contract.yaml" in manifest
    assert "READY FOR VERIFICATION" in (root / "README.md").read_text()


def test_bundle_manifest_detects_tampering(tmp_path):
    from difoundry.artifacts import verify_artifact_manifest

    service = DiscoveryService()
    source = service.discover(
        DiscoverySource(format="auto", document=load_data(ROOT / "examples/discovery/crm-openapi.yaml"), system_id="atlas_crm")
    ).profile
    target = service.discover(
        DiscoverySource(format="auto", document=load_data(ROOT / "examples/discovery/erp.sql"), system_id="atlas_erp")
    ).profile
    profiles = {source.system_id: source, target.system_id: target}
    composition = DaughterComposer().compose(
        CompositionRequest(
            name="Tamper proof daughter",
            source_system_id="atlas_crm",
            source_object_id="customer",
            targets=[TargetIntent(target_system_id="atlas_erp", target_object_id="account", operation_id="insert_account")],
        ),
        profiles,
    )
    root = DaughterBundleWriter().write(tmp_path / "bundle", composition, profiles)
    assert verify_artifact_manifest(root)["valid"] is True
    contract = root / "integration-contract.yaml"
    contract.write_text(contract.read_text() + "\n# modified\n")
    report = verify_artifact_manifest(root)
    assert report["valid"] is False
    assert any("integration-contract.yaml" in error for error in report["errors"])
