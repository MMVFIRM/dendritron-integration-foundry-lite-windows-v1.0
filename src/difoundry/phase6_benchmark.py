from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from .api import app
from .production.api import ProductionContext, configure_context
from .production.database import audit_events, secret_blobs
from .production.settings import ProductionSettings
from .production.worker import run_once


def _profile(system_id: str, name: str, writable: bool) -> dict[str, Any]:
    kind = "create" if writable else "read"
    return {
        "system_id": system_id, "name": name, "version": "1", "protocol": "rest",
        "base_url": f"https://{system_id}.example.com",
        "authentication": {"kind": "api_key", "secret_refs": {}, "config": {}},
        "objects": [{"object_id": "customer", "name": "Customer", "description": "", "identifiers": [], "metadata": {},
                     "fields": [{"name": "customer_name", "path": "customer_name", "data_type": "string", "required": True,
                                 "nullable": False, "description": "", "format": None, "enum": [], "relation": None, "metadata": {}},
                                {"name": "email", "path": "email", "data_type": "string", "required": False,
                                 "nullable": True, "description": "", "format": "email", "enum": [], "relation": None, "metadata": {}}]}],
        "operations": [{"operation_id": f"{kind}_customer", "method": "POST" if writable else "GET", "path": "/customers",
                        "description": "", "object_id": "customer", "operation_kind": kind,
                        "request_schema": {"type": "object", "properties": {"customer_name": {"type": "string"}, "email": {"type": "string"}}, "required": ["customer_name"]} if writable else {},
                        "response_schema": {"type": "object"}, "required_permissions": [f"customer.{kind}"],
                        "idempotency_supported": writable, "timeout_seconds": 30, "metadata": {}}],
        "metadata": {},
    }


def run_phase6_benchmark(output_path: str | Path = "reports/phase6-benchmark.json") -> dict[str, Any]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="difoundry-phase6-") as directory:
        root = Path(directory)
        settings = ProductionSettings(
            environment="development", database_url=f"sqlite:///{root/'platform.sqlite3'}",
            token_signing_key=b"T" * 32, vault_keys={1: b"V" * 32, 2: b"W" * 32}, vault_active_key_version=1, audit_anchor_key=b"A" * 32, rate_limit_per_minute=1000,
            password_time_cost=1, password_memory_cost=8192, password_parallelism=1,
            static_dir=Path(__file__).resolve().parent / "static", bootstrap_enabled=True, bootstrap_token="test-bootstrap-token",
        )
        ctx = ProductionContext(settings)
        configure_context(ctx)
        client = TestClient(app)
        bootstrap = client.post("/platform/bootstrap", headers={"X-Bootstrap-Token": "test-bootstrap-token"}, json={"tenant_name": "Benchmark Tenant", "email": "admin@benchmark.test", "password": "Benchmark-password-123"})
        token = bootstrap.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        def add(spec: dict[str, Any]) -> dict[str, Any]:
            response = client.post("/platform/systems", headers=headers, json={
                "name": spec["name"], "description": "Benchmark system", "protocol": "rest",
                "discovery_format": "system_profile", "specification": spec, "base_url": spec["base_url"],
                "credential_kind": "api_key", "credentials": {"token": f"credential-{spec['system_id']}"},
            })
            response.raise_for_status()
            return response.json()

        source = add(_profile("benchmark-crm", "Benchmark CRM", False))
        target = add(_profile("benchmark-billing", "Benchmark Billing", True))
        session = client.post("/platform/chat/sessions", headers=headers, json={"title": "Benchmark onboarding"}).json()
        client.post(f"/platform/chat/sessions/{session['session_id']}/messages", headers=headers, json={
            "content": "When a customer is approved, create the customer in billing with the same name and email.",
            "attached_system_ids": [source["system_id"], target["system_id"]],
        }).raise_for_status()
        built = client.post(f"/platform/chat/sessions/{session['session_id']}/messages", headers=headers,
                            json={"content": "Build it", "attached_system_ids": []})
        connection_id = built.json()["draft"]["connection_id"]
        queued_before = client.get(f"/platform/connections/{connection_id}", headers=headers).json()
        first_claim = run_once("benchmark-worker-a")
        composed = client.get(f"/platform/connections/{connection_id}", headers=headers).json()
        client.post(f"/platform/connections/{connection_id}/health", headers=headers).raise_for_status()
        second_claim = run_once("benchmark-worker-b")
        healthy = client.get(f"/platform/connections/{connection_id}", headers=headers).json()
        audit = client.get("/platform/audit/verify", headers=headers).json()
        console = client.get("/console")
        metrics = client.get("/platform/metrics", headers=headers)
        secret_row = ctx.database.fetch_one(secret_blobs, secret_blobs.c.tenant_id == bootstrap.json()["principal"]["tenant_id"])
        ciphertext_clean = secret_row is not None and "credential-benchmark" not in secret_row["ciphertext"]
        duplicate_claim = ctx.platform.queue.claim("benchmark-worker-c") is None

        legacy_probes = [
            client.put("/nervous/policy", json={"default_effect": "allow", "rules": []}),
            client.post("/discover", json={}),
            client.post("/repairs/propose", json={}),
            client.post("/tissues/example/train", json={}),
        ]
        tenant_create = client.post("/platform/tenants", headers=headers, json={
            "name": "Second Benchmark Tenant", "slug": "second-benchmark",
            "admin_email": "admin@benchmark.test", "admin_password": "Second-benchmark-password-123",
        })
        second_login = client.post("/platform/login", json={
            "tenant_slug": "second-benchmark", "email": "admin@benchmark.test",
            "password": "Second-benchmark-password-123",
        })
        created_user = client.post("/platform/users", headers=headers, json={
            "email": "operator@benchmark.test", "password": "Operator-benchmark-password-123", "role": "operator",
        })
        operator_login = client.post("/platform/login", json={
            "tenant_slug": "benchmark-tenant", "email": "operator@benchmark.test",
            "password": "Operator-benchmark-password-123",
        })
        operator_headers = {"Authorization": f"Bearer {operator_login.json()['access_token']}"}
        revoked = client.patch(
            f"/platform/users/{created_user.json()['user_id']}", headers=headers, json={"revoke_tokens": True}
        )
        old_token_rejected = client.get("/platform/me", headers=operator_headers).status_code == 401

        rotated = client.post("/platform/vault/rotate", headers=headers, json={"target_key_version": 2})
        root_tenant_id = bootstrap.json()["principal"]["tenant_id"]
        rotated_rows_resolve = all(
            ctx.vault.resolve(root_tenant_id, row["secret_ref"])
            for row in ctx.database.fetch_all(secret_blobs, secret_blobs.c.tenant_id == root_tenant_id)
        )

        second_headers = {"Authorization": f"Bearer {second_login.json()['access_token']}"}
        client.post("/platform/systems", headers=second_headers, json={
            "name": "Second audit source", "description": "audit fixture", "protocol": "rest",
            "credential_kind": "none",
        })
        second_tenant_id = second_login.json()["principal"]["tenant_id"]
        with ctx.database.begin() as connection:
            newest = connection.execute(
                select(audit_events.c.audit_id)
                .where(audit_events.c.tenant_id == second_tenant_id)
                .order_by(audit_events.c.sequence.desc()).limit(1)
            ).scalar_one()
            connection.execute(delete(audit_events).where(audit_events.c.audit_id == newest))
        tail_verification = client.get("/platform/audit/verify", headers=second_headers).json()

        gates = {
            "bootstrap_authentication": bootstrap.status_code == 200,
            "signed_token_access": client.get("/platform/me", headers=headers).status_code == 200,
            "system_discovery": source["profile_id"] is not None and target["profile_id"] is not None,
            "encrypted_secret_storage": ciphertext_clean,
            "chat_intent_capture": bool(built.json()["draft"].get("goal")),
            "durable_job_queued": queued_before["status"] == "queued",
            "worker_claim_and_execution": first_claim,
            "daughter_composed": bool(composed.get("daughter_id")) and composed["status"] in {"shadow", "review_required"},
            "health_job_execution": second_claim,
            "connection_monitoring": healthy["status"] == "healthy" and healthy["health_score"] == 1.0,
            "audit_chain_valid": audit["valid"],
            "single_claim_lease": duplicate_claim,
            "operator_console_served": console.status_code == 200 and "Make every system" in console.text,
            "security_headers": console.headers.get("x-frame-options") == "DENY",
            "metrics_export": metrics.status_code == 200 and "difoundry_tenant_connections" in metrics.text,
            "production_legacy_routes_absent": all(response.status_code == 404 for response in legacy_probes),
            "metrics_require_authentication": client.get("/platform/metrics").status_code == 401,
            "second_tenant_reachable": tenant_create.status_code == 200 and second_login.status_code == 200,
            "same_email_across_tenants": tenant_create.status_code == 200 and second_login.json()["principal"]["tenant_id"] != root_tenant_id,
            "immediate_token_revocation": revoked.status_code == 200 and old_token_rejected,
            "vault_key_rotation": rotated.status_code == 200 and rotated_rows_resolve,
            "audit_tail_truncation_detected": tail_verification["valid"] is False,
        }
        report = {
            "phase": 6,
            "version": "0.7.2",
            "evaluation_kind": "single-process release-gate fixture",
            "claim_boundary": "Small deterministic functional/adversarial fixture only; not a load, penetration, multi-process, availability, or throughput benchmark.",
            "gate_pass": all(gates.values()),
            "gates": gates,
            "measurements": {
                "systems": 2,
                "connections": 1,
                "tenants": 2,
                "chat_messages": len(built.json()["messages"]),
                "audit_events": audit["events"],
                "connection_status": healthy["status"],
                "health_score": healthy["health_score"],
                "harness_runtime_seconds": round(time.perf_counter() - started, 4),
            },
            "boundaries": {
                "database": "SQLAlchemy-backed; thread-safe shared-memory SQLite development fixture, PostgreSQL production target",
                "secrets": "AES-256-GCM reference provider; production KMS/HSM key custody required",
                "identity": "Built-in Argon2id and signed tokens; external OIDC/SAML adapter remains a production extension",
                "chat": "Deterministic provider-neutral planner; external model gateway may augment but not bypass verification",
            },
        }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report
