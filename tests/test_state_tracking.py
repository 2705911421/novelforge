"""Tests for state tracking system (CHAR-004, FACTION-004, LOC-004)."""

import pytest

from src.core.database import Database, generate_id
from src.core.state_tracking import StateTrackingRepository


@pytest.fixture
def db(tmp_path):
    """Create a test database."""
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    yield database


@pytest.fixture
def repo(db):
    """Create a state tracking repository."""
    return StateTrackingRepository(db=db)


@pytest.fixture
def sample_entities(db):
    """Create sample entities for testing."""
    project_id = generate_id()
    book_id = generate_id()
    chapter_id = generate_id()
    character_id = generate_id()
    faction_id = generate_id()
    location_id = generate_id()

    # Create project
    db.execute(
        """INSERT INTO projects(id, name, genre, target_chapters)
           VALUES (?, ?, ?, ?)""",
        (project_id, "Test Project", "Fantasy", 100),
    )

    # Create book
    db.execute(
        """INSERT INTO books(id, project_id, title, genre)
           VALUES (?, ?, ?, ?)""",
        (book_id, project_id, "Test Book", "Fantasy"),
    )

    # Create chapter
    db.execute(
        """INSERT INTO chapters(id, book_id, number, title, content, word_count)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (chapter_id, book_id, 1, "Chapter 1", "Content", 100),
    )

    # Create character
    db.execute(
        """INSERT INTO characters(id, book_id, name, description, personality)
           VALUES (?, ?, ?, ?, ?)""",
        (character_id, book_id, "张三", "主角", "善良，勇敢"),
    )

    # Create faction
    db.execute(
        """INSERT INTO factions(id, book_id, name, description)
           VALUES (?, ?, ?, ?)""",
        (faction_id, book_id, "青云门", "修仙门派"),
    )

    # Create location
    db.execute(
        """INSERT INTO locations(id, book_id, name, description, type)
           VALUES (?, ?, ?, ?, ?)""",
        (location_id, book_id, "青云城", "繁华城市", "city"),
    )

    return {
        "project_id": project_id,
        "book_id": book_id,
        "chapter_id": chapter_id,
        "character_id": character_id,
        "faction_id": faction_id,
        "location_id": location_id,
    }


class TestCharacterState:
    """Test character state tracking (CHAR-004)."""

    def test_create_character_state(self, repo, sample_entities):
        """Test creating a character state snapshot."""
        state_id = repo.create_character_state(
            character_id=sample_entities["character_id"],
            chapter_id=sample_entities["chapter_id"],
            location="青云城",
            status="alive",
            relationships={"李四": "朋友"},
            knowledge=["知道青云门的秘密"],
            emotional_state="平静",
        )

        assert state_id is not None

        # Verify state was created
        state = repo.get_character_state(state_id)
        assert state is not None
        assert state.character_id == sample_entities["character_id"]
        assert state.chapter_id == sample_entities["chapter_id"]
        assert state.location == "青云城"
        assert state.status == "alive"
        assert state.relationships == {"李四": "朋友"}
        assert state.knowledge == ["知道青云门的秘密"]
        assert state.emotional_state == "平静"

    def test_get_character_states(self, repo, sample_entities):
        """Test getting character state history."""
        # Create multiple states
        for i in range(3):
            repo.create_character_state(
                character_id=sample_entities["character_id"],
                chapter_id=sample_entities["chapter_id"],
                location=f"Location {i}",
                status="alive",
            )

        # Get all states
        states = repo.get_character_states(sample_entities["character_id"])
        assert len(states) == 3

    def test_get_character_state_at_chapter(self, repo, sample_entities):
        """Test getting character state at specific chapter."""
        # Create state
        repo.create_character_state(
            character_id=sample_entities["character_id"],
            chapter_id=sample_entities["chapter_id"],
            location="青云城",
            status="alive",
        )

        # Get state at chapter
        state = repo.get_character_state_at_chapter(
            character_id=sample_entities["character_id"],
            chapter_id=sample_entities["chapter_id"],
        )
        assert state is not None
        assert state.location == "青云城"

    def test_get_latest_character_state(self, repo, sample_entities):
        """Test getting latest character state."""
        # Create state
        state_id = repo.create_character_state(
            character_id=sample_entities["character_id"],
            chapter_id=sample_entities["chapter_id"],
            status="injured",
        )

        # Get latest state
        state = repo.get_latest_character_state(sample_entities["character_id"])
        assert state is not None
        assert state.status == "injured"

    def test_character_state_not_found(self, repo):
        """Test getting non-existent character state."""
        state = repo.get_character_state("non-existent-id")
        assert state is None


class TestFactionState:
    """Test faction state tracking (FACTION-004)."""

    def test_create_faction_state(self, repo, sample_entities):
        """Test creating a faction state snapshot."""
        state_id = repo.create_faction_state(
            faction_id=sample_entities["faction_id"],
            chapter_id=sample_entities["chapter_id"],
            territory="青云城及周边",
            power_level="中等",
            allies=["天剑宗"],
            enemies=["魔教"],
        )

        assert state_id is not None

        # Verify state was created
        state = repo.get_faction_state(state_id)
        assert state is not None
        assert state.faction_id == sample_entities["faction_id"]
        assert state.chapter_id == sample_entities["chapter_id"]
        assert state.territory == "青云城及周边"
        assert state.power_level == "中等"
        assert state.allies == ["天剑宗"]
        assert state.enemies == ["魔教"]

    def test_get_faction_states(self, repo, sample_entities):
        """Test getting faction state history."""
        # Create multiple states
        for i in range(3):
            repo.create_faction_state(
                faction_id=sample_entities["faction_id"],
                chapter_id=sample_entities["chapter_id"],
                territory=f"Territory {i}",
            )

        # Get all states
        states = repo.get_faction_states(sample_entities["faction_id"])
        assert len(states) == 3

    def test_get_faction_state_at_chapter(self, repo, sample_entities):
        """Test getting faction state at specific chapter."""
        # Create state
        repo.create_faction_state(
            faction_id=sample_entities["faction_id"],
            chapter_id=sample_entities["chapter_id"],
            territory="青云城",
            power_level="强大",
        )

        # Get state at chapter
        state = repo.get_faction_state_at_chapter(
            faction_id=sample_entities["faction_id"],
            chapter_id=sample_entities["chapter_id"],
        )
        assert state is not None
        assert state.territory == "青云城"
        assert state.power_level == "强大"

    def test_get_latest_faction_state(self, repo, sample_entities):
        """Test getting latest faction state."""
        # Create state
        repo.create_faction_state(
            faction_id=sample_entities["faction_id"],
            chapter_id=sample_entities["chapter_id"],
            power_level="强大",
        )

        # Get latest state
        state = repo.get_latest_faction_state(sample_entities["faction_id"])
        assert state is not None
        assert state.power_level == "强大"

    def test_faction_state_not_found(self, repo):
        """Test getting non-existent faction state."""
        state = repo.get_faction_state("non-existent-id")
        assert state is None


class TestLocationState:
    """Test location state tracking (LOC-004)."""

    def test_create_location_state(self, repo, sample_entities):
        """Test creating a location state snapshot."""
        state_id = repo.create_location_state(
            location_id=sample_entities["location_id"],
            chapter_id=sample_entities["chapter_id"],
            controlling_faction="青云门",
            events=["发生了一场战斗"],
            condition="受损",
        )

        assert state_id is not None

        # Verify state was created
        state = repo.get_location_state(state_id)
        assert state is not None
        assert state.location_id == sample_entities["location_id"]
        assert state.chapter_id == sample_entities["chapter_id"]
        assert state.controlling_faction == "青云门"
        assert state.events == ["发生了一场战斗"]
        assert state.condition == "受损"

    def test_get_location_states(self, repo, sample_entities):
        """Test getting location state history."""
        # Create multiple states
        for i in range(3):
            repo.create_location_state(
                location_id=sample_entities["location_id"],
                chapter_id=sample_entities["chapter_id"],
                condition=f"Condition {i}",
            )

        # Get all states
        states = repo.get_location_states(sample_entities["location_id"])
        assert len(states) == 3

    def test_get_location_state_at_chapter(self, repo, sample_entities):
        """Test getting location state at specific chapter."""
        # Create state
        repo.create_location_state(
            location_id=sample_entities["location_id"],
            chapter_id=sample_entities["chapter_id"],
            controlling_faction="青云门",
            condition="完好",
        )

        # Get state at chapter
        state = repo.get_location_state_at_chapter(
            location_id=sample_entities["location_id"],
            chapter_id=sample_entities["chapter_id"],
        )
        assert state is not None
        assert state.controlling_faction == "青云门"
        assert state.condition == "完好"

    def test_get_latest_location_state(self, repo, sample_entities):
        """Test getting latest location state."""
        # Create state
        repo.create_location_state(
            location_id=sample_entities["location_id"],
            chapter_id=sample_entities["chapter_id"],
            condition="受损",
        )

        # Get latest state
        state = repo.get_latest_location_state(sample_entities["location_id"])
        assert state is not None
        assert state.condition == "受损"

    def test_location_state_not_found(self, repo):
        """Test getting non-existent location state."""
        state = repo.get_location_state("non-existent-id")
        assert state is None
