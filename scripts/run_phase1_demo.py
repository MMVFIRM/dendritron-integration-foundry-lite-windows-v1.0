from __future__ import annotations

import argparse
import json
from pathlib import Path

from difoundry.adapters.memory import MemoryAdapter
from difoundry.artifacts import DaughterBundleWriter
from difoundry.composition import DaughterComposer
from difoundry.discovery import DiscoveryService
from difoundry.io import dump_yaml, load_data
from difoundry.ledger import EventLedger
from difoundry.models import CanonicalEvent, CompositionRequest, DiscoverySource, TargetIntent
from difoundry.simulator import IntegrationSimulator

ROOT = Path(__file__).parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the complete Phase 1 discovery and composition demonstration")
    parser.add_argument("--output", default="build/phase1-demo")
    args = parser.parse_args()
    output = Path(args.output)
    profiles_dir = output / "discovered-profiles"
    daughter_dir = output / "customer-distribution-daughter"
    profiles_dir.mkdir(parents=True, exist_ok=True)

    service = DiscoveryService()
    sources = [
        ("crm-openapi.yaml", "atlas_crm"),
        ("erp.sql", "atlas_erp"),
        ("analytics-asyncapi.yaml", "analytics_bus"),
    ]
    profiles = {}
    discoveries = []
    for filename, system_id in sources:
        result = service.discover(
            DiscoverySource(
                format="auto",
                document=load_data(ROOT / "examples" / "discovery" / filename),
                system_id=system_id,
                metadata={"demo_input": filename},
            )
        )
        profiles[result.profile.system_id] = result.profile
        discoveries.append(result)
        (profiles_dir / f"{system_id}.yaml").write_text(dump_yaml(result.profile), encoding="utf-8")

    composition = DaughterComposer().compose(
        CompositionRequest(
            name="Customer distribution daughter",
            source_system_id="atlas_crm",
            source_object_id="customer",
            event_type="updated",
            targets=[
                TargetIntent(target_system_id="atlas_erp", target_object_id="account", operation_id="insert_account"),
                TargetIntent(
                    target_system_id="analytics_bus",
                    target_object_id="customer_snapshot",
                    operation_id="publish_customer_snapshot",
                ),
            ],
        ),
        profiles,
    )
    DaughterBundleWriter().write(daughter_dir, composition, profiles)

    event = CanonicalEvent(
        event_id="evt_phase1_demo",
        source_system="atlas_crm",
        source_object="customer",
        event_type="updated",
        source_record_id="cust_1001",
        idempotency_key="atlas_crm:customer:cust_1001:v7",
        payload={
            "id": "cust_1001",
            "company_name": "Atlas Fabrication LLC",
            "status": "ACTIVE",
            "primary_email": "ops@atlas.example",
            "updated_at": "2026-07-26T18:00:00-07:00",
        },
    )
    adapters = {system_id: MemoryAdapter(system_id) for system_id in profiles}
    simulation = IntegrationSimulator(profiles, adapters, EventLedger(output / "demo-ledger.sqlite3")).process(
        composition.contract, event
    )
    summary = {
        "discovered_systems": [
            {
                "system_id": result.profile.system_id,
                "provider": result.provider,
                "protocol": result.profile.protocol,
                "source_hash": result.source_hash,
                "objects": len(result.profile.objects),
                "operations": len(result.profile.operations),
            }
            for result in discoveries
        ],
        "daughter_id": composition.daughter_manifest.daughter_id,
        "ready_for_verification": composition.ready_for_verification,
        "questions": [question.model_dump(mode="json") for question in composition.questions],
        "simulation_status": simulation.status,
        "planned_actions": [
            {
                "target_system_id": action.target_system_id,
                "operation_id": action.operation_id,
                "payload": action.payload,
                "certified": action.certified,
            }
            for action in (simulation.plan.actions if simulation.plan else [])
        ],
        "bundle": str(daughter_dir),
    }
    (output / "demo-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
