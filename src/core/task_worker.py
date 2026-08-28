"""Durable task worker with leases, heartbeats, and process-independent polling."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
from typing import Any, Callable, Optional

from .task_runtime import TaskFailure, TaskRuntime


class PersistentTaskWorker:
    """Execute durable tasks without coupling the task runtime to HTTP.

    The worker's small interface is intentionally the only seam a host needs:
    call :meth:`execute_once` for supervised execution or :meth:`run_forever`
    for a dedicated worker process.  Task claiming, lease renewal, terminal
    state updates, and durable error reporting stay local to this module.
    """

    def __init__(self, runtime: TaskRuntime, handlers: dict[str, Callable[[dict[str, Any]], Any]],
                 *, lease_seconds: int = 60, retry_delay_seconds: int = 5):
        self.runtime = runtime
        self.handlers = handlers
        self.lease_seconds = lease_seconds
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        self.retry_delay_seconds = retry_delay_seconds

    async def execute_once(self, worker_id: str = "studio") -> Optional[dict]:
        task = self.runtime.claim(worker_id, lease_seconds=self.lease_seconds)
        if task is None:
            return None
        return await self.execute_claimed(task, worker_id)

    async def execute_task(self, task_id: str, worker_id: str = "studio") -> Optional[dict]:
        """Claim and execute one specific durable task.

        HTTP integrations use this targeted seam when an author expects an
        immediate response but the provider work must still have a persisted
        Task/GenerationRun owner.  A concurrent worker can win the claim; in
        that case the caller receives ``None`` and can read the task record.
        """
        task = self.runtime.claim_by_id(task_id, worker_id, lease_seconds=self.lease_seconds)
        if task is None:
            return None
        return await self.execute_claimed(task, worker_id)

    async def execute_claimed(self, task: dict[str, Any], worker_id: str = "studio") -> Optional[dict]:
        """Run a task that the caller has already claimed with this owner."""
        if not isinstance(task, dict) or not task.get("id"):
            raise ValueError("a claimed durable task is required")
        handler = self.handlers.get(task["type"])
        if handler is None:
            return self.runtime.transition(task["id"], "needs_author_decision", detail={
                "reason": "no safe persisted handler is registered",
            }, lease_owner=worker_id)
        heartbeat = asyncio.create_task(self._heartbeat(task["id"], worker_id))
        try:
            # The compatibility handlers are intentionally synchronous and
            # run in a worker thread.  That lets the event loop renew a lease
            # while a provider call is in flight.
            result = await asyncio.to_thread(handler, task)
            if inspect.isawaitable(result):
                result = await result
            # A parent orchestrator may have durably scheduled a child and
            # released its own lease.  Do not let the generic worker turn
            # that safe hand-off into a false completed parent task.
            if isinstance(result, dict) and result.get("_defer"):
                child_task_id = result.get("child_task_id")
                if not isinstance(child_task_id, str) or not child_task_id:
                    raise RuntimeError("deferred task did not provide child_task_id")
                return self.runtime.defer_until_child(
                    task["id"],
                    child_task_id,
                    detail=result.get("detail") if isinstance(result.get("detail"), dict) else None,
                    lease_owner=worker_id,
                )
            current = self.runtime.get(task["id"])
            if current and current["status"] == "cancelling":
                self.runtime.checkpoint(task["id"], current.get("stage") or "cancelled",
                                        {"safe_boundary": True}, lease_owner=worker_id)
                return self.runtime.transition(task["id"], "cancelled",
                                               detail={"reason": "cancelled_at_safe_boundary"},
                                               lease_owner=worker_id)
            if current and current["status"] == "running":
                # A handler may complete its provider work but deliberately
                # stop at an author/quality gate.  Preserve that explicit
                # result and make the decision boundary durable; otherwise a
                # generic worker would report a blocked artifact as a false
                # successful task.
                if not isinstance(result, dict) or not result:
                    return self.runtime.transition(
                        task["id"],
                        "failed",
                        detail={"reason": "handler returned no task artifact"},
                        error_code="TASK_RESULT_INVALID",
                        error="task handler must return a non-empty object result",
                        result=result if isinstance(result, dict) else None,
                        lease_owner=worker_id,
                    )
                if isinstance(result, dict) and result.get("completed") is False:
                    return self.runtime.transition(
                        task["id"], "needs_author_decision",
                        detail={
                            "reason": "handler reported incomplete result",
                            "result": result,
                        },
                        error_code="TASK_INCOMPLETE",
                        error=str(result.get("error") or result.get("quality_gate") or "task requires author decision"),
                        result=result,
                        lease_owner=worker_id,
                    )
                reported_status = str(result.get("status") or "").strip().lower()
                if reported_status in {"failed", "error", "incomplete"}:
                    return self.runtime.transition(
                        task["id"],
                        "failed",
                        detail={
                            "reason": "handler reported a failed result",
                            "result": result,
                        },
                        error_code="TASK_RESULT_FAILED",
                        error=str(result.get("error") or reported_status),
                        result=result,
                        lease_owner=worker_id,
                    )
                return self.runtime.transition(
                    task["id"], "completed", detail={"result": result or {}},
                    result=result or {}, lease_owner=worker_id
                )
            return current
        except TaskFailure as exc:
            return self.runtime.fail(
                task["id"], exc.code, str(exc), retryable=exc.retryable,
                retry_delay_seconds=(
                    self.retry_delay_seconds
                    if exc.retry_delay_seconds is None
                    else exc.retry_delay_seconds
                ),
                lease_owner=worker_id
            )
        except Exception as exc:  # Handler errors must be observable and durable.
            code, retryable = self._classify_exception(exc)
            return self.runtime.fail(
                task["id"], code, str(exc), retryable=retryable,
                retry_delay_seconds=self.retry_delay_seconds,
                lease_owner=worker_id,
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def run_forever(self, worker_id: str = "novelforge-worker", *, poll_interval: float = 0.25,
                          stop_event: Optional[asyncio.Event] = None) -> None:
        """Recover expired leases and continuously claim durable work.

        A process restart is safe because this loop has no in-memory task
        state.  A caller may pass ``stop_event`` for supervised tests or a
        graceful application shutdown.
        """
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        stop_event = stop_event or asyncio.Event()
        self.runtime.recover_expired_leases()
        while not stop_event.is_set():
            task = await self.execute_once(worker_id)
            if task is None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                except asyncio.TimeoutError:
                    continue

    async def _heartbeat(self, task_id: str, worker_id: str) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            if not self.runtime.renew_lease(task_id, worker_id, lease_seconds=self.lease_seconds):
                return

    @staticmethod
    def _classify_exception(exc: Exception) -> tuple[str, bool]:
        """Map provider-shaped failures to the durable task error contract.

        Legacy adapters currently surface provider failures as ``RuntimeError``
        strings, so this boundary must classify both their HTTP status text and
        normal transport wording.  Unknown handler defects remain explicitly
        non-retryable instead of being retried as if they were network errors.
        """
        explicit_code = getattr(exc, "code", None)
        if isinstance(explicit_code, str) and explicit_code:
            explicit_retryable = getattr(exc, "retryable", None)
            if isinstance(explicit_retryable, bool):
                return explicit_code, explicit_retryable
            return explicit_code, explicit_code in {"RATE_LIMIT", "NETWORK", "PROVIDER_TRANSIENT"}
        message = str(exc).lower()
        code_token = re.match(r"^\s*([A-Z][A-Z0-9_]+)\s*:", str(exc))
        if code_token and code_token.group(1).startswith(("SIMULATION_", "MODEL_", "PROVIDER_")):
            return code_token.group(1), False
        status = re.search(r"\b([1-5]\d\d)\b", message)
        status_code = int(status.group(1)) if status else None
        if status_code in {401, 403} or "unauthorized" in message or "forbidden" in message:
            return "MODEL_CONFIGURATION", False
        if status_code == 429 or "rate limit" in message or "too many requests" in message:
            return "RATE_LIMIT", True
        if status_code is not None and 500 <= status_code <= 599:
            return "PROVIDER_TRANSIENT", True
        if any(term in message for term in ("timeout", "connection", "network", "dns")):
            return "NETWORK", True
        return "HANDLER_ERROR", False
