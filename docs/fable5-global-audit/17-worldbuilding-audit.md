# World Builder and Story Bible Audit

Status: `PARTIAL`

## Implemented surfaces

`WorldBootstrapService` and `StoryBibleRepository` expose persisted wizard
steps/snapshots. The schema includes world rules, power systems, characters,
factions, locations, relationships, timeline events, hooks, and story-bible
steps. `guided_setup.py` can build/refine a `StoryProject` and generate a
structured mind-map payload.

## Runtime gaps

* The active writer loads a subset of Story Bible data (`world`, conflict,
  protagonist, power system, voice) in `writing_pipeline.py:229-256`. There is
  no evidence that every saved faction/location/timeline/map/hook mutation is
  translated into the canonical StoryState used by review and fact extraction.
* `generate_mindmap_data()` is a structured payload generator, but the audit did
  not establish editable graph mutations feeding later writes. A renderable
  graph is not a canonical graph store.
* World/document import is asynchronous ingestion; it does not perform the
  required existing-novel deconstruction into chapters, entities, timeline,
  facts, summaries, foreshadowing, and memory.
* Image model routes are configuration seams only. No authorized provider,
  asset manifest, hash/provenance record, or retry/reconcile run was available.

## Test evidence

Phase-7 wizard/story-bible and visualization tests pass as local contract tests.
They do not prove writer consumption after a step edit, cross-chapter state
rebuild, or restore/import equivalence. Those scenarios remain `UNVERIFIED`.

## Verdict

World building is a useful planning surface and `PARTIAL` persistence model.
Treat it as advisory until mutations are committed to one Story System and a
writer/reviewer trace shows exactly which fields were consumed.
