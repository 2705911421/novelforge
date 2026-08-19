# Memory and Knowledge

Canonical Memory is read-only input. Simulation memory is mutable and scoped by
`simulation_run_id + agent_id`, with episodic, semantic, social, and rumor
types. Every memory entry records source SimulationEvent ids, importance,
confidence, validity, and round boundaries.
