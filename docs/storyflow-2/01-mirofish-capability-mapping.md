# MiroFish Capability Mapping

Reference audit: 2026-08-19. Source reviewed: public MiroFish README workflow
and its stated Graph Building, Environment Setup, Simulation, Report Generation,
and Deep Interaction capabilities. This is a clean-room product mapping; no
reference source, prompt, CSS, component, or algorithm is copied.

| MiroFish capability | NovelForge equivalent | Existing infrastructure | Missing domain/runtime/UI | NovelForge-native design | Priority | Acceptance evidence |
|---|---|---|---|---|---|---|
| Seed/world reconstruction | Canon -> WorldSnapshotBuilder | StoryCommit, StoryState, entities | Full canonical entity coverage/UI | Immutable event/hash-bound snapshot | P0 | Canon hash unchanged after snapshot |
| Ontology/entity graph | StoryGraph read projection | StoryGraphProjector | Simulation graph projection | Read-model adapter only | P1 | Derived graph never changes Canon |
| Agent personas | Character/Faction agent profiles | Character/Faction tables | Profile builder and cognition | Knowledge-scoped agents | P0 | Unknown facts excluded from context |
| Environment setup | Simulation configuration | Snapshot, world rules | Config persistence/UI | Sandbox-only configuration | P1 | Config creates no Canon rows |
| Multi-agent simulation | SimulationRoundEngine | Event ledger, validator | Task worker/provider routing | Injected decisions, typed actions | P0 | Durable round/recovery test |
| Action timeline | SimulationEvent ledger | SQLite append-only events | Timeline UI | Event evidence with visibility | P0 | Replay hash matches checkpoint |
| Dynamic memory | AgentMemory | run/agent memory store | consolidation/retrieval | No Canonical Memory writes | P0 | Agent A cannot read B memory |
| Dynamic graph updates | Simulation projection | StoryGraph read model | projection/UI | Separate sandbox graph | P1 | Projection rebuild is read-only |
| Report/Report Agent | Analyst report | branch comparison evidence | Analyst, persistence, UI | Evidence citations required | P1 | Every claim references events/state |
| Agent interaction | Character chat | KnowledgeScope/Perception | chat task/runtime/UI | Character-local context only | P1 | Secret isolation test |
| Survey/inquiry | Multi-agent survey | Agent profiles | orchestration/results/UI | Aggregated sandbox responses | P1 | No global Canon prompt |
| History database | Simulation run history | snapshots/runs/events/checkpoints | history API/UI | Immutable run provenance | P1 | Run reload/recovery test |
| Variable injection | Intervention | intervention event table | UI | Author intervention is event-ledgered | P0 | Parent/sibling isolation test |
| Simulation branching | SimulationBranch | prefix replay + child ledger | branch memory/checkpoint UI | Fork at immutable event sequence | P0 | A/B isolation test |
| Future comparison | BranchComparisonService | persisted state/event comparison | nested/causal compare UI | Deterministic evidence first | P1 | Divergent event ids and state hashes |
