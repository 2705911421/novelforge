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

Timeline inspection is lazy and durable: clicking a persisted event requests
`GET /api/v1/books/{book_id}/simulation/runs/{run_id}/events/{event_id}` and
renders Actor, Memory, pre-event Context, Why, State Delta, and Related Graph
Changes. The client drops stale clicks from the previous run while an
asynchronous run switch is loading, so an old event id cannot be queried
against a new run. Environment Setup is structured in the author form and
saved through the run-scoped configuration endpoint; only safe pre-round
statuses are editable.

Environment Setup also offers a `Generate from immutable snapshot` action for
safe pre-round runs. It persists the deterministic configuration returned by
the server and survives reload without touching Canon. Analyze renders report
event ids as buttons; after a report is created through a local partial refresh
the buttons are rebound before use, and they open the same timeline inspector.
The graph metrics expose `SIMULATION`, run id, round, and sequence so a
read-only projection cannot be mistaken for Canon or fake client progress.
History exposes archive and soft-delete controls; delete asks for an author
reason, removes the run from the default list, and leaves its evidence
queryable by direct id.
The run detail also has a book-scoped replay endpoint backed by
`rebuild_simulation_state()`, so a missing mutable read model can be verified
against the immutable snapshot/event ledger without touching Canon.

The round form now makes the provider boundary explicit: `Provider Agent
decision` disables the synchronous explicit-action button and routes through a
durable `simulation-round` task, while the author-selected decision role and
provider assignment remain persisted run configuration. HISTORY lists persisted
adoption proposals; an adopted proposal exposes its Planning node and a real
ChapterIntent action. A fresh isolated headed-browser pass verified both
controls and the `ADOPTED -> ChapterIntent PLANNED` reload path with no console
errors and only HTTP 200 API responses.

Provider rounds also expose a bounded multi-select Agent picker. The current
scheduler-active slots are marked and selected by default; author-pinned
multiple ids are carried into the durable task fingerprint, while clearing the
selection delegates activation back to the persisted tier policy. The browser
working set is capped at 100 options with explicit overflow evidence so a large
snapshot does not silently render an unbounded provider form.

The top-level run header now exposes a real STOP action for every cancellable
Sandbox state (`DRAFT`, `PREPARING`, `READY`, `RUNNING`, `PAUSED`,
`PAUSED_BUDGET`, and `FAILED`); it posts the durable `CANCELLED` transition and
retains evidence. Active-run hydration concurrently requests the independent
scheduler, adoption, graph, report, outcome, chat, and survey read models,
then fetches the budget using the persisted active-agent estimate, reducing
serial UI waits without changing domain state. A fresh current-code browser
session showed the STOP control, durable run/roster/HISTORY data, and zero
fresh console errors/warnings.
