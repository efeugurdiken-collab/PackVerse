"""Framework-agnostic Job domain logic (Sprint P8): the Job state
machine. Mirrors app/runtime/models.py's and app/workflows/models.py's
role for this package - plain data/logic with no FastAPI or SQLAlchemy
imports (the persisted ORM model lives in app/models/job.py).

Transition table (see app/models/enums.py's JobStatus docstring for the
full rationale):
    QUEUED    -> RUNNING, CANCELLED
    RUNNING   -> COMPLETED, FAILED, RETRYING, CANCELLED
    RETRYING  -> RUNNING, CANCELLED
    COMPLETED -> (terminal)
    FAILED    -> (terminal)
    CANCELLED -> (terminal)

Re-cancelling an already-CANCELLED job is handled as an idempotent
no-op in app/jobs/service.py's cancel_job, the same way P7's
cancel_run handles an already-CANCELLED WorkflowRun - not by adding a
CANCELLED -> CANCELLED self-loop here, which would be wrong for every
other caller of validate_job_transition.
"""
from __future__ import annotations

from app.jobs.exceptions import InvalidJobTransitionError
from app.models.enums import JobStatus

JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset(
        {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.RETRYING, JobStatus.CANCELLED}
    ),
    JobStatus.RETRYING: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def validate_job_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in JOB_TRANSITIONS[current]:
        raise InvalidJobTransitionError(current, target)
