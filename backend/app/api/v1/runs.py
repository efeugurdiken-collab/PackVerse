"""Agent Run API endpoints (Sprint P6: AI Runtime).

POST /runs creates AND executes a run synchronously, in one request -
there is no background job queue anywhere in this codebase yet, so the
full Queued -> Running -> Completed/Failed flow happens before the
response is returned (see README Known Limitations). A run that fails
at the LLM Gateway step (or due to a misconfigured/inactive agent
discovered at execution time) is still persisted - status FAILED,
error_code/error_message set - but the request itself returns the same
HTTP error app/api/v1/llm.py would return for the equivalent
POST /llm/generate failure, per the sprint's "Return existing API error
format". _map_llm_error is imported directly from app.api.v1.llm rather
than reimplemented here, so there is exactly one LLMError -> HTTP status
mapping table in the whole codebase.

Authorization: creating/executing/cancelling a run requires operator or
admin (same bar as POST /llm/generate, since executing an agent costs
real provider tokens); any active role can read, scoped to their own
runs unless admin. Matches app/api/v1/llm.py's matrix exactly.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.api.v1.llm import _map_llm_error
from app.core.config import Settings, get_settings
from app.database.session import get_db
from app.llm.exceptions import LLMError
from app.llm.factory import get_llm_gateway
from app.llm.gateway import LLMGateway
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
from app.runtime.executor import execute_run
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


@router.post("", response_model=AgentRunRead, status_code=status.HTTP_201_CREATED)
async def create_and_execute_run(
    payload: AgentRunCreate,
    db: AsyncSession = Depends(get_db),
    gateway: LLMGateway = Depends(get_llm_gateway),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(_can_execute),
) -> AgentRunRead:
    try:
        run = await runtime_service.create_run(
            db, agent_id=payload.agent_id, created_by_user_id=current_user.id
        )
    except RuntimeDomainError as exc:
        raise _map_runtime_error(exc) from exc

    try:
        run = await execute_run(
            db,
            gateway,
            settings,
            run=run,
            user_input=payload.user_input,
            context=payload.context,
        )
    except LLMError as exc:
        raise _map_llm_error(exc) from exc
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
    try:
        run = await runtime_service.cancel_run(db, run_id, current_user)
    except RuntimeDomainError as exc:
        raise _map_runtime_error(exc) from exc
    return AgentRunRead.model_validate(run)
