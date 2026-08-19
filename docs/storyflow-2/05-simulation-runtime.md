# Simulation Runtime

The durable round sequence is: recover state, build perceptions, obtain
decisions, validate actions, append events, update agent memory, advance the
round, and checkpoint. The engine still accepts injected decision functions for
deterministic tests and author-controlled actions. Durable `simulation-round`
tasks may also use `decisionMode=provider`: each Agent receives a bounded,
agent-local `SimulationAgentContextBundle`, the selected persisted model route
returns structured JSON, and the result is converted to a typed action before
validation. The existing task-scoped Model Runtime persists the exact
GenerationRun/GenerationAttempt and context manifest; a missing route fails
closed before any simulation event is appended. `None` is a truthful skipped
decision, not a fake event. Migration 39 adds deterministic Agent Tier A/B/C
activation evidence and a GenerationRun-backed simulation cost ledger. Provider
rounds preflight configured call/token/cost budgets and transition the sandbox
to `PAUSED_BUDGET` rather than failing when a limit is reached; an author may
increase the persisted budget and resume. Migration 40 adds an append-only
causal trace ledger. Each event can reference persisted prior events, Agent
memory, goals, relationships, interventions, world rules, and GenerationRun
provenance; the read model is rebuildable and remains strictly Sandbox-scoped.
Migration 41 adds append-only run archive/unarchive history without deleting
Sandbox evidence.
Provider authorization, provider-rate discovery, and large-run
scheduling/performance remain outside this partial slice.

Run configuration may now persist a normalized `providerAssignment` record. Its
`agentDecisionProviderId` is copied into the durable `simulation-round` task,
included in the idempotency fingerprint, and passed to the Model Router as a
provider override; the router still resolves the enabled model, credentials,
GenerationRun, and usage ledger. `memoryProviderId`, `analystProviderId`, and
`embeddingProviderId` are durable capability assignments. Memory consolidation
and sandbox embedding use their selected route; Analyst, Character Chat, and
Survey use persisted capability tasks with the selected analyst/chat route.
Missing assignments or providers fail closed, and external-provider
authorization remains a separate gate.
