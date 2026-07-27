from __future__ import annotations

import hashlib
import hmac
import json
import os
try:
    import fcntl
except ImportError:  # pragma: no cover - production images are Linux
    fcntl = None
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import insert, select, update

from .database import PlatformDatabase, audit_events, audit_heads, decode_json, encode_json, now_iso
from .models import AuditEventView, new_id

ZERO_HASH = "0" * 64


class AuditAnchorStore(Protocol):
    def append(self, tenant_id: str, sequence: int, head_hash: str) -> None: ...
    def latest(self, tenant_id: str) -> dict[str, Any] | None: ...


class SignedFileAuditAnchorStore:
    """Append-only signed anchors. Mount the file on WORM storage or forward it to a SIEM."""

    def __init__(self, path: str | Path, key: bytes):
        if len(key) < 32:
            raise ValueError("Audit anchor key must be at least 32 bytes")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.key = key

    def append(self, tenant_id: str, sequence: int, head_hash: str) -> None:
        record = {
            "tenant_id": tenant_id,
            "sequence": sequence,
            "head_hash": head_hash,
            "anchored_at": now_iso(),
        }
        material = encode_json(record)
        record["signature"] = hmac.new(self.key, material.encode(), hashlib.sha256).hexdigest()
        line = encode_json(record) + "\n"
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        fd = os.open(self.path, flags, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def latest(self, tenant_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        latest: dict[str, Any] | None = None
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            record = json.loads(raw)
            signature = record.pop("signature", "")
            expected = hmac.new(self.key, encode_json(record).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("Audit anchor signature verification failed")
            if record.get("tenant_id") == tenant_id:
                candidate = {**record, "signature": signature}
                if latest is None or int(candidate["sequence"]) > int(latest["sequence"]):
                    latest = candidate
        return latest


class SignedDirectoryAuditAnchorStore:
    """One signed immutable record per sequence, suitable for a WORM-mounted directory.

    Atomic O_EXCL creation avoids shared append ordering across API replicas. The
    storage layer must still enforce retention/immutability outside this process.
    """

    def __init__(self, path: str | Path, key: bytes):
        if len(key) < 32:
            raise ValueError("Audit anchor key must be at least 32 bytes")
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.key = key

    @staticmethod
    def _tenant_key(tenant_id: str) -> str:
        return hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()

    def _record_path(self, tenant_id: str, sequence: int) -> Path:
        return self.path / f"{self._tenant_key(tenant_id)}-{sequence:020d}.json"

    def append(self, tenant_id: str, sequence: int, head_hash: str) -> None:
        record = {
            "tenant_id": tenant_id,
            "sequence": sequence,
            "head_hash": head_hash,
            "anchored_at": now_iso(),
        }
        material = encode_json(record)
        record["signature"] = hmac.new(self.key, material.encode(), hashlib.sha256).hexdigest()
        payload = (encode_json(record) + "\n").encode("utf-8")
        destination = self._record_path(tenant_id, sequence)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(destination, flags, 0o400)
        except FileExistsError:
            stored = json.loads(destination.read_text(encoding="utf-8"))
            signature = stored.pop("signature", "")
            expected = hmac.new(self.key, encode_json(stored).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("Existing immutable audit anchor has an invalid signature")
            if (
                stored.get("tenant_id") != tenant_id
                or int(stored.get("sequence", -1)) != sequence
                or stored.get("head_hash") != head_hash
            ):
                raise ValueError("Conflicting immutable audit anchor already exists")
            return
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            directory_fd = os.open(self.path, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass

    def latest(self, tenant_id: str) -> dict[str, Any] | None:
        prefix = f"{self._tenant_key(tenant_id)}-"
        candidates = sorted(self.path.glob(f"{prefix}*.json"), reverse=True)
        if not candidates:
            return None
        for candidate in candidates:
            record = json.loads(candidate.read_text(encoding="utf-8"))
            signature = record.pop("signature", "")
            expected = hmac.new(self.key, encode_json(record).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError(f"Audit anchor signature verification failed: {candidate.name}")
            if record.get("tenant_id") == tenant_id:
                return {**record, "signature": signature}
        return None


class AuditLedger:
    def __init__(self, database: PlatformDatabase, anchor_store: AuditAnchorStore | None = None):
        self.database = database
        self.anchor_store = anchor_store

    def append(
        self,
        tenant_id: str,
        user_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        details: dict[str, Any] | None = None,
    ) -> AuditEventView:
        details = details or {}
        for _ in range(16):
            stamp = now_iso()
            audit_id = new_id("aud")
            with self.database.begin() as connection:
                head = connection.execute(
                    select(audit_heads).where(audit_heads.c.tenant_id == tenant_id)
                ).mappings().first()
                previous = head["head_hash"] if head else ZERO_HASH
                version = int(head["version"]) if head else 0
                sequence = version + 1
                material = encode_json({
                    "audit_id": audit_id,
                    "tenant_id": tenant_id,
                    "sequence": sequence,
                    "user_id": user_id,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "details": details,
                    "previous_hash": previous,
                    "created_at": stamp,
                })
                event_hash = hashlib.sha256(material.encode()).hexdigest()
                if head:
                    result = connection.execute(
                        update(audit_heads)
                        .where(audit_heads.c.tenant_id == tenant_id, audit_heads.c.version == version)
                        .values(head_hash=event_hash, version=sequence)
                    )
                    if result.rowcount != 1:
                        continue
                else:
                    try:
                        connection.execute(
                            insert(audit_heads).values(
                                tenant_id=tenant_id,
                                head_hash=event_hash,
                                version=sequence,
                            )
                        )
                    except Exception:
                        continue
                connection.execute(insert(audit_events).values(
                    audit_id=audit_id,
                    tenant_id=tenant_id,
                    sequence=sequence,
                    user_id=user_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    details_json=encode_json(details),
                    previous_hash=previous,
                    event_hash=event_hash,
                    created_at=stamp,
                ))
            if self.anchor_store:
                self.anchor_store.append(tenant_id, sequence, event_hash)
            return AuditEventView(
                audit_id=audit_id,
                sequence=sequence,
                tenant_id=tenant_id,
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
                previous_hash=previous,
                event_hash=event_hash,
                created_at=stamp,
            )
        raise RuntimeError("Could not append audit event after concurrent retries")

    def list(self, tenant_id: str, limit: int = 100) -> list[AuditEventView]:
        with self.database.connect() as connection:
            rows = connection.execute(
                select(audit_events)
                .where(audit_events.c.tenant_id == tenant_id)
                .order_by(audit_events.c.sequence.desc())
                .limit(limit)
            ).mappings().all()
        return [self._view(row) for row in rows]

    def verify(self, tenant_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute(
                select(audit_events)
                .where(audit_events.c.tenant_id == tenant_id)
                .order_by(audit_events.c.sequence.asc())
            ).mappings().all()
            head = connection.execute(
                select(audit_heads).where(audit_heads.c.tenant_id == tenant_id)
            ).mappings().first()
        previous = ZERO_HASH
        errors: list[str] = []
        expected_sequence = 1
        for row in rows:
            sequence = int(row["sequence"])
            if sequence != expected_sequence:
                errors.append(f"Missing or duplicate audit sequence: expected {expected_sequence}, got {sequence}")
                expected_sequence = sequence
            if row["previous_hash"] != previous:
                errors.append(f"Broken previous hash at sequence {sequence}")
            material = encode_json({
                "audit_id": row["audit_id"],
                "tenant_id": row["tenant_id"],
                "sequence": sequence,
                "user_id": row["user_id"],
                "action": row["action"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "details": decode_json(row["details_json"], {}),
                "previous_hash": row["previous_hash"],
                "created_at": row["created_at"],
            })
            calculated = hashlib.sha256(material.encode()).hexdigest()
            if calculated != row["event_hash"]:
                errors.append(f"Invalid event hash at sequence {sequence}")
            previous = row["event_hash"]
            expected_sequence = sequence + 1

        database_head = head["head_hash"] if head else ZERO_HASH
        database_version = int(head["version"]) if head else 0
        if database_version != len(rows):
            errors.append(
                f"Audit tail truncation or sequence loss: head version {database_version}, event count {len(rows)}"
            )
        if database_head != previous:
            errors.append("Audit head hash does not match computed event-chain head")

        anchor: dict[str, Any] | None = None
        if self.anchor_store:
            try:
                anchor = self.anchor_store.latest(tenant_id)
                if anchor is None:
                    errors.append("External audit anchor is missing")
                else:
                    if int(anchor["sequence"]) != database_version:
                        errors.append("External audit anchor sequence does not match database head")
                    if anchor["head_hash"] != database_head:
                        errors.append("External audit anchor hash does not match database head")
            except Exception as exc:
                errors.append(f"External audit anchor invalid: {exc}")

        return {
            "valid": not errors,
            "events": len(rows),
            "head_hash": previous,
            "head_version": database_version,
            "external_anchor_configured": self.anchor_store is not None,
            "external_anchor": anchor,
            "errors": errors,
        }

    @staticmethod
    def _view(row: Any) -> AuditEventView:
        return AuditEventView(
            audit_id=row["audit_id"],
            sequence=int(row["sequence"]),
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            action=row["action"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            details=decode_json(row["details_json"], {}),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
            created_at=row["created_at"],
        )
