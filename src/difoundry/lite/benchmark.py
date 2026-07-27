from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import httpx

from ..adapters.http import GenericHTTPAdapter
from .discovery import AutonomousDiscoveryEngine
from .service import LiteContext
from .settings import LiteSettings

_CRM_RECORDS: list[dict[str, Any]] = []


def _openapi_source() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {"title": "Example CRM", "version": "1"},
        "servers": [{"url": "https://crm.local"}],
        "paths": {
            "/deals": {
                "get": {
                    "operationId": "listDeals",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "array", "items": {"$ref": "#/components/schemas/Deal"}}
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Deal": {
                    "type": "object",
                    "required": ["company_name", "email", "amount"],
                    "properties": {
                        "company_name": {"type": "string"},
                        "email": {"type": "string"},
                        "amount": {"type": "number"},
                        "stage": {"type": "string"},
                    },
                }
            }
        },
    }


def _openapi_target() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {"title": "Example Billing", "version": "1"},
        "servers": [{"url": "https://billing.local"}],
        "paths": {
            "/customers": {
                "post": {
                    "operationId": "createCustomer",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Customer"}}
                        },
                    },
                    "responses": {"201": {"description": "created"}},
                }
            }
        },
        "components": {
            "schemas": {
                "Customer": {
                    "type": "object",
                    "required": ["company_name", "email", "amount"],
                    "properties": {
                        "company_name": {"type": "string"},
                        "email": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                }
            }
        },
    }


def _transport(request: httpx.Request) -> httpx.Response:
    if request.url.host == "crm.local" and request.method == "GET" and request.url.path == "/deals":
        return httpx.Response(200, json=list(_CRM_RECORDS))
    if request.url.path == "/openapi.json":
        if request.url.host == "crm.local":
            return httpx.Response(200, json=_openapi_source())
        if request.url.host == "billing.local":
            return httpx.Response(200, json=_openapi_target())
    if request.method == "POST" and request.url.host == "billing.local" and request.url.path == "/customers":
        return httpx.Response(201, json={"id": "cust_1"})
    return httpx.Response(404)


class _Client(httpx.Client):
    def __init__(self, **kwargs: Any):
        super().__init__(transport=httpx.MockTransport(_transport), **kwargs)


def _adapter(profile, secrets):
    return GenericHTTPAdapter(profile, secrets, client=_Client(base_url=profile.base_url, timeout=2))


def run(output: str | Path | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = LiteSettings(root, root / "lite.sqlite3", root / "key", request_timeout_seconds=2)
        context = LiteContext.build(
            settings,
            AutonomousDiscoveryEngine(client_factory=_Client, timeout=2),
            adapter_factory=_adapter,
        )
        service = context.service
        assert service is not None
        _CRM_RECORDS.clear()
        _CRM_RECORDS.append({"id": "deal_1", "company_name": "Acme", "email": "ops@acme.test", "amount": 1200, "stage": "approved"})
        crm = service.add_system("Example CRM", "https://crm.local")
        billing = service.add_system("Example Billing", "https://billing.local")
        chat = service.chat(
            "When a deal is approved, create the customer in billing with company name, email, and amount.",
            crm["system_id"],
            [billing["system_id"]],
        )
        connection = service.list_connections()[0]
        service.set_enabled(connection["connection_id"], True)
        baseline_changes = service.poll_sources_once()
        _CRM_RECORDS[0]["amount"] = 1250
        detected_changes = service.poll_sources_once()
        service.run_once()
        final = service.get_connection(connection["connection_id"])
        daughter_dir = Path(
            service.db.one(
                "SELECT daughter_dir FROM lite_connections WHERE connection_id=?",
                (connection["connection_id"],),
            )["daughter_dir"]
        )
        gates = {
            "no_login_workspace": service.overview()["workspace"]["login_required"] is False,
            "autonomous_source_discovery": crm["status"] == "ready" and crm["discovery"]["method"] == "openapi",
            "autonomous_target_discovery": billing["status"] == "ready",
            "chat_created_daughter": bool(connection["daughter_id"]),
            "plain_language_preview": connection["preview"]["status"] in {"simulated", "planned"},
            "enabled_without_account": final["enabled"] is True,
            "polling_baseline_is_non_destructive": baseline_changes == 0,
            "polling_detects_live_change": detected_changes == 1,
            "background_event_execution": final["last_run_at"] is not None,
            "encrypted_credentials_boundary": service.overview()["security"]["credentials_returned"] is False,
            "daughter_bundle_written": daughter_dir.exists(),
        }
        report = {
            "evaluation_kind": "deterministic_local_product_fixture",
            "claim_boundary": (
                "Validates the Foundry Lite flow against two local mock systems. It is not a SaaS compatibility, "
                "discovery coverage, usability, throughput, or production reliability measurement."
            ),
            "gates": gates,
            "passed": all(gates.values()),
            "systems": 2,
            "connections": 1,
            "activities": len(service.activities()),
            "chat": chat,
        }
        if output:
            Path(output).write_text(json.dumps(report, indent=2, sort_keys=True))
        return report


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
