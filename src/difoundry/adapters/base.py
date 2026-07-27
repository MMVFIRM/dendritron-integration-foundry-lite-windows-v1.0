from __future__ import annotations

from typing import Any, Protocol

from ..models import OperationProfile


class Adapter(Protocol):
    system_id: str

    def execute(
        self,
        operation: OperationProfile,
        payload: dict[str, Any],
        path_parameters: dict[str, Any],
        query_parameters: dict[str, Any],
        idempotency_key: str,
        simulate: bool,
    ) -> dict[str, Any]: ...
