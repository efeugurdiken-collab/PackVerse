"""Tests for app/workflows/service.py (Sprint P7): WorkflowRun creation,
retrieval, listing, step-run listing, and cancellation - independent of
app/workflows/executor.py (no P6 runtime call happens anywhere in this
file; create_workflow_run leaves every step in PENDING).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    AgentStatus,
    UserRole,
    WorkflowRunStatus,
    WorkflowStatus,
    WorkflowStepRunStatus,
)
from app.models.workflow_run import WorkflowRun
from app.models.workflow_step_run import WorkflowStepRun
from app.runtime.exceptions import AgentNotActiveError, AgentNotFoundError
from app.workflows import service as workflow_service
from app.workflows.exceptions import (
    InvalidWorkflowRunTransitionError,
    WorkflowDefinitionInvalidError,
    WorkflowNotActiveError,
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
)


def _one_step_definition(agent_id: uuid.UUID) -> list[dict[str, object]]:
    return [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent_id), "order": 1}]


def _two_step_definition(agent1_id: uuid.UUID, agent2_id: uuid.UUID) -> list[dict[str, object]]:
    return [
        {"step_id": "first", "name": "First", "agent_definition_id": str(agent1_id), "order": 1},
        {"step_id": "second", "name": "Second", "agent_definition_id": str(agent2_id), "order": 2},
    ]


# --- create_workflow_run -----------------------------------------------


async def test_create_workflow_run_persists_queued_row_and_pending_steps(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent1 = await make_agent_definition()
    agent2 = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_two_step_definition(agent1.id, agent2.id))

    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )

    assert run.status == WorkflowRunStatus.QUEUED
    assert run.workflow_id == workflow.id
    assert run.created_by_user_id == user.id
    assert run.started_at is None

    steps = await workflow_service.get_steps(db_session, run.id, user)
    assert [s.step_id for s in steps] == ["first", "second"]
    assert all(s.status == WorkflowStepRunStatus.PENDING for s in steps)
    assert [s.agent_id for s in steps] == [agent1.id, agent2.id]


async def test_create_workflow_run_with_unknown_workflow_raises_not_found(
    db_session: AsyncSession, make_user
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    with pytest.raises(WorkflowNotFoundError):
        await workflow_service.create_workflow_run(
            db_session, workflow_id=uuid.uuid4(), created_by_user_id=user.id
        )


async def test_create_workflow_run_with_draft_workflow_raises_not_active(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(
        status=WorkflowStatus.DRAFT, steps=_one_step_definition(agent.id)
    )

    with pytest.raises(WorkflowNotActiveError):
        await workflow_service.create_workflow_run(
            db_session, workflow_id=workflow.id, created_by_user_id=user.id
        )


async def test_create_workflow_run_with_empty_steps_raises_invalid_definition(
    db_session: AsyncSession, make_user, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    workflow = await make_workflow_definition(steps=[])

    with pytest.raises(WorkflowDefinitionInvalidError):
        await workflow_service.create_workflow_run(
            db_session, workflow_id=workflow.id, created_by_user_id=user.id
        )


async def test_create_workflow_run_with_unknown_agent_raises_agent_not_found(
    db_session: AsyncSession, make_user, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    workflow = await make_workflow_definition(steps=_one_step_definition(uuid.uuid4()))

    with pytest.raises(AgentNotFoundError):
        await workflow_service.create_workflow_run(
            db_session, workflow_id=workflow.id, created_by_user_id=user.id
        )


async def test_create_workflow_run_with_inactive_agent_raises_agent_not_active(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition(status=AgentStatus.DRAFT)
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))

    with pytest.raises(AgentNotActiveError):
        await workflow_service.create_workflow_run(
            db_session, workflow_id=workflow.id, created_by_user_id=user.id
        )


# --- get_run / list_runs -------------------------------------------------


async def test_owner_can_get_own_run(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )

    fetched = await workflow_service.get_run(db_session, run.id, user)
    assert fetched.id == run.id


async def test_unknown_run_id_raises_not_found(db_session: AsyncSession, make_user) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    with pytest.raises(WorkflowRunNotFoundError):
        await workflow_service.get_run(db_session, uuid.uuid4(), user)


async def test_non_owner_cannot_get_someone_elses_run(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    other = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=owner.id
    )

    with pytest.raises(WorkflowRunNotFoundError):
        await workflow_service.get_run(db_session, run.id, other)


async def test_admin_can_get_any_run(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    admin = await make_user(role=UserRole.ADMIN)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=owner.id
    )

    fetched = await workflow_service.get_run(db_session, run.id, admin)
    assert fetched.id == run.id


async def test_list_runs_scopes_to_own_runs_for_non_admin(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    other = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=owner.id
    )
    await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=other.id
    )

    items, total = await workflow_service.list_runs(db_session, owner)

    assert total == 1
    assert all(r.created_by_user_id == owner.id for r in items)


async def test_list_runs_returns_everything_for_admin(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    other = await make_user(role=UserRole.OPERATOR)
    admin = await make_user(role=UserRole.ADMIN)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=owner.id
    )
    await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=other.id
    )

    items, total = await workflow_service.list_runs(db_session, admin)

    assert total == 2
    assert len(items) == 2


async def test_list_runs_respects_pagination(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    for _ in range(3):
        await workflow_service.create_workflow_run(
            db_session, workflow_id=workflow.id, created_by_user_id=user.id
        )

    items, total = await workflow_service.list_runs(db_session, user, limit=2, offset=0)

    assert total == 3
    assert len(items) == 2


# --- get_steps -------------------------------------------------------------


async def test_get_steps_returns_ordered_step_runs(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent1 = await make_agent_definition()
    agent2 = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_two_step_definition(agent1.id, agent2.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )

    steps = await workflow_service.get_steps(db_session, run.id, user)

    assert [s.step_order for s in steps] == [1, 2]


async def test_get_steps_for_unknown_run_raises_not_found(
    db_session: AsyncSession, make_user
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    with pytest.raises(WorkflowRunNotFoundError):
        await workflow_service.get_steps(db_session, uuid.uuid4(), user)


async def test_get_steps_for_someone_elses_run_raises_not_found(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    other = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=owner.id
    )

    with pytest.raises(WorkflowRunNotFoundError):
        await workflow_service.get_steps(db_session, run.id, other)


# --- cancel_run --------------------------------------------------------


async def test_cancel_queued_run_cancels_run_and_pending_steps(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent1 = await make_agent_definition()
    agent2 = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_two_step_definition(agent1.id, agent2.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )

    cancelled = await workflow_service.cancel_run(db_session, run.id, user)

    assert cancelled.status == WorkflowRunStatus.CANCELLED
    assert cancelled.completed_at is not None
    assert cancelled.duration_ms is None  # never started

    steps = await workflow_service.get_steps(db_session, run.id, user)
    assert all(s.status == WorkflowStepRunStatus.CANCELLED for s in steps)


async def test_cancel_already_cancelled_run_is_idempotent(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )
    first = await workflow_service.cancel_run(db_session, run.id, user)

    second = await workflow_service.cancel_run(db_session, run.id, user)

    assert second.status == WorkflowRunStatus.CANCELLED
    assert second.id == first.id


@pytest.mark.parametrize(
    "terminal_status", [WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED]
)
async def test_cancel_completed_or_failed_run_raises_invalid_transition(
    db_session: AsyncSession,
    make_user,
    make_agent_definition,
    make_workflow_definition,
    terminal_status: WorkflowRunStatus,
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        created_by_user_id=user.id,
        status=terminal_status,
    )
    db_session.add(run)
    await db_session.commit()

    with pytest.raises(InvalidWorkflowRunTransitionError):
        await workflow_service.cancel_run(db_session, run.id, user)


async def test_cancel_running_run_only_cancels_pending_steps(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    """Constructs a RUNNING run with a mix of already-COMPLETED and still-
    PENDING step runs directly via the ORM - see app/workflows/service.py's
    cancel_run docstring: there is no background queue in this sprint, so
    this exact state is not reachable through a real concurrent request,
    but the state machine itself must still handle it correctly."""
    user = await make_user(role=UserRole.OPERATOR)
    agent1 = await make_agent_definition()
    agent2 = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_two_step_definition(agent1.id, agent2.id))
    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        created_by_user_id=user.id,
        status=WorkflowRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=2),
    )
    db_session.add(run)
    await db_session.flush()
    completed_step = WorkflowStepRun(
        id=uuid.uuid4(),
        workflow_run_id=run.id,
        step_id="first",
        step_order=1,
        agent_id=agent1.id,
        status=WorkflowStepRunStatus.COMPLETED,
        output_text="done",
    )
    pending_step = WorkflowStepRun(
        id=uuid.uuid4(),
        workflow_run_id=run.id,
        step_id="second",
        step_order=2,
        agent_id=agent2.id,
        status=WorkflowStepRunStatus.PENDING,
    )
    db_session.add_all([completed_step, pending_step])
    await db_session.commit()

    cancelled = await workflow_service.cancel_run(db_session, run.id, user)

    assert cancelled.status == WorkflowRunStatus.CANCELLED
    assert cancelled.duration_ms is not None
    assert cancelled.duration_ms >= 0

    steps = await workflow_service.get_steps(db_session, run.id, user)
    by_id = {s.step_id: s for s in steps}
    assert by_id["first"].status == WorkflowStepRunStatus.COMPLETED
    assert by_id["second"].status == WorkflowStepRunStatus.CANCELLED


async def test_non_owner_cannot_cancel_someone_elses_run(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    other = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=owner.id
    )

    with pytest.raises(WorkflowRunNotFoundError):
        await workflow_service.cancel_run(db_session, run.id, other)


async def test_admin_can_cancel_any_run(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    owner = await make_user(role=UserRole.OPERATOR)
    admin = await make_user(role=UserRole.ADMIN)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=owner.id
    )

    cancelled = await workflow_service.cancel_run(db_session, run.id, admin)
    assert cancelled.status == WorkflowRunStatus.CANCELLED


# --- get_active_workflow -----------------------------------------------


async def test_get_active_workflow_returns_active_workflow(
    db_session: AsyncSession, make_agent_definition, make_workflow_definition
) -> None:
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    fetched = await workflow_service.get_active_workflow(db_session, workflow.id)
    assert fetched.id == workflow.id


async def test_get_active_workflow_raises_not_found_for_unknown_id(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(WorkflowNotFoundError):
        await workflow_service.get_active_workflow(db_session, uuid.uuid4())


async def test_get_active_workflow_raises_not_active_for_draft(
    db_session: AsyncSession, make_agent_definition, make_workflow_definition
) -> None:
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(
        status=WorkflowStatus.DRAFT, steps=_one_step_definition(agent.id)
    )
    with pytest.raises(WorkflowNotActiveError):
        await workflow_service.get_active_workflow(db_session, workflow.id)
