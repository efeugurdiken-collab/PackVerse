"""Execution orchestration (Sprint P7): QUEUED -> RUNNING -> sequential
per-step execution through the P6 runtime -> COMPLETED/FAILED.

Mirrors app/runtime/executor.py's shape closely: persist state before
each call, persist the outcome (success or failure) after it, and on
failure persist first and then re-raise rather than swallowing the
error - app/api/v1/workflow_runs.py decides what HTTP status a failure
becomes, this module only owns the WorkflowRun/WorkflowStepRun rows'
own state and never touches HTTPException.

Deliberately calls app.runtime.service.create_run + app.runtime.executor
.execute_run for every step rather than invoking app.llm or
app.services.llm_service directly - "The executor must not call provider
adapters or the LLM Gateway directly" (sprint section 9). Every step
therefore also produces a real P6 AgentRun row (and, through that, a
real P5 llm_requests audit row) - see WorkflowStepRun.agent_run_id.

On a step failure: that step's own WorkflowStepRun becomes FAILED, every
other still-PENDING WorkflowStepRun becomes SKIPPED, and the WorkflowRun
itself becomes FAILED - "Do not continue executing later steps after a
failure" (sprint section 6). No step after the failed one is invoked.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.exceptions import LLMError
from app.llm.gateway import LLMGateway
from app.models.agent_run import AgentRun
from app.models.enums import WorkflowRunStatus, WorkflowStepRunStatus
from app.models.workflow_definition import WorkflowDefinition
from app.models.workflow_run import WorkflowRun
from app.models.workflow_step_run import WorkflowStepRun
from app.runtime import service as runtime_service
from app.runtime.exceptions import RuntimeDomainError
from app.runtime.executor import execute_run
from app.workflows.definition import parse_workflow_steps
from app.workflows.exceptions import (
    WorkflowDomainError,
    WorkflowInputResolutionError,
    WorkflowNotFoundError,
)
from app.workflows.input_builder import build_step_input
from app.workflows.models import validate_step_run_transition, validate_workflow_run_transition


async def _fail_workflow_run(
    db: AsyncSession, run: WorkflowRun, exc: BaseException, *, duration_ms: int
) -> None:
    """Marks `run` FAILED and every still-PENDING WorkflowStepRun under it
    SKIPPED, in one commit. Called both when a specific step fails (that
    step's own WorkflowStepRun has already been set to FAILED by the
    caller before this runs, so the PENDING filter below never touches
    it) and when a workflow-level problem is discovered before any step
    starts (missing workflow definition, invalid definition, no owning
    user) - in that case every step run is still PENDING and all of them
    become SKIPPED."""
    validate_workflow_run_transition(run.status, WorkflowRunStatus.FAILED)
    now = datetime.now(timezone.utc)
    run.status = WorkflowRunStatus.FAILED
    run.error_code = type(exc).__name__
    run.error_message = str(exc)
    run.duration_ms = duration_ms
    run.completed_at = now
    db.add(run)

    await db.execute(
        update(WorkflowStepRun)
        .where(
            WorkflowStepRun.workflow_run_id == run.id,
            WorkflowStepRun.status == WorkflowStepRunStatus.PENDING,
        )
        .values(status=WorkflowStepRunStatus.SKIPPED, completed_at=now)
    )
    await db.commit()


async def execute_workflow_run(
    db: AsyncSession,
    gateway: LLMGateway,
    settings: Settings,
    *,
    run: WorkflowRun,
    workflow_user_input: str,
    context: dict[str, object] | None,
) -> WorkflowRun:
    """Runs `run` (which must currently be QUEUED) through every one of
    its already-persisted WorkflowStepRun rows (created by
    app/workflows/service.py's create_workflow_run), in step_order, each
    one via the P6 runtime. Returns the completed-or-failed run; raises
    the underlying exception on failure the same way
    app/runtime/executor.py's execute_run does, for
    app/api/v1/workflow_runs.py to map to an HTTP status."""
    validate_workflow_run_transition(run.status, WorkflowRunStatus.RUNNING)
    run.status = WorkflowRunStatus.RUNNING
    run.started_at = datetime.now(timezone.utc)
    db.add(run)
    await db.commit()

    started = time.monotonic()

    try:
        workflow = await db.get(WorkflowDefinition, run.workflow_id)
        if workflow is None:
            # Defensive, not expected in practice: there is no
            # WorkflowDefinition delete endpoint in this codebase, and
            # workflow_runs.workflow_id has no ondelete cascade.
            raise WorkflowNotFoundError(run.workflow_id)
        steps = parse_workflow_steps(run.workflow_id, workflow.definition_json)

        owner_id = run.created_by_user_id
        if owner_id is None:
            # Defensive, not expected in practice: create_workflow_run()
            # always sets this from the caller's current_user.id; it can
            # only become None later if that user row is deleted (the FK
            # is ON DELETE SET NULL - see app/models/workflow_run.py).
            raise RuntimeDomainError(
                f"Workflow run {run.id} has no owning user to attribute usage to"
            )
    except (WorkflowDomainError, RuntimeDomainError) as exc:
        await _fail_workflow_run(db, run, exc, duration_ms=int((time.monotonic() - started) * 1000))
        raise

    steps_by_id = {step.step_id: step for step in steps}
    result = await db.execute(
        select(WorkflowStepRun)
        .where(WorkflowStepRun.workflow_run_id == run.id)
        .order_by(WorkflowStepRun.step_order)
    )
    step_runs = list(result.scalars().all())

    step_outputs: dict[str, str] = {}
    previous_step_id: str | None = None
    final_output: str | None = None

    for step_run in step_runs:
        spec = steps_by_id[step_run.step_id]

        validate_step_run_transition(step_run.status, WorkflowStepRunStatus.RUNNING)
        step_run.status = WorkflowStepRunStatus.RUNNING
        step_run.started_at = datetime.now(timezone.utc)
        db.add(step_run)
        await db.commit()

        step_started = time.monotonic()
        agent_run: AgentRun | None = None
        try:
            step_input = build_step_input(
                spec,
                workflow_user_input=workflow_user_input,
                previous_step_id=previous_step_id,
                step_outputs=step_outputs,
            )
            step_run.input_snapshot = step_input
            agent_run = await runtime_service.create_run(
                db, agent_id=spec.agent_definition_id, created_by_user_id=owner_id
            )
            agent_run = await execute_run(
                db, gateway, settings, run=agent_run, user_input=step_input, context=context
            )
        except (WorkflowInputResolutionError, RuntimeDomainError, LLMError) as exc:
            validate_step_run_transition(step_run.status, WorkflowStepRunStatus.FAILED)
            step_run.status = WorkflowStepRunStatus.FAILED
            step_run.agent_run_id = agent_run.id if agent_run is not None else None
            step_run.error_code = type(exc).__name__
            step_run.error_message = str(exc)
            step_run.duration_ms = int((time.monotonic() - step_started) * 1000)
            step_run.completed_at = datetime.now(timezone.utc)
            db.add(step_run)

            await _fail_workflow_run(
                db, run, exc, duration_ms=int((time.monotonic() - started) * 1000)
            )
            raise

        # The except clause above always re-raises, so reaching this
        # point means the try block ran to completion and agent_run was
        # reassigned to execute_run's (non-Optional) return value.
        assert agent_run is not None
        step_run.status = WorkflowStepRunStatus.COMPLETED
        step_run.agent_run_id = agent_run.id
        step_run.output_text = agent_run.output_text
        step_run.duration_ms = int((time.monotonic() - step_started) * 1000)
        step_run.completed_at = datetime.now(timezone.utc)
        db.add(step_run)
        await db.commit()

        output = agent_run.output_text or ""
        step_outputs[step_run.step_id] = output
        final_output = agent_run.output_text
        previous_step_id = step_run.step_id

    validate_workflow_run_transition(run.status, WorkflowRunStatus.COMPLETED)
    run.status = WorkflowRunStatus.COMPLETED
    run.output_text = final_output
    run.duration_ms = int((time.monotonic() - started) * 1000)
    run.completed_at = datetime.now(timezone.utc)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run
