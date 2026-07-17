"""Job queue domain exceptions (Sprint P8).

Independent hierarchy, same pattern as app.runtime.exceptions and
app.workflows.exceptions: app/jobs/service.py and app/jobs/queue.py
raise these, app/api/v1/runs.py and app/api/v1/workflow_runs.py are the
only places they become HTTP status codes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.enums import JobStatus


class JobDomainError(Exception):
    """Base class for all app.jobs errors."""


class JobNotFoundError(JobDomainError):
    def __init__(self, job_id: object) -> None:
        super().__init__(f"Job {job_id} not found")
        self.job_id = job_id


class InvalidJobTransitionError(JobDomainError):
    def __init__(self, current: "JobStatus", target: "JobStatus") -> None:
        super().__init__(f"Cannot transition job from {current.value!r} to {target.value!r}")
        self.current = current
        self.target = target


class JobAlreadyRunningError(JobDomainError):
    """Raised when attempting to cancel an agent-run job that a worker
    has already claimed and is currently executing. Unlike a workflow-run
    job (which can honor a cancellation request between steps - see
    app/workflows/executor.py's cancellation_check parameter), a single
    agent-run job has no "between steps" checkpoint: its one in-flight
    provider request cannot be safely interrupted. See
    app/jobs/service.py's cancel_agent_run and the Sprint P8 report's
    documented cancellation limitation."""

    def __init__(self, job_id: object) -> None:
        super().__init__(
            f"Job {job_id} is already running and cannot be cancelled - "
            "in-flight provider requests cannot be interrupted"
        )
        self.job_id = job_id
