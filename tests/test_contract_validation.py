from pathlib import Path

from difoundry.io import load_model
from difoundry.models import IntegrationContract, SystemProfile
from difoundry.validation import ContractValidator

ROOT = Path(__file__).parents[1]


def test_cross_artifact_contract_validation():
    profiles = {
        profile.system_id: profile
        for profile in [
            load_model(ROOT / "examples/source_system.yaml", SystemProfile),
            load_model(ROOT / "examples/target_system.yaml", SystemProfile),
            load_model(ROOT / "examples/analytics_system.yaml", SystemProfile),
        ]
    }
    contract = load_model(ROOT / "examples/contract.yaml", IntegrationContract)
    report = ContractValidator().validate(contract, profiles)
    assert report.valid is True
    assert report.errors == []
