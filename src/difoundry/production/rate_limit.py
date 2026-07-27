from __future__ import annotations

import time
import threading
from contextlib import nullcontext

from sqlalchemy import delete, insert, select, update

from .database import PlatformDatabase, rate_buckets


class SqlRateLimiter:
    """Database-backed token bucket with row locking and stale-bucket sweeping."""

    def __init__(self, database: PlatformDatabase, limit_per_minute: int):
        if limit_per_minute < 1:
            raise ValueError("Rate limit must be positive")
        self.database = database
        self.capacity = float(limit_per_minute)
        self.refill_per_second = self.capacity / 60.0
        self._operations = 0
        # SQLite is a single-process evaluation backend. Serialize its deferred
        # transactions so thread races cannot lose bucket updates. PostgreSQL
        # uses row-level locking and remains the multi-replica backend.
        self._sqlite_lock = threading.Lock()

    def consume(self, key: str, cost: float = 1.0) -> tuple[bool, int]:
        self._operations += 1
        if self._operations % 1000 == 0:
            self.sweep()
        now = time.time()
        lock = self._sqlite_lock if self.database.url.startswith("sqlite") else nullcontext()
        with lock:
            return self._consume_transaction(key, cost, now)

    def _consume_transaction(self, key: str, cost: float, now: float) -> tuple[bool, int]:
        with self.database.begin() as connection:
            statement = select(rate_buckets).where(rate_buckets.c.bucket_key == key)
            if not self.database.url.startswith("sqlite"):
                statement = statement.with_for_update()
            row = connection.execute(statement).mappings().first()
            if row is None:
                remaining = self.capacity - cost
                if remaining < 0:
                    return False, 0
                connection.execute(insert(rate_buckets).values(bucket_key=key, tokens=remaining, updated_at=now))
                return True, int(remaining)
            elapsed = max(0.0, now - float(row["updated_at"]))
            available = min(self.capacity, float(row["tokens"]) + elapsed * self.refill_per_second)
            allowed = available >= cost
            remaining = available - cost if allowed else available
            result = connection.execute(
                update(rate_buckets)
                .where(rate_buckets.c.bucket_key == key)
                .values(tokens=remaining, updated_at=now)
            )
            if result.rowcount != 1:
                raise RuntimeError("Rate-limit bucket update failed")
            return allowed, max(0, int(remaining))

    def sweep(self, older_than_seconds: int = 3600) -> int:
        cutoff = time.time() - older_than_seconds
        lock = self._sqlite_lock if self.database.url.startswith("sqlite") else nullcontext()
        with lock:
            with self.database.begin() as connection:
                result = connection.execute(delete(rate_buckets).where(rate_buckets.c.updated_at < cutoff))
                return int(result.rowcount or 0)
