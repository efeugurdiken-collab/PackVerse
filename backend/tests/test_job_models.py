"""Tests for the Job state machine (Sprint P8):
app/jobs/models.py's validate_job_transition."""
from __future__ import annotations

import pytest

from app.jobs.exceptions import InvalidJobTransitionError
from app.jobs.models import validate_job_transition
from app.models.enums import JobStatus

_TERMINAL_STATES = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.QUEUED, JobStatus.RUNNING),
        (JobStatus.QUEUED, JobStatus.CANCELLED),
        (JobStatus.RUNNING, JobStatus.COMPLETED),
        (JobStatus.RUNNING, JobStatus.FAILED),
        (JobStatus.RUNNING, JobStatus.RETRYING),
        (JobStatus.RUNNING, JobStatus.CANCELLED),
        (JobStatus.RETRYING, JobStatus.RUNNING),
        (JobStatus.RETRYING, JobStatus.CANCELLED),
    ],
)
def test_valid_transitions_do_not_raise(current: JobStatus, target: JobStatus) -> None:
    validate_job_transition(current, target)  # must not raise


def test_queued_cannot_go_directly_to_completed() -> None:
    with pytest.raises(InvalidJobTransitionError):
        validate_job_transition(JobStatus.QUEUED, JobStatus.COMPLETED)


def test_queued_cannot_go_directly_to_failed() -> None:
    with pytest.raises(InvalidJobTransitionError):
        validate_job_transition(JobStatus.QUEUED, JobStatus.FAILED)


def test_queued_cannot_go_directly_to_retrying() -> None:
    with pytest.raises(InvalidJobTransitionError):
        validate_job_transition(JobStatus.QUEUED, JobStatus.RETRYING)


def test_retrying_cannot_go_directly_to_completed_or_failed() -> None:
    with pytest.raises(InvalidJobTransitionError):
        validate_job_transition(JobStatus.RETRYING, JobStatus.COMPLETED)
    with pytest.raises(InvalidJobTransitionError):
        validate_job_transition(JobStatus.RETRYING, JobStatus.FAILED)


@pytest.mark.parametrize("terminal_state", _TERMINAL_STATES)
@pytest.mark.parametrize("target", list(JobStatus))
def test_terminal_states_accept_no_further_transitions(
    terminal_state: JobStatus, target: JobStatus
) -> None:
    with pytest.raises(InvalidJobTransitionError):
        validate_job_transition(terminal_state, target)


def test_invalid_transition_error_carries_current_and_target() -> None:
    with pytest.raises(InvalidJobTransitionError) as excinfo:
        validate_job_transition(JobStatus.COMPLETED, JobStatus.RUNNING)
    assert excinfo.value.current == JobStatus.COMPLETED
    assert excinfo.value.target == JobStatus.RUNNING
