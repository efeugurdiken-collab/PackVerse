"""Tests for the job-queue service layer (Sprint P8): app/jobs/service.py
- enqueueing (including the atomic single-commit enqueue-safety property)
and the three-tier cancellation design.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.jobs import queue, service as job_service
from app.jobs.exceptions import JobAlreadyRunningError
from app.models.enums import AgentRunStatus, JobStatus, UserRole, WorkflowRunStatus
from app.models.job import Job
from app.runtime.exceptions import AgentRunNotFoundError, RuntimeDomainError
from app.workflows.exceptions import WorkflowDomainError, WorkflowRunNotFoundError


@pytest.fixture
async def owner(make_user):
    return await make_user(role=UserRole.OPERATOR)


@pytest.fixture
async def other_user(make_user):
    return await make_user(role=UserRole.OPERATOR)


# --- enqueue_agent_run ------------------------------------------------


async def test_enqueue_agent_run_creates_queued_run_and_queued_job(
    db_session, owner, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    run, job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hello",
        context=None,
        max_attempts=3,
    )
    assert run.status == AgentRunStatus.QUEUED
    assert job.status == JobStatus.QUEUED
    assert job.job_type == job_service.AGENT_RUN_JOB_TYPE
    assert job.target_run_id == run.id
    assert job.input_json == {"user_input": "hello", "context": None}
    assert job.max_attempts == 3


async def test_enqueue_agent_run_persists_both_rows_in_one_commit(
    db_session, owner, make_agent_definition
) -> None:
    """Both the run and its paired job must exist after enqueueing - the
    "no naive dual write" property means there is no window where one
    exists without the other."""
    agent = await make_agent_definition()
    run, job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hello",
        context=None,
        max_attempts=3,
    )
    result = await db_session.execute(select(Job).where(Job.target_run_id == run.id))
    persisted_job = result.scalar_one()
    assert persisted_job.id == job.id


async def test_enqueue_agent_run_with_unknown_agent_raises_and_creates_nothing(
    db_session, owner
) -> None:
    with pytest.raises(RuntimeDomainError):
        await job_service.enqueue_agent_run(
            db_session,
            agent_id=uuid.uuid4(),
            created_by_user_id=owner.id,
            user_input="hello",
            context=None,
            max_attempts=3,
        )
    result = await db_session.execute(select(Job))
    assert result.scalars().all() == []


# --- enqueue_workflow_run ------------------------------------------------


async def test_enqueue_workflow_run_creates_queued_run_and_queued_job(
    db_session, owner, make_agent_definition, make_workflow_definition
) -> None:
    agent = await make_agent_definition()
    steps = [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent.id), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)

    run, job = await job_service.enqueue_workflow_run(
        db_session,
        workflow_id=workflow.id,
        created_by_user_id=owner.id,
        user_input="hello",
        context=None,
        max_attempts=3,
    )
    assert run.status == WorkflowRunStatus.QUEUED
    assert job.status == JobStatus.QUEUED
    assert job.job_type == job_service.WORKFLOW_RUN_JOB_TYPE
    assert job.target_run_id == run.id


async def test_enqueue_workflow_run_with_empty_steps_raises_and_creates_nothing(
    db_session, owner, make_workflow_definition
) -> None:
    workflow = await make_workflow_definition(steps=[])
    with pytest.raises(WorkflowDomainError):
        await job_service.enqueue_workflow_run(
            db_session,
            workflow_id=workflow.id,
            created_by_user_id=owner.id,
            user_input="hello",
            context=None,
            max_attempts=3,
        )
    result = await db_session.execute(select(Job))
    assert result.scalars().all() == []


# --- cancel_agent_run: three-tier design ----------------------------------


async def test_cancel_agent_run_queued_cancels_both_job_and_run(
    db_session, owner, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    run, job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )
    result = await job_service.cancel_agent_run(db_session, run.id, owner)
    assert result.status == AgentRunStatus.CANCELLED
    await db_session.refresh(job)
    assert job.status == JobStatus.CANCELLED


async def test_cancel_agent_run_running_job_raises_job_already_running(
    db_session, owner, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    run, job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )
    await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=120.0)

    with pytest.raises(JobAlreadyRunningError):
        await job_service.cancel_agent_run(db_session, run.id, owner)


async def test_cancel_agent_run_unknown_run_raises_not_found(db_session, owner) -> None:
    with pytest.raises(AgentRunNotFoundError):
        await job_service.cancel_agent_run(db_session, uuid.uuid4(), owner)


async def test_cancel_agent_run_non_owner_raises_not_found(
    db_session, owner, other_user, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    run, _job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )
    with pytest.raises(AgentRunNotFoundError):
        await job_service.cancel_agent_run(db_session, run.id, other_user)


async def test_cancel_agent_run_no_paired_job_falls_through_to_run_level_cancel(
    db_session, owner, make_agent_definition
) -> None:
    """A run constructed directly via the ORM (as several P6 tests do,
    bypassing enqueue_agent_run entirely) has no Job row at all - cancel
    must still work, delegating to the unchanged P6 run-level cancel."""
    from app.models.agent_run import AgentRun

    agent = await make_agent_definition()
    run = AgentRun(agent_id=agent.id, created_by_user_id=owner.id, status=AgentRunStatus.QUEUED)
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    result = await job_service.cancel_agent_run(db_session, run.id, owner)
    assert result.status == AgentRunStatus.CANCELLED


# --- cancel_workflow_run: three-tier design (RUNNING case differs) -------


async def test_cancel_workflow_run_queued_cancels_both_job_and_run(
    db_session, owner, make_agent_definition, make_workflow_definition
) -> None:
    agent = await make_agent_definition()
    steps = [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent.id), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)
    run, job = await job_service.enqueue_workflow_run(
        db_session,
        workflow_id=workflow.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )

    result = await job_service.cancel_workflow_run(db_session, run.id, owner)
    assert result.status == WorkflowRunStatus.CANCELLED
    await db_session.refresh(job)
    assert job.status == JobStatus.CANCELLED


async def test_cancel_workflow_run_running_job_sets_cancel_requested_at_not_cancelled(
    db_session, owner, make_agent_definition, make_workflow_definition
) -> None:
    """Unlike an agent-run job, a RUNNING workflow-run job is not
    rejected with JobAlreadyRunningError - a cooperative
    cancel_requested_at flag is set instead, and the run itself is
    returned unchanged (still RUNNING), since the worker checks this flag
    between steps rather than the API interrupting it directly."""
    agent = await make_agent_definition()
    steps = [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent.id), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)
    run, job = await job_service.enqueue_workflow_run(
        db_session,
        workflow_id=workflow.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )
    await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=120.0)

    result = await job_service.cancel_workflow_run(db_session, run.id, owner)

    assert result.status == WorkflowRunStatus.QUEUED  # unchanged by this call; worker owns RUNNING
    await db_session.refresh(job)
    assert job.status == JobStatus.RUNNING
    assert job.cancel_requested_at is not None


async def test_cancel_workflow_run_running_job_cancel_request_is_idempotent(
    db_session, owner, make_agent_definition, make_workflow_definition
) -> None:
    agent = await make_agent_definition()
    steps = [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent.id), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)
    run, job = await job_service.enqueue_workflow_run(
        db_session,
        workflow_id=workflow.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )
    await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=120.0)

    await job_service.cancel_workflow_run(db_session, run.id, owner)
    await db_session.refresh(job)
    first_requested_at = job.cancel_requested_at

    await job_service.cancel_workflow_run(db_session, run.id, owner)
    await db_session.refresh(job)

    assert job.cancel_requested_at == first_requested_at


async def test_cancel_workflow_run_unknown_run_raises_not_found(db_session, owner) -> None:
    with pytest.raises(WorkflowRunNotFoundError):
        await job_service.cancel_workflow_run(db_session, uuid.uuid4(), owner)
