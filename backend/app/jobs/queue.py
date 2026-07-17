"""Low-level durable queue primitives (Sprint P8): claim, heartbeat,
complete, fail, retry, cancel, and recover - the only module in this
codebase that runs `SELECT ... FOR UPDATE SKIP LOCKED` against the
`jobs` table. Used exclusively by app/worker/ (the claim/heartbeat/
complete/fail/retry side) and app/jobs/service.py (the cancel side, on
behalf of the API).

Duplicate-delivery / double-execution safety is layered:
1. `claim_next_job`'s `SELECT ... FOR UPDATE SKIP LOCKED` plus its
   `status IN (QUEUED, RETRYING)` filter guarantee at most one worker
   can ever hold a given job in RUNNING at a time - a second claim
   attempt on the same row simply skips it (it's locked) or doesn't
   match the filter (it's no longer QUEUED/RETRYING).
2. Even if that guarantee were somehow violated, app.runtime.executor.
   execute_run and app.workflows.executor.execute_workflow_run
   themselves call validate_transition(run.status, RUNNING) before
   doing anything - a run that isn't QUEUED raises
   InvalidRunTransitionError/InvalidWorkflowRunTransitionError instead
   of re-executing. See app/worker/dispatch.py's handling of that case
   as an idempotent no-op, not a worker crash.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.exceptions import InvalidJobTransitionError
from app.jobs.models import validate_job_transition
from app.models.enums import JobStatus
from app.models.job import Job


def compute_backoff_seconds(attempt_count: int, *, base_seconds: float) -> float:
    """Exponential backoff: attempt N waits base_seconds * 2^(N-1) -
    mirrors app/llm/gateway.py's own retry backoff shape, one layer up
    (whole-job re-attempts rather than individual provider calls)."""
    return base_seconds * (2 ** max(attempt_count - 1, 0))


async def claim_next_job(
    db: AsyncSession, *, worker_id: str, lease_seconds: float
) -> Job | None:
    """Atomically claims the oldest eligible QUEUED/RETRYING job (whose
    next_attempt_at, if any, has already passed) and transitions it to
    RUNNING, or returns None if no job is currently eligible. Safe to
    call concurrently from multiple worker processes - see module
    docstring."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Job)
        .where(
            Job.status.in_([JobStatus.QUEUED, JobStatus.RETRYING]),
            or_(Job.next_attempt_at.is_(None), Job.next_attempt_at <= now),
        )
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        return None

    validate_job_transition(job.status, JobStatus.RUNNING)
    job.status = JobStatus.RUNNING
    job.attempt_count += 1
    job.worker_id = worker_id
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.heartbeat_at = now
    if job.started_at is None:
        job.started_at = now
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def renew_lease(db: AsyncSession, job: Job, *, lease_seconds: float) -> None:
    """Extends a RUNNING job's lease - called periodically by the worker
    while it's executing, so a genuinely-alive worker never has its job
    reclaimed by recover_stale_jobs out from under it."""
    now = datetime.now(timezone.utc)
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    db.add(job)
    await db.commit()


async def mark_completed(db: AsyncSession, job: Job) -> None:
    validate_job_transition(job.status, JobStatus.COMPLETED)
    now = datetime.now(timezone.utc)
    job.status = JobStatus.COMPLETED
    job.completed_at = now
    job.lease_expires_at = None
    db.add(job)
    await db.commit()


async def mark_failed(
    db: AsyncSession, job: Job, *, error_code: str, error_message: str
) -> None:
    validate_job_transition(job.status, JobStatus.FAILED)
    now = datetime.now(timezone.utc)
    job.status = JobStatus.FAILED
    job.error_code = error_code
    job.error_message = error_message
    job.completed_at = now
    job.lease_expires_at = None
    db.add(job)
    await db.commit()


async def mark_retrying(
    db: AsyncSession,
    job: Job,
    *,
    error_code: str,
    error_message: str,
    backoff_seconds: float,
) -> None:
    validate_job_transition(job.status, JobStatus.RETRYING)
    now = datetime.now(timezone.utc)
    job.status = JobStatus.RETRYING
    job.error_code = error_code
    job.error_message = error_message
    job.next_attempt_at = now + timedelta(seconds=backoff_seconds)
    job.lease_expires_at = None
    job.worker_id = None
    db.add(job)
    await db.commit()


async def mark_failed_or_retry(
    db: AsyncSession,
    job: Job,
    *,
    error_code: str,
    error_message: str,
    backoff_base_seconds: float,
) -> JobStatus:
    """Decides retry vs terminal failure purely from attempt_count vs
    max_attempts and applies the matching transition. Returns the
    resulting status. Callers (app/worker/dispatch.py) only reach this
    for genuinely-unexpected worker/infra-level exceptions - domain
    failures from the P6/P7 executors (LLMError, RuntimeDomainError,
    WorkflowDomainError) are never retried here, since the underlying
    run is already persisted terminally FAILED by the executor itself by
    the time such an exception reaches the worker - see
    app/worker/dispatch.py's module docstring for the full rationale."""
    if job.attempt_count < job.max_attempts:
        backoff = compute_backoff_seconds(job.attempt_count, base_seconds=backoff_base_seconds)
        await mark_retrying(
            db, job, error_code=error_code, error_message=error_message, backoff_seconds=backoff
        )
        return JobStatus.RETRYING
    await mark_failed(db, job, error_code=error_code, error_message=error_message)
    return JobStatus.FAILED


async def mark_cancelled(db: AsyncSession, job: Job) -> None:
    """Valid from RUNNING only - used when a workflow-run job's
    cancellation request was honored mid-execution (between steps) by
    app/workflows/executor.py's cancellation_check, or when a worker
    discovers its claimed job's target run was already CANCELLED by the
    time it looked (a duplicate-delivery edge case - see
    app/worker/dispatch.py). See cancel_queued_job below for the
    separate, more common QUEUED/RETRYING -> CANCELLED immediate-cancel
    path used for a job no worker has claimed yet."""
    validate_job_transition(job.status, JobStatus.CANCELLED)
    now = datetime.now(timezone.utc)
    job.status = JobStatus.CANCELLED
    job.completed_at = now
    if job.cancel_requested_at is None:
        job.cancel_requested_at = now
    db.add(job)
    await db.commit()


async def cancel_queued_job(db: AsyncSession, job: Job) -> None:
    """Valid from QUEUED or RETRYING only - a RUNNING job cannot be
    cancelled this way (see app/jobs/service.py's cancel_agent_run/
    cancel_workflow_run for how a RUNNING job's cancellation is
    requested instead).

    Deliberately does not rely on validate_job_transition alone: RUNNING
    -> CANCELLED is legal in the *general* state machine (mark_cancelled
    above uses exactly that transition, for the separate worker-initiated
    cancellation path), so the shared transition table cannot by itself
    reject a RUNNING job here without also breaking mark_cancelled. This
    function instead enforces its own narrower, function-specific origin
    rule first - QUEUED/RETRYING only - before ever consulting the
    transition table, so the general state machine stays untouched."""
    if job.status not in (JobStatus.QUEUED, JobStatus.RETRYING):
        raise InvalidJobTransitionError(job.status, JobStatus.CANCELLED)
    validate_job_transition(job.status, JobStatus.CANCELLED)
    now = datetime.now(timezone.utc)
    job.status = JobStatus.CANCELLED
    job.completed_at = now
    db.add(job)
    await db.commit()


async def recover_stale_jobs(
    db: AsyncSession, *, backoff_base_seconds: float
) -> int:
    """Reclaims RUNNING jobs whose lease has already expired - crash/
    stuck-worker recovery. Never touches a job with a currently-valid
    lease (the query's own WHERE clause excludes it) and never replays
    COMPLETED/FAILED/CANCELLED work (only RUNNING jobs are eligible).
    Jobs with attempts remaining go back to RETRYING (with the normal
    backoff schedule, not an instant reclaim, in case whatever killed
    the worker is still an active condition); jobs with no attempts left
    go straight to FAILED. Returns the number of jobs reclaimed. Safe to
    call concurrently / repeatedly - SKIP LOCKED means two recovery
    passes never double-reclaim the same row."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.RUNNING, Job.lease_expires_at < now)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    stale_jobs = list(result.scalars().all())

    for job in stale_jobs:
        job.worker_id = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        if job.attempt_count < job.max_attempts:
            job.status = JobStatus.RETRYING
            backoff = compute_backoff_seconds(job.attempt_count, base_seconds=backoff_base_seconds)
            job.next_attempt_at = now + timedelta(seconds=backoff)
            job.error_code = "WorkerLeaseExpired"
            job.error_message = (
                "Worker heartbeat lease expired before the job finished - reclaimed for retry."
            )
        else:
            job.status = JobStatus.FAILED
            job.completed_at = now
            job.error_code = "WorkerLeaseExpired"
            job.error_message = (
                "Worker heartbeat lease expired and max_attempts was already exhausted."
            )
        db.add(job)

    if stale_jobs:
        await db.commit()
    return len(stale_jobs)
