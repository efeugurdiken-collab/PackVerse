"""Worker dispatch (Sprint P8): maps one already-claimed Job to the
correct P6/P7 executor call. The only place a job's persisted
`input_json` is read back into a real `user_input`/`context` and handed
to app.runtime.executor.execute_run or
app.workflows.executor.execute_workflow_run - never duplicates their
logic ("Do not duplicate execution logic in the worker" / "Reuse the P6
runtime executor" / "Reuse the P7 workflow executor"), and never calls
app.llm or app.services.llm_service directly ("Do not call providers
directly from API or worker code").

Retry-policy interpretation (see also app/jobs/queue.py's
mark_failed_or_retry docstring): "Retry only transient infrastructure
failures... Do not retry deterministic failures such as invalid
definitions, inactive agents/workflows, malformed input, configuration
errors, authorization failures, cancelled runs" describes exactly the
failure modes app.llm.exceptions.LLMError, app.runtime.exceptions.
RuntimeDomainError, and app.workflows.exceptions.WorkflowDomainError
already represent - LLMError already went through Sprint P5's own
gateway-level retry policy before ever reaching this far, and by the
time any of the three reaches this module, the underlying AgentRun/
WorkflowRun has ALREADY been persisted terminally FAILED by the executor
itself. Retrying the JOB at that point would mean re-running a run whose
own state machine no longer permits QUEUED -> RUNNING - exactly
backwards. So all three always terminate the job as FAILED too, never
RETRYING.

The only thing genuinely retried here is a truly unexpected exception -
one that did not come from the executors' own well-defined error
surface, meaning something broke in the worker's own claim/dispatch/DB-
connectivity path rather than in the business logic being executed. That
is the "transient infrastructure failure" this sprint's retry policy is
about; crash/lease-timeout recovery (app/jobs/queue.py's
recover_stale_jobs) is the complementary half of the same policy, for
when the worker process dies before it can even reach an except block
here.

Sprint P10B3 adds a third job_type, asset_ingestion, reusing app.
services.ingestion_service.ingest_asset() the same "never duplicate
domain logic in the worker" way the P6/P7 branches reuse execute_run/
execute_workflow_run. Its error classification follows the identical
terminal-vs-retryable split: app.services.exceptions' ingestion-domain
error hierarchy (AssetNotFoundError, AssetDeletedError,
AssetNotIngestableError, AssetAlreadyIngestedError,
AssetStorageOperationFailedError, IngestionExtractionFailedError,
EmptyExtractedTextError, IngestionEmbeddingMismatchError) is exactly as
deterministic as RuntimeDomainError/WorkflowDomainError - re-running
would fail identically - so it's always terminal (mark_failed), never
retried. LLMError is terminal here for the same reason it already is
for agent/workflow jobs: app.llm.gateway.LLMGateway._retry_async already
retried (or determined not to retry) any transient failure inside the
embed() call itself before ever raising past it - there is no second,
job-level retry layer for LLMError anywhere in this codebase. Only a
genuinely unexpected exception (storage I/O blip, DB connectivity drop,
etc.) reaches _retry_or_fail below, same as the other two branches.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.jobs import queue
from app.jobs.service import AGENT_RUN_JOB_TYPE, ASSET_INGESTION_JOB_TYPE, WORKFLOW_RUN_JOB_TYPE
from app.llm.exceptions import LLMError
from app.llm.gateway import LLMGateway
from app.models.agent_run import AgentRun
from app.models.document_chunk import DocumentChunk
from app.models.enums import AgentRunStatus, WorkflowRunStatus
from app.models.job import Job
from app.models.workflow_run import WorkflowRun
from app.runtime.exceptions import RuntimeDomainError
from app.runtime.executor import execute_run
from app.services.exceptions import (
    AssetAlreadyIngestedError,
    AssetDeletedError,
    AssetNotFoundError,
    AssetNotIngestableError,
    AssetStorageOperationFailedError,
    EmptyExtractedTextError,
    IngestionEmbeddingMismatchError,
    IngestionExtractionFailedError,
)
from app.services.ingestion_service import ingest_asset
from app.storage.base import StorageBackend
from app.workflows.exceptions import WorkflowDomainError, WorkflowRunCancelledDuringExecutionError
from app.workflows.executor import execute_workflow_run

logger = logging.getLogger(__name__)

# Every exception type the P6/P7 executors themselves already turn into
# a terminal FAILED run before re-raising - see module docstring for why
# these never trigger a job-level retry.
_DOMAIN_ERROR_TYPES = (LLMError, RuntimeDomainError, WorkflowDomainError)

# ingest_asset()'s own deterministic failure surface (app/services/
# exceptions.py's "Ingestion domain errors" section) - LLMError is
# handled separately below since it's shared with the other two job
# types, not ingestion-specific.
_INGESTION_DOMAIN_ERROR_TYPES = (
    AssetNotFoundError,
    AssetDeletedError,
    AssetNotIngestableError,
    AssetAlreadyIngestedError,
    AssetStorageOperationFailedError,
    IngestionExtractionFailedError,
    EmptyExtractedTextError,
    IngestionEmbeddingMismatchError,
)


async def process_claimed_job(
    db: AsyncSession, gateway: LLMGateway, settings: Settings, *, job: Job, storage: StorageBackend
) -> None:
    """Executes one already-claimed (RUNNING) job to completion,
    persisting the outcome on both the Job and (via the P6/P7 executors
    themselves, or DocumentChunk rows for ingestion) its target. Never
    raises - every exception is caught and turned into the job's own
    COMPLETED/FAILED/RETRYING/CANCELLED state, so a single bad job can
    never crash the worker's poll loop (see app/worker/runner.py).
    `storage` is only used by the asset_ingestion branch."""
    if job.job_type == AGENT_RUN_JOB_TYPE:
        await _process_agent_run_job(db, gateway, settings, job=job)
    elif job.job_type == WORKFLOW_RUN_JOB_TYPE:
        await _process_workflow_run_job(db, gateway, settings, job=job)
    elif job.job_type == ASSET_INGESTION_JOB_TYPE:
        await _process_asset_ingestion_job(db, gateway, settings, job=job, storage=storage)
    else:
        # Defensive - not expected in practice: every enqueue path in
        # app/jobs/service.py only ever sets one of the three constants
        # above.
        logger.error("job %s: unknown job_type %r", job.id, job.job_type)
        await queue.mark_failed(
            db, job, error_code="UnknownJobType", error_message=f"Unknown job_type {job.job_type!r}"
        )


async def _process_agent_run_job(
    db: AsyncSession, gateway: LLMGateway, settings: Settings, *, job: Job
) -> None:
    assert job.target_run_id is not None  # enqueue_agent_run always sets this

    run = await db.get(AgentRun, job.target_run_id)
    if run is None or run.status != AgentRunStatus.QUEUED:
        # Already executed (duplicate delivery) or the run/job pairing
        # is inconsistent - either way there is nothing safe to do:
        # re-executing an already-terminal run would violate "a
        # completed run must never be executed twice." See module
        # docstring's duplicate-delivery note.
        logger.info(
            "job %s: target agent run %s is not QUEUED (already handled) - skipping execution",
            job.id, job.target_run_id,
        )
        if run is not None and run.status == AgentRunStatus.CANCELLED:
            await queue.mark_cancelled(db, job)
        else:
            await queue.mark_completed(db, job)
        return

    user_input = str(job.input_json.get("user_input", ""))
    raw_context = job.input_json.get("context")
    context = raw_context if isinstance(raw_context, dict) else None

    try:
        await execute_run(db, gateway, settings, run=run, user_input=user_input, context=context)
    except _DOMAIN_ERROR_TYPES as exc:
        await queue.mark_failed(db, job, error_code=type(exc).__name__, error_message=str(exc))
        return
    except Exception as exc:  # broad on purpose - see module docstring above
        await _retry_or_fail(
            db, job, exc, backoff_base_seconds=settings.job_retry_backoff_base_seconds
        )
        return

    await queue.mark_completed(db, job)


async def _process_workflow_run_job(
    db: AsyncSession, gateway: LLMGateway, settings: Settings, *, job: Job
) -> None:
    assert job.target_run_id is not None  # enqueue_workflow_run always sets this

    run = await db.get(WorkflowRun, job.target_run_id)
    if run is None or run.status != WorkflowRunStatus.QUEUED:
        logger.info(
            "job %s: target workflow run %s is not QUEUED (already handled) - skipping execution",
            job.id, job.target_run_id,
        )
        if run is not None and run.status == WorkflowRunStatus.CANCELLED:
            await queue.mark_cancelled(db, job)
        else:
            await queue.mark_completed(db, job)
        return

    user_input = str(job.input_json.get("user_input", ""))
    raw_context = job.input_json.get("context")
    context = raw_context if isinstance(raw_context, dict) else None

    job_id = job.id

    async def _cancellation_check() -> bool:
        """Re-fetches the Job fresh so a cancellation requested by a
        concurrent API request (app/jobs/service.py's
        cancel_workflow_run) is actually seen - `job` itself is only
        ever mutated by this same worker, never by the API."""
        current = await db.get(Job, job_id)
        return current is not None and current.cancel_requested_at is not None

    try:
        await execute_workflow_run(
            db,
            gateway,
            settings,
            run=run,
            workflow_user_input=user_input,
            context=context,
            cancellation_check=_cancellation_check,
        )
    except WorkflowRunCancelledDuringExecutionError:
        await queue.mark_cancelled(db, job)
        return
    except _DOMAIN_ERROR_TYPES as exc:
        await queue.mark_failed(db, job, error_code=type(exc).__name__, error_message=str(exc))
        return
    except Exception as exc:  # broad on purpose - see module docstring above
        await _retry_or_fail(
            db, job, exc, backoff_base_seconds=settings.job_retry_backoff_base_seconds
        )
        return

    await queue.mark_completed(db, job)


async def _process_asset_ingestion_job(
    db: AsyncSession,
    gateway: LLMGateway,
    settings: Settings,
    *,
    job: Job,
    storage: StorageBackend,
) -> None:
    assert job.target_run_id is not None  # enqueue_asset_ingestion always sets this
    asset_id = job.target_run_id

    already_ingested = await db.scalar(
        select(DocumentChunk.id).where(DocumentChunk.asset_id == asset_id).limit(1)
    )
    if already_ingested is not None:
        # Duplicate delivery: chunks already exist for this asset, so an
        # earlier attempt at this same job (or one that raced past
        # app/models/job.py's uq_jobs_active_asset_ingestion index before
        # it committed) already finished the work. Same "nothing safe to
        # do, mark it done" handling as the agent/workflow branches'
        # already-terminal-run case above - re-running ingest_asset()
        # would only fail with AssetAlreadyIngestedError anyway.
        logger.info(
            "job %s: asset %s already has document_chunks (already handled) - "
            "skipping ingestion",
            job.id, asset_id,
        )
        await queue.mark_completed(db, job)
        return

    embedding_model = str(job.input_json.get("embedding_model", ""))
    embedding_provider = job.input_json.get("embedding_provider")
    embedding_provider = embedding_provider if isinstance(embedding_provider, str) else None
    raw_chunk_size = job.input_json.get("chunk_size", 1000)
    chunk_size = raw_chunk_size if isinstance(raw_chunk_size, int) else 1000
    raw_chunk_overlap = job.input_json.get("chunk_overlap", 200)
    chunk_overlap = raw_chunk_overlap if isinstance(raw_chunk_overlap, int) else 200

    try:
        await ingest_asset(
            db,
            storage,
            gateway,
            asset_id=asset_id,
            embedding_model=embedding_model,
            embedding_provider=embedding_provider,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    except (*_INGESTION_DOMAIN_ERROR_TYPES, LLMError) as exc:
        await queue.mark_failed(db, job, error_code=type(exc).__name__, error_message=str(exc))
        return
    except Exception as exc:  # broad on purpose - see module docstring above
        await _retry_or_fail(
            db, job, exc, backoff_base_seconds=settings.job_retry_backoff_base_seconds
        )
        return

    await queue.mark_completed(db, job)


async def _retry_or_fail(
    db: AsyncSession, job: Job, exc: Exception, *, backoff_base_seconds: float
) -> None:
    logger.exception("job %s: unexpected worker-level error", job.id)
    await queue.mark_failed_or_retry(
        db,
        job,
        error_code=type(exc).__name__,
        error_message=str(exc),
        backoff_base_seconds=backoff_base_seconds,
    )
