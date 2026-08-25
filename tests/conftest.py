"""Repository-wide test isolation defaults.

Studio's real Worker is exercised by the dedicated TaskRuntime/Worker tests.
HTTP tests use the application as a request surface and must not start a
background loop against the import-time workspace database.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest


_TEST_ROOT: str | None = None
if not os.environ.get("NOVELFORGE_ROOT"):
    _TEST_ROOT = tempfile.mkdtemp(prefix="novelforge-pytest-")
    os.environ["NOVELFORGE_ROOT"] = _TEST_ROOT


@pytest.fixture(autouse=True)
def disable_studio_worker_for_http_tests(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session, exitstatus
    if _TEST_ROOT:
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)
