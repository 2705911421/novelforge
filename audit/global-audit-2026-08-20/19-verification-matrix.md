# Verification Matrix

## Protected P0 contracts

官方 scripts/generate_progress.py --verify 本轮结果：P0 VERIFIED 5/5。此处 VERIFIED 严格限于该脚本直接报告的合约。

| Contract | Implementation verdict | Verification evidence | Scope |
|---|---|---|---|
| STORY-001 Story State | IMPLEMENTED | official verify + phase1 persistence | protected contract |
| WRITE-001 Writing Pipeline | IMPLEMENTED | official verify + phase8 | protected contract |
| REVIEW-001 Review Gate | IMPLEMENTED | official verify + phase9/12 | protected contract |
| CW-001 Continuous Writing | IMPLEMENTED | official verify + L27/L28/L29 | protected contract |
| MEMORY-001 Memory/RAG | IMPLEMENTED | official verify + phase6/rag | protected contract |

官方证据：VERIFIED 5/5。它不覆盖新 StoryFlow 的全部 matrix rows。

## Capability matrix

| Capability | Implementation verdict | Verification status | Evidence |
|---|---|---|---|
| Story Bible draft/confirm/publish | IMPLEMENTED | focused/full tests | SQLite repository + publish guard |
| Canon StoryCommit | PARTIAL | full tests + no-review repro | normal pipeline gated; domain review optional |
| Canon state/event/memory rebuild | PARTIAL | backup-copy rebuild | current DB event/memory empty |
| Story Graph projection | IMPLEMENTED | browser/API smoke | depth 1/2/search real fixture |
| World Snapshot | IMPLEMENTED | 107 focused tests | durable snapshot/repository |
| Simulation runtime | PARTIAL | 23 adversarial + DB | deleted run resume |
| Simulation provider routing | PARTIAL | negative repro | empty assignment/global route |
| Agent-local perception | PARTIAL | negative repro | missing key leaks map |
| Simulation analysis/history | IMPLEMENTED | focused tests/API sample | reports/events/history |
| Interaction/adoption | IMPLEMENTED | focused tests/DB | adoption row + route |
| Planning overlay/reconcile | IMPLEMENTED | writing integration | pending overlay explicit |
| Adoption→write-next | PARTIAL | 3 integration tests | success/persistence only |
| Continuous Writing | IMPLEMENTED | official VERIFIED | new handoff gate still partial |
| TaskRuntime/worker | IMPLEMENTED | full/phase tests | primary path durable |
| Legacy TaskManager removal | NOT_IMPLEMENTED | source audit | parallel P2 architecture |
| Durable RAG | IMPLEMENTED | phase6/rag + source | BM25/hybrid/degraded/stale |
| Context Compiler | IMPLEMENTED | full/focused tests | budget/provenance/hard constraints |
| Context isolation | PARTIAL | negative repro | NF-P1-003 |
| AI generation audit | IMPLEMENTED | source/schema/full tests | run/attempt/prompt/latency/error |
| Real provider E2E | BLOCKED | check-only | explicit opt-in not used |
| SQLite migration/backup | IMPLEMENTED | phase1/full + backup copy | checksums/WAL/rollback |
| Studio basic surface | IMPLEMENTED | isolated browser | page/graph/search, zero console errors |
| Studio full 23-step | BLOCKED | not rerun | historical claim only |
| Full API operation matrix | NOT_IMPLEMENTED | OpenAPI count only | 263 paths, sampled routes |
| Production auth/scale | PARTIAL | source/docs | API key opt-in, no load evidence |

## Final matrix interpretation

- 已有强证据的部分：five protected contracts、migration/backup mechanism、local graph/browser smoke、primary task runtime、Story Bible repository、durable RAG code path。
- 需要整改再验的部分：simulation safety, agent isolation, Canon review invariant, data projection freshness, recent handoff recovery, real provider, full browser/API。
- 产品总体：PARTIAL。
