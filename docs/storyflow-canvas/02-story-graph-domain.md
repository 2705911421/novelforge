# Story Graph 领域模型

## 权威链路

```text
SQLite authoritative tables
  -> StoryGraphProjector (可重建 read model)
  -> Graph query/filter/focus
  -> StoryFlow Canvas views
```

Graph read model 不拥有故事事实。每个节点和边都带 `source_type`、`source_id` 或 provenance；任何投影都能回溯到表、行、章节或 accepted commit。

## Node schema

```json
{
  "id": "character:<id>",
  "type": "Character",
  "subtype": "minor",
  "title": "人物名",
  "summary": "可读摘要",
  "status": "CANON",
  "project_id": "project-id",
  "book_id": "book-id",
  "source_type": "characters",
  "source_id": "row-id",
  "chapter_id": null,
  "metadata": {},
  "created_at": "...",
  "updated_at": "...",
  "version": 1,
  "confidence": 1.0,
  "provenance": [{"kind": "sqlite", "table": "characters", "id": "row-id"}],
  "ports": {"inputs": [], "outputs": []}
}
```

当前 vertical slice 投影 `Chapter`、`Event`、`Character`、`Faction`、`Location`、`Foreshadow`、`Knowledge` 和 `Fact`；schema 允许 Scene、Item、PlotThread、Secret、StoryGoal、Conflict、TimelinePoint、StoryBibleEntry、Arc、Relationship、PlanningNode 等后续类型。

## Semantic edges

首批边类型：

`happens_before`, `appears_in`, `participates_in`, `happens_at`, `member_of`, `controls`, `allies_with`, `hostile_to`, `suspects`, `trusts`, `knows`, `does_not_know`, `reveals`, `hides`, `causes`, `triggers`, `advances`, `resolves`, `foreshadows`, `depends_on`, `blocks`, `changes`, `affects`, `leads_to`, `planned_for`, `discovered_in`, `mentioned_in`, `contains`, `interacts_with`, `parent_of`。

```json
{
  "id": "edge:<stable key>",
  "type": "appears_in",
  "source": "character:<id>",
  "target": "chapter:<id>",
  "label": "出场于",
  "status": "CANON",
  "weight": 1.0,
  "confidence": 1.0,
  "provenance": [{"kind": "sqlite", "table": "chapters", "id": "chapter-id"}],
  "first_chapter": 3,
  "last_chapter": 7,
  "valid_from": null,
  "valid_to": null,
  "metadata": {}
}
```

## Ports and validation

Ports are present in the read model and are used by the planning-edge validator.
The Canvas now supports output-to-input drag editing: it asks
`GET .../story-graph/edge-options` for legal semantic relations, presents the
author with the choices, and persists only after `POST .../planning/edge` repeats
the same validation under the current workspace revision. This remains a
planning mutation even when both endpoints are canonical nodes.

| Node | Input ports | Output ports |
|---|---|---|
| Chapter | characters, locations, preconditions, plot_threads, foreshadow_in | events, facts, character_changes, relationship_changes, foreshadow_out |
| Character | events, knowledge, relationships, faction, location | actions, state_changes, relationship_changes, knowledge_changes |
| Event | participants, location, chapter, causes | changes, reveals, advances, resolves |
| Location | parent, controlling_faction, presence | events, travel, state_changes |
| Faction | members, location, events | controls, allies, conflicts |
| Foreshadow | planted_by, related_character, related_event | advanced_by, resolves_at |

The validator rejects impossible pairs such as `Character -> happens_before -> Location` and accepts `Chapter -> happens_at -> Location`. Unknown relation text is preserved in metadata and normalized to `interacts_with`, never silently collapsed into an unlabelled edge.

## Status semantics

- `CANON` or `ACCEPTED`: accepted/authoritative or persisted domain record.
- `DRAFT`: mutable chapter or draft projection.
- `PLANNED`: planning workspace data.
- `CANDIDATE`: AI forecast overlay.
- `SUPERSEDED`: invalidated by a later chapter version.
- `STALE`: StoryState or projection requires replay.
- `CONFLICT`: optimistic revision or semantic validation conflict.

## Planning and context boundaries

`PlanningNode` and its semantic planning edges are a durable overlay in the
existing revisioned `plot_workspaces` tables. They are projected into StoryFlow
with `PLANNED` or `CANDIDATE` status and never write `StoryFact`/`StoryState`.
Flow-to-Intent saves the same structured intent into the existing Control Surface
runtime so the writing pipeline can consume it.

Context View first shows a bounded, traceable candidate context. Once the writer
runtime persists a `GenerationRun.input_reference.context_manifest`, it replaces
that candidate list with the actual recorded source list and token accounting;
legacy runs without a manifest remain explicitly marked unavailable.
