# World Snapshot

`WorldSnapshotBuilder` reads accepted Canon events/hash, StoryState, and
recorded entities through one read-only SQLite connection. `WorldSnapshotRepository`
persists the detached payload. Snapshot comparison returns `CURRENT`, `STALE`,
or `DIVERGED`; later Canon changes never mutate an existing run.
