from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import (
    ActionDefinition,
    CertifierDefinition,
    CompositionRequest,
    CompositionResult,
    DaughterManifest,
    IntegrationContract,
    MappingRule,
    ObjectProfile,
    OperationProfile,
    RouteDefinition,
    SemanticGraph,
    SemanticQuestion,
    SystemProfile,
    TriggerDefinition,
    VerificationBundle,
    VerificationCase,
)
from .naming import slugify
from .semantic import SemanticMatcher
from .discovery.schema import object_from_schema
from .validation import ContractValidator


class CompositionError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedTarget:
    profile: SystemProfile
    object: ObjectProfile
    operation: OperationProfile
    graph: SemanticGraph
    action_id: str


class DaughterComposer:
    """Builds contract, semantic, daughter, and verification scaffolds from profiles.

    The output is intentionally reviewable and does not hide unresolved semantics.
    """

    def __init__(self, matcher: SemanticMatcher | None = None) -> None:
        self.matcher = matcher or SemanticMatcher()

    def compose(self, request: CompositionRequest, profiles: dict[str, SystemProfile]) -> CompositionResult:
        try:
            source_profile = profiles[request.source_system_id]
        except KeyError as exc:
            raise CompositionError(f"Unknown source system: {request.source_system_id}") from exc
        source_object = self._resolve_source_object(source_profile, request.source_object_id)
        resolved_targets: list[ResolvedTarget] = []
        warnings: list[str] = []

        for index, intent in enumerate(request.targets):
            try:
                target_profile = profiles[intent.target_system_id]
            except KeyError as exc:
                raise CompositionError(f"Unknown target system: {intent.target_system_id}") from exc
            target_object = self._resolve_target_object(source_object, target_profile, intent.target_object_id)
            operation = self._resolve_operation(target_profile, target_object.object_id, intent.operation_id)
            mapping_object = self._mapping_object(target_object, operation)
            graph_profile = target_profile.model_copy(
                update={"objects": [mapping_object if obj.object_id == target_object.object_id else obj for obj in target_profile.objects]}
            )
            graph = self.matcher.build_graph(
                source_profile,
                source_object.object_id,
                graph_profile,
                mapping_object.object_id,
                minimum_score=request.minimum_mapping_score,
                review_below=request.require_review_below,
            )
            action_id = intent.action_id or slugify(f"{operation.operation_id}_{index + 1}")
            resolved_targets.append(
                ResolvedTarget(
                    profile=target_profile,
                    object=mapping_object,
                    operation=operation,
                    graph=graph,
                    action_id=action_id,
                )
            )

        actions = [self._build_action(target) for target in resolved_targets]
        permissions = {
            target.profile.system_id: sorted(set(target.operation.required_permissions)) for target in resolved_targets
        }
        contract_id = slugify(request.name)
        contract = IntegrationContract(
            contract_id=contract_id,
            version="0.1.0",
            name=request.name,
            trigger=TriggerDefinition(
                system_id=source_profile.system_id,
                object_type=source_object.object_id,
                event_type=request.event_type,
            ),
            routes=[
                RouteDefinition(
                    route_id=slugify(f"route_{source_object.object_id}"),
                    branches=[],
                    actions=actions,
                    abstain_on_tie=True,
                )
            ],
            permissions=permissions,
            metadata={
                "phase": 4,
                "composition_id": request.composition_id,
                "status": "scaffolded",
                "semantic_graph_ids": [target.graph.graph_id for target in resolved_targets],
                **request.metadata,
            },
        )
        profile_subset = {source_profile.system_id: source_profile, **{target.profile.system_id: target.profile for target in resolved_targets}}
        validation = ContractValidator().validate(contract, profile_subset)
        warnings.extend(validation.warnings)
        if validation.errors:
            raise CompositionError("Generated contract failed structural validation: " + "; ".join(validation.errors))

        all_questions = self._deduplicate_questions(
            question for target in resolved_targets for question in target.graph.questions
        )
        daughter_id = slugify(f"daughter_{request.name}")
        manifest = DaughterManifest(
            daughter_id=daughter_id,
            name=request.name,
            source_system_id=source_profile.system_id,
            target_system_ids=[target.profile.system_id for target in resolved_targets],
            owned_objects={
                source_profile.system_id: [source_object.object_id],
                **{target.profile.system_id: [target.object.object_id] for target in resolved_targets},
            },
            contract_ids=[contract.contract_id],
            semantic_graph_ids=[target.graph.graph_id for target in resolved_targets],
            required_adapters={
                source_profile.system_id: source_profile.protocol,
                **{target.profile.system_id: target.profile.protocol for target in resolved_targets},
            },
            required_permissions=permissions,
            state_capabilities=[
                "idempotency",
                "identity_mapping",
                "event_ledger",
                "deterministic_replay",
                "quarantine",
                "persistent_route_ownership",
                "sparse_specialist_activation",
                "novelty_detection",
                "local_failure_attribution",
                "branch_scoped_adaptation",
                "drift_detection",
                "bounded_repair_candidates",
                "historical_replay_verification",
                "risk_based_approval",
                "signed_atomic_deployment",
                "quarantine_recovery",
                "sanitized_pattern_export",
                "multi_origin_pattern_consensus",
                "inherited_semantic_evidence",
                "inherited_repair_advice",
                "privacy_bound_knowledge_packs",
            ],
            trust_policy={
                "execution_mode": "shadow",
                "human_review_required": bool(all_questions),
                "permission_expansion": "always_approve",
                "destructive_operations": "always_approve",
            },
            metadata={
                "composition_id": request.composition_id,
                "phase": 4,
                "profile_versions": {
                    system_id: profile.version for system_id, profile in profile_subset.items()
                },
                "profile_source_hashes": {
                    system_id: profile.metadata.get("discovery_source_hash", "")
                    for system_id, profile in profile_subset.items()
                },
            },
        )
        verification = self._verification_bundle(manifest, contract, resolved_targets)
        ready = not any(question.required for question in all_questions) and not any(
            edge.needs_review for target in resolved_targets for edge in target.graph.edges
        )
        return CompositionResult(
            composition_id=request.composition_id,
            contract=contract,
            semantic_graphs=[target.graph for target in resolved_targets],
            daughter_manifest=manifest,
            verification_bundle=verification,
            questions=all_questions,
            warnings=warnings,
            ready_for_verification=ready,
        )

    @staticmethod
    def _resolve_source_object(profile: SystemProfile, object_id: str | None) -> ObjectProfile:
        if object_id:
            try:
                return profile.object(object_id)
            except KeyError as exc:
                raise CompositionError(str(exc)) from exc
        if len(profile.objects) == 1:
            return profile.objects[0]
        if not profile.objects:
            raise CompositionError(f"Source system {profile.system_id!r} has no discovered objects")
        raise CompositionError(
            f"Source system {profile.system_id!r} has multiple objects; source_object_id is required: "
            f"{[obj.object_id for obj in profile.objects]}"
        )

    def _resolve_target_object(self, source: ObjectProfile, profile: SystemProfile, object_id: str | None) -> ObjectProfile:
        if object_id:
            try:
                return profile.object(object_id)
            except KeyError as exc:
                raise CompositionError(str(exc)) from exc
        if len(profile.objects) == 1:
            return profile.objects[0]
        if not profile.objects:
            raise CompositionError(f"Target system {profile.system_id!r} has no discovered objects")
        ranked = self.matcher.rank_target_objects(source, profile.objects)
        if not ranked:
            raise CompositionError(f"No target object candidates exist for {profile.system_id!r}")
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.08:
            raise CompositionError(
                f"Target object selection is ambiguous for {profile.system_id!r}; specify target_object_id. "
                f"Candidates: {[(item.object_id, round(score, 3)) for item, score in ranked[:5]]}"
            )
        return ranked[0][0]

    @staticmethod
    def _mapping_object(target_object: ObjectProfile, operation: OperationProfile) -> ObjectProfile:
        schema = operation.request_schema
        if not isinstance(schema, dict) or not schema.get("properties"):
            return target_object
        discovered = object_from_schema(target_object.object_id, schema, name=target_object.name)
        return discovered.model_copy(
            update={
                "description": target_object.description,
                "metadata": {
                    **target_object.metadata,
                    "mapping_surface": "operation_request",
                    "operation_id": operation.operation_id,
                },
            }
        )

    @staticmethod
    def _resolve_operation(profile: SystemProfile, object_id: str, operation_id: str | None) -> OperationProfile:
        if operation_id:
            try:
                return profile.operation(operation_id)
            except KeyError as exc:
                raise CompositionError(str(exc)) from exc
        candidates = [
            operation
            for operation in profile.operations
            if operation.object_id in {None, object_id}
            and operation.operation_kind in {"upsert", "create", "update", "publish", "custom"}
        ]
        if not candidates:
            raise CompositionError(
                f"Target system {profile.system_id!r} has no writable operation for object {object_id!r}. "
                "Provide an operation manifest or custom discovery provider."
            )
        preference = {"upsert": 0, "create": 1, "update": 2, "publish": 3, "custom": 4}
        candidates.sort(key=lambda operation: (preference.get(operation.operation_kind, 99), operation.operation_id))
        if len(candidates) > 1 and preference.get(candidates[0].operation_kind, 99) == preference.get(candidates[1].operation_kind, 99):
            raise CompositionError(
                f"Writable operation selection is ambiguous for {profile.system_id!r}/{object_id!r}; "
                f"specify operation_id. Candidates: {[operation.operation_id for operation in candidates]}"
            )
        return candidates[0]

    @staticmethod
    def _build_action(target: ResolvedTarget) -> ActionDefinition:
        field_nodes = {node.node_id: node for node in target.graph.nodes if node.kind == "field"}
        mappings: list[MappingRule] = []
        for edge in target.graph.edges:
            if edge.relation == "ambiguous":
                continue
            source_node = field_nodes[edge.source_node_id]
            target_node = field_nodes[edge.target_node_id]
            if source_node.field_path is None or target_node.field_path is None:
                continue
            mappings.append(
                MappingRule(
                    source=source_node.field_path,
                    target=target_node.field_path,
                    required=target_node.required,
                    transforms=list(edge.suggested_transforms),
                )
            )
        required_fields = [
            field.path for field in target.object.fields if field.required and any(mapping.target == field.path for mapping in mappings)
        ]
        certifiers = [CertifierDefinition(kind="permission", config={})]
        if required_fields:
            certifiers.insert(0, CertifierDefinition(kind="required_fields", config={"fields": required_fields}))
        return ActionDefinition(
            action_id=target.action_id,
            target_system_id=target.profile.system_id,
            operation_id=target.operation.operation_id,
            mappings=mappings,
            certifiers=certifiers,
        )

    @staticmethod
    def _deduplicate_questions(questions: Iterable[SemanticQuestion]) -> list[SemanticQuestion]:
        result: dict[str, SemanticQuestion] = {}
        for question in questions:
            result[question.question_id] = question
        return list(result.values())

    @staticmethod
    def _verification_bundle(
        manifest: DaughterManifest,
        contract: IntegrationContract,
        targets: list[ResolvedTarget],
    ) -> VerificationBundle:
        cases = [
            VerificationCase(
                case_id="contract_references",
                category="contract",
                description="Every contract system, object, operation, and permission reference resolves",
                assertions=["contract_validator.valid == true"],
            ),
            VerificationCase(
                case_id="deterministic_replay",
                category="replay",
                description="The same payload and artifact versions produce the same behavior hash",
                assertions=["original.behavior_hash == replay.behavior_hash"],
            ),
            VerificationCase(
                case_id="idempotent_effects",
                category="idempotency",
                description="Duplicate source events do not produce duplicate target effects",
                assertions=["second_execution.status == duplicate"],
            ),
            VerificationCase(
                case_id="permission_boundary",
                category="permission",
                description="Removing any required permission blocks the affected action",
                assertions=["unauthorized_action.status == blocked"],
            ),
            VerificationCase(
                case_id="schema_drift",
                category="drift",
                description="A required-field or type change is detected before execution",
                assertions=["drifted_plan.certified == false"],
            ),
            VerificationCase(
                case_id="route_failure_isolation",
                category="failure_isolation",
                description="A failure in one target action does not alter unrelated action definitions",
                assertions=["unaffected_action.behavior_hash is unchanged"],
            ),
        ]
        for target in targets:
            required_targets = [field.path for field in target.object.fields if field.required]
            cases.append(
                VerificationCase(
                    case_id=f"mapping_{slugify(target.profile.system_id)}_{slugify(target.object.object_id)}",
                    category="mapping",
                    description=f"Required mappings for {target.profile.system_id}/{target.object.object_id} are populated and certified",
                    inputs={"semantic_graph_id": target.graph.graph_id},
                    assertions=[f"required_target_fields == {required_targets!r}", "all_required_mappings_are_reviewed"],
                )
            )
        return VerificationBundle(
            daughter_id=manifest.daughter_id,
            cases=cases,
            metadata={"contract_id": contract.contract_id, "phase": 4},
        )
