from pathlib import Path

from difoundry.discovery import DiscoveryService
from difoundry.io import load_data
from difoundry.models import DiscoverySource

ROOT = Path(__file__).parents[1]


def test_sql_discovery_builds_crud_operation_catalog():
    result = DiscoveryService().discover(
        DiscoverySource(format="auto", document=load_data(ROOT / "examples/discovery/erp.sql"), system_id="atlas_erp")
    )
    assert result.profile.protocol == "sql"
    assert {obj.object_id for obj in result.profile.objects} == {"account", "account_audit"}
    assert result.profile.operation("insert_account").required_permissions == ["accounts.insert"]
    assert result.profile.object("account").field("external_id").metadata["primary_key"] is True


def test_asyncapi_discovery_builds_publish_operation_and_payload_object():
    result = DiscoveryService().discover(
        DiscoverySource(
            format="auto",
            document=load_data(ROOT / "examples/discovery/analytics-asyncapi.yaml"),
            system_id="analytics_bus",
        )
    )
    assert result.profile.protocol == "queue"
    operation = result.profile.operation("publish_customer_snapshot")
    assert operation.operation_kind == "publish"
    assert operation.request_schema["required"] == ["customer_key", "display_name", "lifecycle"]
    assert result.profile.object("customer_snapshot").field("customer_key").required is True


def test_graphql_discovery_builds_query_mutation_and_objects():
    result = DiscoveryService().discover(
        DiscoverySource(
            format="auto",
            document=load_data(ROOT / "examples/discovery/support-graphql.json"),
            system_id="support_graph",
        )
    )
    assert result.profile.protocol == "graphql"
    assert result.profile.operation("create_ticket").operation_kind == "create"
    assert result.profile.operation("ticket").operation_kind == "read"
    assert result.profile.object("ticket").field("id").required is True
