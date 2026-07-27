from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from .models import CanonicalEvent, ExecutionPlan, SimulationResult


class EventLedger:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    behavior_hash TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS results (
                    event_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self.connection.commit()

    def has_idempotency_key(self, key: str) -> bool:
        with self._lock:
            row = self.connection.execute("SELECT 1 FROM events WHERE idempotency_key = ?", (key,)).fetchone()
            return row is not None

    def record_event(self, event: CanonicalEvent) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO events(event_id, idempotency_key, event_json) VALUES (?, ?, ?)",
                (event.event_id, event.idempotency_key, event.model_dump_json()),
            )
            self.connection.commit()

    def record_plan(self, plan: ExecutionPlan) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO plans(plan_id, event_id, plan_hash, behavior_hash, plan_json) VALUES (?, ?, ?, ?, ?)",
                (plan.plan_id, plan.event_id, plan.plan_hash, plan.behavior_hash, plan.model_dump_json()),
            )
            self.connection.commit()

    def record_result(self, result: SimulationResult) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO results(event_id, status, result_json) VALUES (?, ?, ?)",
                (result.event_id, result.status, result.model_dump_json()),
            )
            self.connection.commit()

    def get_event(self, event_id: str) -> CanonicalEvent:
        with self._lock:
            row = self.connection.execute("SELECT event_json FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if row is None:
                raise KeyError(event_id)
            return CanonicalEvent.model_validate_json(row["event_json"])

    def get_latest_result(self, event_id: str) -> SimulationResult:
        with self._lock:
            row = self.connection.execute(
                "SELECT result_json FROM results WHERE event_id = ? ORDER BY rowid DESC LIMIT 1", (event_id,)
            ).fetchone()
            if row is None:
                raise KeyError(event_id)
            return SimulationResult.model_validate_json(row["result_json"])

    def export_event_history(self, event_id: str) -> dict[str, Any]:
        with self._lock:
            event = self.get_event(event_id)
            plans = [
                json.loads(row["plan_json"])
                for row in self.connection.execute("SELECT plan_json FROM plans WHERE event_id = ?", (event_id,))
            ]
            results = [
                json.loads(row["result_json"])
                for row in self.connection.execute("SELECT result_json FROM results WHERE event_id = ?", (event_id,))
            ]
            return {"event": event.model_dump(mode="json"), "plans": plans, "results": results}
