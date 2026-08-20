# StoryFlow 2.0 Goal Tree

This is the executable status map for the clean-room StoryFlow 2.0 goal. A
status is `IMPLEMENTED` only when the corresponding local seam and regression
evidence exist; external-provider and production-scale claims stay explicit.

| Goal | Status | Evidence |
| --- | --- | --- |
| P0-01 reference audit | IMPLEMENTED | `01-mirofish-capability-mapping.md` |
| P0-02 capability map | IMPLEMENTED | capability mapping table |
| P0-03 architecture design | IMPLEMENTED | `00-vision.md`, `02-domain-model.md` |
| P0-04 simulation schema | IMPLEMENTED | migrations 24-45, persistence suite |
| P0-05 world snapshot | IMPLEMENTED | snapshot builder/repository tests |
| P0-06 character agent | IMPLEMENTED | profile/perception/decision tests |
| P0-07 faction agent | IMPLEMENTED | faction roster/perception tests |
| P0-08 knowledge scope | IMPLEMENTED | secret and private-event isolation tests |
| P0-09 simulation runtime | IMPLEMENTED | round engine/task handler tests |
| P0-10 action system | IMPLEMENTED | 29 typed actions and rejection tests |
| P0-11 perception | IMPLEMENTED | location/visibility/communication tests |
| P0-12 decision engine | IMPLEMENTED | typed decision and provider provenance tests |
| P0-13 simulation memory | IMPLEMENTED | episodic/semantic/social/rumor tests |
| P0-14 event ledger | IMPLEMENTED | append-only/replay/rebuild/detail tests; book-scoped replay API |
| P0-15 intervention | IMPLEMENTED | typed sandbox intervention tests |
| P0-16 branching | IMPLEMENTED | lineage/branch isolation tests |
| P0-17 recovery | IMPLEMENTED | crash-at-stage retry tests; live provider recovery remains partial |
| P0-18 Canon isolation | IMPLEMENTED | Canon boundary digest tests |
| P1-01 dynamic graph | IMPLEMENTED | durable projection, typed edges, live refresh |
| P1-02 realtime timeline | IMPLEMENTED | SSE replay plus lazy event inspector |
| P1-03 environment setup | IMPLEMENTED | structured editor and snapshot generator |
| P1-04 analyst | IMPLEMENTED | tool registry, grounded query, causal evidence |
| P1-05 branch compare | IMPLEMENTED | event/state and narrative dimension comparison |
| P1-06 character chat | IMPLEMENTED | agent-local persistence and fail-closed provider path |
| P1-07 survey | IMPLEMENTED | scoped response persistence plus explicit scenario-to-Sandbox fork |
| P1-08 report | IMPLEMENTED | durable report sections and event evidence links |
| P1-09 history | IMPLEMENTED | archive, soft-delete, duplicate, branch, compare |
| P1-10 adoption | IMPLEMENTED | explicit proposal/edit/reject/adopt flow |
| P1-11 ChapterIntent integration | IMPLEMENTED | durable intent and write-next handoff |
| P2-01 navigation refactor | IMPLEMENTED | WORLD/AGENTS/SIMULATE/ANALYZE/INTERACT/HISTORY |
| P2-02 workflow UI | IMPLEMENTED | backend-backed workflow panels |
| P2-03 live simulation UI | IMPLEMENTED | EventSource/timeline/status/intervention-history controls |
| P2-04 agent tier | IMPLEMENTED | A/B/C policy and roster evidence |
| P2-05 scheduler | IMPLEMENTED | persisted activation and `whyActivated` |
| P2-06 provider routing | IMPLEMENTED | run-scoped capability assignment and fail-closed routing |
| P2-07 cost controls | IMPLEMENTED | durable ledger and `PAUSED_BUDGET` |
| P2-08 context compiler | IMPLEMENTED | bounded agent-local bundle with char/token budget |
| P2-09 causal trace | IMPLEMENTED | persisted causal references and event inspector |
| P2-10 performance | IMPLEMENTED | required deterministic 10/20, 25/50, and 50/100 benchmark passes; production-scale virtualization remains outside this Gate |
| Real external Provider E2E | PARTIAL | MiMo connection plus isolated 3-agent/10-round, Chat, Survey, and Analyst runs; full browser/provider matrix remains open |

The overall product verdict remains `PARTIAL` until the external-provider gate,
production-scale performance gate, and complete browser acceptance matrix have
authoritative evidence.

Latest audit evidence (2026-08-20): the full repository is `1024 passed`, all
five protected feature gates are `VERIFIED`, and the required deterministic
10/20, 25/50, and 50/100 runtime benchmark passes with stable hashes and
`canonicalMutation=false`. A fresh local browser session verified the current
durable run/roster/HISTORY surface and STOP control with zero fresh console
errors/warnings. These results strengthen the local seams but do not close the
external-provider full matrix or production-scale virtualization gates.

Current disposable browser evidence additionally completes the local 23-step
flow through a deterministic accepted StoryCommit: pre-write Canon
`4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`, post-write
Canon `2927a3c35e9d57aab719af3c0d09f6c6f8f468edd1a07ad512d0ae33fe079d7e`, and
the source run is truthfully `STALE` after the write. A structured Faction
territory perception regression and mixed Survey browser pass also completed;
the overall verdict remains `PARTIAL` because the provider-backed browser and
production-scale gates remain open. The current deterministic benchmark rerun
measured `287.713/100.279/33.654` events per second at the required scales;
this is a core-ledger baseline only.
