# InkOS Parity Matrix

Status: `AUDIT PARTIAL`

Baseline: InkOS `a6e05d4d4567df0efd5825e9b0037146a16e4f3e`; see
`01-reference-baseline.md`. The 100 `INK-*` rows in `04-reference-feature-inventory.md`
are the row-level inventory. This matrix groups only rows with the same
observed NovelForge boundary; a group is not a claim that a source file alone
proves behavior.

| Inventory rows | InkOS capability family | NovelForge status | Reason |
|---|---|---|---|
| INK-001..013 | project/book/chapter lifecycle and editing | PARTIAL | SQLite models and chapter versions exist; import/delete/reconciliation and file/workspace parity are incomplete. |
| INK-014..016 | book lock, lease metadata, heartbeat | IMPLEMENTED | Task/runtime and write prechecks provide local concurrency controls; distributed/multi-process proof is absent. |
| INK-017..020 | state snapshot restore, rollback, degraded/repair gate | PARTIAL | Chapter-version restore and backup APIs exist; replay does not rebuild every projection and no crash matrix was run. |
| INK-021..031 | plan, compose, write, parser, review, revision, truth settlement | PARTIAL | Active pipeline has stages and tests, but stale facts and issue-gate probes fail. |
| INK-032..040 | length/safety/style/detectors/forecast/script modes | PARTIAL | Some models/helpers exist; forecast is hardcoded and provider-backed detectors were not exercised. |
| INK-041..049 | runtime state, reducer, validator, projections, hooks | PARTIAL | Structured tables exist, but authoritative state is split between legacy `StoryProject`/`MemorySystem` and SQLite story repository. |
| INK-050..056 | memory DB, lexical retrieval, materials | PARTIAL | BM25/reference ingestion works locally; writer and consolidation do not share one memory boundary. |
| INK-057..064 | governed context and narrative controls | PARTIAL | Context assembly is active, but there is no complete per-run trace of all selected facts and stale-fact filtering is missing. |
| INK-065..080 | sessions, tool bridge, SSE, task store, daemon | PARTIAL | HTTP/SSE/task paths exist; restart/durable scheduler and process-independent session guarantees are not fully evidenced. |
| INK-081..086 | prompts and prompt packs | PARTIAL | Registry/version routes exist; runtime provenance is lost in `GenerationRun`. |
| INK-087..091 | skills, import, translation | PARTIAL | Skill/import seams exist; existing-novel import is document ingestion, not state reconstruction. |
| INK-092..096 | graph/flow/forecast/interactive modes | PARTIAL | Visualization and route surfaces exist; structured graph consumption and forecast runtime are not proven. |
| INK-097..100 | diagnostics, analytics, backup/recovery/export | PARTIAL | API and exporters exist; restore reconciliation and long-run evidence are missing. |

## Behavior-parity verdict

NovelForge has recognizable analogues for most InkOS surfaces, but it does not
yet match InkOS's repair-aware truth pipeline. In particular, InkOS's runner
blocks writes after a degraded truth state and has an explicit review-cycle
snapshot boundary; NovelForge accepts a stale-version commit and injects an
invalidated fact in the independent probes. Therefore `BEHAVIOR_PARITY` is not
awarded for the P0 families (INK-017..031, INK-041..060).

## Unverified boundaries

No real provider, image provider, 100-300 chapter endurance, or cross-process
restore test was run. These rows remain `BLOCKED` or `PARTIAL`, not `VERIFIED`.
