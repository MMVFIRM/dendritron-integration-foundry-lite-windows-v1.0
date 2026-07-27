from __future__ import annotations

import json
import sqlite3
from uuid import uuid4
from contextlib import contextmanager
from threading import RLock
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    insert,
    inspect,
    select,
)
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.pool import QueuePool

SCHEMA_VERSION = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


metadata = MetaData()

tenants = Table(
    "platform_tenants", metadata,
    Column("tenant_id", String(80), primary_key=True),
    Column("name", String(160), nullable=False),
    Column("slug", String(180), nullable=False, unique=True),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
users = Table(
    "platform_users", metadata,
    Column("user_id", String(80), primary_key=True),
    Column("tenant_id", String(80), nullable=False, index=True),
    Column("email", String(254), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("role", String(32), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("token_version", Integer, nullable=False, default=1),
    Column("failed_login_count", Integer, nullable=False, default=0),
    Column("locked_until", String(40)),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    UniqueConstraint("tenant_id", "email", name="uq_platform_users_tenant_email"),
)
systems = Table(
    "platform_systems", metadata,
    Column("system_id", String(80), primary_key=True),
    Column("tenant_id", String(80), nullable=False, index=True),
    Column("name", String(160), nullable=False),
    Column("description", Text, nullable=False),
    Column("protocol", String(80), nullable=False),
    Column("discovery_format", String(80)),
    Column("status", String(40), nullable=False),
    Column("profile_id", String(180)),
    Column("profile_json", Text),
    Column("specification_json", Text),
    Column("credential_kind", String(80), nullable=False),
    Column("secret_ref", String(100)),
    Column("base_url", Text),
    Column("last_check_at", String(40)),
    Column("last_error", Text),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
connections = Table(
    "platform_connections", metadata,
    Column("connection_id", String(80), primary_key=True),
    Column("tenant_id", String(80), nullable=False, index=True),
    Column("name", String(180), nullable=False),
    Column("source_system_id", String(80), nullable=False),
    Column("target_system_ids_json", Text, nullable=False),
    Column("goal", Text, nullable=False),
    Column("status", String(40), nullable=False),
    Column("health_score", String(32), nullable=False),
    Column("daughter_id", String(120)),
    Column("contract_id", String(180)),
    Column("contract_json", Text),
    Column("composition_json", Text),
    Column("event_type", String(120), nullable=False),
    Column("source_object_id", String(180)),
    Column("autonomy_level", Integer, nullable=False),
    Column("last_run_at", String(40)),
    Column("last_error", Text),
    Column("error_count", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
chat_sessions = Table(
    "platform_chat_sessions", metadata,
    Column("session_id", String(80), primary_key=True),
    Column("tenant_id", String(80), nullable=False, index=True),
    Column("user_id", String(80), nullable=False),
    Column("title", String(180), nullable=False),
    Column("status", String(40), nullable=False),
    Column("draft_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
chat_messages = Table(
    "platform_chat_messages", metadata,
    Column("message_id", String(80), primary_key=True),
    Column("session_id", String(80), nullable=False, index=True),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("metadata_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)
jobs = Table(
    "platform_jobs", metadata,
    Column("job_id", String(80), primary_key=True),
    Column("tenant_id", String(80), nullable=False, index=True),
    Column("kind", String(80), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("status", String(32), nullable=False, index=True),
    Column("attempts", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("run_after", String(40), nullable=False),
    Column("lease_owner", String(120)),
    Column("lease_token", String(120)),
    Column("leased_until", String(40)),
    Column("last_error", Text),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
secret_blobs = Table(
    "platform_secret_blobs", metadata,
    Column("tenant_id", String(80), primary_key=True),
    Column("secret_ref", String(100), primary_key=True),
    Column("resource_type", String(80), nullable=False),
    Column("resource_id", String(100), nullable=False),
    Column("ciphertext", Text, nullable=False),
    Column("nonce", Text, nullable=False),
    Column("key_version", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
audit_events = Table(
    "platform_audit_events", metadata,
    Column("audit_id", String(80), primary_key=True),
    Column("tenant_id", String(80), nullable=False, index=True),
    Column("sequence", Integer, nullable=False),
    Column("user_id", String(80)),
    Column("action", String(160), nullable=False),
    Column("resource_type", String(80), nullable=False),
    Column("resource_id", String(100)),
    Column("details_json", Text, nullable=False),
    Column("previous_hash", String(64), nullable=False),
    Column("event_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("tenant_id", "sequence", name="uq_audit_tenant_sequence"),
)
audit_heads = Table(
    "platform_audit_heads", metadata,
    Column("tenant_id", String(80), primary_key=True),
    Column("head_hash", String(64), nullable=False),
    Column("version", Integer, nullable=False),
)
security_events = Table(
    "platform_security_events", metadata,
    Column("event_id", String(80), primary_key=True),
    Column("tenant_slug", String(180)),
    Column("email_hash", String(64), nullable=False),
    Column("action", String(80), nullable=False),
    Column("client_id", String(180), nullable=False),
    Column("details_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)
rate_buckets = Table(
    "platform_rate_buckets", metadata,
    Column("bucket_key", String(240), primary_key=True),
    Column("tokens", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
schema_migrations = Table(
    "platform_schema_migrations", metadata,
    Column("version", Integer, primary_key=True),
    Column("applied_at", String(40), nullable=False),
)


class PlatformDatabase:
    def __init__(self, url: str):
        is_sqlite = url.startswith("sqlite")
        is_memory_sqlite = is_sqlite and (":memory:" in url or url.rstrip("/") in {"sqlite:", "sqlite+pysqlite:"})
        self.url = url
        self._sqlite_write_lock = RLock() if is_sqlite else None
        if is_memory_sqlite:
            # A plain SQLite :memory: database is private to one DBAPI connection.
            # Uvicorn runs sync endpoints in a thread pool, so use a uniquely named
            # shared-cache memory database with a real connection pool. This keeps
            # the no-file development default while allowing request threads to see
            # one schema and use independent transactions.
            memory_uri = f"file:difoundry_{uuid4().hex}?mode=memory&cache=shared"
            self.engine = create_engine(
                "sqlite+pysqlite://",
                future=True,
                pool_pre_ping=True,
                poolclass=QueuePool,
                pool_size=8,
                max_overflow=24,
                creator=lambda: sqlite3.connect(
                    memory_uri,
                    uri=True,
                    check_same_thread=False,
                    timeout=30,
                ),
            )
        else:
            connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
            self.engine = create_engine(
                url,
                future=True,
                pool_pre_ping=True,
                connect_args=connect_args,
            )
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        inspector = inspect(self.engine)
        if inspector.has_table("platform_schema_migrations"):
            with self.engine.connect() as connection:
                current = connection.execute(select(schema_migrations.c.version).order_by(schema_migrations.c.version.desc()).limit(1)).scalar()
            if current and int(current) < SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema v{current} requires migration to v{SCHEMA_VERSION}; "
                    "run scripts/migrate_v1_to_v2.py after taking a backup"
                )
        metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            current = connection.execute(select(schema_migrations.c.version).where(schema_migrations.c.version == SCHEMA_VERSION)).first()
            if current is None:
                connection.execute(insert(schema_migrations).values(version=SCHEMA_VERSION, applied_at=now_iso()))

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        if self._sqlite_write_lock is None:
            with self.engine.connect() as connection:
                yield connection
            return
        # Shared-memory SQLite uses table-level locks. Serialize reads and writes
        # in the development backend so request threads cannot strand one another
        # behind transient table locks. PostgreSQL remains fully concurrent.
        with self._sqlite_write_lock:
            with self.engine.connect() as connection:
                yield connection

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        if self._sqlite_write_lock is None:
            with self.engine.begin() as connection:
                yield connection
            return
        with self._sqlite_write_lock:
            with self.engine.begin() as connection:
                yield connection

    def fetch_one(self, table: Table, *conditions: Any) -> RowMapping | None:
        with self.connect() as connection:
            row = connection.execute(select(table).where(*conditions)).mappings().first()
            return row

    def fetch_all(self, table: Table, *conditions: Any, limit: int | None = None) -> list[RowMapping]:
        statement = select(table).where(*conditions)
        if limit:
            statement = statement.limit(limit)
        with self.connect() as connection:
            return list(connection.execute(statement).mappings().all())


def encode_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def decode_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)
