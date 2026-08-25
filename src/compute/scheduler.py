"""Deterministic capability and budget selection for AgentTasks.

Difficulty answers "how hard is this work?" while risk answers "how costly is
an incorrect or unauditable result?".  They are deliberately estimated as
separate signals before the scheduler chooses a capability tier.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import IntEnum
from math import ceil
from typing import Any, Iterable, Mapping

from src.core.database import Database, generate_id
from src.runtime.contracts import AgentTask, AgentTaskProfile, ComputePlan, ModelDescriptor
from src.runtime.errors import (
    CapabilityUnavailable,
    ComputeBudgetExceeded,
    ComputeEscalationDenied,
)


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


def _bounded(value: float, *, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
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
        required = max(1, ceil(difficulty * 5), ceil(risk * 5))
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


class CapabilityRegistry:
    """Registry of discoverable runtime/model capabilities."""

    def __init__(self) -> None:
        self._models: dict[tuple[str, str], RegisteredCapability] = {}
        self._lock = threading.RLock()

    def register_model(
        self,
        descriptor: ModelDescriptor,
        *,
        capability: str | int | CapabilityTier,
        cost_multiplier: float = 1.0,
        health: str = "ready",
        tags: Iterable[str] = (),
    ) -> RegisteredCapability:
        registered = RegisteredCapability(
            descriptor=descriptor,
            capability=_tier(capability),
            cost_multiplier=max(0.0, float(cost_multiplier)),
            health=health,
            tags=tuple(tags),
        )
        with self._lock:
            self._models[(descriptor.runtime_type, descriptor.model_id)] = registered
        return registered

    def remove_model(self, runtime_type: str, model_id: str) -> None:
        with self._lock:
            self._models.pop((runtime_type, model_id), None)

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
            }
            for item in self.candidates()
        ]


@dataclass(frozen=True)
class ComputePolicy:
    default_floor: CapabilityTier = CapabilityTier.C1
    default_preferred: CapabilityTier = CapabilityTier.C2
    default_ceiling: CapabilityTier = CapabilityTier.C4
    critical_floor: CapabilityTier = CapabilityTier.C3
    allow_agent_escalation: bool = False
    budget_unit: str = "NF_CU"


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
            reservation = self._get_active(reservation_id)
            amount = max(0.0, float(amount))
            if amount > reservation.remaining + 1e-9:
                raise ComputeBudgetExceeded("consumption exceeds reservation")
            if self.db is not None:
                with self.db.transaction() as conn:
                    row = conn.execute(
                        "SELECT amount, consumed, status FROM compute_budget_reservations WHERE id=? AND scope=?",
                        (reservation_id, self.scope),
                    ).fetchone()
                    if row is None or row["status"] != "reserved":
                        raise KeyError(f"budget reservation not active: {reservation_id}")
                    if amount > float(row["amount"] - row["consumed"]) + 1e-9:
                        raise ComputeBudgetExceeded("consumption exceeds reservation")
                    conn.execute(
                        "UPDATE compute_budget_reservations SET consumed=consumed+? WHERE id=?",
                        (amount, reservation_id),
                    )
                reservation.consumed += amount
                return reservation.consumed
            reservation.consumed += amount
            self._consumed += amount
            if reservation.critical:
                self._critical_reserved -= amount
            else:
                self._normal_reserved -= amount
            return reservation.consumed

    def release(self, reservation_id: str) -> None:
        with self._lock:
            reservation = self._get_active(reservation_id)
            if self.db is not None:
                self.db.execute(
                    "UPDATE compute_budget_reservations SET status='released', released_at=CURRENT_TIMESTAMP WHERE id=? AND scope=?",
                    (reservation_id, self.scope),
                )
                reservation.released = True
                return
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
                total = float(account.get("total") or 0)
                critical_reserve = float(account.get("critical_reserve") or 0)
                return {
                    "total": total,
                    "criticalReserve": critical_reserve,
                    "normalReserved": float(reserved.get("value") or 0),
                    "criticalReserved": 0.0,
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
    ) -> ComputePlan:
        profile = capability_profile or TaskCapabilityProfile.from_mapping(
            task.input_payload.get("capabilityProfile")
            if isinstance(task.input_payload.get("capabilityProfile"), Mapping) else task.constraints
        )
        estimate = self.estimator.estimate(profile)
        task_profile = task.profile or AgentTaskProfile(task.role, task.task_type)
        floor = max(_tier(task_profile.minimum_capability), self.policy.default_floor, CapabilityTier(estimate.required_tier))
        preferred = max(_tier(task_profile.preferred_capability), self.policy.default_preferred, floor)
        ceiling = min(_tier(task_profile.maximum_capability), self.policy.default_ceiling)
        if estimate.risk >= .8:
            floor = max(floor, self.policy.critical_floor)
        if floor > ceiling:
            raise CapabilityUnavailable(
                f"task requires C{int(floor)} but policy ceiling is C{int(ceiling)}",
                details={"floor": f"C{int(floor)}", "ceiling": f"C{int(ceiling)}", "taskId": task.task_id},
            )

        available = tuple(candidates) if candidates is not None else self.registry.candidates(minimum=floor, maximum=ceiling)
        eligible = [
            candidate for candidate in available
            if floor <= candidate.capability <= ceiling
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
                abs(int(item.capability) - int(preferred)),
                abs(int(item.capability) - int(floor)),
                item.cost_multiplier,
                item.descriptor.runtime_type,
                item.descriptor.model_id,
            ),
        )
        reasoning = self._select_reasoning(task_profile, chosen.descriptor.reasoning_levels)
        context_budget = max(1024, int(max(0.0, profile.context_span) * 100_000))
        output_budget = max(512, int(max(0.0, profile.output_size) * 20_000))
        tool_budget = max(1, int(round(max(0.0, profile.tool_depth) * 20)))
        retry_budget = min(3, max(0, int(profile.prior_failures)))
        estimate_cost = round(
            chosen.cost_multiplier * (1.0 + context_budget / 100_000 + output_budget / 20_000 + tool_budget / 20), 4
        )
        critical = estimate.risk >= .8 or bool(task.constraints.get("critical", False))
        if self.budget is not None:
            reservation = self.budget.reserve(estimate_cost, critical=critical)
            rationale = (*estimate.rationale, f"budgetReservation={reservation.reservation_id}")
        else:
            rationale = estimate.rationale
        return ComputePlan(
            plan_id=generate_id(),
            runtime_type=chosen.descriptor.runtime_type,
            model_id=chosen.descriptor.model_id,
            reasoning=reasoning,
            capability=f"C{int(chosen.capability)}",
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
        )

    def request_escalation(
        self,
        plan: ComputePlan,
        requested_capability: str | int | CapabilityTier,
        *,
        actor: str = "agent",
        approved: bool = False,
    ) -> ComputePlan:
        requested = _tier(requested_capability)
        current = _tier(plan.capability)
        ceiling = _tier(plan.maximum_escalation or plan.capability)
        if requested <= current:
            return plan
        if actor == "agent" and (not approved or not self.policy.allow_agent_escalation):
            raise ComputeEscalationDenied("agent cannot self-elevate compute capability")
        if requested > ceiling:
            raise ComputeEscalationDenied(
                f"requested C{int(requested)} exceeds task ceiling C{int(ceiling)}"
            )
        return ComputePlan(
            **{
                **plan.__dict__,
                "capability": f"C{int(requested)}",
                "rationale": (*plan.rationale, f"escalatedTo=C{int(requested)}"),
            }
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
