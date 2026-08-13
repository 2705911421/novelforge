"""Create a disposable, real SQLite StoryFlow browser fixture.

The fixture is intentionally outside product runtime data. It gives browser
acceptance a deterministic 100+ node book so progressive disclosure, search,
focus, layout persistence, and inspector pagination can be exercised against
the same StoryGraphProjector used by Studio.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import Database  # noqa: E402
from src.llm.model_runtime import PersistentModelRuntime  # noqa: E402
from src.pipeline.writing_pipeline import WritingPipeline  # noqa: E402
from src.planning.story_bible import STORY_BIBLE_STEPS, StoryBibleRepository  # noqa: E402
from src.story_graph.planning import StoryFlowPlanningService  # noqa: E402


def seed(root: Path, chapter_count: int, *, include_health_signals: bool = False) -> dict[str, object]:
    database_path = root / "projects" / "novelforge.db"
    if database_path.exists():
        raise RuntimeError(f"refusing to overwrite existing fixture database: {database_path}")
    database = Database(str(database_path))
    project_id = "storyflow-browser-fixture-project"
    book_id = "storyflow-browser-fixture-book"
    database.insert(
        "projects",
        {
            "id": project_id,
            "name": "StoryFlow browser fixture",
            "genre": "synthetic",
            "description": "Disposable acceptance fixture; not product demo data.",
            "target_chapters": chapter_count,
            "target_volumes": 1,
            "source_kind": "native",
            "migration_status": "native",
        },
    )
    database.insert(
        "books",
        {
            "id": book_id,
            "project_id": project_id,
            "title": f"StoryFlow Fixture · {chapter_count} Chapters",
            "genre": "synthetic",
            "status": "active",
            "total_chapters": chapter_count,
        },
    )

    # Keep the browser fixture truthful for Context View as well: this is a
    # real published Story Bible snapshot, not a hardcoded graph-only node.
    story_bible = StoryBibleRepository(database)
    story_bible.ensure(project_id)
    for step_number, step_key in STORY_BIBLE_STEPS:
        story_bible.save_draft(
            project_id,
            step_key,
            {"summary": f"Fixture Story Bible step {step_number}: {step_key}"},
        )
        story_bible.confirm(project_id, step_key)
    story_bible_snapshot_id = str(story_bible.publish(project_id)["workspace"]["published_snapshot_id"])

    character_names = [f"Fixture Character {index:02d}" for index in range(1, 13)]
    character_ids: list[str] = []
    for index, name in enumerate(character_names, start=1):
        character_id = f"fixture-character-{index:02d}"
        character_ids.append(character_id)
        database.insert(
            "characters",
            {
                "id": character_id,
                "book_id": book_id,
                "name": name,
                "description": f"Synthetic character {index} for progressive disclosure.",
                "goals": f"Trace fixture thread {index % 4 + 1}",
                "importance": "major" if index <= 4 else "supporting",
            },
        )

    location_names = [
        "Fixture World",
        "Fixture Region",
        "Fixture City",
        "Fixture Archive",
        "Fixture Market",
        "Fixture Tower",
        "Fixture Harbor",
        "Fixture Gate",
    ]
    location_ids: list[str] = []
    parent_id: str | None = None
    for index, name in enumerate(location_names):
        location_id = f"fixture-location-{index:02d}"
        location_ids.append(location_id)
        database.insert(
            "locations",
            {
                "id": location_id,
                "book_id": book_id,
                "parent_id": parent_id,
                "name": name,
                "description": f"Synthetic hierarchical location {index}.",
                "type": "world" if index == 0 else "region" if index == 1 else "city" if index == 2 else "site",
            },
        )
        if index < 3:
            parent_id = location_id

    faction_ids = ["fixture-faction-01", "fixture-faction-02", "fixture-faction-03"]
    for index, faction_id in enumerate(faction_ids, start=1):
        database.insert(
            "factions",
            {
                "id": faction_id,
                "book_id": book_id,
                "name": f"Fixture Faction {index:02d}",
                "description": f"Synthetic faction {index}.",
                "goals": f"Control fixture location {index + 2:02d}",
            },
        )

    foreshadow_count = max(12, chapter_count // 8)
    plot_thread_id = "fixture-plot-thread-identity"
    plot_thread_title = "Identity investigation"
    plot_thread_summary = "Trace the missing mark to its source."
    for index in range(1, foreshadow_count + 1):
        created_chapter = min(chapter_count, 1 + (index - 1) * 7)
        resolved_chapter = created_chapter + 21 if created_chapter + 21 <= chapter_count else None
        notes: object = character_names[index % len(character_names)]
        if index == min(13, foreshadow_count):
            notes = {
                "related_characters": [character_ids[index % len(character_ids)]],
                "plot_threads": [
                    {
                        "type": "PlotThread",
                        "id": plot_thread_id,
                        "title": plot_thread_title,
                        "summary": plot_thread_summary,
                    }
                ],
            }
        database.insert(
            "foreshadows",
            {
                "id": f"fixture-foreshadow-{index:03d}",
                "book_id": book_id,
                "created_chapter": created_chapter,
                "resolved_chapter": resolved_chapter,
                "title": f"Fixture Foreshadow {index:03d}",
                "description": "A synthetic lifecycle that remains traceable in Foreshadow View.",
                "status": "resolved" if resolved_chapter else "open",
                "notes": json.dumps(notes, ensure_ascii=False) if isinstance(notes, dict) else notes,
            },
        )

    for number in range(1, chapter_count + 1):
        chapter_id = f"fixture-chapter-{number:04d}"
        character_refs = [character_names[number % len(character_names)], character_names[(number + 3) % len(character_names)]]
        location_name = location_names[3 + (number % (len(location_names) - 3))]
        database.insert(
            "chapters",
            {
                "id": chapter_id,
                "book_id": book_id,
                "number": number,
                "title": f"Synthetic Beat {number:04d}",
                "summary": f"Fixture chapter {number} advances a bounded story subgraph.",
                "status": "committed" if number % 9 else "draft",
                "key_events": json.dumps([f"Fixture event {number:04d}"]),
                "characters_appeared": json.dumps(character_refs),
                "locations_used": json.dumps([location_name]),
            },
        )
        database.insert(
            "timeline_events",
            {
                "id": f"fixture-event-{number:04d}",
                "book_id": book_id,
                "chapter_id": chapter_id,
                "event_time": f"Day {number}",
                "event_type": "fixture",
                "title": f"Fixture Event {number:04d}",
                "description": f"Synthetic event {number}.",
                "characters_involved": json.dumps(character_refs),
                "location": location_name,
                "significance": "browser-fixture",
            },
        )
        if number % 2 == 0:
                database.insert(
                    "story_facts",
                {
                    "id": f"fixture-fact-{number:04d}",
                    "book_id": book_id,
                    "chapter_id": chapter_id,
                    "fact_type": "fixture",
                    "content": f"Fixture fact {number:04d} is accepted for graph traversal.",
                    "entities": json.dumps(character_refs + [location_name]),
                    "confidence": 0.95,
                    "verification_status": "verified",
                    },
                )

    # Explicit typed references exercise the extensible Story Graph read
    # model in browser acceptance. They are real StoryFact rows, not frontend
    # demo nodes, and therefore retain a visible SQLite provenance boundary.
    typed_chapter = min(87, chapter_count)
    typed_event_id = f"fixture-event-{typed_chapter:04d}"
    database.insert(
        "story_facts",
        {
            "id": "fixture-typed-story-evidence",
            "book_id": book_id,
            "chapter_id": f"fixture-chapter-{typed_chapter:04d}",
            "fact_type": "typed_story_evidence",
            "content": "The chapter contains structured scene, item, secret, goal, conflict, timeline, and knowledge references.",
            "entities": json.dumps(
                [
                    {
                        "type": "Scene",
                        "id": "fixture-scene-black-market",
                        "title": "Black Market Deal",
                        "summary": "A controlled exchange in the lower market.",
                    },
                    {
                        "type": "Item",
                        "id": "fixture-item-xuan-token",
                        "title": "Xuan Token",
                        "relation": "owns",
                        "sourceType": "Character",
                        "sourceId": character_ids[0],
                    },
                    {
                        "type": "Secret",
                        "id": "fixture-secret-identity",
                        "title": "The hidden identity",
                        "relation": "reveals",
                        "sourceType": "Event",
                        "sourceId": typed_event_id,
                    },
                    {
                        "type": "StoryGoal",
                        "id": "fixture-goal-investigate",
                        "title": "Investigate the token",
                        "relation": "advances",
                        "sourceType": "Character",
                        "sourceId": character_ids[0],
                    },
                    {
                        "type": "Conflict",
                        "id": "fixture-conflict-wardens",
                        "title": "Wardens close the market",
                        "relation": "causes",
                        "sourceType": "Event",
                        "sourceId": typed_event_id,
                    },
                    {
                        "type": "TimelinePoint",
                        "id": "fixture-time-day-087",
                        "title": f"Day {typed_chapter}",
                    },
                    {
                        "type": "Knowledge",
                        "id": "fixture-knowledge-token",
                        "title": "The witness knows the token's origin",
                        "relation": "knows",
                        "sourceType": "Character",
                        "sourceId": character_ids[0],
                    },
                ],
                ensure_ascii=False,
            ),
            "confidence": 1.0,
            "verification_status": "verified",
        },
    )

    lifecycle_facts = (
        ("fixture-plot-thread-origin", 84, "plot_thread_origin", "planted", "The identity investigation begins."),
        ("fixture-plot-thread-fact", 87, "plot_thread_progress", "advanced", "The identity investigation advances."),
        ("fixture-plot-thread-resolve", 98, "plot_thread_resolved", "resolved", "The identity investigation resolves."),
    )
    for fact_id, chapter_number, fact_type, action, content in lifecycle_facts:
        if chapter_number > chapter_count:
            continue
        database.insert(
            "story_facts",
            {
                "id": fact_id,
                "book_id": book_id,
                "chapter_id": f"fixture-chapter-{chapter_number:04d}",
                "fact_type": fact_type,
                "content": content,
                "entities": json.dumps(
                    [{
                        "type": "PlotThread",
                        "id": plot_thread_id,
                        "action": action,
                        "title": plot_thread_title,
                        "summary": plot_thread_summary,
                    }],
                    ensure_ascii=False,
                ),
                "confidence": 1.0,
                "verification_status": "verified",
            },
        )

    for index, faction_id in enumerate(faction_ids, start=1):
        database.insert(
            "faction_states",
            {
                "id": f"fixture-faction-state-{index:02d}",
                "faction_id": faction_id,
                "chapter_id": f"fixture-chapter-{min(index, chapter_count):04d}",
                "territory": json.dumps([{"location": location_ids[index + 2]}]),
                "power_level": str(50 + index * 10),
            },
        )

    for index, source_id in enumerate(character_ids):
        target_id = character_ids[(index + 1) % len(character_ids)]
        database.insert(
            "relationships",
            {
                "id": f"fixture-relationship-{index:02d}",
                "book_id": book_id,
                "source_type": "character",
                "source_id": source_id,
                "target_type": "character",
                "target_id": target_id,
                "relationship_type": "trusts" if index % 2 == 0 else "hostile_to",
                "description": "Synthetic relationship for Character View.",
                "strength": 4 + index % 6,
            },
        )

    # Keep one explicit Character knowledge boundary in the real SQLite
    # fixture so the Character Inspector can verify known/unknown state
    # without inferring anything from chapter prose.
    database.insert(
        "character_states",
        {
            "id": "fixture-character-state-01-latest",
            "character_id": character_ids[0],
            "chapter_id": f"fixture-chapter-{chapter_count:04d}",
            "location": location_names[3],
            "status": "alert",
            "relationships": json.dumps({character_ids[1]: {"relationship_type": "suspects", "reason": "explicit fixture state"}}),
            "knowledge": json.dumps(
                {
                    "known": [
                        {
                            "content": "The archive key is hidden in the market.",
                            "confidence": 0.91,
                            "sourceChapter": chapter_count,
                        }
                    ],
                    "unknown": [
                        {
                            "content": "Who forged the identity seal.",
                            "confidence": 0.88,
                            "sourceChapter": chapter_count,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "emotional_state": "watchful",
        },
    )

    # Persist one real Chapter Intent so the browser fixture exercises the
    # same revisioned planning overlay and semantic provenance that a writer
    # task receives from StoryFlow. No model is called below.
    _, _, planning_node, _ = StoryFlowPlanningService(database).save_intent_from_flow(
        book_id,
        [
            f"character:{character_ids[0]}",
            f"location:{location_ids[3]}",
            f"foreshadow:fixture-foreshadow-{min(15, foreshadow_count):03d}",
        ],
        chapter_number=chapter_count,
    )

    # Persist one truthful writer trace for the browser acceptance path.  No
    # model is called: this is a disposable GenerationRun record whose
    # manifest points at the same SQLite facts the projector reads.
    provider_id = "storyflow-fixture-provider"
    model_id = "storyflow-fixture-model"
    task_id = "storyflow-fixture-writer-task-0120"
    generation_run_id = "storyflow-fixture-generation-run-0120"
    database.insert(
        "model_providers",
        {"id": provider_id, "name": "StoryFlow fixture provider", "provider_type": "custom"},
    )
    database.insert(
        "models",
        {
            "id": model_id,
            "provider_id": provider_id,
            "name": "StoryFlow fixture model",
            "model_id": "storyflow-fixture",
            "role": "writer",
        },
    )
    database.insert(
        "tasks",
        {
            "id": task_id,
            "type": "write-next",
            "status": "completed",
            "book_id": book_id,
            "chapter_number": chapter_count,
            "data": json.dumps({"fixture": True, "chapter": chapter_count}),
        },
    )
    context_parts = [
        f"## 前文摘要\n第{chapter_count - 1}章 Synthetic Beat {chapter_count - 1:04d}: a bounded prior beat.",
        f"## Story Graph 当前状态（第{chapter_count - 1}章一阶投影；章节状态 committed）\n- 人物：{character_names[chapter_count % len(character_names)]}\n- 地点：{location_names[3 + (chapter_count % (len(location_names) - 3))]}",
        "## 参考资料\n- Retrieval candidate retained only as an excluded manifest source.",
        "## Story Bible 已发布快照\n- Fixture Story Bible is the immutable planning snapshot used by the writer.",
    ]
    storyflow_plan_context = WritingPipeline._build_storyflow_plan_context(
        SimpleNamespace(db=database),
        book_id,
        planning_node["id"],
        chapter_count,
    )
    if storyflow_plan_context.get("text"):
        context_parts.append(str(storyflow_plan_context["text"]))
    context_source_types = [
        ["chapter_summary"],
        ["character", "location"],
        ["rag_chunk"],
        ["story_bible"],
        ["planning_node", "story_graph_node"],
    ]
    context_section_types = context_source_types[: len(context_parts)]
    context_sections = [
        {
            "id": f"context-section:{index}",
            "order": index,
            "title": part.splitlines()[0],
            "contentChars": len(part),
            "contentSha256": hashlib.sha256(part.encode("utf-8")).hexdigest(),
            "sourceTypes": source_types,
            "binding": "exact_context_part",
            "included": True,
        }
        for index, (part, source_types) in enumerate(zip(context_parts, context_section_types))
    ]
    prompt_components = [
        {
            "id": "system",
            "label": "System prompt",
            "location": "system",
            "contentChars": 120,
            "sha256": "fixture-system-sha",
            "binding": "exact_generation_run_input",
        },
        {
            "id": "chapter_plan",
            "label": "Chapter plan",
            "location": "writer_prompt_component",
            "contentChars": 220,
            "sha256": "fixture-plan-sha",
            "binding": "semantic_prompt_component",
        },
        {
            "id": "context",
            "label": "Story context",
            "location": "context",
            "contentChars": sum(len(part) for part in context_parts),
            "sha256": hashlib.sha256("\n\n".join(context_parts).encode("utf-8")).hexdigest(),
            "binding": "exact_context_text_before_prompt_registry",
        },
    ]
    manifest = {
        "schemaVersion": 1,
        "generationRunId": generation_run_id,
        "projectId": project_id,
        "bookId": book_id,
        "chapterNumber": chapter_count,
        "chapterId": f"fixture-chapter-{chapter_count:04d}",
        "items": [
            *(storyflow_plan_context.get("items") or []),
            {
                "sourceType": "story_fact",
                "sourceId": f"fixture-fact-{chapter_count:04d}",
                "label": f"Accepted fact for Ch.{chapter_count}",
                "included": True,
                "contentChars": 180,
                "reason": "verified StoryFact selected for the writer",
                "contextSectionId": "context-section:1",
                "contextSectionTitle": context_sections[1]["title"],
                "promptLocation": "context",
            },
            {
                "sourceType": "character",
                "sourceId": character_ids[chapter_count % len(character_ids)],
                "label": "Current chapter character state",
                "included": True,
                "contentChars": 124,
                "reason": "current chapter character state selected for the writer",
                "contextSectionId": "context-section:1",
                "contextSectionTitle": context_sections[1]["title"],
                "promptLocation": "context",
            },
            {
                "sourceType": "location",
                "sourceId": location_ids[3 + (chapter_count % (len(location_ids) - 3))],
                "label": "Current chapter location",
                "included": True,
                "contentChars": 96,
                "reason": "current chapter location selected for the writer",
                "contextSectionId": "context-section:1",
                "contextSectionTitle": context_sections[1]["title"],
                "promptLocation": "context",
            },
            {
                "sourceType": "rag_chunk",
                "sourceId": f"fixture-rag-excluded-{chapter_count:04d}",
                "label": "Retrieval candidate outside writer budget",
                "included": False,
                "excludedReason": "excluded by the persisted writer context budget",
                "contentChars": 260,
                "reason": "retrieval candidate recorded but excluded by the writer context budget",
                "contextSectionId": "context-section:2",
                "contextSectionTitle": context_sections[2]["title"],
                "promptLocation": "context",
            },
            {
                "sourceType": "story_bible",
                "sourceId": story_bible_snapshot_id,
                "label": "Published Story Bible snapshot",
                "included": True,
                "contentChars": 126,
                "reason": "published planning snapshot selected for the writer",
                "contextSectionId": "context-section:3",
                "contextSectionTitle": context_sections[3]["title"],
                "promptLocation": "context",
            },
        ],
        "contextSections": context_sections,
        "promptComponents": prompt_components,
        "contextChars": sum(len(part) for part in context_parts),
        "writerInput": {
            "promptChars": 920,
            "promptSha256": "storyflow-fixture-prompt-0120",
            "components": prompt_components,
        },
    }
    fixture_context = "\n\n".join(context_parts)
    fixture_plan = "Fixture chapter plan"
    fixture_user_prompt = f"{fixture_plan}\n\n{fixture_context}"
    # Reuse the product binding helpers so browser evidence exercises the same
    # provenance contract as a real Writer run. The fixture still calls no
    # provider; it only stores a deterministic exact input record.
    WritingPipeline._decorate_context_manifest(manifest, context_parts)
    sections_by_id = {
        str(section.get("id")): section
        for section in manifest.get("contextSections", [])
        if isinstance(section, dict) and section.get("id")
    }
    for item in manifest.get("items", []):
        if not isinstance(item, dict):
            continue
        section = sections_by_id.get(str(item.get("contextSectionId") or ""))
        if section and isinstance(section.get("contextRange"), dict):
            item["contextRange"] = {
                **section["contextRange"],
                "precision": "section",
            }
            item["rangeStatus"] = "section"
    WritingPipeline._bind_prompt_ranges(
        manifest,
        fixture_user_prompt,
        {"chapter_plan": fixture_plan, "context": fixture_context},
    )
    manifest["contextGraphSnapshot"] = WritingPipeline._build_context_graph_snapshot(
        manifest,
        focus_node_id=f"chapter:fixture-chapter-{chapter_count:04d}",
    )
    persisted_prompt, prompt_layout = PersistentModelRuntime._build_prompt_layout(
        "Fixture system prompt",
        [{"role": "user", "content": fixture_user_prompt}],
    )
    PersistentModelRuntime._bind_context_manifest_to_prompt_layout(manifest, prompt_layout)
    database.insert(
        "generation_runs",
        {
            "id": generation_run_id,
            "task_id": task_id,
            "agent_role": "writer",
            "provider_id": provider_id,
            "model_id": model_id,
            "prompt_key": "write-next",
            "prompt_version": "fixture-1",
            "input_reference": json.dumps({
                "prompt": persisted_prompt,
                "promptLayout": prompt_layout,
                "prompt_sha256": hashlib.sha256("Fixture system prompt".encode("utf-8")).hexdigest(),
                "persisted_prompt_sha256": hashlib.sha256(persisted_prompt.encode("utf-8")).hexdigest(),
                "context_manifest": manifest,
            }),
            "status": "succeeded",
            "prompt_tokens": 230,
            "completion_tokens": 640,
            "total_tokens": 870,
        },
    )

    # Persist one completed StoryFlow analysis artifact as well. It is
    # explicitly marked as fixture evidence: the browser path exercises the
    # durable task/read model and GenerationRun provenance without claiming a
    # live provider call happened during fixture creation.
    analysis_task_id = "storyflow-fixture-analysis-task-0120"
    analysis_run_id = "storyflow-fixture-analysis-run-0120"
    analysis_node_id = f"chapter:fixture-chapter-{chapter_count:04d}"
    database.insert(
        "tasks",
        {
            "id": analysis_task_id,
            "type": "storyflow-analyze",
            "status": "completed",
            "book_id": book_id,
            "project_id": project_id,
            "data": json.dumps(
                {
                    "node_ids": [analysis_node_id],
                    "analysis_types": ["pace", "next_steps"],
                    "fixture": True,
                },
                ensure_ascii=False,
            ),
            "result": json.dumps(
                {
                    "analysisId": analysis_task_id,
                    "source": "fixture-persisted-report",
                    "selectedNodeIds": [analysis_node_id],
                    "analysisTypes": ["pace", "next_steps"],
                    "generationRunId": analysis_run_id,
                    "summary": "Fixture report proves that a durable StoryFlow analysis can be restored with evidence.",
                    "findings": [
                        {
                            "kind": "pace",
                            "severity": "info",
                            "message": "The selected chapter has a bounded, inspectable evidence neighborhood.",
                            "evidenceNodeIds": [analysis_node_id],
                        }
                    ],
                    "nextSteps": ["Open the selected chapter context to compare writer inputs."],
                },
                ensure_ascii=False,
            ),
        },
    )
    analysis_manifest = {
        "schemaVersion": 1,
        "source": "storyflow.selection",
        "generationRunId": analysis_run_id,
        "selectionNodeIds": [analysis_node_id],
        "items": [
            {
                "sourceType": "story_graph_node",
                "sourceId": analysis_node_id,
                "label": f"Selected chapter {chapter_count}",
                "included": True,
                "contentChars": 96,
                "reason": "author-selected StoryFlow analysis input",
            },
            {
                "sourceType": "character",
                "sourceId": character_ids[0],
                "label": character_names[0],
                "included": True,
                "contentChars": 72,
                "reason": "character selected by the analysis context builder",
            },
            {
                "sourceType": "story_state",
                "sourceId": "fixture-analysis-state",
                "label": "Current fixture story state",
                "included": False,
                "contentChars": 64,
                "reason": "recorded as a candidate but excluded from this analysis input",
                "excludedReason": "analysis context budget",
            },
        ],
        "contextChars": 232,
        "promptBinding": {
            "scope": "planner_user_message",
            "binding": "selection_manifest",
        },
    }
    analysis_manifest["contextGraphSnapshot"] = WritingPipeline._build_context_graph_snapshot(
        analysis_manifest,
        focus_node_id=analysis_node_id,
    )
    database.insert(
        "generation_runs",
        {
            "id": analysis_run_id,
            "task_id": analysis_task_id,
            "agent_role": "planner",
            "provider_id": provider_id,
            "model_id": model_id,
            "prompt_key": "storyflow-analyze",
            "prompt_version": "fixture-1",
            "input_reference": json.dumps(
                {
                    "prompt": "Fixture analysis input; persisted for read-model acceptance only.",
                    "promptLayout": {
                        "scope": "persisted_generation_input",
                        "charCount": 180,
                        "segments": [{"id": "message:0", "contentStart": 48, "contentEnd": 144}],
                    },
                    "persisted_prompt_sha256": "storyflow-fixture-analysis-prompt",
                    "context_manifest": analysis_manifest,
                },
                ensure_ascii=False,
            ),
            "status": "succeeded",
            "prompt_tokens": 72,
            "completion_tokens": 48,
            "total_tokens": 120,
            "latency_ms": 310,
        },
    )

    # Keep one completed forecast task available for the dedicated Candidate
    # Inspector browser path.  It is a persisted fixture artifact, not a live
    # provider claim; the browser applies the returned branch through the same
    # plot-workspace API used by StoryFlow.
    forecast_task_id = "storyflow-fixture-forecast-task-0120"
    forecast_run_id = "storyflow-fixture-forecast-run-0120"
    forecast_node_id = f"chapter:fixture-chapter-{chapter_count:04d}"
    forecast_branch = {
        "id": "fixture-forecast-branch-1",
        "title": "Fixture branch · follow the exposed mark",
        "summary": "A persisted forecast branch for Candidate Inspector acceptance.",
        "plot_points": ["Trace the exposed mark", "Force a choice at the archive"],
        "risks": ["The clue may become stale if Canon changes"],
        "score": 78,
        "narrative": "Fixture-only branch result; no provider call was made while seeding.",
        "candidateSetId": "storyflow-fixture-candidate-set-0120",
        "sourceTaskId": forecast_task_id,
        "generationRunId": forecast_run_id,
        "sourceAnalysisTaskId": analysis_task_id,
        "sourceAnalysisGenerationRunId": analysis_run_id,
    }
    database.insert(
        "tasks",
        {
            "id": forecast_task_id,
            "type": "forecast",
            "status": "completed",
            "book_id": book_id,
            "project_id": project_id,
            "data": json.dumps(
                {
                    "branch_count": 1,
                    "node_ids": [forecast_node_id],
                    "fixture": True,
                },
                ensure_ascii=False,
            ),
            "result": json.dumps(
                {
                    "branches": [forecast_branch],
                    "sourceNodeId": forecast_node_id,
                    "sourceNodeIds": [forecast_node_id],
                    "generationRunId": forecast_run_id,
                    "sourceAnalysisTaskId": analysis_task_id,
                    "sourceAnalysisGenerationRunId": analysis_run_id,
                    "fixture": True,
                },
                ensure_ascii=False,
            ),
        },
    )
    forecast_manifest = {
        "schemaVersion": 1,
        "source": "storyflow.forecast",
        "generationRunId": forecast_run_id,
        "projectId": project_id,
        "bookId": book_id,
        "taskId": forecast_task_id,
        "sourceAnalysisTaskId": analysis_task_id,
        "sourceAnalysisGenerationRunId": analysis_run_id,
        "selectionNodeIds": [forecast_node_id],
        "items": [
            {
                "sourceType": "story_graph_node",
                "sourceId": forecast_node_id,
                "label": f"Selected chapter {chapter_count}",
                "included": True,
                "contentChars": 128,
                "reason": "fixture selected StoryFlow node",
            },
            {
                "sourceType": "plot_workspace_graph",
                "sourceId": f"book:{book_id}",
                "label": "Fixture planning canvas",
                "included": True,
                "contentChars": 420,
                "reason": "fixture visible planning canvas",
            },
            {
                "sourceType": "storyflow_analysis",
                "sourceId": analysis_task_id,
                "label": "Prior StoryFlow analysis",
                "included": True,
                "contentChars": 180,
                "reason": "forecast derived from the persisted StoryFlow analysis task",
            },
        ],
        "contextChars": 548,
    }
    forecast_manifest["contextGraphSnapshot"] = WritingPipeline._build_context_graph_snapshot(
        forecast_manifest,
        focus_node_id=forecast_node_id,
    )
    database.insert(
        "generation_runs",
        {
            "id": forecast_run_id,
            "task_id": forecast_task_id,
            "agent_role": "planner",
            "provider_id": provider_id,
            "model_id": model_id,
            "prompt_key": "forecast",
            "prompt_version": "fixture-1",
            "input_reference": json.dumps(
                {
                    "prompt": "Fixture forecast input; persisted for read-model acceptance only.",
                    "promptLayout": {
                        "scope": "persisted_generation_input",
                        "charCount": 420,
                        "segments": [{"id": "message:0", "contentStart": 48, "contentEnd": 420}],
                    },
                    "persisted_prompt_sha256": "storyflow-fixture-forecast-prompt",
                    "context_manifest": forecast_manifest,
                },
                ensure_ascii=False,
            ),
            "status": "succeeded",
            "prompt_tokens": 140,
            "completion_tokens": 220,
            "total_tokens": 360,
            "latency_ms": 280,
        },
    )

    health_signal_ids: dict[str, str] = {}
    if include_health_signals:
        # These are explicit SQLite rows for browser acceptance of the
        # read-only Story Health panel. They are intentionally opt-in so the
        # general density fixture remains stable for existing evidence.
        inactive_character_id = "fixture-health-inactive-character"
        database.insert(
            "characters",
            {
                "id": inactive_character_id,
                "book_id": book_id,
                "name": "Dormant Witness",
                "description": "Acceptance fixture character with no chapter appearance record.",
                "importance": "supporting",
            },
        )
        health_foreshadow_id = "fixture-health-foreshadow"
        database.insert(
            "foreshadows",
            {
                "id": health_foreshadow_id,
                "book_id": book_id,
                "created_chapter": min(45, chapter_count),
                "title": "The dormant signal",
                "description": "An explicitly planted hook with no later advance or resolution.",
                "status": "open",
            },
        )
        health_thread_id = "fixture-health-plot-thread"
        health_thread_chapter = min(80, chapter_count)
        database.insert(
            "story_facts",
            {
                "id": "fixture-health-plot-thread-origin",
                "book_id": book_id,
                "chapter_id": f"fixture-chapter-{health_thread_chapter:04d}",
                "fact_type": "plot_thread_origin",
                "content": "The dormant identity thread begins.",
                "entities": json.dumps(
                    [{
                        "type": "PlotThread",
                        "id": health_thread_id,
                        "title": "Dormant identity thread",
                        "summary": "An explicit acceptance fixture plot line with no later progress.",
                        "action": "planted",
                    }],
                    ensure_ascii=False,
                ),
                "confidence": 1.0,
                "verification_status": "verified",
            },
        )
        health_signal_ids = {
            "characterId": inactive_character_id,
            "foreshadowId": health_foreshadow_id,
            "plotThreadId": health_thread_id,
        }

    return {
        "root": str(root),
        "database": str(database_path),
        "projectId": project_id,
        "bookId": book_id,
        "chapterCount": chapter_count,
        "contextChapterId": f"fixture-chapter-{chapter_count:04d}",
        "generationRunId": generation_run_id,
        "analysisTaskId": analysis_task_id,
        "analysisRunId": analysis_run_id,
        "forecastTaskId": forecast_task_id,
        "forecastRunId": forecast_run_id,
        "forecastNodeId": forecast_node_id,
        "forecastBranch": forecast_branch,
        "healthSignals": health_signal_ids,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="empty disposable NOVELFORGE_ROOT directory")
    parser.add_argument("--chapters", type=int, default=120)
    parser.add_argument("--health-signals", action="store_true", help="add explicit read-only Story Health acceptance rows")
    args = parser.parse_args()
    if args.chapters < 100:
        raise SystemExit("--chapters must be at least 100 for the browser acceptance fixture")
    args.root.mkdir(parents=True, exist_ok=True)
    print(json.dumps(seed(args.root.resolve(), args.chapters, include_health_signals=args.health_signals), ensure_ascii=False))


if __name__ == "__main__":
    main()
