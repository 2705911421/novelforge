"""Coverage for the draft-import, built-in catalog, and genre contracts."""

from __future__ import annotations

import io
import zipfile

import pytest

from src.core.database import Database
from src.ingestion.draft_import import (
    DraftImportRepository,
    MAX_ANALYSIS_WINDOW_CHARS,
    MAX_ANALYSIS_WINDOW_CHAPTERS,
    build_analysis_windows,
    build_chapter_manifest,
)
from src.integrations import SkillRepository, parse_skill_files, parse_skill_upload
from src.integrations.builtin_skills import BUILTIN_SKILLS, INKOS_BUILTIN_SKILL_KEYS
from src.llm.agent_prompts import DEFAULT_AGENT_SYSTEM_PROMPTS, REQUIRED_AGENT_CONTRACT_SECTIONS
from src.pipeline.rules import GENRE_RULES, get_genre_profile, resolve_genre_key


def test_genre_catalog_contains_planning_and_limits_for_every_profile():
    assert len(GENRE_RULES) >= 30
    for profile in GENRE_RULES.values():
        assert profile["id"]
        assert profile["description"]
        assert profile["tags"]
        assert profile["planning"]["core_promise"]
        assert profile["planning"]["structure"]
        assert profile["planning"]["chapter_template"]
        assert profile["planning"]["pacing"]
        assert profile["planning"]["must_track"]
        assert profile["planning"]["continuation_checks"]
        assert profile["limits"]["hard"]
        assert profile["limits"]["review_gates"]
        assert profile["rules"]
        assert profile["taboos"]

    assert resolve_genre_key("xianxia") == "玄幻修仙"
    assert get_genre_profile("xianxia")["id"] == "xianxia"


def test_routed_prompts_are_structured_agent_contracts():
    assert set(DEFAULT_AGENT_SYSTEM_PROMPTS) == {
        "planner", "writer", "reviewer", "reviser", "context",
        "fact_extraction", "embedding", "rerank", "image",
    }
    for prompt in DEFAULT_AGENT_SYSTEM_PROMPTS.values():
        assert prompt.startswith("# NovelForge Agent Contract:")
        assert "## Authority" in prompt
        assert "## Workflow" in prompt
        assert "## Output Contract" in prompt
        assert "## Forbidden" in prompt
        assert all(section in prompt for section in REQUIRED_AGENT_CONTRACT_SECTIONS)


def test_builtins_are_idempotent_and_protected(tmp_path):
    repository = SkillRepository(Database(str(tmp_path / "skills.db")))
    first_count = repository.seed_builtins()
    first = repository.list()
    second_count = repository.seed_builtins()
    second = repository.list()
    assert {item["key"] for item in first} == set(INKOS_BUILTIN_SKILL_KEYS)
    assert {item["key"] for item in BUILTIN_SKILLS} == set(INKOS_BUILTIN_SKILL_KEYS)
    assert first_count == len(INKOS_BUILTIN_SKILL_KEYS)
    assert second_count == 0
    assert len(second) == len(first)
    assert all(item["source"] == "builtin" for item in second)
    with pytest.raises(Exception, match="built-in"):
        repository.delete(first[0]["id"])


def test_skill_archive_import_reads_manifest_and_never_executes_package(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sample-skill-main/SKILL.md", "---\nname: Archive Skill\ndescription: test\n---\n# Rules\nKeep facts.")
        archive.writestr("sample-skill-main/references/checklist.md", "# checklist")
        archive.writestr("sample-skill-main/hooks/install.sh", "echo should-not-run")
    package = parse_skill_upload(buffer.getvalue(), "sample-skill.zip", origin="local-upload")
    assert package.key == "archive-skill"
    assert package.config["import"]["scriptsExecuted"] is False
    assert any(item["path"].endswith("checklist.md") for item in package.config["import"]["referenceFiles"])


def test_skill_folder_import_rejects_traversal_and_preserves_reference_paths():
    package = parse_skill_files({
        "nested/SKILL.md": b"---\nname: Folder Skill\ndescription: test\n---\n# Rules\nKeep facts.",
        "nested/references/guide.md": b"# Guide",
    }, origin="local-folder")
    assert package.config["import"]["manifestPath"] == "nested/SKILL.md"
    assert package.config["import"]["referenceFiles"][0]["path"] == "nested/references/guide.md"
    with pytest.raises(Exception, match="escapes"):
        parse_skill_files({"../SKILL.md": b"---\nname: Bad\ndescription: bad\n---\ntext"})


def test_chapter_manifest_and_windows_are_deterministic_and_bounded():
    documents = [
        {"id": "ten", "name": "chapter-10.md", "metadata": {"relativePath": "draft/chapter-10.md"}, "full_text": "Chapter 10\n" + "b" * 50},
        {"id": "two", "name": "chapter-2.md", "metadata": {"relativePath": "draft/chapter-2.md"}, "full_text": "第 2 章\n" + "a" * 50},
        {"id": "unknown", "name": "notes.md", "metadata": {"relativePath": "draft/notes.md"}, "full_text": "no heading"},
    ]
    manifest = build_chapter_manifest(documents)
    assert [item["document_id"] for item in manifest] == ["two", "ten", "unknown"]
    assert manifest[0]["chapter_number"] == 2
    assert manifest[-1]["warnings"]
    windows = build_analysis_windows(
        manifest,
        {"two": "a" * 9_000, "ten": "b" * 9_000, "unknown": "c" * 9_000},
        max_chars=MAX_ANALYSIS_WINDOW_CHARS,
        max_chapters=MAX_ANALYSIS_WINDOW_CHAPTERS,
    )
    assert windows[0]["chapters"] == [1, 2]
    assert all(window["character_count"] <= MAX_ANALYSIS_WINDOW_CHARS for window in windows)
    assert all(len(window["items"]) <= MAX_ANALYSIS_WINDOW_CHAPTERS for window in windows)


def test_draft_repository_can_attach_adjustment_plan_without_changing_imported_sources(tmp_path):
    repository = DraftImportRepository(Database(str(tmp_path / "drafts.db")))
    record = repository.create("project-1", story_bible_document_id="story", language_plan_document_id="style", draft_document_ids=["chapter-1"])
    repository.mark_running(record["id"])
    repository.complete(record["id"], {"verdict": "minor_drift"})
    updated = repository.update_report(record["id"], {"adjustment_plan": {"repair_queue": []}}, project_id="project-1", status="completed")
    assert updated["status"] == "completed"
    assert updated["draft_document_ids"] == ["chapter-1"]
    assert updated["report"]["adjustment_plan"]["repair_queue"] == []


def test_draft_import_repository_preserves_report_and_retry_state(tmp_path):
    repository = DraftImportRepository(Database(str(tmp_path / "drafts.db")))
    record = repository.create(
        "project-1",
        story_bible_document_id="story",
        language_plan_document_id="style",
        draft_document_ids=["chapter-1"],
    )
    repository.set_task(record["id"], "task-1", project_id="project-1")
    repository.mark_running(record["id"])
    repository.complete(record["id"], {"verdict": "minor_drift", "drift_score": 12})
    completed = repository.get(record["id"], project_id="project-1")
    assert completed["status"] == "completed"
    assert completed["report"]["drift_score"] == 12
    with pytest.raises(Exception, match="only failed"):
        repository.reset_for_retry(record["id"], project_id="project-1")
    repository.fail(record["id"], "MODEL_FAILED", "provider unavailable")
    retried = repository.reset_for_retry(record["id"], project_id="project-1")
    assert retried["status"] == "uploaded"
    assert retried["report"] == {}
