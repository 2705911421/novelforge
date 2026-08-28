"""Persistent model configuration, credential boundary, and invocation audit trail."""

from __future__ import annotations

import ctypes
import asyncio
import base64
from copy import deepcopy
import hashlib
import json
import logging
import math
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterable, Iterator, Optional, cast

import httpx

from src.core.database import Database, generate_id
from src.core.generation_attempts import (
    GenerationAttemptStore,
    RESPONSE_STATUSES,
    response_from_artifact,
    stable_hash,
)
from src.runtime.contracts import (
    AgentTask,
    AgentTaskProfile,
    AgentRunStatus,
    ComputePlan,
    ModelDescriptor,
    RuntimeEvent,
    default_agent_task_profile,
)
from src.runtime.persistence import AgentRunStore, AgentTaskStore
from src.context.bundles import ContextBundleStore
from src.runtime.errors import RuntimeUnavailable

from .gateway import ImageResponse, LLMConfig, LLMResponse, ModelGateway, ProviderType
from .agent_prompts import (
    DEFAULT_AGENT_SYSTEM_PROMPTS as STRUCTURED_AGENT_SYSTEM_PROMPTS,
    compose_agent_prompt,
    is_structured_agent_contract,
)


logger = logging.getLogger(__name__)


MODEL_ROLES = (
    "planner", "writer", "reviewer", "reviser", "context", "fact_extraction",
    "embedding", "rerank", "image",
)
PROVIDER_TYPES = {item.value for item in ProviderType}
PROVIDER_TYPE_ALIASES = {
    "mimo": "custom",
    "xiaomi-mimo": "custom",
    "xiaomi_mimo": "custom",
    "openai-compatible": "custom",
    "openai_compatible": "custom",
    "compatible": "custom",
}


def normalize_provider_type(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower()
    return PROVIDER_TYPE_ALIASES.get(normalized, normalized)


def normalize_base_url(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ModelConfigurationError("MODEL_CONFIGURATION", "provider base_url must be text")
    normalized = value.strip().rstrip("/")
    # Users commonly paste the complete OpenAI endpoint.  Persist the service
    # root so both /models discovery and /chat/completions invocation work.
    for suffix in ("/chat/completions", "/models"):
        if normalized.lower().endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            break
    return normalized

_LEGACY_AGENT_SYSTEM_PROMPTS = {
    "planner": "你是 NovelForge 的规划师。负责把作者的想法整理成可执行、可审阅的故事结构；先澄清缺口，再提出有因果关系的推进方案。",
    "writer": "你是 NovelForge 的小说写作者。严格遵守已确认的故事事实、章节计划和作品文风，只输出当前任务要求的内容。",
    "reviewer": "你是 NovelForge 的审稿人。依据作品事实和审查标准指出具体问题，给出可定位、可执行的修改建议，不替作者掩盖风险。",
    "reviser": "你是 NovelForge 的修订编辑。只根据已记录的审查问题改进草稿，保持已确认事实和作者意图不变。",
    "context": "你是 NovelForge 的上下文整理员。只汇总与当前任务相关的已确认资料，并标明来源和不确定之处。",
    "fact_extraction": "你是 NovelForge 的事实提取员。只从给定文本提取可追溯的故事事实，不补写文本中没有的内容。",
    "embedding": "你是 NovelForge 的检索索引助手。为检索任务处理结构化输入，不参与故事内容创作。",
    "rerank": "你是 NovelForge 的检索重排助手。依据查询意图和来源相关性排序候选资料，不改变原始事实。",
    "image": "你是 NovelForge 的视觉生成助手。依据作品设定和用户提示生成视觉资产，不虚构已生成的文件或结果。",
}

# Keep the public module constant stable for callers while making the
# structured, AGENTS.md-style contracts the only defaults used by routing.
DEFAULT_AGENT_SYSTEM_PROMPTS = STRUCTURED_AGENT_SYSTEM_PROMPTS


def _normalized_route_prompt(role: str, prompt: Any, *, use_default: bool = False) -> str:
    """Normalize persisted route values without letting legacy labels bypass the contract."""
    value = str(prompt or "").strip()
    if value == _LEGACY_AGENT_SYSTEM_PROMPTS.get(role, ""):
        value = ""
    if value == DEFAULT_AGENT_SYSTEM_PROMPTS.get(role, ""):
        value = ""
    if not value and use_default:
        return DEFAULT_AGENT_SYSTEM_PROMPTS.get(role, "")
    return value


def _effective_route_prompt(role: str, prompt: Any) -> str:
    """Always return a structured role contract plus any saved route override."""
    return compose_agent_prompt(role, _normalized_route_prompt(role, prompt))

# Safe, editable starting points.  They contain endpoints/model identifiers
# only; credentials are entered by the author and remain outside SQLite.
PROVIDER_PRESETS = (
    {
        "id": "openai",
        "name": "OpenAI",
        "providerType": "openai",
        "baseUrl": "https://api.openai.com/v1",
        "modelName": "OpenAI 通用模型",
        "modelId": "gpt-4o-mini",
        "credentialEnv": "OPENAI_API_KEY",
        "capabilities": ["chat", "json"],
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "providerType": "openai",
        "baseUrl": "https://api.deepseek.com",
        "modelName": "DeepSeek 通用模型",
        "modelId": "deepseek-v4-flash",
        "credentialEnv": "DEEPSEEK_API_KEY",
        "capabilities": ["chat", "json"],
    },
    {
        "id": "google-gemini",
        "name": "Google Gemini",
        "providerType": "gemini",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
        "modelName": "Gemini 通用模型",
        "modelId": "gemini-2.5-flash",
        "credentialEnv": "GEMINI_API_KEY",
        "capabilities": ["chat", "json"],
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "providerType": "anthropic",
        "baseUrl": "https://api.anthropic.com/v1",
        "modelName": "Claude 通用模型",
        "modelId": "claude-sonnet-4-20250514",
        "credentialEnv": "ANTHROPIC_API_KEY",
        "capabilities": ["chat", "json"],
    },
    {
        "id": "xiaomi-mimo",
        "name": "小米 MiMo",
        "providerType": "custom",
        "baseUrl": "https://api.xiaomimimo.com/v1",
        "modelName": "由供应商发现",
        "modelId": "",
        "credentialEnv": "MIMO_API_KEY",
        "capabilities": ["chat", "json"],
    },
)


class ModelConfigurationError(ValueError):
    """A persisted provider, model, or role route cannot safely be used."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CredentialError(ModelConfigurationError):
    """A credential reference cannot be persisted or resolved."""


_SENSITIVE_CONFIG_KEYS = frozenset({
    "apikey",
    "authorization",
    "accesstoken",
    "refreshtoken",
    "password",
    "secret",
    "secretkey",
    "credential",
    "credentials",
    "privatekey",
    "signingkey",
    "encryptionkey",
})


def _normalized_config_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _validate_safe_config(value: Any, *, path: str = "config") -> None:
    """Reject secret-shaped provider/model config before it reaches SQLite.

    Credentials have a dedicated protected-file or environment-reference
    boundary.  Arbitrary nested config is still supported for non-secret
    provider options, but a caller must not smuggle a second credential path
    through that JSON object.
    """
    if isinstance(value, dict):
        for key, nested in value.items():
            if _normalized_config_key(key) in _SENSITIVE_CONFIG_KEYS:
                raise ModelConfigurationError(
                    "MODEL_CONFIGURATION",
                    f"{path}.{key} must use apiKey or credentialEnv instead of persisted config",
                )
            _validate_safe_config(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _validate_safe_config(nested, path=f"{path}[{index}]")


def _redact_config(value: Any) -> Any:
    """Keep legacy rows readable without returning any stored secret value."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _normalized_config_key(key) in _SENSITIVE_CONFIG_KEYS else _redact_config(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_config(nested) for nested in value]
    return value


_STAGED_CREDENTIALS: ContextVar[list[str] | None] = ContextVar(
    "novelforge_staged_credentials", default=None,
)
_RETIRED_CREDENTIALS: ContextVar[list[str] | None] = ContextVar(
    "novelforge_retired_credentials", default=None,
)


class CredentialStore:
    """Keep raw secrets outside SQLite using user-scoped Windows DPAPI files."""

    def __init__(self, root: Path):
        self.root = root / ".novelforge-secrets"

    def store(self, secret: str) -> str:
        if not secret:
            raise CredentialError("MODEL_CREDENTIAL_UNAVAILABLE", "API Key must not be empty")
        if os.name != "nt":
            raise CredentialError(
                "MODEL_CREDENTIAL_STORAGE_UNAVAILABLE",
                "raw API Keys require Windows DPAPI; use an env: credential reference on this host",
            )
        self.root.mkdir(parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        target = self.root / f"{identifier}.bin"
        target.write_bytes(self._protect(secret.encode("utf-8")))
        return f"dpapi:{identifier}"

    def resolve(self, reference: Optional[str]) -> str:
        if not reference:
            raise CredentialError("MODEL_CREDENTIAL_UNAVAILABLE", "no credential is configured")
        if reference.startswith("env:"):
            name = reference.removeprefix("env:")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise CredentialError("MODEL_CREDENTIAL_UNAVAILABLE", "invalid credential environment reference")
            secret = os.environ.get(name, "")
            if not secret:
                raise CredentialError("MODEL_CREDENTIAL_UNAVAILABLE", "credential environment variable is not set")
            return secret
        if reference.startswith("dpapi:"):
            identifier = reference.removeprefix("dpapi:")
            if not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise CredentialError("MODEL_CREDENTIAL_UNAVAILABLE", "invalid protected credential reference")
            target = self.root / f"{identifier}.bin"
            if not target.is_file():
                raise CredentialError("MODEL_CREDENTIAL_UNAVAILABLE", "protected credential is unavailable")
            if os.name != "nt":
                raise CredentialError("MODEL_CREDENTIAL_STORAGE_UNAVAILABLE", "Windows DPAPI is unavailable")
            return self._unprotect(target.read_bytes()).decode("utf-8")
        raise CredentialError("MODEL_CREDENTIAL_UNAVAILABLE", "unsupported credential reference")

    def remove(self, reference: Optional[str]) -> None:
        """Remove a protected secret when its provider is explicitly deleted."""
        if not isinstance(reference, str) or not reference.startswith("dpapi:"):
            return
        identifier = reference.removeprefix("dpapi:")
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            return
        target = self.root / f"{identifier}.bin"
        try:
            target.unlink(missing_ok=True)
        except OSError:
            # A stale protected file must not prevent the provider record from
            # being removed; it contains no usable reference afterwards.
            return

    @staticmethod
    def _protect(data: bytes) -> bytes:
        return CredentialStore._crypt(data, protect=True)

    @staticmethod
    def _unprotect(data: bytes) -> bytes:
        return CredentialStore._crypt(data, protect=False)

    @staticmethod
    def _crypt(data: bytes, *, protect: bool) -> bytes:
        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        source_buffer = ctypes.create_string_buffer(data)
        source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
        destination = DataBlob()
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            raise CredentialError("MODEL_CREDENTIAL_STORAGE_UNAVAILABLE", "Windows DPAPI is unavailable")
        crypt32 = windll.crypt32
        kernel32 = windll.kernel32
        if protect:
            succeeded = crypt32.CryptProtectData(
                ctypes.byref(source), "NovelForge credential", None, None, None, 1, ctypes.byref(destination)
            )
        else:
            succeeded = crypt32.CryptUnprotectData(
                ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)
            )
        if not succeeded:
            raise CredentialError("MODEL_CREDENTIAL_STORAGE_UNAVAILABLE", "Windows DPAPI operation failed")
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            kernel32.LocalFree(destination.pbData)


class ModelRepository:
    """The only persistence boundary for model configuration and GenerationRuns."""

    def __init__(self, db: Database, credentials: CredentialStore):
        self.db = db
        self.credentials = credentials

    @contextmanager
    def _configuration_transaction(self) -> Iterator[Any]:
        """Make protected credential files follow the SQLite transaction."""
        staged: list[str] = []
        retired: list[str] = []
        token = _STAGED_CREDENTIALS.set(staged)
        retired_token = _RETIRED_CREDENTIALS.set(retired)
        try:
            with self.db.transaction() as conn:
                yield conn
        except Exception:
            # The DB transaction has rolled back; remove only references
            # created by this failed configuration attempt.
            for reference in staged:
                self.credentials.remove(reference)
            raise
        else:
            # SQLite now points at the replacement reference.  Remove only
            # the old protected files after commit; if validation failed above
            # the old reference remains usable and no secret is lost.
            for reference in retired:
                try:
                    self.credentials.remove(reference)
                except Exception as exc:
                    # Credential cleanup is best-effort after the durable
                    # pointer has moved; a stale file must not make a
                    # successful configuration update look like a failure.
                    logger.warning(
                        "retired model credential cleanup failed for %s: %s",
                        reference,
                        exc,
                        exc_info=exc,
                    )
                    continue
        finally:
            _STAGED_CREDENTIALS.reset(token)
            _RETIRED_CREDENTIALS.reset(retired_token)

    def configuration(self) -> dict[str, Any]:
        providers = [self._provider_dict(row) for row in self.db.fetchall(
            "SELECT * FROM model_providers ORDER BY name"
        )]
        models = [self._model_dict(row) for row in self.db.fetchall(
            "SELECT * FROM models ORDER BY name"
        )]
        route_rows = self.db.fetchall(
            "SELECT agent_role, model_id, system_prompt, system_prompt_version "
            "FROM agent_model_routes ORDER BY agent_role"
        )
        routes = {row["agent_role"]: row["model_id"] for row in route_rows}
        route_prompt_overrides = {
            role: _normalized_route_prompt(role, row.get("system_prompt"))
            for role, row in ((row["agent_role"], row) for row in route_rows)
        }
        route_prompts = {
            role: route_prompt_overrides.get(role) or DEFAULT_AGENT_SYSTEM_PROMPTS.get(role, "")
            for role in MODEL_ROLES
        }
        effective_route_prompts = {
            role: compose_agent_prompt(role, route_prompt_overrides.get(role, ""))
            for role in MODEL_ROLES
        }
        route_prompt_versions = {
            row["agent_role"]: int(row.get("system_prompt_version") or 0) for row in route_rows
        }
        return {
            "providers": providers,
            "models": models,
            "routes": routes,
            # routePrompts remains the compatibility field. New clients edit
            # routePromptOverrides and preview effectiveRoutePrompts.
            "routePrompts": route_prompts,
            "routePromptOverrides": {
                role: route_prompt_overrides.get(role, "") for role in MODEL_ROLES
            },
            "effectiveRoutePrompts": effective_route_prompts,
            "routePromptVersions": {
                role: route_prompt_versions.get(role, 0) for role in MODEL_ROLES
            },
            "defaultRoutePrompts": {
                role: DEFAULT_AGENT_SYSTEM_PROMPTS.get(role, "") for role in MODEL_ROLES
            },
            "roles": list(MODEL_ROLES),
            "presets": [dict(item) for item in PROVIDER_PRESETS],
        }

    def save_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        providers = payload.get("providers", [])
        models = payload.get("models", [])
        routes = payload.get("routes", {})
        system_prompts = payload.get(
            "routePromptOverrides",
            payload.get("systemPrompts", payload.get("routePrompts", {})),
        )
        if not isinstance(providers, list) or not isinstance(models, list) or not isinstance(routes, dict):
            raise ModelConfigurationError("MODEL_CONFIGURATION", "providers, models, and routes are required")
        if not isinstance(system_prompts, dict):
            raise ModelConfigurationError("MODEL_CONFIGURATION", "systemPrompts must be an object")
        with self._configuration_transaction() as conn:
            for item in providers:
                self._upsert_provider(conn, item)
            for item in models:
                self._upsert_model(conn, item)
            for role, route in routes.items():
                if role not in MODEL_ROLES:
                    raise ModelConfigurationError("MODEL_CONFIGURATION", "invalid agent role route")
                route_prompt_supplied = role in system_prompts
                if isinstance(route, dict):
                    model_id = route.get("modelId", route.get("model_id", route.get("id")))
                    if "systemPrompt" in route or "system_prompt" in route:
                        route_prompt_supplied = True
                        route_prompt = route.get("systemPrompt", route.get("system_prompt"))
                    else:
                        route_prompt = system_prompts.get(role)
                else:
                    model_id = route
                    route_prompt = system_prompts.get(role)
                if not isinstance(model_id, str) or not model_id.strip():
                    raise ModelConfigurationError("MODEL_CONFIGURATION", "route model id is required")
                usable = conn.execute(
                    """SELECT 1 FROM models m JOIN model_providers p ON p.id=m.provider_id
                       WHERE m.id=? AND m.enabled=TRUE AND p.enabled=TRUE""", (model_id,)
                ).fetchone()
                if not usable:
                    raise ModelConfigurationError("MODEL_ROUTE_UNAVAILABLE", f"route {role} has no enabled model")
                if route_prompt_supplied:
                    if not isinstance(route_prompt, str) or len(route_prompt) > 100_000:
                        raise ModelConfigurationError("MODEL_CONFIGURATION", "system prompt must be text under 100000 characters")
                    if route_prompt.lstrip().startswith("# NovelForge Agent Contract:") and not is_structured_agent_contract(route_prompt):
                        raise ModelConfigurationError(
                            "MODEL_CONFIGURATION",
                            "structured Agent Contract must include all required sections",
                        )
                    route_prompt = _normalized_route_prompt(role, route_prompt)
                    existing = conn.execute(
                        "SELECT system_prompt_version FROM agent_model_routes WHERE agent_role=?", (role,)
                    ).fetchone()
                    prompt_version = int(existing["system_prompt_version"] or 0) + 1 if existing else 1
                    conn.execute(
                        """INSERT INTO agent_model_routes(agent_role, model_id, system_prompt, system_prompt_version, updated_at)
                           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                           ON CONFLICT(agent_role) DO UPDATE SET model_id=excluded.model_id,
                           system_prompt=excluded.system_prompt, system_prompt_version=excluded.system_prompt_version,
                           updated_at=CURRENT_TIMESTAMP""",
                        (role, model_id.strip(), route_prompt, prompt_version),
                    )
                else:
                    conn.execute(
                        """INSERT INTO agent_model_routes(agent_role, model_id, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                           ON CONFLICT(agent_role) DO UPDATE SET model_id=excluded.model_id, updated_at=CURRENT_TIMESTAMP""",
                        (role, model_id.strip()),
                    )
        return self.configuration()

    def resolve(self, role: str, *, provider_id: Optional[str] = None,
                model_id: Optional[str] = None) -> dict[str, Any]:
        if role not in MODEL_ROLES:
            raise ModelConfigurationError("MODEL_ROUTE_UNAVAILABLE", "unknown agent role")
        if model_id:
            clauses = ["(m.id=? OR m.model_id=?)", "m.enabled=TRUE", "p.enabled=TRUE"]
            params: list[Any] = [model_id, model_id]
            if provider_id:
                clauses.append("p.id=?")
                params.append(provider_id)
            row = self.db.fetchone(
                f"""SELECT m.*, p.name AS provider_name, p.provider_type, p.base_url, p.credential_ref,
                          p.config AS provider_config, p.enabled AS provider_enabled,
                          r.system_prompt AS route_system_prompt,
                          r.system_prompt_version AS route_system_prompt_version
                   FROM models m JOIN model_providers p ON p.id=m.provider_id
                   LEFT JOIN agent_model_routes r ON r.model_id=m.id AND r.agent_role=?
                   WHERE {' AND '.join(clauses)} ORDER BY m.created_at LIMIT 1""",
                (role, *params),
            )
        elif provider_id:
            row = self.db.fetchone(
                """SELECT m.*, p.name AS provider_name, p.provider_type, p.base_url, p.credential_ref,
                          p.config AS provider_config, p.enabled AS provider_enabled,
                          '' AS route_system_prompt, 0 AS route_system_prompt_version
                   FROM models m JOIN model_providers p ON p.id=m.provider_id
                   WHERE p.id=? AND m.enabled=TRUE AND p.enabled=TRUE ORDER BY m.created_at LIMIT 1""", (provider_id,)
            )
        else:
            row = self.db.fetchone(
                """SELECT m.*, p.name AS provider_name, p.provider_type, p.base_url, p.credential_ref,
                          p.config AS provider_config, p.enabled AS provider_enabled,
                          r.system_prompt AS route_system_prompt,
                          r.system_prompt_version AS route_system_prompt_version
                   FROM agent_model_routes r JOIN models m ON m.id=r.model_id
                   JOIN model_providers p ON p.id=m.provider_id
                   WHERE r.agent_role=? AND m.enabled=TRUE AND p.enabled=TRUE""", (role,)
            )
        if not row:
            raise ModelConfigurationError("MODEL_ROUTE_UNAVAILABLE", f"no enabled model route for {role}")
        return dict(row)

    def validate_provider_assignment(self, role: str, provider_id: str) -> dict[str, Any]:
        """Validate an explicit provider before a GenerationRun is allocated.

        Simulation assignments are fail-closed: resolving the enabled model and
        credential here keeps missing/disabled configuration from falling into a
        global role route or leaving a misleading GenerationRun behind.
        """
        resolved = self.resolve(role, provider_id=provider_id)
        self.credentials.resolve(resolved.get("credential_ref"))
        return resolved

    def provider(self, provider_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM model_providers WHERE id=?", (provider_id,))
        if not row:
            raise ModelConfigurationError("MODEL_PROVIDER_NOT_FOUND", "model provider does not exist")
        return dict(row)

    def save_discovered_models(self, provider_id: str, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge a provider's model catalog without changing existing routes."""
        if not self.db.fetchone("SELECT 1 FROM model_providers WHERE id=?", (provider_id,)):
            raise ModelConfigurationError("MODEL_PROVIDER_NOT_FOUND", "model provider does not exist")
        persisted: list[dict[str, Any]] = []
        with self.db.transaction() as conn:
            for item in models:
                external_id = str(item.get("modelId") or "").strip()
                if not external_id:
                    continue
                discovered_name = str(item.get("name") or external_id).strip() or external_id
                capabilities = item.get("capabilities", ["chat"])
                if not isinstance(capabilities, list):
                    capabilities = ["chat"]
                existing = conn.execute(
                    "SELECT id, name FROM models WHERE provider_id=? AND model_id=? ORDER BY created_at LIMIT 1",
                    (provider_id, external_id),
                ).fetchone()
                if existing:
                    model_pk = existing["id"]
                    display_name = existing["name"] or discovered_name
                    if display_name == external_id:
                        display_name = discovered_name
                    conn.execute(
                        """UPDATE models SET name=?, capabilities=?, updated_at=CURRENT_TIMESTAMP
                           WHERE id=?""",
                        (display_name, json.dumps(capabilities, ensure_ascii=False), model_pk),
                    )
                else:
                    model_pk = "discovered-" + hashlib.sha256(
                        f"{provider_id}:{external_id}".encode("utf-8")
                    ).hexdigest()[:28]
                    conn.execute(
                        """INSERT INTO models(id, provider_id, name, model_id, capabilities, enabled, config,
                           created_at, updated_at) VALUES (?, ?, ?, ?, ?, TRUE, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                        (model_pk, provider_id, discovered_name, external_id,
                         json.dumps(capabilities, ensure_ascii=False)),
                    )
                row = conn.execute("SELECT * FROM models WHERE id=?", (model_pk,)).fetchone()
                persisted.append(self._model_dict(row))
        return sorted(persisted, key=lambda item: (item["name"], item["id"]))

    def delete_model(self, model_id: str) -> dict[str, Any]:
        """Delete one model and any role routes pointing at it."""
        if not isinstance(model_id, str) or not model_id.strip():
            raise ModelConfigurationError("MODEL_MODEL_NOT_FOUND", "model does not exist")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM models WHERE id=?", (model_id,)).fetchone()
            if not row:
                raise ModelConfigurationError("MODEL_MODEL_NOT_FOUND", "model does not exist")
            conn.execute("DELETE FROM agent_model_routes WHERE model_id=?", (model_id,))
            conn.execute("DELETE FROM models WHERE id=?", (model_id,))
        return self.configuration()

    def delete_provider(self, provider_id: str) -> dict[str, Any]:
        """Delete a provider, its models, routes, and protected credential."""
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ModelConfigurationError("MODEL_PROVIDER_NOT_FOUND", "model provider does not exist")
        credential_ref = None
        with self.db.transaction() as conn:
            provider = conn.execute(
                "SELECT credential_ref FROM model_providers WHERE id=?", (provider_id,)
            ).fetchone()
            if not provider:
                raise ModelConfigurationError("MODEL_PROVIDER_NOT_FOUND", "model provider does not exist")
            credential_ref = provider["credential_ref"]
            model_ids = [row["id"] for row in conn.execute(
                "SELECT id FROM models WHERE provider_id=?", (provider_id,)
            ).fetchall()]
            if model_ids:
                placeholders = ",".join("?" for _ in model_ids)
                conn.execute(
                    f"DELETE FROM agent_model_routes WHERE model_id IN ({placeholders})", model_ids
                )
            conn.execute("DELETE FROM models WHERE provider_id=?", (provider_id,))
            conn.execute("DELETE FROM model_providers WHERE id=?", (provider_id,))
        self.credentials.remove(credential_ref)
        return self.configuration()

    def create_run(self, *, task_id: str, role: str, resolved: dict[str, Any], prompt_key: Optional[str],
                   prompt_version: Optional[str], input_reference: dict[str, Any]) -> str:
        run_id = generate_id()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO generation_runs(id, task_id, agent_role, provider_id, model_id, prompt_key,
                   prompt_version, input_reference, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')""",
                (run_id, task_id, role, resolved["provider_id"], resolved["id"], prompt_key,
                 prompt_version, json.dumps(input_reference, ensure_ascii=False)),
            )
        return run_id

    def attach_context_manifest(self, run_id: str, manifest: dict[str, Any]) -> None:
        """Attach the exact source manifest after the run id is allocated."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT input_reference FROM generation_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ModelConfigurationError("GENERATION_RUN_NOT_FOUND", "generation run does not exist")
            input_reference = json.loads(row["input_reference"] or "{}")
            if not isinstance(input_reference, dict):
                input_reference = {}
            persisted_manifest = deepcopy(manifest)
            persisted_manifest["generationRunId"] = run_id
            input_reference["context_manifest"] = persisted_manifest
            conn.execute(
                "UPDATE generation_runs SET input_reference=? WHERE id=?",
                (json.dumps(input_reference, ensure_ascii=False), run_id),
            )

    def finish_run(self, run_id: str, response: LLMResponse) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE generation_runs SET status='succeeded', output_reference=?, prompt_tokens=?,
                   completion_tokens=?, total_tokens=?, latency_ms=?, completed_at=CURRENT_TIMESTAMP WHERE id=?""",
                (json.dumps({
                    # Model responses are Markdown in the Studio contract.
                    # Persist the source text and render it safely in the UI.
                    "content": response.content,
                    "content_type": "markdown",
                    "content_chars": len(response.content),
                    "finish_reason": response.finish_reason,
                }, ensure_ascii=False),
                 response.prompt_tokens, response.completion_tokens, response.tokens_used, response.latency_ms, run_id),
            )

    def fail_run(self, run_id: str, code: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE generation_runs SET status='failed', error_code=?, error_detail=?,
                   completed_at=CURRENT_TIMESTAMP WHERE id=?""", (code, "provider invocation failed", run_id),
            )

    def runs_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM generation_runs WHERE task_id=? ORDER BY started_at, id", (task_id,))
        return [self._run_dict(row) for row in rows]

    def _upsert_provider(self, conn: Any, item: Any) -> None:
        if not isinstance(item, dict):
            raise ModelConfigurationError("MODEL_CONFIGURATION", "provider must be an object")
        provider_id = item.get("id") if isinstance(item.get("id"), str) else generate_id()
        name = item.get("name")
        provider_type = normalize_provider_type(item.get("providerType", item.get("provider_type", "")))
        if not isinstance(name, str) or not name.strip() or provider_type not in PROVIDER_TYPES:
            raise ModelConfigurationError("MODEL_CONFIGURATION", "provider name and provider type are required")
        # SSRF protection: validate base_url to prevent requests to internal networks.
        base_url = normalize_base_url(item.get("baseUrl", item.get("base_url", "")))
        if base_url:
            import ipaddress
            import urllib.parse
            parsed = urllib.parse.urlparse(base_url)
            if parsed.scheme not in ("https", "http"):
                raise ModelConfigurationError("MODEL_CONFIGURATION", "provider base_url must use http or https")
            hostname = (parsed.hostname or "").lower()
            # Block obvious internal hostnames.
            _blocked_hosts = {"localhost", "metadata.google.internal", "metadata.goog",
                              "169.254.169.254", "instance-data"}
            if hostname in _blocked_hosts:
                raise ModelConfigurationError(
                    "MODEL_CONFIGURATION",
                    f"provider base_url must not target internal host: {hostname}"
                )
            # Use ipaddress module to check for private/loopback/link-local addresses.
            try:
                addr = ipaddress.ip_address(hostname)
                if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
                    raise ModelConfigurationError(
                        "MODEL_CONFIGURATION",
                        f"provider base_url must not target private/reserved address: {hostname}"
                    )
                # Block cloud metadata endpoint range (169.254.0.0/16).
                if addr.version == 4 and ipaddress.IPv4Address(hostname) in ipaddress.IPv4Network("169.254.0.0/16"):
                    raise ModelConfigurationError(
                        "MODEL_CONFIGURATION",
                        "provider base_url must not target link-local metadata endpoint"
                    )
            except ValueError:
                # Not an IP address - check for known internal hostname patterns.
                # DNS resolution at request time will fail naturally for
                # unreachable hosts; this catches explicit internal names.
                if hostname.endswith(".internal") or hostname.endswith(".local"):
                    raise ModelConfigurationError(
                        "MODEL_CONFIGURATION",
                        f"provider base_url must not target internal hostname: {hostname}"
                    )
        name_conflict = conn.execute(
            "SELECT id FROM model_providers WHERE name=? AND id<>?", (name.strip(), provider_id)
        ).fetchone()
        if name_conflict:
            raise ModelConfigurationError("MODEL_CONFIGURATION", "provider name is already in use")
        config = item.get("config", {})
        if not isinstance(config, dict):
            raise ModelConfigurationError("MODEL_CONFIGURATION", "provider config must be an object")
        # Validate before storing a raw API key so a rejected update cannot
        # leave an orphaned protected credential file behind.
        _validate_safe_config(config, path="provider.config")
        raw_key = item.get("apiKey", item.get("api_key", ""))
        env_ref = item.get("credentialEnv", item.get("credential_env", ""))
        if raw_key and env_ref:
            raise ModelConfigurationError("MODEL_CONFIGURATION", "provide either API Key or credential environment reference")
        existing = conn.execute("SELECT credential_ref FROM model_providers WHERE id=?", (provider_id,)).fetchone()
        credential_ref = existing["credential_ref"] if existing else None
        if raw_key:
            if not isinstance(raw_key, str):
                raise ModelConfigurationError("MODEL_CONFIGURATION", "API Key must be text")
            credential_ref = self.credentials.store(raw_key)
            staged = _STAGED_CREDENTIALS.get()
            if staged is not None:
                staged.append(credential_ref)
        elif env_ref:
            if not isinstance(env_ref, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_ref):
                raise ModelConfigurationError("MODEL_CONFIGURATION", "invalid credential environment variable")
            credential_ref = f"env:{env_ref}"
        if existing and existing["credential_ref"] and existing["credential_ref"] != credential_ref:
            retired = _RETIRED_CREDENTIALS.get()
            if retired is not None:
                retired.append(existing["credential_ref"])
        conn.execute(
            """INSERT INTO model_providers(id, name, provider_type, base_url, credential_ref, enabled, config,
               created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, provider_type=excluded.provider_type,
               base_url=excluded.base_url, credential_ref=excluded.credential_ref, enabled=excluded.enabled,
               config=excluded.config, updated_at=CURRENT_TIMESTAMP""",
            (provider_id, name.strip(), provider_type, base_url, credential_ref,
             bool(item.get("enabled", True)), json.dumps(config, ensure_ascii=False)),
        )

    @staticmethod
    def _upsert_model(conn: Any, item: Any) -> None:
        if not isinstance(item, dict):
            raise ModelConfigurationError("MODEL_CONFIGURATION", "model must be an object")
        model_id = item.get("id") if isinstance(item.get("id"), str) else generate_id()
        provider_id = item.get("providerId", item.get("provider_id"))
        name = item.get("name")
        external_id = item.get("modelId", item.get("model_id"))
        if not isinstance(provider_id, str) or not provider_id.strip() or not isinstance(external_id, str) or not external_id.strip():
            raise ModelConfigurationError("MODEL_CONFIGURATION", "model provider and model id are required")
        if name is None or (isinstance(name, str) and not name.strip()):
            name = external_id
        if not isinstance(name, str) or not name.strip():
            raise ModelConfigurationError("MODEL_CONFIGURATION", "model name must be text")
        if not conn.execute("SELECT 1 FROM model_providers WHERE id=?", (provider_id,)).fetchone():
            raise ModelConfigurationError("MODEL_CONFIGURATION", "model provider does not exist")
        config = item.get("config", {})
        capabilities = item.get("capabilities", [])
        if not isinstance(config, dict) or not isinstance(capabilities, list):
            raise ModelConfigurationError("MODEL_CONFIGURATION", "model config and capabilities are invalid")
        _validate_safe_config(config, path="model.config")
        conn.execute(
            """INSERT INTO models(id, provider_id, name, model_id, capabilities, enabled, config, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET provider_id=excluded.provider_id, name=excluded.name,
               model_id=excluded.model_id, capabilities=excluded.capabilities, enabled=excluded.enabled,
               config=excluded.config, updated_at=CURRENT_TIMESTAMP""",
            (model_id, provider_id, name.strip(), external_id.strip(), json.dumps(capabilities, ensure_ascii=False),
             bool(item.get("enabled", True)), json.dumps(config, ensure_ascii=False)),
        )

    @staticmethod
    def _provider_dict(row: Any) -> dict[str, Any]:
        return {"id": row["id"], "name": row["name"], "providerType": row["provider_type"],
                "baseUrl": row["base_url"] or "", "enabled": bool(row["enabled"]),
                "credentialConfigured": bool(row["credential_ref"]),
                "credentialSource": "environment" if str(row["credential_ref"] or "").startswith("env:") else "protected" if row["credential_ref"] else "none",
                "config": _redact_config(json.loads(row["config"] or "{}"))}

    @staticmethod
    def _model_dict(row: Any) -> dict[str, Any]:
        return {"id": row["id"], "providerId": row["provider_id"], "name": row["name"],
                "modelId": row["model_id"], "enabled": bool(row["enabled"]),
                "capabilities": json.loads(row["capabilities"] or "[]"),
                "config": _redact_config(json.loads(row["config"] or "{}"))}

    @staticmethod
    def _run_dict(row: Any) -> dict[str, Any]:
        result = dict(row)
        for field in ("input_reference", "output_reference"):
            result[field] = json.loads(result[field]) if result.get(field) else {}
        return result


class PersistentModelRuntime:
    """Resolve a persisted route and record every gateway invocation."""

    MAX_EMBEDDING_BATCH_SIZE = 64

    def __init__(self, repository: ModelRepository, gateway: Optional[Any] = None):
        self.repository = repository
        self.gateway = gateway or ModelGateway()
        self._task_id: ContextVar[Optional[str]] = ContextVar("novelforge_model_task_id", default=None)
        self._last_run_id: ContextVar[Optional[str]] = ContextVar("novelforge_model_last_run_id", default=None)
        self._last_agent_run_id: ContextVar[Optional[str]] = ContextVar("novelforge_agent_last_run_id", default=None)
        self._managed_agent_run_id: ContextVar[Optional[str]] = ContextVar(
            "novelforge_managed_agent_run_id", default=None
        )

    def validate_provider(self, provider_id: str, role: str) -> dict[str, Any]:
        """Validate an explicit provider without invoking the external gateway."""
        return self.repository.validate_provider_assignment(role, provider_id)

    @contextmanager
    def task_scope(self, task_id: str) -> Iterator[None]:
        token = self._task_id.set(task_id)
        try:
            yield
        finally:
            self._task_id.reset(token)

    def current_task_id(self) -> str | None:
        return self._task_id.get()

    @contextmanager
    def managed_agent_run(self, run_id: str) -> Iterator[None]:
        """Mark the invocation as owned by the common RuntimeRouter.

        The legacy provider runtime still owns GenerationRun and retry audit
        records, but it must not create a second AgentRun when an adapter is
        executing under the Control Plane.
        """
        token = self._managed_agent_run_id.set(run_id)
        try:
            yield
        finally:
            self._managed_agent_run_id.reset(token)

    def last_generation_run_id(self) -> str | None:
        """Return the latest GenerationRun created/recovered in this task context."""
        return self._last_run_id.get()

    def last_agent_run_id(self) -> str | None:
        """Return the outer AgentRun for the latest provider invocation."""
        return self._managed_agent_run_id.get() or self._last_agent_run_id.get()

    @staticmethod
    def _build_prompt_layout(
        effective_system: str,
        messages: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        """Build the exact persisted prompt and its character-level segments."""
        parts: list[str] = [f"[system]\n{effective_system}" if effective_system else ""]
        specs: list[tuple[str, Optional[int], str, str]] = []
        if effective_system:
            specs.append(("system", None, "system", effective_system))
        for index, message in enumerate(messages):
            role = str(message.get("role", "message"))
            content = str(message.get("content", ""))
            parts.append(f"[{role}]\n{content}")
            specs.append((f"message:{index}", index, role, content))

        raw_prompt = "\n\n".join(parts)
        prompt = raw_prompt.strip()
        left_trim = len(raw_prompt) - len(raw_prompt.lstrip())
        segments: list[dict[str, Any]] = []
        cursor = 0
        spec_index = 0
        for part_index, part in enumerate(parts):
            if part_index:
                cursor += 2
            if spec_index < len(specs):
                segment_id, message_index, role, content = specs[spec_index]
                marker = f"[{role}]\n"
                if part.startswith(marker):
                    content_start = max(0, cursor + len(marker) - left_trim)
                    content_end = min(len(prompt), content_start + len(content))
                    segments.append({
                        "id": segment_id,
                        "role": role,
                        "messageIndex": message_index,
                        "markerStart": max(0, cursor - left_trim),
                        "markerEnd": max(0, cursor + len(marker) - left_trim),
                        "contentStart": content_start,
                        "contentEnd": content_end,
                        "binding": "exact_persisted_prompt",
                    })
                    spec_index += 1
            cursor += len(part)

        return prompt, {
            "scope": "persisted_generation_input",
            "binding": "exact_persisted_prompt",
            "charCount": len(prompt),
            "segments": segments,
        }

    @staticmethod
    def _bind_context_manifest_to_prompt_layout(
        manifest: dict[str, Any],
        prompt_layout: dict[str, Any],
    ) -> None:
        """Rebase pipeline ranges from the user message into persisted input."""
        segments = prompt_layout.get("segments")
        if not isinstance(segments, list):
            return
        message_segments = {
            int(segment["messageIndex"]): segment
            for segment in segments
            if isinstance(segment, dict) and segment.get("messageIndex") is not None
        }

        def rebase(value: dict[str, Any]) -> None:
            source_range = value.get("promptRange")
            if not isinstance(source_range, dict) or source_range.get("scope") != "writer_user_message":
                return
            try:
                start = int(source_range["start"])
                end = int(source_range["end"])
            except (KeyError, TypeError, ValueError):
                value["persistedPromptRangeStatus"] = "invalid"
                return
            message_index = int(source_range.get("messageIndex", 0))
            segment = message_segments.get(message_index)
            if segment is None:
                value["persistedPromptRangeStatus"] = "message_not_found"
                return
            message_start = int(segment["contentStart"])
            message_end = int(segment["contentEnd"])
            if start < 0 or end < start or end > message_end - message_start:
                value["persistedPromptRangeStatus"] = "outside_message"
                return
            value["persistedPromptRange"] = {
                "scope": "persisted_generation_input",
                "messageIndex": message_index,
                "start": message_start + start,
                "end": message_start + end,
                "precision": source_range.get("precision", "exact"),
            }
            value["persistedPromptRangeStatus"] = "exact"

        for collection_name in ("items", "compiledItems", "contextSections"):
            collection = manifest.get(collection_name)
            if isinstance(collection, list):
                for item in collection:
                    if isinstance(item, dict):
                        rebase(item)
        writer_input = manifest.get("writerInput")
        if isinstance(writer_input, dict):
            components = writer_input.get("components")
            if isinstance(components, list):
                for component in components:
                    if isinstance(component, dict):
                        rebase(component)
        prompt_components = manifest.get("promptComponents")
        if isinstance(prompt_components, list):
            for component in prompt_components:
                if isinstance(component, dict):
                    rebase(component)

        binding = manifest.get("promptBinding")
        if isinstance(binding, dict):
            binding["persistedScope"] = "input_reference.prompt"
            binding["persistedLayout"] = "input_reference.promptLayout"
            binding["messageIndex"] = 0
            binding["persistedAvailable"] = any(
                isinstance(item, dict) and item.get("persistedPromptRangeStatus") == "exact"
                for collection_name in ("items", "compiledItems", "contextSections")
                for item in (manifest.get(collection_name) or [])
            )

    def _ensure_agent_task(self, task_id: str, task_type: str, role: str) -> AgentTask:
        """Create the compatibility AgentTask envelope once per durable task."""
        db = self.repository.db
        existing = db.fetchone("SELECT * FROM agent_tasks WHERE task_id=?", (task_id,))
        if existing:
            return self._agent_task_from_row(existing, role=role, task_type=task_type)
        durable = db.fetchone("SELECT * FROM tasks WHERE id=?", (task_id,))
        if durable is None:
            raise ModelConfigurationError("MODEL_TASK_NOT_FOUND", "durable task does not exist")
        # Older durable rows may reach this compatibility bridge before their
        # enqueue-time AgentTask projection exists.  Keep the durable task
        # type as the domain identity; the call-site stage remains provider
        # telemetry, not a replacement for the NovelForge task contract.
        durable_task_type = str(durable.get("type") or "").strip()
        effective_task_type = durable_task_type or task_type
        project_id = durable.get("project_id")
        if project_id and not db.fetchone("SELECT id FROM projects WHERE id=?", (project_id,)):
            project_id = None
        try:
            raw_data = durable.get("data")
            durable_data = json.loads(raw_data or "{}") if isinstance(raw_data, str) else raw_data
        except (TypeError, json.JSONDecodeError):
            durable_data = {}
        if not isinstance(durable_data, dict):
            durable_data = {}

        # This path exists for legacy task rows created before the AgentTask
        # projection was added.  Keep the durable task's domain envelope
        # intact so compatibility recovery does not lose initiator, policy,
        # or lineage metadata at the adapter boundary.
        input_payload = dict(durable_data)
        input_payload.setdefault("durableTaskId", task_id)
        initiated_by = str(
            input_payload.get("initiatedBy")
            or input_payload.get("initiated_by")
            or input_payload.get("source")
            or "system"
        ).strip() or "system"
        input_payload.setdefault("initiatedBy", initiated_by)
        constraints = durable_data.get("constraints")
        if not isinstance(constraints, dict):
            constraints = {}
        expected_output = str(
            durable_data.get("expected_output")
            or durable_data.get("expectedOutput")
            or "AgentArtifact"
        )

        chapter_id = durable_data.get("chapter_id") or durable_data.get("chapterId")
        if chapter_id and not db.fetchone("SELECT id FROM chapters WHERE id=?", (chapter_id,)):
            chapter_id = None
        intent_id = durable_data.get("intent_id") or durable_data.get("intentId")
        context_bundle_id = durable_data.get("context_bundle_id") or durable_data.get("contextBundleId")
        if context_bundle_id and not db.fetchone(
            "SELECT id FROM context_bundles WHERE id=?", (context_bundle_id,)
        ):
            context_bundle_id = None
        parent_task_id = durable_data.get("parent_task_id") or durable_data.get("parentTaskId")
        parent_agent_task_id = None
        if parent_task_id:
            parent_row = db.fetchone("SELECT id FROM agent_tasks WHERE task_id=?", (str(parent_task_id),))
            parent_agent_task_id = parent_row["id"] if parent_row else None
        agent_task_id = f"agent-{task_id}"
        profile = default_agent_task_profile(role, effective_task_type)
        now = datetime.now().isoformat()
        with db.transaction() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO agent_tasks(
                    id, task_id, task_type, role, project_id, chapter_id,
                       intent_id, context_bundle_id, constraints, expected_output,
                       input_payload, profile, parent_task_id, status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)""",
                (
                    agent_task_id, task_id, effective_task_type, role, project_id,
                    chapter_id, intent_id, context_bundle_id,
                    json.dumps(constraints, ensure_ascii=False), expected_output,
                    json.dumps(input_payload, ensure_ascii=False),
                    json.dumps(profile.to_dict(), ensure_ascii=False), parent_agent_task_id,
                    now, now,
                ),
            )
            row = conn.execute("SELECT * FROM agent_tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._agent_task_from_row(dict(row), role=role, task_type=effective_task_type)

    def _context_manifest_for_task(
        self,
        task_id: str,
        *,
        task_stage: str,
        role: str,
    ) -> dict[str, Any]:
        """Return an explicit context snapshot when a legacy caller omitted one."""
        return ContextBundleStore(self.repository.db).manifest_for_task(
            durable_task_id=task_id,
            task_stage=task_stage,
            role=role,
            source="compatibility-runtime-bridge",
        )

    @staticmethod
    def _agent_task_from_row(row: dict[str, Any], *, role: str, task_type: str) -> AgentTask:
        def decode(name: str, default: Any) -> Any:
            try:
                value = json.loads(row.get(name) or "")
                return value if value is not None else default
            except (TypeError, json.JSONDecodeError):
                return default

        profile_data = decode("profile", {})
        if not isinstance(profile_data, dict):
            profile_data = {}
        default_profile = default_agent_task_profile(
            str(row.get("role") or role),
            str(row.get("task_type") or task_type),
        )
        compute_profile_keys = ("allowedComputeTools", "allowed_compute_tools")
        compute_default = (
            ()
            if any(key in profile_data for key in compute_profile_keys)
            else default_profile.allowed_compute_tools
        )
        legacy_empty_narrative_profile = AgentTaskStore._is_legacy_empty_narrative_profile(
            profile_data
        )

        def profile_tuple(
            *names: str,
            default: tuple[str, ...],
            default_on_empty: bool = False,
        ) -> tuple[str, ...]:
            value = next((profile_data[name] for name in names if name in profile_data), default)
            if not isinstance(value, (list, tuple)):
                return ()
            normalized = tuple(str(item) for item in value if str(item).strip())
            return default if not normalized and default_on_empty else normalized

        profile = AgentTaskProfile(
            role=str(profile_data.get("role") or row.get("role") or role),
            task_type=str(profile_data.get("taskType") or row.get("task_type") or task_type),
            allowed_tools=profile_tuple(
                "allowedTools",
                "allowed_tools",
                default=default_profile.allowed_tools,
                default_on_empty=legacy_empty_narrative_profile,
            ),
            forbidden_tools=profile_tuple(
                "forbiddenTools",
                "forbidden_tools",
                default=default_profile.forbidden_tools,
                default_on_empty=legacy_empty_narrative_profile,
            ),
            allowed_compute_tools=profile_tuple(
                "allowedComputeTools", "allowed_compute_tools", default=compute_default,
            ),
            minimum_capability=str(profile_data.get("minimumCapability") or default_profile.minimum_capability),
            preferred_capability=str(profile_data.get("preferredCapability") or default_profile.preferred_capability),
            maximum_capability=str(profile_data.get("maximumCapability") or default_profile.maximum_capability),
            minimum_reasoning=str(profile_data.get("minimumReasoning") or default_profile.minimum_reasoning),
            preferred_reasoning=str(profile_data.get("preferredReasoning") or default_profile.preferred_reasoning),
            maximum_reasoning=str(profile_data.get("maximumReasoning") or default_profile.maximum_reasoning),
        )
        constraints = decode("constraints", {})
        input_payload = decode("input_payload", {})
        initiated_by = str(
            input_payload.get("initiatedBy")
            or input_payload.get("initiated_by")
            or input_payload.get("source")
            or "system"
        ).strip() or "system" if isinstance(input_payload, dict) else "system"
        return AgentTask(
            task_id=str(row["id"]),
            task_type=str(row.get("task_type") or task_type),
            role=str(row.get("role") or role),
            project_id=row.get("project_id"),
            chapter_id=row.get("chapter_id"),
            intent_id=row.get("intent_id"),
            context_bundle_id=row.get("context_bundle_id"),
            constraints=constraints if isinstance(constraints, dict) else {},
            expected_output=str(row.get("expected_output") or "AgentArtifact"),
            input_payload=input_payload if isinstance(input_payload, dict) else {},
            profile=profile,
            parent_task_id=row.get("parent_task_id"),
            created_at=str(row.get("created_at") or datetime.now().isoformat()),
            initiated_by=initiated_by,
        )

    def _start_agent_run(
        self,
        *,
        agent_task: AgentTask,
        durable_task_id: str,
        resolved: dict[str, Any],
        prompt_version: str,
        context_manifest: dict[str, Any] | None,
        reasoning: str,
        output_budget: int,
    ) -> tuple[AgentRunStore, str]:
        store = AgentRunStore(self.repository.db)
        context_bundle_id = self.ensure_context_bundle(
            durable_task_id=durable_task_id,
            agent_task=agent_task,
            context_manifest=context_manifest,
        )
        plan = ComputePlan(
            plan_id=generate_id(),
            runtime_type="api",
            model_id=str(resolved.get("model_id") or resolved.get("id") or "unknown"),
            reasoning=reasoning,
            capability="C2",
            context_budget=max(0, len(json.dumps(context_manifest or {}, ensure_ascii=False)) * 2),
            output_budget=max(0, int(output_budget or 0)),
            maximum_escalation="C3",
            maximum_reasoning="xhigh",
            rationale=("legacy PersistentModelRuntime compatibility adapter",),
            provider_id=str(resolved.get("provider_id") or "").strip() or None,
        )
        run = store.create(
            task=agent_task,
            durable_task_id=durable_task_id,
            compute_plan=plan,
            context_bundle_id=context_bundle_id,
            prompt_version=prompt_version,
        )
        run_id = str(run["id"])
        store.append_event(
            run_id, agent_task,
            RuntimeEvent("api", "turn.started", {"generationRunId": None}, agent_run_id=run_id),
        )
        return store, run_id

    def ensure_context_bundle(
        self,
        *,
        durable_task_id: str,
        agent_task: AgentTask,
        context_manifest: dict[str, Any] | None,
    ) -> str | None:
        """Bind a task to an immutable context snapshot before an AgentRun."""
        if not isinstance(context_manifest, dict):
            if agent_task.context_bundle_id:
                return agent_task.context_bundle_id
            context_manifest = self._context_manifest_for_task(
                durable_task_id,
                task_stage=agent_task.task_type,
                role=agent_task.role,
            )
        task_row = self.repository.db.fetchone(
            "SELECT project_id, book_id FROM tasks WHERE id=?", (durable_task_id,)
        ) or {}
        task_project_id = task_row.get("project_id")
        task_book_id = task_row.get("book_id")
        bound_row = self.repository.db.fetchone(
            "SELECT context_bundle_id, project_id FROM agent_tasks WHERE id=?",
            (agent_task.task_id,),
        ) or {}
        task_project_id = task_project_id or bound_row.get("project_id")
        bound_context_id = str(bound_row.get("context_bundle_id") or "").strip() or None
        candidate = context_manifest.get("bundleId") or context_manifest.get("contextBundleId")
        context_bundle_id = None
        if candidate and self.repository.db.fetchone(
            "SELECT id FROM context_bundles WHERE id=?", (candidate,)
        ):
            context_bundle_id = str(candidate)
            if bound_context_id is not None and context_bundle_id != bound_context_id:
                raise ValueError("context bundle does not match the persisted AgentTask")
            self._validate_context_bundle_scope(
                context_bundle_id,
                project_id=task_project_id,
                book_id=task_book_id,
            )
        elif bound_context_id is not None:
            # Do not create a new unbound snapshot and then return it while
            # COALESCE keeps the AgentTask pointing at the older one.  The
            # recorded context must be the same context the adapter uses.
            context_bundle_id = bound_context_id
            self._validate_context_bundle_scope(
                context_bundle_id,
                project_id=task_project_id,
                book_id=task_book_id,
            )
        if context_bundle_id is None:
            bundle = ContextBundleStore(self.repository.db).create_from_manifest(
                context_manifest,
                project_id=(
                    context_manifest.get("projectId")
                    or task_project_id
                ),
                book_id=context_manifest.get("bookId") or task_book_id,
                source="PersistentModelRuntime",
                task_id=durable_task_id,
                role=agent_task.role,
                expected_project_id=task_project_id,
                expected_book_id=task_book_id,
            )
            context_bundle_id = bundle.bundle_id
        self.repository.db.execute(
            "UPDATE agent_tasks SET context_bundle_id=COALESCE(context_bundle_id, ?), "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (context_bundle_id, agent_task.task_id),
        )
        return context_bundle_id

    def _validate_context_bundle_scope(
        self,
        bundle_id: str,
        *,
        project_id: str | None,
        book_id: str | None,
    ) -> None:
        bundle = ContextBundleStore(self.repository.db).get(bundle_id)
        if bundle is None:
            raise ValueError("context bundle does not exist")
        if project_id and bundle.project_id and str(project_id) != str(bundle.project_id):
            raise ValueError("context bundle is outside the project scope")
        if book_id and bundle.book_id and str(book_id) != str(bundle.book_id):
            raise ValueError("context bundle is outside the book scope")

    @staticmethod
    def _agent_run_failed(store: AgentRunStore, run_id: str, task: AgentTask, code: str, detail: str) -> None:
        current = store.get(run_id) or {}
        if current.get("status") in {AgentRunStatus.RUNNING.value, AgentRunStatus.PAUSED.value}:
            store.transition(run_id, AgentRunStatus.FAILED.value, error_code=code, error_detail=detail)
        store.append_event(
            run_id, task, RuntimeEvent("api", "error", {"code": code, "detail": detail}, agent_run_id=run_id)
        )

    @staticmethod
    def _agent_run_succeeded(store: AgentRunStore, run_id: str, task: AgentTask, response: LLMResponse) -> None:
        artifact = {
            "content": response.content,
            "contentType": "markdown",
            "model": response.model,
            "provider": response.provider,
        }
        usage = {
            "inputTokens": response.prompt_tokens,
            "outputTokens": response.completion_tokens,
            "totalTokens": response.tokens_used,
            "latencyMs": response.latency_ms,
        }
        store.transition(run_id, AgentRunStatus.SUCCEEDED.value, usage=usage, artifacts=artifact)
        store.append_event(
            run_id, task,
            RuntimeEvent("api", "turn.completed", {"artifact": artifact, "usage": usage}, agent_run_id=run_id),
        )

    def invoke(self, role: str, messages: list[dict[str, Any]], system: str = "", *, json_mode: bool = False,
               provider_id: Optional[str] = None, **kwargs: Any) -> LLMResponse:
        task_id = self._task_id.get()
        if not task_id:
            raise ModelConfigurationError("MODEL_TASK_CONTEXT_REQUIRED", "model invocation requires a durable task")
        selected_model_id = kwargs.pop("model_id", None)
        resolved = self.repository.resolve(role, provider_id=provider_id, model_id=selected_model_id)
        route_system_prompt = _effective_route_prompt(role, resolved.get("route_system_prompt"))
        caller_system = str(system or "").strip()
        if route_system_prompt and caller_system:
            effective_system = route_system_prompt + "\n\n" + caller_system
        else:
            effective_system = route_system_prompt or caller_system or DEFAULT_AGENT_SYSTEM_PROMPTS.get(role, "")
        prompt_key = kwargs.pop("prompt_key", None) or f"agent-route:{role}:system"
        prompt_version = kwargs.pop("prompt_version", None)
        prompt_registry = kwargs.pop("prompt_registry", None)
        if not isinstance(prompt_registry, dict):
            prompt_registry = None
        task_stage = str(kwargs.pop("task_stage", "") or role)
        reasoning = str(kwargs.pop("reasoning", "high") or "high")
        context_manifest = kwargs.pop("context_manifest", None)
        if not prompt_version:
            configured_version = int(resolved.get("route_system_prompt_version") or 0)
            prompt_version = str(configured_version) if configured_version else "builtin-1"
        prompt_sha256 = hashlib.sha256(effective_system.encode("utf-8")).hexdigest()
        persisted_prompt, prompt_layout = self._build_prompt_layout(effective_system, messages)
        runtime_context_manifest = (
            deepcopy(context_manifest)
            if isinstance(context_manifest, dict)
            else self._context_manifest_for_task(task_id, task_stage=task_stage, role=role)
        )
        self._bind_context_manifest_to_prompt_layout(runtime_context_manifest, prompt_layout)
        # Bind the compatibility invocation to one immutable Host-owned
        # ContextBundle before deriving the generation idempotency key.  The
        # first call used to hash the metadata-only fallback, while a retry
        # after AgentTask/ContextBundle creation hashed the same snapshot with
        # ``bundleId`` attached.  That made a worker re-call the provider after
        # its response had already been persisted.  Canonicalising the bundle
        # first keeps retries on the exact same request identity.
        agent_task = self._ensure_agent_task(task_id, task_stage, role)
        context_bundle_id = self.ensure_context_bundle(
            durable_task_id=task_id,
            agent_task=agent_task,
            context_manifest=runtime_context_manifest,
        )
        if context_bundle_id:
            bundle = ContextBundleStore(self.repository.db).get(context_bundle_id)
            if bundle is not None:
                runtime_context_manifest = bundle.manifest()
                self._bind_context_manifest_to_prompt_layout(runtime_context_manifest, prompt_layout)
        input_reference = {
            # Keep the complete prompt alongside its audit metadata. The
            # Studio task detail view must show the exact model input.
            "system_prompt": effective_system,
            "messages": messages,
            "prompt": persisted_prompt,
            "promptLayout": prompt_layout,
            "message_count": len(messages),
            "system_chars": len(effective_system),
            "message_chars": sum(len(str(message.get("content", ""))) for message in messages),
            "prompt_sha256": prompt_sha256,
            "persisted_prompt_sha256": hashlib.sha256(persisted_prompt.encode("utf-8")).hexdigest(),
            "prompt_source": "agent-contract+route-override",
            "prompt_registry": deepcopy(prompt_registry),
            "context_manifest": runtime_context_manifest,
        }
        context_hash = stable_hash(runtime_context_manifest or {})
        request_hash = stable_hash({
            "taskStage": task_stage,
            "role": role,
            "providerId": resolved.get("provider_id"),
            "modelId": resolved.get("id"),
            "externalModelId": resolved.get("model_id"),
            "promptKey": prompt_key,
            "promptVersion": prompt_version,
            "promptHash": prompt_sha256,
            "persistedPromptHash": input_reference["persisted_prompt_sha256"],
            "promptRegistry": prompt_registry,
            "contextHash": context_hash,
            "messages": messages,
            "jsonMode": bool(json_mode),
            "reasoning": reasoning,
            "options": kwargs,
        })
        base_idempotency_key = f"{task_id}:{task_stage}:{request_hash}"
        attempts = GenerationAttemptStore(self.repository.db)
        agent_store: AgentRunStore | None = None
        agent_run_id: str | None = None

        def recover(existing: dict[str, Any]) -> LLMResponse:
            self._last_run_id.set(str(existing.get("generation_run_id") or "") or None)
            response = response_from_artifact(existing.get("response_artifact"))
            run = self.repository.db.fetchone(
                "SELECT status FROM generation_runs WHERE id=?",
                (existing.get("generation_run_id"),),
            )
            if run is not None and run["status"] != "succeeded":
                self.repository.finish_run(existing["generation_run_id"], response)
            recovered_agent = self.repository.db.fetchone(
                "SELECT id FROM agent_runs WHERE task_id=? AND status IN ('running', 'paused') ORDER BY started_at DESC LIMIT 1",
                (task_id,),
            )
            if recovered_agent and not self._managed_agent_run_id.get():
                recovered_task = self._ensure_agent_task(task_id, task_stage, role)
                recovered_store = AgentRunStore(self.repository.db)
                self._last_agent_run_id.set(str(recovered_agent["id"]))
                self._agent_run_succeeded(recovered_store, str(recovered_agent["id"]), recovered_task, response)
            attempts.consume(existing["id"])
            return response

        existing = attempts.by_idempotency(base_idempotency_key)
        if existing and existing.get("status") in RESPONSE_STATUSES and existing.get("response_artifact"):
            return recover(existing)
        existing_attempts = [
            item for item in attempts.for_task(task_id)
            if item.get("request_hash") == request_hash and item.get("task_stage") == task_stage
        ]
        if existing_attempts:
            latest = max(existing_attempts, key=lambda item: int(item.get("attempt_number") or 0))
            if latest.get("status") in {"prepared", "requesting"}:
                attempts.abandon(latest["id"])
                self.repository.fail_run(latest["generation_run_id"], "GENERATION_ATTEMPT_RETRY")
            attempt_number = int(latest.get("attempt_number") or 0) + 1
            idempotency_key = f"{base_idempotency_key}:retry:{attempt_number}"
            retry_existing = attempts.by_idempotency(idempotency_key)
            if retry_existing and retry_existing.get("status") in RESPONSE_STATUSES and retry_existing.get("response_artifact"):
                return recover(retry_existing)
        else:
            attempt_number = 1
            idempotency_key = base_idempotency_key
        run_id = self.repository.create_run(
            task_id=task_id, role=role, resolved=resolved,
            prompt_key=prompt_key, prompt_version=prompt_version,
            input_reference=input_reference,
        )
        self._last_run_id.set(run_id)
        attempt = attempts.prepare(
            generation_run_id=run_id,
            task_id=task_id,
            task_stage=task_stage,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            provider_id=str(resolved.get("provider_id") or ""),
            model_id=str(resolved.get("id") or ""),
            prompt_key=str(prompt_key),
            prompt_version=str(prompt_version),
            prompt_hash=prompt_sha256,
            context_hash=context_hash,
        )
        if attempt.get("status") in RESPONSE_STATUSES and attempt.get("response_artifact"):
            self.repository.fail_run(run_id, "GENERATION_ATTEMPT_REUSED")
            return recover(attempt)
        if runtime_context_manifest is not None:
            self.repository.attach_context_manifest(run_id, runtime_context_manifest)
        if not self._managed_agent_run_id.get():
            agent_store, agent_run_id = self._start_agent_run(
                agent_task=agent_task,
                durable_task_id=task_id,
                resolved=resolved,
                prompt_version=str(prompt_version),
                context_manifest=runtime_context_manifest,
                reasoning=reasoning,
                output_budget=int(kwargs.get("max_tokens") or 0),
            )
            self._last_agent_run_id.set(agent_run_id)
        try:
            secret = self.repository.credentials.resolve(resolved.get("credential_ref"))
        except CredentialError as exc:
            attempts.fail(attempt["id"], exc.code, str(exc))
            self.repository.fail_run(run_id, exc.code)
            if agent_store is not None and agent_run_id is not None:
                self._agent_run_failed(agent_store, agent_run_id, agent_task, exc.code, str(exc))
            raise
        try:
            model_config = json.loads(resolved.get("config") or "{}")
            provider_config = json.loads(resolved.get("provider_config") or "{}")
            config = LLMConfig(
                provider=ProviderType(resolved["provider_type"]), model=resolved["model_id"],
                base_url=resolved.get("base_url") or "", api_key=secret,
                temperature=float(model_config.get("temperature", 0.8)),
                max_tokens=int(model_config.get("max_tokens", 8000)),
                timeout=int(provider_config.get("timeout", 300)),
            )
            provider_name = f"run-{resolved['provider_id']}"
            self.gateway.register_provider(provider_name, config)
        except Exception as exc:
            attempts.fail(attempt["id"], "MODEL_CONFIGURATION", str(exc))
            self.repository.fail_run(run_id, "MODEL_CONFIGURATION")
            if agent_store is not None and agent_run_id is not None:
                self._agent_run_failed(agent_store, agent_run_id, agent_task, "MODEL_CONFIGURATION", str(exc))
            raise ModelConfigurationError("MODEL_CONFIGURATION", "model configuration error") from exc
        try:
            attempts.mark_requesting(attempt["id"])
            response = self.gateway.chat(provider_name, messages, effective_system, json_mode=json_mode, **kwargs)
            if not isinstance(response, LLMResponse):
                raise ModelConfigurationError(
                    "PROVIDER_INVALID_RESPONSE", "model provider returned an invalid response"
                )
            if not response.content or not response.content.strip():
                raise ModelConfigurationError(
                    "PROVIDER_EMPTY_RESPONSE", "model provider returned an empty response"
                )
        except Exception as exc:
            code = getattr(exc, "code", None) or self._error_code(exc)
            attempts.fail(attempt["id"], code, str(exc))
            self.repository.fail_run(run_id, code)
            if agent_store is not None and agent_run_id is not None:
                self._agent_run_failed(agent_store, agent_run_id, agent_task, code, str(exc))
            raise ModelConfigurationError(code, "model provider invocation failed") from exc
        attempts.persist_response(attempt["id"], response)
        self.repository.finish_run(run_id, response)
        if agent_store is not None and agent_run_id is not None:
            self._agent_run_succeeded(agent_store, agent_run_id, agent_task, response)
        attempts.consume(attempt["id"])
        return response

    def test_provider(self, provider_id: str) -> LLMResponse:
        return self.invoke("writer", [{"role": "user", "content": "Connection check"}], provider_id=provider_id,
                           max_tokens=10)

    def embed(
        self,
        text: str,
        *,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> list[float]:
        """Invoke the persisted embedding route inside the model-runtime seam.

        Embeddings are a derived RAG projection rather than narrative output,
        so they do not use the chat ``GenerationRun`` response contract.  They
        still require the same durable-task context as every production model
        call, resolve credentials through the Host-owned repository, and keep
        provider-specific HTTP out of the RAG layer.
        """
        return self._embed_request(
            [text],
            provider_id=provider_id,
            model_id=model_id,
            scalar_input=True,
        )[0]

    def embed_many(
        self,
        texts: list[str],
        *,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> list[list[float]]:
        """Invoke a bounded embedding batch through the persisted route.

        The limit is deliberately enforced at the provider boundary so a
        caller cannot turn a RAG rebuild into an unbounded request even if it
        skips the higher-level chunking policy.
        """
        return self._embed_request(
            texts,
            provider_id=provider_id,
            model_id=model_id,
            scalar_input=False,
        )

    def _embed_request(
        self,
        texts: list[str],
        *,
        provider_id: Optional[str],
        model_id: Optional[str],
        scalar_input: bool,
    ) -> list[list[float]]:
        task_id = self._task_id.get()
        if not task_id:
            raise ModelConfigurationError(
                "MODEL_TASK_CONTEXT_REQUIRED",
                "embedding invocation requires a durable task",
            )
        if (
            not isinstance(texts, list)
            or not texts
            or len(texts) > self.MAX_EMBEDDING_BATCH_SIZE
            or any(not isinstance(text, str) or not text.strip() for text in texts)
        ):
            raise ModelConfigurationError(
                "MODEL_INPUT_INVALID",
                f"embedding input must contain 1..{self.MAX_EMBEDDING_BATCH_SIZE} non-empty texts",
            )
        resolved = self.repository.resolve(
            "embedding",
            provider_id=provider_id,
            model_id=model_id,
        )
        try:
            secret = self.repository.credentials.resolve(resolved.get("credential_ref"))
        except CredentialError:
            raise
        base_url = str(resolved.get("base_url") or "").rstrip("/")
        if not base_url:
            raise ModelConfigurationError("MODEL_CONFIGURATION", "embedding provider base URL is missing")
        try:
            provider_config = json.loads(resolved.get("provider_config") or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ModelConfigurationError("MODEL_CONFIGURATION", "embedding provider config is invalid") from exc
        if not isinstance(provider_config, dict):
            provider_config = {}
        try:
            timeout = max(1, min(int(provider_config.get("timeout", 60)), 300))
        except (TypeError, ValueError):
            timeout = 60
        auth_mode = str(
            provider_config.get("authHeader")
            or provider_config.get("auth_header")
            or "bearer"
        ).lower()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if normalize_provider_type(resolved.get("provider_type")) == ProviderType.GEMINI.value:
            headers["x-goog-api-key"] = secret
        elif auth_mode in {"api-key", "api_key", "x-api-key"}:
            headers["api-key" if auth_mode == "api-key" else "x-api-key"] = secret
        else:
            headers["Authorization"] = f"Bearer {secret}"
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{base_url}/embeddings",
                    headers=headers,
                    json={
                        "model": resolved.get("model_id"),
                        "input": texts[0] if scalar_input else texts,
                    },
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            code = self._error_code(exc)
            raise ModelConfigurationError(code, "embedding provider invocation failed") from exc
        entries = body.get("data") if isinstance(body, dict) else None
        if not isinstance(entries, list) or len(entries) != len(texts):
            raise ModelConfigurationError(
                "PROVIDER_INVALID_RESPONSE",
                "embedding provider returned an unexpected vector count",
            )
        indexed_entries: list[dict[str, Any] | None] = [None] * len(texts)
        has_indexes = all(
            isinstance(entry, dict)
            and isinstance(entry.get("index"), int)
            and not isinstance(entry.get("index"), bool)
            for entry in entries
        )
        if has_indexes:
            for entry in entries:
                index = int(entry["index"])
                if index < 0 or index >= len(texts) or indexed_entries[index] is not None:
                    raise ModelConfigurationError(
                        "PROVIDER_INVALID_RESPONSE",
                        "embedding provider returned invalid vector indexes",
                    )
                indexed_entries[index] = entry
        else:
            indexed_entries = [entry if isinstance(entry, dict) else None for entry in entries]
        if any(entry is None for entry in indexed_entries):
            raise ModelConfigurationError(
                "PROVIDER_INVALID_RESPONSE",
                "embedding provider returned incomplete vectors",
            )

        vectors: list[list[float]] = []
        dimension: int | None = None
        for entry in indexed_entries:
            vector = entry.get("embedding") if entry is not None else None
            if not isinstance(vector, list) or not vector:
                raise ModelConfigurationError(
                    "PROVIDER_INVALID_RESPONSE",
                    "embedding provider returned no vector",
                )
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise ModelConfigurationError(
                    "PROVIDER_INVALID_RESPONSE",
                    "embedding provider returned a non-numeric vector",
                ) from exc
            if not values or any(not math.isfinite(value) for value in values):
                raise ModelConfigurationError(
                    "PROVIDER_INVALID_RESPONSE",
                    "embedding provider returned a non-finite vector",
                )
            if dimension is None:
                dimension = len(values)
            elif len(values) != dimension:
                raise ModelConfigurationError(
                    "PROVIDER_INVALID_RESPONSE",
                    "embedding provider returned inconsistent vector dimensions",
                )
            vectors.append(values)
        return vectors

    def discover_models(self, provider_id: str) -> dict[str, Any]:
        """Fetch and persist a provider model catalog from a durable task."""
        provider = self.repository.provider(provider_id)
        try:
            secret = self.repository.credentials.resolve(provider.get("credential_ref"))
        except CredentialError:
            raise
        provider_type = normalize_provider_type(provider.get("provider_type"))
        base_url = normalize_base_url(provider.get("base_url"))
        if not base_url:
            raise ModelConfigurationError("MODEL_CONFIGURATION", "provider base URL is required for model discovery")
        provider_config = json.loads(provider.get("config") or "{}")
        if not isinstance(provider_config, dict):
            provider_config = {}
        try:
            timeout = max(1, min(int(provider_config.get("timeout", 30)), 300))
        except (TypeError, ValueError):
            timeout = 30
        auth_mode = str(provider_config.get("authHeader") or provider_config.get("auth_header") or "bearer").lower()
        headers = {"Accept": "application/json"}
        if provider_type == ProviderType.GEMINI.value:
            headers["x-goog-api-key"] = secret
        elif auth_mode in {"api-key", "api_key", "x-api-key"}:
            headers["api-key" if auth_mode == "api-key" else "x-api-key"] = secret
        else:
            headers["Authorization"] = f"Bearer {secret}"
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.get(f"{base_url}/models", headers=headers)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            code = self._error_code(exc)
            raise ModelConfigurationError(code, "model catalog request failed") from exc
        entries = payload if isinstance(payload, list) else payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            raise ModelConfigurationError("MODEL_DISCOVERY_INVALID", "provider model catalog is not a list")
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in entries[:512]:
            if isinstance(entry, str):
                external_id = entry.strip()
                display_name = external_id
                capabilities = ["chat"]
            elif isinstance(entry, dict):
                external_id = str(entry.get("id") or entry.get("modelId") or entry.get("model_id") or "").strip()
                display_name = str(entry.get("name") or entry.get("display_name") or external_id).strip()
                capabilities = entry.get("capabilities", ["chat"])
            else:
                continue
            if not external_id or external_id in seen:
                continue
            seen.add(external_id)
            if not isinstance(capabilities, list):
                capabilities = ["chat"]
            candidates.append({"modelId": external_id, "name": display_name or external_id, "capabilities": capabilities})
        if not candidates:
            raise ModelConfigurationError("MODEL_DISCOVERY_EMPTY", "provider returned no models")
        models = self.repository.save_discovered_models(provider_id, candidates)
        return {"providerId": provider_id, "models": models, "count": len(models)}

    def generate_image(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        quality: str = "",
        style: str = "",
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> ImageResponse:
        """Invoke the configured image route and keep it inside a durable task."""
        task_id = self._task_id.get()
        if not task_id:
            raise ModelConfigurationError("MODEL_TASK_CONTEXT_REQUIRED", "model invocation requires a durable task")
        if not str(prompt or "").strip():
            raise ModelConfigurationError("MODEL_INPUT_INVALID", "image prompt is required")
        resolved = self.repository.resolve("image", provider_id=provider_id, model_id=model_id)
        run_id = self.repository.create_run(
            task_id=task_id,
            role="image",
            resolved=resolved,
            prompt_key="image-generation",
            prompt_version="1",
            input_reference={"prompt_chars": len(prompt), "size": size, "quality": quality, "style": style},
        )
        try:
            secret = self.repository.credentials.resolve(resolved.get("credential_ref"))
            model_config = json.loads(resolved.get("config") or "{}")
            provider_config = json.loads(resolved.get("provider_config") or "{}")
            config = LLMConfig(
                provider=ProviderType(resolved["provider_type"]), model=resolved["model_id"],
                base_url=resolved.get("base_url") or "", api_key=secret,
                temperature=float(model_config.get("temperature", 0.8)),
                max_tokens=int(model_config.get("max_tokens", 8000)),
                timeout=int(provider_config.get("timeout", 300)),
            )
            provider_name = f"run-{resolved['provider_id']}"
            self.gateway.register_provider(provider_name, config)
            response = self.gateway.generate_image(
                provider_name,
                prompt,
                model=resolved["model_id"],
                size=size,
                quality=quality,
                style=style,
            )
        except CredentialError as exc:
            self.repository.fail_run(run_id, exc.code)
            raise
        except Exception as exc:
            code = self._error_code(exc)
            self.repository.fail_run(run_id, code)
            if isinstance(exc, ModelConfigurationError):
                raise
            raise ModelConfigurationError(code, "image provider invocation failed") from exc
        self.repository.finish_run(
            run_id,
            LLMResponse(content=f"image:{len(response.data)}", model=response.model,
                        latency_ms=response.latency_ms, provider=response.provider),
        )
        return response

    @staticmethod
    def _error_code(exc: Exception) -> str:
        message = str(exc).lower()
        if "401" in message or "403" in message or "unauthorized" in message or "forbidden" in message:
            return "MODEL_AUTHENTICATION"
        if "429" in message or "rate limit" in message:
            return "RATE_LIMIT"
        if any(token in message for token in ("timeout", "connection", "network", "dns")):
            return "NETWORK"
        if re.search(r"\b5\d\d\b", message):
            return "PROVIDER_TRANSIENT"
        return "PROVIDER_REJECTED"


class PersistentModelClient:
    """LLMClient-compatible surface backed by a persisted role route."""

    def __init__(self, runtime: PersistentModelRuntime, role: str, *, manager: Any | None = None):
        self.runtime = runtime
        self.role = role
        self.manager = manager

    def chat(self, messages: list[dict[str, Any]], system: str = "", **kwargs: Any) -> LLMResponse:
        if self.manager is not None and self.manager._router is not None and self.runtime.current_task_id():
            task_type = str(kwargs.pop("task_type", self.role) or self.role)
            return self.manager._router_chat(
                messages, system, role=self.role, task_type=task_type, json_mode=False, kwargs=kwargs
            )
        return self.runtime.invoke(self.role, messages, system, **kwargs)

    def chat_json(self, messages: list[dict[str, Any]], system: str = "", **kwargs: Any) -> dict[str, Any]:
        if self.manager is not None and self.manager._router is not None and self.runtime.current_task_id():
            task_type = str(kwargs.pop("task_type", self.role) or self.role)
            response = self.manager._router_chat(
                messages, system, role=self.role, task_type=task_type, json_mode=True, kwargs=kwargs
            )
        else:
            response = self.runtime.invoke(self.role, messages, system, json_mode=True, **kwargs)
        try:
            text = response.content.strip()
            if text.startswith("```"):
                text = "\n".join(text.splitlines()[1:-1])
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": response.content, "error": "JSON parsing failed"}


class PersistentMultiModelManager:
    """Compatibility façade for legacy agents while enforcing the Phase 4 runtime."""

    _legacy_roles = {"primary": "writer", "review": "reviewer", "extractor": "fact_extraction"}
    _task_roles = {
        "write-next": "writer",
        "draft-chapter": "writer",
        "plan-chapter": "planner",
        "compose-chapter": "context",
        "world-bootstrap": "planner",
        "review": "reviewer",
        "audit-chapter": "reviewer",
        "review-chapter": "reviewer",
        "revision": "reviser",
        "revise-chapter": "reviser",
        "rewrite-chapter": "reviser",
        "fact-extraction": "fact_extraction",
        "story-bible-suggest": "planner",
        "thought-clarify": "planner",
        "thought-framework": "planner",
        "joint-review": "reviewer",
        "dialogue-write": "writer",
        "draft-import-analysis": "reviewer",
        "draft-import-adjustment-plan": "reviewer",
        "planning-synthesis": "planner",
        "planning-views-generate": "planner",
        "planning-views": "planner",
        "model-connection-test": "planner",
        "model-discovery": "planner",
        "simulation-analyst-query": "planner",
        "simulation-character-chat": "writer",
        "simulation-survey": "planner",
        "forecast": "planner",
        "storyflow-analyze": "planner",
        "radar": "planner",
        "radar-scan": "planner",
        "translation": "writer",
        "translation-run": "writer",
        "interactive-film": "planner",
        "interactive-film-generate": "planner",
        "cover-brief": "planner",
        "interactive-film-node-image": "image",
        "cover-image-generate": "image",
        "simulation-round": "planner",
    }

    def __init__(self, runtime: PersistentModelRuntime):
        self.runtime = runtime
        self._clients: dict[str, PersistentModelClient] = {}
        self._router: Any | None = None

    def attach_runtime_router(self, router: Any) -> None:
        """Attach the host-owned router used by synchronous legacy callers."""
        self._router = router

    def task_scope(self, task_id: str) -> ContextManager[None]:
        return self.runtime.task_scope(task_id)

    def last_generation_run_id(self) -> str | None:
        return self.runtime.last_generation_run_id()

    def validate_provider(self, provider_id: str, role: str) -> dict[str, Any]:
        """Expose the simulation fail-closed preflight on the manager facade."""
        return self.runtime.validate_provider(provider_id, role)

    def get_client(self, role: str = "primary") -> PersistentModelClient:
        resolved_role = self._legacy_roles.get(role, role)
        if resolved_role not in MODEL_ROLES:
            resolved_role = "writer"
        if resolved_role not in self._clients:
            self._clients[resolved_role] = PersistentModelClient(self.runtime, resolved_role, manager=self)
        return self._clients[resolved_role]

    def get_writer(self) -> PersistentModelClient:
        return self.get_client("writer")

    def get_reviewer(self) -> PersistentModelClient:
        return self.get_client("reviewer")

    def get_planner(self) -> PersistentModelClient:
        return self.get_client("planner")

    def embed(
        self,
        text: str,
        *,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> list[float]:
        """Route a derived embedding operation through the Host Runtime."""
        if self._router is not None and self.runtime.current_task_id():
            return self._router_embedding(
                text,
                provider_id=provider_id,
                model_id=model_id,
            )
        return self.runtime.embed(text, provider_id=provider_id, model_id=model_id)

    def embed_many(
        self,
        texts: list[str],
        *,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> list[list[float]]:
        """Route a bounded derived-embedding batch through the Host Runtime."""
        max_batch = PersistentModelRuntime.MAX_EMBEDDING_BATCH_SIZE
        if (
            not isinstance(texts, list)
            or not texts
            or len(texts) > max_batch
            or any(not isinstance(text, str) or not text.strip() for text in texts)
        ):
            raise ModelConfigurationError(
                "MODEL_INPUT_INVALID",
                f"embedding input must contain 1..{max_batch} non-empty texts",
            )
        if self._router is not None and self.runtime.current_task_id():
            return self._router_embedding_batch(
                texts,
                provider_id=provider_id,
                model_id=model_id,
            )
        return self.runtime.embed_many(texts, provider_id=provider_id, model_id=model_id)

    def chat(self, messages: list[dict[str, Any]], system: str = "", *, task_type: Optional[str] = None,
             **kwargs: Any) -> LLMResponse:
        """Route the pipeline's legacy ``chat`` call through a durable agent role."""
        role = self._task_roles.get(task_type or "", "writer")
        kwargs.setdefault("task_stage", task_type or role)
        if self._router is not None and self.runtime.current_task_id():
            return self._router_chat(messages, system, role=role, task_type=task_type or role,
                                     json_mode=False, kwargs=kwargs)
        return self.get_client(role).chat(messages, system, **kwargs)

    def chat_json(self, messages: list[dict[str, Any]], system: str = "", *, task_type: Optional[str] = None,
                  **kwargs: Any) -> dict[str, Any]:
        """Provide the same durable routing for JSON-constrained pipeline stages."""
        role = self._task_roles.get(task_type or "", "writer")
        kwargs.setdefault("task_stage", task_type or role)
        if self._router is not None and self.runtime.current_task_id():
            response = self._router_chat(messages, system, role=role, task_type=task_type or role,
                                         json_mode=True, kwargs=kwargs)
            try:
                text = response.content.strip()
                if text.startswith("```"):
                    text = "\n".join(text.splitlines()[1:-1])
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": response.content, "error": "JSON parsing failed"}
        return self.get_client(role).chat_json(messages, system, **kwargs)

    def _router_chat(
        self,
        messages: list[dict[str, Any]],
        system: str,
        *,
        role: str,
        task_type: str,
        json_mode: bool,
        kwargs: dict[str, Any],
    ) -> LLMResponse:
        router = self._router
        if router is None:
            raise ModelConfigurationError("MODEL_RUNTIME_ROUTER_UNAVAILABLE", "runtime router is not attached")
        durable_task_id = self.runtime.current_task_id()
        if not durable_task_id:
            raise ModelConfigurationError("MODEL_TASK_CONTEXT_REQUIRED", "model invocation requires a durable task")
        row = self.runtime.repository.db.fetchone(
            "SELECT * FROM agent_tasks WHERE task_id=?", (durable_task_id,)
        )
        if row is None:
            base_task = self.runtime._ensure_agent_task(durable_task_id, task_type, role)
        else:
            base_task = self.runtime._agent_task_from_row(dict(row), role=role, task_type=task_type)
        payload = dict(base_task.input_payload)
        # A single durable chapter task can contain several role-specific
        # calls.  Give multi-turn runtimes an explicit Host-owned conversation
        # scope so a Reviewer/Revision call cannot accidentally reuse the
        # Writer's provider thread.  Identical requests keep the same scope,
        # which preserves retry/recovery idempotency; callers that genuinely
        # need a continuing conversation may provide ``runtime_session_key``.
        context_manifest = kwargs.get("context_manifest")
        if not isinstance(context_manifest, dict):
            context_manifest = payload.get("contextManifest") or payload.get("context_manifest")
        if not isinstance(context_manifest, dict):
            context_manifest = self.runtime._context_manifest_for_task(
                durable_task_id,
                task_stage=task_type,
                role=role,
            )
        session_scope = kwargs.get("runtime_session_key") or payload.get("runtimeSessionKey")
        if not isinstance(session_scope, str) or not session_scope.strip():
            session_signature = stable_hash({
                "messages": messages,
                "system": system,
                "contextManifest": context_manifest,
            })[:24]
            session_scope = f"{role}:{task_type}:{session_signature}"
        payload["runtimeSessionKey"] = str(session_scope).strip()
        payload.update({
            "messages": messages,
            "system": system,
            "jsonMode": json_mode,
            "runtimeOptions": dict(kwargs),
        })
        payload["contextManifest"] = deepcopy(context_manifest)
        if kwargs.get("prompt_version"):
            payload["promptVersion"] = str(kwargs["prompt_version"])
        provider_id = kwargs.get("provider_id")
        if provider_id:
            payload["providerId"] = provider_id
            base_task = replace(
                base_task,
                constraints={**base_task.constraints, "runtime_type": "api"},
            )
        runtime_profile = base_task.profile
        if (
            runtime_profile is None
            or base_task.role != role
            or base_task.task_type != task_type
        ):
            runtime_profile = default_agent_task_profile(role, task_type)
        # The durable AgentTask row is the compatibility envelope for the
        # chapter workflow.  Each model call still receives the Host-owned
        # role/profile for its actual stage, so a Reviewer cannot inherit the
        # Writer's dynamic tools merely because both calls share one durable
        # queue task.
        task = replace(
            base_task,
            role=role,
            task_type=task_type,
            profile=runtime_profile,
            input_payload=payload,
        )
        terminal = self._run_router_task(task)
        if terminal is None or not isinstance(terminal.payload, dict):
            raise ModelConfigurationError("MODEL_RUNTIME_NO_ARTIFACT", "runtime completed without an artifact")
        artifact = terminal.payload.get("artifact")
        if not isinstance(artifact, dict):
            artifact = terminal.payload
        usage = terminal.payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return LLMResponse(
            content=str(artifact.get("content") or artifact.get("text") or ""),
            model=str(artifact.get("model") or ""),
            provider=str(artifact.get("provider") or ""),
            finish_reason=str(artifact.get("finishReason") or ""),
            prompt_tokens=int(usage.get("inputTokens") or 0),
            completion_tokens=int(usage.get("outputTokens") or 0),
            tokens_used=int(usage.get("totalTokens") or 0),
            latency_ms=int(usage.get("latencyMs") or 0),
        )

    def _router_embedding(
        self,
        text: str,
        *,
        provider_id: Optional[str],
        model_id: Optional[str],
    ) -> list[float]:
        if not isinstance(text, str) or not text.strip():
            raise ModelConfigurationError("MODEL_INPUT_INVALID", "embedding input is empty")
        durable_task_id = self.runtime.current_task_id()
        if not durable_task_id:
            raise ModelConfigurationError("MODEL_TASK_CONTEXT_REQUIRED", "embedding requires a durable task")
        db = self.runtime.repository.db
        row = db.fetchone("SELECT * FROM agent_tasks WHERE task_id=?", (durable_task_id,))
        if row is None:
            base_task = self.runtime._ensure_agent_task(durable_task_id, "embedding", "embedding")
        else:
            base_task = self.runtime._agent_task_from_row(
                dict(row), role="embedding", task_type="embedding"
            )
        payload = dict(base_task.input_payload)
        payload.update({"operation": "embedding", "embeddingInput": text})
        constraints = {**base_task.constraints, "runtime_type": "api"}
        if model_id:
            resolved = self.runtime.repository.resolve(
                "embedding",
                provider_id=provider_id,
                model_id=model_id,
            )
            constraints["model_id"] = str(resolved.get("model_id") or "").strip()
            constraints["provider_id"] = str(resolved.get("provider_id") or "").strip()
            payload["providerId"] = str(resolved.get("provider_id") or "").strip()
        elif provider_id:
            payload["providerId"] = provider_id
            constraints["provider_id"] = provider_id
        task = replace(
            base_task,
            role="embedding",
            task_type="embedding",
            profile=default_agent_task_profile("embedding", "embedding"),
            constraints=constraints,
            input_payload=payload,
        )
        terminal = self._run_router_task(task)
        if terminal is None or not isinstance(terminal.payload, dict):
            raise ModelConfigurationError("MODEL_RUNTIME_NO_ARTIFACT", "runtime completed without an embedding artifact")
        artifact = terminal.payload.get("artifact")
        if not isinstance(artifact, dict):
            artifact = terminal.payload
        vector = artifact.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise ModelConfigurationError("MODEL_RUNTIME_NO_ARTIFACT", "runtime completed without an embedding vector")
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise ModelConfigurationError("MODEL_RUNTIME_INVALID_ARTIFACT", "runtime returned a non-numeric embedding") from exc
        return values

    def _router_embedding_batch(
        self,
        texts: list[str],
        *,
        provider_id: Optional[str],
        model_id: Optional[str],
    ) -> list[list[float]]:
        max_batch = PersistentModelRuntime.MAX_EMBEDDING_BATCH_SIZE
        if not texts or len(texts) > max_batch:
            raise ModelConfigurationError(
                "MODEL_INPUT_INVALID",
                f"embedding batch must contain 1..{max_batch} texts",
            )
        durable_task_id = self.runtime.current_task_id()
        if not durable_task_id:
            raise ModelConfigurationError("MODEL_TASK_CONTEXT_REQUIRED", "embedding requires a durable task")
        db = self.runtime.repository.db
        row = db.fetchone("SELECT * FROM agent_tasks WHERE task_id=?", (durable_task_id,))
        if row is None:
            base_task = self.runtime._ensure_agent_task(durable_task_id, "embedding-batch", "embedding")
        else:
            base_task = self.runtime._agent_task_from_row(
                dict(row), role="embedding", task_type="embedding-batch"
            )
        payload = dict(base_task.input_payload)
        payload.update({"operation": "embedding_batch", "embeddingInputs": list(texts)})
        constraints = {**base_task.constraints, "runtime_type": "api"}
        if model_id:
            resolved = self.runtime.repository.resolve(
                "embedding",
                provider_id=provider_id,
                model_id=model_id,
            )
            constraints["model_id"] = str(resolved.get("model_id") or "").strip()
            constraints["provider_id"] = str(resolved.get("provider_id") or "").strip()
            payload["providerId"] = str(resolved.get("provider_id") or "").strip()
        elif provider_id:
            payload["providerId"] = provider_id
            constraints["provider_id"] = provider_id
        task = replace(
            base_task,
            role="embedding",
            task_type="embedding-batch",
            profile=default_agent_task_profile("embedding", "embedding-batch"),
            constraints=constraints,
            input_payload=payload,
        )
        terminal = self._run_router_task(task)
        if terminal is None or not isinstance(terminal.payload, dict):
            raise ModelConfigurationError("MODEL_RUNTIME_NO_ARTIFACT", "runtime completed without an embedding artifact")
        artifact = terminal.payload.get("artifact")
        if not isinstance(artifact, dict):
            artifact = terminal.payload
        vectors = artifact.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ModelConfigurationError("MODEL_RUNTIME_NO_ARTIFACT", "runtime completed without a complete embedding batch")
        normalized: list[list[float]] = []
        dimension: int | None = None
        for vector in vectors:
            if not isinstance(vector, list) or not vector:
                raise ModelConfigurationError("MODEL_RUNTIME_INVALID_ARTIFACT", "runtime returned an invalid embedding batch")
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise ModelConfigurationError("MODEL_RUNTIME_INVALID_ARTIFACT", "runtime returned a non-numeric embedding batch") from exc
            if any(not math.isfinite(value) for value in values):
                raise ModelConfigurationError("MODEL_RUNTIME_INVALID_ARTIFACT", "runtime returned a non-finite embedding batch")
            if dimension is None:
                dimension = len(values)
            elif len(values) != dimension:
                raise ModelConfigurationError("MODEL_RUNTIME_INVALID_ARTIFACT", "runtime returned inconsistent embedding dimensions")
            normalized.append(values)
        return normalized

    def _router_image(
        self,
        prompt: str,
        *,
        size: str,
        quality: str,
        style: str,
    ) -> ImageResponse:
        router = self._router
        if router is None:
            raise ModelConfigurationError("MODEL_RUNTIME_ROUTER_UNAVAILABLE", "runtime router is not attached")
        durable_task_id = self.runtime.current_task_id()
        if not durable_task_id:
            raise ModelConfigurationError("MODEL_TASK_CONTEXT_REQUIRED", "model invocation requires a durable task")
        db = self.runtime.repository.db
        row = db.fetchone("SELECT * FROM agent_tasks WHERE task_id=?", (durable_task_id,))
        if row is None:
            durable = db.fetchone("SELECT type FROM tasks WHERE id=?", (durable_task_id,)) or {}
            task_type = str(durable.get("type") or "image-generation")
            base_task = self.runtime._ensure_agent_task(durable_task_id, task_type, "image")
        else:
            base_task = self.runtime._agent_task_from_row(dict(row), role="image", task_type="image-generation")
        payload = dict(base_task.input_payload)
        payload.update({
            "operation": "image",
            "imagePrompt": prompt,
            "imageOptions": {
                "size": size,
                "quality": quality,
                "style": style,
            },
        })
        task = replace(
            base_task,
            constraints={**base_task.constraints, "runtime_type": "api"},
            input_payload=payload,
        )
        terminal = self._run_router_task(task)
        if terminal is None or not isinstance(terminal.payload, dict):
            raise ModelConfigurationError("MODEL_RUNTIME_NO_ARTIFACT", "runtime completed without an image artifact")
        artifact = terminal.payload.get("artifact")
        if not isinstance(artifact, dict):
            artifact = terminal.payload
        encoded = artifact.get("dataBase64")
        if not isinstance(encoded, str) or not encoded:
            raise ModelConfigurationError("MODEL_RUNTIME_NO_ARTIFACT", "runtime completed without image data")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise ModelConfigurationError("MODEL_RUNTIME_INVALID_ARTIFACT", "runtime returned invalid image data") from None
        if not data:
            raise ModelConfigurationError("MODEL_RUNTIME_INVALID_ARTIFACT", "runtime returned empty image data")
        return ImageResponse(
            data=data,
            mime_type=str(artifact.get("mimeType") or artifact.get("contentType") or "image/png"),
            model=str(artifact.get("model") or ""),
            provider=str(artifact.get("provider") or ""),
        )

    def _run_router_task(self, task: AgentTask):
        router = self._router
        if router is None:
            raise ModelConfigurationError("MODEL_RUNTIME_ROUTER_UNAVAILABLE", "runtime router is not attached")

        async def collect():
            terminal = None
            # Compatibility callers still enter through the synchronous
            # manager, but the Host Router remains the single execution
            # entrypoint.  Use its explicit fallback seam as well so a
            # transient pre-output provider failure cannot silently bypass
            # the same-quality retry policy used by TaskOrchestrator.
            async for event in router.execute_with_fallback(task):
                if event.event_type in {"turn.completed", "turn.complete"}:
                    terminal = event
            return terminal

        # Legacy callers are synchronous, but Studio's HTTP boundary is an
        # async function and can still enter this compatibility facade while
        # an event loop is already running.  ``asyncio.run`` cannot nest in
        # that case; isolate the synchronous bridge in one short-lived worker
        # thread while keeping the same durable task scope and router plan.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(collect())
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="novelforge-runtime-bridge") as executor:
            return executor.submit(asyncio.run, collect()).result()

    def test_provider(self, provider_id: str) -> LLMResponse:
        if self._router is not None and self.runtime.current_task_id():
            return self._router_chat(
                [{"role": "user", "content": "Connection check"}],
                "",
                role="planner",
                task_type="model-connection-test",
                json_mode=False,
                kwargs={"provider_id": provider_id, "max_tokens": 10},
            )
        return self.runtime.test_provider(provider_id)

    def discover_models(self, provider_id: str) -> dict[str, Any]:
        discovered = self.runtime.discover_models(provider_id)
        # Model discovery is a durable worker operation.  Keep the scheduler
        # in the same long-lived process synchronized with the catalog it just
        # persisted; otherwise a worker would require a restart before a
        # newly discovered model could be selected.  The method is a no-op for
        # isolated compatibility managers whose router has no API adapter.
        self.refresh_api_capabilities()
        return discovered

    def refresh_api_capabilities(self) -> int:
        """Replace the attached Host scheduler's API catalog from persistence.

        Configuration updates and model discovery share the same persisted
        catalog.  Keeping this operation on the manager makes both paths
        update the long-lived Host scheduler instead of leaving stale
        candidates until a process restart or a UI capability request.
        """
        router = self._router
        scheduler = getattr(router, "scheduler", None)
        registry = getattr(scheduler, "registry", None)
        if router is None or registry is None:
            return 0
        try:
            api_runtime = getattr(router, "get", lambda _runtime_type: None)("api")
        except Exception as exc:
            # Capability refresh is an observational synchronization seam.  A
            # compatibility router may reject an absent adapter; keep the
            # persisted model configuration usable while making the skipped
            # refresh visible to operators.
            logger.warning("could not read API runtime during capability refresh: %s", exc, exc_info=exc)
            return 0
        get_models = cast(
            Callable[[], Iterable[ModelDescriptor]] | None,
            getattr(api_runtime, "get_models_sync", None),
        )
        if not callable(get_models):
            return 0
        health = registry.runtime_health("api", default="unknown") if hasattr(registry, "runtime_health") else "ready"
        # The full Studio router exposes a durable Registry readiness gate.
        # Consult it when available so a newly added model cannot be marked
        # ready merely because the previous catalog was empty.  Test and
        # embedded routers without that gate retain their observed capability
        # health.
        readiness = getattr(router, "runtime_readiness", None)
        if callable(readiness):
            try:
                readiness("api")
                health = "ready"
            except Exception:
                health = "unavailable"
        registry.clear_runtime("api")
        models = tuple(get_models())
        for model in models:
            registry.register_model(
                model,
                capability="C2",
                health=health,
                tags=("api",),
            )
        return len(models)

    def generate_image(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        quality: str = "",
        style: str = "",
    ) -> ImageResponse:
        """Expose the durable image route to legacy task handlers."""
        if self._router is not None and self.runtime.current_task_id():
            return self._router_image(
                prompt,
                size=size,
                quality=quality,
                style=style,
            )
        return self.runtime.generate_image(prompt, size=size, quality=quality, style=style)


def build_model_runtime(db: Database, workspace_root: Path) -> tuple[ModelRepository, PersistentModelRuntime, PersistentMultiModelManager]:
    repository = ModelRepository(db, CredentialStore(workspace_root))
    runtime = PersistentModelRuntime(repository)
    manager = PersistentMultiModelManager(runtime)

    # The worker-facing manager is synchronous for historical reasons, while
    # the Host contract is async.  Build the bridge once here: normal task
    # handlers enter the common RuntimeRouter, and the API adapter delegates
    # the actual provider call back to this persisted runtime without creating
    # a second AgentRun.
    from src.compute.scheduler import BudgetBroker, CapabilityRegistry, ComputeScheduler
    from src.runtime.api_runtime import ApiModelRuntime
    from src.runtime.codex import CodexRuntime
    from src.runtime.persistence import AgentRunStore
    from src.runtime.router import RuntimeRouter

    capabilities = CapabilityRegistry()
    rows = db.fetchall(
        """SELECT m.id, m.provider_id, m.model_id, m.name, m.capabilities,
                  EXISTS(
                      SELECT 1 FROM agent_model_routes image_route
                      WHERE image_route.agent_role='image' AND image_route.model_id=m.id
                  ) AS image_route
           FROM models m JOIN model_providers p ON p.id=m.provider_id
           WHERE m.enabled=TRUE AND p.enabled=TRUE
           ORDER BY m.created_at, m.id"""
    )
    for row in rows:
        try:
            raw_capabilities = json.loads(row.get("capabilities") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_capabilities = []
        if isinstance(raw_capabilities, dict):
            model_capabilities = {str(key): str(value) for key, value in raw_capabilities.items()}
        else:
            model_capabilities = {
                str(value): "available" for value in raw_capabilities if isinstance(value, str)
            }
        normalized_capabilities = {
            name.strip().lower() for name in model_capabilities if name.strip()
        }
        supports_image = bool(row.get("image_route")) or bool(
            normalized_capabilities & {"image", "images", "image-generation", "image_generation"}
        )
        supports_embedding = bool(
            normalized_capabilities & {"embedding", "embeddings", "vector", "vectors"}
        )
        descriptor = ModelDescriptor(
            runtime_type="api",
            model_id=str(row["model_id"]),
            display_name=str(row["name"] or row["model_id"]),
            capabilities=model_capabilities,
            reasoning_levels=("medium", "high"),
            context_window=128_000,
            capability_profile={
                "extraction": "C2", "planning": "C2", "writing": "C2",
                "review": "C2", "long_context": "C2", "tool_use": "C1",
                "structured_output": "C2", "revision": "C2", "consistency": "C2",
                "image": "C2" if supports_image else "C0",
                "embedding": "C2" if supports_embedding else "C0",
            },
            provider_id=str(row["provider_id"]),
        )
        capabilities.register_model(
            descriptor,
            capability="C2",
            capability_profile={
                "extraction": "C2",
                "planning": "C2",
                "writing": "C2",
                "review": "C2",
                "long_context": "C2",
                "tool_use": "C1",
                "structured_output": "C2",
                "revision": "C2",
                "consistency": "C2",
                "image": "C2" if supports_image else "C0",
                "embedding": "C2" if supports_embedding else "C0",
            },
        )
    agent_runs = AgentRunStore(db)
    codex_runtime = None
    codex_installation = db.fetchone(
        """SELECT state, path, auth_status, capability_verified, health, verified
           FROM runtime_installations WHERE runtime_type=?""",
        ("codex-app-server",),
    )
    if (
        codex_installation is not None
        and codex_installation.get("state") == "ready"
        and bool(codex_installation.get("verified"))
        and bool(codex_installation.get("capability_verified"))
        and codex_installation.get("auth_status") in {"authenticated", "ready"}
        and codex_installation.get("health") == "healthy"
    ):
        codex_path = str(codex_installation.get("path") or "codex").strip() or "codex"
        codex_runtime = CodexRuntime(
            agent_runs,
            command=(codex_path, "app-server"),
            cwd=workspace_root,
        )
        for model in codex_runtime._models:
            capabilities.register_model(model, capability="C4", tags=("codex", "session"))

    def runtime_readiness(runtime_type: str) -> None:
        if runtime_type != "codex-app-server":
            return
        current = db.fetchone(
            """SELECT state, auth_status, capability_verified, health, verified
               FROM runtime_installations WHERE runtime_type=?""",
            (runtime_type,),
        )
        if (
            current is None
            or current.get("state") != "ready"
            or not bool(current.get("verified"))
            or not bool(current.get("capability_verified"))
            or current.get("auth_status") not in {"authenticated", "ready"}
            or current.get("health") != "healthy"
        ):
            raise RuntimeUnavailable(f"runtime is not ready: {runtime_type}")

    router = RuntimeRouter(
        ComputeScheduler(
            capabilities,
            budget=BudgetBroker(total=10_000, critical_reserve=1_000, db=db, scope="runtime"),
        ),
        runs=agent_runs,
        runtime_readiness=runtime_readiness,
    )
    router.register("api", ApiModelRuntime(runtime, agent_runs))
    if codex_runtime is not None:
        router.register("codex-app-server", codex_runtime)
    manager.attach_runtime_router(router)
    return repository, runtime, manager
