# Phase 18: World Bootstrap Wizard

## Goals

- Implement a guided wizard for creating a new world/story bible.
- Generate initial story bible from user input.
- Support regeneration of specific steps.

## Implementation

- 25-step wizard matching STORY_BIBLE_STEPS.
- Each step collects user input or generates from AI.
- Results saved to story_bible_workspaces.

## Acceptance Criteria

- Wizard guides through all 25 steps.
- User input is preserved.
- AI generation works for each step.
