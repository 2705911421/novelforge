# StoryFlow History Migration 41 Test-Change Request

Migration 41 adds the append-only `simulation_run_history` lifecycle ledger
for author-controlled archive/unarchive actions. The migration engine test's
expected maximum schema version must therefore advance from 40 to 41. This is
an additive schema contract update, not a weakened assertion: the test still
mutates a recorded checksum and requires the migration engine to fail closed.
