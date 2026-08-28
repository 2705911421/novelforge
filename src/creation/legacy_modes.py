"""Compatibility export for the shared legacy creation safety gate.

The implementation lives in ``src.core.legacy_modes`` so low-level pipeline
modules can use it without importing the eager ``src.creation`` package,
whose public exports include legacy pipeline classes.
"""

from src.core.legacy_modes import require_legacy_creation_mode

__all__ = ["require_legacy_creation_mode"]
