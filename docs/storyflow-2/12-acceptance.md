# Acceptance Gates

The P0 gate is not met until snapshot immutability, typed action validation,
knowledge isolation, durable rounds, memory, checkpoint recovery, branching,
intervention, and Canon hash invariance are all exercised together. P1 adds
analyst evidence, interaction, survey, reports, adoption, and browser workflow.
Current repository evidence supports a partial P0/P1/P2 vertical slice, not
full product completion. Causal trace evidence is now persisted and queryable;
the Studio now has real WORLD/AGENTS/SIMULATE/ANALYZE/INTERACT/HISTORY
workspace navigation and refresh recovery, while the full browser workflow and
production/provider gates remain `PARTIAL`. Repeat-run cohorts expose exact
replay-state Outcome Clusters without probability claims, and History
archive/unarchive is an append-only Sandbox lifecycle operation.
Survey detail can be explicitly handed into a new READY Sandbox branch, and
the SIMULATE/HISTORY surface exposes persisted intervention history; both
handoffs remain read-only with respect to Canon. These are local API/UI seams,
not substitutes for the external-provider and complete browser gates.

## Current local browser result (2026-08-20)

The disposable StoryFlow fixture completed the local 23-step browser flow,
including mixed Character/Faction Survey, explicit Adoption, Planning Overlay,
ChapterIntent, and a durable `write-next` handoff. Provider-unavailable Chat and
Survey results were persisted as fail-closed evidence. The deterministic
acceptance worker then completed the adopted ChapterIntent through review and
fact extraction; the browser reloaded `write-next completed · 100%`, accepted
StoryCommit evidence, and marked the immutable Simulation snapshot `STALE`
only after Canon changed. Pre-write/queued Canon was
`4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`; post-write
Canon was
`2927a3c35e9d57aab719af3c0d09f6c6f8f468edd1a07ad512d0ae33fe079d7e`.
The fresh browser session had no console errors/warnings and no duplicate
event IDs after refresh.

This closes deterministic local browser evidence only. Real external-provider
browser execution, live provider quality/authorization, and production-scale
performance remain `PARTIAL` and must not be inferred from this fixture.
