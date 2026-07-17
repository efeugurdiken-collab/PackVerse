"""Workflow service layer (Sprint P7): WorkflowRun creation, retrieval,
listing, step-run listing, and cancellation.

Module-level functions, not a class - same "Follow current project style
exactly" rationale as app/runtime/service.py's own docstring (which this
module otherwise mirrors closely: get_active_workflow parallels
get_active_agent, get_run/list_runs/cancel_run parallel their runtime
counterparts almost line for line).

app/workflows/executor.py (the actual step-by-step execution logic) is a
separate module, same "create/read/cancel" vs "execute" split P6 already
established between app/runtime/service.py and app/runtime/executor.py.

Deliberately does NOT wrap app.runtime.exceptions.AgentNotFoundError /
AgentNotActiveError raised while validating a workflow definition's
referenced agents at run-creation time (see create_workflow_run below).
They propagate as-is: app/api/v1/workflow_runs.py reuses
app/api/v1/runs.py's existing _map_runtime_error for them directly,
the same "reuse, don't duplicate a mapping table" pattern P6 already
applied to app/llm's _map_llm_error.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole, WorkflowRunStatus, WorkflowStatus, WorkflowStepRunStatus
from app.models.user import User
from app.models.workflow_definition import WorkflowDefinition
from app.models.workflow_run import WorkflowRun
from app.models.workflow_step_run import WorkflowStepRun
from app.runtime.service import get_active_agent
from app.workflows.definition import parse_workflow_steps
from app.workflows.exceptions import (
    WorkflowNotActiveError,
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
)
from app.workflows.models import validate_workflow_run_transition

MAX_PAGE_SIZE = 100


async def get_active_workflow(db: AsyncSession, workflow_id: uuid.UUID) -> WorkflowDefinition:
    """Raises WorkflowNotFoundError for a missing id, WorkflowNotActiveError
    for one that exists but is DRAFT/DEPRECATED - only ACTIVE workflows may
    be run, mirroring app/runtime/service.py's get_active_agent exactly."""
    workflow = await db.get(WorkflowDefinition, workflow_id)
    if workflow is None:
        raise WorkflowNotFoundError(workflow_id)
    if workflow.status != WorkflowStatus.ACTIVE:
        raise WorkflowNotActiveError(workflow_id)
    return workflow


async def create_workflow_run(
    db: AsyncSession, *, workflow_id: uuid.UUID, created_by_user_id: uuid.UUID
) -> WorkflowRun:
    """Validates the workflow exists, is active, and has a structurally
    valid definition_json (parse_workflow_steps - raises
    WorkflowDefinitionInvalidError), then validates every referenced
    agent exists and is active (get_active_agent - raises
    AgentNotFoundError/AgentNotActiveError, left unwrapped, see module
    docstring). Only after all of that does it persist: one QUEUED
    WorkflowRun plus one PENDING WorkflowStepRun per step, in a single
    transaction, so a run is never observable half-created.

    Does not execute the run - see app/workflows/executor.py's
    execute_workflow_run. app/api/v1/workflow_runs.py's POST
    /workflow-runs calls both back to back in the same request, same "no
    background job queue in this sprint" reasoning as P6's POST /runs.
    """
    workflow = await get_active_workflow(db, workflow_id)
    steps = parse_workflow_steps(workflow_id, workflow.definition_json)
    for step in steps:
        await get_active_agent(db, step.agent_definition_id)

    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        created_by_user_id=created_by_user_id,
        status=WorkflowRunStatus.QUEUED,
    )
    db.add(run)
    await db.flush()

    for step in steps:
        db.add(
            WorkflowStepRun(
                id=uuid.uuid4(),
                workflow_run_id=run.id,
                step_id=step.step_id,
                step_order=step.order,
                agent_id=step.agent_definition_id,
                status=WorkflowStepRunStatus.PENDING,
            )
        )

    await db.commit()
    await db.refresh(run)
    return run


async def get_run(db: AsyncSession, run_id: uuid.UUID, current_user: User) -> WorkflowRun:
    """Admins may fetch any run; everyone else only their own - both a
    genuinely missing id and someone else's id raise the same
    WorkflowRunNotFoundError, mirroring app/runtime/service.py's
    get_run exactly, for the same "can't be used to probe other users'
    run ids" reasoning."""
    run = await db.get(WorkflowRun, run_id)
    if run is None:
        raise WorkflowRunNotFoundError(run_id)
    if current_user.role != UserRole.ADMIN and run.created_by_user_id != current_user.id:
        raise WorkflowRunNotFoundError(run_id)
    return run


async def list_runs(
    db: AsyncSession, current_user: User, *, limit: int = 20, offset: int = 0
) -> tuple[list[WorkflowRun], int]:
    """Admins see every run; everyone else sees only their own."""
    limit = min(max(limit, 1), MAX_PAGE_SIZE)
    offset = max(offset, 0)

    base_query = select(WorkflowRun)
    if current_user.role != UserRole.ADMIN:
        base_query = base_query.where(WorkflowRun.created_by_user_id == current_user.id)

    total = await db.scalar(select(func.count()).select_from(base_query.subquery()))
    result = await db.execute(
        base_query.order_by(WorkflowRun.created_at.desc()).limit(limit).offset(offset)
    )
    items = list(result.scalars().all())
    return items, int(total or 0)


async def get_steps(
    db: AsyncSession, run_id: uuid.UUID, current_user: User
) -> list[WorkflowStepRun]:
    """Validates access the same way get_run does, then returns the run's
    step runs ordered by step_order. Queried explicitly (rather than via
    WorkflowRun.step_runs) so this stays correct even where the run
    object came from a different session/query path in the future."""
    await get_run(db, run_id, current_user)
    result = await db.execute(
        select(WorkflowStepRun)
        .where(WorkflowStepRun.workflow_run_id == run_id)
        .order_by(WorkflowStepRun.step_order)
    )
    return list(result.scalars().all())


async def cancel_run(db: AsyncSession, run_id: uuid.UUID, current_user: User) -> WorkflowRun:
    """Valid from QUEUED or RUNNING (per app/workflows/models.py's
    transition table) - cancelling an already-CANCELLED run succeeds as
    an idempotent no-op (handled here, before the transition table is
    even consulted - see app/workflows/models.py's docstring for why a
    CANCELLED -> CANCELLED self-loop does not belong in that table).
    Cancelling a COMPLETED/FAILED run still raises
    InvalidWorkflowRunTransitionError.

    Synchronous-execution limitation (Sprint P7 section 10): because
    this sprint has no background queue, a workflow run is normally
    QUEUED only for the instant between create_workflow_run and
    execute_workflow_run within a single request, and RUNNING only while
    that same request's executor loop is on the call stack - so in
    practice this endpoint cancels a run that has already reached a
    terminal status by the time a second request could observe it, or a
    row left RUNNING/QUEUED by a crashed process. Both are still handled
    correctly (PENDING step runs are cancelled, illegal transitions
    still raise), but true "cancel a workflow that is actively executing
    right now" concurrency does not exist in this architecture, mirroring
    app/runtime/service.py's cancel_run's identical limitation for
    AgentRun.
    """
    run = await get_run(db, run_id, current_user)
    if run.status == WorkflowRunStatus.CANCELLED:
        return run

    validate_workflow_run_transition(run.status, WorkflowRunStatus.CANCELLED)

    now = datetime.now(timezone.utc)
    run.status = WorkflowRunStatus.CANCELLED
    run.completed_at = now
    if run.started_at is not None:
        run.duration_ms = int((now - run.started_at).total_seconds() * 1000)
    db.add(run)

    await db.execute(
        update(WorkflowStepRun)
        .where(
            WorkflowStepRun.workflow_run_id == run.id,
            WorkflowStepRun.status == WorkflowStepRunStatus.PENDING,
        )
        .values(status=WorkflowStepRunStatus.CANCELLED, completed_at=now)
    )

    await db.commit()
    await db.refresh(run)
    return run
