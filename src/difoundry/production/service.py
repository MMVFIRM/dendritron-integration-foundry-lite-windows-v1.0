from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import delete, func, insert, select, update

from ..composition import CompositionError, DaughterComposer
from ..discovery import DiscoveryService
from ..models import CompositionRequest, DiscoverySource, SystemProfile, TargetIntent
from .audit import AuditAnchorStore, AuditLedger
from .chat import ChatPlanner
from .database import (
    PlatformDatabase, chat_messages, chat_sessions, connections, decode_json, encode_json,
    jobs, now_iso, security_events, systems, tenants, users,
)
from .models import (
    AuditEventView, BootstrapRequest, ChatMessageCreate, ChatMessageView, ChatSessionView,
    ConnectionCreate, ConnectionView, LoginRequest, Overview, PasswordChange, Principal, Role,
    SystemCreate, SystemView, TenantCreate, TenantView, TokenResponse, UserCreate, UserUpdate,
    UserView, new_id,
)
from .queue import DurableJobQueue
from .security import EncryptedSecretStore, PasswordService, TokenService


class PlatformError(ValueError):
    pass


class ProductionPlatform:
    def __init__(
        self,
        database: PlatformDatabase,
        token_service: TokenService,
        vault: EncryptedSecretStore,
        *,
        audit_anchor_store: AuditAnchorStore | None = None,
        password_time_cost: int = 3,
        password_memory_cost: int = 65536,
        password_parallelism: int = 2,
        login_lockout_threshold: int = 8,
        login_lockout_seconds: int = 900,
        strict_external_url_validation: bool = False,
    ):
        self.database = database
        self.tokens = token_service
        self.vault = vault
        self.passwords = PasswordService(password_time_cost, password_memory_cost, password_parallelism)
        self.audit = AuditLedger(database, audit_anchor_store)
        self.login_lockout_threshold = login_lockout_threshold
        self.login_lockout_seconds = login_lockout_seconds
        self.strict_external_url_validation = strict_external_url_validation
        self.queue = DurableJobQueue(database)
        self.discovery = DiscoveryService()
        self.composer = DaughterComposer()
        self.chat = ChatPlanner()

    def bootstrap(self, request: BootstrapRequest, enabled: bool = True) -> TokenResponse:
        if not enabled:
            raise PlatformError("Bootstrap is disabled")
        with self.database.connect() as connection:
            count = connection.execute(select(func.count()).select_from(tenants)).scalar_one()
        if count:
            raise PlatformError("Platform has already been bootstrapped")
        tenant_id = new_id("ten")
        user_id = new_id("usr")
        stamp = now_iso()
        slug = _slug(request.tenant_name)
        with self.database.begin() as connection:
            connection.execute(insert(tenants).values(
                tenant_id=tenant_id, name=request.tenant_name, slug=slug, active=True,
                created_at=stamp, updated_at=stamp,
            ))
            connection.execute(insert(users).values(
                user_id=user_id, tenant_id=tenant_id, email=request.email.lower(),
                password_hash=self.passwords.hash(request.password), role=Role.PLATFORM_ADMIN.value,
                active=True, token_version=1, failed_login_count=0, locked_until=None,
                created_at=stamp, updated_at=stamp,
            ))
        principal = Principal(
            user_id=user_id, tenant_id=tenant_id, tenant_slug=slug, email=request.email.lower(),
            role=Role.PLATFORM_ADMIN, token_version=1,
        )
        self.audit.append(tenant_id, user_id, "platform.bootstrap", "tenant", tenant_id, {"tenant_name": request.tenant_name})
        return TokenResponse(access_token=self.tokens.issue(principal), expires_in=self.tokens.ttl_seconds, principal=principal)

    def create_tenant(self, principal: Principal, request: TenantCreate) -> TenantView:
        self._require(principal, Role.PLATFORM_ADMIN)
        tenant_id = new_id("ten")
        user_id = new_id("usr")
        stamp = now_iso()
        slug = _slug(request.slug or request.name)
        try:
            with self.database.begin() as connection:
                connection.execute(insert(tenants).values(
                    tenant_id=tenant_id, name=request.name, slug=slug, active=True,
                    created_at=stamp, updated_at=stamp,
                ))
                connection.execute(insert(users).values(
                    user_id=user_id, tenant_id=tenant_id, email=request.admin_email.lower(),
                    password_hash=self.passwords.hash(request.admin_password), role=Role.ADMIN.value,
                    active=True, token_version=1, failed_login_count=0, locked_until=None,
                    created_at=stamp, updated_at=stamp,
                ))
        except Exception as exc:
            raise PlatformError("Tenant slug or tenant-local administrator already exists") from exc
        self.audit.append(principal.tenant_id, principal.user_id, "tenant.create", "tenant", tenant_id, {"slug": slug})
        self.audit.append(tenant_id, user_id, "tenant.provisioned", "tenant", tenant_id, {"created_by": principal.user_id})
        return TenantView(tenant_id=tenant_id, name=request.name, slug=slug, active=True, created_at=stamp)

    def list_tenants(self, principal: Principal) -> list[TenantView]:
        self._require(principal, Role.PLATFORM_ADMIN)
        rows = self.database.fetch_all(tenants)
        return [TenantView(tenant_id=r["tenant_id"], name=r["name"], slug=r["slug"], active=bool(r["active"]), created_at=r["created_at"]) for r in rows]

    def login(self, request: LoginRequest, *, client_id: str = "unknown") -> TokenResponse:
        tenant = self.database.fetch_one(tenants, tenants.c.slug == _slug(request.tenant_slug), tenants.c.active == True)
        row = None
        if tenant is not None:
            row = self.database.fetch_one(
                users, users.c.tenant_id == tenant["tenant_id"], users.c.email == request.email.lower()
            )
        now = datetime.now(timezone.utc)
        locked_until = datetime.fromisoformat(row["locked_until"]) if row and row["locked_until"] else None
        locked = bool(locked_until and locked_until > now)
        # Always execute Argon2 work, including locked and unknown accounts, so
        # response timing does not become an account-existence oracle.
        valid_password = self.passwords.verify(row["password_hash"] if row else None, request.password)
        valid = bool(row and row["active"] and not locked and valid_password)
        if not valid:
            final_locked = locked
            if row is not None:
                # Re-read under a row lock so concurrent guesses cannot lose
                # increments or race a threshold crossing. SQLite serializes this
                # transaction through PlatformDatabase; PostgreSQL uses FOR UPDATE.
                with self.database.begin() as connection:
                    current = connection.execute(
                        select(users).where(users.c.user_id == row["user_id"]).with_for_update()
                    ).mappings().one()
                    current_locked_until = (
                        datetime.fromisoformat(current["locked_until"]) if current["locked_until"] else None
                    )
                    attempt_during_lock = bool(current_locked_until and current_locked_until > now)
                    if attempt_during_lock:
                        failures = max(int(current["failed_login_count"]), self.login_lockout_threshold)
                        next_locked_until = current["locked_until"]
                    else:
                        prior_failures = (
                            0
                            if current_locked_until and current_locked_until <= now
                            else int(current["failed_login_count"])
                        )
                        failures = prior_failures + 1
                        next_locked_until = None
                        if failures >= self.login_lockout_threshold:
                            failures = self.login_lockout_threshold
                            next_locked_until = (
                                now + timedelta(seconds=self.login_lockout_seconds)
                            ).isoformat()
                    connection.execute(
                        update(users)
                        .where(users.c.user_id == row["user_id"])
                        .values(
                            failed_login_count=failures,
                            locked_until=next_locked_until,
                            updated_at=now_iso(),
                        )
                    )
                final_locked = bool(next_locked_until)
                self.audit.append(
                    row["tenant_id"], row["user_id"], "auth.login_failed", "user", row["user_id"],
                    {
                        "client_id": client_id,
                        "locked": final_locked,
                        "attempt_during_lock": attempt_during_lock,
                    },
                )
            self._security_event(
                request.tenant_slug,
                request.email,
                "auth.login_failed",
                client_id,
                {"tenant_resolved": tenant is not None, "locked": final_locked},
            )
            raise PlatformError("Invalid tenant, email, or password")
        # Revalidate success under the same row lock used by failure updates. A
        # correct-password request that races the threshold crossing must not
        # clear a lock based on the stale pre-Argon2 snapshot.
        revalidation_failed = False
        with self.database.begin() as connection:
            current = connection.execute(
                select(users).where(users.c.user_id == row["user_id"]).with_for_update()
            ).mappings().one()
            current_locked_until = (
                datetime.fromisoformat(current["locked_until"]) if current["locked_until"] else None
            )
            revalidation_failed = bool(
                not current["active"]
                or (current_locked_until and current_locked_until > datetime.now(timezone.utc))
                or current["password_hash"] != row["password_hash"]
            )
            if not revalidation_failed:
                connection.execute(update(users).where(users.c.user_id == row["user_id"]).values(
                    failed_login_count=0, locked_until=None, updated_at=now_iso()
                ))
        if revalidation_failed:
            self._security_event(
                request.tenant_slug, request.email, "auth.login_failed", client_id,
                {"tenant_resolved": True, "locked": bool(current_locked_until)},
            )
            self.audit.append(
                current["tenant_id"], current["user_id"], "auth.login_failed", "user", current["user_id"],
                {"client_id": client_id, "locked": bool(current_locked_until), "success_revalidation_failed": True},
            )
            raise PlatformError("Invalid tenant, email, or password")
        principal = Principal(
            user_id=current["user_id"], tenant_id=current["tenant_id"], tenant_slug=tenant["slug"],
            email=current["email"], role=Role(current["role"]), token_version=int(current["token_version"]),
        )
        self.audit.append(principal.tenant_id, principal.user_id, "auth.login", "user", principal.user_id, {"client_id": client_id})
        return TokenResponse(access_token=self.tokens.issue(principal), expires_in=self.tokens.ttl_seconds, principal=principal)

    def validate_principal(self, token_principal: Principal) -> Principal:
        row = self.database.fetch_one(users, users.c.user_id == token_principal.user_id, users.c.tenant_id == token_principal.tenant_id)
        tenant = self.database.fetch_one(tenants, tenants.c.tenant_id == token_principal.tenant_id)
        if row is None or tenant is None or not row["active"] or not tenant["active"]:
            raise PlatformError("Principal is inactive")
        if int(row["token_version"]) != token_principal.token_version:
            raise PlatformError("Token has been revoked")
        if row["role"] != token_principal.role.value or tenant["slug"] != token_principal.tenant_slug:
            raise PlatformError("Token claims are stale")
        return token_principal

    def create_user(self, principal: Principal, request: UserCreate) -> UserView:
        self._require(principal, Role.PLATFORM_ADMIN, Role.ADMIN)
        if request.role == Role.PLATFORM_ADMIN and principal.role != Role.PLATFORM_ADMIN:
            raise PermissionError("Only a platform administrator may grant platform_admin")
        user_id = new_id("usr")
        stamp = now_iso()
        try:
            with self.database.begin() as connection:
                connection.execute(insert(users).values(
                    user_id=user_id, tenant_id=principal.tenant_id, email=request.email.lower(),
                    password_hash=self.passwords.hash(request.password), role=request.role.value, active=True,
                    token_version=1, failed_login_count=0, locked_until=None, created_at=stamp, updated_at=stamp,
                ))
        except Exception as exc:
            raise PlatformError("User email already exists in this tenant") from exc
        self.audit.append(principal.tenant_id, principal.user_id, "user.create", "user", user_id, {"role": request.role.value})
        return UserView(user_id=user_id, tenant_id=principal.tenant_id, email=request.email.lower(), role=request.role, active=True, token_version=1, created_at=stamp)

    def list_users(self, principal: Principal) -> list[UserView]:
        self._require(principal, Role.PLATFORM_ADMIN, Role.ADMIN)
        rows = self.database.fetch_all(users, users.c.tenant_id == principal.tenant_id)
        return [self._user_view(r) for r in rows]

    def update_user(self, principal: Principal, user_id: str, request: UserUpdate) -> UserView:
        self._require(principal, Role.PLATFORM_ADMIN, Role.ADMIN)
        row = self._tenant_row(users, users.c.user_id, user_id, principal.tenant_id)
        values: dict[str, Any] = {"updated_at": now_iso()}
        if request.role is not None:
            if request.role == Role.PLATFORM_ADMIN and principal.role != Role.PLATFORM_ADMIN:
                raise PermissionError("Only a platform administrator may grant platform_admin")
            values["role"] = request.role.value
        if request.active is not None:
            values["active"] = request.active
        if request.revoke_tokens or request.role is not None or request.active is False:
            values["token_version"] = int(row["token_version"]) + 1
        with self.database.begin() as connection:
            connection.execute(update(users).where(users.c.user_id == user_id, users.c.tenant_id == principal.tenant_id).values(**values))
        self.audit.append(principal.tenant_id, principal.user_id, "user.update", "user", user_id, {"role": request.role.value if request.role else None, "active": request.active, "tokens_revoked": "token_version" in values})
        return self._user_view(self._tenant_row(users, users.c.user_id, user_id, principal.tenant_id))

    def delete_user(self, principal: Principal, user_id: str) -> None:
        self._require(principal, Role.PLATFORM_ADMIN, Role.ADMIN)
        if user_id == principal.user_id:
            raise PlatformError("Administrators cannot delete their own active account")
        self._tenant_row(users, users.c.user_id, user_id, principal.tenant_id)
        with self.database.begin() as connection:
            connection.execute(delete(users).where(users.c.user_id == user_id, users.c.tenant_id == principal.tenant_id))
        self.audit.append(principal.tenant_id, principal.user_id, "user.delete", "user", user_id)

    def change_password(self, principal: Principal, request: PasswordChange) -> None:
        row = self._tenant_row(users, users.c.user_id, principal.user_id, principal.tenant_id)
        if not self.passwords.verify(row["password_hash"], request.current_password):
            raise PlatformError("Current password is invalid")
        with self.database.begin() as connection:
            connection.execute(update(users).where(users.c.user_id == principal.user_id).values(
                password_hash=self.passwords.hash(request.new_password), token_version=int(row["token_version"]) + 1, updated_at=now_iso()
            ))
        self.audit.append(principal.tenant_id, principal.user_id, "auth.password_changed", "user", principal.user_id)

    def revoke_tokens(self, principal: Principal) -> None:
        row = self._tenant_row(users, users.c.user_id, principal.user_id, principal.tenant_id)
        with self.database.begin() as connection:
            connection.execute(update(users).where(users.c.user_id == principal.user_id).values(
                token_version=int(row["token_version"]) + 1, updated_at=now_iso()
            ))
        self.audit.append(principal.tenant_id, principal.user_id, "auth.tokens_revoked", "user", principal.user_id)

    def create_system(self, principal: Principal, request: SystemCreate) -> SystemView:
        self._require(principal, Role.PLATFORM_ADMIN, Role.ADMIN, Role.OPERATOR)
        if request.base_url:
            validate_external_url(request.base_url, resolve_dns=self.strict_external_url_validation)
        system_id = new_id("sys")
        stamp = now_iso()
        status = "needs_discovery"
        profile_json = None
        profile_id = None
        last_error = None
        specification_json = encode_json(request.specification) if request.specification is not None else None
        protocol = request.protocol
        if request.specification is not None and request.discovery_format:
            try:
                result = self.discovery.discover(DiscoverySource(
                    format=request.discovery_format, document=request.specification, system_id=system_id,
                    name=request.name, base_url=request.base_url, metadata={"tenant_scoped": True},
                ))
                profile_json = result.profile.model_dump_json()
                profile_id = result.profile.system_id
                protocol = result.profile.protocol
                status = "configured" if request.credential_kind == "none" else "credentials_required"
            except Exception as exc:
                status = "discovery_failed"
                last_error = str(exc)
        secret_ref = None
        if request.credentials:
            secret_ref = self.vault.put(principal.tenant_id, "system", system_id, request.credentials).secret_ref
            if profile_json:
                status = "configured"
        values = dict(system_id=system_id, tenant_id=principal.tenant_id, name=request.name,
                      description=request.description, protocol=protocol, discovery_format=request.discovery_format,
                      status=status, profile_id=profile_id, profile_json=profile_json,
                      specification_json=specification_json, credential_kind=request.credential_kind,
                      secret_ref=secret_ref, base_url=request.base_url, last_check_at=None, last_error=last_error,
                      created_at=stamp, updated_at=stamp)
        with self.database.begin() as connection:
            connection.execute(insert(systems).values(**values))
        self.audit.append(principal.tenant_id, principal.user_id, "system.create", "system", system_id,
                          {"protocol": protocol, "discovery_format": request.discovery_format, "has_credentials": bool(secret_ref)})
        return self._system_view(values)

    def list_systems(self, principal: Principal) -> list[SystemView]:
        rows = self.database.fetch_all(systems, systems.c.tenant_id == principal.tenant_id)
        return [self._system_view(row) for row in rows]

    def get_system(self, principal: Principal, system_id: str) -> SystemView:
        row = self._tenant_row(systems, systems.c.system_id, system_id, principal.tenant_id)
        return self._system_view(row)

    def set_credentials(self, principal: Principal, system_id: str, kind: str, values: dict[str, Any]) -> SystemView:
        self._require(principal, Role.PLATFORM_ADMIN, Role.ADMIN, Role.OPERATOR)
        row = self._tenant_row(systems, systems.c.system_id, system_id, principal.tenant_id)
        envelope = self.vault.put(principal.tenant_id, "system", system_id, values, row["secret_ref"])
        status = "configured" if row["profile_json"] else "needs_discovery"
        with self.database.begin() as connection:
            connection.execute(update(systems).where(systems.c.system_id == system_id, systems.c.tenant_id == principal.tenant_id)
                               .values(secret_ref=envelope.secret_ref, credential_kind=kind, status=status, updated_at=now_iso()))
        self.audit.append(principal.tenant_id, principal.user_id, "system.credentials.update", "system", system_id,
                          {"credential_kind": kind, "secret_ref": envelope.secret_ref})
        return self.get_system(principal, system_id)

    def create_connection(self, principal: Principal, request: ConnectionCreate) -> ConnectionView:
        self._require(principal, Role.PLATFORM_ADMIN, Role.ADMIN, Role.OPERATOR)
        source = self._tenant_row(systems, systems.c.system_id, request.source_system_id, principal.tenant_id)
        targets = [self._tenant_row(systems, systems.c.system_id, item, principal.tenant_id) for item in request.target_system_ids]
        if source["system_id"] in {row["system_id"] for row in targets}:
            raise PlatformError("Source and target systems must be distinct")
        connection_id = new_id("con")
        stamp = now_iso()
        values = dict(connection_id=connection_id, tenant_id=principal.tenant_id, name=request.name,
                      source_system_id=request.source_system_id, target_system_ids_json=encode_json(request.target_system_ids),
                      goal=request.goal, status="queued", health_score="0.0", daughter_id=None, contract_id=None,
                      contract_json=None, composition_json=None, event_type=request.event_type,
                      source_object_id=request.source_object_id, autonomy_level=request.autonomy_level,
                      last_run_at=None, last_error=None, error_count=0, created_at=stamp, updated_at=stamp)
        with self.database.begin() as connection:
            connection.execute(insert(connections).values(**values))
        job = self.queue.enqueue(principal.tenant_id, "compose_connection", {"connection_id": connection_id})
        self.audit.append(principal.tenant_id, principal.user_id, "connection.create", "connection", connection_id,
                          {"source": request.source_system_id, "targets": request.target_system_ids, "job_id": job.job_id})
        return self._connection_view(values)

    def list_connections(self, principal: Principal) -> list[ConnectionView]:
        rows = self.database.fetch_all(connections, connections.c.tenant_id == principal.tenant_id)
        return [self._connection_view(row) for row in rows]

    def get_connection(self, principal: Principal, connection_id: str) -> ConnectionView:
        return self._connection_view(self._tenant_row(connections, connections.c.connection_id, connection_id, principal.tenant_id))

    def queue_health_check(self, principal: Principal, connection_id: str) -> dict[str, str]:
        self._require(principal, Role.PLATFORM_ADMIN, Role.ADMIN, Role.OPERATOR)
        self._tenant_row(connections, connections.c.connection_id, connection_id, principal.tenant_id)
        job = self.queue.enqueue(principal.tenant_id, "connection_health", {"connection_id": connection_id})
        return {"job_id": job.job_id, "status": job.status}

    def create_chat_session(self, principal: Principal, title: str) -> ChatSessionView:
        self._require(principal, Role.PLATFORM_ADMIN, Role.ADMIN, Role.OPERATOR)
        session_id = new_id("chat")
        stamp = now_iso()
        values = dict(session_id=session_id, tenant_id=principal.tenant_id, user_id=principal.user_id,
                      title=title, status="collecting", draft_json="{}", created_at=stamp, updated_at=stamp)
        with self.database.begin() as connection:
            connection.execute(insert(chat_sessions).values(**values))
        greeting = "Describe the systems you need connected and the business outcome. Attach registered systems when possible; I will surface ambiguities instead of guessing."
        self._append_chat(session_id, "assistant", greeting)
        self.audit.append(principal.tenant_id, principal.user_id, "chat.create", "chat_session", session_id, {"title": title})
        return self.get_chat_session(principal, session_id)

    def get_chat_session(self, principal: Principal, session_id: str) -> ChatSessionView:
        row = self._tenant_row(chat_sessions, chat_sessions.c.session_id, session_id, principal.tenant_id)
        with self.database.connect() as connection:
            messages = connection.execute(select(chat_messages).where(chat_messages.c.session_id == session_id)
                                          .order_by(chat_messages.c.created_at.asc())).mappings().all()
        return ChatSessionView(session_id=row["session_id"], tenant_id=row["tenant_id"], title=row["title"],
                               status=row["status"], draft=decode_json(row["draft_json"], {}),
                               messages=[ChatMessageView(message_id=m["message_id"], role=m["role"], content=m["content"],
                                                         metadata=decode_json(m["metadata_json"], {}), created_at=m["created_at"]) for m in messages],
                               created_at=row["created_at"], updated_at=row["updated_at"])

    def list_chat_sessions(self, principal: Principal) -> list[ChatSessionView]:
        rows = self.database.fetch_all(chat_sessions, chat_sessions.c.tenant_id == principal.tenant_id)
        return [self.get_chat_session(principal, row["session_id"]) for row in rows]

    def chat_message(self, principal: Principal, session_id: str, request: ChatMessageCreate) -> ChatSessionView:
        self._require(principal, Role.PLATFORM_ADMIN, Role.ADMIN, Role.OPERATOR)
        row = self._tenant_row(chat_sessions, chat_sessions.c.session_id, session_id, principal.tenant_id)
        self._append_chat(session_id, "user", request.content, {"attached_system_ids": request.attached_system_ids})
        system_rows = self.database.fetch_all(systems, systems.c.tenant_id == principal.tenant_id)
        system_summaries = [{"system_id": item["system_id"], "name": item["name"], "status": item["status"]} for item in system_rows]
        reply, draft, action = self.chat.respond(request.content, decode_json(row["draft_json"], {}), system_summaries, request.attached_system_ids)
        status = "ready" if action == "compose" else "collecting"
        metadata: dict[str, Any] = {}
        if action == "compose":
            source_id = draft["source_system_id"]
            target_ids = list(draft["target_system_ids"])
            connection = self.create_connection(principal, ConnectionCreate(
                name=row["title"], source_system_id=source_id, target_system_ids=target_ids,
                goal=draft["goal"], event_type=draft.get("event_type", "*"), autonomy_level=1,
            ))
            metadata["connection_id"] = connection.connection_id
            draft["connection_id"] = connection.connection_id
            status = "queued"
        self._append_chat(session_id, "assistant", reply, metadata)
        with self.database.begin() as connection:
            connection.execute(update(chat_sessions).where(chat_sessions.c.session_id == session_id)
                               .values(draft_json=encode_json(draft), status=status, updated_at=now_iso()))
        self.audit.append(principal.tenant_id, principal.user_id, "chat.message", "chat_session", session_id,
                          {"action": action, "attached_system_count": len(request.attached_system_ids)})
        return self.get_chat_session(principal, session_id)

    def overview(self, principal: Principal) -> Overview:
        system_rows = self.database.fetch_all(systems, systems.c.tenant_id == principal.tenant_id)
        connection_rows = self.database.fetch_all(connections, connections.c.tenant_id == principal.tenant_id)
        job_rows = self.database.fetch_all(jobs, jobs.c.tenant_id == principal.tenant_id)
        return Overview(
            systems=len(system_rows), connected_systems=sum(row["status"] == "configured" for row in system_rows),
            connections=len(connection_rows), healthy_connections=sum(float(row["health_score"]) >= 0.9 for row in connection_rows),
            degraded_connections=sum(row["status"] in {"degraded", "failed", "review_required"} for row in connection_rows),
            queued_jobs=sum(row["status"] in {"queued", "running"} for row in job_rows),
            failed_jobs=sum(row["status"] == "dead" for row in job_rows), recent_audit=self.audit.list(principal.tenant_id, 8),
        )

    def execute_job(self, job_kind: str, tenant_id: str, payload: dict[str, Any]) -> str:
        if job_kind == "compose_connection":
            return self._compose_connection(tenant_id, payload["connection_id"])
        if job_kind == "connection_health":
            return self._health_connection(tenant_id, payload["connection_id"])
        raise PlatformError(f"Unsupported job kind: {job_kind}")

    def _compose_connection(self, tenant_id: str, connection_id: str) -> str:
        row = self._tenant_row(connections, connections.c.connection_id, connection_id, tenant_id)
        source = self._tenant_row(systems, systems.c.system_id, row["source_system_id"], tenant_id)
        target_ids = decode_json(row["target_system_ids_json"], [])
        targets = [self._tenant_row(systems, systems.c.system_id, item, tenant_id) for item in target_ids]
        all_rows = [source, *targets]
        if any(not item["profile_json"] for item in all_rows):
            missing = [item["name"] for item in all_rows if not item["profile_json"]]
            self._set_connection(connection_id, tenant_id, status="needs_discovery", health_score="0.2",
                                 last_error=f"Missing discovered profile: {', '.join(missing)}")
            return "Connection requires system discovery"
        profiles = {item["profile_id"]: SystemProfile.model_validate_json(item["profile_json"]) for item in all_rows}
        source_profile = profiles[source["profile_id"]]
        source_object = row["source_object_id"] or (source_profile.objects[0].object_id if source_profile.objects else None)
        intents: list[TargetIntent] = []
        for item in targets:
            profile = profiles[item["profile_id"]]
            writable = [op for op in profile.operations if op.operation_kind in {"create", "update", "upsert", "publish", "custom"}]
            operation = writable[0] if writable else (profile.operations[0] if profile.operations else None)
            if operation is None:
                self._set_connection(connection_id, tenant_id, status="review_required", health_score="0.3",
                                     last_error=f"No writable operation discovered for {profile.name}")
                return "Target has no writable operation"
            intents.append(TargetIntent(target_system_id=profile.system_id, target_object_id=operation.object_id,
                                        operation_id=operation.operation_id))
        request = CompositionRequest(name=row["name"], source_system_id=source_profile.system_id,
                                     source_object_id=source_object, event_type=row["event_type"], targets=intents,
                                     metadata={"goal": row["goal"], "tenant_scoped": True})
        try:
            result = self.composer.compose(request, profiles)
        except CompositionError as exc:
            self._set_connection(connection_id, tenant_id, status="review_required", health_score="0.3", last_error=str(exc))
            return "Composition requires review"
        status = "shadow" if result.ready_for_verification and not result.questions else "review_required"
        score = "0.85" if status == "shadow" else "0.55"
        with self.database.begin() as connection:
            connection.execute(update(connections).where(connections.c.connection_id == connection_id, connections.c.tenant_id == tenant_id)
                               .values(status=status, health_score=score, daughter_id=result.daughter_manifest.daughter_id,
                                       contract_id=result.contract.contract_id, contract_json=result.contract.model_dump_json(),
                                       composition_json=result.model_dump_json(), last_error=None if status == "shadow" else "Semantic review required",
                                       updated_at=now_iso()))
        self.audit.append(tenant_id, None, "connection.composed", "connection", connection_id,
                          {"status": status, "questions": len(result.questions), "daughter_id": result.daughter_manifest.daughter_id})
        return f"Connection composed with status {status}"

    def _health_connection(self, tenant_id: str, connection_id: str) -> str:
        row = self._tenant_row(connections, connections.c.connection_id, connection_id, tenant_id)
        system_ids = [row["source_system_id"], *decode_json(row["target_system_ids_json"], [])]
        system_rows = [self._tenant_row(systems, systems.c.system_id, item, tenant_id) for item in system_ids]
        issues = []
        for item in system_rows:
            if not item["profile_json"]:
                issues.append(f"{item['name']}: missing profile")
            if item["credential_kind"] != "none" and not item["secret_ref"]:
                issues.append(f"{item['name']}: missing credentials")
        if issues:
            self._set_connection(connection_id, tenant_id, status="degraded", health_score="0.4",
                                 last_error="; ".join(issues), error_count=int(row["error_count"]) + 1,
                                 last_run_at=now_iso())
            return "Health check found configuration issues"
        status = "healthy" if row["contract_id"] else "needs_composition"
        score = "1.0" if status == "healthy" else "0.7"
        self._set_connection(connection_id, tenant_id, status=status, health_score=score, last_error=None, last_run_at=now_iso())
        return f"Connection is {status}"

    def _set_connection(self, connection_id: str, tenant_id: str, **values: Any) -> None:
        values["updated_at"] = now_iso()
        with self.database.begin() as connection:
            connection.execute(update(connections).where(connections.c.connection_id == connection_id,
                                                         connections.c.tenant_id == tenant_id).values(**values))

    def _append_chat(self, session_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        with self.database.begin() as connection:
            connection.execute(insert(chat_messages).values(message_id=new_id("msg"), session_id=session_id,
                role=role, content=content, metadata_json=encode_json(metadata or {}), created_at=now_iso()))

    def _tenant_row(self, table: Any, id_column: Any, resource_id: str, tenant_id: str) -> Any:
        row = self.database.fetch_one(table, id_column == resource_id, table.c.tenant_id == tenant_id)
        if row is None:
            raise KeyError("Resource not found")
        return row

    def _security_event(self, tenant_slug: str, email: str, action: str, client_id: str, details: dict[str, Any]) -> None:
        with self.database.begin() as connection:
            connection.execute(insert(security_events).values(
                event_id=new_id("sec_evt"), tenant_slug=_slug(tenant_slug),
                email_hash=hashlib.sha256(email.lower().encode()).hexdigest(), action=action,
                client_id=client_id[:180], details_json=encode_json(details), created_at=now_iso(),
            ))

    @staticmethod
    def _user_view(row: Any) -> UserView:
        return UserView(
            user_id=row["user_id"], tenant_id=row["tenant_id"], email=row["email"],
            role=Role(row["role"]), active=bool(row["active"]), token_version=int(row["token_version"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _require(principal: Principal, *roles: Role) -> None:
        if principal.role not in roles:
            raise PermissionError("Insufficient role")

    @staticmethod
    def _system_view(row: Any) -> SystemView:
        return SystemView(system_id=row["system_id"], tenant_id=row["tenant_id"], name=row["name"],
            description=row["description"], protocol=row["protocol"], discovery_format=row["discovery_format"],
            status=row["status"], profile_id=row["profile_id"], credential_kind=row["credential_kind"],
            has_credentials=bool(row["secret_ref"]), base_url=row["base_url"], last_check_at=row["last_check_at"],
            last_error=row["last_error"], created_at=row["created_at"], updated_at=row["updated_at"])

    @staticmethod
    def _connection_view(row: Any) -> ConnectionView:
        return ConnectionView(connection_id=row["connection_id"], tenant_id=row["tenant_id"], name=row["name"],
            source_system_id=row["source_system_id"], target_system_ids=decode_json(row["target_system_ids_json"], []),
            goal=row["goal"], status=row["status"], health_score=float(row["health_score"]), daughter_id=row["daughter_id"],
            contract_id=row["contract_id"], event_type=row["event_type"], source_object_id=row["source_object_id"],
            autonomy_level=int(row["autonomy_level"]), last_run_at=row["last_run_at"], last_error=row["last_error"],
            error_count=int(row["error_count"]), created_at=row["created_at"], updated_at=row["updated_at"])


def validate_external_url(value: str, *, resolve_dns: bool = False) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise PlatformError("Base URL must be an absolute HTTP(S) URL")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise PlatformError("Local addresses are not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if not resolve_dns:
            return
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        except OSError as exc:
            raise PlatformError("Base URL hostname could not be resolved") from exc
        if not addresses:
            raise PlatformError("Base URL hostname did not resolve")
        for address in addresses:
            resolved = ipaddress.ip_address(address)
            if resolved.is_private or resolved.is_loopback or resolved.is_link_local or resolved.is_multicast or resolved.is_reserved:
                raise PlatformError("Hostname resolves to a private or reserved network target")
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise PlatformError("Private or reserved network targets require an explicit egress policy")


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or hashlib.sha256(value.encode()).hexdigest()[:12]
