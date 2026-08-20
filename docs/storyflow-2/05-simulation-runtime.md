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

Migration 45 adds append-only run-history `DELETE` records. Delete is a soft
history operation: default run listings hide the latest deleted record, while
run detail, replay, ledger, snapshot, and audit history remain readable. A
deleted run cannot be archived/unarchived or resumed, and running runs are
refused at the delete boundary. The Simulation Context Compiler also accepts
an approximate token budget (`maxTokens`) alongside its character cap and
records the effective budget in the context evidence manifest.

The explicit `SimulationRepository.rebuild_simulation_state()` seam now
reconstructs a detached state from only the immutable snapshot and append-only
event ledger; `replay()` remains a compatibility alias. The book-scoped
`GET .../simulation/runs/{run_id}/replay` endpoint exposes the rebuilt hash and
state with `canonicalMutation=false`. Deleting the mutable graph projection
and rebuilding it through the graph projector preserves the exact state hash;
the regression covers this deletion/rebuild path.

The first authorized external-provider runtime probe completed on an isolated
database with MiMo: 3 explicitly pinned Tier A Agents ran 10 durable rounds,
creating 30 GenerationRuns/Attempts and 30 cost-ledger rows while the Canon
hash remained unchanged. A one-round provider context check then produced valid
`PLAN`, `TALK`, and `INVESTIGATE` events for all three Agents. The probe also
exposed and fixed two local runtime issues: snapshots without an action catalog
now receive the typed action vocabulary, and an empty `ROUND_CLOCK` no longer
appears as a phantom acted Agent. This is real-provider authorization evidence,
not production-scale or full browser acceptance.

Author-pinned Provider rounds validate every requested Agent id against the
immutable snapshot before scheduling or calling a Provider. Unknown ids fail
the durable task closed with no Provider call and no Sandbox event, preserving
an auditable error rather than silently executing an empty round.

The final current-tree regression is `1016 passed in 16:54`; the focused
StoryFlow regression is `121 passed`, and every protected feature gate remains
`VERIFIED`. A clean isolated headed Studio session additionally verified the
browser-backed durable task path: provider assignment persisted in the run
configuration, DRAFT/READY/RUNNING transitions were visible, a typed WAIT
event advanced the Sandbox, and a queued `simulation-round` task reloaded as
completed while the run still reported `canonicalMutation=false`.

The later audit reran the full repository at `1023 passed in 17:14` and kept
all five protected gates `VERIFIED`. Common typed actions now emit small,
deterministic Sandbox-only state deltas for location, inventory, relationship,
alliance, and goal changes; MOVE/FLEE validation treats the destination as the
action location. Communication actions copy only explicitly reported facts or
messages into target Agent scopes and recipient episodic memory, with event-id
provenance. These deltas remain replayable from the immutable snapshot plus
append-only ledger and never write Canon.
