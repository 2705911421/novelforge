"""L27/L28/L29 tests: Auto-save indicators, Dialogue API, Character Themes."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import Database
from src.llm.rate_limiter import SlidingWindowRateLimiter, RateLimitError
from src.llm.dialogue_cache import DialogueCache
from src.llm.dialogue import DialogueWriter, DialogueWriterError
from src.themes.theme_repository import CharacterThemeRepository


# ---- L27: Auto-save indicator state (frontend logic tested via API) ----

def test_autosave_endpoint_accepts_chapter_save(tmp_path, monkeypatch):
    """Verify chapter save endpoint exists and works (autosave target)."""
    from src.core.database import Database
    from src.core.story_repository import StoryRepository
    from src.core.project import ProjectManager
    from src.web import studio

    db = Database(str(tmp_path / "autosave.db"))
    repository = StoryRepository(db, workspace_root=tmp_path)
    manager = ProjectManager(tmp_path, repository=repository)
    project_id = repository.create_native_project("Autosave Test", "fantasy")

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    client = TestClient(studio.app)

    # Save chapter via PUT (autosave target) — URL param is project_id
    resp = client.put(f"/api/v1/books/{project_id}/chapters/1", json={
        "title": "Opening", "content": "First draft content for autosave.",
    })
    assert resp.status_code == 200
    assert resp.json()["version"] == 1

    # Verify chapter persisted
    get_resp = client.get(f"/api/v1/books/{project_id}/chapters/1")
    assert get_resp.status_code == 200
    assert get_resp.json()["content"] == "First draft content for autosave."
    assert get_resp.json()["title"] == "Opening"

    # Update chapter (simulating autosave overwrite)
    update = client.put(f"/api/v1/books/{project_id}/chapters/1", json={
        "content": "Revised content after autosave.",
        "baseVersion": 1,
    })
    assert update.status_code == 200
    assert update.json()["version"] == 2

    # Verify latest content
    latest = client.get(f"/api/v1/books/{project_id}/chapters/1")
    assert latest.json()["content"] == "Revised content after autosave."
    assert latest.json()["version"] == 2

    # Stale version conflict
    stale = client.put(f"/api/v1/books/{project_id}/chapters/1", json={
        "content": "Should not persist", "baseVersion": 1,
    })
    assert stale.status_code == 409


# ---- L28: Rate Limiter ----

class TestRateLimiter:
    def test_allows_within_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=1.0)
        assert limiter.check("key1") is None
        limiter.record("key1")
        assert limiter.check("key1") is None
        limiter.record("key1")
        assert limiter.check("key1") is None
        limiter.record("key1")

    def test_blocks_when_limit_exceeded(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=1.0)
        limiter.record("key1")
        limiter.record("key1")
        retry_after = limiter.check("key1")
        assert retry_after is not None
        assert retry_after > 0

    def test_allow_raises_on_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=1.0)
        limiter.allow("key1")
        with pytest.raises(RateLimitError):
            limiter.allow("key1")

    def test_different_keys_independent(self):
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=1.0)
        limiter.record("key1")
        assert limiter.check("key2") is None

    def test_retry_after_includes_time_remaining(self):
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)
        limiter.record("key1")
        retry_after = limiter.check("key1")
        assert retry_after is not None
        assert retry_after > 50  # Should be close to 60s


# ---- L28: Dialogue Cache ----

class TestDialogueCache:
    def test_cache_miss_returns_none(self):
        cache = DialogueCache(max_size=10, ttl_seconds=60.0)
        assert cache.get(x=1) is None

    def test_cache_hit_returns_value(self):
        cache = DialogueCache(max_size=10, ttl_seconds=60.0)
        cache.set("hello", x=1)
        assert cache.get(x=1) == "hello"

    def test_cache_eviction(self):
        cache = DialogueCache(max_size=2, ttl_seconds=60.0)
        cache.set("a", x=1)
        cache.set("b", x=2)
        cache.set("c", x=3)  # Should evict x=1
        assert cache.get(x=1) is None
        assert cache.get(x=2) == "b"
        assert cache.get(x=3) == "c"

    def test_cache_ttl_expiration(self):
        import time
        cache = DialogueCache(max_size=10, ttl_seconds=0.1)
        cache.set("hello", x=1)
        time.sleep(0.15)
        assert cache.get(x=1) is None

    def test_cache_size(self):
        cache = DialogueCache(max_size=10, ttl_seconds=60.0)
        assert cache.size == 0
        cache.set("a", x=1)
        assert cache.size == 1


# ---- L28: Dialogue Writer ----

class TestDialogueWriter:
    def test_rejects_empty_character_name(self):
        class DummyMgr:
            pass
        writer = DialogueWriter(DummyMgr())
        with pytest.raises(DialogueWriterError) as exc_info:
            writer.generate(character_name="", scene_description="test")
        assert exc_info.value.code == "INVALID_INPUT"

    def test_rejects_empty_scene(self):
        class DummyMgr:
            pass
        writer = DialogueWriter(DummyMgr())
        with pytest.raises(DialogueWriterError) as exc_info:
            writer.generate(character_name="Alice", scene_description="")
        assert exc_info.value.code == "INVALID_INPUT"

    def test_uses_tone_presets(self):
        from src.llm.dialogue import TONE_PRESETS
        assert "formal" in TONE_PRESETS
        assert "casual" in TONE_PRESETS
        assert "angry" in TONE_PRESETS
        assert "sarcastic" in TONE_PRESETS


# ---- L29: Character Themes Repository ----

@pytest.fixture
def theme_db(tmp_path):
    return Database(str(tmp_path / "themes.db"))


@pytest.fixture
def theme_repo(theme_db):
    return CharacterThemeRepository(theme_db)


class TestCharacterThemes:
    def test_create_theme(self, theme_repo, theme_db):
        with theme_db.transaction() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, source_kind, migration_status) VALUES ('p1', 'Test', 'native', 'migrated')"
            )
        theme = theme_repo.create("p1", "Hero Theme", primary_color="#ff0000")
        assert theme["name"] == "Hero Theme"
        assert theme["primary_color"] == "#ff0000"
        assert theme["id"] is not None

    def test_get_theme(self, theme_repo, theme_db):
        with theme_db.transaction() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, source_kind, migration_status) VALUES ('p1', 'Test', 'native', 'migrated')"
            )
        created = theme_repo.create("p1", "Theme1")
        fetched = theme_repo.get(created["id"])
        assert fetched is not None
        assert fetched["name"] == "Theme1"

    def test_list_by_project(self, theme_repo, theme_db):
        with theme_db.transaction() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, source_kind, migration_status) VALUES ('p1', 'Test', 'native', 'migrated')"
            )
        theme_repo.create("p1", "Theme1")
        theme_repo.create("p1", "Theme2")
        themes = theme_repo.list_by_project("p1")
        assert len(themes) == 2

    def test_update_theme(self, theme_repo, theme_db):
        with theme_db.transaction() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, source_kind, migration_status) VALUES ('p1', 'Test', 'native', 'migrated')"
            )
        created = theme_repo.create("p1", "Theme1")
        theme_repo.update(created["id"], name="Updated", primary_color="#00ff00")
        updated = theme_repo.get(created["id"])
        assert updated["name"] == "Updated"
        assert updated["primary_color"] == "#00ff00"

    def test_delete_theme(self, theme_repo, theme_db):
        with theme_db.transaction() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, source_kind, migration_status) VALUES ('p1', 'Test', 'native', 'migrated')"
            )
        created = theme_repo.create("p1", "Theme1")
        assert theme_repo.delete(created["id"]) is True
        assert theme_repo.get(created["id"]) is None

    def test_delete_nonexistent_returns_false(self, theme_repo):
        assert theme_repo.delete("nonexistent") is False

    def test_list_by_character(self, theme_repo, theme_db):
        with theme_db.transaction() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, source_kind, migration_status) VALUES ('p1', 'Test', 'native', 'migrated')"
            )
        theme_repo.create("p1", "Theme1", character_id="char1")
        theme_repo.create("p1", "Theme2", character_id="char2")
        themes = theme_repo.list_by_character("char1")
        assert len(themes) == 1
        assert themes[0]["character_id"] == "char1"
