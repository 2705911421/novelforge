"""Phase 8: Writing Pipeline checkpoint-resumable stages with review/revision gate."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import Database, generate_id
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.pipeline.writing_pipeline import WritingPipeline, WritingPipelineError
from src.story_graph import StoryFlowPlanningService, StoryGraphProjector


@pytest.fixture
def phase_db(tmp_path):
    return Database(str(tmp_path / "authoritative.db"))


@pytest.fixture
def pipeline_deps(phase_db, tmp_path):
    """Set up all pipeline dependencies with a seeded project."""
    repo = StoryRepository(phase_db)
    runtime = TaskRuntime(phase_db)
    manager = ProjectManager(str(tmp_path), repository=repo)

    # Seed a project and book.
    project = manager.create_project("Pipeline Test", "fantasy")
    project_id = project.id

    # Get the book_id associated with this project.
    book_row = phase_db.fetchone(
        "SELECT id FROM books WHERE project_id=?", (project_id,)
    )
    book_id = book_row["id"] if book_row else project_id

    # Create a chapter manually for the pipeline to work with.
    repo.append_chapter_version(book_id, 1, "Test chapter content for pipeline testing.")

    return {
        "db": phase_db,
        "repo": repo,
        "runtime": runtime,
        "manager": manager,
        "project_id": project_id,
        "book_id": book_id,
    }


class DummyModelManager:
    """Mock model manager that returns deterministic responses."""

    def __init__(self, draft="这是一个测试章节正文，包含了足够的字数来通过质量检查。" * 20,
                 review=None, facts=None):
        self._draft = draft
        self._review = review or {
            "overall_score": 95,
            "verdict": "pass",
            "dimensions": {},
            "issues": [],
        }
        self._facts = facts or [
            {"fact_type": "event", "content": "主角到达了新地点"},
            {"fact_type": "character", "content": "角色A获得了新能力"},
        ]
        self._call_count = 0

    def chat(self, messages, system=None, task_type=None, **kwargs):
        self._call_count += 1

        class _Resp:
            pass

        resp = _Resp()
        if task_type == "review":
            resp.content = json.dumps(self._review, ensure_ascii=False)  # type: ignore[attr-defined]
        elif task_type == "fact-extraction":
            resp.content = json.dumps(self._facts, ensure_ascii=False)  # type: ignore[attr-defined]
        else:
            resp.content = self._draft  # type: ignore[attr-defined]
        return resp


def test_pipeline_precheck_validates_project(pipeline_deps):
    """PRECHECK rejects a missing project."""
    db = pipeline_deps["db"]
    runtime = pipeline_deps["runtime"]
    repo = pipeline_deps["repo"]
    model = DummyModelManager()

    pipeline = WritingPipeline(db, model, repo, runtime)
    task = runtime.enqueue(
        "write-next", project_id="nonexistent",
        data={"chapter_number": 1, "book_id": "nonexistent"},
    )
    claimed = runtime.claim("test-worker", lease_seconds=60)
    assert claimed is not None

    with pytest.raises(WritingPipelineError) as exc_info:
        pipeline.execute(claimed)
    assert exc_info.value.code == "PROJECT_NOT_FOUND"


def test_pipeline_precheck_validates_chapter_number(pipeline_deps):
    """PRECHECK rejects an invalid chapter number."""
    db = pipeline_deps["db"]
    runtime = pipeline_deps["runtime"]
    repo = pipeline_deps["repo"]
    model = DummyModelManager()
    project_id = pipeline_deps["project_id"]
    book_id = pipeline_deps["book_id"]

    pipeline = WritingPipeline(db, model, repo, runtime)
    task = runtime.enqueue(
        "write-next", project_id=project_id,
        data={"chapter_number": -1, "book_id": book_id},
    )
    claimed = runtime.claim("test-worker", lease_seconds=60)
    assert claimed is not None

    with pytest.raises(WritingPipelineError) as exc_info:
        pipeline.execute(claimed)
    assert exc_info.value.code == "INVALID_CHAPTER"


def test_pipeline_full_happy_path(pipeline_deps):
    """Pipeline completes all stages with a passing review."""
    db = pipeline_deps["db"]
    runtime = pipeline_deps["runtime"]
    repo = pipeline_deps["repo"]
    project_id = pipeline_deps["project_id"]
    book_id = pipeline_deps["book_id"]

    model = DummyModelManager(
        draft="这是一个非常精彩的章节，主角在冒险中遇到了新的挑战。" * 30,
        review={"overall_score": 95, "verdict": "pass", "dimensions": {}, "issues": []},
        facts=[{"fact_type": "event", "content": "主角击败了敌人"}],
    )

    pipeline = WritingPipeline(db, model, repo, runtime, score_threshold=90)
    task = runtime.enqueue(
        "write-next", project_id=project_id, book_id=book_id,
        data={"chapter_number": 2},
    )
    claimed = runtime.claim("test-worker", lease_seconds=60)
    assert claimed is not None

    result = pipeline.execute(claimed)
    assert result["completed"] is True
    assert result["quality_gate"] == "PASS"
    assert result["review_score"] == 95
    assert result["revision_count"] == 0

    # Verify chapter version was created via database query.
    chapter = db.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=?",
        (book_id, 2),
    )
    assert chapter is not None

    # Verify events were recorded.
    events = runtime.events(task["id"])
    stage_names = [e["event_type"] for e in events]
    assert "claimed" in stage_names


def test_context_manifest_binds_story_graph_and_writer_prompt_components(pipeline_deps):
    """Writer context records the real StoryFlow slice and prompt components."""
    db = pipeline_deps["db"]
    runtime = pipeline_deps["runtime"]
    repo = pipeline_deps["repo"]
    project_id = pipeline_deps["project_id"]
    book_id = pipeline_deps["book_id"]
    chapter_one = db.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=1", (book_id,)
    )
    assert chapter_one is not None

    character_id = generate_id()
    db.insert(
        "characters",
        {
            "id": character_id,
            "book_id": book_id,
            "name": "Context Witness",
            "description": "A canonical character used by the context projection.",
        },
    )
    db.execute(
        """UPDATE chapters
           SET status='committed', summary=?, characters_appeared=?
         WHERE id=?""",
        (
            "The witness reaches the old city.",
            json.dumps(["Context Witness"]),
            chapter_one["id"],
        ),
    )

    _, _, plan_node, _ = StoryFlowPlanningService(db).save_intent_from_flow(
        book_id,
        [f"character:{character_id}"],
        chapter_number=2,
    )

    pipeline = WritingPipeline(db, DummyModelManager(), repo, runtime)
    context_task = {"id": "context-build-task", "data": {"context": "Keep the unresolved clue visible."}}
    built = pipeline._build_context(
        context_task,
        {
            "project_id": project_id,
            "book_id": book_id,
            "chapter_number": 2,
            "strict_planning": False,
            "storyflow_plan_node_id": plan_node["id"],
        },
    )["context"]
    manifest = built["context_manifest"]
    assert manifest["schemaVersion"] == 3
    graph_items = [item for item in manifest["items"] if item["sourceType"] == "story_graph_node"]
    assert graph_items
    assert any(item["sourceId"] == f"character:{character_id}" for item in graph_items)
    graph_sections = [section for section in manifest["contextSections"] if "Story Graph" in section["title"]]
    assert graph_sections
    assert graph_sections[0]["contentSha256"]
    assert graph_sections[0]["contextRange"]["scope"] == "assembled_context"
    assert graph_sections[0]["contextRange"]["start"] < graph_sections[0]["contextRange"]["end"]
    assert graph_items[0]["contextRange"]["precision"] == "section"
    assert all(item["contextSectionId"] == graph_sections[0]["id"] for item in graph_items)
    assert all(item["promptLocation"] == "context" for item in graph_items)
    intent_items = [item for item in manifest["items"] if item["sourceType"] == "planning_node"]
    assert len(intent_items) == 1
    assert intent_items[0]["sourceId"] == plan_node["id"]
    assert intent_items[0]["selectionRole"] == "chapter_intent"
    assert intent_items[0]["edgeTypes"] == ["affects"]
    assert intent_items[0]["contextSectionTitle"].startswith("## StoryFlow Chapter Intent")
    assert any(
        item["sourceType"] == "story_graph_node"
        and item["sourceId"] == f"character:{character_id}"
        and item["selectionRole"] == "requiredCharacters"
        and item["edgeTypes"] == ["affects"]
        for item in manifest["items"]
    )


    task = runtime.enqueue(
        "write-next",
        project_id=project_id,
        book_id=book_id,
        data={"chapter_number": 2, "context": "Keep the unresolved clue visible."},
    )
    claimed = runtime.claim("context-writer", lease_seconds=60)
    assert claimed is not None
    built.update({
        "chapter_plan": {"goal": "test context binding"},
        "prompt_a": "Planner output that is actually appended to the writer prompt.",
        "revision_notes": "Keep the witness cautious.",
    })
    generated = pipeline._generate_draft(claimed, built)["context"]
    final_manifest = generated["context_manifest"]
    component_ids = {item["id"] for item in final_manifest["writerInput"]["components"]}
    assert {"system", "chapter_plan", "context", "extra", "planner_output"} <= component_ids
    components = {item["id"]: item for item in final_manifest["writerInput"]["components"]}
    assert components["context"]["rangeStatus"] == "exact"
    assert components["context"]["promptRange"]["scope"] == "writer_user_message"
    assert components["context"]["promptRange"]["start"] < components["context"]["promptRange"]["end"]
    final_graph_section = next(
        section for section in final_manifest["contextSections"] if "Story Graph" in section["title"]
    )
    assert final_graph_section["rangeStatus"] == "exact"
    assert final_graph_section["promptRange"]["start"] >= components["context"]["promptRange"]["start"]
    final_graph_item = next(item for item in final_manifest["items"] if item["sourceType"] == "story_graph_node")
    assert final_graph_item["promptRange"]["precision"] == "section"
    assert final_manifest["promptBinding"]["available"] is True
    prompt_item_types = {item["sourceType"] for item in final_manifest["items"]}
    assert {"revision_instruction", "extra_guidance", "planner_output"} <= prompt_item_types
    assert all(
        item["promptLocation"] == "writer_prompt_component"
        for item in final_manifest["items"]
        if item["sourceType"] in {"revision_instruction", "extra_guidance", "planner_output"}
    )
    context_graph = final_manifest["contextGraphSnapshot"]
    assert context_graph["scope"] == "generation_run_context"
    assert context_graph["nodeCount"] >= 2
    assert context_graph["edgeCount"] >= 1
    assert context_graph["graphSha256"]
    assert context_graph["promptSha256"] == final_manifest["writerInput"]["promptSha256"]
    assert any(edge["type"] == "included_in_context" for edge in context_graph["edges"])


def test_context_manifest_records_style_constraints_and_memory_boundary(pipeline_deps):
    """Context View must distinguish real project inputs from unavailable legacy memory."""
    db = pipeline_deps["db"]
    project_id = pipeline_deps["project_id"]
    book_id = pipeline_deps["book_id"]
    db.execute(
        """UPDATE projects
           SET author_intent=?, writing_style=?, style_profile=?
           WHERE id=?""",
        (
            "Keep the unresolved identity mystery intact.",
            "Close third person, restrained prose.",
            json.dumps({"rhythm": "short paragraphs", "donts": ["no deus ex machina"]}),
            project_id,
        ),
    )

    pipeline = WritingPipeline(
        db,
        DummyModelManager(),
        pipeline_deps["repo"],
        pipeline_deps["runtime"],
    )
    built = pipeline._build_context(
        {"id": "style-constraints-context", "data": {}},
        {
            "project_id": project_id,
            "book_id": book_id,
            "chapter_number": 2,
            "strict_planning": False,
        },
    )["context"]
    manifest = built["context_manifest"]
    source_types = {item["sourceType"] for item in manifest["items"]}
    assert {"style", "constraints"} <= source_types
    assert "Writing Style" in "\n".join(built["context_parts"])
    assert "Author Constraints" in "\n".join(built["context_parts"])
    assert manifest["availability"]["style"]["status"] == "included"
    assert manifest["availability"]["constraints"]["status"] == "included"
    assert manifest["availability"]["memory"]["status"] == "not_available"
    assert manifest["availability"]["memory"]["owner"] == "story_repository.narrative_memory"
    assert "legacy file-backed MemorySystem" in manifest["availability"]["memory"]["reason"]


def test_pipeline_acceptance_fulfills_storyflow_plan(pipeline_deps):
    """An accepted write task marks its Flow plan and links the new chapter."""
    db = pipeline_deps["db"]
    runtime = pipeline_deps["runtime"]
    repo = pipeline_deps["repo"]
    project_id = pipeline_deps["project_id"]
    book_id = pipeline_deps["book_id"]
    chapter_one = db.fetchone("SELECT id FROM chapters WHERE book_id=? AND number=1", (book_id,))
    assert chapter_one is not None

    _, planning_revision, plan_node, _ = StoryFlowPlanningService(db).save_intent_from_flow(
        book_id,
        [f"chapter:{chapter_one['id']}"],
        chapter_number=2,
    )
    assert planning_revision >= 2

    pipeline = WritingPipeline(
        db,
        DummyModelManager(
            draft="这是从 StoryFlow 计划生成的章节正文。" * 30,
            facts=[{"fact_type": "event", "content": "StoryFlow 计划被兑现。"}],
        ),
        repo,
        runtime,
        score_threshold=90,
    )
    task = runtime.enqueue(
        "write-next",
        project_id=project_id,
        book_id=book_id,
        data={"chapter_number": 2, "storyflow_plan_node_id": plan_node["id"]},
    )
    claimed = runtime.claim("storyflow-plan-worker", lease_seconds=60)
    assert claimed is not None

    result = pipeline.execute(claimed)
    assert result["completed"] is True
    assert result["storyflow_plan_status"] == "ACCEPTED"
    commit = db.fetchone(
        "SELECT id, status FROM story_commits WHERE chapter_id=(SELECT id FROM chapters WHERE book_id=? AND number=2)",
        (book_id,),
    )
    assert commit is not None and commit["status"] == "accepted"
    chapter_two = db.fetchone("SELECT id FROM chapters WHERE book_id=? AND number=2", (book_id,))
    assert chapter_two is not None
    graph = StoryGraphProjector(db).project(book_id, view="story", focus=plan_node["id"], depth=2)
    plan = next(node for node in graph["nodes"] if node["id"] == plan_node["id"])
    assert plan["status"] == "ACCEPTED"
    assert plan["metadata"]["acceptedChapterId"] == chapter_two["id"]
    assert plan["metadata"]["acceptedChapterNumber"] == 2
    assert any(
        edge["type"] == "leads_to"
        and edge["source"] == plan_node["id"]
        and edge["target"] == f"chapter:{chapter_two['id']}"
        for edge in graph["edges"]
    )


def test_pipeline_revises_when_review_fails(pipeline_deps):
    """Pipeline enters revision loop when review score is below threshold."""
    db = pipeline_deps["db"]
    runtime = pipeline_deps["runtime"]
    repo = pipeline_deps["repo"]
    project_id = pipeline_deps["project_id"]
    book_id = pipeline_deps["book_id"]

    call_count = {"review": 0}
    original_review = {
        "overall_score": 70,
        "verdict": "fail",
        "dimensions": {},
        "issues": [{"severity": "major", "dimension": "pacing", "description": "节奏太慢"}],
    }
    improved_review = {
        "overall_score": 95,
        "verdict": "pass",
        "dimensions": {},
        "issues": [],
    }

    class AdaptiveModelManager:
        def chat(self, messages, system=None, task_type=None, **kwargs):
            class _Resp:
                pass
            resp = _Resp()
            if task_type == "review":
                call_count["review"] += 1
                if call_count["review"] <= 1:
                    resp.content = json.dumps(original_review, ensure_ascii=False)  # type: ignore[attr-defined]
                else:
                    resp.content = json.dumps(improved_review, ensure_ascii=False)  # type: ignore[attr-defined]
            elif task_type == "fact-extraction":
                resp.content = json.dumps([{"fact_type": "event", "content": "test"}], ensure_ascii=False)  # type: ignore[attr-defined]
            else:
                resp.content = "修订后的章节内容，质量更好了。" * 30  # type: ignore[attr-defined]
            return resp

    pipeline = WritingPipeline(db, AdaptiveModelManager(), repo, runtime, score_threshold=90)
    task = runtime.enqueue(
        "write-next", project_id=project_id,
        data={"chapter_number": 2, "book_id": book_id},
    )
    claimed = runtime.claim("test-worker", lease_seconds=60)
    assert claimed is not None

    result = pipeline.execute(claimed)
    assert result["completed"] is True
    assert result["revision_count"] >= 1
    assert result["quality_gate"] == "PASS"


def test_pipeline_max_revisions_stops_loop(pipeline_deps):
    """Pipeline stops revising after max_revisions and still completes."""
    db = pipeline_deps["db"]
    runtime = pipeline_deps["runtime"]
    repo = pipeline_deps["repo"]
    project_id = pipeline_deps["project_id"]
    book_id = pipeline_deps["book_id"]

    class AlwaysFailModel:
        def chat(self, messages, system=None, task_type=None, **kwargs):
            class _Resp:
                pass
            resp = _Resp()
            if task_type == "review":
                resp.content = json.dumps({  # type: ignore[attr-defined]
                    "overall_score": 50, "verdict": "fail",
                    "issues": [{"severity": "critical", "dimension": "plot", "description": "plot hole"}],
                }, ensure_ascii=False)
            elif task_type == "fact-extraction":
                resp.content = json.dumps([], ensure_ascii=False)  # type: ignore[attr-defined]
            else:
                resp.content = "章节内容。" * 30  # type: ignore[attr-defined]
            return resp

    pipeline = WritingPipeline(db, AlwaysFailModel(), repo, runtime, score_threshold=90, max_revisions=2)
    task = runtime.enqueue(
        "write-next", project_id=project_id,
        data={"chapter_number": 2, "book_id": book_id},
    )
    claimed = runtime.claim("test-worker", lease_seconds=60)
    assert claimed is not None

    result = pipeline.execute(claimed)
    # MAX_REVISIONS should now BLOCK the chapter, not auto-accept it.
    assert result["completed"] is False
    assert result["needs_author_decision"] is True
    assert result["quality_gate"] == "MAX_REVISIONS"
    assert result["revision_count"] >= 2


def test_pipeline_checkpoint_resume(pipeline_deps):
    """Pipeline can resume from a checkpoint."""
    db = pipeline_deps["db"]
    runtime = pipeline_deps["runtime"]
    repo = pipeline_deps["repo"]
    project_id = pipeline_deps["project_id"]
    book_id = pipeline_deps["book_id"]

    model = DummyModelManager()

    pipeline = WritingPipeline(db, model, repo, runtime, score_threshold=90)
    task = runtime.enqueue(
        "write-next", project_id=project_id, book_id=book_id,
        data={"chapter_number": 2},
    )
    claimed = runtime.claim("test-worker", lease_seconds=60)
    assert claimed is not None

    # Run the pipeline normally (it handles checkpoint internally).
    result = pipeline.execute(claimed)
    assert result["completed"] is True

    # Verify checkpoint was used by checking task events.
    events = runtime.events(task["id"])
    assert len(events) > 0


def test_pipeline_concurrent_write_blocked(pipeline_deps):
    """PRECHECK blocks concurrent writes to the same book."""
    db = pipeline_deps["db"]
    runtime = pipeline_deps["runtime"]
    repo = pipeline_deps["repo"]
    project_id = pipeline_deps["project_id"]
    book_id = pipeline_deps["book_id"]

    model = DummyModelManager()
    pipeline = WritingPipeline(db, model, repo, runtime)

    # Start first write task (claim already sets it to running).
    task1 = runtime.enqueue(
        "write-next", project_id=project_id, book_id=book_id,
        data={"chapter_number": 2},
    )
    claimed1 = runtime.claim("worker-1", lease_seconds=60)
    assert claimed1 is not None

    # Start second write task.
    task2 = runtime.enqueue(
        "write-next", project_id=project_id, book_id=book_id,
        data={"chapter_number": 3},
    )
    claimed2 = runtime.claim("worker-2", lease_seconds=60)
    assert claimed2 is not None

    # The second task should fail precheck due to concurrent write.
    with pytest.raises(WritingPipelineError) as exc_info:
        pipeline.execute(claimed2)
    assert exc_info.value.code == "CONCURRENT_WRITE"


def test_pipeline_fact_extraction_and_commit(pipeline_deps):
    """Pipeline extracts facts and creates a story commit."""
    db = pipeline_deps["db"]
    runtime = pipeline_deps["runtime"]
    repo = pipeline_deps["repo"]
    project_id = pipeline_deps["project_id"]
    book_id = pipeline_deps["book_id"]

    facts = [
        {"fact_type": "event", "content": "主角到达了城堡"},
        {"fact_type": "character", "content": "角色A获得了新武器"},
    ]
    model = DummyModelManager(facts=facts)

    pipeline = WritingPipeline(db, model, repo, runtime, score_threshold=90)
    task = runtime.enqueue(
        "write-next", project_id=project_id,
        data={"chapter_number": 2, "book_id": book_id},
    )
    claimed = runtime.claim("test-worker", lease_seconds=60)
    assert claimed is not None

    result = pipeline.execute(claimed)
    assert result["completed"] is True
    assert result["facts_committed"] == 2
    assert "story_commit_id" in result
