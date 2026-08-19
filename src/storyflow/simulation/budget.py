"""Durable simulation token/cost accounting and budget gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import SimulationRun
from .repository import SimulationRepository


class SimulationBudgetExceeded(ValueError):
    """A normal author-controlled pause, not a failed simulation run."""

    code = "SIMULATION_BUDGET_EXCEEDED"

    def __init__(self, message: str, *, snapshot: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.snapshot = dict(snapshot)


@dataclass(frozen=True, slots=True)
class SimulationBudget:
    max_generation_calls: int | None = None
    max_tokens: int | None = None
    max_cost: float | None = None
    estimated_tokens_per_call: int = 1000
    cost_per_1k_tokens: float = 0.0

    @classmethod
    def from_run(cls, run: SimulationRun) -> "SimulationBudget":
        config = run.configuration if isinstance(run.configuration, Mapping) else {}
        raw = config.get("budget") or config.get("costControl") or config.get("cost_control") or {}
        if not isinstance(raw, Mapping):
            raw = {}

        def optional_int(*keys: str) -> int | None:
            value = next((raw.get(key) for key in keys if raw.get(key) is not None), None)
            if value is None:
                return None
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"simulation budget {keys[0]} must be an integer") from exc
            if number < 0:
                raise ValueError(f"simulation budget {keys[0]} must be non-negative")
            return number

        def number(*keys: str, default: float) -> float:
            value: Any = next((raw.get(key) for key in keys if raw.get(key) is not None), default)
            try:
                number_value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"simulation budget {keys[0]} must be numeric") from exc
            if number_value < 0:
                raise ValueError(f"simulation budget {keys[0]} must be non-negative")
            return number_value

        estimated = int(number("estimatedTokensPerCall", "estimated_tokens_per_call", default=1000))
        if estimated < 1:
            raise ValueError("simulation budget estimatedTokensPerCall must be positive")
        return cls(
            max_generation_calls=optional_int("maxGenerationCalls", "max_generation_calls"),
            max_tokens=optional_int("maxTokens", "max_tokens"),
            max_cost=number("maxCost", "max_cost", default=-1.0) if any(
                raw.get(key) is not None for key in ("maxCost", "max_cost")
            ) else None,
            estimated_tokens_per_call=estimated,
            cost_per_1k_tokens=number("costPer1KTokens", "cost_per_1k_tokens", default=0.0),
        )

    def estimate(self, calls: int) -> dict[str, Any]:
        calls = max(0, int(calls))
        tokens = calls * self.estimated_tokens_per_call
        cost = tokens / 1000.0 * self.cost_per_1k_tokens
        return {
            "calls": calls,
            "tokens": tokens,
            "cost": round(cost, 8),
            "estimatedTokensPerCall": self.estimated_tokens_per_call,
            "costPer1KTokens": self.cost_per_1k_tokens,
        }


class SimulationBudgetController:
    """Check a run budget before/after provider calls and expose evidence."""

    def __init__(self, repository: SimulationRepository, run: SimulationRun, *, round_number: int,
                 task_id: str | None = None) -> None:
        self.repository = repository
        self.run = run
        self.round_number = round_number
        self.task_id = task_id
        self.budget = SimulationBudget.from_run(run)
        self._sync()

    def _sync(self) -> None:
        self.repository.sync_generation_costs(self.run.id, self.budget.cost_per_1k_tokens)

    def usage(self) -> dict[str, Any]:
        self._sync()
        row = self.repository.database.fetchone(
            """SELECT COUNT(*) AS calls, COALESCE(SUM(total_tokens), 0) AS tokens,
                      COALESCE(SUM(actual_cost), 0) AS cost
                 FROM simulation_cost_ledger WHERE simulation_run_id=?""",
            (self.run.id,),
        )
        return {
            "calls": int(row["calls"] if row else 0),
            "tokens": int(row["tokens"] if row else 0),
            "cost": round(float(row["cost"] if row else 0.0), 8),
        }

    def snapshot(self, *, estimated_calls: int = 0) -> dict[str, Any]:
        usage = self.usage()
        estimate = self.budget.estimate(estimated_calls)
        limits = {
            "maxGenerationCalls": self.budget.max_generation_calls,
            "maxTokens": self.budget.max_tokens,
            "maxCost": self.budget.max_cost,
        }
        remaining = {
            "generationCalls": (None if self.budget.max_generation_calls is None else
                                 max(0, self.budget.max_generation_calls - usage["calls"])),
            "tokens": (None if self.budget.max_tokens is None else max(0, self.budget.max_tokens - usage["tokens"])),
            "cost": (None if self.budget.max_cost is None else max(0.0, round(self.budget.max_cost - usage["cost"], 8))),
        }
        return {
            "limits": limits,
            "usage": usage,
            "estimate": estimate,
            "remaining": remaining,
            "budgetConfigured": any(value is not None for value in limits.values()),
            "status": "within_budget" if not self.exceeded(usage) else "exceeded",
        }

    def ensure_can_schedule(self, calls: int) -> None:
        usage = self.usage()
        estimate = self.budget.estimate(calls)
        if self.budget.max_generation_calls is not None and usage["calls"] + calls > self.budget.max_generation_calls:
            self._raise("max_generation_calls", usage, estimate)
        if self.budget.max_tokens is not None and usage["tokens"] + estimate["tokens"] > self.budget.max_tokens:
            self._raise("max_tokens", usage, estimate)
        if self.budget.max_cost is not None and usage["cost"] + estimate["cost"] > self.budget.max_cost:
            self._raise("max_cost", usage, estimate)

    def ensure_within_budget(self) -> None:
        usage = self.usage()
        if self.exceeded(usage):
            self._raise("actual_usage", usage, self.budget.estimate(0))

    def exceeded(self, usage: Mapping[str, Any]) -> bool:
        return (
            (self.budget.max_generation_calls is not None and int(usage.get("calls", 0)) > self.budget.max_generation_calls)
            or (self.budget.max_tokens is not None and int(usage.get("tokens", 0)) > self.budget.max_tokens)
            or (self.budget.max_cost is not None and float(usage.get("cost", 0.0)) > self.budget.max_cost)
        )

    def _raise(self, dimension: str, usage: Mapping[str, Any], estimate: Mapping[str, Any]) -> None:
        raise SimulationBudgetExceeded(
            f"simulation budget exceeded: {dimension}",
            snapshot={"dimension": dimension, "usage": dict(usage), "estimate": dict(estimate),
                      "budget": self.snapshot(estimated_calls=0)},
        )
