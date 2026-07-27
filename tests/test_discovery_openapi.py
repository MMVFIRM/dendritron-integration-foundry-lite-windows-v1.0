from pathlib import Path

from difoundry.discovery import DiscoveryService
from difoundry.io import load_data
from difoundry.models import DiscoverySource

ROOT = Path(__file__).parents[1]


def test_openapi_discovery_builds_profile_objects_operations_and_authentication():
    result = DiscoveryService().discover(
        DiscoverySource(
            format="auto",
            document=load_data(ROOT / "examples/discovery/crm-openapi.yaml"),
            system_id="atlas_crm",
        )
    )
    assert result.provider == "openapi"
    assert len(result.source_hash) == 64
    assert result.profile.metadata["discovery_source_hash"] == result.source_hash
    assert result.profile.protocol == "rest"
    assert result.profile.authentication.kind == "oauth2"
    assert result.profile.operation("get_customer").required_permissions == ["customers.read"]
    customer = result.profile.object("customer")
    assert customer.field("company_name").required is True
    assert customer.identifiers == ["id"]


def test_swagger_2_body_schema_and_base_url_are_discovered():
    document = {
        "swagger": "2.0",
        "info": {"title": "Legacy API", "version": "1"},
        "host": "legacy.example.invalid",
        "basePath": "/v1",
        "schemes": ["https"],
        "definitions": {
            "Account": {
                "type": "object",
                "required": ["id", "name"],
                "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
            }
        },
        "paths": {
            "/accounts": {
                "post": {
                    "operationId": "createAccount",
                    "tags": ["Accounts"],
                    "parameters": [{"in": "body", "name": "body", "required": True, "schema": {"$ref": "#/definitions/Account"}}],
                    "responses": {"201": {"description": "created", "schema": {"$ref": "#/definitions/Account"}}},
                }
            }
        },
    }
    result = DiscoveryService().discover(DiscoverySource(format="auto", document=document, system_id="legacy_api"))
    assert result.profile.base_url == "https://legacy.example.invalid/v1"
    assert result.profile.operation("create_account").request_schema["required"] == ["id", "name"]
    assert result.profile.object("account").field("name").required is True
