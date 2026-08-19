# Narrative Analyst

Analysis must be grounded in persisted state and event evidence. The current
`BranchComparisonService` reports common prefix, state hashes, changed values,
and divergent event ids. Natural-language synthesis must cite the persisted
event, state, memory, and causal evidence rather than inventing a Canon claim.

The implemented analyst slice now exposes a closed `SimulationAnalystTools`
registry for snapshot/state/event, Character/Faction, memory,
relationship/goal/conflict, foreshadow/plot-thread, world-rule, branch,
Canon-freshness, and Planning inspection. `NarrativeAnalyst.ask` invokes only a
named tool, returns a grounded evidence chain, and the Studio
`simulation/runs/{run_id}/analysis/query` endpoint persists an immutable
`analyst-query` report. `SimulationCausalityService` records an append-only
`simulation_causal_traces` ledger for prior events, Agent memories, open goals,
relationships, interventions, world rules, and GenerationRun provenance. The
causal API and `query_causal_trace` tool expose only that Sandbox evidence with
`canonicalMutation=false`; missing evidence is reported as missing rather than
invented. Provider-backed Analyst/Character Chat/Survey requests run through
durable capability tasks when an enabled route is available; richer prose
synthesis, causal graph inference, and real external-provider acceptance remain
`PARTIAL`.
