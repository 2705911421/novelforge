# Final Product Verdict

Status: `AUDIT PARTIAL`

## Executive answers

1. **Current stage:** Alpha. It is beyond a static demo, but P0 truth/gate
   failures rule out Beta/RC/Production Ready.
2. **Prior MIMO/Codex reports:** useful as claims and regression context, not
   independent proof. Current confidence is limited to freshly observed paths;
   the seven semantic probes lower trust in completion claims.
3. **Classification:** the claim table has 20 `PARTIAL`, 2 local
   `IMPLEMENTED`, 3 `NOT_IMPLEMENTED`, and 1 `BLOCKED` rows. Defect labels add
   at least one `FAKE` forecast path and one `SCAFFOLD` vector path. This is a
   minimum for the audited claims, not a percentage of all source code.
4. **InkOS parity:** 100 capabilities are inventoried. NovelForge has local
   analogues across most families, but no P0 family earns behavior parity while
   invalidation/replay/gate/recovery semantics fail.
5. **webnovel-writer parity:** 80 capabilities are inventoried. NovelForge
   lacks the reference's accepted-event/projection boundary and projection
   retry/replay semantics; parity is not established.
6. **NovelForge originals:** continuous writing, review/revision, Story Bible,
   world building, graph, import, memory/RAG, prompts, recovery, and cost
   controls all have partial runtime surfaces; none is production-ready.
7. **Completely missing/high-risk reference features:** immutable event-first
   projection replay, active-fact filtering, version-fenced acceptance,
   projection-only recovery, durable vector projection, and full import
   deconstruction.
8. **UI/API/model-only features:** forecast is hardcoded; image generation is a
   provider/configuration seam; import queues documents without materializing
   chapters; prompt registry stores versions without runtime provenance.
9. **Story System reliability:** not reliable enough for canonical truth.
10. **Story State canonical truth:** intended to be StoryCommit-derived, but
    not sufficient because facts and derived projections can be stale.
11. **Historical edits:** can contaminate future context through invalidated
    facts and can leave old pending commits acceptable.
12. **Review gate:** not fully effective; a major actionable issue can pass the
    tested gate.
13. **Revision:** issue text is passed to a revision prompt, but issue closure,
    re-review and version provenance are not enforced as one contract.
14. **Continuous Writing:** durable task shell, not a verified durable workflow;
    automatic joint review is absent in the active service.
15. **Worker crash/restart:** task leases/checkpoints recover visible task state;
    provider-to-commit exactly-once behavior is unproven.
16. **Lease handoff:** task claim is fenced, but side effects can be repeated
    before StoryCommit idempotency is established.
17. **Joint Review:** explicit API/task capability, not an automatic closed-loop
    repair workflow.
18. **Memory:** some facts/retrieval enter Writer, but the legacy consolidation
    path is split and stale facts enter context.
19. **RAG:** BM25 enters Writer; vector/hybrid is not fully wired.
20. **Vector RAG:** scaffold in `src/pipeline/rag.py`.
21. **Prompt Registry:** versioned storage and rollback exist; runtime control
    is incomplete.
22. **Generation trace:** model/provider fields are persisted, but selected
    prompt key/version is lost at the active call seam.
23. **World Builder:** persisted planning data influences some context, not all
    later writer/reviewer state with a complete trace.
24. **Graph/Timeline/Map:** structured tables/generators exist; mutation,
    reconciliation and writer consumption are not proven.
25. **Backup:** real SQLite snapshot/integrity implementation, but backup
    metadata can disappear after restore.
26. **Restore:** DB copy with pre-restore backup; it can falsely report success
    in WAL mode while post-snapshot data remains visible. Full projection
    rollback and reconciliation are not implemented/verified.
27. **Existing tests:** they contain false-positive gaps for edit invalidation,
    version fencing, issue derivation, prompt provenance, and interval review.
28. **100-chapter endurance:** deterministic local run completed 100 chapters
    in 105.16 seconds with 100 commits/facts and replay version 100; it created
    zero automatic joint reviews. No real-provider or restart conclusion exists.
29. **Real-provider recommendation:** `NO - NOT READY`.
30. **Before real-provider testing:** fix all P0 defects, then P1 continuous,
    provenance, memory/RAG, restore, auth, and scale controls; run controlled
    deterministic campaigns first.

## Product verdict

* **CURRENT PRODUCT STAGE:** Alpha.
* **REFERENCE PARITY:** partial surface similarity; no P0 behavior parity.
* **CORE ENGINE STATUS:** meaningful SQLite/task/pipeline implementation, but
  canonical truth and review gate are unsafe.
* **LONG-FORM RELIABILITY:** not established for 100-300 chapters.
* **PRODUCTION READINESS:** `NO - NOT READY`.
* **RECOMMENDED NEXT PHASE:** Gate 1 canonical truth/reconciliation, then Gate 2
  review/provenance, then Gate 3 continuous/recovery from
  `25-remediation-roadmap.md`.

## Top risks and improvements

**Top 10 architectural risks:** split legacy/new ownership; non-replayable
projections; mutable invalidation flags; missing version fence; derived-state
fan-out without ledger; provider side effects outside commit idempotency;
duplicate routes; WAL-unsafe DB-only restore; unscoped destructive APIs;
in-memory indexes.

**Top 10 functional gaps:** automatic joint review; actionable issue gate;
review version provenance; prompt provenance; vector RAG; existing-novel
deconstruction; graph mutation feed; full restore reconciliation; forecast
runtime; authenticated project workflows.

**Top 10 reliability risks:** stale facts; wrong-version commits; FK delete
failure; review bypass; missing joint checkpoint; split memory; provider retry
duplication; false-success WAL restore; no endurance evidence; no real-provider
quality evidence.

**Top 10 highest-value improvements:** canonical accepted event; version fence;
active-fact projection; replay-all; reconciled tombstones; derived review gate;
immutable prompt/review provenance; durable joint task; unified memory/vector
projections; deterministic scale/restart campaign.

## Verification status

The official five-group verification script reports `VERIFIED` for its current
contract scope, but the global independent audit is `AUDIT PARTIAL` because the
seven initial semantic probes and the separate WAL restore probe fail, while
required provider/scale/recovery tests are not run. This report intentionally
does not promote the product based on the contract script alone.

## Required report fields

* **IMPLEMENTED:** local BM25 retrieval, SQLite/task primitives, export paths,
  and the five verifier command scopes.
* **PARTIAL:** canonical truth, writing, review/revision, continuous writing,
  memory, prompt routing, world building, backup/restore, UI, and security.
* **BLOCKED:** real-provider/image-provider quality and production-scale
  performance evidence without authorized credentials or a controlled campaign.
* **NOT_IMPLEMENTED:** active-fact-safe replay, vector implementation,
  version-fenced acceptance, full import deconstruction, and network auth.
* **TESTS RUN:** baseline/full pytest, verifier, adversarial suite, audit probes
  (including the separate WAL restore probe), Ruff, protected-file check, and
  deterministic 100-chapter probe; exact results are in
  `27-runtime-test-evidence.md`.
* **TESTS NOT RUN:** real-provider E2E, image E2E, multi-process crash/restart,
  full restore reconciliation, reference full suites, and 300/1000 chapter
  campaigns.
* **KNOWN LIMITATIONS:** this is a source/runtime audit in a dirty worktree;
  prior product changes were preserved and no remediation was applied.
* **VERIFICATION STATUS:** official contract verifier `5/5 VERIFIED` within its
  narrow test mapping; global independent result `AUDIT PARTIAL`.
