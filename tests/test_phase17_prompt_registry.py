"""Phase 17: Prompt Registry - customizable prompts per task type."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import Database
from src.prompts.prompt_repository import DEFAULT_PROMPTS, PromptRepository


@pytest.fixture
def phase_db(tmp_path):
    return Database(str(tmp_path / "authoritative.db"))


@pytest.fixture
def prompt_repo(phase_db):
    return PromptRepository(phase_db)


def test_get_default_prompt(prompt_repo):
    """Test getting a built-in default prompt."""
    prompt = prompt_repo.get_prompt("write-next")
    assert prompt["task_type"] == "write-next"
    assert len(prompt["system_prompt"]) > 0
    assert len(prompt["user_template"]) > 0


def test_get_default_prompt_unknown_type(prompt_repo):
    """Test getting a prompt for unknown task type returns empty."""
    prompt = prompt_repo.get_prompt("unknown-task")
    assert prompt["task_type"] == "unknown-task"
    assert prompt["system_prompt"] == ""


def test_save_and_retrieve_prompt(prompt_repo):
    """Test saving a custom prompt and retrieving it."""
    result = prompt_repo.save_prompt(
        task_type="write-next",
        system_prompt="Custom system prompt",
        user_template="Custom template with {content}",
    )
    assert result["version"] == 1

    prompt = prompt_repo.get_prompt("write-next")
    assert prompt["system_prompt"] == "Custom system prompt"


def test_save_project_specific_prompt(prompt_repo, phase_db):
    """Test saving a project-specific prompt."""
    # Seed a project.
    with phase_db.transaction() as conn:
        conn.execute(
            "INSERT INTO projects(id, name, source_kind, migration_status) VALUES ('proj1', 'Test', 'native', 'migrated')"
        )

    # Save global default.
    prompt_repo.save_prompt(
        task_type="review",
        system_prompt="Global review prompt",
        user_template="Global template",
    )

    # Save project-specific.
    prompt_repo.save_prompt(
        task_type="review",
        system_prompt="Project-specific review prompt",
        user_template="Project template",
        project_id="proj1",
    )

    # Without project_id: get global.
    global_prompt = prompt_repo.get_prompt("review")
    assert global_prompt["system_prompt"] == "Global review prompt"

    # With project_id: get project-specific.
    project_prompt = prompt_repo.get_prompt("review", project_id="proj1")
    assert project_prompt["system_prompt"] == "Project-specific review prompt"


def test_prompt_versioning(prompt_repo):
    """Test prompt versioning."""
    prompt_repo.save_prompt(
        task_type="write-next",
        system_prompt="Version 1",
        user_template="Template 1",
    )
    prompt_repo.save_prompt(
        task_type="write-next",
        system_prompt="Version 2",
        user_template="Template 2",
    )

    prompt = prompt_repo.get_prompt("write-next")
    assert prompt["system_prompt"] == "Version 2"
    assert prompt["version"] == 2


def test_list_prompts(prompt_repo):
    """Test listing all prompts."""
    prompt_repo.save_prompt(task_type="write-next", system_prompt="A", user_template="B")
    prompt_repo.save_prompt(task_type="review", system_prompt="C", user_template="D")

    prompts = prompt_repo.list_prompts()
    task_types = {p["task_type"] for p in prompts}
    assert "write-next" in task_types
    assert "review" in task_types


def test_delete_prompt(prompt_repo):
    """Test deleting a prompt."""
    result = prompt_repo.save_prompt(
        task_type="write-next",
        system_prompt="To delete",
        user_template="Template",
    )
    prompt_id = result["id"]

    deleted = prompt_repo.delete_prompt(prompt_id)
    assert deleted is True

    # Deleting again should return False.
    deleted = prompt_repo.delete_prompt(prompt_id)
    assert deleted is False


def test_default_prompts_cover_all_task_types(prompt_repo):
    """Test that all expected task types have defaults."""
    expected_types = {"write-next", "review", "revision", "fact-extraction", "story-bible-suggest", "joint-review"}
    assert expected_types == set(DEFAULT_PROMPTS.keys())
