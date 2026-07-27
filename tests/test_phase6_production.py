from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from difoundry.api import app
from difoundry.production.api import ProductionContext, configure_context
from difoundry.production.database import audit_events, audit_heads, secret_blobs, tenants, users
from difoundry.production.models import Principal, Role
from difoundry.production.security import AuthenticationError
from difoundry.production.service import validate_external_url
from difoundry.production.settings import ProductionSettings
from difoundry.production.worker import run_once


@pytest.fixture()
def platform(tmp_path: Path, monkeypatch):
    settings = ProductionSettings(
        environment="development",
        database_url=f"sqlite:///{tmp_path / 'platform.sqlite3'}",
        token_signing_key=b"t" * 32,
        vault_master_key=b"v" * 32,
        token_ttl_seconds=3600,
        bootstrap_enabled=True, bootstrap_token="test-bootstrap-token",
        max_request_bytes=100_000,
        rate_limit_per_minute=1000,
        password_time_cost=1, password_memory_cost=8192, password_parallelism=1,
    )
    ctx = ProductionContext(settings)
    configure_context(ctx)
    client = TestClient(app)
    response = client.post("/platform/bootstrap", headers={"X-Bootstrap-Token": "test-bootstrap-token"}, json={
        "tenant_name": "Test Foundry", "email": "admin@example.com", "password": "A-very-long-password-123",
    })
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return ctx, client, {"Authorization": f"Bearer {token}"}


def profile(system_id: str, name: str, writable: bool) -> dict:
    operation_kind = "create" if writable else "read"
    method = "POST" if writable else "GET"
    return {
        "system_id": system_id,
        "name": name,
        "version": "1",
        "protocol": "rest",
        "base_url": f"https://{system_id}.example.com",
        "authentication": {"kind": "api_key", "secret_refs": {}, "config": {}},
        "objects": [{
            "object_id": "customer", "name": "Customer", "description": "",
            "fields": [
                {"name": "customer_name", "path": "customer_name", "data_type": "string", "required": True, "nullable": False, "description": "", "format": None, "enum": [], "relation": None, "metadata": {}},
                {"name": "email", "path": "email", "data_type": "string", "required": False, "nullable": True, "description": "", "format": "email", "enum": [], "relation": None, "metadata": {}},
            ],
            "identifiers": [], "metadata": {},
        }],
        "operations": [{
            "operation_id": f"{operation_kind}_customer", "method": method, "path": "/customers",
            "description": "", "object_id": "customer", "operation_kind": operation_kind,
            "request_schema": ({"type": "object", "properties": {"customer_name": {"type": "string"}, "email": {"type": "string"}}, "required": ["customer_name"]} if writable else {}),
            "response_schema": {"type": "object"}, "required_permissions": ["customers.write"] if writable else ["customers.read"],
            "idempotency_supported": writable, "timeout_seconds": 30, "metadata": {},
        }],
        "metadata": {},
    }


def add_system(client: TestClient, headers: dict[str, str], spec: dict, credentials: bool = True) -> dict:
    response = client.post("/platform/systems", headers=headers, json={
        "name": spec["name"], "description": f"{spec['name']} test system", "protocol": "rest",
        "discovery_format": "system_profile", "specification": spec, "base_url": spec["base_url"],
        "credential_kind": "api_key", "credentials": {"token": f"secret-{spec['system_id']}"} if credentials else None,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_bootstrap_login_token_and_security_headers(platform):
    ctx, client, headers = platform
    assert client.get("/platform/me", headers=headers).json()["role"] == "platform_admin"
    login = client.post("/platform/login", json={"tenant_slug": "test-foundry", "email": "admin@example.com", "password": "A-very-long-password-123"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    with pytest.raises(AuthenticationError):
        ctx.tokens.verify(token[:-1] + ("A" if token[-1] != "A" else "B"))
    console = client.get("/console")
    assert console.status_code == 200
    assert "Dendritron Integration Foundry" in console.text
    assert console.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in console.headers["content-security-policy"]


def test_argon2_password_and_encrypted_secret_never_store_plaintext(platform):
    ctx, client, headers = platform
    row = ctx.database.fetch_one(users, users.c.email == "admin@example.com")
    assert row["password_hash"].startswith("$argon2")
    system = add_system(client, headers, profile("source-one", "Source One", False))
    secret = ctx.database.fetch_one(secret_blobs, secret_blobs.c.tenant_id == row["tenant_id"])
    assert secret is not None
    assert "secret-source-one" not in secret["ciphertext"]
    resolved = ctx.vault.resolve(row["tenant_id"], secret["secret_ref"])
    assert resolved == {"token": "secret-source-one"}
    with pytest.raises(KeyError):
        ctx.vault.resolve("another-tenant", secret["secret_ref"])
    assert client.get(f"/platform/systems/{system['system_id']}", headers=headers).json()["has_credentials"] is True


def test_ssrf_boundary_rejects_private_networks():
    for value in ["http://localhost:8080", "http://127.0.0.1", "http://169.254.169.254/latest/meta-data"]:
        with pytest.raises(ValueError):
            validate_external_url(value)
    validate_external_url("https://api.example.invalid")


def test_tenant_isolation_and_role_enforcement(platform):
    ctx, client, headers = platform
    created = add_system(client, headers, profile("private-source", "Private Source", False))
    stamp = "2026-07-26T00:00:00+00:00"
    password_hash = ctx.platform.passwords.hash("Second-tenant-password-123")
    with ctx.database.begin() as connection:
        connection.execute(insert(tenants).values(tenant_id="ten_other", name="Other", slug="other", active=True, created_at=stamp, updated_at=stamp))
        connection.execute(insert(users).values(user_id="usr_other", tenant_id="ten_other", email="viewer@other.com",
                                               password_hash=password_hash, role="viewer", active=True, token_version=1, failed_login_count=0, locked_until=None, created_at=stamp, updated_at=stamp))
    principal = Principal(user_id="usr_other", tenant_id="ten_other", tenant_slug="other", email="viewer@other.com", role=Role.VIEWER, token_version=1)
    other_headers = {"Authorization": f"Bearer {ctx.tokens.issue(principal)}"}
    assert client.get(f"/platform/systems/{created['system_id']}", headers=other_headers).status_code == 404
    forbidden = client.post("/platform/systems", headers=other_headers, json={"name": "Nope", "protocol": "rest"})
    assert forbidden.status_code == 403
    assert client.post("/platform/chat/sessions", headers=other_headers, json={"title": "Nope"}).status_code == 403


def test_chat_to_composition_worker_and_health_monitor(platform):
    ctx, client, headers = platform
    source = add_system(client, headers, profile("crm", "CRM", False))
    target = add_system(client, headers, profile("billing", "Billing", True))
    session = client.post("/platform/chat/sessions", headers=headers, json={"title": "Customer onboarding"}).json()
    first = client.post(f"/platform/chat/sessions/{session['session_id']}/messages", headers=headers, json={
        "content": "When a customer is approved, create the same customer in billing with their name and email.",
        "attached_system_ids": [source["system_id"], target["system_id"]],
    })
    assert first.status_code == 200
    build = client.post(f"/platform/chat/sessions/{session['session_id']}/messages", headers=headers, json={
        "content": "Build it", "attached_system_ids": [],
    })
    assert build.status_code == 200, build.text
    connection_id = build.json()["draft"]["connection_id"]
    queued = client.get(f"/platform/connections/{connection_id}", headers=headers).json()
    assert queued["status"] == "queued"
    assert run_once("worker-test") is True
    composed = client.get(f"/platform/connections/{connection_id}", headers=headers).json()
    assert composed["status"] in {"shadow", "review_required"}
    assert composed["daughter_id"]
    health_job = client.post(f"/platform/connections/{connection_id}/health", headers=headers)
    assert health_job.status_code == 200
    assert run_once("worker-test") is True
    healthy = client.get(f"/platform/connections/{connection_id}", headers=headers).json()
    assert healthy["status"] == "healthy"
    assert healthy["health_score"] == 1.0


def test_missing_credentials_degrades_connection(platform):
    ctx, client, headers = platform
    source = add_system(client, headers, profile("source-missing", "Source Missing", False), credentials=False)
    target = add_system(client, headers, profile("target-ready", "Target Ready", True))
    response = client.post("/platform/connections", headers=headers, json={
        "name": "Missing credentials", "source_system_id": source["system_id"], "target_system_ids": [target["system_id"]],
        "goal": "Synchronize customers", "event_type": "created", "autonomy_level": 1,
    })
    connection_id = response.json()["connection_id"]
    assert run_once("worker-test") is True
    client.post(f"/platform/connections/{connection_id}/health", headers=headers)
    assert run_once("worker-test") is True
    current = client.get(f"/platform/connections/{connection_id}", headers=headers).json()
    assert current["status"] == "degraded"
    assert "missing credentials" in current["last_error"]


def test_durable_queue_claims_once_and_recovers_expired_lease(platform):
    ctx, _client, _headers = platform
    job = ctx.platform.queue.enqueue("tenant", "connection_health", {"connection_id": "none"}, max_attempts=2)
    first = ctx.platform.queue.claim("worker-a", lease_seconds=60)
    assert first and first[0].job_id == job.job_id
    assert ctx.platform.queue.claim("worker-b") is None
    assert ctx.platform.queue.fail(job.job_id, "worker-a", first[2], "boom", retry_delay_seconds=0) == "queued"
    second = ctx.platform.queue.claim("worker-b")
    assert second and second[0].attempts == 2
    assert ctx.platform.queue.fail(job.job_id, "worker-b", second[2], "boom again", retry_delay_seconds=0) == "dead"


def test_audit_chain_detects_tampering(platform):
    ctx, client, headers = platform
    add_system(client, headers, profile("audit-source", "Audit Source", False))
    verified = client.get("/platform/audit/verify", headers=headers).json()
    assert verified["valid"] is True
    tenant_id = client.get("/platform/me", headers=headers).json()["tenant_id"]
    with ctx.database.begin() as connection:
        row = connection.execute(select(audit_events).where(audit_events.c.tenant_id == tenant_id).limit(1)).mappings().first()
        connection.execute(update(audit_events).where(audit_events.c.audit_id == row["audit_id"]).values(action="tampered"))
    assert client.get("/platform/audit/verify", headers=headers).json()["valid"] is False


def test_metrics_readiness_and_request_limit(platform):
    _ctx, client, headers = platform
    metrics = client.get("/platform/metrics", headers=headers)
    assert metrics.status_code == 200
    assert "difoundry_tenant_systems" in metrics.text
    ready = client.get("/platform/readiness")
    assert ready.status_code == 200
    huge = "x" * 2_100_000
    response = client.post("/platform/chat/sessions", headers={**headers, "Content-Length": str(len(huge))}, content=huge)
    assert response.status_code == 413


def test_production_startup_fails_closed_without_external_configuration(tmp_path: Path):
    settings = ProductionSettings(
        environment="production",
        database_url=f"sqlite:///{tmp_path/'prod.sqlite3'}",
        token_signing_key=b"development-token-key-not-safe"[:32],
        vault_keys={1: b"development-vault-key-not-safe"[:32]},
        audit_anchor_key=b"development-anchor-not-safe"[:32],
    )
    with pytest.raises(ValueError, match="Unsafe production configuration"):
        ProductionContext(settings)
