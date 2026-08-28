"""Integration coverage for Phase 3 native Book/Chapter persistence."""

import pytest
from fastapi.testclient import TestClient

from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import ChapterVersionConflict, StoryRepository


@pytest.fixture
def native_workspace(tmp_path):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    return tmp_path, database, repository, ProjectManager(tmp_path, repository=repository)


def test_native_project_and_chapter_survive_a_fresh_manager_without_story_files(native_workspace):
    root, database, repository, manager = native_workspace
    project = manager.create_project("Native Truth", "science-fiction")

    stored = database.get_by_id("projects", project.id)
    assert stored is not None
    assert stored["source_kind"] == "native"
    assert database.count("books", "project_id = ?", (project.id,)) == 1
    assert not (root / "projects" / project.id / "project.json").exists()
    assert {path.name for path in (root / "projects" / project.id).iterdir()} == {
        "attachments", "exports", "backups"
    }

    manager.save_chapter_content(project.id, 1, "First canonical draft")
    fresh = ProjectManager(root, repository=StoryRepository(Database(str(database.db_path))))
    loaded = fresh.load_project(project.id)
    assert loaded is not None
    assert loaded.chapters[1].content == "First canonical draft"

    loaded.chapters[1].title = "Opening"
    fresh.save_project(loaded)
    assert database.count("chapter_versions", "chapter_id = ?", (
        database.fetchone(
            "SELECT id FROM chapters WHERE book_id = (SELECT id FROM books WHERE project_id = ?) AND number = 1",
            (project.id,),
        )["id"],
    )) == 1

    fresh.save_chapter_content(project.id, 1, "Second canonical draft")
    chapter = database.fetchone(
        "SELECT id, content, title FROM chapters WHERE book_id = (SELECT id FROM books WHERE project_id = ?) AND number = 1",
        (project.id,),
    )
    assert chapter is not None
    assert chapter["content"] == "Second canonical draft"
    assert chapter["title"] == "Opening"
    assert database.count("chapter_versions", "chapter_id = ?", (chapter["id"],)) == 2
    assert fresh.load_chapter_content(project.id, 1) == "Second canonical draft"
    with pytest.raises(ChapterVersionConflict, match="expected 1"):
        repository.save_chapter_content(project.id, 1, "stale edit", expected_version=1)
    assert [version["version"] for version in repository.chapter_versions(project.id, 1)] == [2, 1]


@pytest.mark.integration
def test_native_book_api_uses_sqlite_for_create_update_reload_and_delete(native_workspace, monkeypatch):
    from src.web import studio

    root, database, repository, manager = native_workspace
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    client = TestClient(studio.app)

    created = client.post("/api/v1/books/create", json={
        "title": "API Truth", "genre": "fantasy", "chapterWords": 1200, "targetChapters": 8,
        "language": "en",
    })
    assert created.status_code == 200
    project_id = created.json()["id"]
    assert created.json()["targetChapters"] == 8
    assert created.json()["targetWordCount"] == 9600
    assert created.json()["language"] == "en"
    project_record = database.get_by_id("projects", project_id)
    assert project_record is not None
    assert project_record["target_chapters"] == 8
    assert project_record["target_word_count"] == 9600
    assert project_record["language"] == "en"
    assert not (root / "projects" / project_id / "project.json").exists()
    assert client.get("/api/v1/books").json()["books"][0]["id"] == project_id
    book = client.get(f"/api/v1/books/{project_id}")
    assert book.status_code == 200
    assert book.json()["targetChapters"] == 8
    assert book.json()["chapterWordTarget"] == 1200
    assert book.json()["language"] == "en"

    invalid = client.post("/api/v1/books/create", json={"title": "Invalid", "chapterWords": 0})
    assert invalid.status_code == 422

    updated = client.put(f"/api/v1/books/{project_id}/chapters/1", json={
        "title": "Start", "content": "SQLite only chapter",
    })
    assert updated.status_code == 200
    chapter = client.get(f"/api/v1/books/{project_id}/chapters/1")
    assert chapter.status_code == 200
    assert chapter.json()["content"] == "SQLite only chapter"
    assert chapter.json()["title"] == "Start"
    assert chapter.json()["version"] == 1
    versions = client.get(f"/api/v1/books/{project_id}/chapters/1/versions")
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()["versions"]] == [1]
    invalid_transition = client.put(f"/api/v1/books/{project_id}/chapters/1", json={
        "content": "must not persist", "baseVersion": 1, "status": "committed",
    })
    assert invalid_transition.status_code == 409
    assert client.get(f"/api/v1/books/{project_id}/chapters/1").json()["content"] == "SQLite only chapter"
    assert [item["version"] for item in client.get(
        f"/api/v1/books/{project_id}/chapters/1/versions"
    ).json()["versions"]] == [1]
    assert client.put(f"/api/v1/books/{project_id}/chapters/1", json={
        "title": "Stale", "content": "stale", "baseVersion": 0,
    }).status_code == 409

    reloaded_manager = ProjectManager(root, repository=StoryRepository(Database(str(database.db_path))))
    reloaded = reloaded_manager.load_project(project_id)
    assert reloaded is not None
    assert reloaded.chapters[1].content == "SQLite only chapter"
    assert client.delete(f"/api/v1/books/{project_id}/chapters/1").status_code == 200
    assert client.get(f"/api/v1/books/{project_id}/chapters/1").status_code == 404
    assert database.count("chapter_versions", "chapter_id IN (SELECT id FROM chapters WHERE book_id = (SELECT id FROM books WHERE project_id = ?))", (project_id,)) == 0


@pytest.mark.integration
def test_versions_endpoint_returns_empty_history_for_existing_chapter(native_workspace, monkeypatch):
    """A real chapter without versions is not a missing chapter."""
    _, database, _, manager = native_workspace
    from src.web import studio

    project = manager.create_project("Versionless Chapter", "fantasy")
    book_id = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    database.insert("chapters", {
        "id": "chapter-versionless",
        "book_id": book_id,
        "number": 2,
        "title": "Imported without a version row",
        "content": "",
        "summary": "",
        "word_count": 0,
        "status": "draft",
        "key_events": "[]",
        "characters_appeared": "[]",
        "locations_used": "[]",
    })
    monkeypatch.setattr(studio, "story_repository", StoryRepository(database))
    monkeypatch.setattr(studio, "project_mgr", manager)
    response = TestClient(studio.app).get(f"/api/v1/books/{project.id}/chapters/2/versions")
    assert response.status_code == 200
    assert response.json()["versions"] == []
    assert response.json()["historyAvailable"] is False
    assert TestClient(studio.app).get(f"/api/v1/books/{project.id}/chapters/3/versions").status_code == 404


@pytest.mark.integration
def test_version_diff_and_restore_append_history_and_stale_committed_state(native_workspace, monkeypatch):
    from src.web import studio

    _root, database, repository, manager = native_workspace
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    client = TestClient(studio.app)
    project_id = client.post("/api/v1/books/create", json={"title": "Version truth"}).json()["id"]

    first = client.put(f"/api/v1/books/{project_id}/chapters/1", json={
        "title": "Opening", "content": "First line\nShared line",
    })
    assert first.status_code == 200
    chapter = database.fetchone(
        "SELECT id FROM chapters WHERE book_id = (SELECT id FROM books WHERE project_id = ?)",
        (project_id,),
    )
    assert chapter is not None
    commit_id = repository.create_story_commit(chapter["id"], state_changes={"hero": "at home"})
    repository.accept_story_commit_legacy(commit_id, reason="chapter-core fixture")

    second = client.put(f"/api/v1/books/{project_id}/chapters/1", json={
        "content": "Second line\nShared line", "baseVersion": 1,
    })
    assert second.status_code == 200
    assert second.json()["storyStateStale"] is True
    diff = client.get(
        f"/api/v1/books/{project_id}/chapters/1/versions/diff",
        params={"fromVersion": 1, "toVersion": 2},
    )
    assert diff.status_code == 200
    assert diff.json()["changed"] is True
    assert "-First line" in diff.json()["unified_diff"]
    assert "+Second line" in diff.json()["unified_diff"]

    restored = client.post(
        f"/api/v1/books/{project_id}/chapters/1/versions/1/restore",
        json={"baseVersion": 2},
    )
    assert restored.status_code == 200
    assert restored.json()["restored"] is True
    assert restored.json()["version"] == 3
    assert client.get(f"/api/v1/books/{project_id}/chapters/1").json()["content"] == "First line\nShared line"
    assert [item["version"] for item in repository.chapter_versions(project_id, 1)] == [3, 2, 1]
    assert repository.read_story_state(repository.book_for_project(project_id)["id"])["stale"] is True
    assert client.post(
        f"/api/v1/books/{project_id}/chapters/1/versions/1/restore",
        json={"baseVersion": 2},
    ).status_code == 409
    assert client.get(
        f"/api/v1/books/{project_id}/chapters/1/versions/diff",
        params={"fromVersion": 1, "toVersion": 99},
    ).status_code == 404


def test_deleting_a_committed_chapter_marks_its_story_state_stale(native_workspace):
    _root, database, repository, manager = native_workspace
    project = manager.create_project("Delete projection")
    manager.save_chapter_content(project.id, 1, "Committed chapter")
    chapter = database.fetchone(
        "SELECT id FROM chapters WHERE book_id = (SELECT id FROM books WHERE project_id = ?)",
        (project.id,),
    )
    assert chapter is not None
    commit_id = repository.create_story_commit(chapter["id"], state_changes={"location": "harbor"})
    repository.accept_story_commit_legacy(commit_id, reason="chapter-core fixture")

    assert manager.delete_chapter(project.id, 1) is True
    book = repository.book_for_project(project.id)
    assert book is not None
    assert repository.read_story_state(book["id"])["stale"] is True


def test_story_commit_duplicate_prevention(native_workspace):
    """create_story_commit is idempotent for the same chapter version."""
    _, database, repository, manager = native_workspace
    project = manager.create_project("Dupe Commit", "fantasy")
    manager.save_chapter_content(project.id, 1, "Duplicate Test Content")
    chapter = database.fetchone(
        "SELECT id FROM chapters WHERE book_id = (SELECT id FROM books WHERE project_id = ?) AND number = 1",
        (project.id,),
    )
    assert chapter is not None
    # Get the chapter version id
    versions = repository.chapter_versions(project.id, 1)
    assert len(versions) == 1
    version_id = versions[0]["id"]

    # First commit
    commit1 = repository.create_story_commit(
        chapter["id"], chapter_version_id=version_id,
        facts=[{"fact_type": "event", "content": "test event"}],
    )
    assert commit1 is not None

    # Second commit with same chapter_version_id should return same id
    commit2 = repository.create_story_commit(
        chapter["id"], chapter_version_id=version_id,
        facts=[{"fact_type": "event", "content": "different event"}],
    )
    assert commit2 == commit1, "Duplicate commit should return existing id"
