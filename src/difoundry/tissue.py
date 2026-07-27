from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import Field, model_validator

from .features import SparseFeatureEncoder, weighted_jaccard
from .models import (
    CanonicalEvent,
    IntegrationContract,
    PlannedAction,
    RouteBranch,
    RouteDefinition,
    RouteTrace,
    StrictModel,
)
from .routing import DendriticOwnedRouter, RouteSelection


class TissueIntegrityError(ValueError):
    pass


class DendritronTissueConfig(StrictModel):
    top_k_specialists: int = Field(default=2, ge=1, le=32)
    max_specialists_per_branch: int = Field(default=8, ge=1, le=128)
    learning_rate: float = Field(default=0.25, gt=0.0, le=1.0)
    spawn_below_similarity: float = Field(default=0.72, ge=0.0, le=1.0)
    novelty_threshold: float = Field(default=0.43, ge=0.0, le=1.0)
    ownership_margin: float = Field(default=0.04, ge=0.0, le=1.0)
    abstain_on_novelty: bool = True
    hard_contract_gate: bool = True
    ignored_feature_paths: list[str] = Field(
        default_factory=lambda: [
            "event.event_id",
            "event.correlation_id",
            "event.idempotency_key",
            "event.observed_at",
            "event.source_record_id",
            "metadata.replay_of",
        ]
    )


class DendritronSpecialistState(StrictModel):
    specialist_id: str = Field(default_factory=lambda: f"specialist_{uuid4().hex[:12]}")
    prototype: dict[str, float] = Field(default_factory=dict)
    observations: int = 0
    successes: int = 0
    failures: int = 0
    disabled: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def health(self) -> float:
        return (self.successes + 1.0) / (self.successes + self.failures + 2.0)


class DendritronBranchState(StrictModel):
    route_id: str
    branch_id: str
    ownership_key: str
    specialists: list[DendritronSpecialistState] = Field(default_factory=list)
    observations: int = 0
    successes: int = 0
    failures: int = 0
    local_version: int = 0
    disabled: bool = False
    last_failure_signature: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def unique_specialists(self) -> "DendritronBranchState":
        ids = [item.specialist_id for item in self.specialists]
        if len(ids) != len(set(ids)):
            raise ValueError("specialist identifiers must be unique within a branch")
        return self


class FailureAttribution(StrictModel):
    attribution_id: str = Field(default_factory=lambda: f"failure_{uuid4().hex}")
    action_id: str
    route_id: str
    branch_id: str
    ownership_key: str
    specialist_ids: list[str] = Field(default_factory=list)
    failure_signature: str
    tissue_version: int
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DendritronTissueState(StrictModel):
    tissue_id: str = Field(default_factory=lambda: f"tissue_{uuid4().hex}")
    contract_id: str
    contract_version: str
    version: int = 0
    config: DendritronTissueConfig = Field(default_factory=DendritronTissueConfig)
    branches: list[DendritronBranchState] = Field(default_factory=list)
    failure_attributions: list[FailureAttribution] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_owners(self) -> "DendritronTissueState":
        keys = [branch.ownership_key for branch in self.branches]
        if len(keys) != len(set(keys)):
            raise ValueError("ownership keys must be unique")
        return self

    def branch(self, route_id: str, branch_id: str) -> DendritronBranchState:
        for branch in self.branches:
            if branch.route_id == route_id and branch.branch_id == branch_id:
                return branch
        raise KeyError(f"No tissue owner for {route_id}/{branch_id}")


class RouterTrainingExample(StrictModel):
    event: CanonicalEvent
    route_id: str
    branch_id: str
    reward: float = Field(default=1.0, ge=-1.0, le=1.0)


class RouterTrainingSet(StrictModel):
    examples: list[RouterTrainingExample]


class TissueStore:
    format_name = "difoundry-dendritron-tissue-v1"
    _lock = RLock()

    @classmethod
    def save(cls, path: str | Path, state: DendritronTissueState) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with cls._lock:
            state.updated_at = datetime.now(timezone.utc)
            payload = state.model_dump(mode="json")
            state_hash = cls._hash(payload)
            envelope = {"format": cls.format_name, "state_hash": state_hash, "state": payload}
            temporary = target.with_name(target.name + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(envelope, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        return target

    @classmethod
    def load(cls, path: str | Path) -> DendritronTissueState:
        source = Path(path)
        with cls._lock:
            envelope = json.loads(source.read_text(encoding="utf-8"))
        if envelope.get("format") != cls.format_name:
            raise TissueIntegrityError("Unsupported tissue format")
        expected = envelope.get("state_hash")
        actual = cls._hash(envelope.get("state"))
        if expected != actual:
            raise TissueIntegrityError("Tissue state hash mismatch")
        return DendritronTissueState.model_validate(envelope["state"])

    @staticmethod
    def _hash(payload: Any) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class DendritronRoutingTissue:
    """Persistent, locally adaptive routing tissue for generated daughters.

    Hard contract conditions remain fail-closed gates. Within eligible branches,
    sparse specialists own recurring event patterns. Training and outcome updates
    modify only the addressed branch and its active specialists; there is no
    shared global gradient or cross-branch parameter update.
    """

    router_kind = "dendritron_tissue"

    def __init__(
        self,
        state: DendritronTissueState,
        *,
        encoder: SparseFeatureEncoder | None = None,
        store_path: str | Path | None = None,
    ) -> None:
        self.state = state
        self.encoder = encoder or SparseFeatureEncoder()
        self.store_path = Path(store_path) if store_path else None
        self._declarative = DendriticOwnedRouter()
        self._lock = RLock()

    @classmethod
    def from_contract(
        cls,
        contract: IntegrationContract,
        config: DendritronTissueConfig | None = None,
        *,
        store_path: str | Path | None = None,
    ) -> "DendritronRoutingTissue":
        branches: list[DendritronBranchState] = []
        for route in contract.routes:
            route_branches = route.branches or [RouteBranch(branch_id="default")]
            for branch in route_branches:
                branches.append(
                    DendritronBranchState(
                        route_id=route.route_id,
                        branch_id=branch.branch_id,
                        ownership_key=f"{contract.contract_id}:{route.route_id}:{branch.branch_id}",
                    )
                )
        state = DendritronTissueState(
            contract_id=contract.contract_id,
            contract_version=contract.version,
            config=config or DendritronTissueConfig(),
            branches=branches,
        )
        tissue = cls(state, store_path=store_path)
        tissue._autosave()
        return tissue

    @classmethod
    def load(cls, path: str | Path) -> "DendritronRoutingTissue":
        return cls(TissueStore.load(path), store_path=path)

    def save(self, path: str | Path | None = None) -> Path:
        with self._lock:
            target = Path(path) if path else self.store_path
            if target is None:
                raise ValueError("No tissue store path was provided")
            self.store_path = target
            return TissueStore.save(target, self.state)

    def validate_contract(self, contract: IntegrationContract) -> None:
        if contract.contract_id != self.state.contract_id:
            raise ValueError("Tissue belongs to a different integration contract")
        if contract.version != self.state.contract_version:
            raise ValueError("Tissue contract version does not match")
        expected = {
            (route.route_id, branch.branch_id)
            for route in contract.routes
            for branch in (route.branches or [RouteBranch(branch_id="default")])
        }
        actual = {(branch.route_id, branch.branch_id) for branch in self.state.branches}
        if expected != actual:
            raise ValueError("Tissue ownership graph does not match the contract")

    def select(self, contract: IntegrationContract, event_context: dict[str, Any]) -> list[RouteSelection]:
        with self._lock:
            self.validate_contract(contract)
            features = self._stable_features(event_context)
            selections: list[RouteSelection] = []
            for route in contract.routes:
                selections.append(self._select_route(route, features, event_context))
            return selections

    def learn(self, contract: IntegrationContract, example: RouterTrainingExample) -> str:
        with self._lock:
            return self._learn_unlocked(contract, example)

    def _learn_unlocked(self, contract: IntegrationContract, example: RouterTrainingExample) -> str:
        self.validate_contract(contract)
        context = event_context(example.event)
        features = self._stable_features(context)
        owner = self.state.branch(example.route_id, example.branch_id)
        if owner.disabled:
            raise ValueError("Cannot train a disabled branch")
        specialist = self._nearest(owner, features)[0]
        similarity = 0.0 if specialist is None else weighted_jaccard(features, specialist.prototype)
        if example.reward > 0 and (
            specialist is None
            or similarity < self.state.config.spawn_below_similarity
            and len(owner.specialists) < self.state.config.max_specialists_per_branch
        ):
            specialist = DendritronSpecialistState(prototype=dict(features))
            owner.specialists.append(specialist)
        elif specialist is None:
            raise ValueError("Negative feedback requires an existing specialist")

        assert specialist is not None
        rate = self.state.config.learning_rate * abs(example.reward)
        if example.reward >= 0:
            self._move_toward(specialist, features, rate)
        else:
            self._move_away(specialist, features, rate)
        specialist.observations += 1
        specialist.updated_at = datetime.now(timezone.utc)
        owner.observations += 1
        owner.local_version += 1
        owner.updated_at = datetime.now(timezone.utc)
        self._advance_version()
        return specialist.specialist_id

    def train(self, contract: IntegrationContract, training_set: RouterTrainingSet) -> dict[str, Any]:
        touched: dict[str, int] = {}
        with self._lock:
            self.validate_contract(contract)
            for example in training_set.examples:
                self._learn_unlocked(contract, example)
                key = f"{example.route_id}/{example.branch_id}"
                touched[key] = touched.get(key, 0) + 1
            return {
                "examples": len(training_set.examples),
                "touched_branches": touched,
                "tissue_version": self.state.version,
                "specialists": sum(len(branch.specialists) for branch in self.state.branches),
            }

    def record_outcome(self, action: PlannedAction, success: bool, error: str | None = None) -> FailureAttribution | None:
        with self._lock:
            if not action.route_id or not action.branch_id:
                return None
            owner = self.state.branch(action.route_id, action.branch_id)
            owner.observations += 1
            if success:
                owner.successes += 1
            else:
                owner.failures += 1
                owner.last_failure_signature = failure_signature(error or "unknown failure")
            active = {item.specialist_id: item for item in owner.specialists}
            for specialist_id in action.specialist_ids:
                specialist = active.get(specialist_id)
                if specialist is None:
                    continue
                specialist.observations += 1
                if success:
                    specialist.successes += 1
                else:
                    specialist.failures += 1
                specialist.updated_at = datetime.now(timezone.utc)
            owner.local_version += 1
            owner.updated_at = datetime.now(timezone.utc)
            self._advance_version()
            if success:
                return None
            attribution = FailureAttribution(
                action_id=action.action_id,
                route_id=action.route_id,
                branch_id=action.branch_id,
                ownership_key=action.ownership_key or owner.ownership_key,
                specialist_ids=action.specialist_ids,
                failure_signature=owner.last_failure_signature or "failure:unknown",
                tissue_version=self.state.version,
            )
            self.state.failure_attributions.append(attribution)
            self._autosave()
            return attribution

    def set_branch_enabled(self, route_id: str, branch_id: str, enabled: bool) -> None:
        with self._lock:
            owner = self.state.branch(route_id, branch_id)
            owner.disabled = not enabled
            owner.local_version += 1
            owner.updated_at = datetime.now(timezone.utc)
            self._advance_version()

    def branch_hash(self, route_id: str, branch_id: str) -> str:
        with self._lock:
            owner = self.state.branch(route_id, branch_id)
            payload = owner.model_dump(mode="json")
            return TissueStore._hash(payload)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "tissue_id": self.state.tissue_id,
                "contract_id": self.state.contract_id,
                "contract_version": self.state.contract_version,
                "version": self.state.version,
                "branches": [
                    {
                        "route_id": branch.route_id,
                        "branch_id": branch.branch_id,
                        "ownership_key": branch.ownership_key,
                        "specialists": len(branch.specialists),
                        "observations": branch.observations,
                        "successes": branch.successes,
                        "failures": branch.failures,
                        "local_version": branch.local_version,
                        "disabled": branch.disabled,
                    }
                    for branch in self.state.branches
                ],
                "failure_attributions": len(self.state.failure_attributions),
            }

    def _select_route(
        self,
        route: RouteDefinition,
        features: dict[str, float],
        context: dict[str, Any],
    ) -> RouteSelection:
        route_branches = route.branches or [RouteBranch(branch_id="default")]
        route_owners = [self.state.branch(route.route_id, definition.branch_id) for definition in route_branches]
        trained_route = any(owner.specialists for owner in route_owners)
        candidates: list[dict[str, Any]] = []
        activations: dict[str, float] = {}
        specialist_diagnostics: dict[str, Any] = {}
        for definition, owner in zip(route_branches, route_owners, strict=True):
            hard = self._declarative._branch_activation(definition, context)
            eligible = not owner.disabled and (
                not self.state.config.hard_contract_gate or hard >= definition.minimum_activation
            )
            scored = self._specialist_scores(owner, features) if eligible else []
            selected = scored[: self.state.config.top_k_specialists]
            learned = selected[0][0] if selected else (1.0 if not trained_route else 0.0)
            activation = hard * (0.40 + 0.60 * learned) if eligible else 0.0
            activations[definition.branch_id] = activation
            specialist_diagnostics[definition.branch_id] = {
                "hard_activation": hard,
                "learned_activation": learned,
                "eligible": eligible,
                "active_specialists": [item.specialist_id for _, item in selected],
                "specialist_count": len(owner.specialists),
            }
            candidates.append(
                {
                    "activation": activation,
                    "priority": definition.priority,
                    "definition": definition,
                    "owner": owner,
                    "selected": selected,
                    "learned": learned,
                    "eligible": eligible,
                }
            )
        candidates.sort(key=lambda item: (item["activation"], item["priority"]), reverse=True)
        best = candidates[0]
        reason: str | None = None
        if not best["eligible"] or best["activation"] <= 0.0:
            reason = "No branch satisfied its hard contract gate"
        elif trained_route and self.state.config.abstain_on_novelty and best["learned"] < self.state.config.novelty_threshold:
            reason = f"Novel event: best specialist similarity {best['learned']:.4f} is below threshold"
        elif len(candidates) > 1:
            runner_up = candidates[1]
            margin = best["activation"] - runner_up["activation"]
            exact_tie = best["activation"] == runner_up["activation"] and best["priority"] == runner_up["priority"]
            if route.abstain_on_tie and exact_tie:
                reason = "Multiple branches tied for ownership"
            elif trained_route and margin < self.state.config.ownership_margin:
                reason = f"Ownership margin {margin:.4f} is below required margin"
        novelty = 0.0 if not trained_route else max(0.0, min(1.0, 1.0 - best["learned"]))
        if reason:
            trace = RouteTrace(
                route_id=route.route_id,
                branch_activations=activations,
                abstained=True,
                reason=reason,
                router_kind=self.router_kind,
                novelty_score=novelty,
                tissue_version=self.state.version,
                diagnostics={"branches": specialist_diagnostics},
            )
            return RouteSelection(route=route, trace=trace)
        selected_specialists = [item.specialist_id for _, item in best["selected"]]
        trace = RouteTrace(
            route_id=route.route_id,
            selected_branch_id=best["definition"].branch_id,
            branch_activations=activations,
            router_kind=self.router_kind,
            selected_specialist_ids=selected_specialists,
            novelty_score=novelty,
            ownership_key=best["owner"].ownership_key,
            tissue_version=self.state.version,
            diagnostics={
                "branches": specialist_diagnostics,
                "sparse_activation": {
                    "active": len(selected_specialists),
                    "available": sum(len(item["owner"].specialists) for item in candidates),
                },
            },
        )
        return RouteSelection(route=route, trace=trace)

    def _stable_features(self, context: dict[str, Any]) -> dict[str, float]:
        features = self.encoder.encode(context)
        ignored = self.state.config.ignored_feature_paths
        return {
            key: value
            for key, value in features.items()
            if not any(self._feature_mentions_path(key, path) for path in ignored)
        }

    @staticmethod
    def _feature_mentions_path(feature: str, path: str) -> bool:
        body = feature.split(":", 1)[-1]
        feature_path = body.split("=", 1)[0]
        return feature_path == path or feature_path.startswith(path + ".")

    @staticmethod
    def _move_toward(specialist: DendritronSpecialistState, features: dict[str, float], rate: float) -> None:
        for key in set(specialist.prototype) | set(features):
            old = specialist.prototype.get(key, 0.0)
            target = features.get(key, 0.0)
            updated = (1.0 - rate) * old + rate * target
            if updated < 0.02:
                specialist.prototype.pop(key, None)
            else:
                specialist.prototype[key] = min(1.0, updated)

    @staticmethod
    def _move_away(specialist: DendritronSpecialistState, features: dict[str, float], rate: float) -> None:
        for key in set(features) & set(specialist.prototype):
            updated = specialist.prototype[key] * (1.0 - rate)
            if updated < 0.02:
                specialist.prototype.pop(key, None)
            else:
                specialist.prototype[key] = updated

    @staticmethod
    def _nearest(
        owner: DendritronBranchState, features: dict[str, float]
    ) -> tuple[DendritronSpecialistState | None, float]:
        enabled = [item for item in owner.specialists if not item.disabled]
        if not enabled:
            return None, 0.0
        scored = [(weighted_jaccard(features, item.prototype), item) for item in enabled]
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1], scored[0][0]

    @staticmethod
    def _specialist_scores(
        owner: DendritronBranchState, features: dict[str, float]
    ) -> list[tuple[float, DendritronSpecialistState]]:
        scored = []
        for specialist in owner.specialists:
            if specialist.disabled:
                continue
            similarity = weighted_jaccard(features, specialist.prototype)
            adjusted = similarity * (0.85 + 0.15 * specialist.health)
            scored.append((adjusted, specialist))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored

    def _advance_version(self) -> None:
        self.state.version += 1
        self.state.updated_at = datetime.now(timezone.utc)
        self._autosave()

    def _autosave(self) -> None:
        if self.store_path is not None:
            TissueStore.save(self.store_path, self.state)


def event_context(event: CanonicalEvent) -> dict[str, Any]:
    return {
        "event": event.model_dump(mode="python"),
        "payload": event.payload,
        "metadata": event.metadata,
    }


def failure_signature(error: str) -> str:
    normalized = " ".join(error.casefold().split())[:500]
    return "failure:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
