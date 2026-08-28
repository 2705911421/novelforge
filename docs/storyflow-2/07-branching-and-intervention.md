# Branching and Intervention

Branches fork at an immutable parent event sequence. Parent and sibling rows
are never updated by child execution. Migration 42 records the fork's parent
round and the exact replayed Sandbox state hash, so a branch can be checked
against its immutable prefix after restart. Author interventions are explicit
SimulationEvents with a typed kind, author, rationale, round, and state delta;
they are sandbox evidence, not Canon edits. Location-bearing action events are
only visible to agents at that location or direct participants, while direct
messages retain recipient visibility.

Migration 44 also records the run-level lineage (`base_canon_event_id`,
`branch_parent_id`, and `branch_point_event_id`) so History and API consumers
do not need to reconstruct Canon and branch provenance from names or mutable
configuration. The lineage is descriptive Sandbox metadata and cannot mutate
Canon.
