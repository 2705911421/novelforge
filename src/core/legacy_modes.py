"""Development-only gates for deprecated non-durable compatibility paths."""

from __future__ import annotations

import os


_OPT_IN_ENV = "NOVELFORGE_ENABLE_LEGACY_CREATION_MODES"
_PRODUCTION_ENVS = {"production", "prod", "staging"}


def require_legacy_creation_mode(mode_name: str) -> None:
    """Require an explicit development-only opt-in before using a legacy mode."""
    enabled = os.environ.get(_OPT_IN_ENV, "").strip().lower() in {"1", "true", "yes"}
    deployment = os.environ.get("NOVELFORGE_ENV", "development").strip().lower()
    if not enabled or deployment in _PRODUCTION_ENVS:
        raise RuntimeError(
            "LEGACY_CREATION_MODE_DISABLED: "
            f"{mode_name} is deprecated and non-durable; use the persistent "
            "TaskRuntime/ContinuousWritingService path. Set "
            f"{_OPT_IN_ENV}=1 only for development compatibility."
        )
