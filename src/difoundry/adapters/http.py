from __future__ import annotations

from typing import Any

import httpx

from ..models import OperationProfile, SystemProfile


class GenericHTTPAdapter:
    """System-agnostic REST adapter driven entirely by a System Profile."""

    def __init__(self, profile: SystemProfile, secrets: dict[str, str] | None = None, client: httpx.Client | None = None) -> None:
        if profile.protocol != "rest":
            raise ValueError("GenericHTTPAdapter requires a REST system profile")
        if not profile.base_url:
            raise ValueError("REST system profile requires base_url")
        self.profile = profile
        self.system_id = profile.system_id
        self.secrets = secrets or {}
        self.client = client or httpx.Client(base_url=profile.base_url, timeout=30.0)

    def execute(
        self,
        operation: OperationProfile,
        payload: dict[str, Any],
        path_parameters: dict[str, Any],
        query_parameters: dict[str, Any],
        idempotency_key: str,
        simulate: bool,
    ) -> dict[str, Any]:
        path = operation.path.format(**path_parameters)
        headers = self._headers(idempotency_key)
        request_preview = {
            "method": operation.method,
            "url": f"{self.profile.base_url.rstrip('/')}/{path.lstrip('/')}",
            "headers": self._redact(headers),
            "query": query_parameters,
            "payload": payload,
        }
        if simulate:
            return {"simulated": True, "request": request_preview}
        response = self.client.request(
            operation.method,
            path,
            params=query_parameters,
            json=payload if operation.method != "GET" else None,
            headers=headers,
            timeout=operation.timeout_seconds,
        )
        response.raise_for_status()
        try:
            body: Any = response.json()
        except ValueError:
            body = {"text": response.text}
        return {"simulated": False, "status_code": response.status_code, "body": body}

    def _headers(self, idempotency_key: str) -> dict[str, str]:
        headers = {"Accept": "application/json", "Idempotency-Key": idempotency_key}
        auth = self.profile.authentication
        if auth.kind == "api_key":
            header = str(auth.config.get("header", "X-API-Key"))
            secret_name = auth.secret_refs.get("api_key", "api_key")
            headers[header] = self.secrets[secret_name]
        elif auth.kind in {"bearer", "oauth2"}:
            secret_name = auth.secret_refs.get("token", "token")
            headers["Authorization"] = f"Bearer {self.secrets[secret_name]}"
        elif auth.kind == "basic":
            raise NotImplementedError("Use an injected httpx client with basic auth configured")
        return headers

    @staticmethod
    def _redact(headers: dict[str, str]) -> dict[str, str]:
        return {key: ("***" if key.lower() in {"authorization", "x-api-key"} else value) for key, value in headers.items()}
