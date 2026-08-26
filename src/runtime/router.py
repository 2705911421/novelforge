"""Control Plane router from AgentTask to ComputePlan and runtime adapter."""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from src.compute.scheduler import ComputeScheduler, TaskCapabilityProfile

from .contracts import AgentRunStatus, AgentTask, ComputePlan, IAgentRuntime, RuntimeCapabilities, RuntimeEvent
from .errors import RuntimeUnavailable
from .persistence import AgentRunStore, ComputePlanStore


class RuntimeRouter:
    """Own runtime selection and event persistence without owning Canon."""

    def __init__(
        self,
        scheduler: ComputeScheduler,
        *,
        runs: AgentRunStore,
        plans: ComputePlanStore | None = None,
        event_bus: Any | None = None,
        runtime_readiness: Callable[[str], Any] | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.runs = runs
        self.plans = plans or ComputePlanStore(runs.db)
        self.event_bus = event_bus
        self.runtime_readiness = runtime_readiness
        self._runtimes: dict[str, IAgentRuntime] = {}

    def register(self, runtime_type: str, runtime: IAgentRuntime) -> None:
        if not runtime_type.strip():
            raise ValueError("runtime_type is required")
        if runtime_type in self._runtimes:
            raise ValueError(f"runtime already registered: {runtime_type}")
        self._runtimes[runtime_type] = runtime

    def replace(self, runtime_type: str, runtime: IAgentRuntime) -> None:
        self._runtimes[runtime_type] = runtime

    def get(self, runtime_type: str) -> IAgentRuntime:
        try:
            return self._runtimes[runtime_type]
        except KeyError:
            raise RuntimeUnavailable(f"runtime adapter is not registered: {runtime_type}") from None

    def plan(
        self,
        task: AgentTask,
        *,
        capability_profile: TaskCapabilityProfile | None = None,
        reserve_budget: bool = False,
    ) -> ComputePlan:
        plan = self.scheduler.plan(
            task,
            capability_profile=capability_profile,
            reserve_budget=reserve_budget,
        )
        try:
            self._require_runtime_ready(plan.runtime_type)
            self.plans.create(task.task_id, plan)
        except Exception:
            self._release_budget(plan)
            raise
        return plan

    def _require_runtime_ready(self, runtime_type: str) -> None:
        if self.runtime_readiness is None:
            return
        try:
            result = self.runtime_readiness(runtime_type)
        except RuntimeUnavailable:
            raise
        except Exception as exc:
            raise RuntimeUnavailable(
                f"runtime readiness check failed: {runtime_type}: {exc}"
            ) from exc
        if result is False:
            raise RuntimeUnavailable(f"runtime is not ready: {runtime_type}")

    async def execute(
        self,
        task: AgentTask,
        *,
        capability_profile: TaskCapabilityProfile | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        plan = self.plan(task, capability_profile=capability_profile, reserve_budget=True)
        runtime = None
        observed_cost: float | None = None
        completed = False
        try:
            runtime = self.get(plan.runtime_type)
            async for event in runtime.execute(task, plan):
                if event.agent_run_id:
                    self.runs.append_event(event.agent_run_id, task, event)
                if self.event_bus is not None:
                    self.event_bus.publish(
                        "runtime.event",
                        {
                            "taskId": task.task_id,
                            "agentRunId": event.agent_run_id,
                            "runtimeType": event.runtime_type,
                            "eventType": event.event_type,
                            "payload": dict(event.payload),
                        },
                    )
                event_cost = self._compute_units(event.payload)
                if event_cost is not None:
                    observed_cost = event_cost
                yield event
            completed = True
        finally:
            # A consumer that stops an async generator early must also release
            # its reservation.  Successful executions without provider usage
            # telemetry consume the planned estimate, while failed/aborted
            # executions release the unused estimate.
            if plan.budget_reservation_id:
                amount = observed_cost if observed_cost is not None else (
                    plan.estimated_cost if completed else 0.0
                )
                self._settle_budget(plan, amount)

    async def initialize_all(self) -> dict[str, RuntimeCapabilities]:
        capabilities: dict[str, RuntimeCapabilities] = {}
        for runtime_type, runtime in self._runtimes.items():
            capabilities[runtime_type] = await runtime.initialize()
        return capabilities

    async def capability_snapshot(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for runtime_type, runtime in self._runtimes.items():
            capabilities = await runtime.get_capabilities()
            result.append(capabilities.to_dict())
        return result

    async def shutdown(self) -> None:
        for runtime in self._runtimes.values():
            await runtime.shutdown()

    async def cancel(self, durable_task_id: str) -> list[str]:
        """Forward a durable task cancellation to every active runtime run.

        The durable task transition remains authoritative.  This method only
        sends best-effort provider interrupts, discovered from persisted
        AgentRuns, so a process restart does not depend on an in-memory run
        registry.
        """
        active = {
            AgentRunStatus.CREATED.value,
            AgentRunStatus.RUNNING.value,
            AgentRunStatus.PAUSED.value,
        }
        forwarded: list[str] = []
        seen_runtime_types: set[str] = set()
        for run in self.runs.list_for_task(durable_task_id):
            runtime_type = str(run.get("runtime_type") or "")
            if not runtime_type or run.get("status") not in active or runtime_type in seen_runtime_types:
                continue
            seen_runtime_types.add(runtime_type)
            runtime = self._runtimes.get(runtime_type)
            if runtime is None:
                # The durable task is already in its cancelling state.  A
                # runtime removed during recovery cannot be interrupted in
                # memory, but it must not block the durable cancellation.
                continue
            try:
                await runtime.cancel(durable_task_id)
            except Exception as exc:
                if self.event_bus is not None:
                    self.event_bus.publish(
                        "runtime.cancel.failed",
                        {
                            "taskId": durable_task_id,
                            "runtimeType": runtime_type,
                            "error": str(exc),
                        },
                    )
                continue
            forwarded.append(runtime_type)
        return forwarded

    def _settle_budget(self, plan: ComputePlan, amount: float) -> None:
        if not plan.budget_reservation_id or self.scheduler.budget is None:
            return
        budget = self.scheduler.budget
        try:
            budget.consume(plan.budget_reservation_id, min(max(0.0, amount), plan.estimated_cost))
        except KeyError:
            return
        finally:
            try:
                budget.release(plan.budget_reservation_id)
            except KeyError:
                pass

    def _release_budget(self, plan: ComputePlan) -> None:
        if not plan.budget_reservation_id or self.scheduler.budget is None:
            return
        try:
            self.scheduler.budget.release(plan.budget_reservation_id)
        except KeyError:
            pass

    @staticmethod
    def _compute_units(payload: Any) -> float | None:
        if not isinstance(payload, dict):
            return None
        usage = payload.get("usage")
        candidates = [payload]
        if isinstance(usage, dict):
            candidates.insert(0, usage)
        for item in candidates:
            for key in ("computeUnits", "compute_units", "cost", "totalCost"):
                value = item.get(key)
                if isinstance(value, (int, float)):
                    return max(0.0, float(value))
        return None
