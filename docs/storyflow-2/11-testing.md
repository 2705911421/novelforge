# Testing Strategy

## Current local browser evidence (2026-08-20)

The disposable 100-chapter browser fixture exercised the complete local
23-step author workflow: immutable WORLD snapshot, Agent roster/configuration,
run lifecycle, live timeline, Agent evidence, pause/intervention/resume,
branch/fork/compare, grounded Analyst, Character Chat, mixed Character and
Faction Survey, Adoption, Planning Overlay, ChapterIntent, durable
`write-next`, refresh recovery, and the post-StoryCommit Canon transition.
Without a configured live provider, Chat and Survey deliberately persisted
`PROVIDER_UNAVAILABLE` responses; this is fail-closed evidence, not fabricated
Agent output. The deterministic acceptance worker then processed the queued
author task through review/fact extraction and an accepted StoryCommit.

The fixture Canon hash stayed
`4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` before and
through Simulation, Adoption, and queueing. It changed only after the
accepted write to `2927a3c35e9d57aab719af3c0d09f6c6f8f468edd1a07ad512d0ae33fe079d7e`;
the run's immutable snapshot was then reported as `STALE`. The browser showed
`write-next completed · 100%`, review `95/PASS`, one committed fact, zero
console errors/warnings, no `[object Object]` output, and no duplicate event
IDs after refresh.

The same pass found and fixed a real structured Faction `territory` bug in
`PerceptionBuilder`; the regression now covers list-shaped territory and
mixed Faction Survey responses. Focused StoryFlow/Phase 1/writing tests are
`129 passed`, and the protected `STORY-001` and `WRITE-001` gates are
`VERIFIED`. This does not close real external-provider browser E2E or
production-scale performance evidence. The current deterministic benchmark
rerun completed 200/1,250/5,000 events at `287.713/100.279/33.654` events per
second with stable hashes and `canonicalMutation=false`; it is a core-ledger
baseline, not a provider-backed production SLA.
A full repository rerun after the hard-bound Context Compiler fix completed
`1024 passed in 16:59`; this current-tree result supersedes the older historical
`1023 passed` note below.

Required invariants are Canon immutability, knowledge isolation, branch
isolation, deterministic replay, crash recovery, and Simulation-to-Planning
boundary. Tests must cover happy path, failure path, persistence, and recovery;
browser acceptance must exercise the real Studio API and refresh recovery.

Scheduler/cost coverage additionally asserts deterministic Tier A/B/C
selection, persisted `whyActivated` rows, GenerationRun usage reconciliation
without double charging, `PAUSED_BUDGET`, and author budget increase/resume.
Causal coverage asserts append-only causal rows, bounded evidence references,
API/analyst exposure, and `canonicalMutation=false`. The deterministic runtime
benchmark command is `python scripts/benchmark_storyflow_simulation.py --json`
and covers 10 agents/20 rounds, 25 agents/50 rounds, and 50 agents/100 rounds;
it reports durable event counts, state hashes, elapsed time, and explicitly
labels its core-ledger mode as not provider E2E. The latest run measured
311.826/106.758/36.515 events per second for the three required scales.

Provider-routing coverage also exercises run-scoped Agent Decision, Memory,
Embedding, Analyst, and Character Chat assignments through the persistent Model
Router, asserting GenerationRun/Attempt, task ownership, idempotent retry, and
simulation cost-ledger/context evidence plus Agent-local context redaction.
The Analyst/Chat/Survey HTTP paths enqueue `simulation-*` tasks and execute them
through the targeted lease-fenced worker; real external-provider E2E remains an
explicit separate gate.
The writing integration suite also drives an adopted Simulation proposal through
ChapterIntent, `write-next`, review/fact extraction, and an accepted StoryCommit;
the browser handoff remains Canon-read-only until that writing task is run.
The focused Canon-boundary proof runs 100 sandbox rounds, intervention,
branching, interaction, analysis/report, adoption, and ChapterIntent, then
compares a digest of the canonical fact/state/event/commit tables before and
after the workflow.

Repeat-run coverage creates one explicit cohort, reconstructs each Sandbox,
and groups only exact final-state hashes into Outcome Clusters. It asserts that
the result exposes structural labels and an explicit `probabilityClaim=false`
marker. History coverage archives and restores a run through migration 41's
append-only lifecycle ledger while confirming the run and its Sandbox evidence
remain queryable.

The provenance hardening tests additionally cover the explicit `PREPARING`
state and persisted failure/reload, location-scoped event visibility with
direct-message exceptions, migration 42 branch fork hashes, intervention
authors and typed intervention-kind validation. Snapshot-boundary coverage
also verifies revisioned Planning references and the recorded faction,
location, plot, secret, goal, conflict, item, and narrative-obligation fields.
Migration 43 verifies structured Adoption provenance survives reload and is
carried into ChapterIntent; migration 44 verifies base Canon and branch
lineage on SimulationRun. The Timeline API/UI exposes persisted state deltas
and expandable causal evidence. These records remain Sandbox/Planning-overlay
metadata and carry `canonicalMutation=false` at the API boundary.
The Adoption workspace acceptance also verifies that proposal creation reloads
from durable records and that UI edits preserve the structured source payload
rather than silently dropping provenance fields.

A fresh current-code browser run against an isolated copy of the real book also
exercises the complete sandbox handoff through branch comparison, grounded
Analyst evidence, Character Chat/Survey failure paths, Adoption, the Planning
node, ChapterIntent, and the durable `write-next` queue. Reload recovery
restores the branch tree, events, adopted proposal, Planning node, and controls;
the fresh browser console reports zero errors or warnings. The acceptance
server intentionally disables its worker and has no configured decision
provider, so the queued writing task and `PROVIDER_UNAVAILABLE` interaction
results are explicit external limitations rather than claimed prose or Canon
mutations.

ChapterIntent refresh recovery is separately checked by reading only the
proposal-linked `control/runtime/chapter-*.intent.json` documents. The API
returns the persisted intent and the HISTORY card renders its chapter/status
after reload; the focused regression asserts its provenance still points to
the adopted proposal.

The same refresh check filters durable `write-next` tasks by their persisted
`simulation_adoption_id` and renders the task id/status/progress in HISTORY.
This prevents a queued handoff from appearing as an untracked toast while
keeping the disabled worker and provider limitations explicit.
An isolated deterministic-worker probe also confirms the pre-write author gate
is fail-closed: an uncommitted previous chapter yields
`PREVIOUS_CHAPTER_NOT_COMMITTED`, no model call/StoryCommit, and no Canon
digest change; the error is surfaced in the same durable task summary.
The browser renders that error and the retry control from the same persisted
task row rather than treating the request as successful.
After an author retry requeues the task, the browser distinguishes the live
`queued` state from the prior `last error` detail and hides the terminal retry
control until a new failure is persisted.

Survey interaction coverage also reads the durable survey detail as a bounded
scenario handoff. Starting that scenario forks the source run at its latest
event boundary, embeds the question/Agent responses in the child configuration,
and asserts the source Sandbox and canonical digest are unchanged. Intervention
history coverage reads the append-only `simulation_interventions` rows through
the book-scoped API and confirms the UI can recover kind, rationale, state
delta, event ID, and author after refresh with `canonicalMutation=false`.

An isolated disposable copy also covers the positive writing handoff. After a
deterministic acceptance worker commits the prerequisite chapter, the adopted
ChapterIntent 15 `write-next` task completes through review, fact extraction,
and StoryCommit; a fresh Studio session reloads HISTORY and shows the durable
`completed`/`100%` task, the Planning node, and the ChapterIntent. The run API
continues to report the immutable pre-write snapshot hash and
`canonicalMutation=false`, while its freshness is truthfully `STALE` because
the accepted StoryCommits advanced current Canon. This is deterministic
acceptance evidence only; no live external-provider quality or authorization is
claimed.

The provider-boundary regression also returns an unknown provider action as a
durable validator rejection (`unsupported action type: TELEPORT`) while the
round task remains auditable and no sandbox actor event is written. The Studio
round selector now lists all 29 typed actions, and the Analyze workspace's
persisted graph projection is rendered as a bounded deterministic SVG with
click-to-inspect nodes. A fresh browser pass found the graph, 36 visible
nodes, all action options, and zero console errors; this is local projection
evidence rather than a claim of complete graph virtualization or live provider
execution.

The WORLD workspace now renders a read-only inventory of every snapshot-backed
entity collection, including counts for facts, foreshadows, rules, goals,
conflicts, items, and other non-Agent narrative records. The graph projector
uses a versioned ontology and rebuilds stale caches; a real-book browser/API
probe observed 851 persisted nodes, 801 of them typed narrative entities, with
the UI bounded to 36 visible nodes and no console errors. These records remain
Sandbox read-model evidence with `canonicalMutation=false`.

The scheduler regression now proves that a persistent Tier C goal does not by
itself create a provider slot; an explicit `activateOnGoal` policy and genuine
Sandbox event signals still activate the Agent with durable reasons. The graph
projection was bumped to version 3 and its focused tests cover replayed
location, knowledge, relationship, control, and narrative-reference edges as
well as stale-cache rebuild. The live EventSource path refreshes that graph
read model after a persisted event. These checks strengthen governance and
observability but do not establish external-provider E2E or production-scale
GPU virtualization.

The final fresh headed pass against an isolated copy of the real book observed
`0 active / 29 discovered` in the default Tier C scheduler, then injected one
persisted `TALK` round through the API while the WORLD workspace was open. The
EventSource path updated the graph from `851/0/sequence-0` to
`851/1/sequence-1` (nodes/edges/sequence) without page reload; run evidence
remained `canonicalMutation=false` and `snapshotFreshness=CURRENT`, and the
browser console reported zero errors and warnings.

The event-detail regression now replays a persisted event and verifies actor
before/after state, agent-local memory, pre-event perception context, causal
why evidence, state-delta hashes, related graph changes, cross-run rejection,
and unchanged Canon facts. A fresh headed pass clicked the persisted TALK
event after navigation and after reload; all six inspector sections loaded
from the API with zero console errors or warnings. The same pass edited a
READY run's structured Environment Setup, observed HTTP 200, and verified the
normalized configuration after reload; RUNNING and terminal edits remain
server-rejected.

The final current-tree verification is `1010 passed in 15:40`; the protected
feature verifier reports all five gates `VERIFIED`. Ruff, JavaScript syntax,
compileall, protected-file, and `git diff --check` all pass. The deterministic
runtime benchmark reports 200/1,250/5,000 events at 221.626/84.263/32.237
events per second, with completed runs and `canonicalMutation=false`; it uses
the deterministic runtime provider only and is not a production-scale or
external-provider performance claim.

The replay regression now calls the explicit
`SimulationRepository.rebuild_simulation_state()` seam after deleting the
derived graph node/edge/meta rows. The rebuilt state hash matches the original
ledger replay, and the graph projector recreates its read model with the same
hash. The API check for
`GET /api/v1/books/{book_id}/simulation/runs/{run_id}/replay` also confirms
book scoping, `rebuildable=true`, and `canonicalMutation=false`.

## Current local verification after migration 45

After migration 45 and the snapshot-idempotence fix, the full repository
regression completed `1014 passed in 16:19`. The current benchmark command
completed 200/1,250/5,000 events at 252.568/90.539/32.360 events per second;
all three runs were `COMPLETED` with stable state hashes and
`canonicalMutation=false`. The benchmark still uses
`deterministic-runtime-only` and a core-ledger projection, so it is not a
provider-backed latency or production-scale virtualization gate.

The latest focused regression also covers deterministic configuration
generation from an immutable snapshot, preservation of custom/budget fields,
idempotent repeated snapshot creation at one Canon boundary, migration 45
soft-delete retention, explicit branch-comparison dimensions, token-budget
context evidence, report sections, and graph `SIMULATION` run/round metadata.
The isolated headed browser pass created and reloaded generated Environment
Setup, created a report and clicked its event evidence both after reload and
after local report refresh, and created then soft-deleted a disposable DRAFT
run. The delete response was HTTP 200 with `history.deleted=true` and
`canonicalMutation=false`; the default run list no longer contained that id.
Post-fix browser console checks reported zero errors and warnings. A duplicate
snapshot attempt initially returned HTTP 500 and was retained as a discovery
record; after the idempotent repository/API fix the same action returned HTTP
200 and the persisted snapshot id.

## Authorized provider runtime increment

The configured MiMo route was verified through the durable Model Runtime on a
copied SQLite database. The connection task completed successfully. A real
provider simulation then ran 3 pinned Agents for 10 rounds: all 10 round tasks
completed, the run reached `COMPLETED`, 30 GenerationRuns/Attempts and 30
Simulation cost rows were persisted, and the Canon hash was unchanged. A
follow-up context check produced valid actions for all three Agents. Separate
durable real-provider tasks also completed Character Chat (`ANSWERED`), a
three-Agent Survey (`COMPLETED`, three responses), and an Analyst query with
`providerId=xiaomimimo`; each result recorded `canonicalMutation=false`.
The source database was not used for these writes. Full 23-step browser
provider acceptance and production-scale performance remain open.

## Final current-tree verification

The full repository regression completed `1016 passed in 16:54`. The focused
StoryFlow/world/persistence/writing regression completed `121 passed`. The
protected feature verifier reports CW-001, MEMORY-001, REVIEW-001, STORY-001,
and WRITE-001 as `VERIFIED`. Ruff, JavaScript syntax, compileall,
`check_protected_files.py`, and `git diff --check` all pass.

A fresh isolated headed Studio session created a provider-assigned run,
persisted DRAFT/READY/RUNNING transitions, executed a typed WAIT round, and
queued a durable `simulation-round` task. After reload, the run displayed the
completed task, `RUNNING 1/2`, provider assignment, and
`canonicalMutation=false`; the clean session reported zero console
errors/warnings and no failed HTTP requests. This is browser evidence for the
implemented local workflow and provider configuration boundary, not a claim
that the complete external-provider acceptance matrix or production-scale
gate is closed.

The latest deterministic runtime benchmark covers 10 agents/20 rounds,
25 agents/50 rounds, and 50 agents/100 rounds. It persisted 200/1,250/5,000
events at 127.678/54.297/28.981 events per second respectively; all runs
completed with stable state hashes and `canonicalMutation=false`. This is a
core-ledger deterministic benchmark and does not close the real-provider or
production-scale virtualization gate.

The final headed-browser UI pass selected `Provider Agent decision`, confirmed
the synchronous execute control is disabled and `Queue provider round` is
shown, then switched to HISTORY and created a ChapterIntent from a persisted
ADOPTED proposal. The response was HTTP 200 and the refreshed card showed
`ChapterIntent 15 · PLANNED`; console errors and warnings were both zero and
the request log contained no 4xx/5xx responses. Artifact:
`output/playwright/storyflow-provider-ui-final.png`.

The subsequent UI regression verified the multi-Agent provider picker against
the same isolated copy: the active scheduler slot was preselected and marked,
the listbox became enabled only for `Provider Agent decision`, and the durable
queue action remained the only provider execution path. The browser session had
zero console errors/warnings; screenshot:
`output/playwright/storyflow-provider-multi-agent-ui.png`.

The required deterministic benchmark was rerun after the scheduler
configuration-boundary repair: 10/20 completed 200 events at 315.047 events/s,
25/50 completed 1,250 at 55.415 events/s, and 50/100 completed 5,000 at 17.390
events/s. Every run had a stable state hash, `runStatus=COMPLETED`, and
`canonicalMutation=false`. These are machine-dependent results for the exact
reference benchmark scope, not Provider latency or a production-scale
virtualization claim.

After the roster evidence repair, a disposable current-code Studio smoke
rendered the real Agent roster and confirmed visible per-card `stateHash` plus
`canonicalMutation=false`; the Provider picker was present and disabled in
explicit mode. Backend responses observed by the browser were HTTP 200 and no
page errors occurred. Artifact:
`output/playwright/storyflow-roster-statehash-smoke.png`. Two blocked external
resource requests came from the local network policy, so this smoke is not
reported as a zero-console acceptance pass.

The final current-tree regression then completed `1018 passed in 14:50`, with
the focused StoryFlow/world/persistence/writing set at `123 passed`. The
protected feature verifier completed all five gates (`CW-001`, `MEMORY-001`,
`REVIEW-001`, `STORY-001`, and `WRITE-001`) as `VERIFIED`; static checks and
protected-file checks passed as well. The scheduler nested-policy repair and
unknown pinned-Agent fail-closed regression are included in this evidence.

## Current audit verification

After the knowledge, communication, typed-action, and Studio STOP/hydration
repairs, the full repository completed `1023 passed in 17:14` (one benign
Windows pytest cache warning). The focused StoryFlow/world/persistence/writing
set completed `128 passed` before the final JavaScript-only hydration change;
the repository run includes all Python regressions. The protected verifier
completed all five gates as `VERIFIED`; Ruff, `node --check`, compileall,
protected-file, and diff checks passed.

The required deterministic benchmark completed 10 agents/20 rounds, 25/50,
and 50/100 with 200/1,250/5,000 durable events at 199.847/68.101/24.976
events per second. Every run reached `COMPLETED`, had a stable replay hash,
and reported `canonicalMutation=false`; the provider is explicitly
`deterministic-runtime-only`. A fresh headed local browser session loaded the
current Studio bundle, durable run and 29-agent roster, STOP control, and
persisted HISTORY adoption/ChapterIntent surface with zero fresh console
errors/warnings. This remains local browser evidence, not the full external
provider/23-step/production-scale gate.
