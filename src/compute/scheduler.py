"""Deterministic capability and budget selection for AgentTasks.

Difficulty answers "how hard is this work?" while risk answers "how costly is
an incorrect or unauditable result?".  They are deliberately estimated as
separate signals before the scheduler chooses a capability tier.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, replace
from enum import IntEnum
from math import ceil, isfinite
from typing import Any, Iterable, Mapping

from src.core.database import Database, generate_id
from src.runtime.approvals import is_host_approval_actor
from src.runtime.contracts import AgentTask, AgentTaskProfile, ComputePlan, ModelDescriptor, default_agent_task_profile
from src.runtime.errors import (
    CapabilityUnavailable,
    ComputeBudgetExceeded,
    ComputeEscalationDenied,
)


logger = logging.getLogger(__name__)


class CapabilityTier(IntEnum):
    C0 = 0
    C1 = 1
    C2 = 2
    C3 = 3
    C4 = 4
    C5 = 5


class TaskTier(IntEnum):
    T0 = 0
    T1 = 1
    T2 = 2
    T3 = 3
    T4 = 4
    T5 = 5


_REASONING_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4}
_MECHANICAL_TASK_TYPES = frozenset({
    "formatting", "classification", "metadata",
    "deterministic-transform", "deterministic-format",
})


def _bounded(value: float, *, default: float = 0.0) -> float:
    try:
        number = float(value)
        if not isfinite(number):
            return default
        return max(0.0, min(1.0, number))
    except (TypeError, ValueError):
        return default


def _tier(value: str | int | CapabilityTier) -> CapabilityTier:
    if isinstance(value, CapabilityTier):
        return value
    if isinstance(value, int):
        return CapabilityTier(max(0, min(5, value)))
    text = str(value).strip().upper()
    if text.startswith("C"):
        text = text[1:]
    try:
        return CapabilityTier(max(0, min(5, int(text))))
    except (TypeError, ValueError):
        raise ValueError(f"invalid capability tier: {value!r}") from None


def _reasoning(value: str) -> int:
    return _REASONING_ORDER.get(str(value).strip().lower(), _REASONING_ORDER["medium"])


@dataclass(frozen=True)
class TaskCapabilityProfile:
    """A normalized difficulty/risk input profile for one AgentTask."""

    semantic_complexity: float = 0.0
    context_span: float = 0.0
    constraint_density: float = 0.0
    ambiguity: float = 0.0
    novelty: float = 0.0
    tool_depth: float = 0.0
    irreversibility: float = 0.0
    failure_cost: float = 0.0
    verifiability: float = 1.0
    entity_count: int = 0
    chapter_span: int = 1
    arc_count: int = 0
    planning_horizon: int = 1
    prior_failures: int = 0
    mutation_risk: float = 0.0
    output_size: float = 0.0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "TaskCapabilityProfile":
        raw = dict(value or {})
        kwargs: dict[str, Any] = {}
        for name in (
            "semantic_complexity", "context_span", "constraint_density", "ambiguity",
            "novelty", "tool_depth", "irreversibility", "failure_cost", "verifiability",
            "mutation_risk", "output_size",
        ):
            camel = "".join([name.split("_")[0], *[part.title() for part in name.split("_")[1:]]])
            kwargs[name] = raw.get(name, raw.get(camel, getattr(cls, name)))
        for name in ("entity_count", "chapter_span", "arc_count", "planning_horizon", "prior_failures"):
            camel = name.split("_")[0] + "".join(part.title() for part in name.split("_")[1:])
            kwargs[name] = raw.get(name, raw.get(camel, getattr(cls, name)))
        return cls(**kwargs)

    def difficulty(self) -> float:
        values = {
            "semantic": _bounded(self.semantic_complexity),
            "context": _bounded(self.context_span),
            "constraints": _bounded(self.constraint_density),
            "ambiguity": _bounded(self.ambiguity),
            "novelty": _bounded(self.novelty),
            "tools": _bounded(self.tool_depth),
            "entities": _bounded(self.entity_count / 20),
            "chapters": _bounded(self.chapter_span / 12),
            "arcs": _bounded(self.arc_count / 4),
            "horizon": _bounded(self.planning_horizon / 12),
            "output": _bounded(self.output_size),
        }
        weights = {
            "semantic": .20, "context": .14, "constraints": .14, "ambiguity": .10,
            "novelty": .10, "tools": .08, "entities": .06, "chapters": .05,
            "arcs": .05, "horizon": .05, "output": .03,
        }
        return round(sum(values[key] * weight for key, weight in weights.items()), 4)

    def risk(self) -> float:
        # Low verifiability increases risk.  Prior failures are a bounded
        # signal, not a permanent escalation of every future task.
        return round(
            .25 * _bounded(self.irreversibility)
            + .20 * _bounded(self.failure_cost)
            + .20 * _bounded(self.mutation_risk)
            + .15 * (1.0 - _bounded(self.verifiability, default=1.0))
            + .10 * _bounded(self.prior_failures / 3)
            + .10 * _bounded(self.output_size),
            4,
        )


@dataclass(frozen=True)
class TaskCapabilityAssessment:
    """The Host's auditable view of a task's compute signals."""

    profile: TaskCapabilityProfile
    rationale: tuple[str, ...] = ()
    objective_signals: tuple[str, ...] = ()
    declared_signals: tuple[str, ...] = ()


class TaskCapabilityProfiler:
    """Build a conservative profile from a durable ``AgentTask`` envelope.

    ``input.capabilityProfile`` is an Agent-provided hint and therefore has a
    deliberately small contribution when no Host-observed signal exists.
    Objective fields in the task envelope and its context manifest are the
    authority for routing floors.  This class does not read Canon or invoke a
    provider; it only turns already-persisted task metadata into bounded
    compute inputs.
    """

    _RATIO_FIELDS = (
        "semantic_complexity", "context_span", "constraint_density", "ambiguity",
        "novelty", "tool_depth", "irreversibility", "failure_cost",
        "verifiability", "mutation_risk", "output_size",
    )
    _COUNT_FIELDS = (
        "entity_count", "chapter_span", "arc_count", "planning_horizon", "prior_failures",
    )
    _OBJECTIVE_CONTAINERS = (
        "objectiveProfile", "objective_profile", "objectiveSignals", "objective_signals",
        "hostProfile", "host_profile",
    )

    @classmethod
    def assess(
        cls,
        task: AgentTask,
        *,
        declared_profile: TaskCapabilityProfile | None = None,
    ) -> TaskCapabilityAssessment:
        payload = dict(task.input_payload) if isinstance(task.input_payload, Mapping) else {}
        constraints = dict(task.constraints) if isinstance(task.constraints, Mapping) else {}
        objective_maps = cls._objective_maps(payload, constraints)
        direct_maps = (*objective_maps, payload, constraints)

        declared_names: list[str] = []
        rationale: list[str] = []
        if declared_profile is not None:
            # The explicit scheduler argument is a Host-side assessment.  It
            # remains fully effective for existing integrations, while task
            # objective signals below can still raise a conservative floor.
            values = cls._profile_values(declared_profile)
            rationale.append("hostCapabilityProfile=authoritativeInput")
        else:
            raw_declared = payload.get("capabilityProfile")
            if isinstance(raw_declared, Mapping):
                values = cls._low_weight_declared_values(raw_declared, declared_names)
                rationale.append("agentCapabilityProfile=lowWeight")
            else:
                # Durable constraints are Host-enforced task metadata, not a
                # free-form provider preference.  Preserve the old mapping
                # behavior for callers that already persist these fields.
                values = cls._profile_values(TaskCapabilityProfile.from_mapping(constraints))

        objective_names: list[str] = []

        def record(name: str, value: Any, *, normalized: bool = True) -> None:
            if value is None:
                return
            objective_names.append(name)
            if name in cls._RATIO_FIELDS:
                values[name] = _bounded(value) if normalized else _bounded(float(value))
            else:
                values[name] = max(0, _safe_count(value))

        context_value = cls._lookup(direct_maps, "context_span", "contextSpan")
        if context_value is not None:
            record("context_span", context_value)
        else:
            context_found = cls._lookup_with_key(
                direct_maps,
                "contextSize", "context_size", "contextTokens", "context_tokens",
                "contextChars", "context_chars", "contextLength", "context_length",
            )
            if context_found is not None:
                context_size, context_key = context_found
                values["context_span"] = _size_ratio(context_size, context_key)
                objective_names.append("contextSize")
            else:
                inferred_context = cls._serialized_context_size(payload)
                if inferred_context:
                    values["context_span"] = _bounded(inferred_context / 400_000)
                    objective_names.append("contextSize")

        entity_value = cls._lookup(direct_maps, "entity_count", "entityCount")
        if entity_value is None:
            entity_value = cls._collection_count(
                direct_maps, "entities", "entityIds", "entity_ids", "characters", "locations"
            )
        if entity_value is not None:
            record("entity_count", entity_value, normalized=False)

        chapter_value = cls._lookup(direct_maps, "chapter_span", "chapterSpan")
        if chapter_value is None:
            chapter_value = cls._range_span(direct_maps, "chapterStart", "chapter_start", "chapterEnd", "chapter_end")
        if chapter_value is None:
            chapter_value = cls._collection_count(direct_maps, "chapters", "chapterIds", "chapter_ids")
        if chapter_value is not None:
            record("chapter_span", chapter_value, normalized=False)

        arc_value = cls._lookup(direct_maps, "arc_count", "arcCount")
        if arc_value is None:
            arc_value = cls._collection_count(direct_maps, "arcs", "arcIds", "arc_ids")
        if arc_value is not None:
            record("arc_count", arc_value, normalized=False)

        horizon_value = cls._lookup(direct_maps, "planning_horizon", "planningHorizon")
        if horizon_value is None:
            horizon_value = cls._lookup(
                direct_maps, "targetChapterCount", "target_chapter_count", "planningChapterCount",
            )
        if horizon_value is None:
            horizon_value = cls._range_span(
                direct_maps, "planningStartChapter", "planning_start_chapter",
                "planningEndChapter", "planning_end_chapter",
            )
        if horizon_value is not None:
            record("planning_horizon", horizon_value, normalized=False)

        constraint_value = cls._lookup(direct_maps, "constraint_density", "constraintDensity")
        if constraint_value is not None:
            record("constraint_density", constraint_value)
        else:
            constraint_count = cls._lookup(
                direct_maps, "constraintCount", "constraint_count", "constraintsCount", "constraints_count",
            )
            if constraint_count is None:
                constraint_count = cls._collection_count(
                    direct_maps,
                    "requirements", "rules", "must", "mustNot", "must_not",
                    "bannedElements", "banned_elements", "acceptanceCriteria", "acceptance_criteria",
                )
            if constraint_count is None:
                nested_constraints = payload.get("constraints")
                constraint_count = len(nested_constraints) if isinstance(nested_constraints, Mapping) else None
            if constraint_count is not None:
                values["constraint_density"] = _bounded(_safe_count(constraint_count) / 12)
                objective_names.append("constraintCount")

        tool_value = cls._lookup(direct_maps, "tool_depth", "toolDepth")
        if tool_value is not None:
            record("tool_depth", tool_value)
        else:
            tool_count = cls._lookup(
                direct_maps, "requiredToolCalls", "required_tool_calls", "toolCount", "tool_count",
            )
            if tool_count is None:
                tool_count = cls._collection_count(direct_maps, "toolCalls", "tool_calls")
            if tool_count is not None:
                values["tool_depth"] = _bounded(_safe_count(tool_count) / 20)
                objective_names.append("toolDepth")

        output_value = cls._lookup(direct_maps, "output_size", "outputSize")
        if output_value is not None:
            record("output_size", output_value)
        else:
            output_found = cls._lookup_with_key(
                direct_maps,
                "outputTokens", "output_tokens", "maxOutputTokens", "max_output_tokens",
                "outputChars", "output_chars", "outputLength", "output_length",
            )
            if output_found is not None:
                output_size, output_key = output_found
                values["output_size"] = _size_ratio(output_size, output_key)
                objective_names.append("outputSize")

        prior_failures = cls._lookup(direct_maps, "prior_failures", "priorFailures", "failureCount", "failure_count")
        if prior_failures is not None:
            record("prior_failures", prior_failures, normalized=False)

        unresolved = cls._lookup(
            direct_maps, "unresolvedIssues", "unresolved_issues", "openIssues", "open_issues",
        )
        if unresolved is None:
            unresolved = cls._collection_count(
                direct_maps, "blockingIssues", "blocking_issues", "reviewIssues", "review_issues",
            )
        if unresolved is not None:
            unresolved_count = _safe_count(unresolved)
            values["ambiguity"] = max(values["ambiguity"], _bounded(unresolved_count / 10))
            values["failure_cost"] = max(values["failure_cost"], _bounded(unresolved_count / 12))
            objective_names.append("unresolvedIssues")

        dependency_depth = cls._lookup(
            direct_maps,
            "canonDependencyDepth", "canon_dependency_depth", "dependencyDepth", "dependency_depth",
            "canonDepth", "canon_depth",
        )
        if dependency_depth is not None:
            depth_signal = _bounded(_safe_count(dependency_depth) / 8)
            values["semantic_complexity"] = max(values["semantic_complexity"], depth_signal)
            values["context_span"] = max(values["context_span"], _bounded(depth_signal * .75))
            objective_names.append("canonDependencyDepth")

        novelty_value = cls._lookup(direct_maps, "novelty")
        if novelty_value is None:
            novelty_value = cls._collection_count(direct_maps, "newEntities", "new_entities", "newCanonNodes", "new_canon_nodes")
            if novelty_value is not None:
                novelty_value = _bounded(_safe_count(novelty_value) / 20)
        if novelty_value is not None:
            record("novelty", novelty_value)

        mutation_value = cls._lookup(direct_maps, "mutation_risk", "mutationRisk")
        mutation_class = str(
            constraints.get("canonMutationType")
            or constraints.get("mutationClass")
            or payload.get("canonMutationType")
            or payload.get("mutationClass")
            or ""
        ).strip().lower()
        if mutation_value is None:
            if mutation_class in {"structural", "world_rule", "world-rule", "world_rule_change"}:
                mutation_value = .9
            elif bool(constraints.get("canon_write")) or mutation_class in {"normal", "canon", "canonical"}:
                mutation_value = .8
            elif bool(constraints.get("planning_write")):
                mutation_value = .45
            if mutation_value is not None:
                objective_names.append("mutationRisk")
        if mutation_value is not None:
            values["mutation_risk"] = max(values["mutation_risk"], _bounded(mutation_value))

        irreversibility_value = cls._lookup(
            direct_maps, "irreversibility", "irreversibleRisk", "irreversible_risk",
        )
        if irreversibility_value is None and (
            bool(constraints.get("irreversible"))
            or mutation_class in {"structural", "world_rule", "world-rule", "world_rule_change"}
        ):
            irreversibility_value = .85
            objective_names.append("irreversibility")
        if irreversibility_value is not None:
            values["irreversibility"] = max(values["irreversibility"], _bounded(irreversibility_value))

        normalized_task_type = str(task.task_type).strip().lower().replace("_", "-")
        if normalized_task_type in _MECHANICAL_TASK_TYPES and not objective_names:
            # The task type is a Host-owned contract for these operations; do
            # not let the profile defaults (one chapter/one planning step)
            # manufacture an artificial T1 signal.
            values["semantic_complexity"] = 0.0
            values["context_span"] = 0.0
            values["chapter_span"] = 0
            values["planning_horizon"] = 0
            objective_names.append("mechanicalTaskType")

        semantic_value = cls._lookup(direct_maps, "semantic_complexity", "semanticComplexity")
        if semantic_value is not None:
            record("semantic_complexity", semantic_value)
        elif objective_names and any(name != "mechanicalTaskType" for name in objective_names):
            # Objective size/topology signals provide a bounded complexity
            # hint without allowing any single count to dominate difficulty.
            values["semantic_complexity"] = max(
                values["semantic_complexity"],
                _bounded(values["entity_count"] / 40),
                _bounded(values["chapter_span"] / 24),
                _bounded(values["arc_count"] / 8),
                _bounded(values["planning_horizon"] / 24),
            )
            objective_names.append("taskTopology")

        # A low declared verifiability value may not erase Host evidence.  If
        # no objective value exists, _low_weight_declared_values already keeps
        # the degradation to 25% of its normal influence.
        objective_signals = tuple(dict.fromkeys(objective_names))
        declared_signals = tuple(dict.fromkeys(declared_names))
        if objective_signals:
            rationale.append("objectiveSignals=" + ",".join(objective_signals))
        else:
            rationale.append("objectiveSignals=none")
        return TaskCapabilityAssessment(
            profile=TaskCapabilityProfile(**values),
            rationale=tuple(rationale),
            objective_signals=objective_signals,
            declared_signals=declared_signals,
        )

    @classmethod
    def from_task(
        cls,
        task: AgentTask,
        *,
        declared_profile: TaskCapabilityProfile | None = None,
    ) -> TaskCapabilityProfile:
        """Return only the normalized profile for simple callers."""
        return cls.assess(task, declared_profile=declared_profile).profile

    @classmethod
    def _low_weight_declared_values(
        cls,
        raw: Mapping[str, Any],
        declared_names: list[str],
    ) -> dict[str, Any]:
        defaults = cls._profile_values(TaskCapabilityProfile())
        declared = TaskCapabilityProfile.from_mapping(raw)
        for name in (*cls._RATIO_FIELDS, *cls._COUNT_FIELDS):
            if not cls._has_key(raw, name):
                continue
            declared_names.append(name)
            value = getattr(declared, name)
            if name == "verifiability":
                defaults[name] = 1.0 - (1.0 - _bounded(value, default=1.0)) * .25
            elif name in cls._RATIO_FIELDS:
                defaults[name] = _bounded(value) * .25
            else:
                defaults[name] = max(0, int(round(_safe_count(value) * .25)))
        return defaults

    @staticmethod
    def _profile_values(profile: TaskCapabilityProfile) -> dict[str, Any]:
        return {
            name: getattr(profile, name)
            for name in (
                "semantic_complexity", "context_span", "constraint_density", "ambiguity",
                "novelty", "tool_depth", "irreversibility", "failure_cost", "verifiability",
                "entity_count", "chapter_span", "arc_count", "planning_horizon", "prior_failures",
                "mutation_risk", "output_size",
            )
        }

    @classmethod
    def _objective_maps(cls, payload: Mapping[str, Any], constraints: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        result: list[Mapping[str, Any]] = []
        for source in (payload, constraints):
            for key in cls._OBJECTIVE_CONTAINERS:
                value = source.get(key)
                if isinstance(value, Mapping):
                    result.append(value)
            for key in ("contextManifest", "context_manifest", "context"):
                value = source.get(key)
                if isinstance(value, Mapping):
                    result.append(value)
        return tuple(result)

    @staticmethod
    def _lookup(mappings: Iterable[Mapping[str, Any]], *keys: str) -> Any | None:
        found = TaskCapabilityProfiler._lookup_with_key(mappings, *keys)
        return found[0] if found is not None else None

    @staticmethod
    def _lookup_with_key(
        mappings: Iterable[Mapping[str, Any]], *keys: str,
    ) -> tuple[Any, str] | None:
        for mapping in mappings:
            if not isinstance(mapping, Mapping):
                continue
            for key in keys:
                if key in mapping and mapping[key] is not None:
                    return mapping[key], key
        return None

    @classmethod
    def _collection_count(cls, mappings: Iterable[Mapping[str, Any]], *keys: str) -> int | None:
        found = cls._lookup_with_key(mappings, *keys)
        if found is None:
            return None
        value = found[0]
        if isinstance(value, Mapping):
            return len(value)
        if isinstance(value, (list, tuple, set, frozenset)):
            return len(value)
        return _safe_count(value)

    @classmethod
    def _range_span(
        cls,
        mappings: Iterable[Mapping[str, Any]],
        start_key: str,
        start_alias: str,
        end_key: str,
        end_alias: str,
    ) -> int | None:
        start = cls._lookup(mappings, start_key, start_alias)
        end = cls._lookup(mappings, end_key, end_alias)
        if start is None or end is None:
            return None
        return abs(_safe_count(end) - _safe_count(start)) + 1

    @staticmethod
    def _has_key(mapping: Mapping[str, Any], name: str) -> bool:
        camel = name.split("_")[0] + "".join(part.title() for part in name.split("_")[1:])
        return name in mapping or camel in mapping

    @staticmethod
    def _serialized_context_size(payload: Mapping[str, Any]) -> int:
        candidates: list[Any] = []
        for key in (
            "contextManifest", "context_manifest", "context", "prompt", "draft",
            "chapterText", "chapter_text", "relevantEvidence", "relevant_evidence",
        ):
            if key in payload and payload[key] not in (None, "", [], {}):
                candidates.append(payload[key])
        if not candidates:
            return 0
        try:
            return len(json.dumps(candidates, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return 0


def _safe_count(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (list, tuple, set, frozenset, Mapping)):
        return len(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not isfinite(number):
        return 0
    return max(0, int(round(number)))


def _size_ratio(value: Any, key: str | None) -> float:
    number = _safe_number(value)
    if number is None:
        return 0.0
    if number <= 1:
        return _bounded(number)
    denominator = 400_000
    if key and ("token" in key.lower()):
        denominator = 100_000
    elif key and ("char" in key.lower() or "length" in key.lower()):
        denominator = 400_000
    return _bounded(number / denominator)


def _safe_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


@dataclass(frozen=True)
class DifficultyRiskEstimate:
    difficulty: float
    risk: float
    required_tier: TaskTier
    rationale: tuple[str, ...] = ()


class DifficultyRiskEstimator:
    """Map normalized task signals to a minimum task tier."""

    def estimate(self, profile: TaskCapabilityProfile) -> DifficultyRiskEstimate:
        difficulty = profile.difficulty()
        risk = profile.risk()
        # Risk is intentionally a separate gate.  A small task with a high
        # mutation/failure cost still receives a conservative floor.
        # Zero-signal mechanical work is allowed to remain T0.  Normal task
        # profiles still carry their own C1+ capability floor, so this does
        # not downgrade existing intelligent task defaults.
        required = max(0, ceil(difficulty * 5), ceil(risk * 5))
        if profile.irreversibility >= .75 or profile.mutation_risk >= .75:
            required = max(required, 3)
        required = min(5, required)
        rationale = (
            f"difficulty={difficulty:.3f}",
            f"risk={risk:.3f}",
            f"required={required}",
        )
        return DifficultyRiskEstimate(difficulty, risk, TaskTier(required), rationale)


@dataclass(frozen=True)
class RegisteredCapability:
    descriptor: ModelDescriptor
    capability: CapabilityTier
    cost_multiplier: float = 1.0
    health: str = "ready"
    tags: tuple[str, ...] = ()
    capability_profile: Mapping[str, CapabilityTier] = field(default_factory=dict)

    def capability_for(self, dimension: str | None) -> CapabilityTier:
        if dimension and dimension in self.capability_profile:
            return self.capability_profile[dimension]
        return self.capability


class CapabilityRegistry:
    """Registry of discoverable runtime/model capabilities."""

    def __init__(self) -> None:
        # External model ids are provider-scoped.  Keeping provider_id in the
        # key prevents two providers exposing the same model name from
        # overwriting one another before the Scheduler applies constraints.
        self._models: dict[tuple[str, str, str], RegisteredCapability] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _model_key(descriptor: ModelDescriptor) -> tuple[str, str, str]:
        return (
            descriptor.runtime_type,
            str(descriptor.provider_id or "").strip(),
            descriptor.model_id,
        )

    def register_model(
        self,
        descriptor: ModelDescriptor,
        *,
        capability: str | int | CapabilityTier,
        cost_multiplier: float = 1.0,
        health: str = "ready",
        tags: Iterable[str] = (),
        capability_profile: Mapping[str, str | int | CapabilityTier] | None = None,
    ) -> RegisteredCapability:
        normalized_profile = {
            str(dimension).strip(): _tier(value)
            for dimension, value in (
                capability_profile if capability_profile is not None else descriptor.capability_profile
            ).items()
            if str(dimension).strip()
        }
        registered = RegisteredCapability(
            descriptor=descriptor,
            capability=_tier(capability),
            cost_multiplier=max(0.0, float(cost_multiplier)),
            health=health,
            tags=tuple(tags),
            capability_profile=normalized_profile,
        )
        with self._lock:
            self._models[self._model_key(descriptor)] = registered
        return registered

    def remove_model(self, runtime_type: str, model_id: str, provider_id: str | None = None) -> None:
        with self._lock:
            if provider_id is not None:
                self._models.pop(
                    (runtime_type, str(provider_id).strip(), model_id),
                    None,
                )
                return
            # Preserve the old two-argument API while removing every
            # provider-scoped registration for that external model id.
            keys = [
                key for key in self._models
                if key[0] == runtime_type and key[2] == model_id
            ]
            for key in keys:
                self._models.pop(key, None)

    def clear_runtime(self, runtime_type: str) -> int:
        """Remove every cached model for one runtime before rediscovery."""
        with self._lock:
            keys = [key for key in self._models if key[0] == runtime_type]
            for key in keys:
                self._models.pop(key, None)
            return len(keys)

    def runtime_health(self, runtime_type: str, *, default: str = "ready") -> str:
        """Return the health currently observed for a runtime's models.

        Capability refreshes replace a runtime's model catalog, but they must
        not accidentally turn an unavailable runtime into a ready candidate.
        Runtime health is normally uniform for all models in one adapter; if
        an older catalog contains mixed observations, prefer the conservative
        unavailable state.
        """
        normalized_default = str(default or "unknown").strip().lower() or "unknown"
        with self._lock:
            health = {
                str(item.health or "unknown").strip().lower() or "unknown"
                for item in self._models.values()
                if item.descriptor.runtime_type == runtime_type
            }
        if not health:
            return normalized_default
        if "unavailable" in health:
            return "unavailable"
        if "unknown" in health:
            return "unknown"
        return sorted(health)[0]

    def set_runtime_health(self, runtime_type: str, health: str) -> int:
        """Synchronize all models for a runtime with its host health gate."""
        normalized = str(health or "unknown").strip().lower() or "unknown"
        updated = 0
        with self._lock:
            for key, item in tuple(self._models.items()):
                if item.descriptor.runtime_type != runtime_type or item.health == normalized:
                    continue
                self._models[key] = replace(item, health=normalized)
                updated += 1
        return updated

    def candidates(self, *, minimum: CapabilityTier = CapabilityTier.C0,
                   maximum: CapabilityTier = CapabilityTier.C5) -> tuple[RegisteredCapability, ...]:
        with self._lock:
            return tuple(
                item for item in self._models.values()
                if minimum <= item.capability <= maximum
                and item.descriptor.available and item.health == "ready"
            )

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                **item.descriptor.to_dict(),
                "capability": f"C{int(item.capability)}",
                "costMultiplier": item.cost_multiplier,
                "health": item.health,
                "tags": list(item.tags),
                "capabilityProfile": {
                    dimension: f"C{int(capability)}"
                    for dimension, capability in item.capability_profile.items()
                },
            }
            for item in self.candidates()
        ]


@dataclass(frozen=True)
class ComputePolicy:
    """Host-owned compute strategy applied before model selection.

    The public Studio surface exposes a small set of named strategies rather
    than leaking the scheduler's internal knobs.  The critical floor remains
    an invariant of every strategy; ``budget_mode=soft`` only changes what
    happens after the durable quota is exhausted and never lowers a task's
    capability floor.
    """

    default_floor: CapabilityTier = CapabilityTier.C0
    default_preferred: CapabilityTier = CapabilityTier.C2
    default_ceiling: CapabilityTier = CapabilityTier.C4
    critical_floor: CapabilityTier = CapabilityTier.C3
    allow_agent_escalation: bool = False
    budget_unit: str = "NF_CU"
    strategy: str = "balanced"
    budget_mode: str = "hard"

    def __post_init__(self) -> None:
        strategy = normalize_compute_strategy(self.strategy)
        budget_mode = str(self.budget_mode or "hard").strip().lower()
        if budget_mode not in {"hard", "soft"}:
            raise ValueError("budget_mode must be hard or soft")
        object.__setattr__(self, "strategy", strategy)
        object.__setattr__(self, "budget_mode", budget_mode)

    @classmethod
    def for_strategy(cls, strategy: str) -> "ComputePolicy":
        """Build one of the user-facing strategies with safe defaults."""
        key = normalize_compute_strategy(strategy)
        values = COMPUTE_STRATEGIES[key]
        return cls(
            default_floor=values["default_floor"],
            default_preferred=values["default_preferred"],
            default_ceiling=values["default_ceiling"],
            critical_floor=values["critical_floor"],
            allow_agent_escalation=values["allow_agent_escalation"],
            budget_mode=values["budget_mode"],
            strategy=key,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "floor": f"C{int(self.default_floor)}",
            "preferred": f"C{int(self.default_preferred)}",
            "ceiling": f"C{int(self.default_ceiling)}",
            "criticalFloor": f"C{int(self.critical_floor)}",
            "allowAgentEscalation": self.allow_agent_escalation,
            "budgetMode": self.budget_mode,
            "budgetUnit": self.budget_unit,
        }


_COMPUTE_STRATEGY_ALIASES = {
    "轻量": "light",
    "均衡": "balanced",
    "交付": "delivery",
    "求索": "exploration",
    "explore": "exploration",
    "求索模式": "exploration",
}
_COMPUTE_STRATEGY_NAMES = {
    "light": "轻量",
    "balanced": "均衡",
    "delivery": "交付",
    "exploration": "求索",
}
_COMPUTE_STRATEGY_DESCRIPTIONS = {
    "light": "优先低成本或本地 Runtime，高档模型只处理必要任务。",
    "balanced": "低成本 Runtime 做辅助，中高档模型负责主要创作与复杂任务。",
    "delivery": "质量优先，复杂任务使用高档模型；关键任务保留 Critical Floor。",
    "exploration": "最高质量优先，允许经审批的主动 escalation；预算作为软限制。",
}


def normalize_compute_strategy(strategy: str) -> str:
    key = str(strategy or "").strip().lower().replace("_", "-")
    key = _COMPUTE_STRATEGY_ALIASES.get(key, key)
    if key not in _COMPUTE_STRATEGY_NAMES:
        raise ValueError("strategy must be one of light, balanced, delivery, exploration")
    return key


# These values intentionally differ only in routing bounds and escalation
# authority.  The task profile and mutation-specific Critical Floor are still
# evaluated by ComputeScheduler.plan afterwards.
COMPUTE_STRATEGIES: dict[str, dict[str, Any]] = {
    "light": {
        "name": _COMPUTE_STRATEGY_NAMES["light"],
        "description": _COMPUTE_STRATEGY_DESCRIPTIONS["light"],
        "default_floor": CapabilityTier.C0,
        "default_preferred": CapabilityTier.C1,
        "default_ceiling": CapabilityTier.C3,
        "critical_floor": CapabilityTier.C3,
        "allow_agent_escalation": False,
        "budget_mode": "hard",
    },
    "balanced": {
        "name": _COMPUTE_STRATEGY_NAMES["balanced"],
        "description": _COMPUTE_STRATEGY_DESCRIPTIONS["balanced"],
        "default_floor": CapabilityTier.C0,
        "default_preferred": CapabilityTier.C2,
        "default_ceiling": CapabilityTier.C4,
        "critical_floor": CapabilityTier.C3,
        "allow_agent_escalation": False,
        "budget_mode": "hard",
    },
    "delivery": {
        "name": _COMPUTE_STRATEGY_NAMES["delivery"],
        "description": _COMPUTE_STRATEGY_DESCRIPTIONS["delivery"],
        "default_floor": CapabilityTier.C0,
        "default_preferred": CapabilityTier.C2,
        "default_ceiling": CapabilityTier.C5,
        "critical_floor": CapabilityTier.C3,
        "allow_agent_escalation": False,
        "budget_mode": "hard",
    },
    "exploration": {
        "name": _COMPUTE_STRATEGY_NAMES["exploration"],
        "description": _COMPUTE_STRATEGY_DESCRIPTIONS["exploration"],
        "default_floor": CapabilityTier.C0,
        "default_preferred": CapabilityTier.C3,
        "default_ceiling": CapabilityTier.C5,
        "critical_floor": CapabilityTier.C3,
        "allow_agent_escalation": True,
        "budget_mode": "soft",
    },
}


class ComputePolicyStore:
    """Persist the selected Studio strategy without touching narrative Canon."""

    def __init__(self, db: Database, *, scope: str = "studio") -> None:
        self.db = db
        self.scope = str(scope).strip() or "studio"

    def load(self) -> ComputePolicy:
        row = self.db.fetchone(
            "SELECT strategy, budget_mode FROM compute_policy_settings WHERE scope=?",
            (self.scope,),
        )
        if row is None:
            policy = ComputePolicy.for_strategy("delivery")
            self.save(policy)
            return policy
        strategy = normalize_compute_strategy(str(row.get("strategy") or "delivery"))
        policy = ComputePolicy.for_strategy(strategy)
        stored_budget_mode = str(row.get("budget_mode") or policy.budget_mode).strip().lower()
        if stored_budget_mode != policy.budget_mode:
            policy = ComputePolicy(
                default_floor=policy.default_floor,
                default_preferred=policy.default_preferred,
                default_ceiling=policy.default_ceiling,
                critical_floor=policy.critical_floor,
                allow_agent_escalation=policy.allow_agent_escalation,
                budget_unit=policy.budget_unit,
                strategy=policy.strategy,
                budget_mode=stored_budget_mode,
            )
        return policy

    def save(self, policy: ComputePolicy | str) -> ComputePolicy:
        value = policy if isinstance(policy, ComputePolicy) else ComputePolicy.for_strategy(policy)
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO compute_policy_settings(scope, strategy, budget_mode, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(scope) DO UPDATE SET strategy=excluded.strategy,
                   budget_mode=excluded.budget_mode, updated_at=CURRENT_TIMESTAMP""",
                (self.scope, value.strategy, value.budget_mode),
            )
        return value

    @staticmethod
    def strategies() -> list[dict[str, Any]]:
        return [
            {
                "id": key,
                "name": values["name"],
                "description": values["description"],
                "allowAgentEscalation": values["allow_agent_escalation"],
                "budgetMode": values["budget_mode"],
                "criticalFloor": f"C{int(values['critical_floor'])}",
            }
            for key, values in COMPUTE_STRATEGIES.items()
        ]


@dataclass
class BudgetReservation:
    reservation_id: str
    amount: float
    critical: bool
    consumed: float = 0.0
    released: bool = False

    @property
    def remaining(self) -> float:
        return max(0.0, self.amount - self.consumed)


class BudgetBroker:
    """Thread-safe NF Compute Unit quota with an explicit critical reserve."""

    def __init__(self, *, total: float, critical_reserve: float = 0.0,
                 db: Database | None = None, scope: str = "global") -> None:
        if total < 0 or critical_reserve < 0 or critical_reserve > total:
            raise ValueError("critical_reserve must be within total budget")
        self.total = float(total)
        self.critical_reserve = float(critical_reserve)
        self.db = db
        self.scope = scope
        self._normal_reserved = 0.0
        self._critical_reserved = 0.0
        self._consumed = 0.0
        self._reservations: dict[str, BudgetReservation] = {}
        self._lock = threading.RLock()
        if self.db is not None:
            self.db.execute(
                """INSERT INTO compute_budget_accounts(scope, total, critical_reserve, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(scope) DO NOTHING""",
                (scope, self.total, self.critical_reserve),
            )

    def reserve(self, amount: float, *, critical: bool = False) -> BudgetReservation:
        amount = max(0.0, float(amount))
        with self._lock:
            if self.db is not None:
                with self.db.transaction() as conn:
                    account = conn.execute(
                        "SELECT total, critical_reserve FROM compute_budget_accounts WHERE scope=?",
                        (self.scope,),
                    ).fetchone()
                    if account is None:
                        raise ComputeBudgetExceeded(f"budget account is not configured: {self.scope}")
                    consumed = float(conn.execute(
                        "SELECT COALESCE(SUM(consumed), 0) AS value FROM compute_budget_reservations WHERE scope=?",
                        (self.scope,),
                    ).fetchone()["value"] or 0)
                    reserved = float(conn.execute(
                        """SELECT COALESCE(SUM(amount - consumed), 0) AS value
                           FROM compute_budget_reservations WHERE scope=? AND status='reserved'""",
                        (self.scope,),
                    ).fetchone()["value"] or 0)
                    available = float(account["total"]) - consumed - reserved
                    if not critical:
                        available -= float(account["critical_reserve"])
                    if amount > available + 1e-9:
                        raise ComputeBudgetExceeded(
                            f"insufficient compute budget for {amount:.3f} NF_CU",
                            details={"requested": amount, "available": max(0.0, available), "critical": critical},
                        )
                    reservation = BudgetReservation(generate_id(), amount, critical)
                    conn.execute(
                        """INSERT INTO compute_budget_reservations(
                               id, scope, amount, critical, consumed, status, created_at
                           ) VALUES (?, ?, ?, ?, 0, 'reserved', CURRENT_TIMESTAMP)""",
                        (reservation.reservation_id, self.scope, amount, int(critical)),
                    )
                self._reservations[reservation.reservation_id] = reservation
                return reservation
            available = self.total - self._normal_reserved - self._critical_reserved - self._consumed
            if not critical:
                available -= self.critical_reserve
            if amount > available + 1e-9:
                raise ComputeBudgetExceeded(
                    f"insufficient compute budget for {amount:.3f} NF_CU",
                    details={"requested": amount, "available": max(0.0, available), "critical": critical},
                )
            reservation = BudgetReservation(generate_id(), amount, critical)
            self._reservations[reservation.reservation_id] = reservation
            if critical:
                self._critical_reserved += amount
            else:
                self._normal_reserved += amount
            return reservation

    def consume(self, reservation_id: str, amount: float) -> float:
        with self._lock:
            amount = max(0.0, float(amount))
            if self.db is not None:
                with self.db.transaction() as conn:
                    row = conn.execute(
                        "SELECT amount, critical, consumed, status FROM compute_budget_reservations WHERE id=? AND scope=?",
                        (reservation_id, self.scope),
                    ).fetchone()
                    if row is None or row["status"] != "reserved":
                        raise KeyError(f"budget reservation not active: {reservation_id}")
                    remaining = float(row["amount"] - row["consumed"])
                    if amount > remaining + 1e-9:
                        raise ComputeBudgetExceeded("consumption exceeds reservation")
                    conn.execute(
                        "UPDATE compute_budget_reservations SET consumed=consumed+? WHERE id=? AND scope=?",
                        (amount, reservation_id, self.scope),
                    )
                reservation = self._reservations.get(reservation_id)
                if reservation is None:
                    reservation = BudgetReservation(
                        reservation_id,
                        float(row["amount"]),
                        bool(row["critical"]),
                    )
                    self._reservations[reservation_id] = reservation
                reservation.consumed = float(row["consumed"]) + amount
                return reservation.consumed
            reservation = self._get_active(reservation_id)
            if amount > reservation.remaining + 1e-9:
                raise ComputeBudgetExceeded("consumption exceeds reservation")
            reservation.consumed += amount
            self._consumed += amount
            if reservation.critical:
                self._critical_reserved -= amount
            else:
                self._normal_reserved -= amount
            return reservation.consumed

    def extend(self, reservation_id: str, amount: float) -> BudgetReservation:
        """Atomically extend an active reservation for an approved escalation."""
        amount = max(0.0, float(amount))
        with self._lock:
            if self.db is not None:
                with self.db.transaction() as conn:
                    account = conn.execute(
                        "SELECT total, critical_reserve FROM compute_budget_accounts WHERE scope=?",
                        (self.scope,),
                    ).fetchone()
                    row = conn.execute(
                        "SELECT amount, critical, consumed, status FROM compute_budget_reservations "
                        "WHERE id=? AND scope=?",
                        (reservation_id, self.scope),
                    ).fetchone()
                    if account is None or row is None or row["status"] != "reserved":
                        raise KeyError(f"budget reservation not active: {reservation_id}")
                    consumed = float(conn.execute(
                        "SELECT COALESCE(SUM(consumed), 0) AS value "
                        "FROM compute_budget_reservations WHERE scope=?",
                        (self.scope,),
                    ).fetchone()["value"] or 0)
                    reserved = float(conn.execute(
                        "SELECT COALESCE(SUM(amount - consumed), 0) AS value "
                        "FROM compute_budget_reservations WHERE scope=? AND status='reserved'",
                        (self.scope,),
                    ).fetchone()["value"] or 0)
                    available = float(account["total"]) - consumed - reserved
                    if not bool(row["critical"]):
                        available -= float(account["critical_reserve"])
                    if amount > available + 1e-9:
                        raise ComputeBudgetExceeded(
                            f"insufficient compute budget for escalation of {amount:.3f} NF_CU",
                            details={"requested": amount, "available": max(0.0, available)},
                        )
                    conn.execute(
                        "UPDATE compute_budget_reservations SET amount=amount+? WHERE id=? AND scope=?",
                        (amount, reservation_id, self.scope),
                    )
                reservation = self._reservations.get(reservation_id)
                if reservation is None:
                    reservation = BudgetReservation(
                        reservation_id,
                        float(row["amount"]),
                        bool(row["critical"]),
                        consumed=float(row["consumed"]),
                    )
                    self._reservations[reservation_id] = reservation
                reservation.amount += amount
                return reservation
            reservation = self._get_active(reservation_id)
            available = self.total - self._normal_reserved - self._critical_reserved - self._consumed
            if not reservation.critical:
                available -= self.critical_reserve
            if amount > available + 1e-9:
                raise ComputeBudgetExceeded(
                    f"insufficient compute budget for escalation of {amount:.3f} NF_CU",
                    details={"requested": amount, "available": max(0.0, available)},
                )
            reservation.amount += amount
            if reservation.critical:
                self._critical_reserved += amount
            else:
                self._normal_reserved += amount
            return reservation

    def release(self, reservation_id: str) -> None:
        with self._lock:
            if self.db is not None:
                with self.db.transaction() as conn:
                    row = conn.execute(
                        "SELECT amount, critical, consumed, status FROM compute_budget_reservations WHERE id=? AND scope=?",
                        (reservation_id, self.scope),
                    ).fetchone()
                    if row is None or row["status"] != "reserved":
                        raise KeyError(f"budget reservation not active: {reservation_id}")
                    conn.execute(
                        """UPDATE compute_budget_reservations
                           SET status='released', released_at=CURRENT_TIMESTAMP
                           WHERE id=? AND scope=? AND status='reserved'""",
                        (reservation_id, self.scope),
                    )
                reservation = self._reservations.get(reservation_id)
                if reservation is None:
                    reservation = BudgetReservation(
                        reservation_id,
                        float(row["amount"]),
                        bool(row["critical"]),
                    )
                    self._reservations[reservation_id] = reservation
                reservation.consumed = float(row["consumed"])
                reservation.released = True
                return
            reservation = self._get_active(reservation_id)
            remaining = reservation.remaining
            if reservation.critical:
                self._critical_reserved -= remaining
            else:
                self._normal_reserved -= remaining
            reservation.released = True

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            if self.db is not None:
                account = self.db.fetchone(
                    "SELECT total, critical_reserve FROM compute_budget_accounts WHERE scope=?",
                    (self.scope,),
                ) or {"total": self.total, "critical_reserve": self.critical_reserve}
                consumed = self.db.fetchone(
                    "SELECT COALESCE(SUM(consumed), 0) AS value FROM compute_budget_reservations WHERE scope=?",
                    (self.scope,),
                ) or {"value": 0}
                reserved = self.db.fetchone(
                    """SELECT COALESCE(SUM(amount - consumed), 0) AS value
                       FROM compute_budget_reservations WHERE scope=? AND status='reserved'""",
                       (self.scope,),
                   ) or {"value": 0}
                normal_reserved = self.db.fetchone(
                    """SELECT COALESCE(SUM(amount - consumed), 0) AS value
                       FROM compute_budget_reservations
                       WHERE scope=? AND status='reserved' AND critical=0""",
                    (self.scope,),
                ) or {"value": 0}
                critical_reserved = self.db.fetchone(
                    """SELECT COALESCE(SUM(amount - consumed), 0) AS value
                       FROM compute_budget_reservations
                       WHERE scope=? AND status='reserved' AND critical=1""",
                    (self.scope,),
                ) or {"value": 0}
                total = float(account.get("total") or 0)
                critical_reserve = float(account.get("critical_reserve") or 0)
                return {
                    "total": total,
                    "criticalReserve": critical_reserve,
                    "normalReserved": float(normal_reserved.get("value") or 0),
                    "criticalReserved": float(critical_reserved.get("value") or 0),
                    "consumed": float(consumed.get("value") or 0),
                    "available": max(0.0, total - float(reserved.get("value") or 0) - float(consumed.get("value") or 0)),
                }
            return {
                "total": self.total,
                "criticalReserve": self.critical_reserve,
                "normalReserved": self._normal_reserved,
                "criticalReserved": self._critical_reserved,
                "consumed": self._consumed,
                "available": max(0.0, self.total - self._normal_reserved - self._critical_reserved - self._consumed),
            }

    def _get_active(self, reservation_id: str) -> BudgetReservation:
        reservation = self._reservations.get(reservation_id)
        if reservation is None or reservation.released:
            raise KeyError(f"budget reservation not active: {reservation_id}")
        return reservation


def _capability_dimension(task: AgentTask) -> str:
    """Select the capability dimension that best describes the task."""
    text = f"{task.task_type} {task.role}".lower()
    if "image" in text or "cover" in text:
        return "image"
    if "embed" in text:
        return "embedding"
    if any(token in text for token in ("revise", "rewrite")):
        return "revision"
    if any(token in text for token in ("review", "audit")):
        return "review"
    if any(token in text for token in ("extract", "fact", "import")):
        return "extraction"
    if any(token in text for token in ("plan", "forecast", "planning", "framework")):
        return "planning"
    if any(token in text for token in ("compose", "context")):
        return "long_context"
    if "tool" in text:
        return "tool_use"
    if any(token in text for token in ("json", "structured")):
        return "structured_output"
    if "consisten" in text:
        return "consistency"
    return "writing"


class ComputeScheduler:
    """Select a runtime/model/reasoning plan under explicit policy bounds."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        policy: ComputePolicy | None = None,
        budget: BudgetBroker | None = None,
        estimator: DifficultyRiskEstimator | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or ComputePolicy()
        self.budget = budget
        self.estimator = estimator or DifficultyRiskEstimator()

    def plan(
        self,
        task: AgentTask,
        *,
        capability_profile: TaskCapabilityProfile | None = None,
        candidates: Iterable[RegisteredCapability] | None = None,
        reserve_budget: bool = True,
        excluded_runtime_types: Iterable[str] = (),
        minimum_capability: str | int | CapabilityTier | None = None,
        minimum_reasoning: str | None = None,
        fallback_from_plan_id: str | None = None,
        fallback_reason: str | None = None,
    ) -> ComputePlan:
        assessment = TaskCapabilityProfiler.assess(
            task,
            declared_profile=capability_profile,
        )
        profile = assessment.profile
        estimate = self.estimator.estimate(profile)
        task_profile = task.profile or default_agent_task_profile(task.role, task.task_type)
        floor = max(_tier(task_profile.minimum_capability), self.policy.default_floor, CapabilityTier(estimate.required_tier))
        if minimum_capability is not None:
            floor = max(floor, _tier(minimum_capability))
        preferred = max(_tier(task_profile.preferred_capability), self.policy.default_preferred, floor)
        ceiling = min(_tier(task_profile.maximum_capability), self.policy.default_ceiling)
        if estimate.risk >= .8:
            floor = max(floor, self.policy.critical_floor)
        mutation_class = str(
            task.constraints.get("canonMutationType")
            or task.constraints.get("mutationClass")
            or ""
        ).strip().lower()
        if mutation_class in {"author_intent", "author-intent", "intent"}:
            raise CapabilityUnavailable(
                "Author Intent changes require an explicit human/workflow decision",
                details={"taskId": task.task_id, "mutationClass": mutation_class},
            )
        if mutation_class in {"structural", "world_rule", "world-rule", "world_rule_change"}:
            floor = max(floor, CapabilityTier.C4)
        elif bool(task.constraints.get("canon_write")) or mutation_class in {"normal", "canon", "canonical"}:
            # Canon authority is never allowed to inherit a cheap task's
            # default floor.  The policy may raise this floor further.
            floor = max(floor, self.policy.critical_floor)
        preferred = max(preferred, floor)
        if floor > ceiling:
            raise CapabilityUnavailable(
                f"task requires C{int(floor)} but policy ceiling is C{int(ceiling)}",
                details={"floor": f"C{int(floor)}", "ceiling": f"C{int(ceiling)}", "taskId": task.task_id},
            )

        dimension = _capability_dimension(task)
        available = tuple(candidates) if candidates is not None else self.registry.candidates(
            # The selected dimension, rather than an unrelated aggregate
            # score, is the authority for the floor/ceiling check below.
            minimum=CapabilityTier.C0, maximum=CapabilityTier.C5
        )
        requested_runtime = str(task.constraints.get("runtime_type") or "").strip()
        requested_model = str(
            task.constraints.get("model_id")
            or task.constraints.get("modelId")
            or ""
        ).strip()
        requested_provider = str(
            task.constraints.get("provider_id")
            or task.constraints.get("providerId")
            or ""
        ).strip()
        excluded = {
            str(runtime_type).strip()
            for runtime_type in excluded_runtime_types
            if str(runtime_type).strip()
        }
        eligible = [
            candidate for candidate in available
            if (not requested_runtime or candidate.descriptor.runtime_type == requested_runtime)
            and (not requested_model or candidate.descriptor.model_id == requested_model)
            and (not requested_provider or candidate.descriptor.provider_id == requested_provider)
            and candidate.descriptor.runtime_type not in excluded
            and floor <= candidate.capability_for(dimension) <= ceiling
            and candidate.descriptor.available
            and candidate.health == "ready"
        ]
        if not eligible:
            raise CapabilityUnavailable(
                f"no ready runtime/model satisfies C{int(floor)}..C{int(ceiling)}",
                details={"taskId": task.task_id, "floor": f"C{int(floor)}", "ceiling": f"C{int(ceiling)}"},
            )
        chosen = min(
            eligible,
            key=lambda item: (
                abs(int(item.capability_for(dimension)) - int(preferred)),
                abs(int(item.capability_for(dimension)) - int(floor)),
                item.cost_multiplier,
                item.descriptor.runtime_type,
                item.descriptor.model_id,
                str(item.descriptor.provider_id or ""),
            ),
        )
        selected_capability = chosen.capability_for(dimension)
        reasoning = self._select_reasoning(task_profile, chosen.descriptor.reasoning_levels)
        if minimum_reasoning is not None:
            normalized_minimum_reasoning = str(minimum_reasoning).strip().lower()
            if normalized_minimum_reasoning not in _REASONING_ORDER:
                raise ValueError(f"invalid minimum reasoning: {minimum_reasoning!r}")
            if _reasoning(reasoning) < _reasoning(normalized_minimum_reasoning):
                raise CapabilityUnavailable(
                    "no ready runtime/model satisfies the fallback reasoning floor",
                    details={
                        "taskId": task.task_id,
                        "minimumReasoning": normalized_minimum_reasoning,
                    },
                )
        context_budget = max(1024, int(max(0.0, profile.context_span) * 100_000))
        output_budget = max(512, int(max(0.0, profile.output_size) * 20_000))
        tool_budget = max(1, int(round(max(0.0, profile.tool_depth) * 20)))
        retry_budget = min(3, max(0, int(profile.prior_failures)))
        estimate_cost = round(
            chosen.cost_multiplier * (1.0 + context_budget / 100_000 + output_budget / 20_000 + tool_budget / 20), 4
        )
        critical = (
            estimate.risk >= .8
            or bool(task.constraints.get("critical", False))
            or floor >= self.policy.critical_floor
        )
        plan_id = generate_id()
        reservation_id = None
        if self.budget is not None and reserve_budget:
            try:
                reservation = self.budget.reserve(estimate_cost, critical=critical)
            except ComputeBudgetExceeded:
                if self.policy.budget_mode != "soft":
                    raise
                # Exploration mode may proceed after the durable quota is
                # exhausted, but the absence of a reservation is explicit in
                # the plan and never changes capability or Critical Floor.
                rationale = (*assessment.rationale, *estimate.rationale, "budgetSoftLimitExceeded")
            else:
                reservation_id = reservation.reservation_id
                rationale = (
                    *assessment.rationale,
                    *estimate.rationale,
                    f"budgetReservation={reservation.reservation_id}",
                )
        else:
            rationale = (*assessment.rationale, *estimate.rationale)
        if fallback_from_plan_id:
            rationale = (
                *rationale,
                f"fallbackFrom={fallback_from_plan_id}",
                f"fallbackReason={str(fallback_reason or 'runtime failure').strip()}",
            )
        return ComputePlan(
            plan_id=plan_id,
            runtime_type=chosen.descriptor.runtime_type,
            model_id=chosen.descriptor.model_id,
            reasoning=reasoning,
            capability=f"C{int(selected_capability)}",
            context_budget=context_budget,
            output_budget=output_budget,
            tool_budget=tool_budget,
            retry_budget=retry_budget,
            escalation_capability=f"C{int(ceiling)}" if floor < ceiling else None,
            maximum_escalation=f"C{int(ceiling)}",
            difficulty=estimate.difficulty,
            risk=estimate.risk,
            estimated_cost=estimate_cost,
            budget_unit=self.policy.budget_unit,
            critical_floor=critical,
            rationale=rationale,
            capability_dimension=dimension,
            budget_reservation_id=reservation_id,
            task_tier=f"T{int(estimate.required_tier)}",
            maximum_reasoning=task_profile.maximum_reasoning,
            provider_id=chosen.descriptor.provider_id,
        )

    def request_escalation(
        self,
        plan: ComputePlan,
        requested_capability: str | int | CapabilityTier,
        *,
        requested_reasoning: str | None = None,
        actor: str = "agent",
        approved: bool = False,
    ) -> ComputePlan:
        requested = _tier(requested_capability)
        current = _tier(plan.capability)
        ceiling = _tier(plan.maximum_escalation or plan.capability)
        current_reasoning = _reasoning(plan.reasoning)
        target_reasoning = str(requested_reasoning or plan.reasoning).strip().lower()
        if target_reasoning not in _REASONING_ORDER:
            raise ComputeEscalationDenied(f"unsupported requested reasoning: {requested_reasoning}")
        maximum_reasoning = _reasoning(plan.maximum_reasoning or plan.reasoning)
        requested_reasoning_level = _reasoning(target_reasoning)
        if requested_reasoning_level < current_reasoning:
            target_reasoning = plan.reasoning
            requested_reasoning_level = current_reasoning
        capability_upgrade = requested > current
        reasoning_upgrade = requested_reasoning_level > current_reasoning
        if not capability_upgrade and not reasoning_upgrade:
            return plan
        actor_key = str(actor or "").strip().lower()
        if not approved or not is_host_approval_actor(actor_key):
            raise ComputeEscalationDenied(
                "compute escalation requires explicit Host approval",
                details={
                    "actor": actor_key or None,
                    "approvalRequired": True,
                    "agentSelfElevation": False,
                },
            )
        if requested > ceiling:
            raise ComputeEscalationDenied(
                f"requested C{int(requested)} exceeds task ceiling C{int(ceiling)}"
            )
        if requested_reasoning_level > maximum_reasoning:
            raise ComputeEscalationDenied(
                f"requested reasoning {target_reasoning} exceeds task ceiling "
                f"{plan.maximum_reasoning or plan.reasoning}"
            )
        target_capability = max(current, requested)
        selected = self._escalation_candidate(
            plan,
            capability=target_capability,
            reasoning=target_reasoning,
            ceiling=ceiling,
        )
        selected_capability = selected.capability_for(plan.capability_dimension) if selected else current
        if selected_capability < target_capability:
            raise CapabilityUnavailable(
                f"no ready runtime/model satisfies escalation to C{int(target_capability)}",
                details={
                    "planId": plan.plan_id,
                    "dimension": plan.capability_dimension,
                    "requested": f"C{int(target_capability)}",
                },
            )
        effective_capability_upgrade = selected_capability > current
        base_cost = max(float(plan.estimated_cost), 1.0)
        additional_cost = base_cost * (
            0.25 * max(0, int(selected_capability) - int(current))
            + 0.10 * max(0, requested_reasoning_level - current_reasoning)
        )
        reservation_id = plan.budget_reservation_id
        budget_soft_limit = False
        if additional_cost > 0 and self.budget is not None:
            try:
                if reservation_id:
                    self.budget.extend(reservation_id, additional_cost)
                else:
                    reservation_id = self.budget.reserve(
                        additional_cost,
                        critical=plan.critical_floor,
                    ).reservation_id
            except ComputeBudgetExceeded:
                if self.policy.budget_mode != "soft":
                    raise
                budget_soft_limit = True
                if reservation_id:
                    # The original reservation cannot cover the escalated
                    # estimate.  Release it before explicitly proceeding
                    # without a reservation; otherwise a soft-limit plan
                    # would strand quota and later settlement would exceed
                    # the old reservation.
                    try:
                        self.budget.release(reservation_id)
                    except KeyError as exc:
                        # The reservation may already have been released by
                        # a concurrent settlement; preserve the soft-limit
                        # decision but keep the cleanup anomaly observable.
                        logger.debug("compute budget reservation was already released: %s", exc)
                    reservation_id = None
        rationale = list(plan.rationale)
        if capability_upgrade or effective_capability_upgrade:
            rationale.append(f"escalatedTo=C{int(selected_capability)}")
        if reasoning_upgrade:
            rationale.append(f"escalatedReasoning={target_reasoning}")
        if selected is not None and (
            selected.descriptor.runtime_type != plan.runtime_type
            or selected.descriptor.model_id != plan.model_id
            or selected.descriptor.provider_id != plan.provider_id
        ):
            rationale.append(f"selectedRuntime={selected.descriptor.runtime_type}")
            rationale.append(f"selectedModel={selected.descriptor.model_id}")
            if selected.descriptor.provider_id is not None:
                rationale.append(f"selectedProvider={selected.descriptor.provider_id}")
        rationale.append(f"escalatesFrom={plan.plan_id}")
        if budget_soft_limit:
            rationale.append("budgetSoftLimitExceeded")
        escalation_capability = f"C{int(ceiling)}" if selected_capability < ceiling else None
        return ComputePlan(
            **{
                **plan.__dict__,
                "plan_id": generate_id(),
                "runtime_type": selected.descriptor.runtime_type if selected else plan.runtime_type,
                "model_id": selected.descriptor.model_id if selected else plan.model_id,
                "provider_id": selected.descriptor.provider_id if selected else plan.provider_id,
                "capability": f"C{int(selected_capability)}",
                "reasoning": target_reasoning,
                "estimated_cost": round(float(plan.estimated_cost) + additional_cost, 4),
                "budget_reservation_id": reservation_id,
                "escalation_capability": escalation_capability,
                "rationale": tuple(rationale),
            }
        )

    def validate_escalation_request(
        self,
        plan: ComputePlan,
        requested_capability: str | int | CapabilityTier,
        *,
        requested_reasoning: str | None = None,
    ) -> dict[str, Any]:
        """Validate an escalation request without spending compute.

        A provider may ask the Host to consider an upgrade, but this method
        deliberately stops before candidate selection, budget extension, or
        plan creation.  The Host approval path calls ``request_escalation``
        later, after the request has been reviewed.
        """
        requested = _tier(requested_capability)
        current = _tier(plan.capability)
        ceiling = _tier(plan.maximum_escalation or plan.capability)
        target_reasoning = str(requested_reasoning or plan.reasoning).strip().lower()
        if target_reasoning not in _REASONING_ORDER:
            raise ComputeEscalationDenied(
                f"unsupported requested reasoning: {requested_reasoning}"
            )
        maximum_reasoning = _reasoning(plan.maximum_reasoning or plan.reasoning)
        current_reasoning = _reasoning(plan.reasoning)
        requested_reasoning_level = _reasoning(target_reasoning)
        if requested_reasoning_level < current_reasoning:
            target_reasoning = plan.reasoning
            requested_reasoning_level = current_reasoning
        if requested > ceiling:
            raise ComputeEscalationDenied(
                f"requested C{int(requested)} exceeds task ceiling C{int(ceiling)}"
            )
        if requested_reasoning_level > maximum_reasoning:
            raise ComputeEscalationDenied(
                f"requested reasoning {target_reasoning} exceeds task ceiling "
                f"{plan.maximum_reasoning or plan.reasoning}"
            )
        capability_upgrade = requested > current
        reasoning_upgrade = requested_reasoning_level > current_reasoning
        if not capability_upgrade and not reasoning_upgrade:
            raise ComputeEscalationDenied("escalation request does not increase compute")
        return {
            "currentCapability": plan.capability,
            "requestedCapability": f"C{int(requested)}",
            "maximumCapability": f"C{int(ceiling)}",
            "currentReasoning": plan.reasoning,
            "requestedReasoning": target_reasoning,
            "maximumReasoning": plan.maximum_reasoning or plan.reasoning,
            "capabilityUpgrade": capability_upgrade,
            "reasoningUpgrade": reasoning_upgrade,
        }

    def _escalation_candidate(
        self,
        plan: ComputePlan,
        *,
        capability: CapabilityTier,
        reasoning: str,
        ceiling: CapabilityTier,
    ) -> RegisteredCapability | None:
        """Choose a ready model that can actually execute an escalation.

        A ComputePlan must never claim a capability that the selected model
        does not provide.  Candidate discovery stays inside the Scheduler;
        callers only provide the plan and the approved target.
        """
        dimension = plan.capability_dimension
        target_reasoning = _reasoning(reasoning)
        candidates = [
            item for item in self.registry.candidates(
                minimum=CapabilityTier.C0,
                maximum=CapabilityTier.C5,
            )
            if capability <= item.capability_for(dimension) <= ceiling
            and any(_reasoning(level) >= target_reasoning for level in item.descriptor.reasoning_levels)
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (
                int(item.capability_for(dimension)),
                item.cost_multiplier,
                item.descriptor.runtime_type,
                item.descriptor.model_id,
                str(item.descriptor.provider_id or ""),
            ),
        )

    @staticmethod
    def _select_reasoning(profile: AgentTaskProfile, levels: Iterable[str]) -> str:
        available = tuple(levels)
        if not available:
            return profile.minimum_reasoning
        preferred = [level for level in available if _reasoning(level) >= _reasoning(profile.preferred_reasoning)]
        if preferred:
            return min(preferred, key=_reasoning)
        acceptable = [level for level in available if _reasoning(level) >= _reasoning(profile.minimum_reasoning)]
        if acceptable:
            return max(acceptable, key=_reasoning)
        raise CapabilityUnavailable("model does not satisfy reasoning floor")
