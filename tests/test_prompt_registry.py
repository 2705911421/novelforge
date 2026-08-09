"""Tests for Prompt Registry system (PROMPT-001/002/003/004/005)."""

import pytest

from src.core.database import Database
from src.prompts.prompt_repository import PromptRepository, DEFAULT_PROMPTS


@pytest.fixture
def db(tmp_path):
    """Create a test database."""
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    yield database


@pytest.fixture
def repo(db):
    """Create a prompt repository."""
    return PromptRepository(db)


class TestPromptRepository:
    """Test PromptRepository functionality."""

    # ========== PROMPT-001: 注册表基本功能 ==========

    def test_get_builtin_prompt(self, repo):
        """Test getting a built-in prompt."""
        prompt = repo.get_prompt("write-next")
        assert prompt["task_type"] == "write-next"
        assert prompt["system_prompt"] == DEFAULT_PROMPTS["write-next"]["system"]
        assert prompt["user_template"] == DEFAULT_PROMPTS["write-next"]["user_template"]
        assert prompt["version"] == 0
        assert prompt["is_default"] is True

    def test_get_unknown_prompt(self, repo):
        """Test getting an unknown prompt type."""
        prompt = repo.get_prompt("unknown-type")
        assert prompt["task_type"] == "unknown-type"
        assert prompt["system_prompt"] == ""
        assert prompt["user_template"] == ""

    def test_save_and_get_prompt(self, repo):
        """Test saving and retrieving a prompt."""
        # Save a prompt
        result = repo.save_prompt(
            task_type="write-next",
            system_prompt="Custom system prompt",
            user_template="Custom user template",
        )
        assert result["task_type"] == "write-next"
        assert result["version"] == 1

        # Get the saved prompt
        prompt = repo.get_prompt("write-next")
        assert prompt["system_prompt"] == "Custom system prompt"
        assert prompt["user_template"] == "Custom user template"
        assert prompt["version"] == 1

    def test_save_project_specific_prompt(self, repo):
        """Test saving a project-specific prompt."""
        # Save project-specific prompt
        result = repo.save_prompt(
            task_type="write-next",
            system_prompt="Project specific",
            user_template="Project template",
            project_id="proj-123",
        )
        assert result["project_id"] == "proj-123"

        # Get project-specific prompt
        prompt = repo.get_prompt("write-next", project_id="proj-123")
        assert prompt["system_prompt"] == "Project specific"

        # Global prompt should still be built-in
        global_prompt = repo.get_prompt("write-next")
        assert global_prompt["system_prompt"] == DEFAULT_PROMPTS["write-next"]["system"]

    def test_list_prompts(self, repo):
        """Test listing prompts."""
        # Save some prompts
        repo.save_prompt("write-next", "sys1", "user1")
        repo.save_prompt("review", "sys2", "user2")

        # List all prompts
        prompts = repo.list_prompts()
        assert len(prompts) >= 2

    def test_delete_prompt(self, repo):
        """Test deleting a prompt."""
        # Save a prompt
        result = repo.save_prompt("write-next", "sys", "user")
        prompt_id = result["id"]

        # Delete it
        deleted = repo.delete_prompt(prompt_id)
        assert deleted is True

        # Should return built-in default now
        prompt = repo.get_prompt("write-next")
        assert prompt["version"] == 0

    # ========== PROMPT-002: 版本化 ==========

    def test_version_history(self, repo):
        """Test getting version history."""
        # Save multiple versions
        repo.save_prompt("write-next", "v1 system", "v1 user")
        repo.save_prompt("write-next", "v2 system", "v2 user")
        repo.save_prompt("write-next", "v3 system", "v3 user")

        # Get version history
        versions = repo.get_version_history("write-next")
        assert len(versions) == 3
        assert versions[0]["version"] == 3  # Latest first
        assert versions[1]["version"] == 2
        assert versions[2]["version"] == 1

    def test_rollback_to_version(self, repo):
        """Test rolling back to a specific version."""
        # Save multiple versions
        repo.save_prompt("write-next", "v1 system", "v1 user")
        repo.save_prompt("write-next", "v2 system", "v2 user")
        repo.save_prompt("write-next", "v3 system", "v3 user")

        # Rollback to v1
        result = repo.rollback_to_version("write-next", 1)
        assert result["version"] == 4  # New version created

        # Verify content matches v1
        prompt = repo.get_prompt("write-next")
        assert prompt["system_prompt"] == "v1 system"
        assert prompt["user_template"] == "v1 user"

    def test_rollback_nonexistent_version(self, repo):
        """Test rolling back to a non-existent version."""
        with pytest.raises(ValueError, match="版本不存在"):
            repo.rollback_to_version("write-next", 999)

    # ========== PROMPT-004: 导入导出 ==========

    def test_export_prompts(self, repo):
        """Test exporting prompts."""
        # Save some prompts
        repo.save_prompt("write-next", "sys1", "user1")
        repo.save_prompt("review", "sys2", "user2")

        # Export all
        result = repo.export_prompts()
        assert result["version"] == "1.0"
        assert result["count"] >= 2
        assert len(result["prompts"]) >= 2

    def test_export_prompts_by_type(self, repo):
        """Test exporting specific prompt types."""
        # Save some prompts
        repo.save_prompt("write-next", "sys1", "user1")
        repo.save_prompt("review", "sys2", "user2")

        # Export only write-next
        result = repo.export_prompts(task_types=["write-next"])
        assert result["count"] == 1
        assert result["prompts"][0]["task_type"] == "write-next"

    def test_import_prompts(self, repo):
        """Test importing prompts."""
        # Create import data
        import_data = {
            "version": "1.0",
            "prompts": [
                {
                    "task_type": "write-next",
                    "system_prompt": "Imported system",
                    "user_template": "Imported user",
                },
                {
                    "task_type": "review",
                    "system_prompt": "Imported review",
                    "user_template": "Imported review user",
                },
            ],
        }

        # Import
        result = repo.import_prompts(import_data)
        assert result["imported"] == 2
        assert result["skipped"] == 0
        assert len(result["errors"]) == 0

        # Verify imported prompts
        prompt = repo.get_prompt("write-next")
        assert prompt["system_prompt"] == "Imported system"

    def test_import_prompts_skip_existing(self, repo):
        """Test importing prompts with skip existing."""
        # Save an existing prompt
        repo.save_prompt("write-next", "Existing", "Existing user")

        # Create import data
        import_data = {
            "version": "1.0",
            "prompts": [
                {
                    "task_type": "write-next",
                    "system_prompt": "Imported",
                    "user_template": "Imported user",
                },
            ],
        }

        # Import without overwrite
        result = repo.import_prompts(import_data, overwrite=False)
        assert result["imported"] == 0
        assert result["skipped"] == 1

    def test_import_prompts_overwrite(self, repo):
        """Test importing prompts with overwrite."""
        # Save an existing prompt
        repo.save_prompt("write-next", "Existing", "Existing user")

        # Create import data
        import_data = {
            "version": "1.0",
            "prompts": [
                {
                    "task_type": "write-next",
                    "system_prompt": "Imported",
                    "user_template": "Imported user",
                },
            ],
        }

        # Import with overwrite
        result = repo.import_prompts(import_data, overwrite=True)
        assert result["imported"] == 1
        assert result["skipped"] == 0

    def test_export_import_roundtrip(self, repo):
        """Test export-import roundtrip."""
        # Save some prompts
        repo.save_prompt("write-next", "sys1", "user1")
        repo.save_prompt("review", "sys2", "user2")

        # Export
        exported = repo.export_prompts()

        # Create a new repository
        new_repo = PromptRepository(repo.db)

        # Import
        result = new_repo.import_prompts(exported, overwrite=True)
        assert result["imported"] >= 2

    # ========== PROMPT-005: 恢复默认 ==========

    def test_restore_defaults(self, repo):
        """Test restoring defaults."""
        # Save custom prompts
        repo.save_prompt("write-next", "Custom", "Custom user")
        repo.save_prompt("review", "Custom review", "Custom review user")

        # Verify custom prompts exist
        prompt = repo.get_prompt("write-next")
        assert prompt["system_prompt"] == "Custom"

        # Restore defaults
        result = repo.restore_defaults()
        assert result["restored"] >= 2
        assert len(result["errors"]) == 0

        # Verify defaults are restored
        prompt = repo.get_prompt("write-next")
        assert prompt["system_prompt"] == DEFAULT_PROMPTS["write-next"]["system"]

    def test_restore_defaults_specific_types(self, repo):
        """Test restoring defaults for specific types."""
        # Save custom prompts
        repo.save_prompt("write-next", "Custom", "Custom user")
        repo.save_prompt("review", "Custom review", "Custom review user")

        # Restore only write-next
        result = repo.restore_defaults(task_types=["write-next"])
        assert result["restored"] == 1

        # write-next should be restored
        prompt = repo.get_prompt("write-next")
        assert prompt["system_prompt"] == DEFAULT_PROMPTS["write-next"]["system"]

        # review should still be custom
        prompt = repo.get_prompt("review")
        assert prompt["system_prompt"] == "Custom review"

    # ========== 辅助功能 ==========

    def test_get_all_task_types(self, repo):
        """Test getting all task types."""
        # Save a custom prompt
        repo.save_prompt("custom-type", "sys", "user")

        # Get all task types
        task_types = repo.get_all_task_types()
        assert "write-next" in task_types
        assert "review" in task_types
        assert "custom-type" in task_types
