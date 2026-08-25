"""Runtime Plane registry, discovery, and lifecycle state.

The registry records what NovelForge knows about an intelligence runtime.  It
does not treat a manifest as proof that a process is healthy or authenticated.
Those states are advanced only by discovery, authentication, capability
checks, and supervised health checks.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping

from src.core.database import Database

from .contracts import AuthState, RuntimeCapabilities
from .errors import AuthenticationRequired, RuntimeUnavailable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InstallState(str, Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLING = "installing"
    INSTALLED = "installed"
    NOT_AUTHENTICATED = "not_authenticated"
    AUTHENTICATED = "authenticated"
    CAPABILITY_VERIFIED = "capability_verified"
    READY = "ready"
    BROKEN = "broken"
    NEEDS_UPDATE = "needs_update"
    INCOMPATIBLE = "incompatible"
    REPAIRING = "repairing"


class AcquisitionType(str, Enum):
    BUILTIN = "builtin"
    MANAGED = "managed"
    SYSTEM = "system"
    CUSTOM = "custom"


@dataclass(frozen=True)
class RuntimeManifest:
    runtime_type: str
    display_name: str
    version: str
    protocol: str
    acquisition: AcquisitionType = AcquisitionType.SYSTEM
    executable: str | None = None
    command: tuple[str, ...] = ()
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    models: tuple[Mapping[str, Any], ...] = ()
    dependencies: tuple[Mapping[str, Any], ...] = ()
    source: str = "builtin"
    signature: str | None = None
    minimum_host_version: str | None = None

    def __post_init__(self) -> None:
        for name in ("runtime_type", "display_name", "version", "protocol"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"manifest {name} is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtimeType": self.runtime_type,
            "displayName": self.display_name,
            "version": self.version,
            "protocol": self.protocol,
            "acquisition": self.acquisition.value,
            "executable": self.executable,
            "command": list(self.command),
            "capabilities": dict(self.capabilities),
            "models": [dict(item) for item in self.models],
            "dependencies": [dict(item) for item in self.dependencies],
            "source": self.source,
            "signature": self.signature,
            "minimumHostVersion": self.minimum_host_version,
        }


@dataclass(frozen=True)
class RuntimeInstallation:
    runtime_type: str
    state: InstallState = InstallState.NOT_INSTALLED
    path: str | None = None
    version: str | None = None
    auth: AuthState = field(default_factory=AuthState)
    capability_verified: bool = False
    health: str = "unknown"
    last_error: str | None = None
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtimeType": self.runtime_type,
            "state": self.state.value,
            "path": self.path,
            "version": self.version,
            "auth": {
                "status": self.auth.status,
                "accountLabel": self.auth.account_label,
                "detail": self.auth.detail,
            },
            "capabilityVerified": self.capability_verified,
            "health": self.health,
            "lastError": self.last_error,
            "updatedAt": self.updated_at,
        }


class DependencyResolver:
    """Resolve manifest-level dependencies without executing installers."""

    def resolve(self, manifest: RuntimeManifest, available: Mapping[str, str]) -> list[dict[str, str]]:
        missing: list[dict[str, str]] = []
        for dependency in manifest.dependencies:
            name = str(dependency.get("name") or "").strip()
            minimum = str(dependency.get("minimumVersion") or "")
            if not name or name not in available:
                missing.append({"name": name, "minimumVersion": minimum, "reason": "missing"})
            elif minimum and self._version_key(available[name]) < self._version_key(minimum):
                missing.append({
                    "name": name,
                    "minimumVersion": minimum,
                    "availableVersion": available[name],
                    "reason": "version",
                })
        return missing

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...]:
        parts: list[int] = []
        for part in str(value).split("."):
            digits = "".join(char for char in part if char.isdigit())
            parts.append(int(digits or 0))
        return tuple((parts + [0, 0, 0])[:3])


class RuntimeRegistry:
    """Persistent registry of runtime manifests and observed installations."""

    _TRANSITIONS = {
        InstallState.NOT_INSTALLED: {InstallState.INSTALLING, InstallState.INSTALLED},
        InstallState.INSTALLING: {InstallState.INSTALLED, InstallState.BROKEN, InstallState.INCOMPATIBLE},
        InstallState.INSTALLED: {InstallState.NOT_AUTHENTICATED, InstallState.AUTHENTICATED,
                                 InstallState.CAPABILITY_VERIFIED, InstallState.BROKEN,
                                 InstallState.NEEDS_UPDATE, InstallState.REPAIRING},
        InstallState.NOT_AUTHENTICATED: {InstallState.AUTHENTICATED, InstallState.BROKEN,
                                         InstallState.REPAIRING},
        InstallState.AUTHENTICATED: {InstallState.CAPABILITY_VERIFIED, InstallState.BROKEN,
                                     InstallState.NEEDS_UPDATE, InstallState.REPAIRING},
        InstallState.CAPABILITY_VERIFIED: {InstallState.READY, InstallState.BROKEN,
                                           InstallState.NEEDS_UPDATE, InstallState.REPAIRING},
        InstallState.READY: {InstallState.BROKEN, InstallState.NEEDS_UPDATE, InstallState.REPAIRING,
                             InstallState.NOT_AUTHENTICATED, InstallState.AUTHENTICATED},
        InstallState.BROKEN: {InstallState.REPAIRING, InstallState.INSTALLING, InstallState.NOT_INSTALLED},
        InstallState.NEEDS_UPDATE: {InstallState.INSTALLING, InstallState.REPAIRING, InstallState.BROKEN},
        InstallState.INCOMPATIBLE: {InstallState.REPAIRING, InstallState.NOT_INSTALLED},
        InstallState.REPAIRING: {InstallState.INSTALLED, InstallState.BROKEN, InstallState.INCOMPATIBLE},
    }

    def __init__(self, db: Database | None = None):
        self.db = db
        self._manifests: dict[str, RuntimeManifest] = {}
        self._installations: dict[str, RuntimeInstallation] = {}
        if db is not None:
            self._load()

    def register_manifest(self, manifest: RuntimeManifest) -> RuntimeManifest:
        self._manifests[manifest.runtime_type] = manifest
        self._persist_manifest(manifest)
        if manifest.runtime_type not in self._installations:
            self._set_installation(RuntimeInstallation(manifest.runtime_type, version=manifest.version))
        else:
            current = self._installations[manifest.runtime_type]
            if current.version and current.version != manifest.version and current.state is InstallState.READY:
                self._set_installation(self._replace(current, state=InstallState.NEEDS_UPDATE, version=manifest.version))
        return manifest

    def get_manifest(self, runtime_type: str) -> RuntimeManifest | None:
        return self._manifests.get(runtime_type)

    def get_installation(self, runtime_type: str) -> RuntimeInstallation | None:
        return self._installations.get(runtime_type)

    def list(self) -> list[dict[str, Any]]:
        return [
            {"manifest": manifest.to_dict(), "installation": self._installations.get(
                runtime_type, RuntimeInstallation(runtime_type)
            ).to_dict()}
            for runtime_type, manifest in sorted(self._manifests.items())
        ]

    def discover(self, runtime_type: str) -> RuntimeInstallation:
        manifest = self._require_manifest(runtime_type)
        path = shutil.which(manifest.executable) if manifest.executable else None
        if manifest.acquisition is AcquisitionType.BUILTIN:
            installation = RuntimeInstallation(
                runtime_type, InstallState.INSTALLED, path=manifest.executable,
                version=manifest.version, health="unknown",
            )
        elif path:
            installation = RuntimeInstallation(
                runtime_type, InstallState.INSTALLED, path=path,
                version=manifest.version, health="unknown",
            )
        else:
            installation = RuntimeInstallation(runtime_type, InstallState.NOT_INSTALLED, version=manifest.version)
        self._set_installation(installation)
        return installation

    def mark_authenticated(self, runtime_type: str, auth: AuthState) -> RuntimeInstallation:
        current = self._require_installation(runtime_type)
        state = InstallState.AUTHENTICATED if auth.status in {"authenticated", "ready"} else InstallState.NOT_AUTHENTICATED
        return self._transition(current, state, auth=auth)

    def mark_capability_verified(self, runtime_type: str, capabilities: RuntimeCapabilities) -> RuntimeInstallation:
        current = self._require_installation(runtime_type)
        if current.state not in {InstallState.AUTHENTICATED, InstallState.INSTALLED, InstallState.CAPABILITY_VERIFIED}:
            raise RuntimeUnavailable(f"runtime is not installed/authenticated: {runtime_type}")
        return self._transition(current, InstallState.CAPABILITY_VERIFIED,
                                capability_verified=True, health="healthy")

    def mark_health(self, runtime_type: str, *, healthy: bool, detail: str = "") -> RuntimeInstallation:
        current = self._require_installation(runtime_type)
        if healthy and current.capability_verified:
            return self._transition(current, InstallState.READY, health="healthy", last_error=None)
        if healthy:
            return self._replace(current, health="healthy", last_error=None)
        return self._transition(current, InstallState.BROKEN, health="unhealthy", last_error=detail or "health check failed")

    def require_ready(self, runtime_type: str) -> RuntimeManifest:
        manifest = self._require_manifest(runtime_type)
        installation = self._require_installation(runtime_type)
        if installation.state is not InstallState.READY:
            if installation.state is InstallState.NOT_AUTHENTICATED:
                raise AuthenticationRequired(f"runtime is not authenticated: {runtime_type}")
            raise RuntimeUnavailable(f"runtime is not ready: {runtime_type} ({installation.state.value})")
        return manifest

    def set_error(self, runtime_type: str, detail: str) -> RuntimeInstallation:
        current = self._require_installation(runtime_type)
        return self._transition(current, InstallState.BROKEN, health="unhealthy", last_error=detail)

    def _transition(self, current: RuntimeInstallation, state: InstallState, **changes: Any) -> RuntimeInstallation:
        if state is not current.state and state not in self._TRANSITIONS.get(current.state, set()):
            raise ValueError(f"illegal runtime state transition: {current.state.value} -> {state.value}")
        updated = self._replace(current, state=state, **changes)
        self._set_installation(updated)
        return updated

    def _set_installation(self, installation: RuntimeInstallation) -> None:
        self._installations[installation.runtime_type] = installation
        if self.db is None:
            return
        self.db.execute(
            """INSERT INTO runtime_installations(
                   runtime_type, state, path, version, auth_status, account_label,
                   auth_detail, capability_verified, health, last_error, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(runtime_type) DO UPDATE SET state=excluded.state,
                   path=excluded.path, version=excluded.version,
                   auth_status=excluded.auth_status, account_label=excluded.account_label,
                   auth_detail=excluded.auth_detail, capability_verified=excluded.capability_verified,
                   health=excluded.health, last_error=excluded.last_error, updated_at=excluded.updated_at""",
            (
                installation.runtime_type, installation.state.value, installation.path,
                installation.version, installation.auth.status, installation.auth.account_label,
                installation.auth.detail, int(installation.capability_verified), installation.health,
                installation.last_error, installation.updated_at,
            ),
        )

    def _load(self) -> None:
        db = self.db
        if db is None:
            return
        try:
            rows = db.fetchall("SELECT * FROM runtime_registry ORDER BY runtime_type")
            for row in rows:
                self._manifests[row["runtime_type"]] = RuntimeManifest(
                    runtime_type=row["runtime_type"], display_name=row["display_name"],
                    version=row["version"], protocol=row["protocol"],
                    acquisition=AcquisitionType(row["acquisition"]),
                    executable=row.get("executable"), command=tuple(json.loads(row.get("command") or "[]")),
                    capabilities=json.loads(row.get("capabilities") or "{}"),
                    models=tuple(json.loads(row.get("models") or "[]")),
                    dependencies=tuple(json.loads(row.get("dependencies") or "[]")),
                    source=row.get("source") or "", signature=row.get("signature"),
                    minimum_host_version=row.get("minimum_host_version"),
                )
            rows = db.fetchall("SELECT * FROM runtime_installations")
            for row in rows:
                self._installations[row["runtime_type"]] = RuntimeInstallation(
                    runtime_type=row["runtime_type"], state=InstallState(row["state"]),
                    path=row.get("path"), version=row.get("version"),
                    auth=AuthState(row.get("auth_status") or "unknown", row.get("account_label"), row.get("auth_detail") or ""),
                    capability_verified=bool(row.get("capability_verified")), health=row.get("health") or "unknown",
                    last_error=row.get("last_error"), updated_at=row.get("updated_at") or _now(),
                )
        except Exception as exc:
            # The registry is an additive feature.  A database that predates
            # its migration remains usable, but must not be reported as loaded.
            if "no such table" not in str(exc).lower():
                raise

    def _persist_manifest(self, manifest: RuntimeManifest) -> None:
        if self.db is None:
            return
        self.db.execute(
            """INSERT INTO runtime_registry(
                   runtime_type, display_name, version, protocol, acquisition,
                   executable, command, capabilities, models, dependencies,
                   source, signature, minimum_host_version, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(runtime_type) DO UPDATE SET display_name=excluded.display_name,
                   version=excluded.version, protocol=excluded.protocol,
                   acquisition=excluded.acquisition, executable=excluded.executable,
                   command=excluded.command, capabilities=excluded.capabilities,
                   models=excluded.models, dependencies=excluded.dependencies,
                   source=excluded.source, signature=excluded.signature,
                   minimum_host_version=excluded.minimum_host_version, updated_at=excluded.updated_at""",
            (
                manifest.runtime_type, manifest.display_name, manifest.version, manifest.protocol,
                manifest.acquisition.value, manifest.executable, json.dumps(manifest.command),
                json.dumps(manifest.capabilities), json.dumps(manifest.models), json.dumps(manifest.dependencies),
                manifest.source, manifest.signature, manifest.minimum_host_version, _now(),
            ),
        )

    def _require_manifest(self, runtime_type: str) -> RuntimeManifest:
        manifest = self._manifests.get(runtime_type)
        if manifest is None:
            raise KeyError(f"runtime manifest not found: {runtime_type}")
        return manifest

    def _require_installation(self, runtime_type: str) -> RuntimeInstallation:
        installation = self._installations.get(runtime_type)
        if installation is None:
            raise KeyError(f"runtime installation not found: {runtime_type}")
        return installation

    @staticmethod
    def _replace(current: RuntimeInstallation, **changes: Any) -> RuntimeInstallation:
        values = {
            "runtime_type": current.runtime_type, "state": current.state, "path": current.path,
            "version": current.version, "auth": current.auth, "capability_verified": current.capability_verified,
            "health": current.health, "last_error": current.last_error, "updated_at": _now(),
        }
        values.update(changes)
        return RuntimeInstallation(**values)


class InstallerBroker:
    """Lifecycle facade with a hard boundary around untrusted installers."""

    def __init__(self, registry: RuntimeRegistry, *, executor: Callable[[RuntimeManifest], str] | None = None):
        self.registry = registry
        self.executor = executor

    def install(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        manifest = self.registry._require_manifest(runtime_type)
        current = self.registry._require_installation(runtime_type)
        if manifest.acquisition in {AcquisitionType.CUSTOM, AcquisitionType.MANAGED}:
            if not approved or self.executor is None:
                raise RuntimeUnavailable("managed/custom runtime installation requires explicit approved executor")
            self.registry._transition(current, InstallState.INSTALLING)
            try:
                path = self.executor(manifest)
            except Exception as exc:
                return self.registry.set_error(runtime_type, str(exc))
            return self.registry._transition(
                self.registry._require_installation(runtime_type), InstallState.INSTALLED,
                path=path, version=manifest.version, health="unknown",
            )
        installation = self.registry.discover(runtime_type)
        if installation.state is InstallState.NOT_INSTALLED:
            raise RuntimeUnavailable(f"runtime executable not found: {runtime_type}")
        return installation

    def repair(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        current = self.registry._require_installation(runtime_type)
        self.registry._transition(current, InstallState.REPAIRING)
        try:
            return self.install(runtime_type, approved=approved)
        except Exception as exc:
            return self.registry.set_error(runtime_type, str(exc))

    def uninstall(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        if not approved:
            raise RuntimeUnavailable("uninstall requires explicit approval")
        current = self.registry._require_installation(runtime_type)
        return self.registry._transition(
            current, InstallState.NOT_INSTALLED, path=None, capability_verified=False,
            health="unknown", last_error=None,
        )
