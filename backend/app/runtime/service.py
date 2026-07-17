"""Runtime service layer (Sprint P6): AgentRun creation, retrieval,
listing, and cancellation.

Deliberately module-level functions, not a class - the sprint spec
suggests a "RuntimeService" but every other service in this codebase
(app/services/llm_service.py, app/services/asset_service.py,
app/services/product_service.py) already follows this exact shape, and
"Follow current project style exactly" / "Use existing project
architecture" take precedence over the spec's own "suggested"/"possible"
module wording. See the Sprint P6 report's "Important architectural
decisions".

app/runtime/executor.py (the actual LLM-invoking execution logic) is a
separate module that imports get_active_agent from here - keeping
"create/read/cancel" (no LLM Gateway dependency) apart from "execute"
(does depend on the gateway) makes both halves independently testable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_definition import AgentDefinition
from app.models.agent_run import AgentRun
from app.models.enums import AgentRunStatus, AgentStatus, UserRole
from app.models.user import User
from app.runtime.exceptions import AgentNotActiveError, AgentNotFoundError, AgentRunNotFoundError
from app.runtime.models import validate_transition

MAX_PAGE_SIZE = 100


async def get_active_agent(db: AsyncSession, agent_id: uuid.UUID) -> AgentDefinition:
    """Raises AgentNotFoundError for a missing id, AgentNotActiveError
    for one that exists but is DRAFT/DEPRECATED - only ACTIVE agents may
    be executed. Called both by create_run (creation-time check, before
    any row exists) and by executor.execute_run (re-checked at execution
    time in case the agent's status changed in between - see the
    sprint's "Handle: missing agent" under general Error Handling, not
    just request validation)."""
    agent = await db.get(AgentDefinition, agent_id)
    if agent is None:
        raise AgentNotFoundError(agent_id)
    if agent.status != AgentStatus.ACTIVE:
        raise AgentNotActiveError(agent_id)
    return agent


async def create_run(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
    commit: bool = True,
) -> AgentRun:
    """Validates the agent exists and is active, then persists a QUEUED
    run row. Does not execute it - see app/runtime/executor.py's
    execute_run.

    `commit` defaults to True, preserving this function's original P6
    behavior for every existing caller/test. Sprint P8's
    app/jobs/service.py's enqueue_agent_run passes commit=False so it can
    add a Job row and commit both in one transaction - the "avoid unsafe
    dual writes" enqueue-safety requirement - without this function
    needing to know anything about jobs. When commit=False, the row is
    only flushed (not committed): it has a real id and is queryable
    within the same transaction, but server-generated defaults like
    created_at may not be populated on the Python object until the
    caller's own commit+refresh."""
    await get_active_agent(db, agent_id)
    run = AgentRun(
        id=uuid.uuid4(),
        agent_id=agent_id,
        created_by_user_id=created_by_user_id,
        status=AgentRunStatus.QUEUED,
    )
    db.add(run)
    if commit:
        await db.commit()
        await db.refresh(run)
    else:
        await db.flush()
    return run


async def get_run(db: AsyncSession, run_id: uuid.UUID, current_user: User) -> AgentRun:
    """Admins may fetch any run; everyone else only their own - both a
    genuinely missing id and someone else's id raise the same
    AgentRunNotFoundError, so the endpoint can't be used to probe for
    other users' run ids (same pattern as
    app/services/llm_service.py's get_request)."""
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise AgentRunNotFoundError(run_id)
    if current_user.role != UserRole.ADMIN and run.created_by_user_id != current_user.id:
        raise AgentRunNotFoundError(run_id)
    return run


async def list_runs(
    db: AsyncSession, current_user: User, *, limit: int = 20, offset: int = 0
) -> tuple[list[AgentRun], int]:
    """Admins see every run; everyone else sees only their own."""
    limit = min(max(limit, 1), MAX_PAGE_SIZE)
    offset = max(offset, 0)

    base_query = select(AgentRun)
    if current_user.role != UserRole.ADMIN:
        base_query = base_query.where(AgentRun.created_by_user_id == current_user.id)

    total = await db.scalar(select(func.count()).select_from(base_query.subquery()))
    result = await db.execute(
        base_query.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset)
    )
    items = list(result.scalars().all())
    return items, int(total or 0)


async def cancel_run(db: AsyncSession, run_id: uuid.UUID, current_user: User) -> AgentRun:
    """Valid from QUEUED or RUNNING only (per app/runtime/models.py's
    transition table) - cancelling an already-terminal run raises
    InvalidRunTransitionError instead of silently succeeding."""
    run = await get_run(db, run_id, current_user)
    validate_transition(run.status, AgentRunStatus.CANCELLED)

    run.status = AgentRunStatus.CANCELLED
    run.completed_at = datetime.now(timezone.utc)
    if run.started_at is not None:
        run.duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run
