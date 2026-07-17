"""Workflow Run API endpoints (Sprint P7: Workflow Orchestration; Sprint
P8: async execution).

POST /workflow-runs validates and enqueues a run, then returns 202
Accepted with the run (and its already-persisted, still-PENDING
WorkflowStepRun rows) in its QUEUED state - it no longer executes
anything itself. A separate worker process (app/worker/) claims the
paired Job from the durable queue (app/jobs/) and calls the exact same
app.workflows.executor.execute_workflow_run this endpoint used to call
directly; see app/jobs/service.py's enqueue_workflow_run for how the run
row (plus its step rows) and its Job are persisted atomically.
get/list/steps below keep their original response shapes and status
codes - only POST's status code and the run's returned status changed.

Reuses app/api/v1/runs.py's _map_runtime_error directly for the
RuntimeDomainError that create_workflow_run's own agent-existence checks
can still raise synchronously at enqueue time - exactly the "reuse,
don't duplicate a mapping table" pattern P6 already applied to
_map_llm_error. Only a genuinely new mapping - app.workflows.
exceptions.WorkflowDomainError -> HTTP status - is added here.
_map_llm_error is no longer used by this module (no LLM call happens
before the response is returned) and has been removed from the imports.

Authorization: creating/cancelling a run requires operator or admin
(executing a workflow costs real provider tokens, once per step, once
the worker picks it up); any active role can read, scoped to their own
runs unless admin. Identical matrix to app/api/v1/runs.py.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.api.v1.runs import _map_runtime_error
from app.core.config import Settings, get_settings
from app.database.session import get_db
from app.jobs import service as job_service
from app.models.enums import UserRole
from app.models.user import User
from app.runtime.exceptions import RuntimeDomainError
from app.schemas.common import Page
from app.schemas.workflow_run import WorkflowRunCreate, WorkflowRunRead, WorkflowStepRunRead
from app.workflows import service as workflow_service
from app.workflows.exceptions import (
    InvalidStepRunTransitionError,
    InvalidWorkflowRunTransitionError,
    WorkflowDomainError,
    WorkflowNotActiveError,
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
)

router = APIRouter(prefix="/workflow-runs", tags=["workflow-runs"])

_can_execute = require_roles(UserRole.OPERATOR, UserRole.ADMIN)
_can_read = require_roles(UserRole.VIEWER, UserRole.OPERATOR, UserRole.ADMIN)


def _map_workflow_error(exc: WorkflowDomainError) -> HTTPException:
    """The one place an app.workflows.exceptions.WorkflowDomainError
    becomes an HTTP status code."""
    if isinstance(exc, (WorkflowNotFoundError, WorkflowRunNotFoundError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(
        exc,
        (WorkflowNotActiveError, InvalidWorkflowRunTransitionError, InvalidStepRunTransitionError),
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    # WorkflowDefinitionInvalidError / WorkflowInputResolutionError - a
    # data problem with the workflow definition or its runtime input
    # references, not a request-shape problem.
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


@router.post("", response_model=WorkflowRunRead, status_code=status.HTTP_202_ACCEPTED)
async def create_and_execute_workflow_run(
    payload: WorkflowRunCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(_can_execute),
) -> WorkflowRunRead:
    try:
        run, _job = await job_service.enqueue_workflow_run(
            db,
            workflow_id=payload.workflow_id,
            created_by_user_id=current_user.id,
            user_input=payload.user_input,
            context=payload.context,
            max_attempts=settings.job_max_attempts,
        )
    except WorkflowDomainError as exc:
        raise _map_workflow_error(exc) from exc
    except RuntimeDomainError as exc:
        raise _map_runtime_error(exc) from exc

    return WorkflowRunRead.model_validate(run)


@router.get("/{run_id}", response_model=WorkflowRunRead)
async def get_workflow_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_can_read),
) -> WorkflowRunRead:
    try:
        run = await workflow_service.get_run(db, run_id, current_user)
    except WorkflowRunNotFoundError as exc:
        raise _map_workflow_error(exc) from exc
    return WorkflowRunRead.model_validate(run)


@router.get("", response_model=Page[WorkflowRunRead])
async def list_workflow_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_can_read),
) -> Page[WorkflowRunRead]:
    items, total = await workflow_service.list_runs(db, current_user, limit=limit, offset=offset)
    return Page[WorkflowRunRead](
        items=[WorkflowRunRead.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}/steps", response_model=list[WorkflowStepRunRead])
async def list_workflow_run_steps(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_can_read),
) -> list[WorkflowStepRunRead]:
    try:
        steps = await workflow_service.get_steps(db, run_id, current_user)
    except WorkflowRunNotFoundError as exc:
        raise _map_workflow_error(exc) from exc
    return [WorkflowStepRunRead.model_validate(s) for s in steps]


@router.post("/{run_id}/cancel", response_model=WorkflowRunRead)
async def cancel_workflow_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_can_execute),
) -> WorkflowRunRead:
    """Sprint P8: delegates to app.jobs.service.cancel_workflow_run. A
    still-QUEUED/RETRYING job+run pair is cancelled immediately; a
    RUNNING job instead has Job.cancel_requested_at set and the run is
    returned unchanged (still RUNNING) - the worker's
    cancellation_check callback (app/workflows/executor.py) notices this
    between steps and stops the run there. See that module's docstring
    for the full three-tier cancellation design and its documented
    in-flight-provider-call limitation."""
    try:
        run = await job_service.cancel_workflow_run(db, run_id, current_user)
    except WorkflowDomainError as exc:
        raise _map_workflow_error(exc) from exc
    return WorkflowRunRead.model_validate(run)
