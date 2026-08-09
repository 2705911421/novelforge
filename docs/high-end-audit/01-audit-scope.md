# Audit Scope and Method

## Authoritative inputs

- `CLAUDE.md` and `AGENTS.md`
- User-supplied adversarial audit brief
- `spec/features/*.yaml` (five P0 Feature Contracts)
- `docs/architecture/**`, `docs/audit/**`, `docs/phases/**`
- Current source, tests, git status, and runtime command output

## Audit method

For each material claim, the review sought a chain of:

`claim -> source code -> runtime path -> persistence -> restart/readback -> test result`

File existence, route existence, a rendered page, a mock response, or a passing
happy-path test was not treated as sufficient evidence for a durable feature.
Failure, recovery, concurrency, and malformed-model-output paths were checked
where executable tests existed.

## Classification policy

The report uses the constitution's statuses (`IMPLEMENTED`, `PARTIAL`,
`BLOCKED`, `NOT_IMPLEMENTED`) and the audit brief's evidence qualifiers
(`UNVERIFIED`, `NOT AUDITED`). `VERIFIED` is reserved for the supplied contract
runner and is not promoted to a product-wide status.

## Scope covered

- Story Commit, Story State, chapter status, idempotence, projection replay
- Writing pipeline stages, review gate, revision exhaustion, fact extraction
- Continuous-writing checkpoints, child-task accounting, pause/cancel boundaries
- Task leases, recovery, SSE replay, model role routing, GenerationRuns
- Persistent BM25/RAG path, vector scaffold, memory implementation split
- Prompt registry versus actual model-request construction
- Document ingestion, backup creation/listing, migration backup behavior
- Studio route/UI smoke evidence and test-quality controls

## Scope not fully executable in this environment

- No real third-party provider credential or external model call
- No 100+ chapter endurance run
- No complete database/project restore workflow to test
- No production worker supervisor or multi-process deployment test
- No full browser coverage of every write/review/revise/pause/resume/restore flow

Those items are explicitly marked `UNVERIFIED` or `NOT AUDITED`, never `VERIFIED`.

