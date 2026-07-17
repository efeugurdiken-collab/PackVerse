"""Tests for the WorkflowRun/WorkflowStepRun state machines (Sprint P7):
app/workflows/models.py's validate_workflow_run_transition and
validate_step_run_transition."""
from __future__ import annotations

import pytest

from app.models.enums import WorkflowRunStatus, WorkflowStepRunStatus
from app.workflows.exceptions import (
    InvalidStepRunTransitionError,
    InvalidWorkflowRunTransitionError,
)
from app.workflows.models import validate_step_run_transition, validate_workflow_run_transition

_WORKFLOW_RUN_TERMINAL = (
    WorkflowRunStatus.COMPLETED,
    WorkflowRunStatus.FAILED,
    WorkflowRunStatus.CANCELLED,
)

_STEP_RUN_TERMINAL = (
    WorkflowStepRunStatus.COMPLETED,
    WorkflowStepRunStatus.FAILED,
    WorkflowStepRunStatus.CANCELLED,
    WorkflowStepRunStatus.SKIPPED,
)


# --- WorkflowRun transitions -----------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (WorkflowRunStatus.QUEUED, WorkflowRunStatus.RUNNING),
        (WorkflowRunStatus.QUEUED, WorkflowRunStatus.CANCELLED),
        (WorkflowRunStatus.RUNNING, WorkflowRunStatus.COMPLETED),
        (WorkflowRunStatus.RUNNING, WorkflowRunStatus.FAILED),
        (WorkflowRunStatus.RUNNING, WorkflowRunStatus.CANCELLED),
    ],
)
def test_valid_workflow_run_transitions_do_not_raise(
    current: WorkflowRunStatus, target: WorkflowRunStatus
) -> None:
    validate_workflow_run_transition(current, target)  # must not raise


def test_queued_workflow_run_cannot_go_directly_to_completed() -> None:
    with pytest.raises(InvalidWorkflowRunTransitionError):
        validate_workflow_run_transition(WorkflowRunStatus.QUEUED, WorkflowRunStatus.COMPLETED)


def test_queued_workflow_run_cannot_go_directly_to_failed() -> None:
    with pytest.raises(InvalidWorkflowRunTransitionError):
        validate_workflow_run_transition(WorkflowRunStatus.QUEUED, WorkflowRunStatus.FAILED)


@pytest.mark.parametrize("terminal_state", _WORKFLOW_RUN_TERMINAL)
@pytest.mark.parametrize("target", list(WorkflowRunStatus))
def test_terminal_workflow_run_states_accept_no_further_transitions(
    terminal_state: WorkflowRunStatus, target: WorkflowRunStatus
) -> None:
    with pytest.raises(InvalidWorkflowRunTransitionError):
        validate_workflow_run_transition(terminal_state, target)


def test_invalid_workflow_run_transition_error_carries_current_and_target() -> None:
    with pytest.raises(InvalidWorkflowRunTransitionError) as excinfo:
        validate_workflow_run_transition(WorkflowRunStatus.COMPLETED, WorkflowRunStatus.RUNNING)
    assert excinfo.value.current == WorkflowRunStatus.COMPLETED
    assert excinfo.value.target == WorkflowRunStatus.RUNNING


# --- WorkflowStepRun transitions --------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (WorkflowStepRunStatus.PENDING, WorkflowStepRunStatus.RUNNING),
        (WorkflowStepRunStatus.PENDING, WorkflowStepRunStatus.SKIPPED),
        (WorkflowStepRunStatus.PENDING, WorkflowStepRunStatus.CANCELLED),
        (WorkflowStepRunStatus.RUNNING, WorkflowStepRunStatus.COMPLETED),
        (WorkflowStepRunStatus.RUNNING, WorkflowStepRunStatus.FAILED),
        (WorkflowStepRunStatus.RUNNING, WorkflowStepRunStatus.CANCELLED),
    ],
)
def test_valid_step_run_transitions_do_not_raise(
    current: WorkflowStepRunStatus, target: WorkflowStepRunStatus
) -> None:
    validate_step_run_transition(current, target)  # must not raise


def test_pending_step_run_cannot_go_directly_to_completed() -> None:
    with pytest.raises(InvalidStepRunTransitionError):
        validate_step_run_transition(WorkflowStepRunStatus.PENDING, WorkflowStepRunStatus.COMPLETED)


def test_pending_step_run_cannot_go_directly_to_failed() -> None:
    with pytest.raises(InvalidStepRunTransitionError):
        validate_step_run_transition(WorkflowStepRunStatus.PENDING, WorkflowStepRunStatus.FAILED)


@pytest.mark.parametrize("terminal_state", _STEP_RUN_TERMINAL)
@pytest.mark.parametrize("target", list(WorkflowStepRunStatus))
def test_terminal_step_run_states_accept_no_further_transitions(
    terminal_state: WorkflowStepRunStatus, target: WorkflowStepRunStatus
) -> None:
    with pytest.raises(InvalidStepRunTransitionError):
        validate_step_run_transition(terminal_state, target)


def test_invalid_step_run_transition_error_carries_current_and_target() -> None:
    with pytest.raises(InvalidStepRunTransitionError) as excinfo:
        validate_step_run_transition(
            WorkflowStepRunStatus.COMPLETED, WorkflowStepRunStatus.RUNNING
        )
    assert excinfo.value.current == WorkflowStepRunStatus.COMPLETED
    assert excinfo.value.target == WorkflowStepRunStatus.RUNNING
