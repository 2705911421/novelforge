# NovelForge Implementation Progress

> **StoryFlow 2.0 current browser-gate and perception hardening increment
> (2026-08-20)**: A disposable 100-chapter fixture completed the local
> browser workflow from WORLD snapshot through DRAFT/READY/RUNNING/PAUSED,
> WAIT/intervention, branch execution/comparison, grounded Analyst, Character
> Chat, mixed Character+Faction Survey, explicit Adoption, Planning Overlay,
> ChapterIntent, and durable `write-next`. The first mixed Survey exposed a
> real `territory`-list perception bug (`unhashable list`); structured
> location normalization now covers snapshot context, nearby entities, and
> event visibility, and the regression suite passes. With no live provider,
> Chat/Survey persist `PROVIDER_UNAVAILABLE` fail-closed evidence; after the
> deterministic acceptance worker processed the queued author task, Chapter
> 101 reached `StoryCommit=accepted`, review `95/PASS`, one fact, and the
> HISTORY card showed `write-next completed · 100%`. Canon remained at
> `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` through
> simulation, adoption, and queueing, then changed only after the accepted
> StoryCommit to `2927a3c35e9d57aab719af3c0d09f6c6f8f468edd1a07ad512d0ae33fe079d7e`;
> the immutable run correctly became `STALE`. The fresh browser session had
> zero console errors/warnings, no `[object Object]` rendering, and unique
> event IDs after refresh. Focused StoryFlow/Phase 1/writing tests are
> `128 passed`; STORY-001 and WRITE-001 are `VERIFIED`; static/protected-file
> checks pass. This is deterministic local acceptance evidence; the real
> external-provider matrix and production-scale gate remain **PARTIAL**.

> **StoryFlow 2.0 survey-scenario and intervention-history increment
> (2026-08-20)**: Survey detail now exposes a bounded, persisted scenario
> payload (question, selected Agent IDs, responses, and source evidence), and
> `POST .../simulation/surveys/{survey_id}/run` forks the source Sandbox at its
> latest event boundary into a new `READY` run. The child stores the scenario
> in run configuration and retains branch lineage; the source run and Canon
> remain unchanged. Simulation HISTORY/SIMULATE now also reads durable author
> interventions, showing kind, rationale, delta, event ID, and author after
> refresh. The focused Survey, intervention, and lifecycle API regressions
> pass; the combined StoryFlow/Phase 1/writing integration suite is `128
> passed`, Ruff, JavaScript syntax, compileall, protected-file, and diff checks
> pass. These additions close local traceability seams but do not claim the
> external-provider matrix, full browser acceptance, or production-scale
> performance gate; product status remains **PARTIAL**.

> **StoryFlow 2.0 final audit increment (2026-08-20)**: The pre-this-increment
> full repository regression completed `1023 passed in 17:14`; all five protected feature
> gates (`CW-001`, `MEMORY-001`, `REVIEW-001`, `STORY-001`, `WRITE-001`) are
> `VERIFIED`. The audit closed four sandbox-boundary seams: Snapshot-derived
> Agent knowledge now reaches only the selected Agent scope; INFORM/DECEIVE/
> DISCLOSE_SECRET/SEND_MESSAGE propagate only explicit information to target
> knowledge and recipient episodic memory; common typed actions (movement,
> inventory, relationships, alliances, and goals) have deterministic
> Sandbox state semantics; and MOVE/FLEE validation accepts the destination
> location while other actions remain location-bound. Faction UNKNOWN
> knowledge no longer falls back into visible context. The Studio bundle also
> exposes a durable STOP control and hydrates scheduler/adoption/graph/report/
> interaction panels concurrently from backend records. Ruff, JavaScript
> syntax, compileall, protected-file, and diff checks pass. The exact
> deterministic benchmark persisted 200/1,250/5,000 events at
> 311.826/106.758/36.515 events per second, with stable hashes,
> `runStatus=COMPLETED`, and `canonicalMutation=false`. A fresh headed local
> browser session loaded the current bundle, durable runs, 29-agent roster,
> STOP control, HISTORY adoption/ChapterIntent surface, and zero fresh
> console errors/warnings. External-provider full matrix, production-scale
> virtualization, and the complete 23-step browser gate remain unclaimed;
> the current increment's focused StoryFlow/Phase 1/writing suite is `128
> passed`; product status stays **PARTIAL**.

> **StoryFlow 2.0 full browser workflow evidence increment (2026-08-20)**:
> A fresh Playwright session against current Studio code and an isolated copy of
> the real book exercised the end-to-end sandbox handoff: immutable snapshot,
> DRAFT/READY/RUNNING/PAUSED transitions, persisted WAIT and author
> intervention events, branch fork and branch-only state, outcome comparison,
> grounded Analyst query, Character Chat and Survey failure evidence,
> author Adoption, Planning node, ChapterIntent creation, and the durable
> `write-next` queue. The branch run was
> `438ec0146c444e68b3bfbed4ffe87865`; its Canon hash stayed
> `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` before
> and after the workflow, with `canonicalMutation=false`. Reloading the page
> restored the branch tree, events, adopted proposal, Planning node, and
> controls; a fresh console check reported zero errors or warnings. The
> queued writing task remains durable and queued because this acceptance server
> intentionally disabled the worker; Character Chat/Survey correctly record
> `PROVIDER_UNAVAILABLE` without inventing answers. Automated writing-to-
> StoryCommit coverage remains the authoritative Canon-mutation proof. Product
> status remains **PARTIAL**.

> **StoryFlow 2.0 ChapterIntent refresh visibility increment (2026-08-20)**:
> The Adoption list now reads only the durable, author-owned
> `control/runtime/chapter-*.intent.json` documents whose provenance names the
> proposal, and exposes them as `chapterIntents` without touching Canon. The
> HISTORY card renders the persisted chapter/status after refresh; the focused
> API regression and a fresh browser reload both verified ChapterIntent 15 and
> zero console errors. Product status remains **PARTIAL**.

> **StoryFlow 2.0 durable write-next visibility increment (2026-08-20)**:
> Adoption records now expose book-scoped `write-next` task summaries selected
> by their persisted `simulation_adoption_id`. The HISTORY card renders the
> durable task id, status, and progress after refresh; the isolated browser
> pass showed `write-next queued · task 402a1e0accc241cc89a4f8113af7a24c ·
> 0%` with zero console errors. The acceptance worker stayed disabled, so no
> prose or Canon mutation is claimed from this queued task. Product status
> remains **PARTIAL**.

> The same deterministic worker was then run against that isolated queue as an
> adversarial recovery check. The existing author gate rejected chapter 15
> with `PREVIOUS_CHAPTER_NOT_COMMITTED` (`previous chapter 14 is not committed:
> draft`); the task became `failed`, no model call or StoryCommit occurred, and
> the Canon digest was unchanged. HISTORY now renders the durable error beside
> the task status so this gate is actionable rather than hidden.

> The failed-task path is also browser-visible: the HISTORY card renders the
> persisted `PREVIOUS_CHAPTER_NOT_COMMITTED` reason and exposes the existing
> durable task retry control; the isolated browser reports zero console errors.

> **StoryFlow 2.0 retry-state truthfulness increment (2026-08-20)**: The
> HISTORY write-next evidence now distinguishes a terminal
> `failed/needs_author_decision` task from a requeued task that still carries
> the previous failure detail. Requeued work is shown as `queued` with a
> `last error` annotation and no duplicate retry control; terminal failures
> retain the actionable Retry button. A fresh browser session against the
> isolated backend rendered `write-next queued` plus `last error` after retry,
> with zero console errors or warnings. Focused StoryFlow, writing-integration,
> and persistence tests are `110 passed`; all five protected feature gates
> remain `VERIFIED`. Product status remains **PARTIAL**.

> **StoryFlow 2.0 deterministic browser writing handoff increment
> (2026-08-20)**: A disposable copy of the browser acceptance database was
> processed with the repository's deterministic acceptance model after the
> author gate was satisfied. The existing adopted proposal and ChapterIntent
> 15 task `402a1e0accc241cc89a4f8113af7a24c` reached `completed`, `100%`,
> review score `95`, quality gate `PASS`, one committed fact, and an accepted
> `StoryCommit`; the prerequisite chapter 14 was also committed by the same
> deterministic worker. A fresh Studio browser session then reloaded HISTORY
> and rendered the adopted proposal, Planning node, ChapterIntent 15, and
> `write-next completed` evidence with zero console errors or warnings. The
> run's immutable snapshot still reports the pre-write Canon hash and
> `canonicalMutation=false`; the run is correctly marked `STALE` only because
> the later StoryCommits changed the current Canon hash. This closes the
> deterministic browser handoff evidence, not the real external-provider gate;
> product status remains **PARTIAL**.

> **StoryFlow 2.0 final current-tree verification increment (2026-08-20)**:
> The full repository suite is `1009 passed in 16:22`; the five protected
> feature gates are all `VERIFIED`; Ruff, JavaScript syntax, compileall,
> protected-file, and diff checks pass. The deterministic benchmark completed
> 200/1,250/5,000 events at 248.184/90.677/32.483 events per second with
> `canonicalMutation=false` and `provider=deterministic-runtime-only`.
> This is a reproducible core-ledger baseline, not a production provider SLA.

> **StoryFlow 2.0 Adoption workspace refresh increment (2026-08-20)**:
> The Simulation HISTORY workspace now refreshes from durable backend records
> immediately after creating an Adoption proposal, so the persisted `PROPOSED`
> card is visible without a manual reload. UI edits now round-trip the existing
> structured payload instead of replacing it with a presentation-only marker;
> a fresh current-code browser pass on an isolated copy of the real book
> edited a structured proposal and confirmed its source IDs, event range,
> proposed goals/threads, and `canonicalMutation=false` remained persisted.
> The session produced 0 console errors/warnings. Product status remains
> **PARTIAL**.

> **StoryFlow 2.0 snapshot/adoption contract increment (2026-08-20)**:
> WorldSnapshotBuilder now binds the latest revisioned Planning snapshot and
> exports recorded faction/location states, power systems, entity knowledge,
> plot threads, secrets, goals, conflicts, items, narrative obligations, and
> current chapter position without writing Canon. Migration 43 makes the
> SimulationAdoptionProposal fields (`sourceSimulationId`, branch/event range,
> proposed Planning/plot/goal/foreshadow/ChapterIntent data, and provenance)
> durable and visible in API responses; ChapterIntent provenance carries the
> same boundary evidence. Migration 44 persists SimulationRun base Canon and
> branch lineage (`base_canon_event_id`, `branch_parent_id`,
> `branch_point_event_id`). The Event Timeline now expands persisted actor,
> target, location, action, state-delta, payload, and causal evidence details.
> Focused snapshot/adoption/lineage/API tests pass; the product remains
> **PARTIAL** pending external provider E2E and complete browser
> Writing/Review/StoryCommit execution.

> **StoryFlow 2.0 spatial/provenance hardening increment (2026-08-20)**:
> Added the explicit `PREPARING` run state with persisted configuration and a
> fail/reload path, while preserving the legacy direct `DRAFT -> READY` API
> transition. Action events now carry actor/location provenance and the
> agent-local perception boundary hides location-scoped events from remote
> agents while retaining direct communication visibility. Migration 42 adds
> branch `parent_round`/`fork_snapshot_hash` and intervention `author` fields;
> intervention kinds normalize to the typed contract with compatibility
> aliases. New tests cover persistence, recovery, spatial isolation, branch
> fork hashes, author provenance, invalid kinds, and API responses. The
> combined StoryFlow/Phase 1 suite is `105 passed`; feature verification,
> Ruff, compileall, JavaScript syntax, protected-file, and diff checks pass.
> This is still a partial vertical slice: full browser acceptance, external
> provider E2E, and complete Writing/Review/StoryCommit handoff remain
> unclaimed.
> The final full-repository run completed with `1003 passed` (16:17).

> The same increment now also exposes persisted Adoption Edit/Reject actions
> in the API and Simulation HISTORY workspace. Rejected proposals remain
> visible without a Planning node; only an explicitly adopted proposal can
> reach ChapterIntent or the durable write-next handoff.
> A fresh current-code Playwright pass on an isolated copy of the real book
> created, edited, rejected, and refreshed a proposal; the persisted UI showed
> `REJECTED` with no Planning node and the browser reported zero errors.

> **StoryFlow 2.0 branch causal-trace isolation fix (2026-08-19)**: Branch
> causal backfill now writes only child-owned events while retaining inherited
> parent evidence by event id, preserving the single-run causal batch invariant.
> A real HTTP regression starts a branch, executes a child WAIT event, and
> asserts the branch `/causal-trace` response is 200 with bounded evidence.
> Fresh current-code browser verification observed the same branch trace at
> HTTP 200 with zero console errors/warnings. Overall StoryFlow remains
> **PARTIAL**; external-provider and full Writing/Review/StoryCommit browser
> acceptance remain unclaimed.

> **StoryFlow 2.0 run-scoped Provider Assignment increment (2026-08-19)**:
> Simulation configuration now validates and normalizes capability assignments
> (`agentDecisionProviderId`, `memoryProviderId`, `analystProviderId`, and
> `embeddingProviderId`). Durable `simulation-round` tasks persist the
> normalized record and include it in their idempotency fingerprint. Agent
> Decision, Agent Memory consolidation, and sandbox-local Embedding now pass
> the selected provider through the existing Model Router, preserving
> GenerationRun/Attempt, context provenance, usage and cost-ledger evidence;
> invalid or unavailable assignments fail closed before Sandbox events. Analyst,
> Character Chat, and Survey now run through persisted capability tasks with a
> targeted lease-fenced worker, deterministic idempotency keys, retry-safe
> interaction/report records, and task status/result provenance exposed to the
> existing HTTP/UI surface. Real external-provider E2E is not claimed. Overall
> StoryFlow remains **PARTIAL**.

> **StoryFlow 2.0 durable capability-task increment (2026-08-19)**: Added
> `simulation-analyst-query`, `simulation-character-chat`, and
> `simulation-survey` handlers to the persistent task worker. HTTP requests
> enqueue a book-scoped task before invoking the handler; a restart or competing
> worker can recover the row, while stable report/interaction/survey IDs prevent
> duplicate provider work on retry. Provider evidence retains the existing
> GenerationRun/Attempt and context-manifest boundary, and failure paths remain
> fail-closed without Canon mutation.

> **StoryFlow 2.0 repeat-run/history increment (2026-08-19)**: Added explicit
> repeat-run cohorts and deterministic Outcome Clusters derived from exact
> replayed Sandbox state hashes; the service reports structural
> `dominant outcome`/`plausible outcome` labels and never invents probabilities.
> Migration 41 adds an append-only Simulation archive/unarchive history ledger,
> book-scoped History APIs, and Studio controls. Archived runs retain snapshots,
> events, reports, and branch evidence; Canon remains untouched.

> **StoryFlow 2.0 Canon-boundary proof increment (2026-08-19)**: Added a
> 100-round integration proof covering a sandbox intervention, branch fork and
> branch-only state, Character Chat, Survey, Analyst, persisted Report,
> Adoption, and ChapterIntent. A digest over `story_facts`, `story_states`,
> `narrative_events`, and `story_commits` remains byte-for-byte unchanged;
> this is a focused automated boundary proof, not a claim that the complete
> browser or real-provider acceptance gate is complete.

> **StoryFlow 2.0 adoption handoff browser check (2026-08-19)**: A fresh
> current-code read-only Studio pass loaded the real `ADOPTED` proposal,
> displayed its revisioned Planning node, and exposed `Create ChapterIntent`
> plus `Queue write-next` controls without mutating Canon. The run detail,
> agent roster, scheduler, budget, causal trace, adoption list, and bounded
> event stream all returned HTTP 200; the browser session recorded 0 console
> errors/warnings. This verifies the handoff surface, not the full
> Writing/Review/StoryCommit browser path.

> **StoryFlow 2.0 realtime timeline increment (2026-08-19)**: The workflow
> workspace now attaches a reconnecting `EventSource` to the persisted
> `/events/stream` replay endpoint. New Sandbox events update the timeline and
> ledger count from server records, while `Last-Event-ID`/sequence replay
> prevents duplicate rows; the stream is closed whenever the selected run or
> Simulation page changes. This is a real durable replay path, not client-side
> progress animation, and remains bounded by the existing backend stream. A
> fresh browser pass against current code (`http://127.0.0.1:8006/`) observed
> `/api/v1/.../events/stream` at HTTP 200 with zero fresh console errors.

> **StoryFlow 2.0 workflow-first workspace increment (2026-08-19)**:
> The Simulation Studio now exposes real WORLD, AGENTS, SIMULATE, ANALYZE,
> INTERACT, and HISTORY workspaces over the same persisted run state. The
> AGENTS workspace renders the replayed Character/Faction roster and routes a
> selected Agent into the bounded perception/memory inspector; workspace
> selection is presentation state restored after browser refresh. Current-code
> browser verification exercised all six workspaces, 29 real roster entries,
> refresh recovery of HISTORY, HTTP 200 simulation requests, and zero fresh
> console errors/warnings. This improves P2 workflow clarity but does not claim
> full 23-step browser acceptance or complete Provider/StoryCommit E2E;
> StoryFlow remains **PARTIAL**.

> **Prior verification addendum for the 2026-08-19 StoryFlow increment**:
> Current-code browser verification on `http://127.0.0.1:8002/` loaded the
> persisted simulation adoption list, displayed the Sandbox causal trace, and
> returned HTTP 200 for `causal-trace`, adoption listing, and ChapterIntent
> creation. The fresh browser session recorded 0 console errors/warnings;
> causal evidence and the ChapterIntent response retained
> `canonicalMutation=false`. The focused StoryFlow/Phase 1/performance suite
> is now `102 passed`; `scripts/verify_features.py` reports CW-001, MEMORY-001,
> REVIEW-001, STORY-001, and WRITE-001 as `VERIFIED`. Ruff, JavaScript syntax,
> compileall, protected-file, and diff checks pass; that verification snapshot
> was schema 40 with `PRAGMA integrity_check=ok`. Overall StoryFlow remains
> **PARTIAL**.

> **Current-turn browser control note (2026-08-19)**: The current-code Studio
> server loaded in the in-app browser and exposed the real Dashboard DOM with
> zero fresh console messages, but book navigation clicks exceeded the browser
> control layer's CDP deadline before dispatch. Therefore the full 23-step
> browser acceptance is not claimed; API, TestClient, and static checks remain
> the authoritative evidence for this increment. The current live database is
> schema 41 with `PRAGMA integrity_check=ok` after the History migration.

> **StoryFlow 2.0 causal trace and runtime benchmark increment (2026-08-19)**:
> Added migration 40 with append-only `simulation_causal_traces` rows. Event
> writes now persist bounded, deterministic `causedBy` evidence for prior
> events, Agent memory, goals, relationships, interventions, world rules, and
> GenerationRun provenance; old events can be backfilled through
> `SimulationCausalityService`. The analyst registry, book-scoped
> `/causal-trace` API, and Simulation workspace expose this evidence without
> Canon mutation. Added `scripts/benchmark_storyflow_simulation.py`, exercising
> the durable round engine at 10/20, 25/50, and 50/100 deterministic scales.
> The latest benchmark produced 200/1250/5000 events with elapsed times
> 1.051802s / 13.551701s / 161.249543s using the explicitly labeled
> core-ledger mode (rebuildable graph/memory projections excluded); this is a
> performance baseline, not a production SLA or Provider E2E claim. StoryFlow
> remains **PARTIAL**.

> **StoryFlow 2.0 Agent Tier / Scheduler / Cost Control increment (2026-08-19)**:
> Added migration 39 with append-only `simulation_agent_activations` and
> `simulation_cost_ledger` read models. Run configuration now supports explicit
> Tier A (Primary), Tier B (Active Supporting), and Tier C (Passive) policies;
> the deterministic scheduler scores author pins, goals, nearby events,
> relationship/conflict involvement, recent activity, narrative relevance, and
> activation frequency, persisting `whyActivated` evidence for every Agent.
> Durable provider rounds use the scheduler rather than calling every entity,
> reconcile GenerationRun token usage/cost without double charging, and pause
> the sandbox as `PAUSED_BUDGET` when calls/tokens/cost exceed author limits;
> budget updates and resume stay at an explicit author boundary. Studio now
> renders scheduler evidence and live usage/estimate panels. Dedicated tier,
> persistence, budget pause/increase/resume coverage plus API assertions pass;
> the browser check observed real Tier C scheduler rows, budget estimates,
> `canonicalMutation=false`, all current requests 200, and zero fresh console
> messages. The StoryFlow plus Phase 1 regression is now **84 passed**.
> Provider-rate discovery, richer large-run scheduling heuristics,
> and performance benchmarks remain `PARTIAL`.

> **StoryFlow 2.0 Analyst tools increment (2026-08-19)**: Added a closed
> `SimulationAnalystTools` registry for snapshot/state/event, Character/Faction,
> memory, relationship/goal/conflict, foreshadow/plot-thread, world-rule,
> branch comparison, Canon freshness, and Planning inspection queries. Each
> result carries source metadata and event/snapshot/state references; conflict
> queries explicitly distinguish persisted events from unrecorded action
> rejections. `NarrativeAnalyst.ask` selects only a whitelisted tool and
> returns a grounded evidence chain. The Studio `analysis/query` endpoint
> validates book scope and persists an immutable `analyst-query` report;
> unsupported tools and cross-book comparisons fail closed. Natural-language
> synthesis, causal inference, and richer evidence graphs remain `PARTIAL`.

> **StoryFlow 2.0 dynamic graph projection increment (2026-08-19)**: Added
> migration 38 with a run-scoped, rebuildable graph read model for nodes,
> edges, and projection metadata. Round completion refreshes it after state
> advancement; intervention and branch creation materialize their own
> projections, and recovery retries rebuild after a graph-stage interruption.
> The Graph API reuses the persisted projection when state hash, event
> sequence, and event window match, otherwise it deterministically rebuilds
> from the sandbox snapshot plus ledger. Projection writes are isolated from
> Canon and remain disposable read-model state; Canon row counts stay
> unchanged in regression coverage. Dynamic graph is now a durable vertical
> slice, while richer causal edges, realtime push invalidation, and large-run
> performance benchmarks remain `PARTIAL`.

> **StoryFlow 2.0 provider-decision increment (2026-08-19)**: Added a
> bounded `SimulationAgentContextBundle` compiled only from one Agent's
> sandbox perception (identity, local state/world, scoped knowledge and
> beliefs, goals, relationships, memories, observations, available actions,
> and rules). The compiler records a deterministic context hash and explicit
> character-budget truncation metadata; it does not expose Canon or another
> Agent's private knowledge. Durable `simulation-round` tasks can now choose
> `decisionMode=provider` and a persisted planner/writer/reviewer route. Each
> structured provider response is parsed into a typed `NarrativeAction`,
> validated by the existing deterministic engine, and linked to the exact
> `GenerationRun`/`GenerationAttempt` and context manifest. Missing routes
> fail the task before any simulation event is written. Provider calls are
> task-scoped and recover through the existing idempotent attempt ledger;
> explicit/provider task fingerprints now include mode, Agent set, and role.
> The provider/clock focused regression was **79 passed** before the graph and
> Analyst slices; the current StoryFlow plus Phase 1 regression is **82
> passed**, and the five protected feature gates remain VERIFIED. External-provider production E2E,
> token-native budgets, and broader scheduler/cost controls remain `PARTIAL`.

> **StoryFlow 2.0 deterministic simulation-clock increment (2026-08-19)**:
> Added migration 37 and a run-persisted UTC simulation clock with stable
> defaults, configurable ISO start time and round duration, atomic round/time
> advancement, event-level timestamps, and replay/recovery clock restoration.
> Empty or fully rejected rounds emit a durable `ROUND_CLOCK` event, so the
> sandbox remains reconstructible from snapshot plus append-only events;
> branches inherit their fork time and advance independently. API run views
> expose `simulationTime`. Provider-backed decisions, dynamic graph writes,
> and broader P2 scheduling/cost controls remain `PARTIAL`.

> **StoryFlow 2.0 simulation crash-recovery increment (2026-08-19)**:
> Resolved accepted actions for one round are now persisted atomically as a
> batch. The durable `simulation-round` handler reconciles post-ledger work on
> retry: event-backed episodic memory, semantic consolidation, detached-state
> hash validation, checkpoint creation, and max-round completion are all
> idempotent. A retryable stage-injection seam and parameterized regression
> cover `run_start`, `round_begin`, provider request/response, validation,
> event persistence, memory, state, and checkpoint interruption, asserting no
> duplicate action/event, lost round, or corrupt checkpoint. Real provider
> routing/result persistence is still `PARTIAL`.

> **StoryFlow 2.0 branch-tree increment (2026-08-19)**: Added a book-scoped
> read-only Branch Tree API backed by `simulation_runs` and
> `simulation_branches`, including root/child metadata, fork sequence, and
> persisted edge evidence. The Simulation workspace now renders the tree and
> lets the author switch runs from any node; all responses remain
> `canonicalMutation=false`. Branch comparison and isolation remain persisted
> sandbox behavior; richer graph visualization remains `PARTIAL`.

> **StoryFlow 2.0 event provenance increment (2026-08-19)**: Added migration
> 36 and a first-class nullable `simulation_events.source_generation_run_id`
> column/index. Typed actions now persist this provenance outside payloads;
> replay, timeline, and SSE read models expose it as
> `sourceGenerationRunId`. Regression covers SQLite persistence, rehydration,
> and the real Studio round API. Provider routing and broader external
> provider-result recovery remain `PARTIAL`.

> **StoryFlow 2.0 snapshot provenance/freshness increment (2026-08-19)**:
> Added a book-scoped immutable snapshot read model and exposed its BASE CANON,
> EVENT, HASH, SNAPSHOT TIME, current Canon event/hash, and read-only freshness
> classification (`CURRENT`, `STALE`, `DIVERGED`) in the Simulation Run API and
> workspace. Tests create a real accepted StoryCommit after a snapshot and
> observe `STALE`; cross-book snapshot access is rejected and provenance reads
> leave Canon row counts unchanged. Browser verification rendered the new
> `CURRENT` evidence panel from the current Studio server with no browser
> diagnostics. Snapshot freshness is now explicit, while full historical Canon
> replay and provider-backed simulation remain `PARTIAL`.

> **StoryFlow 2.0 environment configuration UI increment (2026-08-19)**:
> The run setup modal now accepts a real JSON Simulation Configuration covering
> snapshot-derived agents, clock/round duration, decision frequency, memory,
> communication/world rules, action limits, randomness, deterministic conflict
> resolution, and provider assignment. The persisted run detail renders that
> configuration as a read-only sandbox read model with `canonicalMutation=false`;
> invalid JSON is rejected before snapshot/run creation. Browser verification
> opened the setup form and observed the default configuration fields; no Canon
> mutation or console error occurred. Provider routing and richer structured
> editors remain `PARTIAL`.

> **StoryFlow 2.0 durable simulation task recovery increment (2026-08-19)**:
> Simulation round tasks are now bound back to their book-scoped Run through the
> persisted `simulation_runs.task_id` seam. The API exposes the bound task and a
> dedicated task read endpoint, rejects a second active round for the same Run,
> preserves idempotent retries, and leaves completed/cancelled task history
> recoverable after refresh. The Simulation workspace renders the real persisted
> status, stage, checkpoint and progress, with pause/resume/cancel/retry controls
> mapped to the existing Task Runtime. Browser verification queued a real round,
> observed `running` with the task id, then reloaded and recovered `completed`
> at 100% with the sandbox event ledger advanced; Canon remained unchanged.
> Provider-driven cognition, crash injection at every provider boundary, and
> production-scale scheduling remain `PARTIAL`.

> **StoryFlow 2.0 agent interaction workspace increment (2026-08-19)**:
> The Simulation workspace now exposes persisted Character Chat and Multi-agent
> Survey controls over the existing agent-local interaction services. Both forms
> select only Character Agents, reload persisted chat/survey history with each
> run, and present per-agent response status without reading or writing Canon.
> Browser verification submitted an Agent-local `where are you?` interaction,
> completed a two-character survey, refreshed the Studio, and recovered both
> records with no fresh console errors. Provider-backed dialogue remains
> `PARTIAL`; unsupported prompts remain truthfully `PROVIDER_UNAVAILABLE`.

> **StoryFlow 2.0 persisted adoption-to-intent UI increment (2026-08-19)**:
> The Simulation workspace now reloads persisted proposals for the selected run,
> lets an author explicitly adopt a `PROPOSED` item into the revisioned Planning
> overlay, and exposes the resulting `ADOPTED` Planning node with a ChapterIntent
> creation control. Browser verification against the current Studio server
> refreshed an existing proposal, adopted it, and created ChapterIntent chapter 1;
> both API responses remained `canonicalMutation=false`. This reaches Planning
> and the writing handoff boundary only; provider execution, review/revision
> completion, and StoryCommit remain `PARTIAL`.

> **StoryFlow 2.0 Agent roster and local evidence increment (2026-08-19)**:
> Added a book/run-scoped Agent roster read model derived from replayed sandbox
> state, covering both Character and Faction entities. The Simulation round
> form now selects a real persisted Agent, submits its explicit `actorType`, and
> shows local location/goals plus bounded perception evidence before action
> execution. The individual inspector accepts faction agents as well as
> characters and remains Canon-isolated. Current-code API verification returned
> 29 agents with `canonicalMutation=false`; StoryFlow regression remained 41
> passed. Provider cognition and richer Agent scheduling remain `PARTIAL`.

> **StoryFlow 2.0 round execution and adoption controls increment (2026-08-19)**:
> The Simulation workspace now exposes structured typed actions for direct
> round execution or durable `simulation-round` task queuing, plus an explicit
> author action that persists a Simulation Adoption Proposal. Both paths remain
> book-scoped and report `canonicalMutation=false`; action validation, conflict
> resolution, event persistence, and checkpointing remain owned by the existing
> simulation runtime. Browser verification executed a validated `WAIT` round
> (round 1, ledger sequence 2) and created an adoption proposal without writing
> Canon. Provider-backed decisions, durable task completion UI, and the later
> adoption-to-writing controls remain `PARTIAL`.

> **StoryFlow 2.0 Simulation workspace increment (2026-08-19)**:
> Added a real book-scoped Simulation workspace to Studio. Authors can create
> a Canon-bound immutable snapshot and durable run, move through lifecycle
> states, inspect the persisted event ledger, apply explicit sandbox
> interventions, fork isolated branches, and request deterministic evidence
> reports. The workspace uses an in-app form rather than unsupported browser
> prompts and exposes no Canon mutation path. API and browser verification
> exercised run creation, lifecycle, intervention, branch creation, and report
> rendering against SQLite. Full action scheduling, adoption UI, provider
> cognition, and browser acceptance coverage remain `PARTIAL`.

> **StoryFlow 2.0 deterministic memory retrieval increment (2026-08-19)**:
> Agent-scoped memory reads now support a local relevance query over persisted
> memory content, with deterministic weighting for term matches, importance,
> confidence, and round recency. Simulation rounds and Character Chat use the
> same retrieval boundary; memories from other agents remain excluded. StoryFlow
> regression: 41 passed; Phase 1 persistence: 19 passed; Ruff, protected-file
> verification, diff, compile, and feature verification checks passed. Vector
> retrieval, summarization, and provider-backed cognition remain `PARTIAL`.

> **StoryFlow 2.0 social and rumor memory consolidation increment (2026-08-19)**:
> The deterministic `AgentMemoryConsolidator` now derives sandbox-only `SOCIAL`
> target indexes and `RUMOR` outbound-information indexes from an acting agent's
> persisted episodic event evidence. Source event ids and memory identities are
> stable, so a retry remains idempotent; the consolidation neither fabricates
> claims nor accesses Canonical Memory. StoryFlow regression: 40 passed; Phase 1
> persistence: 19 passed; Ruff, protected-file verification, diff, and compile
> checks passed. Retrieval ranking, incoming rumor propagation, and richer
> social interpretation remain `PARTIAL`.

> **StoryFlow 2.0 semantic memory consolidation increment (2026-08-19)**:
> Added deterministic `AgentMemoryConsolidator` to create an auditable semantic
> event index after accepted round actions. The index contains only persisted
> episodic source-event ids, counts and action-type totals, uses stable source
> hashing for idempotence, and is recorded before the round checkpoint. No LLM
> summary or invented narrative claim is stored. StoryFlow regression: 39
> passed; Phase 1 persistence: 19 passed; Ruff, protected-file verification,
> and diff checks passed. Vector retrieval and richer social/rumor behavior
> remain `PARTIAL`.

> **StoryFlow 2.0 memory recovery increment (2026-08-19)**: Episodic memory
> rows now use a stable `run + actor + event` identity and repository insertion
> is idempotent. A retry of an already persisted simulation round replays its
> actor events to restore a missing memory projection without appending another
> event or duplicate memory. The recovery test explicitly simulates an event
> persisted before its memory update. StoryFlow regression: 38 passed; Phase 1
> persistence: 19 passed; Ruff, protected-file verification, and diff checks
> passed. Semantic/social memory consolidation remains `PARTIAL`.

> **StoryFlow 2.0 terminal round lifecycle increment (2026-08-19)**: The round
> engine now checkpoints first and transitions a run to `COMPLETED` when it
> reaches `max_rounds`, recording completion time in the durable run metadata.
> Round/task responses expose the resulting status, and a retry of the final
> durable task returns the prior event/checkpoint evidence idempotently instead
> of failing because the run is terminal. StoryFlow regression: 38 passed;
> Phase 1 persistence: 19 passed; Ruff, protected-file verification, and diff
> checks passed. Broader crash injection coverage remains `PARTIAL`.

> **StoryFlow 2.0 durable run configuration increment (2026-08-19)**: Added
> migration 35 and persisted SimulationRun description, purpose, author,
> configuration, task binding, and lifecycle timestamps. Studio create/read APIs
> now round-trip the environment configuration and Canon/snapshot lifecycle
> evidence; branch runs inherit configuration but remain independent run rows.
> Configuration is deep-detached and status transitions record start/pause/end
> times. StoryFlow regression: 38 passed; Phase 1 persistence: 19 passed; Ruff,
> protected-file verification, and diff checks passed. Provider configuration
> routing and scheduled simulation execution remain `PARTIAL`.

> **StoryFlow 2.0 dynamic simulation graph increment (2026-08-19)**: Added a
> read-only `SimulationGraphProjector` and
> `GET .../simulation/runs/{run_id}/graph`. The projection is rebuilt from
> replayed sandbox state plus the persisted event ledger, includes Character,
> Faction, Location nodes and relationship/event edges, and carries state hash,
> sequence and `canonicalMutation=false` evidence. It does not write Canon or
> reuse the authoritative StoryGraph projection cache. StoryFlow regression:
> 37 passed; Phase 1 persistence: 19 passed; Ruff, protected-file verification,
> and diff checks passed. Realtime graph streaming and richer causal edges
> remain `PARTIAL`.

> **StoryFlow 2.0 deterministic action conflict increment (2026-08-19)**:
> Added `ActionConflictResolver` to the simulation round engine. Same-round
> actions are collected before state mutation and resolved by confidence plus
> stable actor/action ids; conflicting state writes and incompatible shared
> target actions are rejected with auditable errors, while accepted events keep
> the append-only ledger and faction/character actor types. StoryFlow
> regression: 36 passed; Phase 1 persistence: 19 passed; Ruff, protected-file
> verification, and diff checks passed. Rich causal conflict policy and
> provider-backed negotiation remain `PARTIAL`.

> **StoryFlow 2.0 Faction runtime increment (2026-08-19)**: Faction agents now
> participate in `PerceptionBuilder`, `ActionValidator`, and
> `SimulationRoundEngine`. `NarrativeAction.actor_type` remains backward
> compatible for character callers and lets faction actions persist as
> `actor_type=faction`; round scheduling includes both character and faction
> entities, while faction perception reads only its own recorded information.
> StoryFlow regression: 35 passed; Phase 1 persistence: 19 passed; Ruff,
> protected-file verification, and diff checks passed. Provider-backed faction
> cognition and conflict resolution remain `PARTIAL`.

> **StoryFlow 2.0 adopted-intent writing handoff increment (2026-08-19)**:
> Added `POST .../simulation/adoptions/{proposal_id}/writing-task`. After an
> explicit adoption, the endpoint persists/loads the simulation-derived
> ChapterIntent, applies existing model/planning readiness gates, and enqueues
> the durable `write-next` task with intent and adoption provenance. The worker
> remains responsible for writing, review/revision, and StoryCommit gates; the
> HTTP surface performs no provider call and no Canon mutation. TestClient
> coverage verifies adoption -> ChapterIntent -> queued writing task in SQLite.
> StoryFlow regression: 34 passed; Phase 1 persistence: 19 passed; Ruff,
> protected-file verification, and diff checks passed. Automatic worker
> completion and browser workflow remain `PARTIAL`.

> **StoryFlow 2.0 Simulation-to-ChapterIntent increment (2026-08-19)**:
> Added `SimulationChapterIntentService` and
> `POST .../simulation/adoptions/{proposal_id}/chapter-intent`. The handoff is
> gated on explicit `ADOPTED` status, maps adopted payload fields into the
> existing `ChapterIntent`, persists it through `ControlSurface` for the
> Writing Pipeline, and records proposal/run/planning-node provenance with
> `canonicalMutation=false`. It never writes StoryFact, StoryState, or
> StoryCommit. StoryFlow regression: 34 passed; Phase 1 persistence: 19
> passed; Ruff, protected-file verification, and diff checks passed. Automatic
> writing/review/commit handoff remains `PARTIAL`.

> **StoryFlow 2.0 Character/Faction Agent profile increment (2026-08-19)**:
> Added immutable `CharacterAgentProfile` and `FactionAgentProfile` plus an
> `AgentProfileBuilder` derived strictly from `SimulationWorldSnapshot`.
> Profiles preserve recorded goals, strategies, resources, relationships,
> knowledge, unknowns, and decision fields without inferring absent data or
> exposing snapshot `story_state` as agent knowledge. StoryFlow regression: 33
> passed; Phase 1 persistence: 19 passed; Ruff, protected-file verification,
> and diff checks passed. Faction runtime scheduling, provider cognition and
> conflict resolution remain `PARTIAL`.

> **StoryFlow 2.0 multi-agent Survey increment (2026-08-19)**: Added
> migration 34 with durable surveys and immutable per-agent survey responses.
> `SimulationSurveyService` fans one author question out to selected or all
> simulation characters through the existing Agent-local interaction boundary,
> records each interaction/evidence link, and aggregates `COMPLETED` versus
> `PARTIAL` status without exposing global Canon. Studio now provides create,
> list, and book-scoped survey detail endpoints. StoryFlow regression: 32
> passed; Phase 1 persistence: 19 passed; Ruff, protected-file verification,
> and diff checks passed. Provider-backed answers and richer survey analysis
> remain `PARTIAL`.

> **StoryFlow 2.0 agent-local interaction increment (2026-08-19)**: Added
> immutable `simulation_character_interactions` persistence and Studio chat
> endpoints scoped to a simulation run and agent. `CharacterChatService` builds
> responses from only the selected Agent's PerceptionBuilder output and scoped
> memory, records state/event/memory evidence, and explicitly returns
> `PROVIDER_UNAVAILABLE` for unsupported questions when no decision provider is
> configured. This provides a truthful interaction boundary without injecting
> global Canon or fabricating LLM dialogue. StoryFlow regression: 30 passed;
> Phase 1 persistence: 19 passed; Ruff, protected-file verification, and diff
> checks passed. Provider-backed character cognition and multi-agent Survey
> remain `PARTIAL`.

> **StoryFlow 2.0 durable simulation task increment (2026-08-19)**: Added a
> `SimulationTaskHandlers` adapter and book-scoped `POST .../round-tasks`
> endpoint. Simulation rounds can now be queued in the existing durable Task
> Runtime and executed by the persistent worker outside an HTTP request. Task
> actions receive stable task-scoped action ids; retries reuse already persisted
> events and return an idempotent result instead of duplicating ledger entries.
> Worker/API tests cover enqueue, execution, persistence, and retry behavior.
> Full provider decision routing, multi-agent conflict resolution, and crash
> injection across every round boundary remain `PARTIAL`.

> **StoryFlow 2.0 evidence-grounded Analyst increment (2026-08-19)**: Added
> migration 32 and immutable `simulation_analysis_reports` persistence. The
> deterministic `SimulationAnalyst` derives run summary evidence from the
> persisted event ledger, replayed sandbox state, and bound world snapshot;
> reports include event ids, state hash, event/actor counts, round coverage,
> and Canon binding while explicitly marking `canonicalMutation=false`. Studio
> now exposes create/list/get report endpoints with book/run ownership checks.
> StoryFlow regression: 26 passed; Phase 1 persistence: 19 passed; Ruff,
> protected-file verification, and diff checks passed. Natural-language causal
> synthesis, provider-backed Analyst, interaction, survey, and browser workflow
> remain `PARTIAL`.

> **StoryFlow 2.0 bounded timeline stream increment (2026-08-19)**: Added a
> book/run-scoped `GET .../simulation/runs/{run_id}/events/stream` endpoint.
> It replays only persisted `SimulationEvent` ledger rows, emits sequence-based
> SSE ids, supports `after_sequence` and `Last-Event-ID` reconnects, and ends
> after the current ledger is drained. No polling loop, fabricated keep-alive,
> or Canon mutation is involved. TestClient coverage verifies replay, resume,
> invalid cursor handling, and existing StoryFlow/Phase 1 regressions. Live
> worker push, provider-backed cognition, UI, and browser acceptance remain
> `PARTIAL`.

> **StoryFlow 2.0 clean-room architecture audit (2026-08-19)**: Reviewed the
> public MiroFish README workflow (Graph Building, Environment Setup,
> Simulation, Report, Deep Interaction) and mapped capabilities to
> NovelForge-native Canon/Planning/StoryCommit boundaries. Added the complete
> `docs/storyflow-2/00-12` architecture and acceptance documentation. No
> reference source code, prompts, CSS, components, or algorithms were copied.
> This is architecture evidence, not a completion claim; product status remains
> `PARTIAL`.

> **StoryFlow 2.0 round execution API increment (2026-08-19)**: Added a real
> `POST .../simulation/runs/{run_id}/rounds` endpoint accepting explicit typed
> actions. It reuses PerceptionBuilder, ActionValidator, SimulationRoundEngine,
> event persistence, memory updates, and checkpoint recovery; it does not
> invent random actions or claim provider completion. TestClient coverage now
> exercises lifecycle -> intervention -> round -> branch -> adoption on the
> real Studio app/SQLite database: StoryFlow 23 tests and Phase 1 persistence
> 19 tests passed. Ruff, protected-file verification, and diff checks passed.
> Provider-backed decision routing and browser acceptance remain `PARTIAL`.

> **StoryFlow 2.0 P0 increment (2026-08-19)**: Added the first clean-room
> simulation boundary in `src/storyflow/world/`. `SimulationWorldSnapshot`
> deep-detaches nested world input, binds a run to the originating Canon event/hash,
> exposes a stable snapshot id, and classifies later Canon state as `CURRENT`,
> `STALE`, or `DIVERGED` without mutating Canon. Migration 23 creates the
> FK-scoped `simulation_world_snapshots` table, and `WorldSnapshotRepository`
> persists/reloads only this detached payload after validating book/project ownership.
> Targeted verification: `python -m pytest -q tests/test_storyflow_world_snapshot.py
> tests/test_phase1_persistence.py -k 'storyflow_world_snapshot or migration_checksum'`
> (7 passed), Ruff, protected-file verification, and `git diff --check` passed.
> This is a foundational P0 slice; runtime, agents, memory, branching, UI, and
> browser acceptance remain `PARTIAL`.

> **StoryFlow 2.0 timeline/agent read API increment (2026-08-19)**: Added
> incremental durable event timeline reads at
> `GET .../simulation/runs/{run_id}/events?after_sequence=N`, plus a scoped
> Agent Inspector at `GET .../simulation/runs/{run_id}/agents/{agent_id}`.
> Inspector output is built from replayed state, `PerceptionBuilder`, and
> run/agent memory only; it does not expose global StoryState/Canon context.
> TestClient StoryFlow coverage remains 23 passed; Phase 1 persistence 19
> passed. Ruff, protected-file verification, and diff checks passed. SSE/live
> push, UI workspaces, and browser acceptance remain `PARTIAL`.

> **StoryFlow 2.0 Simulation workflow API increment (2026-08-19)**: Studio now
> exposes durable book-scoped lifecycle transitions, branch creation,
> sandbox interventions, adoption proposals, and author adoption into the
> revisioned Planning overlay. Endpoints validate run/snapshot/proposal
> ownership; adoption creates only a `PLANNED` PlanningNode and does not write
> Canon tables. TestClient coverage exercises the real Studio app and isolated
> SQLite database: 23 StoryFlow tests and 19 Phase 1 persistence tests passed.
> Ruff, protected-file verification, and diff checks passed. Round execution
> UI, provider-backed decisions, and browser acceptance remain `PARTIAL`.

> **StoryFlow 2.0 Studio API increment (2026-08-19)**: Added book-scoped,
> SQLite-backed Studio APIs for creating a Canon-derived simulation snapshot,
> creating/reading a SimulationRun, and comparing two runs. Endpoints validate
> snapshot/run book ownership, return persisted state/event evidence, and mark
> the read surface `canonicalMutation=false`. Browser/API tests use the real
> Studio app and isolated SQLite database; regression: 41 tests passed. Ruff,
> protected-file verification, and diff checks passed. Run lifecycle actions,
> adoption endpoints, UI, and browser workflow acceptance remain `PARTIAL`.

> **StoryFlow 2.0 P0 recovery increment (2026-08-19)**: Added constrained
> `SimulationRun` transitions, durable `SimulationCheckpoint`, and
> `SimulationRepository.recover()`. Checkpoints persist the replayed state and
> hash at an event sequence; recovery verifies the hash, resumes from that
> sequence, and applies only later events. Migration 27 adds immutable
> checkpoint rows and SQLite triggers. Event append now advances the durable
> round monotonically. Full persistence regression for this surface: 33 passed;
> Ruff, protected-file verification, and diff checks passed. Provider crash
> recovery, worker leases, branch isolation, and browser acceptance remain
> `PARTIAL`.

> **StoryFlow 2.0 P0 round-engine increment (2026-08-19)**: Added
> `SimulationRoundEngine` and `RoundResult`. A round reads the durable run,
> builds agent-local perceptions, invokes only injected decision functions,
> validates returned `NarrativeAction`s, appends accepted actions as ordered
> SimulationEvents, advances the durable round, and checkpoints the result.
> Agents with no decision are reported as skipped; rejected actions are
> reported with validation errors and never become events. The engine refuses
> non-RUNNING runs and max-round overflow. Persistence/StoryFlow regression:
> 34 passed; Ruff, protected-file verification, and diff checks passed. Real
> model decision routing, multi-agent conflict resolution, provider recovery,
> branching, and browser acceptance remain `PARTIAL`.

> **StoryFlow 2.0 P0 action increment (2026-08-19)**: Added typed
> `NarrativeAction` / `ActionType` and a sandbox `ActionValidator`. The
> validator rejects unknown actors/targets, dead actors, location mismatches,
> missing knowledge, unavailable inventory, unknown secrets, and invalid
> movement actions. `SimulationRepository.append_action()` validates against
> replayed state before creating a `SimulationEvent`, preserving the existing
> append-only Canon boundary and recording generation provenance. Targeted
> snapshot/ledger/persistence tests: 31 passed; Ruff and protected-file checks
> passed. Full agent cognition, perception, round scheduling, and browser
> acceptance remain `PARTIAL`.

> **StoryFlow 2.0 P0 branching/intervention increment (2026-08-19)**: Added
> durable `SimulationBranch` and `SimulationIntervention`. Migration 29 stores
> a parent run plus immutable fork sequence; branch replay reads only the parent
> ledger prefix and appends new events solely to the child. Migration 30 records
> author interventions as explicit sandbox `INTERVENTION` events with rationale
> and state delta. Parent/sibling runs and Canon remain untouched. Persistence
> regression: 37 tests passed; Ruff, protected-file verification, and diff
> checks passed. Branch comparison, adoption, Task Runtime integration, and
> browser workflows remain `PARTIAL`.

> **StoryFlow 2.0 Simulation-to-Planning increment (2026-08-19)**: Added
> durable `SimulationAdoptionProposal` and explicit author adoption. Migration
> 31 persists proposed/adopted outcomes. Adoption invokes the existing
> revisioned `StoryFlowPlanningService.add_node()` to create an author-owned
> `PLANNED` planning node with simulation provenance; it never writes
> `StoryFact`, `StoryState`, or `StoryCommit`. Regression: 38 tests passed;
> Ruff, protected-file verification, and diff checks passed. ChapterIntent
> synthesis, writing-task handoff, review/commit E2E, and browser acceptance
> remain `PARTIAL`.

> **StoryFlow 2.0 P0 WorldSnapshotBuilder increment (2026-08-19)**: Added
> `WorldSnapshotBuilder`, which reads a book's active Canon events/hash,
> StoryState version, recorded world rules, entities, latest character states,
> relationships, timeline, foreshadows, and accepted facts in a read-only
> SQLite connection. It produces a detached immutable `SimulationWorldSnapshot`
> and leaves missing Canon dimensions absent rather than inferring them from
> text/layout. Regression: 40 tests passed; Ruff, protected-file verification,
> and diff checks passed. Full Canon entity coverage, Studio API/UI, and
> browser acceptance remain `PARTIAL`.

> **StoryFlow 2.0 P1 branch comparison increment (2026-08-19)**: Added
> `BranchComparisonService`, a deterministic read model over persisted
> SimulationEvent ledgers and replayed SimulationWorldState. It returns common
> prefix sequence, per-run state hashes, changed top-level sandbox values, and
> exact divergent event ids with an explicit `canonicalMutation=false` evidence
> marker. It makes no inferred causality or LLM-generated claims. Regression:
> 39 tests passed; Ruff, protected-file verification, and diff checks passed.
> Analyst reasoning, report persistence, UI, and browser acceptance remain
> `PARTIAL`.

> **StoryFlow 2.0 P0 simulation memory increment (2026-08-19)**: Added
> simulation-only `AgentMemory` with `EPISODIC`, `SEMANTIC`, `SOCIAL`, and
> `RUMOR` types. Migration 28 creates the run/agent-scoped memory table and
> index. `AgentMemoryRepository` never reads or writes Canonical Memory;
> `SimulationRoundEngine` records accepted action events as actor episodic
> memory with source event provenance, and passes bounded agent memory into
> perception. Regression: 35 tests passed; Ruff, protected-file verification,
> and diff checks passed. Memory consolidation, semantic retrieval, faction
> memory, and browser acceptance remain `PARTIAL`.

> **StoryFlow 2.0 P0 knowledge/perception increment (2026-08-19)**: Added
> `KnowledgeStatus`, `KnowledgeItem`, and `KnowledgeScope` with explicit
> `KNOWS`, `BELIEVES`, `SUSPECTS`, `MISBELIEVES`, `UNKNOWN`, `SECRET_OWNER`,
> and `HEARD_RUMOR` semantics. `PerceptionBuilder` now emits an agent-local
> context (identity, local world, scoped knowledge/beliefs, goals,
> relationships, observations, rules, and available actions) and filters
> private SimulationEvents by `visibility_scope`; it never injects global
> Canon into an agent. Action preconditions use the same scope when available.
> Targeted StoryFlow tests: 13 passed; Ruff passed. Full cognition/runtime,
> memory persistence, and browser acceptance remain `PARTIAL`.

> **StoryFlow 2.0 P0 ledger increment (2026-08-19)**: Added a detached
> `SimulationWorldState`, durable `SimulationRun`, and append-only
> `SimulationEvent` ledger under `src/storyflow/simulation/`. State forks from
> an immutable World Snapshot and accepts only consecutive ledger events;
> replay rebuilds the same sandbox state from the persisted event order.
> Migrations 24-26 add the snapshot/run/event schema plus SQLite triggers that reject
> snapshot/event updates and deletes. The schema executor now uses SQLite
> statement completion so procedural trigger bodies remain atomic migrations.
> This proves a small persisted/replayable sandbox boundary only; no Canon table
> is written. Agent rounds, action validation, checkpoints, recovery, UI, and
> browser acceptance remain `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: The StoryFlow Minimap viewport is now a real navigation control: clicking recenters the Canvas, dragging the viewport rectangle moves the same Canvas transform while preserving zoom, and viewport continuation waits for pointer release instead of fanning out one Graph request per move. This is workspace navigation only; it does not write Canon or layout facts. Headed-browser evidence is recorded in `storyflow-20260814-minimap-drag-1280.png`; the full suite is `898 passed in 434.93s`; product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: StoryFlow View switching now preserves the currently focused/selected real node whenever that node is legal in the target projection. Context View remains chapter-anchored and never treats an unrelated entity as an AI context focus. This keeps Story, Character, Timeline, World, and Foreshadow projections navigationally continuous without changing Canon or Graph facts; the navigation contract test covers the seam. The full suite is now `897 passed in 855.62s`; the final static/browser/API gates are green. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: The existing model-readiness contract is now enforced at the StoryFlow API boundary as well as in the Canvas. `/forecast`, `story-graph/actions/analyze`, and `story-graph/planning/generate` return the truthful `LLM_PROVIDER_REQUIRED` response before creating a durable task when Provider/model role routing is unavailable. Revisioned planning-node and Chapter Intent saves remain available without a model. Regression coverage proves both no-enqueue failure and ready-route enqueue behavior; the full suite is `896 passed in 437.96s`, with ruff, pyright, `verify.py`, feature verification, progress verification, and protected-file verification also green. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: StoryFlow now reads the existing `/creation/preflight` contract before exposing model-backed actions. The workbench shows `AI RUNTIME · READY`, `SETUP REQUIRED`, `UNAVAILABLE`, or `CHECKING`; missing Provider/model routes disable generation, candidate forecasting, and AI analysis while leaving revisioned planning saves available. `Open AI config` routes to the existing Agent Config page. Headed-browser evidence covers both read-only and explicit Planning Edit states in `storyflow-20260814-ai-runtime-setup-1280.png` and `storyflow-20260814-ai-runtime-planning-1280.png`. This is a truthful runtime gate, not a new configuration source; product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: StoryFlow now opens on the requested warm paper authoring surface instead of forcing the dark graphite treatment. The existing dark theme remains an explicit user preference and was browser-checked after toggling from the paper surface; the Canvas, semantic edges, Story Ports, Inspector, and Minimap remain readable in both modes. Evidence: `storyflow-20260814-paper-default-1280.png`, `storyflow-20260814-paper-character-1280.png`, and `storyflow-20260814-paper-dark-1280.png`. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Full Graph viewport reads now page semantic edges independently from node pages through the same SQLite-backed Graph API. `edge_page_token` is bound to the view/filter/viewport/edge-limit/source/workspace fingerprint; the response exposes `internalEdgeCount`, page offsets, and `nextInternalEdgePageToken`. The Canvas merges edge pages by semantic edge id and shows an explicit “Load more semantic edges” action, so edges between not-yet-hydrated node pages are not silently lost. This is a bounded read-model improvement, not GPU virtualization; product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Explicit Full Graph now starts with a real `limit=240&edge_limit=600` browser working set instead of serializing the historical `1200/3000` compatibility payload. The existing SQLite world-coordinate cursor then incrementally merged a 500-chapter fixture from 240 to 480 to 720 loaded nodes; the Character Inspector retained recorded state/knowledge, boundary semantic edges, and provenance, and Story/Timeline/World switches returned HTTP 200 with empty browser diagnostics. The full suite is `891 passed in 576.54s`; the synthetic 100/500/1000 read-model rerun is recorded in the performance baseline. This is a transport-budget/progressive-disclosure increment, not full GPU virtualization; product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Dense StoryFlow semantic edges now use a real hybrid renderer. When the bounded viewport contains at least 40 rendered edges, one 2D Canvas surface paints curves, status styles, arrows, labels, and hover/click hit testing from the same SQLite-derived edge records; sparse Story Flow remains SVG and the connection preview remains SVG. On the real 500-chapter fixture, 1,200 projected nodes / 3,000 indexed edges were bounded to 38 DOM nodes and 334 Canvas-painted edges at both 1920x1080 and 1366x768. Switching back to sparse mode cleared Canvas paint state; browser diagnostics were empty. The full suite is `890 passed in 522.20s`; ruff, pyright (`0 errors, 0 warnings`), `verify.py`, feature verification (`5/5`), progress verification, and protected-file verification also pass. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Context View now exposes a truthful `tokenSummary.inputAccounting` read model. It reconciles the persisted GenerationRun prompt layout with manifest source/section/component character ranges, reports union coverage, provenance overlap, untracked prompt/message characters, missing included-source ranges, and explicit legacy degradation states. The Inspector renders the same character-level accounting beside whole-run provider usage; no per-source provider tokens are invented and no Canon/UI rows are mutated. The canonical full suite is `889 passed in 459.25s`; browser evidence covers 1920x1080 and 1366x768 with zero page/console diagnostics. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Chapter History now exposes a real `canonicalGraphHistory` timeline in the existing `/story-graph/history` response. Rows are accepted StoryCommit graph-snapshot boundaries, retain older accepted boundaries after supersession, report bounded semantic changes and snapshot provenance, and explicitly break when a capture is missing. The Inspector renders `CANON GRAPH` / `STALE GRAPH` evidence without reconstructing mutable entity tables or mutating Canon. The canonical full suite is `888 passed in 537.16s`; headed-browser evidence covers 1920x1080, 1366x768, and 1280x720 with the accepted diff action and refresh recovery. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Chapter Version Compare now exposes a separate `canonicalSurface.historicalDependencySurface` when both ChapterVersions have accepted StoryCommit graph snapshots. The projector seeds on changed graph nodes/edge endpoints and traverses bounded target-snapshot semantic edges; the Inspector renders direct/downstream evidence separately from the current projection impact list. Missing snapshots remain explicitly unavailable, and `mutableDomainTablesHistorical=false` prevents false historical claims. Targeted tests and a headed 120-chapter browser fixture passed with zero console errors/warnings. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Accepted StoryCommit projection-capture failures now persist a source fingerprint/revision boundary and appear in Chapter History as explicit `STALE` operational evidence. Idempotent accept and `POST .../story-graph/snapshots/retry` recover only when the authoritative source boundary is unchanged; mutable-source changes and missing failure boundaries refuse historical backfill. Canonical replay now accepts only `reason=story_commit_accept` snapshots, so an ordinary observed `history_read` snapshot cannot upgrade a ledger-only historical view. The StoryGraph suite is `86 passed in 371.20s`; headed browser evidence verified visible failure → retry → cleared state with zero console errors/warnings. Product verdict remains `PARTIAL`.

> **Previous canonical verification (2026-08-14)**: `887 passed in 654.39s`; a second full-suite run was green after the historical dependency-surface increment. This remains a historical iteration record; the latest canonical result is the `888 passed in 537.16s` run above. Ruff, pyright (`0 errors, 0 warnings`), `verify.py`, protected-file verification, feature verification (`5/5`), and progress verification also passed in that earlier run. One earlier full-suite attempt had a single same-snapshot `snapshot_diff` failure; the test passed in isolation and the complete rerun passed without changing the contract. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: High-degree multi-selection external edges now use a SQLite-side count/type aggregate and bounded page query. `externalPageToken` is bound to the selected node ids, page size, and source fingerprint; changed selections and authoritative mutations produce explicit mismatch/expired errors. The Inspector starts with 60 external edges, merges the next page by edge id, and keeps remote endpoints as read-only focus evidence. Targeted unit/API tests and headed browser evidence pass; product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: `storyflow_graph_semantic_edge_index` now pairs with the SQLite node index under the same source fingerprint and schema. Warm `/story-graph/nodes`, `/neighbors`, and focused Depth 1/2/3 projections hydrate selected nodes and incident semantic edges without reopening the full JSON catalog; cold or invalidated indexes fall back and rebuild. The public read-model marker is `sqlite_node_index+semantic_edge_index`. Real browser recheck: Chapter API 200 with 17 neighbors, focused Depth 2 API 200, Character Inspector + Depth 2 rendered, and zero page/console diagnostics. Product verdict remains `PARTIAL` because true GPU virtualization, complete high-degree edge paging, full historical replay, and provider-backed AI execution remain incomplete.

> **Current implementation increment (2026-08-14)**: High-degree Inspector pagination now executes SQLite `COUNT` plus ordered `LIMIT/OFFSET` against the paired node/semantic-edge indexes before hydrating remote nodes. The API also returns a query-bound opaque `nextPageToken` and rejects changed-query or source-stale continuations; the browser prefers this cursor and retains offset compatibility. Full Graph cross-viewport boundary evidence uses a bounded CTE count/type aggregate plus a page payload query for ordinary Canvas working sets. New regression coverage proves page-size bounds, no duplicate continuation rows, cursor mismatch handling, and boundary-page correctness. This reduces read amplification but does not claim GPU virtualization or remove the explicit >900-selected-node compatibility fallback.

> **Latest implementation increment (2026-08-14)**: Multi-selection projection now uses the warmed `sqlite_node_index+semantic_edge_index` seam for the selection working set that feeds Chapter Intent and AI analysis. It resolves selected ids/source ids, reads only their incident semantic edge frontier, and hydrates bounded remote endpoint summaries; source-epoch mutation falls back to a rebuild and exposes the changed authoritative title. New tests prove the warm path works with `_read_catalog` unavailable and that the rebuild does not mutate StoryFact, StoryState, or StoryCommit. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Full Graph world-coordinate responses now expose a query-bound opaque continuation cursor (`pageSize`, `pageOffset`, `pageIndex`, `hasMore`, `nextPageToken`). The API rejects malformed, mismatched, or stale cursors; the Canvas keeps pages incremental and exposes an explicit next-page action. Targeted viewport/API tests: `4 passed`; ruff and frontend syntax pass. This is a transport/working-set seam, not a claim of complete server-side virtualization.

> **Current increment (2026-08-14)**: Full Graph warm viewport reads now reuse a
> rebuildable SQLite spatial/semantic-edge read model keyed by source,
> projection, candidate-set, workspace, and read-model schema fingerprints.
> `boundary_node_id` plus `boundary_page_token` provide exact paged
> cross-viewport semantic evidence without adding remote nodes to the Canvas.
> The related StoryFlow navigation/API suite is `92 passed`; a Canon immutability test
> covers delete-and-rebuild of the derived rows. The cold path still starts
> from the JSON read catalog, so this remains `PARTIAL` virtualization.

> **Latest increment (2026-08-14)**: Warm Full Graph viewport candidate reads
> now use a rebuildable SQLite `storyflow_graph_node_index` with scalar filter
> keys and one derived node payload per row. `storyflow_projection_epochs`
> triggers invalidate that index on authoritative changes; the next cold read
> rebuilds it and restores the exact content fingerprint. The public Graph
> contract is unchanged and reports `projectionReadModel=sqlite_node_index` on
> the warm path. New seam tests prove the warm viewport succeeds even when the
> full catalog reader is unavailable and that Chapter mutation invalidates and
> rebuilds the index without changing StoryFact/StoryState/StoryCommit. Search
> now uses the same indexed rows after the first build, so a warm search also
> avoids full-catalog deserialization.

> **Verification correction**: the older `887`/`885`/`884`/`882`/`881`/`878`/`875`/`874`/`871`/`868`/`866`/`864`/`862`/`861`/`853`/`851`/`850` figures retained below are historical iteration records; the current canonical full-suite result is the `888 passed in 537.16s` result at the top of this file.

> 最后审计：2026-08-08 (High-End Audit)。数据经过源码验证、测试验证和静态分析。
> **注意**: 本文件中的 Phase 状态和历史完成度都是开发方声明，不是验收结论。
> 当前可执行验证仅覆盖 `spec/features/*.yaml` 中的 5 个合同；详见
> `docs/high-end-audit/` 目录下的独立运行证据。
> **历史验证**: 2026-08-13 851 tests passed in 269.40s, ruff clean, pyright 0 errors, 5/5 合同特征 VERIFIED
> **功能矩阵更新**: NOT_STARTED 从 57 降至 37，TESTED 从 21 增至 41
> **历史回归**: 2026-08-13 `851 passed in 269.40s`, `ruff check .` 通过，`pyright src tests` 为 0 errors/warnings
> **本轮产品结论**: `PARTIAL`；StoryFlow P0 vertical slice 已接通，但完整历史重放、真实 Provider 完成态和高级候选编排仍未完成

## StoryFlow Canvas 迭代（2026-08-11 至 2026-08-12）

本轮目标是把分散的思维导图、剧情工作流、人物关系、时间线和世界地图入口收敛为真实的 StoryFlow Canvas。当前结论是 `PARTIAL`：P0 vertical slice 已接通，但不能宣称完整 StoryFlow 产品。

### 已实现

- 完成全局审查和实现计划：见 [`docs/storyflow-canvas/00-current-state-audit.md`](storyflow-canvas/00-current-state-audit.md) 至 [`06-performance-baseline.md`](storyflow-canvas/06-performance-baseline.md)。
- `StoryGraphProjector` 从真实 SQLite authoritative tables 投影可重建节点/语义边；不新增平行故事事实源。
- 新增 Graph API：graph、search、node、neighbors、context、layout、auto-layout；支持 view、focus、depth 1/2/3 和常用过滤。
- Studio 新增 StoryFlow Canvas：平移、缩放、fit/reset、节点拖动、框选、多选、聚焦、邻域展开、搜索、Inspector、Minimap、右键菜单、自动布局和布局保存/刷新恢复。
- StoryFlow 规划编辑现在可以直接创建作者 `PlanningNode`；表单通过 revisioned `plot_workspaces` 写入后立即回投影，刷新后可通过真实搜索找回，且不会写入 StoryFact/StoryState/StoryCommit。
- Story、Character、Timeline、World、Foreshadow、Context 视图共享同一 Graph API；旧思维导图、剧情工作流、时间线、世界地图、伏笔和人物关系入口优先路由到对应 StoryFlow view，旧渲染器仅保留为兼容 fallback。
- PlotThread 已加入真实垂直切片：typed reference 只建立可追溯读模型关联；显式 `plot_thread_origin/progress/resolved` StoryFact 才投影 `originates_from`、`advances`、`resolves` 生命周期边，Inspector 显示起源/推进/回收章节、事实 ID 和 SQLite provenance；同一事实中的 Foreshadow action 不会串线推进 PlotThread。
- PlotThread 过滤现在由同一 read model 的语义边反向建立 `plotThreadIds/plotThreadTitles` 索引，支持按稳定 ID 或标题筛选章节、伏笔及其关联节点；这不是前端第二数据源。目录缓存 schema 已升至 10，旧 payload 自动重建。
- Canvas 的剧情线/时间/章节/卷/状态/类型过滤会清除旧节点 focus，让 Graph API 根据过滤后的真实候选重新选择焦点；真实 120 章 fixture 在 1920×1080 与 1366×768 均验证 `Identity investigation` 返回 PlotThread + 关联章节子图，未保留第 120 章旧焦点。
- 空项目返回真实空图而非演示数据；Context provenance 缺失时明确标记，不伪造 AI 实际 token 输入。
- StoryFlow 选中 Flow 后可保存章节计划或直接生成下一章：`planning/generate` 把真实节点编译为 `ChapterIntent`，写入现有 `plot_workspaces`/Control Surface，并排队标准 `write-next` 任务；追加式章节边界、重复任务保护和 Canon 未变更前置测试已覆盖。
- Chapter Inspector 的 `StoryCommit / History` 会为每个真实 `ChapterVersion` 提供版本级 `查看编辑影响`；旧版本点击后通过其 SQLite `sourceId` 请求 `versionId`，只读报告保留 History 列表并显示 pinned version、superseded Commit、STALE StoryState 与记录的下游依赖。
- 工作区布局历史已独立于 StoryFact/StoryState 持久化为 revision snapshots；保存后支持 API/UI 撤销与重做，撤销后保存新布局会清理 redo tail；浏览器已验证两次保存、撤销、重做和刷新恢复。
- 画布现在明确区分“只读 · Canon”和“规划编辑”模式：默认只读；Story Port 连接、章节计划、候选分支和候选采纳/丢弃等规划写入前必须显式切换，AI 分析作为独立的只读报告任务可在 Canon 模式运行，且不会写入 StoryFact/StoryState/StoryCommit。Chapter Inspector 已打通打开章节、审查、重写、查看版本；章节工作台新增“查看本章关系”，并为无 `chapter_versions` 但真实存在的章节返回 truthful empty history。

### 尚未实现或仅部分实现

- 本轮继续补齐了真实卷/故事时间/剧情线筛选、只读 impact 分析、节点 history Inspector 与 `/story-graph/history` API；Canvas 目前按视口裁剪 DOM 节点/边，Minimap 仍读取完整 bounded subgraph。
- History 读取现有 SQLite ChapterVersion、StoryCommit、StoryFact、状态记录和 planning revisions，并新增可重建的 `storyflow_graph_snapshots` 观察缓存；两个已观察投影之间可返回节点/语义边 diff，同时明确 `graphSnapshotScope=observed_projection`、`graphSnapshotHistoryComplete=false`，不能把它冒充完整 replay。
- `/story-graph/neighbors/{node_id}` 已支持 `limit`/`offset`/`direction`/`types` 分页，Inspector 可按 `nextOffset` 增量读取高连接度节点，而不必一次载入全部邻居。
- `StoryGraphProjector` 已增加基于 authoritative 投影字段内容指纹的 `storyflow_graph_catalog_cache`；命中时复用可重建 catalog，指纹变化、缓存损坏或新进程启动后仍会从 SQLite 重建，不写入 StoryFact/StoryState。

- Story Ports 已进入节点 schema/UI 展示、OUTPUT→INPUT 拖拽、后端 edge-options 查询和基础边校验；`POST .../planning/edge` 已开放受 schema 约束的规划边创建。
- PlanningNode、Candidate Graph Overlay、候选采纳/废弃、候选集合分组读取、Flow → Chapter Intent、Flow → 标准 `write-next` 队列、实际 GenerationRun context manifest、持久化 StoryFlow AI 分析任务和 forecast→Candidate 接入已完成；accepted StoryCommit 后 Graph 通过 authoritative SQLite 重建自动反映新事实。候选集合来自现有 revisioned `plot_workspaces`，显式 `candidateSetId` 缺失时按 task/run/origin lineage 回退分组；采用/丢弃不绕过 Canon。
- AI 分析现已能排队、持久化结果并在刷新后从 `tasks.result` 恢复到 Inspector，但仍依赖已配置 Provider，且不会把分析结论自动变成 Canon。
- 投影健康已可视化：旧 ChapterVersion 的 pending StoryCommit 标记 `STALE`，阻塞 Review evidence 标记 `CONFLICT`，侧栏/节点 Inspector 显示只读 `graphDiagnostics` 与 `projectionHealth`。viewport culling 仍是 Canvas DOM 层的 bounded-subgraph 优化；neighbors 已具备后端分页，catalog cache 已覆盖当前 projector 读取的 authoritative 字段。章节影响分析和 exact observed projection diff 已提供只读解释；History 还提供 accepted StoryCommit / StoryFact / StoryState immutable ledger 的 commit-scoped canonical replay/diff，并明确其不重建 mutable entity tables 的历史状态。
- 当前 viewport culling 是 Canvas DOM 层的 bounded-subgraph 优化；neighbors 已具备后端分页，catalog cache 和 canonical ledger replay 都是可重建/只读边界，不写入 StoryFact/StoryState。accepted StoryCommit 后捕获的 full-catalog projection snapshot 现在可用于对应 commit 的 historicalGraph/replay；没有 snapshot 的旧边界仍明确降级为 ledger-only。任意未捕获历史时刻及 mutable source-table 的独立版本化仍未完成。
- 当前数据库中的关系、timeline 和空间事实并不完整，因此某些 view 可能只显示一个节点；实现保持事实诚实，不补硬编码关系。
- Story Bible projection 已接入同一 read model：published snapshot/entry、draft snapshot 和未发布 step overlay 均保留真实 `story_bible_*` provenance；Chapter 依赖已发布 snapshot，GenerationRun manifest 可解析到同一 `StoryBibleEntry`，但 Story Bible 编辑仍必须回到现有 25 步向导，Canvas 不直接写 Canon。

### Candidate set atomic-import addendum (2026-08-13)

Forecast branch import now has a backend-owned group seam:
`POST /plot-canvas/apply-candidate-set` validates the shared task-scoped
`candidateSetId`, writes all roots/steps/semantic overlay edges in one
`plot_workspace` revision and the corresponding `forecast_imports` audit rows
in the same SQLite transaction, then returns the complete candidate-set
metadata. The Canvas no longer performs N independent branch writes. Replaying
the same external branch ids is idempotent (`createdBranchCount=0`), while a
revision or audit failure leaves no partial overlay. The legacy `apply-branch`
endpoint remains available as a compatibility adapter.

### Character Inspector and Context evidence addendum (2026-08-13)

Character nodes now expose recent authoritative appearance fields in the
StoryGraph projection (`recentAppearanceChapters` and
`lastAppearanceChapter`). The Inspector groups current state, location,
emotion, state source chapter, direct Character/Faction semantic relationships,
PlotThread links, Foreshadow links, and recent appearances. Empty
`character_states` values remain visibly `未记录`; no prose inference is
introduced. `查看时间线` switches the same node into the shared chronological
projection, and `AI 分析` reuses the durable StoryFlow analysis task seam.

The headed 120-chapter SQLite browser run verified the Character Inspector and
the persisted GenerationRun Context View at 1920x1080 and 1366x768. Context
evidence showed 4 included and 1 excluded source, section/component bindings,
persisted input ranges, whole-run provider usage, and the explicit distinction
between contentChars/4 estimates and provider token authority. Evidence is in
`docs/storyflow-canvas/evidence/storyflow-20260813-character-inspector-*` and
`storyflow-20260813-context-character-*`. Product verdict remains `PARTIAL`.

### Context Graph snapshot addendum (2026-08-13)

The Writer pipeline now persists a deterministic `contextGraphSnapshot` inside
the existing `GenerationRun.input_reference.context_manifest`. It is a
metadata-only, rebuildable read model: source/focus nodes, included/excluded
manifest evidence, and recorded semantic selection edges. The Writer chapter
is the explicit focus target, so the projection does not emit self-loop edges
when a manifest item also names itself as its focus. No StoryFact, StoryState,
StoryCommit, or second front-end fact store is introduced.

The Context API recomputes the canonical node/edge payload and exposes stored
and computed `graphSha256`, `valid`, counts, truncation, and an explicit
integrity reason in `trace.contextGraphSnapshot`; Graph metadata exposes the
same bounded summary. Older runs without a snapshot remain `available=false`.
Unit/API coverage includes tamper detection and hash equality. A fresh real
120-chapter SQLite browser fixture returned HTTP 200 with 8 snapshot nodes, 10
snapshot edges, 6 included edges, 1 excluded edge, 0 self-loops, and matching
hashes. After refresh the Context Inspector still showed the same GenerationRun
and hash. Evidence is in
`docs/storyflow-canvas/evidence/storyflow-20260813-context-snapshot-1920.png`
and `...-1366.png`; the headed session reported 0 console errors/warnings.
This is a completed explainability vertical slice, not exact per-source
provider-token attribution; product verdict remains `PARTIAL`.

### Context progressive-depth addendum (2026-08-13)

Context View 的 Depth 1/2/3 控件现在真正贯通到后端：
`GET .../story-graph/context/{chapter_id}?depth=1|2|3` 会重新投影 bounded
semantic neighborhood，并重新叠加同一 GenerationRun 的只读 manifest。深度
不会改变 Writer 实际输入、token 总量或 Canon；Inspector 明确标记当前图深度
和这一边界。新增 unit/API 覆盖深度元数据与渐进节点数量，真实 120 章 SQLite
fixture 在浏览器中验证了 depth 1→2→3 请求、D3 Inspector、viewport culling、
1920×1080/1366×768 截图以及 0 console errors/warnings。证据见
`storyflow-20260813-context-depth3-1920.png` 和
`storyflow-20260813-context-depth3-1366.png`。

### Atomic Flow → Chapter Intent addendum (2026-08-13)

`StoryFlowPlanningService.save_intent_from_flow()` 现在先构造并校验整个
Chapter Intent 操作集，再通过一次 `plot_workspaces` revision 写入
`PlanningNode` 和全部语义边。并发 revision 冲突或任一语义校验失败都在
SQLite 提交前返回，不能留下“只有节点、缺少部分边”的规划状态；这仍然
是 planning overlay，不会绕过 StoryFact/StoryState/StoryCommit。新增回归
覆盖单 revision 历史和校验失败无残留。

### 本轮证据

| 检查 | 结果 |
|---|---|
| `pytest -q --tb=short` | `842 passed in 166.61s`（2026-08-13，本轮最终回归） |
| StoryGraph / Planning / GenerationRun 定向测试 | StoryGraph `59 passed in 61.57s`；新增 Chapter workflow evidence grouping/history、Context depth progressive disclosure、candidate-set grouping/decision/provenance/audit rollback、candidate comparison semantic deltas、atomic Flow → Chapter Intent、Story Bible、typed evidence projection/semantic-edge 与 AI action GenerationRun provenance coverage |
| `ruff check src tests` / `ruff check .` / `pyright` | 均通过（2026-08-13）；pyright `0 errors, 0 warnings` |
| `verify.py` / protected-file check | 均通过 |
| `scripts/verify_features.py` / `generate_progress.py --verify` | 合同 `5/5 VERIFIED`；P0 `5/5` |
| Browser E2E | 真实作品、空项目、搜索/聚焦、多视图、Inspector、Context 边界、拖动保存/刷新恢复、布局历史撤销/重做、Story Ports edge-options、持久 AI 分析任务边界、exact History diff、STALE/CONFLICT 投影健康、邻居 120→161 增量加载、Writing Studio ↔ StoryFlow 章节操作桥、只读/规划编辑模式、候选集合聚合/分支聚焦/采用、Flow → Chapter Intent 原子保存/刷新恢复、1366×768 与 1920×1080 已检查；新增 120 章真实 SQLite fixture：默认 9 节点焦点子图、Depth 2 为 116 节点/307 语义边、viewport culling、搜索定位、布局刷新恢复，以及选中真实 Chapter 后 `planning/generate` 200、PlanningNode 和 queued `write-next` 任务均已检查；另以真实 StoryCommit/StoryFact/StoryState 预置数据验证计划 `ACCEPTED`、实际章节和 Commit provenance；真实 Provider 完成态未执行 |
| Synthetic benchmark | 100/500/1000 target nodes 的冷投影/缓存命中实测记录见 [`06-performance-baseline.md`](storyflow-canvas/06-performance-baseline.md) |
| Latest StoryFlow browser addendum | History Inspector/API 200、exact `/story-graph/diff` 200、durable `/actions/analyze` history 200、STALE/CONFLICT health banner、neighbors 分页 API 200、volume/story-time/plot-thread query、viewport culling dataset、100+ fixture 真实节点与布局恢复、布局 history/undo/redo API 200、Writing Studio 章节动作与无版本历史 truthful 200、只读模式未发出 `edge-options`、规划编辑模式合法端口选项 200、Flow → Chapter Intent `POST`/刷新恢复 200、PlotThread lifecycle 搜索/Inspector/provenance/语义边 200、candidate-set API/decision/refresh 200、1920×1080 与 1366×768 截图；console 0 errors/0 warnings，最近 Graph/search/node/layout/candidates/planning requests 无 4xx/5xx；真实 Provider 完成态仍未执行 |

旧 Phase 表和历史验证数字保留为历史记录；本节是本轮 StoryFlow 的最新边界。

本轮最新浏览器补充已关闭旧 Browser E2E 行中“完整端口落边仍待验收”的历史表述：真实 fixture 已完成 `Chapter.events -> Location.presence` 的 schema chooser、planning edge 写入和刷新恢复；本次进一步验证只读模式不会发出 `edge-options`，规划编辑模式才启用 port write surface；Chapter Inspector 与章节工作台联动也已点通。“真实 Provider 完成态未执行”仍然有效。

### Chapter workflow evidence addendum (2026-08-13)

本轮继续补齐“点击章节后能看懂本章运行状态”的核心工作流切片。选中真实
`Chapter` 后，Inspector 复用 `/story-graph/nodes/{node_id}` 的 SQLite 邻接投影，
按人物/势力、地点、事件/场景、剧情线/冲突、伏笔/秘密、时间/设定分组，并
单独列出 `本章依赖 / 输入` 与 `本章改变 / 输出`。每条证据保留真实节点类型、
状态、方向和语义 edge label，点击可跳转到同一 Story Graph 的真实邻居节点。

章节选中后会自动读取现有 `/story-graph/history?nodeId=chapter:...`，以
`本章 Canon 变更 / StoryCommit` 显示不可变版本、commit、事实和状态变化摘要；
没有 durable history 时仍返回并显示 truthful empty history。该 slice 没有新增
数据库真源、没有从 prose 推断事实，也没有通过前端写入 StoryFact、StoryState
或 StoryCommit。新增 unit 覆盖真实 Chapter → Character/Location/Event/Foreshadow/
Fact 投影，真实 120 章 headed browser 在 1920x1080 与 1366x768 完成截图，点击
地点证据后再次读取真实 node detail；相关 Graph/node/history/neighbor 请求均为
HTTP 200，console 为 0 errors/0 warnings。证据见
`docs/storyflow-canvas/evidence/storyflow-20260813-chapter-inspector-*`。

本项状态：`IMPLEMENTED`（Chapter workflow read slice）；产品总 verdict 仍为
`PARTIAL`，因为完整 mutable-entity 历史重放、真实 Provider 完成态和高级候选
编排仍未完成。

### Character knowledge boundary addendum (2026-08-13)

Character Inspector now exposes the two explicit knowledge sets already
projected from SQLite `character_states.knowledge`: `她/他知道` and
`她/他不知道`. Each row retains recorded chapter and confidence metadata when
present, while the UI states that missing known data is not evidence of
ignorance. No knowledge table, StoryFact, StoryState, StoryCommit, or prose
inference was added. The real 120-chapter fixture seeded one Character state,
and headed browser verification at 1920x1080 and 1366x768 showed both sets;
Graph and Character node-detail requests returned HTTP 200 with zero console
errors/warnings. Evidence is in
`docs/storyflow-canvas/evidence/storyflow-20260813-character-knowledge-*`.

本项状态：`IMPLEMENTED`（Character knowledge read surface）；产品总 verdict
仍为 `PARTIAL`。

## StoryFlow AI action provenance addendum (2026-08-12)

The current vertical slice also records and restores a durable StoryFlow AI
analysis report through the existing SQLite task/GenerationRun boundary. The
Canvas Inspector exposes safe run metadata, provider/model labels, whole-run
usage, selected nodes, context-manifest counts/source types, and persisted
character-range coverage. Prompt bodies and credentials are not exposed, and a
missing or mismatched manifest is explicitly unavailable. Browser evidence is
recorded in `docs/storyflow-canvas/evidence/storyflow-20260812-ai-provenance-*`.
The product verdict remains `PARTIAL`: provider execution and broader AI
branching/context capabilities are not represented as complete merely because
the persisted report path is available.

## StoryFlow typed-evidence browser evidence (2026-08-12)

The real 120-chapter SQLite fixture now includes one verified StoryFact with
explicit Scene/Item/Secret/StoryGoal/Conflict/TimelinePoint/Knowledge
references. Headed browser search and focus selected Scene and Secret nodes;
Inspector showed the read-model boundary, reference id, source record, and
SQLite provenance. The Secret graph showed `Event -> reveals -> Secret`.
1920×1080 and 1366×768 screenshots are recorded in
`docs/storyflow-canvas/evidence/` as `storyflow-20260812-typed-evidence-*` and
`storyflow-20260812-typed-secret-*`. Graph/search/node/layout requests and the
Character/Timeline/World view switches returned 200; the session reported zero
console errors and warnings. Product status remains `PARTIAL`.

### Candidate comparison addendum (2026-08-13)

候选分支现在不仅按集合聚合，还能在 StoryFlow 侧栏打开只读比较 Inspector。
`StoryFlowPlanningService.compare_candidate_set()` 从同一 SQLite
`plot_workspaces` read model 计算两到八个候选方案的共同步骤、相对步骤增删和
语义边增删；`GET .../story-graph/candidates/compare` 只返回安全摘要、来源和
planning boundary，不创建比较表、不写 StoryFact/StoryState/StoryCommit。浏览器
已用真实 120 章 SQLite fixture 验证 1920×1080 与 1366×768：比较入口、评分/风险、
有序步骤、语义边差异和“在 StoryFlow 中定位”均可用，API 200，console errors/warnings
均为 0。刷新会回到普通节点 Inspector；再次打开比较时由 SQLite overlay 重新计算，
不依赖前端持久化状态。产品结论仍为 `PARTIAL`，provider-backed generation、跨运行
候选编排和完整历史重建未宣称完成。

## 当前产品完成度（审计后）

| 指标 | 数值 |
|---|---:|
| Total Features | 183 |
| NOT_STARTED | 0 |
| SCAFFOLD_ONLY | 0 |
| PARTIAL | 36 |
| FUNCTIONAL | 69 |
| TESTED | 77 |
| REFERENCE_PARITY | 0 |
| Product completion | **不计算主观百分比；以 `python scripts/generate_progress.py --verify` 的合同结果为准** |

合同验证结果不能替代完整产品 Feature Inventory、真实 Provider、恢复、并发和长篇耐久性验收。

## 质量基线

| 检查 | 当前结果 | 结论 |
|---|---|---|
| `python -m pytest -q --tb=short` | 842 passed（2026-08-13，本轮最终回归 166.61s） | 单元、公开 API、持久化集成和独立敌对测试通过 |
| `python verify.py` | 通过（2026-08-11） | 导入与基础对象烟测通过 |
| `ruff check src tests` / `ruff check .` | 均通过（2026-08-13） | 运行时、测试源码与全仓检查通过 |
| `pyright src tests` | 0 errors, 0 warnings, 0 informations（2026-08-13） | 全仓类型检查通过 |
| API integration | 通过（FastAPI `TestClient`） | 覆盖迁移确认、任务控制、SSE 重放、StoryState |
| Browser E2E（Phase 2/3 边界） | 通过 | 隔离 Studio：创建作品、入队写作任务、刷新后恢复任务状态、持久 SSE replay；并验证手动章节 v1/v2、历史 diff、追加式恢复与刷新后读取；未调用模型生成 |
| 真实 Provider E2E | 未配置有效用户凭据 | 未执行 |

## Phase 状态

| Phase | 名称 | 状态 | 关闭条件 |
|---|---|---|---|
| 0 | 可信审计 | ✅ 完成 | 8 份审计与 clean-room 证据已建立 |
| 1 | Architecture + Data Model | ✅ 完成 | 15 份 Architecture V2 文档、领域模型与 ER 图已冻结 |
| 2 | Database + Story System | ✅ 完成 | 迁移前验证 backup、StoryCommit、SQLite task runtime、独立 worker、兼容 API/CLI 任务收敛及浏览器 queued-state 验收均有证据 |
| 3 | Book + Chapter Core | ✅ 完成 | 原生 Project/Book/Chapter、创建元数据、版本 diff/追加式恢复、乐观并发、事务化状态校验与 StoryState stale 均已通过 API/浏览器验证 |
| 4 | Model Gateway + Router | ✅ 完成 | SQLite Provider/Model、凭据边界、9 角色路由、GenerationRun、worker 连接测试、错误分类、API/UI/浏览器与测试证据 |
| 5 | Document Ingestion | ✅ 完成 | Migration 7、附件→任务→解析分块→SQLite provenance、Studio/CLI/兼容入口、失败重试及浏览器/自动验证 |
| 6 | Memory + RAG | ✅ 完成 | SQLite BM25检索、项目/类型过滤、Studio API/CLI、重启重建与测试证据 |
| 7 | Planning / Story Bible | ✅ 完成 | Migration 8、25步状态机、Studio API 5端点、CLI bible 命令、task handler、20项测试全通过 |
| 8 | Writing Pipeline | ✅ 完成 | checkpoint-resumable pipeline、PRECHECK/REVIEW/QUALITY_GATE、revision loop、fact extraction、8项测试全通过 |
| 9 | Review Pipeline | ✅ 完成 | ReviewRepository、多维度审查、issues持久化、Studio API 4端点、8项测试全通过 |
| 10 | Export System | ✅ 完成 | ExportService、SQLite权威导出、导出历史追踪、Migration 9、6项测试全通过 |
| 11 | Continuous Writing | ✅ 完成 | ContinuousWritingService、批量章节写作、checkpoint恢复、与WritingPipeline集成 |
| 12 | Joint Review | ✅ 完成 | JointReviewService、跨章节一致性分析、Studio API 3端点、5项测试全通过 |
| 13 | Studio UI Enhancements | ✅ 完成 | 章节编辑器、Story Bible向导、任务管理页面、导航增强 |
| 14 | Task Dashboard | ✅ 完成 | 任务列表/详情/暂停/恢复/取消 API |
| 15 | Backup and Recovery | ✅ 完成 | 自动备份、手动备份API、健康检查 |
| 16 | Real-time Streaming | ✅ 完成 | SSE实时进度流、任务事件订阅 |
| 17 | Prompt Registry | ✅ 完成 | prompt_templates表、PromptRepository、Studio API 4端点、8项测试全通过 |
| 18 | World Bootstrap | ✅ 完成 | WorldBootstrapService、25步向导、Studio API 5端点、5项测试全通过 |
| 19 | Production Hardening | ✅ 完成 | 健康检查API、错误处理增强 |

## Phase 0 产物

- [功能矩阵](audit/01-reference-feature-matrix.md) 与 [当前审计](audit/02-current-novelforge-audit.md)
- [差距分析](audit/03-gap-analysis.md)、[UI](audit/04-ui-inventory.md)、[Backend](audit/05-backend-inventory.md)、[AI](audit/06-ai-pipeline-inventory.md)、[数据](audit/07-data-model-current.md)、[参考架构](audit/08-reference-architecture-analysis.md)
- [Architecture V2](architecture/01-system-architecture.md) 至 [备份恢复](architecture/15-backup-recovery.md)
- [Phase 编号对齐](phases/phase-numbering-reconciliation.md)、[Phase 1 规格](phases/phase-01-architecture-data-model.md)、[Phase 2 规格](phases/phase-02-database-story-system.md)

## Phase 2 实施证据（2026-08-07）

- `schema_migrations` checksum runner 已将集中库升级到 migration 4；旧 `db_version` 只保留兼容读取。
- 已有数据库在任一未应用 schema migration 前，先以 SQLite online backup 创建 `.novelforge-backups/schema-migrations/` 快照，验证源与备份完整性并写入 SHA-256 manifest；新空库与已完成迁移的库不会创建冗余备份。
- `StoryRepository` 提供 ChapterVersion、StoryCommit 原子接受、StoryFact 与 StoryState projection/replay；`TaskRuntime` 提供持久租约、checkpoint、状态机与 SSE replay；`LegacyMigrationService` 提供显式 fingerprint 预检、hash 备份与无覆盖导入。
- 当前阶段**不自动迁移**真实 `projects/` 中的项目；文件项目必须先调用预检、再以确认 fingerprint 调用迁移 API。
- 独立的真实 Studio 浏览器路径已验证创建作品、写作任务入队、刷新后的任务状态恢复和 `Last-Event-ID` SSE replay。UI 会持久保存活动作品、页面和写作 Task ID，只从 `GET /api/v1/tasks/{id}` 读取状态；它不保留浏览器内存任务。
- worker 现在将 401/403 归类为 `MODEL_CONFIGURATION`、429 为可重试 `RATE_LIMIT`、5xx 为可重试 `PROVIDER_TRANSIENT`、传输问题为可重试 `NETWORK`；未知处理器异常仍为非重试 `HANDLER_ERROR`。
- 完整 Studio 与旧 `/api/projects/*` 兼容 API 的所有模型工作流（世界观、草稿、写作、审查、修订、重写、规划、编排、联合审查、Provider 探测）均已收敛为 SQLite 任务；CLI `wizard`、`write`、`continuous` 也只入队。`tests/test_phase1_persistence.py` 覆盖 HTTP/CLI 入队而不启动 worker；隔离浏览器显示 `queued` 的真实持久状态且控制台 0 errors。
- 原生 Project/Book/Chapter 创建、列表、加载、编辑、删除已通过 SQLite authoritative workflow；新项目不生成 `project.json` 或章节 Markdown。旧文件项目仍只读，需显式迁移。
- Studio 章节工作台增加手动新建/编辑/删除路径；隔离浏览器验证编辑器保存新章节版本、刷新后仍显示章节内容。
- 章节保存支持 `baseVersion` 乐观并发校验（过期编辑返回 409），提供版本历史读取；章节状态机与 Review→ChapterVersion 关联已进入 authoritative repository。
- Phase 3 的既定 Book/Chapter Core 验收已通过：版本 diff/追加式恢复、事务化状态校验与 StoryState stale 均有 TestClient 与独立浏览器证据。它不包含 Review/Revision Pipeline；Phase 2 收口后，Phase 3 已正式验收。

## Phase 4 实施证据（2026-08-07）

- Migrations 5–6 add durable `agent_model_routes` and `generation_runs`, clear the legacy credential column after backup, and keep Provider/Model configuration authoritative in SQLite.
- Model credentials are represented by `credential_ref`; raw API Keys are never returned or written to SQLite, task data, run metadata, or logs. Windows saves submitted keys through user-scoped DPAPI; environment references are supported explicitly.
- `PersistentModelRuntime` and `PersistentMultiModelManager` route worker-side Planner/Writer/Reviewer/Wizard/connection-test calls through durable role resolution and GenerationRun recording. Error codes are propagated into the task state machine.
- Studio now edits multiple Providers/Models and nine Agent roles, supports legacy primary/review queue aliases, and exposes task GenerationRuns. Isolated browser verification recovered the configuration after refresh with zero console errors/warnings.
- Phase 4 tests are included in the 167-test suite; real third-party Provider E2E remains intentionally unexecuted without user credentials.

## Phase 5 实施证据（2026-08-08）

- `DocumentRepository` 将原始文件安全保存到项目附件目录，SQLite `reference_documents`/`document_chunks` 保存状态、指纹、解析器版本、字符范围和 checksum；旧 `content` 列不从新边界返回。
- `ingest-document` 由持久化 worker 执行，支持 `uploaded → parsing → indexed`/`failed`、原子重建 chunks、缺失附件显式错误和同附件 retry；HTTP 不再直接解析或写章节。
- Studio `/documents`、`/chunks`、`/retry` 和 `/import/chapters` 兼容边界，以及 `novelforge ingest <project> <file> --type ...` CLI 均复用同一任务流。章节源文件只索引，不提前物化为 Chapter。
- 隔离 Studio 已验证作品创建、导入页、真实 TXT 上传、`queued` 任务、独立 worker 完成、刷新恢复为 `indexed`、分块溯源 `0–65` 及控制台 0 errors/0 warnings。独立 API/worker 烟测也验证了 `queued → completed → indexed` 和 chunk 字符范围。
## Phase 6 Evidence (2026-08-08)

- `PersistentRAGRetriever` reads only indexed `reference_documents` and `document_chunks` from SQLite, rebuilds BM25 after restart, filters by project and explicit/classified document type, and returns chunk/document provenance, source fingerprint, checksum, and character ranges.
- Studio `GET /api/v1/books/{book_id}/rag/search`, the References workspace search form, and `novelforge rag-search` expose the same durable query boundary with explicit `bm25_fallback` and `degraded` state. No fake embedding is persisted.
- `tests/test_phase6_memory_rag.py` covers restart rebuild, filters, failed-document exclusion, validation, and Studio API behavior. Targeted checks pass: 4 tests, pyright 0 errors, ruff clean.

## Phase 7 Evidence (2026-08-08)

- Migration 8 creates `story_bible_workspaces`, `story_bible_steps`, and `story_bible_snapshots` tables with proper indexes and constraints.
- `StoryBibleRepository` implements the full 25-step ordered draft/confirm/publish state machine with snapshot versioning, checksum, and project truth projection on publish.
- Studio adds 5 new endpoints: `GET /api/v1/books/{book_id}/story-bible`, `PUT .../steps/{step_key}`, `POST .../steps/{step_key}/confirm`, `POST .../publish`, `POST .../steps/{step_key}/suggest`.
- `story-bible-suggest` task handler is registered in `LegacyTaskHandlers`, uses confirmed preceding steps as context, invokes model through durable runtime, and saves suggestion without changing confirmed state.
- CLI `novelforge bible <project> show|set|confirm|publish` exposes the same SQLite workflow.
- `tests/test_phase7_story_bible.py` covers: workspace creation/idempotency, draft/confirm/publish state machine, ordering enforcement, empty draft rejection, snapshot creation, suggestion behavior, all 5 Studio API endpoints, handler registration, and end-to-end suggestion save. 20 tests pass.
- Full regression: 201 passed, ruff clean, pyright 0 errors.

## StoryFlow latest semantic addendum (2026-08-11 to 2026-08-12)

This addendum supersedes older StoryFlow-only counts in this file; the repository-wide progress table and protected verification contracts remain unchanged.

- `StoryGraphProjector` now projects structured character knowledge boundaries (`knows` / `does_not_know`) and authoritative relationship rows as `Relationship` nodes with `connects` provenance. It does not derive knowledge from chapter prose.
- Context explainability now validates `GenerationRun` ownership, separates included/excluded manifest sources, preserves unresolved `sourceId`, and only reports a real graph `nodeId` when projection resolution succeeds. Mismatches are explicitly unavailable.
- Canvas edge selection has a semantic Edge Inspector; Story/Context auto-layout compresses chapter coordinates within the current bounded projection so late-chapter focus does not create an unreadable full-book gap. Layout persistence remains workspace-only.
- Latest targeted StoryFlow regression count: `35 passed` in `tests/test_story_graph.py`. Latest browser evidence is in `docs/storyflow-canvas/evidence/`, including current 1920×1080 and 1366×768 screenshots, semantic Edge Inspector, layout history controls, persisted prompt-range Inspector, Writing Studio actions, and read-only/planning-edit mode boundaries.
- Final clean-browser Context View evidence: 1920×1080 and 1366×768 screenshots show the bounded 10-node/19-edge GenerationRun graph; the long ContextSource provenance title wraps in the narrow Inspector. Latest clean session recorded only 200 responses for the StoryFlow/API requests and `0` console errors/warnings.
- The real 120-chapter browser fixture also exercised the selected-Flow “生成章节” action: `POST .../planning/generate` returned 200, persisted a `PLANNED` chapter-intent node for the next chapter, and queued the standard `write-next` task. The worker was disabled for this acceptance run, so no fake Provider completion or Canon mutation was claimed; evidence is in `storyflow-20260812-1920-generate.png` and `storyflow-20260812-1366-generate.png`.
- The accepted StoryCommit closure is now covered end to end: `WritingPipeline` accepts the canonical commit first, then revision-safely marks the linked plan `ACCEPTED`, records the actual chapter number/commit id, and adds a provenance-bearing `PlanningNode → leads_to → Chapter` edge. Overlay failure is surfaced as `ACCEPTED_PENDING_OVERLAY` without rolling back Canon; evidence is in `storyflow-20260812-1920-accepted.png` and `storyflow-20260812-1366-accepted.png`.
- Context View now projects a bounded read-only overlay from a persisted Writer `GenerationRun` manifest: included/excluded semantic edges, resolved canonical nodes, unresolved `ContextSource` evidence nodes, source/edge Inspectors, and explicit manifest mismatch handling. The overlay is never written to the canonical graph catalog, StoryFact, or StoryState; manual planning rejects its evidence edges.
- Context explainability now binds the Writer's actual assembled context to `contextSections` with exact-part hashes and to `writerInput.components` / `promptComponents` for system, plan, context, revision/task guidance, and planner output. Source rows expose manifest inclusion reasons, section binding, prompt location, focus chapter status, depth, and semantic evidence types; provider token totals remain authoritative while per-source values stay labelled as estimates.
- Context provenance now adds `contextRange` (assembled-context scope), `promptRange` (final Writer user-message scope), and runtime-rebased `persistedPromptRange` (the exact `GenerationRun.input_reference.prompt` scope). `promptLayout` records system/message boundaries. Ranges are emitted only for uniquely found component text; unavailable/ambiguous bindings remain explicit, and none are presented as provider token offsets.
- Story Bible is now a first-class read-model projection: the latest published snapshot and its 25 step entries are `StoryBibleEntry/CANON`; a reopened workspace retains that snapshot alongside the latest draft snapshot and mutable `DRAFT`/`PLANNED` steps. The catalog fingerprint covers `story_bible_workspaces`, `story_bible_steps`, and `story_bible_snapshots`; the schema version is 10 so older caches rebuild from SQLite. Chapter dependency and Context `story_bible` provenance both resolve to the same snapshot node, while the wizard remains the only write authority.
- The Writer pipeline reuses the same `StoryGraphProjector` for a bounded depth-1 prior-chapter projection and records whether that chapter was `committed`, `approved`, or `drafted`; it no longer labels every prior chapter as accepted. New unit/integration coverage verifies the binding, and the latest browser fixture evidence is `storyflow-20260812-context-explainability-1920.png`, `storyflow-20260812-context-explainability-1366.png`, and `storyflow-20260812-context-trace-sections-1920.png`.
- Story Ports are now browser-verified end to end: `Chapter.events -> Location.presence` offers only `happens_at`, writes a revisioned `PLANNED` edge, and survives refresh. The layout projector uses occupancy-aware row bands so dense semantic port cards do not intercept adjacent nodes; the new separation regression is covered in `tests/test_story_graph.py`.
- Candidate branches now persist a shared `candidateBranchId` across root, steps, and overlay edges. Inspector metadata exposes origin/position/decision, and adopt/discard transitions the complete branch group atomically in planning state. Real browser evidence is in `storyflow-20260812-ports-planning-1920.png` and `storyflow-20260812-ports-planning-1366.png`.
- A newly created empty work was opened in StoryFlow through the real Studio navigation and displayed only its SQLite-backed work root; no fake chapter/entity data was injected. Evidence: `storyflow-20260812-empty-1366.png`.
- Writing Studio ↔ StoryFlow Chapter bridge is now browser-verified: Chapter Inspector `打开章节` reaches the existing editor, `审查`/`重写` use the existing chapter-workspace action bridge, `查看版本` returns truthful empty history for a real SQLite chapter with no `chapter_versions`, and Chapter Workspace `查看本章关系` opens the same controller's Character View with the real chapter focus. Evidence: `storyflow-20260812-writing-links-1920.png`, `storyflow-20260812-writing-links-1366.png`.
- Canvas write boundary is now explicit and browser-verified: default `只读 · Canon` disables planning writes and a direct output-port click emits no `edge-options`; the non-Canon AI analysis report remains available for real selected nodes; `规划编辑` enables the legal port chooser, while the backend still owns semantic validation. Evidence: `storyflow-20260812-mode-1920.png`, `storyflow-20260812-mode-1366.png`, and the newer read-only analysis evidence below.
- Product status remains `PARTIAL`: observed projection history/diff, commit-scoped accepted-ledger canonical replay/diff, and conflict/stale visualization are implemented. Full historical replay of mutable entity tables, full provider-backed Context View, advanced candidate branching, and provider-independent AI success are not claimed.

- Context View now returns a bounded same-chapter Writer-run catalog and accepts an explicit generation_run_id only after book/chapter ownership validation. The Inspector exposes the selected run, component attribution (character count, binding/range status, labelled /4 estimate), and whole-run provider-usage authority. Unknown or cross-scope run ids return 404 rather than fabricating provenance. Browser evidence: storyflow-20260812-context-runs-1920.png, storyflow-20260812-context-runs-1366.png, storyflow-20260812-context-components-1920.png, and storyflow-20260812-context-components-1366.png.

## Direct PlanningNode authoring addendum (2026-08-12)

- The StoryFlow toolbar now exposes `新建规划节点` only after the author explicitly enters `规划编辑`. The modal writes a revision-checked author `PlanningNode` through the existing `plot_workspaces` planning service, including title, summary, status, optional anchor metadata, and—when the author leaves the default checkbox enabled—the semantic anchor edge in the same atomic revisioned operation. It does not write StoryFact, StoryState, or StoryCommit. Invalid anchor types are rejected before either object is persisted.
- The headed browser run submitted the form against a real SQLite fixture (`POST .../story-graph/planning/node` = 200), saw the selected node, `originates_from` edge, and `plot_workspaces` provenance in the Inspector, refreshed, and found the linked node in the default Chapter-focused subgraph. Evidence: `storyflow-20260812-planning-anchor-1920.png` and `storyflow-20260812-planning-anchor-1366.png`; console was 0 errors/0 warnings. The current backend contract additionally covers atomic invalid-anchor rollback.
- An explicitly unlinked planning node remains supported and can be found through Search/type filter; progressive disclosure still prevents a global Full Graph.

## StoryFlow World Graph addendum (2026-08-12)

World View now has a real hierarchical read projection instead of a linear
location list. `StoryGraphProjector` adds a `World` root derived from the
authoritative Book, preserves every persisted location as one `Location` node,
and projects `locations.parent_id + locations.type` into
`World → Region → City → Location` metadata and `parent_of` edges. It also
projects `controls` from `faction_states` / `location_states`, `present_at`
from `character_states`, and location `happens_at` overlays from timeline/state
rows. The API returns `meta.worldGraph` with source tables and
`spatialMap=false` when no coordinates exist; Canvas uses hierarchical layout
and the Inspector shows hierarchy path, current control and control history.

The catalog cache schema is now version 10 so an older payload is rebuilt from
SQLite rather than hiding the new World root. Unit/API coverage is in
`tests/test_story_graph.py`; the acceptance contract is item 43 in
`docs/storyflow-canvas/05-acceptance-plan.md`. Spatial coordinates, uploaded
map binding, distance and travel planning remain unimplemented; product status
is still `PARTIAL`.

## StoryFlow Foreshadow lifecycle addendum (2026-08-12)

Foreshadow View now projects an authoritative lifecycle rather than only a
static hook row. `foreshadows.created_chapter` supplies `planted`, explicit
typed `story_facts.entities` actions supply `advanced`/`resolved`, and the
projector emits provenance-bearing `advances`/`resolves` edges. Structured
association fields in `foreshadows.notes` emit typed `involves` edges to
characters, factions, locations, events, and plot threads. Inspector metadata
exposes lifecycle events, advance chapters, related entities, and current
stage. Unit and API coverage are in `tests/test_story_graph.py`. The real
SQLite browser fixture was verified at 1920x1080 and 1366x768; the Inspector
shows the planted/advanced/resolved records and merged typed associations,
and the fresh headed session reported zero console errors and warnings.
Product status remains `PARTIAL`.

Browser evidence: `storyflow-20260812-foreshadow-1920.png` and
`storyflow-20260812-foreshadow-1366.png`.

## StoryFlow typed reference addendum (2026-08-12)

The projector now handles extensible concepts that do not yet have a
first-class SQLite entity table through explicit typed references only. A
`PlotThread` declared in `StoryFact.entities` or structured `Foreshadow.notes`
becomes one deterministic read-model node; its metadata and provenance merge
the exact source table, record id, field, reference type, and reference id.
Untyped strings remain unresolved, and this projection does not create a new
canonical table or write StoryFact/StoryState. PlotThread ports and
port-specific semantic options are covered by unit/API tests. The catalog
schema is version 10 so older cached payloads are rebuilt.

The product verdict remains `PARTIAL`: this closes a traceability gap in the
Foreshadow/PlotThread vertical slice but does not make the broader planning,
candidate-branch, AI provenance, or historical-replay roadmap complete.

## StoryFlow PlotThread lifecycle addendum (2026-08-12)

PlotThread references now have a target-scoped lifecycle read model. Explicit
PlotThread actions in existing `story_facts` project to `originates_from`,
`advances`, and `resolves` semantic edges and expose ordered lifecycle events,
origin/advance/resolve chapter lists, current stage, related entities, and
fact/commit/SQLite provenance in the Inspector. A generic Foreshadow action in
the same fact is association-only and cannot advance the PlotThread. This keeps
the projection rebuildable and preserves the StoryFact/StoryCommit authority
boundary. The catalog schema is version 10 so older cached payloads rebuild.

The product verdict remains `PARTIAL`; this is a completed vertical slice, not
completion of the broader StoryFlow planning, candidate branch, AI context,
and historical graph roadmap.

## StoryFlow Chapter Intent context provenance addendum (2026-08-13)

The StoryFlow planning overlay now has a real read-only bridge into the
existing Writer context pipeline. A `write-next` task carrying
`storyflow_plan_node_id` reads the revisioned `PlanningNode` from SQLite
`plot_workspaces`, formats its structured Chapter Intent, and persists the
plan plus resolved Character/Location/PlotThread/Foreshadow sources in the
same `GenerationRun` context manifest. Each item carries an explicit
`selectionRole` and `provenanceKind`; no prose inference or second front-end
fact store is introduced.

The Graph Context API preserves the plan source and roles on ContextSource
metadata, `included_in_context` edge provenance, section binding, and the
persisted prompt range. The headed browser evidence is
`docs/storyflow-canvas/evidence/storyflow-20260813-context-intent-1920.png`
and `...-1366.png`: the real 120-chapter SQLite fixture shows
`chapter_intent`, planned chapter 120, source selection roles, and provenance;
the session recorded HTTP 200 requests with 0 console errors and 0 warnings.
Missing/stale plan ids produce explicit context warnings and leave the
existing write path intact. This is a provenance vertical slice, not live
provider completion or per-source Provider token accounting; the product
verdict remains `PARTIAL`.

The same manifest path now preserves the exact semantic planning edge types
(`affects`, `advances`, and future schema-compatible types) on both plan/source
items and `included_in_context` edge provenance. The Context Inspector labels
them as semantic evidence rather than deriving causality from layout. Fresh
headed-browser evidence is recorded in
`storyflow-20260813-context-intent-edge-1920.png` and
`...-1366.png`; both viewports showed the evidence with HTTP 200 requests and
0 console errors/0 warnings.

## StoryFlow extensible typed-evidence addendum (2026-08-12)

Scene, Item, Secret, StoryGoal, Conflict, TimelinePoint, and Knowledge now
have Story Ports and view membership. When a real verified StoryFact or
structured Foreshadow note explicitly declares one of these types, the
projector creates a deterministic evidence node with `referenceType`,
`referenceId`, source record provenance, and a Chapter materialization edge.
Explicit `relation` plus `sourceType/sourceId` values are checked by the same
semantic edge validator and can project `owns`, `reveals`, `advances`,
`causes`, and `knows`. The Inspector labels the read-model boundary; no new
Canon table or front-end fact source is created. Coverage is in
`tests/test_story_graph.py`; the product verdict remains `PARTIAL`.

## StoryFlow projection cold-path addendum (2026-08-12)

The projector now batches chapter-scoped fact, latest commit, latest version,
and blocking-review reads instead of performing them once per chapter. The
source fingerprint includes `chapter_versions`, so a new version invalidates
the rebuildable catalog even when `chapters.updated_at` is unchanged. The
100/500/1000 synthetic benchmark observed cold projection times of 151.10 ms,
185.70 ms, and 332.28 ms respectively, with cache-hit follow-up reads of
56.37 ms, 75.55 ms, and 120.48 ms. These are local observations; full dirty
incremental projection remains unimplemented and the product verdict remains
`PARTIAL`.

## Canvas keyboard and Minimap increment (2026-08-13)

The Canvas now focuses itself when the author begins a Canvas interaction and
supports scoped workflow shortcuts for zoom, fit/reset, progressive depth,
search focus, visible-node selection, selection clear, layout undo/redo, and
workspace save. Minimap clicks now recenter the current world coordinate and
return focus to the Canvas; the Minimap is an actual navigation control rather
than decoration. A headed 500-chapter fixture run verified the shortcut state
changes, real layout POST, Minimap transform change, 1920x1080 and 1366x768
rendering, and zero console errors/warnings. This is a workspace interaction
increment and does not change the broader `PARTIAL` product verdict.

## Candidate branch lineage read-model slice (2026-08-13)

The parent/child identifiers from the reforecast slice now have a safe query
surface rather than being exposed only as flat Candidate Set metadata.
`StoryFlowPlanningService.candidate_lineage()` reads the existing SQLite
`plot_workspaces` projection and returns bounded branch-root nodes plus semantic
`originates_from` edges. Exact set/branch/root matching is enforced; missing or
mismatched parents are reported in `missingParents` and never guessed. A
lineage/history read can retain `PLANNED` or `SUPERSEDED` parent roots after a
decision, while the active candidate decision list remains unchanged.

Studio now exposes
`GET /api/v1/books/{book_id}/story-graph/candidates/lineage` with bounded
`depth` and `ancestors`/`descendants`/`both` direction. The response explicitly
states `planning_overlay_only`, `canonicalMutation=false`, and
`canonicalSource=sqlite.plot_workspaces`; prompt/provider data is excluded.
Candidate Branch Inspector adds `查看谱系`, and the focus can be reconstructed
after a full refresh from persisted candidate ids.

Regression coverage includes active, adopted-parent, descendant, and
missing/mismatched-parent cases plus the API contract. Real headed browser
evidence is in
`docs/storyflow-canvas/evidence/storyflow-20260813-candidate-lineage-*`:
the 1920x1080 and 1366x768 captures show the 2-node/1-edge lineage Inspector,
the refresh recheck received HTTP 200, and the session reported 0 console
errors and 0 warnings. No Canon row was mutated; the disposable fixture worker
was disabled, so no live provider call is claimed. Product verdict remains
`PARTIAL`.

## StoryFlow analysis-to-candidate addendum (2026-08-12)

The durable analysis Inspector now offers `生成三个候选分支` as a real
planning action. The action is disabled in `只读 · Canon` and enabled only in
`规划编辑`; it hands the selected analysis scope to the existing forecast task
boundary rather than creating a second prompt path. Forecast and analysis task
results now carry the latest successful SQLite `GenerationRun` id resolved by
`task_id`, without exposing prompt bodies or credentials.

`PlotWorkspaceRepository.apply_branch()` preserves that id on the candidate
branch root, every step, and the source edge. The read model keeps the entire
branch as `CANDIDATE` planning overlay data; no StoryFact, StoryState, or
StoryCommit is written. Unit coverage is in `tests/test_story_graph.py`, and
the real headed browser evidence is
`storyflow-20260812-ai-branch-action-1920.png` plus
`storyflow-20260812-ai-branch-action-1366.png`. The fixture uses a persisted
provider-independent report, so provider-backed branch generation remains
PARTIAL until a configured model runtime is exercised.

Forecast now carries a real `storyflow.forecast` context manifest through the
existing PersistentModelRuntime. It records selected semantic StoryFlow
sources, planning canvas sources, recent chapters, open foreshadows, world
state, and author guidance by source type/count; `GenerationRun` remains the
only run authority. A safe run-id API and Candidate Inspector action expose
this metadata without Prompt bodies or credentials. Coverage includes the
PersistentModelRuntime integration test and book-ownership 404 boundary.

## StoryFlow Timeline dual-axis addendum (2026-08-12)

本轮补齐了审计发现的 Timeline 语义缺口：`StoryGraphProjector` 从真实
SQLite `timeline_events.event_time` 保留原始文本，同时生成可解释的
`storyTimeOrder`；Chapter 使用 `narrativeOrder`。Timeline API 返回
`meta.timelineAxes`，Canvas 使用横向 Narrative Order、纵向 Story Time 的
专用 chronological 布局，无法可靠解析的时间标签不会被伪造为日期。

真实 120 章浏览器 fixture 加入了第 120 章的 `10 years ago` flashback，已在
1920×1080 与 1366×768 headed 浏览器中验证视图切换、自动布局、轴提示、事件
Inspector、保存布局和刷新后的布局恢复；Story Graph、节点和 auto-layout
请求均为 200，console 为 0 errors/0 warnings。对应证据见
`docs/storyflow-canvas/evidence/storyflow-20260812-timeline-*.png`。

当前产品 verdict 仍为 `PARTIAL`：Timeline 双轴 vertical slice 已实现并验证，
但完整 mutable-entity historical replay、provider-independent AI 成功、完整
Context token attribution 和高级候选分支编排仍未宣称完成。

## StoryFlow candidate branch-set addendum (2026-08-13)

本轮将候选分支从“若干独立 overlay 节点”推进为可比较的候选集合读模型。
`StoryFlowPlanningService.candidate_sets()` 直接读取现有 revisioned
`plot_workspaces`，优先使用 forecast result 的 `candidateSetId`；历史 overlay
缺少该字段时按 `sourceTaskId + generationRunId + originNodeId` 做稳定回退，
不新增第二套数据库。新增 `GET .../story-graph/candidates` 返回集合/分支摘要、
步骤、状态、score/risks、origin 和安全的 task/run provenance，不返回 prompt 或
凭据。采用/丢弃仍通过原有 revision-checked planning decision，并保持 branch/set
metadata 在节点、步骤和 semantic overlay edges 上。

Canvas 侧栏现在按集合展示多个 alternatives，支持分支根节点聚焦、分支级采用/
丢弃和全部丢弃；只读模式禁用写入，规划编辑模式才解锁。真实 120 章 SQLite
fixture 通过 apply-branch 写入同一 set 的两个分支，浏览器验证了 `MIXED · 2 方案`、
分支聚焦、采用后 `PLANNED`、刷新恢复和 1920×1080/1366×768 布局；candidate、
graph、planning API 均为 200，console 为 0 errors/0 warnings。证据见
`docs/storyflow-canvas/evidence/storyflow-20260813-candidate-set-*.png`。

本轮继续把新 forecast 的集合身份下沉到后端：worker 以 `forecast:{taskId}`
生成 `candidateSetId`，同时写入任务结果与 `storyflow.forecast` manifest；Canvas
只转发该值，旧任务仍保留 lineage fallback。已用受控模型单测验证任务结果持久化，
且不会在浏览器重新推导新运行的集合身份。

当前代码的 headed browser 回归也已在真实 120 章 SQLite fixture 上完成：规划编辑
模式下 Candidate Set、分支聚焦、一个分支采用、Inspector 的 set/position/origin/
GenerationRun provenance 和刷新恢复均可用；1920×1080、1366×768 均截图，console
为 0 errors/0 warnings。新增证据为
`storyflow-20260813-authoritative-candidate-set-1920.png` 与
`storyflow-20260813-authoritative-candidate-set-1366.png`。

候选导入现在进一步收敛为后端原子 seam：`POST
.../plot-canvas/apply-candidate-set` 一次 revision 写入整组 root/step/edge，
校验共享 `candidateSetId`，并以 external branch id 支持重试幂等。定向 unit/API
测试验证 revision 冲突不会留下半组 overlay；真实浏览器已调用该 endpoint，刷新后
仍显示同一 `MIXED · 2 方案` 集合，并在采用一支后保留 `PLANNED + CANDIDATE`。
证据见 `storyflow-20260813-atomic-candidate-set-focused-1920.png` 与
`storyflow-20260813-atomic-candidate-set-focused-1366.png`。

本项仍是 `PARTIAL` vertical slice：provider-backed forecast、候选集合的跨运行
编排、候选冲突合并和自动生成分支尚未宣称完成。

### Accepted-commit projection capture addendum (2026-08-13)

`StoryRepository.accept_story_commit()` now captures a rebuildable
`storyflow_graph_snapshots` read-model boundary after the accepted Canon
transaction commits. The result carries the accepted `commitId`, snapshot id,
StoryState version, and `scope=observed_projection`; StoryFlow History labels
these entries as captured after an accepted StoryCommit. The capture is
best-effort and explicitly reported/logged if the read-model cache fails, so it
cannot weaken or roll back the authoritative commit. If the graph payload is
unchanged from a pre-commit query, the snapshot identity is fenced by the
accepted commit and StoryState version so History still records a distinct
boundary. This closes the gap where an accepted write could occur while no
StoryFlow request was open, but it does not reconstruct older mutable
entity-table states and does not change the product verdict from `PARTIAL`.

### Forecast worker-side candidate persistence addendum (2026-08-13)

The forecast handler now treats candidate import as part of the durable
StoryFlow task boundary. After the existing `PersistentModelRuntime` succeeds,
the worker writes the task-scoped candidate set through
`PlotWorkspaceRepository.apply_candidate_set_with_audit`, using the current
workspace revision and preserving the same `candidateSetId`, task id, and
GenerationRun id on the planning overlay and audit rows. Closing the browser
before task polling no longer loses the Canvas candidate overlay; the browser
path remains an idempotent fallback for legacy results and explicit retry.

Import and model execution have separate status contracts. A planning
projection failure keeps the model task result and GenerationRun durable while
returning `candidateImport.status=failed`, `retryable=true`, and the explicit
error; no partial overlay or Canon mutation is accepted. Coverage is in
`tests/test_phase4_model_gateway_router.py` for both worker-side success and
failure recovery. This is a real controlled-runtime vertical slice, not a
claim that an external provider is configured or that advanced cross-run
branch orchestration is complete. The product verdict remains `PARTIAL`.

### Recoverable forecast task addendum (2026-08-13)

Completed forecast tasks whose candidate overlay was not persisted can now be
recovered after StoryFlow is reopened. The new read endpoint returns a safe
task-scoped summary only; the Planning Edit action reuses the atomic,
revision-checked candidate-set import transaction and preserves task/run
provenance. Repeated imports are idempotent, the recoverable item disappears
once its set is present, and the action remains planning-only. The new API
regression test also asserts that StoryFact and StoryState counts do not change.
Browser evidence for the reopened recovery state must still be captured before
this seam can be considered complete in the final E2E matrix; the product
verdict remains `PARTIAL`.

### Impact explanation evidence addendum (2026-08-13)

The read-only Chapter Inspector impact action now carries an explicit evidence
contract. The projector annotates each direct/downstream result with its
`impactBoundary`, `evidenceStatus`, and deduplicated recorded SQLite evidence;
the mapping recognizes `StoryFact`, `StoryCommit`, `StoryState`, and revisioned
`plot_workspace` planning sources. Canon and planning/candidate boundaries are
not conflated, and missing provenance is reported as `node_projection_only`
rather than inferred from layout or text. The API also returns boundary and
evidence counts, while the Inspector renders those labels and the source ids.

Regression coverage is in `tests/test_story_graph.py` for Canon and Planning
evidence plus the API contract. The real headed-browser recheck used the
120-chapter SQLite fixture: Chapter 120 returned 44 impacted nodes, the impact
request returned HTTP 200, the request log contained no 4xx/5xx responses, and
the 1920x1080 and 1366x768 captures are recorded in
`docs/storyflow-canvas/evidence/storyflow-20260813-impact-evidence-*.png`.
The session reported 0 console errors and 0 warnings. This closes the
source-backed impact explanation vertical slice; it does not claim complete
historical graph replay or full chapter-edit impact mutation analysis. The
product verdict remains `PARTIAL`.

### Candidate-set audit transaction addendum (2026-08-13)

候选集合导入现在由 `PlotWorkspaceRepository.apply_candidate_set_with_audit`
在一个 SQLite transaction 内同时写入 `plot_workspaces` overlay、revision
history 和 `forecast_imports` audit rows。重复 external branch id 仍保持幂等；
新增回滚测试证明 audit foreign-key 失败时不会留下半组规划节点或前进的
workspace revision。真实浏览器通过当前 `POST
.../plot-canvas/apply-candidate-set` 导入两支候选，刷新后在 StoryFlow
sidebar 重新显示同一集合；1920x1080 与 1366x768 证据见
`storyflow-20260813-candidate-audit-*.png`。这不改变 Canon 边界，也不意味着
provider-backed forecast 或跨运行候选编排已经完成。

### Chapter edit impact addendum (2026-08-13)

### Chapter version comparison addendum (2026-08-13)

`StoryGraphProjector.chapter_version_compare()` and
`GET .../story-graph/chapter-version-compare/{node_id}` now compare two real
immutable `ChapterVersion` ids. The response includes a bounded deterministic
unified text diff, attached StoryCommit summaries when present, and the
current recorded dependency surface. That surface is explicitly
`scope=current_projection`; historical mutable entity tables are not inferred.
The endpoint is read-only with `canonicalMutation=false` and
`canonicalSource=sqlite`. Unit/API coverage and headed-browser evidence are
recorded in `docs/storyflow-canvas/evidence/storyflow-20260813-version-compare-v1-*.png`.

The version-compare response now also exposes `canonicalSurface`. This surface
reads immutable `story_facts` (or the commit's extracted-facts fallback) and
the accepted `story_projections.payload` boundary from SQLite. It reports the
real superseded-to-accepted commit transition, added/removed fact evidence,
and StoryState keys before and after the edit. When both accepted commits have
valid full-catalog projection snapshots, it additionally returns
`historicalGraph.scope=accepted_commit_snapshot_diff` and
`graphReplayComplete=true`; missing capture remains an explicit ledger-only
fallback. `commitEvidenceComplete` and `stateComplete` distinguish missing
legacy evidence. Compatibility `graphRefs` remains labeled
`current_catalog_references`; the UI does not confuse it with the separate
historical snapshot. The fresh 1920x1080 and 1366x768 browser evidence is
recorded in
`docs/storyflow-canvas/evidence/storyflow-20260813-historical-graph-*.png`.
The product verdict remains `PARTIAL`.

本轮新增 `StoryGraphProjector.chapter_edit_impact()` 和
`GET .../story-graph/chapter-impact/{node_id}`。它把真实
`ChapterVersion`、已接受/已 supersede 的 `StoryCommit`、当前
`StoryState` 与有界语义下游投影组合为只读报告，按 future chapters、affected
facts、planning dependencies 和 stale/conflict hazards 分组；`versionId`
可以固定到指定 immutable version，响应明确返回 `canonicalMutation=false`
与 SQLite evidence boundary。

Chapter Inspector 已增加 `编辑影响` 操作和可滚动结果区，显示版本、Commit
状态、StoryState stale、后续章节、事实来源和需要重新提取/接受的警告。新增
projector/API 回归测试，且用 120 章真实 SQLite 夹具完成 headed browser
验收：Ch.87 的 v2、superseded commit、stale state、8 个后续章节依赖和 26 个
事实依赖均在 UI 中可见；1920×1080 与 1366×768 证据见
`docs/storyflow-canvas/evidence/storyflow-20260813-chapter-edit-impact-*`，
该会话 0 console errors / warnings，chapter-impact 请求 HTTP 200。该切片仍
是只读解释，不宣称自动重写 Canon 或完整历史时刻重建；产品结论保持
`PARTIAL`。
> **Latest Context Graph coverage (2026-08-13)**: Writer, `forecast`, and `storyflow-analyze` now persist the same metadata-only Context Graph snapshot through existing `GenerationRun` manifests. Generic GenerationRun trace exposes safe integrity/count/focus/hash metadata; per-source provider token attribution and historical mutable-table replay remain intentionally unavailable. Targeted Context/forecast/analyze tests pass; product verdict remains `PARTIAL`.
## Forecast/Analysis Context Graph Inspector slice (2026-08-13)

The existing SQLite `GenerationRun.input_reference.context_manifest` seam now
has a book-scoped Context Graph endpoint and StoryFlow Inspector action for
restored `forecast` and `storyflow-analyze` results. The UI reads the bounded
metadata-only snapshot on demand and shows source nodes, included/excluded
semantic edges, focus ids, counts, SHA-256 integrity, and the explicit prompt /
credential exclusion boundary. Missing snapshots remain unavailable; no
StoryFact, StoryState, StoryCommit, or front-end fact store is written.

Targeted regression tests: 2 passed. Headed browser evidence used a real
120-chapter SQLite fixture at 1920x1080 and 1366x768; the new endpoint returned
HTTP 200, the Inspector showed 3 nodes / 2 edges (1 included / 1 excluded),
the hash matched, long hashes wrapped without horizontal overflow, and the
session reported zero console errors and warnings. Evidence:
`docs/storyflow-canvas/evidence/storyflow-20260813-analysis-context-graph-*`.
Product verdict remains `PARTIAL` pending the broader P1/P2 roadmap.

## Forecast Context Graph browser addendum (2026-08-13)

The missing browser half of acceptance item 67 is now closed. A fresh 120-chapter
SQLite fixture restored a durable `forecast` task into the planning overlay,
focused its candidate branch, and used the existing `查看生成上下文` action to
read `generation-runs/{id}/context-graph`. The Candidate Inspector showed the
forecast GenerationRun id, 2 metadata-only source nodes, 1 included semantic
edge, matching graph/snapshot hashes, and the prompt/credential exclusion
boundary. A full page refresh preserved the candidate set in SQLite; selecting
the branch again restored the same Context Graph.

Headed browser evidence at 1920x1080 and 1366x768 is recorded in
`docs/storyflow-canvas/evidence/storyflow-20260813-forecast-context-graph-*`.
The browser request log had no StoryFlow 4xx/5xx responses and the clean session
reported 0 console errors and 0 warnings. This verifies the Forecast read and
recovery path using fixture metadata; it does not claim a live third-party model
invocation. Product verdict remains `PARTIAL`.

## StoryFlow analysis -> forecast provenance addendum (2026-08-13)

The StoryFlow Inspector action `生成三个候选分支` now carries the completed
`storyflow-analyze` task id into the `forecast` task. The worker validates that
the source task belongs to the same book, is completed, and has a successful
`GenerationRun` before invoking the model runtime. A bounded summary/findings/
next-steps extract is passed as a planning input and is explicitly not Canon.

The forecast `GenerationRun` manifest, metadata-only Context Graph, candidate
set, and candidate branch preserve both the source analysis task id and its
GenerationRun id. Recovery and adoption remain revisioned planning overlay
operations; this path does not write `StoryFact`, `StoryState`, or
`StoryCommit`. Invalid, missing, cross-book, incomplete, or run-less analysis
references fail before a provider call.

Regression coverage includes the successful handoff and pre-provider rejection
tests. Headed browser evidence from the real 120-chapter SQLite fixture is in
`docs/storyflow-canvas/evidence/storyflow-20260813-analysis-derived-*`; the
branch Inspector shows the analysis/run provenance and the Context Graph shows
the `storyflow_analysis` source, included semantic edges, and integrity hash.
The fixture intentionally uses persisted metadata and makes no live provider
call. Cross-run provider orchestration, per-source token attribution, and the
broader P1/P2 roadmap remain incomplete; product verdict remains `PARTIAL`.

## Candidate branch reforecast lineage slice (2026-08-13)

Implemented a second planning-only continuation seam: a candidate branch can be
selected in Planning Edit and sent back through the existing durable `forecast`
task as its parent. The request carries three explicit ids:
`sourceCandidateSetId`, `sourceCandidateBranchId`, and
`sourceCandidateRootNodeId`. The worker resolves the root from SQLite,
validates that the root belongs to the requested set and branch, rejects
inactive/conflicted parents before model invocation, and inherits a prior
analysis task id only when it is already recorded on the parent.

The existing `GenerationRun` manifest now records a bounded
`candidate_branch` source with `relation=derived_from` and `planningOnly=true`.
The child candidate set/branch persists the parent set, branch, and root ids in
the same `plot_workspaces` metadata and the existing planning read model. The
model input receives only bounded parent branch context; it is explicitly not
Canon. Candidate child nodes are imported atomically into the revisioned
planning overlay and the worker verifies that no StoryFact or StoryState row is
created.

Regression coverage: the forecast worker success path, read-model lineage,
invalid/inactive parent rejection, Studio request parity, and the existing
Story Graph/Studio regression set pass. Headed browser evidence from the real
120-chapter SQLite fixture is in
`docs/storyflow-canvas/evidence/storyflow-20260813-candidate-reforecast-*`.
The final browser action produced a real `POST .../forecast` with all three
parent ids, HTTP 200, no console errors/warnings, and a queued task while the
fixture server intentionally had its worker disabled. This proves the UI/API
boundary, not a live third-party provider invocation. Product verdict remains
`PARTIAL`.

## StoryFlow analysis evidence navigation slice (2026-08-13)

`storyflow-analyze` now persists the author selection boundary in the existing
GenerationRun context manifest. Each selected node records its analysis role,
stable focus id, depth, the real semantic edge types observed from the
authoritative Story Graph node-detail neighborhood, and an explicit
`author_selected_storyflow_analysis` provenance kind. This is metadata-only:
the handler does not write StoryFact, StoryState, StoryCommit, or planning
Canon.

Analysis finding evidence ids are now clickable in the StoryFlow Inspector.
The click resolves the node type, switches to its shared StoryFlow projection,
focuses the evidence node at depth 1, and reloads the book-scoped Graph API.
The persisted analysis report remains visible as a read-only task artifact;
the Inspector changes to the authoritative evidence node instead of opening a
second data source.

Targeted regression coverage passed:

- `tests/test_creation_workflow.py -k storyflow_analysis`: 1 passed;
- `tests/test_story_graph.py -k "context or generation_run"`: 8 passed;
- `ruff` for changed Python files, `node --check` for the StoryFlow module.

Headed browser evidence used the real 120-chapter SQLite fixture at
1920x1080 and 1366x768. Clicking a persisted finding evidence id produced the
real `view=character&focus=character:fixture-character-01` Graph API request;
relevant requests were HTTP 200 and the console reported 0 errors and 0
warnings. The screenshots also make the remaining limitation explicit: a
high-degree 48-node Character projection is still visually dense after radial
layout. This slice therefore improves traceable navigation but does not claim
full high-degree readability. Product verdict remains `PARTIAL`.

## Context inclusion explainability slice (2026-08-13)

The existing `GenerationRun.input_reference.context_manifest` now produces a
metadata-only `explainability` record on each Context source and on the
rebuildable Context Graph snapshot. It preserves the recorded inclusion or
exclusion reason, selection role, focus/depth, planned chapter, semantic edge
evidence, and the explicit `generation_run.input_reference.context_manifest`
boundary. The Context API and Inspector render this as “Why this source is
here”; absent causality is reported as not recorded rather than inferred from
layout, prompt prose, or token estimates.

Targeted Context/GenerationRun regression coverage is green, and headed-browser
evidence at 1920x1080 and 1366x768 is recorded in
`docs/storyflow-canvas/evidence/storyflow-20260813-context-explainability-*`.
This closes the explainability display seam but does not claim exact per-source
provider-token attribution, live provider execution, or full StoryFlow
completion. Product verdict remains `PARTIAL`.

## Character View progressive clustering slice (2026-08-13)

The Story Graph API now accepts `presentation=expanded|clustered`. The default
remains `expanded` for compatibility. Character View requests `clustered` and
receives a rebuildable `meta.presentation` read model built from the already
bounded authoritative SQLite projection. Repeated Chapter/Event/Scene activity
is grouped into deterministic presentation-only clusters carrying exact member
ids, type counts, chapter range, source semantic edge types, and provenance.
No cluster or presentation grouping edge is written to Canon.

The Canvas now shows the authoritative-vs-displayed counts, Activity Cluster
cards, a view-only cluster Inspector, member navigation, Expand group, and an
All evidence nodes toggle. Expanded members use the normal node-detail API.
`layoutSaved` preserves user workspace positions when the presentation policy
is recalculated; layout remains separate from StoryFact/StoryState.

Targeted regression coverage includes deterministic cluster membership,
non-Canonical boundaries, API presentation metadata, and layout persistence.
Headed browser evidence uses the real 120-chapter SQLite fixture at
1920x1080 and 1366x768. It verified 48 real nodes → 11 displayed objects / 3
clusters, real member Inspector navigation, expansion to a real Chapter node,
the expanded 48-node toggle, Timeline/World view switching, HTTP 200 Graph API
requests, and zero console errors/warnings. Full Fit at 1366px is intentionally
smaller; focused Character View is the readable default. High-degree semantic
edge density, advanced aggregation for other views, and the broader P1/P2
roadmap remain incomplete; product verdict remains `PARTIAL`.

## Read-only AI analysis action slice (2026-08-13)

The StoryFlow UI no longer incorrectly gates `storyflow-analyze` behind
Planning Edit. In the default `只读 · Canon` mode, the toolbar, multi-selection
Inspector, and Character Inspector can queue the existing durable analysis task
when the selection contains real Story Graph nodes. Presentation-only Activity
Clusters and ContextSource nodes are excluded from the task payload; they remain
display projections rather than analysis authority.

Planning writes remain separately protected: creating a PlanningNode, saving a
Chapter Intent, generating a chapter, generating candidates, and adopting or
discarding candidates still require explicit Planning Edit. The read-only AI
analysis banner states that its report is stored in `tasks.result` and does not
write StoryFact, StoryState, or StoryCommit.

Verification for this slice:

- `tests/test_story_graph.py::test_story_graph_api_uses_real_sqlite_and_layout_endpoint`
  now compares StoryFact/StoryState/StoryCommit counts before and after queueing
  analysis; `tests/test_creation_workflow.py::test_storyflow_analysis_is_a_durable_non_canon_task`
  continues to verify the worker-side boundary. Both pass.
- A real headed Playwright session against the 120-chapter SQLite fixture selected
  `character:fixture-character-01` and `character:fixture-character-12` in read-only
  mode and received HTTP 200 from `POST .../story-graph/actions/analyze`; the
  request body contained exactly those real ids and the response was a durable
  queued task. The fixture worker was disabled, so no live Provider completion is
  claimed.
- Evidence is recorded at
  `docs/storyflow-canvas/evidence/storyflow-20260813-ai-analysis-readonly-1920.png`
  and `...-1366.png`; the browser session reported zero console errors/warnings.

This fixes the action permission boundary but does not complete provider-backed
AI execution, automatic Canon mutation, or the broader P1/P2 StoryFlow roadmap.
Product verdict remains `PARTIAL`.

## Character-state semantic edge normalization (2026-08-13)

The Character-state projection now has one normalized relationship path. A
structured `character_states.relationships` value is emitted once with its
canonical edge type and readable label; the raw SQLite object remains in edge
metadata as provenance instead of leaking into the Inspector as a Python
dictionary string. `GRAPH_CATALOG_SCHEMA_VERSION` moved to `10`, invalidating
old rebuildable catalog payloads after this projector contract change.

The regression test verifies the exact `suspects` edge, state id, and reason.
A fresh 120-chapter SQLite browser fixture was checked at 1920x1080 and
1366x768; both known/unknown knowledge rows and semantic relationship labels
were readable, all Graph/node requests returned HTTP 200, and the headed
session reported zero console errors or warnings. Product verdict remains
`PARTIAL`.

## Explicit bounded Full Graph and Story activity presentation (2026-08-13)

Full Graph is now an explicit `view=all` entry. With no author-selected focus,
the backend returns `focus=null`, `layoutStrategy=grid`, and enforces the
book-scoped `limit`/`edge_limit` bounds instead of choosing an implicit Chapter
focus. The toolbar and sidebar expose the bounded boundary to the author.

`presentation=clustered` is available for Story and Full Graph as well as
Character. It retains structural anchors as real SQLite-projected nodes and
groups dense repeated activity into deterministic, view-only aggregates with
exact membership metadata. The Canvas can toggle back to
`presentation=expanded`; no cluster is stored as a StoryFact, StoryState,
StoryCommit, or semantic edge.

Real headed-browser evidence against a 120-chapter SQLite fixture is recorded
at `docs/storyflow-canvas/evidence/storyflow-20260813-full-graph-1920.png`
and `...-1366.png`. The run observed 514 authoritative nodes, 1884 semantic
edges, 95 clustered display objects, 7 activity groups, HTTP 200 Graph/API
requests, zero console errors/warnings, and layout save/refresh restoration.
This is a bounded-density slice, not a claim of full graph virtualization or
complete high-degree readability; product verdict remains `PARTIAL`.

## Verification after Full Graph increment (2026-08-13)

- `python -m pytest -q --tb=short`: **844 passed**.
- `ruff check .`: **All checks passed**.
- `pyright src tests`: **0 errors, 0 warnings, 0 informations**.
- `python verify.py`: **ALL TESTS PASSED**.
- `python scripts/verify_features.py`: **P0 VERIFIED 5/5; 100%**.
- `python scripts/generate_progress.py --verify`: **passed**.
- `python scripts/check_protected_files.py`: **Protected verification artifacts unchanged**.
- `node --check src/web/static/studio-storyflow.js`: **passed**.
- `git diff --check`: **passed** (only the repository's existing LF/CRLF
  normalization warnings were reported by Git).

The synthetic Story Graph benchmark was rerun at 100/500/1000 target nodes;
observed cold depth-1 / cached depth-3 timings were 112.20/52.27 ms,
219.07/108.93 ms, and 492.39/153.44 ms respectively. These are local query
harness observations, not an FPS or production capacity claim.

## Server-side viewport projection increment (2026-08-13)

The Graph API now accepts a complete world-coordinate viewport boundary via
`x_from`, `x_to`, `y_from`, `y_to`, and `viewport_padding`. The projector lays
out the complete filtered candidate set first, applies the separate saved UI
workspace coordinates, then returns only the viewport slice. `meta.viewport`
reports whether the boundary was requested, its counts, truncation, and
`layoutScope=filtered_candidates`. This is a rebuildable SQLite read model;
viewport coordinates are not StoryFact/StoryState/StoryCommit.

The Full Graph / All evidence browser path now debounces those parameters after
Canvas transform changes, replaces the bounded viewport page, keeps the
authoritative totals visible, and leaves native DOM culling enabled. A fresh
500-chapter real SQLite fixture projected 1,891 nodes / 7,488 edges. The
headed run observed 1,024 loaded / 752 DOM nodes at one viewport and 272 loaded
/ 208 DOM nodes after zooming to 31%; pan and zoom issued real HTTP 200
viewport Graph requests. Evidence is recorded at
`docs/storyflow-canvas/evidence/storyflow-20260813-viewport-1920.png` and
`...-1366.png`, with zero console errors/warnings.

This closes the server-side incremental read boundary, but does not claim GPU
rendering, complete virtualization, or a fully readable 1,891-node overview.
Product verdict remains `PARTIAL` until the remaining P1/P2 and full acceptance
requirements are implemented and verified.

## Structured Context token attribution (2026-08-13)

Context View now exposes the persisted GenerationRun token boundary without
inventing provider offsets. The SQLite-backed context response includes
whole-run Provider usage metadata, per-source `contentChars/4` estimates, and
the persisted prompt-range authority. ContextSource Inspector rows repeat the
same boundary and mark source estimates as estimates. A real 500-chapter headed
browser run verified the structured banner and source Inspector at 1920x1080
and 1366x768, with zero console errors/warnings. This improves explainability,
but does not provide exact per-source tokenizer/provider offsets because the
current GenerationRun schema does not persist them; product verdict remains
`PARTIAL`.

The projector harness was also rerun after this increment for 100/500/1000
target nodes: cold depth-1 / cached depth-3 observations were 133.17/55.24 ms,
238.42/114.51 ms, and 388.62/160.81 ms. These remain local query observations,
not a production capacity claim.

## Chapter Intent preview and confirmation increment (2026-08-13)

The StoryFlow writing actions now expose a real confirmation boundary. The
Canvas first calls `POST .../story-graph/planning/intent` with `save=false` and
renders the backend-derived structured Chapter Intent: goal, required
characters, locations, plot threads, foreshadowing, preconditions, outcomes,
source nodes, and target chapter. This read-only preview does not change the
planning overlay or any StoryFact/StoryState/StoryCommit row.

After author confirmation, the existing revisioned save path creates the
PLANNED Chapter Intent node. The “生成章节” action uses the same preview and,
only after confirmation, passes the target chapter and optional guidance into
the existing `story-graph/planning/generate` endpoint and standard `write-next`
task runtime. A real 500-chapter SQLite browser fixture verified the preview,
save/reload PLANNED node, generation preview cancellation, and 1920x1080 /
1366x768 layout. Evidence: `storyflow-20260813-intent-preview-1920.png`,
`storyflow-20260813-intent-preview-1366.png`,
`storyflow-20260813-intent-generate-preview-1920.png`,
`storyflow-20260813-intent-generate-preview-1366.png`, and
`storyflow-20260813-intent-planned-1920.png`. Browser console errors/warnings
were zero. The asynchronous preview now exposes a busy state immediately, and
the narrow modal keeps its confirmation actions sticky and visible while the
structured fields scroll. This improves the authoring boundary but does not claim
provider-backed generation completion; product verdict remains `PARTIAL`.

## Story Health read-only projection increment (2026-08-13)

Added a deterministic `story_health()` read model to the existing
`StoryGraphProjector` and exposed it at
`GET /api/v1/books/{book_id}/story-graph/health`. It reports only explicit
stalled PlotThread, unresolved Foreshadow, and inactive/never-recorded
Character signals, with lifecycle/appearance evidence, chapter gaps, source
ids, and a clear no-AI-inference boundary. Resolved/closed and non-Canon nodes
are excluded, and the endpoint is read-only.

The StoryFlow sidebar now renders a bounded Story Health summary and lets the
author focus a real signal node in its Story/Character/Foreshadow view. The
browser path was verified against an opt-in 500-chapter SQLite fixture at
1920x1080 and 1366x768; the click-through to a real Foreshadow focus returned
HTTP 200 and browser diagnostics remained empty. Evidence is recorded at
`docs/storyflow-canvas/evidence/storyflow-20260813-health-1920.png` and
`...-1366.png`.

Targeted and full verification after this increment:

- `python -m pytest -q tests/test_story_graph.py -k "story_graph_health or story_graph_api_uses_real_sqlite" --tb=short`: **3 passed**.
- `python -m pytest -q --tb=short`: **847 passed in 194.82s (0:03:14)**.
- `ruff check .`: **All checks passed**.
- `pyright src tests`: **0 errors, 0 warnings, 0 informations**.
- `node --check src/web/static/studio-storyflow.js`: **passed**.
- `python verify.py`: **ALL TESTS PASSED**.
- `python scripts/verify_features.py`: **P0 VERIFIED 5/5; 100%**.
- `python scripts/generate_progress.py --verify`: **passed**.
- `python scripts/check_protected_files.py`: **Protected verification artifacts unchanged**.
- `git diff --check`: **passed** with only LF/CRLF normalization warnings.

This increment improves deterministic author-facing diagnosis but does not
claim AI diagnosis, automatic health-driven writing, exact high-degree graph
virtualization, or completion of the remaining P1/P2 roadmap. Product verdict
remains `PARTIAL`.

## Full Graph cross-viewport semantic boundary (2026-08-14)

The authoritative viewport projection now reports exact
`crossBoundaryEdgeCount`/type counts and a capped semantic edge sample with
`loadedEndpointId` and a read-only remote endpoint summary. The Full Graph
toolbar exposes the count; the selected-node Inspector exposes sampled remote
relationships as recorded SQLite evidence and can focus a new authoritative
query. Remote nodes are not silently added to the current page and no Canon
table is written. The exact `/story-graph/neighbors/{node_id}` page remains the
high-degree inspection path. Unit/API/navigation contracts pass; headed browser
evidence for this boundary is still pending in this iteration, so the product
verdict remains `PARTIAL`.

## Long-lived Canvas freshness increment (2026-08-13)

Added a read-only `story-graph/changes` API over the existing immutable
`storyflow_graph_snapshots` observed-projection boundary. The projector compares
the client snapshot with the current SQLite-authoritative projection, reports a
truthful scoped diff or explicit resync requirement, and exposes the current
source StoryCommit without creating a second event log. Unit/API coverage now
checks unchanged polling, Accepted Commit detection, current source commit
metadata, and missing-snapshot resync behavior.

StoryFlow polls the seam every 12 seconds. A safe read-only session refreshes
automatically after a relevant Accepted Commit; Planning Edit, an active port
connection, or an unsaved layout instead keeps the current workspace and shows
`CANON UPDATE · REFRESH REQUIRED` with an explicit Refresh button. A real
headed browser run against a 120-chapter SQLite fixture accepted two external
StoryCommits, verified both paths at 1920x1080 and 1366x768, observed HTTP 200
for the Graph/changes/node/history/health/layout requests, and reported zero
console errors/warnings. Evidence is recorded under
`docs/storyflow-canvas/evidence/storyflow-20260813-freshness-*`.

This closes a live-session freshness gap but does not claim server-push
delivery, full GPU virtualization, exact per-source provider tokens, or full
P1/P2 completion. Product verdict remains `PARTIAL`.

Verification for this increment: `python -m pytest -q --tb=short` reported
**850 passed in 217.89s (0:03:37)**. `ruff check .`, `pyright src tests`,
`verify.py`, `scripts/verify_features.py`,
`scripts/generate_progress.py --verify`, `scripts/check_protected_files.py`,
`node --check src/web/static/studio-storyflow.js`, and `git diff --check` all
passed. This feature still does not change the overall `PARTIAL` product
verdict.

## Legacy visualization entry convergence increment (2026-08-13)

The base Studio router now sends the historical Mind Map, Plot Workflow,
Timeline, World Map, Foreshadowing, and Character Relations entries into the
single StoryFlow controller with explicit view intent. The mappings are
`mindmap -> story`, `plot -> story`, `timeline -> timeline`,
`world-map -> world`, `foreshadowing -> foreshadow`, and
`characters -> character`. This closes the normal navigation split without
deleting the old `PAGES.*` renderers or their APIs, which remain compatibility
fallbacks.

A headed browser run against the real 120-chapter SQLite fixture exercised all
six mappings, verified HTTP 200 Graph requests and zero console errors or
warnings, and captured 1920x1080 and 1366x768 evidence under
`docs/storyflow-canvas/evidence/storyflow-20260813-legacy-*`. The product
verdict remains `PARTIAL`: legacy fallback internals, true large-graph
virtualization, and provider-backed completion still require follow-up.

## Writing pipeline → StoryFlow projection increment (2026-08-13)

Added a regression contract that exercises the production
`PersistentTaskWorker -> LegacyTaskHandlers -> WritingPipeline` path with a
deterministic provider-shaped test double. A successful task now has explicit
coverage for the full boundary: the chapter becomes committed, the
`StoryCommit` is accepted, extracted `StoryFact` rows are linked to that
commit, and the same `StoryGraphProjector` exposes the new Chapter/Fact as
`CANON` nodes with semantic edges. The accepted commit also captures the
observed projection snapshot without bypassing the StoryRepository boundary.

The reusable acceptance harness is
[`scripts/run_storyflow_deterministic_write.py`](../scripts/run_storyflow_deterministic_write.py);
it only operates on an explicitly supplied disposable SQLite root and emits a
safe summary rather than prompt or credential payloads. A headed browser run
left StoryFlow open while the harness wrote Chapter 121. The existing
read-only freshness poll discovered the new projection, and selecting the
chapter showed the newly extracted Canon fact, StoryCommit history, semantic
edges, and SQLite provenance at 1920x1080 and 1366x768. Evidence:
[`storyflow-20260813-writing-before-1920.png`](storyflow-canvas/evidence/storyflow-20260813-writing-before-1920.png),
[`storyflow-20260813-writing-after-1920.png`](storyflow-canvas/evidence/storyflow-20260813-writing-after-1920.png),
[`storyflow-20260813-writing-after-1366.png`](storyflow-canvas/evidence/storyflow-20260813-writing-after-1366.png).

This closes the provider-independent task-to-Canon projection seam for the
acceptance fixture. It does not claim a live external provider completion,
provider quality, full historical mutable-entity replay, or completion of the
remaining P1/P2 roadmap; the product verdict remains `PARTIAL`.

## Workspace recovery and focused node actions increment (2026-08-13)

The Canvas now refreshes the complete workspace shell after Hide/Delete, so a
hidden real node immediately appears in a recoverable sidebar section. Restore
keeps the change local until the author saves the layout, then re-centers the
real node and reloads its SQLite-backed Inspector. The backend regression now
covers persisted `collapsed`, `pinned`, and `hidden` flags surviving projection
refresh without changing `StoryState`; the navigation contract covers the
shared controller routing for Character, Foreshadow, Location, and Faction
actions.

The headed browser run verified Hide -> Restore at 1920x1080 and 1366x768, and
verified that opening a Character source changes to the shared radial Character
View while preserving the `character:*` focus. The session reported zero
console errors/warnings. Evidence is recorded under
`docs/storyflow-canvas/evidence/storyflow-20260813-hidden-restore-*` and
`storyflow-20260813-node-action-focus-1366.png`.

This is a workspace/navigation increment only. It does not claim full graph
virtualization, complete mutable-entity history replay, or live external
provider quality; the product verdict remains `PARTIAL`.

## Canon-before-overlay recovery and Context source boundary (2026-08-13)

The optional planning fulfillment after Writer Canon acceptance now has a
durable recovery seam. When `StoryRepository.accept_story_commit()` succeeds
but the revisioned planning overlay loses a race, the production Worker /
Handler / WritingPipeline path persists `storyflow_plan_status=
ACCEPTED_PENDING_OVERLAY`, the plan node id, accepted chapter id, and
`story_commit_id` in `tasks.result`. `StoryFlowPlanningService` exposes safe
reconciliation candidates and accepts a retry only after rechecking task
ownership/completion, accepted Commit ownership/status/chapter/number, and the
strict `PLANNED -> ACCEPTED` lifecycle. The Studio Inspector discovers these
candidates from SQLite, gates retry behind Planning Edit, and calls the
revision-checked `/planning/reconcile` endpoint. Reconciliation writes only the
planning overlay and is idempotent; it never repeats the canonical transaction.

Context assembly now records schema v3 availability in the existing
`GenerationRun.input_reference.context_manifest`: authoritative project style
and author constraints are included when present, while the legacy file-backed
MemorySystem is explicitly marked `not_included` because it is not an input to
the SQLite-authoritative Writer pipeline. Context View renders the persisted
availability boundary. The port-aware SVG endpoint rendering and its legacy
fallback are covered by the navigation contract.

## Full Graph incremental viewport merge (2026-08-14)

Full Graph expanded evidence mode now merges successful server-side
world-coordinate viewport pages into the current client read model. It no
longer replaces the bounded graph after a pan, preserves unsaved workspace
layout/visibility fields on overlap, deduplicates page keys, and schedules the
expensive projection request after pointerup. The toolbar reports authoritative
`loaded / total` counts and the Canvas exposes loaded-node/edge diagnostics
while retaining DOM viewport culling.

Against the real 500-chapter SQLite browser fixture, the initial bounded page
was `1200` nodes / `3415` edges out of `1891` authoritative nodes. A completed
pan into a new coordinate region grew the merged read model to `1891` nodes /
`3963` edges; the viewport requests returned HTTP 200 and the headed browser
session reported zero console errors/warnings at 1920x1080 and 1366x768.
Observed local request times were 4.009s and 1.654s, not production SLA
claims. This is progressive loaded-projection merging with DOM culling, not
true graph virtualization or complete cross-page edge paging; product verdict
remains `PARTIAL`.

Unit, integration, API, and navigation-contract coverage for the boundary
projection passes locally. The Inspector treats the boundary as the current
world-coordinate page (not the client cache), so a remote node cached from an
earlier page is still shown as off-page evidence. A fresh headed browser run
against the real 500-chapter fixture verified the corrected semantics at
1920x1080 and 1366x768: the Inspector showed the bounded remote sample and a
click issued a focus query for the remote Character. Evidence is recorded in
`docs/storyflow-canvas/evidence/storyflow-20260814-boundary-*`. The product
verdict remains `PARTIAL` because true thousands-node virtualization, complete
mutable-entity history replay, and live provider completion are not claimed.

## StoryFlow multi-selection working-set increment (2026-08-14)

Added `StoryGraphProjector.selection_projection()` and the book-scoped
`GET .../story-graph/selection` endpoint. It resolves a selected id set against
the SQLite-authoritative catalog, returns internal semantic edges separately from
bounded external edges, preserves missing ids truthfully, and marks the response
read-only with `canonicalMutation=false`. Remote endpoint summaries remain
projection evidence; they are not silently appended to the current Canvas page.

The multi-selection Inspector now presents this response as a StoryFlow working
unit. Its existing Save Intent, Generate Chapter, and AI Analyze actions keep the
same selected ids, while the semantic summary explains what the actions are
operating on. External rows can focus a remote endpoint that is not currently
loaded by issuing a fresh authoritative `focus` query; selection response
matching is set-based so canonical server ordering cannot let a late response
overwrite a newer user selection.

Against the real 500-chapter SQLite browser fixture, the Character/Event
 selection displayed `2 nodes`, `1 inside edge`, `216 outbound edges`, and the
recorded `participates_in` edge at 1920x1080 and 1366x768. A remote Chapter
focus returned HTTP 200 and selected `chapter:fixture-chapter-0005`; the headed
session reported zero console errors/warnings. Evidence is recorded under
`docs/storyflow-canvas/evidence/storyflow-20260814-selection-*`.

This increment does not claim complete high-degree edge pagination, true graph
virtualization, AI inference for unrecorded relationships, or a complete
selection editor. The overall product verdict remains `PARTIAL`.

## Boundary cursor integrity after Full Graph search focus (2026-08-14)

The expanded Full Graph search path now preserves the active bounded
projection. A searched node is fetched through the viewport read model rather
than replacing the page with an unbounded type-specific graph. Inspector-only
boundary pagination stores the exact viewport coordinates that signed its
cursor; ordinary viewport merges cannot erase an active cursor, and a terminal
page releases the preservation latch. Search focus no longer recenters the
Canvas before continuation, avoiding a false `422` caused by mixing a cursor
with a different coordinate window.

The 500-chapter headed fixture was rechecked at 1920x1080 and 1366x768 with
no NovelForge page/API errors and the boundary action visible in both views.
The final screenshot pass also exposed one Browser Use harness-only
`clipboard bridge is unavailable` diagnostic; it is recorded in the evidence
README and is not a page error. The result remains `PARTIAL`: this closes
cursor correctness and interaction continuity, not true GPU virtualization,
full predicate pushdown, or complete high-degree edge paging.

## StoryFlow provider rejection and typed-action/graph increment (2026-08-20)

Provider-supplied action names are now normalized before construction and
unknown names reach the typed `ActionValidator` as durable rejected proposals.
The persistent simulation-round task still completes its round bookkeeping,
records the exact rejection reason, emits no actor action event, and preserves
the selected provider id; a focused regression covers this fail-closed path.

The Simulation workspace now exposes the complete 29-value `ActionType`
ontology instead of a hand-picked subset. Analyze also renders the persisted
dynamic graph as a deterministic, bounded SVG viewport (36 visible nodes with
omitted-count evidence, typed styling, edge arrows, and click-to-inspect
selection). It contains no synthetic timer/progress state: graph nodes and
edges come only from the run projection, and the interaction remains
`canonicalMutation=false`.

A fresh headed browser session against the isolated real-book copy verified
the graph SVG, 36-node bounded view, all 29 action options, graph-node agent
selection, and zero console errors/warnings. This closes the local visibility
and rejection evidence only; provider availability, large-scale graph
virtualization, and production scheduler/performance remain `PARTIAL`.

## StoryFlow world entity inventory and graph ontology increment (2026-08-20)

The WORLD workspace now exposes immutable `snapshot.world` as a read-only
entity inventory with counts and bounded previews for Characters, Factions,
Locations, Relationships, Timeline, Foreshadows, Plot Threads, Secrets,
Goals, Conflicts, Items, Facts, Narrative Obligations, World Rules, and Power
Systems. This makes absent/empty Canon data visible rather than implying a
complete Agent roster from a partial projection.

The persisted Simulation graph projection now includes the same non-Agent
narrative entity classes as typed nodes, and projection versioning invalidates
old caches when the ontology changes. Against the isolated real-book run the
API rebuilt `851` nodes (`50` Character/Faction/Location nodes plus `801`
snapshot-backed narrative nodes) with `projectionVersion=2` at that probe; the bounded SVG
still renders 36 nodes and remains read-only. A fresh browser check confirmed
the inventory, graph count, and zero console errors/warnings. This is complete
local entity visibility, not a claim of true large-scale GPU virtualization or
provider-backed cognition; product status remains `PARTIAL`.

## StoryFlow scheduler governance and live graph edges increment (2026-08-20)

Tier C scheduling no longer treats the mere presence of a persistent goal as a
round trigger. Goals remain explainable ranking evidence, while passive Agents
activate from Sandbox events, relationships, conflicts, recent activity,
author pins, or an explicit per-Agent `activateOnGoal` policy. This closes the
default-all-passive-provider-slot loophole without changing Tier A/B policy or
author pin semantics; focused scheduler and invalid-action regressions pass.

The graph projection is now version `3` and rebuilds version `2` caches. It
adds replayed `present_at`, `controls`, relationship, knowledge, narrative
reference, and event-target node references while retaining legacy raw edge
ids for compatibility. The EventSource timeline refreshes the graph read model
after persisted Sandbox events, so the Analyze view does not depend on a
synthetic timer. This remains a deterministic, bounded SVG/read-model path;
production-scale virtualization and external-provider scheduling remain
`PARTIAL`.

The final current-code isolated-book browser/API pass observed `0 active / 29
discovered` on the default Tier C preview, then streamed a persisted `TALK`
round into the same WORLD view; the graph changed from `851 nodes / 0 edges /
sequence 0` to `851 nodes / 1 edge / sequence 1` without a manual page reload.
The run detail remained `canonicalMutation=false` and `snapshotFreshness=CURRENT`;
the fresh console had `0` errors and `0` warnings.

## StoryFlow event inspector and structured environment setup increment (2026-08-20)

Timeline events now have a durable, book-scoped detail endpoint and lazy Studio
inspector. Opening an event replays the immutable snapshot plus the persisted
prefix and returns Actor before/after state, Agent-local Memory, pre-event
Context, causal Why evidence, State Delta hashes, and related graph nodes/edges.
The endpoint rejects cross-book/run event ids, keeps `canonicalMutation=false`,
and the focused API regression proves the Canon fact count is unchanged. The
browser inspector was checked after a fresh run selection and after a page
reload; all six evidence sections loaded from the persisted event id with zero
console errors or warnings. A stale-summary race during asynchronous run
switching is now ignored instead of issuing a cross-run detail request.

Environment Setup is no longer JSON-only in the Simulation workspace. DRAFT,
PREPARING, READY, PAUSED, and PAUSED_BUDGET runs expose structured summaries
and an author edit form for initial location, goals/activity, decision
frequency, memory policy, clock duration, action budget, narrative randomness,
conflict resolution, communication/world rules, and provider assignment. A
book-scoped configuration endpoint persists the normalized object and refuses
changes while a run is RUNNING or terminal. Focused API coverage and a fresh
headed browser save/reload check passed; the configuration remains Sandbox-only.
The overall product verdict remains `PARTIAL` because the real external
provider gate, production-scale scheduling/virtualization, and full acceptance
matrix are still open.

## StoryFlow current-tree verification increment (2026-08-20)

The current tree now passes the full repository suite with `1010 passed in
15:40`, all five protected feature gates (`CW-001`, `MEMORY-001`,
`REVIEW-001`, `STORY-001`, and `WRITE-001`) report `VERIFIED`, and Ruff,
JavaScript syntax, compileall, protected-file, and diff checks are clean. The
deterministic benchmark completed 200/1,250/5,000 persisted events at
221.626/84.263/32.237 events per second with `canonicalMutation=false` and
`provider=deterministic-runtime-only`. This is a reproducible local runtime
baseline, not an external-provider SLA or production-scale guarantee; product
status remains `PARTIAL`.

## StoryFlow environment generation, evidence linking, and history increment (2026-08-20)

Environment Setup now has a deterministic `SimulationConfigurationGenerator`
that derives run-scoped agents/policies, goals, horizon, clock, memory,
conflict, and provider-assignment defaults from the immutable World Snapshot.
The API supports preview and explicit persistence; unknown author fields and
budget settings are preserved, and the response records
`source=immutable_simulation_world_snapshot` plus `canonicalMutation=false`.
Repeated snapshot creation at the same Canon boundary is idempotent when the
payload is identical and returns the already-persisted snapshot id, so creating
multiple counterfactual runs cannot turn a normal workflow into a uniqueness
error.

Run history now has migration 45 append-only `DELETE` semantics. The Studio
button soft-deletes a non-running run from the default list while retaining the
run, snapshot, ledger, replay, and history evidence for direct inspection;
archive/unarchive cannot erase or revive a deleted record. Branch comparison
exposes explicit narrative dimensions (characters, factions, locations,
relationships, goals, conflicts, secrets, knowledge, foreshadowing, plot
threads, world variables, opportunities, and risks) in addition to raw state
keys.

Context compilation accepts a run-scoped approximate token budget and reports
the effective cap and omitted sections. Analyst reports persist a stable
`sections` map plus deterministic risk/chapter-plan fields; event evidence is
clickable in Analyze and opens the same durable event inspector used by the
timeline. Graph evidence now explicitly labels `SIMULATION`, run id, round,
and ledger sequence. A fresh current-code headed browser session verified
snapshot reuse, generated configuration persistence after reload, dynamic
report-to-event evidence linking (including after local report refresh),
SIMULATION graph metadata, and soft-delete HTTP 200/history retention on an
isolated real-book copy. The initial duplicate-snapshot probe was a deliberate
failure discovery; after the fix the fresh console had zero errors and
warnings. External-provider execution, production-scale virtualization, and
the full acceptance matrix remain open, so the product verdict remains
`PARTIAL`.

## StoryFlow final local verification after migration 45 (2026-08-20)

The current tree now passes `1014 passed in 16:19`; the five protected feature
gates remain `VERIFIED`. The focused StoryFlow suite is `97 passed` and phase
persistence is `19 passed`. The current deterministic benchmark completed
200/1,250/5,000 events at 252.568/90.539/32.360 events per second with stable
state hashes, `runStatus=COMPLETED`, `canonicalMutation=false`, and
`provider=deterministic-runtime-only`. This is a local deterministic ledger
baseline, not an external-provider SLA or production-scale virtualization
claim; overall status remains `PARTIAL`.

The explicit replay seam is now named and exposed:
`SimulationRepository.rebuild_simulation_state()` rebuilds only from the
immutable snapshot plus append-only SimulationEvent ledger, while the
book-scoped `/simulation/runs/{run_id}/replay` endpoint returns the rebuilt
state/hash with `canonicalMutation=false`. A regression deletes the mutable
graph projection rows, rebuilds the state, and verifies that the graph
projector restores the exact hash. External-provider execution, production
scale, and the complete browser acceptance matrix remain open.

## StoryFlow isolated browser acceptance increment (2026-08-20)

A fresh headed Playwright session against current code and an isolated copy of
the real `玖安余陈` book completed the locally available workflow through the
planning handoff. The run `9f2505d34f5545baae0a87d09e1c3448` was paused and
resumed, received an author `WORLD_VARIABLE` intervention, was forked through
the UI, started, and advanced with a persisted `WAIT` round. Analyze created a
deterministic report, a grounded Analyst query, and a cross-run comparison;
Interact persisted a Character Chat record and a 29-agent Character/Faction
Survey (all responses explicitly `PROVIDER_UNAVAILABLE` because no provider
was configured). History then created and adopted proposal
`83da18cc1ba146c1bc1310b00130f737`, created ChapterIntent 15, and queued the
durable `write-next` task `46006551086340a1bea9a9659ae72c90` at 0% without
changing Canon. The run detail remained `snapshotFreshness=CURRENT` and
`canonicalMutation=false`; the snapshot/current Canon hash stayed
`4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`.

After closing the exploratory session, a new browser session loaded the
persisted run and History overlay after refresh. It reported zero console
errors/warnings and no HTTP 4xx/5xx responses; the adoption, Planning node,
ChapterIntent, and queued write-next evidence were still visible. This is
authoritative local browser evidence for the implemented path, not a claim
that the external-provider, production-scale, or full 23-step acceptance
gates are complete. Overall status remains `PARTIAL`.

## StoryFlow writing handoff overlay closure (2026-08-20)

The durable History `write-next` handoff now carries the adopted Planning node
id as explicit task context. The worker uses that author-owned id only after a
quality-gated StoryCommit is accepted, then marks the Planning node `ACCEPTED`
and links the committed chapter; Simulation still never mutates Canon directly.
Focused API and writing-integration regressions passed (`1` and `3` tests),
including assertions for the accepted node and StoryCommit linkage.

A fresh headed browser proof on an isolated project/run
(`75844b774e4a4813b8e2a2e78ced3fed`) queued and completed task
`62f85b2ccd6b4d38b92ecfd39ecf638b` at `100%`. The deterministic local worker
reported review score `95`, quality gate `PASS`, and StoryCommit
`1306e900...`; the source Planning node
`planning:b50994542d814ef38f0a16255e2ef44b` was `ACCEPTED` and linked to the
committed chapter. The browser displayed the completed task, recorded zero
console errors/warnings, and all observed API requests returned HTTP `200`.
This is deterministic local acceptance evidence rather than real-provider E2E;
external-provider execution, production-scale scheduling, and the full
acceptance matrix remain open. Overall status remains `PARTIAL`.

## StoryFlow authorized provider and Agent-context increment (2026-08-20)

The current Model Runtime reports one enabled, credentialed MiMo provider and
the connection task completed on a copied SQLite database (`connected=true`,
model `mimo-v2.5-pro`). A real-provider Simulation probe on that isolated copy
ran three explicitly pinned Tier A Agents for ten durable rounds. The run
reached `COMPLETED` at round 10 with 30 GenerationRuns, 30 GenerationAttempts,
30 cost-ledger rows, stable Sandbox evidence, and an unchanged Canon hash.
The same isolated run also completed real-provider Character Chat
(`ANSWERED`), a three-Agent Survey (`COMPLETED`, three responses), and a
provider-backed Analyst query; all returned `canonicalMutation=false`.

That probe found and fixed two runtime gaps. Agent-local perception now falls
back to the typed action ontology when a legacy snapshot has no explicit action
catalog, and exposes only same-location entity references so provider models can
distinguish `targets` from `location`. Empty rounds still persist `ROUND_CLOCK`
for replay, but no longer report its null actor as `actedAgents=[""]`. Focused
regressions cover the fallback vocabulary, local-entity boundary, and clock
semantics. These are authorized-provider and isolated-runtime proofs; the full
23-step browser/provider matrix and production-scale scheduling remain open.
Overall status remains `PARTIAL`.

## StoryFlow final verification and provider-browser increment (2026-08-20)

The current tree passes the full repository suite: `1016 passed in 16:54`.
The focused StoryFlow/world/persistence/writing regression passes `121 passed`.
The protected verifier completed CW-001, MEMORY-001, REVIEW-001, STORY-001,
and WRITE-001 as `VERIFIED`; Ruff, JavaScript syntax, compileall,
protected-file, and `git diff --check` also pass.

An isolated current-code Studio server was exercised in a fresh headed browser
session. It created a run with persisted MiMo assignment, traversed
DRAFT -> READY -> RUNNING, executed a typed WAIT round, queued a durable
`simulation-round` task, and reloaded the completed task from durable storage.
The run remained Sandbox-only (`canonicalMutation=false`) and the browser
reported zero console errors/warnings with all observed requests successful.
Screenshots are retained under `output/playwright/`. The external-provider
runtime, full 23-step browser/provider matrix, and production-scale
virtualization remain partial; product status remains `PARTIAL`.

The current deterministic runtime benchmark covers the required 10/20,
25/50, and 50/100 scales: 200/1,250/5,000 ledger events completed at
127.678/54.297/28.981 events per second with stable hashes,
`runStatus=COMPLETED`, and `canonicalMutation=false`. It is explicitly a
deterministic-runtime-only throughput measurement, not a provider latency or
production-scale virtualization claim.

The follow-up fresh headed browser session against the same isolated provider
copy also verified the newly exposed provider decision controls and adoption
handoff. Selecting `Provider Agent decision` disabled the immediate execute
button and changed the durable action to `Queue provider round`; the persisted
Provider browser run displayed its completed `OBSERVE` event, provider
assignment, budget ledger, and `canonicalMutation=false`. In HISTORY, an
existing `ADOPTED` proposal loaded from `GET .../adoptions`, and clicking
`Create ChapterIntent` returned HTTP `200` and reloaded `ChapterIntent 15 ·
PLANNED`. The fresh session had zero console errors/warnings and every
observed API request returned HTTP `200`; screenshot:
`output/playwright/storyflow-provider-ui-final.png`. This closes the browser
evidence for the current adoption/ChapterIntent/provider-mode UI seam, but not
the full 23-step acceptance matrix or production-scale gate.

The Provider Agent decision form now exposes a real multi-select Agent picker.
It preselects the persisted scheduler's active slots, lets the author pin
multiple Character/Faction Agents for one durable round, and sends the selected
`agentIds` list through the existing idempotent `round-tasks` boundary. Clearing
the picker restores the backend scheduler's tier policy; the roster is capped
to a bounded 100-option working set with an explicit overflow note. A fresh
headed browser pass against the isolated provider copy verified the enabled
picker, active-slot marking, disabled synchronous execute control, and
`Queue provider round`; console errors/warnings remained zero. Artifact:
`output/playwright/storyflow-provider-multi-agent-ui.png`. This improves the
multi-Agent UI seam but does not claim a 10-agent live-provider run or close
production-scale virtualization.

## StoryFlow scheduler configuration-boundary increment (2026-08-20)

The gap audit found that Environment Setup generated nested Agent policies under
`agents.policies`, while `AgentScheduler` only read the legacy top-level policy
aliases. As a result, an author-selected Tier A/B policy could silently fall
back to Tier C at runtime. The scheduler now unwraps the generated nested map,
retains compatibility with the legacy `agentPolicies` and `agent_policies`
forms, and preserves the explicit author-pin behavior. A focused regression
proves a generated nested Tier A policy produces an active Tier A activation;
the local scheduler/configuration tests pass. This is a correctness repair,
not a claim that the external-provider or production-scale gates are closed.

## StoryFlow performance Gate rerun (2026-08-20)

The required deterministic benchmark was rerun against the current tree. The
10-agent/20-round case persisted 200 events at 315.047 events/s, the
25-agent/50-round case persisted 1,250 events at 55.415 events/s, and the
50-agent/100-round case persisted 5,000 events at 17.390 events/s. All three
runs reached `COMPLETED`, produced stable state hashes, and reported
`canonicalMutation=false`. The benchmark is the exact deterministic scope
specified by the reference Gate; provider latency and production-scale UI
virtualization remain explicitly unclaimed.

The post-repair isolated browser smoke loaded the current Studio bundle against
the disposable provider fixture and rendered the Agent roster from the real
API. Each visible card now displayed the replayed `stateHash` and
`canonicalMutation=false`; the Provider picker was present and disabled in
explicit mode. All observed backend responses were HTTP 200 and there were no
page errors. The smoke captured
`output/playwright/storyflow-roster-statehash-smoke.png`; two external resource
requests were blocked by the local network policy and are not treated as clean
console evidence.

The Provider round boundary also now fails closed when an author-pinned
`agentIds` entry is absent from the immutable snapshot. The durable task is
marked `HANDLER_ERROR` before any Provider call or Sandbox event, instead of
silently running an empty round. A focused regression covers the persisted
failure and zero Provider-call invariant.

## StoryFlow final current-tree verification (2026-08-20)

The current tree passes the full repository suite: `1018 passed in 14:50`.
The focused StoryFlow/world/persistence/writing regression passes `123 passed`.
The protected verifier reports CW-001, MEMORY-001, REVIEW-001, STORY-001,
and WRITE-001 as `VERIFIED`; Ruff, JavaScript syntax, compileall,
`check_protected_files.py`, and `git diff --check` also pass. The latest
changes specifically cover nested Environment Setup Agent policies and
unknown pinned Agent ids failing before any Provider call or Sandbox event.
All current evidence remains local/deterministic or isolated-provider evidence;
the complete external-provider browser matrix, production-scale virtualization,
and broad live-provider crash/recovery gate remain `PARTIAL`.
