from __future__ import annotations

import json
import sys
from pathlib import Path

from difoundry.cli import main


def invoke(monkeypatch, capsys, *args: str) -> str:
    monkeypatch.setattr(sys, "argv", ["difoundry", *args])
    main()
    return capsys.readouterr().out


def test_cli_providers_and_discovery(monkeypatch, capsys, tmp_path: Path):
    providers = json.loads(invoke(monkeypatch, capsys, "providers"))
    assert "openapi" in providers["formats"]

    output = tmp_path / "profile.yaml"
    result = json.loads(invoke(
        monkeypatch,
        capsys,
        "discover",
        "--input", "examples/discovery/crm-openapi.yaml",
        "--format", "openapi",
        "--system-id", "cli-crm",
        "--name", "CLI CRM",
        "--output", str(output),
    ))
    assert result["profile"]["system_id"] == "cli-crm"
    assert output.exists()


def test_cli_validate_tissue_and_phase_benchmarks(monkeypatch, capsys, tmp_path: Path):
    validation = json.loads(invoke(
        monkeypatch,
        capsys,
        "validate",
        "--profile", "examples/source_system.yaml",
        "--profile", "examples/target_system.yaml",
        "--profile", "examples/analytics_system.yaml",
        "--contract", "examples/contract.yaml",
    ))
    assert validation["valid"] is True

    tissue = tmp_path / "tissue.json"
    initialized = json.loads(invoke(
        monkeypatch,
        capsys,
        "tissue-init",
        "--contract", "examples/phase2/contract.yaml",
        "--output", str(tissue),
    ))
    assert initialized["tissue_id"]
    verified = json.loads(invoke(monkeypatch, capsys, "tissue-verify", "--tissue", str(tissue)))
    assert verified["valid"] is True

    for phase in (2, 6):
        output = tmp_path / f"phase{phase}.json"
        report = json.loads(invoke(
            monkeypatch,
            capsys,
            f"benchmark-phase{phase}",
            "--output", str(output),
        ))
        assert report["gate_pass"] is True
        assert json.loads(output.read_text())["gate_pass"] is True
