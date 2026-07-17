"""Framework-agnostic workflow domain logic (Sprint P7): the WorkflowRun
and WorkflowStepRun state machines. Mirrors app/runtime/models.py's role
for this package - plain data/logic with no FastAPI or SQLAlchemy
imports (the persisted ORM models live in app/models/workflow_run.py
and app/models/workflow_step_run.py, alongside every other model in
this codebase).

WorkflowRun transition table (identical shape to AgentRunStatus's in
app/runtime/models.py):
    QUEUED    -> RUNNING, CANCELLED
    RUNNING   -> COMPLETED, FAILED, CANCELLED
    COMPLETED -> (terminal)
    FAILED    -> (terminal)
    CANCELLED -> (terminal) - but see app/workflows/service.py's
                 cancel_run: re-cancelling an already-CANCELLED run is
                 handled as an idempotent no-op *before* this table is
                 even consulted, not by adding a CANCELLED -> CANCELLED
                 self-loop here (a self-loop would be wrong for every
                 other caller of validate_workflow_run_transition).

WorkflowStepRun transition table:
    PENDING   -> RUNNING, SKIPPED, CANCELLED
    RUNNING   -> COMPLETED, FAILED, CANCELLED
    COMPLETED -> (terminal)
    FAILED    -> (terminal)
    CANCELLED -> (terminal)
    SKIPPED   -> (terminal)
"""
from __future__ import annotations

from app.models.enums import WorkflowRunStatus, WorkflowStepRunStatus
from app.workflows.exceptions import (
    InvalidStepRunTransitionError,
    InvalidWorkflowRunTransitionError,
)

WORKFLOW_RUN_TRANSITIONS: dict[WorkflowRunStatus, frozenset[WorkflowRunStatus]] = {
    WorkflowRunStatus.QUEUED: frozenset({WorkflowRunStatus.RUNNING, WorkflowRunStatus.CANCELLED}),
    WorkflowRunStatus.RUNNING: frozenset(
        {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED, WorkflowRunStatus.CANCELLED}
    ),
    WorkflowRunStatus.COMPLETED: frozenset(),
    WorkflowRunStatus.FAILED: frozenset(),
    WorkflowRunStatus.CANCELLED: frozenset(),
}

STEP_RUN_TRANSITIONS: dict[WorkflowStepRunStatus, frozenset[WorkflowStepRunStatus]] = {
    WorkflowStepRunStatus.PENDING: frozenset(
        {
            WorkflowStepRunStatus.RUNNING,
            WorkflowStepRunStatus.SKIPPED,
            WorkflowStepRunStatus.CANCELLED,
        }
    ),
    WorkflowStepRunStatus.RUNNING: frozenset(
        {
            WorkflowStepRunStatus.COMPLETED,
            WorkflowStepRunStatus.FAILED,
            WorkflowStepRunStatus.CANCELLED,
        }
    ),
    WorkflowStepRunStatus.COMPLETED: frozenset(),
    WorkflowStepRunStatus.FAILED: frozenset(),
    WorkflowStepRunStatus.CANCELLED: frozenset(),
    WorkflowStepRunStatus.SKIPPED: frozenset(),
}


def validate_workflow_run_transition(
    current: WorkflowRunStatus, target: WorkflowRunStatus
) -> None:
    if target not in WORKFLOW_RUN_TRANSITIONS[current]:
        raise InvalidWorkflowRunTransitionError(current, target)


def validate_step_run_transition(
    current: WorkflowStepRunStatus, target: WorkflowStepRunStatus
) -> None:
    if target not in STEP_RUN_TRANSITIONS[current]:
        raise InvalidStepRunTransitionError(current, target)
