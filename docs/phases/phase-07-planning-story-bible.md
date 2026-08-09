# Phase 7: Planning and Story Bible

## Goals

- Replace the one-shot world wizard mutation with a durable 25-step Story Bible workspace.
- Keep author drafts, AI suggestions, confirmations, and published snapshots distinct.
- Enforce ordered confirmation: an AI suggestion never changes authoritative project truth by itself.
- Persist immutable Story Bible snapshots in SQLite and make the latest workspace recoverable after restart.
- Preserve the existing durable task/GenerationRun boundary for AI suggestions.

## Non-goals

- Interactive graph/map rendering, image generation, or full timeline editing.
- Automatically creating chapters, Story Facts, or Memory projections from an unconfirmed draft.
- Claiming a real provider path when no configured provider is available.

## Data Changes

Migration 8 adds:

- `story_bible_workspaces`: one current workspace per project/book, current step, status, draft metadata, and published snapshot reference.
- `story_bible_steps`: one row per workspace step with draft/suggestion payload, confirmation status, source, error, and timestamps.
- `story_bible_snapshots`: immutable aggregate JSON and checksum for every publishable confirmation boundary.

The existing `projects`/`books` structured fields are updated only by an explicit final publish transaction after all 25 steps are confirmed. Attachments and legacy project files remain untouched.

## Step Contract

The ordered step keys are: `intent`, `audience`, `selling_points`, `core_conflict`, `world`, `world_rules`, `power_system`, `protagonist`, `main_characters`, `relationships`, `factions`, `locations`, `history`, `timeline`, `ending`, `plot_summary`, `volumes`, `arcs`, `chapter_plan`, `foreshadowing`, `hooks`, `voice`, `techniques`, `references`, `confirmation`.

Each step accepts a JSON object/value, records `source` as `author` or `ai`, and enters `draft` before confirmation. A step can be confirmed only after every preceding step is confirmed. `confirmation` is a review gate, not an automatic write.

## API

- `GET /api/v1/books/{book_id}/story-bible`
- `PUT /api/v1/books/{book_id}/story-bible/steps/{step_key}` saves an author draft.
- `POST /api/v1/books/{book_id}/story-bible/steps/{step_key}/confirm` confirms one step and creates a durable snapshot.
- `POST /api/v1/books/{book_id}/story-bible/publish` publishes only when all steps are confirmed.
- `POST /api/v1/books/{book_id}/story-bible/suggest` queues an AI suggestion task; it never publishes.

All routes validate project scope and return explicit conflicts for out-of-order confirmation or incomplete publish.

## CLI

`novelforge bible <project_id> show|set|confirm|publish` exposes the same SQLite workflow. `novelforge bible <project_id> suggest <step>` queues AI work and prints the durable task id.

## Worker Workflow

1. Claim `story-bible-suggest` through `PersistentTaskWorker`.
2. Load only confirmed preceding steps plus the requested current-step draft.
3. Invoke the persisted `writer` route inside `task_scope`; record GenerationRun input/output references.
4. Validate the JSON response and save it as an unconfirmed `ai` suggestion.
5. Leave project truth and published snapshots unchanged on success or failure.

## Error Cases

- Unknown step, invalid payload, missing project, blank suggestion brief, and malformed AI JSON are visible errors.
- Confirming a step before its predecessor returns conflict and does not mutate the workspace.
- Publishing with any unconfirmed step returns conflict and does not mutate project truth.
- Provider configuration/auth/network failures fail the durable task and leave the draft recoverable.
- Retried suggestion tasks are idempotent per workspace/step/request fingerprint.

## Acceptance Criteria

- A fresh repository reconstructs the same workspace, step statuses, suggestions, and snapshots.
- Author edits survive page reload and process restart.
- AI suggestions are observable as task state and remain unconfirmed until an author confirms them.
- Out-of-order confirmation and incomplete publish are rejected atomically.
- Final publish updates the authoritative project only once all steps are confirmed and records a checksumed immutable snapshot.
- API, CLI, worker, unit/integration tests, and browser verification cover the workflow.

## Tests

- Migration 8 schema and restart recovery.
- Ordered draft/confirm/publish state machine and atomic failure cases.
- Worker suggestion success/failure with deterministic test provider and GenerationRun evidence.
- Studio API/UI states and CLI commands.
- Browser smoke path: create/open book, edit several steps, refresh, confirm, and observe publish gating.
