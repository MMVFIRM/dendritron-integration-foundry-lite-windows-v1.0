from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from difoundry.adapters.http import GenericHTTPAdapter
from difoundry.lite.api import create_lite_app
from difoundry.lite.benchmark import _openapi_source, _openapi_target
from difoundry.lite.discovery import AutonomousDiscoveryEngine
from difoundry.lite.service import LiteContext
from difoundry.lite.settings import LiteSettings
from difoundry.artifacts import verify_artifact_manifest

POLL_RECORDS: list[dict[str, Any]] = []


def transport(request: httpx.Request) -> httpx.Response:
    host=request.url.host
    path=request.url.path
    if host == "crm.local" and path == "/deals" and request.method == "GET": return httpx.Response(200,json=list(POLL_RECORDS))
    if path == "/openapi.json":
        if host == "crm.local": return httpx.Response(200,json=_openapi_source())
        if host == "billing.local": return httpx.Response(200,json=_openapi_target())
        if host == "billing-tax.local":
            document=_openapi_target()
            customer=document["components"]["schemas"]["Customer"]
            customer["required"].append("tax_code")
            customer["properties"]["tax_code"]={"type":"string"}
            document["servers"]=[{"url":"https://billing-tax.local"}]
            return httpx.Response(200,json=document)
    if host == "graphql.local" and path == "/graphql" and request.method == "POST":
        return httpx.Response(200,json={"data":{"__schema":{"queryType":{"name":"Query"},"mutationType":{"name":"Mutation"},"subscriptionType":None,"types":[{"kind":"OBJECT","name":"Query","fields":[{"name":"customers","args":[],"type":{"kind":"OBJECT","name":"Customer"}}]},{"kind":"OBJECT","name":"Mutation","fields":[{"name":"createCustomer","args":[{"name":"name","type":{"kind":"NON_NULL","ofType":{"kind":"SCALAR","name":"String"}}}],"type":{"kind":"OBJECT","name":"Customer"}}]},{"kind":"OBJECT","name":"Customer","fields":[{"name":"id","type":{"kind":"SCALAR","name":"ID"}},{"name":"name","type":{"kind":"SCALAR","name":"String"}}]}]}}})
    if host == "odata.local" and path == "/$metadata":
        return httpx.Response(200,text='''<?xml version="1.0"?><edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"><edmx:DataServices><Schema xmlns="http://docs.oasis-open.org/odata/ns/edm"><EntityType Name="Invoice"><Key><PropertyRef Name="Id"/></Key><Property Name="Id" Type="Edm.String" Nullable="false"/><Property Name="Amount" Type="Edm.Decimal" Nullable="false"/></EntityType></Schema></edmx:DataServices></edmx:Edmx>''',headers={"content-type":"application/xml"})
    if host == "cap.local" and path == "/resources": return httpx.Response(200,json={"resources":["orders"]})
    if host == "cap.local" and path == "/orders" and request.method == "GET": return httpx.Response(200,json=[{"id":"o1","customer_name":"Acme","amount":12.5}])
    if host == "cap.local" and path == "/orders" and request.method == "OPTIONS": return httpx.Response(204,headers={"allow":"GET, POST"})
    if host == "behavior.local" and path == "/api" and request.method == "GET": return httpx.Response(200,json=[{"id":"1","title":"One"}])
    if host == "behavior.local" and path == "/api" and request.method == "OPTIONS": return httpx.Response(204,headers={"allow":"GET, POST"})
    if host == "billing.local" and path == "/customers" and request.method == "POST": return httpx.Response(201,json={"id":"c1"})
    return httpx.Response(404)


class MockClient(httpx.Client):
    def __init__(self, **kwargs: Any): super().__init__(transport=httpx.MockTransport(transport),**kwargs)


def adapter_factory(profile, secrets):
    return GenericHTTPAdapter(profile,secrets,client=MockClient(base_url=profile.base_url,timeout=2))


@pytest.fixture
def context(tmp_path: Path) -> LiteContext:
    settings=LiteSettings(tmp_path,tmp_path/"lite.sqlite3",tmp_path/"vault.key",request_timeout_seconds=2)
    return LiteContext.build(settings,AutonomousDiscoveryEngine(client_factory=MockClient,timeout=2),adapter_factory)


def test_no_login_workspace_is_created_automatically(context: LiteContext):
    overview=context.service.overview()
    assert overview["workspace"]["login_required"] is False
    assert context.database.one("SELECT * FROM lite_workspace")["workspace_id"] == "local"


def test_openapi_is_discovered_live_without_user_document(context: LiteContext):
    system=context.service.add_system("CRM","https://crm.local")
    assert system["status"] == "ready"
    assert system["discovery"]["method"] == "openapi"
    assert "Deal" in system["capabilities"]["objects"]


def test_graphql_introspection_is_automatic(context: LiteContext):
    system=context.service.add_system("Graph","https://graphql.local")
    assert system["status"] == "ready"
    assert system["discovery"]["method"] == "graphql-introspection"
    assert "Customer" in system["capabilities"]["objects"]


def test_odata_metadata_is_automatic(context: LiteContext):
    system=context.service.add_system("Ledger","https://odata.local")
    assert system["status"] == "ready"
    assert system["discovery"]["method"] == "odata-metadata"
    assert "Invoice" in system["capabilities"]["objects"]


def test_capability_index_and_options_are_inferred(context: LiteContext):
    system=context.service.add_system("Orders","https://cap.local")
    assert system["discovery"]["method"] == "capability-probe"
    kinds={item["kind"] for item in system["capabilities"]["operations"]}
    assert {"list","create"} <= kinds


def test_behavioral_read_only_fallback(context: LiteContext):
    system=context.service.add_system("Items","https://behavior.local")
    assert system["discovery"]["method"] == "behavioral-probe"
    assert any(item["kind"] == "create" for item in system["capabilities"]["operations"])


def test_credentials_are_encrypted_and_never_returned(context: LiteContext):
    system=context.service.add_system("CRM","https://crm.local","api_key",{"api_key":"super-secret"})
    raw=context.database.one("SELECT * FROM lite_secrets")
    assert "super-secret" not in raw["ciphertext"]
    assert "super-secret" not in json.dumps(system)
    assert context.vault.resolve(context.database.one("SELECT secret_ref FROM lite_systems")["secret_ref"])["api_key"] == "super-secret"


def test_chat_builds_task_specific_daughter_and_preview(context: LiteContext):
    crm=context.service.add_system("CRM","https://crm.local")
    billing=context.service.add_system("Billing","https://billing.local")
    response=context.service.chat("When a deal is approved, create a customer with company name, email, and amount.",crm["system_id"],[billing["system_id"]])
    connection=context.service.list_connections()[0]
    assert response["metadata"]["connection_id"] == connection["connection_id"]
    assert connection["daughter_id"]
    assert connection["preview"]["actions"]
    daughter=Path(context.database.one("SELECT daughter_dir FROM lite_connections")["daughter_dir"])
    assert (daughter/"runtime"/"dendritron-tissue.json").exists()
    assert (daughter/"integration-contract.yaml").exists()
    assert verify_artifact_manifest(daughter)["valid"] is True


def test_enabled_connection_processes_event_without_browser(context: LiteContext):
    crm=context.service.add_system("CRM","https://crm.local")
    billing=context.service.add_system("Billing","https://billing.local")
    connection=context.service.compose(crm["system_id"],[billing["system_id"]],"When a deal is approved, create a customer with company name, email, and amount.")
    context.service.set_enabled(connection["connection_id"],True)
    context.service.enqueue(connection["connection_id"],{"company_name":"Acme","email":"a@a.test","amount":10,"stage":"approved"})
    assert context.service.run_once() is True
    final=context.service.get_connection(connection["connection_id"])
    assert final["last_run_at"]
    assert any(item["kind"] == "run" and item["status"] == "success" for item in context.service.activities())


def session_client(context: LiteContext) -> tuple[TestClient,dict[str,str]]:
    client=TestClient(create_lite_app(context))
    client.get("/console")
    token=client.cookies.get("foundry_lite_session")
    return client,{"X-Foundry-Lite-Session":token}


def test_api_requires_invisible_same_origin_session_not_login(context: LiteContext):
    client=TestClient(create_lite_app(context))
    assert client.get("/lite/overview").status_code == 403
    client,headers=session_client(context)
    assert client.get("/lite/overview",headers=headers).status_code == 200
    assert client.get("/lite/liveness").json()["login_required"] is False


def test_api_end_to_end(context: LiteContext):
    client,headers=session_client(context)
    crm=client.post("/lite/systems",headers=headers,json={"name":"CRM","base_url":"https://crm.local","auth_kind":"none"}).json()
    billing=client.post("/lite/systems",headers=headers,json={"name":"Billing","base_url":"https://billing.local","auth_kind":"none"}).json()
    built=client.post("/lite/chat",headers=headers,json={"message":"When a deal is approved, create a customer with company name, email, and amount.","source_system_id":crm["system_id"],"target_system_ids":[billing["system_id"]]}).json()
    connection_id=built["metadata"]["connection_id"]
    enabled=client.put(f"/lite/connections/{connection_id}/enabled",headers=headers,json={"enabled":True})
    assert enabled.status_code == 200
    connection=enabled.json()
    hook=client.post(connection["webhook_path"],headers=headers,json={"payload":{"company_name":"Acme","email":"a@a.test","amount":10}})
    assert hook.status_code == 200


def test_wrong_webhook_token_is_hidden(context: LiteContext):
    crm=context.service.add_system("CRM","https://crm.local")
    billing=context.service.add_system("Billing","https://billing.local")
    connection=context.service.compose(crm["system_id"],[billing["system_id"]],"create customer company name email amount")
    context.service.set_enabled(connection["connection_id"],True)
    client,headers=session_client(context)
    response=client.post(f"/lite/hooks/{connection['connection_id']}/wrong",headers=headers,json={"payload":{}})
    assert response.status_code == 404


def test_ui_contains_no_login_form_and_has_four_workspaces(context: LiteContext):
    client=TestClient(create_lite_app(context))
    html=client.get("/console").text
    assert "workspace-scoped sign-in" not in html.lower()
    assert "forgot password" not in html.lower()
    for label in ("Create","Systems","Connections","Activity"):
        assert label in html
    assert "Foundry learns each system automatically" in html


def test_business_question_can_be_answered_without_editing_schema(context: LiteContext):
    crm=context.service.add_system("CRM","https://crm.local")
    billing=context.service.add_system("Tax Billing","https://billing-tax.local")
    connection=context.service.compose(crm["system_id"],[billing["system_id"]],"When a deal is approved, create a customer with company name, email, and amount.")
    assert connection["status"] == "questions"
    question=next(item for item in connection["questions"] if item["target_node_id"].endswith(":tax_code"))
    updated=context.service.answer_questions(connection["connection_id"],{question["question_id"]:"STANDARD"})
    assert updated["status"] == "ready"
    contract=context.database.loads(context.database.one("SELECT contract_json FROM lite_connections WHERE connection_id=?",(connection["connection_id"],))["contract_json"])
    mappings=contract["routes"][0]["actions"][0]["mappings"]
    assert any(item["target"]=="tax_code" and item["default"]=="STANDARD" for item in mappings)


def test_polling_baselines_then_detects_changes(context: LiteContext):
    POLL_RECORDS.clear(); POLL_RECORDS.append({"id":"d1","company_name":"Acme","email":"a@a.test","amount":10,"stage":"approved"})
    crm=context.service.add_system("CRM","https://crm.local")
    billing=context.service.add_system("Billing","https://billing.local")
    connection=context.service.compose(crm["system_id"],[billing["system_id"]],"When a deal is approved, create a customer with company name, email, and amount.")
    context.service.set_enabled(connection["connection_id"],True)
    assert context.service.poll_sources_once() == 0
    assert context.database.one("SELECT COUNT(*) AS n FROM lite_events")["n"] == 0
    POLL_RECORDS[0]["amount"] = 11
    assert context.service.poll_sources_once() == 1
    assert context.database.one("SELECT status FROM lite_events")["status"] == "queued"
    context.service.run_once()
    assert context.service.get_connection(connection["connection_id"])["last_run_at"]


def test_non_matching_trigger_condition_abstains(context: LiteContext):
    crm=context.service.add_system("CRM","https://crm.local")
    billing=context.service.add_system("Billing","https://billing.local")
    connection=context.service.compose(crm["system_id"],[billing["system_id"]],"When a deal is approved, create a customer with company name, email, and amount.")
    context.service.set_enabled(connection["connection_id"],True)
    context.service.enqueue(connection["connection_id"],{"company_name":"Acme","email":"a@a.test","amount":10,"stage":"pending"})
    context.service.run_once()
    assert any(item["kind"]=="run" and item["status"]=="abstained" for item in context.service.activities())


def test_exported_daughter_is_a_zip_with_full_artifacts(context: LiteContext):
    import zipfile
    crm=context.service.add_system("CRM","https://crm.local")
    billing=context.service.add_system("Billing","https://billing.local")
    connection=context.service.compose(crm["system_id"],[billing["system_id"]],"create customer company name email amount")
    path=context.service.export_connection(connection["connection_id"])
    assert path.exists()
    with zipfile.ZipFile(path) as archive:
        names=set(archive.namelist())
    assert "integration-contract.yaml" in names
    assert "runtime/dendritron-tissue.json" in names
    assert "TECHNICAL.md" in names


def test_production_style_docs_and_upload_surfaces_are_absent(context: LiteContext):
    client,headers=session_client(context)
    for path in ("/docs","/redoc","/openapi.json"):
        assert client.get(path).status_code == 404
    for path in ("/lite/upload-schema","/lite/import-openapi"):
        assert client.get(path,headers=headers).status_code == 404


def test_package_import_does_not_create_local_database(tmp_path: Path):
    import os, subprocess, sys
    import difoundry
    package_parent=str(Path(difoundry.__file__).resolve().parents[1])
    env={**os.environ,"HOME":str(tmp_path),"PYTHONPATH":package_parent}
    subprocess.run([sys.executable,"-c","import difoundry.lite"],env=env,check=True)
    assert not (tmp_path/".difoundry-lite").exists()


def test_vault_key_permissions_are_private_when_supported(context: LiteContext):
    mode=context.settings.key_path.stat().st_mode & 0o777
    assert mode & 0o077 == 0


def test_live_uvicorn_lite_starts_without_login(tmp_path: Path):
    import os
    import socket
    import subprocess
    import sys
    import time
    import httpx
    import difoundry

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    package_parent = str(Path(difoundry.__file__).resolve().parents[1])
    env = {
        **os.environ,
        "PYTHONPATH": package_parent,
        "DIFOUNDRY_LITE_DATA_DIR": str(tmp_path / "data"),
        "DIFOUNDRY_LITE_HOST": "127.0.0.1",
        "DIFOUNDRY_LITE_PORT": str(port),
        "DIFOUNDRY_LITE_OPEN_BROWSER": "false",
    }
    code = (
        "import uvicorn; "
        "from difoundry.lite.api import create_lite_app; "
        f"uvicorn.run(create_lite_app(), host='127.0.0.1', port={port}, proxy_headers=False, log_level='warning')"
    )
    process = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(80):
            try:
                if httpx.get(base + "/lite/liveness", timeout=0.5).status_code == 200:
                    break
            except Exception:
                pass
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=2)
                raise AssertionError(f"Lite server exited early: {stdout!r} {stderr!r}")
            time.sleep(0.1)
        else:
            raise AssertionError("Lite server did not become live")
        with httpx.Client(base_url=base, timeout=2) as client:
            console = client.get("/console")
            assert console.status_code == 200
            assert "Foundry Lite" in console.text
            token = client.cookies.get("foundry_lite_session")
            overview = client.get("/lite/overview", headers={"X-Foundry-Lite-Session": token})
            assert overview.status_code == 200
            assert overview.json()["workspace"]["login_required"] is False
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
