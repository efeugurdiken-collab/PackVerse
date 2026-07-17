"""Tests for app/runtime/service.py (Sprint P6): AgentRun creation,
retrieval, listing, and cancellation - independent of app/runtime/
executor.py (no LLM Gateway call happens anywhere in this file).

create_run leaves a run in QUEUED - never RUNNING/terminal - which is
exactly what makes the QUEUED -> CANCELLED path here a genuine,
independently-exercised success case rather than something only
reachable via a race condition against the synchronous POST /runs flow
(see app/runtime/service.py's create_run docstring).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_definition import AgentDefinition
from app.models.agent_run import AgentRun
from app.models.enums import AgentRunStatus, AgentStatus, UserRole
from app.runtime import service as runtime_service
from app.runtime.exceptions import (
    AgentNotActiveError,
    AgentNotFoundError,
    AgentRunNotFoundError,
    InvalidRunTransitionError,
)


async def test_create_run_persists_queued_row(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()

    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )

    assert run.status == AgentRunStatus.QUEUED
    assert run.agent_id == agent.id
    assert run.created_by_user_id == user.id
    assert run.started_at is None
    assert run.completed_at is None


async def test_create_run_with_unknown_agent_raises_not_found(
    db_session: AsyncSession, make_user
) -> None:
    user = await make_user(role=UserRole.OPERATOR)

    with pytest.raises(AgentNotFoundError):
        await runtime_service.create_run(
            db_session, agent_id=uuid.uuid4(), created_by_user_id=user.id
        )


async def test_create_run_with_draft_agent_raises_not_active(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition(status=AgentStatus.DRAFT)

    with pytest.raises(AgentNotActiveError):
        await runtime_service.create_run(
            db_session, agent_id=agent.id, created_by_user_id=user.id
        )


async def test_create_run_with_deprecated_agent_raises_not_active(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition(status=AgentStatus.DEPRECATED)

    with pytest.raises(AgentNotActiveError):
        await runtime_service.create_run(
            db_session, agent_id=agent.id, created_by_user_id=user.id
        )


async def test_owner_can_get_own_run(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )

    fetched = await runtime_service.get_run(db_session, run.id, user)
    assert fetched.id == run.id


async def test_unknown_run_id_raises_not_found(
    db_session: AsyncSession, make_user
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    with pytest.raises(AgentRunNotFoundError):
        await runtime_service.get_run(db_session, uuid.uuid4(), user)


async def test_non_owner_cannot_get_someone_elses_run(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    other = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=owner.id
    )

    with pytest.raises(AgentRunNotFoundError):
        await runtime_service.get_run(db_session, run.id, other)


async def test_admin_can_get_any_run(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    admin = await make_user(role=UserRole.ADMIN)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=owner.id
    )

    fetched = await runtime_service.get_run(db_session, run.id, admin)
    assert fetched.id == run.id


async def test_list_runs_scopes_to_own_runs_for_non_admin(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    other = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    await runtime_service.create_run(db_session, agent_id=agent.id, created_by_user_id=owner.id)
    await runtime_service.create_run(db_session, agent_id=agent.id, created_by_user_id=other.id)

    items, total = await runtime_service.list_runs(db_session, owner)

    assert total == 1
    assert all(r.created_by_user_id == owner.id for r in items)


async def test_list_runs_returns_everything_for_admin(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    other = await make_user(role=UserRole.OPERATOR)
    admin = await make_user(role=UserRole.ADMIN)
    agent = await make_agent_definition()
    await runtime_service.create_run(db_session, agent_id=agent.id, created_by_user_id=owner.id)
    await runtime_service.create_run(db_session, agent_id=agent.id, created_by_user_id=other.id)

    items, total = await runtime_service.list_runs(db_session, admin)

    assert total == 2
    assert len(items) == 2


async def test_list_runs_respects_pagination(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    for _ in range(3):
        await runtime_service.create_run(db_session, agent_id=agent.id, created_by_user_id=user.id)

    items, total = await runtime_service.list_runs(db_session, user, limit=2, offset=0)

    assert total == 3
    assert len(items) == 2


async def test_cancel_queued_run_succeeds(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )

    cancelled = await runtime_service.cancel_run(db_session, run.id, user)

    assert cancelled.status == AgentRunStatus.CANCELLED
    assert cancelled.completed_at is not None
    # Never started, so duration is undefined, not zero.
    assert cancelled.duration_ms is None


async def test_cancel_running_run_succeeds_and_computes_duration(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    """Constructs a RUNNING row directly via the ORM - see the module
    docstring: there is no background queue in this sprint, so a run
    genuinely concurrent with a second request is not otherwise
    reachable, but the state machine itself must still handle it
    correctly (it's a documented, intentionally-supported transition,
    not dead code) - see app/runtime/models.py."""
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = AgentRun(
        id=uuid.uuid4(),
        agent_id=agent.id,
        created_by_user_id=user.id,
        status=AgentRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=2),
    )
    db_session.add(run)
    await db_session.commit()

    cancelled = await runtime_service.cancel_run(db_session, run.id, user)

    assert cancelled.status == AgentRunStatus.CANCELLED
    assert cancelled.duration_ms is not None
    assert cancelled.duration_ms >= 0


@pytest.mark.parametrize(
    "terminal_status", [AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED]
)
async def test_cancel_terminal_run_raises_invalid_transition(
    db_session: AsyncSession,
    make_user,
    make_agent_definition,
    terminal_status: AgentRunStatus,
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = AgentRun(
        id=uuid.uuid4(),
        agent_id=agent.id,
        created_by_user_id=user.id,
        status=terminal_status,
    )
    db_session.add(run)
    await db_session.commit()

    with pytest.raises(InvalidRunTransitionError):
        await runtime_service.cancel_run(db_session, run.id, user)


async def test_non_owner_cannot_cancel_someone_elses_run(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    other = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=owner.id
    )

    with pytest.raises(AgentRunNotFoundError):
        await runtime_service.cancel_run(db_session, run.id, other)


async def test_admin_can_cancel_any_run(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    admin = await make_user(role=UserRole.ADMIN)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=owner.id
    )

    cancelled = await runtime_service.cancel_run(db_session, run.id, admin)
    assert cancelled.status == AgentRunStatus.CANCELLED


async def test_get_active_agent_returns_active_agent(
    db_session: AsyncSession, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    fetched = await runtime_service.get_active_agent(db_session, agent.id)
    assert isinstance(fetched, AgentDefinition)
    assert fetched.id == agent.id


async def test_get_active_agent_raises_not_found_for_unknown_id(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(AgentNotFoundError):
        await runtime_service.get_active_agent(db_session, uuid.uuid4())


async def test_get_active_agent_raises_not_active_for_draft(
    db_session: AsyncSession, make_agent_definition
) -> None:
    agent = await make_agent_definition(status=AgentStatus.DRAFT)
    with pytest.raises(AgentNotActiveError):
        await runtime_service.get_active_agent(db_session, agent.id)
