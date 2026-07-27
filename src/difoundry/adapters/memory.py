from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from ..models import OperationProfile


class MemoryAdapter:
    """Generic in-memory adapter used by Phase 0 simulations and tests."""

    def __init__(self, system_id: str) -> None:
        self.system_id = system_id
        self.calls: list[dict[str, Any]] = []
        self.records: dict[str, dict[str, Any]] = {}
        self.idempotent_results: dict[str, dict[str, Any]] = {}

    def execute(
        self,
        operation: OperationProfile,
        payload: dict[str, Any],
        path_parameters: dict[str, Any],
        query_parameters: dict[str, Any],
        idempotency_key: str,
        simulate: bool,
    ) -> dict[str, Any]:
        if idempotency_key in self.idempotent_results:
            return deepcopy(self.idempotent_results[idempotency_key])
        record_id = str(payload.get("id") or payload.get("external_id") or uuid4().hex)
        response = {
            "system_id": self.system_id,
            "operation_id": operation.operation_id,
            "simulated": simulate,
            "record_id": record_id,
            "accepted_payload": deepcopy(payload),
            "path_parameters": deepcopy(path_parameters),
            "query_parameters": deepcopy(query_parameters),
        }
        self.calls.append(response)
        if not simulate:
            self.records[record_id] = deepcopy(payload)
        self.idempotent_results[idempotency_key] = deepcopy(response)
        return response
