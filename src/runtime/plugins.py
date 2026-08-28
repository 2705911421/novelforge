"""Host-owned Plugin Bus primitives.

The bus is deliberately a registration and discovery seam, not a dynamic
importer.  A manifest or catalog may describe a plugin, but only host code
that has passed the caller's trust decision can bind an executable
implementation.  Narrative authority, transaction semantics, and the
runtime contract remain outside the replaceable plugin surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Mapping

from .errors import PluginTrustError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PluginKind(str, Enum):
    RUNTIME = "runtime"
    DOMAIN = "domain"
    UI = "ui"
    STORAGE = "storage"
    INTEGRATION = "integration"


@dataclass(frozen=True)
class PluginDescriptor:
    """Stable, serializable metadata for one host-bound plugin."""

    plugin_id: str
    kind: PluginKind
    display_name: str
    version: str
    source_kind: str = "builtin"
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("plugin_id", "display_name", "version"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"plugin {name} is required")
        kind = self.kind if isinstance(self.kind, PluginKind) else PluginKind(str(self.kind).strip().lower())
        object.__setattr__(self, "kind", kind)
        source_kind = str(self.source_kind or "builtin").strip().lower() or "builtin"
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "capabilities", dict(self.capabilities))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def key(self) -> tuple[PluginKind, str]:
        return self.kind, self.plugin_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "pluginId": self.plugin_id,
            "kind": self.kind.value,
            "displayName": self.display_name,
            "version": self.version,
            "sourceKind": self.source_kind,
            "capabilities": dict(self.capabilities),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PluginRegistration:
    descriptor: PluginDescriptor
    implementation: Any
    trusted: bool
    registered_at: str = field(default_factory=_now)


class PluginBus:
    """Register and resolve host-approved implementations by stable identity."""

    def __init__(self) -> None:
        self._registrations: dict[tuple[PluginKind, str], PluginRegistration] = {}
        self._lock = RLock()

    def register(
        self,
        descriptor: PluginDescriptor,
        implementation: Any,
        *,
        trusted: bool = False,
        replace: bool = False,
    ) -> PluginRegistration:
        if implementation is None:
            raise ValueError("plugin implementation is required")
        # A non-builtin/community implementation cannot become executable
        # merely by appearing in a manifest.  Host adapters may be marked
        # trusted explicitly even when the vendor source is external.
        if descriptor.source_kind not in {"builtin", "host"} and not trusted:
            raise PluginTrustError(
                f"plugin implementation requires explicit host trust: {descriptor.plugin_id}"
            )
        registration = PluginRegistration(descriptor, implementation, bool(trusted))
        with self._lock:
            if descriptor.key in self._registrations and not replace:
                raise ValueError(f"plugin already registered: {descriptor.kind.value}/{descriptor.plugin_id}")
            self._registrations[descriptor.key] = registration
        return registration

    def get(self, kind: PluginKind | str, plugin_id: str) -> PluginRegistration | None:
        normalized = kind if isinstance(kind, PluginKind) else PluginKind(str(kind).strip().lower())
        with self._lock:
            return self._registrations.get((normalized, str(plugin_id).strip()))

    def require(self, kind: PluginKind | str, plugin_id: str) -> PluginRegistration:
        registration = self.get(kind, plugin_id)
        if registration is None:
            normalized = kind.value if isinstance(kind, PluginKind) else str(kind)
            raise KeyError(f"plugin not registered: {normalized}/{plugin_id}")
        return registration

    def implementation(self, kind: PluginKind | str, plugin_id: str) -> Any:
        return self.require(kind, plugin_id).implementation

    def unregister(self, kind: PluginKind | str, plugin_id: str) -> None:
        normalized = kind if isinstance(kind, PluginKind) else PluginKind(str(kind).strip().lower())
        with self._lock:
            self._registrations.pop((normalized, str(plugin_id).strip()), None)

    def catalog(self) -> list[dict[str, Any]]:
        """Return safe metadata only; implementations never cross the API seam."""
        with self._lock:
            registrations = sorted(
                self._registrations.values(),
                key=lambda item: (item.descriptor.kind.value, item.descriptor.plugin_id),
            )
        return [
            {
                **registration.descriptor.to_dict(),
                "trusted": registration.trusted,
                "registeredAt": registration.registered_at,
            }
            for registration in registrations
        ]

