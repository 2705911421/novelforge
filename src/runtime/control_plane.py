"""Host-owned Control Plane dispatch seams.

The buses own dispatch and observation policy; durable task/runtime state stays
in SQLite.  Command receipts and control events are optionally persisted in
SQLite as restart/replay evidence, while listeners remain process-local.
"""

from __future__ import annotations

import asyncio
import inspect
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
)
from .approvals import ApprovalEngine
from .contracts import AgentTask, RuntimeEvent
from .errors import ControlCommandLeaseLost, TaskInterrupted
from .router import RuntimeRouter
from .tool_gateway import PermissionEngine, ToolGateway


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
        return self._execute_sync(command, worker_id=worker_id)

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
        command = ControlCommand(
            name=str(claimed["name"]),
            payload=claimed.get("payload") if isinstance(claimed.get("payload"), Mapping) else {},
            actor=str(claimed.get("actor") or "system"),
            command_id=str(claimed["commandId"]),
        )
        try:
            await self.bus.execute_claimed_async(command, worker_id=self.worker_id)
        except Exception:
            # Handler failures are already durable rejected receipts.  Return
            # the receipt so a supervising loop can observe the failure while
            # keeping the worker alive for the next independent command.
            receipt = self.bus.receipts.get(command.command_id)
            return receipt or claimed
        return self.bus.receipts.get(command.command_id) or claimed

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
    ) -> None:
        self.task_runtime = task_runtime
        self.router = router
        self.agent_tasks = agent_tasks or AgentTaskStore(task_runtime.db)

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
        claimed = self.task_runtime.claim_by_id(
            durable_task_id,
            worker_id,
            lease_seconds=lease_seconds,
        )
        if claimed is None:
            return None

        events: list[RuntimeEvent] = []
        try:
            async for event in self.router.execute(task):
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
        self.queries.register("task.get", self._get_task)
        self.queries.register("task.events", self._task_events)
        self.queries.register("task.agent-task", self._agent_task)
        self.queries.register("task.agent-runs", self._agent_runs)
        self.queries.register("task.domain-events", self._domain_events)
        self.queries.register("task.compute-plans", self._compute_plans)
        self.queries.register("task.context-bundles", self._context_bundles)
        self.queries.register("runtime.ui-events", self._ui_events)
        self.queries.register("approval.get", self._get_approval)
        self.queries.register("approval.list", self._list_approvals)
        self.queries.register("task.tool-calls", self._tool_calls)
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

    def _enqueue(self, payload: Mapping[str, Any], _actor: str) -> dict[str, Any]:
        task_type = str(payload.get("taskType") or payload.get("task_type") or "").strip()
        if not task_type:
            raise ValueError("taskType is required")
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        return self.task_runtime.enqueue(
            task_type,
            project_id=payload.get("projectId") or payload.get("project_id"),
            book_id=payload.get("bookId") or payload.get("book_id"),
            chapter_number=payload.get("chapterNumber") or payload.get("chapter_number"),
            data=data,
            stage=str(payload.get("stage") or "queued"),
            idempotency_key=(
                payload.get("idempotencyKey")
                or payload.get("idempotency_key")
                or payload.get("_commandId")
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
        events = RuntimeEventStore(self.task_runtime.db)
        result: list[dict[str, Any]] = []
        for run in self._agent_runs(payload):
            result.extend(events.domain_events(str(run["id"])))
        return result

    def _compute_plans(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        agent_task = self._agent_task(payload)
        if not agent_task:
            return []
        return ComputePlanStore(self.task_runtime.db).list(str(agent_task["id"]))

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
