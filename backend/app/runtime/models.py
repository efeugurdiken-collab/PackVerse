"""Framework-agnostic runtime domain logic (Sprint P6): the AgentRun
state machine. Mirrors app/llm/models.py's role for this package - plain
data/logic with no FastAPI or SQLAlchemy imports (the persisted ORM
model itself lives in app/models/agent_run.py, alongside every other
model in this codebase - see that module's docstring).

Transition table:
    QUEUED    -> RUNNING, CANCELLED
    RUNNING   -> COMPLETED, FAILED, CANCELLED
    COMPLETED -> (terminal, no transitions out)
    FAILED    -> (terminal, no transitions out)
    CANCELLED -> (terminal, no transitions out)

RUNNING -> CANCELLED is a real, validated transition even though
app/runtime/executor.py's own synchronous execution never triggers it
itself (there is no background task queue in this sprint, so nothing
else is running concurrently against the same row while executor.py
holds it) - see the sprint report's "Important architectural decisions"
for why this is still meaningful and independently tested at the
service layer, not dead code.
"""
from __future__ import annotations

from app.models.enums import AgentRunStatus
from app.runtime.exceptions import InvalidRunTransitionError

TRANSITIONS: dict[AgentRunStatus, frozenset[AgentRunStatus]] = {
    AgentRunStatus.QUEUED: frozenset({AgentRunStatus.RUNNING, AgentRunStatus.CANCELLED}),
    AgentRunStatus.RUNNING: frozenset(
        {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}
    ),
    AgentRunStatus.COMPLETED: frozenset(),
    AgentRunStatus.FAILED: frozenset(),
    AgentRunStatus.CANCELLED: frozenset(),
}


def validate_transition(current: AgentRunStatus, target: AgentRunStatus) -> None:
    """Raises InvalidRunTransitionError if `target` is not reachable from
    `current` per TRANSITIONS above. Callers apply the transition
    themselves after this returns without raising - this function only
    validates, it never mutates anything."""
    if target not in TRANSITIONS[current]:
        raise InvalidRunTransitionError(current, target)
