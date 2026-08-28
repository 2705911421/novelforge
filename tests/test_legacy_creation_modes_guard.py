"""Deprecated creation modes cannot become an accidental production path."""

import pytest
from typing import Any, cast

from src.creation.continuous import ContinuousCreationMode
from src.creation.fast_continuous import FastContinuousCreationMode
from src.creation.pipeline_continuous import PipelineOrchestrator
from src.llm.client import LLMClient
from src.llm.gateway import ModelGateway
from src.llm.router import AgentRole, ModelRouter
from src.pipeline.control_surface import AuthorIntent, ControlSurface
from src.pipeline.story_system import StorySystem


@pytest.mark.parametrize(
    "mode",
    [ContinuousCreationMode, FastContinuousCreationMode, PipelineOrchestrator],
)
def test_non_durable_creation_modes_require_explicit_development_opt_in(mode, monkeypatch):
    monkeypatch.delenv("NOVELFORGE_ENABLE_LEGACY_CREATION_MODES", raising=False)
    monkeypatch.setenv("NOVELFORGE_ENV", "development")

    with pytest.raises(RuntimeError, match="LEGACY_CREATION_MODE_DISABLED"):
        mode(cast(Any, None), cast(Any, None), cast(Any, None),
             cast(Any, None), cast(Any, None), {})


def test_legacy_creation_modes_remain_disabled_in_production_even_with_opt_in(monkeypatch):
    monkeypatch.setenv("NOVELFORGE_ENABLE_LEGACY_CREATION_MODES", "1")
    monkeypatch.setenv("NOVELFORGE_ENV", "production")

    with pytest.raises(RuntimeError, match="LEGACY_CREATION_MODE_DISABLED"):
        ContinuousCreationMode(cast(Any, None), cast(Any, None), cast(Any, None),
                               cast(Any, None), cast(Any, None), {})


def test_file_backed_story_system_requires_explicit_development_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("NOVELFORGE_ENABLE_LEGACY_CREATION_MODES", raising=False)
    monkeypatch.setenv("NOVELFORGE_ENV", "development")

    with pytest.raises(RuntimeError, match="LEGACY_STORY_SYSTEM_DISABLED"):
        StorySystem(tmp_path)
    assert not (tmp_path / "story-system").exists()


def test_file_backed_story_system_stays_disabled_in_production(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELFORGE_ENABLE_LEGACY_CREATION_MODES", "1")
    monkeypatch.setenv("NOVELFORGE_ENV", "production")

    with pytest.raises(RuntimeError, match="LEGACY_STORY_SYSTEM_DISABLED"):
        StorySystem(tmp_path)


def test_control_surface_read_does_not_create_workspace_directories(tmp_path):
    surface = ControlSurface(tmp_path)

    assert not (tmp_path / "control").exists()
    assert surface.load_author_intent().content == ""
    assert surface.load_current_focus().content == ""
    assert not (tmp_path / "control").exists()


def test_control_surface_author_intent_write_requires_legacy_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("NOVELFORGE_ENABLE_LEGACY_CREATION_MODES", raising=False)
    monkeypatch.setenv("NOVELFORGE_ENV", "development")

    with pytest.raises(RuntimeError, match="LEGACY_CREATION_MODE_DISABLED"):
        ControlSurface(tmp_path).save_author_intent(AuthorIntent(content="must use Story Bible"))

    assert not (tmp_path / "control").exists()


@pytest.mark.parametrize(
    "operation",
    [
        lambda: LLMClient({"api_key": "test"}).chat([]),
        lambda: ModelRouter(ModelGateway()).chat(AgentRole.WRITER, []),
    ],
)
def test_legacy_direct_provider_paths_require_explicit_development_opt_in(operation, monkeypatch):
    monkeypatch.delenv("NOVELFORGE_ENABLE_LEGACY_LLM_CLIENT", raising=False)
    monkeypatch.setenv("NOVELFORGE_ENV", "development")

    with pytest.raises(RuntimeError, match="LEGACY_LLM_CLIENT_DISABLED"):
        operation()


def test_legacy_direct_provider_paths_stay_disabled_in_production(monkeypatch):
    monkeypatch.setenv("NOVELFORGE_ENABLE_LEGACY_LLM_CLIENT", "1")
    monkeypatch.setenv("NOVELFORGE_ENV", "production")

    with pytest.raises(RuntimeError, match="LEGACY_LLM_CLIENT_DISABLED"):
        LLMClient({"api_key": "test"}).chat([])
