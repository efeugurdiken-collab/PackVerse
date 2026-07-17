"""Job-queue service layer (Sprint P8): enqueueing agent/workflow runs
atomically alongside their Job row, and cancellation.

Enqueue-safety design ("Avoid unsafe dual writes... do not use a naive
commit the run, then hope enqueue succeeds flow"): enqueue_agent_run and
enqueue_workflow_run build BOTH the run row (via app.runtime.service.
create_run / app.workflows.service.create_workflow_run, called with
commit=False - a small, backward-compatible addition to both functions,
see their own docstrings) and the Job row, then issue exactly ONE
db.commit() for both. There is no separate outbox table or relay process
- the "queue" IS a table in the same PostgreSQL database as the run
tables, so the run and its Job are, by construction, either both
committed or both rolled back together. See app/jobs/__init__.py's
module docstring for the full queue-technology rationale.

Cancellation ("queued run -> cancelled, queued job -> cancelled"):
cancel_agent_run/cancel_workflow_run look up the Job paired with a run
and branch on the JOB's status (not just the run's):
- QUEUED/RETRYING: cancel the job immediately (so no worker can ever
  claim it), then cancel the run via the existing, unchanged P6/P7
  service-layer cancel (still handles ownership/authorization/terminal-
  state rules exactly as before).
- RUNNING (agent_run): raises JobAlreadyRunningError (409) - an
  in-flight provider request cannot be interrupted, no "between steps"
  checkpoint exists for a single call. Documented limitation, not a bug.
- RUNNING (workflow_run): sets Job.cancel_requested_at (idempotent) and
  returns the run unchanged (still RUNNING) - the workflow worker checks
  this flag between steps (app/workflows/executor.py's
  cancellation_check parameter) and stops the run there once it notices.
- Terminal (COMPLETED/FAILED/CANCELLED) or no job at all (e.g. a run
  constructed directly via the ORM, as several P6/P7 tests do): falls
  through to the existing run-level cancel unchanged, which raises the
  correct InvalidRunTransitionError/InvalidWorkflowRunTransitionError or
  succeeds exactly as it did before this sprint.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs import queue
from app.jobs.exceptions import JobAlreadyRunningError, JobNotFoundError
from app.models.agent_run import AgentRun
from app.models.enums import JobStatus
from app.models.job import Job
from app.models.user import User
from app.models.workflow_run import WorkflowRun
from app.runtime import service as runtime_service
from app.workflows import service as workflow_service

AGENT_RUN_JOB_TYPE = "agent_run"
WORKFLOW_RUN_JOB_TYPE = "workflow_run"


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await db.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    return job


async def _get_job_for_run(db: AsyncSession, *, job_type: str, run_id: uuid.UUID) -> Job | None:
    result = await db.execute(
        select(Job).where(Job.job_type == job_type, Job.target_run_id == run_id)
    )
    return result.scalar_one_or_none()


async def enqueue_agent_run(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
    user_input: str,
    context: dict[str, object] | None,
    max_attempts: int,
) -> tuple[AgentRun, Job]:
    """Validates the agent (via create_run's own existing
    get_active_agent check) and persists a QUEUED AgentRun plus a
    QUEUED Job in one transaction. Does not execute anything - see
    app/worker/dispatch.py."""
    run = await runtime_service.create_run(
        db, agent_id=agent_id, created_by_user_id=created_by_user_id, commit=False
    )
    job = Job(
        id=uuid.uuid4(),
        job_type=AGENT_RUN_JOB_TYPE,
        status=JobStatus.QUEUED,
        target_run_id=run.id,
        input_json={"user_input": user_input, "context": context},
        max_attempts=max_attempts,
    )
    db.add(job)
    await db.commit()
    await db.refresh(run)
    await db.refresh(job)
    return run, job


async def enqueue_workflow_run(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
    user_input: str,
    context: dict[str, object] | None,
    max_attempts: int,
) -> tuple[WorkflowRun, Job]:
    """Validates the workflow definition and every referenced agent (via
    create_workflow_run's own existing checks) and persists a QUEUED
    WorkflowRun (plus one PENDING WorkflowStepRun per step) and a QUEUED
    Job in one transaction."""
    run = await workflow_service.create_workflow_run(
        db, workflow_id=workflow_id, created_by_user_id=created_by_user_id, commit=False
    )
    job = Job(
        id=uuid.uuid4(),
        job_type=WORKFLOW_RUN_JOB_TYPE,
        status=JobStatus.QUEUED,
        target_run_id=run.id,
        input_json={"user_input": user_input, "context": context},
        max_attempts=max_attempts,
    )
    db.add(job)
    await db.commit()
    await db.refresh(run)
    await db.refresh(job)
    return run, job


async def cancel_agent_run(db: AsyncSession, run_id: uuid.UUID, current_user: User) -> AgentRun:
    await runtime_service.get_run(db, run_id, current_user)  # authorization/existence check
    job = await _get_job_for_run(db, job_type=AGENT_RUN_JOB_TYPE, run_id=run_id)

    if job is not None and job.status in (JobStatus.QUEUED, JobStatus.RETRYING):
        await queue.cancel_queued_job(db, job)
        return await runtime_service.cancel_run(db, run_id, current_user)

    if job is not None and job.status == JobStatus.RUNNING:
        raise JobAlreadyRunningError(job.id)

    # No job (defensive - e.g. a run constructed directly via the ORM),
    # or the job is already terminal: delegate unchanged.
    return await runtime_service.cancel_run(db, run_id, current_user)


async def cancel_workflow_run(
    db: AsyncSession, run_id: uuid.UUID, current_user: User
) -> WorkflowRun:
    await workflow_service.get_run(db, run_id, current_user)  # authorization/existence check
    job = await _get_job_for_run(db, job_type=WORKFLOW_RUN_JOB_TYPE, run_id=run_id)

    if job is not None and job.status in (JobStatus.QUEUED, JobStatus.RETRYING):
        await queue.cancel_queued_job(db, job)
        return await workflow_service.cancel_run(db, run_id, current_user)

    if job is not None and job.status == JobStatus.RUNNING:
        if job.cancel_requested_at is None:
            job.cancel_requested_at = datetime.now(timezone.utc)
            db.add(job)
            await db.commit()
        run = await workflow_service.get_run(db, run_id, current_user)
        return run

    return await workflow_service.cancel_run(db, run_id, current_user)
