from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import insert, or_, select, update

from .database import PlatformDatabase, decode_json, encode_json, jobs, now_iso
from .models import JobView, new_id


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class JobLeaseLostError(KeyError):
    """The worker no longer owns a live lease for the requested job."""


class DurableJobQueue:
    def __init__(self, database: PlatformDatabase):
        self.database = database

    def enqueue(
        self,
        tenant_id: str,
        kind: str,
        payload: dict[str, Any],
        max_attempts: int = 5,
        run_after: datetime | None = None,
    ) -> JobView:
        job_id = new_id("job")
        stamp = now_iso()
        run = (run_after or datetime.now(timezone.utc)).isoformat()
        values = dict(
            job_id=job_id,
            tenant_id=tenant_id,
            kind=kind,
            payload_json=encode_json(payload),
            status="queued",
            attempts=0,
            max_attempts=max_attempts,
            run_after=run,
            lease_owner=None,
            lease_token=None,
            leased_until=None,
            last_error=None,
            created_at=stamp,
            updated_at=stamp,
        )
        with self.database.begin() as connection:
            connection.execute(insert(jobs).values(**values))
        return self._view(values)

    def claim(self, worker_id: str, lease_seconds: int = 60) -> tuple[JobView, dict[str, Any], str] | None:
        now = datetime.now(timezone.utc)
        now_value = now.isoformat()
        lease_token = uuid4().hex
        if not self.database.url.startswith("sqlite"):
            with self.database.begin() as connection:
                row = connection.execute(
                    select(jobs)
                    .where(
                        jobs.c.run_after <= now_value,
                        or_(
                            jobs.c.status == "queued",
                            (jobs.c.status == "running") & (jobs.c.leased_until < now_value),
                        ),
                    )
                    .order_by(jobs.c.run_after.asc(), jobs.c.created_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                ).mappings().first()
                if row is None:
                    return None
                connection.execute(
                    update(jobs)
                    .where(jobs.c.job_id == row["job_id"])
                    .values(
                        status="running",
                        lease_owner=worker_id,
                        lease_token=lease_token,
                        leased_until=(now + timedelta(seconds=lease_seconds)).isoformat(),
                        attempts=jobs.c.attempts + 1,
                        updated_at=now_iso(),
                    )
                )
                claimed = connection.execute(select(jobs).where(jobs.c.job_id == row["job_id"])).mappings().one()
                return self._view(claimed), decode_json(claimed["payload_json"], {}), lease_token

        # SQLite reference path: compare-and-swap over candidate rows; writes are serialized.
        with self.database.connect() as connection:
            candidates = connection.execute(
                select(jobs.c.job_id)
                .where(
                    jobs.c.run_after <= now_value,
                    or_(
                        jobs.c.status == "queued",
                        (jobs.c.status == "running") & (jobs.c.leased_until < now_value),
                    ),
                )
                .order_by(jobs.c.run_after.asc(), jobs.c.created_at.asc())
                .limit(20)
            ).scalars().all()
        for job_id in candidates:
            with self.database.begin() as connection:
                result = connection.execute(
                    update(jobs)
                    .where(
                        jobs.c.job_id == job_id,
                        or_(
                            jobs.c.status == "queued",
                            (jobs.c.status == "running") & (jobs.c.leased_until < now_value),
                        ),
                    )
                    .values(
                        status="running",
                        lease_owner=worker_id,
                        lease_token=lease_token,
                        leased_until=(now + timedelta(seconds=lease_seconds)).isoformat(),
                        attempts=jobs.c.attempts + 1,
                        updated_at=now_iso(),
                    )
                )
                if result.rowcount != 1:
                    continue
                row = connection.execute(select(jobs).where(jobs.c.job_id == job_id)).mappings().one()
                return self._view(row), decode_json(row["payload_json"], {}), lease_token
        return None

    def renew(self, job_id: str, worker_id: str, lease_token: str, lease_seconds: int = 60) -> None:
        now = datetime.now(timezone.utc)
        with self.database.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.job_id == job_id,
                    jobs.c.status == "running",
                    jobs.c.lease_owner == worker_id,
                    jobs.c.lease_token == lease_token,
                    jobs.c.leased_until >= now.isoformat(),
                )
                .values(leased_until=(now + timedelta(seconds=lease_seconds)).isoformat(), updated_at=now_iso())
            )
            if result.rowcount != 1:
                raise JobLeaseLostError("Active job lease not found or lease expired")

    def complete(self, job_id: str, worker_id: str, lease_token: str) -> None:
        now_value = datetime.now(timezone.utc).isoformat()
        with self.database.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.job_id == job_id,
                    jobs.c.status == "running",
                    jobs.c.lease_owner == worker_id,
                    jobs.c.lease_token == lease_token,
                    jobs.c.leased_until >= now_value,
                )
                .values(
                    status="succeeded",
                    lease_owner=None,
                    lease_token=None,
                    leased_until=None,
                    updated_at=now_iso(),
                )
            )
            if result.rowcount != 1:
                raise JobLeaseLostError("Active job lease not found or lease expired")

    def fail(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        error: str,
        retry_delay_seconds: int = 10,
    ) -> str:
        now_value = datetime.now(timezone.utc).isoformat()
        with self.database.begin() as connection:
            row = connection.execute(
                select(jobs).where(
                    jobs.c.job_id == job_id,
                    jobs.c.status == "running",
                    jobs.c.lease_owner == worker_id,
                    jobs.c.lease_token == lease_token,
                    jobs.c.leased_until >= now_value,
                )
            ).mappings().first()
            if row is None:
                raise JobLeaseLostError("Active job lease not found or lease expired")
            terminal = int(row["attempts"]) >= int(row["max_attempts"])
            status = "dead" if terminal else "queued"
            run_after = (datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)).isoformat()
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.job_id == job_id,
                    jobs.c.lease_owner == worker_id,
                    jobs.c.lease_token == lease_token,
                )
                .values(
                    status=status,
                    lease_owner=None,
                    lease_token=None,
                    leased_until=None,
                    run_after=run_after,
                    last_error=error[:4000],
                    updated_at=now_iso(),
                )
            )
            if result.rowcount != 1:
                raise JobLeaseLostError("Job lease was stolen during failure handling")
            return status

    def list(self, tenant_id: str, limit: int = 100) -> list[JobView]:
        with self.database.connect() as connection:
            rows = connection.execute(
                select(jobs)
                .where(jobs.c.tenant_id == tenant_id)
                .order_by(jobs.c.created_at.desc())
                .limit(limit)
            ).mappings().all()
        return [self._view(row) for row in rows]

    @staticmethod
    def _view(row: Any) -> JobView:
        return JobView(
            job_id=row["job_id"],
            tenant_id=row["tenant_id"],
            kind=row["kind"],
            status=row["status"],
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            run_after=_dt(row["run_after"]),
            leased_until=_dt(row["leased_until"]),
            last_error=row["last_error"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )
