#!/usr/bin/env python3
"""Migrate the Phase 6 production database from schema v1 to v2.

Take a verified backup first. SQLite file databases are copied automatically
unless --no-backup is supplied. PostgreSQL operators should use their normal
snapshot/backup tooling before running this script.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

from difoundry.production.audit import ZERO_HASH
from difoundry.production.database import (
    SCHEMA_VERSION,
    audit_events,
    audit_heads,
    encode_json,
    jobs,
    metadata,
    now_iso,
    rate_buckets,
    schema_migrations,
    secret_blobs,
    security_events,
    tenants,
    users,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database_url", help="SQLAlchemy database URL")
    parser.add_argument("--backup", type=Path, help="SQLite backup destination")
    parser.add_argument("--no-backup", action="store_true", help="Skip automatic SQLite backup")
    return parser.parse_args()


def sqlite_path(url: str) -> Path | None:
    prefixes = ("sqlite:///", "sqlite+pysqlite:///")
    for prefix in prefixes:
        if url.startswith(prefix):
            value = url[len(prefix):]
            if value != ":memory:":
                return Path(value).expanduser().resolve()
    return None


def backup_sqlite(url: str, destination: Path | None, no_backup: bool) -> None:
    source = sqlite_path(url)
    if source is None or no_backup:
        return
    if not source.exists():
        raise SystemExit(f"SQLite database does not exist: {source}")
    target = destination or source.with_suffix(source.suffix + ".v1-backup")
    if target.exists():
        raise SystemExit(f"Backup already exists: {target}")
    shutil.copy2(source, target)
    print(f"Backup written: {target}")


def current_version(connection: Any) -> int:
    inspector = inspect(connection)
    if not inspector.has_table("platform_schema_migrations"):
        raise RuntimeError("platform_schema_migrations is missing; this is not a supported v1 database")
    value = connection.execute(text("SELECT MAX(version) FROM platform_schema_migrations")).scalar()
    return int(value or 0)


def hash_audit_row(row: dict[str, Any], sequence: int, previous_hash: str) -> str:
    material = encode_json({
        "audit_id": row["audit_id"],
        "tenant_id": row["tenant_id"],
        "sequence": sequence,
        "user_id": row["user_id"],
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "details": __import__("json").loads(row["details_json"]),
        "previous_hash": previous_hash,
        "created_at": row["created_at"],
    })
    return hashlib.sha256(material.encode()).hexdigest()


def promote_platform_admin(connection: Any) -> None:
    row = connection.execute(text(
        "SELECT user_id FROM platform_users WHERE active = TRUE AND role = 'admin' "
        "ORDER BY created_at, user_id LIMIT 1"
    )).first()
    if row:
        connection.execute(
            text("UPDATE platform_users SET role = 'platform_admin', token_version = token_version + 1 WHERE user_id = :id"),
            {"id": row[0]},
        )


def migrate_sqlite(connection: Any) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")

    # Additive columns on tables whose keys do not change.
    connection.exec_driver_sql("ALTER TABLE platform_tenants ADD COLUMN active BOOLEAN NOT NULL DEFAULT 1")
    connection.exec_driver_sql("ALTER TABLE platform_tenants ADD COLUMN updated_at VARCHAR(40)")
    connection.exec_driver_sql("UPDATE platform_tenants SET updated_at = created_at WHERE updated_at IS NULL")
    connection.exec_driver_sql("ALTER TABLE platform_jobs ADD COLUMN lease_token VARCHAR(120)")

    # SQLite keeps explicit index names when a table is renamed; drop and recreate them.
    connection.exec_driver_sql("DROP INDEX IF EXISTS ix_platform_users_tenant_id")
    connection.exec_driver_sql("DROP INDEX IF EXISTS ix_platform_secret_blobs_tenant_id")
    connection.exec_driver_sql("DROP INDEX IF EXISTS ix_platform_audit_events_tenant_id")

    # Rebuild users to replace global email uniqueness with tenant-scoped uniqueness.
    connection.exec_driver_sql("ALTER TABLE platform_users RENAME TO platform_users_v1")
    users.create(connection)
    connection.exec_driver_sql("""
        INSERT INTO platform_users (
            user_id, tenant_id, email, password_hash, role, active,
            token_version, failed_login_count, locked_until, created_at, updated_at
        )
        SELECT user_id, tenant_id, lower(email), password_hash, role, active,
               1, 0, NULL, created_at, created_at
        FROM platform_users_v1
    """)

    # Rebuild the vault with a tenant-scoped composite key.
    connection.exec_driver_sql("ALTER TABLE platform_secret_blobs RENAME TO platform_secret_blobs_v1")
    secret_blobs.create(connection)
    connection.exec_driver_sql("""
        INSERT INTO platform_secret_blobs (
            tenant_id, secret_ref, resource_type, resource_id, ciphertext,
            nonce, key_version, created_at, updated_at
        )
        SELECT tenant_id, secret_ref, resource_type, resource_id, ciphertext,
               nonce, key_version, created_at, updated_at
        FROM platform_secret_blobs_v1
    """)

    # Rebuild and re-hash audit chains with a deterministic monotonic sequence.
    old_rows = list(connection.execute(text(
        "SELECT * FROM platform_audit_events ORDER BY tenant_id, created_at, audit_id"
    )).mappings())
    connection.exec_driver_sql("ALTER TABLE platform_audit_events RENAME TO platform_audit_events_v1")
    audit_events.create(connection)
    connection.execute(text("DELETE FROM platform_audit_heads"))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in old_rows:
        grouped[str(row["tenant_id"])].append(dict(row))
    for tenant_id, rows in grouped.items():
        previous = ZERO_HASH
        for sequence, row in enumerate(rows, start=1):
            event_hash = hash_audit_row(row, sequence, previous)
            connection.execute(audit_events.insert().values(
                audit_id=row["audit_id"], tenant_id=tenant_id, sequence=sequence,
                user_id=row["user_id"], action=row["action"],
                resource_type=row["resource_type"], resource_id=row["resource_id"],
                details_json=row["details_json"], previous_hash=previous,
                event_hash=event_hash, created_at=row["created_at"],
            ))
            previous = event_hash
        connection.execute(audit_heads.insert().values(
            tenant_id=tenant_id, head_hash=previous, version=len(rows)
        ))

    security_events.create(connection)
    rate_buckets.create(connection)
    connection.exec_driver_sql("DROP TABLE IF EXISTS platform_rate_windows")
    connection.exec_driver_sql("DROP TABLE IF EXISTS platform_idempotency")
    connection.exec_driver_sql("DROP TABLE platform_users_v1")
    connection.exec_driver_sql("DROP TABLE platform_secret_blobs_v1")
    connection.exec_driver_sql("DROP TABLE platform_audit_events_v1")
    promote_platform_admin(connection)
    connection.execute(schema_migrations.insert().values(version=SCHEMA_VERSION, applied_at=now_iso()))
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def drop_single_column_email_unique(connection: Any) -> None:
    inspector = inspect(connection)
    for constraint in inspector.get_unique_constraints("platform_users"):
        if constraint.get("column_names") == ["email"] and constraint.get("name"):
            name = constraint["name"].replace('"', '""')
            connection.exec_driver_sql(f'ALTER TABLE platform_users DROP CONSTRAINT "{name}"')


def primary_key_name(connection: Any, table_name: str) -> str:
    value = inspect(connection).get_pk_constraint(table_name).get("name")
    if not value:
        raise RuntimeError(f"Could not determine primary-key constraint for {table_name}")
    return str(value).replace('"', '""')


def migrate_postgresql(connection: Any) -> None:
    connection.exec_driver_sql("ALTER TABLE platform_tenants ADD COLUMN active BOOLEAN NOT NULL DEFAULT TRUE")
    connection.exec_driver_sql("ALTER TABLE platform_tenants ADD COLUMN updated_at VARCHAR(40)")
    connection.exec_driver_sql("UPDATE platform_tenants SET updated_at = created_at WHERE updated_at IS NULL")
    connection.exec_driver_sql("ALTER TABLE platform_tenants ALTER COLUMN updated_at SET NOT NULL")

    connection.exec_driver_sql("ALTER TABLE platform_users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 1")
    connection.exec_driver_sql("ALTER TABLE platform_users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0")
    connection.exec_driver_sql("ALTER TABLE platform_users ADD COLUMN locked_until VARCHAR(40)")
    connection.exec_driver_sql("ALTER TABLE platform_users ADD COLUMN updated_at VARCHAR(40)")
    connection.exec_driver_sql("UPDATE platform_users SET email = lower(email), updated_at = created_at")
    connection.exec_driver_sql("ALTER TABLE platform_users ALTER COLUMN updated_at SET NOT NULL")
    drop_single_column_email_unique(connection)
    connection.exec_driver_sql(
        "ALTER TABLE platform_users ADD CONSTRAINT uq_platform_users_tenant_email UNIQUE (tenant_id, email)"
    )

    connection.exec_driver_sql("ALTER TABLE platform_jobs ADD COLUMN lease_token VARCHAR(120)")
    pk_name = primary_key_name(connection, "platform_secret_blobs")
    connection.exec_driver_sql(f'ALTER TABLE platform_secret_blobs DROP CONSTRAINT "{pk_name}"')
    connection.exec_driver_sql(
        "ALTER TABLE platform_secret_blobs ADD CONSTRAINT pk_platform_secret_blobs PRIMARY KEY (tenant_id, secret_ref)"
    )

    connection.exec_driver_sql("ALTER TABLE platform_audit_events ADD COLUMN sequence INTEGER")
    old_rows = list(connection.execute(text(
        "SELECT * FROM platform_audit_events ORDER BY tenant_id, created_at, audit_id"
    )).mappings())
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in old_rows:
        grouped[str(row["tenant_id"])].append(dict(row))
    connection.execute(text("DELETE FROM platform_audit_heads"))
    for tenant_id, rows in grouped.items():
        previous = ZERO_HASH
        for sequence, row in enumerate(rows, start=1):
            event_hash = hash_audit_row(row, sequence, previous)
            connection.execute(text("""
                UPDATE platform_audit_events
                SET sequence=:sequence, previous_hash=:previous_hash, event_hash=:event_hash
                WHERE audit_id=:audit_id
            """), {
                "sequence": sequence, "previous_hash": previous,
                "event_hash": event_hash, "audit_id": row["audit_id"],
            })
            previous = event_hash
        connection.execute(text("""
            INSERT INTO platform_audit_heads (tenant_id, head_hash, version)
            VALUES (:tenant_id, :head_hash, :version)
        """), {"tenant_id": tenant_id, "head_hash": previous, "version": len(rows)})
    connection.exec_driver_sql("ALTER TABLE platform_audit_events ALTER COLUMN sequence SET NOT NULL")
    connection.exec_driver_sql(
        "ALTER TABLE platform_audit_events ADD CONSTRAINT uq_audit_tenant_sequence UNIQUE (tenant_id, sequence)"
    )

    security_events.create(connection)
    rate_buckets.create(connection)
    connection.exec_driver_sql("DROP TABLE IF EXISTS platform_rate_windows")
    connection.exec_driver_sql("DROP TABLE IF EXISTS platform_idempotency")
    promote_platform_admin(connection)
    connection.execute(schema_migrations.insert().values(version=SCHEMA_VERSION, applied_at=now_iso()))


def main() -> int:
    args = parse_args()
    backup_sqlite(args.database_url, args.backup, args.no_backup)
    engine = create_engine(args.database_url, future=True)
    with engine.begin() as connection:
        version = current_version(connection)
        if version == SCHEMA_VERSION:
            print(f"Database is already at schema v{SCHEMA_VERSION}")
            return 0
        if version != 1:
            raise RuntimeError(f"Only schema v1 is supported; found v{version}")
        if connection.dialect.name == "sqlite":
            migrate_sqlite(connection)
        elif connection.dialect.name == "postgresql":
            migrate_postgresql(connection)
        else:
            raise RuntimeError(f"Unsupported migration dialect: {connection.dialect.name}")
    print(f"Migration complete: schema v1 -> v{SCHEMA_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
