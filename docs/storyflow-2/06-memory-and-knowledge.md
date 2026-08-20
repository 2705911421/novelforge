# Memory and Knowledge

Canonical Memory is read-only input. Simulation memory is mutable and scoped by
`simulation_run_id + agent_id`, with episodic, semantic, social, and rumor
types. Every memory entry records source SimulationEvent ids, importance,
confidence, validity, and round boundaries.

Knowledge scopes are explicit at both the Snapshot and runtime seams.
`PerceptionBuilder`, `ActionValidator`, and Agent profile construction pass the
selected Agent identity into `KnowledgeScope`; an explicit `UNKNOWN` record
cannot be re-exposed through a Faction's legacy `known_information` fallback.
INFORM, DECEIVE, DISCLOSE_SECRET, and SEND_MESSAGE validate disclosure against
the actor's visible scope and propagate only named facts or a deterministic
local `message:` record to the requested targets. Recipient episodic memory is
persisted with `received`, `sender_id`, and the source event id. The targeted
isolation/communication regressions are part of the current `1023 passed`
repository run; Canon and canonical memory remain read-only.
