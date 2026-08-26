"""Structured CLI runtime adapters.

Plain CLI Harnesses are deliberately kept behind the same host-owned
``IAgentRuntime`` seam as App Server runtimes.  The adapter never enables
vendor tools or permission bypasses; it starts an argv-only child, keeps the
durable AgentRun as the lifecycle record, and treats vendor JSON as an
artifact rather than as NovelForge Canon.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

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


logger = logging.getLogger(__name__)

ProcessFactory = Callable[[Sequence[str], str | None], Awaitable[Any] | Any]


class StructuredCliRuntime:
    """Host-supervised one-shot runtime for a structured CLI Harness.

    The base class is intentionally vendor-neutral.  A subclass only defines
    command construction and authentication parsing; process supervision,
    durable AgentRun state, cancellation, bounded diagnostics, and artifact
    projection remain shared.
    """

    runtime_type = "structured-cli"
    executable = ""
    integration_grade = "C"
    default_model_id = "default"

    def __init__(
        self,
        runs: AgentRunStore,
        *,
        cwd: str | Path | None = None,
        executable: str | None = None,
        models: Sequence[ModelDescriptor] | None = None,
        process_factory: ProcessFactory | None = None,
        timeout_seconds: float = 300.0,
        max_output_chars: int = 200_000,
        max_budget_usd: float | None = None,
    ) -> None:
        self.runs = runs
        self.cwd = str(cwd) if cwd else None
        self.executable = str(executable or self.executable).strip()
        if not self.executable:
            raise ValueError("CLI executable is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive")
        if max_budget_usd is not None and float(max_budget_usd) <= 0:
            raise ValueError("max_budget_usd must be positive when provided")
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_chars = int(max_output_chars)
        self.max_budget_usd = max_budget_usd
        self._process_factory = process_factory or self._spawn
        self._models = tuple(models or self._default_models())
        self._capabilities = RuntimeCapabilities(
            runtime_type=self.runtime_type,
            streaming=False,
            sessions=False,
            tools=False,
            approvals=False,
            pause_resume=False,
            models=self._models,
            integration_grade=self.integration_grade,
        )
        self._processes: dict[str, Any] = {}
        self._durable_task_ids: dict[str, str] = {}
        self._cancelled: set[str] = set()

    async def initialize(self, config: Mapping[str, Any] | None = None) -> RuntimeCapabilities:
        del config
        return self._capabilities

    async def authenticate(self) -> AuthState:
        """Read vendor-managed auth without scraping credentials."""
        command = self._auth_command()
        if not command:
            return AuthState("unknown", detail="CLI runtime has no auth probe")
        try:
            completed = await self._run_probe(command)
        except FileNotFoundError:
            return AuthState("not_authenticated", detail=f"executable not found: {self.executable}")
        output = self._decode(getattr(completed, "stdout", ""))
        error = self._decode(getattr(completed, "stderr", ""))
        return self._parse_auth_result(
            getattr(completed, "returncode", 1), output, error,
        )

    async def execute(self, task: AgentTask, compute_plan: ComputePlan):
        link = self.runs.db.fetchone(
            "SELECT task_id FROM agent_tasks WHERE id=?", (task.task_id,)
        ) or {}
        durable_task_id = str(
            link.get("task_id")
            or task.input_payload.get("durableTaskId")
            or task.task_id
        )
        context_bundle_id = task.context_bundle_id
        context_manifest = task.input_payload.get("contextManifest")
        if isinstance(context_manifest, Mapping):
            task_row = self.runs.db.fetchone(
                "SELECT project_id, book_id FROM tasks WHERE id=?", (durable_task_id,)
            ) or {}
            bundle = ContextBundleStore(self.runs.db).create_from_manifest(
                context_manifest,
                project_id=context_manifest.get("projectId") or task_row.get("project_id") or task.project_id,
                book_id=context_manifest.get("bookId") or task_row.get("book_id"),
                source=self.__class__.__name__,
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
            prompt_version=str(
                task.input_payload.get("promptVersion") or f"{self.runtime_type}-1"
            ),
        )
        run_id = str(run["id"])
        self._durable_task_ids[task.task_id] = durable_task_id
        if self._is_cancelled(task.task_id, durable_task_id):
            self.runs.transition(
                run_id,
                AgentRunStatus.INTERRUPTED.value,
                error_code="TASK_INTERRUPTED",
                error_detail="cancel requested before CLI execution",
            )
            raise TaskInterrupted("task was cancelled before CLI execution")

        process = None
        try:
            argv = self._command(task, compute_plan)
            process = await self._spawn_process(argv)
            self._processes[task.task_id] = process
            self.runs.transition(
                run_id,
                AgentRunStatus.RUNNING.value,
                runtime_thread_id=f"cli:{self.runtime_type}",
            )
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="turn.started",
                payload={"argv": self._redact_argv(argv), "modelId": compute_plan.model_id},
                agent_run_id=run_id,
            )

            stdout, stderr = await self._communicate(process)
            if self._is_cancelled(task.task_id, durable_task_id):
                self.runs.transition(
                    run_id,
                    AgentRunStatus.INTERRUPTED.value,
                    error_code="TASK_INTERRUPTED",
                    error_detail="cancel requested during CLI execution",
                )
                raise TaskInterrupted("task cancellation requested")

            returncode = self._returncode(process)
            stdout_text = self._bounded(self._decode(stdout))
            stderr_text = self._bounded(self._decode(stderr))
            if returncode not in (0, None):
                raise RuntimeCrashed(
                    f"{self.runtime_type} exited with code {returncode}",
                    details={"returncode": returncode, "stderr": stderr_text},
                )

            payload = self._parse_output(stdout_text)
            if isinstance(payload, Mapping) and bool(payload.get("is_error")):
                raise RuntimeCrashed(
                    f"{self.runtime_type} reported an error",
                    details={"payload": self._safe_payload(payload), "stderr": stderr_text},
                )
            artifact = self._artifact(payload, stdout_text, stderr_text)
            usage = self._usage(payload)
            self.runs.transition(
                run_id,
                AgentRunStatus.SUCCEEDED.value,
                usage=usage,
                artifacts=artifact,
            )
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="turn.completed",
                payload={"artifact": artifact, "usage": usage},
                agent_run_id=run_id,
            )
        except asyncio.CancelledError:
            await self._terminate(process)
            current = self.runs.get(run_id) or {}
            if current.get("status") in {
                AgentRunStatus.RUNNING.value,
                AgentRunStatus.PAUSED.value,
            }:
                self.runs.transition(
                    run_id,
                    AgentRunStatus.INTERRUPTED.value,
                    error_code="TASK_CANCELLED",
                    error_detail="runtime coroutine cancelled",
                )
            raise
        except TaskInterrupted:
            await self._terminate(process)
            raise
        except Exception as exc:
            await self._terminate(process)
            current = self.runs.get(run_id) or {}
            if current.get("status") in {
                AgentRunStatus.RUNNING.value,
                AgentRunStatus.PAUSED.value,
            }:
                self.runs.transition(
                    run_id,
                    AgentRunStatus.INTERRUPTED.value,
                    error_code=str(getattr(exc, "code", None) or "RUNTIME_CRASHED"),
                    error_detail=str(exc),
                )
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="error",
                payload={
                    "code": str(getattr(exc, "code", None) or "RUNTIME_CRASHED"),
                    "detail": str(exc),
                },
                agent_run_id=run_id,
            )
            if isinstance(exc, RuntimeCrashed):
                raise
            raise RuntimeCrashed(
                f"{self.runtime_type} execution failed", details={"detail": str(exc)}
            ) from exc
        finally:
            self._processes.pop(task.task_id, None)
            self._durable_task_ids.pop(task.task_id, None)
            self._cancelled.discard(task.task_id)
            self._cancelled.discard(durable_task_id)

    async def pause(self, task_id: str) -> None:
        del task_id
        raise RuntimeUnavailable(f"{self.runtime_type} does not support pause/resume")

    async def resume(self, task_id: str) -> None:
        del task_id
        raise RuntimeUnavailable(f"{self.runtime_type} does not support pause/resume")

    async def cancel(self, task_id: str) -> None:
        self._cancelled.add(task_id)
        agent_task_id = task_id
        if agent_task_id not in self._processes:
            agent_task_id = next(
                (
                    candidate
                    for candidate, durable_id in self._durable_task_ids.items()
                    if durable_id == task_id
                ),
                task_id,
            )
        await self._terminate(self._processes.get(agent_task_id))

    async def get_models(self) -> Sequence[ModelDescriptor]:
        return self._models

    async def get_capabilities(self) -> RuntimeCapabilities:
        return self._capabilities

    async def get_usage(self) -> UsageSnapshot:
        return UsageSnapshot()

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(self._terminate(process) for process in tuple(self._processes.values())),
            return_exceptions=True,
        )
        self._processes.clear()
        self._durable_task_ids.clear()
        self._cancelled.clear()

    async def _spawn_process(self, argv: Sequence[str]) -> Any:
        result = self._process_factory(tuple(argv), self.cwd)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    async def _spawn(argv: Sequence[str], cwd: str | None) -> Any:
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _communicate(self, process: Any) -> tuple[Any, Any]:
        communicate = getattr(process, "communicate", None)
        if communicate is None:
            raise RuntimeCrashed("CLI process does not expose communicate()")
        try:
            result = await asyncio.wait_for(communicate(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            await self._terminate(process)
            raise RuntimeCrashed(
                f"{self.runtime_type} timed out after {self.timeout_seconds:.0f}s"
            ) from exc
        if not isinstance(result, tuple) or len(result) != 2:
            raise RuntimeCrashed("CLI process returned an invalid communicate result")
        return result[0], result[1]

    async def _run_probe(self, argv: Sequence[str]) -> Any:
        process = await self._spawn_process(argv)
        try:
            returncode = getattr(process, "returncode", None)
            stdout, stderr = await self._communicate(process)
            if returncode is None:
                returncode = self._returncode(process)
            # A small immutable result keeps auth parsing independent of the
            # subprocess implementation used by tests and the real host.
            return _CompletedProbe(
                returncode,
                self._bounded(self._decode(stdout)),
                self._bounded(self._decode(stderr)),
            )
        finally:
            await self._terminate(process)

    async def _terminate(self, process: Any | None) -> None:
        if process is None:
            return
        try:
            if self._returncode(process) is not None:
                return
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                terminate()
            else:
                kill = getattr(process, "kill", None)
                if callable(kill):
                    kill()
            wait = getattr(process, "wait", None)
            if callable(wait):
                result = wait()
                if inspect.isawaitable(result):
                    try:
                        await asyncio.wait_for(result, timeout=3)
                    except asyncio.TimeoutError:
                        kill = getattr(process, "kill", None)
                        if callable(kill):
                            kill()
        except Exception as exc:
            logger.warning("CLI runtime process cleanup failed", exc_info=exc)

    @staticmethod
    def _returncode(process: Any) -> int | None:
        value = getattr(process, "returncode", None)
        if value is None:
            poll = getattr(process, "poll", None)
            if callable(poll):
                value = poll()
        return value

    def _command(self, task: AgentTask, plan: ComputePlan) -> tuple[str, ...]:
        raise NotImplementedError

    def _auth_command(self) -> tuple[str, ...]:
        return ()

    def _parse_auth_result(self, returncode: int | None, output: str, error: str) -> AuthState:
        del output
        if returncode not in (0, None):
            return AuthState("not_authenticated", detail=error or "CLI auth probe failed")
        return AuthState("authenticated", detail="CLI auth probe succeeded")

    def _default_models(self) -> tuple[ModelDescriptor, ...]:
        return (
            ModelDescriptor(
                runtime_type=self.runtime_type,
                model_id=self.default_model_id,
                display_name=f"{self.runtime_type} default",
                reasoning_levels=("low", "medium", "high", "xhigh"),
                context_window=100_000,
                capability_profile={
                    "extraction": "C2", "planning": "C3", "writing": "C3",
                    "review": "C3", "long_context": "C3", "tool_use": "C1",
                    "structured_output": "C2", "revision": "C3", "consistency": "C3",
                },
            ),
        )

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

    def _is_cancelled(self, *task_ids: str) -> bool:
        return any(task_id in self._cancelled for task_id in task_ids)

    def _artifact(self, payload: Any, stdout: str, stderr: str) -> dict[str, Any]:
        if isinstance(payload, Mapping):
            result = payload.get("result")
            content = result if isinstance(result, str) else payload.get("content")
            if content is None:
                content = self._safe_payload(payload)
            artifact = {
                "content": self._bounded(str(content)),
                "contentType": "text",
                "sessionId": payload.get("session_id") or payload.get("sessionId"),
                "stopReason": payload.get("stop_reason") or payload.get("stopReason"),
            }
        else:
            artifact = {"content": self._bounded(str(payload)), "contentType": "text"}
        if stderr:
            artifact["diagnostic"] = stderr
        if len(stdout) >= self.max_output_chars:
            artifact["truncated"] = True
        return artifact

    @staticmethod
    def _usage(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            return {}
        usage = payload.get("usage")
        result = dict(usage) if isinstance(usage, Mapping) else {}
        if payload.get("total_cost_usd") is not None:
            result["costUsd"] = payload.get("total_cost_usd")
        if payload.get("duration_api_ms") is not None:
            result["latencyMs"] = payload.get("duration_api_ms")
        return result

    @staticmethod
    def _parse_output(output: str) -> Any:
        text = output.strip()
        if not text:
            return ""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Structured output is preferred, but a plain CLI response is
            # still an honest text artifact rather than a fake JSON success.
            return text

    def _safe_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        safe = dict(payload)
        for key in ("api_key", "apiKey", "token", "access_token", "refresh_token"):
            if key in safe:
                safe[key] = "[redacted]"
        return safe

    def _bounded(self, value: str) -> str:
        return value[: self.max_output_chars]

    @staticmethod
    def _decode(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

    @staticmethod
    def _redact_argv(argv: Sequence[str]) -> list[str]:
        result = [str(item) for item in argv]
        for index, item in enumerate(result[:-1]):
            if item in {"-p", "--prompt"}:
                result[index + 1] = "[prompt]"
        return result


class ClaudeCodeRuntime(StructuredCliRuntime):
    """Claude Code ``--print --output-format json`` adapter."""

    runtime_type = "claude-code"
    executable = "claude"
    integration_grade = "C"
    default_model_id = "default"

    def _command(self, task: AgentTask, plan: ComputePlan) -> tuple[str, ...]:
        command: list[str] = [
            self.executable,
            "-p",
            self._prompt(task),
            "--bare",
            "--output-format",
            "json",
            "--permission-mode",
            "manual",
            "--tools",
            "",
            "--no-session-persistence",
            "--effort",
            plan.reasoning,
        ]
        if plan.model_id and plan.model_id not in {"default", "claude-default"}:
            command.extend(("--model", plan.model_id))
        if self.max_budget_usd is not None:
            command.extend(("--max-budget-usd", str(self.max_budget_usd)))
        return tuple(command)

    def _auth_command(self) -> tuple[str, ...]:
        return (self.executable, "auth", "status")

    def _parse_auth_result(self, returncode: int | None, output: str, error: str) -> AuthState:
        if returncode not in (0, None):
            return AuthState("not_authenticated", detail=error or "Claude auth status failed")
        try:
            payload = json.loads(output or "{}")
        except json.JSONDecodeError:
            return AuthState("not_authenticated", detail="Claude auth status was not valid JSON")
        if not isinstance(payload, Mapping) or not bool(payload.get("loggedIn")):
            return AuthState("not_authenticated", detail="Claude Code reports no logged-in account")
        account = payload.get("email") or payload.get("account") or payload.get("authMethod")
        return AuthState(
            "authenticated",
            account_label=str(account) if account else None,
            detail="claude auth status",
        )


class _CompletedProbe:
    def __init__(self, returncode: int | None, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
