"""Host-owned Control Plane dispatch seams.

The buses own dispatch and observation policy; durable task/runtime state stays
in SQLite.  Command receipts and control events are optionally persisted in
SQLite as restart/replay evidence, while listeners remain process-local.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from src.context.bundles import ContextBundleStore
from src.core.database import generate_id
from src.core.task_runtime import TaskRuntime, TaskStateError

from .events import RuntimeEventStore
from .persistence import (
    AgentRunStore,
    AgentTaskStore,
    ControlCommandStore,
    ControlEventStore,
    ComputePlanStore,
    ProposalStore,
)
from .approvals import ApprovalEngine, ApprovalStatus, is_host_approval_actor
from .contracts import AgentTask, ComputePlan, RuntimeEvent
from .errors import (
    AgentRuntimeError,
    ComputeEscalationDenied,
    ControlCommandLeaseLost,
    TaskInterrupted,
)
from .router import RuntimeFallbackPolicy, RuntimeRouter
from .tool_gateway import PermissionEngine, ToolCallContext, ToolGateway


logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ControlCommand:
    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    actor: str = "system"
    command_id: str = field(default_factory=lambda: f"cmd-{generate_id()}")


@dataclass(frozen=True)
class ControlEvent:
    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    command_id: str | None = None
    created_at: str = field(default_factory=_now)
    event_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "name": self.name,
            "payload": dict(self.payload),
            "commandId": self.command_id,
            "createdAt": self.created_at,
        }


class ControlCommandInProgress(RuntimeError):
    """A second caller observed a command receipt still being processed."""


class ControlCommandRejected(RuntimeError):
    """A repeated command id already has a durable rejected receipt."""


CommandHandler = Callable[[Mapping[str, Any], str], Any]
AsyncCommandHandler = Callable[[Mapping[str, Any], str], Awaitable[Any]]
QueryHandler = Callable[[Mapping[str, Any]], Any]
EventListener = Callable[[ControlEvent], Any]


class CommandBus:
    """Dispatch named commands to host-owned handlers.

    Handlers are deliberately side-effect explicit.  A command can enqueue a
    durable task, but the bus itself never keeps task state in memory.
    """

    def __init__(
        self,
        events: "EventBus | None" = None,
        receipts: ControlCommandStore | None = None,
    ):
        self._handlers: dict[str, CommandHandler] = {}
        self._async_handlers: dict[str, AsyncCommandHandler] = {}
        self.events = events
        self.receipts = receipts

    def register(self, name: str, handler: CommandHandler) -> None:
        key = str(name).strip()
        if not key:
            raise ValueError("command name is required")
        if key in self._handlers:
            raise ValueError(f"command already registered: {key}")
        self._handlers[key] = handler

    def register_async(self, name: str, handler: AsyncCommandHandler) -> None:
        key = str(name).strip()
        if not key:
            raise ValueError("command name is required")
        if key in self._async_handlers:
            raise ValueError(f"async command already registered: {key}")
        self._async_handlers[key] = handler

    def dispatch(
        self,
        command: ControlCommand | str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str = "system",
    ) -> Any:
        envelope = self._envelope(command, payload, actor=actor)
        self._require_handler(envelope.name, async_handler=False)
        replayed, replay = self._begin_receipt(envelope)
        if replayed:
            return replay
        return self._execute_sync(envelope)

    def enqueue(
        self,
        command: ControlCommand | str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str = "system",
    ) -> dict[str, Any]:
        """Persist a command for a ControlCommandWorker to claim later."""
        envelope = self._envelope(command, payload, actor=actor)
        self._require_handler(envelope.name)
        if self.receipts is None:
            raise RuntimeError("durable command receipts are required for queued dispatch")
        receipt = self.receipts.enqueue(
            envelope.command_id,
            envelope.name,
            envelope.payload,
            envelope.actor,
        )
        is_new = bool(receipt.pop("_new", False))
        self._validate_receipt(envelope, receipt)
        if receipt.get("status") == "rejected":
            raise ControlCommandRejected(
                str(receipt.get("error") or f"command was rejected: {envelope.command_id}")
            )
        if receipt.get("status") == "processing" and not receipt.get("queue") and not is_new:
            raise ControlCommandInProgress(f"command is still processing: {envelope.command_id}")
        if is_new and self.events:
            self.events.publish(
                "control.command.queued",
                {"command": envelope.name},
                command_id=envelope.command_id,
            )
        return receipt

    async def dispatch_async(
        self,
        command: ControlCommand | str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str = "system",
    ) -> Any:
        """Dispatch an async override while retaining the same receipt path."""
        envelope = self._envelope(command, payload, actor=actor)
        handler = self._async_handlers.get(envelope.name)
        if handler is None:
            return self.dispatch(envelope)
        self._require_handler(envelope.name, async_handler=True)
        replayed, replay = self._begin_receipt(envelope)
        if replayed:
            return replay
        return await self._execute_async(envelope)

    def execute_claimed(
        self,
        command: ControlCommand,
        *,
        worker_id: str,
    ) -> Any:
        """Execute a command whose queue lease was claimed by ``worker_id``."""
        self._require_handler(command.name, async_handler=False)
        self._assert_claimed(command, worker_id)
        return self._execute_sync(command, worker_id=worker_id)

    async def execute_claimed_async(
        self,
        command: ControlCommand,
        *,
        worker_id: str,
    ) -> Any:
        """Execute a claimed sync or async command without reacquiring it."""
        self._assert_claimed(command, worker_id)
        if command.name in self._async_handlers:
            return await self._execute_async(command, worker_id=worker_id)
        self._require_handler(command.name, async_handler=False)
        return await asyncio.to_thread(self._execute_sync, command, worker_id=worker_id)

    def _execute_sync(self, envelope: ControlCommand, *, worker_id: str | None = None) -> Any:
        handler = self._handlers[envelope.name]
        try:
            result = handler(self._handler_payload(envelope), envelope.actor)
            if inspect.isawaitable(result):
                raise TypeError("CommandBus handlers must be synchronous; use dispatch_async")
        except Exception as exc:
            self._finish_receipt(envelope, status="rejected", error=str(exc), worker_id=worker_id)
            if self.events:
                self.events.publish(
                    "control.command.rejected",
                    {"command": envelope.name, "error": str(exc)},
                    command_id=envelope.command_id,
                )
            raise
        self._finish_receipt(envelope, status="accepted", result=result, worker_id=worker_id)
        if self.events:
            self.events.publish(
                "control.command.accepted",
                {"command": envelope.name, "result": result},
                command_id=envelope.command_id,
            )
        return result

    async def _execute_async(self, envelope: ControlCommand, *, worker_id: str | None = None) -> Any:
        handler = self._async_handlers[envelope.name]
        try:
            result = await handler(self._handler_payload(envelope), envelope.actor)
        except Exception as exc:
            self._finish_receipt(envelope, status="rejected", error=str(exc), worker_id=worker_id)
            if self.events:
                self.events.publish(
                    "control.command.rejected",
                    {"command": envelope.name, "error": str(exc)},
                    command_id=envelope.command_id,
                )
            raise
        self._finish_receipt(envelope, status="accepted", result=result, worker_id=worker_id)
        if self.events:
            self.events.publish(
                "control.command.accepted",
                {"command": envelope.name, "result": result},
                command_id=envelope.command_id,
            )
        return result

    @staticmethod
    def _envelope(
        command: ControlCommand | str,
        payload: Mapping[str, Any] | None,
        *,
        actor: str,
    ) -> ControlCommand:
        return command if isinstance(command, ControlCommand) else ControlCommand(
            name=command,
            payload=payload or {},
            actor=actor,
        )

    @staticmethod
    def _handler_payload(envelope: ControlCommand) -> dict[str, Any]:
        payload = dict(envelope.payload)
        payload.setdefault("_commandId", envelope.command_id)
        return payload

    def _begin_receipt(self, envelope: ControlCommand) -> tuple[bool, Any]:
        if self.receipts is None:
            return False, None
        receipt = self.receipts.begin(
            envelope.command_id,
            envelope.name,
            envelope.payload,
            envelope.actor,
        )
        is_new = bool(receipt.pop("_new", False))
        if is_new:
            return False, None
        self._validate_receipt(envelope, receipt)
        status = receipt.get("status")
        if status == "accepted":
            return True, receipt.get("result")
        if status == "rejected":
            raise ControlCommandRejected(
                str(receipt.get("error") or f"command was rejected: {envelope.command_id}")
            )
        raise ControlCommandInProgress(f"command is still processing: {envelope.command_id}")

    def _finish_receipt(
        self,
        envelope: ControlCommand,
        *,
        status: str,
        result: Any = None,
        error: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        if self.receipts is not None:
            self.receipts.complete(
                envelope.command_id,
                status=status,
                result=result,
                error=error,
                worker_id=worker_id,
            )

    def _assert_claimed(self, envelope: ControlCommand, worker_id: str) -> None:
        if self.receipts is None:
            raise RuntimeError("durable command receipts are required for worker execution")
        receipt = self.receipts.get(envelope.command_id)
        if receipt is None:
            raise KeyError(f"command receipt not found: {envelope.command_id}")
        self._validate_receipt(envelope, receipt)
        queue = receipt.get("queue") or {}
        if (
            receipt.get("status") != "processing"
            or queue.get("status") != "processing"
            or queue.get("workerId") != worker_id
        ):
            raise ControlCommandLeaseLost(
                f"command worker lease is no longer valid: {envelope.command_id}",
                details={"commandId": envelope.command_id, "workerId": worker_id},
            )

    @staticmethod
    def _validate_receipt(envelope: ControlCommand, receipt: Mapping[str, Any]) -> None:
        if (
            receipt.get("name") != envelope.name
            or receipt.get("actor") != envelope.actor
            or receipt.get("payload") != dict(envelope.payload)
        ):
            raise ValueError(
                f"command id is already bound to a different envelope: {envelope.command_id}"
            )

    def _require_handler(self, name: str, *, async_handler: bool | None = None) -> None:
        handlers = self._async_handlers if async_handler else self._handlers
        if async_handler is None:
            if name not in self._handlers and name not in self._async_handlers:
                raise KeyError(f"command handler not found: {name}")
            return
        if name not in handlers:
            raise KeyError(f"command handler not found: {name}")


class ControlCommandWorker:
    """Claim and execute durable Control Plane commands.

    The worker is intentionally host-owned: a second process can use the
    same SQLite database and register the same Control Plane handlers, while
    the queue lease fences completion to the process that actually claimed
    the command.
    """

    def __init__(
        self,
        bus: CommandBus,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        worker_id = str(worker_id).strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds cannot be negative")
        self.bus = bus
        self.worker_id = worker_id
        self.lease_seconds = int(lease_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)

    async def run_once(self) -> dict[str, Any] | None:
        """Claim at most one command and return its durable receipt."""
        if self.bus.receipts is None:
            raise RuntimeError("durable command receipts are required for worker execution")
        claimed = self.bus.receipts.claim(
            self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if claimed is None:
            return None
        payload_raw = claimed.get("payload")
        command = ControlCommand(
            name=str(claimed["name"]),
            payload=payload_raw if isinstance(payload_raw, Mapping) else {},
            actor=str(claimed.get("actor") or "system"),
            command_id=str(claimed["commandId"]),
        )
        heartbeat = asyncio.create_task(self._heartbeat(command.command_id))
        try:
            await self.bus.execute_claimed_async(command, worker_id=self.worker_id)
        except Exception as exc:
            # Handler failures are already durable rejected receipts.  Return
            # the receipt so a supervising loop can observe the failure while
            # keeping the worker alive for the next independent command.
            logger.warning(
                "Control command worker observed a handler failure",
                extra={"command_id": command.command_id, "command_name": command.name},
                exc_info=exc,
            )
            receipt = self.bus.receipts.get(command.command_id)
            return receipt or claimed
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        return self.bus.receipts.get(command.command_id) or claimed

    async def _heartbeat(self, command_id: str) -> None:
        """Keep a live claim from becoming eligible for duplicate execution."""
        if self.bus.receipts is None:
            return
        interval = max(0.1, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            if not self.bus.receipts.renew(
                command_id,
                self.worker_id,
                lease_seconds=self.lease_seconds,
            ):
                return

    async def run_forever(self, *, stop_event: asyncio.Event) -> None:
        """Keep claiming work until the host lifecycle asks this worker to stop."""
        while not stop_event.is_set():
            receipt = await self.run_once()
            if receipt is None and self.poll_interval_seconds:
                await asyncio.sleep(self.poll_interval_seconds)


class QueryBus:
    """Read-only named query dispatch; handlers must not mutate Canon."""

    def __init__(self):
        self._handlers: dict[str, QueryHandler] = {}

    def register(self, name: str, handler: QueryHandler) -> None:
        key = str(name).strip()
        if not key:
            raise ValueError("query name is required")
        if key in self._handlers:
            raise ValueError(f"query already registered: {key}")
        self._handlers[key] = handler

    def dispatch(self, name: str, payload: Mapping[str, Any] | None = None) -> Any:
        try:
            handler = self._handlers[name]
        except KeyError:
            raise KeyError(f"query handler not found: {name}") from None
        result = handler(payload or {})
        if inspect.isawaitable(result):
            raise TypeError("QueryBus handlers must be synchronous")
        return result


class EventBus:
    """Observer bus with an optional append-only cross-process event ledger."""

    def __init__(self, store: ControlEventStore | None = None):
        self._listeners: dict[str, list[EventListener]] = {}
        self.store = store

    def subscribe(self, name: str, listener: EventListener) -> None:
        key = str(name).strip()
        if not key:
            raise ValueError("event name is required")
        self._listeners.setdefault(key, []).append(listener)

    def publish(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        command_id: str | None = None,
    ) -> ControlEvent:
        event = ControlEvent(name=name, payload=dict(payload or {}), command_id=command_id)
        if self.store is not None:
            event_id = self.store.append(
                event.name,
                event.payload,
                command_id=event.command_id,
                created_at=event.created_at,
            )
            event = replace(event, event_id=event_id)
        for listener in tuple(self._listeners.get(name, ())):
            result = listener(event)
            if inspect.isawaitable(result):
                raise TypeError("EventBus listeners must be synchronous")
        return event

    def list_since(
        self,
        *,
        after_id: int = 0,
        name: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        return self.store.list_since(after_id=after_id, name=name, limit=limit)


class TaskOrchestrator:
    """Run an explicit AgentTask through the durable task and runtime seams.

    Existing chapter handlers remain owned by ``PersistentTaskWorker``.  This
    orchestrator is the narrow execution path for callers that intentionally
    submit a provider-neutral AgentTask to the RuntimeRouter; it claims the
    linked durable task, persists runtime events through the router, and closes
    the durable task only after the adapter stream ends.
    """

    def __init__(
        self,
        task_runtime: TaskRuntime,
        router: RuntimeRouter,
        *,
        agent_tasks: AgentTaskStore | None = None,
        fallback_policy: RuntimeFallbackPolicy | None = None,
    ) -> None:
        self.task_runtime = task_runtime
        self.router = router
        self.agent_tasks = agent_tasks or AgentTaskStore(task_runtime.db)
        self.fallback_policy = fallback_policy or RuntimeFallbackPolicy()

    def enqueue(self, task: AgentTask, **kwargs: Any) -> dict[str, Any]:
        """Persist a provider-neutral AgentTask and its durable task link."""
        return self.task_runtime.enqueue_agent_task(task, **kwargs)

    def get(self, task_id: str) -> AgentTask | None:
        """Resolve either an AgentTask id or its linked durable task id."""
        task = self.agent_tasks.contract(task_id)
        return task or self.agent_tasks.contract_for_durable_task(task_id)

    async def execute(
        self,
        durable_task_id: str,
        *,
        worker_id: str = "agent-orchestrator",
        lease_seconds: int = 60,
        compute_plan_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Claim and execute one linked AgentTask.

        A lost lease or a provider failure is handled by the durable task
        state machine.  The adapter's AgentRun remains the detailed runtime
        audit, while the task row receives only a compact execution summary.
        """
        task = self.agent_tasks.contract_for_durable_task(durable_task_id)
        if task is None:
            raise KeyError(f"agent task not found for durable task: {durable_task_id}")
        durable = self.task_runtime.get(durable_task_id)
        if durable is not None and durable.get("stage") == "blocked":
            raise TaskStateError("blocked tasks require an explicit author/workflow transition")
        compute_plan: ComputePlan | None = None
        if compute_plan_id:
            record = self.router.plans.get(compute_plan_id)
            if record is None:
                raise KeyError(f"compute plan not found: {compute_plan_id}")
            if str(record.get("agent_task_id") or record.get("agentTaskId") or "") != task.task_id:
                raise ValueError("compute plan is not owned by the AgentTask")
            raw_plan = record.get("plan")
            if not isinstance(raw_plan, dict):
                raise ValueError("persisted compute plan is invalid")
            compute_plan = ComputePlan.from_mapping(raw_plan)

        claimed = self.task_runtime.claim_by_id(
            durable_task_id,
            worker_id,
            lease_seconds=lease_seconds,
        )
        if claimed is None:
            return None

        events: list[RuntimeEvent] = []
        try:
            async for event in self.router.execute_with_fallback(
                task,
                compute_plan=compute_plan,
                fallback_policy=self.fallback_policy,
            ):
                events.append(event)
            current = self.task_runtime.get(durable_task_id)
            if current is None:
                return None
            if current.get("status") == "cancelling":
                return self.task_runtime.transition(
                    durable_task_id,
                    "cancelled",
                    detail={"reason": "cancelled_at_runtime_boundary"},
                    lease_owner=worker_id,
                )
            if current.get("status") == "running":
                self._require_succeeded_agent_run(events, durable_task_id)
                return self.task_runtime.transition(
                    durable_task_id,
                    "completed",
                    detail=self._execution_summary(events),
                    result=self._execution_summary(events),
                    lease_owner=worker_id,
                )
            return current
        except TaskInterrupted as exc:
            current = self.task_runtime.get(durable_task_id)
            if current and current.get("status") in {"running", "cancelling"}:
                return self.task_runtime.transition(
                    durable_task_id,
                    "cancelled",
                    detail={"reason": str(exc) or "runtime interruption"},
                    lease_owner=worker_id,
                )
            return current
        except Exception as exc:
            current = self.task_runtime.get(durable_task_id)
            if current and current.get("status") in {"running", "cancelling"}:
                code = str(getattr(exc, "code", None) or "RUNTIME_EXECUTION_FAILED")
                retryable = bool(getattr(exc, "retryable", False))
                return self.task_runtime.fail(
                    durable_task_id,
                    code,
                    str(exc),
                    retryable=retryable,
                    lease_owner=worker_id,
                )
            raise

    def _require_succeeded_agent_run(
        self,
        events: list[RuntimeEvent],
        durable_task_id: str,
    ) -> None:
        """Require a provider success to be backed by durable run state."""
        terminal = next(
            (
                event
                for event in reversed(events)
                if event.event_type.replace("/", ".") in {"turn.completed", "turn.complete"}
            ),
            None,
        )
        if terminal is None or not terminal.agent_run_id:
            raise AgentRuntimeError(
                "runtime completed without a durable AgentRun",
                code="RUNTIME_PROTOCOL_ERROR",
                retryable=True,
            )
        run = self.router.runs.get(terminal.agent_run_id)
        if (
            run is None
            or str(run.get("task_id") or "") != str(durable_task_id)
            or run.get("status") != "succeeded"
        ):
            raise AgentRuntimeError(
                "runtime completed without a succeeded AgentRun",
                code="RUNTIME_PROTOCOL_ERROR",
                retryable=True,
            )

    def prepare_compute_escalation_request(
        self,
        task_id: str,
        *,
        requested_capability: str | int,
        requested_reasoning: str | None = None,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate an Agent escalation request without changing execution."""
        task = self.agent_tasks.contract(task_id)
        durable_task_id = task_id
        if task is None:
            task = self.agent_tasks.contract_for_durable_task(task_id)
        else:
            linked = self.task_runtime.get(task_id)
            if linked is not None:
                durable_task_id = task_id
            else:
                row = self.agent_tasks.get(task.task_id)
                durable_task_id = str(row.get("task_id") or task_id) if row else task_id
        if task is None:
            raise KeyError(f"agent task not found: {task_id}")
        durable = self.task_runtime.get(durable_task_id)
        if durable and durable.get("status") in {
            "completed", "failed", "cancelled", "needs_author_decision",
        }:
            raise TaskStateError("a terminal task cannot request compute escalation")
        validation = self.router.validate_escalation_request(
            task,
            plan_id=plan_id,
            requested_capability=requested_capability,
            requested_reasoning=requested_reasoning,
        )
        return {
            "taskId": durable_task_id,
            "agentTaskId": task.task_id,
            **validation,
        }

    def request_escalation(
        self,
        durable_task_id: str,
        *,
        requested_capability: str | int,
        requested_reasoning: str | None = None,
        plan_id: str | None = None,
        actor: str = "agent",
        approved: bool = False,
    ) -> dict[str, Any]:
        """Create a durable, Host-approved successor ComputePlan.

        This method does not execute a provider call.  The caller receives a
        plan id and may explicitly pass it to :meth:`execute`; an AgentTask or
        Runtime cannot turn the request into an implicit capability upgrade.
        """
        task = self.agent_tasks.contract_for_durable_task(durable_task_id)
        if task is None:
            raise KeyError(f"agent task not found for durable task: {durable_task_id}")
        durable = self.task_runtime.get(durable_task_id)
        if durable and durable.get("status") in {
            "completed", "failed", "cancelled", "needs_author_decision",
        }:
            raise TaskStateError("a terminal task cannot request compute escalation")
        plan = self.router.request_escalation(
            task,
            plan_id=plan_id,
            requested_capability=requested_capability,
            requested_reasoning=requested_reasoning,
            actor=actor,
            approved=approved,
        )
        return {
            "taskId": durable_task_id,
            "agentTaskId": task.task_id,
            "plan": plan.to_dict(),
            "executeWithPlanId": plan.plan_id,
        }

    async def cancel(self, durable_task_id: str) -> dict[str, Any]:
        """Persist cancellation and forward provider interrupts from audit."""
        before = self.task_runtime.get(durable_task_id) or {}
        child_id = before.get("waiting_for_task_id") or before.get("waitingForTaskId")
        result = self.task_runtime.cancel(durable_task_id)
        targets = [durable_task_id]
        if child_id:
            targets.append(str(child_id))
        for target in targets:
            await self.router.cancel(target)
        return result

    @staticmethod
    def _execution_summary(events: list[RuntimeEvent]) -> dict[str, Any]:
        run_ids = sorted({event.agent_run_id for event in events if event.agent_run_id})
        last_event = events[-1] if events else None
        return {
            "agentRunIds": run_ids,
            "eventCount": len(events),
            "lastEventType": last_event.event_type if last_event else None,
        }


class ControlPlane:
    """A concrete host facade over the durable task and runtime seams."""

    def __init__(
        self,
        task_runtime: TaskRuntime,
        *,
        events: EventBus | None = None,
        approvals: ApprovalEngine | None = None,
        permissions: PermissionEngine | None = None,
        orchestrator: TaskOrchestrator | None = None,
        tools: ToolGateway | None = None,
        receipts: ControlCommandStore | None = None,
    ):
        self.task_runtime = task_runtime
        self.receipts = receipts or ControlCommandStore(task_runtime.db)
        self.events = events or EventBus(ControlEventStore(task_runtime.db))
        self.approvals = approvals or ApprovalEngine(db=task_runtime.db)
        self.permissions = permissions or PermissionEngine()
        self.orchestrator = orchestrator
        self.tools = tools
        self.commands = CommandBus(self.events, self.receipts)
        self.queries = QueryBus()
        self.commands.register("task.enqueue", self._enqueue)
        for operation in ("pause", "resume", "cancel", "retry"):
            self.commands.register(
                f"task.{operation}",
                lambda payload, _actor, operation=operation: self._task_operation(payload, operation),
            )
        self.commands.register_async("task.cancel", self._cancel_async)
        self.commands.register("approval.request", self._request_approval)
        self.commands.register("approval.approve", self._approve_approval)
        self.commands.register("approval.reject", self._reject_approval)
        self.commands.register("approval.revoke", self._revoke_approval)
        self.commands.register(
            "compute.escalation.request", self._request_compute_escalation
        )
        self.commands.register("compute.escalate", self._compute_escalate)
        self.commands.register(
            "proposal.accept",
            lambda payload, actor: self._decide_proposal(payload, actor, "ACCEPTED"),
        )
        self.commands.register(
            "proposal.reject",
            lambda payload, actor: self._decide_proposal(payload, actor, "REJECTED"),
        )
        self.commands.register(
            "proposal.supersede",
            lambda payload, actor: self._decide_proposal(payload, actor, "SUPERSEDED"),
        )
        self.queries.register("task.get", self._get_task)
        self.queries.register("task.events", self._task_events)
        self.queries.register("task.agent-task", self._agent_task)
        self.queries.register("task.agent-runs", self._agent_runs)
        self.queries.register("task.domain-events", self._domain_events)
        self.queries.register("task.compute-plans", self._compute_plans)
        self.queries.register(
            "task.compute-escalation-requests", self._compute_escalation_requests
        )
        self.queries.register("task.context-bundles", self._context_bundles)
        self.queries.register("runtime.ui-events", self._ui_events)
        self.queries.register("approval.get", self._get_approval)
        self.queries.register("approval.list", self._list_approvals)
        self.queries.register("task.tool-calls", self._tool_calls)
        self.queries.register("task.proposals", self._proposals)
        self.queries.register("task.approvals", self._task_approvals)
        self.queries.register("control.command-receipts", self._command_receipts)
        self.queries.register("control.events", self._control_events)

    async def dispatch_async(
        self,
        command: ControlCommand | str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str = "system",
    ) -> Any:
        """Dispatch a command and await an async runtime cancellation path."""
        return await self.commands.dispatch_async(command, payload, actor=actor)

    def enqueue(
        self,
        command: ControlCommand | str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str = "system",
    ) -> dict[str, Any]:
        """Persist a command for a host-owned ControlCommandWorker."""
        return self.commands.enqueue(command, payload, actor=actor)

    async def _cancel_async(self, payload: Mapping[str, Any], _actor: str) -> dict[str, Any]:
        task_id = self._task_id(payload)
        if self.orchestrator is not None:
            return await self.orchestrator.cancel(task_id)
        return self.task_runtime.cancel(task_id)

    def _enqueue(self, payload: Mapping[str, Any], actor: str) -> dict[str, Any]:
        task_type = str(payload.get("taskType") or payload.get("task_type") or "").strip()
        if not task_type:
            raise ValueError("taskType is required")
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        project_id = payload.get("projectId") or payload.get("project_id")
        book_id = payload.get("bookId") or payload.get("book_id")
        idempotency_key = (
            payload.get("idempotencyKey")
            or payload.get("idempotency_key")
            or payload.get("_commandId")
        )
        initiated_by = str(actor or "system").strip() or "system"
        if task_type == "continuous":
            return self.task_runtime.enqueue_continuous(
                project_id=str(project_id or ""),
                book_id=str(book_id or ""),
                data=data,
                idempotency_key=str(idempotency_key),
                initiated_by=initiated_by,
            )
        return self.task_runtime.enqueue(
            task_type,
            project_id=project_id,
            book_id=book_id,
            chapter_number=payload.get("chapterNumber") or payload.get("chapter_number"),
            data=data,
            stage=str(payload.get("stage") or "queued"),
            idempotency_key=(
                idempotency_key
            ),
            initiated_by=initiated_by,
            initial_status=str(
                payload.get("initialStatus")
                or payload.get("initial_status")
                or "queued"
            ),
        )

    def _task_operation(self, payload: Mapping[str, Any], operation: str) -> dict[str, Any]:
        task_id = self._task_id(payload)
        return getattr(self.task_runtime, operation)(task_id)

    def _request_approval(self, payload: Mapping[str, Any], actor: str) -> dict[str, Any]:
        task_id = self._task_id(payload)
        task_row = AgentTaskStore(self.task_runtime.db).get_for_durable_task(task_id)
        if task_row is None:
            task_row = AgentTaskStore(self.task_runtime.db).get(task_id)
        if task_row is None:
            raise KeyError(f"agent task not found: {task_id}")
        durable_row = self.task_runtime.get(str(task_row["durableTaskId"]))
        if durable_row is None:
            raise KeyError(f"durable task not found: {task_row['durableTaskId']}")
        if durable_row.get("status") in {
            "completed", "failed", "cancelled", "needs_author_decision",
        }:
            raise TaskStateError("a terminal task cannot request a new approval")
        tool_name = str(payload.get("toolName") or payload.get("tool_name") or "").strip()
        domain = str(payload.get("domain") or "").strip()
        if not tool_name or not domain:
            raise ValueError("toolName and domain are required")
        if self.tools is not None:
            definition = self.tools.get(tool_name)
            if not definition.requires_approval:
                raise ValueError(f"tool does not require an approval grant: {tool_name}")
            if definition.domain != domain:
                raise ValueError(f"approval domain does not match tool definition: {tool_name}")
        record = self.approvals.request(
            str(task_row["agentTaskId"]),
            tool_name,
            domain,
            requested_by=actor or "system",
            ttl_seconds=payload.get("ttlSeconds") or payload.get("ttl_seconds"),
            reason=str(payload.get("reason") or ""),
        )
        self.events.publish(
            "approval.requested",
            record.to_dict(),
            command_id=self._command_id(payload),
        )
        return record.to_dict()

    def _approve_approval(self, payload: Mapping[str, Any], actor: str) -> dict[str, Any]:
        approval_id = self._approval_id(payload)
        record = self.approvals.approve(
            approval_id,
            approved_by=actor or "system",
            reason=str(payload.get("reason") or ""),
        )
        self.events.publish(
            "approval.approved",
            record.to_dict(),
            command_id=self._command_id(payload),
        )
        return record.to_dict()

    def _reject_approval(self, payload: Mapping[str, Any], actor: str) -> dict[str, Any]:
        approval_id = self._approval_id(payload)
        record = self.approvals.reject(
            approval_id,
            rejected_by=actor or "system",
            reason=str(payload.get("reason") or ""),
        )
        self.events.publish(
            "approval.rejected",
            record.to_dict(),
            command_id=self._command_id(payload),
        )
        return record.to_dict()

    def _revoke_approval(self, payload: Mapping[str, Any], actor: str) -> dict[str, Any]:
        approval_id = self._approval_id(payload)
        record = self.approvals.revoke(
            approval_id,
            revoked_by=actor or "system",
            reason=str(payload.get("reason") or ""),
        )
        self.events.publish(
            "approval.revoked",
            record.to_dict(),
            command_id=self._command_id(payload),
        )
        return record.to_dict()

    def request_compute_escalation_from_agent(
        self,
        arguments: Mapping[str, Any],
        context: ToolCallContext,
    ) -> dict[str, Any]:
        """Host-bind the runtime tool used by an Agent to ask for escalation."""
        row = AgentTaskStore(self.task_runtime.db).get(context.task.task_id)
        if row is None:
            raise KeyError(f"agent task not found: {context.task.task_id}")
        payload = dict(arguments)
        payload["taskId"] = row["task_id"]
        supplied_run_id = str(
            payload.get("agentRunId") or payload.get("agent_run_id") or ""
        ).strip() or None
        bound_run_id = str(context.agent_run_id or "").strip() or None
        if supplied_run_id and bound_run_id is None:
            raise ValueError("agentRunId requires a Host-bound AgentRun context")
        if supplied_run_id and supplied_run_id != bound_run_id:
            raise ValueError("agentRunId must match the Host-bound AgentRun")
        if bound_run_id:
            run = self.task_runtime.db.fetchone(
                "SELECT agent_task_id, task_id FROM agent_runs WHERE id=?",
                (bound_run_id,),
            )
            if (
                run is None
                or str(run["agent_task_id"] or "") != str(context.task.task_id)
                or str(run["task_id"] or "") != str(row["task_id"] or "")
            ):
                raise ValueError("Host-bound AgentRun is outside the AgentTask scope")
            payload["agentRunId"] = bound_run_id
        return self._request_compute_escalation(payload, actor="agent")

    def _request_compute_escalation(
        self,
        payload: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        if self.orchestrator is None:
            raise RuntimeError("compute escalation requires a RuntimeRouter-backed orchestrator")
        actor_key = str(actor or "").strip().lower()
        scheduler = getattr(self.orchestrator.router, "scheduler", None)
        policy = getattr(scheduler, "policy", None)
        if (
            not is_host_approval_actor(actor_key)
            and policy is not None
            and not bool(getattr(policy, "allow_agent_escalation", False))
        ):
            raise ComputeEscalationDenied(
                "Agent compute escalation requests are disabled by the active Compute policy",
                details={
                    "actor": actor_key or None,
                    "strategy": getattr(policy, "strategy", None),
                    "allowAgentEscalation": False,
                },
            )
        task_id = self._task_id(payload)
        requested = payload.get("requestedCapability") or payload.get("requested_capability")
        if requested is None or not str(requested).strip():
            raise ValueError("requestedCapability is required")
        requested_reasoning = payload.get("requestedReasoning") or payload.get("requested_reasoning")
        if requested_reasoning is not None and not str(requested_reasoning).strip():
            requested_reasoning = None
        plan_id = str(payload.get("planId") or payload.get("plan_id") or "").strip() or None
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise ValueError("reason is required for a compute escalation request")
        if len(reason) > 2_000:
            raise ValueError("reason exceeds the escalation request limit")
        raw_evidence = payload.get("evidence", [])
        if not isinstance(raw_evidence, list):
            raise ValueError("evidence must be an array")
        if len(raw_evidence) > 32:
            raise ValueError("evidence cannot contain more than 32 items")
        evidence = []
        for item in raw_evidence:
            value = str(item).strip()
            if not value or len(value) > 500:
                raise ValueError("each escalation evidence item must be 1-500 characters")
            evidence.append(value)

        validated = self.orchestrator.prepare_compute_escalation_request(
            task_id,
            requested_capability=str(requested),
            requested_reasoning=(
                str(requested_reasoning) if requested_reasoning is not None else None
            ),
            plan_id=plan_id,
        )
        agent_task_id = str(validated["agentTaskId"])
        request_payload = {
            "taskId": validated["taskId"],
            "agentTaskId": agent_task_id,
            "agentRunId": payload.get("agentRunId") or payload.get("agent_run_id"),
            "planId": validated["plan"]["planId"],
            "requestedCapability": validated["requestedCapability"],
            "requestedReasoning": validated["requestedReasoning"],
            "reason": reason,
            "evidence": evidence,
        }
        request_payload = {
            key: value for key, value in request_payload.items() if value is not None
        }
        serialized_reason = json.dumps(
            request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

        existing = next(
            (
                item for item in self.approvals.list(task_id=agent_task_id)
                if item.tool_name == "compute.escalation"
                and item.domain == "compute"
                and item.status in {ApprovalStatus.REQUESTED, ApprovalStatus.APPROVED}
            ),
            None,
        )
        if existing is not None:
            if existing.reason != serialized_reason:
                raise ValueError("a different compute escalation request is already pending")
            approval = existing
            created = False
        else:
            approval = self.approvals.request(
                agent_task_id,
                "compute.escalation",
                "compute",
                requested_by=str(actor or "agent").strip() or "agent",
                ttl_seconds=payload.get("ttlSeconds") or payload.get("ttl_seconds"),
                reason=serialized_reason,
            )
            created = True
        if created:
            self.events.publish(
                "compute.escalation.requested",
                {
                    **request_payload,
                    "approvalId": approval.approval_id,
                    "status": "PENDING_HOST_APPROVAL",
                    "validation": {
                        key: value for key, value in validated.items() if key != "plan"
                    },
                    "plan": validated["plan"],
                },
                command_id=self._command_id(payload),
            )
        return {
            **request_payload,
            "requestId": approval.approval_id,
            "approvalId": approval.approval_id,
            "status": (
                "PENDING_HOST_APPROVAL"
                if approval.status is ApprovalStatus.REQUESTED
                else "APPROVED"
            ),
            "canonicalMutation": False,
            "computePlanChanged": False,
        }

    def _compute_escalate(self, payload: Mapping[str, Any], actor: str) -> dict[str, Any]:
        if self.orchestrator is None:
            raise RuntimeError("compute escalation requires a RuntimeRouter-backed orchestrator")
        task_id = self._task_id(payload)
        request_id = str(payload.get("requestId") or payload.get("request_id") or "").strip()
        if request_id:
            if not is_host_approval_actor(actor):
                raise ValueError("only a Host actor can apply an approved compute escalation")
            task_row = AgentTaskStore(self.task_runtime.db).get_for_durable_task(task_id)
            if task_row is None:
                raise KeyError(f"agent task not found for durable task: {task_id}")
            approval = self.approvals.get(request_id)
            if approval is None:
                raise KeyError(f"compute escalation approval not found: {request_id}")
            if (
                approval.task_id != str(task_row["agentTaskId"])
                or approval.tool_name != "compute.escalation"
                or approval.domain != "compute"
            ):
                raise ValueError("compute escalation approval is outside the task scope")
            if approval.status is not ApprovalStatus.APPROVED:
                raise ValueError(
                    f"compute escalation approval is not active: {approval.status.value}"
                )
            if not is_host_approval_actor(approval.approved_by):
                raise ValueError("compute escalation approval lacks a Host approver")
            try:
                request_payload = json.loads(approval.reason or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError("compute escalation approval payload is invalid") from exc
            if not isinstance(request_payload, dict):
                raise ValueError("compute escalation approval payload is invalid")
            request_task_id = str(request_payload.get("taskId") or "")
            if request_task_id != task_id:
                raise ValueError("compute escalation approval is outside the task scope")
            requested = request_payload.get("requestedCapability")
            if not requested:
                raise ValueError("compute escalation approval has no requested capability")
            # Validate the immutable request again before consuming its
            # one-shot Host approval.  The apply command never trusts caller
            # supplied target fields over the approved request payload.
            self.orchestrator.prepare_compute_escalation_request(
                task_id,
                requested_capability=str(requested),
                requested_reasoning=(
                    str(request_payload.get("requestedReasoning"))
                    if request_payload.get("requestedReasoning") is not None else None
                ),
                plan_id=str(request_payload.get("planId") or "").strip() or None,
            )
            consumed = self.approvals.consume(
                str(task_row["agentTaskId"]),
                "compute.escalation",
                domain="compute",
                approval_id=request_id,
            )
            result = self.orchestrator.request_escalation(
                task_id,
                requested_capability=str(requested),
                requested_reasoning=(
                    str(request_payload.get("requestedReasoning"))
                    if request_payload.get("requestedReasoning") is not None else None
                ),
                plan_id=str(request_payload.get("planId") or "").strip() or None,
                actor=str(consumed.approved_by or actor),
                approved=True,
            )
            result.update({
                "status": "APPLIED",
                "requestId": request_id,
                "approvalId": request_id,
            })
            self.events.publish(
                "compute.escalation.applied",
                result,
                command_id=self._command_id(payload),
            )
            return result
        requested = payload.get("requestedCapability") or payload.get("requested_capability")
        if requested is None or not str(requested).strip():
            raise ValueError("requestedCapability is required")
        plan_id = str(payload.get("planId") or payload.get("plan_id") or "").strip() or None
        requested_reasoning = payload.get("requestedReasoning") or payload.get("requested_reasoning")
        # A provider/runtime may submit a request, but only an authenticated
        # Host actor can approve it.  The boolean in a provider payload is not
        # an approval credential.
        caller = str(actor or "system").strip().lower()
        approved = bool(payload.get("approved", False)) and caller not in {
            "agent", "runtime", "provider", "codex", "claude", "gemini",
        }
        return self.orchestrator.request_escalation(
            task_id,
            requested_capability=str(requested),
            requested_reasoning=str(requested_reasoning) if requested_reasoning is not None else None,
            plan_id=plan_id,
            actor=caller or "system",
            approved=approved,
        )

    def _decide_proposal(
        self,
        payload: Mapping[str, Any],
        actor: str,
        status: str,
    ) -> dict[str, Any]:
        """Record a Host decision without accepting anything into Canon."""
        if not is_host_approval_actor(actor):
            raise ValueError("only a Host actor can decide an Agent proposal")
        task_id = self._task_id(payload)
        if self.task_runtime.get(task_id) is None:
            raise KeyError(f"durable task not found: {task_id}")
        proposal_id = str(
            payload.get("proposalId") or payload.get("proposal_id") or ""
        ).strip()
        if not proposal_id:
            raise ValueError("proposalId is required")
        reason = str(payload.get("reason") or "")[:4000]
        successor_id = str(
            payload.get("successorProposalId")
            or payload.get("successor_proposal_id")
            or ""
        ).strip() or None
        proposal_store = ProposalStore(self.task_runtime.db)
        proposal = proposal_store.get(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")
        if status == "ACCEPTED" and str(proposal.get("proposalType") or "").strip().lower() == "planning_synthesis":
            raise ValueError(
                "planning synthesis requires the author confirmation endpoint; "
                "generic proposal acceptance cannot apply planning projections"
            )
        if status == "ACCEPTED" and str(proposal.get("proposalType") or "").strip().lower() == "world_bootstrap":
            raise ValueError(
                "world bootstrap requires the author confirmation endpoint; "
                "generic proposal acceptance cannot stage Story Bible drafts"
            )
        result = proposal_store.decide_for_task(
            proposal_id,
            task_id,
            status,
            decided_by=str(actor).strip() or "system",
            reason=reason,
            successor_proposal_id=successor_id,
        )
        event_payload = {
            "taskId": task_id,
            "proposalId": proposal_id,
            "proposalType": result.get("proposalType"),
            "status": result.get("status"),
            "decidedBy": result.get("decided_by") or result.get("decidedBy"),
            "reason": result.get("decision_reason") or reason,
            "canonicalMutation": False,
        }
        if successor_id:
            event_payload["successorProposalId"] = successor_id
        self.events.publish(
            f"proposal.{status.lower()}",
            event_payload,
            command_id=self._command_id(payload),
        )
        return {
            **result,
            "status": result.get("status", status),
            "canonicalMutation": False,
            "decision": status,
        }

    def _get_approval(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        approval = self.approvals.get(self._approval_id(payload))
        return approval.to_dict() if approval else None

    def _list_approvals(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        task_id = payload.get("taskId") or payload.get("task_id")
        if task_id:
            row = AgentTaskStore(self.task_runtime.db).get_for_durable_task(str(task_id))
            task_id = row["agentTaskId"] if row else str(task_id)
        return [item.to_dict() for item in self.approvals.list(task_id=str(task_id) if task_id else None)]

    @staticmethod
    def _approval_id(payload: Mapping[str, Any]) -> str:
        approval_id = str(payload.get("approvalId") or payload.get("approval_id") or "").strip()
        if not approval_id:
            raise ValueError("approvalId is required")
        return approval_id

    @staticmethod
    def _command_id(payload: Mapping[str, Any]) -> str | None:
        value = str(payload.get("_commandId") or "").strip()
        return value or None

    @staticmethod
    def _task_id(payload: Mapping[str, Any]) -> str:
        task_id = str(payload.get("taskId") or payload.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("taskId is required")
        return task_id

    def _get_task(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        return self.task_runtime.get(self._task_id(payload))

    def _task_events(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        return self.task_runtime.events(
            self._task_id(payload), after_id=int(payload.get("afterId") or 0)
        )

    def _agent_task(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        return AgentTaskStore(self.task_runtime.db).get_for_durable_task(self._task_id(payload))

    def _agent_runs(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        return AgentRunStore(self.task_runtime.db).list_for_task(self._task_id(payload))

    def _domain_events(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Read a task's DomainEvent ledger with a durable cross-run cursor.

        AgentRun sequence numbers restart for every retry or role-specific
        run.  The DomainEvent row id is therefore the only cursor that can
        safely resume a task-wide projection without duplicating or skipping
        events.
        """
        raw_after = payload.get("afterId") or payload.get("after_id") or 0
        raw_limit = payload.get("limit", 200)
        try:
            after_id = max(0, int(raw_after))
            limit = max(1, min(1000, int(raw_limit)))
        except (TypeError, ValueError):
            raise ValueError("afterId and limit must be integers") from None
        return RuntimeEventStore(self.task_runtime.db).domain_events_for_task(
            self._task_id(payload), after_id=after_id, limit=limit,
        )

    def _compute_plans(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        agent_task = self._agent_task(payload)
        if not agent_task:
            return []
        return ComputePlanStore(self.task_runtime.db).list(str(agent_task["id"]))

    def _compute_escalation_requests(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Read durable escalation requests and their current approval state."""
        task_id = self._task_id(payload)
        agent_task = self._agent_task(payload)
        if agent_task is None:
            return []
        raw_limit = payload.get("limit", 200)
        try:
            limit = max(1, min(1000, int(raw_limit)))
        except (TypeError, ValueError):
            raise ValueError("limit must be an integer") from None
        events = self.events.list_since(
            after_id=0,
            name="compute.escalation.requested",
            limit=limit,
        )
        result: list[dict[str, Any]] = []
        for event in events:
            request = event.get("payload")
            if not isinstance(request, Mapping):
                continue
            if str(request.get("taskId") or "") != task_id:
                continue
            item = dict(request)
            item["eventId"] = event.get("eventId")
            approval_id = str(
                item.get("approvalId") or item.get("requestId") or ""
            ).strip()
            approval = self.approvals.get(approval_id) if approval_id else None
            if approval is not None:
                item["approval"] = approval.to_dict()
                item["status"] = (
                    "PENDING_HOST_APPROVAL"
                    if approval.status is ApprovalStatus.REQUESTED
                    else approval.status.value.upper()
                )
            result.append(item)
        return result

    def _context_bundles(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        run_rows = self._agent_runs(payload)
        bundle_runs: dict[str, list[str]] = {}
        for run in run_rows:
            bundle_id = run.get("context_bundle_id")
            if bundle_id:
                bundle_runs.setdefault(str(bundle_id), []).append(str(run["id"]))
        store = ContextBundleStore(self.task_runtime.db)
        result: list[dict[str, Any]] = []
        for bundle_id, run_ids in bundle_runs.items():
            bundle = store.get(bundle_id)
            if bundle is None:
                continue
            manifest = bundle.manifest()
            manifest["agentRunIds"] = run_ids
            result.append(manifest)
        return result

    def _ui_events(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        run_id = str(payload.get("agentRunId") or payload.get("agent_run_id") or "").strip()
        if not run_id:
            raise ValueError("agentRunId is required")
        return RuntimeEventStore(self.task_runtime.db).ui_events(
            run_id,
            after_sequence=int(payload.get("afterSequence") or payload.get("after_sequence") or 0),
        )

    def _tool_calls(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        store = AgentRunStore(self.task_runtime.db)
        result: list[dict[str, Any]] = []
        for run in self._agent_runs(payload):
            for call in store.tool_calls(str(run["id"])):
                result.append({"agentRunId": run["id"], **call})
        return result

    def _proposals(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        task_id = self._task_id(payload)
        return ProposalStore(self.task_runtime.db).list_for_task(task_id)

    def _task_approvals(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        store = AgentRunStore(self.task_runtime.db)
        result: list[dict[str, Any]] = []
        for run in self._agent_runs(payload):
            for approval in store.approvals(str(run["id"])):
                result.append({"agentRunId": run["id"], **approval})
        agent_task = self._agent_task(payload)
        if agent_task:
            result.extend(
                {
                    "agentTaskId": agent_task["agentTaskId"],
                    **approval.to_dict(),
                }
                for approval in self.approvals.list(task_id=str(agent_task["agentTaskId"]))
            )
        return result

    def _command_receipts(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        command_id = str(payload.get("commandId") or payload.get("command_id") or "").strip()
        if command_id:
            receipt = self.receipts.get(command_id)
            return [receipt] if receipt else []
        raw_limit = payload.get("limit", 100)
        try:
            limit = max(1, min(1000, int(raw_limit)))
        except (TypeError, ValueError):
            raise ValueError("limit must be an integer") from None
        status = str(payload.get("status") or "").strip() or None
        if status and status not in {"processing", "accepted", "rejected"}:
            raise ValueError("invalid command receipt status")
        return self.receipts.list(status=status, limit=limit)

    def _control_events(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_after = payload.get("afterId") or payload.get("after_id") or 0
        raw_limit = payload.get("limit", 200)
        try:
            after_id = max(0, int(raw_after))
            limit = max(1, min(1000, int(raw_limit)))
        except (TypeError, ValueError):
            raise ValueError("afterId and limit must be integers") from None
        name = str(payload.get("name") or "").strip() or None
        return self.events.list_since(after_id=after_id, name=name, limit=limit)
