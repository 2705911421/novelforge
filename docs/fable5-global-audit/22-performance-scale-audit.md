# Performance and Scale Audit

Status: `PARTIAL`

## Evidence available

The SQLite schema has indexes for chapter number, facts, reviews, tasks,
timeline, and joint reviews. Task queries are bounded in several API endpoints,
and BM25 retrieval accepts `top_k`. These are static scalability affordances,
not measured capacity.

## Deterministic evidence

A fresh 100-chapter run through `ContinuousWritingService` completed in
105.16 seconds with 100 chapters, 100 accepted commits, 100 facts, and
`state_version=100`. It produced zero automatic joint reviews. This is a local
control-flow measurement only, with a deterministic test model and a temporary
SQLite workspace.

## Not run

No 300/1000 chapter synthetic workflow, multi-process worker contention,
lease-expiry storm, vector-index growth, backup-size growth, or real-provider
latency/cost campaign was run. The existing suite mostly creates small fixtures
and does not record throughput, p95 latency, memory, SQLite lock time, or
projection lag.

The requested real-provider endurance is `BLOCKED_REAL_PROVIDER`; deterministic
model control-flow tests cannot establish token cost, output quality, rate-limit
behavior, or model drift.

## Risk projection (not a benchmark)

* `StoryRepository._mark_story_state_stale_for_chapter()` scans all accepted
  commits at and after an edit; replay then scans all accepted commits. This is
  workable for small books but has no measured 300/1000-chapter bound.
* BM25 index construction is in-memory and rebuilt from SQLite rows; no durable
  incremental index or memory ceiling is proven.
* Auto-backup after acceptance copies the whole database (with a five-minute
  suppression window), so large databases need measured write amplification.

## Release requirement

Run staged deterministic 10 -> 50 -> 100 -> 300 chapter campaigns with injected
provider delays/failures, then a separately authorized real-provider campaign.
Record throughput, p95/p99 stage latency, peak RSS, DB size, index size, backup
time, retry count, projection lag, and hash-equivalence after restart/restore.
