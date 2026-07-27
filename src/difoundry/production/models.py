from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(str, Enum):
    PLATFORM_ADMIN = "platform_admin"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Principal(StrictModel):
    user_id: str
    tenant_id: str
    tenant_slug: str
    email: str
    role: Role
    token_version: int = 1


class BootstrapRequest(StrictModel):
    tenant_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(StrictModel):
    tenant_slug: str = Field(min_length=1, max_length=180)
    email: str
    password: str


class TokenResponse(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    principal: Principal


class TenantCreate(StrictModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=180)
    admin_email: str = Field(min_length=3, max_length=254)
    admin_password: str = Field(min_length=12, max_length=256)


class TenantView(StrictModel):
    tenant_id: str
    name: str
    slug: str
    active: bool
    created_at: datetime


class UserCreate(StrictModel):
    email: str
    password: str = Field(min_length=12, max_length=256)
    role: Role = Role.OPERATOR


class UserUpdate(StrictModel):
    role: Role | None = None
    active: bool | None = None
    revoke_tokens: bool = False


class PasswordChange(StrictModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class UserView(StrictModel):
    user_id: str
    tenant_id: str
    email: str
    role: Role
    active: bool
    token_version: int
    created_at: datetime


class SystemCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    protocol: str = Field(default="rest", min_length=1, max_length=80)
    discovery_format: str | None = None
    specification: Any | None = None
    base_url: str | None = None
    credential_kind: str = "none"
    credentials: dict[str, Any] | None = None


class SystemView(StrictModel):
    system_id: str
    tenant_id: str
    name: str
    description: str
    protocol: str
    discovery_format: str | None
    status: str
    profile_id: str | None
    credential_kind: str
    has_credentials: bool
    base_url: str | None
    last_check_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class CredentialUpdate(StrictModel):
    kind: str = Field(min_length=1, max_length=80)
    values: dict[str, Any]


class ConnectionCreate(StrictModel):
    name: str = Field(min_length=1, max_length=180)
    source_system_id: str
    target_system_ids: list[str] = Field(min_length=1, max_length=32)
    goal: str = Field(min_length=3, max_length=8000)
    event_type: str = "*"
    source_object_id: str | None = None
    autonomy_level: int = Field(default=1, ge=0, le=4)


class ConnectionView(StrictModel):
    connection_id: str
    tenant_id: str
    name: str
    source_system_id: str
    target_system_ids: list[str]
    goal: str
    status: str
    health_score: float
    daughter_id: str | None
    contract_id: str | None
    event_type: str
    source_object_id: str | None
    autonomy_level: int
    last_run_at: datetime | None
    last_error: str | None
    error_count: int
    created_at: datetime
    updated_at: datetime


class ChatSessionCreate(StrictModel):
    title: str = Field(default="New integration", max_length=180)


class ChatMessageCreate(StrictModel):
    content: str = Field(min_length=1, max_length=12000)
    attached_system_ids: list[str] = Field(default_factory=list, max_length=32)


class ChatMessageView(StrictModel):
    message_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatSessionView(StrictModel):
    session_id: str
    tenant_id: str
    title: str
    status: str
    draft: dict[str, Any]
    messages: list[ChatMessageView]
    created_at: datetime
    updated_at: datetime


class JobView(StrictModel):
    job_id: str
    tenant_id: str
    kind: str
    status: str
    attempts: int
    max_attempts: int
    run_after: datetime
    leased_until: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class AuditEventView(StrictModel):
    audit_id: str
    sequence: int
    tenant_id: str
    user_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    details: dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: datetime


class Overview(StrictModel):
    systems: int
    connected_systems: int
    connections: int
    healthy_connections: int
    degraded_connections: int
    queued_jobs: int
    failed_jobs: int
    recent_audit: list[AuditEventView]


class VaultRotationRequest(StrictModel):
    target_key_version: int = Field(ge=1)


class WorkerResult(StrictModel):
    job_id: str
    status: str
    detail: str = ""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"
