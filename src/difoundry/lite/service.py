from __future__ import annotations

import hashlib
import json
import platform
import secrets
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable

from ..adapters.http import GenericHTTPAdapter
from ..adapters.memory import MemoryAdapter
from ..artifacts import DaughterBundleWriter
from ..composition import DaughterComposer, ResolvedTarget
from ..models import ActionDefinition, CanonicalEvent, CertifierDefinition, CompositionRequest, IntegrationContract, MappingRule, RouteBranch, RouteCondition, SystemProfile, TargetIntent
from ..simulator import IntegrationSimulator
from .catalog import ConnectorCatalog
from .database import LiteDatabase, now_iso
from .discovery import AutonomousDiscoveryEngine
from .intent import TaskInterpreter
from .settings import LiteSettings
from .vault import LocalVault

AdapterFactory = Callable[[SystemProfile, dict[str, Any]], Any]


class LiteDaughterComposer(DaughterComposer):
    """Create a reviewable scaffold even when business constants are unresolved.

    The enterprise composer fails closed when a required target input has no source
    mapping. Lite preserves that security boundary at execution time, but inserts an
    explicit unresolved constant placeholder so the user can answer the business
    question conversationally instead of editing a schema or contract.
    """

    @staticmethod
    def _build_action(target: ResolvedTarget) -> ActionDefinition:
        action = DaughterComposer._build_action(target)
        mapped = {mapping.target for mapping in action.mappings}
        required_fields = [field.path for field in target.object.fields if field.required]
        for field_path in required_fields:
            if field_path in mapped:
                continue
            action.mappings.append(
                MappingRule(
                    source="__foundry_unresolved_business_value__",
                    target=field_path,
                    required=False,
                    default=None,
                )
            )

        if required_fields:
            certifier = next((item for item in action.certifiers if item.kind == "required_fields"), None)
            if certifier is None:
                action.certifiers.insert(0, CertifierDefinition(kind="required_fields", config={"fields": required_fields}))
            else:
                certifier.config["fields"] = required_fields
        return action


@dataclass(slots=True)
class LiteContext:
    settings: LiteSettings
    database: LiteDatabase
    vault: LocalVault
    discovery: AutonomousDiscoveryEngine
    catalog: ConnectorCatalog
    adapter_factory: AdapterFactory | None = None
    service: "FoundryLiteService | None" = None

    @classmethod
    def build(
        cls,
        settings: LiteSettings | None = None,
        discovery: AutonomousDiscoveryEngine | None = None,
        adapter_factory: AdapterFactory | None = None,
    ) -> "LiteContext":
        settings = settings or LiteSettings.from_env()
        settings.ensure()
        database = LiteDatabase(settings.database_path)
        integrity = database.integrity_check()
        if not integrity["valid"]:
            raise RuntimeError(f"Foundry Lite database integrity check failed: {integrity['results']}")
        if not database.created_new:
            database.backup_if_due(settings.backup_dir or (settings.data_dir / "Backups"), retain=settings.backup_retention)
        context = cls(
            settings,
            database,
            LocalVault(database, settings.key_path),
            discovery
            or AutonomousDiscoveryEngine(
                timeout=settings.request_timeout_seconds,
                max_probes=settings.max_probe_endpoints,
            ),
            ConnectorCatalog(),
            adapter_factory,
        )
        context.service = FoundryLiteService(context)
        return context


class FoundryLiteService:
    def __init__(self, context: LiteContext):
        self.context = context
        self.db = context.database
        self.vault = context.vault
        self.composer = LiteDaughterComposer()
        self.interpreter = TaskInterpreter()
        self._stop = Event()
        self._thread: Thread | None = None

    def overview(self) -> dict[str, Any]:
        connections = self.list_connections()
        return {
            "workspace": {"id": "local", "name": "My Foundry", "login_required": False},
            "systems": len(self.list_systems()),
            "connections": len(connections),
            "enabled_connections": sum(1 for item in connections if item["enabled"]),
            "recent_activity": self.activities(8),
            "first_run": not self.list_systems(),
            "security": {
                "bind_default": "127.0.0.1",
                "local_only": not self.context.settings.allow_lan,
                "vault": "AES-256-GCM",
                "vault_key_protection": "Windows DPAPI" if platform.system() == "Windows" else "private local key file",
                "credentials_returned": False,
                "same_origin_session_required": True,
            },
        }

    def add_system(
        self,
        name: str,
        base_url: str,
        auth_kind: str = "none",
        credentials: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system_id = self.db.new_id("sys")
        stamp = now_iso()
        secret_ref = self.vault.put(credentials) if credentials else None
        with self.db.transaction() as db:
            db.execute(
                "INSERT INTO lite_systems(system_id,name,base_url,auth_kind,secret_ref,status,profile_json,discovery_json,last_error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (system_id, name, base_url, auth_kind, secret_ref, "learning", None, None, None, stamp, stamp),
            )
        self._activity(None, "system", "learning", f"Learning {name}", {"system_id": system_id})
        try:
            live = self.context.discovery.discover(name, base_url, auth_kind, credentials or {}, system_id)
            profile = live.profile.model_copy(
                update={"authentication": live.profile.authentication.model_copy(update={"kind": auth_kind})}
            )
            report = {
                "method": live.method,
                "evidence": [asdict(item) for item in live.evidence],
                "warnings": live.warnings,
            }
            with self.db.transaction() as db:
                db.execute(
                    "UPDATE lite_systems SET status=?,profile_json=?,discovery_json=?,updated_at=? WHERE system_id=?",
                    (
                        "ready",
                        self.db.dumps(profile.model_dump(mode="json")),
                        self.db.dumps(report),
                        now_iso(),
                        system_id,
                    ),
                )
            self._activity(
                None,
                "system",
                "success",
                f"Learned {name}: {len(profile.objects)} objects and {len(profile.operations)} operations",
                {"system_id": system_id, "method": live.method},
            )
        except Exception as exc:
            with self.db.transaction() as db:
                db.execute(
                    "UPDATE lite_systems SET status=?,last_error=?,updated_at=? WHERE system_id=?",
                    ("needs_attention", str(exc), now_iso(), system_id),
                )
            self._activity(
                None,
                "system",
                "failed",
                f"Could not safely learn {name}",
                {"system_id": system_id, "error": str(exc)},
            )
        return self.get_system(system_id)

    def list_systems(self) -> list[dict[str, Any]]:
        return [self._system_public(row) for row in self.db.all("SELECT * FROM lite_systems ORDER BY created_at DESC")]

    def get_system(self, system_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM lite_systems WHERE system_id=?", (system_id,))
        if not row:
            raise KeyError("System not found")
        return self._system_public(row)

    def _profile(self, system_id: str) -> SystemProfile:
        row = self.db.one("SELECT profile_json FROM lite_systems WHERE system_id=?", (system_id,))
        if not row or not row["profile_json"]:
            raise ValueError("System discovery is not complete")
        return SystemProfile.model_validate(self.db.loads(row["profile_json"]))

    def _system_public(self, row: dict[str, Any]) -> dict[str, Any]:
        profile = self.db.loads(row.get("profile_json"), {})
        return {
            "system_id": row["system_id"],
            "name": row["name"],
            "base_url": row["base_url"],
            "auth_kind": row["auth_kind"],
            "status": row["status"],
            "last_error": row["last_error"],
            "discovery": self.db.loads(row.get("discovery_json"), {}),
            "capabilities": {
                "objects": [obj.get("name") for obj in profile.get("objects", [])],
                "operations": [
                    {"name": operation.get("operation_id"), "kind": operation.get("operation_kind")}
                    for operation in profile.get("operations", [])
                ],
            },
            "created_at": row["created_at"],
        }

    def compose(self, source_system_id: str, target_system_ids: list[str], goal: str) -> dict[str, Any]:
        source = self._profile(source_system_id)
        targets = [self._profile(item) for item in target_system_ids]
        intent = self.interpreter.interpret(goal, source, targets)
        request = CompositionRequest(
            name=intent.name,
            source_system_id=source.system_id,
            source_object_id=intent.source_object_id,
            event_type=intent.event_type,
            targets=[
                TargetIntent(target_system_id=system_id, target_object_id=object_id, operation_id=operation_id)
                for system_id, object_id, operation_id in intent.targets
            ],
            minimum_mapping_score=0.52,
            require_review_below=0.68,
            metadata={"edition": "lite", "goal": goal, "intent_explanation": intent.explanation},
        )
        profiles = {profile.system_id: profile for profile in [source, *targets]}
        result = self.composer.compose(request, profiles)
        if intent.condition_path and intent.condition_value:
            contract = result.contract.model_copy(deep=True)
            contract.routes[0].branches = [
                RouteBranch(
                    branch_id="task_condition",
                    description=f"User condition: {intent.condition_path} equals {intent.condition_value}",
                    conditions=[RouteCondition(path=intent.condition_path, value=intent.condition_value)],
                    priority=100,
                )
            ]
            result = result.model_copy(update={"contract": contract})
        preview = self._preview(result, profiles, intent.condition_path, intent.condition_value)
        connection_id = self.db.new_id("conn")
        daughter_dir = self.context.settings.data_dir / "daughters" / connection_id
        writer = DaughterBundleWriter()
        writer.write(daughter_dir, result, profiles)
        self._write_lite_bundle_summary(daughter_dir, result, goal)
        writer._write_artifact_manifest(daughter_dir, result)
        status = "ready" if result.ready_for_verification else "questions"
        stamp = now_iso()
        token = secrets.token_urlsafe(24)
        with self.db.transaction() as db:
            db.execute(
                "INSERT INTO lite_connections(connection_id,name,source_system_id,target_system_ids_json,goal,status,enabled,composition_json,contract_json,preview_json,daughter_dir,webhook_token,last_run_at,last_error,error_count,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    connection_id,
                    intent.name,
                    source_system_id,
                    self.db.dumps(target_system_ids),
                    goal,
                    status,
                    0,
                    self.db.dumps(result.model_dump(mode="json")),
                    self.db.dumps(result.contract.model_dump(mode="json")),
                    self.db.dumps(preview),
                    str(daughter_dir),
                    token,
                    None,
                    None,
                    0,
                    stamp,
                    stamp,
                ),
            )
        self._activity(
            connection_id,
            "connection",
            "success" if status == "ready" else "review",
            f"Built {intent.name}",
            {"questions": [q.model_dump(mode="json") for q in result.questions], "preview": preview},
        )
        return self.get_connection(connection_id)

    def _preview(
        self,
        result: Any,
        profiles: dict[str, SystemProfile],
        condition_path: str | None = None,
        condition_value: Any = None,
    ) -> dict[str, Any]:
        source = profiles[result.contract.trigger.system_id].object(result.contract.trigger.object_type)
        payload = {field.path: self._sample_value(field.data_type, field.name) for field in source.fields}
        if condition_path:
            payload_path = condition_path.removeprefix("payload.")
            payload[payload_path] = condition_value
        event = CanonicalEvent(
            source_system=result.contract.trigger.system_id,
            source_object=result.contract.trigger.object_type,
            event_type=result.contract.trigger.event_type,
            idempotency_key=f"preview:{result.composition_id}",
            payload=payload,
        )
        adapters = {system_id: MemoryAdapter(system_id) for system_id in result.daughter_manifest.target_system_ids}
        simulation = IntegrationSimulator(profiles, adapters).process(result.contract, event, simulate=True)
        return {
            "sample_event": event.model_dump(mode="json"),
            "status": simulation.status,
            "actions": [
                {
                    "action_id": execution.action_id,
                    "status": execution.status,
                    "request": execution.response.get("request") or execution.response,
                }
                for execution in simulation.executions
            ],
            "questions": [q.model_dump(mode="json") for q in result.questions],
            "explanation": f"Foundry generated {len(simulation.executions)} safe preview action(s).",
        }

    @staticmethod
    def _sample_value(data_type: str, name: str) -> Any:
        lower = name.lower()
        if "email" in lower:
            return "sample@example.com"
        if "name" in lower:
            return "Sample Record"
        if data_type in {"integer", "number", "float", "decimal"}:
            return 100
        if data_type == "boolean":
            return True
        return f"sample_{name}"

    @staticmethod
    def _write_lite_bundle_summary(daughter_dir: Path, result: Any, goal: str) -> None:
        technical = daughter_dir / "README.md"
        if technical.exists():
            (daughter_dir / "TECHNICAL.md").write_text(technical.read_text(encoding="utf-8"), encoding="utf-8")
        questions = "\n".join(f"- {question.prompt}" for question in result.questions) or "- None"
        technical.write_text(
            f"# {result.daughter_manifest.name}\n\n"
            f"**Goal:** {goal}\n\n"
            f"**Status:** {'Ready for review' if result.ready_for_verification else 'Business decision required'}\n\n"
            "This is a Foundry Lite daughter. The simple application view is backed by the full versioned contract, semantic map, Dendritron ownership tissue, verification bundle, repair boundary, and artifact manifest.\n\n"
            f"## Open decisions\n\n{questions}\n",
            encoding="utf-8",
        )

    def answer_questions(self, connection_id: str, answers: dict[str, Any]) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM lite_connections WHERE connection_id=?", (connection_id,))
        if not row:
            raise KeyError("Connection not found")
        composition = self.db.loads(row["composition_json"], {})
        questions = composition.get("questions", [])
        contract = IntegrationContract.model_validate(self.db.loads(row["contract_json"]))
        source_profile = self._profile(row["source_system_id"])
        source_object = source_profile.object(contract.trigger.object_type)
        valid_sources = {field.path for field in source_object.fields}
        unresolved = []
        for question in questions:
            question_id = question.get("question_id")
            if question_id not in answers:
                unresolved.append(question)
                continue
            answer = answers[question_id]
            target_node = question.get("target_node_id") or ""
            parts = target_node.split(":", 2)
            if len(parts) != 3:
                raise ValueError(f"Question {question_id} has no target field")
            target_system_id, _object_id, target_path = parts
            source_path = None
            default = None
            if isinstance(answer, dict):
                source_path = answer.get("source")
                default = answer.get("value")
            elif isinstance(answer, str) and answer in valid_sources:
                source_path = answer
            else:
                default = answer
            if source_path and source_path not in valid_sources:
                raise ValueError(f"Unknown source field: {source_path}")
            mapping = MappingRule(
                source=source_path or "__foundry_constant__",
                target=target_path,
                required=bool(question.get("required", True)),
                default=default,
            )
            matched = False
            for route in contract.routes:
                for action in route.actions:
                    if action.target_system_id != target_system_id:
                        continue
                    action.mappings = [item for item in action.mappings if item.target != target_path]
                    action.mappings.append(mapping)
                    matched = True
            if not matched:
                raise ValueError(f"No target action owns {target_path}")
        composition["questions"] = unresolved
        composition["ready_for_verification"] = not any(item.get("required", True) for item in unresolved)
        target_ids = self.db.loads(row["target_system_ids_json"], [])
        profiles = {source_profile.system_id: source_profile, **{target_id: self._profile(target_id) for target_id in target_ids}}
        # Rehydrate the composition model so the existing preview and bundle writer remain authoritative.
        from ..models import CompositionResult
        result = CompositionResult.model_validate({**composition, "contract": contract.model_dump(mode="json")})
        condition = next((branch.conditions[0] for route in contract.routes for branch in route.branches if branch.conditions), None)
        preview = self._preview(result, profiles, condition.path if condition else None, condition.value if condition else None)
        daughter_dir = Path(row["daughter_dir"])
        writer = DaughterBundleWriter()
        writer.write(daughter_dir, result, profiles)
        self._write_lite_bundle_summary(daughter_dir, result, row["goal"])
        writer._write_artifact_manifest(daughter_dir, result)
        status = "ready" if result.ready_for_verification else "questions"
        with self.db.transaction() as db:
            db.execute(
                "UPDATE lite_connections SET status=?,composition_json=?,contract_json=?,preview_json=?,updated_at=? WHERE connection_id=?",
                (status, self.db.dumps(result.model_dump(mode="json")), self.db.dumps(contract.model_dump(mode="json")), self.db.dumps(preview), now_iso(), connection_id),
            )
        self._activity(connection_id, "decision", "success", "Business decisions applied", {"remaining_questions": len(unresolved)})
        return self.get_connection(connection_id)

    def export_connection(self, connection_id: str) -> Path:
        row = self.db.one("SELECT daughter_dir FROM lite_connections WHERE connection_id=?", (connection_id,))
        if not row:
            raise KeyError("Connection not found")
        daughter_dir = Path(row["daughter_dir"])
        if not daughter_dir.exists():
            raise FileNotFoundError("Daughter bundle is missing")
        import shutil
        export_dir = self.context.settings.data_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        destination = export_dir / f"{connection_id}.zip"
        temporary = shutil.make_archive(str(destination.with_suffix("")), "zip", root_dir=daughter_dir)
        return Path(temporary)

    def list_connections(self) -> list[dict[str, Any]]:
        return [
            self._connection_public(row)
            for row in self.db.all("SELECT * FROM lite_connections ORDER BY created_at DESC")
        ]

    def get_connection(self, connection_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM lite_connections WHERE connection_id=?", (connection_id,))
        if not row:
            raise KeyError("Connection not found")
        return self._connection_public(row)

    def _connection_public(self, row: dict[str, Any]) -> dict[str, Any]:
        composition = self.db.loads(row["composition_json"], {})
        return {
            "connection_id": row["connection_id"],
            "name": row["name"],
            "source_system_id": row["source_system_id"],
            "target_system_ids": self.db.loads(row["target_system_ids_json"], []),
            "goal": row["goal"],
            "status": row["status"],
            "enabled": bool(row["enabled"]),
            "preview": self.db.loads(row["preview_json"], {}),
            "questions": composition.get("questions", []),
            "daughter_id": composition.get("daughter_manifest", {}).get("daughter_id"),
            "webhook_path": f"/lite/hooks/{row['connection_id']}/{row['webhook_token']}",
            "trigger_mode": self._trigger_mode(row["source_system_id"], self.db.loads(row["contract_json"])),
            "last_run_at": row["last_run_at"],
            "last_error": row["last_error"],
            "error_count": row["error_count"],
            "created_at": row["created_at"],
        }

    def set_enabled(self, connection_id: str, enabled: bool) -> dict[str, Any]:
        current = self.get_connection(connection_id)
        if enabled and current["status"] != "ready":
            raise ValueError("Resolve required business questions before enabling this connection")
        with self.db.transaction() as db:
            db.execute(
                "UPDATE lite_connections SET enabled=?,status=?,updated_at=? WHERE connection_id=?",
                (1 if enabled else 0, "on" if enabled else "paused", now_iso(), connection_id),
            )
        self._activity(
            connection_id,
            "connection",
            "success",
            f"Connection {'enabled' if enabled else 'paused'}",
            {},
        )
        return self.get_connection(connection_id)

    def delete_connection(self, connection_id: str) -> None:
        row = self.db.one("SELECT daughter_dir FROM lite_connections WHERE connection_id=?", (connection_id,))
        if not row:
            raise KeyError("Connection not found")
        with self.db.transaction() as db:
            db.execute("DELETE FROM lite_events WHERE connection_id=?", (connection_id,))
            db.execute("DELETE FROM lite_trigger_state WHERE connection_id=?", (connection_id,))
            db.execute("DELETE FROM lite_poll_status WHERE connection_id=?", (connection_id,))
            db.execute("DELETE FROM lite_connections WHERE connection_id=?", (connection_id,))
        self._activity(connection_id, "connection", "deleted", "Connection deleted", {})

    def _source_read_operation(self, source_system_id: str, contract_data: dict[str, Any]):
        profile = self._profile(source_system_id)
        object_id = contract_data.get("trigger", {}).get("object_type")
        candidates = [
            operation
            for operation in profile.operations
            if operation.object_id in {None, object_id} and operation.operation_kind in {"list", "search", "read"}
        ]
        order = {"list": 0, "search": 1, "read": 2}
        candidates.sort(key=lambda item: (order.get(item.operation_kind, 9), item.operation_id))
        return candidates[0] if candidates else None

    def _trigger_mode(self, source_system_id: str, contract_data: dict[str, Any]) -> str:
        return "polling + webhook" if self._source_read_operation(source_system_id, contract_data) else "webhook"

    def poll_sources_once(self) -> int:
        changed = 0
        for row in self.db.all("SELECT * FROM lite_connections WHERE enabled=1 ORDER BY created_at"):
            contract_data = self.db.loads(row["contract_json"], {})
            operation = self._source_read_operation(row["source_system_id"], contract_data)
            if not operation:
                continue
            source_row = self.db.one("SELECT * FROM lite_systems WHERE system_id=?", (row["source_system_id"],))
            profile = self._profile(row["source_system_id"])
            secret = self.vault.resolve(source_row["secret_ref"]) if source_row and source_row["secret_ref"] else {}
            adapter = self.context.adapter_factory(profile, secret) if self.context.adapter_factory else GenericHTTPAdapter(profile, secret)
            response = adapter.execute(operation, {}, {}, {}, f"poll:{row['connection_id']}:{now_iso()}", simulate=False)
            body = response.get("body", response)
            if isinstance(body, dict):
                body = body.get("value") or body.get("items") or body.get("data") or body.get("results") or [body]
            records = body if isinstance(body, list) else []
            status = self.db.one("SELECT * FROM lite_poll_status WHERE connection_id=?", (row["connection_id"],))
            initialized = bool(status and status["initialized"])
            source_object = profile.object(contract_data["trigger"]["object_type"])
            identifier = source_object.identifiers[0] if source_object.identifiers else None
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                record_key = str(record.get(identifier) if identifier else record.get("id") or record.get("uuid") or index)
                fingerprint = hashlib.sha256(self.db.dumps(record).encode()).hexdigest()
                existing = self.db.one(
                    "SELECT fingerprint FROM lite_trigger_state WHERE connection_id=? AND record_key=?",
                    (row["connection_id"], record_key),
                )
                with self.db.transaction() as db:
                    db.execute(
                        "INSERT INTO lite_trigger_state(connection_id,record_key,fingerprint,updated_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(connection_id,record_key) DO UPDATE SET fingerprint=excluded.fingerprint,updated_at=excluded.updated_at",
                        (row["connection_id"], record_key, fingerprint, now_iso()),
                    )
                if initialized and (existing is None or existing["fingerprint"] != fingerprint):
                    self.enqueue(row["connection_id"], record)
                    changed += 1
            with self.db.transaction() as db:
                db.execute(
                    "INSERT INTO lite_poll_status(connection_id,initialized,last_polled_at) VALUES(?,?,?) "
                    "ON CONFLICT(connection_id) DO UPDATE SET initialized=1,last_polled_at=excluded.last_polled_at",
                    (row["connection_id"], 1, now_iso()),
                )
        return changed

    def enqueue(self, connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        connection = self.get_connection(connection_id)
        if not connection["enabled"]:
            raise ValueError("Connection is not enabled")
        event_id = self.db.new_id("queue")
        stamp = now_iso()
        with self.db.transaction() as db:
            db.execute(
                "INSERT INTO lite_events(queued_event_id,connection_id,payload_json,status,attempts,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (event_id, connection_id, self.db.dumps(payload), "queued", 0, None, stamp, stamp),
            )
        self._activity(connection_id, "event", "queued", "Event queued", {"queued_event_id": event_id})
        return {"queued_event_id": event_id, "status": "queued"}

    def run_once(self) -> bool:
        with self.db.transaction() as db:
            row = db.execute("SELECT * FROM lite_events WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return False
            db.execute(
                "UPDATE lite_events SET status='running',attempts=attempts+1,updated_at=? WHERE queued_event_id=?",
                (now_iso(), row["queued_event_id"]),
            )
            item = dict(row)
        try:
            self._execute_connection(item["connection_id"], self.db.loads(item["payload_json"], {}))
            with self.db.transaction() as db:
                db.execute(
                    "UPDATE lite_events SET status='succeeded',updated_at=? WHERE queued_event_id=?",
                    (now_iso(), item["queued_event_id"]),
                )
            return True
        except Exception as exc:
            with self.db.transaction() as db:
                db.execute(
                    "UPDATE lite_events SET status='failed',last_error=?,updated_at=? WHERE queued_event_id=?",
                    (str(exc), now_iso(), item["queued_event_id"]),
                )
            self._activity(item["connection_id"], "event", "failed", "Connection event failed", {"error": str(exc)})
            return True

    def _execute_connection(self, connection_id: str, payload: dict[str, Any]) -> None:
        row = self.db.one("SELECT * FROM lite_connections WHERE connection_id=?", (connection_id,))
        if not row or not row["enabled"]:
            raise ValueError("Connection is not enabled")
        contract = IntegrationContract.model_validate(self.db.loads(row["contract_json"]))
        profiles = {row["source_system_id"]: self._profile(row["source_system_id"])}
        target_ids = self.db.loads(row["target_system_ids_json"])
        for target_id in target_ids:
            profiles[target_id] = self._profile(target_id)
        adapters = {}
        for target_id in target_ids:
            system_row = self.db.one("SELECT secret_ref FROM lite_systems WHERE system_id=?", (target_id,))
            secrets_value = self.vault.resolve(system_row["secret_ref"]) if system_row and system_row["secret_ref"] else {}
            profile = profiles[target_id]
            if self.context.adapter_factory:
                adapters[target_id] = self.context.adapter_factory(profile, secrets_value)
            else:
                adapters[target_id] = GenericHTTPAdapter(profile, secrets_value) if profile.protocol == "rest" else MemoryAdapter(target_id)
        event = CanonicalEvent(
            source_system=contract.trigger.system_id,
            source_object=contract.trigger.object_type,
            event_type=contract.trigger.event_type,
            idempotency_key=f"lite:{connection_id}:{hashlib.sha256(self.db.dumps(payload).encode()).hexdigest()}",
            payload=payload,
        )
        result = IntegrationSimulator(profiles, adapters).process(contract, event, simulate=False)
        stamp = now_iso()
        error = result.message or next((execution.error for execution in result.executions if execution.error), None)
        with self.db.transaction() as db:
            db.execute(
                "UPDATE lite_connections SET last_run_at=?,last_error=?,error_count=error_count+?,updated_at=? WHERE connection_id=?",
                (stamp, error, 1 if result.status == "failed" else 0, stamp, connection_id),
            )
        self._activity(
            connection_id,
            "run",
            "success" if result.status == "succeeded" else result.status,
            f"Connection run {result.status}",
            result.model_dump(mode="json"),
        )

    def create_backup(self, label: str = "manual") -> Path:
        backup_dir = self.context.settings.backup_dir or (self.context.settings.data_dir / "Backups")
        path = self.db.create_backup(backup_dir, label=label)
        self.db.prune_backups(backup_dir, self.context.settings.backup_retention)
        self._activity(None, "backup", "success", "Created encrypted local database backup", {"filename": path.name})
        return path

    def desktop_status(self) -> dict[str, Any]:
        from difoundry import __version__
        from .desktop_state import installed_mode, startup_enabled

        backup_dir = self.context.settings.backup_dir or (self.context.settings.data_dir / "Backups")
        backups = sorted(backup_dir.glob("foundry-lite-*.sqlite3"), reverse=True) if backup_dir.exists() else []
        return {
            "version": __version__,
            "installed_desktop": installed_mode(),
            "platform": platform.platform(),
            "data_dir": str(self.context.settings.data_dir),
            "database": str(self.context.settings.database_path),
            "backup_count": len(backups),
            "latest_backup": backups[0].name if backups else None,
            "database_integrity": self.db.integrity_check(),
            "start_at_sign_in": startup_enabled(),
            "local_url": f"http://{self.context.settings.host}:{self.context.settings.port}/console",
        }

    def create_support_bundle(self) -> Path:
        """Create a redacted support ZIP. Secret rows and event payloads are excluded."""
        destination = self.context.settings.data_dir / "Exports" / f"foundry-lite-support-{now_iso().replace(':', '').replace('+', '_')}.zip"
        destination.parent.mkdir(parents=True, exist_ok=True)
        systems = self.list_systems()
        for item in systems:
            item.pop("base_url", None)
        payload = {
            "desktop": self.desktop_status(),
            "overview": self.overview(),
            "systems": systems,
            "connections": self.list_connections(),
            "activity": self.activities(200),
        }

        blocked_fragments = ("password", "secret", "token", "authorization", "api_key", "webhook", "payload", "request", "response", "sample_event")
        def redact(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: ("[REDACTED]" if any(fragment in key.lower() for fragment in blocked_fragments) else redact(item))
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value
        payload = redact(payload)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("support.json", json.dumps(payload, indent=2, sort_keys=True, default=str))
            log_dir = self.context.settings.log_dir or (self.context.settings.data_dir / "Logs")
            log_path = log_dir / "foundry-lite.log"
            if log_path.exists():
                text = log_path.read_text(encoding="utf-8", errors="replace")[-250_000:]
                archive.writestr("foundry-lite.log", text)
        self._activity(None, "support", "success", "Created redacted support bundle", {"filename": destination.name})
        return destination

    def activities(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT * FROM lite_activities ORDER BY created_at DESC LIMIT ?", (limit,))
        for row in rows:
            row["details"] = self.db.loads(row.pop("details_json"), {})
        return rows

    def chat(
        self,
        message: str,
        source_system_id: str | None = None,
        target_system_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self._chat("user", message, {})
        if source_system_id and target_system_ids:
            try:
                connection = self.compose(source_system_id, target_system_ids, message)
                answer = (
                    f"I learned the connected systems and built ‘{connection['name']}’. "
                    f"The preview produced {len(connection['preview'].get('actions', []))} action(s)."
                )
                if connection["questions"]:
                    answer += f" I still need {len(connection['questions'])} business decision(s) before it can be enabled."
                else:
                    answer += " It is ready for you to review and turn on."
                metadata = {
                    "connection_id": connection["connection_id"],
                    "preview": connection["preview"],
                    "questions": connection["questions"],
                }
            except Exception as exc:
                answer = f"I could not build that connection safely yet: {exc}"
                metadata = {"error": str(exc)}
        else:
            answer = (
                "Choose the connected source and target systems, then describe the outcome you want. "
                "I will use their live discovered schemas to build the daughter."
            )
            metadata = {"needs_system_selection": True}
        self._chat("assistant", answer, metadata)
        return {"role": "assistant", "content": answer, "metadata": metadata}

    def chat_history(self) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT * FROM lite_chat_messages ORDER BY created_at")
        for row in rows:
            row["metadata"] = self.db.loads(row.pop("metadata_json"), {})
        return rows

    def _chat(self, role: str, content: str, metadata: dict[str, Any]) -> None:
        with self.db.transaction() as db:
            db.execute(
                "INSERT INTO lite_chat_messages(message_id,role,content,metadata_json,created_at) VALUES(?,?,?,?,?)",
                (self.db.new_id("msg"), role, content, self.db.dumps(metadata), now_iso()),
            )

    def _activity(
        self,
        connection_id: str | None,
        kind: str,
        status: str,
        summary: str,
        details: dict[str, Any],
    ) -> None:
        with self.db.transaction() as db:
            db.execute(
                "INSERT INTO lite_activities(activity_id,connection_id,kind,status,summary,details_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (self.db.new_id("act"), connection_id, kind, status, summary, self.db.dumps(details), now_iso()),
            )

    def start_runner(self, poll_seconds: float = 0.5) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            import time
            next_poll = 0.0
            while not self._stop.wait(poll_seconds):
                if time.monotonic() >= next_poll:
                    try:
                        self.poll_sources_once()
                    except Exception as exc:
                        self._activity(None, "poll", "failed", "Source polling encountered an error", {"error": str(exc)})
                    next_poll = time.monotonic() + 30.0
                while self.run_once():
                    pass

        self._thread = Thread(target=loop, name="foundry-lite-runner", daemon=True)
        self._thread.start()

    def stop_runner(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
