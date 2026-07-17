"""Tests for the AgentRun state machine (Sprint P6):
app/runtime/models.py's validate_transition."""
from __future__ import annotations

import pytest

from app.models.enums import AgentRunStatus
from app.runtime.exceptions import InvalidRunTransitionError
from app.runtime.models import validate_transition

_TERMINAL_STATES = (
    AgentRunStatus.COMPLETED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (AgentRunStatus.QUEUED, AgentRunStatus.RUNNING),
        (AgentRunStatus.QUEUED, AgentRunStatus.CANCELLED),
        (AgentRunStatus.RUNNING, AgentRunStatus.COMPLETED),
        (AgentRunStatus.RUNNING, AgentRunStatus.FAILED),
        (AgentRunStatus.RUNNING, AgentRunStatus.CANCELLED),
    ],
)
def test_valid_transitions_do_not_raise(
    current: AgentRunStatus, target: AgentRunStatus
) -> None:
    validate_transition(current, target)  # must not raise


def test_queued_cannot_go_directly_to_completed() -> None:
    with pytest.raises(InvalidRunTransitionError):
        validate_transition(AgentRunStatus.QUEUED, AgentRunStatus.COMPLETED)


def test_queued_cannot_go_directly_to_failed() -> None:
    with pytest.raises(InvalidRunTransitionError):
        validate_transition(AgentRunStatus.QUEUED, AgentRunStatus.FAILED)


@pytest.mark.parametrize("terminal_state", _TERMINAL_STATES)
@pytest.mark.parametrize("target", list(AgentRunStatus))
def test_terminal_states_accept_no_further_transitions(
    terminal_state: AgentRunStatus, target: AgentRunStatus
) -> None:
    with pytest.raises(InvalidRunTransitionError):
        validate_transition(terminal_state, target)


def test_invalid_transition_error_carries_current_and_target() -> None:
    with pytest.raises(InvalidRunTransitionError) as excinfo:
        validate_transition(AgentRunStatus.COMPLETED, AgentRunStatus.RUNNING)
    assert excinfo.value.current == AgentRunStatus.COMPLETED
    assert excinfo.value.target == AgentRunStatus.RUNNING
