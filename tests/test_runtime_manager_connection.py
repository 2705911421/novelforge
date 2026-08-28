from __future__ import annotations

import asyncio
import hashlib
import sys
from types import SimpleNamespace

import pytest

from src.core.database import Database
from src.runtime.contracts import AuthState, RuntimeCapabilities
from src.runtime.registry import (
    AcquisitionType,
    ArtifactDownloader,
    DependencyResolver,
    InstallState,
    InstallAction,
    InstallerBroker,
    RuntimeManager,
    RuntimeManifest,
    RuntimeRegistry,
    RuntimeSource,
    TrustedInstallationPolicy,
    _ValidatedRedirectHandler,
)
from src.runtime.errors import RuntimeUnavailable
from src.runtime.catalog import RuntimeCatalogClient


class _ProbeRuntime:
    def __init__(self, auth: AuthState):
        self.auth = auth
        self.initialized = 0
        self.authenticated = 0

    async def initialize(self):
        self.initialized += 1
        return RuntimeCapabilities(
            runtime_type="probe-runtime",
            models=(),
            integration_grade="C",
        )

    async def authenticate(self):
        self.authenticated += 1
        return self.auth

    async def get_capabilities(self):
        return await self.initialize()


def _registry(tmp_path):
    registry = RuntimeRegistry(Database(str(tmp_path / "runtime-manager.sqlite3")))
    registry.register_manifest(RuntimeManifest(
        runtime_type="probe-runtime",
        display_name="Probe Runtime",
        version="1",
        protocol="structured-cli",
        acquisition=AcquisitionType.EXTERNAL,
        executable=sys.executable,
        source="test",
        source_kind=RuntimeSource.CUSTOM,
        compatibility={"minimumVersion": "1", "maximumTestedVersion": "9"},
    ))
    discovered = registry.discover("probe-runtime")
    registry._set_installation(registry._replace(
        discovered,
        state=InstallState.INSTALLED,
        path=sys.executable,
    ))
    return registry


def test_runtime_manager_reconnect_runs_official_probes_and_reaches_ready(tmp_path):
    registry = _registry(tmp_path)
    runtime = _ProbeRuntime(AuthState("authenticated", account_label="test-account"))
    manager = RuntimeManager(registry, runtime_adapters={"probe-runtime": runtime})

    result = asyncio.run(manager.reconnect("probe-runtime"))

    assert result["action"] == "reconnect"
    assert result["ready"] is True
    assert result["installation"]["state"] == "ready"
    assert result["auth"]["accountLabel"] == "test-account"
    assert result["capabilities"]["runtimeType"] == "probe-runtime"
    assert runtime.initialized == 1
    assert runtime.authenticated == 1


def test_runtime_manager_reconnect_preserves_adapter_failure_reason(tmp_path):
    registry = _registry(tmp_path)

    class _FailingRuntime(_ProbeRuntime):
        async def initialize(self):
            raise RuntimeUnavailable("vendor capability probe unavailable")

    manager = RuntimeManager(
        registry,
        runtime_adapters={
            "probe-runtime": _FailingRuntime(
                AuthState("authenticated", account_label="failure-account")
            )
        },
    )

    with pytest.raises(RuntimeUnavailable, match="vendor capability probe unavailable"):
        asyncio.run(manager.reconnect("probe-runtime"))

    installation = registry.get_installation("probe-runtime")
    assert installation is not None
    assert installation.state is InstallState.BROKEN
    assert installation.last_error == "vendor capability probe unavailable"


@pytest.mark.parametrize("failure_state", [InstallState.BROKEN, InstallState.INCOMPATIBLE])
def test_runtime_manager_reconnect_can_clear_recoverable_failure_state(tmp_path, failure_state):
    registry = _registry(tmp_path)
    if failure_state is InstallState.BROKEN:
        registry.set_error("probe-runtime", "temporary probe failure")
    else:
        registry.mark_incompatible("probe-runtime", "old observed version")
    runtime = _ProbeRuntime(AuthState("authenticated", account_label="recovered-account"))
    manager = RuntimeManager(registry, runtime_adapters={"probe-runtime": runtime})

    result = asyncio.run(manager.reconnect("probe-runtime"))

    assert result["ready"] is True
    assert result["installation"]["state"] == "ready"
    assert registry.get_installation("probe-runtime").state is InstallState.READY


def test_runtime_manager_reauthenticate_preserves_not_authenticated_truth(tmp_path):
    registry = _registry(tmp_path)
    runtime = _ProbeRuntime(AuthState("not_authenticated", detail="official login required"))
    manager = RuntimeManager(registry, runtime_adapters={"probe-runtime": runtime})

    result = asyncio.run(manager.reauthenticate("probe-runtime"))

    assert result["action"] == "reauthenticate"
    assert result["ready"] is False
    assert result["installation"]["state"] == "not_authenticated"
    assert result["auth"]["detail"] == "official login required"
    assert result["capabilities"] is None


def test_discovery_preserves_a_connected_custom_executable_path(tmp_path):
    registry = RuntimeRegistry(Database(str(tmp_path / "custom-path.sqlite3")))
    registry.register_manifest(RuntimeManifest(
        runtime_type="custom-path-runtime",
        display_name="Custom Path Runtime",
        version="1",
        protocol="structured-cli",
        acquisition=AcquisitionType.EXTERNAL,
        executable="executable-not-on-path",
        source="test",
        source_kind=RuntimeSource.CUSTOM,
    ))
    initial = registry.discover("custom-path-runtime")
    registry._set_installation(registry._replace(
        initial,
        state=InstallState.INSTALLED,
        path=sys.executable,
    ))

    rediscovered = registry.discover("custom-path-runtime")

    assert rediscovered.state is InstallState.INSTALLED
    assert rediscovered.path == sys.executable


def test_dependency_resolver_probes_declared_minimum_versions_with_argv_only(tmp_path):
    calls = []

    def run(command):
        calls.append(tuple(command))
        return SimpleNamespace(returncode=0, stdout="node v20.11.1", stderr="")

    resolver = DependencyResolver(runner=run)
    manifest = RuntimeManifest(
        runtime_type="dependency-runtime",
        display_name="Dependency Runtime",
        version="1",
        protocol="structured-cli",
        dependencies=(
            {"name": "node", "minimumVersion": "18.0.0"},
            {"name": "git-bash"},
        ),
    )

    result = resolver.check_prerequisites(
        manifest,
        available={
            "node": str(tmp_path / "node.exe"),
            "git-bash": str(tmp_path / "bash.exe"),
        },
    )

    assert result.ready is True
    assert result.checks[0].version == "20.11.1"
    assert result.checks[0].available is True
    assert result.checks[1].available is True
    assert calls == [(str(tmp_path / "node.exe"), "--version")]


def test_dependency_resolver_fails_closed_when_minimum_version_probe_fails(tmp_path):
    def run(_command):
        return SimpleNamespace(returncode=7, stdout="", stderr="unsupported")

    resolver = DependencyResolver(runner=run)
    manifest = RuntimeManifest(
        runtime_type="dependency-failure-runtime",
        display_name="Dependency Failure Runtime",
        version="1",
        protocol="structured-cli",
        dependencies=({"name": "python", "minimumVersion": "3.11"},),
    )

    result = resolver.check_prerequisites(
        manifest,
        available={"python": str(tmp_path / "python.exe")},
    )

    assert result.ready is False
    assert result.checks[0].available is False
    assert "cannot be verified" in result.checks[0].detail


def test_dependency_version_probe_drains_real_pipes_with_bounded_retention():
    """A verbose dependency cannot block or fill Host memory during probing."""
    resolver = DependencyResolver(timeout_seconds=5)

    result = resolver._run_version_probe((
        sys.executable,
        "-c",
        "import sys; sys.stdout.write('x' * 262144); sys.stderr.write('y' * 262144)",
    ))

    assert result.returncode == 0
    assert result.stdout.startswith("x")
    assert result.stderr.startswith("y")
    assert len(result.stdout.encode("utf-8")) <= 16_001
    assert len(result.stderr.encode("utf-8")) <= 16_001


@pytest.mark.parametrize("state", [InstallState.BROKEN, InstallState.INCOMPATIBLE, InstallState.NEEDS_UPDATE])
def test_discovery_preserves_actionable_failure_state_when_runtime_is_still_missing(tmp_path, state):
    registry = RuntimeRegistry(Database(str(tmp_path / f"missing-{state.value}.sqlite3")))
    registry.register_manifest(RuntimeManifest(
        runtime_type="missing-runtime",
        display_name="Missing Runtime",
        version="2",
        protocol="structured-cli",
        acquisition=AcquisitionType.EXTERNAL,
        executable="executable-not-on-path",
        source="test",
        source_kind=RuntimeSource.CUSTOM,
    ))
    discovered = registry.discover("missing-runtime")
    registry._set_installation(registry._replace(
        discovered,
        state=state,
        path=str(tmp_path / "runtime-that-is-gone"),
        last_error="preserve this reason",
    ))

    rediscovered = registry.discover("missing-runtime")

    assert rediscovered.state is state
    assert rediscovered.last_error == "preserve this reason"


def test_download_manifest_rejects_incomplete_plan_before_approval_prompt(tmp_path):
    registry = RuntimeRegistry(Database(str(tmp_path / "download-plan.sqlite3")))
    manifest = RuntimeManifest(
        runtime_type="invalid-download-runtime",
        display_name="Invalid Download Runtime",
        version="1",
        protocol="structured-cli",
        acquisition=AcquisitionType.DOWNLOAD_BINARY,
        source="community",
        source_kind=RuntimeSource.CUSTOM,
        installer={"resultPath": str(tmp_path / "runtime.exe")},
    )

    trust = TrustedInstallationPolicy().evaluate(manifest, InstallAction.INSTALL)

    assert trust.allowed is False
    assert "downloadUrl" in trust.reason


def test_default_transport_rejects_private_redirect_before_following():
    """A public installer/catalog endpoint cannot redirect into the Host."""
    catalog_client = RuntimeCatalogClient()
    request = SimpleNamespace(get_method=lambda: "GET")

    with pytest.raises(RuntimeUnavailable, match="HTTPS and a non-private host"):
        _ValidatedRedirectHandler(catalog_client._validate_url).redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://127.0.0.1/private-catalog.json",
        )


class _ArtifactResponse:
    status = 200
    headers = {}

    def __init__(self, body: bytes):
        self.body = body
        self.offset = 0
        self.closed = False

    def geturl(self):
        return "https://download.example/runtime.exe"

    def read(self, size=-1):
        if size < 0:
            size = len(self.body) - self.offset
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self):
        self.closed = True


def _download_registry(tmp_path, target, digest):
    registry = RuntimeRegistry(Database(str(tmp_path / "download-runtime.sqlite3")))
    registry.register_manifest(RuntimeManifest(
        runtime_type="download-runtime",
        display_name="Download Runtime",
        version="1",
        protocol="structured-cli",
        acquisition=AcquisitionType.DOWNLOAD_BINARY,
        executable=str(target),
        source="novelforge",
        source_kind=RuntimeSource.MANAGED,
        verification={"sha256": digest},
        installer={
            "downloadUrl": "https://download.example/runtime.exe",
            "resultPath": str(target),
        },
    ))
    return registry


def test_download_binary_is_verified_and_uninstalled_only_at_declared_target(tmp_path):
    body = b"verified runtime binary"
    target = tmp_path / "runtime.exe"
    registry = _download_registry(tmp_path, target, hashlib.sha256(body).hexdigest())
    response = _ArtifactResponse(body)
    broker = InstallerBroker(
        registry,
        artifact_downloader=ArtifactDownloader(opener=lambda request, timeout: response),
    )

    installed = broker.install("download-runtime", approved=True)

    assert installed.state is InstallState.INSTALLED
    assert installed.verified is True
    assert target.read_bytes() == body
    assert response.closed is True
    assert any(event["phase"] == "download" for event in registry.install_events("download-runtime"))

    removed = broker.uninstall("download-runtime", approved=True)
    assert removed.state is InstallState.NOT_INSTALLED
    assert not target.exists()


def test_download_binary_hash_failure_preserves_existing_target(tmp_path):
    target = tmp_path / "runtime.exe"
    target.write_bytes(b"existing artifact")
    body = b"tampered artifact"
    registry = _download_registry(tmp_path, target, hashlib.sha256(b"different").hexdigest())
    broker = InstallerBroker(
        registry,
        artifact_downloader=ArtifactDownloader(
            opener=lambda request, timeout: _ArtifactResponse(body),
        ),
    )

    try:
        broker.install("download-runtime", approved=True)
    except RuntimeUnavailable as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("hash mismatch must reject the install")
    assert target.read_bytes() == b"existing artifact"


def test_bundled_runtime_requires_and_reuses_a_real_declared_executable(tmp_path):
    target = tmp_path / "bundled-runtime.exe"
    target.write_bytes(b"bundled runtime")
    registry = RuntimeRegistry(Database(str(tmp_path / "bundled-runtime.sqlite3")))
    registry.register_manifest(RuntimeManifest(
        runtime_type="bundled-runtime",
        display_name="Bundled Runtime",
        version="1.0.0",
        protocol="structured-cli",
        acquisition=AcquisitionType.BUNDLED,
        executable=str(target),
        source="novelforge",
        source_kind=RuntimeSource.MANAGED,
    ))

    discovered = registry.discover("bundled-runtime")
    assert discovered.state is InstallState.INSTALLED
    assert discovered.path == str(target)

    installed = InstallerBroker(registry).install("bundled-runtime", approved=True)

    assert installed.state is InstallState.INSTALLED
    assert installed.verified is True
    assert installed.path == str(target)

    target.unlink()
    registry._set_installation(registry._replace(
        installed,
        state=InstallState.INSTALLED,
    ))
    broken = registry.discover("bundled-runtime")
    assert broken.state is InstallState.BROKEN
    assert broken.verified is False
