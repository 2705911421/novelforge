"""Persistent model configuration, credential boundary, and invocation audit trail."""

from __future__ import annotations

import ctypes
import json
import os
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ContextManager, Iterator, Optional

from src.core.database import Database, generate_id

from .gateway import LLMConfig, LLMResponse, ModelGateway, ProviderType


MODEL_ROLES = (
    "planner", "writer", "reviewer", "reviser", "context", "fact_extraction",
    "embedding", "rerank", "image",
)
PROVIDER_TYPES = {item.value for item in ProviderType}


class ModelConfigurationError(ValueError):
    """A persisted provider, model, or role route cannot safely be used."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CredentialError(ModelConfigurationError):
    """A credential reference cannot be persisted or resolved."""


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
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
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

    def configuration(self) -> dict[str, Any]:
        providers = [self._provider_dict(row) for row in self.db.fetchall(
            "SELECT * FROM model_providers ORDER BY name"
        )]
        models = [self._model_dict(row) for row in self.db.fetchall(
            "SELECT * FROM models ORDER BY name"
        )]
        routes = {
            row["agent_role"]: row["model_id"]
            for row in self.db.fetchall("SELECT agent_role, model_id FROM agent_model_routes")
        }
        return {"providers": providers, "models": models, "routes": routes, "roles": list(MODEL_ROLES)}

    def save_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        providers = payload.get("providers", [])
        models = payload.get("models", [])
        routes = payload.get("routes", {})
        if not isinstance(providers, list) or not isinstance(models, list) or not isinstance(routes, dict):
            raise ModelConfigurationError("MODEL_CONFIGURATION", "providers, models, and routes are required")
        with self.db.transaction() as conn:
            for item in providers:
                self._upsert_provider(conn, item)
            for item in models:
                self._upsert_model(conn, item)
            for role, model_id in routes.items():
                if role not in MODEL_ROLES or not isinstance(model_id, str):
                    raise ModelConfigurationError("MODEL_CONFIGURATION", "invalid agent role route")
                usable = conn.execute(
                    """SELECT 1 FROM models m JOIN model_providers p ON p.id=m.provider_id
                       WHERE m.id=? AND m.enabled=TRUE AND p.enabled=TRUE""", (model_id,)
                ).fetchone()
                if not usable:
                    raise ModelConfigurationError("MODEL_ROUTE_UNAVAILABLE", f"route {role} has no enabled model")
                conn.execute(
                    """INSERT INTO agent_model_routes(agent_role, model_id, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(agent_role) DO UPDATE SET model_id=excluded.model_id, updated_at=CURRENT_TIMESTAMP""",
                    (role, model_id),
                )
        return self.configuration()

    def resolve(self, role: str, *, provider_id: Optional[str] = None) -> dict[str, Any]:
        if role not in MODEL_ROLES:
            raise ModelConfigurationError("MODEL_ROUTE_UNAVAILABLE", "unknown agent role")
        if provider_id:
            row = self.db.fetchone(
                """SELECT m.*, p.name AS provider_name, p.provider_type, p.base_url, p.credential_ref,
                          p.config AS provider_config, p.enabled AS provider_enabled
                   FROM models m JOIN model_providers p ON p.id=m.provider_id
                   WHERE p.id=? AND m.enabled=TRUE AND p.enabled=TRUE ORDER BY m.created_at LIMIT 1""", (provider_id,)
            )
        else:
            row = self.db.fetchone(
                """SELECT m.*, p.name AS provider_name, p.provider_type, p.base_url, p.credential_ref,
                          p.config AS provider_config, p.enabled AS provider_enabled
                   FROM agent_model_routes r JOIN models m ON m.id=r.model_id
                   JOIN model_providers p ON p.id=m.provider_id
                   WHERE r.agent_role=? AND m.enabled=TRUE AND p.enabled=TRUE""", (role,)
            )
        if not row:
            raise ModelConfigurationError("MODEL_ROUTE_UNAVAILABLE", f"no enabled model route for {role}")
        return dict(row)

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

    def finish_run(self, run_id: str, response: LLMResponse) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE generation_runs SET status='succeeded', output_reference=?, prompt_tokens=?,
                   completion_tokens=?, total_tokens=?, latency_ms=?, completed_at=CURRENT_TIMESTAMP WHERE id=?""",
                (json.dumps({"content_chars": len(response.content), "finish_reason": response.finish_reason}),
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
        provider_type = item.get("providerType", item.get("provider_type", ""))
        if not isinstance(name, str) or not name.strip() or provider_type not in PROVIDER_TYPES:
            raise ModelConfigurationError("MODEL_CONFIGURATION", "provider name and provider type are required")
        # SSRF protection: validate base_url to prevent requests to internal networks.
        base_url = item.get("baseUrl", item.get("base_url", ""))
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
        elif env_ref:
            if not isinstance(env_ref, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_ref):
                raise ModelConfigurationError("MODEL_CONFIGURATION", "invalid credential environment variable")
            credential_ref = f"env:{env_ref}"
        config = item.get("config", {})
        if not isinstance(config, dict):
            raise ModelConfigurationError("MODEL_CONFIGURATION", "provider config must be an object")
        conn.execute(
            """INSERT INTO model_providers(id, name, provider_type, base_url, credential_ref, enabled, config,
               created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, provider_type=excluded.provider_type,
               base_url=excluded.base_url, credential_ref=excluded.credential_ref, enabled=excluded.enabled,
               config=excluded.config, updated_at=CURRENT_TIMESTAMP""",
            (provider_id, name.strip(), provider_type, item.get("baseUrl", item.get("base_url", "")), credential_ref,
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
        if not isinstance(provider_id, str) or not provider_id.strip() or not isinstance(name, str) or not name.strip() or not isinstance(external_id, str) or not external_id.strip():
            raise ModelConfigurationError("MODEL_CONFIGURATION", "model provider, name, and model id are required")
        if not conn.execute("SELECT 1 FROM model_providers WHERE id=?", (provider_id,)).fetchone():
            raise ModelConfigurationError("MODEL_CONFIGURATION", "model provider does not exist")
        config = item.get("config", {})
        capabilities = item.get("capabilities", [])
        if not isinstance(config, dict) or not isinstance(capabilities, list):
            raise ModelConfigurationError("MODEL_CONFIGURATION", "model config and capabilities are invalid")
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
                "config": json.loads(row["config"] or "{}")}

    @staticmethod
    def _model_dict(row: Any) -> dict[str, Any]:
        return {"id": row["id"], "providerId": row["provider_id"], "name": row["name"],
                "modelId": row["model_id"], "enabled": bool(row["enabled"]),
                "capabilities": json.loads(row["capabilities"] or "[]"), "config": json.loads(row["config"] or "{}")}

    @staticmethod
    def _run_dict(row: Any) -> dict[str, Any]:
        result = dict(row)
        for field in ("input_reference", "output_reference"):
            result[field] = json.loads(result[field]) if result.get(field) else {}
        return result


class PersistentModelRuntime:
    """Resolve a persisted route and record every gateway invocation."""

    def __init__(self, repository: ModelRepository, gateway: Optional[Any] = None):
        self.repository = repository
        self.gateway = gateway or ModelGateway()
        self._task_id: ContextVar[Optional[str]] = ContextVar("novelforge_model_task_id", default=None)

    @contextmanager
    def task_scope(self, task_id: str) -> Iterator[None]:
        token = self._task_id.set(task_id)
        try:
            yield
        finally:
            self._task_id.reset(token)

    def invoke(self, role: str, messages: list[dict[str, Any]], system: str = "", *, json_mode: bool = False,
               provider_id: Optional[str] = None, **kwargs: Any) -> LLMResponse:
        task_id = self._task_id.get()
        if not task_id:
            raise ModelConfigurationError("MODEL_TASK_CONTEXT_REQUIRED", "model invocation requires a durable task")
        resolved = self.repository.resolve(role, provider_id=provider_id)
        run_id = self.repository.create_run(
            task_id=task_id, role=role, resolved=resolved,
            prompt_key=kwargs.pop("prompt_key", None), prompt_version=kwargs.pop("prompt_version", None),
            input_reference={"message_count": len(messages), "system_chars": len(system),
                             "message_chars": sum(len(str(message.get("content", ""))) for message in messages)},
        )
        try:
            secret = self.repository.credentials.resolve(resolved.get("credential_ref"))
        except CredentialError as exc:
            self.repository.fail_run(run_id, exc.code)
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
            self.repository.fail_run(run_id, "MODEL_CONFIGURATION")
            raise ModelConfigurationError("MODEL_CONFIGURATION", "model configuration error") from exc
        try:
            response = self.gateway.chat(provider_name, messages, system, json_mode=json_mode, **kwargs)
        except Exception as exc:
            code = self._error_code(exc)
            self.repository.fail_run(run_id, code)
            raise ModelConfigurationError(code, "model provider invocation failed") from exc
        self.repository.finish_run(run_id, response)
        return response

    def test_provider(self, provider_id: str) -> LLMResponse:
        return self.invoke("writer", [{"role": "user", "content": "Connection check"}], provider_id=provider_id,
                           max_tokens=10)

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

    def __init__(self, runtime: PersistentModelRuntime, role: str):
        self.runtime = runtime
        self.role = role

    def chat(self, messages: list[dict[str, Any]], system: str = "", **kwargs: Any) -> LLMResponse:
        return self.runtime.invoke(self.role, messages, system, **kwargs)

    def chat_json(self, messages: list[dict[str, Any]], system: str = "", **kwargs: Any) -> dict[str, Any]:
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
        "review": "reviewer",
        "revision": "reviser",
        "fact-extraction": "fact_extraction",
        "story-bible-suggest": "planner",
        "joint-review": "reviewer",
    }

    def __init__(self, runtime: PersistentModelRuntime):
        self.runtime = runtime
        self._clients: dict[str, PersistentModelClient] = {}

    def task_scope(self, task_id: str) -> ContextManager[None]:
        return self.runtime.task_scope(task_id)

    def get_client(self, role: str = "primary") -> PersistentModelClient:
        resolved_role = self._legacy_roles.get(role, role)
        if resolved_role not in MODEL_ROLES:
            resolved_role = "writer"
        if resolved_role not in self._clients:
            self._clients[resolved_role] = PersistentModelClient(self.runtime, resolved_role)
        return self._clients[resolved_role]

    def get_writer(self) -> PersistentModelClient:
        return self.get_client("writer")

    def get_reviewer(self) -> PersistentModelClient:
        return self.get_client("reviewer")

    def get_planner(self) -> PersistentModelClient:
        return self.get_client("planner")

    def chat(self, messages: list[dict[str, Any]], system: str = "", *, task_type: Optional[str] = None,
             **kwargs: Any) -> LLMResponse:
        """Route the pipeline's legacy ``chat`` call through a durable agent role."""
        role = self._task_roles.get(task_type or "", "writer")
        return self.get_client(role).chat(messages, system, **kwargs)

    def chat_json(self, messages: list[dict[str, Any]], system: str = "", *, task_type: Optional[str] = None,
                  **kwargs: Any) -> dict[str, Any]:
        """Provide the same durable routing for JSON-constrained pipeline stages."""
        role = self._task_roles.get(task_type or "", "writer")
        return self.get_client(role).chat_json(messages, system, **kwargs)

    def test_provider(self, provider_id: str) -> LLMResponse:
        return self.runtime.test_provider(provider_id)


def build_model_runtime(db: Database, workspace_root: Path) -> tuple[ModelRepository, PersistentModelRuntime, PersistentMultiModelManager]:
    repository = ModelRepository(db, CredentialStore(workspace_root))
    runtime = PersistentModelRuntime(repository)
    return repository, runtime, PersistentMultiModelManager(runtime)
