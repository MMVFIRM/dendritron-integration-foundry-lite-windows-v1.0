from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .models import (
    CompositionResult,
    ObjectFieldProfile,
    ObjectProfile,
    SemanticEdge,
    SemanticGraph,
    SemanticQuestion,
    StrictModel,
    SystemProfile,
)
from .naming import lexical_similarity, slugify, tokens
from .repair import RepairCandidate
from .semantic import SemanticMatcher
from .tissue import DendritronTissueState

PatternKind = Literal["semantic_mapping", "repair_strategy", "routing_topology", "adapter_behavior"]


def _canonical_hash(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _pattern_hash(value: "IntelligencePattern | dict[str, Any]") -> str:
    data = value.model_dump(mode="json") if hasattr(value, "model_dump") else copy.deepcopy(value)
    for key in ("pattern_id", "pattern_hash", "support_count", "origin_hashes", "provenance", "privacy_report", "confidence", "metadata", "created_at", "updated_at"):
        data.pop(key, None)
    return _canonical_hash(data)


def _pack_hash(value: "IntelligencePack | dict[str, Any]") -> str:
    data = value.model_dump(mode="json") if hasattr(value, "model_dump") else copy.deepcopy(value)
    for key in ("pack_hash", "signature", "generated_at"):
        data.pop(key, None)
    return _canonical_hash(data)


class PrivacyPolicy(StrictModel):
    forbidden_key_fragments: list[str] = Field(
        default_factory=lambda: [
            "secret", "password", "token", "credential", "private_key", "access_key",
            "payload", "record", "customer_id", "tenant_id", "user_id", "email", "phone",
        ]
    )
    forbidden_literal_patterns: list[str] = Field(
        default_factory=lambda: [
            r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
            r"https?://[^\s]+",
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
            r"(?:^|[\\/])(?:home|Users|mnt|var|etc)[\\/]",
        ]
    )
    maximum_literal_length: int = Field(default=96, ge=16, le=512)
    minimum_distinct_origins: int = Field(default=2, ge=1, le=100)
    allow_schema_terms: bool = True


class PrivacyFinding(StrictModel):
    path: str
    rule: str
    value_preview: str = ""


class PrivacyReport(StrictModel):
    passed: bool
    findings: list[PrivacyFinding] = Field(default_factory=list)
    inspected_fields: int = 0
    policy_version: str = "1"


class PatternProvenance(StrictModel):
    origin_hash: str
    artifact_hashes: list[str] = Field(default_factory=list)
    consent_scope: Literal["private", "organization", "sanitized_shared"] = "sanitized_shared"
    generator: str = "difoundry-phase4"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IntelligencePattern(StrictModel):
    pattern_id: str = Field(default_factory=lambda: f"pattern_{uuid4().hex}")
    kind: PatternKind
    schema_version: str = "1"
    payload: dict[str, Any]
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    support_count: int = Field(default=1, ge=1)
    origin_hashes: list[str] = Field(default_factory=list)
    provenance: list[PatternProvenance] = Field(default_factory=list)
    privacy_report: PrivacyReport | None = None
    pattern_hash: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bind_pattern(self) -> "IntelligencePattern":
        if not self.origin_hashes:
            self.origin_hashes = sorted({item.origin_hash for item in self.provenance})
        else:
            self.origin_hashes = sorted(set(self.origin_hashes))
        self.support_count = max(self.support_count, len(self.origin_hashes))
        actual = _pattern_hash(self)
        if self.pattern_hash and self.pattern_hash != actual:
            raise ValueError("intelligence pattern hash mismatch")
        self.pattern_hash = actual
        return self


class IntelligencePackSignature(StrictModel):
    algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    key_id: str
    signed_hash: str
    value: str
    signed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IntelligencePack(StrictModel):
    pack_id: str = Field(default_factory=lambda: f"pack_{uuid4().hex}")
    schema_version: str = "1"
    patterns: list[IntelligencePattern]
    pack_hash: str = ""
    signature: IntelligencePackSignature | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bind_pack(self) -> "IntelligencePack":
        actual = _pack_hash(self)
        if self.pack_hash and self.pack_hash != actual:
            raise ValueError("intelligence pack hash mismatch")
        self.pack_hash = actual
        return self


class InheritancePolicy(StrictModel):
    minimum_distinct_origins: int = Field(default=2, ge=1)
    minimum_confidence: float = Field(default=0.72, ge=0.0, le=1.0)
    minimum_pattern_fit: float = Field(default=0.68, ge=0.0, le=1.0)
    automatic_accept_origins: int = Field(default=3, ge=1, le=100)
    allowed_consent_scopes: list[Literal["private", "organization", "sanitized_shared"]] = Field(
        default_factory=lambda: ["sanitized_shared"]
    )
    maximum_mapping_boost: float = Field(default=0.25, ge=0.0, le=0.5)
    require_privacy_pass: bool = True
    allowed_kinds: list[PatternKind] = Field(
        default_factory=lambda: ["semantic_mapping", "repair_strategy", "routing_topology", "adapter_behavior"]
    )


class InheritanceDecision(StrictModel):
    pattern_id: str
    pattern_hash: str
    applied: bool
    reason: str
    score: float = 0.0
    target: str | None = None


class InheritanceReport(StrictModel):
    eligible_pattern_count: int = 0
    applied_pattern_ids: list[str] = Field(default_factory=list)
    decisions: list[InheritanceDecision] = Field(default_factory=list)
    inherited_mapping_count: int = 0
    baseline_review_count: int = 0
    final_review_count: int = 0


class PrivacySanitizer:
    def __init__(self, policy: PrivacyPolicy | None = None) -> None:
        self.policy = policy or PrivacyPolicy()
        self._literal_regexes = [re.compile(item, re.IGNORECASE) for item in self.policy.forbidden_literal_patterns]

    def inspect(self, payload: Any) -> PrivacyReport:
        findings: list[PrivacyFinding] = []
        inspected = 0

        def walk(value: Any, path: str) -> None:
            nonlocal inspected
            inspected += 1
            if isinstance(value, dict):
                for key, item in value.items():
                    lowered = str(key).lower()
                    for fragment in self.policy.forbidden_key_fragments:
                        if fragment in lowered:
                            findings.append(PrivacyFinding(path=f"{path}/{key}", rule=f"forbidden_key:{fragment}"))
                    walk(item, f"{path}/{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}/{index}")
            elif isinstance(value, str):
                if len(value) > self.policy.maximum_literal_length:
                    findings.append(
                        PrivacyFinding(path=path, rule="literal_too_long", value_preview=value[:24])
                    )
                for regex in self._literal_regexes:
                    if regex.search(value):
                        findings.append(
                            PrivacyFinding(path=path, rule=f"forbidden_literal:{regex.pattern}", value_preview=value[:24])
                        )

        walk(payload, "$")
        return PrivacyReport(passed=not findings, findings=findings, inspected_fields=inspected)

    def require_safe(self, payload: Any) -> PrivacyReport:
        report = self.inspect(payload)
        if not report.passed:
            reasons = "; ".join(f"{item.path}:{item.rule}" for item in report.findings[:10])
            raise ValueError(f"pattern failed privacy policy: {reasons}")
        return report


_STOP_TERMS = {
    "id", "field", "value", "object", "record", "source", "target", "primary", "default", "current",
    "create", "update", "request", "response", "input", "output", "data", "item", "entity",
}


def _safe_terms(*values: str) -> list[str]:
    result: set[str] = set()
    for value in values:
        result |= {item for item in tokens(value) if item not in _STOP_TERMS and len(item) > 1}
    return sorted(result)[:16]


def _origin_hash(origin_ref: str) -> str:
    return hashlib.sha256(origin_ref.encode("utf-8")).hexdigest()


def _path_shape(path: str) -> str:
    parts = path.split("/")
    return "/".join("*" if part.isdigit() else part for part in parts)


class IntelligenceExporter:
    def __init__(self, sanitizer: PrivacySanitizer | None = None) -> None:
        self.sanitizer = sanitizer or PrivacySanitizer()

    def from_composition(
        self,
        composition: CompositionResult,
        profiles: dict[str, SystemProfile],
        origin_ref: str,
        consent_scope: Literal["private", "organization", "sanitized_shared"] = "sanitized_shared",
    ) -> list[IntelligencePattern]:
        origin = _origin_hash(origin_ref)
        graph_hashes = {graph.graph_id: _canonical_hash(graph) for graph in composition.semantic_graphs}
        patterns: list[IntelligencePattern] = []
        for graph in composition.semantic_graphs:
            nodes = {node.node_id: node for node in graph.nodes}
            for edge in graph.edges:
                if edge.relation not in {"exact", "likely", "derived"} or edge.needs_review:
                    continue
                source = nodes[edge.source_node_id]
                target = nodes[edge.target_node_id]
                payload = {
                    "source": {
                        "field_terms": _safe_terms(source.label, source.field_path or ""),
                        "object_terms": _safe_terms(graph.source_object_id),
                        "data_type": source.data_type,
                        "required": source.required,
                    },
                    "target": {
                        "field_terms": _safe_terms(target.label, target.field_path or ""),
                        "object_terms": _safe_terms(graph.target_object_id),
                        "data_type": target.data_type,
                        "required": target.required,
                    },
                    "relation": edge.relation,
                    "transforms": [item for item in edge.suggested_transforms if isinstance(item, str)],
                }
                privacy = self.sanitizer.require_safe(payload)
                provenance = PatternProvenance(
                    origin_hash=origin,
                    artifact_hashes=[graph_hashes[graph.graph_id], _canonical_hash(composition.contract)],
                    consent_scope=consent_scope,
                )
                patterns.append(
                    IntelligencePattern(
                        kind="semantic_mapping",
                        payload=payload,
                        confidence=edge.score,
                        provenance=[provenance],
                        privacy_report=privacy,
                        metadata={"source": "verified_semantic_edge"},
                    )
                )
        return patterns

    def from_repair(
        self,
        candidate: RepairCandidate,
        origin_ref: str,
        drift_kind: str,
        failure_signatures: list[str] | None = None,
        consent_scope: Literal["private", "organization", "sanitized_shared"] = "sanitized_shared",
    ) -> IntelligencePattern:
        payload = {
            "drift_kind": drift_kind,
            "risk_level": candidate.risk,
            "patch_shapes": [
                {
                    "artifact": patch.artifact,
                    "operation": patch.operation,
                    "path": _path_shape(patch.path),
                    "value_type": type(patch.value).__name__,
                }
                for patch in candidate.patches
            ],
            "rollback_available": bool(candidate.rollback_patches),
            "failure_signatures": sorted(set(failure_signatures or [])),
        }
        privacy = self.sanitizer.require_safe(payload)
        provenance = PatternProvenance(
            origin_hash=_origin_hash(origin_ref),
            artifact_hashes=[candidate.candidate_hash],
            consent_scope=consent_scope,
        )
        return IntelligencePattern(
            kind="repair_strategy",
            payload=payload,
            confidence=1.0 if candidate.verification and candidate.verification.passed else 0.75,
            provenance=[provenance],
            privacy_report=privacy,
            metadata={"source": "bounded_repair"},
        )

    def from_tissue(
        self,
        tissue: DendritronTissueState,
        origin_ref: str,
        consent_scope: Literal["private", "organization", "sanitized_shared"] = "sanitized_shared",
    ) -> IntelligencePattern:
        payload = {
            "config": tissue.config.model_dump(mode="json"),
            "branch_count": len(tissue.branches),
            "specialists_per_branch": sorted(len(branch.specialists) for branch in tissue.branches),
            "health_buckets": sorted(
                "healthy" if branch.failures <= branch.successes else "degraded" for branch in tissue.branches
            ),
        }
        privacy = self.sanitizer.require_safe(payload)
        provenance = PatternProvenance(
            origin_hash=_origin_hash(origin_ref), artifact_hashes=[_canonical_hash(tissue)], consent_scope=consent_scope
        )
        return IntelligencePattern(
            kind="routing_topology",
            payload=payload,
            confidence=0.8,
            provenance=[provenance],
            privacy_report=privacy,
            metadata={"source": "dendritron_tissue_topology"},
        )


class IntelligenceRegistry:
    def __init__(self, policy: InheritancePolicy | None = None, sanitizer: PrivacySanitizer | None = None) -> None:
        self.policy = policy or InheritancePolicy()
        self.sanitizer = sanitizer or PrivacySanitizer()
        self.patterns: dict[str, IntelligencePattern] = {}
        self._by_hash: dict[str, str] = {}
        self._lock = RLock()

    def add(self, pattern: IntelligencePattern) -> IntelligencePattern:
        if self.policy.require_privacy_pass:
            report = self.sanitizer.require_safe(pattern.payload)
            pattern.privacy_report = report
        with self._lock:
            existing_id = self._by_hash.get(pattern.pattern_hash)
            if existing_id:
                existing = self.patterns[existing_id]
                provenance_by_origin = {item.origin_hash: item for item in existing.provenance}
                for item in pattern.provenance:
                    provenance_by_origin.setdefault(item.origin_hash, item)
                existing.provenance = list(provenance_by_origin.values())
                existing.origin_hashes = sorted(provenance_by_origin)
                existing.support_count = len(existing.origin_hashes)
                existing.confidence = max(existing.confidence, pattern.confidence)
                existing.updated_at = datetime.now(timezone.utc)
                return existing
            self.patterns[pattern.pattern_id] = pattern
            self._by_hash[pattern.pattern_hash] = pattern.pattern_id
            return pattern

    def import_pack(self, pack: IntelligencePack, signing_key: bytes | None = None) -> list[IntelligencePattern]:
        if pack.pack_hash != _pack_hash(pack):
            raise ValueError("intelligence pack hash mismatch")
        if signing_key is not None and not IntelligencePackSigner.verify(pack, signing_key):
            raise ValueError("intelligence pack signature verification failed")
        return [self.add(pattern) for pattern in pack.patterns]

    def eligible(self, kind: PatternKind | None = None) -> list[IntelligencePattern]:
        result = []
        for pattern in self.patterns.values():
            if kind and pattern.kind != kind:
                continue
            if pattern.kind not in self.policy.allowed_kinds:
                continue
            if pattern.confidence < self.policy.minimum_confidence:
                continue
            eligible_origins = {
                item.origin_hash
                for item in pattern.provenance
                if item.consent_scope in self.policy.allowed_consent_scopes
            }
            if len(eligible_origins) < self.policy.minimum_distinct_origins:
                continue
            if self.policy.require_privacy_pass and (not pattern.privacy_report or not pattern.privacy_report.passed):
                continue
            result.append(pattern)
        return sorted(result, key=lambda item: (item.kind, -item.support_count, -item.confidence, item.pattern_hash))

    def quarantined(self) -> list[IntelligencePattern]:
        eligible_ids = {item.pattern_id for item in self.eligible()}
        return [item for item in self.patterns.values() if item.pattern_id not in eligible_ids]

    def pack(self, include_quarantined: bool = False, metadata: dict[str, Any] | None = None) -> IntelligencePack:
        patterns = list(self.patterns.values()) if include_quarantined else self.eligible()
        return IntelligencePack(patterns=patterns, metadata=metadata or {})

    def repair_advice(self, drift_kind: str) -> list[IntelligencePattern]:
        return [
            item for item in self.eligible("repair_strategy") if item.payload.get("drift_kind") == drift_kind
        ]


class IntelligencePackSigner:
    @staticmethod
    def sign(pack: IntelligencePack, key: bytes, key_id: str = "local") -> IntelligencePack:
        pack.signature = None
        pack.pack_hash = _pack_hash(pack)
        value = hmac.new(key, pack.pack_hash.encode("utf-8"), hashlib.sha256).hexdigest()
        pack.signature = IntelligencePackSignature(key_id=key_id, signed_hash=pack.pack_hash, value=value)
        return pack

    @staticmethod
    def verify(pack: IntelligencePack, key: bytes) -> bool:
        if pack.signature is None or pack.signature.signed_hash != pack.pack_hash:
            return False
        expected = hmac.new(key, pack.pack_hash.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, pack.signature.value)


class IntelligenceStore:
    format_name = "difoundry-inherited-intelligence-v1"

    @classmethod
    def save(cls, path: str | Path, pack: IntelligencePack) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"format": cls.format_name, "pack": pack.model_dump(mode="json")}
        payload["state_hash"] = _canonical_hash(payload["pack"])
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> IntelligencePack:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("format") != cls.format_name:
            raise ValueError("unsupported intelligence pack format")
        if payload.get("state_hash") != _canonical_hash(payload.get("pack")):
            raise ValueError("intelligence pack storage hash mismatch")
        return IntelligencePack.model_validate(payload["pack"])


class InheritedSemanticMatcher(SemanticMatcher):
    """Augments deterministic semantic matching with eligible sanitized patterns.

    Inherited evidence may raise a mapping score, but it never bypasses contract,
    schema, permission, or execution certification.
    """

    def __init__(self, registry: IntelligenceRegistry, policy: InheritancePolicy | None = None) -> None:
        super().__init__()
        self.registry = registry
        self.policy = policy or registry.policy
        self.last_report = InheritanceReport()

    def build_graph(
        self,
        source_profile: SystemProfile,
        source_object_id: str,
        target_profile: SystemProfile,
        target_object_id: str,
        minimum_score: float = 0.58,
        review_below: float = 0.78,
    ) -> SemanticGraph:
        graph = super().build_graph(
            source_profile, source_object_id, target_profile, target_object_id, minimum_score, review_below
        )
        baseline_review = len([item for item in graph.questions if item.required])
        patterns = self.registry.eligible("semantic_mapping")
        decisions: list[InheritanceDecision] = []
        if not patterns:
            self.last_report = InheritanceReport(
                eligible_pattern_count=0, baseline_review_count=baseline_review, final_review_count=baseline_review
            )
            return graph

        source_object = source_profile.object(source_object_id)
        target_object = target_profile.object(target_object_id)
        edges_by_target = {edge.target_node_id: edge for edge in graph.edges}
        applied: list[str] = []
        inherited_count = 0

        for target_field in target_object.fields:
            target_node_id = self._field_node_id(target_profile.system_id, target_object.object_id, target_field.path)
            best: tuple[float, IntelligencePattern, ObjectFieldProfile] | None = None
            for pattern in patterns:
                for source_field in source_object.fields:
                    fit = self._pattern_fit(pattern, source_object, source_field, target_object, target_field)
                    if fit < self.policy.minimum_pattern_fit:
                        continue
                    inherited_score = min(
                        0.99,
                        pattern.confidence * 0.72 + fit * 0.28 + min(0.05, 0.01 * (pattern.support_count - 1)),
                    )
                    if best is None or inherited_score > best[0]:
                        best = (inherited_score, pattern, source_field)
            if best is None:
                continue
            score, pattern, source_field = best
            existing = edges_by_target.get(target_node_id)
            baseline_score = existing.score if existing else 0.0
            boosted = min(score, baseline_score + self.policy.maximum_mapping_boost) if existing else score
            if boosted <= baseline_score + 1e-9:
                decisions.append(
                    InheritanceDecision(
                        pattern_id=pattern.pattern_id,
                        pattern_hash=pattern.pattern_hash,
                        applied=False,
                        reason="inherited evidence did not improve baseline",
                        score=boosted,
                        target=target_field.path,
                    )
                )
                continue
            source_node_id = self._field_node_id(source_profile.system_id, source_object.object_id, source_field.path)
            relation = "exact" if boosted >= 0.90 else "likely"
            inherited_edge = SemanticEdge(
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relation=relation,
                score=round(boosted, 6),
                evidence=[
                    f"inherited_pattern={pattern.pattern_hash}",
                    f"independent_origins={len(pattern.origin_hashes)}",
                    f"pattern_fit={self._pattern_fit(pattern, source_object, source_field, target_object, target_field):.3f}",
                    f"baseline_score={baseline_score:.3f}",
                ],
                suggested_transforms=pattern.payload.get("transforms", []),
                needs_review=(boosted < review_below or len(pattern.origin_hashes) < self.policy.automatic_accept_origins),
            )
            if existing:
                graph.edges[graph.edges.index(existing)] = inherited_edge
            else:
                graph.edges.append(inherited_edge)
            edges_by_target[target_node_id] = inherited_edge
            applied.append(pattern.pattern_id)
            inherited_count += 1
            decisions.append(
                InheritanceDecision(
                    pattern_id=pattern.pattern_id,
                    pattern_hash=pattern.pattern_hash,
                    applied=True,
                    reason="eligible multi-origin pattern improved semantic evidence",
                    score=boosted,
                    target=target_field.path,
                )
            )

        accepted_targets = {
            edge.target_node_id for edge in graph.edges if edge.score >= review_below and not edge.needs_review
        }
        graph.questions = [question for question in graph.questions if question.target_node_id not in accepted_targets]
        graph.metadata = {
            **graph.metadata,
            "matcher": "deterministic_plus_inherited_intelligence_v1",
            "eligible_pattern_count": len(patterns),
            "applied_pattern_hashes": sorted(
                {self.registry.patterns[item].pattern_hash for item in applied if item in self.registry.patterns}
            ),
            "inheritance_policy": self.policy.model_dump(mode="json"),
        }
        final_review = len([item for item in graph.questions if item.required])
        self.last_report = InheritanceReport(
            eligible_pattern_count=len(patterns),
            applied_pattern_ids=sorted(set(applied)),
            decisions=decisions,
            inherited_mapping_count=inherited_count,
            baseline_review_count=baseline_review,
            final_review_count=final_review,
        )
        return graph

    def _pattern_fit(
        self,
        pattern: IntelligencePattern,
        source_object: ObjectProfile,
        source_field: ObjectFieldProfile,
        target_object: ObjectProfile,
        target_field: ObjectFieldProfile,
    ) -> float:
        payload = pattern.payload
        source_sig = payload.get("source", {})
        target_sig = payload.get("target", {})
        source_terms = " ".join(source_sig.get("field_terms", []))
        target_terms = " ".join(target_sig.get("field_terms", []))
        source_object_terms = " ".join(source_sig.get("object_terms", []))
        target_object_terms = " ".join(target_sig.get("object_terms", []))
        source_lexical = max(
            lexical_similarity(source_field.name, source_terms),
            lexical_similarity(source_field.path, source_terms),
        )
        target_lexical = max(
            lexical_similarity(target_field.name, target_terms),
            lexical_similarity(target_field.path, target_terms),
        )
        object_fit = (
            lexical_similarity(source_object.name, source_object_terms)
            + lexical_similarity(target_object.name, target_object_terms)
        ) / 2.0
        type_fit = (
            self._type_compatibility(source_field.data_type, source_sig.get("data_type", "any"))
            + self._type_compatibility(target_field.data_type, target_sig.get("data_type", "any"))
        ) / 2.0
        return source_lexical * 0.36 + target_lexical * 0.36 + object_fit * 0.10 + type_fit * 0.18


class InheritedRepairAdvisor:
    def __init__(self, registry: IntelligenceRegistry) -> None:
        self.registry = registry

    def advise(self, drift_kind: str) -> list[dict[str, Any]]:
        return [
            {
                "pattern_id": pattern.pattern_id,
                "pattern_hash": pattern.pattern_hash,
                "support_count": pattern.support_count,
                "confidence": pattern.confidence,
                "patch_shapes": pattern.payload.get("patch_shapes", []),
                "rollback_available": pattern.payload.get("rollback_available", False),
            }
            for pattern in self.registry.repair_advice(drift_kind)
        ]
