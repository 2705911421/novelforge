"""Control Plane router from AgentTask to ComputePlan and runtime adapter."""

from __future__ import annotations

from typing import Any, AsyncIterator

from src.compute.scheduler import ComputeScheduler, TaskCapabilityProfile

from .contracts import AgentTask, ComputePlan, IAgentRuntime, RuntimeCapabilities, RuntimeEvent
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
    ) -> None:
        self.scheduler = scheduler
        self.runs = runs
        self.plans = plans or ComputePlanStore(runs.db)
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
    ) -> ComputePlan:
        plan = self.scheduler.plan(task, capability_profile=capability_profile)
        self.plans.create(task.task_id, plan)
        return plan

    async def execute(
        self,
        task: AgentTask,
        *,
        capability_profile: TaskCapabilityProfile | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        plan = self.plan(task, capability_profile=capability_profile)
        runtime = self.get(plan.runtime_type)
        async for event in runtime.execute(task, plan):
            if event.agent_run_id:
                self.runs.append_event(event.agent_run_id, task, event)
            yield event

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
