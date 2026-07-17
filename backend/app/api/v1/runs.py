"""Agent Run API endpoints (Sprint P6: AI Runtime; Sprint P8: async
execution).

POST /runs validates and enqueues a run, then returns 202 Accepted with
the run in its QUEUED state - it no longer executes anything itself. A
separate worker process (app/worker/) claims the paired Job from the
durable queue (app/jobs/) and calls the exact same
app.runtime.executor.execute_run this endpoint used to call directly;
see app/jobs/service.py's enqueue_agent_run for how the run row and its
Job are persisted atomically. get/list/cancel below keep their original
response shapes and status codes - only POST's status code and the
run's returned status changed (previously always terminal by the time
the response was sent; now always QUEUED). _map_llm_error is still
imported here only because app/api/v1/workflow_runs.py's own error
mapping re-exports it via this module's _map_runtime_error pattern; this
file itself no longer raises LLMError-derived HTTP errors (POST returns
before any LLM call happens).

Authorization: creating/cancelling a run requires operator or admin
(same bar as POST /llm/generate, since executing an agent costs real
provider tokens once the worker picks it up); any active role can read,
scoped to their own runs unless admin. Matches app/api/v1/llm.py's
matrix exactly.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.config import Settings, get_settings
from app.database.session import get_db
from app.jobs import service as job_service
from app.jobs.exceptions import JobAlreadyRunningError
from app.models.enums import UserRole
from app.models.user import User
from app.runtime import service as runtime_service
from app.runtime.exceptions import (
    AgentNotActiveError,
    AgentNotFoundError,
    AgentRunNotFoundError,
    InvalidRunTransitionError,
    RuntimeDomainError,
)
from app.schemas.common import Page
from app.schemas.runtime import AgentRunCreate, AgentRunRead

router = APIRouter(prefix="/runs", tags=["runs"])

_can_execute = require_roles(UserRole.OPERATOR, UserRole.ADMIN)
_can_read = require_roles(UserRole.VIEWER, UserRole.OPERATOR, UserRole.ADMIN)


def _map_runtime_error(exc: RuntimeDomainError) -> HTTPException:
    """The one place an app.runtime.exceptions.RuntimeDomainError becomes
    an HTTP status code."""
    if isinstance(exc, (AgentNotFoundError, AgentRunNotFoundError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, (AgentNotActiveError, InvalidRunTransitionError)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    # AgentConfigurationError - a data problem with the agent definition
    # discovered while building the prompt, not a request-shape problem.
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


@router.post("", response_model=AgentRunRead, status_code=status.HTTP_202_ACCEPTED)
async def create_and_execute_run(
    payload: AgentRunCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(_can_execute),
) -> AgentRunRead:
    try:
        run, _job = await job_service.enqueue_agent_run(
            db,
            agent_id=payload.agent_id,
            created_by_user_id=current_user.id,
            user_input=payload.user_input,
            context=payload.context,
            max_attempts=settings.job_max_attempts,
        )
    except RuntimeDomainError as exc:
        raise _map_runtime_error(exc) from exc

    return AgentRunRead.model_validate(run)


@router.get("/{run_id}", response_model=AgentRunRead)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_can_read),
) -> AgentRunRead:
    try:
        run = await runtime_service.get_run(db, run_id, current_user)
    except AgentRunNotFoundError as exc:
        raise _map_runtime_error(exc) from exc
    return AgentRunRead.model_validate(run)


@router.get("", response_model=Page[AgentRunRead])
async def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_can_read),
) -> Page[AgentRunRead]:
    items, total = await runtime_service.list_runs(
        db, current_user, limit=limit, offset=offset
    )
    return Page[AgentRunRead](
        items=[AgentRunRead.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/{run_id}/cancel", response_model=AgentRunRead)
async def cancel_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_can_execute),
) -> AgentRunRead:
    """Sprint P8: delegates to app.jobs.service.cancel_agent_run, which
    cancels a still-QUEUED/RETRYING job+run pair, or raises
    JobAlreadyRunningError (409) if a worker has already claimed the job
    - an in-flight agent-run job cannot be interrupted mid-request. See
    that module's docstring for the full three-tier cancellation
    design."""
    try:
        run = await job_service.cancel_agent_run(db, run_id, current_user)
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeDomainError as exc:
        raise _map_runtime_error(exc) from exc
    return AgentRunRead.model_validate(run)
