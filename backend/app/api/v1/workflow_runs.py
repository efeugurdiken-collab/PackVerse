"""Workflow Run API endpoints (Sprint P7: Workflow Orchestration).

POST /workflow-runs creates AND executes a run synchronously, in one
request - same "no background job queue anywhere in this codebase yet"
reasoning as app/api/v1/runs.py's POST /runs (see that module's
docstring and this sprint's Known Limitations). Every step's own P6
AgentRun/llm_requests rows are still fully persisted even when the
overall request returns an error.

Reuses BOTH app/api/v1/llm.py's _map_llm_error and
app/api/v1/runs.py's _map_runtime_error directly for the LLMError/
RuntimeDomainError that can surface from a step's execution (through
app.workflows.executor calling straight into the P6 runtime) - exactly
the "reuse, don't duplicate a mapping table" pattern P6 already applied
to _map_llm_error. Only a genuinely new mapping - app.workflows.
exceptions.WorkflowDomainError -> HTTP status - is added here.

Authorization: creating/cancelling a run requires operator or admin
(executing a workflow costs real provider tokens, once per step); any
active role can read, scoped to their own runs unless admin. Identical
matrix to app/api/v1/runs.py.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.api.v1.llm import _map_llm_error
from app.api.v1.runs import _map_runtime_error
from app.core.config import Settings, get_settings
from app.database.session import get_db
from app.llm.exceptions import LLMError
from app.llm.factory import get_llm_gateway
from app.llm.gateway import LLMGateway
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
from app.workflows.executor import execute_workflow_run

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


@router.post("", response_model=WorkflowRunRead, status_code=status.HTTP_201_CREATED)
async def create_and_execute_workflow_run(
    payload: WorkflowRunCreate,
    db: AsyncSession = Depends(get_db),
    gateway: LLMGateway = Depends(get_llm_gateway),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(_can_execute),
) -> WorkflowRunRead:
    try:
        run = await workflow_service.create_workflow_run(
            db, workflow_id=payload.workflow_id, created_by_user_id=current_user.id
        )
    except WorkflowDomainError as exc:
        raise _map_workflow_error(exc) from exc
    except RuntimeDomainError as exc:
        raise _map_runtime_error(exc) from exc

    try:
        run = await execute_workflow_run(
            db,
            gateway,
            settings,
            run=run,
            workflow_user_input=payload.user_input,
            context=payload.context,
        )
    except LLMError as exc:
        raise _map_llm_error(exc) from exc
    except RuntimeDomainError as exc:
        raise _map_runtime_error(exc) from exc
    except WorkflowDomainError as exc:
        raise _map_workflow_error(exc) from exc

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
    try:
        run = await workflow_service.cancel_run(db, run_id, current_user)
    except WorkflowDomainError as exc:
        raise _map_workflow_error(exc) from exc
    return WorkflowRunRead.model_validate(run)
