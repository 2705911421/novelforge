"""Canonical state policy for durable chapter-generation tasks.

The queue's ``status`` answers whether a worker may run a task.  This module
owns the second, domain-facing state machine used by the Task Center: it
answers how far a chapter has progressed toward an accepted StoryCommit.
Keeping the policy separate from SQLite persistence gives TaskRuntime a small
write seam while preserving the legacy checkpoint labels as an adapter.
"""

from __future__ import annotations

from typing import Any, Mapping


CHAPTER_WORKFLOW_TASK_TYPES = frozenset({"write", "write-next"})

CHAPTER_WORKFLOW_STATES = (
    "PLANNED",
    "CONTEXT_READY",
    "COMPUTE_PLANNED",
    "GENERATING",
    "DRAFTED",
    "REVIEWING",
    "REVISION_REQUIRED",
    "REVISING",
    "GATE_PENDING",
    "VERIFIED",
    "COMMIT_PENDING",
    "COMMITTED",
)

_STATE_ORDER = {state: index for index, state in enumerate(CHAPTER_WORKFLOW_STATES)}

# Revisions deliberately return to a newly drafted candidate before the
# reviewer runs again.  This is the only backward-looking edge in the linear
# presentation of the workflow; it still represents forward progress within a
# new review round.
CHAPTER_WORKFLOW_TRANSITIONS: dict[str, frozenset[str]] = {
    "PLANNED": frozenset({"CONTEXT_READY"}),
    "CONTEXT_READY": frozenset({"COMPUTE_PLANNED"}),
    "COMPUTE_PLANNED": frozenset({"GENERATING"}),
    "GENERATING": frozenset({"DRAFTED"}),
    "DRAFTED": frozenset({"REVIEWING"}),
    "REVIEWING": frozenset({"REVISION_REQUIRED", "GATE_PENDING"}),
    "REVISION_REQUIRED": frozenset({"REVISING"}),
    "REVISING": frozenset({"DRAFTED"}),
    "GATE_PENDING": frozenset({"VERIFIED", "REVISION_REQUIRED"}),
    "VERIFIED": frozenset({"COMMIT_PENDING"}),
    "COMMIT_PENDING": frozenset({"COMMITTED"}),
    "COMMITTED": frozenset(),
}


# Checkpoint labels remain an intentionally lossless compatibility adapter.
# The pipeline can supply an explicit state when a label has two meanings:
# ``REVISION`` is REVISION_REQUIRED when the gate schedules it and REVISING
# when the reviser has actually started.
_CHECKPOINT_STATE_BY_STAGE = {
    "queued": "PLANNED",
    "pending": "PLANNED",
    "blocked": "PLANNED",
    "PRECHECK": "PLANNED",
    "LOAD_CHAPTER_PLAN": "PLANNED",
    "BUILD_CONTEXT": "PLANNED",
    "RETRIEVE_MEMORY": "CONTEXT_READY",
    "PLAN_CHAPTER": "CONTEXT_READY",
    "EXTRACT_REQUIREMENTS": "COMPUTE_PLANNED",
    "COMPOSE_WRITING_PROMPT": "COMPUTE_PLANNED",
    "GENERATE_DRAFT": "GENERATING",
    "REVIEW": "DRAFTED",
    "QUALITY_GATE": "REVIEWING",
    "REVISION": "REVISION_REQUIRED",
    "EXTRACT_FACTS": "VERIFIED",
    "CREATE_STORY_COMMIT": "COMMIT_PENDING",
    "COMPLETE": "COMMIT_PENDING",
    "DONE": "COMMITTED",
    # Legacy chapter handlers use lower-case labels.  They are retained as
    # compatibility inputs, but the production write-next path owns the full
    # review/gate/commit sequence.
    "plan": "COMPUTE_PLANNED",
    "draft": "DRAFTED",
    "review": "REVIEWING",
    "re-review": "REVIEWING",
    "revise": "REVISING",
    "rewrite": "GENERATING",
}


def is_chapter_workflow_task(task_type: Any) -> bool:
    return isinstance(task_type, str) and task_type in CHAPTER_WORKFLOW_TASK_TYPES


def normalize_workflow_state(value: Any) -> str:
    """Normalize and validate a persisted or API-supplied workflow state."""
    state = str(value or "").strip().upper()
    if state not in _STATE_ORDER:
        raise ValueError(f"invalid chapter workflow state: {value!r}")
    return state


def _context_from_state(state: Any) -> Mapping[str, Any]:
    if not isinstance(state, Mapping):
        return {}
    context = state.get("context")
    if isinstance(context, Mapping):
        return context
    return state


def story_commit_id(value: Any) -> str | None:
    """Read an explicit StoryCommit reference from a task result/context."""
    context = _context_from_state(value)
    for key in ("story_commit_id", "storyCommitId", "commit_id", "commitId"):
        candidate = context.get(key)
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return None


def state_for_stage(stage: Any, state: Any = None, *, current: str | None = None) -> str | None:
    """Translate a legacy checkpoint label to the canonical state.

    ``DONE`` is only terminal when the checkpoint carries both an explicit
    successful result and a StoryCommit reference.  That prevents a generic
    ``DONE`` label from manufacturing a committed narrative state.
    """
    label = str(stage or "").strip()
    normalized = _CHECKPOINT_STATE_BY_STAGE.get(label)
    if normalized is None:
        normalized = _CHECKPOINT_STATE_BY_STAGE.get(label.upper())
    if normalized is None:
        return current

    context = _context_from_state(state)
    if label.upper() == "DONE":
        if context.get("completed") is True and story_commit_id(context):
            return "COMMITTED"
        return current
    if label.upper() == "COMPLETE":
        quality_gate = str(context.get("quality_gate") or "").upper()
        if quality_gate in {"MAX_REVISIONS", "FAIL", "FAILED"} and not story_commit_id(context):
            return "REVISION_REQUIRED"
    return normalized


def initial_workflow_state(
    task_type: Any,
    *,
    stage: Any = "queued",
    data: Any = None,
) -> str | None:
    """Return the durable starting state for a newly queued task."""
    if not is_chapter_workflow_task(task_type):
        return None
    payload = data if isinstance(data, Mapping) else {}
    explicit = payload.get("workflowState") or payload.get("workflow_state")
    if explicit is not None:
        return normalize_workflow_state(explicit)
    resume_stage = payload.get("resume_stage") or payload.get("resumeStage")
    selected_stage = resume_stage or stage
    # A resumed author decision starts at the state represented by the exact
    # pipeline entrypoint, even though the queue row itself is newly created.
    return state_for_stage(selected_stage, payload, current="PLANNED") or "PLANNED"


def can_advance_workflow_state(current: Any, target: Any) -> bool:
    """Return whether a checkpoint may advance the persisted state.

    Recovery can legitimately restore a task at a later checkpoint (for
    example, a process died after producing a draft).  Such restores may skip
    intermediate read-model states but may never move backward except through
    the explicit revision loop.
    """
    current_state = normalize_workflow_state(current)
    target_state = normalize_workflow_state(target)
    if current_state == target_state:
        return True
    if target_state in CHAPTER_WORKFLOW_TRANSITIONS[current_state]:
        return True
    if current_state == "COMMITTED":
        return False
    return _STATE_ORDER[target_state] > _STATE_ORDER[current_state]


def workflow_state_for_checkpoint(
    task_type: Any,
    current: Any,
    stage: Any,
    state: Any,
    explicit: Any = None,
) -> str | None:
    """Resolve a checkpoint's next canonical state for TaskRuntime."""
    if not is_chapter_workflow_task(task_type):
        if explicit is not None:
            raise ValueError("workflow state is only valid for chapter workflow tasks")
        return None
    current_state = normalize_workflow_state(current) if current else initial_workflow_state(
        task_type, stage=stage, data=state
    )
    target = normalize_workflow_state(explicit) if explicit is not None else state_for_stage(
        stage, state, current=current_state
    )
    if target is None:
        return current_state
    if current_state and not can_advance_workflow_state(current_state, target):
        raise ValueError(f"illegal chapter workflow transition: {current_state} -> {target}")
    return target

