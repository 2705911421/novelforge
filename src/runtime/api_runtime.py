"""Adapter for NovelForge's existing persisted API model gateway."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Mapping, Sequence

from src.llm.model_runtime import PersistentModelRuntime

from .contracts import (
    AgentTask,
    AgentRunStatus,
    AuthState,
    ComputePlan,
    ModelDescriptor,
    RuntimeCapabilities,
    RuntimeEvent,
    UsageSnapshot,
)
from .errors import AgentRuntimeError, RuntimeUnavailable, TaskInterrupted
from .persistence import AgentRunStore


logger = logging.getLogger(__name__)


class ApiModelRuntime:
    """Expose the legacy provider gateway behind the common AgentRuntime seam.

    ``GenerationRun`` remains written by ``PersistentModelRuntime`` for
    compatibility.  ``AgentRun`` is the outer lifecycle and may contain one
    or more provider attempts in a future implementation.
    """

    runtime_type = "api"

    def __init__(self, runtime: PersistentModelRuntime, runs: AgentRunStore):
        self.runtime = runtime
        self.runs = runs
        self._cancelled: set[str] = set()
        self._capabilities = RuntimeCapabilities(runtime_type=self.runtime_type, integration_grade="B")

    async def initialize(self, config: Mapping[str, Any] | None = None) -> RuntimeCapabilities:
        self._capabilities = RuntimeCapabilities(
            runtime_type=self.runtime_type,
            streaming=False,
            sessions=False,
            tools=False,
            approvals=False,
            pause_resume=False,
            models=tuple(await self.get_models()),
            integration_grade="B",
        )
        return self._capabilities

    def authenticate_sync(self) -> AuthState:
        """Check persisted provider credentials without requiring an event loop."""
        rows = self.runtime.repository.db.fetchall(
            "SELECT credential_ref FROM model_providers WHERE enabled=TRUE ORDER BY created_at"
        )
        if not rows:
            return AuthState("not_authenticated", detail="no enabled API provider")
        for row in rows:
            try:
                self.runtime.repository.credentials.resolve(row.get("credential_ref"))
                return AuthState("authenticated", detail="provider credential resolved")
            except Exception as exc:
                logger.debug(
                    "API provider credential resolution failed",
                    extra={"credential_ref": row.get("credential_ref")},
                    exc_info=exc,
                )
                continue
        return AuthState("not_authenticated", detail="enabled providers have no resolvable credential")

    async def authenticate(self) -> AuthState:
        return self.authenticate_sync()

    async def execute(self, task: AgentTask, compute_plan: ComputePlan):
        link = self.runs.db.fetchone("SELECT task_id FROM agent_tasks WHERE id=?", (task.task_id,)) or {}
        durable_task_id = str(link.get("task_id") or task.input_payload.get("durableTaskId") or task.task_id)
        context_manifest = task.input_payload.get("contextManifest")
        context_bundle_id = self.runtime.ensure_context_bundle(
            durable_task_id=durable_task_id,
            agent_task=task,
            context_manifest=context_manifest if isinstance(context_manifest, dict) else None,
        )
        run = self.runs.create(
            task=task,
            durable_task_id=durable_task_id,
            compute_plan=compute_plan,
            context_bundle_id=context_bundle_id,
            prompt_version=str(task.input_payload.get("promptVersion") or "agent-runtime-1"),
        )
        run_id = str(run["id"])
        try:
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="turn.started",
                payload={"agentRunId": run_id, "modelId": compute_plan.model_id},
                agent_run_id=run_id,
            )
            if task.task_id in self._cancelled or durable_task_id in self._cancelled:
                self.runs.transition(
                    run_id,
                    AgentRunStatus.INTERRUPTED.value,
                    error_code="TASK_INTERRUPTED",
                    error_detail="cancel requested before invocation",
                )
                raise TaskInterrupted("task was cancelled before API invocation")
            try:
                operation = str(task.input_payload.get("operation") or "chat").strip().lower()
                if operation == "image":
                    response = await asyncio.to_thread(
                        self._invoke_image, task, compute_plan, durable_task_id, run_id
                    )
                    artifact = {
                        "content": "image",
                        "contentType": response.mime_type,
                        "dataBase64": base64.b64encode(response.data).decode("ascii"),
                        "mimeType": response.mime_type,
                        "model": response.model,
                        "provider": response.provider,
                    }
                elif operation == "embedding":
                    vector = await asyncio.to_thread(
                        self._invoke_embedding, task, compute_plan, durable_task_id, run_id
                    )
                    artifact = {
                        "embedding": vector,
                        "dimension": len(vector),
                        "model": compute_plan.model_id,
                        "provider": self.runtime_type,
                    }
                elif operation == "embedding_batch":
                    vectors = await asyncio.to_thread(
                        self._invoke_embedding_batch, task, compute_plan, durable_task_id, run_id
                    )
                    artifact = {
                        "embeddings": vectors,
                        "count": len(vectors),
                        "dimension": len(vectors[0]) if vectors else 0,
                        "model": compute_plan.model_id,
                        "provider": self.runtime_type,
                    }
                elif operation == "chat":
                    response = await asyncio.to_thread(self._invoke, task, compute_plan, durable_task_id, run_id)
                    artifact = {
                        "content": response.content,
                        "contentType": "markdown",
                        "model": response.model,
                        "provider": response.provider,
                        "finishReason": response.finish_reason,
                    }
                else:
                    raise AgentRuntimeError(
                        "API runtime operation is unsupported",
                        code="RUNTIME_OPERATION_UNSUPPORTED",
                    )
            except TaskInterrupted:
                self.runs.transition(
                    run_id,
                    AgentRunStatus.INTERRUPTED.value,
                    error_code="TASK_INTERRUPTED",
                    error_detail="cancel requested during invocation",
                )
                raise
            except Exception as exc:
                code = str(getattr(exc, "code", None) or exc.__class__.__name__).upper()
                self.runs.transition(run_id, AgentRunStatus.FAILED.value, error_code=code, error_detail=str(exc))
                yield RuntimeEvent(
                    runtime_type=self.runtime_type,
                    event_type="error",
                    payload={"agentRunId": run_id, "code": code, "detail": str(exc)},
                    agent_run_id=run_id,
                )
                raise AgentRuntimeError("API runtime invocation failed", code=code, retryable=True) from exc
            if task.task_id in self._cancelled or durable_task_id in self._cancelled:
                self.runs.transition(
                    run_id,
                    AgentRunStatus.INTERRUPTED.value,
                    error_code="TASK_INTERRUPTED",
                    error_detail="cancel requested after invocation",
                )
                raise TaskInterrupted("task was cancelled after API invocation")
            if operation == "image":
                usage = {"latencyMs": int(getattr(response, "latency_ms", 0) or 0)}
            elif operation == "embedding":
                usage = {"embeddingDimensions": len(artifact.get("embedding", []))}
            elif operation == "embedding_batch":
                usage = {
                    "embeddingCount": int(artifact.get("count", 0) or 0),
                    "embeddingDimensions": int(artifact.get("dimension", 0) or 0),
                }
            else:
                usage = {
                    "inputTokens": int(getattr(response, "prompt_tokens", 0) or 0),
                    "outputTokens": int(getattr(response, "completion_tokens", 0) or 0),
                    "totalTokens": int(getattr(response, "tokens_used", 0) or 0),
                    "latencyMs": int(getattr(response, "latency_ms", 0) or 0),
                }
            self.runs.transition(run_id, AgentRunStatus.SUCCEEDED.value, usage=usage, artifacts=artifact)
            yield RuntimeEvent(
                runtime_type=self.runtime_type,
                event_type="turn.completed",
                payload={"agentRunId": run_id, "artifact": artifact, "usage": usage},
                agent_run_id=run_id,
            )
        finally:
            self._cancelled.discard(task.task_id)
            self._cancelled.discard(durable_task_id)

    def _invoke(self, task: AgentTask, plan: ComputePlan, durable_task_id: str, run_id: str):
        payload = dict(task.input_payload)
        messages = payload.get("messages")
        if not isinstance(messages, list):
            prompt = str(payload.get("prompt") or payload.get("input") or "")
            messages = [{"role": "user", "content": prompt}]
        system = str(payload.get("system") or "")
        # The ComputePlan is the Host-issued selection.  The payload remains
        # a compatibility fallback for older persisted plans that predate
        # provider-scoped model selection.
        provider_id = plan.provider_id or payload.get("providerId")
        context_manifest = payload.get("contextManifest")
        runtime_options = dict(payload.get("runtimeOptions") or {})
        runtime_options.pop("model_id", None)
        runtime_options.pop("provider_id", None)
        runtime_options.pop("prompt_version", None)
        runtime_options.pop("context_manifest", None)
        runtime_options.pop("task_stage", None)
        runtime_options.pop("max_tokens", None)
        runtime_options.pop("json_mode", None)
        with self.runtime.task_scope(durable_task_id):
            with self.runtime.managed_agent_run(run_id):
                return self.runtime.invoke(
                    task.role,
                    messages,
                    system,
                    json_mode=bool(payload.get("jsonMode", False)),
                    provider_id=str(provider_id) if provider_id else None,
                    model_id=plan.model_id,
                    task_stage=task.task_type,
                    prompt_version=str(payload.get("promptVersion") or "agent-runtime-1"),
                    context_manifest=context_manifest if isinstance(context_manifest, dict) else None,
                    max_tokens=plan.output_budget or None,
                    **runtime_options,
                )

    def _invoke_image(self, task: AgentTask, plan: ComputePlan, durable_task_id: str, run_id: str):
        payload = dict(task.input_payload)
        options = payload.get("imageOptions")
        options = dict(options) if isinstance(options, Mapping) else {}
        prompt = str(payload.get("imagePrompt") or payload.get("prompt") or "").strip()
        if not prompt:
            raise AgentRuntimeError("image operation requires a prompt", code="RUNTIME_INPUT_INVALID")
        with self.runtime.task_scope(durable_task_id):
            with self.runtime.managed_agent_run(run_id):
                return self.runtime.generate_image(
                    prompt,
                    size=str(options.get("size") or "1024x1024"),
                    quality=str(options.get("quality") or ""),
                    style=str(options.get("style") or ""),
                    provider_id=plan.provider_id or (
                        str(payload.get("providerId")) if payload.get("providerId") else None
                    ),
                    model_id=plan.model_id,
                )

    def _invoke_embedding(self, task: AgentTask, plan: ComputePlan, durable_task_id: str, run_id: str):
        payload = dict(task.input_payload)
        text = str(payload.get("embeddingInput") or payload.get("input") or "").strip()
        if not text:
            raise AgentRuntimeError("embedding operation requires input", code="RUNTIME_INPUT_INVALID")
        with self.runtime.task_scope(durable_task_id):
            return self.runtime.embed(
                text,
                provider_id=plan.provider_id or (
                    str(payload.get("providerId")) if payload.get("providerId") else None
                ),
                model_id=plan.model_id,
            )

    def _invoke_embedding_batch(
        self,
        task: AgentTask,
        plan: ComputePlan,
        durable_task_id: str,
        run_id: str,
    ) -> list[list[float]]:
        payload = dict(task.input_payload)
        texts = payload.get("embeddingInputs") or payload.get("inputs")
        if not isinstance(texts, list) or not texts:
            raise AgentRuntimeError("embedding batch requires inputs", code="RUNTIME_INPUT_INVALID")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise AgentRuntimeError("embedding batch contains invalid input", code="RUNTIME_INPUT_INVALID")
        with self.runtime.task_scope(durable_task_id):
            return self.runtime.embed_many(
                texts,
                provider_id=plan.provider_id or (
                    str(payload.get("providerId")) if payload.get("providerId") else None
                ),
                model_id=plan.model_id,
            )

    async def pause(self, task_id: str) -> None:
        raise RuntimeUnavailable("API model runtime does not support pause/resume")

    async def resume(self, task_id: str) -> None:
        raise RuntimeUnavailable("API model runtime does not support pause/resume")

    async def cancel(self, task_id: str) -> None:
        self._cancelled.add(task_id)

    async def get_models(self) -> Sequence[ModelDescriptor]:
        return self.get_models_sync()

    def get_models_sync(self) -> tuple[ModelDescriptor, ...]:
        """Read configured API models without requiring an event loop.

        Studio constructs its Runtime Plane synchronously, while the public
        runtime contract exposes async capability discovery.  Keeping the
        descriptor construction here prevents the scheduler from depending
        on a prior UI request to populate the API model candidates.
        """
        rows = self.runtime.repository.db.fetchall(
            """SELECT m.model_id, m.name, m.capabilities, m.enabled, p.id AS provider_id,
                      p.enabled AS provider_enabled,
                      EXISTS(
                          SELECT 1 FROM agent_model_routes image_route
                          WHERE image_route.agent_role='image' AND image_route.model_id=m.id
                      ) AS image_route
               FROM models m JOIN model_providers p ON p.id=m.provider_id
               ORDER BY m.created_at, m.id"""
        )
        descriptors: list[ModelDescriptor] = []
        for row in rows:
            if not row.get("enabled") or not row.get("provider_enabled"):
                continue
            try:
                capabilities = row.get("capabilities") or "[]"
                capability_names = json.loads(capabilities) if isinstance(capabilities, str) else capabilities
            except (TypeError, ValueError, json.JSONDecodeError):
                capability_names = []
            normalized_capabilities = {
                str(name).strip().lower()
                for name in capability_names
                if isinstance(name, str) and str(name).strip()
            }
            supports_image = bool(row.get("image_route")) or bool(
                normalized_capabilities & {"image", "images", "image-generation", "image_generation"}
            )
            supports_embedding = bool(
                normalized_capabilities & {"embedding", "embeddings", "vector", "vectors"}
            )
            descriptor = ModelDescriptor(
                runtime_type=self.runtime_type,
                model_id=str(row["model_id"]),
                display_name=str(row["name"] or row["model_id"]),
                capabilities={name: "chat" for name in capability_names if isinstance(name, str)},
                reasoning_levels=("medium", "high"),
                context_window=128_000,
                available=True,
                capability_profile={
                    "extraction": "C2", "planning": "C2", "writing": "C2",
                    "review": "C2", "long_context": "C2", "tool_use": "C1",
                    "structured_output": "C2", "revision": "C2", "consistency": "C2",
                    # A model is image-capable only when the persisted model
                    # catalog says so or the author assigned the Image role.
                    # C0 prevents the aggregate C2 fallback from advertising
                    # a chat-only model to an image task.
                    "image": "C2" if supports_image else "C0",
                    "embedding": "C2" if supports_embedding else "C0",
                },
                provider_id=str(row["provider_id"]),
            )
            descriptors.append(descriptor)
        return tuple(descriptors)

    async def get_capabilities(self) -> RuntimeCapabilities:
        if not self._capabilities.models:
            await self.initialize()
        return self._capabilities

    async def get_usage(self) -> UsageSnapshot:
        # ``generation_runs`` is the legacy provider-attempt ledger and is
        # also populated inside a router-owned AgentRun.  Reporting it here
        # would double-count modern requests and would make the API adapter
        # disagree with Codex/CLI about the common Runtime Plane contract.
        return self.runs.usage_snapshot(self.runtime_type)

    async def shutdown(self) -> None:
        self._cancelled.clear()
