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
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.jobs import queue
from app.jobs.service import AGENT_RUN_JOB_TYPE, WORKFLOW_RUN_JOB_TYPE
from app.llm.exceptions import LLMError
from app.llm.gateway import LLMGateway
from app.models.agent_run import AgentRun
from app.models.enums import AgentRunStatus, WorkflowRunStatus
from app.models.job import Job
from app.models.workflow_run import WorkflowRun
from app.runtime.exceptions import RuntimeDomainError
from app.runtime.executor import execute_run
from app.workflows.exceptions import WorkflowDomainError, WorkflowRunCancelledDuringExecutionError
from app.workflows.executor import execute_workflow_run

logger = logging.getLogger(__name__)

# Every exception type the P6/P7 executors themselves already turn into
# a terminal FAILED run before re-raising - see module docstring for why
# these never trigger a job-level retry.
_DOMAIN_ERROR_TYPES = (LLMError, RuntimeDomainError, WorkflowDomainError)


async def process_claimed_job(
    db: AsyncSession, gateway: LLMGateway, settings: Settings, *, job: Job
) -> None:
    """Executes one already-claimed (RUNNING) job to completion,
    persisting the outcome on both the Job and (via the P6/P7 executors
    themselves) its target run. Never raises - every exception is caught
    and turned into the job's own COMPLETED/FAILED/RETRYING/CANCELLED
    state, so a single bad job can never crash the worker's poll loop
    (see app/worker/runner.py)."""
    if job.job_type == AGENT_RUN_JOB_TYPE:
        await _process_agent_run_job(db, gateway, settings, job=job)
    elif job.job_type == WORKFLOW_RUN_JOB_TYPE:
        await _process_workflow_run_job(db, gateway, settings, job=job)
    else:
        # Defensive - not expected in practice: every enqueue path in
        # app/jobs/service.py only ever sets one of the two constants
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
