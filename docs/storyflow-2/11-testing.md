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
