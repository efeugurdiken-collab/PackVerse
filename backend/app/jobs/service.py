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

Asset ingestion (Sprint P10B3): enqueue_asset_ingestion follows the same
"validate via the existing check, then persist one Job row, one commit"
shape as enqueue_agent_run/enqueue_workflow_run, but there is no paired
run row to create alongside it - the Job row IS the durable record of
this ingestion attempt (app/services/ingestion_service.py's DocumentChunk
rows are the eventual result). Concurrent double-enqueue for the same
asset is closed at the database level by app/models/job.py's
uq_jobs_active_asset_ingestion partial unique index, not just the
upfront check_ingestable() call - see that index's docstring for why
this differs from agent/workflow runs, where concurrent double-
submission is legitimate rather than a bug. No cancel_asset_ingestion
here (yet) - see the Sprint P10B3 report's Known Limitations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs import queue
from app.jobs.exceptions import JobAlreadyRunningError, JobNotFoundError
from app.models.agent_run import AgentRun
from app.models.enums import JobStatus
from app.models.job import Job
from app.models.user import User
from app.models.workflow_run import WorkflowRun
from app.runtime import service as runtime_service
from app.services import ingestion_service
from app.services.exceptions import AssetIngestionAlreadyQueuedError
from app.workflows import service as workflow_service

AGENT_RUN_JOB_TYPE = "agent_run"
WORKFLOW_RUN_JOB_TYPE = "workflow_run"
ASSET_INGESTION_JOB_TYPE = "asset_ingestion"


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


async def enqueue_asset_ingestion(
    db: AsyncSession,
    *,
    asset_id: uuid.UUID,
    embedding_model: str,
    embedding_provider: str | None,
    chunk_size: int,
    chunk_overlap: int,
    max_attempts: int,
) -> Job:
    """Validates asset_id via app.services.ingestion_service.
    check_ingestable (asset exists/not deleted, content type
    ingestable, not already ingested - AssetNotFoundError/
    AssetDeletedError/AssetNotIngestableError/AssetAlreadyIngestedError
    propagate unchanged) and persists a QUEUED Job pointing at it
    (target_run_id=asset_id, job_type=ASSET_INGESTION_JOB_TYPE) - same
    polymorphic-target convention as enqueue_agent_run/
    enqueue_workflow_run's target_run_id, see app/models/job.py's module
    docstring. embedding_model/embedding_provider/chunk_size/
    chunk_overlap travel in input_json exactly like user_input/context
    do for an agent/workflow run - app/worker/dispatch.py's
    _process_asset_ingestion_job reads them back out to call
    ingest_asset().

    Raises AssetIngestionAlreadyQueuedError if a non-terminal ingestion
    job already exists for this asset - app/models/job.py's
    uq_jobs_active_asset_ingestion partial unique index is the actual
    guarantee (this function's IntegrityError handling below is what
    turns a lost race into that domain error); nothing here does a
    "check then insert" that could itself race safely without it.
    """
    await ingestion_service.check_ingestable(db, asset_id)

    job = Job(
        id=uuid.uuid4(),
        job_type=ASSET_INGESTION_JOB_TYPE,
        status=JobStatus.QUEUED,
        target_run_id=asset_id,
        input_json={
            "embedding_model": embedding_model,
            "embedding_provider": embedding_provider,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        },
        max_attempts=max_attempts,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AssetIngestionAlreadyQueuedError(asset_id) from exc
    await db.refresh(job)
    return job


async def get_latest_asset_ingestion_job(db: AsyncSession, asset_id: uuid.UUID) -> Job | None:
    """The most recently created asset_ingestion Job targeting
    asset_id, or None if this asset has never had one enqueued - backs
    GET /assets/{asset_id}/ingest (app/api/v1/assets.py). Deliberately
    the newest, not "the currently-active one": once a job reaches a
    terminal state a caller can enqueue a new one (see
    uq_jobs_active_asset_ingestion's docstring), and the newest job is
    always the one whose status answers "what happened to my most
    recent ingest request" - the same job a caller who just POSTed
    would expect to see."""
    result = await db.execute(
        select(Job)
        .where(Job.job_type == ASSET_INGESTION_JOB_TYPE, Job.target_run_id == asset_id)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


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
