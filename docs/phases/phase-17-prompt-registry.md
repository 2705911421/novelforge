# Phase 17: Prompt Registry

## Goals

- Create a registry of customizable prompts for each task type.
- Allow users to customize prompts per project.
- Support prompt versioning and rollback.

## Implementation

- Store prompts in SQLite `prompts` table.
- Default prompts shipped with application.
- User can override per project.

## Acceptance Criteria

- Prompts are stored in SQLite.
- Users can customize prompts per project.
- Prompt changes are versioned.
