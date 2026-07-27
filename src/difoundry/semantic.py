from __future__ import annotations

from dataclasses import dataclass
from math import isclose
from typing import Iterable

from .models import (
    ObjectFieldProfile,
    ObjectProfile,
    SemanticEdge,
    SemanticGraph,
    SemanticNode,
    SemanticQuestion,
    SystemProfile,
)
from .naming import lexical_similarity, slugify, tokens


@dataclass(frozen=True)
class MatchScore:
    total: float
    lexical: float
    type_score: float
    description: float
    identifier: float


class SemanticMatcher:
    """Deterministic baseline for semantic graph construction.

    It produces evidence, uncertainty, and explicit review questions. A learned or
    model-assisted matcher can replace or augment this class behind the same output model.
    """

    def build_graph(
        self,
        source_profile: SystemProfile,
        source_object_id: str,
        target_profile: SystemProfile,
        target_object_id: str,
        minimum_score: float = 0.58,
        review_below: float = 0.78,
    ) -> SemanticGraph:
        source_object = source_profile.object(source_object_id)
        target_object = target_profile.object(target_object_id)
        nodes = [
            self._object_node(source_profile, source_object),
            self._object_node(target_profile, target_object),
            *[self._field_node(source_profile, source_object, field) for field in source_object.fields],
            *[self._field_node(target_profile, target_object, field) for field in target_object.fields],
        ]
        edges: list[SemanticEdge] = []
        questions: list[SemanticQuestion] = []

        source_fields = source_object.fields
        for target_field in target_object.fields:
            ranked = sorted(
                ((source_field, self.score(source_object, source_field, target_object, target_field)) for source_field in source_fields),
                key=lambda item: item[1].total,
                reverse=True,
            )
            target_node_id = self._field_node_id(target_profile.system_id, target_object.object_id, target_field.path)
            if not ranked or ranked[0][1].total < minimum_score:
                if target_field.required:
                    questions.append(
                        SemanticQuestion(
                            question_id=f"map_{slugify(target_profile.system_id)}_{slugify(target_object.object_id)}_{slugify(target_field.path)}",
                            prompt=f"Which source value should populate required target field '{target_field.path}'?",
                            reason="No source field met the minimum semantic mapping score",
                            target_node_id=target_node_id,
                        )
                    )
                continue

            source_field, top = ranked[0]
            second = ranked[1][1] if len(ranked) > 1 else None
            ambiguous = bool(second and abs(top.total - second.total) < 0.08 and second.total >= minimum_score)
            relation = "exact" if top.total >= 0.90 else "likely"
            needs_review = top.total < review_below or ambiguous
            if ambiguous:
                relation = "ambiguous"
            source_node_id = self._field_node_id(source_profile.system_id, source_object.object_id, source_field.path)
            evidence = [
                f"lexical={top.lexical:.3f}",
                f"type={top.type_score:.3f}",
                f"description={top.description:.3f}",
                f"identifier={top.identifier:.3f}",
            ]
            suggested_transforms = self._suggest_transforms(source_field, target_field)
            edges.append(
                SemanticEdge(
                    source_node_id=source_node_id,
                    target_node_id=target_node_id,
                    relation=relation,
                    score=round(top.total, 6),
                    evidence=evidence,
                    suggested_transforms=suggested_transforms,
                    needs_review=needs_review,
                )
            )
            if needs_review:
                choices = [source_field.path]
                if ambiguous and len(ranked) > 1:
                    choices.append(ranked[1][0].path)
                choices.append("Do not map automatically")
                questions.append(
                    SemanticQuestion(
                        question_id=f"review_{slugify(target_profile.system_id)}_{slugify(target_object.object_id)}_{slugify(target_field.path)}",
                        prompt=f"Confirm the mapping for target field '{target_field.path}'.",
                        reason="The mapping is below the automatic acceptance threshold" if not ambiguous else "Multiple source fields have similar semantic scores",
                        source_node_id=source_node_id,
                        target_node_id=target_node_id,
                        choices=choices,
                        required=target_field.required,
                    )
                )

        return SemanticGraph(
            source_system_id=source_profile.system_id,
            source_object_id=source_object.object_id,
            target_system_id=target_profile.system_id,
            target_object_id=target_object.object_id,
            nodes=nodes,
            edges=edges,
            questions=questions,
            metadata={
                "matcher": "deterministic_baseline_v1",
                "minimum_score": minimum_score,
                "review_below": review_below,
            },
        )

    def rank_target_objects(self, source_object: ObjectProfile, targets: Iterable[ObjectProfile]) -> list[tuple[ObjectProfile, float]]:
        ranked: list[tuple[ObjectProfile, float]] = []
        for target in targets:
            object_name = lexical_similarity(source_object.object_id, target.object_id)
            source_names = {field.name for field in source_object.fields}
            target_names = {field.name for field in target.fields}
            field_overlap = 0.0
            if source_names and target_names:
                best_scores = [max(lexical_similarity(target_name, source_name) for source_name in source_names) for target_name in target_names]
                field_overlap = sum(best_scores) / len(best_scores)
            score = min(1.0, object_name * 0.55 + field_overlap * 0.45)
            ranked.append((target, score))
        return sorted(ranked, key=lambda item: item[1], reverse=True)

    def score(
        self,
        source_object: ObjectProfile,
        source: ObjectFieldProfile,
        target_object: ObjectProfile,
        target: ObjectFieldProfile,
    ) -> MatchScore:
        lexical = max(
            lexical_similarity(source.name, target.name),
            lexical_similarity(source.path, target.path),
            lexical_similarity(self._contextual_name(source.name, source_object), self._contextual_name(target.name, target_object)),
        )
        type_score = self._type_compatibility(source.data_type, target.data_type)
        description = self._description_similarity(source.description, target.description)
        source_identifier = source.path in source_object.identifiers or bool(source.metadata.get("primary_key"))
        target_identifier = target.path in target_object.identifiers or bool(target.metadata.get("primary_key"))
        identifier = 1.0 if source_identifier and target_identifier else 0.0
        if source_identifier and target_identifier:
            lexical = max(lexical, 0.86)
        total = lexical * 0.58 + type_score * 0.24 + description * 0.10 + identifier * 0.08
        if source.required == target.required:
            total += 0.02
        return MatchScore(total=min(1.0, total), lexical=lexical, type_score=type_score, description=description, identifier=identifier)

    @staticmethod
    def _contextual_name(value: str, obj: ObjectProfile) -> str:
        neutral = {"primary", "main", "default", "current", "source", "target"}
        object_tokens = tokens(obj.object_id) | tokens(obj.name)
        remaining = [token for token in slugify(value).split("_") if token and token not in object_tokens and token not in neutral]
        return "_".join(remaining) or slugify(value)

    @staticmethod
    def _type_compatibility(source_type: str, target_type: str) -> float:
        source = source_type.lower()
        target = target_type.lower()
        if source == target:
            return 1.0
        numeric = {"integer", "number", "float", "decimal"}
        if source in numeric and target in numeric:
            return 0.85
        if "any" in {source, target}:
            return 0.6
        if source == "enum" and target == "string" or source == "string" and target == "enum":
            return 0.75
        if source == "string" and target in numeric | {"boolean"}:
            return 0.35
        if target == "string" and source in numeric | {"boolean"}:
            return 0.55
        return 0.0

    @staticmethod
    def _description_similarity(left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        left_tokens = tokens(left)
        right_tokens = tokens(right)
        return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))

    @staticmethod
    def _suggest_transforms(source: ObjectFieldProfile, target: ObjectFieldProfile) -> list[str | dict[str, object]]:
        if source.data_type == target.data_type:
            return []
        if source.data_type in {"integer", "number"} and target.data_type == "string":
            return ["to_string"]
        if source.data_type == "string" and target.data_type == "integer":
            return ["to_int"]
        if source.data_type == "string" and target.data_type == "number":
            return ["to_float"]
        return []

    @staticmethod
    def _object_node(profile: SystemProfile, obj: ObjectProfile) -> SemanticNode:
        return SemanticNode(
            node_id=f"{profile.system_id}:{obj.object_id}",
            system_id=profile.system_id,
            object_id=obj.object_id,
            label=obj.name,
            kind="object",
            description=obj.description,
        )

    @staticmethod
    def _field_node(profile: SystemProfile, obj: ObjectProfile, field: ObjectFieldProfile) -> SemanticNode:
        return SemanticNode(
            node_id=SemanticMatcher._field_node_id(profile.system_id, obj.object_id, field.path),
            system_id=profile.system_id,
            object_id=obj.object_id,
            field_path=field.path,
            label=field.name,
            kind="field",
            data_type=field.data_type,
            required=field.required,
            description=field.description,
        )

    @staticmethod
    def _field_node_id(system_id: str, object_id: str, path: str) -> str:
        return f"{system_id}:{object_id}:{path}"
