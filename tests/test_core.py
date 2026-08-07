"""NovelForge 单元测试"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.models import StoryProject, Chapter, Character, ChapterStatus
from src.core.config import Config
from src.core.memory import MemorySystem
from src.core.state import StateManager


def test_story_project_creation():
    project = StoryProject(id="test", name="Test Novel", genre="Fantasy")
    assert project.id == "test"
    assert project.name == "Test Novel"
    assert project.get_chapter_count() == 0
    assert project.get_latest_chapter_number() == 0


def test_character_model():
    char = Character(name="Hero", role="protagonist", personality="brave")
    assert char.name == "Hero"
    assert char.status == "alive"


def test_chapter_status():
    ch = Chapter(number=1, title="Chapter 1", content="Content")
    assert ch.status == ChapterStatus.PLANNED
    assert ch.number == 1


def test_config_defaults():
    config = Config()
    assert config.get("project", "chapter_words_min") == 2000
    assert config.get("review", "pass_score") == 93


def test_memory_system():
    tmpdir = tempfile.mkdtemp()
    try:
        memory = MemorySystem(Path(tmpdir))
        memory.store_chapter_summary(1, "Summary 1", ["Event1"], ["Char1"], ["Loc1"])
        summaries = memory.get_recent_summaries(1)
        assert len(summaries) == 1
        assert summaries[0]["summary"] == "Summary 1"

        memory.store_fact(1, "character", "Hero is brave")
        facts = memory.search_facts("Hero")
        assert len(facts) == 1

        context = memory.get_chapter_context(2)
        assert "Summary 1" in context
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_memory_system_releases_sqlite_handle_before_temp_cleanup():
    """Windows must be able to remove a project's memory database immediately."""
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = MemorySystem(Path(tmpdir))
        memory.store_fact(1, "character", "Hero is brave")
        assert memory.search_facts("Hero")


def test_state_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        state = StateManager(Path(tmpdir))
        state.set_phase("writing")
        state.set_current_chapter(5)
        state.add_tokens(1000)
        status = state.get_status()
        assert status["current_phase"] == "writing"
        assert status["current_chapter"] == 5
        assert status["total_tokens_used"] == 1000


def test_foreshadowing_filter():
    project = StoryProject(id="test", name="Test")
    from src.core.models import Foreshadowing
    project.foreshadowing["f1"] = Foreshadowing(id="f1", description="hook1", status="open")
    project.foreshadowing["f2"] = Foreshadowing(id="f2", description="hook2", status="resolved")
    open_hooks = project.get_open_foreshadowing()
    assert len(open_hooks) == 1
    assert open_hooks[0].id == "f1"
