"""Phase 18: World Bootstrap Wizard - guided story bible creation."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.wizard.world_bootstrap_service import WorldBootstrapService


@pytest.fixture
def phase_db(tmp_path):
    return Database(str(tmp_path / "authoritative.db"))


@pytest.fixture
def wizard_deps(phase_db, tmp_path):
    """Set up wizard dependencies with a seeded project."""
    repo = StoryRepository(phase_db)
    manager = ProjectManager(str(tmp_path), repository=repo)

    project = manager.create_project("Wizard Test", "fantasy")
    project_id = project.id

    return {
        "db": phase_db,
        "repo": repo,
        "manager": manager,
        "project_id": project_id,
    }


class DummyModelManager:
    """Mock model manager for wizard."""

    def chat(self, messages, system=None, task_type=None):
        class Response:
            content = '{"theme": "revenge", "setting": "ancient kingdom"}'
        return Response()


def test_get_wizard_state(wizard_deps):
    """Test getting wizard state."""
    db = wizard_deps["db"]
    model = DummyModelManager()
    service = WorldBootstrapService(db, model)
    project_id = wizard_deps["project_id"]

    state = service.get_wizard_state(project_id)
    assert state["total_steps"] == 25
    assert state["current_step"] == 1
    assert state["status"] == "draft"
    assert len(state["steps"]) == 25


def test_submit_step(wizard_deps):
    """Test submitting a draft for a step."""
    db = wizard_deps["db"]
    model = DummyModelManager()
    service = WorldBootstrapService(db, model)
    project_id = wizard_deps["project_id"]

    result = service.submit_step(project_id, "intent", {"theme": "revenge"})
    assert result["step_key"] == "intent"
    assert result["status"] == "draft"


def test_confirm_step(wizard_deps):
    """Test confirming a step."""
    db = wizard_deps["db"]
    model = DummyModelManager()
    service = WorldBootstrapService(db, model)
    project_id = wizard_deps["project_id"]

    service.submit_step(project_id, "intent", {"theme": "revenge"})
    result = service.confirm_step(project_id, "intent")
    assert result["step_key"] == "intent"
    assert result["status"] == "confirmed"


def test_generate_step(wizard_deps):
    """Test generating an AI suggestion for a step."""
    db = wizard_deps["db"]
    model = DummyModelManager()
    service = WorldBootstrapService(db, model)
    project_id = wizard_deps["project_id"]

    result = service.generate_step(project_id, "intent", brief="A dark revenge story")
    assert result["step_key"] == "intent"
    assert "suggestion" in result


def test_wizard_state_after_submissions(wizard_deps):
    """Test wizard state after submitting multiple steps."""
    db = wizard_deps["db"]
    model = DummyModelManager()
    service = WorldBootstrapService(db, model)
    project_id = wizard_deps["project_id"]

    service.submit_step(project_id, "intent", {"theme": "revenge"})
    service.confirm_step(project_id, "intent")
    service.submit_step(project_id, "audience", {"age": "adult"})

    state = service.get_wizard_state(project_id)
    # Current step should be 2 (audience), since step 1 is confirmed.
    assert state["current_step"] == 2

    # Check step statuses.
    step1 = next(s for s in state["steps"] if s["key"] == "intent")
    step2 = next(s for s in state["steps"] if s["key"] == "audience")
    assert step1["status"] == "confirmed"
    assert step2["status"] == "draft"
