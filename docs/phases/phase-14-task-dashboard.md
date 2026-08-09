# Phase 14: Task Dashboard

## Goals

- Add a task dashboard to Studio showing all running/queued tasks.
- Display task progress, checkpoints, and errors.
- Allow pausing, resuming, and cancelling tasks.

## Non-goals

- Real-time task streaming (Phase 16 covers that).

## Data Model

Uses existing `tasks` and `task_events` tables.

## Studio API

- `GET /api/v1/tasks` - List all tasks with status
- `GET /api/v1/tasks/{task_id}` - Get task details with events
- `POST /api/v1/tasks/{task_id}/pause` - Pause a task
- `POST /api/v1/tasks/{task_id}/resume` - Resume a task
- `POST /api/v1/tasks/{task_id}/cancel` - Cancel a task

## Acceptance Criteria

- Task dashboard shows all tasks with status indicators.
- Task details show checkpoints and events.
- Tasks can be paused, resumed, and cancelled.
