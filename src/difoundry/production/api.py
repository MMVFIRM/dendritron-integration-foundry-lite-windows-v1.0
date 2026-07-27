from __future__ import annotations

import hmac
import ipaddress
import threading
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import func, select

from .audit import SignedDirectoryAuditAnchorStore
from .database import PlatformDatabase, connections, jobs, schema_migrations, systems
from .models import (
    AuditEventView,
    BootstrapRequest,
    ChatMessageCreate,
    ChatSessionCreate,
    ChatSessionView,
    ConnectionCreate,
    ConnectionView,
    CredentialUpdate,
    JobView,
    LoginRequest,
    Overview,
    PasswordChange,
    Principal,
    Role,
    SystemCreate,
    SystemView,
    TenantCreate,
    TenantView,
    TokenResponse,
    UserCreate,
    UserUpdate,
    UserView,
    VaultRotationRequest,
)
from .rate_limit import SqlRateLimiter
from .security import AuthenticationError, EncryptedSecretStore, TokenService
from .service import PlatformError, ProductionPlatform
from .settings import ProductionSettings, production_key_configured


class ProductionContext:
    def __init__(self, settings: ProductionSettings):
        settings.assert_startup_safe()
        self.settings = settings
        self.database = PlatformDatabase(settings.database_url)
        self.tokens = TokenService(settings.token_signing_key, settings.issuer, settings.token_ttl_seconds)
        self.vault = EncryptedSecretStore(
            self.database,
            keys=settings.vault_keys,
            active_key_version=settings.vault_active_key_version,
        )
        anchor = None
        if settings.audit_anchor_path:
            anchor = SignedDirectoryAuditAnchorStore(settings.audit_anchor_path, settings.audit_anchor_key)
        self.platform = ProductionPlatform(
            self.database,
            self.tokens,
            self.vault,
            audit_anchor_store=anchor,
            password_time_cost=settings.password_time_cost,
            password_memory_cost=settings.password_memory_cost,
            password_parallelism=settings.password_parallelism,
            login_lockout_threshold=settings.login_lockout_threshold,
            login_lockout_seconds=settings.login_lockout_seconds,
            strict_external_url_validation=settings.environment == "production",
        )
        self.rate_limiter = SqlRateLimiter(self.database, settings.rate_limit_per_minute)


_context: ProductionContext | None = None
_context_lock = threading.Lock()
router = APIRouter()


def configure_context(new_context: ProductionContext | None) -> None:
    global _context
    with _context_lock:
        _context = new_context


def context() -> ProductionContext:
    global _context
    if _context is None:
        with _context_lock:
            if _context is None:
                _context = ProductionContext(ProductionSettings.from_env())
    return _context


def _client_id(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    trusted = context().settings.trusted_proxy_cidrs
    if not trusted:
        return peer
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    networks = [ipaddress.ip_network(value, strict=False) for value in trusted]
    if not any(peer_ip in network for network in networks):
        return peer
    forwarded = [item.strip() for item in request.headers.get("x-forwarded-for", "").split(",") if item.strip()]
    if not forwarded:
        return peer
    # Walk from the trusted edge inward and select the first untrusted address.
    for candidate in reversed(forwarded):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not any(address in network for network in networks):
            return candidate
    return forwarded[0]


def principal_from_token(authorization: Annotated[str | None, Header()] = None) -> Principal:
    ctx = context()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    try:
        token_principal = ctx.tokens.verify(authorization.split(" ", 1)[1])
        principal = ctx.platform.validate_principal(token_principal)
    except (AuthenticationError, PlatformError) as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    allowed, remaining = ctx.rate_limiter.consume(f"principal:{principal.tenant_id}:{principal.user_id}")
    if not allowed:
        raise HTTPException(
            429,
            "Rate limit exceeded",
            headers={"Retry-After": "1", "X-RateLimit-Remaining": "0"},
        )
    return principal


CurrentPrincipal = Annotated[Principal, Depends(principal_from_token)]


@router.post("/platform/bootstrap", response_model=TokenResponse)
def bootstrap(
    request: BootstrapRequest,
    http_request: Request,
    bootstrap_token: Annotated[str | None, Header(alias="X-Bootstrap-Token")] = None,
) -> TokenResponse:
    ctx = context()
    client = _client_id(http_request)
    allowed, _ = ctx.rate_limiter.consume(f"bootstrap:{client}")
    if not allowed:
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "1"})
    if not ctx.settings.bootstrap_enabled:
        raise HTTPException(404, "Not found")
    token_required = ctx.settings.environment == "production" or bool(ctx.settings.bootstrap_token)
    if token_required and (
        not ctx.settings.bootstrap_token
        or not bootstrap_token
        or not hmac.compare_digest(bootstrap_token, ctx.settings.bootstrap_token)
    ):
        raise HTTPException(403, "Valid bootstrap token required")
    try:
        return ctx.platform.bootstrap(request, True)
    except PlatformError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/platform/login", response_model=TokenResponse)
def login(request: LoginRequest, http_request: Request) -> TokenResponse:
    ctx = context()
    client = _client_id(http_request)
    allowed, _ = ctx.rate_limiter.consume(
        f"login:{client}:{request.tenant_slug.lower()}:{request.email.lower()}"
    )
    if not allowed:
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "1"})
    try:
        return ctx.platform.login(request, client_id=client)
    except PlatformError as exc:
        raise HTTPException(401, str(exc)) from exc


@router.post("/platform/logout", status_code=204)
def logout(principal: CurrentPrincipal) -> Response:
    context().platform.revoke_tokens(principal)
    return Response(status_code=204)


@router.post("/platform/password", status_code=204)
def change_password(request: PasswordChange, principal: CurrentPrincipal) -> Response:
    try:
        context().platform.change_password(principal, request)
    except PlatformError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(status_code=204)


@router.get("/platform/me", response_model=Principal)
def me(principal: CurrentPrincipal) -> Principal:
    return principal


@router.get("/platform/overview", response_model=Overview)
def overview(principal: CurrentPrincipal) -> Overview:
    return context().platform.overview(principal)


@router.post("/platform/tenants", response_model=TenantView)
def create_tenant(request: TenantCreate, principal: CurrentPrincipal) -> TenantView:
    try:
        return context().platform.create_tenant(principal, request)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/platform/tenants", response_model=list[TenantView])
def list_tenants(principal: CurrentPrincipal) -> list[TenantView]:
    try:
        return context().platform.list_tenants(principal)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/platform/users", response_model=UserView)
def create_user(request: UserCreate, principal: CurrentPrincipal) -> UserView:
    try:
        return context().platform.create_user(principal, request)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/platform/users", response_model=list[UserView])
def list_users(principal: CurrentPrincipal) -> list[UserView]:
    try:
        return context().platform.list_users(principal)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.patch("/platform/users/{user_id}", response_model=UserView)
def update_user(user_id: str, request: UserUpdate, principal: CurrentPrincipal) -> UserView:
    try:
        return context().platform.update_user(principal, user_id, request)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "User not found") from exc


@router.delete("/platform/users/{user_id}", status_code=204)
def delete_user(user_id: str, principal: CurrentPrincipal) -> Response:
    try:
        context().platform.delete_user(principal, user_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "User not found") from exc
    except PlatformError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(status_code=204)


@router.post("/platform/vault/rotate")
def rotate_vault(request: VaultRotationRequest, principal: CurrentPrincipal) -> dict[str, int]:
    if principal.role not in {Role.PLATFORM_ADMIN, Role.ADMIN}:
        raise HTTPException(403, "Insufficient role")
    try:
        rotated = context().vault.rotate_tenant(principal.tenant_id, request.target_key_version)
    except KeyError as exc:
        raise HTTPException(409, str(exc)) from exc
    context().platform.audit.append(
        principal.tenant_id,
        principal.user_id,
        "vault.rotate",
        "tenant",
        principal.tenant_id,
        {"target_key_version": request.target_key_version, "rotated": rotated},
    )
    return {"rotated": rotated, "active_key_version": request.target_key_version}


@router.post("/platform/systems", response_model=SystemView)
def create_system(request: SystemCreate, principal: CurrentPrincipal) -> SystemView:
    try:
        return context().platform.create_system(principal, request)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/platform/systems", response_model=list[SystemView])
def list_systems(principal: CurrentPrincipal) -> list[SystemView]:
    return context().platform.list_systems(principal)


@router.get("/platform/systems/{system_id}", response_model=SystemView)
def get_system(system_id: str, principal: CurrentPrincipal) -> SystemView:
    try:
        return context().platform.get_system(principal, system_id)
    except KeyError as exc:
        raise HTTPException(404, "System not found") from exc


@router.put("/platform/systems/{system_id}/credentials", response_model=SystemView)
def set_credentials(system_id: str, request: CredentialUpdate, principal: CurrentPrincipal) -> SystemView:
    try:
        return context().platform.set_credentials(principal, system_id, request.kind, request.values)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "System not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/platform/connections", response_model=ConnectionView)
def create_connection(request: ConnectionCreate, principal: CurrentPrincipal) -> ConnectionView:
    try:
        return context().platform.create_connection(principal, request)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "A system was not found in this tenant") from exc
    except PlatformError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/platform/connections", response_model=list[ConnectionView])
def list_connections(principal: CurrentPrincipal) -> list[ConnectionView]:
    return context().platform.list_connections(principal)


@router.get("/platform/connections/{connection_id}", response_model=ConnectionView)
def get_connection(connection_id: str, principal: CurrentPrincipal) -> ConnectionView:
    try:
        return context().platform.get_connection(principal, connection_id)
    except KeyError as exc:
        raise HTTPException(404, "Connection not found") from exc


@router.post("/platform/connections/{connection_id}/health")
def queue_health(connection_id: str, principal: CurrentPrincipal) -> dict[str, str]:
    try:
        return context().platform.queue_health_check(principal, connection_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Connection not found") from exc


@router.post("/platform/chat/sessions", response_model=ChatSessionView)
def create_chat_session(request: ChatSessionCreate, principal: CurrentPrincipal) -> ChatSessionView:
    try:
        return context().platform.create_chat_session(principal, request.title)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/platform/chat/sessions", response_model=list[ChatSessionView])
def list_chat_sessions(principal: CurrentPrincipal) -> list[ChatSessionView]:
    return context().platform.list_chat_sessions(principal)


@router.get("/platform/chat/sessions/{session_id}", response_model=ChatSessionView)
def get_chat_session(session_id: str, principal: CurrentPrincipal) -> ChatSessionView:
    try:
        return context().platform.get_chat_session(principal, session_id)
    except KeyError as exc:
        raise HTTPException(404, "Chat session not found") from exc


@router.post("/platform/chat/sessions/{session_id}/messages", response_model=ChatSessionView)
def chat_message(session_id: str, request: ChatMessageCreate, principal: CurrentPrincipal) -> ChatSessionView:
    try:
        return context().platform.chat_message(principal, session_id, request)
    except KeyError as exc:
        raise HTTPException(404, "Chat session or attached system not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/platform/jobs", response_model=list[JobView])
def list_jobs(principal: CurrentPrincipal, limit: int = Query(default=100, ge=1, le=500)) -> list[JobView]:
    return context().platform.queue.list(principal.tenant_id, limit)


@router.get("/platform/audit", response_model=list[AuditEventView])
def list_audit(principal: CurrentPrincipal, limit: int = Query(default=100, ge=1, le=500)) -> list[AuditEventView]:
    return context().platform.audit.list(principal.tenant_id, limit)


@router.get("/platform/audit/verify")
def verify_audit(principal: CurrentPrincipal) -> dict[str, Any]:
    return context().platform.audit.verify(principal.tenant_id)


@router.get("/platform/liveness")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/platform/readiness")
def readiness() -> JSONResponse:
    ctx = context()
    checks: dict[str, Any] = {
        "database": False,
        "token_key": True,
        "vault_keyring": bool(ctx.settings.vault_keys),
        "audit_anchor": True if ctx.settings.environment != "production" else ctx.platform.audit.anchor_store is not None,
        "bootstrap_closed": not ctx.settings.bootstrap_enabled,
        "bootstrap_available": ctx.settings.bootstrap_enabled,
        "bootstrap_token_required": ctx.settings.environment == "production" or bool(ctx.settings.bootstrap_token),
        "bootstrap_protected": not ctx.settings.bootstrap_enabled or ctx.settings.environment != "production" or bool(ctx.settings.bootstrap_token),
    }
    try:
        with ctx.database.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
            checks["schema_version"] = connection.execute(
                select(schema_migrations.c.version).order_by(schema_migrations.c.version.desc()).limit(1)
            ).scalar_one()
        checks["database"] = True
    except Exception:
        checks["database"] = False
    if ctx.settings.environment == "production":
        checks["token_key"] = production_key_configured("DIFOUNDRY_TOKEN_KEY")
        checks["vault_keyring"] = bool(
            production_key_configured("DIFOUNDRY_VAULT_KEYS")
            or production_key_configured("DIFOUNDRY_VAULT_KEY")
        )
        checks["audit_anchor"] = bool(
            ctx.settings.audit_anchor_path and production_key_configured("DIFOUNDRY_AUDIT_ANCHOR_KEY")
        )
    required = ("database", "token_key", "vault_keyring", "audit_anchor", "bootstrap_protected")
    ready = all(bool(checks[key]) for key in required)
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status_code=200 if ready else 503,
    )


@router.get("/platform/metrics", response_class=PlainTextResponse)
def metrics(principal: CurrentPrincipal) -> str:
    ctx = context()
    with ctx.database.connect() as connection:
        values = {
            "difoundry_tenant_systems": connection.execute(
                select(func.count()).select_from(systems).where(systems.c.tenant_id == principal.tenant_id)
            ).scalar_one(),
            "difoundry_tenant_connections": connection.execute(
                select(func.count()).select_from(connections).where(connections.c.tenant_id == principal.tenant_id)
            ).scalar_one(),
            "difoundry_tenant_jobs_active": connection.execute(
                select(func.count()).select_from(jobs).where(
                    jobs.c.tenant_id == principal.tenant_id,
                    jobs.c.status.in_(["queued", "running"]),
                )
            ).scalar_one(),
        }
    return "\n".join(
        ["# TYPE difoundry_tenant gauge", *[f"{key} {value}" for key, value in values.items()]]
    ) + "\n"


@router.get("/", include_in_schema=False)
def root_console() -> RedirectResponse:
    return RedirectResponse("/console", status_code=307)


@router.get("/console", include_in_schema=False)
def console() -> FileResponse:
    return FileResponse(_static_dir() / "index.html")


@router.get("/assets/{asset_name}", include_in_schema=False)
def console_asset(asset_name: str) -> FileResponse:
    if asset_name not in {"app.js", "styles.css"}:
        raise HTTPException(404)
    return FileResponse(_static_dir() / asset_name)


def _static_dir() -> Path:
    ctx = context()
    if ctx.settings.static_dir:
        return ctx.settings.static_dir
    return Path(__file__).resolve().parent.parent / "static"
