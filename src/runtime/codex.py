"""Codex App Server runtime adapter.

The adapter speaks the documented newline-delimited JSON-RPC App Server
protocol over a supervised ``codex app-server`` subprocess.  It never reads a
credential from a Codex config file and never treats a provider thread as
NovelForge task state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.context.bundles import ContextBundleStore

from .contracts import (
    AgentRunStatus,
    AgentTask,
    AuthState,
    ComputePlan,
    ModelDescriptor,
    RuntimeCapabilities,
    RuntimeEvent,
    UsageSnapshot,
)
from .errors import RuntimeCrashed, RuntimeUnavailable, TaskInterrupted
from .persistence import AgentRunStore
from .process import resolve_executable_argv
from .tool_gateway import ToolCallContext, ToolGateway


logger = logging.getLogger(__name__)


_CODEX_STDERR_DIAGNOSTIC_CHARS = 16_000
_CODEX_PROCESS_READ_CHUNK = 64 * 1024
_CODEX_MAX_PROTOCOL_LINE_CHARS = 8 * 1024 * 1024
_CODEX_MAX_ARTIFACT_CHARS = 2_000_000


class CodexProcessManager:
    """Small synchronous JSONL process seam, safe to call via ``to_thread``."""

    def __init__(
        self,
        *,
        command: Sequence[str] = ("codex", "app-server"),
        cwd: str | Path | None = None,
        popen_factory: Callable[..., Any] | None = None,
        max_protocol_line_chars: int = _CODEX_MAX_PROTOCOL_LINE_CHARS,
    ) -> None:
        if max_protocol_line_chars <= 0:
            raise ValueError("max_protocol_line_chars must be positive")
        self.command = tuple(command)
        self.cwd = str(cwd) if cwd else None
        self._popen_factory = popen_factory or subprocess.Popen
        self.max_protocol_line_chars = int(max_protocol_line_chars)
        self._process: Any | None = None
        self._next_id = 1
        self._pending_notifications: list[dict[str, Any]] = []
        self._ignored_response_ids: set[str | int] = set()
        self._lock = threading.RLock()
        self._stderr_drain_thread: threading.Thread | None = None
        self._stderr_excerpt = b""
        # Only one ordinary request may consume the response stream at a
        # time.  Unlike ``_lock``, this lock is not held while a request waits
        # on stdout, so cancellation can still close or interrupt the process.
        self._request_lock = threading.Lock()

    @property
    def process(self) -> Any | None:
        return self._process

    def start(self) -> None:
        with self._request_lock:
            with self._lock:
                if self._process is not None and self._process.poll() is None:
                    return
                try:
                    launch_command = resolve_executable_argv(self.command)
                    self._process = self._popen_factory(
                        list(launch_command),
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=self.cwd,
                        text=True,
                        encoding="utf-8",
                        bufsize=1,
                    )
                    self._stderr_excerpt = b""
                    stderr = getattr(self._process, "stderr", None)
                    if stderr is not None and hasattr(stderr, "read"):
                        process = self._process
                        self._stderr_drain_thread = threading.Thread(
                            target=self._drain_stderr,
                            args=(process, stderr),
                            name="novelforge-codex-stderr",
                            daemon=True,
                        )
                        self._stderr_drain_thread.start()
                except Exception as exc:
                    self.close()
                    raise RuntimeCrashed("failed to start Codex App Server", details={"detail": str(exc)}) from exc
            try:
                self._request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "novelforge",
                            "title": "NovelForge",
                            "version": "runtime-plane-v1",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                )
                self._notify("initialized", {})
            except Exception as exc:
                self.close()
                raise RuntimeCrashed("failed to start Codex App Server", details={"detail": str(exc)}) from exc

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._request_lock:
            return self._request(method, params or {})

    def send_request(self, method: str, params: Mapping[str, Any] | None = None) -> str | int:
        """Send a request whose response will be consumed by the event reader.

        Cancellation is initiated from a second coroutine while the runtime
        is blocked reading turn events.  Waiting for the interrupt response in
        that coroutine would create a competing stdout reader, so the event
        loop owns the response and discards it after it arrives.
        """
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            self._ignored_response_ids.add(request_id)
            self._write({"id": request_id, "method": method, "params": dict(params or {})})
            return request_id

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        with self._lock:
            self._notify(method, params or {})

    def respond(
        self,
        request_id: str | int,
        *,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        """Answer a server-initiated JSON-RPC request.

        App Server tool calls and approval requests are requests in the
        opposite direction from the normal Host-to-runtime flow.  Keeping the
        response write behind the process seam makes it impossible for a
        runtime adapter to bypass the JSON-RPC envelope or write to stdout
        without synchronization.
        """
        if result is not None and error is not None:
            raise ValueError("a JSON-RPC response cannot contain both result and error")
        message: dict[str, Any] = {"id": request_id}
        if error is not None:
            message["error"] = dict(error)
        else:
            message["result"] = dict(result or {})
        with self._lock:
            self._write(message)

    def read_message(self) -> dict[str, Any]:
        # Do not hold the process lock while blocking on stdout.  A concurrent
        # cancel() must be able to acquire the lock and send turn/interrupt to
        # an App Server whose current turn is waiting for input.
        with self._lock:
            if self._pending_notifications:
                return self._pending_notifications.pop(0)
        return self._read_from_stdout()

    def close(self) -> None:
        stderr_thread: threading.Thread | None
        with self._lock:
            process = self._process
            self._process = None
            stderr_thread = self._stderr_drain_thread
            self._stderr_drain_thread = None
            self._pending_notifications.clear()
            self._ignored_response_ids.clear()
            if process is not None:
                for stream_name in ("stdin", "stdout", "stderr"):
                    stream = getattr(process, stream_name, None)
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception as exc:
                        logger.debug("Codex %s stream close failed during shutdown: %s", stream_name, exc)
                try:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=2)
                except Exception as exc:
                    logger.debug("Codex process termination failed during shutdown: %s", exc)
                    try:
                        process.kill()
                    except Exception as kill_exc:
                        logger.warning("Codex process force-close failed during shutdown: %s", kill_exc)
        if stderr_thread is not None and stderr_thread is not threading.current_thread():
            stderr_thread.join(timeout=1)

    def _drain_stderr(self, process: Any, stream: Any) -> None:
        """Drain Codex diagnostics so a noisy child cannot block stdout."""
        retained = bytearray()
        retained_limit = _CODEX_STDERR_DIAGNOSTIC_CHARS + 1
        try:
            while True:
                chunk = stream.read(_CODEX_PROCESS_READ_CHUNK)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                if not isinstance(chunk, (bytes, bytearray)):
                    break
                if len(retained) < retained_limit:
                    retained.extend(chunk[: retained_limit - len(retained)])
        except Exception as exc:
            # Closing a blocked pipe is the normal shutdown wake-up path.
            logger.debug("Codex stderr drain stopped while closing the process: %s", exc)
            return
        with self._lock:
            if self._process is process:
                self._stderr_excerpt = bytes(retained)

    def _request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
        self._write({"id": request_id, "method": method, "params": dict(params)})
        while True:
            # A notification read while waiting for a response must be held
            # for the event consumer, but must not be returned to this same
            # request on the next loop.  Reading through ``_read_message``
            # would pop the pending notification repeatedly and could hang
            # forever before the response is observed.
            message = self._read_from_stdout()
            if self._consume_ignored_response(message):
                continue
            if message.get("id") == request_id:
                if message.get("error"):
                    raise RuntimeCrashed(
                        f"Codex App Server rejected {method}",
                        details={"method": method, "error": message.get("error")},
                    )
                return message
            if message.get("method"):
                with self._lock:
                    self._pending_notifications.append(message)

    def consume_ignored_response(self, message: Mapping[str, Any]) -> bool:
        """Discard a response for a request sent with :meth:`send_request`."""
        return self._consume_ignored_response(message)

    def _consume_ignored_response(self, message: Mapping[str, Any]) -> bool:
        request_id = message.get("id")
        if request_id is None:
            return False
        with self._lock:
            if request_id not in self._ignored_response_ids:
                return False
            self._ignored_response_ids.remove(request_id)
            return True

    def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        self._write({"method": method, "params": dict(params)})

    def _write(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            raise RuntimeCrashed("Codex App Server process is not running")
        stdin = getattr(process, "stdin", None)
        if stdin is None:
            raise RuntimeCrashed("Codex App Server stdin is unavailable")
        stdin.write(json.dumps(dict(message), ensure_ascii=False) + "\n")
        stdin.flush()

    def _read_from_stdout(self) -> dict[str, Any]:
        process = self._process
        stdout = getattr(process, "stdout", None) if process is not None else None
        if stdout is None:
            raise RuntimeCrashed("Codex App Server stdout is unavailable")
        line = stdout.readline(self.max_protocol_line_chars + 1)
        if not line:
            detail = "process exited" if process is None or process.poll() is not None else "stdout closed"
            raise RuntimeCrashed(f"Codex App Server {detail}")
        if len(line) > self.max_protocol_line_chars:
            raise RuntimeCrashed(
                "Codex App Server protocol message exceeds the configured size limit",
                details={"maxChars": self.max_protocol_line_chars},
            )
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeCrashed("Codex App Server returned invalid JSON", details={"line": line[:500]}) from exc
        if not isinstance(message, dict):
            raise RuntimeCrashed("Codex App Server returned a non-object message")
        return message


class CodexRuntime:
    """NovelForge IAgentRuntime adapter backed by Codex App Server."""

    runtime_type = "codex-app-server"

    def __init__(
        self,
        runs: AgentRunStore,
        *,
        process: CodexProcessManager | None = None,
        command: Sequence[str] | None = None,
        cwd: str | Path | None = None,
        models: Sequence[ModelDescriptor] | None = None,
        tool_gateway: ToolGateway | None = None,
        cancel_grace_seconds: float = 2.0,
    ) -> None:
        if cancel_grace_seconds < 0:
            raise ValueError("cancel_grace_seconds must be non-negative")
        self.runs = runs
        self.cancel_grace_seconds = float(cancel_grace_seconds)
        self._context_bundles = ContextBundleStore(runs.db)
        self.process = process or CodexProcessManager(
            command=tuple(command or ("codex", "app-server")),
            cwd=cwd,
        )
        self.tool_gateway = tool_gateway
        self._models = tuple(models or (
            ModelDescriptor(
                runtime_type=self.runtime_type,
                model_id="codex-default",
                display_name="Codex App Server 默认模型",
                capabilities={"agent": "full", "tools": "available"},
                reasoning_levels=("medium", "high", "xhigh"),
                context_window=256_000,
                capability_profile={
                    "extraction": "C4", "planning": "C4", "writing": "C4",
                    "review": "C4", "long_context": "C4", "tool_use": "C4",
                    "structured_output": "C4", "revision": "C4", "consistency": "C4",
                },
            ),
        ))
        self._threads: dict[str, str] = {}
        self._turns: dict[str, tuple[str, str]] = {}
        self._durable_task_ids: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._cancel_ack_events: dict[str, threading.Event] = {}
        # App Server exposes one ordered stdout stream.  Until the Host owns
        # a multiplexing reader, serialize turns so one AgentRun cannot
        # consume another AgentRun's notifications.
        self._execution_lock = asyncio.Lock()
        self._capabilities = RuntimeCapabilities(
            runtime_type=self.runtime_type,
            streaming=True,
            sessions=True,
            tools=tool_gateway is not None,
            approvals=tool_gateway is not None,
            pause_resume=False,
            models=self._models,
            integration_grade="S",
        )

    async def initialize(self, config: Mapping[str, Any] | None = None) -> RuntimeCapabilities:
        await self._process_call(self.process.start)
        return self._capabilities

    async def authenticate(self) -> AuthState:
        # Authentication is deliberately delegated to the official Codex
        # process.  The adapter does not scrape local credential stores or
        # reproduce OAuth.  ``account/read`` is the App Server's own
        # authenticated-state query.
        await self._process_call(self.process.start)
        response = await self._process_call(
            self.process.request,
            "account/read",
            {"refreshToken": False},
        )
        result = response.get("result") if isinstance(response, Mapping) else None
        result = result if isinstance(result, Mapping) else {}
        account = result.get("account")
        if not isinstance(account, Mapping):
            detail = "Codex App Server reports no authenticated account"
            if result.get("requiresOpenaiAuth"):
                detail = "Codex App Server requires official OpenAI authentication"
            return AuthState("not_authenticated", detail=detail)
        account_type = str(account.get("type") or "codex")
        label = account.get("email") or account_type
        return AuthState("authenticated", account_label=str(label), detail="account/read")

    async def execute(self, task: AgentTask, compute_plan: ComputePlan):
        async with self._execution_lock:
            async for event in self._execute_serial(task, compute_plan):
                yield event

    async def _execute_serial(self, task: AgentTask, compute_plan: ComputePlan):
        link = self.runs.db.fetchone("SELECT task_id FROM agent_tasks WHERE id=?", (task.task_id,)) or {}
        durable_task_id = str(
            link.get("task_id") or task.input_payload.get("durableTaskId") or task.task_id
        )
        session_key = self._session_key(task)
        context_manifest = task.input_payload.get("contextManifest")
        context_bundle_id = task.context_bundle_id
        if not isinstance(context_manifest, dict) and context_bundle_id is None:
            context_manifest = self._context_bundles.manifest_for_task(
                durable_task_id=durable_task_id,
                task_stage=task.task_type,
                role=task.role,
                task=task,
                source="CodexRuntime",
            )
        if isinstance(context_manifest, dict):
            task_row = self.runs.db.fetchone(
                "SELECT project_id, book_id FROM tasks WHERE id=?", (durable_task_id,)
            ) or {}
            persisted = self.runs.db.fetchone(
                "SELECT context_bundle_id FROM agent_tasks WHERE id=?", (task.task_id,)
            ) or {}
            bound_context_id = str(
                context_bundle_id or persisted.get("context_bundle_id") or ""
            ).strip() or None
            requested_context_id = str(
                context_manifest.get("bundleId") or context_manifest.get("contextBundleId") or ""
            ).strip() or None
            if bound_context_id is not None:
                if requested_context_id is not None and requested_context_id != bound_context_id:
                    raise ValueError("Codex ContextBundle does not match the persisted AgentTask")
                context_bundle_id = bound_context_id
            elif requested_context_id is not None and self._context_bundles.get(requested_context_id) is not None:
                context_bundle_id = requested_context_id
            else:
                bundle = self._context_bundles.create_from_manifest(
                    context_manifest,
                    project_id=context_manifest.get("projectId") or task_row.get("project_id") or task.project_id,
                    book_id=context_manifest.get("bookId") or task_row.get("book_id"),
                    source="CodexRuntime",
                    task_id=durable_task_id,
                    role=task.role,
                    expected_project_id=task_row.get("project_id") or task.project_id,
                    expected_book_id=task_row.get("book_id"),
                )
                context_bundle_id = bundle.bundle_id
                self.runs.db.execute(
                    "UPDATE agent_tasks SET context_bundle_id=COALESCE(context_bundle_id, ?), "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (context_bundle_id, task.task_id),
                )
                persisted = self.runs.db.fetchone(
                    "SELECT context_bundle_id FROM agent_tasks WHERE id=?", (task.task_id,)
                ) or {}
                context_bundle_id = str(
                    persisted.get("context_bundle_id") or context_bundle_id
                ).strip() or None
        recovery = self._recovery_envelope(
            durable_task_id,
            task,
            context_bundle_id=context_bundle_id,
        )
        run = self.runs.create(
            task=task,
            durable_task_id=durable_task_id,
            compute_plan=compute_plan,
            context_bundle_id=context_bundle_id,
            prompt_version=str(
                task.input_payload.get("promptVersion")
                or ("codex-app-server-1-recovery" if recovery else "codex-app-server-1")
            ),
        )
        run_id = str(run["id"])
        self._durable_task_ids[session_key] = durable_task_id
        cancel_ack = threading.Event()
        self._cancel_ack_events[session_key] = cancel_ack
        if task.task_id in self._cancelled or durable_task_id in self._cancelled:
            self.runs.transition(run_id, AgentRunStatus.INTERRUPTED.value,
                                 error_code="TASK_INTERRUPTED", error_detail="cancel requested before start")
            self._durable_task_ids.pop(session_key, None)
            self._cancelled.discard(task.task_id)
            self._cancelled.discard(durable_task_id)
            raise TaskInterrupted("task was cancelled before Codex execution")
        try:
            if recovery:
                yield RuntimeEvent(
                    runtime_type=self.runtime_type,
                    event_type="recovery.started",
                    payload=recovery,
                    agent_run_id=run_id,
                )
            await self._process_call(self.process.start)
            thread_id = self._threads.get(session_key)
            if not thread_id:
                thread_response = await self._process_call(
                    self.process.request,
                    "thread/start",
                    self._thread_start_params(task, compute_plan),
                )
                thread_id = self._extract_id(thread_response, "thread")
                self._threads[session_key] = thread_id
            self.runs.transition(run_id, AgentRunStatus.RUNNING.value, runtime_thread_id=thread_id)
            yield RuntimeEvent(
                    runtime_type=self.runtime_type,
                    event_type="thread.started",
                    payload={"threadId": thread_id, "recovered": bool(recovery)},
                    agent_run_id=run_id,
                )
            if task.task_id in self._cancelled or durable_task_id in self._cancelled:
                self.runs.transition(
                    run_id,
                    AgentRunStatus.INTERRUPTED.value,
                    error_code="TASK_INTERRUPTED",
                    error_detail="cancel requested before turn start",
                )
                raise TaskInterrupted("task cancellation requested")
            prompt = self._prompt(task, recovery=recovery)
            turn_response = await self._process_call(
                self.process.request,
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "model": compute_plan.model_id,
                    # The current App Server protocol calls this field
                    # ``effort``; ``reasoning`` belongs to NovelForge's
                    # provider-neutral ComputePlan vocabulary.
                    "effort": compute_plan.reasoning,
                },
            )
            turn_id = self._extract_id(turn_response, "turn")
            self._turns[session_key] = (thread_id, turn_id)
            self.runs.transition(run_id, AgentRunStatus.RUNNING.value, runtime_turn_id=turn_id)
            if task.task_id in self._cancelled or durable_task_id in self._cancelled:
                await self.cancel(durable_task_id)
                self.runs.transition(
                    run_id,
                    AgentRunStatus.INTERRUPTED.value,
                    error_code="TASK_INTERRUPTED",
                    error_detail="cancel requested before turn event loop",
                )
                raise TaskInterrupted("task cancellation requested")
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="turn.started",
                payload={"threadId": thread_id, "turnId": turn_id},
                agent_run_id=run_id,
            )
            delta_fragments: list[str] = []
            completed_item_fragments: list[str] = []
            delta_seen = False
            delta_chars = 0
            completed_item_chars = 0
            while True:
                if task.task_id in self._cancelled or durable_task_id in self._cancelled:
                    self.runs.transition(run_id, AgentRunStatus.INTERRUPTED.value,
                                         error_code="TASK_INTERRUPTED", error_detail="cancel requested")
                    raise TaskInterrupted("task cancellation requested")
                message = await self._process_call(self.process.read_message)
                if self.process.consume_ignored_response(message):
                    continue
                if message.get("error"):
                    raise RuntimeCrashed("Codex App Server runtime error", details={"error": message["error"]})
                method = str(message.get("method") or "")
                if not method:
                    continue
                if "id" in message:
                    event = await self._handle_server_request(message, task, run_id)
                    yield event
                    continue
                payload = message.get("params") or message.get("result") or {}
                event_type = self._event_type(method, payload)
                normalized_payload = payload if isinstance(payload, Mapping) else {"value": payload}
                terminal_events = {"turn.completed", "turn.complete", "turn.failed", "turn.cancelled"}
                if event_type not in terminal_events:
                    fragment = self._output_fragment(event_type, normalized_payload)
                    if fragment:
                        if self._is_delta_event(event_type):
                            delta_seen = True
                            if delta_chars < _CODEX_MAX_ARTIFACT_CHARS:
                                fragment = fragment[:_CODEX_MAX_ARTIFACT_CHARS - delta_chars]
                                delta_fragments.append(fragment)
                                delta_chars += len(fragment)
                        elif not delta_seen and completed_item_chars < _CODEX_MAX_ARTIFACT_CHARS:
                            fragment = fragment[:_CODEX_MAX_ARTIFACT_CHARS - completed_item_chars]
                            completed_item_fragments.append(fragment)
                            completed_item_chars += len(fragment)
                if event_type in {"turn.completed", "turn.complete"}:
                    fragments = delta_fragments if delta_seen else completed_item_fragments
                    artifact = self._completion_artifact(normalized_payload, fragments)
                    normalized_payload = dict(normalized_payload)
                    normalized_payload["artifact"] = artifact
                yield RuntimeEvent(
                    runtime_type=self.runtime_type,
                    event_type=event_type,
                    payload=normalized_payload,
                    agent_run_id=run_id,
                )
                if event_type in terminal_events:
                    artifacts = normalized_payload
                    status = {
                        "turn.completed": AgentRunStatus.SUCCEEDED.value,
                        "turn.complete": AgentRunStatus.SUCCEEDED.value,
                        "turn.cancelled": AgentRunStatus.CANCELLED.value,
                        "turn.failed": AgentRunStatus.FAILED.value,
                    }[event_type]
                    cancel_ack.set()
                    usage = self._usage(payload)
                    self.runs.transition(
                        run_id,
                        status,
                        usage=usage or None,
                        artifacts=artifacts,
                    )
                    return
        except asyncio.CancelledError:
            current = self.runs.get(run_id) or {}
            if current.get("status") in {AgentRunStatus.RUNNING.value, AgentRunStatus.PAUSED.value}:
                self.runs.transition(
                    run_id,
                    AgentRunStatus.INTERRUPTED.value,
                    error_code="TASK_CANCELLED",
                    error_detail="runtime coroutine cancelled",
                )
            raise
        except TaskInterrupted:
            raise
        except Exception as exc:
            current = self.runs.get(run_id) or {}
            cancellation_requested = (
                task.task_id in self._cancelled or durable_task_id in self._cancelled
            )
            if cancellation_requested:
                if current.get("status") in {
                    AgentRunStatus.CREATED.value,
                    AgentRunStatus.RUNNING.value,
                    AgentRunStatus.PAUSED.value,
                }:
                    self.runs.transition(
                        run_id,
                        AgentRunStatus.INTERRUPTED.value,
                        error_code="TASK_INTERRUPTED",
                        error_detail="provider interrupt did not produce a terminal event",
                    )
                raise TaskInterrupted("task cancellation requested") from exc
            if current.get("status") in {
                AgentRunStatus.CREATED.value,
                AgentRunStatus.RUNNING.value,
                AgentRunStatus.PAUSED.value,
            }:
                self.runs.transition(run_id, AgentRunStatus.INTERRUPTED.value,
                                     error_code=str(getattr(exc, "code", None) or "RUNTIME_CRASHED"),
                                     error_detail=str(exc))
            # A protocol/transport failure invalidates the process session.
            # Leaving a malformed or half-closed process attached would make
            # the next AgentTask reuse corrupted runtime state instead of
            # exercising the supervised restart path.
            try:
                await asyncio.to_thread(self.process.close)
            except Exception as cleanup_exc:
                # The original runtime error is the durable failure; cleanup
                # is best effort because the process may already have exited,
                # but the cleanup failure remains visible to host logs.
                logger.warning("Codex runtime cleanup failed after execution error", exc_info=cleanup_exc)
            self._threads.pop(session_key, None)
            self._turns.pop(session_key, None)
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="error",
                payload={"code": str(getattr(exc, "code", None) or "RUNTIME_CRASHED"), "detail": str(exc)},
                agent_run_id=run_id,
            )
            if isinstance(exc, RuntimeCrashed):
                raise
            raise RuntimeCrashed("Codex App Server execution failed", details={"detail": str(exc)}) from exc
        finally:
            self._turns.pop(session_key, None)
            self._durable_task_ids.pop(session_key, None)
            self._cancel_ack_events.pop(session_key, None)
            self._cancelled.discard(task.task_id)
            self._cancelled.discard(durable_task_id)

    async def pause(self, task_id: str) -> None:
        raise RuntimeUnavailable("Codex App Server pause/resume is not enabled by this adapter")

    async def resume(self, task_id: str) -> None:
        raise RuntimeUnavailable("Codex App Server pause/resume is not enabled by this adapter")

    async def cancel(self, task_id: str) -> None:
        self._cancelled.add(task_id)
        session_key = task_id
        if session_key not in self._turns:
            session_key = next(
                (
                    candidate
                    for candidate, durable_task_id in self._durable_task_ids.items()
                    if durable_task_id == task_id
                ),
                task_id,
            )
        turn = self._turns.get(session_key)
        if turn and self.process.process is not None:
            thread_id, turn_id = turn
            try:
                # The official App Server protocol exposes an explicit
                # interrupt request.  The durable TaskRuntime remains the
                # final boundary if the subprocess is unavailable.
                await asyncio.to_thread(
                    self.process.send_request,
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                )
            except Exception as exc:
                # Durable TaskRuntime cancellation remains authoritative, but
                # a failed provider interrupt must stay visible to operators.
                logger.warning("Codex interrupt request failed", exc_info=exc)
            cancel_ack = self._cancel_ack_events.get(session_key)
            if cancel_ack is not None and not cancel_ack.is_set():
                acknowledged = await asyncio.to_thread(
                    cancel_ack.wait,
                    self.cancel_grace_seconds,
                )
                if not acknowledged and not cancel_ack.is_set():
                    # One App Server connection can host multiple sessions;
                    # force-close only when this is the sole active turn so a
                    # stuck provider cannot cancel unrelated AgentRuns.
                    other_active_turn = any(
                        key != session_key for key in self._turns
                    )
                    if not other_active_turn:
                        await asyncio.to_thread(self.process.close)
                    else:
                        logger.warning(
                            "Codex interrupt was not acknowledged; preserving shared App Server for other turns"
                        )

    async def get_models(self) -> Sequence[ModelDescriptor]:
        return self._models

    async def get_capabilities(self) -> RuntimeCapabilities:
        return self._capabilities

    async def get_usage(self) -> UsageSnapshot:
        return self.runs.usage_snapshot(self.runtime_type)

    async def shutdown(self) -> None:
        await asyncio.to_thread(self.process.close)
        self._threads.clear()
        self._turns.clear()
        self._durable_task_ids.clear()
        self._cancel_ack_events.clear()
        self._cancelled.clear()

    async def _process_call(self, function: Callable[..., Any], *args: Any) -> Any:
        """Run a blocking process operation and close it if its task is cancelled."""
        try:
            return await asyncio.to_thread(function, *args)
        except asyncio.CancelledError:
            # ``to_thread`` cannot cancel a blocking ``readline``.  Closing
            # the process is the explicit wake-up path for the reader thread;
            # the next call then observes RuntimeCrashed and the AgentRun is
            # recovered by the normal runtime error path.
            await asyncio.to_thread(self.process.close)
            raise

    @staticmethod
    def _session_key(task: AgentTask) -> str:
        """Scope provider threads by an explicit Host conversation boundary."""
        scope = task.input_payload.get("runtimeSessionKey")
        if not isinstance(scope, str) or not scope.strip():
            scope = task.input_payload.get("runtime_session_key")
        scope = str(scope or "").strip()
        return f"{task.task_id}:{scope}" if scope else task.task_id

    def _thread_start_params(self, task: AgentTask, plan: ComputePlan) -> dict[str, Any]:
        """Build the provider request from the Host-owned task envelope.

        Codex's native shell/file tools are kept in a read-only sandbox.  The
        only tools that can have NovelForge meaning are dynamic tools exposed
        by ``ToolGateway``; their handlers never receive a database handle.
        """
        params: dict[str, Any] = {
            "model": plan.model_id,
            "sandbox": "read-only",
            "approvalPolicy": "never",
        }
        tools = self._dynamic_tool_specs(task)
        if tools:
            params["dynamicTools"] = tools
        return params

    def _dynamic_tool_specs(self, task: AgentTask) -> list[dict[str, Any]]:
        if self.tool_gateway is None:
            return []
        specs: list[dict[str, Any]] = []
        for definition in self.tool_gateway.catalog(task, include_compute=True):
            input_schema = definition.get("inputSchema")
            if not isinstance(input_schema, Mapping):
                input_schema = {"type": "object"}
            specs.append({
                "type": "function",
                "name": str(definition.get("name") or ""),
                "description": str(definition.get("description") or "NovelForge tool"),
                "inputSchema": dict(input_schema),
            })
        return [item for item in specs if item["name"]]

    def _recovery_envelope(
        self,
        durable_task_id: str,
        task: AgentTask,
        *,
        context_bundle_id: str | None,
    ) -> dict[str, Any] | None:
        """Build a read-only resume envelope after a provider thread is lost.

        The provider thread is intentionally disposable.  A retry of the same
        durable task starts a fresh thread and receives only Host-owned
        checkpoint/context provenance; it never receives a database handle or a
        permission to turn this envelope into a Canon mutation.
        """
        previous = None
        for candidate in reversed(self.runs.list_for_task(durable_task_id)):
            status = str(candidate.get("status") or "")
            error_code = str(candidate.get("error_code") or "").upper()
            if not candidate.get("runtime_thread_id"):
                continue
            if status not in {AgentRunStatus.INTERRUPTED.value, AgentRunStatus.FAILED.value}:
                continue
            if error_code in {"TASK_CANCELLED", "TASK_INTERRUPTED"}:
                continue
            previous = candidate
            break
        if previous is None:
            return None

        checkpoint = self.runs.db.fetchone(
            "SELECT id, stage, state, created_at FROM task_checkpoints "
            "WHERE task_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
            (durable_task_id,),
        )
        checkpoint_payload: dict[str, Any] | None = None
        if checkpoint is not None:
            try:
                state = json.loads(checkpoint.get("state") or "{}")
            except (TypeError, json.JSONDecodeError):
                state = {}
            checkpoint_payload = {
                "id": checkpoint.get("id"),
                "stage": checkpoint.get("stage"),
                "state": state if isinstance(state, Mapping) else {},
                "createdAt": checkpoint.get("created_at"),
            }

        context_bundle = None
        if context_bundle_id:
            context_bundle = self._context_bundles.get(context_bundle_id)

        envelope: dict[str, Any] = {
            "durableTaskId": durable_task_id,
            "recoveredFromAgentRunId": previous.get("id"),
            "previousRuntimeThreadId": previous.get("runtime_thread_id"),
            "checkpoint": checkpoint_payload,
            "contextBundleId": context_bundle_id,
            "authority": {
                "boundary": "NovelForge Authority DB",
                "canonicalWrites": "StoryCommit through the Host Gate only",
                "canonCommit": context_bundle.canon_commit if context_bundle else None,
            },
        }
        if context_bundle is not None:
            envelope["contextBundle"] = context_bundle.manifest()
        return envelope

    @staticmethod
    def _event_type(method: str, payload: Any) -> str:
        """Normalize common App Server status notifications to turn events."""
        event_type = method.replace("/", ".")
        if event_type not in {"turn.status", "turn.status.changed"}:
            return event_type
        status = payload.get("status") if isinstance(payload, Mapping) else None
        status = str(status or "").strip().lower().replace("_", ".")
        return {
            "completed": "turn.completed",
            "complete": "turn.completed",
            "failed": "turn.failed",
            "cancelled": "turn.cancelled",
            "canceled": "turn.cancelled",
            "interrupted": "turn.cancelled",
        }.get(status, event_type)

    @classmethod
    def _output_fragment(cls, event_type: str, payload: Mapping[str, Any]) -> str:
        """Extract text only from output-shaped App Server notifications."""
        marker = event_type.replace("/", ".").lower()
        output_event = any(
            token in marker
            for token in ("agent_message", "agent-message", "assistant_message", "output_text")
        )
        if not output_event and not cls._is_delta_event(event_type):
            output_event = cls._contains_output_item(payload)
        if not output_event:
            return ""
        return cls._extract_text_value(payload)

    @staticmethod
    def _is_delta_event(event_type: str) -> bool:
        marker = event_type.replace("/", ".").lower()
        return marker.endswith(".delta") or ".delta." in marker

    @classmethod
    def _contains_output_item(cls, value: Any, *, depth: int = 0) -> bool:
        if depth > 5:
            return False
        if isinstance(value, Mapping):
            raw_type = str(value.get("type") or value.get("itemType") or "")
            item_type = raw_type.replace("-", "_").replace(".", "_").lower()
            if item_type in {"agent_message", "assistant_message", "output_text", "text"}:
                return True
            return any(
                cls._contains_output_item(value[key], depth=depth + 1)
                for key in ("item", "message", "output", "content")
                if key in value
            )
        if isinstance(value, (list, tuple)):
            return any(cls._contains_output_item(item, depth=depth + 1) for item in value)
        return False

    @classmethod
    def _extract_text_value(cls, value: Any, *, depth: int = 0) -> str:
        if depth > 6:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            for key in (
                "text", "delta", "output_text", "outputText", "content", "message",
                "artifact", "item", "result",
            ):
                if key in value:
                    text = cls._extract_text_value(value[key], depth=depth + 1)
                    if text:
                        return text
            return ""
        if isinstance(value, (list, tuple)):
            return "".join(cls._extract_text_value(item, depth=depth + 1) for item in value)
        return ""

    @classmethod
    def _completion_artifact(
        cls,
        payload: Mapping[str, Any],
        fragments: Sequence[str],
    ) -> dict[str, Any]:
        raw_artifact = payload.get("artifact")
        if isinstance(raw_artifact, Mapping):
            artifact = dict(raw_artifact)
        else:
            artifact = {}
        content = cls._extract_text_value(artifact)
        if not content and raw_artifact is not None and not isinstance(raw_artifact, Mapping):
            content = cls._extract_text_value(raw_artifact)
        if not content:
            content = cls._extract_text_value(payload)
        if not content:
            content = "".join(fragments)
        if not content.strip():
            raise RuntimeCrashed("Codex App Server returned an empty artifact")
        artifact["content"] = content[:_CODEX_MAX_ARTIFACT_CHARS]
        artifact.setdefault("contentType", "text")
        for key in ("model", "provider", "finishReason", "mimeType"):
            if key not in artifact and payload.get(key) is not None:
                artifact[key] = payload[key]
        return artifact

    @staticmethod
    def _usage(payload: Any) -> dict[str, Any]:
        """Extract only vendor-reported usage fields from a completion event."""
        if not isinstance(payload, Mapping):
            return {}
        raw = payload.get("usage") or payload.get("tokenUsage") or payload.get("token_usage")
        if not isinstance(raw, Mapping):
            return {}
        result: dict[str, Any] = {}
        aliases = {
            "inputTokens": ("inputTokens", "input_tokens", "promptTokens", "prompt_tokens"),
            "outputTokens": ("outputTokens", "output_tokens", "completionTokens", "completion_tokens"),
            "totalTokens": ("totalTokens", "total_tokens"),
            "computeUnits": ("computeUnits", "compute_units"),
            "actualCost": ("actualCost", "actual_cost", "cost"),
        }
        for target, keys in aliases.items():
            for key in keys:
                if raw.get(key) is not None:
                    result[target] = raw[key]
                    break
        return result

    async def _handle_server_request(
        self,
        message: Mapping[str, Any],
        task: AgentTask,
        run_id: str,
    ) -> RuntimeEvent:
        """Handle App Server callbacks without confusing them with events."""
        raw_id = message.get("id")
        # JSON-RPC responses require the exact request id; a malformed message
        # without a usable id still gets a response so the child never wedges.
        request_id: str | int = raw_id if isinstance(raw_id, (str, int)) else "unknown"
        method = str(message.get("method") or "")
        params = message.get("params")
        params = params if isinstance(params, Mapping) else {}
        if method == "item/tool/call":
            tool_name = self._requested_tool_name(params)
            arguments = params.get("arguments")
            payload: dict[str, Any] = {
                "toolName": tool_name,
                "callId": params.get("callId"),
                "threadId": params.get("threadId"),
                "turnId": params.get("turnId"),
            }
            try:
                if self.tool_gateway is None:
                    raise RuntimeUnavailable("NovelForge Tool Gateway is not bound to Codex runtime")
                if not isinstance(arguments, Mapping):
                    raise TypeError("dynamic tool arguments must be an object")
                result = await self.tool_gateway.invoke(
                    tool_name,
                    arguments,
                    self._tool_context(task, run_id, tool_name),
                )
                output = self._json_text(result.output)
                self.process.respond(
                    request_id,
                    result={
                        "success": True,
                        "contentItems": [{"type": "inputText", "text": output}],
                    },
                )
                payload.update({"success": True, "authority": result.authority, "proposal": result.proposal})
                if result.proposal and isinstance(result.output, Mapping):
                    payload.update({
                        "proposalId": result.output.get("proposalId"),
                        "proposalStatus": result.output.get("status"),
                        "proposalPersisted": result.output.get("persisted"),
                    })
                if tool_name == "request_more_context" and isinstance(result.output, Mapping):
                    request = result.output.get("request")
                    if isinstance(request, Mapping):
                        payload["contextRequest"] = {
                            "type": request.get("type"),
                            "sections": list(request.get("sections") or []),
                        }
                    provided = result.output.get("provided")
                    if isinstance(provided, Mapping):
                        payload["contextProvidedSections"] = sorted(str(key) for key in provided)
                    denied = result.output.get("denied")
                    if isinstance(denied, Mapping):
                        payload["contextDeniedSections"] = sorted(str(key) for key in denied)
                return RuntimeEvent(
                    runtime_type=self.runtime_type,
                    event_type="tool.call.completed",
                    payload=payload,
                    agent_run_id=run_id,
                )
            except Exception as exc:
                detail = str(exc)
                self.process.respond(
                    request_id,
                    result={
                        "success": False,
                        "contentItems": [{"type": "inputText", "text": detail}],
                    },
                )
                payload.update({
                    "success": False,
                    "errorCode": str(getattr(exc, "code", None) or exc.__class__.__name__).upper(),
                    "error": detail,
                })
                return RuntimeEvent(
                    runtime_type=self.runtime_type,
                    event_type="tool.call.failed",
                    payload=payload,
                    agent_run_id=run_id,
                )

        # Native Codex command/file/permission prompts are deliberately not a
        # second authority channel.  They are denied or rejected here; a
        # future Host approval adapter can replace this method explicitly.
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            self.process.respond(request_id, result={"decision": "decline"})
            return RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="approval.denied",
                payload={"method": method, "reason": "NovelForge runtime approval bridge is not granted"},
                agent_run_id=run_id,
            )
        if method == "item/permissions/requestApproval":
            self.process.respond(
                request_id,
                result={
                    "permissions": {"fileSystem": None, "network": None},
                    "scope": "turn",
                },
            )
            return RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="approval.denied",
                payload={"method": method, "reason": "additional permissions are not granted"},
                agent_run_id=run_id,
            )

        self.process.respond(
            request_id,
            error={"code": -32601, "message": f"unsupported App Server request: {method}"},
        )
        return RuntimeEvent(
            runtime_type=self.runtime_type,
            event_type="runtime.request.rejected",
            payload={"method": method, "reason": "request is outside the Host runtime contract"},
            agent_run_id=run_id,
        )

    @staticmethod
    def _requested_tool_name(params: Mapping[str, Any]) -> str:
        tool = str(params.get("tool") or "").strip()
        namespace = str(params.get("namespace") or "").strip()
        if namespace and tool and "." not in tool:
            return f"{namespace}.{tool}"
        return tool

    @staticmethod
    def _json_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _tool_context(task: AgentTask, run_id: str, tool_name: str) -> ToolCallContext:
        approvals = task.input_payload.get("toolApprovals")
        approval = approvals.get(tool_name) if isinstance(approvals, Mapping) else None
        approval_id: str | None = None
        if isinstance(approval, Mapping):
            raw_id = approval.get("approvalId") or approval.get("approval_id")
            approval_id = str(raw_id).strip() if raw_id else None
        domain_context = task.input_payload.get("domainContext")
        context = dict(domain_context) if isinstance(domain_context, Mapping) else {}
        # These are Host authority facts, not model/task input.  Keep other
        # domain context available to read/proposal tools but never forward a
        # provider-supplied author-confirmation claim to an authority handler.
        context.pop("authorConfirmed", None)
        context.pop("author_confirmed", None)
        return ToolCallContext(
            task=task,
            agent_run_id=run_id,
            approval_id=approval_id,
            domain_context=context,
        )

    @staticmethod
    def _extract_id(response: Mapping[str, Any], kind: str) -> str:
        result = response.get("result") or {}
        value = result.get(kind) if isinstance(result, Mapping) else None
        if isinstance(value, Mapping):
            value = value.get("id")
        if not value and isinstance(result, Mapping):
            value = result.get("id")
        if not value:
            raise RuntimeCrashed(f"Codex App Server response missing {kind} id")
        return str(value)

    @staticmethod
    def _prompt(task: AgentTask, *, recovery: Mapping[str, Any] | None = None) -> str:
        payload = dict(task.input_payload)
        messages = payload.get("messages")
        if isinstance(messages, list):
            original = "\n\n".join(
                f"[{message.get('role', 'user')}]\n{message.get('content', '')}"
                for message in messages if isinstance(message, Mapping)
            )
        else:
            original = str(payload.get("prompt") or payload.get("input") or "")
        if not recovery:
            return original
        return (
            "[NovelForge recovery envelope]\n"
            "The previous provider thread is unavailable. This is a new execution "
            "context for the same durable task. Treat the following as read-only "
            "Host provenance, continue from the latest checkpoint, and do not "
            "write Canon directly.\n"
            + json.dumps(dict(recovery), ensure_ascii=False, default=str)
            + "\n\n[Original task]\n"
            + original
        )
