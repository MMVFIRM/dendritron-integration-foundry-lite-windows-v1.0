from __future__ import annotations

import os
import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select, update

from difoundry.api import app
from difoundry.production.api import ProductionContext, configure_context
from difoundry.production.audit import AuditLedger, SignedFileAuditAnchorStore
from difoundry.production.database import (
    PlatformDatabase,
    audit_events,
    audit_heads,
    security_events,
    secret_blobs,
    tenants,
    users,
)
from difoundry.production.models import BootstrapRequest, LoginRequest, Principal, Role, TenantCreate
from difoundry.production.security import EncryptedSecretStore, PasswordService, TokenService
from difoundry.production.service import ProductionPlatform
from difoundry.production.settings import ProductionSettings


def _installed_package_parent() -> str:
    spec = importlib.util.find_spec("difoundry")
    assert spec is not None and spec.origin is not None
    return str(Path(spec.origin).resolve().parents[1])


def make_context(tmp_path: Path, *, anchor: bool = False, rate: int = 1000) -> ProductionContext:
    return ProductionContext(
        ProductionSettings(
            environment="development",
            database_url=f"sqlite:///{tmp_path / 'platform.sqlite3'}",
            token_signing_key=b"T" * 32,
            vault_keys={1: b"A" * 32, 2: b"B" * 32},
            vault_active_key_version=1,
            audit_anchor_key=b"C" * 32,
            audit_anchor_path=(tmp_path / "audit-anchors.jsonl") if anchor else None,
            bootstrap_enabled=True,
            bootstrap_token="bootstrap-secret",
            rate_limit_per_minute=rate,
            password_time_cost=1,
            password_memory_cost=8192,
            password_parallelism=1,
            login_lockout_threshold=3,
            login_lockout_seconds=60,
        )
    )


def bootstrap_client(tmp_path: Path, *, anchor: bool = False) -> tuple[ProductionContext, TestClient, dict[str, str]]:
    ctx = make_context(tmp_path, anchor=anchor)
    configure_context(ctx)
    client = TestClient(app)
    response = client.post(
        "/platform/bootstrap",
        headers={"X-Bootstrap-Token": "bootstrap-secret"},
        json={"tenant_name": "Root Tenant", "email": "owner@example.com", "password": "Owner-password-123"},
    )
    assert response.status_code == 200, response.text
    return ctx, client, {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_production_app_does_not_mount_legacy_control_routes(tmp_path: Path):
    _ctx, client, _headers = bootstrap_client(tmp_path)
    probes = [
        ("put", "/nervous/policy", {"default_effect": "allow", "rules": []}),
        ("post", "/discover", {}),
        ("post", "/repairs/propose", {}),
        ("post", "/tissues/example/train", {}),
        ("get", "/health", None),
    ]
    for method, path, body in probes:
        response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
        assert response.status_code == 404, (method, path, response.text)


def test_bootstrap_requires_out_of_band_secret_and_is_not_default_public(tmp_path: Path):
    ctx = make_context(tmp_path)
    configure_context(ctx)
    client = TestClient(app)
    body = {"tenant_name": "Claimed", "email": "attacker@example.com", "password": "Attacker-password-123"}
    assert client.post("/platform/bootstrap", json=body).status_code == 403
    assert client.post("/platform/bootstrap", headers={"X-Bootstrap-Token": "wrong"}, json=body).status_code == 403
    assert ctx.database.fetch_all(tenants) == []


def test_platform_admin_can_create_multiple_tenants_with_same_email(tmp_path: Path):
    ctx, client, headers = bootstrap_client(tmp_path)
    created = client.post(
        "/platform/tenants",
        headers=headers,
        json={
            "name": "Second Tenant",
            "slug": "second",
            "admin_email": "owner@example.com",
            "admin_password": "Second-password-123",
        },
    )
    assert created.status_code == 200, created.text
    assert len(ctx.database.fetch_all(tenants)) == 2
    assert len(ctx.database.fetch_all(users, users.c.email == "owner@example.com")) == 2

    first_login = client.post(
        "/platform/login",
        json={"tenant_slug": "root-tenant", "email": "owner@example.com", "password": "Owner-password-123"},
    )
    second_login = client.post(
        "/platform/login",
        json={"tenant_slug": "second", "email": "owner@example.com", "password": "Second-password-123"},
    )
    assert first_login.status_code == second_login.status_code == 200
    assert first_login.json()["principal"]["tenant_id"] != second_login.json()["principal"]["tenant_id"]


def test_audit_verification_detects_tail_truncation_and_uses_sequence(tmp_path: Path, monkeypatch):
    ctx, client, headers = bootstrap_client(tmp_path, anchor=True)
    # Force identical timestamps; sequence, not timestamp, must define chain order.
    monkeypatch.setattr("difoundry.production.audit.now_iso", lambda: "2026-07-27T00:00:00+00:00")
    principal = ctx.tokens.verify(headers["Authorization"].split()[1])
    ctx.platform.audit.append(principal.tenant_id, principal.user_id, "one", "test", "1")
    ctx.platform.audit.append(principal.tenant_id, principal.user_id, "two", "test", "2")
    verified = client.get("/platform/audit/verify", headers=headers).json()
    assert verified["valid"] is True
    assert verified["external_anchor_configured"] is True

    with ctx.database.begin() as connection:
        newest = connection.execute(
            select(audit_events.c.audit_id)
            .where(audit_events.c.tenant_id == principal.tenant_id)
            .order_by(audit_events.c.sequence.desc())
            .limit(2)
        ).scalars().all()
        connection.execute(delete(audit_events).where(audit_events.c.audit_id.in_(newest)))
    truncated = client.get("/platform/audit/verify", headers=headers).json()
    assert truncated["valid"] is False
    assert any("tail truncation" in error.lower() or "anchor" in error.lower() for error in truncated["errors"])


def test_audit_concurrent_appends_preserve_contiguous_sequence(tmp_path: Path):
    db = PlatformDatabase(f"sqlite:///{tmp_path / 'audit.sqlite3'}")
    ledger = AuditLedger(db)
    tenant_id = "tenant"
    errors: list[Exception] = []

    def append(index: int) -> None:
        try:
            ledger.append(tenant_id, None, "concurrent", "test", str(index))
        except Exception as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    threads = [threading.Thread(target=append, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    result = ledger.verify(tenant_id)
    assert result["valid"] is True
    assert result["events"] == 20
    rows = db.fetch_all(audit_events, audit_events.c.tenant_id == tenant_id)
    assert sorted(int(row["sequence"]) for row in rows) == list(range(1, 21))


def test_vault_collision_is_tenant_scoped_and_rotation_is_real(tmp_path: Path):
    db = PlatformDatabase(f"sqlite:///{tmp_path / 'vault.sqlite3'}")
    vault = EncryptedSecretStore(db, keys={1: b"A" * 32, 2: b"B" * 32}, active_key_version=1)
    vault.put("tenant-a", "system", "a", {"api_key": "tenant-a-secret"}, "shared-ref")
    vault.put("tenant-b", "system", "b", {"api_key": "tenant-b-secret"}, "shared-ref")
    assert vault.resolve("tenant-a", "shared-ref") == {"api_key": "tenant-a-secret"}
    assert vault.resolve("tenant-b", "shared-ref") == {"api_key": "tenant-b-secret"}

    rotated = vault.rotate_tenant("tenant-a", 2)
    assert rotated == 1
    assert vault.resolve("tenant-a", "shared-ref") == {"api_key": "tenant-a-secret"}
    row_a = db.fetch_one(secret_blobs, secret_blobs.c.tenant_id == "tenant-a", secret_blobs.c.secret_ref == "shared-ref")
    row_b = db.fetch_one(secret_blobs, secret_blobs.c.tenant_id == "tenant-b", secret_blobs.c.secret_ref == "shared-ref")
    assert int(row_a["key_version"]) == 2
    assert int(row_b["key_version"]) == 1


def test_role_change_and_logout_revoke_existing_tokens_immediately(tmp_path: Path):
    _ctx, client, headers = bootstrap_client(tmp_path)
    created = client.post(
        "/platform/users",
        headers=headers,
        json={"email": "operator@example.com", "password": "Operator-password-123", "role": "operator"},
    ).json()
    login = client.post(
        "/platform/login",
        json={"tenant_slug": "root-tenant", "email": "operator@example.com", "password": "Operator-password-123"},
    )
    operator_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/platform/me", headers=operator_headers).status_code == 200
    changed = client.patch(
        f"/platform/users/{created['user_id']}",
        headers=headers,
        json={"role": "viewer"},
    )
    assert changed.status_code == 200
    assert client.get("/platform/me", headers=operator_headers).status_code == 401

    refreshed = client.post(
        "/platform/login",
        json={"tenant_slug": "root-tenant", "email": "operator@example.com", "password": "Operator-password-123"},
    )
    refreshed_headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    assert client.post("/platform/logout", headers=refreshed_headers).status_code == 204
    assert client.get("/platform/me", headers=refreshed_headers).status_code == 401


def test_failed_logins_are_audited_and_lockout_is_enforced(tmp_path: Path):
    ctx, client, headers = bootstrap_client(tmp_path)
    payload = {"tenant_slug": "root-tenant", "email": "owner@example.com", "password": "wrong-password-value"}
    for _ in range(3):
        assert client.post("/platform/login", json=payload).status_code == 401
    rows = ctx.database.fetch_all(security_events)
    assert len(rows) == 3
    audit = client.get("/platform/audit", headers=headers).json()
    assert sum(event["action"] == "auth.login_failed" for event in audit) == 3
    correct = client.post(
        "/platform/login",
        json={"tenant_slug": "root-tenant", "email": "owner@example.com", "password": "Owner-password-123"},
    )
    assert correct.status_code == 401


def test_unknown_user_path_still_executes_argon2_verification(monkeypatch):
    passwords = PasswordService(time_cost=1, memory_cost=8192, parallelism=1)
    calls: list[str] = []
    original = type(passwords._hasher).verify

    def wrapped(instance, encoded: str, password: str):
        calls.append(encoded)
        return original(instance, encoded, password)

    monkeypatch.setattr(type(passwords._hasher), "verify", wrapped)
    assert passwords.verify(None, "not-a-real-password") is False
    assert len(calls) == 1
    assert calls[0].startswith("$argon2")


def test_stolen_or_expired_job_lease_cannot_complete(tmp_path: Path):
    ctx = make_context(tmp_path)
    job = ctx.platform.queue.enqueue("tenant", "noop", {})
    claimed = ctx.platform.queue.claim("worker-a", lease_seconds=1)
    assert claimed is not None
    _view, _payload, token = claimed
    with pytest.raises(KeyError):
        ctx.platform.queue.complete(job.job_id, "worker-a", "stolen-token")
    # Expire the lease directly to make the test deterministic.
    from difoundry.production.database import jobs
    with ctx.database.begin() as connection:
        connection.execute(update(jobs).where(jobs.c.job_id == job.job_id).values(leased_until="2000-01-01T00:00:00+00:00"))
    with pytest.raises(KeyError):
        ctx.platform.queue.complete(job.job_id, "worker-a", token)
    reclaimed = ctx.platform.queue.claim("worker-b", lease_seconds=60)
    assert reclaimed is not None
    with pytest.raises(KeyError):
        ctx.platform.queue.complete(job.job_id, "worker-a", token)
    ctx.platform.queue.complete(job.job_id, "worker-b", reclaimed[2])


def test_two_contexts_share_all_production_state_through_database(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'shared.sqlite3'}"
    settings = ProductionSettings(
        database_url=database_url,
        token_signing_key=b"T" * 32,
        vault_keys={1: b"A" * 32},
        bootstrap_enabled=True,
        bootstrap_token="bootstrap-secret",
        password_time_cost=1,
        password_memory_cost=8192,
        password_parallelism=1,
    )
    first = ProductionContext(settings)
    token = first.platform.bootstrap(
        BootstrapRequest(tenant_name="Shared", email="shared@example.com", password="Shared-password-123"),
        True,
    )
    second = ProductionContext(settings)
    login = second.platform.login(
        LoginRequest(tenant_slug="shared", email="shared@example.com", password="Shared-password-123")
    )
    assert login.principal.tenant_id == token.principal.tenant_id
    assert second.platform.list_users(login.principal)[0].email == "shared@example.com"


def test_importing_production_app_does_not_create_a_sqlite_file(tmp_path: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = _installed_package_parent()
    env.pop("DIFOUNDRY_DATABASE_URL", None)
    subprocess.run(
        [sys.executable, "-c", "import difoundry.api; print('ok')"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not list(tmp_path.glob("*.sqlite3"))


def test_signed_directory_anchor_is_replica_order_independent(tmp_path: Path):
    from difoundry.production.audit import SignedDirectoryAuditAnchorStore

    store = SignedDirectoryAuditAnchorStore(tmp_path / "anchors", b"K" * 32)
    # Simulate replicas completing filesystem writes out of sequence.
    store.append("tenant", 2, "b" * 64)
    store.append("tenant", 1, "a" * 64)
    latest = store.latest("tenant")
    assert latest is not None
    assert latest["sequence"] == 2
    assert latest["head_hash"] == "b" * 64
    # An existing sequence cannot be rebound to a different head.
    with pytest.raises(ValueError):
        store.append("tenant", 2, "c" * 64)


def test_rate_limiter_concurrent_consumers_do_not_oversubscribe(tmp_path: Path, monkeypatch):
    from difoundry.production.rate_limit import SqlRateLimiter

    db = PlatformDatabase(f"sqlite:///{tmp_path / 'rate.sqlite3'}")
    limiter = SqlRateLimiter(db, 10)
    monkeypatch.setattr("difoundry.production.rate_limit.time.time", lambda: 1000.0)
    results: list[bool] = []
    lock = threading.Lock()

    def consume() -> None:
        allowed, _ = limiter.consume("shared")
        with lock:
            results.append(allowed)

    threads = [threading.Thread(target=consume) for _ in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(results) == 10
    assert len(results) == 30


def test_forwarded_client_identity_requires_trusted_immediate_proxy(tmp_path: Path):
    from starlette.requests import Request
    from difoundry.production.api import _client_id

    ctx = make_context(tmp_path)
    ctx.settings.trusted_proxy_cidrs = ("127.0.0.1/32",)
    configure_context(ctx)

    trusted = Request({
        "type": "http", "method": "GET", "path": "/", "headers": [(b"x-forwarded-for", b"203.0.113.9")],
        "client": ("127.0.0.1", 1234), "server": ("test", 80), "scheme": "http", "query_string": b"",
    })
    assert _client_id(trusted) == "203.0.113.9"

    untrusted = Request({
        "type": "http", "method": "GET", "path": "/", "headers": [(b"x-forwarded-for", b"203.0.113.9")],
        "client": ("198.51.100.10", 1234), "server": ("test", 80), "scheme": "http", "query_string": b"",
    })
    assert _client_id(untrusted) == "198.51.100.10"


def test_metrics_require_authentication(tmp_path: Path):
    _ctx, client, headers = bootstrap_client(tmp_path)
    assert client.get("/platform/metrics").status_code == 401
    assert client.get("/platform/metrics", headers=headers).status_code == 200


def test_every_nonpublic_production_route_has_auth_dependency():
    from fastapi.routing import APIRoute

    public = {
        "/",
        "/console",
        "/assets/{asset_name}",
        "/platform/bootstrap",
        "/platform/login",
        "/platform/liveness",
        "/platform/readiness",
    }
    exposed: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in public:
            continue
        if route.path.startswith("/platform/") and not route.dependant.dependencies:
            exposed.append(route.path)
    assert exposed == []


def test_schema_v1_to_v2_migration_preserves_and_rehashes(tmp_path: Path):
    import hashlib
    import json
    import sqlite3

    database_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.executescript("""
        CREATE TABLE platform_schema_migrations (version INTEGER PRIMARY KEY, applied_at VARCHAR(40) NOT NULL);
        INSERT INTO platform_schema_migrations VALUES (1, '2026-07-27T00:00:00+00:00');
        CREATE TABLE platform_tenants (tenant_id VARCHAR(80) PRIMARY KEY, name VARCHAR(160) NOT NULL, slug VARCHAR(180) NOT NULL UNIQUE, created_at VARCHAR(40) NOT NULL);
        CREATE TABLE platform_users (user_id VARCHAR(80) PRIMARY KEY, tenant_id VARCHAR(80) NOT NULL, email VARCHAR(254) NOT NULL UNIQUE, password_hash TEXT NOT NULL, role VARCHAR(32) NOT NULL, active BOOLEAN NOT NULL, created_at VARCHAR(40) NOT NULL);
        CREATE TABLE platform_jobs (job_id VARCHAR(80) PRIMARY KEY, tenant_id VARCHAR(80) NOT NULL, kind VARCHAR(80) NOT NULL, payload_json TEXT NOT NULL, status VARCHAR(32) NOT NULL, attempts INTEGER NOT NULL, max_attempts INTEGER NOT NULL, run_after VARCHAR(40) NOT NULL, lease_owner VARCHAR(120), leased_until VARCHAR(40), last_error TEXT, created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL);
        CREATE TABLE platform_secret_blobs (secret_ref VARCHAR(100) PRIMARY KEY, tenant_id VARCHAR(80) NOT NULL, resource_type VARCHAR(80) NOT NULL, resource_id VARCHAR(100) NOT NULL, ciphertext TEXT NOT NULL, nonce TEXT NOT NULL, key_version INTEGER NOT NULL, created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL);
        CREATE TABLE platform_audit_events (audit_id VARCHAR(80) PRIMARY KEY, tenant_id VARCHAR(80) NOT NULL, user_id VARCHAR(80), action VARCHAR(160) NOT NULL, resource_type VARCHAR(80) NOT NULL, resource_id VARCHAR(100), details_json TEXT NOT NULL, previous_hash VARCHAR(64) NOT NULL, event_hash VARCHAR(64) NOT NULL, created_at VARCHAR(40) NOT NULL);
        CREATE TABLE platform_audit_heads (tenant_id VARCHAR(80) PRIMARY KEY, head_hash VARCHAR(64) NOT NULL, version INTEGER NOT NULL);
        CREATE TABLE platform_rate_windows (bucket_key VARCHAR(240) PRIMARY KEY, window_start INTEGER NOT NULL, count INTEGER NOT NULL);
        CREATE TABLE platform_idempotency (scope_key VARCHAR(240) PRIMARY KEY, request_hash VARCHAR(64) NOT NULL, response_json TEXT NOT NULL, created_at VARCHAR(40) NOT NULL);
    """)
    stamp = "2026-07-27T00:00:00+00:00"
    connection.execute("INSERT INTO platform_tenants VALUES (?,?,?,?)", ("ten_1", "Legacy", "legacy", stamp))
    connection.execute("INSERT INTO platform_users VALUES (?,?,?,?,?,?,?)", ("usr_1", "ten_1", "ADMIN@EXAMPLE.COM", "hash", "admin", 1, stamp))
    connection.execute("INSERT INTO platform_secret_blobs VALUES (?,?,?,?,?,?,?,?,?)", ("shared", "ten_1", "system", "sys_1", "cipher", "nonce", 1, stamp, stamp))
    details = json.dumps({"legacy": True}, separators=(",", ":"), sort_keys=True)
    previous = "0" * 64
    material = json.dumps({
        "audit_id": "aud_1", "tenant_id": "ten_1", "user_id": "usr_1",
        "action": "legacy", "resource_type": "tenant", "resource_id": "ten_1",
        "details": {"legacy": True}, "previous_hash": previous, "created_at": stamp,
    }, separators=(",", ":"), sort_keys=True)
    old_hash = hashlib.sha256(material.encode()).hexdigest()
    connection.execute("INSERT INTO platform_audit_events VALUES (?,?,?,?,?,?,?,?,?,?)", ("aud_1", "ten_1", "usr_1", "legacy", "tenant", "ten_1", details, previous, old_hash, stamp))
    connection.execute("INSERT INTO platform_audit_heads VALUES (?,?,?)", ("ten_1", old_hash, 1))
    connection.commit()
    connection.close()

    completed = subprocess.run(
        [sys.executable, "scripts/migrate_v1_to_v2.py", f"sqlite:///{database_path}", "--no-backup"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONPATH": _installed_package_parent()},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    db = PlatformDatabase(f"sqlite:///{database_path}")
    migrated = db.fetch_one(users, users.c.user_id == "usr_1")
    assert migrated["email"] == "admin@example.com"
    assert migrated["role"] == "platform_admin"
    assert int(migrated["token_version"]) == 2
    assert AuditLedger(db).verify("ten_1")["valid"] is True


def test_lockout_survives_threshold_plus_one_attack_attempt(tmp_path: Path):
    ctx, client, _headers = bootstrap_client(tmp_path)
    wrong = {"tenant_slug": "root-tenant", "email": "owner@example.com", "password": "wrong-password-value"}
    correct = {"tenant_slug": "root-tenant", "email": "owner@example.com", "password": "Owner-password-123"}

    for _ in range(ctx.platform.login_lockout_threshold):
        assert client.post("/platform/login", json=wrong).status_code == 401
    assert client.post("/platform/login", json=correct).status_code == 401

    # The attacker's next guess must not clear the active lock.
    assert client.post("/platform/login", json=wrong).status_code == 401
    assert client.post("/platform/login", json=correct).status_code == 401

    row = ctx.database.fetch_one(users, users.c.email == "owner@example.com")
    assert int(row["failed_login_count"]) == ctx.platform.login_lockout_threshold
    assert row["locked_until"] is not None


def test_default_in_memory_database_is_shared_across_request_threads():
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.pool import QueuePool
    from difoundry.production.database import schema_migrations

    db = PlatformDatabase("sqlite:///:memory:")
    assert isinstance(db.engine.pool, QueuePool)

    def read_schema(_index: int) -> int:
        row = db.fetch_one(schema_migrations)
        return int(row["version"])

    with ThreadPoolExecutor(max_workers=24) as executor:
        versions = list(executor.map(read_schema, range(48)))
    assert versions == [2] * 48


def test_live_uvicorn_default_database_handles_concurrent_logins(tmp_path: Path):
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    import socket
    import httpx

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    env = os.environ.copy()
    env.update({
        "PYTHONPATH": _installed_package_parent(),
        "DIFOUNDRY_ENV": "development",
        "DIFOUNDRY_DATABASE_URL": "sqlite:///:memory:",
        "DIFOUNDRY_BOOTSTRAP_ENABLED": "true",
        "DIFOUNDRY_PASSWORD_TIME_COST": "1",
        "DIFOUNDRY_PASSWORD_MEMORY_COST": "8192",
        "DIFOUNDRY_PASSWORD_PARALLELISM": "1",
        "DIFOUNDRY_RATE_LIMIT_PER_MINUTE": "10000",
    })
    env.pop("DIFOUNDRY_BOOTSTRAP_TOKEN", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "difoundry.api:app", "--host", "127.0.0.1", "--port", str(port), "--no-proxy-headers"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/platform/liveness", timeout=0.5).status_code == 200:
                    break
            except Exception:
                time.sleep(0.05)
        else:
            raise AssertionError("Uvicorn did not become live")

        ready = httpx.get(f"{base}/platform/readiness", timeout=2)
        assert ready.status_code == 200, ready.text
        assert ready.json()["checks"]["bootstrap_token_required"] is False

        boot = httpx.post(
            f"{base}/platform/bootstrap",
            json={"tenant_name": "Runtime", "email": "runtime@example.com", "password": "Runtime-password-123"},
            timeout=5,
        )
        assert boot.status_code == 200, boot.text

        payload = {"tenant_slug": "runtime", "email": "runtime@example.com", "password": "Runtime-password-123"}
        def login_once(_index: int) -> str:
            return str(httpx.post(f"{base}/platform/login", json=payload, timeout=10).status_code)

        with ThreadPoolExecutor(max_workers=24) as executor:
            statuses = Counter(executor.map(login_once, range(24)))
        assert statuses == Counter({"200": 24}), statuses
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.returncode not in {0, -15}:
            stdout, stderr = process.communicate()
            raise AssertionError(f"uvicorn exited {process.returncode}\nstdout={stdout}\nstderr={stderr}")


def test_worker_does_not_exit_when_completion_lease_is_expired(tmp_path: Path, monkeypatch):
    from difoundry.production.database import jobs
    from difoundry.production.worker import run_once

    ctx = make_context(tmp_path)
    configure_context(ctx)
    job = ctx.platform.queue.enqueue("tenant", "slow", {})

    def finish_after_expiry(_kind, _tenant_id, _payload):
        with ctx.database.begin() as connection:
            connection.execute(update(jobs).where(jobs.c.job_id == job.job_id).values(leased_until="2000-01-01T00:00:00+00:00"))
        return {"finished": True}

    monkeypatch.setattr(ctx.platform, "execute_job", finish_after_expiry)
    assert run_once("worker-a", lease_seconds=60, heartbeat_seconds=30) is True
    row = ctx.database.fetch_one(jobs, jobs.c.job_id == job.job_id)
    assert row["status"] == "running"
    audit = ctx.platform.audit.list("tenant", 20)
    assert any(event.action == "job.lease_lost" for event in audit)


def test_worker_does_not_exit_when_failure_handler_has_lost_lease(tmp_path: Path, monkeypatch):
    from difoundry.production.database import jobs
    from difoundry.production.worker import run_once

    ctx = make_context(tmp_path)
    configure_context(ctx)
    job = ctx.platform.queue.enqueue("tenant", "slow-failure", {})

    def fail_after_expiry(_kind, _tenant_id, _payload):
        with ctx.database.begin() as connection:
            connection.execute(update(jobs).where(jobs.c.job_id == job.job_id).values(leased_until="2000-01-01T00:00:00+00:00"))
        raise RuntimeError("execution failed after lease expiry")

    monkeypatch.setattr(ctx.platform, "execute_job", fail_after_expiry)
    assert run_once("worker-a", lease_seconds=60, heartbeat_seconds=30) is True
    audit = ctx.platform.audit.list("tenant", 20)
    assert any(event.action == "job.lease_lost" for event in audit)


def test_worker_heartbeat_renews_long_running_job(tmp_path: Path, monkeypatch):
    from difoundry.production.database import jobs
    from difoundry.production.worker import run_once

    ctx = make_context(tmp_path)
    configure_context(ctx)
    job = ctx.platform.queue.enqueue("tenant", "long-running", {})

    def slow_success(_kind, _tenant_id, _payload):
        time.sleep(1.4)
        return {"finished": True}

    monkeypatch.setattr(ctx.platform, "execute_job", slow_success)
    assert run_once("worker-a", lease_seconds=1, heartbeat_seconds=0.2) is True
    row = ctx.database.fetch_one(jobs, jobs.c.job_id == job.job_id)
    assert row["status"] == "succeeded"


def test_production_disables_openapi_and_interactive_docs(tmp_path: Path):
    from difoundry.api import create_app

    settings = ProductionSettings(
        environment="production",
        database_url="postgresql+psycopg://user:password@database.example/difoundry",
        token_signing_key=b"T" * 32,
        vault_keys={1: b"V" * 32},
        audit_anchor_key=b"A" * 32,
        audit_anchor_path=tmp_path / "anchors",
        bootstrap_enabled=False,
    )
    production_app = create_app(settings)
    client = TestClient(production_app)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_default_development_bootstrap_is_usable_and_ready():
    ctx = ProductionContext(ProductionSettings(
        environment="development",
        database_url="sqlite:///:memory:",
        token_signing_key=b"T" * 32,
        vault_keys={1: b"V" * 32},
        bootstrap_enabled=True,
        bootstrap_token=None,
        password_time_cost=1,
        password_memory_cost=8192,
        password_parallelism=1,
    ))
    configure_context(ctx)
    client = TestClient(app)
    ready = client.get("/platform/readiness")
    assert ready.status_code == 200
    assert ready.json()["checks"]["bootstrap_token_required"] is False
    boot = client.post(
        "/platform/bootstrap",
        json={"tenant_name": "Developer", "email": "dev@example.com", "password": "Developer-password-123"},
    )
    assert boot.status_code == 200, boot.text


def test_concurrent_failed_logins_cannot_bypass_threshold(tmp_path: Path):
    from concurrent.futures import ThreadPoolExecutor

    ctx = make_context(tmp_path, rate=10000)
    ctx.platform.bootstrap(
        BootstrapRequest(tenant_name="Concurrent", email="owner@example.com", password="Owner-password-123"),
        True,
    )
    wrong = LoginRequest(tenant_slug="concurrent", email="owner@example.com", password="wrong-password-value")

    def fail(index: int) -> str:
        try:
            ctx.platform.login(wrong, client_id=f"client-{index}")
        except Exception as exc:
            return type(exc).__name__
        return "unexpected-success"

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(fail, range(12)))
    assert set(outcomes) == {"PlatformError"}
    row = ctx.database.fetch_one(users, users.c.email == "owner@example.com")
    assert int(row["failed_login_count"]) == ctx.platform.login_lockout_threshold
    assert row["locked_until"] is not None
    with pytest.raises(Exception):
        ctx.platform.login(
            LoginRequest(tenant_slug="concurrent", email="owner@example.com", password="Owner-password-123")
        )


def test_worker_process_handles_sigterm_gracefully(tmp_path: Path):
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": _installed_package_parent(),
        "DIFOUNDRY_ENV": "development",
        "DIFOUNDRY_DATABASE_URL": "sqlite:///:memory:",
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "difoundry.production.worker", "--poll-seconds", "10"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        import select as select_module
        readable, _, _ = select_module.select([process.stdout], [], [], 5)
        assert readable, "worker did not report readiness"
        assert process.stdout.readline().strip() == "difoundry-worker ready"
        process.terminate()
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_correct_password_cannot_race_and_clear_new_lock(tmp_path: Path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import threading

    ctx = make_context(tmp_path, rate=10000)
    ctx.platform.bootstrap(
        BootstrapRequest(tenant_name="Race", email="owner@example.com", password="Owner-password-123"),
        True,
    )
    entered_verify = threading.Event()
    release_verify = threading.Event()
    original_verify = ctx.platform.passwords.verify

    def delayed_verify(encoded, password):
        entered_verify.set()
        assert release_verify.wait(timeout=5)
        return original_verify(encoded, password)

    monkeypatch.setattr(ctx.platform.passwords, "verify", delayed_verify)
    outcome: list[str] = []

    def login_correctly():
        try:
            ctx.platform.login(
                LoginRequest(tenant_slug="race", email="owner@example.com", password="Owner-password-123")
            )
            outcome.append("success")
        except Exception as exc:
            outcome.append(type(exc).__name__)

    thread = threading.Thread(target=login_correctly)
    thread.start()
    assert entered_verify.wait(timeout=5)
    lock_until = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    with ctx.database.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.email == "owner@example.com")
            .values(failed_login_count=ctx.platform.login_lockout_threshold, locked_until=lock_until)
        )
    release_verify.set()
    thread.join(timeout=5)
    assert outcome == ["PlatformError"]
    row = ctx.database.fetch_one(users, users.c.email == "owner@example.com")
    assert int(row["failed_login_count"]) == ctx.platform.login_lockout_threshold
    assert row["locked_until"] == lock_until
