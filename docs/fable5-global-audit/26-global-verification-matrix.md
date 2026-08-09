# Global Verification Matrix

Status: `AUDIT PARTIAL`

| Domain | Required evidence | Fresh result | Status |
|---|---|---|---|
| Reference baselines | frozen SHA/license/source maps/inventory | 01-04 complete; 180 capabilities | IMPLEMENTED |
| Story System | edit, version fence, replay, delete/reconcile | 4 semantic probes failed | PARTIAL |
| Writing pipeline | stage, failure, persistence, recovery | phase-8 green; stale facts/provenance failures | PARTIAL |
| Review/revision | dual gate, issue targeting, re-review, exhaustion | phase-9/12 green; major issue bypass/version loss | PARTIAL |
| Continuous writing | 5-200 durable workflow, interval review, restart | 5-chapter joint-review probe failed | PARTIAL |
| Memory | commit -> summary/memory -> writer | split legacy/SQLite path | PARTIAL |
| RAG | BM25/vector/hybrid/rerank/update/delete | BM25 tests green; pipeline vector stub | PARTIAL |
| World Builder/Story Bible | persisted steps affect writer/state | local wizard tests; end-to-end unverified | PARTIAL |
| Model/Prompt | role routing + exact prompt/model trace | storage seam exists; active provenance probe failed | PARTIAL |
| Existing-novel import | DOCX/MD/TXT to chapters/entities/state/memory | upload queues ingestion only | PARTIAL |
| Backup/Restore | validate, WAL-safe restore, rollback, reconcile | DB snapshot tests pass, but normal restore loses catalog entries and WAL restore falsely reports success | PARTIAL |
| Data integrity | FK/replay/idempotency/concurrency | delete/version/gate probes failed | PARTIAL |
| UI functional | controls invoke durable runtime | broad pages/routes; forecast hardcoded/duplicate route | PARTIAL |
| Security | authn/authz/project scope/secret controls | localhost CORS and input checks; no auth | PARTIAL |
| Scale | 100/300/1000 endurance and metrics | deterministic 100 chapters completed; 300/1000 and real provider blocked | PARTIAL |
| Tests | regression, adversarial, acceptance, lint | baseline 708 green; earlier full rerun 708 passed + 7 semantic failures, then separate WAL probe failed; 18 adversarial; no acceptance collection; 34 ruff errors | PARTIAL |

## Official contract script versus independent audit

`python scripts/generate_progress.py --verify` reports `5 / 5` P0 contract
features as `VERIFIED`. That result is valid only for the five configured
acceptance groups and their current assertions. It does not cover the seven
independent semantic probes, so the global product status remains `AUDIT
PARTIAL`.

## Not tested or blocked

Real-provider generation, image generation, multi-process crash/restart,
full-workspace restore, 300+ chapter endurance, network security, and
reference full-suite parity were not run. They are explicitly `BLOCKED` or
`UNVERIFIED`, never inferred as pass.

## Current-state update (2026-08-09)

A fresh full-suite rerun produced `1 failed, 715 passed`. The failure is in the
authoritative project-save path, not only in the earlier independent audit
probes. The product therefore remains `AUDIT PARTIAL` even though the configured
contract verifier is still `5/5 VERIFIED`.
