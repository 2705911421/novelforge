# UI Architecture

The Studio API is book-scoped and uses the existing FastAPI/SQLite runtime.
Current endpoints create/read snapshots and runs and compare runs. The intended
workspaces are World, Agents, Simulate, Analyze, Interact, and History; UI
controls must expose durable status and recovery rather than fake progress.

The Simulation workspace also exposes the real scheduler activation list
(`tier`, active/passive state, and `whyActivated`) and a token/cost panel backed
by `simulation_cost_ledger`. A budget pause is rendered as `PAUSED_BUDGET` with
an author-only increase/resume boundary; no visual progress is synthesized by
the client.

The current Simulation Studio is workflow-first rather than a single long
Canvas: WORLD, AGENTS, SIMULATE, ANALYZE, INTERACT, and HISTORY tabs select
durable evidence panels without changing the run or Canon. WORLD shows snapshot
and environment provenance; AGENTS shows replayed Character/Faction roster,
scheduler, and bounded Agent inspection; SIMULATE owns interventions and
rounds; ANALYZE owns causal evidence, reports, graph, and comparison; INTERACT
owns Character Chat and Survey; HISTORY owns branches, timeline, reports, and
adoption. The selected workspace is optional presentation state and is
restored after browser refresh; all domain state remains sourced from the
book-scoped API.

Provider choices are run-scoped configuration, not browser-only settings. A
queued round task carries the normalized `providerAssignment` and hashes it in
the idempotency fingerprint. Agent Decision, Memory, Embedding, Analyst, Chat,
and Survey use the selected route through the existing Model Router or its
durable capability task; unavailable external providers fail closed.

The timeline also subscribes to the book-scoped `events/stream` endpoint. It
replays from the highest rendered sequence, accepts only events for the active
run, and updates the visible ledger from persisted event payloads. The stream
is closed on run/page changes; no client-side timer represents simulation
progress.
