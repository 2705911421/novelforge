"""Control Plane router from AgentTask to ComputePlan and runtime adapter."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, AsyncIterator, Callable

from src.compute.scheduler import ComputeScheduler, TaskCapabilityProfile

from .contracts import AgentRunStatus, AgentTask, ComputePlan, IAgentRuntime, RuntimeCapabilities, RuntimeEvent
from .errors import AgentRuntimeError, RuntimeUnavailable, TaskInterrupted
from .persistence import AgentRunStore, ComputePlanStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeFallbackPolicy:
    """Explicit same-quality fallback policy for transient runtime failures.

    Fallback is deliberately conservative: the Router may try another
    registered runtime only before an attempt emits content or invokes a
    tool, and the replacement plan must preserve the failed plan's capability
    and reasoning floors by default.  A caller can disable the policy or set
    ``max_fallbacks`` to zero without changing the primary plan.
    """

    enabled: bool = True
    max_fallbacks: int = 1
    preserve_capability_floor: bool = True
    preserve_reasoning_floor: bool = True
    retryable_codes: tuple[str, ...] = (
        "RUNTIME_UNAVAILABLE",
        "RUNTIME_CRASHED",
        "RUNTIME_EXECUTION_FAILED",
        "RUNTIME_PROTOCOL_ERROR",
    )
    excluded_runtime_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if int(self.max_fallbacks) < 0:
            raise ValueError("max_fallbacks cannot be negative")
        object.__setattr__(self, "max_fallbacks", int(self.max_fallbacks))
        object.__setattr__(
            self,
            "retryable_codes",
            tuple(dict.fromkeys(str(code).strip().upper() for code in self.retryable_codes if str(code).strip())),
        )
        object.__setattr__(
            self,
            "excluded_runtime_types",
            tuple(dict.fromkeys(str(runtime_type).strip() for runtime_type in self.excluded_runtime_types if str(runtime_type).strip())),
        )

    def allows_error(self, error: AgentRuntimeError) -> bool:
        return bool(self.enabled and str(getattr(error, "code", "")).upper() in self.retryable_codes)


class RuntimeRouter:
    """Own runtime selection and event persistence without owning Canon."""

    _SUCCESS_EVENTS = {"turn.completed", "turn.complete"}
    _FAILURE_EVENTS = {"turn.failed", "turn.cancelled", "error"}

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
        excluded_runtime_types: tuple[str, ...] | set[str] = (),
        minimum_capability: str | int | None = None,
        minimum_reasoning: str | None = None,
        fallback_from_plan_id: str | None = None,
        fallback_reason: str | None = None,
    ) -> ComputePlan:
        plan = self.scheduler.plan(
            task,
            capability_profile=capability_profile,
            reserve_budget=reserve_budget,
            excluded_runtime_types=excluded_runtime_types,
            minimum_capability=minimum_capability,
            minimum_reasoning=minimum_reasoning,
            fallback_from_plan_id=fallback_from_plan_id,
            fallback_reason=fallback_reason,
        )
        try:
            self._require_runtime_ready(plan.runtime_type)
            # A capability catalog entry is not executable by itself.  Keep
            # the durable plan ledger free of selections that have no
            # Host-owned runtime adapter behind them.
            self.get(plan.runtime_type)
            self.plans.create(task.task_id, plan)
        except Exception:
            self._release_budget(plan)
            raise
        return plan

    async def execute_with_fallback(
        self,
        task: AgentTask,
        *,
        capability_profile: TaskCapabilityProfile | None = None,
        compute_plan: ComputePlan | None = None,
        fallback_policy: RuntimeFallbackPolicy | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        """Execute with an explicit, non-downgrading Runtime fallback.

        Each replacement is a new persisted ComputePlan and AgentRun.  A
        failed attempt that has emitted anything other than lifecycle/error
        notifications is never replayed automatically, because doing so could
        duplicate a proposal or another tool effect.  If no equivalent
        capability is ready, the original runtime error is preserved.
        """
        policy = fallback_policy or RuntimeFallbackPolicy()
        plan = compute_plan
        attempted_runtime_types = set(policy.excluded_runtime_types)
        fallback_count = 0
        while True:
            if plan is None:
                plan = self.plan(
                    task,
                    capability_profile=capability_profile,
                    reserve_budget=True,
                )
            attempted_runtime_types.add(plan.runtime_type)
            observed: list[RuntimeEvent] = []
            try:
                async for event in self.execute(task, compute_plan=plan):
                    observed.append(event)
                    yield event
                return
            except TaskInterrupted:
                raise
            except AgentRuntimeError as exc:
                if (
                    fallback_count >= policy.max_fallbacks
                    or not policy.allows_error(exc)
                    or any(self._fallback_event_has_effect(event) for event in observed)
                ):
                    raise
                try:
                    replacement = self.plan(
                        task,
                        capability_profile=capability_profile,
                        reserve_budget=bool(plan.budget_reservation_id),
                        excluded_runtime_types=attempted_runtime_types,
                        minimum_capability=plan.capability if policy.preserve_capability_floor else None,
                        minimum_reasoning=plan.reasoning if policy.preserve_reasoning_floor else None,
                        fallback_from_plan_id=plan.plan_id,
                        fallback_reason=str(getattr(exc, "code", None) or "RUNTIME_ERROR"),
                    )
                except AgentRuntimeError as fallback_error:
                    if self.event_bus is not None:
                        self.event_bus.publish(
                            "runtime.fallback.unavailable",
                            {
                                "taskId": task.task_id,
                                "failedPlanId": plan.plan_id,
                                "failedRuntimeType": plan.runtime_type,
                                "errorCode": str(getattr(exc, "code", None) or "RUNTIME_ERROR"),
                                "fallbackErrorCode": str(getattr(fallback_error, "code", None) or "RUNTIME_ERROR"),
                                "preservedCapability": policy.preserve_capability_floor,
                                "preservedReasoning": policy.preserve_reasoning_floor,
                            },
                        )
                    raise exc from fallback_error
                attempted_runtime_types.add(replacement.runtime_type)
                fallback_count += 1
                if self.event_bus is not None:
                    self.event_bus.publish(
                        "runtime.fallback.selected",
                        {
                            "taskId": task.task_id,
                            "failedPlanId": plan.plan_id,
                            "failedRuntimeType": plan.runtime_type,
                            "replacementPlanId": replacement.plan_id,
                            "replacementRuntimeType": replacement.runtime_type,
                            "replacementCapability": replacement.capability,
                            "replacementReasoning": replacement.reasoning,
                            "errorCode": str(getattr(exc, "code", None) or "RUNTIME_ERROR"),
                            "preservedCapability": policy.preserve_capability_floor,
                            "preservedReasoning": policy.preserve_reasoning_floor,
                        },
                    )
                plan = replacement

    @staticmethod
    def _fallback_event_has_effect(event: RuntimeEvent) -> bool:
        """Return true once an attempt leaves its replay-safe lifecycle phase."""
        event_type = event.event_type.replace("/", ".").strip().lower()
        return event_type not in {
            "recovery.started",
            "thread.started",
            "turn.started",
            "turn.failed",
            "error",
        }

    def persisted_plan(
        self,
        task: AgentTask,
        *,
        plan_id: str | None = None,
    ) -> ComputePlan:
        """Load one immutable plan owned by the supplied AgentTask."""
        record = self.plans.get(plan_id) if plan_id else self.plans.latest(task.task_id)
        if record is None:
            raise KeyError(f"compute plan not found for AgentTask: {task.task_id}")
        owner = str(record.get("agent_task_id") or record.get("agentTaskId") or "")
        if owner != task.task_id:
            raise ValueError("compute plan is not owned by the supplied AgentTask")
        raw_plan = record.get("plan")
        if not isinstance(raw_plan, dict):
            raise ValueError("persisted compute plan is invalid")
        return ComputePlan.from_mapping(raw_plan)

    def validate_escalation_request(
        self,
        task: AgentTask,
        *,
        plan_id: str | None = None,
        requested_capability: str | int,
        requested_reasoning: str | None = None,
    ) -> dict[str, Any]:
        """Validate a request while keeping plan and budget state unchanged."""
        plan = self.persisted_plan(task, plan_id=plan_id)
        validation = self.scheduler.validate_escalation_request(
            plan,
            requested_capability,
            requested_reasoning=requested_reasoning,
        )
        return {"plan": plan.to_dict(), **validation}

    def request_escalation(
        self,
        task: AgentTask,
        *,
        plan_id: str | None = None,
        requested_capability: str | int,
        requested_reasoning: str | None = None,
        actor: str = "agent",
        approved: bool = False,
    ) -> ComputePlan:
        """Apply an approved Compute escalation at the Host seam.

        The Scheduler owns policy, candidate selection, and budget extension;
        this Router owns the durable plan history and runtime readiness.  The
        returned plan is a new append-only record, so an existing plan and its
        audit trail are never overwritten.  Callers that want to execute this
        exact plan can pass it back to :meth:`execute`.
        """
        current = self.persisted_plan(task, plan_id=plan_id)
        upgraded = self.scheduler.request_escalation(
            current,
            requested_capability,
            requested_reasoning=requested_reasoning,
            actor=actor,
            approved=approved,
        )
        self._require_runtime_ready(upgraded.runtime_type)
        self.get(upgraded.runtime_type)
        self.plans.create(task.task_id, upgraded)
        if self.event_bus is not None:
            self.event_bus.publish(
                "compute.escalated",
                {
                    "taskId": task.task_id,
                    "previousPlanId": current.plan_id,
                    "plan": upgraded.to_dict(),
                    "actor": actor,
                },
            )
        return upgraded

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
        compute_plan: ComputePlan | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        if compute_plan is None:
            plan = self.plan(task, capability_profile=capability_profile, reserve_budget=True)
        else:
            # An injected plan is only an execution selector when it is an
            # immutable record previously issued by this Router for this
            # AgentTask.  TaskOrchestrator performs the same check at its
            # durable-task boundary, but keeping it here closes the lower
            # level entrypoint as well: callers cannot smuggle an unpersisted
            # model/capability selection into a runtime adapter.
            # Rehydrate from storage rather than trusting a caller's mutated
            # in-memory copy with the same plan id.
            try:
                plan = self.persisted_plan(task, plan_id=compute_plan.plan_id)
            except KeyError as exc:
                raise ValueError(
                    "compute plan is not persisted by the RuntimeRouter"
                ) from exc
            self._require_runtime_ready(plan.runtime_type)
        runtime = None
        observed_cost: float | None = None
        completed = False
        terminal_event: RuntimeEvent | None = None
        try:
            runtime = self.get(plan.runtime_type)
            async for event in runtime.execute(task, plan):
                if not isinstance(event, RuntimeEvent):
                    self._fail_active_runs(
                        self._durable_task_id(task.task_id),
                        code="RUNTIME_PROTOCOL_ERROR",
                        detail="runtime emitted a non-RuntimeEvent value",
                    )
                    raise AgentRuntimeError(
                        "runtime emitted a non-RuntimeEvent value",
                        code="RUNTIME_PROTOCOL_ERROR",
                        retryable=True,
                    )
                if event.runtime_type != plan.runtime_type:
                    self._fail_active_runs(
                        self._durable_task_id(task.task_id),
                        code="RUNTIME_PROTOCOL_ERROR",
                        detail="runtime event type does not match the ComputePlan",
                    )
                    raise AgentRuntimeError(
                        "runtime event type does not match the ComputePlan",
                        code="RUNTIME_PROTOCOL_ERROR",
                        retryable=True,
                    )
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
                normalized_event_type = event.event_type.replace("/", ".")
                if normalized_event_type in self._SUCCESS_EVENTS | self._FAILURE_EVENTS:
                    terminal_event = event
                if normalized_event_type in self._SUCCESS_EVENTS and not event.agent_run_id:
                    # A successful terminal event is an artifact-bearing
                    # result.  It must be tied to the AgentRun that owns the
                    # durable task before it can leave the Runtime boundary;
                    # otherwise a direct Router consumer could accept an
                    # orphan success without the orchestrator's later guard.
                    self._fail_active_runs(
                        self._durable_task_id(task.task_id),
                        code="RUNTIME_PROTOCOL_ERROR",
                        detail="runtime emitted success without an AgentRun",
                    )
                    raise AgentRuntimeError(
                        "runtime emitted success without an AgentRun",
                        code="RUNTIME_PROTOCOL_ERROR",
                        retryable=True,
                    )
                yield event
            if terminal_event is None:
                self._fail_active_runs(
                    self._durable_task_id(task.task_id),
                    code="RUNTIME_PROTOCOL_ERROR",
                    detail="runtime ended without a terminal turn event",
                )
                raise AgentRuntimeError(
                    "runtime ended without a terminal turn event",
                    code="RUNTIME_PROTOCOL_ERROR",
                    retryable=True,
                )
            normalized_terminal = terminal_event.event_type.replace("/", ".")
            if normalized_terminal == "turn.cancelled":
                raise TaskInterrupted("runtime reported a cancelled turn")
            if normalized_terminal in {"turn.failed", "error"}:
                self._fail_active_runs(
                    self._durable_task_id(task.task_id),
                    code="RUNTIME_EXECUTION_FAILED",
                    detail=str(terminal_event.payload.get("detail") or terminal_event.payload.get("error") or normalized_terminal),
                )
                raise AgentRuntimeError(
                    "runtime reported a failed turn",
                    code="RUNTIME_EXECUTION_FAILED",
                    retryable=True,
                    details=dict(terminal_event.payload),
                )
            if normalized_terminal in self._SUCCESS_EVENTS and not terminal_event.agent_run_id:
                # This is also guarded before yielding the event above.  Keep
                # the post-loop invariant explicit so future terminal-event
                # handling cannot accidentally re-open the orphan-success
                # path.
                self._fail_active_runs(
                    self._durable_task_id(task.task_id),
                    code="RUNTIME_PROTOCOL_ERROR",
                    detail="runtime emitted success without an AgentRun",
                )
                raise AgentRuntimeError(
                    "runtime emitted success without an AgentRun",
                    code="RUNTIME_PROTOCOL_ERROR",
                    retryable=True,
                )
            if terminal_event.agent_run_id:
                run = self.runs.get(terminal_event.agent_run_id)
                durable_task_id = self._durable_task_id(task.task_id)
                if (
                    run is None
                    or str(run.get("task_id") or "") != durable_task_id
                    or run.get("status") != AgentRunStatus.SUCCEEDED.value
                ):
                    self._fail_active_runs(
                        durable_task_id,
                        code="RUNTIME_PROTOCOL_ERROR",
                        detail="runtime emitted success without a succeeded AgentRun",
                    )
                    raise AgentRuntimeError(
                        "runtime emitted success without a succeeded AgentRun",
                        code="RUNTIME_PROTOCOL_ERROR",
                        retryable=True,
                    )
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
                logger.warning(
                    "runtime cancellation forwarding failed for task %s on %s: %s",
                    durable_task_id,
                    runtime_type,
                    exc,
                    exc_info=exc,
                )
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
        except KeyError as exc:
            logger.debug("compute budget reservation was already settled: %s", exc)
            return
        finally:
            try:
                budget.release(plan.budget_reservation_id)
            except KeyError as exc:
                logger.debug("compute budget reservation release was already applied: %s", exc)

    def _release_budget(self, plan: ComputePlan) -> None:
        if not plan.budget_reservation_id or self.scheduler.budget is None:
            return
        try:
            self.scheduler.budget.release(plan.budget_reservation_id)
        except KeyError as exc:
            logger.debug("compute budget reservation was already released: %s", exc)

    def _fail_active_runs(self, durable_task_id: str, *, code: str, detail: str) -> None:
        """Close provider runs when an adapter violates the terminal-event contract."""
        active = {
            AgentRunStatus.CREATED.value,
            AgentRunStatus.RUNNING.value,
            AgentRunStatus.PAUSED.value,
        }
        for run in self.runs.list_for_task(durable_task_id):
            if run.get("status") not in active:
                continue
            try:
                self.runs.transition(
                    str(run["id"]),
                    AgentRunStatus.FAILED.value,
                    error_code=code,
                    error_detail=detail,
                )
            except (KeyError, ValueError) as exc:
                # A concurrent adapter shutdown may have closed the run after
                # the snapshot; the durable task still receives the protocol
                # error from the caller.
                logger.debug("active AgentRun was already closed during protocol failure handling: %s", exc)
                continue

    def _durable_task_id(self, agent_task_id: str) -> str:
        """Resolve the Task id used by AgentRunStore's task index."""
        link = self.runs.db.fetchone(
            "SELECT task_id FROM agent_tasks WHERE id=?",
            (agent_task_id,),
        )
        linked = link.get("task_id") if link else None
        return str(linked or agent_task_id)

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
