# World Snapshot

`WorldSnapshotBuilder` reads accepted Canon events/hash, StoryState, and
recorded entities through one read-only SQLite connection. `WorldSnapshotRepository`
persists the detached payload. Snapshot comparison returns `CURRENT`, `STALE`,
or `DIVERGED`; later Canon changes never mutate an existing run.

`WorldSnapshotRepository.create` reuses an existing row when a rebuilt
candidate has the same Canon boundary and identical world payload. The
candidate id includes creation time, so this idempotent boundary lookup is
required for repeated run creation; the API returns the persisted snapshot id,
not an uncommitted candidate. A different payload is still subject to the
database boundary constraint and is never silently merged.
