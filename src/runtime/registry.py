"""Runtime Plane registry, discovery, and lifecycle state.

The registry records what NovelForge knows about an intelligence runtime.  It
does not treat a manifest as proof that a process is healthy or authenticated.
Those states are advanced only by discovery, authentication, capability
checks, and supervised health checks.
"""

from __future__ import annotations

import json
import hashlib
import base64
import hmac
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from src.core.database import Database

from .contracts import AuthState, RuntimeCapabilities
from .errors import AuthenticationRequired, RuntimeIncompatible, RuntimeNotInstalled, RuntimeUnavailable


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
    BUNDLED = "bundled"
    DOWNLOAD_BINARY = "download_binary"
    PACKAGE_MANAGER = "package_manager"
    VENDOR_INSTALLER = "vendor_installer"
    COMMAND_BOOTSTRAP = "command_bootstrap"
    EXTERNAL = "external"
    # Legacy names remain readable for manifests created before the Runtime
    # Plane acquisition taxonomy was expanded.
    MANAGED = "managed"
    SYSTEM = "system"
    CUSTOM = "custom"


class RuntimeSource(str, Enum):
    """Where the executable is expected to come from."""

    BUILTIN = "builtin"
    MANAGED = "managed"
    SYSTEM = "system"
    CUSTOM = "custom"
    EXTERNAL = "external"


class InstallAction(str, Enum):
    DISCOVER = "discover"
    INSTALL = "install"
    UPDATE = "update"
    REPAIR = "repair"
    UNINSTALL = "uninstall"


@dataclass(frozen=True)
class PrerequisiteCheck:
    name: str
    required: bool
    available: bool
    detail: str = ""
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "available": self.available,
            "detail": self.detail,
            "version": self.version,
        }


@dataclass(frozen=True)
class PrerequisiteResult:
    ready: bool
    checks: tuple[PrerequisiteCheck, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"ready": self.ready, "checks": [item.to_dict() for item in self.checks]}


@dataclass(frozen=True)
class CompatibilityResult:
    compatible: bool
    warning: str | None = None
    reason: str | None = None
    observed_version: str | None = None
    minimum_version: str | None = None
    maximum_tested_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "compatible": self.compatible,
            "warning": self.warning,
            "reason": self.reason,
            "observedVersion": self.observed_version,
            "minimumVersion": self.minimum_version,
            "maximumTestedVersion": self.maximum_tested_version,
        }


@dataclass(frozen=True)
class InstallerPlan:
    runtime_type: str
    action: InstallAction
    source: RuntimeSource
    acquisition: AcquisitionType
    command: tuple[str, ...] = ()
    requires_approval: bool = False
    trusted: bool = False
    allowed: bool = True
    shell: bool = False
    risk: str = "low"
    explanation: str = ""
    artifact_url: str | None = None
    artifact_path: str | None = None
    artifact_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtimeType": self.runtime_type,
            "action": self.action.value,
            "source": self.source.value,
            "acquisition": self.acquisition.value,
            "command": list(self.command),
            "requiresApproval": self.requires_approval,
            "trusted": self.trusted,
            "allowed": self.allowed,
            "shell": self.shell,
            "risk": self.risk,
            "explanation": self.explanation,
            "artifactUrl": self.artifact_url,
            "artifactPath": self.artifact_path,
            "artifactSha256": self.artifact_sha256,
        }


@dataclass(frozen=True)
class InstallEvent:
    runtime_type: str
    action: InstallAction
    phase: str
    status: str
    message: str
    detail: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtimeType": self.runtime_type,
            "action": self.action.value,
            "phase": self.phase,
            "status": self.status,
            "message": self.message,
            "detail": dict(self.detail),
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    path: str | None = None
    version: str | None = None
    checks: tuple[str, ...] = ()
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "path": self.path,
            "version": self.version,
            "checks": list(self.checks),
            "reason": self.reason,
        }


class IPluginInstaller(Protocol):
    """Concrete installer contract behind the host-owned InstallerBroker."""

    def detect(self) -> RuntimeInstallation: ...

    def check_prerequisites(self) -> PrerequisiteResult: ...

    def plan(self, action: InstallAction) -> InstallerPlan: ...

    def install(self, *, approved: bool = False) -> RuntimeInstallation: ...

    def update(self, *, approved: bool = False) -> RuntimeInstallation: ...

    def repair(self, *, approved: bool = False) -> RuntimeInstallation: ...

    def uninstall(self, *, approved: bool = False) -> RuntimeInstallation: ...

    def verify(self) -> VerificationResult: ...


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
    source_kind: RuntimeSource = RuntimeSource.SYSTEM
    integration_grade: str = "C"
    platforms: Mapping[str, Any] = field(default_factory=dict)
    verification: Mapping[str, Any] = field(default_factory=dict)
    authentication: Mapping[str, Any] = field(default_factory=dict)
    compatibility: Mapping[str, Any] = field(default_factory=dict)
    installer: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("runtime_type", "display_name", "version", "protocol"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"manifest {name} is required")
        if not isinstance(self.acquisition, AcquisitionType):
            object.__setattr__(
                self,
                "acquisition",
                AcquisitionType(str(self.acquisition).strip().lower()),
            )
        grade = str(self.integration_grade).strip().upper()
        if grade not in {"S", "A", "B", "C", "D"}:
            raise ValueError("integration_grade must be one of S, A, B, C, D")
        object.__setattr__(self, "integration_grade", grade)
        if not isinstance(self.source_kind, RuntimeSource):
            object.__setattr__(
                self,
                "source_kind",
                RuntimeSource(str(self.source_kind).strip().lower()),
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RuntimeManifest":
        """Parse an external manifest without silently dropping bad fields."""
        if not isinstance(payload, Mapping):
            raise ValueError("runtime manifest must be an object")

        def required(name: str, *aliases: str) -> str:
            for key in (name, *aliases):
                value = payload.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
            raise ValueError(f"manifest {name} is required")

        def mapping(name: str, default: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
            value = payload.get(name)
            if value is None:
                return default or {}
            if not isinstance(value, Mapping):
                raise ValueError(f"manifest {name} must be an object")
            return dict(value)

        def sequence(name: str) -> tuple[Any, ...]:
            value = payload.get(name)
            if value is None:
                return ()
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise ValueError(f"manifest {name} must be an array")
            return tuple(value)

        def mapping_sequence(name: str) -> tuple[Mapping[str, Any], ...]:
            values = sequence(name)
            if any(not isinstance(item, Mapping) for item in values):
                raise ValueError(f"manifest {name} entries must be objects")
            return tuple(dict(item) for item in values)

        command = sequence("command")
        if any(not isinstance(item, (str, int, float, bool)) for item in command):
            raise ValueError("manifest command entries must be scalar argv values")
        source = str(payload.get("source") or "").strip()
        raw_source_kind = str(payload.get("sourceKind") or RuntimeSource.EXTERNAL.value)
        try:
            source_kind = RuntimeSource(raw_source_kind)
        except ValueError:
            source_kind = RuntimeSource.EXTERNAL
        return cls(
            runtime_type=required("runtimeType", "id"),
            display_name=required("displayName", "name", "id"),
            version=required("version"),
            protocol=required("protocol"),
            acquisition=payload.get("acquisition", AcquisitionType.SYSTEM.value),
            executable=str(payload.get("executable") or "") or None,
            command=tuple(str(item) for item in command),
            capabilities=mapping("capabilities"),
            models=mapping_sequence("models"),
            dependencies=mapping_sequence("dependencies"),
            source=source,
            signature=str(payload.get("signature") or "") or None,
            minimum_host_version=str(payload.get("minimumHostVersion") or "") or None,
            source_kind=source_kind,
            integration_grade=str(payload.get("integrationGrade") or "C"),
            platforms=mapping("platforms"),
            verification=mapping("verification"),
            authentication=mapping("authentication"),
            compatibility=mapping("compatibility"),
            installer=mapping("installer"),
        )

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
            "sourceKind": self.source_kind.value,
            "integrationGrade": self.integration_grade.upper(),
            "platforms": dict(self.platforms),
            "verification": dict(self.verification),
            "authentication": dict(self.authentication),
            "compatibility": dict(self.compatibility),
            "installer": dict(self.installer),
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
    source_kind: RuntimeSource = RuntimeSource.SYSTEM
    verified: bool = False

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
            "sourceKind": self.source_kind.value,
            "verified": self.verified,
        }


class DependencyResolver:
    """Resolve manifest-level dependencies without executing installers.

    Discovery is deliberately read-only.  Installing or repairing a missing
    dependency remains an explicit InstallerBroker action with its own
    approval and command preview.
    """

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

    def check_prerequisites(
        self,
        manifest: RuntimeManifest,
        *,
        available: Mapping[str, str] | None = None,
    ) -> PrerequisiteResult:
        environment = {
            key.lower(): value for key, value in (available or self.detect_environment()).items()
        }
        checks: list[PrerequisiteCheck] = []
        current_platform = platform.system().lower()
        supported = manifest.platforms.get(current_platform) if isinstance(manifest.platforms, Mapping) else None
        if supported is False:
            checks.append(PrerequisiteCheck(
                current_platform, True, False,
                f"runtime manifest does not support {current_platform}",
            ))
        elif isinstance(manifest.platforms, Mapping) and manifest.platforms:
            checks.append(PrerequisiteCheck(current_platform, True, supported is not False, "platform supported"))

        for dependency in manifest.dependencies:
            name = str(dependency.get("name") or dependency.get("executable") or "").strip()
            if not name:
                checks.append(PrerequisiteCheck("manifest dependency", True, False, "dependency name is missing"))
                continue
            key = name.lower()
            value = environment.get(key)
            minimum = str(dependency.get("minimumVersion") or "")
            available_version = str(dependency.get("version") or "") or None
            if value is None:
                checks.append(PrerequisiteCheck(name, bool(dependency.get("required", True)), False, "not found"))
                continue
            if minimum and available_version and self._version_key(available_version) < self._version_key(minimum):
                checks.append(PrerequisiteCheck(
                    name, bool(dependency.get("required", True)), False,
                    f"version {available_version} is below {minimum}", available_version,
                ))
                continue
            checks.append(PrerequisiteCheck(name, bool(dependency.get("required", True)), True, value, available_version))
        return PrerequisiteResult(
            ready=all(item.available or not item.required for item in checks),
            checks=tuple(checks),
        )

    @staticmethod
    def detect_environment() -> dict[str, str]:
        """Return executable locations available to the current host."""
        candidates = {
            "node": ("node",),
            "npm": ("npm",),
            "python": (sys.executable or "python",),
            "git": ("git",),
            "git bash": ("bash",),
            "wsl": ("wsl",),
            # Windows ships Windows PowerShell while newer installations may
            # expose PowerShell 7 as ``pwsh``.  Treat both as first-class.
            "powershell": ("pwsh", "powershell", "powershell.exe"),
            "winget": ("winget",),
        }
        available: dict[str, str] = {"platform": platform.system().lower()}
        for name, executables in candidates.items():
            for executable in executables:
                path = shutil.which(executable)
                if path:
                    available[name] = path
                    break
        return available

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...]:
        parts: list[int] = []
        for part in str(value).split("."):
            digits = "".join(char for char in part if char.isdigit())
            parts.append(int(digits or 0))
        return tuple((parts + [0, 0, 0])[:3])


@dataclass(frozen=True)
class ManifestTrust:
    trusted: bool
    allowed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"trusted": self.trusted, "allowed": self.allowed, "reason": self.reason}


class ManifestVerifier:
    """Validate manifest provenance before any installer command is exposed."""

    def __init__(
        self,
        trusted_sources: Iterable[str] = ("novelforge", "openai"),
        trusted_public_keys: Mapping[str, bytes | str] | None = None,
    ):
        self.trusted_sources = frozenset(str(item).strip().lower() for item in trusted_sources if str(item).strip())
        self.trusted_public_keys = {
            str(key).strip(): value
            for key, value in (trusted_public_keys or {}).items()
            if str(key).strip()
        }

    def verify(self, manifest: RuntimeManifest) -> ManifestTrust:
        payload = manifest.to_dict()
        signature = payload.pop("signature", None)
        trust = self.verify_payload(
            payload,
            signature,
            source=manifest.source,
            source_kind=manifest.source_kind,
        )
        if not signature and manifest.installer and not trust.trusted:
            return ManifestTrust(False, True, "untrusted installer manifest requires explicit approval")
        return trust

    def verify_payload(
        self,
        payload: Mapping[str, Any],
        signature: str | None,
        *,
        source: str = "",
        source_kind: RuntimeSource = RuntimeSource.SYSTEM,
    ) -> ManifestTrust:
        """Verify a canonical catalog or manifest payload before import."""
        source_text = str(source or "").strip().lower()
        if not isinstance(source_kind, RuntimeSource):
            try:
                source_kind = RuntimeSource(str(source_kind).strip().lower())
            except ValueError:
                source_kind = RuntimeSource.CUSTOM
        trusted = source_text in self.trusted_sources or source_kind is RuntimeSource.BUILTIN
        signature = str(signature or "").strip()
        if signature:
            if signature.lower().startswith("sha256:") or self._valid_signature_format(signature):
                expected = signature.lower().removeprefix("sha256:")
                try:
                    actual = hashlib.sha256(self.canonical_payload_for(payload)).hexdigest()
                except (TypeError, ValueError):
                    return ManifestTrust(False, False, "manifest cannot be canonicalized for verification")
                if not hmac.compare_digest(actual, expected):
                    return ManifestTrust(False, False, "manifest SHA-256 does not match its canonical payload")
                if trusted:
                    return ManifestTrust(True, True, "trusted source and manifest SHA-256 verified")
                return ManifestTrust(False, True, "manifest SHA-256 verified; source still requires explicit approval")
            if signature.lower().startswith("ed25519:"):
                return self._verify_ed25519_payload(payload, signature, trusted)
            return ManifestTrust(False, False, "manifest signature format is unsupported")
        return ManifestTrust(trusted, True, "trusted source" if trusted else "unsigned payload")

    @staticmethod
    def canonical_payload(manifest: RuntimeManifest) -> bytes:
        """Return the stable bytes covered by a manifest integrity signature."""
        payload = manifest.to_dict()
        payload.pop("signature", None)
        return ManifestVerifier.canonical_payload_for(payload)

    @staticmethod
    def canonical_payload_for(payload: Mapping[str, Any]) -> bytes:
        """Return stable UTF-8 bytes for a signed JSON-compatible payload."""
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _verify_ed25519(self, manifest: RuntimeManifest, signature: str, trusted: bool) -> ManifestTrust:
        payload = manifest.to_dict()
        payload.pop("signature", None)
        return self._verify_ed25519_payload(payload, signature, trusted)

    def _verify_ed25519_payload(
        self,
        payload: Mapping[str, Any],
        signature: str,
        trusted: bool,
    ) -> ManifestTrust:
        parts = signature.split(":", 2)
        if len(parts) != 3 or not parts[1].strip() or not parts[2].strip():
            return ManifestTrust(False, False, "ed25519 manifest signature is malformed")
        key_id, encoded_signature = parts[1].strip(), parts[2].strip()
        public_key = self.trusted_public_keys.get(key_id)
        if public_key is None:
            return ManifestTrust(False, False, f"manifest signing key is not trusted: {key_id}")
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            key_bytes = self._decode_signature_bytes(public_key, expected_length=32)
            signature_bytes = self._decode_signature_bytes(encoded_signature, expected_length=64)
            Ed25519PublicKey.from_public_bytes(key_bytes).verify(
                signature_bytes,
                self.canonical_payload_for(payload),
            )
        except ImportError:
            return ManifestTrust(False, False, "Ed25519 verification support is unavailable")
        except (TypeError, ValueError) as exc:
            return ManifestTrust(False, False, f"invalid Ed25519 manifest signature: {exc}")
        except Exception:
            return ManifestTrust(False, False, "Ed25519 manifest signature verification failed")
        return ManifestTrust(
            True,
            True,
            "trusted source and Ed25519 manifest signature verified" if trusted
            else "Ed25519 manifest signature verified",
        )

    @staticmethod
    def _decode_signature_bytes(value: bytes | str, *, expected_length: int) -> bytes:
        if isinstance(value, bytes):
            decoded = value
        else:
            text = str(value).strip()
            try:
                decoded = base64.b64decode(text, validate=True)
            except (ValueError, TypeError):
                decoded = bytes.fromhex(text)
        if len(decoded) != expected_length:
            raise ValueError(f"expected {expected_length} bytes, got {len(decoded)}")
        return decoded

    @staticmethod
    def _valid_signature_format(signature: str) -> bool:
        value = signature.lower()
        if value.startswith("sha256:"):
            value = value[7:]
        return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


class ManifestCatalog:
    """Import a signed catalog without fetching or executing anything.

    Transport is intentionally outside this class.  A caller may obtain a
    document from a future Marketplace service or a local file, but the Host
    must present the complete signed document here before any manifest enters
    the persistent Registry.  Catalog signatures require Ed25519 so a claimed
    source name cannot substitute for an authenticity check.
    """

    def __init__(self, verifier: ManifestVerifier | None = None) -> None:
        self.verifier = verifier or ManifestVerifier()

    def parse(self, document: Mapping[str, Any]) -> tuple[RuntimeManifest, ...]:
        if not isinstance(document, Mapping):
            raise ValueError("runtime catalog must be an object")
        payload = dict(document)
        signature = str(payload.pop("signature", "") or "").strip()
        source = str(payload.get("source") or "").strip()
        raw_source_kind = payload.get("sourceKind", RuntimeSource.EXTERNAL.value)
        try:
            source_kind = raw_source_kind if isinstance(raw_source_kind, RuntimeSource) else RuntimeSource(
                str(raw_source_kind).strip().lower()
            )
        except ValueError as exc:
            raise ValueError(f"catalog sourceKind is invalid: {raw_source_kind!r}") from exc
        if not signature.lower().startswith("ed25519:"):
            raise RuntimeUnavailable("runtime catalog requires an Ed25519 signature")
        trust = self.verifier.verify_payload(
            payload,
            signature,
            source=source,
            source_kind=source_kind,
        )
        if not trust.trusted or not trust.allowed:
            raise RuntimeUnavailable(f"runtime catalog was rejected: {trust.reason}")

        raw_manifests = payload.get("manifests")
        if isinstance(raw_manifests, (str, bytes)) or not isinstance(raw_manifests, Sequence):
            raise ValueError("runtime catalog manifests must be an array")
        manifests: list[RuntimeManifest] = []
        seen: set[str] = set()
        for index, raw_manifest in enumerate(raw_manifests):
            if not isinstance(raw_manifest, Mapping):
                raise ValueError(f"runtime catalog manifest {index} must be an object")
            manifest_payload = dict(raw_manifest)
            manifest_payload.setdefault("source", source)
            manifest_payload.setdefault("sourceKind", source_kind.value)
            manifest = RuntimeManifest.from_dict(manifest_payload)
            if manifest.runtime_type in seen:
                raise ValueError(f"runtime catalog contains duplicate runtime: {manifest.runtime_type}")
            seen.add(manifest.runtime_type)
            if manifest.signature:
                manifest_trust = self.verifier.verify(manifest)
                if not manifest_trust.allowed:
                    raise RuntimeUnavailable(
                        f"runtime manifest was rejected: {manifest.runtime_type}: {manifest_trust.reason}"
                    )
            manifests.append(manifest)
        if not manifests:
            raise ValueError("runtime catalog contains no manifests")
        return tuple(manifests)

    def import_into(
        self,
        registry: "RuntimeRegistry",
        document: Mapping[str, Any],
    ) -> tuple[RuntimeManifest, ...]:
        """Validate the complete catalog, then register all of its manifests."""
        manifests = self.parse(document)
        for manifest in manifests:
            registry.register_manifest(manifest)
        return manifests


class ArtifactVerifier:
    """Verify a downloaded/local artifact without executing it."""

    def verify(self, path: str | Path | None, expected_sha256: str | None = None) -> VerificationResult:
        if not path:
            return VerificationResult(False, reason="artifact path is missing")
        artifact = Path(path).expanduser()
        if not artifact.is_file():
            return VerificationResult(False, path=str(artifact), reason="artifact does not exist")
        checks = ["file-exists"]
        if expected_sha256:
            digest = hashlib.sha256()
            with artifact.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            expected = expected_sha256.lower().removeprefix("sha256:")
            if digest.hexdigest().lower() != expected:
                return VerificationResult(
                    False, path=str(artifact), checks=tuple(checks),
                    reason="artifact SHA-256 does not match manifest",
                )
            checks.append("sha256")
        return VerificationResult(True, path=str(artifact), checks=tuple(checks))


class ArtifactDownloader:
    """Download a declared binary into a verified, atomically replaced path.

    This is intentionally a byte transport primitive.  It never executes the
    result and it requires a manifest SHA-256 before replacing the target.
    Install approval is enforced by :class:`ManifestPluginInstaller`; this
    class additionally bounds the response and rejects insecure URLs.
    """

    DEFAULT_MAX_BYTES = 256 * 1024 * 1024

    def __init__(
        self,
        *,
        timeout_seconds: float = 60.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        opener: Callable[..., Any] | None = None,
        allow_loopback_http: bool = False,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.timeout_seconds = float(timeout_seconds)
        self.max_bytes = int(max_bytes)
        self.opener = opener or urlopen
        self.allow_loopback_http = bool(allow_loopback_http)

    def download(
        self,
        url: str,
        target: str | Path,
        expected_sha256: str,
    ) -> VerificationResult:
        normalized_url = str(url or "").strip()
        self._validate_url(normalized_url)
        expected = str(expected_sha256 or "").strip().lower().removeprefix("sha256:")
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise RuntimeUnavailable("downloaded binaries require a valid manifest SHA-256")
        target_text = str(target or "").strip()
        if not target_text:
            raise RuntimeUnavailable("downloaded binaries require an explicit target path")
        target_path = Path(target_text).expanduser()
        if not target_path.name or target_path.is_dir():
            raise RuntimeUnavailable("download target must be a file path")

        request = Request(
            normalized_url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": "NovelForge-RuntimeArtifact/1",
            },
            method="GET",
        )
        response: Any | None = None
        temporary_path: Path | None = None
        try:
            try:
                response = self.opener(request, timeout=self.timeout_seconds)
            except Exception as exc:
                raise RuntimeUnavailable(
                    f"runtime artifact fetch failed: {exc}",
                    details={"url": normalized_url},
                ) from exc

            final_url = getattr(response, "geturl", lambda: normalized_url)()
            self._validate_url(str(final_url or normalized_url))
            status = getattr(response, "status", None)
            if status is None:
                status = getattr(response, "code", 200)
            if isinstance(status, int) and status >= 400:
                raise RuntimeUnavailable(
                    f"runtime artifact returned HTTP {status}",
                    details={"url": normalized_url, "status": status},
                )
            headers = getattr(response, "headers", None)
            content_length = headers.get("Content-Length") if headers is not None else None
            if content_length is not None:
                try:
                    if int(content_length) > self.max_bytes:
                        raise RuntimeUnavailable("runtime artifact exceeds the maximum size")
                except ValueError as exc:
                    raise RuntimeUnavailable("runtime artifact Content-Length is invalid") from exc

            target_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target_path.name}.", suffix=".download", dir=str(target_path.parent)
            )
            temporary_path = Path(temporary_name)
            digest = hashlib.sha256()
            total = 0
            with os.fdopen(descriptor, "wb") as stream:
                while True:
                    chunk = response.read(min(1024 * 1024, self.max_bytes - total + 1))
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise RuntimeUnavailable("runtime artifact response was not bytes")
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise RuntimeUnavailable("runtime artifact exceeds the maximum size")
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())

            if not hmac.compare_digest(digest.hexdigest().lower(), expected):
                raise RuntimeUnavailable("downloaded artifact SHA-256 does not match manifest")
            os.replace(temporary_path, target_path)
            temporary_path = None
            return VerificationResult(
                True,
                path=str(target_path),
                checks=("download", "sha256", "atomic-install"),
            )
        except RuntimeUnavailable:
            raise
        except Exception as exc:
            raise RuntimeUnavailable(
                f"runtime artifact could not be installed: {exc}",
                details={"url": normalized_url, "target": str(target_path)},
            ) from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _validate_url(self, url: str) -> None:
        if self.url_allowed(url, allow_loopback_http=self.allow_loopback_http):
            return
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            raise RuntimeUnavailable("runtime artifact URL must not contain userinfo")
        if not parsed.netloc:
            raise RuntimeUnavailable("runtime artifact URL must include a host")
        raise RuntimeUnavailable("runtime artifact transport requires HTTPS")

    @staticmethod
    def url_allowed(url: str, *, allow_loopback_http: bool = False) -> bool:
        parsed = urlparse(str(url or "").strip())
        if parsed.username or parsed.password or not parsed.netloc:
            return False
        if parsed.scheme == "https":
            return True
        return bool(
            allow_loopback_http
            and parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )


class TrustedInstallationPolicy:
    """Host policy preventing silent arbitrary shell execution."""

    _PACKAGE_MANAGERS = frozenset({"npm", "npm.cmd", "pip", "pip.exe", "python", "python.exe", "uv", "winget"})
    _VERSION_ARGS = frozenset({"--version", "-V", "-v", "/version"})

    def __init__(self, verifier: ManifestVerifier | None = None):
        self.verifier = verifier or ManifestVerifier()

    def evaluate(self, manifest: RuntimeManifest, action: InstallAction) -> ManifestTrust:
        trust = self.verifier.verify(manifest)
        raw_command = manifest.installer.get(f"{action.value}Command") if isinstance(manifest.installer, Mapping) else None
        if raw_command is None and action is InstallAction.INSTALL and isinstance(manifest.installer, Mapping):
            raw_command = manifest.installer.get("command")
        if isinstance(raw_command, str):
            return ManifestTrust(False, False, "installer command must be an argv array, not a shell string")
        command = self.command_for(manifest, action)
        if not command:
            return trust
        if any(not str(part).strip() for part in command):
            return ManifestTrust(False, False, "installer command contains an empty argument")
        if bool(manifest.installer.get("shell")):
            return ManifestTrust(False, False, "shell installers are not allowed through the runtime broker")
        if manifest.acquisition is AcquisitionType.DOWNLOAD_BINARY:
            if action is not InstallAction.UNINSTALL:
                download_url, target_path, expected_hash = self.download_spec(manifest)
                if not download_url:
                    return ManifestTrust(False, False, "downloaded binaries require an installer downloadUrl")
                if not target_path:
                    return ManifestTrust(False, False, "downloaded binaries require an installer resultPath")
                if command:
                    return ManifestTrust(False, False, "downloaded binaries cannot use an installer command")
                if not ArtifactDownloader.url_allowed(download_url):
                    return ManifestTrust(False, False, "downloaded binaries require an HTTPS downloadUrl")
                normalized_hash = str(expected_hash or "").strip().lower().removeprefix("sha256:")
                if len(normalized_hash) != 64 or any(char not in "0123456789abcdef" for char in normalized_hash):
                    return ManifestTrust(False, False, "downloaded binaries require a valid manifest SHA-256")
        executable = Path(command[0]).name.lower()
        if manifest.acquisition is AcquisitionType.PACKAGE_MANAGER and executable not in self._PACKAGE_MANAGERS:
            return ManifestTrust(False, False, f"package manager is not allowlisted: {executable}")
        if not trust.allowed:
            return trust
        return trust

    @staticmethod
    def download_spec(manifest: RuntimeManifest) -> tuple[str | None, str | None, str | None]:
        installer = manifest.installer if isinstance(manifest.installer, Mapping) else {}
        download_url = installer.get("downloadUrl") or installer.get("url")
        target_path = installer.get("resultPath") or installer.get("targetPath") or installer.get("downloadPath")
        expected_hash = manifest.verification.get("sha256") if isinstance(manifest.verification, Mapping) else None
        return (
            str(download_url).strip() if download_url is not None and str(download_url).strip() else None,
            str(target_path).strip() if target_path is not None and str(target_path).strip() else None,
            str(expected_hash).strip() if expected_hash is not None and str(expected_hash).strip() else None,
        )

    @staticmethod
    def command_for(manifest: RuntimeManifest, action: InstallAction) -> tuple[str, ...]:
        if not isinstance(manifest.installer, Mapping):
            return ()
        raw = manifest.installer.get(f"{action.value}Command")
        if raw is None and action is InstallAction.INSTALL:
            raw = manifest.installer.get("command")
        if isinstance(raw, str):
            # The policy rejects strings; keep this branch empty for callers
            # that use command_for only to build a safe preview.
            return ()
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            return tuple(str(item) for item in raw)
        return ()

    @classmethod
    def version_command_allowed(
        cls,
        manifest: RuntimeManifest,
        command: Sequence[str],
        runtime_path: str | None,
    ) -> bool:
        """Keep verification probes read-only and bound to the declared executable."""
        if len(command) != 2 or any(not str(item).strip() for item in command):
            return False
        if str(command[1]) not in cls._VERSION_ARGS:
            return False
        declared = runtime_path or manifest.executable or (manifest.command[0] if manifest.command else None)
        if not declared:
            return False
        return cls._executable_key(command[0]) == cls._executable_key(declared)

    @staticmethod
    def _executable_key(value: str | Path) -> str:
        name = Path(str(value)).name.casefold()
        for suffix in (".exe", ".cmd", ".bat", ".ps1", ".com"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name


class RuntimeRegistry:
    """Persistent registry of runtime manifests and observed installations."""

    _TRANSITIONS = {
        InstallState.NOT_INSTALLED: {InstallState.INSTALLING, InstallState.INSTALLED},
        InstallState.INSTALLING: {InstallState.INSTALLED, InstallState.BROKEN, InstallState.INCOMPATIBLE,
                                  InstallState.NOT_INSTALLED},
        InstallState.INSTALLED: {InstallState.INSTALLING, InstallState.NOT_AUTHENTICATED, InstallState.AUTHENTICATED,
                                 InstallState.CAPABILITY_VERIFIED, InstallState.BROKEN, InstallState.INCOMPATIBLE,
                                 InstallState.NEEDS_UPDATE, InstallState.REPAIRING, InstallState.NOT_INSTALLED},
        InstallState.NOT_AUTHENTICATED: {InstallState.INSTALLING, InstallState.AUTHENTICATED, InstallState.BROKEN,
                                         InstallState.INCOMPATIBLE,
                                         InstallState.REPAIRING, InstallState.NOT_INSTALLED},
        InstallState.AUTHENTICATED: {InstallState.INSTALLING, InstallState.CAPABILITY_VERIFIED, InstallState.BROKEN,
                                     InstallState.INCOMPATIBLE,
                                     InstallState.NEEDS_UPDATE, InstallState.REPAIRING, InstallState.NOT_INSTALLED},
        InstallState.CAPABILITY_VERIFIED: {InstallState.INSTALLING, InstallState.READY, InstallState.BROKEN,
                                           InstallState.INCOMPATIBLE,
                                           InstallState.NEEDS_UPDATE, InstallState.REPAIRING, InstallState.NOT_INSTALLED},
        InstallState.READY: {InstallState.INSTALLING, InstallState.BROKEN, InstallState.INCOMPATIBLE,
                             InstallState.NEEDS_UPDATE, InstallState.REPAIRING,
                             InstallState.NOT_AUTHENTICATED, InstallState.AUTHENTICATED,
                             InstallState.NOT_INSTALLED},
        InstallState.BROKEN: {InstallState.REPAIRING, InstallState.INSTALLING, InstallState.NOT_INSTALLED},
        InstallState.NEEDS_UPDATE: {InstallState.INSTALLING, InstallState.REPAIRING,
                                    InstallState.BROKEN, InstallState.NOT_INSTALLED},
        InstallState.INCOMPATIBLE: {InstallState.REPAIRING, InstallState.NOT_INSTALLED},
        InstallState.REPAIRING: {InstallState.INSTALLED, InstallState.INSTALLING,
                                 InstallState.BROKEN, InstallState.INCOMPATIBLE, InstallState.NOT_INSTALLED},
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
            self._set_installation(RuntimeInstallation(
                manifest.runtime_type,
                version=manifest.version,
                source_kind=manifest.source_kind,
            ))
        else:
            current = self._installations[manifest.runtime_type]
            changes: dict[str, Any] = {}
            if current.source_kind is not manifest.source_kind:
                changes["source_kind"] = manifest.source_kind
            if current.version and current.version != manifest.version and current.state is InstallState.READY:
                # Keep the observed installed version; the manifest version is
                # the candidate version and must not erase that evidence.
                changes["state"] = InstallState.NEEDS_UPDATE
            if changes:
                self._set_installation(self._replace(current, **changes))
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
        path = self._resolve_executable(manifest)
        current = self._installations.get(runtime_type)
        if current is not None and current.state not in {
            InstallState.NOT_INSTALLED, InstallState.INSTALLING, InstallState.REPAIRING,
        } and (path or manifest.acquisition in {AcquisitionType.BUILTIN, AcquisitionType.BUNDLED}):
            # Discovery is observational.  It must not erase authentication,
            # capability, or health evidence when a user presses refresh.
            updated = self._replace(
                current,
                path=path or current.path,
                source_kind=manifest.source_kind,
            )
            self._set_installation(updated)
            return updated
        if current is not None and current.state in {
            InstallState.INSTALLED,
            InstallState.NOT_AUTHENTICATED,
            InstallState.AUTHENTICATED,
            InstallState.CAPABILITY_VERIFIED,
            InstallState.READY,
        } and not path and manifest.acquisition not in {AcquisitionType.BUILTIN, AcquisitionType.BUNDLED}:
            # A previously connected executable disappearing is a broken
            # installation, not a clean "not installed" state.  Preserve the
            # last path and evidence so the Marketplace can explain Repair.
            return self._transition(
                current,
                InstallState.BROKEN,
                capability_verified=False,
                health="unhealthy",
                last_error="runtime executable was not found during discovery",
                verified=False,
            )
        if manifest.acquisition is AcquisitionType.BUILTIN:
            installation = RuntimeInstallation(
                runtime_type, InstallState.INSTALLED, path=manifest.executable,
                version=manifest.version, health="unknown", source_kind=manifest.source_kind,
            )
        elif path:
            installation = RuntimeInstallation(
                runtime_type, InstallState.INSTALLED, path=path,
                version=manifest.version, health="unknown", source_kind=manifest.source_kind,
            )
        else:
            installation = RuntimeInstallation(
                runtime_type, InstallState.NOT_INSTALLED,
                version=manifest.version, source_kind=manifest.source_kind,
            )
        self._set_installation(installation)
        return installation

    @staticmethod
    def _resolve_executable(manifest: RuntimeManifest) -> str | None:
        candidate = manifest.executable or (manifest.command[0] if manifest.command else None)
        if not candidate:
            return None
        explicit = Path(candidate).expanduser()
        if explicit.is_file():
            return str(explicit)
        return shutil.which(candidate)

    def mark_authenticated(self, runtime_type: str, auth: AuthState) -> RuntimeInstallation:
        current = self._require_installation(runtime_type)
        if not current.verified:
            raise RuntimeUnavailable(f"runtime artifact is not verified: {runtime_type}")
        state = InstallState.AUTHENTICATED if auth.status in {"authenticated", "ready"} else InstallState.NOT_AUTHENTICATED
        return self._transition(current, state, auth=auth)

    def mark_capability_verified(self, runtime_type: str, capabilities: RuntimeCapabilities) -> RuntimeInstallation:
        current = self._require_installation(runtime_type)
        if not current.verified:
            raise RuntimeUnavailable(f"runtime artifact is not verified: {runtime_type}")
        if current.state not in {InstallState.AUTHENTICATED, InstallState.INSTALLED, InstallState.CAPABILITY_VERIFIED}:
            if current.state is InstallState.NOT_INSTALLED:
                raise RuntimeNotInstalled(f"runtime is not installed: {runtime_type}")
            raise RuntimeUnavailable(f"runtime is not installed/authenticated: {runtime_type}")
        return self._transition(current, InstallState.CAPABILITY_VERIFIED,
                                capability_verified=True, health="healthy")

    def mark_verified(self, runtime_type: str, result: VerificationResult) -> RuntimeInstallation:
        current = self._require_installation(runtime_type)
        if not result.verified:
            updated = self._replace(current, verified=False, last_error=result.reason or "runtime verification failed")
            self._set_installation(updated)
            return updated
        updated = self._replace(
            current,
            path=result.path or current.path,
            version=result.version or current.version,
            verified=True,
            last_error=None,
        )
        # Verification is a durable lifecycle fact, not just a read-model
        # decoration.  Persist it before authentication/capability refreshes
        # reload the current installation and overwrite the in-memory value.
        self._set_installation(updated)
        return updated

    def mark_incompatible(self, runtime_type: str, reason: str) -> RuntimeInstallation:
        current = self._require_installation(runtime_type)
        return self._transition(
            current,
            InstallState.INCOMPATIBLE,
            last_error=reason or "runtime version is incompatible",
        )

    def compatibility(self, runtime_type: str, observed_version: str | None = None) -> CompatibilityResult:
        manifest = self._require_manifest(runtime_type)
        compatibility = manifest.compatibility if isinstance(manifest.compatibility, Mapping) else {}
        minimum = str(compatibility.get("minimumVersion") or "") or None
        maximum = str(compatibility.get("maximumTestedVersion") or "") or None
        observed = observed_version or (self._installations.get(runtime_type) or RuntimeInstallation(runtime_type)).version
        if observed and minimum and DependencyResolver._version_key(observed) < DependencyResolver._version_key(minimum):
            return CompatibilityResult(False, reason=f"version {observed} is below minimum {minimum}",
                                       observed_version=observed, minimum_version=minimum,
                                       maximum_tested_version=maximum)
        if observed and maximum and DependencyResolver._version_key(observed) > DependencyResolver._version_key(maximum):
            return CompatibilityResult(True, warning=f"version {observed} is newer than maximum tested {maximum}",
                                       observed_version=observed, minimum_version=minimum,
                                       maximum_tested_version=maximum)
        tested = compatibility.get("testedVersions") if isinstance(compatibility, Mapping) else None
        if observed and isinstance(tested, Sequence) and not isinstance(tested, (str, bytes)) and str(observed) not in {
            str(item) for item in tested
        }:
            return CompatibilityResult(True, warning=f"version {observed} is not listed as tested",
                                       observed_version=observed, minimum_version=minimum,
                                       maximum_tested_version=maximum)
        return CompatibilityResult(True, observed_version=observed, minimum_version=minimum,
                                   maximum_tested_version=maximum)

    def mark_health(self, runtime_type: str, *, healthy: bool, detail: str = "") -> RuntimeInstallation:
        current = self._require_installation(runtime_type)
        if healthy and current.capability_verified and current.verified:
            return self._transition(current, InstallState.READY, health="healthy", last_error=None)
        if healthy:
            updated = self._replace(current, health="healthy", last_error=None)
            self._set_installation(updated)
            return updated
        return self._transition(current, InstallState.BROKEN, health="unhealthy", last_error=detail or "health check failed")

    def require_ready(self, runtime_type: str) -> RuntimeManifest:
        manifest = self._require_manifest(runtime_type)
        installation = self._require_installation(runtime_type)
        if installation.state is not InstallState.READY:
            if installation.state is InstallState.NOT_INSTALLED:
                raise RuntimeNotInstalled(f"runtime is not installed: {runtime_type}")
            if installation.state is InstallState.INCOMPATIBLE:
                raise RuntimeIncompatible(f"runtime is incompatible: {runtime_type}")
            if installation.state is InstallState.NOT_AUTHENTICATED:
                raise AuthenticationRequired(f"runtime is not authenticated: {runtime_type}")
            raise RuntimeUnavailable(f"runtime is not ready: {runtime_type} ({installation.state.value})")
        if not installation.verified:
            raise RuntimeUnavailable(f"runtime artifact is not verified: {runtime_type}")
        if installation.auth.status not in {"authenticated", "ready"}:
            raise AuthenticationRequired(f"runtime is not authenticated: {runtime_type}")
        if not installation.capability_verified:
            raise RuntimeUnavailable(f"runtime capabilities are not verified: {runtime_type}")
        return manifest

    def set_error(self, runtime_type: str, detail: str) -> RuntimeInstallation:
        current = self._require_installation(runtime_type)
        return self._transition(current, InstallState.BROKEN, health="unhealthy", last_error=detail)

    def record_install_event(self, event: InstallEvent) -> None:
        if self.db is None:
            return
        self.db.execute(
            """INSERT INTO runtime_install_events(
                   runtime_type, action, phase, status, message, detail, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event.runtime_type, event.action.value, event.phase, event.status,
                event.message, json.dumps(dict(event.detail), ensure_ascii=False), event.created_at,
            ),
        )

    def install_events(self, runtime_type: str, *, limit: int = 50) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        rows = self.db.fetchall(
            """SELECT runtime_type, action, phase, status, message, detail, created_at
               FROM runtime_install_events WHERE runtime_type=?
               ORDER BY id DESC LIMIT ?""",
            (runtime_type, max(1, min(int(limit), 200))),
        )
        result: list[dict[str, Any]] = []
        for row in reversed(rows):
            detail = row.get("detail")
            try:
                detail = json.loads(detail or "{}")
            except (TypeError, json.JSONDecodeError):
                detail = {"value": detail}
            result.append({
                "runtimeType": row.get("runtime_type"), "action": row.get("action"),
                "phase": row.get("phase"), "status": row.get("status"),
                "message": row.get("message"), "detail": detail,
                "createdAt": row.get("created_at"),
            })
        return result

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
                   auth_detail, capability_verified, health, last_error, updated_at,
                   source_kind, verified
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(runtime_type) DO UPDATE SET state=excluded.state,
                   path=excluded.path, version=excluded.version,
                   auth_status=excluded.auth_status, account_label=excluded.account_label,
                   auth_detail=excluded.auth_detail, capability_verified=excluded.capability_verified,
                   health=excluded.health, last_error=excluded.last_error, updated_at=excluded.updated_at,
                   source_kind=excluded.source_kind, verified=excluded.verified""",
            (
                installation.runtime_type, installation.state.value, installation.path,
                installation.version, installation.auth.status, installation.auth.account_label,
                installation.auth.detail, int(installation.capability_verified), installation.health,
                installation.last_error, installation.updated_at, installation.source_kind.value,
                int(installation.verified),
            ),
        )

    def _load(self) -> None:
        db = self.db
        if db is None:
            return
        try:
            rows = db.fetchall("SELECT * FROM runtime_registry ORDER BY runtime_type")
            for row in rows:
                metadata = json.loads(row.get("metadata") or "{}")
                if not isinstance(metadata, Mapping):
                    metadata = {}
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
                    source_kind=RuntimeSource(metadata.get("sourceKind") or RuntimeSource.SYSTEM.value),
                    integration_grade=str(metadata.get("integrationGrade") or "C"),
                    platforms=metadata.get("platforms") or {},
                    verification=metadata.get("verification") or {},
                    authentication=metadata.get("authentication") or {},
                    compatibility=metadata.get("compatibility") or {},
                    installer=metadata.get("installer") or {},
                )
            rows = db.fetchall("SELECT * FROM runtime_installations")
            for row in rows:
                manifest = self._manifests.get(row["runtime_type"])
                self._installations[row["runtime_type"]] = RuntimeInstallation(
                    runtime_type=row["runtime_type"], state=InstallState(row["state"]),
                    path=row.get("path"), version=row.get("version"),
                    auth=AuthState(row.get("auth_status") or "unknown", row.get("account_label"), row.get("auth_detail") or ""),
                    capability_verified=bool(row.get("capability_verified")), health=row.get("health") or "unknown",
                    last_error=row.get("last_error"), updated_at=row.get("updated_at") or _now(),
                    source_kind=RuntimeSource(row.get("source_kind") or (
                        manifest.source_kind.value if manifest else RuntimeSource.SYSTEM.value
                    )),
                    verified=bool(row.get("verified")),
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
                   source, signature, minimum_host_version, metadata, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(runtime_type) DO UPDATE SET display_name=excluded.display_name,
                   version=excluded.version, protocol=excluded.protocol,
                   acquisition=excluded.acquisition, executable=excluded.executable,
                   command=excluded.command, capabilities=excluded.capabilities,
                   models=excluded.models, dependencies=excluded.dependencies,
                   source=excluded.source, signature=excluded.signature,
                   minimum_host_version=excluded.minimum_host_version, metadata=excluded.metadata,
                   updated_at=excluded.updated_at""",
            (
                manifest.runtime_type, manifest.display_name, manifest.version, manifest.protocol,
                manifest.acquisition.value, manifest.executable, json.dumps(manifest.command),
                json.dumps(manifest.capabilities), json.dumps(manifest.models), json.dumps(manifest.dependencies),
                manifest.source, manifest.signature, manifest.minimum_host_version,
                json.dumps({
                    "sourceKind": manifest.source_kind.value,
                    "integrationGrade": manifest.integration_grade.upper(),
                    "platforms": dict(manifest.platforms),
                    "verification": dict(manifest.verification),
                    "authentication": dict(manifest.authentication),
                    "compatibility": dict(manifest.compatibility),
                    "installer": dict(manifest.installer),
                }, ensure_ascii=False),
                _now(),
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
            "source_kind": current.source_kind, "verified": current.verified,
        }
        values.update(changes)
        return RuntimeInstallation(**values)


class ManifestPluginInstaller:
    """Manifest-backed installer used by every Runtime Plane adapter.

    The default runner invokes argv arrays with ``shell=False``.  A manifest
    can therefore describe a package-manager or vendor installer without
    turning Marketplace metadata into an implicit shell script.  A custom
    executor remains injectable for a trusted host integration and is never
    selected implicitly by a community manifest.
    """

    def __init__(
        self,
        registry: RuntimeRegistry,
        runtime_type: str,
        *,
        dependency_resolver: DependencyResolver | None = None,
        policy: TrustedInstallationPolicy | None = None,
        artifact_verifier: ArtifactVerifier | None = None,
        artifact_downloader: ArtifactDownloader | None = None,
        executor: Callable[[RuntimeManifest], str] | None = None,
        runner: Callable[[Sequence[str]], Any] | None = None,
    ) -> None:
        self.registry = registry
        self.runtime_type = runtime_type
        self.dependency_resolver = dependency_resolver or DependencyResolver()
        self.policy = policy or TrustedInstallationPolicy()
        self.artifact_verifier = artifact_verifier or ArtifactVerifier()
        self.artifact_downloader = artifact_downloader or ArtifactDownloader()
        self.executor = executor
        self.runner = runner or self._run_process

    def detect(self) -> RuntimeInstallation:
        self._emit(InstallAction.DISCOVER, "discovery", "started", "Checking for an existing runtime")
        installation = self.registry.discover(self.runtime_type)
        self._emit(
            InstallAction.DISCOVER, "discovery", "completed",
            "Existing runtime detected" if installation.state is not InstallState.NOT_INSTALLED else "Runtime not found",
            {"state": installation.state.value, "path": installation.path},
        )
        return installation

    def check_prerequisites(self) -> PrerequisiteResult:
        manifest = self.registry._require_manifest(self.runtime_type)
        return self.dependency_resolver.check_prerequisites(manifest)

    def plan(self, action: InstallAction) -> InstallerPlan:
        manifest = self.registry._require_manifest(self.runtime_type)
        trust = self.policy.evaluate(manifest, action)
        command = self.policy.command_for(manifest, action)
        artifact_url, artifact_path, artifact_sha256 = self.policy.download_spec(manifest)
        write_action = action in {InstallAction.INSTALL, InstallAction.UPDATE, InstallAction.REPAIR, InstallAction.UNINSTALL}
        # Every mutating lifecycle action is an explicit user decision, even
        # when it only connects an existing system executable.  Discovery is
        # the read-only path for a no-confirmation probe.
        requires_approval = write_action
        risk = "high" if command or artifact_url else ("medium" if write_action else "low")
        explanation = trust.reason or (
            "connect the detected executable" if action is InstallAction.INSTALL and not command
            else "download and verify the declared binary" if artifact_url and action is not InstallAction.UNINSTALL
            else "no installer command declared"
        )
        return InstallerPlan(
            runtime_type=self.runtime_type,
            action=action,
            source=manifest.source_kind,
            acquisition=manifest.acquisition,
            command=command,
            requires_approval=requires_approval,
            trusted=trust.trusted,
            allowed=trust.allowed,
            shell=bool(manifest.installer.get("shell")) if isinstance(manifest.installer, Mapping) else False,
            risk=risk,
            explanation=explanation,
            artifact_url=artifact_url,
            artifact_path=artifact_path,
            artifact_sha256=artifact_sha256,
        )

    def verify(self) -> VerificationResult:
        manifest = self.registry._require_manifest(self.runtime_type)
        current = self.registry._require_installation(self.runtime_type)
        if manifest.acquisition in {AcquisitionType.BUILTIN, AcquisitionType.BUNDLED} and not current.path:
            return VerificationResult(True, version=manifest.version, checks=("builtin-manifest",))
        expected = manifest.verification.get("sha256") if isinstance(manifest.verification, Mapping) else None
        path = current.path or RuntimeRegistry._resolve_executable(manifest)
        artifact = self.artifact_verifier.verify(path, str(expected) if expected else None)
        if not artifact.verified:
            return artifact
        checks = list(artifact.checks)
        version = current.version or manifest.version
        version_command = manifest.verification.get("versionCommand") if isinstance(manifest.verification, Mapping) else None
        if version_command is not None:
            if isinstance(version_command, str):
                return VerificationResult(False, path=artifact.path, checks=tuple(checks),
                                          reason="version command must be an argv array")
            if not isinstance(version_command, Sequence) or isinstance(version_command, (bytes, str)):
                return VerificationResult(False, path=artifact.path, checks=tuple(checks),
                                          reason="version command is invalid")
            command = tuple(str(item) for item in version_command)
            if not command or any(not item.strip() for item in command):
                return VerificationResult(False, path=artifact.path, checks=tuple(checks),
                                          reason="version command is empty")
            if not self.policy.version_command_allowed(manifest, command, artifact.path):
                return VerificationResult(
                    False,
                    path=artifact.path,
                    checks=tuple(checks),
                    reason="version command must call the declared runtime with a read-only version argument",
                )
            # Bind the read-only probe to the path that was actually verified;
            # re-resolving a bare command through PATH could otherwise probe a
            # different executable than the artifact whose hash was checked.
            verified_path = artifact.path
            if verified_path is None:
                return VerificationResult(False, checks=tuple(checks),
                                          reason="verified artifact has no executable path")
            result = self.runner((verified_path, *command[1:]))
            return_code = getattr(result, "returncode", None)
            if return_code is None and isinstance(result, tuple):
                return_code = result[0]
            if return_code not in (0, None):
                return VerificationResult(False, path=artifact.path, checks=tuple(checks),
                                          reason=f"version command failed with exit code {return_code}")
            output = ""
            if hasattr(result, "stdout"):
                output += str(getattr(result, "stdout") or "")
            if hasattr(result, "stderr"):
                output += " " + str(getattr(result, "stderr") or "")
            match = re.search(r"\b\d+(?:\.\d+){1,3}\b", output)
            if match:
                version = match.group(0)
            checks.append("version-command")
        return VerificationResult(True, path=artifact.path, version=version, checks=tuple(checks))

    def install(self, *, approved: bool = False) -> RuntimeInstallation:
        return self._execute(InstallAction.INSTALL, approved=approved)

    def update(self, *, approved: bool = False) -> RuntimeInstallation:
        return self._execute(InstallAction.UPDATE, approved=approved)

    def repair(self, *, approved: bool = False) -> RuntimeInstallation:
        return self._execute(InstallAction.REPAIR, approved=approved)

    def uninstall(self, *, approved: bool = False) -> RuntimeInstallation:
        return self._execute(InstallAction.UNINSTALL, approved=approved)

    def _execute(self, action: InstallAction, *, approved: bool) -> RuntimeInstallation:
        manifest = self.registry._require_manifest(self.runtime_type)
        current = self.registry._require_installation(self.runtime_type)
        plan = self.plan(action)
        if not plan.allowed:
            raise RuntimeUnavailable(plan.explanation or "runtime installer plan is not allowed")
        if not plan.trusted and plan.command:
            # Untrusted declarative manifests may still be reviewed, but they
            # cannot smuggle a command past the host trust policy.
            if not approved:
                raise RuntimeUnavailable(f"installer trust review required: {plan.explanation}")
        if plan.shell:
            raise RuntimeUnavailable("shell installers are not supported by the runtime broker")
        if plan.requires_approval and not approved:
            raise RuntimeUnavailable(f"explicit approval required for runtime {action.value}")

        if action in {InstallAction.UPDATE, InstallAction.REPAIR} and current.state is InstallState.NOT_INSTALLED:
            raise RuntimeNotInstalled(f"runtime is not installed: {self.runtime_type}")

        if action in {InstallAction.INSTALL, InstallAction.UPDATE, InstallAction.REPAIR}:
            prerequisites = self.check_prerequisites()
            if not prerequisites.ready:
                missing = [item.name for item in prerequisites.checks if item.required and not item.available]
                detail = ", ".join(missing) or "runtime prerequisites are not satisfied"
                self._emit(action, "prerequisites", "failed", detail, prerequisites.to_dict())
                raise RuntimeUnavailable(detail)
            target_state = InstallState.REPAIRING if action is InstallAction.REPAIR else InstallState.INSTALLING
            if current.state is not target_state:
                current = self.registry._transition(current, target_state)
            self._emit(action, "installation", "started", f"{action.value} started", {"plan": plan.to_dict()})
            try:
                path = self._run_install_command(manifest, plan, action=action, current=current)
                if path:
                    current = self.registry._replace(current, path=path)
                    self.registry._set_installation(current)
                elif not plan.command:
                    current = self.registry.discover(self.runtime_type)
                if current.state is InstallState.NOT_INSTALLED:
                    raise RuntimeNotInstalled(f"runtime executable not found: {self.runtime_type}")
                verification = self.verify()
                self._emit(action, "verification", "completed" if verification.verified else "failed",
                           "Runtime verification completed" if verification.verified else "Runtime verification failed",
                           verification.to_dict())
                if not verification.verified:
                    self.registry._transition(
                        self.registry._require_installation(self.runtime_type), InstallState.BROKEN,
                        health="unhealthy", last_error=verification.reason or "runtime verification failed",
                    )
                    raise RuntimeUnavailable(verification.reason or "runtime verification failed")
                compatibility = self.registry.compatibility(self.runtime_type, verification.version)
                if not compatibility.compatible:
                    self.registry.mark_incompatible(
                        self.runtime_type,
                        compatibility.reason or "runtime version is incompatible",
                    )
                    raise RuntimeIncompatible(compatibility.reason or "runtime version is incompatible")
                current = self.registry.mark_verified(self.runtime_type, verification)
                current = self.registry._transition(
                    current, InstallState.INSTALLED,
                    path=verification.path or current.path,
                    version=verification.version or current.version or manifest.version,
                    health="unknown", last_error=None,
                )
                self._emit(action, "installation", "completed", f"{action.value} completed",
                           {"verification": verification.to_dict(), "compatibility": compatibility.to_dict()})
                return current
            except Exception as exc:
                if not isinstance(exc, RuntimeUnavailable):
                    detail = str(exc)
                else:
                    detail = str(exc)
                current = self.registry._require_installation(self.runtime_type)
                if current.state in {InstallState.INSTALLING, InstallState.REPAIRING}:
                    current = self.registry.set_error(self.runtime_type, detail)
                self._emit(action, "installation", "failed", detail, {"errorType": type(exc).__name__})
                raise

        if action is InstallAction.UNINSTALL:
            if manifest.acquisition in {AcquisitionType.BUILTIN, AcquisitionType.BUNDLED}:
                raise RuntimeUnavailable(f"built-in runtime cannot be uninstalled: {self.runtime_type}")
            self._emit(action, "uninstallation", "started", "Uninstall started", {"plan": plan.to_dict()})
            try:
                self._run_install_command(manifest, plan, action=action, current=current)
                result = self.registry._transition(
                    current, InstallState.NOT_INSTALLED, path=None, capability_verified=False,
                    health="unknown", last_error=None, verified=False,
                )
                self._emit(action, "uninstallation", "completed", "Runtime uninstalled")
                return result
            except Exception as exc:
                if current.state is not InstallState.BROKEN:
                    self.registry.set_error(self.runtime_type, str(exc))
                self._emit(action, "uninstallation", "failed", str(exc), {"errorType": type(exc).__name__})
                raise
        raise ValueError(f"unsupported installer action: {action.value}")

    def _run_install_command(
        self,
        manifest: RuntimeManifest,
        plan: InstallerPlan,
        *,
        action: InstallAction,
        current: RuntimeInstallation | None = None,
    ) -> str | None:
        if manifest.acquisition is AcquisitionType.DOWNLOAD_BINARY:
            download_url, target_path, expected_hash = self.policy.download_spec(manifest)
            if action is InstallAction.UNINSTALL and not plan.command:
                if not current or not current.path:
                    return None
                if not target_path or self._path_key(current.path) != self._path_key(target_path):
                    raise RuntimeUnavailable(
                        "refusing to remove a downloaded artifact outside its declared resultPath"
                    )
                Path(current.path).unlink(missing_ok=True)
                return None
            if not download_url or not target_path or not expected_hash:
                raise RuntimeUnavailable("downloaded binary installer metadata is incomplete")
            self._emit(
                action,
                "download",
                "started",
                "Downloading declared runtime artifact",
                {"url": download_url, "target": target_path, "sha256": expected_hash},
            )
            result = self.artifact_downloader.download(download_url, target_path, expected_hash)
            self._emit(action, "download", "completed", "Runtime artifact downloaded and verified", result.to_dict())
            return result.path
        if self.executor is not None:
            return self.executor(manifest)
        if not plan.command:
            return None
        result = self.runner(plan.command)
        return_code = getattr(result, "returncode", None)
        if return_code is None and isinstance(result, tuple):
            return_code = result[0]
        if return_code not in (0, None):
            raise RuntimeUnavailable(f"installer command failed with exit code {return_code}")
        configured_path = manifest.installer.get("resultPath") if isinstance(manifest.installer, Mapping) else None
        return str(configured_path) if configured_path else None

    @staticmethod
    def _path_key(value: str | Path) -> str:
        return os.path.normcase(os.path.abspath(str(Path(value).expanduser())))

    @staticmethod
    def _run_process(command: Sequence[str]) -> Any:
        return subprocess.run(
            list(command), shell=False, capture_output=True, text=True,
            timeout=300, check=False,
        )

    def _emit(
        self,
        action: InstallAction,
        phase: str,
        status: str,
        message: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        self.registry.record_install_event(InstallEvent(
            self.runtime_type, action, phase, status, message, detail or {},
        ))


class CodexInstaller(ManifestPluginInstaller):
    """Codex App Server installer/diagnostic adapter."""


class ClaudeCodeInstaller(ManifestPluginInstaller):
    """Claude Code installer/diagnostic adapter."""


class GeminiInstaller(ManifestPluginInstaller):
    """Gemini CLI installer/diagnostic adapter."""


class LocalRuntimeInstaller(ManifestPluginInstaller):
    """Local model runtime installer/diagnostic adapter."""


class InstallerBroker:
    """Lifecycle facade with explicit plans, trust checks, and audit events."""

    _ADAPTERS: Mapping[str, type[ManifestPluginInstaller]] = {
        "codex-app-server": CodexInstaller,
        "claude-code": ClaudeCodeInstaller,
        "gemini-cli": GeminiInstaller,
        "local-runtime": LocalRuntimeInstaller,
    }

    def __init__(
        self,
        registry: RuntimeRegistry,
        *,
        executor: Callable[[RuntimeManifest], str] | None = None,
        runner: Callable[[Sequence[str]], Any] | None = None,
        dependency_resolver: DependencyResolver | None = None,
        policy: TrustedInstallationPolicy | None = None,
        artifact_downloader: ArtifactDownloader | None = None,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.runner = runner
        self.dependency_resolver = dependency_resolver or DependencyResolver()
        self.policy = policy or TrustedInstallationPolicy()
        self.artifact_downloader = artifact_downloader or ArtifactDownloader()
        self._installers: dict[str, ManifestPluginInstaller] = {}

    def installer(self, runtime_type: str) -> ManifestPluginInstaller:
        if runtime_type not in self._installers:
            installer_type = self._ADAPTERS.get(runtime_type, ManifestPluginInstaller)
            self._installers[runtime_type] = installer_type(
                self.registry, runtime_type,
                dependency_resolver=self.dependency_resolver,
                policy=self.policy,
                artifact_downloader=self.artifact_downloader,
                executor=self.executor,
                runner=self.runner,
            )
        return self._installers[runtime_type]

    def discover(self, runtime_type: str) -> RuntimeInstallation:
        return self.installer(runtime_type).detect()

    def plan(self, runtime_type: str, action: InstallAction = InstallAction.INSTALL) -> InstallerPlan:
        return self.installer(runtime_type).plan(action)

    def diagnostics(self, runtime_type: str) -> dict[str, Any]:
        installer = self.installer(runtime_type)
        installation = self.registry._require_installation(runtime_type)
        return {
            "manifest": self.registry._require_manifest(runtime_type).to_dict(),
            "installation": installation.to_dict(),
            "prerequisites": installer.check_prerequisites().to_dict(),
            "compatibility": self.registry.compatibility(runtime_type).to_dict(),
            "plans": {
                action.value: installer.plan(action).to_dict()
                for action in InstallAction
                if action is not InstallAction.DISCOVER
            },
            "events": self.registry.install_events(runtime_type),
            "trust": self.policy.verifier.verify(self.registry._require_manifest(runtime_type)).to_dict(),
        }

    def install(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        return self.installer(runtime_type).install(approved=approved)

    def update(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        return self.installer(runtime_type).update(approved=approved)

    def repair(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        return self.installer(runtime_type).repair(approved=approved)

    def uninstall(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        return self.installer(runtime_type).uninstall(approved=approved)


class RuntimeManager:
    """Host-owned Runtime Plane facade over Registry and InstallerBroker.

    The manager is the stable lifecycle entrypoint for Studio integrations.
    It keeps manifest/discovery/installation concerns together without making
    vendor adapters or Marketplace payloads responsible for Registry writes.
    Authentication and capability probes remain adapter-owned and are exposed
    separately through the runtime contract.
    """

    def __init__(self, registry: RuntimeRegistry, broker: InstallerBroker | None = None) -> None:
        self.registry = registry
        self.broker = broker or InstallerBroker(registry)

    def list(self) -> list[dict[str, Any]]:
        return self.registry.list()

    def manifest(self, runtime_type: str) -> RuntimeManifest:
        return self.registry._require_manifest(runtime_type)

    def installation(self, runtime_type: str) -> RuntimeInstallation:
        return self.registry._require_installation(runtime_type)

    def installer(self, runtime_type: str) -> ManifestPluginInstaller:
        return self.broker.installer(runtime_type)

    def discover(self, runtime_type: str) -> RuntimeInstallation:
        return self.broker.discover(runtime_type)

    def plan(self, runtime_type: str, action: InstallAction = InstallAction.INSTALL) -> InstallerPlan:
        return self.broker.plan(runtime_type, action)

    def diagnostics(self, runtime_type: str) -> dict[str, Any]:
        return self.broker.diagnostics(runtime_type)

    def install(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        return self.broker.install(runtime_type, approved=approved)

    def update(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        return self.broker.update(runtime_type, approved=approved)

    def repair(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        return self.broker.repair(runtime_type, approved=approved)

    def uninstall(self, runtime_type: str, *, approved: bool = False) -> RuntimeInstallation:
        return self.broker.uninstall(runtime_type, approved=approved)
