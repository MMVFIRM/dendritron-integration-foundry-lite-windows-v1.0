from __future__ import annotations

import json
import sqlite3
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS lite_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lite_workspace (
    workspace_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lite_systems (
    system_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    auth_kind TEXT NOT NULL,
    secret_ref TEXT,
    status TEXT NOT NULL,
    profile_json TEXT,
    discovery_json TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lite_secrets (
    secret_ref TEXT PRIMARY KEY,
    nonce TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lite_oauth_providers (
    provider_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    secret_ref TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lite_connections (
    connection_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_system_id TEXT NOT NULL,
    target_system_ids_json TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    composition_json TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    preview_json TEXT NOT NULL,
    daughter_dir TEXT NOT NULL,
    webhook_token TEXT NOT NULL,
    last_run_at TEXT,
    last_error TEXT,
    error_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lite_activities (
    activity_id TEXT PRIMARY KEY,
    connection_id TEXT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lite_events (
    queued_event_id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lite_chat_messages (
    message_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lite_trigger_state (
    connection_id TEXT NOT NULL,
    record_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(connection_id, record_key)
);
CREATE TABLE IF NOT EXISTS lite_poll_status (
    connection_id TEXT PRIMARY KEY,
    initialized INTEGER NOT NULL,
    last_polled_at TEXT
);
"""


class LiteDatabase:
    """Small single-workspace database. One process owns writes; WAL supports UI reads."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = RLock()
        database_path = Path(self.path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.created_new = not database_path.exists() or database_path.stat().st_size == 0
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self.transaction() as db:
            db.executescript(_SCHEMA)
            row = db.execute("SELECT workspace_id FROM lite_workspace LIMIT 1").fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO lite_workspace(workspace_id,name,created_at) VALUES(?,?,?)",
                    ("local", "My Foundry", now_iso()),
                )
            db.execute(
                "INSERT INTO lite_meta(key,value) VALUES('schema_version','1') "
                "ON CONFLICT(key) DO NOTHING"
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            db = self._connect()
            try:
                return [dict(row) for row in db.execute(query, params).fetchall()]
            finally:
                db.close()

    def one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.all(query, params)
        return rows[0] if rows else None


    def integrity_check(self) -> dict[str, Any]:
        with self._lock:
            db = self._connect()
            try:
                rows = [row[0] for row in db.execute("PRAGMA integrity_check").fetchall()]
                return {"valid": rows == ["ok"], "results": rows}
            finally:
                db.close()

    def create_backup(self, backup_dir: str | Path, *, label: str = "startup") -> Path:
        destination_dir = Path(backup_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = destination_dir / f"foundry-lite-{label}-{stamp}.sqlite3"
        with self._lock:
            source = self._connect()
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                target.commit()
            finally:
                target.close()
                source.close()
        return destination

    @staticmethod
    def prune_backups(backup_dir: str | Path, retain: int) -> list[Path]:
        files = sorted(Path(backup_dir).glob("foundry-lite-*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True)
        removed: list[Path] = []
        for item in files[max(1, retain):]:
            item.unlink(missing_ok=True)
            removed.append(item)
        return removed

    def backup_if_due(self, backup_dir: str | Path, *, retain: int = 7, minimum_age_seconds: int = 86400) -> Path | None:
        directory = Path(backup_dir)
        existing = sorted(directory.glob("foundry-lite-*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True) if directory.exists() else []
        if existing:
            age = datetime.now(timezone.utc).timestamp() - existing[0].stat().st_mtime
            if age < minimum_age_seconds:
                return None
        result = self.create_backup(directory)
        self.prune_backups(directory, retain)
        return result

    @staticmethod
    def dumps(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def loads(value: str | None, default: Any = None) -> Any:
        return default if value is None else json.loads(value)

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"
