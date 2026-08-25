"""Codex App Server runtime adapter.

The adapter speaks the documented newline-delimited JSON-RPC App Server
protocol over a supervised ``codex app-server`` subprocess.  It never reads a
credential from a Codex config file and never treats a provider thread as
NovelForge task state.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
        self._lock = threading.RLock()

    @property
    def process(self) -> Any | None:
        return self._process

    def start(self) -> None:
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
                    bufsize=1,
                )
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
        with self._lock:
            return self._request(method, params or {})

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        with self._lock:
            self._notify(method, params or {})

    def read_message(self) -> dict[str, Any]:
        with self._lock:
            return self._read_message()

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._pending_notifications.clear()
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
        request_id = self._next_id
        self._next_id += 1
        self._write({"id": request_id, "method": method, "params": dict(params)})
        while True:
            message = self._read_message()
            if message.get("id") == request_id:
                if message.get("error"):
                    raise RuntimeCrashed(
                        f"Codex App Server rejected {method}",
                        details={"method": method, "error": message.get("error")},
                    )
                return message
            if message.get("method"):
                self._pending_notifications.append(message)

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

    def _read_message(self) -> dict[str, Any]:
        process = self._process
        stdout = getattr(process, "stdout", None) if process is not None else None
        if stdout is None:
            raise RuntimeCrashed("Codex App Server stdout is unavailable")
        while self._pending_notifications:
            return self._pending_notifications.pop(0)
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
    ) -> None:
        self.runs = runs
        self.process = process or CodexProcessManager(cwd=cwd)
        self._models = tuple(models or (
            ModelDescriptor(
                runtime_type=self.runtime_type,
                model_id="codex-default",
                display_name="Codex App Server 默认模型",
                capabilities={"agent": "full", "tools": "available"},
                reasoning_levels=("medium", "high", "xhigh"),
                context_window=256_000,
            ),
        ))
        self._threads: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._capabilities = RuntimeCapabilities(
            runtime_type=self.runtime_type,
            streaming=True,
            sessions=True,
            tools=True,
            approvals=True,
            pause_resume=False,
            models=self._models,
            integration_grade="B",
        )

    async def initialize(self, config: Mapping[str, Any] | None = None) -> RuntimeCapabilities:
        await asyncio.to_thread(self.process.start)
        return self._capabilities

    async def authenticate(self) -> AuthState:
        # Authentication is deliberately delegated to the official Codex
        # process.  The adapter does not scrape local credential stores.
        return AuthState("delegated", detail="Codex App Server owns authentication")

    async def execute(self, task: AgentTask, compute_plan: ComputePlan):
        link = self.runs.db.fetchone("SELECT task_id FROM agent_tasks WHERE id=?", (task.task_id,)) or {}
        durable_task_id = str(link.get("task_id") or task.task_id)
        run = self.runs.create(
            task=task,
            durable_task_id=durable_task_id,
            compute_plan=compute_plan,
            context_bundle_id=task.context_bundle_id,
            prompt_version=str(task.input_payload.get("promptVersion") or "codex-app-server-1"),
        )
        run_id = str(run["id"])
        if task.task_id in self._cancelled:
            self.runs.transition(run_id, AgentRunStatus.INTERRUPTED.value,
                                 error_code="TASK_INTERRUPTED", error_detail="cancel requested before start")
            raise TaskInterrupted("task was cancelled before Codex execution")
        try:
            await asyncio.to_thread(self.process.start)
            thread_id = self._threads.get(task.task_id)
            if not thread_id:
                thread_response = await asyncio.to_thread(
                    self.process.request,
                    "thread/start",
                    {"model": compute_plan.model_id, "metadata": {"novelforgeTaskId": task.task_id}},
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
            turn_response = await asyncio.to_thread(
                self.process.request,
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "model": compute_plan.model_id,
                    "reasoning": compute_plan.reasoning,
                },
            )
            turn_id = self._extract_id(turn_response, "turn")
            self.runs.transition(run_id, AgentRunStatus.RUNNING.value, runtime_turn_id=turn_id)
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="turn.started",
                payload={"threadId": thread_id, "turnId": turn_id},
                agent_run_id=run_id,
            )
            while True:
                if task.task_id in self._cancelled:
                    self.runs.transition(run_id, AgentRunStatus.INTERRUPTED.value,
                                         error_code="TASK_INTERRUPTED", error_detail="cancel requested")
                    raise TaskInterrupted("task cancellation requested")
                message = await asyncio.to_thread(self.process.read_message)
                if message.get("error"):
                    raise RuntimeCrashed("Codex App Server runtime error", details={"error": message["error"]})
                method = str(message.get("method") or "")
                if not method:
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
                    status = AgentRunStatus.SUCCEEDED.value if event_type == "turn.completed" else AgentRunStatus.FAILED.value
                    self.runs.transition(run_id, status, artifacts=artifacts)
                    return
        except TaskInterrupted:
            raise
        except Exception as exc:
            current = self.runs.get(run_id) or {}
            if current.get("status") in {AgentRunStatus.RUNNING.value, AgentRunStatus.PAUSED.value}:
                self.runs.transition(run_id, AgentRunStatus.INTERRUPTED.value,
                                     error_code=str(getattr(exc, "code", None) or "RUNTIME_CRASHED"),
                                     error_detail=str(exc))
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="error",
                payload={"code": str(getattr(exc, "code", None) or "RUNTIME_CRASHED"), "detail": str(exc)},
                agent_run_id=run_id,
            )
            raise RuntimeCrashed("Codex App Server execution failed", details={"detail": str(exc)}) from exc

    async def pause(self, task_id: str) -> None:
        raise RuntimeUnavailable("Codex App Server pause/resume is not enabled by this adapter")

    async def resume(self, task_id: str) -> None:
        raise RuntimeUnavailable("Codex App Server pause/resume is not enabled by this adapter")

    async def cancel(self, task_id: str) -> None:
        # Cancellation is cooperative until the protocol's turn control is
        # negotiated; the durable TaskRuntime remains the hard cancellation
        # boundary and the generator checks this set between events.
        self._cancelled.add(task_id)

    async def get_models(self) -> Sequence[ModelDescriptor]:
        return self._models

    async def get_capabilities(self) -> RuntimeCapabilities:
        return self._capabilities

    async def get_usage(self) -> UsageSnapshot:
        return UsageSnapshot()

    async def shutdown(self) -> None:
        await asyncio.to_thread(self.process.close)
        self._threads.clear()

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
