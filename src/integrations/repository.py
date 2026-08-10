"""Durable registries for user-defined Skills and MCP server definitions.

The registries deliberately stop at configuration.  A Skill is instruction
text that can be selected by an Agent, while an MCP record describes how a
future tool host should launch/connect to a server.  Neither registry executes
user supplied code during CRUD operations.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Optional

from src.core.database import Database, generate_id


_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_NAME_RE = re.compile(r"(api[_-]?key|token|secret|password|authorization|credential)", re.I)
_MCP_TRANSPORTS = {"stdio", "sse", "streamable_http", "streamable-http", "http"}
_MAX_SKILL_INSTRUCTIONS = 200_000
_MAX_MCP_ARGS = 128
_MAX_MCP_ARG_CHARS = 4_096
_EXTENSION_TYPES = {"skill", "mcp"}


def _skill_key(name: str) -> str:
    candidate = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-._")
    return (candidate or f"skill-{generate_id()[:12]}")[:64]


class ExtensionConfigurationError(ValueError):
    """A user extension definition is invalid or unsafe to persist."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ExtensionConfigurationError("EXTENSION_JSON_INVALID", "extension JSON is invalid") from exc
    return value


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _string(value: Any, field: str, *, required: bool = False, max_chars: int = 100_000) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ExtensionConfigurationError("EXTENSION_FIELD_INVALID", f"{field} must be text")
    value = value.strip()
    if required and not value:
        raise ExtensionConfigurationError("EXTENSION_FIELD_REQUIRED", f"{field} is required")
    if len(value) > max_chars:
        raise ExtensionConfigurationError("EXTENSION_FIELD_TOO_LARGE", f"{field} is too large")
    return value


def _bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ExtensionConfigurationError("EXTENSION_FIELD_INVALID", "enabled must be a boolean")


def _project_overrides(db: Database, project_id: Optional[str], extension_type: str) -> dict[str, bool]:
    if not project_id:
        return {}
    rows = db.fetchall(
        "SELECT extension_id, enabled FROM agent_extension_overrides "
        "WHERE project_id=? AND extension_type=?",
        (project_id, extension_type),
    )
    return {row["extension_id"]: bool(row["enabled"]) for row in rows}


def _set_project_override(
    db: Database,
    *,
    project_id: str,
    extension_type: str,
    extension_id: str,
    enabled: bool,
) -> None:
    if not isinstance(project_id, str) or not project_id.strip():
        raise ExtensionConfigurationError("EXTENSION_PROJECT_INVALID", "project id is required")
    if extension_type not in _EXTENSION_TYPES:
        raise ExtensionConfigurationError("EXTENSION_TYPE_INVALID", "unsupported extension type")
    if not isinstance(extension_id, str) or not extension_id.strip():
        raise ExtensionConfigurationError("EXTENSION_ID_INVALID", "extension id is required")
    if not isinstance(enabled, bool):
        raise ExtensionConfigurationError("EXTENSION_ENABLED_INVALID", "enabled must be a boolean")
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO agent_extension_overrides(project_id, extension_type, extension_id, enabled)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(project_id, extension_type, extension_id) DO UPDATE SET
               enabled=excluded.enabled, updated_at=CURRENT_TIMESTAMP""",
            (project_id.strip(), extension_type, extension_id.strip(), enabled),
        )


def _clear_project_override(db: Database, project_id: str, extension_type: str, extension_id: str) -> bool:
    with db.transaction() as conn:
        return conn.execute(
            "DELETE FROM agent_extension_overrides WHERE project_id=? AND extension_type=? AND extension_id=?",
            (project_id, extension_type, extension_id),
        ).rowcount == 1


class SkillRepository:
    """SQLite boundary for built-in and user-created Skills."""

    def __init__(self, db: Database):
        self.db = db

    def seed_builtins(self) -> int:
        """Install or refresh the shipped workflow Skills idempotently.

        Tests and isolated repository callers can opt out simply by not
        calling this method.  Studio calls it once for its authoritative
        database, so a user-created Skill with the same key is never silently
        overwritten.
        """
        from .builtin_skills import BUILTIN_SKILLS

        installed = 0
        for payload in BUILTIN_SKILLS:
            key = payload["key"]
            existing = self.db.fetchone(
                "SELECT id, source FROM skills WHERE key=? OR name=? LIMIT 1",
                (key, payload["name"]),
            )
            if existing and existing.get("source") != "builtin":
                continue
            if existing:
                current = self.get(existing["id"])
                if current and (
                    current.get("name") == payload.get("name")
                    and current.get("description") == payload.get("description")
                    and (current.get("instructions") or "").strip() == (payload.get("instructions") or "").strip()
                    and (current.get("config") or {}).get("inkosWorkflow") == key
                ):
                    continue
            self.save(
                {**payload, "id": existing["id"] if existing else None},
                skill_id=existing["id"] if existing else None,
            )
            installed += 1
        return installed

    def list(self, *, enabled_only: bool = False, project_id: Optional[str] = None) -> list[dict[str, Any]]:
        where = " WHERE enabled=1" if enabled_only and not project_id else ""
        rows = self.db.fetchall(f"SELECT * FROM skills{where} ORDER BY name, id")
        overrides = _project_overrides(self.db, project_id, "skill")
        result = []
        for row in rows:
            item = self._dict(row)
            if project_id:
                global_enabled = item["enabled"]
                project_override = overrides.get(item["id"])
                item["globalEnabled"] = global_enabled
                item["projectOverride"] = project_override
                item["enabled"] = project_override if project_override is not None else global_enabled
                item["effectiveEnabled"] = item["enabled"]
            if not enabled_only or item["enabled"]:
                result.append(item)
        return result

    def get(self, skill_id: str) -> Optional[dict[str, Any]]:
        row = self.db.fetchone("SELECT * FROM skills WHERE id=?", (skill_id,))
        return self._dict(row) if row else None

    def save(self, payload: dict[str, Any], *, skill_id: Optional[str] = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ExtensionConfigurationError("SKILL_INVALID", "skill must be an object")
        skill_id = skill_id or (payload.get("id") if isinstance(payload.get("id"), str) else None) or generate_id()
        name = _string(payload.get("name"), "name", required=True, max_chars=120)
        requested_key = payload.get("key") or payload.get("skillKey")
        if not requested_key and skill_id:
            existing_key = self.db.fetchone("SELECT key FROM skills WHERE id=?", (skill_id,))
            requested_key = existing_key["key"] if existing_key and existing_key["key"] else None
        key = _string(requested_key or _skill_key(name), "key", max_chars=64)
        if not _KEY_RE.fullmatch(key):
            raise ExtensionConfigurationError("SKILL_KEY_INVALID", "skill key must use lowercase letters, digits, '.', '_' or '-'")
        description = _string(payload.get("description"), "description", max_chars=4_000)
        instructions = _string(
            payload.get("instructions", payload.get("content", "")),
            "instructions",
            required=True,
            max_chars=_MAX_SKILL_INSTRUCTIONS,
        )
        config = _json(payload.get("config"), {})
        if not isinstance(config, dict):
            raise ExtensionConfigurationError("SKILL_CONFIG_INVALID", "skill config must be an object")
        definition = payload.get("definition")
        if definition is not None:
            if not isinstance(definition, dict):
                raise ExtensionConfigurationError("SKILL_DEFINITION_INVALID", "skill definition must be an object")
            config = {**config, "definition": definition}
        enabled = _bool(payload.get("enabled"), True)
        source = _string(payload.get("source") or "user", "source", max_chars=40) or "user"

        with self.db.transaction() as conn:
            existing_row = conn.execute("SELECT source FROM skills WHERE id=?", (skill_id,)).fetchone()
            if existing_row and existing_row["source"] == "builtin" and source != "builtin":
                raise ExtensionConfigurationError(
                    "SKILL_BUILTIN_PROTECTED", "built-in skills can only be refreshed by the system"
                )
            duplicate = conn.execute(
                "SELECT id FROM skills WHERE (name=? OR (key IS NOT NULL AND key=?)) AND id<>?",
                (name, key, skill_id),
            ).fetchone()
            if duplicate:
                raise ExtensionConfigurationError("SKILL_DUPLICATE", "skill name or key is already in use")
            existing = conn.execute("SELECT version FROM skills WHERE id=?", (skill_id,)).fetchone()
            version = int(existing["version"] or 0) + 1 if existing else 1
            if existing:
                conn.execute(
                    """UPDATE skills SET name=?, key=?, description=?, instructions=?, enabled=?,
                       version=?, source=?, config=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (name, key, description, instructions, enabled, version, source, _dump(config), skill_id),
                )
            else:
                conn.execute(
                    """INSERT INTO skills(id, name, key, description, instructions, enabled, version, source, config)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (skill_id, name, key, description, instructions, enabled, version, source, _dump(config)),
                )
        result = self.get(skill_id)
        if result is None:
            raise ExtensionConfigurationError("SKILL_PERSISTENCE", "skill was not persisted")
        return result

    def set_enabled(self, skill_id: str, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ExtensionConfigurationError("SKILL_ENABLED_INVALID", "enabled must be a boolean")
        with self.db.transaction() as conn:
            updated = conn.execute(
                "UPDATE skills SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (enabled, skill_id),
            )
            if updated.rowcount != 1:
                raise ExtensionConfigurationError("SKILL_NOT_FOUND", "skill was not found")
        return self.get(skill_id) or {}

    def set_project_enabled(self, project_id: str, skill_id: str, enabled: bool) -> dict[str, Any]:
        if self.get(skill_id) is None:
            raise ExtensionConfigurationError("SKILL_NOT_FOUND", "skill was not found")
        _set_project_override(
            self.db, project_id=project_id, extension_type="skill", extension_id=skill_id, enabled=enabled
        )
        return next(item for item in self.list(project_id=project_id) if item["id"] == skill_id)

    def clear_project_override(self, project_id: str, skill_id: str) -> bool:
        return _clear_project_override(self.db, project_id, "skill", skill_id)

    def delete(self, skill_id: str) -> bool:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT source FROM skills WHERE id=?", (skill_id,)).fetchone()
            if row is None:
                return False
            if row["source"] == "builtin":
                raise ExtensionConfigurationError("SKILL_BUILTIN_PROTECTED", "built-in skills cannot be deleted")
            deleted = conn.execute("DELETE FROM skills WHERE id=?", (skill_id,)).rowcount == 1
            conn.execute(
                "DELETE FROM agent_extension_overrides WHERE extension_type='skill' AND extension_id=?",
                (skill_id,),
            )
            return deleted

    def instructions_for(self, skill_ids: list[str], project_id: Optional[str] = None) -> list[dict[str, str]]:
        """Resolve enabled Skills by id or key for an Agent request."""
        if not isinstance(skill_ids, list):
            raise ExtensionConfigurationError("SKILL_SELECTION_INVALID", "skillIds must be an array")
        resolved: list[dict[str, str]] = []
        seen: set[str] = set()
        for identifier in skill_ids[:32]:
            if not isinstance(identifier, str) or not identifier.strip():
                continue
            if project_id:
                row = self.db.fetchone(
                    """SELECT s.id, s.name, s.instructions
                       FROM skills s LEFT JOIN agent_extension_overrides o
                         ON o.project_id=? AND o.extension_type='skill' AND o.extension_id=s.id
                       WHERE COALESCE(o.enabled, s.enabled)=1 AND (s.id=? OR s.key=?)""",
                    (project_id, identifier.strip(), identifier.strip()),
                )
            else:
                row = self.db.fetchone(
                    "SELECT id, name, instructions FROM skills WHERE enabled=1 AND (id=? OR key=?)",
                    (identifier.strip(), identifier.strip()),
                )
            if row and row["id"] not in seen:
                seen.add(row["id"])
                resolved.append({"id": row["id"], "name": row["name"], "instructions": row["instructions"]})
        return resolved

    @staticmethod
    def _dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["enabled"] = bool(result.get("enabled"))
        result["version"] = int(result.get("version") or 1)
        result["config"] = _json(result.get("config"), {})
        result["definition"] = result["config"].get("definition") if isinstance(result["config"], dict) else None
        return result


class MCPServerRepository:
    """SQLite boundary for user-managed MCP server connection definitions."""

    def __init__(self, db: Database):
        self.db = db

    def list(self, *, enabled_only: bool = False, project_id: Optional[str] = None) -> list[dict[str, Any]]:
        where = " WHERE enabled=1" if enabled_only and not project_id else ""
        rows = self.db.fetchall(f"SELECT * FROM mcp_servers{where} ORDER BY name, id")
        overrides = _project_overrides(self.db, project_id, "mcp")
        result = []
        for row in rows:
            item = self._dict(row)
            if project_id:
                global_enabled = item["enabled"]
                project_override = overrides.get(item["id"])
                item["globalEnabled"] = global_enabled
                item["projectOverride"] = project_override
                item["enabled"] = project_override if project_override is not None else global_enabled
                item["effectiveEnabled"] = item["enabled"]
            if not enabled_only or item["enabled"]:
                result.append(item)
        return result

    def get(self, server_id: str) -> Optional[dict[str, Any]]:
        row = self.db.fetchone("SELECT * FROM mcp_servers WHERE id=?", (server_id,))
        return self._dict(row) if row else None

    def save(self, payload: dict[str, Any], *, server_id: Optional[str] = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ExtensionConfigurationError("MCP_INVALID", "MCP server must be an object")
        server_id = server_id or (payload.get("id") if isinstance(payload.get("id"), str) else None) or generate_id()
        name = _string(payload.get("name"), "name", required=True, max_chars=120)
        transport = _string(payload.get("transport") or "stdio", "transport", max_chars=32).lower()
        if transport == "streamable-http" or transport == "http":
            transport = "streamable_http"
        if transport not in {"stdio", "sse", "streamable_http"}:
            raise ExtensionConfigurationError("MCP_TRANSPORT_INVALID", "transport must be stdio, sse, or streamable_http")
        command = _string(payload.get("command"), "command", max_chars=1_000)
        url = _string(payload.get("url"), "url", max_chars=4_000)
        if transport == "stdio" and not command:
            raise ExtensionConfigurationError("MCP_COMMAND_REQUIRED", "stdio MCP servers require a command")
        if transport != "stdio":
            if not url:
                raise ExtensionConfigurationError("MCP_URL_REQUIRED", "remote MCP servers require a URL")
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ExtensionConfigurationError("MCP_URL_INVALID", "MCP URL must use http or https")
        args = _json(payload.get("args"), [])
        if not isinstance(args, list) or len(args) > _MAX_MCP_ARGS or any(
            not isinstance(item, str) or len(item) > _MAX_MCP_ARG_CHARS for item in args
        ):
            raise ExtensionConfigurationError("MCP_ARGS_INVALID", "MCP args must be a list of short strings")
        environment = _json(payload.get("environment", payload.get("env")), {})
        headers = _json(payload.get("headers"), {})
        self._validate_bindings(environment, "environment", require_secret_refs=False)
        self._validate_bindings(headers, "headers", require_secret_refs=True)
        config = _json(payload.get("config"), {})
        if not isinstance(config, dict):
            raise ExtensionConfigurationError("MCP_CONFIG_INVALID", "MCP config must be an object")
        enabled = _bool(payload.get("enabled"), True)

        with self.db.transaction() as conn:
            duplicate = conn.execute(
                "SELECT id FROM mcp_servers WHERE name=? AND id<>?", (name, server_id)
            ).fetchone()
            if duplicate:
                raise ExtensionConfigurationError("MCP_DUPLICATE", "MCP server name is already in use")
            conn.execute(
                """INSERT INTO mcp_servers(id, name, transport, command, args, url, environment, headers, enabled, config)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, transport=excluded.transport,
                   command=excluded.command, args=excluded.args, url=excluded.url,
                   environment=excluded.environment, headers=excluded.headers, enabled=excluded.enabled,
                   config=excluded.config, updated_at=CURRENT_TIMESTAMP""",
                (server_id, name, transport, command, _dump(args), url, _dump(environment), _dump(headers), enabled, _dump(config)),
            )
        result = self.get(server_id)
        if result is None:
            raise ExtensionConfigurationError("MCP_PERSISTENCE", "MCP server was not persisted")
        return result

    def set_enabled(self, server_id: str, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ExtensionConfigurationError("MCP_ENABLED_INVALID", "enabled must be a boolean")
        with self.db.transaction() as conn:
            updated = conn.execute(
                "UPDATE mcp_servers SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (enabled, server_id),
            )
            if updated.rowcount != 1:
                raise ExtensionConfigurationError("MCP_NOT_FOUND", "MCP server was not found")
        return self.get(server_id) or {}

    def set_project_enabled(self, project_id: str, server_id: str, enabled: bool) -> dict[str, Any]:
        if self.get(server_id) is None:
            raise ExtensionConfigurationError("MCP_NOT_FOUND", "MCP server was not found")
        _set_project_override(
            self.db, project_id=project_id, extension_type="mcp", extension_id=server_id, enabled=enabled
        )
        return next(item for item in self.list(project_id=project_id) if item["id"] == server_id)

    def clear_project_override(self, project_id: str, server_id: str) -> bool:
        return _clear_project_override(self.db, project_id, "mcp", server_id)

    def delete(self, server_id: str) -> bool:
        with self.db.transaction() as conn:
            deleted = conn.execute("DELETE FROM mcp_servers WHERE id=?", (server_id,)).rowcount == 1
            conn.execute(
                "DELETE FROM agent_extension_overrides WHERE extension_type='mcp' AND extension_id=?",
                (server_id,),
            )
            return deleted

    def validate(self, server_id: str) -> dict[str, Any]:
        server = self.get(server_id)
        if server is None:
            raise ExtensionConfigurationError("MCP_NOT_FOUND", "MCP server was not found")
        # This is intentionally a local definition check.  It never claims a
        # remote server is reachable until a real MCP host performs a handshake.
        return {"valid": True, "serverId": server_id, "transport": server["transport"], "connectivity": "not_tested"}

    @staticmethod
    def _validate_bindings(value: Any, field: str, *, require_secret_refs: bool) -> None:
        if not isinstance(value, dict):
            raise ExtensionConfigurationError("MCP_BINDINGS_INVALID", f"{field} must be an object")
        for key, item in value.items():
            if not isinstance(key, str) or not _ENV_RE.fullmatch(key) or len(key) > 128:
                raise ExtensionConfigurationError("MCP_BINDING_NAME_INVALID", f"{field} contains an invalid variable name")
            if not isinstance(item, str) or len(item) > 2_000:
                raise ExtensionConfigurationError("MCP_BINDING_VALUE_INVALID", f"{field} values must be short strings")
            if item.startswith("env:"):
                if not _ENV_RE.fullmatch(item[4:]):
                    raise ExtensionConfigurationError("MCP_CREDENTIAL_REF_INVALID", f"{field}.{key} has an invalid env reference")
            elif require_secret_refs or _SECRET_NAME_RE.search(key):
                raise ExtensionConfigurationError(
                    "MCP_CREDENTIAL_REF_REQUIRED",
                    f"{field}.{key} must use an env:NAME reference; raw secrets are not stored",
                )

    @staticmethod
    def _dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["enabled"] = bool(result.get("enabled"))
        for field, default in (("args", []), ("environment", {}), ("headers", {}), ("config", {})):
            result[field] = _json(result.get(field), default)
        return result
