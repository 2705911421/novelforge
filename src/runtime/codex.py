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
from .tool_gateway import ToolCallContext, ToolGateway


logger = logging.getLogger(__name__)


class CodexProcessManager:
    """Small synchronous JSONL process seam, safe to call via ``to_thread``."""

    def __init__(
        self,
        *,
        command: Sequence[str] = ("codex", "app-server"),
        cwd: str | Path | None = None,
        popen_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.command = tuple(command)
        self.cwd = str(cwd) if cwd else None
        self._popen_factory = popen_factory or subprocess.Popen
        self._process: Any | None = None
        self._next_id = 1
        self._pending_notifications: list[dict[str, Any]] = []
        self._ignored_response_ids: set[str | int] = set()
        self._lock = threading.RLock()
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
                    self._process = self._popen_factory(
                        list(self.command),
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=self.cwd,
                        text=True,
                        encoding="utf-8",
                        bufsize=1,
                    )
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
        with self._lock:
            process = self._process
            self._process = None
            self._pending_notifications.clear()
            self._ignored_response_ids.clear()
            if process is None:
                return
            for stream_name in ("stdin", "stdout", "stderr"):
                stream = getattr(process, stream_name, None)
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

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
        line = stdout.readline()
        if not line:
            detail = "process exited" if process is None or process.poll() is not None else "stdout closed"
            raise RuntimeCrashed(f"Codex App Server {detail}")
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
        cwd: str | Path | None = None,
        models: Sequence[ModelDescriptor] | None = None,
        tool_gateway: ToolGateway | None = None,
    ) -> None:
        self.runs = runs
        self.process = process or CodexProcessManager(cwd=cwd)
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
        link = self.runs.db.fetchone("SELECT task_id FROM agent_tasks WHERE id=?", (task.task_id,)) or {}
        durable_task_id = str(
            link.get("task_id") or task.input_payload.get("durableTaskId") or task.task_id
        )
        context_manifest = task.input_payload.get("contextManifest")
        context_bundle_id = task.context_bundle_id
        if isinstance(context_manifest, dict):
            task_row = self.runs.db.fetchone(
                "SELECT project_id, book_id FROM tasks WHERE id=?", (durable_task_id,)
            ) or {}
            bundle = ContextBundleStore(self.runs.db).create_from_manifest(
                context_manifest,
                project_id=context_manifest.get("projectId") or task_row.get("project_id") or task.project_id,
                book_id=context_manifest.get("bookId") or task_row.get("book_id"),
                source="CodexRuntime",
                task_id=durable_task_id,
                role=task.role,
            )
            context_bundle_id = bundle.bundle_id
            self.runs.db.execute(
                "UPDATE agent_tasks SET context_bundle_id=COALESCE(context_bundle_id, ?), "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (context_bundle_id, task.task_id),
            )
        run = self.runs.create(
            task=task,
            durable_task_id=durable_task_id,
            compute_plan=compute_plan,
            context_bundle_id=context_bundle_id,
            prompt_version=str(task.input_payload.get("promptVersion") or "codex-app-server-1"),
        )
        run_id = str(run["id"])
        self._durable_task_ids[task.task_id] = durable_task_id
        if task.task_id in self._cancelled or durable_task_id in self._cancelled:
            self.runs.transition(run_id, AgentRunStatus.INTERRUPTED.value,
                                 error_code="TASK_INTERRUPTED", error_detail="cancel requested before start")
            self._durable_task_ids.pop(task.task_id, None)
            self._cancelled.discard(task.task_id)
            self._cancelled.discard(durable_task_id)
            raise TaskInterrupted("task was cancelled before Codex execution")
        try:
            await self._process_call(self.process.start)
            thread_id = self._threads.get(task.task_id)
            if not thread_id:
                thread_response = await self._process_call(
                    self.process.request,
                    "thread/start",
                    self._thread_start_params(task, compute_plan),
                )
                thread_id = self._extract_id(thread_response, "thread")
                self._threads[task.task_id] = thread_id
            self.runs.transition(run_id, AgentRunStatus.RUNNING.value, runtime_thread_id=thread_id)
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="thread.started",
                payload={"threadId": thread_id},
                agent_run_id=run_id,
            )
            prompt = self._prompt(task)
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
            self._turns[task.task_id] = (thread_id, turn_id)
            self.runs.transition(run_id, AgentRunStatus.RUNNING.value, runtime_turn_id=turn_id)
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="turn.started",
                payload={"threadId": thread_id, "turnId": turn_id},
                agent_run_id=run_id,
            )
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
                event_type = method.replace("/", ".")
                payload = message.get("params") or message.get("result") or {}
                yield RuntimeEvent(
                    runtime_type=self.runtime_type,
                    event_type=event_type,
                    payload=payload if isinstance(payload, Mapping) else {"value": payload},
                    agent_run_id=run_id,
                )
                if event_type in {"turn.completed", "turn.complete", "turn.failed", "turn.cancelled"}:
                    artifacts = payload if isinstance(payload, Mapping) else {"value": payload}
                    status = {
                        "turn.completed": AgentRunStatus.SUCCEEDED.value,
                        "turn.cancelled": AgentRunStatus.CANCELLED.value,
                        "turn.failed": AgentRunStatus.FAILED.value,
                    }.get(event_type, AgentRunStatus.FAILED.value)
                    self.runs.transition(run_id, status, artifacts=artifacts)
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
            self._threads.pop(task.task_id, None)
            self._turns.pop(task.task_id, None)
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="error",
                payload={"code": str(getattr(exc, "code", None) or "RUNTIME_CRASHED"), "detail": str(exc)},
                agent_run_id=run_id,
            )
            raise RuntimeCrashed("Codex App Server execution failed", details={"detail": str(exc)}) from exc
        finally:
            self._turns.pop(task.task_id, None)
            self._durable_task_ids.pop(task.task_id, None)
            self._cancelled.discard(task.task_id)
            self._cancelled.discard(durable_task_id)

    async def pause(self, task_id: str) -> None:
        raise RuntimeUnavailable("Codex App Server pause/resume is not enabled by this adapter")

    async def resume(self, task_id: str) -> None:
        raise RuntimeUnavailable("Codex App Server pause/resume is not enabled by this adapter")

    async def cancel(self, task_id: str) -> None:
        self._cancelled.add(task_id)
        runtime_task_id = task_id
        if runtime_task_id not in self._turns:
            runtime_task_id = next(
                (
                    agent_task_id
                    for agent_task_id, durable_task_id in self._durable_task_ids.items()
                    if durable_task_id == task_id
                ),
                task_id,
            )
        turn = self._turns.get(runtime_task_id)
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

    async def get_models(self) -> Sequence[ModelDescriptor]:
        return self._models

    async def get_capabilities(self) -> RuntimeCapabilities:
        return self._capabilities

    async def get_usage(self) -> UsageSnapshot:
        return UsageSnapshot()

    async def shutdown(self) -> None:
        await asyncio.to_thread(self.process.close)
        self._threads.clear()
        self._turns.clear()
        self._durable_task_ids.clear()
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
        for definition in self.tool_gateway.catalog(task):
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
        approved = False
        if isinstance(approval, Mapping):
            raw_id = approval.get("approvalId") or approval.get("approval_id")
            approval_id = str(raw_id).strip() if raw_id else None
            approved = bool(approval.get("approved"))
        domain_context = task.input_payload.get("domainContext")
        context = dict(domain_context) if isinstance(domain_context, Mapping) else {}
        if "authorConfirmed" not in context:
            context["authorConfirmed"] = bool(task.constraints.get("authorConfirmed", False))
        return ToolCallContext(
            task=task,
            agent_run_id=run_id,
            approval_id=approval_id,
            approved=approved,
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
    def _prompt(task: AgentTask) -> str:
        payload = dict(task.input_payload)
        messages = payload.get("messages")
        if isinstance(messages, list):
            return "\n\n".join(
                f"[{message.get('role', 'user')}]\n{message.get('content', '')}"
                for message in messages if isinstance(message, Mapping)
            )
        return str(payload.get("prompt") or payload.get("input") or "")
