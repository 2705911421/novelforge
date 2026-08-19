# Testing Strategy

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
labels its core-ledger mode as not provider E2E.

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
